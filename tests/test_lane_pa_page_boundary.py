"""A step sentence cut at a page end (lane PA, human decision 3, 2026-10-06).

A step that a page ends in the middle of, and that the next page finishes,
is accepted as evidence only when the sentence is found exactly in the text
the two pages make when joined: the body text that ends the first page (the
lowest text block above the running footer) followed by the first text of the
next page. Both pages are recorded on the evidence. Every other evidence rule
stands: an invented continuation, pieces in the wrong order, a piece that is
not at the page end or the next page's start, and a page outside the document
are refused as before. Page text, its hash and evidence identities are never
changed.

The synthetic pages below are shaped like in-gel p.8/p.9 (lane P3): the step
block ends the page, a side-column duration and the footer follow it, and the
next page opens with the rest of the sentence. The last test reads the real
in-gel PDF when it is present (condition A) and is skipped otherwise.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
    parse_protocol_analysis_response,
    validate_protocol_analysis_evidence,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import (
    deserialize_analysis,
    serialize_analysis,
)
from voiney_lab.pdf_text_engine import PdfTextBlock

ROOT = Path(__file__).resolve().parents[1]
INGEL_PDF = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"

TITLE = "Peptide extraction test protocol"
FOOTER_1 = "example.org | https://example.org/protocol June 19, 2025 1/3"
FOOTER_2 = "example.org | https://example.org/protocol June 19, 2025 2/3"
FOOTER_3 = "example.org | https://example.org/protocol June 19, 2025 3/3"

PAGE_1_BODY = (
    f"{TITLE}\n"
    "Extract the peptides 30m\n"
    "24 Spin down the digest. Add formic acid to 1% (v/v). Keep the solution, which will contain the\n"
    "16h\n"
    "30m\n"
)
PAGE_2_BODY = (
    "peptides. To extract more peptides, soak the gel piece in buffer. Pool the peptides.\n"
    "25 Dry the extracted peptides in a speedvac.\n"
    "26 Store the dried peptides until analysis. Keep the tube\n"
)
PAGE_3_BODY = "closed and cold.\n"


def _block(x0, y0, x1, y1, text):
    return PdfTextBlock(x0=x0, y0=y0, x1=x1, y1=y1, font_size=12.0, bold=False, text=text)


def _page(number: int, body: str, footer: str, blocks) -> ProtocolPdfPage:
    return ProtocolPdfPage(
        number, body + footer, False,
        bottom_band_offset=len(body), blocks=tuple(blocks),
    )


def source() -> ProtocolPdfExtraction:
    pages = (
        _page(1, PAGE_1_BODY, FOOTER_1, (
            _block(40, 40, 400, 55, TITLE),
            _block(42, 600, 578, 615, "Extract the peptides\n30m"),
            _block(41, 684, 511, 711, "24\nSpin down the digest. Add formic acid to 1% (v/v). Keep the solution, which will contain the"),
            _block(560, 600, 575, 610, "16h"),
            _block(557, 688, 576, 698, "30m"),
            _block(21, 762, 590, 774, "example.org | https://example.org/protocol\nJune 19, 2025\n1/3"),
        )),
        _page(2, PAGE_2_BODY, FOOTER_2, (
            _block(75, 44, 501, 56, "peptides. To extract more peptides, soak the gel piece in buffer. Pool the peptides."),
            _block(41, 90, 400, 104, "25\nDry the extracted peptides in a speedvac."),
            _block(41, 690, 400, 711, "26\nStore the dried peptides until analysis. Keep the tube"),
            _block(21, 762, 590, 774, "example.org | https://example.org/protocol\nJune 19, 2025\n2/3"),
        )),
        _page(3, PAGE_3_BODY, FOOTER_3, (
            _block(75, 44, 501, 56, "closed and cold."),
            _block(21, 762, 590, 774, "example.org | https://example.org/protocol\nJune 19, 2025\n3/3"),
        )),
    )
    return ProtocolPdfExtraction(
        original_filename="boundary.pdf", byte_size=1, sha256="b" * 64,
        media_type="application/pdf", page_count=len(pages), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=pages,
    )


CROSSING = (
    "Spin down the digest. Add formic acid to 1% (v/v). Keep the solution, "
    "which will contain the peptides. To extract more peptides, soak the gel piece in buffer."
)
PAGE_1_PART = (
    "24 Spin down the digest. Add formic acid to 1% (v/v). Keep the solution, "
    "which will contain the"
)


def evidence(excerpt: str, page: int = 1) -> dict:
    return {"source_page_number": page, "source_excerpt": excerpt}


def step(label: str, instruction: str, excerpt: str, page: int = 1, step_id: str | None = None) -> dict:
    return {
        "step_id": step_id or f"step-{label}",
        "source_label": label,
        "instruction_source_text": instruction,
        "evidence": evidence(excerpt, page),
    }


def response(*steps: dict) -> dict:
    return {
        "analysis_schema_version": 1,
        "pdf_sha256": "b" * 64,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "protocol-pa",
            "metadata": {"title": TITLE, "original_language": "en", "evidence": evidence(TITLE)},
            "before_start": [],
            "materials": [],
            "equipment": [],
            "sections": [{
                "section_id": "procedure",
                "title_source_text": TITLE,
                "evidence": evidence(TITLE),
                "steps": list(steps),
            }],
            "constructs": [],
            "description": None,
        },
    }


def parse(*steps: dict, extraction: ProtocolPdfExtraction | None = None):
    return parse_protocol_analysis_response(json.dumps(response(*steps)), extraction or source())


def first_step(draft):
    return draft.protocol.sections[0].steps[0]


class CutSentenceIsAcceptedTests(unittest.TestCase):
    def test_a_claim_cut_at_the_page_end_is_found_in_the_joined_pages(self):
        draft = parse(step("24", CROSSING, PAGE_1_PART))
        self.assertEqual(first_step(draft).instruction_source_text, CROSSING)

    def test_both_pages_are_recorded_on_the_evidence(self):
        found = first_step(parse(step("24", CROSSING, PAGE_1_PART))).evidence
        self.assertEqual(found.source_page_number, 1)
        self.assertEqual(found.source_excerpt, PAGE_1_PART)
        self.assertEqual(found.continued_on_page_number, 2)
        self.assertEqual(
            found.continued_excerpt,
            "peptides. To extract more peptides, soak the gel piece in buffer.",
        )

    def test_an_excerpt_quoted_across_the_page_end_is_split_onto_its_two_pages(self):
        found = first_step(parse(step("24", CROSSING, "24 " + CROSSING))).evidence
        # Each piece is the page's own characters, so it is on its page as is.
        self.assertEqual(found.source_excerpt, PAGE_1_PART)
        self.assertIn(found.source_excerpt, source().pages[0].text)
        self.assertEqual(found.continued_on_page_number, 2)
        self.assertIn(found.continued_excerpt, source().pages[1].text)
        self.assertTrue(source().pages[1].text.startswith(found.continued_excerpt))

    def test_a_step_that_crosses_from_the_middle_page_to_the_last_one(self):
        found = first_step(parse(step(
            "26", "Store the dried peptides until analysis. Keep the tube closed and cold.",
            "26 Store the dried peptides until analysis. Keep the tube", page=2,
        ))).evidence
        self.assertEqual((found.source_page_number, found.continued_on_page_number), (2, 3))
        self.assertEqual(found.continued_excerpt, "closed and cold.")

    def test_a_step_on_one_page_records_no_second_page(self):
        found = first_step(parse(step(
            "25", "Dry the extracted peptides in a speedvac.",
            "25 Dry the extracted peptides in a speedvac.", page=2,
        ))).evidence
        self.assertIsNone(found.continued_on_page_number)
        self.assertIsNone(found.continued_excerpt)

    def test_the_page_text_and_its_hash_are_untouched(self):
        extraction = source()
        before = tuple(page.text for page in extraction.pages)
        parse(step("24", CROSSING, PAGE_1_PART), extraction=extraction)
        self.assertEqual(tuple(page.text for page in extraction.pages), before)
        self.assertEqual(extraction.sha256, "b" * 64)

    def test_a_stored_analysis_keeps_both_pages_and_revalidates(self):
        draft = parse(step("24", CROSSING, PAGE_1_PART))
        payload, _ = serialize_analysis(draft.protocol, draft.readiness, draft.capability_policy_id)
        protocol = deserialize_analysis(payload)[0]
        stored = protocol.sections[0].steps[0].evidence
        self.assertEqual(stored.continued_on_page_number, 2)
        verified, _ = validate_protocol_analysis_evidence(protocol, source())
        self.assertEqual(verified.sections[0].steps[0].evidence, stored)


class OtherEvidenceRulesStandTests(unittest.TestCase):
    def assertRefused(self, *steps: dict) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(*steps)

    def test_an_invented_continuation_is_refused(self):
        self.assertRefused(step(
            "24", "Keep the solution, which will contain the proteins. To extract more",
            PAGE_1_PART,
        ))

    def test_pieces_in_the_wrong_order_are_refused(self):
        self.assertRefused(step(
            "24", "peptides. To extract more peptides, which will contain the",
            PAGE_1_PART,
        ))

    def test_a_first_piece_that_is_not_the_page_end_is_refused(self):
        # "Extract the peptides" is page 1 text, but not the text that ends it.
        self.assertRefused(step(
            "24", "Extract the peptides 30m peptides. To extract more peptides",
            PAGE_1_PART,
        ))

    def test_a_second_piece_that_is_not_the_next_page_start_is_refused(self):
        self.assertRefused(step(
            "24", "which will contain the Dry the extracted peptides in a speedvac.",
            PAGE_1_PART,
        ))

    def test_the_side_column_and_footer_are_not_part_of_the_joined_text(self):
        self.assertRefused(step(
            "24", "which will contain the 16h 30m peptides.", PAGE_1_PART,
        ))
        self.assertRefused(step(
            "24", "which will contain the example.org | https://example.org/protocol",
            PAGE_1_PART,
        ))

    def test_a_page_outside_the_document_is_refused(self):
        self.assertRefused(step(
            "x", "closed and cold. more text", "closed and cold.", page=3,
        ))

    def test_a_claim_that_starts_on_the_next_page_is_not_cited_from_the_previous_one(self):
        self.assertRefused(step(
            "24", "peptides. To extract more peptides, soak the gel piece in buffer.",
            PAGE_1_PART,
        ))

    def test_a_provider_cannot_supply_the_second_page_itself(self):
        payload = step("24", CROSSING, PAGE_1_PART)
        payload["evidence"]["continued_on_page_number"] = 2
        payload["evidence"]["continued_excerpt"] = "peptides."
        with self.assertRaises(ProtocolAnalysisResponseError):
            parse(payload)

    def test_a_recorded_second_page_that_does_not_continue_the_first_is_refused(self):
        draft = parse(step("24", CROSSING, PAGE_1_PART))
        protocol = draft.protocol
        section = protocol.sections[0]
        bad = replace(
            section.steps[0],
            evidence=replace(section.steps[0].evidence, continued_excerpt="Dry the extracted peptides"),
        )
        tampered = replace(protocol, sections=(replace(section, steps=(bad,)),))
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            validate_protocol_analysis_evidence(tampered, source())

    def test_the_domain_refuses_a_second_page_that_is_not_the_next_page(self):
        draft = parse(step("24", CROSSING, PAGE_1_PART))
        protocol = draft.protocol
        section = protocol.sections[0]
        bad = replace(
            section.steps[0],
            evidence=replace(section.steps[0].evidence, continued_on_page_number=3),
        )
        tampered = replace(protocol, sections=(replace(section, steps=(bad,)),))
        with self.assertRaises(domain.ProtocolValidationError):
            domain.validate_protocol(tampered)


@unittest.skipUnless(INGEL_PDF.is_file(), "licensed in-gel PDF absent (condition B)")
class InGelStep24Tests(unittest.TestCase):
    def test_in_gel_step_24_is_accepted_with_pages_8_and_9(self):
        extraction = extract_protocol_pdf(INGEL_PDF)
        page_8 = extraction.pages[7].text
        start = page_8.index("Quickly spin down")
        end = page_8.index("which will contain the") + len("which will contain the")
        claim = page_8[start:end].replace("\n", " ") + " peptides."
        payload = response(step("24", claim, "24 " + page_8[start:end], page=8))
        title = extraction.pages[0].text.splitlines()[1]
        payload["pdf_sha256"] = extraction.sha256
        payload["protocol"]["metadata"] = {
            "title": title, "original_language": "en", "evidence": evidence(title),
        }
        payload["protocol"]["sections"][0]["title_source_text"] = title
        payload["protocol"]["sections"][0]["evidence"] = evidence(title)
        draft = parse_protocol_analysis_response(json.dumps(payload), extraction)
        found = draft.protocol.sections[0].steps[0].evidence
        self.assertEqual((found.source_page_number, found.continued_on_page_number), (8, 9))
        self.assertEqual(found.continued_excerpt, "peptides.")


if __name__ == "__main__":
    unittest.main()
