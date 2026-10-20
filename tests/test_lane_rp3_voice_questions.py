"""What the run asks for the notebook, by voice (lane RP3, decision ②, 2026-10-20).

Human decision: short questions the experimenter can skip, none of which
changes the workflow's state --
  a. once at the start: "오늘 실험 목적이나 시료를 한 줄로 말씀해 주세요. 없으면
     '넘어가'라고 하세요." The next words are the purpose and samples (the
     sample names, count and control read deterministically); a command
     ("다음", "1단계 완료했어", "시작", "종료", a note request, a question) skips
     the question and runs as it does now; a bare "넘어가"/"없어" skips it
     without moving anything;
  b. a note that names a sample ("3번 샘플", "S2", "대조군") carries it;
  c. only at a step whose source names something to observe (endpoint
     words), once when that step is completed without a description:
     "관찰한 것 있으면 말씀해 주세요." -- off with
     VOINEY_LAB_NOTEBOOK_OBSERVATION_QUESTION=false;
  d. once at the end, after the values were confirmed (or at once when there
     is nothing to confirm): "결과 한 줄과 다음에 바꿀 점이 있으면 말씀해 주세요.";
  e. "목적 고쳐: …", "결과 고쳐: …" amend what was said, as lane N's
     corrections do (appended, never rewritten).
The record is append-only; nothing here advances a step or ends a run.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from voiney_lab import curated_protocol as cp
from voiney_lab import experiment_reports as er
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

from tests.lane_n_support import VoiceNotesHarness, notes_fixture, shown

PURPOSE = "오늘은 세포에 약물 X를 처리해서 생존율을 보는 실험이에요. 1번 2번 샘플이 처리군이고 3번은 대조군"


class _Voice(unittest.TestCase):
    """One session through the rules' path, its records written as the server writes them."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_TIMEZONE": "Asia/Seoul"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.session = CuratedProtocolSession(notes_fixture())
        self.session.activate_configured()
        self.listener = SimpleNamespace(
            session_id="lane-rp3-voice", experiment_report_store=er.ExperimentReportStore(Path(tmp.name) / "r.sqlite"),
            experiment_report_id=None,
        )
        self.turn_id = 0

    def say(self, text: str):
        self.turn_id += 1
        before = self.session.current_index
        plan = route_curated_runtime_turn(
            self.session, text, turn_id=self.turn_id, language="ko", configuration_id=1, generation=1,
        ).plan
        if server_module._reportable(plan) or plan.action.value == "report_anomaly":
            with mock.patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
                server_module._record_experiment_report_plan(
                    self.listener, self.session, plan, turn_id=self.turn_id, generation=1,
                    pre_transition_index=before)
        return plan

    def events(self) -> list[tuple[str, str | None, str | None, dict]]:
        if self.listener.experiment_report_id is None:
            return []
        report = self.listener.experiment_report_store.get_report(self.listener.experiment_report_id)
        return [(e["event_type"], e["step_label"], e["user_wording"], e.get("payload") or {}) for e in report["events"]]

    def markdown(self) -> str:
        return self.listener.experiment_report_store.export_markdown(
            self.listener.experiment_report_id, fixture=self.session.fixture, start_check=(), previous=None).decode()

    def start(self, *, skip: bool = True):
        plan = self.say("프로토콜 시작해줘")
        if skip:
            self.say("넘어가")
        return plan


