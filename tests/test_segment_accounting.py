"""A hazard block on a page is a segment of its own, not part of the step.

What is left of the segment-accounting tests: the claim request, the page
coverage accounting and its helpers were deleted on 2026-10-13 (lane AN,
decision 1; the git history keeps them). The page is cut by the surviving
``generate_page_evidence_segments``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.pdf_fixture_support import write_lined_pages
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.protocol_claim_analysis import generate_page_evidence_segments

REVISION = "pdf-1"

# One numbered step, then a hazard block that belongs to it but is not it.
PAGE = (
    "50 Prepare a 72% H2SO4 solution.",
    "Safety information",
    "Danger, highly corrosive.",
    "Wear gloves, labcoat, safety glasses.",
    "Use a cylinder to measure 242 ml of water.",
)


class SegmentAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        path = Path(self._temp.name) / "page.pdf"
        write_lined_pages(path, (PAGE,))
        self.extraction = extract_protocol_pdf(path)
        self.texts = {
            segment.segment_index: segment.text
            for segment in generate_page_evidence_segments(
                self.extraction, source_revision=REVISION, page_number=1
            )
        }

    def test_the_page_splits_the_hazard_out_of_the_step(self) -> None:
        joined = [" ".join(text.split()) for text in self.texts.values()]
        self.assertIn("50 Prepare a 72% H2SO4 solution.", joined)
        self.assertTrue(
            any("Danger, highly corrosive." in text for text in joined)
        )


if __name__ == "__main__":
    unittest.main()
