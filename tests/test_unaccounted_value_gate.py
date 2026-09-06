"""An omitted value keeps a Protocol out of execution.

Two ways to lose a value the source states, and until now only one of them
cost anything. Declining a segment that states a value inside a numbered step
is refused outright and the whole chunk goes -- ``declined_segment_states_a_value``.
Saying nothing about the same segment raised nothing at all.

The comment beside that refusal claimed the silence was caught later, that an
incomplete page "blocks the whole-document merge just as firmly". STEP 28
removed that veto and left the sentence behind, so for six steps the cheapest
way to lose a value was to not mention it. STEP 28's replacement -- telling the
experimenter which page the machine had not finished -- was built and never
fed: the only assignment to ``unread_pages`` in the tree was in a test.

This is the other half, and it is deliberately not symmetric in severity. An
omission is a silence, not a false statement, so the chunk survives and so do
the claims that were right. What it costs is execution: the Protocol does not
run until a person has read the pages where a value went unaccounted.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from voice_workflow_agent import experiment_protocol as domain

ROOT = Path(__file__).resolve().parents[1]
IN_GEL = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"


class TheGateItselfTests(unittest.TestCase):
    """The readiness reason, in isolation from any document."""

    def _protocol(self):
        from tests.test_pdf_to_session_walkthrough import _pipeline

        if not IN_GEL.is_file():
            self.skipTest(f"{IN_GEL} is not present.")
        return _pipeline()[3].protocol

    def test_no_unaccounted_value_raises_nothing(self) -> None:
        codes = domain.assess_readiness(self._protocol()).reason_codes
        self.assertNotIn(
            domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value, codes
        )

    def test_an_unaccounted_value_blocks_and_names_the_page_count(self) -> None:
        assessment = domain.assess_readiness(
            self._protocol(), pages_stating_unaccounted_values=(8,)
        )
        self.assertIn(
            domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value,
            assessment.reason_codes,
        )
        self.assertEqual(
            assessment.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )
        reason = next(
            item
            for item in assessment.reasons
            if item.code
            is domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ
        )
        self.assertIn("1 source page(s)", reason.message)

    def test_the_page_list_is_deduplicated_and_junk_is_ignored(self) -> None:
        """Fail closed on shape: a bad entry must not become a bad page."""

        assessment = domain.assess_readiness(
            self._protocol(),
            pages_stating_unaccounted_values=(8, 8, True, "9", None, 3),
        )
        reason = next(
            item
            for item in assessment.reasons
            if item.code
            is domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ
        )
        self.assertIn("2 source page(s)", reason.message)

    def test_it_is_a_gate_a_reviewer_can_clear(self) -> None:
        from voice_workflow_agent.protocol_catalog import (
            _ACKNOWLEDGEABLE_GATES,
            ProtocolCatalog,
        )

        code = domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value
        self.assertIn(code, _ACKNOWLEDGEABLE_GATES)
        self.assertEqual(
            ProtocolCatalog._BLOCKER_RESOLUTION[code],
            {"kind": "reviewer_can_clear", "action": "acknowledge_gate"},
        )


class DerivedFromTheMergeTests(unittest.TestCase):
    """Which pages count, measured on the real coverage records."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        from voice_workflow_agent.chunk_analysis_cache import (
            ChunkAnalysisCache,
            key_for_chunk,
        )
        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_chunk_analysis import (
            ChunkAnalysisLimits,
            extraction_for_chunk,
            plan_protocol_chunks,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            prepare_chunk_claim_request_context,
        )

        cls.extraction = extract_protocol_pdf(IN_GEL)
        plan = plan_protocol_chunks(
            cls.extraction,
            f"protocol-{cls.extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
        )
        cache = ChunkAnalysisCache()
        cls.coverage = []
        for chunk in plan.chunks:
            scoped = extraction_for_chunk(cls.extraction, chunk)
            request = prepare_chunk_claim_request_context(
                scoped,
                source_revision=chunk.candidate_revision_id,
                chunk_id=chunk.chunk_id,
                ordinal=chunk.ordinal,
                core_page_refs=chunk.core_page_refs,
                context_page_refs=chunk.overlap_page_refs,
            )
            hit = cache.load(
                key_for_chunk(cls.extraction, chunk, request),
                extraction=scoped,
                request=request,
            )
            if hit is not None:
                cls.coverage.extend(hit.analysis.page_coverage)
        if not cls.coverage:
            raise unittest.SkipTest("no cached chunk to derive coverage from.")

    def test_a_page_with_no_value_omitted_does_not_gate(self) -> None:
        """Most incomplete pages are incomplete about nothing measurable."""

        from voice_workflow_agent.protocol_claim_analysis import (
            pages_stating_unaccounted_values,
            unaccounted_segments_by_page,
        )

        incomplete = unaccounted_segments_by_page(
            self.extraction, self.coverage, source_revision="pdf-1"
        )
        gating = pages_stating_unaccounted_values(
            self.extraction, self.coverage, source_revision="pdf-1"
        )
        self.assertTrue(incomplete)
        # Strictly fewer: an omission is only a gate when a value went with it.
        self.assertLess(len(gating), len(incomplete))
        for page in gating:
            self.assertIn(page, incomplete)

    def test_a_page_that_omitted_a_value_is_named(self) -> None:
        """Named exactly, and only where a value actually went missing.

        Not pinned to a literal page list: the list grows as chunks are
        collected, and it did -- page 6 joined page 8 when ord1 arrived, which
        is new data rather than a regression. What is invariant is that every
        page named really does hold an unaccounted segment stating a value, and
        that page 8 does, which is the case STEP 34 measured by hand.
        """

        from voice_workflow_agent.protocol_claim_analysis import (
            generate_page_evidence_segments,
            pages_stating_unaccounted_values,
            segment_carries_unit_bearing_value,
            unaccounted_segments_by_page,
        )

        named = pages_stating_unaccounted_values(
            self.extraction, self.coverage, source_revision="pdf-1"
        )
        self.assertIn(8, named)
        omitted = unaccounted_segments_by_page(
            self.extraction, self.coverage, source_revision="pdf-1"
        )
        for page in named:
            with self.subTest(page=page):
                wanted = set(omitted[page])
                self.assertTrue(
                    any(
                        segment.segment_id in wanted
                        and segment_carries_unit_bearing_value(segment.text)
                        for segment in generate_page_evidence_segments(
                            self.extraction,
                            source_revision="pdf-1",
                            page_number=page,
                        )
                    )
                )
        # And a page whose omissions state no value is not named. Pages 1 and 2
        # are the title block and the abstract: no numbered label at all, every
        # segment unaccounted, and nothing measurable lost.
        self.assertNotIn(1, named)
        self.assertNotIn(2, named)

    def test_a_dict_shaped_coverage_record_reads_the_same(self) -> None:
        """The catalog stores coverage as dicts; both shapes must agree."""

        from voice_workflow_agent.protocol_claim_analysis import (
            pages_stating_unaccounted_values,
        )

        as_dicts = [item.public_dict() for item in self.coverage]
        self.assertEqual(
            pages_stating_unaccounted_values(
                self.extraction, as_dicts, source_revision="pdf-1"
            ),
            pages_stating_unaccounted_values(
                self.extraction, self.coverage, source_revision="pdf-1"
            ),
        )