class StartQuestionTests(_Voice):
    def test_the_start_asks_once_for_the_purpose_and_samples(self) -> None:
        plan = self.say("프로토콜 시작해줘")
        self.assertTrue(plan.speech_text.endswith(cp.PURPOSE_QUESTION_KO), plan.speech_text)
        self.assertTrue(plan.display_text.endswith(cp.PURPOSE_QUESTION_KO), plan.display_text)
        self.assertEqual(cp.PURPOSE_QUESTION_KO,
                         "오늘 실험 목적이나 시료를 한 줄로 말씀해 주세요. 없으면 '넘어가'라고 하세요.")
        self.assertEqual((self.session.active, self.session.current_index), (True, 0))
        self.assertEqual(self.session.notebook_question_open(), "purpose")

    def test_the_next_words_are_the_purpose_and_the_samples(self) -> None:
        self.say("프로토콜 시작해줘")
        plan = self.say(PURPOSE)
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.session.current_index, 0)
        self.assertEqual(plan.notebook_record, {
            "kind": "purpose", "text": PURPOSE,
            "samples": [
                {"name": "1번 샘플", "condition": "처리군", "control": False},
                {"name": "2번 샘플", "condition": "처리군", "control": False},
                {"name": "3번 샘플", "condition": "대조군", "control": True},
            ],
        })
        self.assertEqual(plan.speech_text, "목적과 시료 3개를 기록했어요.")
        self.assertIsNone(self.session.notebook_question_open())
        recorded = [e for e in self.events() if e[0] == "purpose_recorded"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][2], PURPOSE)
        self.assertEqual([s["name"] for s in recorded[0][3]["samples"]], ["1번 샘플", "2번 샘플", "3번 샘플"])
        text = self.markdown()
        self.assertTrue(text.startswith("# 세포에 약물 X를 처리해서 생존율을 보는 실험\n"), text[:80])
        self.assertIn("| 3번 샘플 | 대조군 | 예 |", text)
        # The question was answered: the next free words are not a purpose.
        later = self.say("그리고 배지는 새로 만든 거야")
        self.assertIsNone(later.notebook_record)
        self.assertEqual(len([e for e in self.events() if e[0] == "purpose_recorded"]), 1)

    def test_a_purpose_without_samples_is_recorded_as_said(self) -> None:
        self.say("프로토콜 시작해줘")
        plan = self.say("오늘은 세척만 해 볼게")
        self.assertEqual(plan.notebook_record, {"kind": "purpose", "text": "오늘은 세척만 해 볼게", "samples": []})
        self.assertEqual(plan.speech_text, "목적을 기록했어요.")
        self.assertIn("# 세척만 해 볼게\n", self.markdown())
        self.assertIn("> 시료는 말하지 않음. 말한 그대로: “오늘은 세척만 해 볼게”", self.markdown())

    def test_skip_words_skip_the_question_without_moving(self) -> None:
        for words in ("넘어가", "없어요", "그냥 넘어가", "아니"):
            with self.subTest(words=words):
                self.setUp()
                self.say("프로토콜 시작해줘")
                plan = self.say(words)
                self.assertEqual(plan.speech_text, "알겠어요.")
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.session.current_index, 0)
                self.assertIsNone(plan.notebook_record)
                self.assertIsNone(self.session.notebook_question_open())
        # Said again with no question open, "넘어가" moves on as it does now.
        plan = self.say("넘어가")
        self.assertTrue(plan.state_changed)
        self.assertEqual(self.session.current_index, 1)
        self.assertEqual([e[0] for e in self.events() if e[0] == "purpose_recorded"], [])

    def test_a_command_drops_the_question_and_runs_as_now(self) -> None:
        self.say("프로토콜 시작해줘")
        plan = self.say("1단계 완료했어")
        self.assertTrue(plan.state_changed)
        self.assertEqual(self.session.current_index, 1)
        self.assertIsNone(plan.notebook_record)
        self.assertEqual([e[0] for e in self.events() if e[0] == "purpose_recorded"], [])
        self.assertIsNone(self.session.notebook_question_open())
        for words, kind in (("기록해 줘, pH 7.2", "observation"), ("1단계 설명해 줘", None), ("실험 종료", None)):
            with self.subTest(words=words):
                self.setUp()
                self.say("프로토콜 시작해줘")
                plan = self.say(words)
                self.assertIsNone(plan.notebook_record)
                self.assertEqual(self.session.current_index, 0)
                self.assertEqual([e[0] for e in self.events() if e[0] == "purpose_recorded"], [])
                if kind:
                    self.assertIn(kind, [e[0] for e in self.events()])

    def test_the_question_lapses_once_the_run_moved(self) -> None:
        self.say("프로토콜 시작해줘")
        self.session.current_index = 2  # as the evaluation sets move a session
        plan = self.say("세포가 잘 자랐어")
        self.assertIsNone(plan.notebook_record)
        self.assertEqual([e[0] for e in self.events() if e[0] == "purpose_recorded"], [])


