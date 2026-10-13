"""A photo read through the PDF evidence path (lane AN, FM c).

Human decision 2026-10-13: a JPG, PNG or HEIC photo becomes a one-page PDF of
the photo, which OCR reads as it reads a scanned page. The PDF is the
registered source; the upload's own checksum is kept with the registration,
so the same file uploaded again finds the protocol it made. HEIC needs the
optional pillow-heif package (``.[heic]``). Every OCR engine here is fake;
Pillow is real.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

from tests.test_lane_px_auto_ocr import MixedProvider
from tests.test_protocol_catalog import _dedicated_to_thread, write_text_pdf
from voiney_lab import experiment_protocol_pdf as pdf_module
from voiney_lab import server as server_module
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import (
    IMAGE_PDF_DPI,
    ProtocolSourceConversionUnavailableError,
    ProtocolSourceFormatError,
    clear_protocol_pdf_cache,
    convert_source_to_pdf,
    extract_protocol_pdf,
    pdf_filename,
)
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.pdf_text_engine import OCR_RENDER_DPI, render_page_png_in_process
from voiney_lab.protocol_catalog import ProtocolCatalog


def photo_bytes(size=(600, 300), *, fmt="JPEG", mode="RGB", orientation=None) -> bytes:
    image = Image.new(mode, size, (255, 255, 255, 0) if mode == "RGBA" else "white")
    for x in range(50, 250):
        for y in range(100, 110):
            image.putpixel((x, y), (0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0))
    out = io.BytesIO()
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        image.save(out, fmt, exif=exif)
    else:
        image.save(out, fmt)
    return out.getvalue()


class ConversionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_a_photo_becomes_one_page_without_text_that_ocr_reads(self) -> None:
        source = self.write("bench.jpg", photo_bytes())
        conversion = convert_source_to_pdf(source, "image/jpeg", self.root / "bench.pdf")
        extraction = extract_protocol_pdf(self.root / "bench.pdf")
        self.assertEqual(extraction.page_count, 1)
        self.assertTrue(extraction.pages[0].ocr_required)
        payload = conversion.payload()
        self.assertEqual(payload["media_type"], "image/jpeg")
        self.assertEqual(payload["pixels"], [600, 300])
        self.assertEqual(payload["byte_size"], source.stat().st_size)
        self.assertTrue(payload["converter"].startswith("pillow-"))

    def test_ocr_reads_the_photo_at_its_own_pixel_size(self) -> None:
        self.assertEqual(IMAGE_PDF_DPI, OCR_RENDER_DPI)
        convert_source_to_pdf(self.write("a.png", photo_bytes(fmt="PNG")), "image/png", self.root / "a.pdf")
        rendered = Image.open(io.BytesIO(render_page_png_in_process(self.root / "a.pdf", 1)))
        self.assertEqual(rendered.size, (600, 300))

    def test_the_same_photo_gives_the_same_pdf(self) -> None:
        source = self.write("a.jpg", photo_bytes())
        convert_source_to_pdf(source, "image/jpeg", self.root / "1.pdf")
        convert_source_to_pdf(source, "image/jpeg", self.root / "2.pdf")
        self.assertEqual((self.root / "1.pdf").read_bytes(), (self.root / "2.pdf").read_bytes())

    def test_a_photo_is_turned_upright_by_its_exif_orientation(self) -> None:
        source = self.write("turned.jpg", photo_bytes((600, 300), orientation=6))
        conversion = convert_source_to_pdf(source, "image/jpeg", self.root / "turned.pdf")
        rendered = Image.open(io.BytesIO(render_page_png_in_process(self.root / "turned.pdf", 1)))
        self.assertEqual(rendered.size, (300, 600))
        self.assertTrue(conversion.payload()["exif_orientation_applied"])

    def test_transparency_is_laid_on_white(self) -> None:
        convert_source_to_pdf(self.write("t.png", photo_bytes(fmt="PNG", mode="RGBA")), "image/png",
                              self.root / "t.pdf")
        rendered = Image.open(io.BytesIO(render_page_png_in_process(self.root / "t.pdf", 1))).convert("RGB")
        self.assertGreater(min(rendered.getpixel((5, 5))), 240)

    def test_a_file_that_is_not_the_photo_its_type_names_is_refused(self) -> None:
        for name, data, media_type in (
            ("a.jpg", photo_bytes(fmt="PNG"), "image/jpeg"),
            ("b.png", b"not an image", "image/png"),
        ):
            with self.subTest(name=name), self.assertRaises(ProtocolSourceFormatError):
                convert_source_to_pdf(self.write(name, data), media_type, self.root / "x.pdf")

    def test_a_photo_with_too_many_pixels_is_refused(self) -> None:
        with patch.object(pdf_module, "MAX_SOURCE_IMAGE_PIXELS", 1000), \
                self.assertRaises(pdf_module.ProtocolSourceTooLargeError):
            convert_source_to_pdf(self.write("big.jpg", photo_bytes()), "image/jpeg", self.root / "x.pdf")

    def test_heic_without_pillow_heif_is_refused_by_name(self) -> None:
        with patch.dict(sys.modules, {"pillow_heif": None}), \
                self.assertRaises(ProtocolSourceConversionUnavailableError) as raised:
            convert_source_to_pdf(self.write("a.heic", b"...."), "image/heic", self.root / "x.pdf")
        self.assertEqual(raised.exception.code, "heic_support_unavailable")

    @unittest.skipUnless(
        __import__("importlib").util.find_spec("pillow_heif"), "pillow-heif (.[heic]) not installed")
    def test_a_heic_photo_is_read_with_pillow_heif(self) -> None:
        import pillow_heif

        pillow_heif.register_heif_opener()
        out = io.BytesIO()
        Image.open(io.BytesIO(photo_bytes())).save(out, "HEIF")
        conversion = convert_source_to_pdf(self.write("a.heic", out.getvalue()), "image/heic",
                                           self.root / "a.pdf")
        self.assertEqual(conversion.payload()["pixels"], [600, 300])
        self.assertTrue(extract_protocol_pdf(self.root / "a.pdf").pages[0].ocr_required)

    def test_the_pdf_name_of_a_converted_upload(self) -> None:
        self.assertEqual(pdf_filename("bench notes.jpg"), "bench notes.pdf")
        self.assertEqual(pdf_filename("bench.v2.png"), "bench.v2.pdf")
        self.assertEqual(pdf_filename("noext"), "noext.pdf")


class ServerUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "catalog"
        clear_protocol_pdf_cache()
        self.started: list[str] = []
        self.env = patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "false",
            "VOINEY_LAB_USAGE_SCOPE": "demo",
        })
        self.env.start()
        self.provider = MixedProvider()
        self.patches = (
            patch.object(server_module, "_open_protocol_catalog", self._open_catalog),
            patch.object(server_module, "_protocol_ocr_provider", lambda: self.provider),
            patch.object(server_module, "_begin_background_analysis", self._begin_analysis),
            patch("fastapi.routing.run_in_threadpool", side_effect=_dedicated_to_thread),
            patch.object(server_module.asyncio, "to_thread", side_effect=_dedicated_to_thread),
        )
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.env.stop()
        server_module._PROTOCOL_OCR_TASKS.clear()
        server_module._PROTOCOL_ANALYSIS_TASKS.clear()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def _open_catalog(self):
        store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.data_dir))
        return ProtocolCatalog(store), store

    async def _begin_analysis(self, protocol_id, *, principal, analysis_id=None):
        self.started.append(protocol_id)
        return {"analysis_request_accepted": True}

    async def upload(self, client, data: bytes, name: str, media_type: str):
        return await client.post(f"/api/protocols?filename={name}", content=data,
                                 headers={"Content-Type": media_type})

    def registered_event(self, protocol_id: str) -> dict:
        catalog, store = self._open_catalog()
        try:
            return next(event.payload for event in store.list_events(protocol_id)
                        if event.event_type == "protocol_registered")
        finally:
            store.close()

    async def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=server_module.app),
                                 base_url="http://testserver")

    async def test_a_photo_upload_is_ocr_d_and_its_analysis_started(self) -> None:
        async with await self.client() as client:
            response = await self.upload(client, photo_bytes(), "bench.jpg", "image/jpeg")
            self.assertEqual(response.status_code, 201, response.text)
            payload = response.json()
            protocol_id = payload["protocol"]["protocol_id"]
            self.assertEqual(payload["protocol"]["analysis_status"], "ocr_required")
            self.assertTrue(payload["ocr"]["automatic"])
            task = server_module._PROTOCOL_OCR_TASKS.get(protocol_id)
            if task is not None:
                await task
            status = (await client.get(f"/api/protocols/{protocol_id}/ocr")).json()
            self.assertEqual(status["state"], "accepted_for_analysis")
            self.assertEqual(self.started, [protocol_id])
        event = self.registered_event(protocol_id)
        self.assertEqual(event["source_filename"], "bench.pdf")
        origin = event["converted_from"]
        self.assertEqual((origin["media_type"], origin["filename"]), ("image/jpeg", "bench.jpg"))
        self.assertEqual(len(origin["sha256"]), 64)

    async def test_the_same_photo_again_finds_its_protocol(self) -> None:
        async with await self.client() as client:
            first = await self.upload(client, photo_bytes(), "a.jpg", "image/jpeg")
            again = await self.upload(client, photo_bytes(), "a-again.jpg", "image/jpeg")
        self.assertTrue(again.json()["deduplicated"])
        self.assertEqual(again.json()["protocol"]["protocol_id"], first.json()["protocol"]["protocol_id"])

    async def test_refusals_name_their_reason(self) -> None:
        cases = (
            (b"%PDF-1.7", "notes.txt", "text/plain", 415, "unsupported_pdf_media_type"),
            (photo_bytes(fmt="PNG"), "a.jpg", "image/jpeg", 422, "invalid_source_file"),
            (photo_bytes(), "../a.jpg", "image/jpeg", 400, "protocol_registration_invalid"),
        )
        async with await self.client() as client:
            for data, name, media_type, status, detail in cases:
                with self.subTest(name=name, media_type=media_type):
                    response = await self.upload(client, data, name, media_type)
                    self.assertEqual((response.status_code, response.json()["detail"]), (status, detail))
            with patch.dict(sys.modules, {"pillow_heif": None}):
                heic = await self.upload(client, b"....", "a.heic", "image/heic")
            self.assertEqual((heic.status_code, heic.json()["detail"]), (503, "heic_support_unavailable"))
        # Nothing was registered from a refused upload.
        catalog, store = self._open_catalog()
        try:
            self.assertEqual(store.list_experiments(), ())
        finally:
            store.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
