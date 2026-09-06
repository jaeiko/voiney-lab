"""A rule the server enforces must be a rule the provider was told.

Three refusals in four steps were for rules nobody had written down, and each
was discovered by spending provider calls. This is the check that turns that
class of defect into a failing test instead of a failed run.
"""

from __future__ import annotations

import unittest

from voice_workflow_agent.claim_contract_audit import (
    ALTERNATIVE_RULES,
    CITE_DOCUMENT_LEVEL,
    CONTRACT_EVIDENCE,
    PROMPT,
    SCHEMA,
    SERVER_ONLY,
    collect_refusal_codes,
    cornered_segments,
    prompt_permits,
    segment_positions,
    server_accepts,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    extraction_for_chunk,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    claim_response_schema,
    prepare_chunk_claim_request_context,
)

import tempfile
from pathlib import Path

from tests.test_protocol_catalog import write_text_pdf


def _flat_prompt() -> str:
    return " ".join(CLAIM_ANALYSIS_SYSTEM_PROMPT.split())


def _example_schema():
    temp = tempfile.TemporaryDirectory()
    source = Path(temp.name) / "source.pdf"
    write_text_pdf(
        source,
        "Protocol Test\nSection preparation\n1. Add 10 mL of buffer.",
        title="Protocol Test",
    )
    extraction = extract_protocol_pdf(source)
    plan = plan_protocol_chunks(
        extraction,
        f"protocol-{extraction.sha256[:32]}",
        "pdf-1",
        limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
    )
    chunk = plan.chunks[0]
    request = prepare_chunk_claim_request_context(
        extraction_for_chunk(extraction, chunk),
        source_revision=chunk.candidate_revision_id,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        core_page_refs=chunk.core_page_refs,
        context_page_refs=chunk.overlap_page_refs,
    )
    return claim_response_schema(request), temp


def _resolve(schema, dotted: str):
    node = schema
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
            continue
        if isinstance(node, dict) and "$defs" in node and part in node["$defs"]:
            node = node["$defs"][part]
            continue
        return None
    return node


class ContractAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema, cls._temp = _example_schema()
        cls.codes = collect_refusal_codes()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def test_every_refusal_code_says_where_its_rule_was_stated(self) -> None:
        """A refusal with no entry here is a rule the provider never saw."""

        unexplained = sorted(set(self.codes) - set(CONTRACT_EVIDENCE))
        self.assertEqual(
            unexplained,
            [],
            "these refusals enforce a rule that is not recorded as stated "
            "anywhere; add the evidence to CONTRACT_EVIDENCE, and if there is "
            "none, state the rule in the prompt or the schema first",
        )

    def test_the_table_has_no_entries_for_refusals_that_do_not_exist(self):
        """A stale entry would let a real gap hide behind it."""

        stale = sorted(set(CONTRACT_EVIDENCE) - set(self.codes))
        self.assertEqual(stale, [])

    def test_every_prompt_claim_is_actually_in_the_prompt(self) -> None:
        """The recorded phrase must be there verbatim, not merely plausible."""

        prompt = _flat_prompt()
        missing = sorted(
            code
            for code, evidence in CONTRACT_EVIDENCE.items()
            if evidence.kind == PROMPT and evidence.detail not in prompt
        )
        self.assertEqual(missing, [])

    def test_every_schema_claim_resolves_to_a_real_schema_node(self) -> None:
        missing = sorted(
            code
            for code, evidence in CONTRACT_EVIDENCE.items()
            if evidence.kind == SCHEMA
            and _resolve(self.schema, evidence.detail) is None
        )
        self.assertEqual(missing, [])

    def test_server_only_refusals_say_why_a_provider_is_not_at_fault(self):
        for code, evidence in sorted(CONTRACT_EVIDENCE.items()):
            if evidence.kind != SERVER_ONLY:
                continue
            with self.subTest(code=code):
                self.assertGreater(len(evidence.detail.split()), 3)

    def test_the_three_defects_this_audit_exists_for_are_covered(self) -> None:
        """The regressions that motivated it, named."""

        for code, kind in (
            ("chunk_identity_mismatch", SCHEMA),
            ("protocol_title_missing_or_conflicting", PROMPT),
            ("declined_segment_states_a_value", PROMPT),
        ):
            with self.subTest(code=code):
                self.assertIn(code, CONTRACT_EVIDENCE)
                self.assertEqual(CONTRACT_EVIDENCE[code].kind, kind)

    def test_the_audit_reads_across_file_boundaries(self) -> None:
        """The title refusal lives in a different module from most of them."""

        self.assertGreaterEqual(len(self.codes), 50)
        modules = {module for paths in self.codes.values() for module in paths}
        self.assertGreaterEqual(len(modules), 3)


