"""A repeat must say which steps it repeats, and cite text that says so.

Two faults measured on real documents, both silent:

`repeated_step_ids` held only the enclosing step, so "repeat steps 2 to 7"
was recorded as repeating step 7. Put a bound on that and an operator is told
to re-run one step where the protocol asks for six -- a different experiment,
recorded without anyone being told.

A repeat construct cited the enclosing step's instruction while the repeat
sentence sat in the following prose, so the evidence for a construct asserting
a repetition did not contain the repetition instruction.

The rule is a shape, not a vocabulary: the claim declares two labels, and the
cited excerpt must contain those two numbers written as a range. Nothing reads
the words "repeat" or "steps", and nothing is inferred -- a repeat whose range
the source does not state is refused, never narrowed to one step.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_claim_analysis import (
    ClaimCategory,
    ProtocolClaimConsistencyError,
    _repeated_range_step_ids,
    excerpt_states_range,
)
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


class ExcerptMustStateTheRangeTests(unittest.TestCase):
    def test_a_range_in_the_excerpt_is_accepted(self) -> None:
        self.assertTrue(
            excerpt_states_range(
                "7 Repeat steps 2-7 until the gel band is fully destained.",
                "2",
                "7",
            )
        )

    def test_the_measured_failure_is_rejected(self) -> None:
        """The p6 excerpt: an instruction with no repetition in it."""

        self.assertFalse(
            excerpt_states_range(
                "9 Remove and discard the acetonitrile. Your gel band should "
                "have a whitish appearance when dry.",
                "8",
                "9",
            )
        )

    def test_a_different_range_is_rejected(self) -> None:
        self.assertFalse(
            excerpt_states_range("7 Repeat steps 2-7 until clear.", "3", "7")
        )

    def test_dash_variants_all_count(self) -> None:
        for dash in ("-", "‐", "–", "—", "−"):
            with self.subTest(dash=dash):
                self.assertTrue(
                    excerpt_states_range(f"repeat steps 17{dash}18", "17", "18")
                )

    def test_spacing_around_the_dash_is_tolerated(self) -> None:
        self.assertTrue(excerpt_states_range("steps 17 - 18 again", "17", "18"))

    def test_a_longer_number_is_not_a_partial_match(self) -> None:
        self.assertFalse(excerpt_states_range("steps 170-180", "17", "18"))

    def test_no_range_at_all_is_rejected(self) -> None:
        self.assertFalse(excerpt_states_range("repeat until clear", "2", "7"))


class RangeExpandsToEveryStepTests(unittest.TestCase):
    def _claim(self, labels):
        from voice_workflow_agent.protocol_claim_analysis import (
            ClaimSourceEvidence,
            ProtocolClaim,
        )

        return ProtocolClaim(
            claim_id="repeat-1",
            category=ClaimCategory.REPEAT_CONDITION,
            source_order=1,
            source_text="Repeat steps 2-7 until clear.",
            section_id="section-1",
            step_id="step-7",
            source_label=None,
            target_claim_id="action-7",
            required_for_execution=True,
            evidence=ClaimSourceEvidence(
                source_revision="pdf-1",
                source_sha256="0" * 64,
                source_page_number=5,
                page_text_sha256="1" * 64,
                evidence_segment_ids=("seg-1",),
                source_excerpt="Repeat steps 2-7 until clear.",
            ),
            repeated_step_labels=labels,
        )

    @property
    def steps(self):
        return {str(number): f"step-{number}" for number in range(1, 10)}

    def test_the_whole_range_is_named(self) -> None:
        self.assertEqual(
            _repeated_range_step_ids(self._claim(("2", "7")), self.steps),
            ("step-2", "step-3", "step-4", "step-5", "step-6", "step-7"),
        )

    def test_a_single_step_range_is_that_step(self) -> None:
        self.assertEqual(
            _repeated_range_step_ids(self._claim(("4", "4")), self.steps),
            ("step-4",),
        )

    def test_no_declared_range_is_refused(self) -> None:
        with self.assertRaises(ProtocolClaimConsistencyError) as caught:
            _repeated_range_step_ids(self._claim(None), self.steps)
        self.assertEqual(caught.exception.reason_code, "repeat_range_missing")

    def test_a_backwards_range_is_refused(self) -> None:
        with self.assertRaises(ProtocolClaimConsistencyError) as caught:
            _repeated_range_step_ids(self._claim(("7", "2")), self.steps)
        self.assertEqual(caught.exception.reason_code, "repeat_range_inverted")

    def test_a_range_naming_a_step_that_does_not_exist_is_refused(self) -> None:
        with self.assertRaises(ProtocolClaimConsistencyError) as caught:
            _repeated_range_step_ids(self._claim(("8", "12")), self.steps)
        self.assertEqual(caught.exception.reason_code, "repeat_range_step_unknown")

    def test_a_partly_resolvable_range_is_not_quietly_trimmed(self) -> None:
        """Repeating part of a range is a different experiment."""

        with self.assertRaises(ProtocolClaimConsistencyError):
            _repeated_range_step_ids(self._claim(("5", "11")), self.steps)


class OnTheRealDocumentTests(unittest.TestCase):
    """What the fix changes on the document that exposed it."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        from prototype_claim_chunks import ExactNumberedStepClaimModel

        extraction = extract_protocol_pdf(IN_GEL)
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
        )
        model = ExactNumberedStepClaimModel(extraction)
        merged = merge_validated_chunk_results(
            extraction,
            plan,
            tuple(
                ValidatedChunkResult(
                    chunk, analyze_protocol_chunk(extraction, chunk, model)
                )
                for chunk in plan.chunks
            ),
        )
        cls.protocol = assemble_validated_protocol_claims(
            extraction, merged
        ).protocol

    def _repeats(self):
        from voice_workflow_agent import experiment_protocol as domain

        return [
            construct
            for construct in self.protocol.constructs
            if isinstance(construct, domain.RepeatUntil)
        ]

    def test_the_range_is_recorded_not_the_enclosing_step_alone(self) -> None:
        repeats = {
            construct.repetition_id: construct.repeated_step_ids
            for construct in self._repeats()
        }
        self.assertEqual(
            repeats["repeat-p5-3"],
            ("step-2", "step-3", "step-4", "step-5", "step-6", "step-7"),
        )
        self.assertEqual(repeats["repeat-p6-0"], ("step-8", "step-9"))

    def test_every_repeat_cites_text_containing_its_range(self) -> None:
        for construct in self._repeats():
            with self.subTest(repetition=construct.repetition_id):
                first = construct.repeated_step_ids[0].removeprefix("step-")
                last = construct.repeated_step_ids[-1].removeprefix("step-")
                self.assertTrue(
                    excerpt_states_range(
                        construct.evidence.source_excerpt, first, last
                    ),
                    construct.evidence.source_excerpt,
                )

    def test_a_repeat_no_longer_cites_an_unrelated_instruction(self) -> None:
        """The p6 construct used to quote step 9's own instruction."""

        excerpts = {
            construct.repetition_id: construct.evidence.source_excerpt
            for construct in self._repeats()
        }
        self.assertNotIn(
            "Remove and discard the acetonitrile", excerpts["repeat-p6-0"]
        )
        self.assertIn("repeat steps 8-9", excerpts["repeat-p6-0"])


