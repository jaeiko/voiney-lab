"""What a page prints in its margins, skipped where a quote runs onto the next page (lane AN5, decision 2).

Lane AN4 found most of the evidence cards left after its re-ask were quotes
that run from one page's end onto the next, every word of them printed, that
the page-end rule refused: the page's margin printed lines the rule did not
know as furniture -- the article's first-page copyright and licence footer
(printed once, so never a running line), a method-and-page number line
("200.8-24"), a page number the PDF's page count does not match, a running
footer whose page number moves from its start to its end page by page.

Human decision 2026-10-20 (the recommended option): a line in a page's top
or bottom margin is skipped when the page-end rule judges whether a quote
runs on -- and only then -- when it is (a) a page number or a method number
with the page number, as the document numbers its pages, (b) printed nearly
alike in the margin of two pages or more, or (c) the copyright or licence
footer of the first page (the second, after a cover page). Such a line is
never part of a quote: the two pieces stay each page's own characters, and
nothing in a page's own text is skipped by it.
"""

from __future__ import annotations

import unittest

from voiney_lab.experiment_protocol import SourceEvidence
from voiney_lab.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    _furniture_line,
    _page_edges,
    _verified_evidence,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.pdf_text_engine import PdfTextBlock

WORDS = ("Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo")


def filler(page: int) -> list[str]:
    """Body lines no other page prints."""

    return [f"{WORDS[page]} line {n} of the body says what is done at this point." for n in range(1, 9)]


LICENSE = "This is an open access article under the CC BY license (http://creativecommons.org/licenses/by/4.0/)."
COPYRIGHT = "STAR Protocols 3, 101366, June 17, 2022 © 2022 The Authors."

PAGE_2_END = "Animals were purchased from the same barrier and vendor for each experiment. All"
PAGE_3_START = "wild-type (WT) conventional mice were purchased from Taconic Biosciences (USA)."
PAGE_3_END = "Spin the tubes in the cold room at 500 x g for"
PAGE_4_START = "5 min at 4 °C and keep the pellet on ice."


def _star_pages(page_2_tail: list[str] | None = None, page_3_tail: list[str] | None = None) -> list[str]:
    """A STAR Protocols layout: a cover page, the article's first page with its
    licence footer and page number 1, then pages whose running footer prints
    its page number first on one page and last on the next."""

    cover = ["Protocol", "Isolation of lamina propria lymphocytes", *filler(1),
             "Kim et al., STAR Protocols 3,", "101366", "June 17, 2022 © 2022 The", "Authors.",
             "ll", "OPEN ACCESS"]
    first = ["Protocol", "Isolation of lamina propria lymphocytes from mouse intestines", *filler(2),
             PAGE_2_END, *(page_2_tail if page_2_tail is not None else [COPYRIGHT, LICENSE, "1"]),
             "ll", "OPEN ACCESS"]
    third = [PAGE_3_START, *filler(3), PAGE_3_END,
             *(page_3_tail if page_3_tail is not None else
               ["ll", "OPEN ACCESS", "2 STAR Protocols 3, 101366, June 17, 2022", "Protocol"])]
    fourth = [PAGE_4_START, *filler(4), "Store the pellet at -80 °C.",
              "STAR Protocols 3, 101366, June 17, 2022 3", "ll", "OPEN ACCESS"]
    fifth = ["Protocol", *filler(5), "Read the plate.", "ll", "OPEN ACCESS",
             "4 STAR Protocols 3, 101366, June 17, 2022", "Protocol"]
    sixth = [*filler(6), "Record the result.", "STAR Protocols 3, 101366, June 17, 2022 5", "ll",
             "OPEN ACCESS"]
    return ["\n".join(lines) + "\n" for lines in (cover, first, third, fourth, fifth, sixth)]


def _extraction(texts: list[str], blocks: dict[int, tuple[PdfTextBlock, ...]] | None = None
                ) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="margins.pdf", byte_size=1, sha256="f" * 64,
        media_type="application/pdf", page_count=len(texts), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(
            ProtocolPdfPage(source_page_number=number, text=text, text_empty=False,
                            blocks=(blocks or {}).get(number, ()))
            for number, text in enumerate(texts, start=1)
        ),
    )


def _verify(extraction: ProtocolPdfExtraction, page: int, quote: str) -> SourceEvidence:
    return _verified_evidence(SourceEvidence(source_page_number=page, source_excerpt=quote), extraction)


def _epa_pages() -> list[str]:
    pages = []
    for number in range(1, 6):
        body = [f"Section {WORDS[number]} of the method describes the work in plain words.",
                *filler(number)]
        if number == 3:
            body.append("11.2.5 Cover the lip of the beaker with a watch glass to reduce additional")
        if number == 4:
            body.insert(0, "evaporation and gently reflux the sample for 30 minutes.")
        pages.append("\n".join([*body, f"200.8-{number}"]) + "\n")
    return pages


