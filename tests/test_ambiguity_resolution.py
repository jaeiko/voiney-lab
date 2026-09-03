"""A reviewer can settle one ambiguity, and withdraw the finding again.

The pipeline stopped permanently on something a person settles in seconds: a
source that states one interval twice, once in prose and once as a timer
literal. Acknowledging a readiness gate is too coarse for that -- it would
clear every ambiguity in the document at once, including ones nobody looked at
-- so a finding is recorded per ambiguity instead.

Nothing here infers whether two statements agree. No string or numeric
comparison decides it, because that would be repairing the document on a
guess. Only a person decides, the source is never edited, no claim is deleted
or rewritten, and with nothing recorded the answer stays blocked.
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
    AMBIGUITY_SINGLE_AUTHORITATIVE,
    AMBIGUITY_STATEMENTS_DISTINCT,
    ProtocolApprovalError,
    ProtocolCatalog,
)
from voice_workflow_agent.protocol_claim_analysis import (
    generate_page_evidence_segments,
)

_PAGE = "Protocol Ambiguity\nSection preparation\n1. Incubate for 15min.\n"
_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value


class AmbiguityResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.pdf = self.root / "ambiguity.pdf"
        write_text_pdf(self.pdf, _PAGE, title="Protocol Ambiguity")
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
            source_filename="ambiguity.pdf",
            media_type="application/pdf",
        )
        self.protocol_id = registration.entry.protocol_id
        protocol = self._protocol()
        self.store.append_analysis_revision(
            self.protocol_id,
            1,
            "analysis-ambiguity",
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

    def _protocol(self):
        excerpt = self.segments[0].text
        evidence = domain.SourceEvidence(
            source_page_number=1,
            source_excerpt=excerpt,
            evidence_segment_ids=self.handles,
        )
        plain = lambda text: domain.SourceEvidence(1, text)
        instruction = "1. Incubate for 15min."
        return domain.validate_protocol(
            domain.ExperimentProtocol(
                self.protocol_id,
                domain.ProtocolMetadata(
                    self.extraction,
                    "Protocol Ambiguity",
                    "en",
                    evidence=plain("Protocol Ambiguity"),
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
                constructs=(
                    domain.SourceAmbiguity(
                        "ambiguity-1",
                        excerpt,
                        evidence,
                        step_id="step-1",
                    ),
                ),
            )
        )

    def _cleared(self) -> bool:
        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        return self.catalog._readiness_gates_cleared(
            self.protocol_id, 1, analysis
        )

    def _resolve(self, decision: str, **overrides):
        return self.catalog.resolve_ambiguity(
            self.protocol_id,
            self.revision_id,
            ambiguity_id=overrides.pop("ambiguity_id", "ambiguity-1"),
            decision=decision,
            evidence_segment_ids=overrides.pop(
                "evidence_segment_ids", self.handles
            ),
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            **overrides,
        )

    # --- the three directions the loop needs -------------------------------

    def test_before_any_finding_it_is_blocked(self) -> None:
        self.assertFalse(self._cleared())
        self.assertEqual(
            self.catalog.ambiguity_findings(
                self.protocol_id, self.revision_id
            ),
            {},
        )

    def test_an_authoritative_finding_clears_it(self) -> None:
        self._resolve(
            AMBIGUITY_SINGLE_AUTHORITATIVE,
            comment="Prose and timer state the same interval.",
        )
        self.assertTrue(self._cleared())

    def test_withdrawing_the_finding_blocks_it_again(self) -> None:
        self._resolve(AMBIGUITY_SINGLE_AUTHORITATIVE)
        self.assertTrue(self._cleared())
        self.catalog.revoke_ambiguity_resolution(
            self.protocol_id,
            self.revision_id,
            ambiguity_id="ambiguity-1",
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        self.assertFalse(self._cleared())
        self.assertEqual(
            self.catalog.ambiguity_findings(
                self.protocol_id, self.revision_id
            ),
            {},
        )

    # --- what a finding records --------------------------------------------

    def test_the_finding_records_who_when_what_and_the_handles(self) -> None:
        self._resolve(
            AMBIGUITY_SINGLE_AUTHORITATIVE, comment="Same interval, twice."
        )
        events = [
            event
            for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_ambiguity_resolved"
        ]
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["ambiguity_id"], "ambiguity-1")
        self.assertEqual(payload["decision"], AMBIGUITY_SINGLE_AUTHORITATIVE)
        self.assertEqual(payload["actor_principal_id"], "reviewer@example.org")
        self.assertEqual(payload["actor_role"], "reviewer")
        self.assertEqual(payload["step_id"], "step-1")
        self.assertEqual(payload["source_page_number"], 1)
        self.assertEqual(payload["evidence_segment_ids"], list(self.handles))
        self.assertEqual(payload["comment"], "Same interval, twice.")
        self.assertTrue(events[0].recorded_at)

    def test_a_withdrawal_does_not_erase_the_earlier_finding(self) -> None:
        """The ledger is append-only: both decisions stay readable."""

        self._resolve(AMBIGUITY_SINGLE_AUTHORITATIVE)
        self.catalog.revoke_ambiguity_resolution(
            self.protocol_id,
            self.revision_id,
            ambiguity_id="ambiguity-1",
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        kinds = [
            event.event_type
            for event in self.store.list_events(self.protocol_id)
            if "ambiguity" in event.event_type
        ]
        self.assertEqual(
            kinds,
            [
                "protocol_ambiguity_resolved",
                "protocol_ambiguity_resolution_revoked",
            ],
        )

    def test_a_reviewer_may_change_their_mind(self) -> None:
        self._resolve(AMBIGUITY_SINGLE_AUTHORITATIVE)
        self.catalog.revoke_ambiguity_resolution(
            self.protocol_id,
            self.revision_id,
            ambiguity_id="ambiguity-1",
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        self._resolve(AMBIGUITY_STATEMENTS_DISTINCT)
        self.assertFalse(self._cleared())

    # --- what a finding cannot do ------------------------------------------

    def test_finding_them_distinct_clears_nothing(self) -> None:
        """That is a finding, not a resolution. It stays blocked."""

        self._resolve(AMBIGUITY_STATEMENTS_DISTINCT)
        self.assertFalse(self._cleared())
        self.assertEqual(
            self.catalog.ambiguity_findings(
                self.protocol_id, self.revision_id
            ),
            {"ambiguity-1": AMBIGUITY_STATEMENTS_DISTINCT},
        )

    def test_the_stored_analysis_is_not_rewritten(self) -> None:
        """No source edit, no claim deleted, no `resolved` flag flipped."""

        before = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self._resolve(AMBIGUITY_SINGLE_AUTHORITATIVE)
        after = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertEqual(before.payload_sha256, after.payload_sha256)
        ambiguity = after.protocol.constructs[0]
        self.assertFalse(ambiguity.resolved)
        self.assertIsNone(ambiguity.resolution_source_text)
        self.assertEqual(
            ambiguity.evidence.source_excerpt,
            before.protocol.constructs[0].evidence.source_excerpt,
        )

    def test_a_citation_that_does_not_resolve_is_refused(self) -> None:
        """A decision cannot rest on a span that does not exist."""

        with self.assertRaises(ProtocolApprovalError):
            self._resolve(
                AMBIGUITY_SINGLE_AUTHORITATIVE,
                evidence_segment_ids=("s-not-a-real-handle",),
            )
        self.assertFalse(self._cleared())

    def test_a_finding_must_cite_something(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._resolve(
                AMBIGUITY_SINGLE_AUTHORITATIVE, evidence_segment_ids=()
            )

    def test_an_unknown_ambiguity_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._resolve(
                AMBIGUITY_SINGLE_AUTHORITATIVE, ambiguity_id="ambiguity-404"
            )

    def test_an_unsupported_decision_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._resolve("looks_the_same_to_me")

    def test_an_unidentified_actor_is_refused(self) -> None:
        for principal, role in (("", "reviewer"), ("someone", "")):
            with self.subTest(principal=principal, role=role):
                with self.assertRaises(ProtocolApprovalError):
                    self.catalog.resolve_ambiguity(
                        self.protocol_id,
                        self.revision_id,
                        ambiguity_id="ambiguity-1",
                        decision=AMBIGUITY_SINGLE_AUTHORITATIVE,
                        evidence_segment_ids=self.handles,
                        actor_principal_id=principal,
                        actor_role=role,
                    )

    def test_revoking_a_finding_that_was_never_made_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.revoke_ambiguity_resolution(
                self.protocol_id,
                self.revision_id,
                ambiguity_id="ambiguity-1",
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
            )

    def test_resolving_an_ambiguity_never_clears_the_safety_gate(self) -> None:
        """The two are separate decisions and one never stands for the other."""

        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog2")
        )
        self.addCleanup(store.close)
        catalog = ProtocolCatalog(store)
        registration = catalog.register(
            self.pdf,
            source_filename="ambiguity.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        protocol = domain.validate_protocol(
            replace(self._protocol(), protocol_id=protocol_id)
        )
        store.append_analysis_revision(
            protocol_id,
            1,
            "analysis-ambiguity",
            protocol,
            domain.assess_readiness(protocol),
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        catalog.resolve_ambiguity(
            protocol_id,
            "pdf-1-analysis-1",
            ambiguity_id="ambiguity-1",
            decision=AMBIGUITY_SINGLE_AUTHORITATIVE,
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