class ServerRefusesAtValidationTests(unittest.TestCase):
    """The refusal happens on the response, before anything is assembled."""

    def _labels(self, raw, category=ClaimCategory.REPEAT_CONDITION,
                excerpt="7 Repeat steps 2-7 until clear."):
        from voice_workflow_agent.protocol_claim_analysis import (
            _repeated_step_labels,
        )

        return _repeated_step_labels(
            raw,
            category=category,
            excerpt=excerpt,
            item_index=0,
            chunk_id="chunk-1",
            source_revision="pdf-1",
            source_hash="0" * 64,
            page_number=5,
        )

    def _refused(self, raw, **kwargs):
        from voice_workflow_agent.experiment_protocol_analysis import (
            ProtocolAnalysisEvidenceError,
        )

        with self.assertRaises(ProtocolAnalysisEvidenceError) as caught:
            self._labels(raw, **kwargs)
        return caught.exception.diagnostic.reason_code

    def test_a_declared_range_in_the_evidence_is_accepted(self) -> None:
        self.assertEqual(self._labels(["2", "7"]), ("2", "7"))

    def test_a_repeat_with_no_range_is_refused(self) -> None:
        self.assertEqual(self._refused(None), "repeat_range_missing")

    def test_a_range_absent_from_the_evidence_is_refused(self) -> None:
        self.assertEqual(
            self._refused(
                ["8", "9"],
                excerpt=(
                    "9 Remove and discard the acetonitrile. Your gel band "
                    "should have a whitish appearance when dry."
                ),
            ),
            "repeat_range_not_in_evidence",
        )

    def test_a_backwards_range_is_refused(self) -> None:
        self.assertEqual(self._refused(["7", "2"]), "repeat_range_inverted")

    def test_a_malformed_range_is_refused(self) -> None:
        for raw in (["2"], ["2", "7", "9"], ["two", "seven"], "2-7", [2, 7]):
            with self.subTest(raw=raw):
                self.assertEqual(
                    self._refused(raw), "repeat_range_malformed"
                )

    def test_another_category_must_leave_it_null(self) -> None:
        self.assertIsNone(
            self._labels(None, category=ClaimCategory.DURATION)
        )
        self.assertEqual(
            self._refused(["2", "7"], category=ClaimCategory.DURATION),
            "repeat_range_not_applicable",
        )


