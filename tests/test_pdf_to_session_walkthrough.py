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

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import (
    CuratedProtocolFixture,
    CuratedProtocolSession,
)
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.experiment_protocol_store import (
    ProtocolPersistenceSettings,
    initialize_protocol_store,
)
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    ProtocolCatalogUnavailableError,
)
from voiney_lab.protocol_claim_analysis import ClaimCategory
from voiney_lab.protocol_chunk_analysis import (
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
        self.assertEqual(self.extraction.ocr_required_page_numbers, ())
        # Five since STEP 26, not three. The planner used to bound a chunk by
        # source bytes as a proxy for how many claims it would owe, and
        # measurement said the proxy was wrong: this document's chunk 0 held
        # 3398 bytes and 2 numbered labels and passed a real provider three
        # times over, while chunk 1 held 4007 bytes -- barely more -- and 22
        # labels, and was rejected on every attempt. Bounding the labels
        # instead takes the worst chunk here from 22 to 9.
        self.assertEqual(len(self.plan.chunks), 5)
        from voiney_lab.protocol_claim_analysis import (
            _numbered_step_labels,
        )

        owed = [
            sum(
                len(_numbered_step_labels(self.extraction.pages[page - 1].text))
                for page in chunk.core_page_refs
            )
            for chunk in self.plan.chunks
        ]
        self.assertEqual(owed, [2, 9, 9, 4, 1])

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
        """Premise updated: P1 supports REPEAT_UNTIL, so that reason is gone.

        The property is the exact set -- the pipeline reaches a verdict and
        names every blocking reason its content implies, with nothing extra
        and nothing missing. That is unchanged; only the expected members
        are. The repeat constructs themselves are still assembled and still
        gate execution at the bench; see
        tests/test_repeat_until_declaration_properties.py.
        """

        readiness = domain.assess_readiness(self.draft.protocol)
        self.assertIs(
            readiness.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )
        self.assertEqual(
            sorted(set(readiness.reason_codes)),
            [
                _GATE,
                domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
            ],
        )
        # The construct did not disappear with its reason.
        self.assertTrue(
            [
                construct
                for construct in self.draft.protocol.constructs
                if isinstance(construct, domain.RepeatUntil)
            ]
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

    def test_the_assembled_analysis_may_run_under_the_mvp_rule(self) -> None:
        """Stages 7 to 9 under the rule of 2026-10-08 (lane DI).

        The assembled analysis carries the safety reason and the ambiguities
        the hand-built fixture carries too. Neither is an execution blocker
        any more: both are notices the experimenter reads before pressing
        start, so the catalog loads the executable fixture without any
        approval, finding or activation. What the rule does *not* do is
        release the repeat steps: 7, 9 and 20 are still refused at the bench
        until the experimenter reports the endpoint the document states.
        """

        entry = self.catalog.get_entry(self.protocol_id)
        review = self.catalog.review(self.protocol_id)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.execution_blocker_codes, ())
        self.assertEqual(review["execution_blockers"], [])
        self.assertEqual(
            sorted({item["code"] for item in review["execution_notices"]}),
            [
                domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
                domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
            ],
        )
        self.assertEqual(
            {event.event_type for event in self.store.list_events(self.protocol_id)},
            {"protocol_registered"},
        )
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.status, "analysis_passed")

        # Selectable, and still gated where the source states a repeat.
        session = CuratedProtocolSession(fixture)
        session.active = True
        session._workflow_status = "active"
        anchors = [
            index
            for index, step in enumerate(fixture.steps)
            if session.endpoint_observation_outstanding(index)
        ]
        self.assertTrue(anchors)
        for index in anchors:
            session.current_index = index
            self.assertTrue(session.endpoint_observation_outstanding(index))

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
