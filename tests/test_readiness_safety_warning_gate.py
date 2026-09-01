"""Absent-safety-warning readiness gate and its audited human override."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
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
    ProtocolCatalogUnavailableError,
    SharedSecretApprovalPolicy,
)

_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS
_PAGE = "Protocol Gate\nSection preparation\n1. Add solution.\nWear gloves."


def build_protocol(path: Path, protocol_id: str, *, declare_warning: bool):
    extraction = extract_protocol_pdf(path)
    evidence = lambda excerpt: domain.SourceEvidence(1, excerpt)
    instruction = "1. Add solution."
    warning_text = "Wear gloves."
    warnings = (
        (
            domain.SourceStatement(
                "glove-warning", warning_text, evidence(warning_text)
            ),
        )
        if declare_warning
        else ()
    )
    protocol = domain.ExperimentProtocol(
        protocol_id,
        domain.ProtocolMetadata(
            extraction, "Protocol Gate", "en", evidence=evidence("Protocol Gate")
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
                        instruction,
                        evidence(instruction),
                        warnings=warnings,
                    ),
                ),
            ),
        ),
    )
    return domain.validate_protocol(protocol), extraction


class DeclaredSafetyWarningReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.pdf = self.root / "gate.pdf"
        write_text_pdf(self.pdf, _PAGE, title="Protocol Gate")

    def test_zero_declared_warnings_blocks_readiness(self) -> None:
        protocol, _ = build_protocol(self.pdf, "p-1", declare_warning=False)
        assessment = domain.assess_readiness(protocol)
        self.assertEqual(domain.declared_safety_warning_count(protocol), 0)
        self.assertIs(assessment.status, domain.ReadinessStatus.ANALYSIS_REQUIRED)
        self.assertIn(_GATE.value, assessment.reason_codes)

    def test_a_declared_warning_clears_the_gate(self) -> None:
        protocol, _ = build_protocol(self.pdf, "p-1", declare_warning=True)
        assessment = domain.assess_readiness(protocol)
        self.assertEqual(domain.declared_safety_warning_count(protocol), 1)
        self.assertIs(assessment.status, domain.ReadinessStatus.GUIDANCE_READY)
        self.assertNotIn(_GATE.value, assessment.reason_codes)

    def test_action_scoped_warning_also_counts(self) -> None:
        protocol, _ = build_protocol(self.pdf, "p-1", declare_warning=False)
        step = protocol.sections[0].steps[0]
        evidence = domain.SourceEvidence(1, "Wear gloves.")
        action = domain.ProtocolSubAction(
            "action-1",
            "1. Add solution.",
            domain.SourceEvidence(1, "1. Add solution."),
            warnings=(
                domain.SourceStatement("w", "Wear gloves.", evidence),
            ),
        )
        rebuilt = replace(
            protocol,
            sections=(
                replace(
                    protocol.sections[0],
                    steps=(replace(step, sub_actions=(action,)),),
                ),
            ),
        )
        self.assertEqual(domain.declared_safety_warning_count(rebuilt), 1)
        self.assertNotIn(
            _GATE.value, domain.assess_readiness(rebuilt).reason_codes
        )

    def test_gate_is_silent_when_there_are_no_steps(self) -> None:
        """A stepless Protocol already fails; do not stack a second reason."""

        protocol, _ = build_protocol(self.pdf, "p-1", declare_warning=False)
        empty = replace(
            protocol,
            sections=(replace(protocol.sections[0], steps=()),),
        )
        codes = domain.assess_readiness(empty).reason_codes
        self.assertIn(
            domain.ReadinessReasonCode.NO_EXECUTABLE_STEPS.value, codes
        )
        self.assertNotIn(_GATE.value, codes)

    def test_gate_never_reads_the_source_document_for_hazard_wording(self) -> None:
        """The gate measures our output, not the PDF's phrasing."""

        protocol, extraction = build_protocol(
            self.pdf, "p-1", declare_warning=False
        )
        self.assertIn("Wear gloves.", extraction.pages[0].text)
        self.assertIn(_GATE.value, domain.assess_readiness(protocol).reason_codes)


class SafetyAcknowledgementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = self.root / "gate.pdf"
        write_text_pdf(self.pdf, _PAGE, title="Protocol Gate")

    def _register(self, *, declare_warning: bool):
        registration = self.catalog.register(
            self.pdf,
            source_filename="gate.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        protocol, _ = build_protocol(
            self.pdf, protocol_id, declare_warning=declare_warning
        )
        self.store.append_analysis_revision(
            protocol_id,
            1,
            "analysis-gate",
            protocol,
            domain.assess_readiness(protocol),
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        return self.catalog.get_entry(protocol_id)

    def _approve(self, entry):
        return self.catalog.approve(
            entry.protocol_id,
            entry.revision_id,
            policy=SharedSecretApprovalPolicy("review-secret"),
            presented_secret="review-secret",
        )

    def test_unacknowledged_gate_blocks_approval_and_activation(self) -> None:
        entry = self._register(declare_warning=False)
        with self.assertRaises(ProtocolApprovalError):
            self._approve(entry)
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.activate_development(entry.protocol_id)

    def test_acknowledgement_records_actor_and_time_then_unblocks(self) -> None:
        entry = self._register(declare_warning=False)
        self.catalog.acknowledge_absent_safety_warnings(
            entry.protocol_id,
            entry.revision_id,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            comment="Source carries no safety warning.",
        )
        events = [
            event
            for event in self.store.list_events(entry.protocol_id)
            if event.event_type == "protocol_safety_warnings_acknowledged"
        ]
        self.assertEqual(len(events), 1)
        recorded = events[0]
        self.assertEqual(
            recorded.payload["actor_principal_id"], "reviewer@example.org"
        )
        self.assertEqual(recorded.payload["actor_role"], "reviewer")
        self.assertEqual(recorded.payload["reason_code"], _GATE.value)
        self.assertTrue(recorded.recorded_at)
        self.assertEqual(recorded.analysis_revision_number, 1)

        approved = self._approve(entry)
        self.assertTrue(approved.available_for_execution)
        self.catalog.activate_development(entry.protocol_id)

    def test_acknowledgement_does_not_clear_any_other_reason(self) -> None:
        registration = self.catalog.register(
            self.pdf,
            source_filename="gate.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        protocol, _ = build_protocol(self.pdf, protocol_id, declare_warning=False)
        stacked = domain.ReadinessAssessment(
            status=domain.ReadinessStatus.ANALYSIS_REQUIRED,
            label=domain.ANALYSIS_REQUIRED_LABEL,
            reasons=(
                domain.ReadinessReason(
                    code=domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
                    message="Ambiguous.",
                ),
                domain.ReadinessReason(code=_GATE, message="No warning."),
            ),
        )
        self.store.append_analysis_revision(
            protocol_id,
            1,
            "analysis-gate",
            protocol,
            stacked,
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        entry = self.catalog.get_entry(protocol_id)
        self.catalog.acknowledge_absent_safety_warnings(
            entry.protocol_id,
            entry.revision_id,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        with self.assertRaises(ProtocolApprovalError):
            self._approve(entry)

    def test_acknowledging_an_ungated_analysis_is_rejected(self) -> None:
        entry = self._register(declare_warning=True)
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.acknowledge_absent_safety_warnings(
                entry.protocol_id,
                entry.revision_id,
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
            )

    def test_unidentified_or_unauthorized_actor_is_rejected(self) -> None:
        entry = self._register(declare_warning=False)
        for principal, role in (
            ("reviewer@example.org", "researcher"),
            ("", "reviewer"),
            ("bad actor", "reviewer"),
        ):
            with self.subTest(principal=principal, role=role):
                with self.assertRaises(ProtocolApprovalError):
                    self.catalog.acknowledge_absent_safety_warnings(
                        entry.protocol_id,
                        entry.revision_id,
                        actor_principal_id=principal,
                        actor_role=role,
                    )

    def test_reviewer_payload_surfaces_the_gate(self) -> None:
        entry = self._register(declare_warning=False)
        review = self.catalog.review(entry.protocol_id)
        readiness = review["readiness"]
        self.assertEqual(readiness["status"], "analysis_required")
        self.assertIn(
            _GATE.value,
            [reason["code"] for reason in readiness["reasons"]],
        )


if __name__ == "__main__":
    unittest.main()
