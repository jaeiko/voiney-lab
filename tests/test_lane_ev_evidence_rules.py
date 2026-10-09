"""Lane EV: evidence comparison rules from lane DS-2 (human decision 2026-10-09).

Lane DS-2 replayed stored analysis responses offline and found answers
refused for how a page prints a thing, not for what it says: a metadata date
normalized to ISO form, a step number printed in parentheses, one sentence
printed twice on its page, a material name whose parenthesis the page closes
differently. Each rule here is a comparison form only -- page text, its hash
and the evidence identities are untouched -- and each keeps the refusals the
human decision lists: a different number (0.5 / 5, 1:1000 / 1:100, 30 / 3), a
different unit (mL / µL), a dropped negation ("하지 않는다" / "한다") and the
same text cited from a different page.
"""

from __future__ import annotations

import json
import unittest

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


def step(label: str, text: str, page: int = 1, excerpt: str | None = None) -> dict:
    return {
        "step_id": f"step-{label or 'unlabelled'}",
        "source_label": label,
        "instruction_source_text": text,
        "evidence": evidence(page, text if excerpt is None else excerpt),
        "sub_actions": [],
    }


def response(
    source: ProtocolPdfExtraction,
    *,
    metadata: dict | None = None,
    steps: list[dict] | None = None,
    materials: list[dict] | None = None,
) -> str:
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
    return json.dumps(
        {
            "analysis_schema_version": 1,
            "pdf_sha256": source.sha256,
            "capability_policy_id": "p1-conservative",
            "protocol": protocol,
        }
    )


def parse(source: ProtocolPdfExtraction, **parts):
    return analysis_module.parse_protocol_analysis_response(
        response(source, **parts), source
    )


class RefusalAssertions(unittest.TestCase):
    def assert_refused(
        self, source: ProtocolPdfExtraction, reason_code: str, **parts
    ) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            parse(source, **parts)
        self.assertEqual(raised.exception.diagnostic.reason_code, reason_code)


def dated_page(date_line: str) -> ProtocolPdfExtraction:
    return extraction(f"{TITLE}\n{date_line}\n{STEP}")


def date_claim(claim: str, excerpt: str, field: str = "publication_date") -> dict:
    return {field: claim, f"{field}_evidence": evidence(1, excerpt)}


class WrittenDateTests(RefusalAssertions):
    """Rule iso_date: an ISO date claim against the date its excerpt prints."""

    def test_an_iso_date_printed_in_words_is_supported(self):
        # The forms lane DS-2 met: PMC7359711, PMC9036317, PMC6526448.
        for line, claim in (
            ("Published: 3 April 2020", "2020-04-03"),
            ("June 17, 2022", "2022-06-17"),
            ("Received: 20 March 2018", "2018-03-20"),
            ("published: 22 December 2014", "2014-12-22"),
        ):
            with self.subTest(line=line):
                draft = parse(dated_page(line), metadata=date_claim(claim, line))
                self.assertEqual(draft.protocol.metadata.publication_date, claim)

    def test_every_metadata_date_field_reads_the_written_date(self):
        line = "Received: 7 May 2014"
        for field in ("created_date", "modified_date", "publication_date"):
            with self.subTest(field=field):
                draft = parse(
                    dated_page(line), metadata=date_claim("2014-05-07", line, field)
                )
                self.assertEqual(getattr(draft.protocol.metadata, field), "2014-05-07")

    def test_the_written_forms_of_lane_ds2(self):
        for line in (
            "Published: 03 April 2020",
            "Published: 3 Apr 2020",
            "Published: April 3 2020",
            "Published: Apr 3, 2020",
            "Published: 2020/04/03",
            "Published: 2020.04.03",
            "게재일: 2020년 4월 3일",
            "Published: 3\nApril 2020",
            "PUBLISHED: 3 APRIL 2020",
        ):
            with self.subTest(line=line):
                parse(dated_page(line), metadata=date_claim("2020-04-03", line))

    def test_a_day_first_numeric_date_is_read_only_where_it_cannot_be_month_first(self):
        for line, claim in (("Published: 20.03.2018", "2018-03-20"), ("Published: 4.4.2020", "2020-04-04")):
            with self.subTest(line=line):
                parse(dated_page(line), metadata=date_claim(claim, line))
        # 03.04.2020 is 3 April day-first but 4 March month-first: refused.
        line = "Published: 03.04.2020"
        self.assert_refused(
            dated_page(line), "claim_not_found", metadata=date_claim("2020-04-03", line)
        )

    def test_the_date_must_be_in_the_fields_own_excerpt(self):
        # The page prints the date, but the evidence the claim cites does not.
        self.assert_refused(
            dated_page("Published: 3 April 2020"),
            "claim_not_found",
            metadata=date_claim("2020-04-03", TITLE),
        )

    def test_only_a_metadata_date_field_reads_a_written_date(self):
        line = "Published: 3 April 2020"
        self.assert_refused(
            dated_page(line), "claim_not_found", metadata=date_claim("2020-04-03", line, "version")
        )

    def test_a_calendar_date_that_does_not_exist_is_refused(self):
        line = "Published: 30 February 2020"
        self.assert_refused(
            dated_page(line), "claim_not_found", metadata=date_claim("2020-02-30", line)
        )

    def test_the_human_decisions_refusals(self):
        cases = {
            # A day digit that is the end of a decimal.
            "0.5 / 5": ("2020-04-05", "Released: 0.5 April 2020"),
            # A number the page continues: the ratio's 10 and a longer year.
            "1:1000 / 1:100": ("2020-01-10", "Diluted 1:10 January 2020"),
            "1:1000 / 1:100, longer year": ("2020-01-10", "Published: 10 January 20201"),
            "30 / 3": ("2020-04-03", "Published: 13 April 2020"),
            "3 / 30": ("2020-04-03", "Published: 30 April 2020"),
            "30 / 3, claimed 30": ("2020-04-30", "Published: 3 April 2020"),
            # The month is the date's unit.
            "mL / µL": ("2020-03-03", "Published: 3 May 2020"),
            # Only a bare ISO date is read: a statement never is.
            "하지 않는다 / 한다": ("2020-04-03 이후 하지 않는다", "2020년 4월 3일 이후 한다"),
        }
        for name, (claim, line) in cases.items():
            with self.subTest(name):
                self.assert_refused(
                    dated_page(line), "claim_not_found", metadata=date_claim(claim, line)
                )

    def test_the_date_on_a_different_page_is_refused(self):
        line = "Published: 3 April 2020"
        source = extraction(f"{TITLE}\n{STEP}", line)
        # Cited from page 1, where it is not printed.
        self.assert_refused(source, "quote_not_found", metadata=date_claim("2020-04-03", line))
        self.assert_refused(source, "claim_not_found", metadata=date_claim("2020-04-03", TITLE))


