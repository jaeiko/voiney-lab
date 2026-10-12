"""The page-end span is the run of blocks that ends the page body (lane EV3, decision 4).

Human decision 2026-10-12. Lane M3 found 5 of the "medium" evidence failures
with both pieces of a quote across a page end verbatim, refused because the
page-end span was one text block: the lowest drawn. Bio-protocol, CDC and FDA
PDFs draw one block per line, so a first piece of several lines never fit
(PMC8250384 p.12, CDC p.5); FDA prints its footer at the top of the page text
in a block whose lines are not the page text's lines, so the footer was the
"lowest block" (fda_eam_4_7 p.12, zenodo_13880870 p.3); and two blocks on the
lowest baseline gave no span at all (mfds_tricaine p.6).

Now the span runs up from the block that ends the page body -- the lowest
drawn, the later in the page text where two share the lowest baseline --
through the blocks just above it in the page text, as far as the first piece
needs; the running header and footer and the page number are not part of it,
also where a block's own lines differ from the page text's lines. The first
piece must still be the end of that span and the second the head of the next
page's body: a quote that matches text in the middle of the page is refused
as before, and so is one with any other text between its pieces.
"""

from __future__ import annotations

import unittest

from voiney_lab import experiment_protocol as domain
from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisEvidenceError
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.pdf_text_engine import PdfTextBlock

STEP_LINES = (
    "Data analysis",
    "1. Align paired-end reads to hg19 using Bowtie2 version 2.3.4.3 with options: --end-to-end --very-",
    "sensitive --no-unal --no-mixed --no-discordant --phred33 -I 10 -X 700. For mapping E. coli carry-",
    "over fragments, we also use the --no-overlap --no-dovetail options to avoid possible cross-",
    "mapping of the experimental genome to that of the carry-over E. coli DNA that is used for",
    "calibration. Tracks are made as bedgraph files of normalized counts, which are the fraction of",
)
NEXT_LINES = (
    "total counts at each basepair scaled by the size of the hg19 genome.",
    "Note: To calibrate samples in a series, we use counts of E. coli fragments.",
)
LAST_LINES = ("2. Plot the calibrated tracks.",)
FIRST_PIECE = " \n".join(STEP_LINES[1:])
SECOND_PIECE = NEXT_LINES[0]


def _block(y0: float, text: str, *, x0: float = 40, height: float = 12) -> PdfTextBlock:
    return PdfTextBlock(x0=x0, y0=y0, x1=560, y1=y0 + height, font_size=10.0, bold=False, text=text)


def _page(number: int, lines: tuple[str, ...], *, footer: bool, last_row: tuple[str, ...] = ()) -> ProtocolPdfPage:
    """One block per line; the footer, when there is one, printed first in the
    page text but drawn lowest, its block broken into two lines where the page
    text keeps one (FDA)."""

    text = "".join(f"{line} \n" for line in lines)
    blocks = [_block(100 + 16 * index, line) for index, line in enumerate(lines)]
    if last_row:
        text += " ".join(last_row) + " \n"
        y0 = 100 + 16 * len(lines)
        blocks += [_block(y0, cell, x0=40 + 120 * index) for index, cell in enumerate(last_row)]
    if footer:
        text = f"Page {number} of 3 (April 2025) \n" + text
        blocks.append(_block(760, f"Page {number} of 3\n(April 2025)", height=30))
    return ProtocolPdfPage(number, text, False, blocks=tuple(blocks))


def source(
    lines: tuple[str, ...] = STEP_LINES, *, footer: bool = True, last_row: tuple[str, ...] = (),
) -> ProtocolPdfExtraction:
    pages = (
        _page(1, lines, footer=footer, last_row=last_row),
        _page(2, NEXT_LINES, footer=footer),
        _page(3, LAST_LINES, footer=footer),
    )
    return ProtocolPdfExtraction(
        original_filename="page-end.pdf", byte_size=1, sha256="f" * 64,
        media_type="application/pdf", page_count=len(pages), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=pages,
    )


def verified(first: str, second: str | None, extraction: ProtocolPdfExtraction) -> domain.SourceEvidence:
    return analysis_module._verified_evidence(
        domain.SourceEvidence(
            1, first, continued_on_page_number=None if second is None else 2,
            continued_excerpt=second),
        extraction,
    )


