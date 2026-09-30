"""Digit and English step numbers past 25 name that step, not a neighbour.

The digit form ("26단계") and English "step 26" were matched by a range that
stopped at 25, so "26단계 완료했어" was not a numbered completion at all. It
fell through to a related question and was answered with the current step's
text. The spoken Korean form already reaches 99 (test_spoken_step_numerals).

The fixture is fictional, built in memory with 99 steps, so these tests run
with or without the externally licensed source PDF.
"""

from __future__ import annotations

import hashlib
import unittest

from voiney_lab import experiment_protocol as domain
from voiney_lab.completion_intent import resolve_korean_completion_decision
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolFixture,
    CuratedProtocolSession,
    classify_curated_control_intent,
)
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisDraft
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)

STEP_COUNT = 99

#: Completion reports that name a step, and the step each one names.
NUMBERED_COMPLETIONS = {
    "26단계 완료했어": 26,
    "67단계 완료했어": 67,
    "99단계 완료했어": 99,
    "이번 26단계 완료": 26,
    "step 26 is done": 26,
    "step 67 done": 67,
    "I completed step 99": 99,
}
#: Three-digit numbers, which a two-digit range must not cut short to 12 or 10.
THREE_DIGIT = (
    "100단계 완료했어",
    "126단계 완료했어",
    "step 126 is done",
    "126단계 뭐야",
    "126단계 미리 알려줘",
)


def _fixture() -> CuratedProtocolFixture:
    """One fictional 99-step protocol with a step for every number above."""

    title = "Fictional labelling protocol."
    lines = [
        f"{number}. Label sample tube {number} with the marker."
        for number in range(1, STEP_COUNT + 1)
    ]
    page = "\n".join([title, *lines])
    extraction = ProtocolPdfExtraction(
        original_filename="fictional-step-number-protocol.pdf",
        byte_size=len(page.encode("utf-8")),
        sha256=hashlib.sha256(page.encode("utf-8")).hexdigest(),
        media_type="application/pdf",
        page_count=1,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title=title,
            author="Voice Workflow Agent",
            subject="Non-operational step number fixture",
            creator="voice-workflow-tests",
            producer="voice-workflow-tests",
            creation_date=None,
            modification_date=None,
        ),
        pages=(ProtocolPdfPage(1, page, False),),
    )
    on_page = lambda text: domain.SourceEvidence(1, text)  # noqa: E731
    protocol = domain.ExperimentProtocol(
        protocol_id="fictional-step-number-protocol",
        metadata=domain.ProtocolMetadata(
            pdf=extraction,
            title=title,
            original_language="en",
            version="1.0-test",
            source_status="fictional_non_operational",
            evidence=on_page(title),
        ),
        materials=(),
        equipment=(),
        before_start=(),
        sections=(
            domain.ProtocolSection(
                "s1",
                title,
                on_page(title),
                steps=tuple(
                    domain.ProtocolSourceStep(
                        f"step-{number}", str(number), line, on_page(line)
                    )
                    for number, line in enumerate(lines, start=1)
                ),
            ),
        ),
    )
    domain.validate_protocol(protocol)
    draft = ProtocolAnalysisDraft(
        extraction=extraction,
        protocol=protocol,
        readiness=domain.assess_readiness(protocol),
        capability_policy=domain.P1_CAPABILITY_POLICY,
        analysis_schema_version=1,
        verified_evidence_count=STEP_COUNT,
    )
    return CuratedProtocolFixture(
        draft=draft,
        status="fictional_non_operational",
        ordered_step_labels=tuple(str(n) for n in range(1, STEP_COUNT + 1)),
        fixture_sha256=hashlib.sha256(b"fictional-step-number").hexdigest(),
        revision_id="step-number-fixture-v1",
        development_only=True,
        source_filename=extraction.original_filename,
    )


class DigitStepNumberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _fixture()

    def _session_at(self, label: int) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session.current_index = label - 1
        return session

    def test_the_fixture_has_every_step_the_transcripts_name(self):
        # Guard the guard: a missing step would turn a pass into a fallback.
        self.assertEqual(len(self.fixture.steps), STEP_COUNT)
        self.assertEqual(self.fixture.steps[25].source_label, "26")
        self.assertEqual(self.fixture.steps[98].source_label, "99")

    def test_a_numbered_completion_names_that_step(self):
        for transcript, step in NUMBERED_COMPLETIONS.items():
            with self.subTest(transcript=transcript):
                decision = resolve_korean_completion_decision(
                    transcript, language="ko"
                )
                self.assertTrue(decision.is_completion)
                self.assertEqual(decision.target_kind, "explicit_step")
                self.assertEqual(decision.target_step_number, step)

                intent = classify_curated_control_intent(transcript, language="ko")
                self.assertEqual(intent.action, CuratedProtocolAction.NEXT)
                self.assertTrue(intent.reported_completion)
                self.assertEqual(intent.target_step, str(step))

    def test_completing_the_named_current_step_advances_one_step(self):
        for transcript, step in NUMBERED_COMPLETIONS.items():
            with self.subTest(transcript=transcript):
                session = self._session_at(step)
                plan = session.plan(transcript, turn_id=1, language="ko")

                self.assertEqual(plan.action, CuratedProtocolAction.NEXT)
                self.assertEqual(plan.intent_kind, "report_completion")
                self.assertTrue(plan.state_changed)
                # The last step completes the run instead of moving past it.
                self.assertEqual(
                    session.current_index, min(step, STEP_COUNT - 1)
                )

    def test_naming_another_step_asks_about_the_current_one(self):
        session = self._session_at(1)
        plan = session.plan("67단계 완료했어", turn_id=1, language="ko")

        self.assertEqual(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertEqual(plan.intent_kind, "explicit_step_mismatch_clarification")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.current_index, 0)
        pending = session.pending_completion_confirmation
        self.assertIsNotNone(pending)
        self.assertEqual(pending.requested_target_step, "67")

    def test_preview_and_lookup_read_the_named_step(self):
        cases = {
            "26단계 미리 알려줘": (CuratedProtocolAction.PREVIEW_STEP, "26"),
            "preview step 67": (CuratedProtocolAction.PREVIEW_STEP, "67"),
            "step 99 미리보기": (CuratedProtocolAction.PREVIEW_STEP, "99"),
            "26단계 뭐야": (CuratedProtocolAction.FULL_DETAIL, "26"),
            "step 67 explain": (CuratedProtocolAction.FULL_DETAIL, "67"),
        }
        for transcript, (action, label) in cases.items():
            with self.subTest(transcript=transcript):
                session = self._session_at(1)
                plan = session.plan(transcript, turn_id=1, language="ko")

                self.assertEqual(plan.action, action)
                self.assertEqual(plan.target_step, label)
                self.assertIn(f"Label sample tube {label} ", plan.display_text)
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.current_index, 0)

    def test_a_three_digit_number_is_not_cut_short(self):
        for transcript in THREE_DIGIT:
            with self.subTest(transcript=transcript):
                decision = resolve_korean_completion_decision(
                    transcript, language="ko"
                )
                self.assertNotEqual(decision.target_kind, "explicit_step")

                intent = classify_curated_control_intent(transcript, language="ko")
                self.assertNotIn(intent.target_step, {"1", "10", "12", "26"})

                session = self._session_at(12)
                plan = session.plan(transcript, turn_id=1, language="ko")
                self.assertFalse(plan.state_changed)
                self.assertEqual(session.current_index, 11)


if __name__ == "__main__":
    unittest.main()
