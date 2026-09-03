"""A repetition whose count the document hands to the experimenter.

"Repeat steps 19-20 for the required number of replicates" is not ambiguous:
the source is perfectly clear that the operator decides. Calling it an
ambiguity would misdescribe it, and a reviewer confirming a fixed count would
be inventing one. The first real provider run hit exactly this -- the model
classified it as a fixed repetition and left the count null, and the contract
refused it, because a fixed repetition without a count cannot execute.

The shape is fully determined and only the number is open, so the protocol can
become ready with it. What must not happen is a session starting the repetition
without a number: that is supplied at session start, by a named person, and
recorded. Neither the server nor a model may guess it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_claim_analysis import write_lined_pages
from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolFixture,
    CuratedProtocolSession,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_claim_analysis import (
    generate_page_evidence_segments,
)

_PAGE = (
    "Protocol Replicates",
    "Section plating",
    "1. Apply the culture to the plate.",
    "2. Spread the culture across the plate.",
    "3 Repeat steps 1-2 for the required number of replicates.",
)


def _protocol(extraction, segments, protocol_id="protocol-replicates"):
    plain = lambda text: domain.SourceEvidence(1, text)
    evidence = domain.SourceEvidence(
        source_page_number=1,
        source_excerpt=segments[0].text,
        evidence_segment_ids=(segments[0].segment_id,),
    )
    steps = tuple(
        domain.ProtocolSourceStep(
            f"step-{number}",
            str(number),
            text,
            plain(text),
        )
        for number, text in (
            (1, "1. Apply the culture to the plate."),
            (2, "2. Spread the culture across the plate."),
        )
    )
    return domain.validate_protocol(
        domain.ExperimentProtocol(
            protocol_id,
            domain.ProtocolMetadata(
                extraction,
                "Protocol Replicates",
                "en",
                evidence=plain("Protocol Replicates"),
            ),
            sections=(
                domain.ProtocolSection(
                    "plating",
                    "Section plating",
                    plain("Section plating"),
                    steps,
                ),
            ),
            constructs=(
                domain.OperatorDeterminedRepetition(
                    "repetition-1",
                    "step-1",
                    "step-2",
                    segments[0].text,
                    evidence,
                    step_id="step-2",
                ),
            ),
        )
    )


class TheShapeIsSupportedTests(unittest.TestCase):
    """The protocol may be ready: nothing about the shape is unknown."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.pdf = root / "replicates.pdf"
        write_lined_pages(self.pdf, (_PAGE,))
        self.extraction = extract_protocol_pdf(self.pdf)
        self.segments = generate_page_evidence_segments(
            self.extraction, source_revision="pdf-1", page_number=1
        )
        self.protocol = _protocol(self.extraction, self.segments)

    def test_the_policy_supports_the_shape(self) -> None:
        self.assertIn(
            domain.FeatureCode.OPERATOR_DETERMINED_REPETITION,
            domain.P1_CAPABILITY_POLICY.supported_features,
        )

    def test_it_raises_no_unsupported_reason(self) -> None:
        readiness = domain.assess_readiness(self.protocol)
        self.assertNotIn(
            domain.ReadinessReasonCode
            .UNSUPPORTED_OPERATOR_DETERMINED_REPETITION.value,
            readiness.reason_codes,
        )
        self.assertNotIn(
            domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL.value,
            readiness.reason_codes,
        )

    def test_it_needs_no_reviewer_count_confirmation(self) -> None:
        """There is no count to confirm; inventing one is the failure."""

        readiness = domain.assess_readiness(self.protocol)
        self.assertNotIn(
            domain.ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION.value,
            readiness.reason_codes,
        )

    def test_it_is_detected_as_its_own_feature(self) -> None:
        codes = {
            feature.code
            for feature in domain._detect_features(self.protocol)
        }
        self.assertIn(
            domain.FeatureCode.OPERATOR_DETERMINED_REPETITION, codes
        )


class TheSessionWillNotStartItUnnumberedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.pdf = root / "replicates.pdf"
        write_lined_pages(self.pdf, (_PAGE,))
        self.extraction = extract_protocol_pdf(self.pdf)
        self.segments = generate_page_evidence_segments(
            self.extraction, source_revision="pdf-1", page_number=1
        )
        protocol = _protocol(self.extraction, self.segments)
        from voice_workflow_agent.experiment_protocol_analysis import (
            ProtocolAnalysisDraft,
        )

        draft = ProtocolAnalysisDraft(
            extraction=self.extraction,
            protocol=protocol,
            readiness=domain.assess_readiness(protocol),
            capability_policy=domain.P1_CAPABILITY_POLICY,
            analysis_schema_version=1,
            verified_evidence_count=0,
        )
        self.session = CuratedProtocolSession(
            CuratedProtocolFixture(
                draft=draft,
                status="fictional_non_operational",
                ordered_step_labels=("1", "2"),
                fixture_sha256="0" * 64,
                revision_id="replicates-1",
                development_only=True,
                source_filename=self.extraction.original_filename,
            )
        )

    def test_it_starts_awaiting_a_count(self) -> None:
        self.assertEqual(
            self.session.repetitions_awaiting_a_count(), ("repetition-1",)
        )
        self.assertFalse(self.session.may_begin_step("step-1"))
        self.assertFalse(self.session.may_begin_step("step-2"))

    def test_a_step_outside_the_repetition_is_unaffected(self) -> None:
        self.assertTrue(self.session.may_begin_step("step-9"))

    def test_supplying_the_count_unblocks_it(self) -> None:
        self.session.provide_operator_repetition_count(
            "repetition-1",
            4,
            actor_principal_id="operator@example.org",
            actor_role="operator",
        )
        self.assertEqual(self.session.repetitions_awaiting_a_count(), ())
        self.assertTrue(self.session.may_begin_step("step-1"))

    def test_the_supplied_count_records_who_and_when(self) -> None:
        self.session.provide_operator_repetition_count(
            "repetition-1",
            4,
            actor_principal_id="operator@example.org",
            actor_role="operator",
        )
        record = self.session.operator_repetition_counts()["repetition-1"]
        self.assertEqual(record["count"], 4)
        self.assertEqual(record["actor_principal_id"], "operator@example.org")
        self.assertEqual(record["actor_role"], "operator")
        self.assertTrue(record["recorded_at"])

    def test_a_nonsense_count_is_refused(self) -> None:
        for count in (0, -1, True, "four"):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    self.session.provide_operator_repetition_count(
                        "repetition-1",
                        count,
                        actor_principal_id="operator@example.org",
                        actor_role="operator",
                    )
        self.assertFalse(self.session.may_begin_step("step-1"))

    def test_an_unnamed_actor_is_refused(self) -> None:
        for principal, role in (("", "operator"), ("someone", "")):
            with self.subTest(principal=principal, role=role):
                with self.assertRaises(ValueError):
                    self.session.provide_operator_repetition_count(
                        "repetition-1",
                        4,
                        actor_principal_id=principal,
                        actor_role=role,
                    )

    def test_an_unknown_repetition_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.session.provide_operator_repetition_count(
                "repetition-404",
                4,
                actor_principal_id="operator@example.org",
                actor_role="operator",
            )

    def test_nothing_defaults_the_count(self) -> None:
        """No number appears from anywhere until a person supplies one."""

        self.assertEqual(self.session.operator_repetition_counts(), {})
        self.assertFalse(self.session.may_begin_step("step-1"))


if __name__ == "__main__":
    unittest.main()
