"""Answering the completion prompt at a repeat-anchor step must not crash.

At a step the source states a repeat at -- in-gel's 7, 9 and 20 -- saying
"다음" opens a completion confirmation, and the operator answers it with 네 or
아니요. ``plan`` then reaches a guard that reads ``stale_observation_reply``,
a name only the final ``else`` of the pending-reply chain binds, so the answer
raised UnboundLocalError instead of advancing or declining.

The guard is reached only when all of these hold, which is why the crash is
confined to those three steps: the session is active, the current step anchors
a repetition, and the intent did not already report an observation. Three
branches of the chain satisfy the third without binding the name: the two
completion-confirmation branches, which set ``reported_completion`` and leave
``reported_observation`` alone, and the pending-reply language-mismatch branch
-- a short Latin-letter reply such as "hmm" in a Korean session while the
prompt is outstanding -- which re-arms the prompt and asks again.

Needs the externally licensed source PDF (README, "Two pytest baselines"), so
it guards itself rather than failing where the file is absent.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

#: The labels in-gel states a repeat at. Asserted against the fixture below, so
#: a change in the document is a visible failure rather than a silent pass.
REPEAT_ANCHOR_LABELS = ("7", "9", "20")


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class CompletionReplyAtRepeatAnchorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def _index_of(self, label: str) -> int:
        for index, step in enumerate(self.fixture.steps):
            if step.source_label == label:
                return index
        raise AssertionError(f"step {label} is absent from the fixture")

    def test_the_three_labels_are_the_repeat_anchors(self):
        """Guard the guard: if these stop anchoring repeats, the rest proves nothing."""

        session = CuratedProtocolSession(self.fixture)
        anchors = session._steps_anchoring_a_repetition()
        labels = {
            step.source_label
            for step in self.fixture.steps
            if step.step_id in anchors
        }
        self.assertEqual(labels, set(REPEAT_ANCHOR_LABELS))

    def test_affirmative_reply_opens_the_observation_gate(self):
        """네 answers the completion prompt and is met by the endpoint gate.

        It deliberately does not advance. At a step the source states a repeat
        at, the observation is what releases the step, so confirming completion
        opens the second prompt rather than moving on.
        """

        for label in REPEAT_ANCHOR_LABELS:
            with self.subTest(step=label, reply="네"):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = self._index_of(label)
                before = session.current_index
                session.plan("다음", turn_id=1, language="ko")
                self.assertIsNotNone(session.pending_completion_confirmation)

                answered = session.plan("네", turn_id=2, language="ko")

                self.assertEqual(
                    answered.action, CuratedProtocolAction.CLARIFY_COMPLETION
                )
                self.assertEqual(
                    answered.intent_kind, "observation_confirmation_required"
                )
                self.assertFalse(answered.state_changed)
                self.assertEqual(session.current_index, before)
                self.assertIsNotNone(session.pending_observation_confirmation)

    def test_answering_the_observation_gate_advances_exactly_one_step(self):
        """The whole sequence the crash interrupted, end to end."""

        for label in REPEAT_ANCHOR_LABELS:
            with self.subTest(step=label):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = self._index_of(label)
                before = session.current_index
                session.plan("다음", turn_id=1, language="ko")
                session.plan("네", turn_id=2, language="ko")

                released = session.plan("네", turn_id=3, language="ko")

                self.assertEqual(released.action, CuratedProtocolAction.NEXT)
                self.assertEqual(released.intent_kind, "pending_observation_confirmed")
                self.assertTrue(released.state_changed)
                self.assertEqual(session.current_index, before + 1)
                self.assertIsNone(session.pending_observation_confirmation)

    def test_negative_reply_declines_without_moving(self):
        for label in REPEAT_ANCHOR_LABELS:
            with self.subTest(step=label, reply="아니요"):
                session = CuratedProtocolSession(self.fixture)
                session.active = True
                session.current_index = self._index_of(label)
                before = session.current_index
                session.plan("다음", turn_id=1, language="ko")

                answered = session.plan("아니요", turn_id=2, language="ko")

                self.assertEqual(
                    answered.action, CuratedProtocolAction.DECLINE_COMPLETION
                )
                self.assertEqual(answered.intent_kind, "pending_completion_declined")
                self.assertFalse(answered.state_changed)
                self.assertEqual(session.current_index, before)
                self.assertIsNone(session.pending_completion_confirmation)

    def test_unreliable_short_reply_keeps_the_completion_prompt(self):
        """A Latin-letter non-answer in a Korean session is asked again.

        It is neither a yes nor a no, so it must not advance or decline; the
        prompt is re-armed for the next turn so the operator can still answer it.
        """

        for label in REPEAT_ANCHOR_LABELS:
            for reply in ("hmm", "ok", "wait"):
                with self.subTest(step=label, reply=reply):
                    session = CuratedProtocolSession(self.fixture)
                    session.active = True
                    session.current_index = self._index_of(label)
                    before = session.current_index
                    session.plan("다음", turn_id=1, language="ko")

                    answered = session.plan(reply, turn_id=2, language="ko")

                    self.assertEqual(
                        answered.action, CuratedProtocolAction.TRANSCRIPT_UNRELIABLE
                    )
                    self.assertEqual(
                        answered.intent_kind, "pending_reply_transcript_unreliable"
                    )
                    self.assertFalse(answered.state_changed)
                    self.assertEqual(session.current_index, before)
                    pending = session.pending_completion_confirmation
                    self.assertIsNotNone(pending)
                    self.assertEqual(pending.requested_turn_id, 2)


if __name__ == "__main__":
    unittest.main()
