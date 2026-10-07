"""Notes said aloud and their corrections (lane N, decisions 1-2 of 2026-10-07).

1. "실험노트에 적어 줘 / 노트에 적어 줘 / 기록해 줘 / 메모해 줘 / 메모 추가해" with
   words records the words at the current step as the STT gave them -- case
   and spelling kept -- as a measurement (a number with a unit), a deviation
   ("원문과 다르게", "대신", "더/덜 넣었어"), an observation, or otherwise a
   memo. Once stored it is read back, never asked about: a measurement value
   by value ("피에이치 칠 점 이로 기록했어요"), anything else "N단계에 기록했어요".
   The kinds are the router's record_log allow-list.
2. "방금 기록 고쳐 줘, X가 아니라 Y" and "방금 기록 지워 줘" are asked about once
   ("방금 기록 '…'을 '…'로 고칠까요?"); a yes appends a correction or a
   withdrawal beside the record, which stays. The screen and the report show
   the latest words marked 정정됨 (or 취소됨).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.lane_n_support import VoiceNotesHarness, notes_fixture, shown
from tests.test_lane_r7_rule_gaps import _Turns
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    CuratedProtocolAction,
    josa,
    josa_ro,
    measurement_spans,
    note_kind,
    note_request,
    record_fix_request,
    spoken_korean,
)
from voiney_lab.llm_router import RECORD_LOG_RULES, RECORD_LOG_TYPES, ToolProposal
from voiney_lab.server import _public_experiment_report_state

ROOT = Path(__file__).resolve().parents[1]


class NoteReadingTests(unittest.TestCase):
    """What the rules read in the words, before anything is stored."""

    def test_the_command_and_the_words_said_before_or_after_it(self) -> None:
        cases = {
            "실험노트에 적어 줘, pH 7.2": "pH 7.2",
            "노트에 적어 줘 젤이 투명해졌어": "젤이 투명해졌어",
            "기록해 줘: OD600 0.45": "OD600 0.45",
            "메모해 줘 튜브 라벨 A-170": "튜브 라벨 A-170",
            "메모 추가해: LB 배지가 맑고 덩어리 없이 다 녹았어": "LB 배지가 맑고 덩어리 없이 다 녹았어",
            "관찰 기록 LB 배지가 맑고 덩어리 없이 다 녹았어": "LB 배지가 맑고 덩어리 없이 다 녹았어",
            "pH 7.2라고 적어 줘": "pH 7.2",
            "pH 7.2로 기록해 줘": "pH 7.2",
            "젤이 투명해졌다고 노트에 적어 줘": "젤이 투명해졌다",
            "튜브 라벨 A-170 메모해 줘": "튜브 라벨 A-170",
            "탈색이 됐는지 모르겠어 일단 메모해 줘": "탈색이 됐는지 모르겠어",
        }
        for said, words in cases.items():
            with self.subTest(said=said):
                request = note_request(said)
                self.assertIsNotNone(request)
                self.assertEqual(request[0], words)

    def test_a_command_with_no_words_asks_for_them(self) -> None:
        for said in ("실험노트에 적어 줘", "기록해 줘", "메모해 줘", "이거 메모해 줘", "메모 추가해"):
            with self.subTest(said=said):
                self.assertEqual(note_request(said)[0], None)

    def test_asking_about_notes_or_the_record_is_not_a_note(self) -> None:
        for said in (
            "실험 기록 보여줘", "기록 보여줘", "기록해야 돼?", "적어야 할까?",
            "적어도 10분 기다려야 해?", "용액 조금 남겨 줘", "메모장 어디 있어?",
        ):
            with self.subTest(said=said):
                self.assertIsNone(note_request(said))

    def test_the_kind_is_read_by_rule_and_unclear_is_a_memo(self) -> None:
        cases = {
            "pH 7.2": "measurement",
            "OD600 0.45": "measurement",
            "흡광도 0.45": "measurement",
            "온도 37도": "measurement",
            "15 mL 넣었어": "measurement",
            "원문과 다르게 37도 대신 40도에서 했어": "deviation",
            "Solution B를 50 µL 더 넣었어": "deviation",
            "버퍼를 덜 넣었어": "deviation",
            "LB 배지가 맑고 덩어리 없이 다 녹았어": "observation",
            "침전이 생겼어": "observation",
            "튜브 라벨 A-170": "memo",
            "내일 다시 확인": "memo",
        }
        for words, kind in cases.items():
            with self.subTest(words=words):
                self.assertEqual(note_kind(words), kind)
        self.assertEqual(note_kind("침전 생김", noun="관찰"), "observation")

    def test_the_kinds_are_the_routers_record_log_allow_list(self) -> None:
        self.assertEqual(
            RECORD_LOG_TYPES, ("observation", "measurement", "deviation", "memo", "anomaly"))
        for kind in ("observation", "measurement", "deviation", "memo"):
            with self.subTest(kind=kind):
                self.assertEqual(RECORD_LOG_RULES[kind].runs_as, "record_observation")

    def test_values_are_read_the_way_they_are_said(self) -> None:
        self.assertEqual(measurement_spans("pH 7.2 나왔어"), ("pH 7.2",))
        self.assertEqual(measurement_spans("37도에서 10분"), ("37도", "10분"))
        self.assertEqual(spoken_korean("pH 7.2"), "피에이치 칠 점 이")
        self.assertEqual(spoken_korean("37도"), "삼십칠 도")
        self.assertEqual(spoken_korean("15 mL"), "십오 밀리리터")
        self.assertEqual(spoken_korean("13,000 x g"), "만 삼천 엑스 지")
        self.assertEqual(spoken_korean("OD600 0.45"), "오디 육백 영 점 사 오")
        self.assertEqual(josa_ro("pH 7.2"), "pH 7.2로")
        self.assertEqual(josa_ro("150 rpm"), "150 rpm으로")
        self.assertEqual(josa_ro("10분"), "10분으로")
        self.assertEqual(josa("튜브 라벨 A-170", "을", "를"), "튜브 라벨 A-170을")
        self.assertEqual(josa("pH 7.2", "을", "를"), "pH 7.2를")

    def test_a_correction_is_read_from_its_words(self) -> None:
        self.assertEqual(
            record_fix_request("방금 기록 고쳐 줘, 7.2가 아니라 7.4"),
            {"mode": "correct", "x": "7.2", "y": "7.4"})
        self.assertEqual(
            record_fix_request("방금 기록 고쳐줘 7.2를 7.4로"),
            {"mode": "correct", "x": "7.2", "y": "7.4"})
        self.assertEqual(
            record_fix_request("메모 고쳐 줘 A-170 말고 A-171"),
            {"mode": "correct", "x": "A-170", "y": "A-171"})
        self.assertEqual(record_fix_request("방금 기록 지워 줘")["mode"], "retract")
        self.assertIsNone(record_fix_request("기록 고쳐야 돼?"))
        self.assertIsNone(record_fix_request("고쳐 줘, 7.2가 아니라 7.4"))
        self.assertEqual(
            record_fix_request("고쳐 줘, 7.2가 아니라 7.4", review=True),
            {"mode": "correct", "x": "7.2", "y": "7.4"})


class NoteRuleTests(_Turns, unittest.TestCase):
    """Decision 1 on the rules' path: a front rule, the words kept as said."""

    def open_notes(self) -> None:
        self.open(1, fixture=notes_fixture())

    def test_a_note_is_a_front_rule_recorded_at_the_current_step(self) -> None:
        self.open_notes()
        before = self.projection()
        plan = self.say("실험노트에 적어 줘, pH 7.2", front=True)
        self.assertIsNotNone(plan)
        self.assertEqual(self.session.last_front_rule, "note_record")
        self.assertIs(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertTrue(plan.reported_observation)
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.observation_predicate, "measurement")
        self.assertEqual(plan.observation_outcome, "pH 7.2")
        self.assertEqual(plan.note_record["measurements"], ["pH 7.2"])
        self.assertEqual(plan.note_record["step_label"], "2")
        self.assertEqual(self.projection(), before)
        self.assertIn("note_record", FRONT_RULES)

    def test_the_words_are_stored_as_the_stt_gave_them(self) -> None:
        self.open_notes()
        plan = self.say("메모 추가해: LB 배지가 맑고 덩어리 없이 다 녹았어")
        self.assertEqual(plan.observation_outcome, "LB 배지가 맑고 덩어리 없이 다 녹았어")
        self.assertEqual(plan.observation_predicate, "observation")

    def test_each_kind_is_kept_as_its_category(self) -> None:
        self.open_notes()
        for said, category in (
            ("기록해 줘, 원문과 다르게 37도 대신 40도에서 했어", "deviation"),
            ("노트에 적어 줘 Solution B를 50 µL 더 넣었어", "deviation"),
            ("메모해 줘 튜브 라벨 A-170", "note"),
            ("관찰 기록해 줘 침전이 생겼어", "observation"),
            ("기록해 줘 OD600 0.45", "measurement"),
        ):
            with self.subTest(said=said):
                self.assertEqual(self.say(said).observation_predicate, category)

    def test_no_words_asks_for_them_and_the_reply_is_read_by_rule(self) -> None:
        self.open_notes()
        asked = self.say("실험노트에 적어 줘")
        self.assertEqual(asked.speech_text, "어떤 내용을 기록할까요?")
        self.assertFalse(asked.reported_observation)
        recorded = self.say("pH 7.4")
        self.assertEqual(recorded.observation_outcome, "pH 7.4")
        self.assertEqual(recorded.observation_predicate, "measurement")

    def test_a_completion_or_a_spill_said_as_a_note_is_left_to_its_own_rule(self) -> None:
        self.open_notes()
        plan = self.say("다음 단계를 완료했다고 기록해 줘")
        self.assertIsNot(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertEqual(self.label(), "2")
        plan = self.say("시약을 흘렸다고 기록해 줘")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertEqual(self.session.last_front_rule, "anomaly_report")

    def test_paused_or_not_started_nothing_is_recorded(self) -> None:
        self.open(None, fixture=notes_fixture())
        self.assertIsNot(
            self.say("실험노트에 적어 줘, pH 7.2").action,
            CuratedProtocolAction.RECORD_OBSERVATION)
        self.open_notes()
        self.say("잠깐 멈춰")
        plan = self.say("실험노트에 적어 줘, pH 7.2")
        self.assertIsNot(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertFalse(plan.reported_observation)

    def test_a_note_under_an_open_endpoint_question_keeps_the_question(self) -> None:
        self.open(4, fixture=notes_fixture())
        self.say("5단계 완료했어")
        self.assertIsNotNone(self.session.pending_observation_confirmation)
        plan = self.say("메모해 줘 튜브 라벨 A-170")
        self.assertIs(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertIsNotNone(self.session.pending_observation_confirmation)
        self.assertEqual(self.label(), "5")


class CorrectionRuleTests(_Turns, unittest.TestCase):
    """Decision 2 on the rules' path: asked once, then appended, never rewritten."""

    def setUp(self) -> None:
        self.open(1, fixture=notes_fixture())
        self.say("실험노트에 적어 줘, pH 7.2")

    def test_a_correction_is_asked_about_once(self) -> None:
        plan = self.say("방금 기록 고쳐 줘, 7.2가 아니라 7.4", front=True)
        self.assertEqual(self.session.last_front_rule, "record_fix")
        self.assertIs(plan.action, CuratedProtocolAction.RECORD_CORRECTION)
        self.assertEqual(plan.speech_text, "방금 기록 'pH 7.2'를 'pH 7.4'로 고칠까요?")
        self.assertIsNone(plan.record_fix)
        self.assertTrue(self.session.awaiting_server_confirmation)

    def test_a_yes_hands_the_server_the_correction(self) -> None:
        self.say("방금 기록 고쳐 줘, 7.2가 아니라 7.4")
        plan = self.say("응")
        self.assertEqual(self.session.last_front_rule, "yes_no_open_question")
        self.assertEqual(plan.record_fix["mode"], "correct")
        self.assertEqual(plan.record_fix["before"], "pH 7.2")
        self.assertEqual(plan.record_fix["after"], "pH 7.4")
        self.assertEqual(plan.record_fix["target"]["turn_id"], 2)
        self.assertEqual(plan.record_fix["target"]["action"], "record_observation")
        self.assertFalse(plan.state_changed)
        # "방금 기록" now names the corrected words.
        asked = self.say("방금 기록 지워 줘")
        self.assertEqual(asked.speech_text, "방금 기록 'pH 7.4'를 지울까요?")

    def test_a_no_changes_nothing(self) -> None:
        self.say("방금 기록 고쳐 줘, 7.2가 아니라 7.4")
        plan = self.say("아니")
        self.assertIsNone(plan.record_fix)
        self.assertEqual(plan.speech_text, "알겠습니다. 기록을 고치지 않았어요.")
        asked = self.say("방금 기록 지워 줘")
        self.assertIn("'pH 7.2'", asked.speech_text)

    def test_a_withdrawal_is_asked_about_too(self) -> None:
        asked = self.say("방금 기록 지워 줘")
        self.assertEqual(asked.speech_text, "방금 기록 'pH 7.2'를 지울까요?")
        plan = self.say("네")
        self.assertEqual(plan.record_fix["mode"], "retract")
        self.assertEqual(self.say("방금 기록 지워 줘").speech_text, "방금 고칠 기록이 없어요. 고치지 않았어요.")

    def test_words_not_in_the_record_or_no_words_change_nothing(self) -> None:
        plan = self.say("방금 기록 고쳐 줘, 7.3이 아니라 7.4")
        self.assertEqual(plan.speech_text, "방금 기록 'pH 7.2'에는 '7.3'이 없어요. 고치지 않았어요.")
        self.assertFalse(self.session.awaiting_server_confirmation)
        plan = self.say("방금 기록 고쳐 줘")
        self.assertIn("무엇을 무엇으로 고칠지", plan.speech_text)


class RouterNoteTests(unittest.TestCase):
    """The router's record_log: the new kinds, and the words kept as said."""

    def test_a_proposed_kind_is_recorded_with_the_utterances_own_spelling(self) -> None:
        from tests.test_lane_r7_rule_gaps import _Turns as Turns

        turns = Turns()
        session = turns.open(1, fixture=notes_fixture())
        said = "lb 아니고 LB 배지 37도로 녹였어 남겨 둬"
        turns.turn_id += 1
        basis = session.proposal_basis(turn_id=turns.turn_id, generation=1)
        applied = session.apply_tool_proposal(
            [ToolProposal(tool="record_log", log_type="measurement",
                          value="lb 배지 37도로 녹였어", evidence="남겨 둬")],
            transcript=said, basis=basis, turn_id=turns.turn_id, language="ko",
            configuration_id=1, generation=1,
        )
        self.assertEqual(applied.verdict.effect, "execute")
        self.assertEqual(applied.plan.observation_predicate, "measurement")
        self.assertEqual(applied.plan.observation_outcome, "LB 배지 37도로 녹였어")

    def test_a_memo_is_stored_under_the_records_note_category(self) -> None:
        from tests.test_lane_r7_rule_gaps import _Turns as Turns

        turns = Turns()
        session = turns.open(1, fixture=notes_fixture())
        turns.turn_id += 1
        basis = session.proposal_basis(turn_id=turns.turn_id, generation=1)
        applied = session.apply_tool_proposal(
            [ToolProposal(tool="record_log", log_type="memo",
                          value="튜브 라벨 A-170", evidence="남겨 줘")],
            transcript="튜브 라벨 A-170 남겨 줘", basis=basis, turn_id=turns.turn_id,
            language="ko", configuration_id=1, generation=1,
        )
        self.assertEqual(applied.plan.observation_predicate, "note")


class ServedNoteTests(VoiceNotesHarness, unittest.TestCase):
    """The production path: run_turn, the experiment report and the timeline."""

    def test_a_note_is_stored_then_read_back(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘", "1단계 완료했어",
            "실험노트에 적어 줘, pH 7.2", "메모해 줘 튜브 라벨 A-170",
        )
        replies = shown(socket)
        self.assertEqual(replies[3], "pH 7.2로 기록했어요.")
        self.assertEqual(self.spoken[3], ["피에이치 칠 점 이로 기록했어요."])
        self.assertEqual(replies[4], "2단계에 기록했어요.")
        notes = [e for e in self.report()["events"] if e["event_type"] == "observation"]
        self.assertEqual(
            [(e["step_label"], e["category"], e["user_wording"]) for e in notes],
            [("2", "measurement", "pH 7.2"), ("2", "note", "튜브 라벨 A-170")])
        recorded = [
            e["observation"] for e in self.timeline()["timeline"]
            if e["event_type"] == "observation_recorded"
        ]
        self.assertEqual(
            [(o["category"], o["content"]) for o in recorded],
            [("measurement", "pH 7.2"), ("note", "튜브 라벨 A-170")])
        markdown = self.report_markdown()
        self.assertIn("| 2 | 측정 | pH 7.2 |", markdown)
        self.assertIn("| 2 | 메모 | 튜브 라벨 A-170 |", markdown)
        self.assertIn("측정 1건 · 관찰 0건 · 메모 1건 · 이상 0건 · 사진 0건", markdown)

    def test_a_correction_is_appended_and_the_record_shows_the_latest_words(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘", "1단계 완료했어", "실험노트에 적어 줘, pH 7.2",
            "방금 기록 고쳐 줘, 7.2가 아니라 7.4", "응",
        )
        replies = shown(socket)
        self.assertEqual(replies[4], "방금 기록 'pH 7.2'를 'pH 7.4'로 고칠까요?")
        self.assertEqual(replies[5], "방금 기록을 'pH 7.4'로 고쳤어요.")
        self.assertEqual(self.spoken[5], ["방금 기록을 피에이치 칠 점 사로 고쳤어요."])
        events = self.report()["events"]
        original = next(e for e in events if e["event_type"] == "observation")
        self.assertEqual(original["user_wording"], "pH 7.2")
        fix = next(e for e in events if e["event_type"] == "record_corrected")
        self.assertEqual(fix["payload"]["record_fix"]["target_event_key"], original["event_key"])
        self.assertEqual(fix["user_wording"], "pH 7.4")
        public = _public_experiment_report_state(self.report())
        amended = next(e for e in public["events"] if e["event_key"] == original["event_key"])
        self.assertEqual(amended["amended"], {"status": "정정됨", "text": "pH 7.4"})
        self.assertIn("| 2 | 측정 | pH 7.4 (정정됨 — 처음 기록 “pH 7.2”) |", self.report_markdown())
        observation = next(
            e["observation"] for e in self.timeline()["timeline"]
            if e["event_type"] == "observation_recorded"
        )
        self.assertEqual(observation["content"], "pH 7.2")
        self.assertEqual(observation["current_content"], "pH 7.4")
        self.assertTrue(observation["corrected"])

    def test_a_withdrawal_keeps_the_record_marked_withdrawn(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘", "1단계 완료했어", "메모해 줘 튜브 라벨 A-170",
            "방금 기록 지워 줘", "응",
        )
        self.assertEqual(
            shown(socket)[5], "방금 기록을 취소로 표시했어요. 처음 기록은 지우지 않고 남겨 두었어요.")
        markdown = self.report_markdown()
        self.assertIn("| 2 | 메모 | 튜브 라벨 A-170 (취소됨) |", markdown)
        self.assertIn("관찰 0건 · 이상 0건 · 사진 0건", markdown)
        timeline = self.timeline()["timeline"]
        self.assertTrue(any(e["event_type"] == "observation_retracted" for e in timeline))

    def test_a_deviation_note_is_a_point_done_differently(self) -> None:
        self.turns(
            "프로토콜 시작해줘", "1단계 완료했어",
            "기록해 줘, 원문과 다르게 Solution A를 600 µL 넣었어",
        )
        markdown = self.report_markdown()
        self.assertIn("- 2단계: 연구자 기록 — “원문과 다르게 Solution A를 600 µL 넣었어”.", markdown)
        self.assertIn("| 2 | 편차 |", markdown)


class ScreenTests(unittest.TestCase):
    """The cockpit shows the latest words and the mark, as text only."""

    def test_the_timeline_and_the_report_list_show_corrections(self) -> None:
        html = (ROOT / "src/voiney_lab/static/index.html").read_text(encoding="utf-8")
        block = html.split("function observationRecordText", 1)[1].split("\nfunction ", 1)[0]
        for mark in ("정정됨", "취소됨", "current_content"):
            self.assertIn(mark, block)
        self.assertNotIn("innerHTML", block)
        for label in ('record_corrected:"기록 정정"', 'observation_corrected:"기록 정정"',
                      'experiment_record_corrected:"실험 기록 정정"'):
            self.assertIn(label, html)


if __name__ == "__main__":
    unittest.main()
