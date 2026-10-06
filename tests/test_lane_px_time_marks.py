"""protocols.io time marks in evidence comparison (lane PX, rule 7).

Human decision of 2026-10-06, adopting lane PA's (w) and (p): in the evidence
comparison only -- page text and hashes unchanged -- (w) a duration widget
standing alone on its line between two sentences of a step ("03:00:00") is
left out, and (p) a space right before punctuation ("00:30:00 .") is left
out. Both shapes are protocols.io's: on ANKOM page 16 the text layer draws
the stated 3 h again as its own block, and on page 21 the export puts a space
before the full stop. The pages below are written with the same shapes and
read with PyMuPDF; the last tests read the real ANKOM PDF when it is present.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.test_pdf_text_engine import _write_raw_pages
from voiney_lab import experiment_protocol as domain
from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisEvidenceError
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf

ROOT = Path(__file__).resolve().parents[1]
ANKOM = ROOT / (
    "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    "/5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)
ANKOM_COPY = Path.home() / (
    "reports/scratch/lane-pa/live_data/objects/sha256/53"
    "/5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)

PAGE_16_STEP = (
    "Completely dry the filter bags in an oven at 102 +/- 2 C for 3 h. "
    "During that time, go to Step 20 and Flush Procedure (Step 21)."
)
PAGE_21_STEP = "Cool to ambient temperature for 00:30:00."


def _line(y: int, text: str, x: int = 72) -> str:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"BT /F1 12 Tf {x} {y} Td ({escaped}) Tj ET "


class _Pages(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        path = self.root / "protocols-io.pdf"
        _write_raw_pages(path, (
            _line(720, "19 Completely dry the filter bags in an oven at 102 +/- 2 C for 3 h. ")
            + _line(706, "03:00:00")
            + _line(692, "During that time, go to Step 20 and Flush Procedure (Step 21). ")
            + _line(678, "20 Rinse the vessel 2 times with 02:00:00 of hot water.")
            + _line(20, "protocols.io | example December 14, 2019 16/40"),
            _line(720, "28 When dry, remove the filter bags from the oven. Cool to ambient")
            + _line(706, "temperature for 00:30:00 . ")
            + _line(20, "protocols.io | example December 14, 2019 21/40"),
        ))
        self.extraction = extract_protocol_pdf(path)
        self.page_16 = self.extraction.pages[0].text
        self.page_21 = self.extraction.pages[1].text

    def tearDown(self) -> None:
        clear_protocol_pdf_cache()
        self.temp.cleanup()


class TimeMarkRulesTests(_Pages):
    def test_the_pages_read_as_protocols_io_draws_them(self) -> None:
        self.assertIn("for 3 h. \n03:00:00\nDuring that time", self.page_16)
        self.assertIn("temperature for 00:30:00 . ", self.page_21)

    def test_a_widget_alone_on_its_line_is_left_out_of_the_comparison(self) -> None:
        self.assertTrue(analysis_module._claim_occurs_in_text(PAGE_16_STEP, self.page_16))
        verified = analysis_module._verified_evidence(
            domain.SourceEvidence(1, PAGE_16_STEP), self.extraction)
        # The excerpt is projected back to the page's own characters, widget
        # line and all: nothing was removed from the source.
        self.assertIn("03:00:00", verified.source_excerpt)
        self.assertTrue(verified.source_excerpt.startswith("Completely dry"))
        self.assertTrue(verified.source_excerpt.endswith("(Step 21)."))

    def test_a_space_before_punctuation_is_left_out_of_the_comparison(self) -> None:
        self.assertTrue(analysis_module._claim_occurs_in_text(PAGE_21_STEP, self.page_21))
        verified = analysis_module._verified_evidence(
            domain.SourceEvidence(2, PAGE_21_STEP), self.extraction)
        self.assertEqual(verified.source_excerpt, "Cool to ambient\ntemperature for 00:30:00 .")

    def test_the_rules_are_tried_after_the_forms_that_do_not_discount(self) -> None:
        forms = analysis_module._comparison_forms(ocr_derived=False)
        self.assertEqual([form["skip_duration_widget_lines"] for form in forms], [False, False, True, True])
        self.assertEqual(forms[0], {
            "join_line_end_hyphens": False, "join_hangul_line_breaks": False,
            "skip_duration_widget_lines": False, "drop_space_before_punctuation": False})
        # A claim quoting the widget as the page draws it is still accepted.
        self.assertTrue(analysis_module._claim_occurs_in_text(
            "for 3 h. 03:00:00 During that time", self.page_16))

    def test_a_widget_inside_a_sentence_stays_a_number_to_match(self) -> None:
        self.assertTrue(analysis_module._claim_occurs_in_text(
            "Rinse the vessel 2 times with 02:00:00 of hot water.", self.page_16))
        self.assertFalse(analysis_module._claim_occurs_in_text(
            "Rinse the vessel 2 times with of hot water.", self.page_16))

    def test_a_changed_number_or_an_invented_sentence_is_still_refused(self) -> None:
        for claim in (
            PAGE_16_STEP.replace("for 3 h", "for 4 h"),
            PAGE_16_STEP.replace("Step 20", "Step 22"),
            "Cool to ambient temperature for 00:35:00.",
            "Cool to ambient temperature for 00:30:00 and shake.",
        ):
            with self.subTest(claim=claim):
                self.assertFalse(analysis_module._claim_occurs_in_text(claim, self.page_16))
                self.assertFalse(analysis_module._claim_occurs_in_text(claim, self.page_21))
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            analysis_module._verified_evidence(
                domain.SourceEvidence(2, "Cool to ambient temperature for 00:35:00."),
                self.extraction)

    def test_the_page_text_and_its_hash_are_untouched(self) -> None:
        before = hashlib.sha256(self.page_16.encode()).hexdigest()
        analysis_module._claim_occurs_in_text(PAGE_16_STEP, self.page_16)
        analysis_module._verified_evidence(
            domain.SourceEvidence(1, PAGE_16_STEP), self.extraction)
        self.assertEqual(hashlib.sha256(self.extraction.pages[0].text.encode()).hexdigest(), before)
        self.assertIn("\n03:00:00\n", self.extraction.pages[0].text)
        self.assertIn("00:30:00 . ", self.extraction.pages[1].text)

    def test_the_normalized_form_itself(self) -> None:
        text, starts, ends = analysis_module._normalized_text_with_bounds(
            "for 3 h. \n03:00:00\nDuring that time", skip_duration_widget_lines=True)
        self.assertEqual(text, "for 3 h. During that time")
        # Bounds still point into the original: "During" starts after the widget.
        self.assertEqual(starts[text.index("During")], "for 3 h. \n03:00:00\n".__len__())
        text, _, _ = analysis_module._normalized_text_with_bounds(
            "for 00:30:00 . Pool", drop_space_before_punctuation=True)
        self.assertEqual(text, "for 00:30:00. Pool")
        text, _, _ = analysis_module._normalized_text_with_bounds(
            "03:00:00\nDuring", skip_duration_widget_lines=True)
        self.assertEqual(text, "During")


class RealAnkomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = next((p for p in (ANKOM, ANKOM_COPY) if p.is_file()), None)
        if self.path is None:
            self.skipTest("the ANKOM PDF is not present")
        clear_protocol_pdf_cache()
        self.extraction = extract_protocol_pdf(self.path)

    def test_ankom_page_16_step_19_and_page_21_step_28_are_accepted(self) -> None:
        step_19 = ("Completely dry the filter bags in an oven at 102 ± 2°C for 3 h. "
                   "During that time, go to Step 20 and Flush Procedure (Step 21).")
        step_28 = ("When dry, remove the filter bags from the oven and immediately place them "
                   "directly into a collapsible desiccant pouch and flatten it to remove any air. "
                   "Cool to ambient temperature for 00:30:00.")
        self.assertTrue(analysis_module._claim_occurs_in_text(step_19, self.extraction.pages[15].text))
        self.assertTrue(analysis_module._claim_occurs_in_text(step_28, self.extraction.pages[20].text))
        verified = analysis_module._verified_evidence(
            domain.SourceEvidence(16, step_19), self.extraction)
        self.assertIn("03:00:00", verified.source_excerpt)
        verified = analysis_module._verified_evidence(
            domain.SourceEvidence(21, step_28), self.extraction)
        self.assertTrue(verified.source_excerpt.endswith("00:30:00 ."))

    def test_the_rules_are_needed_for_exactly_those_shapes(self) -> None:
        plain = analysis_module._comparison_forms(ocr_derived=False)[:2]
        claim = ("Completely dry the filter bags in an oven at 102 ± 2°C for 3 h. "
                 "During that time, go to Step 20")
        page = self.extraction.pages[15].text
        found_without = any(
            analysis_module._normalized_text_with_bounds(claim, **form)[0]
            in analysis_module._normalized_text_with_bounds(page, **form)[0]
            for form in plain)
        self.assertFalse(found_without)
        self.assertTrue(analysis_module._claim_occurs_in_text(claim, page))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
