"""Content outside every numbered step is cut into segments of its own.

What is left of the document-level claim tests: the claim DTOs, their
whole-protocol validation and the assembly were deleted on 2026-10-13 (lane
AN, decision 1; the git history keeps them). The page they were written
against is still cut by the surviving ``generate_page_evidence_segments``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.pdf_fixture_support import write_lined_pages
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.protocol_claim_analysis import generate_page_evidence_segments

REVISION = "pdf-1"

PAGE = (
    "Before start samples are dried for 72 h at 65 degrees C.",
    "50 Prepare the acid solution.",
    "Safety information",
    "Danger, highly corrosive.",
    "Use a cylinder to measure 242 ml of water.",
)


class DocumentLevelClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        path = Path(self._temp.name) / "page.pdf"
        write_lined_pages(path, (PAGE,))
        self.extraction = extract_protocol_pdf(path)
        self.segments = generate_page_evidence_segments(
            self.extraction, source_revision=REVISION, page_number=1
        )
        self.page_text = self.extraction.pages[0].text

    def _index_of(self, needle: str) -> int:
        return next(
            index
            for index, segment in enumerate(self.segments)
            if needle in segment.text
        )

    def test_the_page_puts_the_before_start_line_outside_every_step(self) -> None:
        outside = self._index_of("Before start")
        inside = self._index_of("242 ml")
        self.assertLess(outside, self._index_of("50 Prepare the acid"))
        self.assertGreater(inside, self._index_of("50 Prepare the acid"))


if __name__ == "__main__":
    unittest.main()
