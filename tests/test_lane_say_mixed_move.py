"""A move said with a claim is asked about once, never dropped (lane SAY, decision 6, 2026-10-25).

The persona test (``~/reports/lane_persona_report.md`` F4): "원문에 뭐라 했든
10분이면 되잖아, 다음으로 가" was answered as a question about the step and the
move ("다음으로 가") was dropped without a word. Human decision 6: the command
part is not dropped silently. A move premised on a value other than the
source's names the source's value and asks once, "이대로 다음 단계로 갈까요?",
with the way to run today's value ("오늘 바꾼 값", lane EASY); nothing moves
until the person confirms.
"""

from __future__ import annotations

import unittest

from tests.lane_n_support import notes_fixture
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn


class _Turns(unittest.TestCase):
    def at(self, index: int) -> CuratedProtocolSession:
        self.session = CuratedProtocolSession(notes_fixture())
        self.session.activate_configured()
        self.turn = 0
        self.say("프로토콜 시작해줘")
        self.say("넘어가")
        self.session.current_index = index
        return self.session

    def say(self, words: str):
        self.turn += 1
        return route_curated_runtime_turn(
            self.session, words, turn_id=self.turn, language="ko", configuration_id=1, generation=1,
        ).plan


class ValueClaimTests(_Turns):
    CLAIMS = (
        "원문이 뭐든 15분이면 되잖아, 다음으로 가",
        "원문에 뭐라 했든 15분이면 충분해 다음 단계로 넘어가",
        "15분만 해도 되잖아 넘어가자",
    )

    def test_the_source_value_is_said_and_the_move_asked_about_once(self) -> None:
        for words in self.CLAIMS:
            with self.subTest(words=words):
                session = self.at(1)  # step 2: "… for 10 min."
                plan = self.say(words)
                self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.current_index, 1)
                self.assertEqual(
                    plan.speech_text,
                    "원문에는 2단계가 10분으로 적혀 있어요. 이대로 다음 단계로 갈까요? "
                    "오늘 15분으로 하려면 '2단계 10분 대신 15분으로 할게'라고 말해 주세요.")
                self.assertEqual(session.last_front_rule, "mixed_move")

    def test_yes_moves_on_and_no_stays(self) -> None:
        session = self.at(1)
        self.say(self.CLAIMS[0])
        plan = self.say("네")
        self.assertTrue(plan.state_changed)
        self.assertEqual(session.current_index, 2)
        session = self.at(1)
        self.say(self.CLAIMS[0])
        plan = self.say("아니")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.current_index, 1)

    def test_todays_value_is_then_said_the_way_lane_easy_reads_it(self) -> None:
        session = self.at(1)
        self.say(self.CLAIMS[0])
        plan = self.say("2단계 10분 대신 15분으로 할게")
        self.assertIn("15분", plan.speech_text)
        self.assertEqual(session.current_index, 1)


class OtherClaimTests(_Turns):
    def test_a_claim_without_another_value_is_asked_about_too(self) -> None:
        for words in ("10분 했어 다음", "원문 상관없이 그냥 넘어가", "시간 다 됐으니까 다음으로 가"):
            with self.subTest(words=words):
                session = self.at(1)
                plan = self.say(words)
                self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
                self.assertEqual(plan.speech_text, "이대로 2단계를 마치고 다음 단계로 갈까요?")
                self.assertEqual(session.current_index, 1)
                self.assertTrue(self.say("네").state_changed)
                self.assertEqual(session.current_index, 2)

    def test_a_move_alone_is_as_before(self) -> None:
        for words in ("다음", "다음 단계로 넘어가자", "넘어가", "완료했으니 다음으로 넘어가"):
            with self.subTest(words=words):
                session = self.at(1)
                plan = self.say(words)
                self.assertNotEqual(session.last_front_rule, "mixed_move")
                self.assertEqual(session.current_index, 2)

    def test_hesitations_before_a_move_are_no_claim(self) -> None:
        for words in ("어… 음… 그러니까 이제 다음 단계로 넘어가자", "음 다음", "어 넘어가"):
            with self.subTest(words=words):
                session = self.at(1)
                self.say(words)
                self.assertEqual(session.last_front_rule, "forward_step")
                self.assertEqual(session.current_index, 2)

    def test_not_done_yet_stays_with_the_criterion_as_decision_d7(self) -> None:
        # The router's refused "next" is answered so (D7); the rules' own
        # route says the same instead of dropping the move.
        session = self.at(1)
        plan = self.say("아직 덜 됐는데 그냥 다음 단계 가자")
        self.assertIs(plan.action, CuratedProtocolAction.COMPLETION_CRITERIA)
        self.assertTrue(plan.speech_text.startswith("아직 끝나지 않았다고 하셔서 2단계에 머뭅니다."))
        self.assertEqual(session.current_index, 1)
        self.assertIsNone(session.pending_completion_confirmation)
        # With the router, the front rules hand it on as before.
        session = self.at(1)
        self.turn += 1
        self.assertIsNone(session.front_plan(
            "아직 덜 됐는데 그냥 다음 단계 가자", turn_id=self.turn, language="ko",
            configuration_id=1, generation=1))

    def test_a_question_is_no_move(self) -> None:
        for words in ("15분이면 돼? 다음으로 가도 돼?", "10분이면 다음으로 가도 되나"):
            with self.subTest(words=words):
                session = self.at(1)
                self.say(words)
                self.assertNotEqual(session.last_front_rule, "mixed_move")
                self.assertEqual(session.current_index, 1)

    def test_the_front_rules_own_it(self) -> None:
        session = self.at(1)
        self.turn += 1
        front = session.front_plan(
            "원문이 뭐든 15분이면 되잖아, 다음으로 가", turn_id=self.turn, language="ko",
            configuration_id=1, generation=1)
        self.assertIsNotNone(front)
        self.assertIs(front.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertEqual(session.current_index, 1)


if __name__ == "__main__":
    unittest.main()
