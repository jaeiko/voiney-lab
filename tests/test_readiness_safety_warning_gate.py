"""The safety-warning readiness reason, and what it means under the MVP rule.

The domain still records ``no_declared_safety_warnings`` on every analysis
with steps. Since lane DI (2026-10-08) it is a notice, not a gate: the
source's safety statements are shown before the start and read at each
step, and the experimenter's press of start is the one confirmation.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.experiment_protocol_store import (
    ProtocolPersistenceSettings,
    initialize_protocol_store,
)
from voiney_lab.protocol_catalog import ProtocolCatalog

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

    def test_the_reason_is_a_notice_and_the_analysis_may_run(self) -> None:
        entry = self._register(declare_warning=False)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.execution_blocker_codes, ())
        self.assertNotIn(_GATE, domain.EXECUTION_BLOCKING_REASON_CODES)
        self.assertEqual(
            self.catalog.load_executable_fixture(entry.protocol_id).status,
            "analysis_passed",
        )

    def test_a_stacked_ambiguity_is_a_notice_too(self) -> None:
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
        self.assertTrue(entry.available_for_execution)
        review = self.catalog.review(protocol_id)
        self.assertEqual(review["execution_blockers"], [])
        self.assertEqual(
            [item["code"] for item in review["execution_notices"]],
            ["unresolved_ambiguity", _GATE.value],
        )

    def test_a_stepless_analysis_carries_no_safety_reason(self) -> None:
        entry = self._register(declare_warning=True, with_steps=False)
        review = self.catalog.review(entry.protocol_id)
        codes = [reason["code"] for reason in review["readiness"]["reasons"]]
        self.assertNotIn(_GATE.value, codes)
        # No steps to run: that one still blocks.
        self.assertIn("no_executable_steps", entry.execution_blocker_codes)
        self.assertFalse(entry.available_for_execution)

    def test_the_review_payload_surfaces_the_reason_as_a_notice(self) -> None:
        entry = self._register(declare_warning=False)
        review = self.catalog.review(entry.protocol_id)
        readiness = review["readiness"]
        self.assertEqual(readiness["status"], "analysis_required")
        self.assertIn(
            _GATE.value,
            [reason["code"] for reason in readiness["reasons"]],
        )
        self.assertIn(
            _GATE.value, [item["code"] for item in review["execution_notices"]]
        )


class SafetyNoticeSourceTests(unittest.TestCase):
    """The start screen's safety statements do not depend on hazard wording."""

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

    def test_innocuous_wording_is_shown_as_the_source_states_it(self) -> None:
        """The retired word list contained none of these terms."""

        review = self._review("Wear gloves.")
        self.assertEqual(review["declared_safety_warning_count"], 1)
        self.assertEqual(
            [item["source_text"] for item in review["safety_notice_sources"]],
            ["Wear gloves."],
        )

    def test_alarming_wording_is_treated_identically(self) -> None:
        review = self._review("Danger, highly corrosive.")
        self.assertEqual(review["declared_safety_warning_count"], 1)
        self.assertEqual(
            [item["source_text"] for item in review["safety_notice_sources"]],
            ["Danger, highly corrosive."],
        )

    def test_zero_warnings_is_said_and_the_reason_is_recorded(self) -> None:
        """The inverted case: worse extraction must not look safer.

        With no statement found, the start screen says so in its own words
        (nothing is invented), and the readiness reason is still recorded.
        """

        review = self._review(None)
        self.assertEqual(review["declared_safety_warning_count"], 0)
        self.assertEqual(review["safety_notice_sources"], [])
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


class ProviderClaimsAreShownNotJudgedTests(unittest.TestCase):
    """A provider's hazard claim is shown as the source states it.

    The measured defect of 2026-09: `warning_hazard` claim -> `step.warnings`
    -> `declared_safety_warning_count` non-zero -> the gate absent -> readiness
    `guidance_ready`. The reason is still recorded whatever the count says,
    so a claim never changes the recorded assessment; under the MVP rule the
    person who reads the statements is the experimenter, before pressing
    start, and the words shown are the document's own.
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

    def test_a_provider_hazard_claim_never_changes_the_recorded_reason(self) -> None:
        self.assertEqual(
            domain.declared_safety_warning_count(self.protocol), 1
        )
        review = self.catalog.review(self.entry.protocol_id)
        codes = [r["code"] for r in review["readiness"]["reasons"]]
        self.assertIn(_GATE.value, codes)
        self.assertEqual(review["declared_safety_warning_count"], 1)
        self.assertEqual(
            [item["source_text"] for item in review["safety_notice_sources"]],
            ["Wear gloves."],
        )

    def test_nothing_is_written_to_the_ledger_before_the_start(self) -> None:
        """No approval, finding or activation event exists any more."""

        self.assertTrue(self.entry.available_for_execution)
        self.assertEqual(
            {event.event_type for event in self.store.list_events(self.protocol_id)},
            {"protocol_registered"},
        )

    def test_the_claim_itself_is_untouched(self) -> None:
        """Only the authority to open the gate moved; the claim is unchanged."""

        step = self.protocol.sections[0].steps[0]
        self.assertEqual(len(step.warnings), 1)
        self.assertEqual(step.warnings[0].source_text, "Wear gloves.")
        self.assertEqual(step.warnings[0].evidence.source_page_number, 1)
