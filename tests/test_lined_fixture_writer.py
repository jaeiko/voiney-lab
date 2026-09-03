"""The multi-line fixture writer, without which boundary rules go untested."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_claim_analysis import write_lined_pages, write_pages
from voice_workflow_agent.experiment_protocol_pdf import (
    TextVerification,
    extract_protocol_pdf,
)

_PAGE = (
    "1 Prepare the acid solution.",
    "Safety information",
    "Danger, highly corrosive.",
    "Wear gloves, labcoat, safety glasses.",
    "2 Add 250 ml of water.",
)


class LinedFixtureWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def test_extracted_text_contains_real_line_breaks(self) -> None:
        path = self.root / "lined.pdf"
        write_lined_pages(path, (_PAGE,))
        text = extract_protocol_pdf(path).pages[0].text
        self.assertEqual(text.count("\n"), len(_PAGE) - 1)
        self.assertEqual(tuple(text.split("\n")), _PAGE)

    def test_the_plain_writer_now_honours_newlines_too(self) -> None:
        """It used to replace every newline with a space.

        That is why this helper had to exist, and it also quietly weakened the
        fixtures: a page written as "Preparation\n1. Add buffer." became one
        line, so its numbered step sat mid-sentence and only matched because
        the trigger accepted a number in the middle of a line. Removing that
        trigger made the collapse visible, and the writer now emits the lines
        the fixture asked for.
        """

        path = self.root / "flat.pdf"
        write_pages(path, ("\n".join(_PAGE),))
        text = extract_protocol_pdf(path).pages[0].text
        self.assertEqual(text.count("\n"), len(_PAGE) - 1)
        self.assertEqual(tuple(text.split("\n")), _PAGE)

    def test_a_page_with_no_newline_is_written_as_one_line(self) -> None:
        path = self.root / "single.pdf"
        write_pages(path, ("Preparation 1. Add buffer.",))
        text = extract_protocol_pdf(path).pages[0].text
        self.assertEqual(text.count("\n"), 0)

    def test_lined_pages_pass_the_extraction_cross_check(self) -> None:
        path = self.root / "lined.pdf"
        write_lined_pages(path, (_PAGE,))
        extraction = extract_protocol_pdf(path)
        self.assertIs(extraction.text_verification, TextVerification.VERIFIED)
        self.assertEqual(extraction.divergent_page_numbers, ())

    def test_multiple_pages_stay_separate(self) -> None:
        path = self.root / "two.pdf"
        write_lined_pages(path, (_PAGE, ("3 Cool the sample.", "Done.")))
        extraction = extract_protocol_pdf(path)
        self.assertEqual(extraction.page_count, 2)
        self.assertEqual(
            extraction.pages[1].text, "3 Cool the sample.\nDone."
        )

    def test_parentheses_and_backslashes_survive(self) -> None:
        path = self.root / "escape.pdf"
        write_lined_pages(path, ((r"1 Add (50:49:1) mix.", "Note (see above)."),))
        text = extract_protocol_pdf(path).pages[0].text
        self.assertIn("(50:49:1)", text)
        self.assertIn("Note (see above).", text)


if __name__ == "__main__":
    unittest.main()
