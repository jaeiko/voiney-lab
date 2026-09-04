"""Only a person may say a numbered line is not an execution step.

On the first real use of the provider-facing version of this judgement, a model
disposed of six numbered lines on headspace pages 6-8 -- labels 22, 30, 31, 32,
33, 34 -- every one an imperative carrying a temperature, a time or a piece of
equipment. Approved, the protocol would have been missing six steps and nothing
in the extraction would have said so.

The field is gone from the provider contract, so a model has no means to make
that judgement and therefore cannot make it wrongly. Every numbered line is an
execution step as far as extraction is concerned; treating a description as a
step only costs an operator hearing a description read out.

A reviewer may still record the finding, with the same provenance as any other
and revocable -- and it clears nothing, so it cannot become a route around the
obligation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_catalog import write_text_pdf
from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    ProtocolPersistenceSettings,
    initialize_protocol_store,
)
from voice_workflow_agent.protocol_catalog import (
    ProtocolApprovalError,
    ProtocolCatalog,
)
from voice_workflow_agent.protocol_claim_analysis import (
    generate_page_evidence_segments,
)

_PAGE = (
    "Protocol Labels\nSection preparation\n1. Wash the pellet.\n"
    "2 Buffer contains sodium chloride.\n"
)
_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value


class TheModelCannotMakeThisJudgementTests(unittest.TestCase):
    def test_the_response_schema_has_no_field_for_it(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            CLAIM_RESPONSE_SCHEMA,
        )

        coverage = CLAIM_RESPONSE_SCHEMA["properties"]["page_coverage"][
            "items"
        ]
        self.assertNotIn("non_step_labels", coverage["properties"])
        self.assertNotIn("non_step_labels", coverage["required"])

    def test_the_prompt_no_longer_asks_for_it(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            CLAIM_ANALYSIS_SYSTEM_PROMPT,
        )

        prompt = " ".join(CLAIM_ANALYSIS_SYSTEM_PROMPT.split())
        self.assertNotIn("non_step_labels", prompt)
        self.assertIn(
            "Every numbered label on a core page is an execution step", prompt
        )

    def test_the_protocol_carries_no_disposition_collection(self) -> None:
        from dataclasses import fields

        self.assertNotIn(
            "label_dispositions",
            [field.name for field in fields(domain.ExperimentProtocol)],
        )

    def test_no_readiness_gate_stands_over_an_empty_collection(self) -> None:
        self.assertFalse(
            hasattr(
                domain.ReadinessReasonCode, "UNCONFIRMED_LABEL_DISPOSITION"
            )
        )


class AReviewerStillMayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.pdf = self.root / "labels.pdf"
        write_text_pdf(self.pdf, _PAGE, title="Protocol Labels")
        self.extraction = extract_protocol_pdf(self.pdf)
        self.segments = generate_page_evidence_segments(
            self.extraction, source_revision="pdf-1", page_number=1
        )
        self.handles = (self.segments[0].segment_id,)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        registration = self.catalog.register(
            self.pdf,
            source_filename="labels.pdf",
            media_type="application/pdf",
        )
        self.protocol_id = registration.entry.protocol_id
        plain = lambda text: domain.SourceEvidence(1, text)
        instruction = "1. Wash the pellet."
        protocol = domain.validate_protocol(
            domain.ExperimentProtocol(
                self.protocol_id,
                domain.ProtocolMetadata(
                    self.extraction,
                    "Protocol Labels",
                    "en",
                    evidence=plain("Protocol Labels"),
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
                                instruction,
                                plain(instruction),
                            ),
                        ),
                    ),
                ),
            )
        )
        self.store.append_analysis_revision(
            self.protocol_id,
            1,
            "analysis-labels",
            protocol,
            domain.assess_readiness(protocol),
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        self.revision_id = "pdf-1-analysis-1"

    def _record(self, **overrides):
        return self.catalog.confirm_label_disposition(
            self.protocol_id,
            self.revision_id,
            source_page_number=overrides.pop("source_page_number", 1),
            source_label=overrides.pop("source_label", "2"),
            evidence_segment_ids=overrides.pop(
                "evidence_segment_ids", self.handles
            ),
            actor_principal_id=overrides.pop(
                "actor_principal_id", "reviewer@example.org"
            ),
            actor_role=overrides.pop("actor_role", "reviewer"),
            **overrides,
        )

    def test_a_reviewer_finding_is_recorded_with_its_provenance(self) -> None:
        self._record(comment="Numbered line describes a buffer.")
        events = [
            event
            for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_label_disposition_confirmed"
        ]
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["source_label"], "2")
        self.assertEqual(payload["source_page_number"], 1)
        self.assertEqual(payload["actor_principal_id"], "reviewer@example.org")
        self.assertEqual(payload["actor_role"], "reviewer")
        self.assertEqual(payload["evidence_segment_ids"], list(self.handles))
        self.assertTrue(events[0].recorded_at)

    def test_it_is_revocable_and_the_earlier_finding_survives(self) -> None:
        self._record()
        self.assertEqual(
            self.catalog.label_disposition_findings(
                self.protocol_id, self.revision_id
            ),
            frozenset({"1:2"}),
        )
        self.catalog.revoke_label_disposition_confirmation(
            self.protocol_id,
            self.revision_id,
            source_page_number=1,
            source_label="2",
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        self.assertEqual(
            self.catalog.label_disposition_findings(
                self.protocol_id, self.revision_id
            ),
            frozenset(),
        )
        kinds = [
            event.event_type
            for event in self.store.list_events(self.protocol_id)
            if "label_disposition" in event.event_type
        ]
        self.assertEqual(
            kinds,
            [
                "protocol_label_disposition_confirmed",
                "protocol_label_disposition_confirmation_revoked",
            ],
        )

    def test_a_label_the_document_does_not_print_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._record(source_label="9")

    def test_a_page_outside_the_source_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._record(source_page_number=7)

    def test_a_citation_that_does_not_resolve_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._record(evidence_segment_ids=("s-not-a-real-handle",))

    def test_a_finding_must_cite_something(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._record(evidence_segment_ids=())

    def test_an_unidentified_actor_is_refused(self) -> None:
        for principal, role in (("", "reviewer"), ("someone", "")):
            with self.subTest(principal=principal, role=role):
                with self.assertRaises(ProtocolApprovalError):
                    self._record(
                        actor_principal_id=principal, actor_role=role
                    )

    def test_the_finding_clears_nothing(self) -> None:
        """It must not become a route around the numbered-action obligation."""

        self._record()
        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertIn(_GATE, analysis.readiness.reason_codes)
        self.assertFalse(
            self.catalog._readiness_gates_cleared(self.protocol_id, 1, analysis)
        )

    def test_the_stored_analysis_is_not_rewritten(self) -> None:
        before = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self._record()
        after = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertEqual(before.payload_sha256, after.payload_sha256)


if __name__ == "__main__":
    unittest.main()
