"""A provider's explicit "no claim here" must survive to the reviewer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_catalog import write_text_pdf
from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    ProtocolPersistenceSettings,
    ProtocolSerializationError,
    deserialize_analysis,
    initialize_protocol_store,
    serialize_analysis,
)
from voice_workflow_agent.protocol_catalog import ProtocolCatalog

_PAGE = (
    "Protocol Declined\nSection preparation\n1. Add solution.\nWear gloves."
)
_COVERAGE = {
    "source_revision": "pdf-1",
    "source_sha256": "a" * 64,
    "source_page_number": 1,
    "page_text_sha256": "b" * 64,
    "status": "complete",
    "evidence_item_ids": ["action-1"],
    "declined_segment_ids": ["seg-hazard-4", "seg-hazard-5"],
    "unaccounted_segment_ids": [],
    "non_step_labels": [],
}


class AnalysisPayloadCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.pdf = self.root / "declined.pdf"
        write_text_pdf(self.pdf, _PAGE, title="Protocol Declined")
        extraction = extract_protocol_pdf(self.pdf)
        evidence = lambda excerpt: domain.SourceEvidence(1, excerpt)
        self.protocol = domain.ExperimentProtocol(
            "protocol-declined",
            domain.ProtocolMetadata(
                extraction,
                "Protocol Declined",
                "en",
                evidence=evidence("Protocol Declined"),
            ),
            sections=(
                domain.ProtocolSection(
                    "preparation",
                    "Section preparation",
                    evidence("Section preparation"),
                    (
                        domain.ProtocolSourceStep(
                            "step-1",
                            "1",
                            "1. Add solution.",
                            evidence("1. Add solution."),
                            warnings=(
                                domain.SourceStatement(
                                    "glove-warning",
                                    "Wear gloves.",
                                    evidence("Wear gloves."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )
        self.readiness = domain.assess_readiness(self.protocol)

    def test_page_coverage_round_trips_through_the_payload(self) -> None:
        payload, _ = serialize_analysis(
            self.protocol,
            self.readiness,
            domain.P1_CAPABILITY_POLICY.profile_id,
            (_COVERAGE,),
        )
        *_, coverage = deserialize_analysis(payload)
        self.assertEqual(len(coverage), 1)
        self.assertEqual(
            coverage[0]["declined_segment_ids"],
            ["seg-hazard-4", "seg-hazard-5"],
        )

    def test_an_analysis_stored_before_coverage_existed_still_reads(self) -> None:
        payload, _ = serialize_analysis(
            self.protocol,
            self.readiness,
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        self.assertNotIn("page_coverage", payload)
        *_, coverage = deserialize_analysis(payload)
        self.assertEqual(coverage, ())

    def test_a_malformed_coverage_record_is_refused(self) -> None:
        for broken in (
            {**_COVERAGE, "declined_segment_ids": "seg-hazard-4"},
            {**_COVERAGE, "source_page_number": "1"},
            {key: value for key, value in _COVERAGE.items() if key != "status"},
        ):
            with self.subTest(broken=sorted(broken)):
                with self.assertRaises(ProtocolSerializationError):
                    serialize_analysis(
                        self.protocol,
                        self.readiness,
                        domain.P1_CAPABILITY_POLICY.profile_id,
                        (broken,),
                    )

    def test_declinations_reach_the_review_payload(self) -> None:
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.addCleanup(store.close)
        catalog = ProtocolCatalog(store)
        registration = catalog.register(
            self.pdf,
            source_filename="declined.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        from dataclasses import replace

        store.append_analysis_revision(
            protocol_id,
            1,
            "analysis-declined",
            replace(self.protocol, protocol_id=protocol_id),
            self.readiness,
            domain.P1_CAPABILITY_POLICY.profile_id,
            (_COVERAGE,),
        )
        review = catalog.review(protocol_id)
        self.assertEqual(review["declined_segment_count"], 2)
        self.assertEqual(
            review["page_coverage"][0]["declined_segment_ids"],
            ["seg-hazard-4", "seg-hazard-5"],
        )
        self.assertEqual(review["page_coverage"][0]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