class ObservationQuestionTests(_Voice):
    def reach_step_five(self) -> None:
        self.start()
        for label in ("1", "2", "3", "4"):
            self.say(f"{label}단계 완료했어")
        self.assertEqual(self.session.current_index, 4)

    def test_asked_once_when_a_step_with_endpoint_words_is_completed_with_a_bare_yes(self) -> None:
        self.reach_step_five()
        self.say("5단계 완료했어")  # the endpoint question (lane E) opens first
        plan = self.say("네")
        self.assertEqual(self.session.current_index, 5)
        self.assertTrue(plan.speech_text.endswith(cp.OBSERVATION_QUESTION_KO), plan.speech_text)
        self.assertEqual(cp.OBSERVATION_QUESTION_KO, "관찰한 것 있으면 말씀해 주세요.")
        self.assertEqual(self.session.notebook_question_open(), "observation")
        answer = self.say("밴드가 투명하고 깨끗해")
        self.assertFalse(answer.state_changed)
        self.assertEqual(self.session.current_index, 5)
        self.assertEqual(answer.speech_text, "관찰을 기록했어요.")
        self.assertEqual(answer.notebook_record["kind"], "observation")
        self.assertEqual(answer.notebook_record["step_label"], "5")
        observed = [e for e in self.events() if e[0] == "observation" and e[2] == "밴드가 투명하고 깨끗해"]
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][1], "5")
        self.assertIn("| 5단계 · Fictional wash | — | 밴드가 투명하고 깨끗해 |", self.markdown())
        self.assertIsNone(self.session.notebook_question_open())

    def test_not_asked_when_the_completion_carried_a_description_or_the_step_names_nothing(self) -> None:
        self.reach_step_five()
        self.say("5단계 완료했어")
        plan = self.say("밴드가 투명해")
        self.assertEqual(self.session.current_index, 5)
        self.assertNotIn(cp.OBSERVATION_QUESTION_KO, plan.speech_text)
        self.assertIsNone(self.session.notebook_question_open())
        self.setUp()
        self.start()
        plan = self.say("1단계 완료했어")
        self.assertNotIn(cp.OBSERVATION_QUESTION_KO, plan.speech_text)
        self.assertIsNone(self.session.notebook_question_open())

    def test_skip_words_and_commands_after_the_question(self) -> None:
        self.reach_step_five()
        self.say("5단계 완료했어")
        self.say("네")
        plan = self.say("없어")
        self.assertEqual(plan.speech_text, "알겠어요.")
        self.assertEqual(self.session.current_index, 5)
        self.setUp()
        self.reach_step_five()
        self.say("5단계 완료했어")
        self.say("네")
        plan = self.say("6단계 완료했어")
        self.assertTrue(plan.state_changed)
        self.assertIsNone(plan.notebook_record)

    def test_the_setting_turns_it_off(self) -> None:
        with mock.patch.dict(os.environ, {"VOINEY_LAB_NOTEBOOK_OBSERVATION_QUESTION": "false"}):
            self.session = CuratedProtocolSession(notes_fixture())
            self.session.activate_configured()
            self.reach_step_five()
            self.say("5단계 완료했어")
            plan = self.say("네")
        self.assertNotIn(cp.OBSERVATION_QUESTION_KO, plan.speech_text)
        self.assertIsNone(self.session.notebook_question_open())

    def test_a_note_names_the_sample_it_speaks_of(self) -> None:
        self.start()
        self.say("기록해 줘, 3번 샘플 세포 밀도가 낮아")
        self.say("기록해 줘, 대조군은 정상")
        self.say("기록해 줘, pH 7.2")
        samples = [e[3].get("sample") for e in self.events() if e[0] == "observation"]
        self.assertEqual(samples, ["3번 샘플", "대조군", None])
        self.assertIn("| 1단계 · Fictional wash | 3번 샘플 | 3번 샘플 세포 밀도가 낮아 |", self.markdown())


