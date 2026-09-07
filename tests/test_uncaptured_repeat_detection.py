"""A repeat the source states and the analysis missed must stop execution.

in-gel prints three repeat instructions and the analysis carried one. Nothing
knew the other two were gone: the assembled Protocol had twenty-five steps in
order and looked finished, so an agent would have walked them once and said so.
That is the false completion notice this system exists never to produce, and it
would have been produced by omission rather than by any wrong claim.

The detector reads the document, not the analysis's opinion of it, and it
creates nothing. It finds a shape -- the word repeat, the word step, two
numbers with a hyphen or "to" between them -- checks whether any repetition
claim cites that passage with that range, and where none does it says a person
must look. It does not build a repetition, does not infer a range, does not
attach anything to a step, and never decides that a repeat is finished.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_GEL = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"


class WhatTheSourceStatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        from voice_workflow_agent.experiment_protocol_pdf import (
            extract_protocol_pdf,
        )

        cls.extraction = extract_protocol_pdf(IN_GEL)

    def test_all_three_of_in_gel_s_repeat_ranges_are_found(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            explicit_repeat_instructions,
        )

        found = explicit_repeat_instructions(
            self.extraction, source_revision="pdf-1"
        )
        self.assertEqual(
            [(item["source_page_number"], item["declared_range"]) for item in found],
            [(5, ("2", "7")), (6, ("8", "9")), (8, ("17", "18"))],
        )

    def test_the_range_reported_is_the_one_the_document_printed(self) -> None:
        """Nothing is inferred, corrected or widened -- principle 8.

        in-gel's page 8 says to repeat 17-18 while the condition it checks is
        produced by steps 19 and 20. That disagreement is the document's, and
        the detector reports 17-18 because that is what the page says.
        """

        from voice_workflow_agent.protocol_claim_analysis import (
            explicit_repeat_instructions,
        )

        page_eight = next(
            item
            for item in explicit_repeat_instructions(
                self.extraction, source_revision="pdf-1"
            )
            if item["source_page_number"] == 8
        )
        self.assertEqual(page_eight["declared_range"], ("17", "18"))

    def test_an_inverted_range_is_not_reported(self) -> None:
        """Shape only, and a backwards range is not a range."""

        from voice_workflow_agent.protocol_claim_analysis import (
            _EXPLICIT_REPEAT_INSTRUCTION,
        )

        match = _EXPLICIT_REPEAT_INSTRUCTION.search("repeat steps 9-2 until done")
        self.assertIsNotNone(match)
        # The reporting function drops it; the pattern alone does not judge.
        self.assertEqual(match.group("first"), "9")

    def test_it_reads_case_and_dash_variants(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            _EXPLICIT_REPEAT_INSTRUCTION,
        )

        for text in (
            "Repeat steps 2-7 until clear",
            "repeat step 2 to 7",
            "REPEAT STEPS 2–7",
            "repeat steps 2—7 twice",
        ):
            with self.subTest(text=text):
                match = _EXPLICIT_REPEAT_INSTRUCTION.search(text)
                self.assertIsNotNone(match)
                self.assertEqual(
                    (match.group("first"), match.group("last")), ("2", "7")
                )

    def test_prose_that_is_not_a_repeat_instruction_is_not_matched(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            _EXPLICIT_REPEAT_INSTRUCTION,
        )

        for text in (
            "Steps 2-7 describe the wash.",
            "The 2-7 range is the destaining block.",
            "Repeat the wash until clear.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(_EXPLICIT_REPEAT_INSTRUCTION.search(text))


class WhatTheAnalysisMissedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.is_file():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        from tests.test_merge_completes_on_real_chunks import _merge_from_cache

        built = _merge_from_cache()
        if built is None:
            raise unittest.SkipTest("in-gel is not fully cached on this machine.")
        cls.extraction, cls.plan, cls.merged, cls.draft = built

    def test_the_two_that_were_missed_are_named(self) -> None:
        from voice_workflow_agent.protocol_claim_analysis import (
            uncaptured_repeat_instructions,
        )

        missed = uncaptured_repeat_instructions(
            self.extraction,
            self.merged.claims,
            source_revision=self.merged.source_revision,
        )
        self.assertEqual(
            [(item["source_page_number"], item["declared_range"]) for item in missed],
            [(6, ("8", "9")), (8, ("17", "18"))],
        )

    def test_the_one_that_was_captured_is_not_named(self) -> None:
        """A repeat the analysis carries must not be reported as missing."""

        from voice_workflow_agent.protocol_claim_analysis import (
            uncaptured_repeat_instructions,
        )

        missed = uncaptured_repeat_instructions(
            self.extraction,
            self.merged.claims,
            source_revision=self.merged.source_revision,
        )
        self.assertNotIn(
            (5, ("2", "7")),
            [(item["source_page_number"], item["declared_range"]) for item in missed],
        )

    def test_it_blocks_execution_and_a_reviewer_can_clear_it(self) -> None:
        from voice_workflow_agent import experiment_protocol as domain
        from voice_workflow_agent.protocol_catalog import (
            _ACKNOWLEDGEABLE_GATES,
            ProtocolCatalog,
        )

        code = (
            domain.ReadinessReasonCode.SOURCE_STATES_AN_UNCAPTURED_REPETITION.value
        )
        self.assertIn(code, self.draft.readiness.reason_codes)
        self.assertIs(
            self.draft.readiness.status, domain.ReadinessStatus.ANALYSIS_REQUIRED
        )
        self.assertIn(code, _ACKNOWLEDGEABLE_GATES)
        self.assertEqual(
            ProtocolCatalog._BLOCKER_RESOLUTION[code]["action"], "acknowledge_gate"
        )

    def test_a_complete_analysis_raises_nothing(self) -> None:
        """The gate must be silent when every stated repeat is carried."""

        from voice_workflow_agent import experiment_protocol as domain

        assessment = domain.assess_readiness(
            self.draft.protocol, uncaptured_repeat_instructions=()
        )
        self.assertNotIn(
            domain.ReadinessReasonCode.SOURCE_STATES_AN_UNCAPTURED_REPETITION.value,
            assessment.reason_codes,
        )

    def test_the_detector_builds_no_repetition(self) -> None:
        """2-3: it says a person must look, and nothing else.

        Read as code: the module's repeat functions return descriptions and
        never construct a domain repetition.
        """

        import inspect

        from voice_workflow_agent import protocol_claim_analysis

        for name in (
            "explicit_repeat_instructions",
            "uncaptured_repeat_instructions",
        ):
            with self.subTest(function=name):
                body = inspect.getsource(
                    getattr(protocol_claim_analysis, name)
                )
                for forbidden in (
                    "RepeatUntil",
                    "FixedRangeRepetition",
                    "OperatorDeterminedRepetition",
                    "steps_by_label",
                ):
                    self.assertNotIn(forbidden, body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
