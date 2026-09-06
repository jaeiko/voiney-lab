"""What a chunk could not know, and what it must not be allowed to blur.

A chunk is analysed alone: the model sees its own pages and nothing else. Three
consequences of that were being treated as document faults and cost the whole
document at merge time.

* Two chunks reach for the same handle -- ``c1`` -- because neither can see
  what the other named.
* No chunk supplies a protocol title, because in-gel's title sits inside a
  1143-character front-matter segment with the date and DOI and there is
  nothing precise to cite.
* Three chunks leave ``section_id`` empty, because pages 7, 8 and 9 print no
  heading and inventing one would be worse.

None of those is the model being wrong. What is *not* relaxed is the case that
looks the same and is not: one handle standing for two different passages of
the source. That ambiguity is exactly what a merge exists to refuse, and no
rename makes it unambiguous.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_GEL = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"


class NameOverlapVersusConflictTests(unittest.TestCase):
    """The discrimination is made on the citation, not on the name."""

    def test_the_same_passage_under_one_name_is_an_overlap(self) -> None:
        from voice_workflow_agent.protocol_chunk_analysis import (
            _cited_the_same_place,
        )

        from tests.test_protocol_claim_analysis import (
            ProtocolClaimAnalysisTests,  # noqa: F401  (import guard only)
        )

        class Item:
            def __init__(self, evidence, source_text):
                self.evidence = evidence
                self.source_text = source_text

        class Evidence:
            def __init__(self, page, segments, excerpt):
                self.source_page_number = page
                self.evidence_segment_ids = segments
                self.source_excerpt = excerpt

        same = Item(Evidence(3, ("seg-a",), "excerpt"), "excerpt")
        also = Item(Evidence(3, ("seg-a",), "excerpt"), "excerpt")
        other = Item(Evidence(4, ("seg-b",), "different"), "different")

        self.assertTrue(_cited_the_same_place(same, also))
        self.assertFalse(_cited_the_same_place(same, other))

    def test_a_missing_citation_is_never_treated_as_a_match(self) -> None:
        """Fail closed on shape: no evidence is not evidence of sameness."""

        from voice_workflow_agent.protocol_chunk_analysis import (
            _cited_the_same_place,
        )

        class Bare:
            evidence = None
            source_text = "x"

        self.assertFalse(_cited_the_same_place(Bare(), Bare()))


class SectionInheritanceTests(unittest.TestCase):
    """3-1 and 3-3: carried forward, never guessed, and always marked."""

    def _marker(self, section_id, page, order):
        class Kind:
            value = "section"

        class Evidence:
            source_page_number = page
            source_order = order

        class Marker:
            pass

        marker = Marker()
        marker.section_id = section_id
        marker.kind = Kind()
        marker.evidence = Evidence()
        marker.source_order = order
        return marker

    def _action(self, step_id, page, order, section_id=None):
        from dataclasses import dataclass, field

        from voice_workflow_agent.protocol_claim_analysis import ClaimCategory

        @dataclass
        class Evidence:
            source_page_number: int

        @dataclass
        class Action:
            step_id: str
            section_id: object
            source_order: int
            evidence: Evidence
            claim_id: str
            target_claim_id: object = None
            category: object = ClaimCategory.ACTION

        return Action(step_id, section_id, order, Evidence(page), f"claim-{step_id}")

    def test_a_step_below_a_heading_inherits_it(self) -> None:
        from voice_workflow_agent.protocol_chunk_analysis import (
            _inherit_declared_section,
        )

        markers = (self._marker("prep", 3, 0), self._marker("digest", 5, 0))
        claims = (
            self._action("step-1", 3, 1),
            self._action("step-9", 7, 0),
        )
        updated, inherited = _inherit_declared_section(markers, claims)
        self.assertEqual([item.section_id for item in updated], ["prep", "digest"])
        self.assertEqual(inherited, ("step-1", "step-9"))

    def test_a_step_above_every_heading_inherits_nothing(self) -> None:
        """3-1: no section declared before it, so none is supplied."""

        from voice_workflow_agent.protocol_chunk_analysis import (
            _inherit_declared_section,
        )

        markers = (self._marker("prep", 5, 0),)
        claims = (self._action("step-0", 2, 0),)
        updated, inherited = _inherit_declared_section(markers, claims)
        self.assertIsNone(updated[0].section_id)
        self.assertEqual(inherited, ())

    def test_a_document_with_no_heading_supplies_nothing(self) -> None:
        from voice_workflow_agent.protocol_chunk_analysis import (
            _inherit_declared_section,
        )

        claims = (self._action("step-1", 3, 0),)
        updated, inherited = _inherit_declared_section((), claims)
        self.assertIsNone(updated[0].section_id)
        self.assertEqual(inherited, ())

    def test_a_step_that_named_its_own_section_keeps_it(self) -> None:
        from voice_workflow_agent.protocol_chunk_analysis import (
            _inherit_declared_section,
        )

        markers = (self._marker("prep", 3, 0),)
        claims = (self._action("step-1", 7, 0, section_id="stated-by-the-source"),)
        updated, inherited = _inherit_declared_section(markers, claims)
        self.assertEqual(updated[0].section_id, "stated-by-the-source")
        self.assertEqual(inherited, ())


class TitleFromTheFileTests(unittest.TestCase):
    """2-1 to 2-3: the server's own copy, marked as such, unblocking nothing."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")

    def test_it_is_the_file_s_title_and_says_where_it_came_from(self) -> None:
        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            MergedProtocolClaims,
            _title_from_the_file,
        )

        extraction = extract_protocol_pdf(IN_GEL)
        merged = MergedProtocolClaims(
            protocol_id="p",
            source_revision="pdf-1",
            source_sha256=extraction.sha256,
            capability_policy_id="p1-conservative",
            required_chunk_ids=(),
            page_coverage=(),
            structure=(),
            claims=(),
        )
        marker = _title_from_the_file(extraction, merged)
        self.assertEqual(marker.source_text, extraction.metadata.title)
        # A reviewer must be able to tell this from a model's assertion.
        self.assertEqual(marker.marker_id, "title-read-from-the-source-file")
        # And it cites a real segment, so it resolves like any other citation.
        self.assertEqual(marker.evidence.source_page_number, 1)
        self.assertEqual(len(marker.evidence.evidence_segment_ids), 1)

    def test_a_file_with_no_title_produces_no_title_rather_than_a_guess(self):
        from dataclasses import replace as _replace

        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            MergedProtocolClaims,
            _title_from_the_file,
        )

        extraction = extract_protocol_pdf(IN_GEL)
        untitled = _replace(
            extraction, metadata=_replace(extraction.metadata, title="")
        )
        merged = MergedProtocolClaims(
            protocol_id="p",
            source_revision="pdf-1",
            source_sha256=untitled.sha256,
            capability_policy_id="p1-conservative",
            required_chunk_ids=(),
            page_coverage=(),
            structure=(),
            claims=(),
        )
        marker = _title_from_the_file(untitled, merged)
        self.assertEqual(marker.source_text, "")
        self.assertEqual(marker.evidence.evidence_segment_ids, ())

    def test_two_titles_still_fail_closed(self) -> None:
        """Only the absent case was relaxed. A disagreement is still refused."""

        import inspect

        from voice_workflow_agent import protocol_claim_analysis

        body = inspect.getsource(
            protocol_claim_analysis.validate_whole_protocol_claims
        )
        self.assertIn("if len(title_markers) > 1:", body)
        self.assertIn("protocol_title_missing_or_conflicting", body)


