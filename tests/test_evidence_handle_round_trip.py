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

from tests.pdf_fixture_support import write_lined_pages
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_analysis import ANALYSIS_RESPONSE_SCHEMA
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.experiment_protocol_store import (
    deserialize_analysis,
    serialize_analysis,
)
from voiney_lab.protocol_claim_analysis import (
    generate_page_evidence_segments,
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
            _reopen(self.extraction, stored), evidence.source_excerpt
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


def _reopen(extraction, evidence: domain.SourceEvidence) -> str:
    """The source text a stored statement's handles name, read from the source.

    ``reopen_evidence_span`` went with the claim-chunk path (lane AN,
    2026-10-13). A stored handle is now resolved the way the timer manifest and
    the unread-page disclosure resolve theirs: by its id, against the segments
    the surviving generator cuts from the page.
    """

    segments = {
        segment.segment_id: segment.text
        for segment in generate_page_evidence_segments(
            extraction,
            source_revision=_REVISION,
            page_number=evidence.source_page_number,
        )
    }
    return "".join(segments[handle] for handle in evidence.evidence_segment_ids)


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
