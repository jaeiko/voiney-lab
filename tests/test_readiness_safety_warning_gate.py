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
_PAGE = (
    "Protocol Gate\nSection preparation\n1. Add solution.\n"
    "Wear gloves.\nDanger, highly corrosive."
)


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

    def test_a_provider_warning_does_not_clear_the_gate(self) -> None:
        """The defect this gate had, stated as a test.

        A warning in this Protocol is a warning the provider produced, so a
        non-zero count records that a model called something a hazard, never
        that the document declares one. One such claim used to take readiness
        from analysis_required to guidance_ready, so the model's own output
        waived the human review the gate exists to compel.
        """

        protocol, _ = build_protocol(self.pdf, "p-1", declare_warning=True)
        assessment = domain.assess_readiness(protocol)
        self.assertEqual(domain.declared_safety_warning_count(protocol), 1)
        self.assertIs(assessment.status, domain.ReadinessStatus.ANALYSIS_REQUIRED)
        self.assertIn(_GATE.value, assessment.reason_codes)

    def test_the_gate_does_not_depend_on_the_count_either_way(self) -> None:
        for declared in (False, True):
            protocol, _ = build_protocol(
                self.pdf, "p-1", declare_warning=declared
            )
            with self.subTest(declared=declared):
                self.assertIn(
                    _GATE.value, domain.assess_readiness(protocol).reason_codes
                )

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
        # The count still includes action-scoped warnings, because it is
        # reported for review. It just no longer opens anything.
        self.assertEqual(domain.declared_safety_warning_count(rebuilt), 1)
        self.assertIn(
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

    def _register(self, *, declare_warning: bool, with_steps: bool = True):
        registration = self.catalog.register(
            self.pdf,
            source_filename="gate.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        protocol, _ = build_protocol(
            self.pdf, protocol_id, declare_warning=declare_warning
        )
        if not with_steps:
            protocol = domain.validate_protocol(
                replace(
                    protocol,
                    sections=(replace(protocol.sections[0], steps=()),),
                )
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
        self.catalog.acknowledge_readiness_gate(
            entry.protocol_id,
            entry.revision_id,
            reason_code=_GATE.value,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            comment="Source carries no safety warning.",
        )
        events = [
            event
            for event in self.store.list_events(entry.protocol_id)
            if event.event_type == "protocol_readiness_gate_acknowledged"
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
        self.catalog.acknowledge_readiness_gate(
            entry.protocol_id,
            entry.revision_id,
            reason_code=_GATE.value,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        with self.assertRaises(ProtocolApprovalError):
            self._approve(entry)

    def test_acknowledging_an_ungated_analysis_is_rejected(self) -> None:
        """A stepless analysis carries no safety gate, so there is none to clear.

        This used to use a Protocol that declared a warning, because a warning
        cleared the gate. It no longer does.
        """

        entry = self._register(declare_warning=True, with_steps=False)
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.acknowledge_readiness_gate(
                entry.protocol_id,
                entry.revision_id,
                reason_code=_GATE.value,
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
                    self.catalog.acknowledge_readiness_gate(
                        entry.protocol_id,
                        entry.revision_id,
                        reason_code=_GATE.value,
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


class HazardReviewSignalTests(unittest.TestCase):
    """The reviewer hazard signal must not depend on hazard wording."""

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
        self._analyses = 0

    def _review(self, warning_text: str | None):
        registration = self.catalog.register(
            self.pdf,
            source_filename="gate.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        protocol, _ = build_protocol(
            self.pdf, protocol_id, declare_warning=False
        )
        if warning_text is not None:
            step = protocol.sections[0].steps[0]
            statement = domain.SourceStatement(
                "declared-warning",
                warning_text,
                domain.SourceEvidence(1, warning_text),
            )
            protocol = replace(
                protocol,
                sections=(
                    replace(
                        protocol.sections[0],
                        steps=(replace(step, warnings=(statement,)),),
                    ),
                ),
            )
        self._analyses += 1
        self.store.append_analysis_revision(
            protocol_id,
            1,
            f"analysis-gate-{self._analyses}",
            protocol,
            domain.assess_readiness(protocol),
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        return self.catalog.review(protocol_id)

    def test_innocuous_wording_still_requires_hazard_review(self) -> None:
        """The retired word list contained none of these terms."""

        review = self._review("Wear gloves.")
        self.assertTrue(review["hazard_review_required"])
        self.assertEqual(review["gates"]["hazard_review"], "review_required")
        self.assertEqual(review["declared_safety_warning_count"], 1)

    def test_alarming_wording_is_treated_identically(self) -> None:
        review = self._review("Danger, highly corrosive.")
        self.assertEqual(review["gates"]["hazard_review"], "review_required")
        self.assertEqual(review["declared_safety_warning_count"], 1)

    def test_zero_warnings_is_never_reported_as_passed(self) -> None:
        """The inverted case: worse extraction must not look safer.

        Zero warnings used to report a bespoke "not_declared", which read as a
        finished gate for exactly the case that most needs a reviewer. It now
        reports the same "review_required" as any other count, because the
        reviewer's job is the same either way.
        """

        review = self._review(None)
        self.assertEqual(review["declared_safety_warning_count"], 0)
        self.assertEqual(review["gates"]["hazard_review"], "review_required")
        self.assertNotEqual(review["gates"]["hazard_review"], "passed")
        self.assertIn(
            _GATE.value,
            [reason["code"] for reason in review["readiness"]["reasons"]],
        )

    def test_declared_count_matches_the_domain_helper(self) -> None:
        for text in ("Wear gloves.", "Danger, highly corrosive.", None):
            with self.subTest(text=text):
                review = self._review(text)
                self.assertEqual(
                    review["declared_safety_warning_count"],
                    0 if text is None else 1,
                )


if __name__ == "__main__":
    unittest.main()


class ProviderClaimsNeverOpenTheGateTests(unittest.TestCase):
    """Both directions of the fix, through the catalog rather than the domain.

    The measured defect: `warning_hazard` claim -> `step.warnings` ->
    `declared_safety_warning_count` non-zero -> the gate absent -> readiness
    `guidance_ready`. One provider claim was enough to waive the human review
    the gate exists to compel, and on the response actually measured the only
    warning-shaped text on those pages was a note about analysis software
    crashing -- no chemical, thermal or physical hazard at all.

    No hazard vocabulary is involved in the fix. What counts as a hazard is
    still the provider's judgement; whether this Protocol may execute on that
    judgement is now a person's.
    """

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
        registration = self.catalog.register(
            self.pdf,
            source_filename="gate.pdf",
            media_type="application/pdf",
        )
        self.protocol_id = registration.entry.protocol_id
        protocol, _ = build_protocol(
            self.pdf, self.protocol_id, declare_warning=True
        )
        self.protocol = protocol
        self.store.append_analysis_revision(
            self.protocol_id,
            1,
            "analysis-gate",
            protocol,
            domain.assess_readiness(protocol),
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        self.entry = self.catalog.get_entry(self.protocol_id)

    def test_a_provider_hazard_claim_alone_leaves_the_gate_shut(self) -> None:
        self.assertEqual(
            domain.declared_safety_warning_count(self.protocol), 1
        )
        review = self.catalog.review(self.entry.protocol_id)
        codes = [r["code"] for r in review["readiness"]["reasons"]]
        self.assertIn(_GATE.value, codes)
        self.assertEqual(review["declared_safety_warning_count"], 1)
        self.assertTrue(review["hazard_review_required"])

    def test_it_blocks_approval_while_a_warning_is_declared(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.approve(
                self.entry.protocol_id,
                self.entry.revision_id,
                policy=SharedSecretApprovalPolicy("review-secret"),
                presented_secret="review-secret",
            )

    def test_an_audited_human_confirmation_opens_it(self) -> None:
        updated = self.catalog.acknowledge_readiness_gate(
            self.entry.protocol_id,
            self.entry.revision_id,
            reason_code=_GATE.value,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            comment="Reviewed the extracted warnings against the source.",
        )
        self.assertTrue(updated)
        review = self.catalog.review(self.entry.protocol_id)
        codes = [r["code"] for r in review["readiness"]["reasons"]]
        self.assertIn(_GATE.value, codes)
        self.assertTrue(
            self.catalog._readiness_gates_cleared(
                self.protocol_id,
                1,
                self.store.get_analysis_revision(self.protocol_id, 1, 1),
            )
        )

    def test_the_confirmation_names_who_gave_it(self) -> None:
        self.catalog.acknowledge_readiness_gate(
            self.entry.protocol_id,
            self.entry.revision_id,
            reason_code=_GATE.value,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        events = [
            event
            for event in self.store.list_events(self.protocol_id)
            if _GATE.value in str(event)
        ]
        self.assertTrue(events)
        self.assertIn("reviewer@example.org", str(events))

    def test_the_claim_itself_is_untouched(self) -> None:
        """Only the authority to open the gate moved; the claim is unchanged."""

        step = self.protocol.sections[0].steps[0]
        self.assertEqual(len(step.warnings), 1)
        self.assertEqual(step.warnings[0].source_text, "Wear gloves.")
        self.assertEqual(step.warnings[0].evidence.source_page_number, 1)