class ChunkLocalPreChecksTests(unittest.TestCase):
    """5-2 and 5-4: judged from one chunk, before the merge is reached."""

    def test_the_moved_rules_are_raised_in_chunk_validation(self) -> None:
        import ast
        import pathlib

        from voice_workflow_agent import protocol_claim_analysis

        tree = ast.parse(
            pathlib.Path(protocol_claim_analysis.__file__).read_text()
        )
        target = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_refuse_chunk_local_inconsistency"
        )
        raised = {
            node.args[0].value
            for node in ast.walk(target)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "fail"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }
        self.assertEqual(
            raised,
            {
                "action_structure_invalid",
                "claim_target_invalid",
                "missing_value_scope_invalid",
            },
        )

    def test_the_deferred_rule_is_deliberately_not_here(self) -> None:
        """top_level_claim_scope_invalid is STEP 36's defect (4), still open.

        Adding it would settle an open question by deleting the two paid chunks
        whose fate depends on the answer, since a cached payload that fails
        revalidation is discarded.
        """

        import inspect

        from voice_workflow_agent import protocol_claim_analysis

        body = inspect.getsource(
            protocol_claim_analysis._refuse_chunk_local_inconsistency
        )
        self.assertIn("top_level_claim_scope_invalid", body)
        self.assertIn("deliberately absent", body)
        # Named in the comment, never raised.
        self.assertNotIn('fail("top_level_claim_scope_invalid"', body)


class ThePaidChunksSurviveTests(unittest.TestCase):
    """8-4: nothing in this step may cost a call already spent."""

    def test_every_cached_chunk_still_loads(self) -> None:
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

        if not IN_GEL.is_file():
            self.skipTest(f"{IN_GEL} is not present.")
        extraction = extract_protocol_pdf(IN_GEL)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
        )
        cache = ChunkAnalysisCache()
        loaded = []
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
            if cache.load(
                key_for_chunk(extraction, chunk, request),
                extraction=scoped,
                request=request,
            ):
                loaded.append(chunk.ordinal)
        if not loaded:
            self.skipTest("no chunk is cached on this machine.")
        self.assertEqual(loaded, [0, 1, 2, 3, 4])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
