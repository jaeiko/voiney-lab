"""Lane FX2, decision 1 (2026-10-12): a reply that says yes and no at once is asked again.

At every place the server asks a yes or no -- a source condition, the
completion question, 시끄러운 곳 mode's question, the next round of a repeat,
the endpoint, the end, the questions before the start -- a reply holding a
yes and a no ("네 아니", "응 아니야", "아니 맞아") answers neither: nothing changes, and
the question is asked once more ("해당하면 '네', 아니면 '아니요'라고 말씀해
주세요"), still open for the next words. A reply that sounds like a
correction is asked again too: both are in it. A plain yes or no -- said
once or twice, after a filler -- is read as before.

Before this, the utterance key took a leading "네"/"응" for a filler, so
"네 아니" at headspace step 42 (and EPA 9045D 7.2.1 in lane GE's small
exhaustive check) was read as "아니" and skipped the branch.

The protocol is lane CB's headspace-shaped one, built from text alone, so the
tests run in both pytest baselines.
"""

from __future__ import annotations

import unittest

from tests.lane_cb_support import CONDITION_42, index_of
from tests.lane_cf_support import Session, notes_fixture
from voiney_lab.curated_protocol import CuratedProtocolAction

ASK_AGAIN = "해당하면 '네', 아니면 '아니요'라고 말씀해 주세요."
MIXED = ("네 아니", "응 아니", "응 아니야", "아니 맞아", "예 아니요", "아니 네", "아니요 맞아요")