class TheCacheIsNotDisturbedTests(unittest.TestCase):
    """1-3: this change is server-side only, so nothing paid for is lost."""

    def test_the_contract_identities_are_untouched(self) -> None:
        from voice_workflow_agent.chunk_analysis_cache import prompt_sha256
        from voice_workflow_agent.protocol_claim_analysis import (
            CLAIM_SCHEMA_VERSION,
            EVIDENCE_SEGMENT_VERSION,
        )

        # Pinned as of STEP 31, when the contract was last deliberately moved.
        self.assertEqual(CLAIM_SCHEMA_VERSION, 10)
        self.assertEqual(EVIDENCE_SEGMENT_VERSION, 6)
        self.assertTrue(
            prompt_sha256().startswith("dca143b5b81ddffa"),
            "the system prompt moved; every cached chunk would have to be "
            "bought again, which this change was required not to do",
        )

    def test_the_key_does_not_name_readiness_at_all(self) -> None:
        """A readiness reason is downstream of the answer, not part of it."""

        from dataclasses import fields

        from voice_workflow_agent.chunk_analysis_cache import ChunkCacheKey

        names = {item.name for item in fields(ChunkCacheKey)}
        self.assertEqual(
            names & {"readiness", "reason_codes", "unread_pages"}, set()
        )


