"""The five paid chunks merge, and the guards that let them are still guards.

Extending the rename to every cross-chunk collision removed the thing that had
been standing in for a contradiction check. It was never a good one: it fired
on two chunks reaching for the same word and stayed silent on one chunk saying
two incompatible things about one sentence. Both replacements are asserted
here, because relaxing the first without the second would have removed the
wrong watchman and posted no other.

The materials a model attached to a step are not overturned either. The domain
keeps materials at document level -- a rule the prompt states zero times -- so
the claim is scoped as the domain requires and what the model said is written
down beside it, marked unverified, where nothing can mistake it for a check.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_GEL = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"


def _merge_from_cache():
    """Merge whatever the cache holds for in-gel. No provider call."""

    from voice_workflow_agent.chunk_analysis_cache import (
        ChunkAnalysisCache,
        key_for_chunk,
    )
    from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
    from voice_workflow_agent.protocol_chunk_analysis import (
        ChunkAnalysisLimits,
        ValidatedChunkResult,
        assemble_validated_protocol_claims,
        extraction_for_chunk,
        merge_validated_chunk_results,
        plan_protocol_chunks,
    )
    from voice_workflow_agent.protocol_claim_analysis import (
        prepare_chunk_claim_request_context,
    )

    extraction = extract_protocol_pdf(IN_GEL)
    plan = plan_protocol_chunks(
        extraction,
        f"protocol-{extraction.sha256[:32]}",
        "pdf-1",
        limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
    )
    cache = ChunkAnalysisCache()
    results = []
    for chunk in plan.chunks:
        scoped = extraction_for_chunk(extraction, chunk)
        request = prepare_chunk_claim_request_context(
            scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
            core_page_refs=chunk.core_page_refs,
            context_page_refs=chunk.overlap_page_refs,
        )
        hit = cache.load(
            key_for_chunk(extraction, chunk, request),
            extraction=scoped,
            request=request,
        )
        if hit is None:
            return None
        results.append(ValidatedChunkResult(chunk, hit.analysis))
    merged = merge_validated_chunk_results(extraction, plan, tuple(results))
    return (
        extraction,
        plan,
        merged,
        assemble_validated_protocol_claims(extraction, merged),
    )


class TheRealDocumentMergesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        built = _merge_from_cache()
        if built is None:
            raise unittest.SkipTest("in-gel is not fully cached on this machine.")
        cls.extraction, cls.plan, cls.merged, cls.draft = built

    def test_every_numbered_step_survives_in_order(self) -> None:
        steps = [
            step
            for section in self.draft.protocol.sections
            for step in section.steps
        ]
        self.assertEqual(
            [step.source_label for step in steps],
            [str(number) for number in range(1, 26)],
        )

    def test_the_rename_never_reaches_the_assembled_document(self) -> None:
        """1-4: STEP 36 regressed eight tests here by prefixing everything."""

        prefixes = tuple(f"{chunk.chunk_id}." for chunk in self.plan.chunks)
        leaked = []
        for section in self.draft.protocol.sections:
            if section.section_id.startswith(prefixes):
                leaked.append(section.section_id)
            for step in section.steps:
                if step.step_id.startswith(prefixes):
                    leaked.append(step.step_id)
        for construct in self.draft.protocol.constructs:
            for attribute in ("repetition_id", "ambiguity_id"):
                value = getattr(construct, attribute, None)
                if isinstance(value, str) and value.startswith(prefixes):
                    leaked.append(value)
        self.assertEqual(leaked, [])
        # And it really did rename something, or this proves nothing.
        self.assertTrue(
            any(claim.claim_id.startswith(prefixes) for claim in self.merged.claims)
        )

    def test_every_cited_address_still_resolves(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            generate_page_evidence_segments,
        )

        known: dict[int, set[str]] = {}
        for claim in self.merged.claims:
            page = claim.evidence.source_page_number
            if page not in known:
                known[page] = {
                    segment.segment_id
                    for segment in generate_page_evidence_segments(
                        self.extraction,
                        source_revision=self.merged.source_revision,
                        page_number=page,
                    )
                }
            for segment_id in claim.evidence.evidence_segment_ids:
                self.assertIn(segment_id, known[page])

    def test_the_title_came_from_the_file_and_says_so(self) -> None:
        self.assertTrue(self.draft.title_taken_from_the_file)
        self.assertEqual(
            self.draft.protocol.metadata.title,
            self.extraction.metadata.title,
        )


class ConditionATests(unittest.TestCase):
    """A duplicate inside one chunk is self-contradiction, and still refused."""

    def test_a_chunk_may_not_use_one_identifier_twice(self) -> None:
        import ast
        import inspect

        from voice_workflow_agent import protocol_claim_analysis

        source = inspect.getsource(protocol_claim_analysis)
        self.assertIn("duplicate_evidence_item_identifier", source)
        # It is raised where a chunk's own items are counted, not at the merge.
        tree = ast.parse(source)
        raising = [
            node.lineno
            for node in ast.walk(tree)
            for keyword in getattr(node, "keywords", [])
            if getattr(keyword, "arg", None) == "reason_code"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "duplicate_evidence_item_identifier"
        ]
        self.assertTrue(raising)
        whole = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "validate_whole_protocol_claims"
        )
        for line in raising:
            self.assertFalse(whole.lineno <= line <= whole.end_lineno)


class ConditionBTests(unittest.TestCase):
    """The guard that replaces what the rename used to catch by accident."""

    def _analysis(self, claims):
        from voice_workflow_agent.protocol_claim_analysis import (
            ProtocolChunkClaimAnalysis,
        )

        return ProtocolChunkClaimAnalysis(
            claim_schema_version=10,
            capability_policy_id="p1-conservative",
            source_revision="pdf-1",
            source_sha256="0" * 64,
            chunk_id="chunk-1",
            page_coverage=(),
            structure=(),
            claims=tuple(claims),
        )

    def _claim(self, claim_id, category, *, segments=("seg-a",), labels=None,
               count=None, required=True):
        from voice_workflow_agent.protocol_claim_analysis import (
            ClaimSourceEvidence,
            ProtocolClaim,
        )

        return ProtocolClaim(
            claim_id=claim_id,
            category=category,
            source_order=0,
            source_text="text",
            section_id=None,
            step_id=None,
            source_label=None,
            target_claim_id=None,
            required_for_execution=required,
            evidence=ClaimSourceEvidence(
                source_revision="pdf-1",
                source_sha256="0" * 64,
                source_page_number=1,
                page_text_sha256="0" * 64,
                evidence_segment_ids=tuple(segments),
                source_excerpt="text",
            ),
            repeated_step_labels=labels,
            repetition_count=count,
        )

    def _check(self, claims):
        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            _refuse_contradictory_claims,
        )

        if not IN_GEL.is_file():
            self.skipTest(f"{IN_GEL} is not present.")
        _refuse_contradictory_claims(
            self._analysis(claims),
            "chunk-1",
            "pdf-1",
            extract_protocol_pdf(IN_GEL),
        )

    def test_two_repeats_on_one_sentence_declaring_different_ranges_fail(self):
        from voice_workflow_agent.protocol_claim_analysis import (
            ClaimCategory,
            ProtocolAnalysisEvidenceError,
        )

        with self.assertRaises(ProtocolAnalysisEvidenceError) as caught:
            self._check(
                [
                    self._claim("r1", ClaimCategory.REPEAT_CONDITION, labels=("2", "7")),
                    self._claim("r2", ClaimCategory.REPEAT_CONDITION, labels=("8", "9")),
                ]
            )
        self.assertEqual(
            caught.exception.diagnostic.reason_code,
            "contradictory_repetition_claims",
        )

    def test_one_passage_that_must_and_need_not_be_done_fails(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            ClaimCategory,
            ProtocolAnalysisEvidenceError,
        )

        with self.assertRaises(ProtocolAnalysisEvidenceError) as caught:
            self._check(
                [
                    self._claim("q1", ClaimCategory.QUANTITY, required=True),
                    self._claim("q2", ClaimCategory.QUANTITY, required=False),
                ]
            )
        self.assertEqual(
            caught.exception.diagnostic.reason_code,
            "contradictory_execution_requirement",
        )

    def test_the_ordinary_case_is_not_refused(self) -> None:
        """One segment, two quantities, and an action beside a repetition.

        Measured on in-gel: 18 evidence addresses carry more than one claim and
        every one of them is a case like this. A guard that refused these would
        refuse the document for being written normally.
        """

        from voice_workflow_agent.protocol_claim_analysis import ClaimCategory

        self._check(
            [
                self._claim("q1", ClaimCategory.QUANTITY),
                self._claim("q2", ClaimCategory.QUANTITY),
                self._claim("a1", ClaimCategory.ACTION),
                self._claim("r1", ClaimCategory.REPEAT_CONDITION, labels=("2", "7")),
            ]
        )

    def test_different_passages_are_never_compared(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import ClaimCategory

        self._check(
            [
                self._claim("r1", ClaimCategory.REPEAT_CONDITION,
                            segments=("seg-a",), labels=("2", "7")),
                self._claim("r2", ClaimCategory.REPEAT_CONDITION,
                            segments=("seg-b",), labels=("8", "9")),
            ]
        )


class ConditionCTests(unittest.TestCase):
    """A preserved attribution is the model's word and opens nothing."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        built = _merge_from_cache()
        if built is None:
            raise unittest.SkipTest("in-gel is not fully cached on this machine.")
        cls.extraction, cls.plan, cls.merged, cls.draft = built

    def test_what_the_model_said_is_kept_verbatim_and_marked(self) -> None:
        kept = self.merged.unverified_step_attributions
        self.assertTrue(kept)
        for item in kept:
            with self.subTest(claim=item["claim_id"]):
                self.assertEqual(item["status"], "model_claim_unverified")
                self.assertTrue(item["stated_by_the_model"])
                self.assertIn("step_id", item["stated_by_the_model"])

    def test_the_claim_itself_carries_no_step_scoping(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import ClaimCategory

        kept = {item["claim_id"] for item in self.merged.unverified_step_attributions}
        for claim in self.merged.claims:
            if claim.claim_id not in kept:
                continue
            with self.subTest(claim=claim.claim_id):
                self.assertIsNone(claim.step_id)
                self.assertIsNone(claim.section_id)
                self.assertIsNone(claim.target_claim_id)
                self.assertIn(
                    claim.category,
                    {
                        ClaimCategory.MATERIAL,
                        ClaimCategory.EQUIPMENT,
                        ClaimCategory.PREREQUISITE,
                    },
                )

    def test_a_preserved_attribution_makes_nothing_executable(self) -> None:
        """Condition C: it is a record, never a readiness input."""

        from voice_workflow_agent import experiment_protocol as domain

        self.assertIs(
            self.draft.readiness.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )
        # It is not a readiness reason, and it does not remove one either.
        codes = set(self.draft.readiness.reason_codes)
        self.assertTrue(codes)
        self.assertEqual(
            [code for code in codes if "attribution" in code or "unverified" in code],
            [],
        )

    def test_it_is_distinguishable_from_a_checked_fact(self) -> None:
        """The only place these live is the unverified list, by name."""

        from dataclasses import fields

        from voice_workflow_agent.protocol_claim_analysis import (
            MergedProtocolClaims,
        )

        names = {item.name for item in fields(MergedProtocolClaims)}
        self.assertIn("unverified_step_attributions", names)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