class EndQuestionTests(_Voice):
    def test_the_result_and_the_next_change_after_the_end(self) -> None:
        self.start()
        self.say("실험 종료")
        self.say("응")
        self.assertFalse(self.session.active)
        words = self.session.open_notebook_question("result")
        self.assertEqual(words, cp.RESULT_QUESTION_KO)
        self.assertEqual(cp.RESULT_QUESTION_KO, "결과 한 줄과 다음에 바꿀 점이 있으면 말씀해 주세요.")
        plan = self.say("밴드가 깨끗하게 나왔어. 다음에는 세척을 한 번 더 할게")
        self.assertFalse(plan.state_changed)
        self.assertFalse(self.session.active)
        self.assertEqual(plan.speech_text, "결과와 다음에 바꿀 점을 기록했어요.")
        self.assertEqual(plan.notebook_record, {
            "kind": "result", "text": "밴드가 깨끗하게 나왔어. 다음에는 세척을 한 번 더 할게",
            "result": "밴드가 깨끗하게 나왔어.", "next_change": "세척을 한 번 더 할게"})
        recorded = [e for e in self.events() if e[0] == "result_recorded"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual((recorded[0][3]["result"], recorded[0][3]["next_change"]),
                         ("밴드가 깨끗하게 나왔어.", "세척을 한 번 더 할게"))
        text = self.markdown()
        self.assertIn("> 결과 (실험자가 말함): “밴드가 깨끗하게 나왔어.”", text)
        self.assertIn("- 세척을 한 번 더 할게 (실험자가 말함)", text)

    def test_skip_and_a_command_after_the_end(self) -> None:
        self.start()
        self.say("실험 종료")
        self.say("응")
        self.session.open_notebook_question("result")
        plan = self.say("넘어가")
        self.assertEqual(plan.speech_text, "알겠어요.")
        self.assertEqual([e[0] for e in self.events() if e[0] == "result_recorded"], [])
        self.session.open_notebook_question("result")
        plan = self.say("프로토콜 시작해줘")
        self.assertIsNone(plan.notebook_record)
        self.assertEqual([e[0] for e in self.events() if e[0] == "result_recorded"], [])


class FixTests(_Voice):
    def test_the_purpose_and_the_result_are_corrected_in_words(self) -> None:
        self.say("프로토콜 시작해줘")
        self.say(PURPOSE)
        plan = self.say("목적 고쳐: 약물 X 생존율 실험, 시료 3개")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.speech_text, "목적을 고쳤어요.")
        self.assertEqual(plan.notebook_record, {"kind": "fix", "target": "purpose", "text": "약물 X 생존율 실험, 시료 3개"})
        fixes = [e for e in self.events() if e[0] == "record_corrected"]
        self.assertEqual(len(fixes), 1)
        purpose_key = next(e for e in self.listener.experiment_report_store.get_report(
            self.listener.experiment_report_id)["events"] if e["event_type"] == "purpose_recorded")["event_key"]
        self.assertEqual(fixes[0][3]["record_fix"]["target_event_key"], purpose_key)
        self.assertEqual(fixes[0][3]["record_fix"]["text_after"], "약물 X 생존율 실험, 시료 3개")
        text = self.markdown()
        self.assertTrue(text.startswith("# 약물 X 생존율 실험, 시료 3개\n"), text[:80])
        self.assertIn("| 3번 시료 |", text)  # the samples are read from the corrected words
        # Nothing said as a result yet: nothing to correct, nothing recorded.
        plan = self.say("결과 고쳐: 밴드가 흐릿했어")
        self.assertEqual(plan.speech_text, "고칠 결과 기록이 없어요.")
        self.assertIsNone(plan.notebook_record)
        self.assertEqual(len([e for e in self.events() if e[0] == "record_corrected"]), 1)

    def test_the_result_is_corrected_after_the_end(self) -> None:
        self.start()
        self.say("실험 종료")
        self.say("응")
        self.session.open_notebook_question("result")
        self.say("밴드가 깨끗하게 나왔어")
        plan = self.say("결과 고쳐: 밴드가 조금 흐릿했어. 다음에는 세척 시간을 늘릴게")
        self.assertEqual(plan.speech_text, "결과를 고쳤어요.")
        text = self.markdown()
        self.assertIn("> 결과 (실험자가 말함): “밴드가 조금 흐릿했어.”", text)
        self.assertIn("- 세척 시간을 늘릴게 (실험자가 말함)", text)
        self.assertRegex(text, r"밴드가 깨끗하게 나왔어 → \d\d:\d\d 정정: 밴드가 조금 흐릿했어")