def numbered_page(*lines: str) -> ProtocolPdfExtraction:
    return extraction("\n".join((TITLE, *lines)))


class ParenthesizedLabelTests(RefusalAssertions):
    """Rule paren_label: a step label printed in parentheses."""

    def test_a_label_printed_in_parentheses_opens_its_excerpt(self):
        # fda_eam_4_7_icpms: "(1) Add a few drops of reagent grade deionized water".
        for text in ("(1) Add a few drops of water.", "1) Add a few drops of water."):
            with self.subTest(text=text):
                draft = parse(numbered_page(text), steps=[step("1", text)])
                self.assertEqual(draft.protocol.sections[0].steps[0].source_label, "1")

    def test_a_label_printed_in_parentheses_before_the_excerpt(self):
        text = "Add a few drops of water."
        for printed in ("(1)", "1)"):
            with self.subTest(printed=printed):
                parse(numbered_page(f"{printed} {text}"), steps=[step("1", text)])

    def test_every_label_form_reads_its_parentheses(self):
        for label, text in (("a", "(a) Add water."), ("6.1", "6.1) Add water."), ("12", "(12) Add water.")):
            with self.subTest(label=label):
                parse(numbered_page(text), steps=[step(label, text)])

    def test_a_label_that_holds_the_parentheses_is_still_refused(self):
        # fda_eam_4_13_iodine: the label "(1)" -- the prompt asks for the number.
        text = "(1) Add water."
        self.assert_refused(numbered_page(text), "source_label_not_found", steps=[step("(1)", text)])

    def test_a_figure_caption_step_is_still_refused(self):
        # PMC6093710: "Step 1: the tagged protein ..." is a figure legend.
        text = "Step 1: the tagged protein is extracted."
        self.assert_refused(numbered_page(text), "source_label_not_found", steps=[step("Step 1", text)])

    def test_the_human_decisions_refusals(self):
        add = "Add water."
        cases = {
            "0.5 / 5": ("5", "(0.5) Add water.", None),
            "0.5 / 5, before the excerpt": ("5", "0.5) Add water.", add),
            "1:1000 / 1:100": ("100", "(1:100) Add water.", None),
            "1:1000 / 1:100, longer number": ("100", "(1000) Add water.", None),
            "1:1000 / 1:100, before the excerpt": ("100", "1:100) Add water.", add),
            "30 / 3": ("3", "(30) Add water.", None),
            "30 / 3, a longer number": ("3", "(13) Add water.", None),
            "30 / 3, before the excerpt": ("3", "13) Add water.", add),
            "mL / µL": ("1", "(1 mL) Add water.", None),
            "µL / mL": ("1", "(1 µL) Add water.", None),
        }
        for name, (label, line, excerpt) in cases.items():
            with self.subTest(name):
                self.assert_refused(
                    numbered_page(line),
                    "source_label_not_found",
                    steps=[step(label, line if excerpt is None else excerpt)],
                )

    def test_the_label_never_loosens_the_instruction(self):
        for page, claim in (
            ("(1) Do not add water.", "(1) Add water."),
            ("(1) 시료를 가열하지 않는다.", "(1) 시료를 가열한다."),
        ):
            with self.subTest(claim=claim):
                self.assert_refused(
                    numbered_page(page), "claim_not_found", steps=[step("1", claim, excerpt=page)]
                )

    def test_the_numbered_text_on_a_different_page_is_refused(self):
        source = extraction(f"{TITLE}\nAdd water.", "(1) Add water.")
        self.assert_refused(source, "source_label_not_found", steps=[step("1", "Add water.")])
        self.assert_refused(source, "quote_not_found", steps=[step("1", "(1) Add water.")])


