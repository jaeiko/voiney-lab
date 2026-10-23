"""Text extraction reads image geometry without encoding every image as PNG."""
from unittest.mock import patch

import pymupdf

from voiney_lab import pdf_text_engine as engine


def test_worker_text_read_does_not_materialize_image_bytes(tmp_path):
    path = tmp_path / "picture.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=792)
    page.insert_text((72, 72), "1 Add buffer.")
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0)
    pixmap.set_rect(pixmap.irect, (200, 30, 30))
    page.insert_image(pymupdf.Rect(100, 300, 300, 450), stream=pixmap.tobytes("png"))
    document.save(path)
    document.close()
    original = pymupdf.Page.get_text
    flags = []

    def text(self, option="text", *args, **kwargs):
        if option == "rawdict":
            flags.append(kwargs["flags"])
        return original(self, option, *args, **kwargs)

    with patch.object(pymupdf.Page, "get_text", text):
        result = engine.read_document_in_process(path)
    assert flags
    assert all(not value & pymupdf.TEXT_PRESERVE_IMAGES for value in flags)
    assert result.pages[0].text == "1 Add buffer."
    assert result.pages[0].images == (engine.PdfImageBox(100.0, 300.0, 300.0, 450.0),)
