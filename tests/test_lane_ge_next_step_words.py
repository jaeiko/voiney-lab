"""Lane GE, decision 1 (2026-10-11): words about the next step, by their form.

A question or a request to be told, read or guided to the next step describes
it and changes nothing -- "다음 단계 뭐야?", "다음 단계 알려 줘", "다음 단계로
안내해 줘" (the "로" does not make it a move; before this change it opened the
completion question). Only moving words change state: "다음", "넘어가", "다음
단계로 가자", "다음 단계로 넘어가자" complete the step and present the next one
(lane VX, decision 1), which is what the four evaluation-set rows the lane VX
report listed as "wrong state changes" (R033, R037, R191, R197) actually say.

The fixture is lane CB's headspace subset, built from text alone, so the tests
run in both pytest baselines.
"""

from __future__ import annotations

import unittest

from tests.lane_cb_support import headspace_fixture, index_of
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    forward_step_request,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

DESCRIBES = (
    "다음 단계 뭐야?",
    "다음 단계 알려 줘",
    "다음 단계 알려줘",
    "다음 단계로 안내해 줘",
    "다음 단계로 안내해 줘.",
    "다음 단계로 안내해줘",
    "다음 단계로 안내해 줄래?",
    "다음 단계로 안내해 주세요",
    "다음 단계 안내 부탁해",
    "다음 단계로 안내 부탁해요",
    "다음 단계로 알려 줘",
)
#: The four evaluation-set utterances (R033, R037, R191, R197) and the forms the
#: decision names.
MOVES = (
    "다음 단계로 넘어가자",
    "넘어가",
    "어… 음… 다음 단계로 넘어가 주세요",
    "넘어가자 넘어가",
    "다음",
    "다음 단계로 가자",
)
#: Asks permission: the question is asked, nothing moves (lane VX kept these).
ASKS_PERMISSION = ("다음 단계로 넘어가도 돼?", "다음 단계 진행할까?")


class NextStepWordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = headspace_fixture()

    def open_at(self, label: str) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        self.turn = 0
        self.say(session, "프로토콜 시작해줘")
        session.current_index = index_of(label)
        return session

    def say(self, session: CuratedProtocolSession, said: str):
        self.turn += 1
        return route_curated_runtime_turn(
            session, said, turn_id=self.turn, language="ko",
            configuration_id=1, generation=1,
        ).plan

    def test_a_description_changes_nothing_and_asks_nothing(self) -> None:
        for said in DESCRIBES:
            with self.subTest(said=said):
                session = self.open_at("3")
                at = session.current_index
                plan = self.say(session, said)
                self.assertEqual(plan.action, CuratedProtocolAction.NEXT_INFORMATION)
                self.assertEqual(plan.intent_kind, "next_step_information")
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.current_index, at)
                self.assertIsNone(session.pending_completion_confirmation)
                self.assertFalse(forward_step_request(said))

    def test_moving_words_complete_the_step_and_move_on(self) -> None:
        for said in MOVES:
            with self.subTest(said=said):
                session = self.open_at("3")
                at = session.current_index
                plan = self.say(session, said)
                self.assertTrue(forward_step_request(said))
                self.assertEqual(plan.action, CuratedProtocolAction.NEXT)
                self.assertTrue(plan.state_changed)
                self.assertEqual(session.current_index, at + 1)
                self.assertIn("완료로 기록했어요", plan.speech_text or "")

    def test_asking_permission_still_asks(self) -> None:
        for said in ASKS_PERMISSION:
            with self.subTest(said=said):
                session = self.open_at("3")
                at = session.current_index
                plan = self.say(session, said)
                self.assertEqual(plan.action, CuratedProtocolAction.DECLINE_COMPLETION)
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.current_index, at)
                self.assertIsNone(session.pending_completion_confirmation)

    def test_a_yes_after_a_description_is_not_a_completion(self) -> None:
        session = self.open_at("3")
        at = session.current_index
        self.say(session, "다음 단계로 안내해 줘")
        plan = self.say(session, "네")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.current_index, at)


if __name__ == "__main__":
    unittest.main()
