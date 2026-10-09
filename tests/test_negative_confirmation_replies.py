"""More ways of saying no decline a confirmation question, and yes still confirms.

The session reads an answer to its own yes/no question with two patterns:
_NEGATIVE_COMPLETION_CONFIRMATION for the completion prompt, and the negative
branch of _binary_frame_reply. The observation prompt reads
_observation_binary_reply, whose negative branch is the same pattern.
They had drifted apart, and neither knew common replies such as 아뇨 or
아직이요. An unrecognised answer to the completion prompt fell through to the
off-topic route and the question was dropped without a decline being recorded.
The binary reply now reads the same negative pattern.

The completion prompt uses the fictional in-memory fixture from
``test_curated_answer_gating``, so it runs with or without the externally
licensed source PDF. The observation prompt needs in-gel's repeat steps and
skips where that PDF is absent.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_curated_answer_gating import _fixture
from voiney_lab.curated_protocol import (
    _AFFIRMATIVE_COMPLETION_CONFIRMATION,
    _NEGATIVE_COMPLETION_CONFIRMATION,
    CuratedProtocolAction,
    CuratedProtocolSession,
    _binary_frame_reply,
    _observation_binary_reply,
    _semantic_utterance_key,
    load_curated_protocol_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

#: Negative replies neither pattern recognised.
ADDED_NEGATIVES = (
    "아뇨",
    "아니에요",
    "아닙니다",
    "아니야",
    "아직이요",
    "아직요",
    "아직 아니에요",
    "안 했어요",
    "안 했어",
    "아직 못 했어",
    "아직 안 됐어요",
    "아직 안 끝났어요",
)
#: Negative replies only one of the two patterns recognised.
ALIGNED_NEGATIVES = (
    "아직 안 됐어",
    "no not yet",
    "아직 안 했어요",
    "아니 아직 안 끝났어",
)
#: Punctuated as speech-to-text writes them.
PUNCTUATED_NEGATIVES = ("아뇨.", "아니에요.", "no, not yet", "아니, 아직 안 끝났어.")
NEGATIVES = ADDED_NEGATIVES + ALIGNED_NEGATIVES + PUNCTUATED_NEGATIVES
AFFIRMATIVES = ("네", "예", "완료했어요")
#: The observation prompt asks whether the source endpoint was seen, so a
#: report that the work was done ("완료했어요") does not answer it; see
#: test_observation_yes_no_narrowing.
OBSERVATION_AFFIRMATIVES = ("네", "예")
#: A bare "next", and a completion report naming a step other than the
#: current one: the two ways the session asks before advancing.
#: Lane VX, decision 1: "다음" no longer asks; "완료했어" does.
QUESTION_OPENERS = ("완료했어", "2단계 완료했어")


def _answer(opener: str, reply: str):
    session = CuratedProtocolSession(_fixture("fictional-negative-reply-protocol"))
    session.active = True
    session.current_index = 0
    session.plan(opener, turn_id=1, language="ko")
    asked = session.pending_completion_confirmation is not None
    plan = session.plan(reply, turn_id=2, language="ko")
    return asked, plan, session


class NegativeReplyPatternTests(unittest.TestCase):
    def test_both_patterns_read_every_negative_as_negative(self):
        # The completion prompt accepts either pattern, so the session tests
        # below would still pass if one of them missed a reply. The
        # observation prompt reads only its own binary reply.
        for reply in NEGATIVES:
            with self.subTest(reply=reply):
                key = _semantic_utterance_key(reply)
                self.assertIsNotNone(_NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(key))
                self.assertEqual(_binary_frame_reply(reply), "negative")
                self.assertEqual(_observation_binary_reply(reply), "negative")

    def test_affirmatives_are_still_affirmative(self):
        for reply in AFFIRMATIVES:
            with self.subTest(reply=reply):
                key = _semantic_utterance_key(reply)
                self.assertIsNotNone(_AFFIRMATIVE_COMPLETION_CONFIRMATION.fullmatch(key))
                self.assertIsNone(_NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(key))
                self.assertEqual(_binary_frame_reply(reply), "affirmative")


class CompletionPromptReplyTests(unittest.TestCase):
    def test_a_negative_declines_without_moving(self):
        for opener in QUESTION_OPENERS:
            for reply in NEGATIVES:
                with self.subTest(opener=opener, reply=reply):
                    asked, plan, session = _answer(opener, reply)

                    # Guard the guard: the reply met an outstanding question.
                    self.assertTrue(asked)
                    self.assertEqual(plan.action, CuratedProtocolAction.DECLINE_COMPLETION)
                    self.assertEqual(plan.intent_kind, "pending_completion_declined")
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(session.current_index, 0)
                    self.assertIsNone(session.pending_completion_confirmation)

    def test_an_affirmative_still_advances_one_step(self):
        for opener in QUESTION_OPENERS:
            for reply in AFFIRMATIVES:
                with self.subTest(opener=opener, reply=reply):
                    asked, plan, session = _answer(opener, reply)

                    self.assertTrue(asked)
                    self.assertEqual(plan.action, CuratedProtocolAction.NEXT)
                    self.assertEqual(plan.intent_kind, "pending_completion_confirmed")
                    self.assertTrue(plan.state_changed)
                    self.assertEqual(session.current_index, 1)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class ObservationPromptReplyTests(unittest.TestCase):
    """At in-gel's repeat steps the endpoint prompt reads the binary reply."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)
        cls.index = {step.source_label: i for i, step in enumerate(cls.fixture.steps)}

    def _answer_observation_prompt(self, label: str, reply: str):
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = self.index[label]
        session.plan("다음", turn_id=1, language="ko")
        opened = session.plan("네", turn_id=2, language="ko")
        # Guard the guard: the reply meets the observation prompt.
        self.assertEqual(opened.intent_kind, "observation_confirmation_required")
        self.assertIsNotNone(session.pending_observation_confirmation)
        return session.plan(reply, turn_id=3, language="ko"), session

    def test_a_negative_holds_the_step(self):
        for label in ("7", "9", "20"):
            for reply in NEGATIVES:
                with self.subTest(step=label, reply=reply):
                    plan, session = self._answer_observation_prompt(label, reply)

                    self.assertEqual(plan.intent_kind, "pending_observation_confirmed")
                    self.assertEqual(plan.observation_predicate, "negative")
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(session.current_index, self.index[label])

    def test_an_affirmative_still_releases_the_step(self):
        for label in ("7", "9", "20"):
            for reply in OBSERVATION_AFFIRMATIVES:
                with self.subTest(step=label, reply=reply):
                    plan, session = self._answer_observation_prompt(label, reply)

                    self.assertEqual(plan.intent_kind, "pending_observation_confirmed")
                    self.assertEqual(plan.observation_predicate, "positive")
                    self.assertTrue(plan.state_changed)
                    self.assertEqual(session.current_index, self.index[label] + 1)


if __name__ == "__main__":
    unittest.main()