class ContractStatesTheRuleTests(unittest.TestCase):
    """Enforced syntactically, so stated syntactically."""

    def test_the_prompt_states_the_shape_not_a_paraphrase(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            CLAIM_ANALYSIS_SYSTEM_PROMPT,
        )

        prompt = " ".join(CLAIM_ANALYSIS_SYSTEM_PROMPT.split())
        for phrase in (
            "must set repeated_step_labels to the first and last",
            "two numbers joined by a hyphen or dash",
            "cite the segment that carries the repeat instruction itself",
            "The range must run forwards",
            "every other claim must leave it null",
            "do not guess a range",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, prompt)

    def test_the_schema_declares_the_field_and_its_shape(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            CLAIM_RESPONSE_SCHEMA,
        )

        field = CLAIM_RESPONSE_SCHEMA["properties"]["claims"]["items"][
            "properties"
        ]["repeated_step_labels"]
        self.assertIn("repeat_condition", field["description"])
        self.assertIn("hyphen or dash", field["description"])
        array = next(
            branch for branch in field["anyOf"] if branch.get("type") == "array"
        )
        self.assertEqual(array["minItems"], 2)
        self.assertEqual(array["maxItems"], 2)
        for field in ("repeated_step_labels", "repetition_count"):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    CLAIM_RESPONSE_SCHEMA["properties"]["claims"]["items"][
                        "required"
                    ],
                )


if __name__ == "__main__":
    unittest.main()