#: Every local source, and none of them required. Three are user-owned files
#: outside the repository; a machine without them still runs the synthetic
#: corpus below, which is the part that must never be skipped.
_LOCAL_SOURCES = {
    "in-gel": "data/runtime/candidate-a-source/in-gel-digestion.pdf",
    "headspace": "usingdynamicheadspacecollections.pdf",
    "intracellular": "intracellularmetaboliteextraction.pdf",
    "ANKOM": (
        "data/runtime/candidate-a-live-acceptance/objects/sha256/53/"
        "5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
    ),
}

_ROOT = Path(__file__).resolve().parents[1]


def _historical_step_30_rules():
    """The wording STEP 30 replaced, as a rule table.

    The escape hatch sent a value-bearing segment inside a numbered step to a
    document-level claim, which the attachment rule forbids. Reconstructed here
    so the checker can be shown catching the defect it was written for -- a
    contradiction detector that has only ever returned zero has not been
    tested.
    """

    from dataclasses import replace

    return tuple(
        replace(
            rule,
            permits=frozenset({CITE_DOCUMENT_LEVEL}),
            phrase=(
                "claim it as a document-level claim of the category that fits"
                " the value"
            ),
        )
        if rule.name == "evidence_inside_a_step_belongs_to_that_step"
        else rule
        for rule in ALTERNATIVE_RULES
    )


class ProhibitionsLeaveSomethingToDoTests(unittest.TestCase):
    """A rule may forbid a move only if another move is still open.

    STEP 26, 28 and 30 each cost provider calls to find a rule the model could
    not obey. The first two were rules nobody had written down, which the table
    above now catches. The third was different and worse: every rule was
    written down, and two of them disagreed. For a value-bearing segment inside
    a numbered step the value-honesty escape hatch named a document-level claim
    and the positional attachment rule forbade exactly that. Both moves were
    refused, and there was no third.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.prompt = _flat_prompt()

    def _positions(self, extraction):
        return segment_positions(extraction)

    def _synthetic(self):
        """A corpus that reaches every branch without needing a real source."""

        from tests.test_protocol_claim_analysis import write_lined_pages

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        source = Path(temp.name) / "corpus.pdf"
        write_lined_pages(
            source,
            (
                (
                    "Reagent preparation",
                    "Dissolve 5 g of powder in 100 ml of water.",
                    "1. Add 10 ml of buffer to the tube.",
                    "Note: the tube holds 50 ml, do not overfill.",
                    "2. Incubate for 15 min at 37 C.",
                    "Danger, highly corrosive.",
                ),
                (
                    "3 Centrifuge at 800 rpm",
                    "for 10 min, then discard the supernatant.",
                    "In-house protocol v3   Page 2 of 2",
                ),
            ),
        )
        return extract_protocol_pdf(source)

    def test_every_recorded_alternative_is_worded_that_way_in_the_prompt(self):
        """A rule table that has drifted from the prompt checks nothing."""

        missing = sorted(
            rule.name
            for rule in ALTERNATIVE_RULES
            if rule.phrase not in self.prompt
        )
        self.assertEqual(missing, [])

    def test_no_synthetic_segment_is_left_with_nothing_to_do(self) -> None:
        positions = self._positions(self._synthetic())
        self.assertTrue(any(item.carries_value for item in positions))
        self.assertTrue(any(item.inside_a_step for item in positions))
        self.assertTrue(any(item.outside_every_step for item in positions))
        self.assertEqual(cornered_segments(positions), ())

    def test_the_checker_catches_the_defect_it_was_written_for(self) -> None:
        """Under STEP 30's wording the same corpus must be full of holes."""

        positions = self._positions(self._synthetic())
        cornered = cornered_segments(positions, _historical_step_30_rules())
        self.assertTrue(
            cornered,
            "the reconstructed STEP 30 prompt corners nobody, so this test is "
            "not testing the checker",
        )
        position, permitted, accepted = cornered[0]
        self.assertEqual(permitted, frozenset())
        self.assertIn("cite_targeting_the_enclosing_step", accepted)
        self.assertTrue(position.inside_a_step and position.carries_value)

    def test_no_real_source_segment_is_left_with_nothing_to_do(self) -> None:
        """The four local documents, for as many as this machine holds."""

        measured = 0
        for name, relative in sorted(_LOCAL_SOURCES.items()):
            source = _ROOT / relative
            if not source.is_file():
                continue
            with self.subTest(document=name):
                positions = self._positions(extract_protocol_pdf(source))
                measured += 1
                self.assertEqual(
                    [
                        (item.page_number, item.segment_index, sorted(accepted))
                        for item, _permitted, accepted in cornered_segments(
                            positions
                        )
                    ],
                    [],
                )
                # And the same corpus under the old wording, so a machine that
                # has the sources also confirms the checker bites on them.
                self.assertTrue(
                    cornered_segments(positions, _historical_step_30_rules())
                )
        if not measured:
            self.skipTest("no local source document is present")

    def test_a_segment_never_straddles_a_step_boundary(self) -> None:
        """Neither inside a step nor clear of every step: citable nowhere.

        Boundaries are cut at every numbered label, so a segment cannot span
        one. That is what makes the three moves exhaustive, and it is measured
        rather than assumed: if it ever stopped holding, a segment could be
        forced to a citation with no legal place to attach.
        """

        positions = list(self._positions(self._synthetic()))
        for name, relative in sorted(_LOCAL_SOURCES.items()):
            source = _ROOT / relative
            if source.is_file():
                positions.extend(self._positions(extract_protocol_pdf(source)))
        self.assertEqual(
            [
                (item.page_number, item.segment_index)
                for item in positions
                if item.straddles_a_step_boundary
            ],
            [],
        )

    def test_a_prompt_stricter_than_the_server_is_not_a_contradiction(self):
        """Only the empty intersection is a defect, not any disagreement.

        The prompt tells the model to cite every value-bearing segment, while
        the server only refuses the decline for one inside a numbered step. A
        segment outside every step is therefore permitted less than it would be
        accepted, and that is fine -- the model gives up a move it did not need.
        A check that flagged this would report the contract as broken on 253 of
        the 600 real segments and be ignored accordingly.
        """

        outside = [
            item
            for item in self._positions(self._synthetic())
            if item.substantive and item.carries_value and item.outside_every_step
        ]
        self.assertTrue(outside)
        for item in outside:
            with self.subTest(page=item.page_number, index=item.segment_index):
                self.assertNotIn("decline", prompt_permits(item))
                self.assertIn("decline", server_accepts(item))
                self.assertTrue(prompt_permits(item) & server_accepts(item))