class QuotesRunOnPastTheMarginTests(unittest.TestCase):
    def test_past_the_first_page_licence_footer_and_its_page_number(self) -> None:
        found = _verify(_extraction(_star_pages()), 2, f"{PAGE_2_END} {PAGE_3_START}")
        self.assertEqual((found.source_page_number, found.continued_on_page_number), (2, 3))
        self.assertEqual(found.source_excerpt, PAGE_2_END)
        self.assertEqual(found.continued_excerpt, PAGE_3_START)

    def test_past_a_running_footer_whose_page_number_moves(self) -> None:
        found = _verify(_extraction(_star_pages()), 3, f"{PAGE_3_END} {PAGE_4_START}")
        self.assertEqual((found.source_excerpt, found.continued_excerpt), (PAGE_3_END, PAGE_4_START))

    def test_past_a_method_and_page_number_line(self) -> None:
        quote = ("11.2.5 Cover the lip of the beaker with a watch glass to reduce additional "
                 "evaporation and gently reflux the sample for 30 minutes.")
        found = _verify(_extraction(_epa_pages()), 3, quote)
        self.assertEqual(found.continued_on_page_number, 4)
        self.assertEqual(found.continued_excerpt, "evaporation and gently reflux the sample for 30 minutes.")
        self.assertNotIn("200.8", found.source_excerpt + found.continued_excerpt)

    def test_past_the_footer_block_drawn_lowest_on_the_page(self) -> None:
        texts = _star_pages()
        body = "\n".join(["Protocol", "Isolation of lamina propria lymphocytes from mouse intestines",
                          *filler(2), PAGE_2_END])
        block = lambda y0, y1, text: PdfTextBlock(40, y0, 560, y1, 9.0, False, text)  # noqa: E731
        blocks = {2: (block(60, 700, body), block(758, 765, "1"),
                      block(757, 772, f"{COPYRIGHT}\n{LICENSE}"), block(775, 782, "ll"),
                      block(775, 782, "OPEN ACCESS"))}
        found = _verify(_extraction(texts, blocks), 2, f"{PAGE_2_END} {PAGE_3_START}")
        self.assertEqual((found.source_excerpt, found.continued_excerpt), (PAGE_2_END, PAGE_3_START))


class WhatStaysRefusedTests(unittest.TestCase):
    def test_a_body_line_between_the_two_pieces(self) -> None:
        texts = _star_pages(page_2_tail=["Mice were housed under standard conditions.", COPYRIGHT,
                                         LICENSE, "1"])
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            _verify(_extraction(texts), 2, f"{PAGE_2_END} {PAGE_3_START}")

    def test_a_licence_line_in_the_margin_of_a_later_page(self) -> None:
        # Printed on this page only (on two pages it would be rule (b)).
        texts = _star_pages(page_3_tail=["Figure 2 is reused under the CC BY license.", "ll", "OPEN ACCESS",
                                         "2 STAR Protocols 3, 101366, June 17, 2022", "Protocol"])
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            _verify(_extraction(texts), 3, f"{PAGE_3_END} {PAGE_4_START}")

    def test_a_number_that_is_not_the_page_s_number(self) -> None:
        texts = _star_pages(page_3_tail=["7", "ll", "OPEN ACCESS",
                                         "2 STAR Protocols 3, 101366, June 17, 2022", "Protocol"])
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            _verify(_extraction(texts), 3, f"{PAGE_3_END} {PAGE_4_START}")

    def test_a_quote_that_holds_the_margin_s_words(self) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            _verify(_extraction(_star_pages()), 2, f"{PAGE_2_END} {LICENSE} {PAGE_3_START}")

    def test_a_page_s_own_text_never_leaves_these_lines_out(self) -> None:
        extraction = _extraction(_star_pages())
        page = extraction.pages[1]
        within = _furniture_line(page, extraction)
        edges = _page_edges(extraction)
        for line in (COPYRIGHT, LICENSE, "1"):
            with self.subTest(line=line):
                self.assertFalse(within(line))
                self.assertTrue(edges.is_crossing_furniture(line, 2))
        self.assertFalse(edges.is_crossing_furniture(PAGE_2_END, 2))


if __name__ == "__main__":
    unittest.main()


class CircledNumbersTests(unittest.TestCase):
    """Found while measuring (lane AN5): a Korean manual prints "③ ..." lines in
    its margins; "③" is a digit to Python's isdigit() but not a number int()
    reads, and the margin rule failed every piece of the 315-page manual."""

    def test_a_margin_line_opening_with_a_circled_number_is_read_without_failing(self) -> None:
        pages = []
        for number in range(1, 5):
            lines = [f"③ 시험 방법 {number}", *filler(number), f"{WORDS[number]} 마지막 줄",
                     f"③ 식품공전 미생물 시험법 {number}"]
            pages.append("\n".join(lines) + "\n")
        extraction = _extraction(pages)
        edges = _page_edges(extraction)
        self.assertTrue(edges.is_crossing_furniture("③ 식품공전 미생물 시험법 2", 2))
        self.assertFalse(edges.is_crossing_furniture(f"{WORDS[2]} 마지막 줄", 2))
        found = _verify(extraction, 2, f"{WORDS[2]} 마지막 줄 {filler(3)[0]}")
        self.assertEqual((found.source_excerpt, found.continued_on_page_number, found.continued_excerpt),
                         (f"{WORDS[2]} 마지막 줄", 3, filler(3)[0]))