class MixedReplyTests(Session, unittest.TestCase):

    def assert_asked_again(self, plan, before) -> None:
        self.assertEqual(plan.intent_kind, "yes_no_reply_mixed")
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertFalse(plan.state_changed)
        self.assertTrue(plan.speech_text.endswith(ASK_AGAIN), plan.speech_text)
        self.assertEqual(self.projection(), before)
        self.assertEqual(self.session.last_front_rule, "yes_no_open_question")

    # -- a source condition (lane CB, decision 1) ---------------------------

    def at_42(self) -> None:
        self.open_with(index_of("41"))
        self.say("41단계 완료했어")
        self.assertEqual(self.label(), "42")
        self.assertEqual(self.session.open_server_question()["kind"], "branch")

    def test_a_mixed_reply_to_a_source_condition_records_nothing_and_asks_again(self) -> None:
        for said in MIXED:
            with self.subTest(said=said):
                self.at_42()
                before = self.projection()
                plan = self.say(said)
                self.assert_asked_again(plan, before)
                self.assertIn(CONDITION_42, plan.speech_text)
                self.assertEqual(self.session.branch_answers(), {})
                self.assertEqual(self.session.open_server_question()["kind"], "branch")
                # Still open: the next plain answer is taken.
                plan = self.say("아니요")
                self.assertEqual(self.session.branch_answers()["branch-42"]["answer"], "no")
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.label(), "43")

    def test_a_plain_answer_to_a_source_condition_is_read_as_before(self) -> None:
        for said, answer in (
            ("아니", "no"), ("아니 아니", "no"), ("어 아니", "no"), ("아니요", "no"),
            ("네", "yes"), ("네 네", "yes"), ("응 맞아", "yes"),
        ):
            with self.subTest(said=said):
                self.at_42()
                self.say(said)
                self.assertEqual(self.session.last_front_rule, "branch_condition")
                self.assertEqual(self.session.branch_answers()["branch-42"]["answer"], answer)

    # -- the completion question -------------------------------------------

    def asked_completion(self) -> None:
        self.open_with(index_of("13"))
        asked = self.say("완료했어")
        self.assertEqual(asked.speech_text, "13단계 완료하셨나요?")

    def test_a_mixed_reply_to_the_completion_question_completes_nothing(self) -> None:
        for said in (*MIXED, "아니 완료했어", "네 아직 안 했어", "아니 다 했어"):
            with self.subTest(said=said):
                self.asked_completion()
                before = self.projection()
                plan = self.say(said)
                self.assert_asked_again(plan, before)
                # The question is still open on the next turn.
                plan = self.say("네")
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.label(), "14")

    def test_a_plain_reply_to_the_completion_question_is_read_as_before(self) -> None:
        self.asked_completion()
        plan = self.say("아니 아직 안 했어")
        self.assertEqual(plan.intent_kind, "pending_completion_declined")
        self.asked_completion()
        plan = self.say("네 완료했어")
        self.assertEqual(plan.intent_kind, "pending_completion_confirmed")
        self.assertEqual(self.label(), "14")
        # A question beside a no is no mixed reply; it is answered as before.
        self.asked_completion()
        plan = self.say("아니 다음 단계 뭐야?")
        self.assertNotEqual(plan.intent_kind, "yes_no_reply_mixed")
        self.assertEqual(self.label(), "13")

    # -- the next round of a repeat (lane CB, decision 3) -------------------

    def test_a_mixed_reply_to_the_round_question_moves_nothing(self) -> None:
        for said in ("네 아니", "응 아니야", "아니 한 번 더", "돌아가 아니"):
            with self.subTest(said=said):
                self.open_with(index_of("12"))
                self.complete("12", "13", "14", "15", "16")
                self.assertEqual(self.session.open_server_question()["kind"], "repeat_round")
                before = self.projection()
                plan = self.say(said)
                self.assert_asked_again(plan, before)
                self.assertEqual(self.session.open_server_question()["kind"], "repeat_round")
                self.say("네")
                self.assertEqual(self.label(), "12")

    # -- 시끄러운 곳 mode's question (lane SP1, decision 3) -----------------

    def test_a_mixed_reply_to_the_noisy_mode_question_does_nothing(self) -> None:
        for said in ("네 아니", "아니 맞아"):
            with self.subTest(said=said):
                self.open_with(index_of("13"))
                self.session.apply_experimenter_settings({"ambient_mode": "noisy"})
                asked = self.say("다음")
                self.assertEqual(asked.intent_kind, "ambient_confirmation_required")
                before = self.projection()
                plan = self.say(said)
                self.assert_asked_again(plan, before)
                plan = self.say("응")
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.label(), "14")

    # -- the endpoint question at a repeat-until step (lane R7's wash) -------

    def test_a_mixed_reply_to_the_endpoint_question_records_no_observation(self) -> None:
        self.open_with(index_of("5"), fixture=notes_fixture())
        asked = self.say("5단계 완료했어")
        self.assertEqual(asked.intent_kind, "observation_confirmation_required")
        before = self.projection()
        plan = self.say("네 아니")
        self.assert_asked_again(plan, before)
        self.assertIsNone(plan.step_record)
        plan = self.say("네")
        self.assertEqual(plan.intent_kind, "pending_observation_confirmed")
        self.assertEqual(self.label(), "6")

    # -- the end question and the questions before the start ----------------

    def test_a_mixed_reply_to_the_end_question_ends_nothing(self) -> None:
        self.open_with(index_of("13"))
        self.say("실험 종료해줘")
        before = self.projection()
        plan = self.say("네 아니")
        self.assert_asked_again(plan, before)
        self.assertTrue(self.session.active)
        plan = self.say("네")
        self.assertEqual(plan.action, CuratedProtocolAction.STOP)

    def test_a_mixed_reply_before_the_start_starts_nothing(self) -> None:
        self.open_with(None, question_timing="before_start")
        self.say("프로토콜 시작해줘")
        self.say("세 번")
        self.assertEqual(self.session.open_server_question()["kind"], "prestart")
        plan = self.say("응 아니야")
        self.assertEqual(plan.intent_kind, "yes_no_reply_mixed")
        self.assertFalse(plan.state_changed)
        self.assertFalse(self.session.active)
        self.assertEqual(self.session.open_server_question()["kind"], "prestart")
        plan = self.say("아니요")
        self.assertTrue(self.session.active)
        self.assertEqual(self.session.branch_answers()["branch-42"]["answer"], "no")


if __name__ == "__main__":
    unittest.main()
