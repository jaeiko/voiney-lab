"""A bounded repetition does not execute on the model's word.

The two ways of getting a repetition's kind wrong are not symmetric. Calling a
conditional repetition fixed makes the agent stop early and announce
completion while the source's own condition is unmet -- a false completion
notice, the worst outcome this system can produce. Calling a fixed repetition
conditional only makes it ask a person.

So the safe direction is the default: an unconfirmed fixed repetition blocks,
and a reviewer confirms both that it really is bounded and what the bound is.
Nothing quietly downgrades an unconfirmed fixed repetition to a conditional
one either -- that would be another guess.
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
    "Protocol Repetition\nSection preparation\n1. Wash the pellet.\n"
    "2. Repeat steps 1-1 twice more.\n"
)
_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value
_REPEAT_GATE = domain.ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION.value


class FixedRepetitionConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.pdf = self.root / "repetition.pdf"
        write_text_pdf(self.pdf, _PAGE, title="Protocol Repetition")
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
            source_filename="repetition.pdf",
            media_type="application/pdf",
        )
        self.protocol_id = registration.entry.protocol_id
        protocol = self._protocol()
        self.store.append_analysis_revision(
            self.protocol_id,
            1,
            "analysis-repetition",
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

    def _protocol(self, repeat_count: int = 2):
        excerpt = self.segments[0].text
        evidence = domain.SourceEvidence(
            source_page_number=1,
            source_excerpt=excerpt,
            evidence_segment_ids=self.handles,
        )
        plain = lambda text: domain.SourceEvidence(1, text)
        instruction = "1. Wash the pellet."
        return domain.validate_protocol(
            domain.ExperimentProtocol(
                self.protocol_id,
                domain.ProtocolMetadata(
                    self.extraction,
                    "Protocol Repetition",
                    "en",
                    evidence=plain("Protocol Repetition"),
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
                    domain.FixedRangeRepetition(
                        "repetition-1",
                        "step-1",
                        "step-1",
                        excerpt,
                        evidence,
                        repeat_count=repeat_count,
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

    def _confirm(self, count: int = 2, **overrides):
        return self.catalog.confirm_fixed_repetition(
            self.protocol_id,
            self.revision_id,
            repetition_id=overrides.pop("repetition_id", "repetition-1"),
            repeat_count=count,
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
        self.assertIn(_REPEAT_GATE, analysis.readiness.reason_codes)
        self.assertFalse(self._cleared())
        self.assertEqual(
            self.catalog.repetition_findings(
                self.protocol_id, self.revision_id
            ),
            {},
        )

    def test_a_confirmation_clears_it(self) -> None:
        self._confirm(comment="Source says twice more; bound is 2.")
        self.assertTrue(self._cleared())
        self.assertEqual(
            self.catalog.repetition_findings(
                self.protocol_id, self.revision_id
            ),
            {"repetition-1": 2},
        )

    def test_withdrawing_it_blocks_again(self) -> None:
        self._confirm()
        self.assertTrue(self._cleared())
        self.catalog.revoke_fixed_repetition_confirmation(
            self.protocol_id,
            self.revision_id,
            repetition_id="repetition-1",
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        self.assertFalse(self._cleared())
        self.assertEqual(
            self.catalog.repetition_findings(
                self.protocol_id, self.revision_id
            ),
            {},
        )

    # --- what the confirmation records and refuses -------------------------

    def test_it_records_who_when_what_and_the_handles(self) -> None:
        self._confirm(comment="Bound is 2.")
        events = [
            event
            for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_fixed_repetition_confirmed"
        ]
        self.assertEqual(len(events), 1)
        payload = events[0].payload
        self.assertEqual(payload["repetition_id"], "repetition-1")
        self.assertEqual(payload["repeat_count"], 2)
        self.assertEqual(payload["decision"], "fixed_count_confirmed")
        self.assertEqual(payload["actor_principal_id"], "reviewer@example.org")
        self.assertEqual(payload["actor_role"], "reviewer")
        self.assertEqual(payload["start_step_id"], "step-1")
        self.assertEqual(payload["end_step_id"], "step-1")
        self.assertEqual(payload["evidence_segment_ids"], list(self.handles))
        self.assertEqual(payload["comment"], "Bound is 2.")
        self.assertTrue(events[0].recorded_at)

    def test_a_count_that_disagrees_with_the_analysis_is_refused(self) -> None:
        """Confirming a different number is not confirming this repetition."""

        with self.assertRaises(ProtocolApprovalError):
            self._confirm(3)
        self.assertFalse(self._cleared())

    def test_a_citation_that_does_not_resolve_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._confirm(evidence_segment_ids=("s-not-a-real-handle",))
        self.assertFalse(self._cleared())

    def test_a_confirmation_must_cite_something(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._confirm(evidence_segment_ids=())

    def test_an_unknown_repetition_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self._confirm(repetition_id="repetition-404")

    def test_a_nonsense_count_is_refused(self) -> None:
        for count in (0, -1, True):
            with self.subTest(count=count):
                with self.assertRaises(ProtocolApprovalError):
                    self._confirm(count)

    def test_an_unidentified_actor_is_refused(self) -> None:
        for principal, role in (("", "reviewer"), ("someone", "")):
            with self.subTest(principal=principal, role=role):
                with self.assertRaises(ProtocolApprovalError):
                    self._confirm(
                        actor_principal_id=principal, actor_role=role
                    )

    def test_revoking_a_confirmation_never_made_is_refused(self) -> None:
        with self.assertRaises(ProtocolApprovalError):
            self.catalog.revoke_fixed_repetition_confirmation(
                self.protocol_id,
                self.revision_id,
                repetition_id="repetition-1",
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
            )

    def test_the_stored_analysis_is_not_rewritten(self) -> None:
        before = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self._confirm()
        after = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertEqual(before.payload_sha256, after.payload_sha256)
        self.assertEqual(after.protocol.constructs[0].repeat_count, 2)

    def test_a_confirmation_never_clears_the_safety_gate(self) -> None:
        """Separate decisions; neither stands for the other."""

        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog2")
        )
        self.addCleanup(store.close)
        catalog = ProtocolCatalog(store)
        registration = catalog.register(
            self.pdf,
            source_filename="repetition.pdf",
            media_type="application/pdf",
        )
        protocol_id = registration.entry.protocol_id
        protocol = domain.validate_protocol(
            replace(self._protocol(), protocol_id=protocol_id)
        )
        store.append_analysis_revision(
            protocol_id,
            1,
            "analysis-repetition",
            protocol,
            domain.assess_readiness(protocol),
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        catalog.confirm_fixed_repetition(
            protocol_id,
            "pdf-1-analysis-1",
            repetition_id="repetition-1",
            repeat_count=2,
            evidence_segment_ids=self.handles,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        analysis = store.get_analysis_revision(protocol_id, 1, 1)
        self.assertIn(_GATE, analysis.readiness.reason_codes)
        self.assertFalse(
            catalog._readiness_gates_cleared(protocol_id, 1, analysis)
        )

    def test_nothing_downgrades_an_unconfirmed_repetition(self) -> None:
        """It stays a fixed repetition, and it stays blocked."""

        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        construct = analysis.protocol.constructs[0]
        self.assertIsInstance(construct, domain.FixedRangeRepetition)
        self.assertEqual(construct.repeat_count, 2)
        self.assertFalse(self._cleared())


class ClassifyingBoundedUnblocksTheUnsupportedShapeTests(unittest.TestCase):
    """A provider that says "fixed" removes the unsupported blocker.

    This is what the vocabulary was added for. A statement classified as
    fixed_range_repetition assembles into the construct P1 supports, so
    `unsupported_repeat_until` is gone and only the confirmation gate remains.
    The offline fixture cannot make this choice -- it does not read prose and
    always says repeat_condition, the safe direction -- so a document whose
    repetitions are all bounded still blocks until a real model classifies
    them.
    """

    def test_a_bounded_classification_assembles_and_leaves_one_gate(self) -> None:
        import json

        from tests.test_protocol_claim_analysis import write_lined_pages
        from voice_workflow_agent.protocol_claim_analysis import (
            CLAIM_SCHEMA_VERSION,
            parse_chunk_claim_response,
            prepare_chunk_claim_request_context,
        )
        from voice_workflow_agent.protocol_chunk_analysis import (
            ChunkAnalysisLimits,
            ValidatedChunkResult,
            assemble_validated_protocol_claims,
            extraction_for_chunk,
            merge_validated_chunk_results,
            plan_protocol_chunks,
        )

        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "fixed.pdf"
            write_lined_pages(
                pdf,
                (
                    (
                        "Protocol Fixed",
                        "Section wash",
                        "1. Wash the pellet with 10 mL buffer.",
                        "2. Repeat steps 1-1 twice more.",
                    ),
                ),
            )
            extraction = extract_protocol_pdf(pdf)
            plan = plan_protocol_chunks(
                extraction,
                f"protocol-{extraction.sha256[:32]}",
                "pdf-1",
                limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
            )
            chunk = plan.chunks[0]
            scoped = extraction_for_chunk(extraction, chunk)
            request = prepare_chunk_claim_request_context(
                scoped,
                source_revision=chunk.candidate_revision_id,
                chunk_id=chunk.chunk_id,
                ordinal=chunk.ordinal,
                core_page_refs=chunk.core_page_refs,
                context_page_refs=chunk.overlap_page_refs,
            )
            handles = {
                item.segment.text: item.handle
                for page in request.pages
                for item in page.evidence
            }
            find = lambda needle: [
                handle
                for text, handle in handles.items()
                if needle in text
            ]
            def claim(claim_id, category, needle, order, **kwargs):
                return {
                    "claim_id": claim_id,
                    "category": category,
                    "source_order": order,
                    "section_id": "section-1",
                    "step_id": kwargs.get("step_id"),
                    "source_label": kwargs.get("source_label"),
                    "target_claim_id": kwargs.get("target"),
                    "required_for_execution": category == "action",
                    "repeated_step_labels": kwargs.get("labels"),
                    "repetition_count": kwargs.get("count"),
                    "evidence": {
                        "source_page_number": 1,
                        "evidence_segment_ids": find(needle),
                    },
                }

            claims = [
                claim("action-1", "action", "Wash the pellet", 1,
                      step_id="step-1", source_label="1"),
                claim("action-2", "action", "Repeat steps 1-1", 2,
                      step_id="step-2", source_label="2"),
                claim("fixed-1", "fixed_range_repetition", "Repeat steps 1-1",
                      3, step_id="step-2", target="action-2",
                      labels=["1", "1"], count=2),
            ]
            title = find("Protocol Fixed")
            structure = [
                {
                    "marker_id": "protocol-title",
                    "kind": "protocol_title",
                    "source_order": 0,
                    "section_id": None,
                    "evidence": {
                        "source_page_number": 1,
                        "evidence_segment_ids": title,
                    },
                },
                {
                    "marker_id": "marker-1",
                    "kind": "section",
                    "source_order": 1,
                    "section_id": "section-1",
                    "evidence": {
                        "source_page_number": 1,
                        "evidence_segment_ids": title,
                    },
                },
            ]
            cited = {
                handle
                for record in claims
                for handle in record["evidence"]["evidence_segment_ids"]
            } | set(title)
            payload = {
                "claim_schema_version": CLAIM_SCHEMA_VERSION,
                "capability_policy_id": "p1-conservative",
                "request_handle": request.request_handle,
                "page_coverage": [
                    {
                        "source_page_number": 1,
                        "analysis_incomplete": False,
                        "non_step_labels": [],
                        "declined_evidence_segment_ids": [
                            handle
                            for handle in handles.values()
                            if handle not in cited
                        ],
                    }
                ],
                "structure": structure,
                "claims": claims,
            }
            analysis = parse_chunk_claim_response(
                json.dumps(payload),
                extraction=scoped,
                source_revision=chunk.candidate_revision_id,
                chunk_id=chunk.chunk_id,
                core_page_refs=chunk.core_page_refs,
                request=request,
            )
            merged = merge_validated_chunk_results(
                extraction, plan, (ValidatedChunkResult(chunk, analysis),)
            )
            protocol = assemble_validated_protocol_claims(
                extraction, merged
            ).protocol

        self.assertEqual(len(protocol.constructs), 1)
        construct = protocol.constructs[0]
        self.assertIsInstance(construct, domain.FixedRangeRepetition)
        self.assertEqual(construct.repeat_count, 2)
        self.assertEqual(construct.start_step_id, "step-1")
        self.assertEqual(construct.end_step_id, "step-1")
        readiness = domain.assess_readiness(protocol)
        self.assertEqual(
            sorted(set(readiness.reason_codes)), [_GATE, _REPEAT_GATE]
        )
        self.assertNotIn(
            domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL.value,
            readiness.reason_codes,
        )


if __name__ == "__main__":
    unittest.main()
