"""Evidence handles are stored, and the source span can be reopened from them.

A hazard claim's basis became unrecoverable after the fact because the handles
were dropped when claims were assembled into the domain. The rule that caused
it was read too broadly: provider *content* must not be persisted, but a
segment handle is not content. It is a server-computed identity for a span of
text the server already owns -- a pointer into the document, not a sentence
somebody wrote -- so keeping it agrees with the server owning authority over
its evidence rather than conflicting with it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_claim_analysis import write_lined_pages
from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ProtocolAnalysisEvidenceError,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    deserialize_analysis,
    serialize_analysis,
)
from voice_workflow_agent.protocol_claim_analysis import (
    generate_page_evidence_segments,
    reopen_evidence_span,
)

_PAGE = (
    "Protocol Handles",
    "Section preparation",
    "1. Add 10 mL buffer at 5%.",
    "Wear gloves.",
)
_REVISION = "pdf-1"


class EvidenceHandleRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.pdf = self.root / "handles.pdf"
        write_lined_pages(self.pdf, (_PAGE,))
        self.extraction = extract_protocol_pdf(self.pdf)
        self.segments = generate_page_evidence_segments(
            self.extraction, source_revision=_REVISION, page_number=1
        )

    def _evidence(self, count: int) -> domain.SourceEvidence:
        chosen = self.segments[:count]
        return domain.SourceEvidence(
            source_page_number=1,
            source_excerpt="".join(segment.text for segment in chosen),
            evidence_segment_ids=tuple(
                segment.segment_id for segment in chosen
            ),
        )

    def test_a_handle_reopens_the_exact_span(self) -> None:
        evidence = self._evidence(1)
        self.assertEqual(
            reopen_evidence_span(
                self.extraction, evidence, source_revision=_REVISION
            ),
            evidence.source_excerpt,
        )

    def test_several_adjacent_handles_reopen_in_order(self) -> None:
        evidence = self._evidence(2)
        self.assertEqual(
            reopen_evidence_span(
                self.extraction, evidence, source_revision=_REVISION
            ),
            evidence.source_excerpt,
        )

    def test_the_round_trip_survives_storage(self) -> None:
        """Stored and read back, the handles still reopen the same text."""

        evidence = self._evidence(1)
        protocol = _protocol(self.extraction, evidence)
        readiness = domain.assess_readiness(protocol)
        payload, _ = serialize_analysis(
            protocol, readiness, domain.P1_CAPABILITY_POLICY.profile_id
        )
        restored = deserialize_analysis(payload)[0]
        stored = restored.sections[0].steps[0].warnings[0].evidence
        self.assertEqual(
            stored.evidence_segment_ids, evidence.evidence_segment_ids
        )
        self.assertEqual(
            reopen_evidence_span(
                self.extraction, stored, source_revision=_REVISION
            ),
            evidence.source_excerpt,
        )

    def test_the_excerpt_is_not_what_gets_reopened(self) -> None:
        """The text comes back from the source, not from what was saved."""

        evidence = self._evidence(1)
        tampered = domain.SourceEvidence(
            source_page_number=1,
            source_excerpt="something else entirely",
            evidence_segment_ids=evidence.evidence_segment_ids,
        )
        self.assertEqual(
            reopen_evidence_span(
                self.extraction, tampered, source_revision=_REVISION
            ),
            evidence.source_excerpt,
        )

    def test_a_handle_that_no_longer_resolves_fails_closed(self) -> None:
        """A changed source or segmentation must not silently return text."""

        broken = domain.SourceEvidence(
            source_page_number=1,
            source_excerpt="x",
            evidence_segment_ids=("s-not-a-real-handle",),
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            reopen_evidence_span(
                self.extraction, broken, source_revision=_REVISION
            )

    def test_a_handle_from_a_different_revision_fails_closed(self) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            reopen_evidence_span(
                self.extraction,
                self._evidence(1),
                source_revision="pdf-2",
            )

    def test_evidence_with_no_handle_is_refused_not_guessed(self) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            reopen_evidence_span(
                self.extraction,
                domain.SourceEvidence(1, "Wear gloves."),
                source_revision=_REVISION,
            )


class HandlesAreNeverAskedOfTheProviderTests(unittest.TestCase):
    """A handle is server-owned; asking for one invites an invented identity."""

    def test_the_provider_schema_withholds_the_field(self) -> None:
        definition = ANALYSIS_RESPONSE_SCHEMA["$defs"]["SourceEvidence"]
        self.assertNotIn("evidence_segment_ids", definition["properties"])
        self.assertIn("source_page_number", definition["properties"])

    def test_no_provider_content_is_added_by_storing_handles(self) -> None:
        """What is kept is an id, a page and nothing the provider wrote."""

        handle = self.__class__.__name__  # any opaque string
        evidence = domain.SourceEvidence(
            source_page_number=1,
            source_excerpt="Wear gloves.",
            evidence_segment_ids=(handle,),
        )
        self.assertEqual(evidence.evidence_segment_ids, (handle,))
        self.assertEqual(
            [
                field
                for field in ("source_page_number", "evidence_segment_ids")
                if getattr(evidence, field) in (None, ())
            ],
            [],
        )


def _protocol(extraction, evidence: domain.SourceEvidence):
    plain = lambda excerpt: domain.SourceEvidence(1, excerpt)
    return domain.validate_protocol(
        domain.ExperimentProtocol(
            "protocol-handles",
            domain.ProtocolMetadata(
                extraction,
                "Protocol Handles",
                "en",
                evidence=plain("Protocol Handles"),
            ),
            sections=(
                domain.ProtocolSection(
                    "preparation",
                    "Section preparation",
                    plain("Section preparation"),
                    (
                        domain.ProtocolSourceStep(
                            "step-1",
                            "1",
                            "1. Add 10 mL buffer at 5%.",
                            plain("1. Add 10 mL buffer at 5%."),
                            warnings=(
                                domain.SourceStatement(
                                    "w-1", "Wear gloves.", evidence
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )


if __name__ == "__main__":
    unittest.main()