class ValueHonestyScopeTests(unittest.TestCase):
    """The check applies to segments inside a numbered step.

    A timer or a quantity belonging to a step is read out to an operator, so
    losing one is dangerous and refusing its declination is right. Outside
    every numbered step the same test was refusing correct answers, and it was
    the most frequent refusal of the last provider run: three of five.

    The narrowing is structural -- does the segment lie inside a numbered
    step's span -- and never a phrase, domain or pattern list. Two such lists
    have been rejected in this project and both times the "obviously trivial"
    category turned out to contain safety text.
    """

    def test_a_page_with_no_numbered_label_has_nothing_in_scope(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            _segments_inside_numbered_steps,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            ProtocolEvidenceSegment,
        )

        segments = tuple(
            ProtocolEvidenceSegment(
                segment_id=f"seg-{index}",
                source_revision="pdf-1",
                source_sha256="0" * 64,
                source_page_number=1,
                page_text_sha256="1" * 64,
                segment_index=index,
                text=text,
            )
            for index, text in enumerate(
                ("Abstract\n", "This protocol takes 1h 30m.\n")
            )
        )
        self.assertEqual(_segments_inside_numbered_steps(segments), frozenset())

    def test_a_segment_under_a_label_is_in_scope(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            ProtocolEvidenceSegment,
            _segments_inside_numbered_steps,
        )

        segments = tuple(
            ProtocolEvidenceSegment(
                segment_id=f"seg-{index}",
                source_revision="pdf-1",
                source_sha256="0" * 64,
                source_page_number=1,
                page_text_sha256="1" * 64,
                segment_index=index,
                text=text,
            )
            for index, text in enumerate(
                ("Heading\n", "1 Incubate for 15 min.\n", "Note: 2 mL added.\n")
            )
        )
        inside = _segments_inside_numbered_steps(segments)
        self.assertNotIn("seg-0", inside)
        self.assertIn("seg-1", inside)
        self.assertIn("seg-2", inside)

    def test_the_measured_reduction_over_the_local_sources(self) -> None:
        """96 value-bearing segments in scope becomes 77."""

        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            _segments_inside_numbered_steps,
            generate_page_evidence_segments,
            segment_carries_unit_bearing_value,
            segment_is_substantive,
        )

        source = Path("data/runtime/candidate-a-source/in-gel-digestion.pdf")
        if not source.is_file():
            self.skipTest(f"{source} is not present.")
        extraction = extract_protocol_pdf(source)
        before = after = 0
        for page in extraction.pages:
            segments = generate_page_evidence_segments(
                extraction,
                source_revision="pdf-1",
                page_number=page.source_page_number,
            )
            inside = _segments_inside_numbered_steps(segments)
            for segment in segments:
                if not segment_is_substantive(segment.text):
                    continue
                if not segment_carries_unit_bearing_value(segment.text):
                    continue
                before += 1
                if segment.segment_id in inside:
                    after += 1
        self.assertEqual((before, after), (22, 20))

    def test_a_trailing_footer_leaves_scope_by_geometry(self) -> None:
        """The case that motivated the narrowing, and finally fixed in STEP 30.

        headspace page 6 ends with a running footer whose text begins "1h 30m",
        the document's own estimated duration, which matches digit-plus-unit.
        The narrowing by numbered-step membership did not remove it for nine
        steps: the last label on a page owned everything to the end of that
        page, deliberately, so a step keeps its own notes and warnings -- and
        the footer sat inside that territory. In-gel page 7's footer cost a
        provider call in STEP 30 for exactly this.

        Two geometric facts fix it, and both are needed. The band is a segment
        boundary, so the duration badge is no longer glued to the page number;
        and the band lies outside every step's span, so the footer leaves scope
        without anything recognising a domain or a phrase.
        """

        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            _segments_inside_numbered_steps,
            generate_page_evidence_segments,
            segment_carries_unit_bearing_value,
        )

        source = Path("usingdynamicheadspacecollections.pdf")
        if not source.is_file():
            self.skipTest(f"{source.name} is not present.")
        extraction = extract_protocol_pdf(source)
        page = extraction.pages[5]
        segments = generate_page_evidence_segments(
            extraction, source_revision="pdf-1", page_number=6
        )
        inside = _segments_inside_numbered_steps(
            segments, page.bottom_band_offset
        )
        footer = segments[-1]
        self.assertIn("protocols.io", footer.text)
        # The footer is now its own segment, and it is out of scope.
        self.assertNotIn(footer.segment_id, inside)
        self.assertFalse(segment_carries_unit_bearing_value(footer.text))

        # The duration it used to be glued to is a segment of its own, still
        # in scope, because it really is that step's duration.
        badge = segments[-2]
        self.assertEqual(badge.text.strip(), "1h 30m")
        self.assertTrue(segment_carries_unit_bearing_value(badge.text))
        self.assertIn(badge.segment_id, inside)

    def test_no_running_footer_is_left_in_scope_on_any_local_source(self) -> None:
        """Nine were, across the four sources. The measurement is the test."""

        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )
        from voice_workflow_agent.protocol_claim_analysis import (
            _segments_inside_numbered_steps,
            generate_page_evidence_segments,
            segment_carries_unit_bearing_value,
            segment_is_substantive,
        )

        sources = [
            Path("data/runtime/candidate-a-source/in-gel-digestion.pdf"),
            Path("usingdynamicheadspacecollections.pdf"),
            Path("intracellularmetaboliteextraction.pdf"),
            Path(
                "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
                "/5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
            ),
        ]
        checked = 0
        for source in sources:
            if not source.is_file():
                continue
            extraction = extract_protocol_pdf(source)
            for number in range(1, extraction.page_count + 1):
                page = extraction.pages[number - 1]
                segments = generate_page_evidence_segments(
                    extraction, source_revision="pdf-1", page_number=number
                )
                inside = _segments_inside_numbered_steps(
                    segments, page.bottom_band_offset
                )
                for segment in segments:
                    if (
                        segment_is_substantive(segment.text)
                        and segment.segment_id in inside
                        and segment_carries_unit_bearing_value(segment.text)
                    ):
                        checked += 1
                        self.assertNotIn(
                            "protocols.io |",
                            segment.text,
                            f"{source.name} page {number}: a running footer is "
                            f"still in the value-honesty scope",
                        )
        if not checked:
            self.skipTest("no local source is present")
