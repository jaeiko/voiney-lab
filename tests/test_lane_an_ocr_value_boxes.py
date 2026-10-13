"""A value read by OCR keeps its place on the page image (lane AN, FM).

Human decision 2026-10-13: a number read from handwriting or a photo is kept
with the piece of the original image it was read from, so a person can check
the one against the other (the review tab that shows it is lane RV's). The
OCR result of each page now carries its values -- a number with the unit
printed after it -- with their boxes in the pixels of the page rendered at
``render_dpi``. OCR results stored before carry neither and read as before.
Every engine here is fake.
"""

from __future__ import annotations

import math
import unittest

from tests.test_protocol_ocr_providers import PNG, _FakeEngine, _ScannedPdf
from voiney_lab.protocol_ocr import (
    OcrPage,
    OcrResult,
    OcrValueBox,
    ProtocolOcrResultError,
    ocr_result_payload,
    validate_ocr_result,
)
from voiney_lab.protocol_ocr_providers import GOOGLE, OcrWord, RecognizedPage, value_boxes_of


def word(text: str, x: float, after: str = " ", y: float = 100) -> OcrWord:
    return OcrWord(text, x, y, x + 10 * len(text), y + 30, after)


class _WordEngine(_FakeEngine):
    def __init__(self, name: str, words: tuple[OcrWord, ...]) -> None:
        super().__init__(name, "".join(item.text + item.after for item in words).strip())
        self.words = words

    def recognize_page(self, png: bytes, *, page_number: int) -> RecognizedPage:
        page = super().recognize_page(png, page_number=page_number)
        return RecognizedPage(text=page.text, confidence=page.confidence, words=self.words)


class ValueBoxTests(unittest.TestCase):
    def test_a_number_takes_in_the_unit_printed_after_it(self) -> None:
        boxes = value_boxes_of((
            word("시료", 0), word("0.5", 60), word("mL", 100), word("를", 130),
            word("37", 160, ""), word("°C", 180), word("에서", 210, "\n"),
        ))
        self.assertEqual([box.text for box in boxes], ["0.5 mL", "37°C"])
        self.assertEqual((boxes[0].x0, boxes[0].x1), (60, 120))

    def test_pieces_the_engine_runs_together_make_one_value(self) -> None:
        (box,) = value_boxes_of((word("12", 0, ""), word(",", 20, ""), word("000", 30), word("×g", 70, "\n")))
        self.assertEqual(box.text, "12,000 ×g")

    def test_a_word_that_only_holds_a_digit_is_not_a_value(self) -> None:
        boxes = value_boxes_of((word("H2SO4", 0), word("SOP_013", 80), word("-20", 160), word("°C", 200)))
        self.assertEqual([box.text for box in boxes], ["-20 °C"])

    def test_a_value_stops_at_the_line_end_and_at_a_word(self) -> None:
        boxes = value_boxes_of((word("5", 0, "\n"), word("mL", 0), word("2", 40), word("tubes", 60)))
        self.assertEqual([box.text for box in boxes], ["5", "2"])


class ProviderTests(_ScannedPdf):
    def test_the_chosen_engine_s_values_are_kept_with_the_render_resolution(self) -> None:
        engine = _WordEngine(GOOGLE, (word("Add", 0), word("5", 50), word("mL", 70), word("buffer", 110, "\n")))
        result = self._recognize(engine)
        layer, scanned = result.pages
        self.assertEqual((layer.value_boxes, layer.render_dpi), ((), None))
        self.assertEqual([box.text for box in scanned.value_boxes], ["5 mL"])
        self.assertEqual(scanned.render_dpi, self.rendered[0][1])
        payload = ocr_result_payload(result)["pages"][1]
        self.assertEqual(payload["value_boxes"], [{"text": "5 mL", "box": [50.0, 100.0, 90.0, 130.0]}])
        self.assertEqual(payload["render_dpi"], 300)

    def test_an_engine_without_word_boxes_keeps_none(self) -> None:
        result = self._recognize(_FakeEngine(GOOGLE, "Add 5 mL buffer"))
        self.assertEqual((result.pages[1].value_boxes, result.pages[1].render_dpi), ((), None))


class ValidationTests(unittest.TestCase):
    def result(self, **page) -> OcrResult:
        return OcrResult(
            source_sha256="a" * 64, provider="google", provider_version="google-v1",
            pages=(OcrPage(1, "Add 5 mL", 0.9, **page),),
        )

    def test_a_box_that_is_not_a_box_is_refused(self) -> None:
        for boxes, dpi in (
            ((OcrValueBox("5", 10, 10, 5, 20),), 300),
            ((OcrValueBox("5", -1, 10, 5, 20),), 300),
            ((OcrValueBox("5", 0, 0, math.inf, 20),), 300),
            ((OcrValueBox("", 0, 0, 5, 20),), 300),
            ((OcrValueBox("5", 0, 0, 5, 20),), 5000),
        ):
            with self.subTest(boxes=boxes, dpi=dpi), self.assertRaises(ProtocolOcrResultError):
                validate_ocr_result(self.result(value_boxes=boxes, render_dpi=dpi),
                                    expected_sha256="a" * 64, expected_page_count=1)

    def test_a_result_without_boxes_reads_as_before(self) -> None:
        validated = validate_ocr_result(self.result(), expected_sha256="a" * 64, expected_page_count=1)
        page = ocr_result_payload(validated)["pages"][0]
        self.assertEqual((page["value_boxes"], page["render_dpi"]), ([], None))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