class TheProductionPathTests(unittest.TestCase):
    """1-2's real requirement: the wiring, not just the mechanism.

    STEP 28 built the disclosure and STEP 32 found nothing feeding it. A test
    that only exercises the domain function would have passed then too, so
    this one goes through the catalog: register, analyse with a page that
    omitted a value, and ask the catalog the two questions that matter.
    """

    def setUp(self) -> None:
        import tempfile

        from voice_workflow_agent.experiment_protocol_config import (
            ProtocolPersistenceSettings,
        )
        from voice_workflow_agent.experiment_protocol_store import (
            initialize_protocol_store,
        )
        from voice_workflow_agent.protocol_catalog import (
            ProtocolCatalog,
            SharedSecretApprovalPolicy,
        )

        globals().setdefault("SharedSecretApprovalPolicy", SharedSecretApprovalPolicy)

        from tests.test_protocol_catalog import analysis_draft, write_text_pdf

        self._draft = analysis_draft
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "alpha.pdf"
        # analysis_draft pins the first three lines; the fourth is here so the
        # page states a value there is something to omit. Nothing about this
        # document is special -- any page that writes a number with a unit
        # would do, which is the point of the shape test.
        write_text_pdf(
            self.source,
            "Protocol Alpha\nSection preparation\n1. Add solution.\n"
            "Wear gloves.\nIncubate for 15 min before use.",
            title="Protocol Alpha",
        )
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)

    def _register_with_coverage(self, *, omit_a_value: bool):
        """Analyse the document, optionally leaving a stated value unaccounted."""

        from voice_workflow_agent.protocol_claim_analysis import (
            generate_page_evidence_segments,
            pages_stating_unaccounted_values,
            segment_carries_unit_bearing_value,
        )

        registration = self.catalog.register(
            self.source,
            source_filename="alpha.pdf",
            media_type="application/pdf",
        )
        entry = registration.entry
        draft = self._draft(self.source, entry.protocol_id, entry.title)

        segments = generate_page_evidence_segments(
            draft.extraction, source_revision=entry.revision_id, page_number=1
        )
        with_value = [
            segment
            for segment in segments
            if segment_carries_unit_bearing_value(segment.text)
        ]
        self.assertTrue(with_value, "the fixture states no value to omit")
        omitted = (with_value[0].segment_id,) if omit_a_value else ()
        coverage = (
            {
                "source_revision": entry.revision_id,
                "source_sha256": draft.extraction.sha256,
                "source_page_number": 1,
                "page_text_sha256": "0" * 64,
                "status": "analysis_incomplete" if omitted else "complete",
                "evidence_item_ids": [],
                "declined_segment_ids": [],
                "unaccounted_segment_ids": list(omitted),
            },
        )
        gating = pages_stating_unaccounted_values(
            draft.extraction, coverage, source_revision=entry.revision_id
        )
        self.assertEqual(bool(gating), omit_a_value)
        readiness = domain.assess_readiness(
            draft.protocol, pages_stating_unaccounted_values=gating
        )
        self.store.append_analysis_revision(
            entry.protocol_id,
            1,
            f"analysis-{entry.source_sha256[:24]}",
            draft.protocol,
            readiness,
            draft.capability_policy_id,
            coverage,
        )
        return entry

    def _acknowledge(self, entry, code):
        analyzed = self.catalog.get_entry(entry.protocol_id)
        self.catalog.acknowledge_readiness_gate(
            analyzed.protocol_id,
            analyzed.revision_id,
            reason_code=code,
            actor_principal_id="reviewer@example.org",
            actor_role="reviewer",
            comment="Read the page against the source.",
        )

    def test_an_omitted_value_blocks_execution_until_someone_reads_it(self):
        code = domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value
        entry = self._register_with_coverage(omit_a_value=True)

        blocked = self.catalog.get_entry(entry.protocol_id)
        self.assertFalse(blocked.available_for_execution)
        self.assertIn(
            code,
            {
                item["code"]
                for item in self.catalog.review(entry.protocol_id)["outstanding_blockers"]
            },
        )

        # Clearing only the safety gate is not enough: this one still stands.
        self._acknowledge(
            entry,
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
        )
        self.assertFalse(
            self.catalog.get_entry(entry.protocol_id).available_for_execution
        )

        # Cleared and approved, it can run. The gate is a gate, not a wall.
        self._acknowledge(entry, code)
        approved = self.catalog.get_entry(entry.protocol_id)
        self.catalog.approve(
            approved.protocol_id,
            approved.revision_id,
            policy=SharedSecretApprovalPolicy("review-secret"),
            presented_secret="review-secret",
        )
        self.assertTrue(
            self.catalog.get_entry(entry.protocol_id).available_for_execution
        )

    def test_the_executable_fixture_carries_the_unread_page(self) -> None:
        """The assignment that existed only in a test file until now."""

        entry = self._register_with_coverage(omit_a_value=True)
        for code in (
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
            domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value,
        ):
            self._acknowledge(entry, code)
        approved = self.catalog.get_entry(entry.protocol_id)
        self.catalog.approve(
            approved.protocol_id,
            approved.revision_id,
            policy=SharedSecretApprovalPolicy("review-secret"),
            presented_secret="review-secret",
        )

        fixture = self.catalog.load_executable_fixture(entry.protocol_id)
        self.assertIsNotNone(fixture.unread_pages)
        self.assertEqual(set(fixture.unread_pages), {1})
        self.assertTrue(fixture.unread_pages[1])

        # And the session's disclosure duty now has something to fire on.
        from voice_workflow_agent.curated_protocol import CuratedProtocolSession

        session = CuratedProtocolSession(fixture)
        session.active = True
        self.assertEqual(session.unread_pages_awaiting_acknowledgement(), (1,))

    def test_a_fully_accounted_document_carries_no_unread_page(self) -> None:
        entry = self._register_with_coverage(omit_a_value=False)
        current = self.catalog.get_entry(entry.protocol_id)
        self.assertNotIn(
            domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value,
            {
                item["code"]
                for item in self.catalog.review(entry.protocol_id)["outstanding_blockers"]
            },
        )
        self._acknowledge(
            entry,
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
        )
        approved = self.catalog.get_entry(entry.protocol_id)
        self.catalog.approve(
            approved.protocol_id,
            approved.revision_id,
            policy=SharedSecretApprovalPolicy("review-secret"),
            presented_secret="review-secret",
        )
        fixture = self.catalog.load_executable_fixture(entry.protocol_id)
        self.assertIsNone(fixture.unread_pages)