def verify(text: str, excerpt: str, page: int = 1, *pages: str):
    source = extraction(text, *pages)
    return analysis_module._verified_evidence(
        domain.SourceEvidence(page, excerpt), source
    )


TWICE = "Do not allow sample \nto go to dryness.\nNote.\nDo not allow sample to go to \ndryness."


class EqualSpansTests(unittest.TestCase):
    """Rule first_equal_span: one excerpt printed more than once on its page."""

    def assert_refused(self, reason_code: str, text: str, excerpt: str, page: int = 1, *pages: str) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            verify(text, excerpt, page, *pages)
        self.assertEqual(raised.exception.diagnostic.reason_code, reason_code)

    def test_a_sentence_printed_twice_is_the_first_one(self):
        # epa_365_1: the same warning under two steps, broken at different places.
        verified = verify(TWICE, "Do not allow sample to go to dryness.")

        self.assertEqual(verified.source_excerpt, "Do not allow sample \nto go to dryness.")

    def test_a_title_printed_in_the_header_and_the_body_is_supported(self):
        # PMC7988192: the title in the running header and above the abstract.
        source = extraction(
            "SP3 Protocol for \nProteomic Plant Sample Preparation\n"
            "Abstract text.\nSP3 Protocol for Proteomic Plant \nSample Preparation\n"
            f"{TITLE}\n{STEP}"
        )
        draft = parse(
            source,
            metadata={
                "title": "SP3 Protocol for Proteomic Plant Sample Preparation",
                "evidence": evidence(1, "SP3 Protocol for Proteomic Plant Sample Preparation"),
            },
        )

        self.assertEqual(
            draft.protocol.metadata.evidence.source_excerpt,
            "SP3 Protocol for \nProteomic Plant Sample Preparation",
        )

    def test_matches_that_are_the_same_only_in_a_comparison_form_stay_ambiguous(self):
        # "5-\n10" and "5-10" are one range only once the line-end hyphen is
        # joined; read plainly they are different text.
        self.assert_refused(
            "ambiguous_source_match", "for 5-\n10 min.\nThen\nfor 5-10 min.", "for 5-10  min."
        )

    def test_the_human_decisions_refusals(self):
        cases = {
            "0.5 / 5": ("Add 0.5 mL\nbuffer.\nThen\nAdd 0.5\nmL buffer.", "Add 5 mL buffer."),
            "1:1000 / 1:100": ("Dilute 1:1000 in\nPBS.\nThen\nDilute 1:1000\nin PBS.", "Dilute 1:100 in PBS."),
            "1:100 / 1:1000": ("Dilute 1:100 in\nPBS.\nThen\nDilute 1:100\nin PBS.", "Dilute 1:1000 in PBS."),
            "30 / 3": ("Spin for 30\nmin.\nThen\nSpin for\n30 min.", "Spin for 3 min."),
            "mL / µL": ("Add 50 µL\nwater.\nThen\nAdd 50\nµL water.", "Add 50 mL water."),
            "하지 않는다 / 한다": ("시료를 가열하지\n않는다.\n그리고\n시료를\n가열하지 않는다.", "시료를 가열한다."),
            "do not / do": (TWICE, "Do allow sample to go to dryness."),
        }
        for name, (text, excerpt) in cases.items():
            with self.subTest(name):
                self.assert_refused("quote_not_found", text, excerpt)

    def test_the_sentence_printed_twice_on_a_different_page_is_refused(self):
        self.assert_refused(
            "quote_not_found", "Title page.", "Do not allow sample to go to dryness.", 1, TWICE
        )


if __name__ == "__main__":
    unittest.main()
