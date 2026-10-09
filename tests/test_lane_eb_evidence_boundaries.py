"""Lane EB: where a match stands on its page (human decision 2026-10-09).

Lane EV found that the existing exact comparison reads a claim or a quote as
a substring of its page: the claim "5 mL buffer" was supported by "Add 0.5 mL
buffer.", "20 °C" by "Store at −20 °C.", and the quote "allow sample to go to
dryness." by "Do not allow sample to go to dryness.". A match is now evidence
only where it stands on its page as what it says: not part of a longer number
(a), not starting or ending inside an English word or a number, nor ending
inside a Korean word right before a negative ending (b), and not right after a
negation or right before a Korean negative ending the claim leaves out (c).
Page text, its hash, stored excerpts and evidence identities are untouched.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from voiney_lab import experiment_protocol as domain
from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisEvidenceError
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)

TITLE = "Protocol Alpha"
STEP = "1. Add water."


def extraction(*pages: str) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="synthetic.pdf",
        byte_size=1,
        sha256="e" * 64,
        media_type="application/pdf",
        page_count=len(pages),
        encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(
            ProtocolPdfPage(number, text, False)
            for number, text in enumerate(pages, start=1)
        ),
    )


def evidence(page: int, excerpt: str) -> dict:
    return {"source_page_number": page, "source_excerpt": excerpt}


def step(label: str, text: str, excerpt: str | None = None, **parts) -> dict:
    return {
        "step_id": f"step-{label or 'unlabelled'}",
        "source_label": label,
        "instruction_source_text": text,
        "evidence": evidence(1, text if excerpt is None else excerpt),
        "sub_actions": [],
        **parts,
    }


def material(name: str, excerpt: str, *quantities: str) -> dict:
    return {
        "material_id": "material-1",
        "name_source_text": name,
        "evidence": evidence(1, excerpt),
        "quantities": [{"source_text": quantity} for quantity in quantities],
    }


def parse(source: ProtocolPdfExtraction, *, steps=None, materials=None, metadata=None):
    protocol: dict = {
        "protocol_id": "protocol-alpha",
        "metadata": {
            "title": TITLE,
            "original_language": "en",
            "evidence": evidence(1, TITLE),
            **(metadata or {}),
        },
        "sections": [
            {
                "section_id": "section-1",
                "title_source_text": TITLE,
                "evidence": evidence(1, TITLE),
                "steps": steps if steps is not None else [step("1", STEP)],
            }
        ],
    }
    if materials is not None:
        protocol["materials"] = materials
    return analysis_module.parse_protocol_analysis_response(
        json.dumps(
            {
                "analysis_schema_version": 1,
                "pdf_sha256": source.sha256,
                "capability_policy_id": "p1-conservative",
                "protocol": protocol,
            }
        ),
        source,
    )


def page(*lines: str) -> ProtocolPdfExtraction:
    return extraction("\n".join((TITLE, *lines, STEP)))


class BoundaryAssertions(unittest.TestCase):
    def assert_refused(self, source, reason_code: str, **parts) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            parse(source, **parts)
        self.assertEqual(raised.exception.diagnostic.reason_code, reason_code)


class LaneEvExamplesTests(BoundaryAssertions):
    """The three cases lane EV found the existing exact comparison accepts."""

    def test_a_claim_inside_a_decimal_is_refused(self):
        line = "Add 0.5 mL buffer."
        self.assert_refused(
            page(line), "claim_not_found", materials=[material("buffer", line, "5 mL buffer")]
        )

    def test_a_claim_without_its_minus_sign_is_refused(self):
        line = "Antibody stock: store at −20 °C."
        self.assert_refused(
            page(line), "claim_not_found", materials=[material("Antibody stock", line, "20 °C")]
        )

    def test_a_quote_without_its_negation_is_refused(self):
        source = extraction(f"{TITLE}\nDo not allow sample to go to dryness.")
        self.assert_refused(
            source, "quote_not_found", steps=[step("", "allow sample to go to dryness.")]
        )


def occurs(claim: str, text: str) -> bool:
    return analysis_module._claim_occurs_in_text(claim, text)


def verify(text: str, excerpt: str, page_number: int = 1, *pages: str):
    return analysis_module._verified_evidence(
        domain.SourceEvidence(page_number, excerpt), extraction(text, *pages)
    )


class NumberBoundaryTests(BoundaryAssertions):
    """Rule (a): a match the page continues as a longer number."""

    def test_the_human_decisions_refusals(self):
        cases = {
            "0.5 / 5": ("Add 0.5 mL buffer.", "5 mL buffer"),
            "−20 °C / 20 °C": ("Keep the buffer at −20 °C.", "20 °C"),
            "15 min / 5 min": ("Incubate the buffer for 15 min.", "5 min"),
            "1:1000 / 1:100": ("Dilute the buffer 1:1000 in PBS.", "1:100"),
            "1:1000 / 1000": ("Dilute the buffer 1:1000 in PBS.", "1000 in PBS"),
            "1,000 / 000": ("Spin the buffer at 1,000 g.", "000 g"),
            "5-10 / 10": ("Wash the buffer 5-10 times.", "10 times"),
            "5 – 10 / 10": ("Stir the buffer 5 – 10 min.", "10 min"),
            "20 ± 2 / 2": ("Keep the buffer at 20 ± 2 °C.", "2 °C"),
            "+4 / 4": ("Keep the buffer at +4 °C.", "4 °C"),
            "7.4 / 7": ("Adjust the buffer to pH 7.4 with HCl.", "pH 7"),
            ".5 / 5": ("Add .5 mL buffer.", "5 mL buffer"),
            "4⏎~ 5 / 4": ("Shake the buffer with an amplitude of 4\n~ 5 cm.", "amplitude of 4"),
        }
        for name, (line, claim) in cases.items():
            with self.subTest(name):
                self.assertFalse(occurs(claim, line))
                self.assert_refused(
                    page(line), "claim_not_found", materials=[material("buffer", line, claim)]
                )

    def test_a_quote_inside_a_longer_number_is_refused(self):
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            verify("Add 0.5 mL buffer.", "5 mL buffer.")
        self.assertEqual(raised.exception.diagnostic.reason_code, "quote_not_found")

    def test_the_same_number_standing_alone_is_supported(self):
        cases = {
            "sentence start": ("5 mL buffer is added.", "5 mL buffer"),
            "in parentheses": ("Add buffer (5 mL) to the tube.", "5 mL"),
            "line end": ("Bring the buffer to 5 mL\nthen mix.", "5 mL"),
            "line start": ("Bring the buffer to\n5 mL with water.", "5 mL"),
            "the sign printed in the claim": ("Keep the buffer at −20 °C.", "−20 °C"),
            "the ratio printed whole": ("Dilute the buffer 1:1000 in PBS.", "1:1000"),
            "a count times a time": ("Wash the buffer 3 × 5 min.", "5 min"),
            "a sentence end": ("Spin the buffer for 5 min. Then wash.", "Spin the buffer for 5 min."),
            "the same number elsewhere": ("Add 0.5 mL buffer. Then add 5 mL buffer.", "5 mL buffer"),
        }
        for name, (line, claim) in cases.items():
            with self.subTest(name):
                self.assertTrue(occurs(claim, line))
                draft = parse(page(line), materials=[material("buffer", line, claim)])
                self.assertEqual(draft.protocol.materials[0].quantities[0].source_text, claim)

    def test_a_label_beside_a_number_is_not_a_range(self):
        # A DOI after its label or in its link; a page number line ("- 106 -")
        # above a section number; list dashes under each other; a lone dash
        # line above a numbered item (nifs, protocols.io, EPA 300.0 pages).
        for text, claim in (
            ("doi:10.1371/journal.pone.0123", "10.1371/journal.pone.0123"),
            ("https://doi.org/10.1371/journal.pone.0123", "10.1371/journal.pone.0123"),
            ("- 106 -\n6. 시험방법\n6.1 시료", "6. 시험방법"),
            ("-  605 mg Tris\n- 0.15 g CaCl2\n-  0.876 g NaCl", "0.876 g NaCl"),
            ("12.3 Report results in mg/L.\n-\n12.4 Report NO2 as N", "12.4 Report NO2 as N"),
        ):
            with self.subTest(claim):
                self.assertTrue(occurs(claim, text))

    def test_a_number_the_page_prints_glued_to_another_is_refused(self):
        # An OCR page that printed section 5.7 against "1%": the page text
        # itself is a longer number.
        self.assertFalse(occurs("1%(v/v) 질산 용액", "5.71%(v/v) 질산 용액"))


class WordBoundaryTests(BoundaryAssertions):
    """Rule (b): a match that starts or ends inside a word of its page."""

    def test_the_human_decisions_refusals(self):
        cases = {
            "anti-mouse / mouse": ("Add anti-mouse IgG.", "mouse IgG"),
            "antimouse / mouse": ("Add antimouse IgG.", "mouse IgG"),
            "anti-⏎mouse / mouse": ("Add anti-\nmouse IgG.", "mouse IgG"),
            "goat anti-mouse / goat anti": ("Add goat anti-mouse IgG.", "goat anti"),
            "PBST / PBS": ("Wash in PBST twice.", "Wash in PBS"),
            "phosphate-⏎buffered / phosphate-": ("PBS or phosphate-\nbuffered water", "PBS or phosphate-"),
            "Photo-⏎graphs / graphs": ("Photo-\ngraphs were taken.", "graphs were taken."),
        }
        for name, (line, claim) in cases.items():
            with self.subTest(name):
                self.assertFalse(occurs(claim, line))
                self.assert_refused(
                    page(line), "claim_not_found", materials=[material(claim, line)]
                )

    def test_a_korean_quote_cut_before_a_negative_ending_is_refused(self):
        for line, quote in (
            ("시료를 건조시키지 마십시오.", "시료를 건조시키"),
            ("시료를 가열하지 않는다.", "시료를 가열하"),
            ("용기를 흔들지 말 것.", "용기를 흔들"),
        ):
            with self.subTest(quote):
                with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
                    verify(line, quote)
                self.assertEqual(raised.exception.diagnostic.reason_code, "quote_not_found")

    def test_whole_words_are_supported(self):
        cases = {
            "the compound printed whole": ("Add anti-mouse IgG.", "anti-mouse IgG"),
            "a Korean particle": ("에탄올을 넣는다.", "에탄올"),
            "a Korean verb ending": ("시료를 건조시킨다.", "시료를 건조시"),
            "a word before a bracket": ("Add PBS (pH 7.4).", "Add PBS"),
            "a word before a slash": ("Penicillin/Streptomycin 1%", "Penicillin"),
            "a reference mark after a word": ("as described previously12. Then", "as described previously"),
            "a mark before a word": ("1Department of Biology", "Department of Biology"),
        }
        for name, (line, claim) in cases.items():
            with self.subTest(name):
                self.assertTrue(occurs(claim, line))

    def test_an_author_name_beside_its_affiliation_mark_is_supported(self):
        # PMC8250384, PMC6931130, PMC7888634, PMC6093710 title pages.
        line = (
            "Steven Henikoff1, 2, *, Joana C. Prataa,*,1, Teresa Rocha-Santosa\n"
            "João P. da Costaª, William L. RedmondID1*"
        )
        names = ["Steven Henikoff", "Joana C. Prata", "Teresa Rocha-Santos",
                 "João P. da Costa", "William L. Redmond"]
        draft = parse(page(line), metadata={"authors": names, "authors_evidence": evidence(1, line)})
        self.assertEqual(list(draft.protocol.metadata.authors), names)

    def test_only_an_author_name_reads_past_an_affiliation_mark(self):
        line = "Joana C. Prataa,*,1"
        self.assertFalse(occurs("Joana C. Prata", line))
        self.assert_refused(page(line), "claim_not_found", materials=[material("Joana C. Prata", line)])


class NegationTests(BoundaryAssertions):
    """Rule (c): a negation right before the match, or a Korean one right after it."""

    def test_the_human_decisions_refusals(self):
        cases = {
            "do not": ("Do not allow sample to go to dryness.", "allow sample to go to dryness."),
            "don't": ("Don't vortex the tube.", "vortex the tube."),
            "never": ("Never heat the sample above 50 °C.", "heat the sample above 50 °C."),
            "no": ("Make sure no bubbles remain.", "bubbles remain."),
            "avoid": ("Avoid freezing and thawing.", "freezing and thawing."),
            "without": ("Centrifuge without brake.", "brake."),
            "not to": ("Be careful not to touch the pellet.", "touch the pellet."),
            "not more than": ("Use not more than 5 mL.", "more than 5 mL."),
            "not across a line break": ("Do not\nallow sample to dry.", "allow sample to dry."),
            "금지 before": ("금지: 화기 사용", "화기 사용"),
            "하지 않는다": ("시료를 가열하지 않는다.", "시료를 가열하지"),
            "하지 마십시오": ("시료를 건조시키지 마십시오.", "시료를 건조시키지"),
            "하지 말 것": ("용기를 흔들지 말 것.", "용기를 흔들지"),
            "해서는 안 된다": ("시료를 가열해서는 안 된다.", "시료를 가열"),
            "사용 금지": ("화기 사용 금지.", "화기 사용"),
            "없이": ("교반 없이 10분 둔다.", "교반"),
            "a word broken before its ending": ("시료를 건조시키\n지 마십시오.", "시료를 건조시키"),
            "a capitalized No": ("Caution: No Smoking in the room.", "Smoking in the room."),
        }
        for name, (line, quote) in cases.items():
            with self.subTest(name):
                self.assertFalse(occurs(quote, line))
                with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
                    verify(line, quote)
                self.assertEqual(raised.exception.diagnostic.reason_code, "quote_not_found")

    def test_a_claim_without_its_negation_is_refused(self):
        line = "Do not vortex the tube; flick it."
        self.assert_refused(
            page(line), "claim_not_found", materials=[material("vortex the tube", line)]
        )

    def test_a_negation_the_match_quotes_or_another_clause_holds_is_supported(self):
        cases = {
            "the negation quoted": ("Do not allow sample to go to dryness.", "Do not allow sample to go to dryness."),
            "another sentence": ("Do not vortex. Add 5 mL buffer.", "Add 5 mL buffer."),
            "another clause": ("If not, add 5 mL buffer.", "add 5 mL buffer."),
            "a number sign": ("Use filter paper No 1 for this.", "1 for this."),
            "a form field above": ("AOAC/ASTM: No\nAuthors: Todor I. Todorov", "Authors: Todor I. Todorov"),
            "a form field before": ("AOAC/ASTM: No Authors: Todor I. Todorov", "Authors: Todor I. Todorov"),
            "까지": ("37 °C까지 가열한다.", "37 °C까지 가열한다."),
            "the Korean negation quoted": ("시료를 가열하지 않는다.", "시료를 가열하지 않는다."),
            "the Korean negation quoted to a line end": ("시료는 여과해서는안\n된다.", "시료는 여과해서는안"),
            "a negation further on": ("이 용액은\n공기층이 남지 않도록 나눈다.", "이 용액은"),
            "a heading above its text": ("4. 시료의 채취 및 보관\n공기와의 접촉이 없도록", "4. 시료의 채취 및 보관"),
        }
        for name, (line, quote) in cases.items():
            with self.subTest(name):
                self.assertTrue(occurs(quote, line))
                self.assertEqual(verify(line, quote).source_excerpt, quote)


class EveryComparisonTests(unittest.TestCase):
    """The boundaries apply wherever the existing comparison verifies text."""

    def test_a_step_timer_inside_a_longer_time_is_refused(self):
        text = "1. Incubate the plate for 15 min."
        source = extraction(f"{TITLE}\n{text}")
        evidence_ = domain.SourceEvidence(1, text)
        protocol = domain.ExperimentProtocol(
            "timer",
            domain.ProtocolMetadata(source, TITLE, "en", evidence=domain.SourceEvidence(1, TITLE)),
            sections=(domain.ProtocolSection("s", TITLE, domain.SourceEvidence(1, TITLE), (
                domain.ProtocolSourceStep("t1", "1", text, evidence_, sub_actions=(
                    domain.ProtocolSubAction(
                        "a1", "Incubate the plate", evidence_,
                        estimated_duration=domain.EstimatedDuration("5 min", 300),
                    ),
                )),
            )),),
        )
        table = analysis_module.verify_step_timers(protocol, source)
        self.assertNotIn("5 min", {timer.literal for timer in table.verified})
        self.assertIn(("5 min", "not_in_step_text"), {(item.literal, item.reason) for item in table.refused})
        self.assertIn(("t1", "15 min"), {(timer.step_id, timer.literal) for timer in table.verified})

    def test_a_statement_across_a_page_end_reads_both_pages(self):
        source = extraction(f"{TITLE}\nAdd 0.", "5 mL buffer to the tube.")
        self.assertIsNone(
            analysis_module._statement_across_page_end("5 mL buffer to the tube.", source, 1)
        )
        source = extraction(f"{TITLE}\nAdd 0.5 mL", "buffer to the tube.")
        self.assertIsNotNone(
            analysis_module._statement_across_page_end("0.5 mL buffer to the tube.", source, 1)
        )

    def test_the_other_page_diagnostic_reads_the_boundaries(self):
        source = extraction(f"{TITLE}\n{STEP}", "Add 0.5 mL buffer.")
        self.assertEqual(analysis_module._matching_source_pages("5 mL buffer", source), ())
        self.assertEqual(analysis_module._matching_source_pages("0.5 mL buffer", source), (2,))

    def test_the_chunk_merge_revalidation_reads_the_boundaries(self):
        from voiney_lab import protocol_claim_analysis

        self.assertIs(
            protocol_claim_analysis.validate_protocol_analysis_evidence,
            analysis_module.validate_protocol_analysis_evidence,
        )
        line = "Add 0.5 mL buffer."
        source = page(line)
        draft = parse(source, materials=[material("buffer", line, "0.5 mL buffer")])

        def quantity(text: str):
            return replace(draft.protocol, materials=(replace(
                draft.protocol.materials[0], quantities=(domain.ScientificValue(text),),
            ),))

        verified, _ = analysis_module.validate_protocol_analysis_evidence(quantity("0.5 mL buffer"), source)
        self.assertEqual(verified.materials[0].quantities[0].source_text, "0.5 mL buffer")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            analysis_module.validate_protocol_analysis_evidence(quantity("5 mL buffer"), source)


if __name__ == "__main__":
    unittest.main()