class ADeclinedValueBlocksWithoutDiscardingTests(unittest.TestCase):
    """STEP 35: being right about a heading no longer costs a provider call.

    ``declined_segment_states_a_value`` discarded the whole chunk. Measured on
    in-gel that cost two of five chunks over one segment each -- "Reduction and
    alkylation of cysteines 1h", a section heading with its own time estimate,
    and a column of four durations -- and took with them four correct claims
    including a repetition over steps 2-7 that had been derived correctly.

    Safety is "not executed before a person has looked". Both designs achieve
    it. Only one of them also throws away the evidence.
    """

    def test_the_refusal_no_longer_exists(self) -> None:
        from voice_workflow_agent.claim_contract_audit import (
            collect_refusal_codes,
        )

        self.assertNotIn(
            "declined_segment_states_a_value", collect_refusal_codes()
        )

    def test_a_declined_value_inside_a_step_blocks_execution(self) -> None:
        assessment = domain.assess_readiness(
            _synthetic_protocol(self), pages_declining_stated_values=(5,)
        )
        self.assertIn(
            domain.ReadinessReasonCode.DECLINED_VALUE_NOT_RESOLVED.value,
            assessment.reason_codes,
        )
        self.assertEqual(
            assessment.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )

    def test_it_is_a_separate_reason_from_an_omission(self) -> None:
        """A silence and a judgement are different faults and read differently."""

        both = domain.assess_readiness(
            _synthetic_protocol(self),
            pages_stating_unaccounted_values=(8,),
            pages_declining_stated_values=(5,),
        )
        self.assertIn(
            domain.ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ.value,
            both.reason_codes,
        )
        self.assertIn(
            domain.ReadinessReasonCode.DECLINED_VALUE_NOT_RESOLVED.value,
            both.reason_codes,
        )

    def test_the_allowance_has_a_floor_that_the_old_proposal_lacked(self):
        """min(2, count//10) was measured to be 0 for two paid chunks."""

        from voice_workflow_agent.protocol_claim_analysis import (
            declined_value_allowance,
        )

        # The floor is what stops a small chunk being held to a stricter
        # standard than a large one. ord3 has nine substantive segments and
        # ord4 four; under the earlier proposal both had an allowance of zero,
        # so a single declination would have exceeded it.
        self.assertEqual(declined_value_allowance(4), 2)
        self.assertEqual(declined_value_allowance(9), 3)
        self.assertEqual(declined_value_allowance(25), 7)
        self.assertEqual(declined_value_allowance(0), 2)
        # Monotonic, so a bigger chunk is never allowed less.
        allowances = [declined_value_allowance(n) for n in range(0, 120)]
        self.assertEqual(allowances, sorted(allowances))

    def test_exceeding_the_allowance_signals_and_does_not_destroy(self) -> None:
        crowded = domain.assess_readiness(
            _synthetic_protocol(self), pages_declining_excessive_values=(7,)
        )
        self.assertIn(
            domain.ReadinessReasonCode.EXCESSIVE_DECLINED_VALUES.value,
            crowded.reason_codes,
        )
        self.assertEqual(
            crowded.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )

    def test_both_new_reasons_are_gates_a_reviewer_can_clear(self) -> None:
        from voice_workflow_agent.protocol_catalog import (
            _ACKNOWLEDGEABLE_GATES,
            ProtocolCatalog,
        )

        for code in (
            domain.ReadinessReasonCode.DECLINED_VALUE_NOT_RESOLVED.value,
            domain.ReadinessReasonCode.EXCESSIVE_DECLINED_VALUES.value,
        ):
            with self.subTest(code=code):
                self.assertIn(code, _ACKNOWLEDGEABLE_GATES)
                self.assertEqual(
                    ProtocolCatalog._BLOCKER_RESOLUTION[code]["action"],
                    "acknowledge_gate",
                )


