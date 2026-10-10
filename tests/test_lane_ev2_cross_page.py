"""A quote that runs from one page's end into the next page's start (lane EV2, decision 1).

Human decision 2026-10-10: a quote that is the text ending page N joined to the
text opening page N+1 -- with the pages' running header and footer lines and
page-number lines left out, and lane EB's boundary rules read on each page --
is evidence, stored in lane PA's form: the page-N piece as the excerpt and the
page-N+1 piece as its continuation. A quote over three pages, or one that leaves
out other text between the two pieces, is refused.

The pages below are shaped like Bio-protocol's PMC8250384 p.7/p.8 (lane AQ, 7):
the text layer prints each page's running header and its copyright footer at the
top of the page text although the footer is drawn lowest on the page, so the
lowest text block is the footer and the next page's text opens with the header,
the footer and the page number before the sentence goes on.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from voiney_lab.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    parse_protocol_analysis_response,
    validate_protocol_analysis_evidence,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.experiment_protocol_store import deserialize_analysis, serialize_analysis
from voiney_lab.pdf_text_engine import PdfTextBlock

TITLE = "Simplified Epigenome Profiling Using Tethered Tagmentation"
HEADER = (
    "www.bio-protocol.org/e4043     \n"
    "Bio-protocol 11(11): e4043. \n"
    "DOI:10.21769/BioProtoc.4043\n"
)
FOOTER = (
    "  Copyright Henikoff et al. \n"
    "This article is distributed under the terms of the Creative Commons Attribution License (CC BY 4.0). \n"
)


def _furniture(number: int) -> str:
    return f"                 \n{number} \n \n{HEADER} \n \n{FOOTER}"


PAGE_1_BODY = (
    f"{TITLE}\n"
    "B. Prepare beads (10 min) \n"
    "1. Gently resuspend the beads and withdraw enough slurry for the samples. \n"
    " \n"
    "C. Bind nuclei to beads (15 min) \n"
    "1. Thaw a frozen aliquot of nuclei at room temperature. \n"
    "Note: The control can use either native or lightly cross-linked nuclei, preferably "
)
PAGE_2_BODY = (
    "prepared as previously described (Kaya-Okur et al., 2020). Do not use whole cells. \n"
    "2. Transfer the thawed nuclei suspension in aliquots of no more than 50,000 cells to each tube. \n"
    "3. Incubate the nuclei with the beads for 10 min. Place the tube on a magnet and remove the \n"
)
PAGE_3_BODY = (
    "supernatant. \n"
    "4. Add 50 µl of antibody buffer. \n"
)

NOTE_PAGE_1 = "Note: The control can use either native or lightly cross-linked nuclei, preferably"
NOTE_PAGE_2 = "prepared as previously described (Kaya-Okur et al., 2020)."
NOTE = f"{NOTE_PAGE_1} {NOTE_PAGE_2}"


def _block(y0: float, y1: float, text: str) -> PdfTextBlock:
    return PdfTextBlock(x0=40, y0=y0, x1=560, y1=y1, font_size=10.0, bold=False, text=text)


def _page(number: int, body: str, body_blocks) -> ProtocolPdfPage:
    return ProtocolPdfPage(
        number,
        _furniture(number) + body,
        False,
        blocks=(
            _block(42, 92, f"{number}\n" + HEADER.strip()),
            # Drawn lowest on the page, printed near the top of the text.
            _block(755, 800, FOOTER.strip()),
            *body_blocks,
        ),
    )


def source(*, page_2_body: str = PAGE_2_BODY) -> ProtocolPdfExtraction:
    pages = (
        _page(1, PAGE_1_BODY, (
            _block(120, 135, TITLE),
            _block(150, 200, "B. Prepare beads (10 min)\n1. Gently resuspend the beads and withdraw enough slurry for the samples."),
            _block(640, 700, "C. Bind nuclei to beads (15 min)\n1. Thaw a frozen aliquot of nuclei at room temperature."),
            _block(700, 720, "Note: The control can use either native or lightly cross-linked nuclei, preferably"),
        )),
        _page(2, page_2_body, (
            _block(110, 720, page_2_body.strip()),
        )),
        _page(3, PAGE_3_BODY, (
            _block(110, 200, PAGE_3_BODY.strip()),
        )),
    )
    return ProtocolPdfExtraction(
        original_filename="cross-page.pdf", byte_size=1, sha256="e" * 64,
        media_type="application/pdf", page_count=len(pages), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=pages,
    )


def evidence(excerpt: str, page: int = 1) -> dict:
    return {"source_page_number": page, "source_excerpt": excerpt}


def step(label: str, instruction: str, excerpt: str, page: int = 1, notes=()) -> dict:
    return {
        "step_id": f"step-{label}-{page}",
        "source_label": label,
        "instruction_source_text": instruction,
        "evidence": evidence(excerpt, page),
        "notes": list(notes),
    }


def note(text: str, excerpt: str, page: int = 1) -> dict:
    return {"statement_id": "note-1", "source_text": text, "evidence": evidence(excerpt, page)}


def response(*steps: dict) -> dict:
    return {
        "analysis_schema_version": 1,
        "pdf_sha256": "e" * 64,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "protocol-ev2",
            "metadata": {"title": TITLE, "original_language": "en", "evidence": evidence(TITLE)},
            "before_start": [],
            "materials": [],
            "equipment": [],
            "sections": [{
                "section_id": "bind",
                "title_source_text": "C. Bind nuclei to beads (15 min)",
                "evidence": evidence("C. Bind nuclei to beads (15 min)"),
                "steps": list(steps),
            }],
            "constructs": [],
            "description": None,
        },
    }


THAW = "1. Thaw a frozen aliquot of nuclei at room temperature."


def parse(*steps: dict, extraction: ProtocolPdfExtraction | None = None):
    return parse_protocol_analysis_response(json.dumps(response(*steps)), extraction or source())


def first_note(draft):
    return draft.protocol.sections[0].steps[0].notes[0]


class QuoteAcrossThePageEndIsAcceptedTests(unittest.TestCase):
    def test_the_joined_quote_is_split_onto_its_two_pages(self):
        quote = f"{NOTE_PAGE_1} \n{NOTE_PAGE_2}"
        found = first_note(parse(step("1", THAW, THAW, notes=[note(NOTE, quote)]))).evidence
        self.assertEqual(found.source_page_number, 1)
        self.assertEqual(found.source_excerpt, NOTE_PAGE_1)
        self.assertEqual(found.continued_on_page_number, 2)
        self.assertEqual(found.continued_excerpt, NOTE_PAGE_2)
        # Each piece is its own page's characters.
        pages = source().pages
        self.assertIn(found.source_excerpt, pages[0].text)
        self.assertIn(found.continued_excerpt, pages[1].text)

    def test_a_claim_cut_at_the_page_end_is_found_past_the_header_footer_and_page_number(self):
        found = first_note(parse(step("1", THAW, THAW, notes=[note(NOTE, NOTE_PAGE_1)])))
        self.assertEqual(found.source_text, NOTE)
        self.assertEqual(found.evidence.source_excerpt, NOTE_PAGE_1)
        self.assertEqual(found.evidence.continued_on_page_number, 2)
        self.assertEqual(found.evidence.continued_excerpt, NOTE_PAGE_2)

    def test_a_step_cut_at_a_page_end_without_its_own_continuation(self):
        draft = parse(step(
            "3", "Incubate the nuclei with the beads for 10 min. Place the tube on a magnet and remove the supernatant.",
            "3. Incubate the nuclei with the beads for 10 min. Place the tube on a magnet and remove the \nsupernatant.",
            page=2,
        ))
        found = draft.protocol.sections[0].steps[0].evidence
        self.assertEqual((found.source_page_number, found.continued_on_page_number), (2, 3))
        self.assertEqual(found.continued_excerpt, "supernatant.")

    def test_a_stored_analysis_keeps_both_pages_and_revalidates(self):
        draft = parse(step("1", THAW, THAW, notes=[note(NOTE, f"{NOTE_PAGE_1} {NOTE_PAGE_2}")]))
        payload, _ = serialize_analysis(draft.protocol, draft.readiness, draft.capability_policy_id)
        protocol = deserialize_analysis(payload)[0]
        stored = protocol.sections[0].steps[0].notes[0].evidence
        self.assertEqual(stored.continued_on_page_number, 2)
        verified, _ = validate_protocol_analysis_evidence(protocol, source())
        self.assertEqual(verified.sections[0].steps[0].notes[0].evidence, stored)

    def test_page_text_is_untouched(self):
        extraction = source()
        before = tuple(page.text for page in extraction.pages)
        parse(step("1", THAW, THAW, notes=[note(NOTE, f"{NOTE_PAGE_1} {NOTE_PAGE_2}")]), extraction=extraction)
        self.assertEqual(tuple(page.text for page in extraction.pages), before)


class OtherTextBetweenThePiecesIsRefusedTests(unittest.TestCase):
    def assertRefused(self, *steps: dict, extraction: ProtocolPdfExtraction | None = None) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(*steps, extraction=extraction)

    def test_a_quote_over_three_pages_is_refused(self):
        quote = (
            f"{NOTE_PAGE_1} {PAGE_2_BODY.strip()} supernatant."
        )
        self.assertRefused(step("1", THAW, THAW, notes=[note(quote, quote)]))

    def test_a_quote_that_leaves_out_body_text_of_the_next_page_is_refused(self):
        body = "Spin the tube briefly to collect the liquid. \n" + PAGE_2_BODY
        quote = f"{NOTE_PAGE_1} {NOTE_PAGE_2}"
        self.assertRefused(
            step("1", THAW, THAW, notes=[note(NOTE, quote)]),
            extraction=source(page_2_body=body),
        )

    def test_a_number_line_that_is_not_the_page_number_is_not_skipped(self):
        # A step number printed alone on its line opens page 2.
        body = "25 \n" + PAGE_2_BODY
        quote = f"{NOTE_PAGE_1} {NOTE_PAGE_2}"
        self.assertRefused(
            step("1", THAW, THAW, notes=[note(NOTE, quote)]),
            extraction=source(page_2_body=body),
        )

    def test_a_first_piece_that_is_not_the_page_end_is_refused(self):
        quote = f"1. Thaw a frozen aliquot of nuclei at room temperature. {NOTE_PAGE_2}"
        self.assertRefused(step("1", quote, quote))

    def test_the_header_and_footer_are_not_part_of_the_joined_text(self):
        quote = f"{NOTE_PAGE_1} Copyright Henikoff et al. {NOTE_PAGE_2}"
        self.assertRefused(step("1", THAW, THAW, notes=[note(quote, quote)]))

    def test_a_second_piece_that_stops_inside_a_word_of_the_next_page_is_refused(self):
        # Lane EB's word boundary, read on page 2's own text.
        quote = f"{NOTE_PAGE_1} prepared as previously desc"
        self.assertRefused(step("1", THAW, THAW, notes=[note(quote, quote)]))

    def test_a_second_piece_that_stops_inside_a_number_of_the_next_page_is_refused(self):
        # Lane EB's number boundary, read on page 2's own text ("50,000").
        quote = (
            f"{NOTE_PAGE_1} {NOTE_PAGE_2} Do not use whole cells. "
            "2. Transfer the thawed nuclei suspension in aliquots of no more than 50"
        )
        self.assertRefused(step("1", THAW, THAW, notes=[note(quote, quote)]))

    def test_a_first_piece_that_starts_inside_a_word_of_the_page_is_refused(self):
        # Lane EB's word boundary, read on page 1's own text ("preferably").
        quote = f"ferably {NOTE_PAGE_2}"
        self.assertRefused(step("1", THAW, THAW, notes=[note(quote, quote)]))

    def test_a_recorded_second_page_that_skips_text_is_refused_on_revalidation(self):
        draft = parse(step("1", THAW, THAW, notes=[note(NOTE, f"{NOTE_PAGE_1} {NOTE_PAGE_2}")]))
        protocol = draft.protocol
        section = protocol.sections[0]
        stored = section.steps[0].notes[0]
        bad = replace(stored, evidence=replace(stored.evidence, continued_excerpt="Do not use whole cells."))
        tampered = replace(
            protocol,
            sections=(replace(section, steps=(replace(section.steps[0], notes=(bad,)),)),),
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            validate_protocol_analysis_evidence(tampered, source())


class OcrPageEdgesTests(unittest.TestCase):
    """A page with no text blocks (OCR): its trailing page number line is not body."""

    def extraction(self) -> ProtocolPdfExtraction:
        page_1 = ProtocolPdfPage(
            1, f"{TITLE}\nNote: The control can use either native or lightly cross-linked nuclei, preferably\n- 1 -\n",
            False, ocr_derived=True,
        )
        page_2 = ProtocolPdfPage(
            2, "- 2 -\nprepared as previously described (Kaya-Okur et al., 2020). Do not use whole cells.\n",
            False, ocr_derived=True,
        )
        return ProtocolPdfExtraction(
            original_filename="ocr.pdf", byte_size=1, sha256="e" * 64,
            media_type="application/pdf", page_count=2, encrypted=False,
            metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
            pages=(page_1, page_2),
        )

    def test_the_dashed_page_numbers_are_skipped_on_both_pages(self):
        payload = response(step("1", NOTE, f"{NOTE_PAGE_1}\n{NOTE_PAGE_2}"))
        payload["protocol"]["sections"][0]["title_source_text"] = TITLE
        payload["protocol"]["sections"][0]["evidence"] = evidence(TITLE)
        payload["protocol"]["sections"][0]["steps"][0]["source_label"] = ""
        payload["protocol"]["sections"][0]["steps"][0]["evidence"]["source_excerpt"] = NOTE
        draft = parse_protocol_analysis_response(json.dumps(payload), self.extraction())
        found = draft.protocol.sections[0].steps[0].evidence
        self.assertEqual((found.source_page_number, found.continued_on_page_number), (1, 2))
        self.assertEqual(found.source_excerpt, NOTE_PAGE_1)
        self.assertEqual(found.continued_excerpt, NOTE_PAGE_2)


if __name__ == "__main__":
    unittest.main()
