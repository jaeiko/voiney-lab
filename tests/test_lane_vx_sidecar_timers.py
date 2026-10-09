"""Lane VX, decisions 5 and 6 (2026-10-09): time questions and timers on a sidecar timer.

The in-gel development fixture times its steps from a sidecar manifest the
loader checked against the source, not from an analysis (lane PT left it so).

Decision 5: "몇 분 반응시켜?", "이 단계 몇 분이야?" are the front rule step_time
there too, answered with the sidecar's value and the step's source sentence.

Decision 6: "타이머 시작해줘" at a step with no timer answers "이 단계에는 원문에
시간이 없어요." and changes nothing -- with the workspace on as well, where
the change it used to claim was refused and the reply became a save failure.

The rule tests run on lane N's wash, which has the same sidecar shape (a
10-minute manifest timer on step 2, no analysis timer table); the in-gel ones
need the licensed PDF.
"""

from __future__ import annotations

import unittest

from tests.lane_cf_support import VoiceNotesHarness, shown, wash_session
from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

NO_TIME = "이 단계에는 원문에 시간이 없어요."
STEP_2 = "2 Wash the pieces with 500 µL of Solution A for 10 min."


class SidecarTimeQuestionTests(unittest.TestCase):

    def at(self, label: str):
        turns = wash_session()
        turns.say("프로토콜 시작해줘")
        turns.session.current_index = int(label) - 1
        return turns

    def test_how_long_is_the_sidecar_value_and_the_source_sentence(self) -> None:
        for said in ("몇 분 반응시켜?", "이 단계 몇 분이야?", "몇 분 동안 해?"):
            with self.subTest(said=said):
                turns = self.at("2")
                before = turns.projection()
                plan = turns.say(said)
                self.assertEqual(plan.intent_kind, "step_duration_question")
                self.assertIn(turns.session.last_front_rule, {"step_time", "step_homophone"})
                self.assertIn(
                    f"2단계 타이머는 10분이에요. 원문에는 ‘{STEP_2}’라고 적혀 있어요. "
                    "타이머를 시작하려면 '타이머 시작해줘'라고 말씀해 주세요.",
                    plan.speech_text)
                self.assertFalse(plan.state_changed)
                self.assertEqual(turns.projection(), before)

    def test_a_step_with_no_timer_reads_its_sentence(self) -> None:
        turns = self.at("3")
        plan = turns.say("몇 분 반응시켜?")
        self.assertEqual(turns.session.last_front_rule, "step_time")
        self.assertEqual(
            plan.speech_text,
            "3단계 원문에는 ‘3 Remove and discard Solution A.’라고 적혀 있고, 시간은 적혀 있지 않아요.")

    def test_a_running_timer_is_not_offered_again(self) -> None:
        turns = self.at("2")
        turns.say("타이머 시작해줘")
        plan = turns.say("몇 분 반응시켜?")
        self.assertIn("2단계 타이머는 10분이에요.", plan.speech_text)
        self.assertNotIn("타이머를 시작하려면", plan.speech_text)

    def test_asked_under_an_open_endpoint_question_the_question_stays(self) -> None:
        turns = self.at("5")
        turns.say("5단계 완료했어")
        self.assertIsNotNone(turns.session.pending_observation_confirmation)
        plan = turns.say("몇 분 걸려?")
        self.assertEqual(plan.intent_kind, "step_duration_question")
        self.assertIn("‘until’: 숫자로 적힌 시간이 없어요", plan.speech_text)
        self.assertIsNotNone(turns.session.pending_observation_confirmation)

    def test_it_is_a_front_rule_that_needs_no_model(self) -> None:
        turns = self.at("2")
        plan = turns.say("몇 분 반응시켜?", front=True)
        self.assertIsNotNone(plan)
        self.assertEqual(turns.session.last_front_rule, "step_time")


class SidecarNoTimerStartTests(unittest.TestCase):

    def test_a_start_at_a_step_with_no_timer_changes_nothing(self) -> None:
        turns = wash_session()
        turns.say("프로토콜 시작해줘")
        turns.session.current_index = 2
        plan = turns.say("타이머 시작해줘")
        self.assertEqual(plan.speech_text, NO_TIME)
        self.assertEqual(plan.display_text, NO_TIME)
        self.assertFalse(plan.state_changed)
        self.assertEqual(turns.session.timer_status()["state"], "not_started")

    def test_a_start_at_a_timed_step_runs_as_before(self) -> None:
        turns = wash_session()
        turns.say("프로토콜 시작해줘")
        turns.session.current_index = 1
        plan = turns.say("타이머 시작해줘")
        self.assertEqual(plan.speech_text, "10분 타이머를 시작했습니다. 화면에서 남은 시간을 확인할 수 있습니다.")
        self.assertTrue(plan.state_changed)


class ServedNoTimerStartTests(VoiceNotesHarness, unittest.TestCase):
    """The production path with the workspace on, where the reply used to fail."""

    def test_the_reply_is_said_and_no_save_failure(self) -> None:
        socket = self.turns("프로토콜 시작해줘", "1단계 완료했어", "2단계 완료했어", "타이머 시작해줘")
        reply = shown(socket)[4]
        self.assertEqual(reply, NO_TIME)
        self.assertNotIn("저장하지 못", reply)
        kinds = [e["event_type"] for e in self.report()["events"]]
        self.assertNotIn("timer_started", kinds)


@unittest.skipUnless(SOURCE_PDF.is_file(), f"requires the licensed in-gel PDF at {SOURCE_PDF}")
class InGelTests(unittest.TestCase):

    def session_at(self, label: str) -> CuratedProtocolSession:
        session = CuratedProtocolSession(in_gel_fixture())
        session.activate_configured()
        self.turn = 0
        self.say(session, "프로토콜 시작해줘")
        session.current_index = int(label) - 1
        return session

    def say(self, session, text: str):
        self.turn += 1
        return route_curated_runtime_turn(
            session, text, turn_id=self.turn, language="ko", configuration_id=1, generation=1,
        ).plan

    def test_a_sidecar_step_says_its_value_and_sentence(self) -> None:
        session = self.session_at("3")
        plan = self.say(session, "몇 분 반응시켜?")
        self.assertEqual(session.last_front_rule, "step_time")
        self.assertTrue(plan.speech_text.startswith("3단계 타이머는 15분이에요. 원문에는 ‘3 Wash the band"))
        self.assertFalse(plan.state_changed)

    def test_overnight_with_a_sidecar_value_says_the_value(self) -> None:
        session = self.session_at("23")
        plan = self.say(session, "몇 시간 배양해?")
        self.assertTrue(plan.speech_text.startswith("23단계 타이머는 16시간이에요."), plan.speech_text)

    def test_until_with_no_timer_says_why(self) -> None:
        session = self.session_at("7")
        plan = self.say(session, "이 단계 몇 분이야?")
        self.assertIn("‘until’: 숫자로 적힌 시간이 없어요", plan.speech_text)
        self.assertFalse(plan.state_changed)

    def test_no_timer_start_changes_nothing(self) -> None:
        session = self.session_at("4")
        plan = self.say(session, "타이머 시작해줘")
        self.assertEqual(plan.speech_text, NO_TIME)
        self.assertFalse(plan.state_changed)


if __name__ == "__main__":
    unittest.main()