class AFirstPieceOfSeveralBlocksTests(unittest.TestCase):
    def test_a_split_quote_whose_first_piece_is_five_line_blocks(self) -> None:
        for footer in (False, True):
            with self.subTest(footer=footer):
                found = verified(FIRST_PIECE, SECOND_PIECE, source(footer=footer))
                self.assertEqual(found.continued_on_page_number, 2)
                self.assertEqual(found.continued_excerpt, SECOND_PIECE)
                self.assertTrue(found.source_excerpt.startswith("1. Align"))

    def test_one_joined_quote_is_split_onto_its_two_pages(self) -> None:
        found = verified(f"{FIRST_PIECE} {SECOND_PIECE}", None, source())
        self.assertEqual(found.source_page_number, 1)
        self.assertEqual(found.continued_on_page_number, 2)
        self.assertTrue(found.source_excerpt.startswith("1. Align"))
        self.assertTrue(found.source_excerpt.rstrip().endswith("which are the fraction of"))
        self.assertEqual(found.continued_excerpt, SECOND_PIECE)
        # Each piece is its own page's characters.
        extraction = source()
        self.assertIn(found.source_excerpt, extraction.pages[0].text)
        self.assertIn(found.continued_excerpt, extraction.pages[1].text)

    def test_the_heading_above_the_step_may_open_the_first_piece(self) -> None:
        found = verified(" \n".join(STEP_LINES), SECOND_PIECE, source())
        self.assertTrue(found.source_excerpt.startswith("Data analysis"))


class TheRunningFooterIsNotThePageEndTests(unittest.TestCase):
    def test_a_one_line_first_piece_under_a_footer_drawn_lowest(self) -> None:
        found = verified(STEP_LINES[-1], SECOND_PIECE, source())
        self.assertEqual(found.continued_excerpt, SECOND_PIECE)

    def test_the_footer_is_not_part_of_either_piece(self) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            verified(STEP_LINES[-1], f"Page 2 of 3 (April 2025) {SECOND_PIECE}", source())


class TheLowestBaselineTests(unittest.TestCase):
    ROW = ("centrifuge at 4,800 ×g", "for 10 min.")

    def test_two_blocks_on_the_lowest_baseline_end_the_page_with_the_later_one(self) -> None:
        extraction = source(STEP_LINES[:2], footer=False, last_row=self.ROW)
        first = f"{STEP_LINES[1]} \n{' '.join(self.ROW)}"
        found = verified(first, SECOND_PIECE, extraction)
        self.assertEqual(found.continued_excerpt, SECOND_PIECE)

    def test_the_earlier_block_on_that_baseline_is_not_the_page_end(self) -> None:
        extraction = source(STEP_LINES[:2], footer=False, last_row=self.ROW)
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            verified(f"{STEP_LINES[1]} \n{self.ROW[0]}", SECOND_PIECE, extraction)


class TextInTheMiddleOfThePageIsStillRefusedTests(unittest.TestCase):
    def test_a_first_piece_that_ends_above_the_last_line(self) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            verified(" \n".join(STEP_LINES[1:-1]), SECOND_PIECE, source())

    def test_a_first_piece_followed_by_another_paragraph(self) -> None:
        extraction = source((*STEP_LINES, "Figure 3. Calibrated tracks of two samples."))
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            verified(FIRST_PIECE, SECOND_PIECE, extraction)

    def test_a_joined_quote_that_leaves_out_a_line_of_the_first_page(self) -> None:
        skipped = " \n".join((*STEP_LINES[1:3], *STEP_LINES[4:]))
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            verified(f"{skipped} {SECOND_PIECE}", None, source())

    def test_a_second_piece_that_is_not_the_next_page_head(self) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            verified(FIRST_PIECE, NEXT_LINES[1], source())

    def test_a_quote_over_three_pages(self) -> None:
        quote = f"{FIRST_PIECE} {' '.join(NEXT_LINES)} {LAST_LINES[0]}"
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            verified(quote, None, source())


class TheSpanTests(unittest.TestCase):
    def test_the_span_reaches_as_far_as_the_first_piece_needs(self) -> None:
        extraction = source(footer=False)
        page = extraction.pages[0]
        one = analysis_module._page_end_span(page, extraction)
        self.assertEqual(page.text[one[0]:one[1]].strip(), STEP_LINES[-1])
        reach = len("".join(FIRST_PIECE.split()))
        start, end = analysis_module._page_end_span(page, extraction, reach=reach)
        self.assertEqual(page.text[start:end].strip(), FIRST_PIECE.strip())
        start, end = analysis_module._page_end_span(page, extraction, reach=10_000)
        self.assertTrue(page.text[start:end].startswith("Data analysis"))

    def test_a_footer_block_whose_page_text_line_is_a_running_line_is_furniture(self) -> None:
        extraction = source()
        page = extraction.pages[0]
        def span(block):
            (found,) = analysis_module._canonical_match_spans(page.text, block.text)
            return found

        footer = next(block for block in page.blocks if block.text.startswith("Page"))
        # Its own two lines are not both running lines; the page-text line is.
        self.assertFalse(analysis_module._is_furniture_block(footer, page, extraction))
        self.assertTrue(analysis_module._is_furniture_block(footer, page, extraction, span(footer)))
        body = next(block for block in page.blocks if block.text == STEP_LINES[-1])
        self.assertFalse(analysis_module._is_furniture_block(body, page, extraction, span(body)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
