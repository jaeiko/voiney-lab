"""Short command words are never written down as an answer (lane SAY, decision 5, 2026-10-25).

The person's voice test of 2026-10-25: with the start's notebook question
open ("오늘 실험 목적이나 시료를 한 줄로 말씀해 주세요."), "자세히" -- heard as
"차세이" -- was answered "목적을 기록했어요." and stored as the run's purpose.
Human decision 5: "자세히" and its kin ("더 자세히", "자세하게", "좀 더 설명해
줘") are never recorded, in any state; the other short command words were
swept the same way (``~/reports/say/tools/short_words_sweep.py``: with a
notebook question open, 28 of them were stored as the purpose or as an
observation; with none open, the detail words fell to the general "질문을
도와드릴 수 있어요").

Here, through the rules' route and the server's record writer:
  * with the purpose, observation or result question open, a short command
    word is not the answer -- nothing is recorded; an acknowledgement
    ("알겠어", "고마워") skips the question like "넘어가";
  * the detail words ask for the current step in detail (read-only), and
    "다시" / "한 번 더" repeat the last guidance;
  * a real answer, however short ("연습", "BSA 정량"), is still recorded.
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
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

from tests.lane_n_support import notes_fixture

#: The detail family, as said and as the STT of 2026-10-25 heard it.
DETAIL_WORDS = (
    "자세히", "더 자세히", "자세하게", "자세히요", "좀 더 자세히", "더 자세하게",
    "자세히 좀", "조금 더 자세히", "좀 더 설명해 줘", "더 설명해 줘", "설명해 줘",
    "자세히 알려줘", "더 알려줘", "쉽게 설명해 줘", "차세이", "자세이",
)
#: Other short command words the sweep found written down as an answer.
COMMAND_WORDS = (
    "다시", "한 번 더", "천천히", "크게", "작게", "짧게", "길게", "이전", "이전 단계",
    "취소", "요약", "원문", "그림", "타이머", "몰라", "뭐라고",
)
ACKNOWLEDGEMENTS = ("알겠어", "고마워", "좋아", "오케이", "확인")
RECORD_EVENTS = {"purpose_recorded", "observation", "result_recorded", "anomaly", "note"}


class _Voice(unittest.TestCase):
    """One session through the rules' path, its records written as the server writes them."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_TIMEZONE": "Asia/Seoul"})
        env.start()
        self.addCleanup(env.stop)
        self.session = CuratedProtocolSession(notes_fixture())
        self.session.activate_configured()
        self.listener = SimpleNamespace(
            session_id="lane-say-short", experiment_report_id=None,
            experiment_report_store=er.ExperimentReportStore(Path(tmp.name) / "r.sqlite"),
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

    def recorded(self) -> list[str]:
        if self.listener.experiment_report_id is None:
            return []
        report = self.listener.experiment_report_store.get_report(self.listener.experiment_report_id)
        return [event["event_type"] for event in report["events"] if event["event_type"] in RECORD_EVENTS]

    def assert_not_recorded(self, plan, words: str) -> None:
        self.assertIsNone(plan.notebook_record, words)
        self.assertIsNone(plan.note_record, words)
        self.assertFalse(plan.reported_observation, words)
        self.assertFalse(plan.reported_anomaly, words)
        self.assertNotIn("기록했어요", plan.speech_text or "", words)
        self.assertEqual(self.recorded(), [], words)


class PurposeQuestionTests(_Voice):
    def open_purpose(self) -> None:
        self.setUp()
        self.say("프로토콜 시작해줘")
        self.assertEqual(self.session.notebook_question_open(), "purpose")

    def test_the_detail_words_are_never_the_purpose(self) -> None:
        for words in DETAIL_WORDS:
            with self.subTest(words=words):
                self.open_purpose()
                plan = self.say(words)
                self.assert_not_recorded(plan, words)
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.session.current_index, 0)

    def test_the_detail_words_read_the_current_step_in_detail(self) -> None:
        for words in DETAIL_WORDS:
            with self.subTest(words=words):
                self.open_purpose()
                plan = self.say(words)
                self.assertIs(plan.action, CuratedProtocolAction.FULL_DETAIL, plan.intent_kind)
                self.assertEqual(plan.intent_kind, "step_elaboration")

    def test_other_short_command_words_are_never_the_purpose(self) -> None:
        for words in COMMAND_WORDS:
            with self.subTest(words=words):
                self.open_purpose()
                plan = self.say(words)
                self.assert_not_recorded(plan, words)
                self.assertEqual(self.session.current_index, 0)

    def test_an_acknowledgement_skips_the_question(self) -> None:
        for words in ACKNOWLEDGEMENTS:
            with self.subTest(words=words):
                self.open_purpose()
                plan = self.say(words)
                self.assert_not_recorded(plan, words)
                self.assertEqual(plan.speech_text, cp.NOTEBOOK_SKIPPED_KO)
                self.assertIsNone(self.session.notebook_question_open())

    def test_a_short_real_answer_is_still_the_purpose(self) -> None:
        for words in ("연습", "BSA 정량", "세척 연습이야", "오늘은 세척만 해 볼게"):
            with self.subTest(words=words):
                self.open_purpose()
                plan = self.say(words)
                self.assertEqual(plan.notebook_record["kind"], "purpose")
                self.assertEqual(plan.notebook_record["text"], words)
                self.assertEqual(self.recorded(), ["purpose_recorded"])

    def test_after_a_detail_word_the_answer_is_still_taken(self) -> None:
        self.open_purpose()
        self.say("자세히")
        self.assertEqual(self.session.notebook_question_open(), "purpose")
        plan = self.say("오늘은 세척만 해 볼게")
        self.assertEqual(plan.notebook_record["text"], "오늘은 세척만 해 볼게")


