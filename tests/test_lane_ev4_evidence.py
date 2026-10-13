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
    OCR_REASON_DROPPED_GLYPHS,
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
    _with_dropped_hangul_pages_marked,
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


class HangulSpacingTests(unittest.TestCase):
    """(e) whitespace between Hangul letters is left out of the comparison."""

    def test_a_text_layer_without_spaces_matches_a_spaced_quote(self):
        source = extraction(f"{TITLE}\n1. 1.0 μm의유리섬유여과지로 거른다.")
        parse(source, step("1", "1.0 μm의 유리섬유 여과지로 거른다.", "1. 1.0 μm의 유리섬유 여과지로 거른다."))

    def test_and_a_spaced_text_layer_matches_a_quote_without_spaces(self):
        source = extraction(f"{TITLE}\n1. 용존산소의 분석에 사용되는 시약을 준비한다.")
        parse(source, step("1", "용존산소의분석에사용되는시약을준비한다.",
                           "1. 용존산소의분석에사용되는시약을준비한다."))

    def test_a_line_break_between_hangul_in_a_text_layer_still_counts(self):
        # Lane PX: a Hangul line break is joined in comparison on OCR pages only.
        source = extraction(f"{TITLE}\n1. 개봉한 날\n짜를 용기에 기록한다.")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(source, step("1", "개봉한 날짜를 용기에 기록한다.", "1. 개봉한 날짜를 용기에 기록한다."))

    def test_spaces_between_digits_still_count(self):
        source = extraction(f"{TITLE}\n1. 시료 1 0 mL 를 넣는다.")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(source, step("1", "시료 10 mL 를 넣는다.", "1. 시료 10 mL 를 넣는다."))


def _report_pages(*bodies: str) -> tuple[str, ...]:
    """Pages that open with a header table: page cell, revision, date, title line."""

    count = len(bodies)
    return tuple(
        f"{n} {count}\n01 \n15/08/24 \nR4ALL - SOP – {TITLE}  Page: {n}/{count} \n{body}"
        for n, body in enumerate(bodies, start=1)
    )


class PageFurnitureTests(unittest.TestCase):
    """(a) running lines anywhere on the page, header cells and reprinted headers."""

    def test_header_table_number_cells_are_not_body_between_two_parts(self):
        source = extraction(*_report_pages(
            f"{TITLE}\n1. Seed the cells.\n2. Incubate for 24 h.\n3. Detection",
            "Cells are fixed with 8 % formaldehyde.\n4. Read the plate.",
            "5. Store the plate.",
        ))
        draft = parse(source, step(
            "3", "Detection Cells are fixed with 8 % formaldehyde.", "3. Detection", page=1,
            continued_on_page_number=2, continued_excerpt="Cells are fixed with 8 % formaldehyde."))
        self.assertEqual(draft.protocol.sections[0].steps[0].evidence.continued_on_page_number, 2)

    def test_a_running_line_printed_mid_sentence_is_left_out(self):
        running = "Molinari and Talbot Current Protocols"
        pages = [f"{running}\n{n}. Step {n} text.\n{running}" for n in range(1, 5)]
        pages[0] = f"{running}\n{TITLE}\n1. Step 1 text.\n{running}"
        pages[1] = (f"{running}\n2. Wash the pellet twice with\n{running}\n"
                    "1 mL of cold PBS.\n")
        source = extraction(*pages)
        parse(source, step("2", "Wash the pellet twice with 1 mL of cold PBS.",
                           "2. Wash the pellet twice with 1 mL of cold PBS.", page=2))

    def test_a_table_header_reprinted_at_the_next_page_top_is_skipped(self):
        source = extraction(
            f"{TITLE}\nElectrophoresis Conditions\nStep Action\n1. Set the following:\nRun at 6 V/cm for",
            "Electrophoresis Conditions\nStep Action\n18 h at 14 °C.\n2. Stain the gel.",
        )
        draft = parse(source, step(
            "1", "Set the following: Run at 6 V/cm for 18 h at 14 °C.",
            "1. Set the following:\nRun at 6 V/cm for", page=1,
            continued_on_page_number=2, continued_excerpt="18 h at 14 °C."))
        self.assertEqual(draft.protocol.sections[0].steps[0].evidence.continued_excerpt, "18 h at 14 °C.")

    def test_a_repeated_sentence_is_never_skipped_as_a_header(self):
        note = "NOTE: Alternatively, digest the samples in a microwave."
        source = extraction(
            f"{TITLE}\n{note}\n1. Heat the sample and cover it with",
            f"{note}\n2. Cool the vessel.",
        )
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(source, step(
                "1", "Heat the sample and cover it with 2. Cool the vessel.",
                "1. Heat the sample and cover it with", page=1,
                continued_on_page_number=2, continued_excerpt="2. Cool the vessel."))


class DroppedHangulTests(unittest.TestCase):
    """(d) a text layer that drops its Hangul glyphs goes to OCR."""

    def pages(self, *texts: str) -> tuple[ProtocolPdfPage, ...]:
        return tuple(ProtocolPdfPage(n, text, False) for n, text in enumerate(texts, start=1))

    def test_every_page_of_such_a_layer_is_marked(self):
        dropped = "시험법\n . \n    .\n0.1 M Na2-EDTA 80% \n C18 \n . \n2 g 50 mL\n , \n"
        marked = _with_dropped_hangul_pages_marked(self.pages(dropped, dropped, "4,800 ×g 10 .\n . \n"))
        self.assertEqual([page.ocr_required for page in marked], [True, True, True])
        self.assertEqual({page.ocr_reason for page in marked}, {OCR_REASON_DROPPED_GLYPHS})

    def test_a_korean_layer_with_its_hangul_is_untouched(self):
        page = "시료 2 g 을 50 mL 튜브에 넣는다.\n0.1 M Na2-EDTA 1 mL 를 넣고 섞는다.\n . \n"
        pages = self.pages(page, page)
        self.assertEqual(_with_dropped_hangul_pages_marked(pages), pages)

    def test_dot_leaders_of_an_english_layer_are_untouched(self):
        toc = "Contents\n.......\n1. Scope ........ 3\n.......\n2. Method ...... 4\n.......\n"
        pages = self.pages(toc, "1. Scope of the method.")
        self.assertEqual(_with_dropped_hangul_pages_marked(pages), pages)


if __name__ == "__main__":
    unittest.main()
