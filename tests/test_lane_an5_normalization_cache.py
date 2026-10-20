"""A page's text is put in each comparison form once per analysis (lane AN5, decision 4).

Human decision 2026-10-20 (the recommended option): lane AN4 found that an
answer with many refused quotes spent its evidence check putting the same
page text in the same comparison form again for every quote -- one stored
DS-2 answer, 59 refusals, 61.1 s of CPU, nearly all of it there. The rules
stay as they are; a page-sized text already put in a form is kept and given
back, and what any check sees is the same as before, character for character.
"""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from voiney_lab import experiment_protocol_analysis as analysis
from voiney_lab.experiment_protocol_analysis import (
    _comparison_forms,
    _furniture_line,
    _normalized_text_with_bounds,
    _verified_evidence,
)
from voiney_lab.experiment_protocol import SourceEvidence
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)

SENTENCES = [
    f"{number}. Add {number} mL of buffer to tube {number} and mix gently by pipetting up and down."
    for number in range(1, 31)
]
#: Page text long enough to be kept: line breaks, a hyphen at a line end, a
#: running footer printed between two lines, a soft hyphen.
PAGE_TEXT = (
    "Journal of Methods 2020, 1, 19 1 of 3\n"
    + "\n".join(SENTENCES[:15])
    + "\nIncubate at 37 °C for 5-\n10 min.\nJournal of Methods 2020, 1, 19 1 of 3\n"
    + "\n".join(SENTENCES[15:])
    + "\nformu­lated buffer\n"
)


def _extraction(*texts: str) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="methods.pdf", byte_size=1, sha256="a" * 64,
        media_type="application/pdf", page_count=len(texts), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(
            ProtocolPdfPage(source_page_number=number, text=text, text_empty=False)
            for number, text in enumerate(texts, start=1)
        ),
    )


def _forms(extraction: ProtocolPdfExtraction):
    page = extraction.pages[0]
    return _comparison_forms(False, _furniture_line(page, extraction)) + _comparison_forms(True)


class NormalizationCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        analysis.clear_normalization_cache()
        self.addCleanup(analysis.clear_normalization_cache)

    def test_the_kept_form_is_what_the_form_gives_character_for_character(self) -> None:
        extraction = _extraction(PAGE_TEXT, PAGE_TEXT.replace("1 of 3", "2 of 3"))
        for form in _forms(extraction):
            with self.subTest(form=sorted(form)):
                expected = analysis._normalize_text_with_bounds(PAGE_TEXT, **form)
                first = _normalized_text_with_bounds(PAGE_TEXT, **form)
                again = _normalized_text_with_bounds(PAGE_TEXT, **form)
                self.assertEqual(first, expected)
                self.assertEqual(again, expected)

    def test_a_caller_that_changes_its_bounds_does_not_change_the_kept_ones(self) -> None:
        text, starts, ends = _normalized_text_with_bounds(PAGE_TEXT)
        starts.clear()
        ends.append(-1)
        again = _normalized_text_with_bounds(PAGE_TEXT)
        self.assertEqual(again, analysis._normalize_text_with_bounds(PAGE_TEXT))
        self.assertEqual(again[0], text)

    def test_many_quotes_on_one_page_put_the_page_in_each_form_once(self) -> None:
        # Refused quotes are what made it slow: each is looked for in every
        # form, and found in none, before it is refused.
        extraction = _extraction(PAGE_TEXT, "Second page.\n" * 60)
        quotes = [f"Stir {number} min at 99 °C with the lid open." for number in range(20)]
        real = analysis._normalize_text_with_bounds
        seen: list[int] = []

        def counted(value, **form):
            if value == PAGE_TEXT:
                seen.append(1)
            return real(value, **form)

        with mock.patch.object(analysis, "_normalize_text_with_bounds", side_effect=counted):
            for quote in quotes:
                with self.assertRaises(analysis.ProtocolAnalysisEvidenceError):
                    _verified_evidence(SourceEvidence(source_page_number=1, source_excerpt=quote),
                                       extraction)
        forms = len(_comparison_forms(False, _furniture_line(extraction.pages[0], extraction)))
        self.assertLessEqual(len(seen), forms)
        self.assertGreater(len(seen), 0)

    def test_the_same_page_reads_its_furniture_through_one_equal_key(self) -> None:
        extraction = _extraction(PAGE_TEXT, PAGE_TEXT, PAGE_TEXT)
        page = extraction.pages[1]
        first, second = _furniture_line(page, extraction), _furniture_line(page, extraction)
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertNotEqual(first, _furniture_line(extraction.pages[0], extraction))
        self.assertTrue(first("2 of 3"))
        self.assertFalse(first("1 of 2"))
        self.assertFalse(first("Add 5 mL of buffer."))

    def test_a_short_text_and_any_other_line_rule_are_not_kept(self) -> None:
        _normalized_text_with_bounds("Add 5 mL.")
        _normalized_text_with_bounds(PAGE_TEXT, skip_line=lambda line: False)
        self.assertEqual(analysis.normalization_cache_size(), (0, 0))

    def test_the_kept_texts_stay_within_their_bound(self) -> None:
        with mock.patch.object(analysis, "_NORMALIZED_CACHE_MAX_CHARACTERS", 5 * len(PAGE_TEXT)):
            for number in range(40):
                text = PAGE_TEXT.replace("Journal", f"Journal {number:02d}")
                self.assertEqual(_normalized_text_with_bounds(text),
                                 analysis._normalize_text_with_bounds(text))
            entries, characters = analysis.normalization_cache_size()
        self.assertLessEqual(characters, 5 * len(PAGE_TEXT))
        self.assertGreater(entries, 0)

    def test_threads_reading_the_same_pages_get_the_same_forms(self) -> None:
        extraction = _extraction(PAGE_TEXT, PAGE_TEXT.replace("1 of 3", "2 of 3"))
        forms = _forms(extraction)
        expected = [analysis._normalize_text_with_bounds(PAGE_TEXT, **form) for form in forms]
        results: list[list[object]] = []

        def read() -> None:
            results.append([_normalized_text_with_bounds(PAGE_TEXT, **form) for form in forms])

        threads = [threading.Thread(target=read) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 6)
        for result in results:
            self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
