"""Lane AN2, decision 4: a Hangul line break is joined in comparison on a page
whose text layer is Korean written without spaces between words.

Lane AN's rule (e) ignored only the spaces between two Hangul letters on one
line; a line break between them stayed strict on a text layer, because lanes
P3 and PX joined it on OCR pages only. Twelve NIFS and two MFDS documents print
Korean with no word spaces at all and break their lines in the middle of a
word, so a quote that was right failed (nifs_waste_vibrio_tox,
nifs_waste_pcbs). The person's decision of 2026-10-14: widen it, but only on
a page judged "Korean text layer without word spaces".

The judgement is deterministic and per page (``unspaced_korean_text_layer``):
at least ``UNSPACED_KOREAN_MIN_HANGUL`` Hangul letters, Hangul at least
``UNSPACED_KOREAN_MIN_HANGUL_SHARE`` of the page's letters (Hangul plus ASCII),
and at most ``UNSPACED_KOREAN_MAX_SPACES_PER_HANGUL`` spaces between two
Hangul letters on a line per Hangul letter. The numbers come from the 200-file
corpus (lane AN2 report). Digits stay strict everywhere.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from tests.test_lane_ev4_evidence import TITLE, extraction, parse, step
from voiney_lab.experiment_protocol_analysis import (
    UNSPACED_KOREAN_MAX_SPACES_PER_HANGUL,
    UNSPACED_KOREAN_MIN_HANGUL,
    UNSPACED_KOREAN_MIN_HANGUL_SHARE,
    ProtocolAnalysisEvidenceError,
    unspaced_korean_text_layer,
)

# Korean with no word spaces, lines broken inside a word (the shape of the NIFS PDFs).
UNSPACED = (
    "1. 시료를정확히취하여삼각플라스크에옮기고증류\n"
    "수를가하여희석한다.\n"
    "2. 용액을충분히혼합한뒤실온에서삼십분간방치한다."
)
SPACED = (
    "1. 시료를 정확히 취하여 삼각 플라스크에 옮기고 증류\n"
    "수를 가하여 희석한다.\n"
    "2. 용액을 충분히 혼합한 뒤 실온에서 삼십 분간 방치한다."
)


def hangul_count(text: str) -> int:
    return sum(1 for c in text if "가" <= c <= "힣")


class TheJudgementTests(unittest.TestCase):
    def test_the_fixtures_are_what_they_claim(self) -> None:
        self.assertGreaterEqual(hangul_count(UNSPACED), UNSPACED_KOREAN_MIN_HANGUL)
        self.assertGreaterEqual(hangul_count(SPACED), UNSPACED_KOREAN_MIN_HANGUL)

    def test_korean_without_word_spaces_is_judged_unspaced(self) -> None:
        self.assertTrue(unspaced_korean_text_layer(UNSPACED))
        self.assertTrue(unspaced_korean_text_layer(f"{TITLE}\n{UNSPACED}"))

    def test_korean_with_word_spaces_is_not(self) -> None:
        self.assertFalse(unspaced_korean_text_layer(SPACED))
        self.assertFalse(unspaced_korean_text_layer(f"{TITLE}\n{SPACED}"))

    def test_too_little_korean_is_never_judged(self) -> None:
        short = "개봉한날짜를용기에기록한다."
        self.assertLess(hangul_count(short), UNSPACED_KOREAN_MIN_HANGUL)
        self.assertFalse(unspaced_korean_text_layer(short))
        edge = "가" * UNSPACED_KOREAN_MIN_HANGUL
        self.assertTrue(unspaced_korean_text_layer(edge))
        self.assertFalse(unspaced_korean_text_layer(edge[:-1]))

    def test_a_few_korean_words_among_latin_text_are_not_a_korean_layer(self) -> None:
        # A reference list: Korean titles between English ones, each Korean word
        # alone on its line, so no spaces between Hangul at all -- yet not Korean text.
        english = "ISO (2022). Microbiology of the food chain - Horizontal method for enumeration.\n"
        page = english * 6 + "식품의약품안전처 (2024). 식품공전.\n" * 4 + "식품의약품안전처 (2024). 건강기능식품공전.\n" * 2
        self.assertGreaterEqual(hangul_count(page), UNSPACED_KOREAN_MIN_HANGUL)
        self.assertFalse(unspaced_korean_text_layer(page))

    def test_korean_with_digits_units_and_an_english_title_is_still_a_korean_layer(self) -> None:
        page = f"{TITLE}\n" + "1. 시료10mL를취하여0.45μm여과지로거른다.\n" * 3 + UNSPACED
        self.assertTrue(unspaced_korean_text_layer(page))

    def test_the_spaces_per_hangul_bound_is_inclusive(self) -> None:
        def page(spaces: int, letters: int = 100) -> str:
            size, extra = divmod(letters, spaces + 1)
            return " ".join("가" * (size + (1 if n < extra else 0)) for n in range(spaces + 1))

        at_bound = page(5)  # 5 spaces among 100 letters: exactly 0.05
        self.assertEqual(at_bound.count(" ") / hangul_count(at_bound), UNSPACED_KOREAN_MAX_SPACES_PER_HANGUL)
        self.assertTrue(unspaced_korean_text_layer(at_bound))
        beyond = page(6)
        self.assertGreater(beyond.count(" ") / hangul_count(beyond), UNSPACED_KOREAN_MAX_SPACES_PER_HANGUL)
        self.assertFalse(unspaced_korean_text_layer(beyond))

    def test_lines_broken_between_hangul_letters_add_no_word_spaces(self) -> None:
        # Breaking lines is what an unspaced layer does; only a space on one line counts.
        self.assertTrue(unspaced_korean_text_layer("가나다라마바사아자차\n" * 6))

    def test_english_empty_and_digits_are_not_judged(self) -> None:
        for text in ("", "Add 10 mL of buffer.", "1234 5678\n" * 20):
            with self.subTest(text=text[:20]):
                self.assertFalse(unspaced_korean_text_layer(text))

    def test_the_numbers_are_the_documented_ones(self) -> None:
        self.assertEqual(UNSPACED_KOREAN_MIN_HANGUL, 30)
        self.assertEqual(UNSPACED_KOREAN_MIN_HANGUL_SHARE, 0.3)
        self.assertEqual(UNSPACED_KOREAN_MAX_SPACES_PER_HANGUL, 0.05)


QUOTE = "1. 시료를정확히취하여삼각플라스크에옮기고증류수를가하여희석한다."


class OnAnUnspacedPageTests(unittest.TestCase):
    def test_a_quote_written_across_a_hangul_line_break_is_found(self) -> None:
        source = extraction(f"{TITLE}\n{UNSPACED}")
        draft = parse(source, step("1", QUOTE[3:], QUOTE))
        evidence = draft.protocol.sections[0].steps[0].evidence
        # Projected back onto the page's own characters, line break included.
        self.assertEqual(evidence.source_excerpt, "1. 시료를정확히취하여삼각플라스크에옮기고증류\n수를가하여희석한다.")

    def test_the_quote_may_carry_spaces_the_page_does_not_print(self) -> None:
        source = extraction(f"{TITLE}\n{UNSPACED}")
        parse(source, step("1", "시료를 정확히 취하여 삼각플라스크에 옮기고 증류수를 가하여 희석한다.",
                           "1. 시료를 정확히 취하여 삼각플라스크에 옮기고 증류수를 가하여 희석한다."))

    def test_the_page_text_and_its_hash_are_untouched(self) -> None:
        source = extraction(f"{TITLE}\n{UNSPACED}")
        before = source.pages[0].text
        parse(source, step("1", QUOTE[3:], QUOTE))
        self.assertEqual(source.pages[0].text, before)
        self.assertEqual(source.sha256, "f" * 64)

    def test_an_invented_word_is_still_refused(self) -> None:
        source = extraction(f"{TITLE}\n{UNSPACED}")
        wrong = QUOTE.replace("증류수", "정제수")
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            parse(source, step("1", wrong[3:], wrong))
        self.assertEqual(raised.exception.diagnostic.reason_code, "quote_not_found")

    def test_a_break_between_digits_is_still_strict(self) -> None:
        page = f"{TITLE}\n" + UNSPACED + "\n3. 시료1\n0mL를취하여삼각플라스크에옮기고증류수를가하여희석한다."
        source = extraction(page)
        quote = "3. 시료10mL를취하여삼각플라스크에옮기고증류수를가하여희석한다."
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(source, step("3", quote[3:], quote))

    def test_a_break_between_a_hangul_letter_and_a_digit_or_latin_letter_is_not_joined(self) -> None:
        for broken, quote in (
            ("3. 시료를취하여삼각플라스크에옮기고증류수를가하여희석한다 가\n10mL를넣는다.",
             "3. 시료를취하여삼각플라스크에옮기고증류수를가하여희석한다 가10mL를넣는다."),
            ("3. 시료를취하여삼각플라스크에옮기고증류수를가하여희석한다 가\nabc를넣는다.",
             "3. 시료를취하여삼각플라스크에옮기고증류수를가하여희석한다 가abc를넣는다."),
        ):
            with self.subTest(quote=quote[-12:]):
                source = extraction(f"{TITLE}\n{UNSPACED}\n{broken}")
                with self.assertRaises(ProtocolAnalysisEvidenceError):
                    parse(source, step("3", quote[3:], quote))


class OnOtherPagesTests(unittest.TestCase):
    def test_a_spaced_korean_page_still_refuses_a_hangul_line_break(self) -> None:
        source = extraction(f"{TITLE}\n{SPACED}")
        quote = "1. 시료를 정확히 취하여 삼각 플라스크에 옮기고 증류수를 가하여 희석한다."
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            parse(source, step("1", quote[3:], quote))
        self.assertEqual(raised.exception.diagnostic.reason_code, "quote_not_found")

    def test_the_short_page_of_lane_pxs_test_is_not_judged_and_is_still_refused(self) -> None:
        source = extraction(f"{TITLE}\n1. 개봉한 날\n짜를 용기에 기록한다.")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(source, step("1", "개봉한 날짜를 용기에 기록한다.", "1. 개봉한 날짜를 용기에 기록한다."))

    def test_the_judgement_is_per_page(self) -> None:
        # Page 1 is unspaced, page 2 is spaced: the same break is joined on 1 only.
        unspaced = f"{TITLE}\n{UNSPACED}"
        spaced = f"{SPACED}"
        source = extraction(unspaced, spaced)
        parse(source, step("1", QUOTE[3:], QUOTE, page=1))
        spaced_quote = "1. 시료를 정확히 취하여 삼각 플라스크에 옮기고 증류수를 가하여 희석한다."
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse(source, step("1", spaced_quote[3:], spaced_quote, page=2))

    def test_an_ocr_page_still_joins_whether_or_not_it_is_spaced(self) -> None:
        source = extraction(f"{TITLE}\n{SPACED}")
        ocr = replace(source.pages[0], ocr_derived=True)
        source = replace(source, pages=(ocr,))
        quote = "1. 시료를 정확히 취하여 삼각 플라스크에 옮기고 증류수를 가하여 희석한다."
        parse(source, step("1", quote[3:], quote))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
