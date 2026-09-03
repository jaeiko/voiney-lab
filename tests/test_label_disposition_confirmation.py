"""A numbered label may be disposed of, but never silently and never alone.

The numbered-action obligation assumed every numbered line is an instruction.
Measured across the four local sources that is false in three of them: ANKOM
carries fourteen bare `Flush procedure:` headings, headspace one materials
description, and the near-unnumbered document twelve section headings and a
contents list. Demanding an action claim for those refused correct responses --
it refused a real one, on headspace page 9, during the first provider run.

The asymmetry runs opposite to the repetition one. Turning a description into a
step only makes the agent read a description aloud, which is a nuisance.
Disposing of a real step removes it from the protocol, which is dangerous. So
the default is to claim it as an action, the prompt says so, and a disposition
is the exception a person confirms.
"""

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
)
from voice_workflow_agent.protocol_claim_analysis import (
    generate_page_evidence_segments,
)

_PAGE = (
    "Protocol Labels\nSection preparation\n1. Wash the pellet.\n"
    "2 Buffer contains sodium chloride.\n"
)
_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value
_LABEL_GATE = domain.ReadinessReasonCode.UNCONFIRMED_LABEL_DISPOSITION.value


class LabelDispositionConfirmationTests(unittest.TestCase):
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
        protocol = self._protocol()
        self.store.append_analysis_revision(
            self.protocol_id,
            1,
            "analysis-labels",
            protocol,
            domain.assess_readiness(protocol),
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        self.revision_id = "pdf-1-analysis-1"
        self.catalog.acknowledge_readiness_gate(
            self.protocol_id,
            self.revision_id,
            reason_code=_GATE,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )

    def _protocol(self, label: str = "2"):
        excerpt = self.segments[0].text
        plain = lambda text: domain.SourceEvidence(1, text)
        instruction = "1. Wash the pellet."
        return domain.validate_protocol(
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
                label_dispositions=(
                    domain.NonStepLabelDisposition(
                        source_page_number=1,
                        source_label=label,
                        evidence=domain.SourceEvidence(
                            source_page_number=1,
                            source_excerpt=excerpt,
                            evidence_segment_ids=self.handles,
                        ),
                    ),
                ),
            )
        )

    def _cleared(self) -> bool:
        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        return self.catalog._readiness_gates_cleared(
            self.protocol_id, 1, analysis
        )

    def _confirm(self, **overrides):
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

    # --- the three directions ---------------------------------------------

    def test_before_confirmation_it_is_blocked(self) -> None:
        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertIn(_LABEL_GATE, analysis.readiness.reason_codes)
        self.assertFalse(self._cleared())
        self.assertEqual(
            self.catalog.label_disposition_findings(
                self.protocol_id, self.revision_id
            ),
            frozenset(),
        )

    def test_a_confirmation_clears_it(self) -> None:
        self._confirm(comment="Numbered line describes a buffer, not a step.")
        self.assertTrue(self._cleared())
        self.assertEqual(
            self.catalog.label_disposition_findings(
                self.protocol_id, self.revision_id
            ),
            frozenset({"1:2"}),
        )

    def test_withdrawing_it_blocks_again(self) -> None:
        self._confirm()
        self.assertTrue(self._cleared())
        self.catalog.revoke_label_disposition_confirmation(
            self.protocol_id,
            self.revision_id,
            source_page_number=1,
            source_label="2",
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        self.assertFalse(self._cleared())

    # --- what it records and refuses ---------------------------------------

    def test_it_records_who_when_what_and_the_handles(self) -> None:
        self._confirm(comment="Describes a buffer.")
        events = [
            event
            for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_label_disposition_confirmed"
        ]
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["source_label"], "2")
        self.assertEqual(payload["source_page_number"], 1)
        self.assertEqual(payload["decision"], "not_an_execution_step")
        self.assertEqual(payload["actor_principal_id"], "reviewer@example.org")
        self.assertEqual(payload["evidence_segment_ids"], list(self.handles))
        self.assertEqual(payload["comment"], "Describes a buffer.")
        self.assertTrue(events[0].recorded_at)

    def test_a_citation_that_does_not_resolve_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._confirm(evidence_segment_ids=("s-not-a-real-handle",))
        self.assertFalse(self._cleared())

    def test_a_confirmation_must_cite_something(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._confirm(evidence_segment_ids=())

    def test_an_unknown_label_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._confirm(source_label="9")

    def test_the_wrong_page_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._confirm(source_page_number=2)

    def test_an_unidentified_actor_is_refused(self) -> None:
        for principal, role in (("", "reviewer"), ("someone", "")):
            with self.subTest(principal=principal, role=role):
                with self.assertRaises(ProtocolApprovalError):
                    self._confirm(
                        actor_principal_id=principal, actor_role=role
                    )

    def test_revoking_one_never_made_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.revoke_label_disposition_confirmation(
                self.protocol_id,
                self.revision_id,
                source_page_number=1,
                source_label="2",
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
            )

    def test_the_stored_analysis_is_not_rewritten(self) -> None:
        before = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self._confirm()
        after = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertEqual(before.payload_sha256, after.payload_sha256)
        self.assertEqual(len(after.protocol.label_dispositions), 1)

    def test_it_never_clears_the_safety_gate(self) -> None:
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog2")
        )
        self.addCleanup(store.close)
        catalog = ProtocolCatalog(store)
        registration = catalog.register(
            self.pdf,
            source_filename="labels.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        protocol = domain.validate_protocol(
            replace(self._protocol(), protocol_id=protocol_id)
        )
        store.append_analysis_revision(
            protocol_id,
            1,
            "analysis-labels",
            protocol,
            domain.assess_readiness(protocol),
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        catalog.confirm_label_disposition(
            protocol_id,
            "pdf-1-analysis-1",
            source_page_number=1,
            source_label="2",
            evidence_segment_ids=self.handles,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        analysis = store.get_analysis_revision(protocol_id, 1, 1)
        self.assertIn(_GATE, analysis.readiness.reason_codes)
        self.assertFalse(
            catalog._readiness_gates_cleared(protocol_id, 1, analysis)
        )


if __name__ == "__main__":
    unittest.main()