class NotAnAnswerTests(_Voice):
    """Words after the start question that are not its answer -- found by the
    full suite at the first ② commit: a pause word, words cut short (lane RA's
    fragment), the pick after "A요, B요?" (lane VT), a request the rules do
    not understand ("원문 그림 띄워 줘")."""

    def test_a_pause_word_pauses(self) -> None:
        self.say("프로토콜 시작해줘")
        plan = self.say("잠깐")
        self.assertIsNone(plan.notebook_record)
        self.assertEqual(self.session.last_front_rule, "pause")
        self.assertEqual(self.session.current_index, 0)
        self.assertEqual([e[0] for e in self.events() if e[0] == "purpose_recorded"], [])

    def test_words_cut_short_are_not_the_purpose(self) -> None:
        self.say("프로토콜 시작해줘")
        plan = self.say("어 그러니까 음 지금 이 단계에서 쓰는")
        self.assertIsNone(plan.notebook_record)
        self.assertEqual(self.session.notebook_question_open(), "purpose")
        self.assertEqual(self.say(PURPOSE).speech_text, "목적과 시료 3개를 기록했어요.")

    def test_a_request_is_a_command_whatever_the_rules_make_of_it(self) -> None:
        for words in ("원문 그림 띄워 줘", "관찰 메모해 줘 멤브레인이 살짝 말랐어", "이 단계 다시 읽어 주세요"):
            with self.subTest(words=words):
                self.setUp()
                self.say("프로토콜 시작해줘")
                plan = self.say(words)
                self.assertIsNone(plan.notebook_record)
                self.assertEqual(self.session.current_index, 0)
                self.assertEqual([e[0] for e in self.events() if e[0] == "purpose_recorded"], [])

    def test_the_words_after_a_value_choice_pick_one_first(self) -> None:
        from tests.test_lane_vt_vague_reference import mixed_fixture

        self.session = CuratedProtocolSession(mixed_fixture())
        self.session.activate_configured()
        self.say("프로토콜 시작해줘")
        self.assertEqual(self.say("그거 얼마나 넣어?").display_text, "Buffer A요, Buffer B요?")
        picked = self.say("버퍼 비")
        self.assertEqual(picked.display_text, "이 단계의 Buffer B는 30 µL예요.")
        self.assertIsNone(picked.notebook_record)
        self.assertEqual(self.session.notebook_question_open(), "purpose")
        self.assertEqual(self.say(PURPOSE).speech_text, "목적과 시료 3개를 기록했어요.")


class ServedTests(VoiceNotesHarness, unittest.TestCase):
    """The production path: the socket, the server's records, the notebook."""

    def test_the_start_asks_and_the_answer_is_recorded(self) -> None:
        socket = self.turns("프로토콜 시작해줘", "오늘은 세척 실험이에요. 시료 2개에 대조군 1개", "1단계 완료했어")
        replies = shown(socket)
        self.assertTrue(replies[1].endswith(cp.PURPOSE_QUESTION_KO), replies[1])
        self.assertEqual(replies[2], "목적과 시료 3개를 기록했어요.")
        recorded = [e for e in self.report()["events"] if e["event_type"] == "purpose_recorded"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual([s["name"] for s in recorded[0]["payload"]["samples"]], ["1번 시료", "2번 시료", "대조군"])
        self.assertTrue(self.report_markdown().startswith("# 세척 실험\n"))

    def test_the_end_asks_after_the_values_are_confirmed_and_at_once_without_any(self) -> None:
        socket = self.turns("프로토콜 시작해줘", "넘어가", "1단계 완료했어", "실험노트에 적어 줘, pH 7.2",
                            "실험 종료해", "응", "네", "밴드가 깨끗했어. 다음엔 세척을 더 할게")
        replies = shown(socket)
        self.assertTrue(replies[7].endswith(cp.RESULT_QUESTION_KO), replies[7])
        self.assertIn("화면에서", replies[7])
        self.assertLess(replies[7].index("화면에서"), replies[7].index(cp.RESULT_QUESTION_KO))
        self.assertEqual(replies[8], "결과와 다음에 바꿀 점을 기록했어요.")
        recorded = [e for e in self.report()["events"] if e["event_type"] == "result_recorded"]
        self.assertEqual((recorded[0]["payload"]["result"], recorded[0]["payload"]["next_change"]),
                         ("밴드가 깨끗했어.", "세척을 더 할게"))
        # Nothing to confirm: asked right after the end.
        socket = self.turns("프로토콜 시작해줘", "넘어가", "1단계 완료했어", "실험 종료해", "응", "결과는 없어")
        replies = shown(socket)
        self.assertTrue(replies[5].endswith(cp.RESULT_QUESTION_KO), replies[5])
        self.assertEqual(replies[6], "알겠어요.")


if __name__ == "__main__":
    unittest.main()