class TheRealRefusalScenarioTests(unittest.TestCase):
    """The two segments that refused the two chunks, run through the validator.

    Not argued from the rules. A contract-satisfying response is generated
    offline for ord1 and ord2, edited to decline exactly the segment that
    refused the real run -- in-gel page 5 segment 7 and page 7 segment 10 --
    and pushed through ``parse_chunk_claim_response``.

    This is the check that says whether collecting is worth calls again. If it
    ever fails, the known blocker is back and a collection would buy nothing.
    """

    TARGETS = {1: (5, 7), 2: (7, 10)}

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))

    def _accept_with_declination(self, ordinal):
        import json

        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_chunk_analysis import (
            ChunkAnalysisLimits,
            extraction_for_chunk,
            plan_protocol_chunks,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            CLAIM_ANALYSIS_SYSTEM_PROMPT,
            claim_response_schema,
            generate_page_evidence_segments,
            parse_chunk_claim_response,
            prepare_chunk_claim_request_context,
        )

        from prototype_claim_chunks import ExactNumberedStepClaimModel

        page, index = self.TARGETS[ordinal]
        extraction = extract_protocol_pdf(IN_GEL)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
        )
        chunk = next(item for item in plan.chunks if item.ordinal == ordinal)
        scoped = extraction_for_chunk(extraction, chunk)
        request = prepare_chunk_claim_request_context(
            scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
            core_page_refs=chunk.core_page_refs,
            context_page_refs=chunk.overlap_page_refs,
        )
        payload = json.loads(
            ExactNumberedStepClaimModel(extraction).analyze(
                system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
                input_json=request.input_json(),
                response_schema=claim_response_schema(request),
            )
        )
        segment = generate_page_evidence_segments(
            extraction,
            source_revision=chunk.candidate_revision_id,
            page_number=page,
        )[index]
        handle = next(
            item.handle
            for item in request.pages
            for item in item.evidence
            if item.segment.segment_id == segment.segment_id
        )
        payload["claims"] = [
            claim
            for claim in payload["claims"]
            if handle
            not in (claim.get("evidence") or {}).get("evidence_segment_ids", [])
        ]
        for coverage in payload["page_coverage"]:
            if coverage["source_page_number"] == page:
                coverage["declined_evidence_segment_ids"] = sorted(
                    set(coverage.get("declined_evidence_segment_ids") or [])
                    | {handle}
                )
        analysis = parse_chunk_claim_response(
            json.dumps(payload),
            extraction=scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            core_page_refs=chunk.core_page_refs,
            request=request,
        )
        return extraction, chunk, analysis, page

    def test_ord1_and_ord2_are_no_longer_discarded_for_them(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            pages_declining_excessive_values,
            pages_declining_stated_values,
        )

        for ordinal in (1, 2):
            with self.subTest(ordinal=ordinal):
                extraction, chunk, analysis, page = (
                    self._accept_with_declination(ordinal)
                )
                # Accepted, and the claims that were right are still here.
                self.assertGreater(len(analysis.claims), 20)
                # And it is not waved through: the declination is a blocker.
                self.assertEqual(
                    pages_declining_stated_values(
                        extraction,
                        analysis.page_coverage,
                        source_revision=chunk.candidate_revision_id,
                    ),
                    (page,),
                )
                # One declination is nowhere near "this page was not read".
                self.assertEqual(
                    pages_declining_excessive_values(
                        extraction,
                        analysis.page_coverage,
                        source_revision=chunk.candidate_revision_id,
                    ),
                    (),
                )


def _synthetic_protocol(case):
    if not IN_GEL.is_file():
        case.skipTest(f"{IN_GEL} is not present.")
    from tests.test_pdf_to_session_walkthrough import _pipeline

    return _pipeline()[3].protocol


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
