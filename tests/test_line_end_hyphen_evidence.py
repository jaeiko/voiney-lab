"""Line-end hyphen in evidence comparison (human decision 2026-10-03).

A line break after a hyphen is ignored when an excerpt or claim is compared
with its page, and only there: the hyphen stays, the page text, its hash and
the evidence identities are not changed, and every other strict comparison of
numbers and symbols is as before.
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

RANGE_PAGE = "3 Incubate the plate for 5-\n10 min at 37 °C.\n4 Add alpha-\namylase."


def extraction(text: str) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="synthetic.pdf",
        byte_size=1,
        sha256="b" * 64,
        media_type="application/pdf",
        page_count=1,
        encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=(ProtocolPdfPage(1, text, False),),
    )


def verify(text: str, excerpt: str) -> domain.SourceEvidence:
    return analysis_module._verified_evidence(
        domain.SourceEvidence(1, excerpt), extraction(text)
    )


class LineEndHyphenMatchesTests(unittest.TestCase):
    def test_numeric_range_split_at_the_hyphen_matches_as_one_range(self):
        verified = verify(RANGE_PAGE, "Incubate the plate for 5-10 min at 37 °C.")

        # Projected back to the exact source span: hyphen and line break kept.
        self.assertEqual(
            verified.source_excerpt, "Incubate the plate for 5-\n10 min at 37 °C."
        )

    def test_a_claim_quoting_the_range_occurs_on_the_page(self):
        self.assertTrue(analysis_module._claim_occurs_in_text("5-10 min", RANGE_PAGE))

    def test_a_word_hyphenated_at_the_line_end_matches(self):
        verified = verify(RANGE_PAGE, "Add alpha-amylase.")

        self.assertEqual(verified.source_excerpt, "Add alpha-\namylase.")

    def test_unicode_hyphens_are_joined_too(self):
        for hyphen in ("‐", "‑"):
            with self.subTest(hyphen=hyphen):
                verified = verify(f"Use 5{hyphen}\n10 mL.", f"Use 5{hyphen}10 mL.")
                self.assertEqual(verified.source_excerpt, f"Use 5{hyphen}\n10 mL.")

    def test_forms_accepted_before_the_rule_are_still_accepted(self):
        for excerpt in ("for 5-\n10 min", "for 5- 10 min", "for 5-  \n 10 min"):
            with self.subTest(excerpt=excerpt):
                self.assertEqual(verify(RANGE_PAGE, excerpt).source_excerpt, "for 5-\n10 min")

    def test_one_source_span_found_by_both_forms_is_not_ambiguous(self):
        spans = analysis_module._canonical_match_spans(RANGE_PAGE, "Add alpha")

        self.assertEqual(len(spans), 1)


class StrictComparisonUnchangedTests(unittest.TestCase):
    def assert_rejected(self, text: str, excerpt: str) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            verify(text, excerpt)
        self.assertEqual(raised.exception.diagnostic.reason_code, "quote_not_found")

    def test_the_hyphen_itself_is_never_dropped(self):
        self.assert_rejected(RANGE_PAGE, "for 510 min")
        self.assert_rejected(RANGE_PAGE, "Add alphaamylase.")
        self.assertFalse(analysis_module._claim_occurs_in_text("510 min", RANGE_PAGE))

    def test_a_different_number_is_still_rejected(self):
        self.assert_rejected(RANGE_PAGE, "for 5-11 min")
        self.assert_rejected(RANGE_PAGE, "for 5-10 mL")
        self.assertFalse(analysis_module._claim_occurs_in_text("5-11 min", RANGE_PAGE))

    def test_a_hyphen_followed_by_a_space_on_the_same_line_is_not_joined(self):
        self.assert_rejected("for 5- 10 min", "for 5-10 min")

    def test_an_en_dash_or_minus_at_the_line_end_is_not_a_hyphen(self):
        self.assert_rejected("for 5–\n10 min", "for 5–10 min")
        self.assert_rejected("store at −\n20 °C", "store at −20 °C")

    def test_a_line_break_without_a_hyphen_is_not_joined(self):
        self.assert_rejected("for 5\n10 min", "for 510 min")


class SourceUntouchedTests(unittest.TestCase):
    def test_default_normalization_is_the_previous_one(self):
        normalized, _, _ = analysis_module._normalized_text_with_bounds("5-\n10")

        self.assertEqual(normalized, "5- 10")

    def test_joined_form_keeps_source_bounds(self):
        normalized, starts, ends = analysis_module._normalized_text_with_bounds(
            "5-\n10", join_line_end_hyphens=True
        )

        self.assertEqual(normalized, "5-10")
        self.assertEqual(starts, [0, 1, 3, 4])
        self.assertEqual(ends, [1, 2, 4, 5])

    def test_page_text_and_identity_do_not_change(self):
        source = extraction(RANGE_PAGE)

        analysis_module._verified_evidence(
            domain.SourceEvidence(1, "for 5-10 min"), source
        )

        self.assertEqual(source.pages[0].text, RANGE_PAGE)
        self.assertEqual(source.sha256, "b" * 64)


if __name__ == "__main__":
    unittest.main()