class ObservationAndResultQuestionTests(_Voice):
    def open_observation(self) -> None:
        self.setUp()
        self.say("프로토콜 시작해줘")
        self.say("넘어가")
        for label in ("1", "2", "3", "4"):
            self.say(f"{label}단계 완료했어")
        self.say("5단계 완료했어")
        self.say("네")
        self.assertEqual(self.session.notebook_question_open(), "observation")

    def open_result(self) -> None:
        self.setUp()
        self.say("프로토콜 시작해줘")
        self.say("넘어가")
        self.session.open_notebook_question("result")
        self.session.active = False
        self.assertEqual(self.session.notebook_question_open(), "result")

    def test_no_short_command_word_is_an_observation(self) -> None:
        for words in (*DETAIL_WORDS, *COMMAND_WORDS, *ACKNOWLEDGEMENTS):
            with self.subTest(words=words):
                self.open_observation()
                plan = self.say(words)
                self.assert_not_recorded(plan, words)

    def test_no_short_command_word_is_a_result(self) -> None:
        for words in (*DETAIL_WORDS, *COMMAND_WORDS, *ACKNOWLEDGEMENTS):
            with self.subTest(words=words):
                self.open_result()
                plan = self.say(words)
                self.assertIsNone(plan.notebook_record, words)


class NoQuestionOpenTests(_Voice):
    def at_step_three(self) -> None:
        self.setUp()
        self.say("프로토콜 시작해줘")
        self.say("넘어가")
        self.session.current_index = 2

    def test_the_detail_words_explain_the_current_step(self) -> None:
        for words in DETAIL_WORDS:
            with self.subTest(words=words):
                self.at_step_three()
                plan = self.say(words)
                self.assertIs(plan.action, CuratedProtocolAction.FULL_DETAIL)
                self.assertEqual(plan.intent_kind, "step_elaboration")
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.session.current_index, 2)
                self.assertEqual(plan.step_label, "3")
                self.assert_not_recorded(plan, words)

    def test_again_repeats_the_last_guidance(self) -> None:
        for words in ("다시", "한 번 더", "다시 한 번"):
            with self.subTest(words=words):
                self.at_step_three()
                plan = self.say(words)
                self.assertIs(plan.action, CuratedProtocolAction.REPEAT)
                self.assertFalse(plan.state_changed)
                self.assert_not_recorded(plan, words)


class RouterProposalTests(unittest.TestCase):
    """With the router on, a model's ``record_log`` of the detail words writes nothing."""

    def test_no_record_log_of_a_detail_word_is_written(self) -> None:
        from voiney_lab.llm_router import ProposalBasis, RouterTurnFacts, ToolProposal, validate_tool_proposals

        basis = ProposalBasis(turn_id=5, generation=1, workflow_revision=3, step_id="step-4")
        for words in DETAIL_WORDS:
            facts = RouterTurnFacts(
                utterance=words, language="ko", turn_id=5, generation=1,
                workflow_revision=3, step_id="step-4", current_step_label="4",
                workflow_active=True, workflow_status="active", paused=False,
                experiment_started=True, experiment_running=True, open_question=None,
                observation_step=False, step_timer_seconds=0, timer_running=False,
                control_question=False, transcript_unreliable=False,
            )
            for log_type in ("observation", "measurement", "memo", "deviation"):
                with self.subTest(words=words, log_type=log_type):
                    verdict = validate_tool_proposals(
                        [ToolProposal(tool="record_log", log_type=log_type, value=words, evidence=words)],
                        facts, basis)
                    self.assertNotEqual(verdict.effect, "execute")
            with self.subTest(words=words, log_type="anomaly"):
                verdict = validate_tool_proposals(
                    [ToolProposal(tool="record_log", log_type="anomaly", value=words, evidence=words)],
                    facts, basis)
                # Not a problem: asked about once ("이상 사항으로 기록할까요?"), never written at once.
                self.assertNotEqual(verdict.effect, "execute")


if __name__ == "__main__":
    unittest.main()
