"""One real PDF through every stage, offline, in an isolated store.

This is a plumbing test, not a quality measurement. The claim model is the
deterministic offline fixture, so the analysis it feeds the later stages is
fixed and synthetic; nothing here says anything about what a real provider
would produce. What it pins down is that the stages are connected, that
assembly does not lose or duplicate information, and exactly where the loop
stops.

The operational store is never opened: the catalog lives in a temporary
directory and the source PDF is only read.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolFixture,
    CuratedProtocolSession,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import (
    ProtocolPersistenceSettings,
    initialize_protocol_store,
)
from voice_workflow_agent.protocol_catalog import (
    ProtocolCatalog,
    ProtocolCatalogUnavailableError,
)
from voice_workflow_agent.protocol_claim_analysis import ClaimCategory
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ValidatedChunkResult,
    analyze_protocol_chunk,
    assemble_validated_protocol_claims,
    merge_validated_chunk_results,
    plan_protocol_chunks,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

IN_GEL = Path("data/runtime/candidate-a-source/in-gel-digestion.pdf")
_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value


def _pipeline():
    extraction = extract_protocol_pdf(IN_GEL)
    plan = plan_protocol_chunks(
        extraction,
        f"protocol-{extraction.sha256[:32]}",
        "pdf-1",
        limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
    )
    from prototype_claim_chunks import ExactNumberedStepClaimModel

    model = ExactNumberedStepClaimModel(extraction)
    results = tuple(
        ValidatedChunkResult(
            chunk, analyze_protocol_chunk(extraction, chunk, model)
        )
        for chunk in plan.chunks
    )
    merged = merge_validated_chunk_results(extraction, plan, results)
    return extraction, plan, merged, assemble_validated_protocol_claims(
        extraction, merged
    )


class PipelineReachesAssemblyTests(unittest.TestCase):
    """Stages 1 to 6. Before this, merge had never been attempted at all."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(
                f"{IN_GEL} is not present; it is the local protocol source "
                "this walkthrough reads."
            )
        cls.extraction, cls.plan, cls.merged, cls.draft = _pipeline()

    def test_the_offline_model_is_in_scope_for_this_document(self) -> None:
        from prototype_claim_chunks import fixture_scope

        scope = fixture_scope(self.extraction)
        self.assertTrue(scope["in_scope"])
        self.assertEqual(scope["duplicate_labels"], 0)

    def test_extraction_and_admission(self) -> None:
        self.assertEqual(self.extraction.page_count, 9)
        self.assertTrue(self.extraction.text_cross_checked)
        self.assertEqual(len(self.plan.chunks), 3)

    def test_every_chunk_validates_and_merges(self) -> None:
        self.assertEqual(len(self.merged.claims), 86)
        self.assertEqual(len(self.merged.structure), 2)

    def test_assembly_produces_the_steps_the_labels_promise(self) -> None:
        steps = [
            step
            for section in self.draft.protocol.sections
            for step in section.steps
        ]
        self.assertEqual(len(steps), 25)
        self.assertEqual(
            [step.source_label for step in steps],
            [str(number) for number in range(1, 26)],
        )

    def test_readiness_is_reached_and_names_its_blockers(self) -> None:
        readiness = domain.assess_readiness(self.draft.protocol)
        self.assertIs(
            readiness.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )
        self.assertEqual(
            sorted(set(readiness.reason_codes)),
            [
                _GATE,
                domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
                domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL.value,
            ],
        )


