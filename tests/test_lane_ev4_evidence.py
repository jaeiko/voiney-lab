"""Evidence the source prints, read past its layout (lane AN, EV4).

Human decision 2026-10-13, from the evidence failures lanes EV2, EV3 and M3
left (each measured against the stored DS-2, M3 and EV3 answers, with no pass
lost and no wrong item accepted -- lane AN report):

(a) the running header, footer and page number a page prints in the middle of
    its body, a table header reprinted at the top of the next page, and a
    header table's number-only cells, are left out of the comparison;
(b) a soft hyphen, a subscript and a superscript compare as the characters
    they stand for -- only in the comparison, and a superscript after a digit
    stays itself ("10⁵" is never "105");
(c) a step label written "3)" or "(2)" is the step number 3 or 2;
(d) a text layer that drops its Hangul glyphs is read by OCR;
(e) whitespace between two Hangul letters is left out of the comparison.

A person's "분석 다시 시도" after a failed first analysis gets the one
automatic retry a first analysis gets.
"""

from __future__ import annotations

import json
import re
import unittest

from voiney_lab.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    parse_protocol_analysis_response,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)

TITLE = "Phospholipidosis detection"


def extraction(*pages: str) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="ev4.pdf", byte_size=1, sha256="f" * 64,
        media_type="application/pdf", page_count=len(pages), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(ProtocolPdfPage(n, text, False) for n, text in enumerate(pages, start=1)),
    )


def evidence(excerpt: str, page: int = 1, **more) -> dict:
    return {"source_page_number": page, "source_excerpt": excerpt, **more}


def step(label: str, instruction: str, excerpt: str, page: int = 1, **more) -> dict:
    return {"step_id": f"step-{re.sub(r'[^0-9a-z]+', '-', label) or 'x'}-{page}".replace("--", "-"),
            "source_label": label,
            "instruction_source_text": instruction, "evidence": evidence(excerpt, page, **more)}


def parse(source: ProtocolPdfExtraction, *steps: dict):
    return parse_protocol_analysis_response(json.dumps({
        "analysis_schema_version": 1, "pdf_sha256": source.sha256,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "protocol-ev4",
            "metadata": {"title": TITLE, "original_language": "en", "evidence": evidence(TITLE)},
            "before_start": [], "materials": [], "equipment": [],
            "sections": [{"section_id": "s1", "title_source_text": TITLE,
                          "evidence": evidence(TITLE), "steps": list(steps)}],
            "constructs": [], "description": None,
        },
    }), source)


class ParenthesizedLabelTests(unittest.TestCase):
    """(c) "3)" and "(2)" are the step numbers 3 and 2, as the page prints them."""

    def test_a_label_with_its_parentheses_is_read_as_the_number(self):
        source = extraction(f"{TITLE}\n(1) Add 10 mL of buffer to the tube.\n3) Mix gently.")
        draft = parse(source,
                      step("(1)", "Add 10 mL of buffer to the tube.", "(1) Add 10 mL of buffer to the tube."),
                      step("3)", "Mix gently.", "3) Mix gently."))
        self.assertEqual([s.source_label for s in draft.protocol.sections[0].steps], ["1", "3"])
        # The quotes are kept as the model gave them.
        self.assertEqual(draft.protocol.sections[0].steps[0].evidence.source_excerpt,
                         "(1) Add 10 mL of buffer to the tube.")

    def test_a_number_the_page_does_not_print_there_is_still_refused(self):
        source = extraction(f"{TITLE}\n(1) Add 10 mL of buffer to the tube.")
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            parse(source, step("(2)", "Add 10 mL of buffer to the tube.", "(1) Add 10 mL of buffer to the tube."))
        self.assertEqual(raised.exception.diagnostic.reason_code, "source_label_not_found")

    def test_other_text_in_parentheses_is_not_a_label(self):
        source = extraction(f"{TITLE}\n(1:100) Dilute the antibody.")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(source, step("(1:100)", "Dilute the antibody.", "Dilute the antibody."))


class ScriptAndSoftHyphenTests(unittest.TestCase):
    """(b) soft hyphen, subscripts and superscripts compare as what they write."""

    def test_a_soft_hyphen_at_a_line_end_joins_the_word(self):
        source = extraction(f"{TITLE}\n1. Use the reagent formu­\nlated for lysis.")
        draft = parse(source, step("1", "Use the reagent formulated for lysis.",
                                   "1. Use the reagent formulated for lysis."))
        self.assertEqual(draft.protocol.sections[0].steps[0].evidence.source_excerpt,
                         "1. Use the reagent formu­\nlated for lysis.")

    def test_a_soft_hyphen_may_be_quoted_as_a_hyphen(self):
        source = extraction(f"{TITLE}\n1. Plate on DW­\nMEA agar.")
        parse(source, step("1", "Plate on DW-MEA agar.", "1. Plate on DW-MEA agar."))

    def test_subscripts_and_superscripts_after_a_letter_read_as_digits(self):
        source = extraction(f"{TITLE}\n1. Add 1 mL of 30% H2O2 per cm2 of tissue.")
        parse(source, step("1", "Add 1 mL of 30% H₂O₂ per cm² of tissue.",
                           "1. Add 1 mL of 30% H₂O₂ per cm² of tissue."))

    def test_a_superscript_after_a_digit_is_never_a_digit(self):
        source = extraction(f"{TITLE}\n1. Seed 105 cells per well.")
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            parse(source, step("1", "Seed 10⁵ cells per well.", "1. Seed 10⁵ cells per well."))
        self.assertEqual(raised.exception.diagnostic.reason_code, "quote_not_found")


if __name__ == "__main__":
    unittest.main()
