"""SAFE1 regressions: an uncertain transcript cannot enter the notebook unchecked."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.lane_n_support import VoiceNotesHarness, shown
from tests.test_lane_rp3_voice_questions import _Voice


CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "safe1_transcript_regressions.json").read_text(
        encoding="utf-8"
    )
)


class SafeRecordVoiceBoundaryTests(VoiceNotesHarness, unittest.TestCase):
    """The real socket -> router-off rules -> report-store boundary."""

    def test_short_purpose_is_confirmed_before_the_report_changes(self) -> None:
        socket = self.turns("프로토콜 시작해줘", "편. 편.")
        report = self.report()
        self.assertNotIn("purpose_recorded", [event["event_type"] for event in report["events"]])
        # Lane SAFE2 (2026-10-31): a phrase the recognizer wrote twice is read
        # once ("편. 편." is one "편"), so the question and the record hold it once.
        self.assertIn("목적을 '편'으로 기록할까요?", shown(socket)[2])

        socket = self.turns("프로토콜 시작해줘", "편. 편.", "네")
        purpose = next(
            event for event in self.report()["events"] if event["event_type"] == "purpose_recorded"
        )
        self.assertEqual(purpose["user_wording"], "편")
        self.assertEqual(purpose["confirmation_state"], "experimenter_confirmed")
        self.assertIn("확인했어요", shown(socket)[3])

    def test_normal_purpose_is_read_back_and_voice_correction_is_append_only(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘",
            "오늘은 세척 조건을 비교해 볼게",
            "정정: 오늘은 완충액 세척 조건을 비교해 볼게",
        )
        events = self.report()["events"]
        purpose = next(event for event in events if event["event_type"] == "purpose_recorded")
        correction = next(event for event in events if event["event_type"] == "record_corrected")
        self.assertEqual(purpose["user_wording"], "오늘은 세척 조건을 비교해 볼게")
        self.assertEqual(purpose["confirmation_state"], "read_back")
        self.assertEqual(correction["payload"]["record_fix"]["text_before"], purpose["user_wording"])
        self.assertEqual(
            correction["payload"]["record_fix"]["text_after"],
            "오늘은 완충액 세척 조건을 비교해 볼게",
        )
        self.assertIn("오늘은 세척 조건을 비교해 볼게", shown(socket)[2])
        self.assertIn("틀리면 '정정'", shown(socket)[2])

    def test_numeric_observation_cannot_be_saved_until_the_experimenter_confirms(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘", "넘어가", "관찰 기록해 줘: 측정값 37 °C"
        )
        self.assertFalse(
            any(
                event["event_type"] == "observation"
                and event["user_wording"] == "측정값 37 °C"
                for event in self.report()["events"]
            )
        )
        self.assertIn("37 °C로 기록할까요?", shown(socket)[3])

        self.turns(
            "프로토콜 시작해줘", "넘어가", "관찰 기록해 줘: 측정값 37 °C", "네"
        )
        observation = next(
            event
            for event in self.report()["events"]
            if event["event_type"] == "observation" and event["user_wording"] == "측정값 37 °C"
        )
        self.assertEqual(observation["confirmation_state"], "experimenter_confirmed")

    def test_numeric_observation_can_be_corrected_before_any_value_is_saved(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘", "넘어가", "기록해 줘: 1.7 100",
            "정정", "1.8 100",
        )
        observations = [
            event for event in self.report()["events"] if event["event_type"] == "observation"
        ]
        self.assertEqual([event["user_wording"] for event in observations], ["1.8 100"])
        self.assertEqual(observations[0]["confirmation_state"], "experimenter_confirmed")
        self.assertIn("고칠 값을 말해 주세요", shown(socket)[4])


class SafeRecordReplayTests(_Voice):
    def test_diagnostic_transcripts_do_not_write_before_confirmation(self) -> None:
        for case in CASES:
            with self.subTest(case=case["case_id"]):
                self.setUp()
                if case["setup"] == "purpose_question":
                    self.say("프로토콜 시작해줘")
                elif case["setup"] == "result_question":
                    self.session.open_notebook_question("result")
                    self.session.active = False
                else:
                    self.start()
                plan = self.say(case["transcript"])
                self.assertEqual(plan.intent_kind, case["expected_intent"])
                self.assertNotIn(case["must_not_record"], [event[0] for event in self.events()])

    def test_existing_short_commands_keep_their_deterministic_meaning(self) -> None:
        self.start()
        moved = self.say("다음")
        self.assertTrue(moved.state_changed)
        self.assertEqual(self.session.current_index, 1)
        timer = self.say("타이머 시작해 줘")
        self.assertEqual(timer.action.value, "start_timer")