class AssemblyDoesNotDuplicateStepsTests(unittest.TestCase):
    """An action claim is a step, never also a before-start item.

    The catch-all that routes untargeted claims into ``before_start`` was
    catching action claims too, so on the first document taken through
    assembly every instruction appeared twice -- once as an executable step and
    once as something to do before starting, 25 of 27 entries.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        _, _, cls.merged, cls.draft = _pipeline()

    def test_before_start_holds_only_what_lies_outside_the_steps(self) -> None:
        protocol = self.draft.protocol
        self.assertEqual(len(protocol.before_start), 2)
        instructions = {
            step.instruction_source_text
            for section in protocol.sections
            for step in section.steps
        }
        for item in protocol.before_start:
            with self.subTest(prerequisite=item.prerequisite_id):
                self.assertNotIn(item.source_text, instructions)

    def test_no_action_claim_becomes_a_prerequisite(self) -> None:
        action_texts = {
            claim.source_text
            for claim in self.merged.claims
            if claim.category is ClaimCategory.ACTION
        }
        self.assertEqual(len(action_texts), 25)
        for item in self.draft.protocol.before_start:
            with self.subTest(prerequisite=item.prerequisite_id):
                self.assertNotIn(item.source_text, action_texts)

    def test_the_untargeted_values_still_surface(self) -> None:
        """Excluding actions must not silence a genuinely stray value."""

        self.assertEqual(
            {item.prerequisite_id for item in self.draft.protocol.before_start},
            {
                "condition-duration-outside-p8-2",
                "condition-temperature-outside-p9-0",
            },
        )


class TheLoopStopsAtExecutionReadinessTests(unittest.TestCase):
    """Stages 7 to 10, in an isolated store."""

    def setUp(self) -> None:
        if not IN_GEL.is_file():
            self.skipTest(f"{IN_GEL} is not present.")
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        _, _, _, self.draft = _pipeline()
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        registration = self.catalog.register(
            IN_GEL,
            source_filename=IN_GEL.name,
            media_type="application/pdf",
        )
        self.protocol_id = registration.entry.protocol_id
        protocol = domain.validate_protocol(
            replace(self.draft.protocol, protocol_id=self.protocol_id)
        )
        self.store.append_analysis_revision(
            self.protocol_id,
            1,
            "analysis-walkthrough",
            protocol,
            domain.assess_readiness(protocol),
            self.draft.capability_policy.profile_id,
        )
        self.revision_id = "pdf-1-analysis-1"

    def test_the_safety_confirmation_is_recorded(self) -> None:
        self.catalog.acknowledge_readiness_gate(
            self.protocol_id,
            self.revision_id,
            reason_code=_GATE,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            comment="Plumbing walkthrough.",
        )
        recorded = [
            event
            for event in self.store.list_events(self.protocol_id)
            if event.event_type == "protocol_readiness_gate_acknowledged"
        ]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(
            recorded[0].payload["actor_principal_id"], "reviewer@example.org"
        )

    def test_activation_still_refuses_on_reasons_nobody_may_clear(self) -> None:
        """Where the loop stops, and why it is not a plumbing fault.

        Unresolved ambiguity and an unsupported repeat-until are not
        acknowledgeable gates. The hand-built fixture over this same document
        carries the same two blocker classes, so this wall is not something the
        pipeline introduced.
        """

        self.catalog.acknowledge_readiness_gate(
            self.protocol_id,
            self.revision_id,
            reason_code=_GATE,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.activate_development(self.protocol_id)
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_executable_fixture(self.protocol_id)

    def test_resolving_the_ambiguities_narrows_the_wall(self) -> None:
        """Through the audited route, not around it.

        With every ambiguity settled and the safety warnings confirmed, the
        only reason left is the unsupported repeat-until, which no
        acknowledgement or finding clears. Stage 8 still refuses, and that is
        the correct outcome rather than something to work around.
        """

        from voice_workflow_agent.protocol_catalog import (
            AMBIGUITY_SINGLE_AUTHORITATIVE,
        )

        self.catalog.acknowledge_readiness_gate(
            self.protocol_id,
            self.revision_id,
            reason_code=_GATE,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
        )
        ambiguities = [
            construct
            for construct in self.draft.protocol.constructs
            if isinstance(construct, domain.SourceAmbiguity)
        ]
        self.assertEqual(len(ambiguities), 4)
        for ambiguity in ambiguities:
            self.catalog.resolve_ambiguity(
                self.protocol_id,
                self.revision_id,
                ambiguity_id=ambiguity.ambiguity_id,
                decision=AMBIGUITY_SINGLE_AUTHORITATIVE,
                evidence_segment_ids=ambiguity.evidence.evidence_segment_ids,
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
                comment="Prose interval and timer literal agree.",
            )
        analysis = self.store.get_analysis_revision(self.protocol_id, 1, 1)
        self.assertTrue(
            self.catalog._every_ambiguity_resolved(self.protocol_id, 1, analysis)
        )
        self.assertFalse(
            self.catalog._readiness_gates_cleared(self.protocol_id, 1, analysis)
        )
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.activate_development(self.protocol_id)

    def test_the_session_runs_on_the_assembled_protocol(self) -> None:
        """Stages 9 and 10 as a diagnostic, with the wall stepped around.

        This builds the fixture directly, the way ``replay_turns`` does, so a
        plumbing fault in the last two stages cannot hide behind the policy
        wall in front of them. It makes nothing executable and changes no rule.
        """

        labels = tuple(
            step.source_label
            for section in self.draft.protocol.sections
            for step in section.steps
        )
        fixture = CuratedProtocolFixture(
            draft=self.draft,
            status="fictional_non_operational",
            ordered_step_labels=labels,
            fixture_sha256=hashlib.sha256(b"walkthrough-diagnostic").hexdigest(),
            revision_id="walkthrough-diagnostic",
            development_only=True,
            source_filename=self.draft.extraction.original_filename,
        )
        session = CuratedProtocolSession(fixture)
        session.active = True
        session.current_index = 0
        frame = session.current_step_semantic_frame()
        self.assertEqual(frame.step_id, "step-1")
        self.assertEqual(frame.step_label, "1")
        self.assertTrue(frame.parameters)
        self.assertTrue(frame.actions)


if __name__ == "__main__":
    unittest.main()