class ARepeatMayReachBackPastItsChunkTests(unittest.TestCase):
    """A range that starts in an earlier chunk must still be declared.

    The prompt used to say every label in a repeat's range "must be a numbered
    step you also claimed", which reads, inside a chunk, as a prohibition on
    declaring a range that begins on somebody else's pages. In-gel is exactly
    that case: labels 1 and 2 are on page 3, chunk 0, and "7 Repeat steps 2-7
    until the gel band is fully destained" is on page 5, chunk 1.

    The alternative offered -- explicit_missing_ambiguous_value -- is for when
    the source does not say which steps to repeat, and this source says so
    plainly. So obeying the sentence left one move: claim the numbered action
    and say nothing about the repeat. Nothing refuses that. The step assembles,
    the document passes, and an operator destains once where the source asks
    for six steps repeated until the band is clear. A refusal costs a provider
    call; this costs an experiment, quietly.

    The server never had that rule. Ranges resolve against the whole assembled
    document, so the claim is admissible exactly as the source states it, and
    the prompt now says so.
    """

    def test_the_prompt_says_a_range_may_begin_outside_the_chunk(self) -> None:
        prompt = _flat_prompt()
        self.assertIn(
            "the labels are resolved against the whole assembled document, not"
            " against your chunk",
            prompt,
        )
        self.assertNotIn(
            "every label from the first to the last must be a numbered step"
            " you also claimed",
            prompt,
        )

    def test_such_a_repeat_survives_the_merge_and_assembly(self) -> None:
        """Measured on the real document, not argued from the rules."""

        source = (
            _ROOT / "data" / "runtime" / "candidate-a-source"
            / "in-gel-digestion.pdf"
        )
        if not source.is_file():
            self.skipTest(f"{source} is not present.")
        import sys

        sys.path.insert(0, str(_ROOT / "scripts"))
        from tests.test_pdf_to_session_walkthrough import _pipeline

        extraction, plan, merged, draft = _pipeline()
        chunk_of = {
            page: chunk.ordinal
            for chunk in plan.chunks
            for page in chunk.core_page_refs
        }
        label_pages = {}
        from voice_workflow_agent.protocol_claim_analysis import (
            _numbered_action_matches,
        )

        for number in range(1, extraction.page_count + 1):
            for match in _numbered_action_matches(extraction.pages[number - 1].text):
                label_pages.setdefault(match.group("label"), number)

        crossing = [
            claim
            for claim in merged.claims
            if claim.repeated_step_labels is not None
            and chunk_of.get(label_pages.get(claim.repeated_step_labels[0]))
            != chunk_of.get(claim.evidence.source_page_number)
        ]
        self.assertTrue(
            crossing,
            "no repeat in this document reaches back past its own chunk, so "
            "this test is no longer measuring what it was written for",
        )
        claim = crossing[0]
        self.assertEqual(claim.repeated_step_labels, ("2", "7"))
        self.assertEqual(claim.evidence.source_page_number, 5)
        self.assertEqual(label_pages["2"], 3)
        self.assertNotEqual(chunk_of[5], chunk_of[3])

        # And it is in the assembled document, covering all six steps it names
        # rather than the one it happens to sit in.
        steps = {
            step.source_label: step.step_id
            for section in draft.protocol.sections
            for step in section.steps
        }
        covered = {
            construct.repetition_id: tuple(construct.repeated_step_ids)
            for construct in draft.protocol.constructs
            if hasattr(construct, "repeated_step_ids")
        }
        self.assertIn(
            tuple(steps[str(label)] for label in range(2, 8)),
            set(covered.values()),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
