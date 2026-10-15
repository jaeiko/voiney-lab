"""The piece of a photo an OCR value was read from, beside the value (lane RV, decision e).

Lane AN (2026-10-13) kept, for a page read by OCR, each value with its box in
the pixels of the page rendered at ``render_dpi``, for the review tab to show
the value beside the piece of the original image. The review's source page
names those boxes and the lines an unclear reading holds; one route renders
the piece. Every OCR engine here is fake.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from tests.test_protocol_ocr_providers import _write_raw_pages
from voiney_lab import server
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog, ProtocolCatalogNotFoundError
from voiney_lab.protocol_ocr import OcrPage, OcrResult, OcrValueBox
from voiney_lab.protocol_ocr_providers import TEXT_LAYER

OCR_TEXT = "2 Incubate the tube at 37 °C\nfor 10 min."


class _FakeOcr:
    def __init__(self, first_page_text: str) -> None:
        self.first_page_text = first_page_text

    def recognize(self, source_pdf, *, source_sha256: str, page_count: int) -> OcrResult:
        return OcrResult(
            source_sha256=source_sha256, provider="fake-ocr", provider_version="1",
            pages=(
                OcrPage(1, self.first_page_text, provider=TEXT_LAYER),
                OcrPage(2, OCR_TEXT, confidence=0.91, provider="google_vision",
                        numeric_review_required=True, render_dpi=144,
                        value_boxes=(OcrValueBox("37 °C", 300, 120, 420, 160),
                                     OcrValueBox("10 min", 60, 180, 190, 220))),
            ),
        )


class ValueBoxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = root / "scan.pdf"
        _write_raw_pages(
            self.pdf, ("BT /F1 12 Tf 72 720 Td (1 Add 5 ml of buffer and mix.) Tj ET", None))
        text = extract_protocol_pdf(self.pdf).pages[0].text
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="scan.pdf", media_type="application/pdf",
        ).entry.protocol_id
        self.catalog.run_ocr(self.protocol_id, _FakeOcr(text), ocr_id="ocr-" + "a" * 32)

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def test_the_review_page_names_the_boxes_and_the_unclear_value_lines(self) -> None:
        page = self.catalog.review_source_page(self.protocol_id, 2)
        self.assertTrue(page["ocr"])
        self.assertTrue(page["ocr_unclear"])
        self.assertEqual(page["render_dpi"], 144)
        self.assertEqual(
            [line["text"] for line in page["lines"]], ["2 Incubate the tube at 37 °C", "for 10 min."])
        self.assertEqual([line["unclear"] for line in page["lines"]], [True, True])
        self.assertEqual(page["unclear_line_count"], 2)
        self.assertEqual(page["value_boxes"], [
            {"index": 0, "text": "37 °C", "box": [300.0, 120.0, 420.0, 160.0]},
            {"index": 1, "text": "10 min", "box": [60.0, 180.0, 190.0, 220.0]},
        ])
        text_page = self.catalog.review_source_page(self.protocol_id, 1)
        self.assertFalse(text_page["ocr"])
        self.assertEqual(text_page["value_boxes"], [])

    def test_the_piece_of_the_page_is_rendered_from_the_stored_source(self) -> None:
        png = self.catalog.review_value_box_image(self.protocol_id, 2, 0)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.review_value_box_image(self.protocol_id, 2, 5)
        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.review_value_box_image(self.protocol_id, 1, 0)

    def test_the_route_answers_an_image_or_not_found(self) -> None:
        self.store.close()

        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog):
                image = server.get_protocol_review_value_box_image(self.protocol_id, 2, 1)
                with self.assertRaises(HTTPException) as missing:
                    server.get_protocol_review_value_box_image(self.protocol_id, 2, 9)
        finally:
            self.store = initialize_protocol_store(self.settings)
        self.assertEqual(image.media_type, "image/png")
        self.assertEqual(image.headers["cache-control"], "private, no-store")
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
