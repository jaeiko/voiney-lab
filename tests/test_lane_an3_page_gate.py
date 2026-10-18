"""Lane AN3, round 2, decision ②: a page read poorly settles nothing by itself.

Live measurement (lane AN3, stage ②) found four values both engines read alike
and wrongly, all on one low-quality scan: a decimal point both engines lost
("8.5" -> "85") and "μm" both read as "pm". Human decision 2026-10-18: on a page
where at least 15% of the chosen engine's numbers are read differently by the
other engine or have no partner in place, or where the lower of the two
engines' page confidences is below 0.90, no value is settled by the two
engines' agreement; the page's values used in execution go to its page card.
Every OCR engine and model here is fake; nothing is "live-tested".
"""

from __future__ import annotations

import unittest

from tests.test_lane_an3_ocr_two_engines import SCAN, _ScanCase, _WordsEngine, provider
from tests.test_protocol_ocr_providers import PNG
from voiney_lab.protocol_ocr import ocr_result_payload, validate_ocr_result
from voiney_lab.protocol_ocr_providers import (
    CLOVA,
    GOOGLE,
    DualEngineOcrProvider,
    RecognizedPage,
)

# SCAN holds seven numbers: 25 mM, 2, 4000 rpm, 5 min, 12, 3 and 4.
TWO_OFF = [line.replace("25 mM", "26 mM").replace("reference 12", "reference 13") for line in SCAN]
ONE_OFF = [line.replace("reference 12", "reference 13") for line in SCAN]


class _Confident(_WordsEngine):
    """A fake engine that reads ``lines`` with its own page confidence."""

    def __init__(self, name: str, lines: list[str], confidence: float) -> None:
        super().__init__(name, lines)
        self.confidence = confidence

    def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage:
        page = super().recognize_page(png, page_number=page_number)
        return RecognizedPage(page.text, self.confidence, page.words)


def confident(google: float, clova: float) -> DualEngineOcrProvider:
    return DualEngineOcrProvider(
        (_Confident(CLOVA, SCAN, clova), _Confident(GOOGLE, SCAN, google)),
        render=lambda *a, **k: PNG)


class WidelyDifferingPageTests(_ScanCase):
    def test_15_percent_of_the_numbers_read_differently_asks_even_the_values_read_alike(self) -> None:
        entry = self.analyze(provider(SCAN, TWO_OFF))  # 2 of 7 numbers: 29%
        self.assertFalse(entry.available_for_execution)
        (card,) = self.cards()
        self.assertEqual(card["card_id"], "ocr_page:2")
        self.assertEqual([value["item_id"] for value in card["values"]],
                         ["ocr_value:2:4000rpm", "ocr_value:2:5min", "ocr_value:2:4"])
        self.assertEqual({value["reason_code"] for value in card["values"]}, {"ocr_page_differs_widely"})
        self.assertEqual(card["values"][0]["reason_ko"],
                         "두 OCR 엔진이 이 쪽 숫자의 15% 이상을 다르게 읽어, 같게 읽은 값도 확인해요")
        self.assertEqual([choice["label_ko"] for choice in card["values"][0]["choices"]],
                         ["4000 rpm", "직접 입력"])
        (pending,) = self.events("protocol_analysis_review_pending")
        self.assertNotIn("auto_resolved", pending)

    def test_under_15_percent_still_settles_the_values_read_alike(self) -> None:
        entry = self.analyze(provider(SCAN, ONE_OFF))  # 1 of 7 numbers: 14%
        self.assertTrue(entry.available_for_execution)

    def test_a_number_the_other_engine_read_nowhere_counts_against_the_page(self) -> None:
        missing = [line.replace("Buffer B 25 mM", "Buffer B").replace("reference 12", "reference")
                   for line in SCAN]
        self.analyze(provider(SCAN, missing))
        (card,) = self.cards()
        self.assertEqual({value["reason_code"] for value in card["values"]}, {"ocr_page_differs_widely"})


class LowConfidencePageTests(_ScanCase):
    def test_either_engine_below_090_asks_the_page_s_values(self) -> None:
        for google, clova in ((0.95, 0.85), (0.89, 0.97)):
            with self.subTest(google=google, clova=clova):
                self.tearDown()
                self.setUp()
                entry = self.analyze(confident(google, clova))
                self.assertFalse(entry.available_for_execution)
                (card,) = self.cards()
                self.assertEqual({value["reason_code"] for value in card["values"]},
                                 {"ocr_page_low_confidence"})
                self.assertEqual(card["values"][0]["reason_ko"],
                                 "OCR 엔진의 이 쪽 신뢰도가 0.90 아래라, 같게 읽은 값도 확인해요")

    def test_both_engines_at_090_or_above_settle_the_page(self) -> None:
        self.assertTrue(self.analyze(confident(0.90, 0.93)).available_for_execution)

    def test_the_other_engine_s_page_confidence_is_kept_with_the_page(self) -> None:
        extraction_pdf = self.pdf
        from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
        extraction = extract_protocol_pdf(extraction_pdf)
        result = confident(0.95, 0.85).recognize(
            extraction_pdf, source_sha256=extraction.sha256, page_count=extraction.page_count)
        page = result.pages[1]
        self.assertEqual((page.confidence, page.other_confidence), (0.95, 0.85))
        payload = ocr_result_payload(validate_ocr_result(
            result, expected_sha256=extraction.sha256, expected_page_count=2))
        self.assertEqual(payload["pages"][1]["other_confidence"], 0.85)


if __name__ == "__main__":
    unittest.main()
