"""A Google Drive link brings a protocol in the way an upload does (lane DR, decision 1).

Human decision 2026-10-20: ``POST /api/protocols/import-link {url}``, for a
signed-in person only. A Google Drive file link (PDF, Word, photo) is
downloaded; a Google Docs link is exported as a PDF; a Google Sheets link is
exported as .xlsx and attached as a reagent table -- only to a protocol named
in the request. What is downloaded then goes through the upload route
itself, so it meets the same checks and the same analysis path, and the
address it came from is kept as the source in the protocol's ledger.

Every HTTP answer here comes from a fake transport; no request leaves the
process. The OCR engine and LibreOffice are fakes too.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from openpyxl import Workbook

from tests.test_lane_an_source_formats import FakeOffice, docx_bytes, photo_bytes
from tests.test_lane_px_auto_ocr import MixedProvider
from tests.test_protocol_catalog import _dedicated_to_thread, write_text_pdf
from voiney_lab import experiment_protocol_pdf as pdf_module
from voiney_lab import protocol_link_import as link_module
from voiney_lab import server as server_module
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, docx_to_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
DOC_ID = "1DocIdForAFictionalProtocol_abcdefghijklmn"
SHEET_ID = "1SheetIdForAFictionalReagentTable_abcdefgh"
DRIVE_LINK = f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing"
DOC_LINK = f"https://docs.google.com/document/d/{DOC_ID}/edit?usp=sharing"
SHEET_LINK = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0"
#: Where a Docs export is handed on to, as Google answered on 2026-10-20.
DOC_EXPORT_HOST = "doc-0g-3o-docstext.googleusercontent.com"
SHEET_EXPORT_HOST = "doc-08-4o-sheets.googleusercontent.com"
#: A public address of Google's, as the fake resolver answers.
GOOGLE_ADDRESS = "142.250.196.110"


def pdf_bytes(text: str = "Fictional protocol. 1. Add 5 mL buffer.", *, title: str = "linked") -> bytes:
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "source.pdf"
        write_text_pdf(path, text, title=title)
        return path.read_bytes()


def xlsx_bytes() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Reagents"
    sheet["A1"], sheet["B1"] = "Reagent", "Volume"
    sheet["A2"], sheet["B2"] = "Buffer A", "5 mL"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class FakeGoogle:
    """Answers like Google: a route per URL, every request kept."""

    def __init__(self) -> None:
        self.routes: dict[str, object] = {}
        self.requests: list[httpx.Request] = []

    def file(self, url: str, body: bytes, *, content_type: str = "application/octet-stream",
             filename: str | None = None, headers: dict[str, str] | None = None) -> None:
        extra = dict(headers or {})
        if filename is not None:
            extra["content-disposition"] = f'attachment; filename="{filename}"'
        self.routes[url] = httpx.Response(
            200, content=body, headers={"content-type": content_type, **extra})

    def redirect(self, url: str, location: str, *, status: int = 307,
                 headers: dict[str, str] | None = None) -> None:
        self.routes[url] = httpx.Response(
            status, headers={"location": location, **(headers or {})})

    def answer(self, url: str, response: object) -> None:
        self.routes[url] = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(str(request.url))
        if answer is None:
            return httpx.Response(404, content=b"<html>Not Found</html>",
                                  headers={"content-type": "text/html"})
        if callable(answer):
            return answer(request)
        if isinstance(answer, BaseException):
            raise answer
        return httpx.Response(answer.status_code, headers=answer.headers,
                              content=answer.content, request=request)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def drive_download(file_id: str = FILE_ID) -> str:
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download"


def doc_export(doc_id: str = DOC_ID) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/export?format=pdf"


def sheet_export(sheet_id: str = SHEET_ID) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"


class LinkServerCase(unittest.IsolatedAsyncioTestCase):
    """The server with its catalog in a temporary folder and Google faked."""

    signed_in = True

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "catalog"
        clear_protocol_pdf_cache()
        self.started: list[str] = []
        self.google = FakeGoogle()
        self.resolved: list[str] = []
        self.env = patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "false",
            "VOINEY_LAB_USAGE_SCOPE": "demo",
        })
        self.env.start()
        self.provider = MixedProvider()
        self.office = FakeOffice()
        self.patches = [
            patch.object(server_module, "_open_protocol_catalog", self._open_catalog),
            patch.object(server_module, "_protocol_ocr_provider", lambda: self.provider),
            patch.object(server_module, "_begin_background_analysis", self._begin_analysis),
            patch("fastapi.routing.run_in_threadpool", side_effect=_dedicated_to_thread),
            patch.object(server_module.asyncio, "to_thread", side_effect=_dedicated_to_thread),
            patch.object(pdf_module, "find_office_converter", return_value="/usr/bin/soffice"),
            patch.object(pdf_module, "docx_to_pdf", self._docx_to_pdf),
            patch.object(link_module, "_transport", lambda: self.google.transport()),
            patch.object(link_module, "_resolve", self._resolve),
        ]
        if self.signed_in:
            self.patches.append(
                patch.object(server_module, "_request_account", lambda: object()))
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

    _real_docx_to_pdf = staticmethod(docx_to_pdf)

    def _docx_to_pdf(self, source, destination, *, converter=None, run=None):
        return self._real_docx_to_pdf(source, destination, converter=converter, run=self.office)

    def _open_catalog(self):
        store = initialize_protocol_store(ProtocolPersistenceSettings(True, self.data_dir))
        return ProtocolCatalog(store), store

    async def _begin_analysis(self, protocol_id, *, principal, analysis_id=None):
        self.started.append(protocol_id)
        return {"analysis_request_accepted": True}

    def _resolve(self, host: str) -> list[str]:
        self.resolved.append(host)
        return [GOOGLE_ADDRESS]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=server_module.app),
                                 base_url="http://testserver")

    async def import_link(self, url: object, **extra) -> httpx.Response:
        async with self.client() as client:
            return await client.post("/api/protocols/import-link", json={"url": url, **extra})

    def events(self, protocol_id: str, event_type: str) -> list[dict]:
        catalog, store = self._open_catalog()
        try:
            return [event.payload for event in store.list_events(protocol_id)
                    if event.event_type == event_type]
        finally:
            store.close()


class DriveFileLinkTests(LinkServerCase):
    async def test_a_drive_pdf_link_is_registered_like_an_upload(self) -> None:
        self.google.file(drive_download(), pdf_bytes(), content_type="application/pdf",
                         filename="digestion.pdf")
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        # The upload route's own answer, with the link it came from beside it.
        self.assertEqual(set(payload) - {"source_link"}, {"protocol", "deduplicated", "ocr"})
        protocol_id = payload["protocol"]["protocol_id"]
        self.assertEqual(payload["protocol"]["source_filename"], "digestion.pdf")
        self.assertEqual(payload["protocol"]["analysis_status"], "structured_analysis_ready")
        self.assertFalse(payload["deduplicated"])
        registered = self.events(protocol_id, "protocol_registered")
        self.assertEqual(len(registered), 1)
        self.assertEqual(registered[0]["source_filename"], "digestion.pdf")
        # The address is the source, kept in the protocol's ledger.
        linked = self.events(protocol_id, "protocol_source_linked")
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["url"], DRIVE_LINK)
        self.assertEqual(linked[0]["link_kind"], "drive_file")
        self.assertEqual(linked[0]["file_id"], FILE_ID)
        self.assertEqual(linked[0]["media_type"], "application/pdf")
        self.assertEqual(linked[0]["sha256"], registered[0]["source_sha256"])
        self.assertEqual(linked[0]["attached_as"], "protocol_source")
        self.assertEqual(payload["source_link"]["url"], DRIVE_LINK)
        # One download from the Drive download host, nothing else asked.
        self.assertEqual([str(request.url) for request in self.google.requests], [drive_download()])

    async def test_the_same_file_by_link_and_by_upload_is_one_protocol(self) -> None:
        data = pdf_bytes()
        async with self.client() as client:
            uploaded = await client.post("/api/protocols?filename=bench.pdf", content=data,
                                         headers={"Content-Type": "application/pdf"})
        self.google.file(drive_download(), data, content_type="application/pdf")
        linked = await self.import_link(DRIVE_LINK)
        self.assertEqual(linked.status_code, 201, linked.text)
        self.assertTrue(linked.json()["deduplicated"])
        self.assertEqual(linked.json()["protocol"]["protocol_id"],
                         uploaded.json()["protocol"]["protocol_id"])

    async def test_other_drive_file_link_shapes_name_the_same_file(self) -> None:
        for url in (
            f"https://drive.google.com/open?id={FILE_ID}",
            f"https://drive.google.com/uc?id={FILE_ID}&export=download",
            f"https://drive.google.com/file/u/1/d/{FILE_ID}/view",
            f"https://drive.usercontent.google.com/download?id={FILE_ID}&export=download",
        ):
            with self.subTest(url=url):
                link = link_module.parse_google_link(url)
                self.assertEqual((link.kind, link.file_id), ("drive_file", FILE_ID))
                self.assertEqual(link.download_url, drive_download())

    async def test_a_resource_key_in_the_link_is_carried_to_the_download(self) -> None:
        link = link_module.parse_google_link(
            f"https://drive.google.com/file/d/{FILE_ID}/view?resourcekey=0-AbC_d")
        self.assertEqual(link.download_url, drive_download() + "&resourcekey=0-AbC_d")

    async def test_a_drive_word_document_is_read_as_its_pdf(self) -> None:
        self.google.file(drive_download(), docx_bytes(), filename="protocol.docx")
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 201, response.text)
        protocol_id = response.json()["protocol"]["protocol_id"]
        self.assertEqual(response.json()["protocol"]["source_filename"], "protocol.pdf")
        origin = self.events(protocol_id, "protocol_registered")[0]["converted_from"]
        self.assertEqual(origin["converter"], "libreoffice-24.2.7.2")
        self.assertEqual(origin["filename"], "protocol.docx")
        linked = self.events(protocol_id, "protocol_source_linked")[0]
        self.assertEqual(linked["media_type"], pdf_module.DOCX_MEDIA_TYPE)
        self.assertEqual(linked["sha256"], origin["sha256"])

    async def test_a_drive_photo_is_ocr_d_like_an_uploaded_photo(self) -> None:
        # Google names the type wrongly here; the bytes decide.
        self.google.file(drive_download(), photo_bytes(), content_type="application/octet-stream",
                         filename="bench.jpg")
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        protocol_id = payload["protocol"]["protocol_id"]
        self.assertEqual(payload["protocol"]["analysis_status"], "ocr_required")
        self.assertTrue(payload["ocr"]["automatic"])
        task = server_module._PROTOCOL_OCR_TASKS.get(protocol_id)
        if task is not None:
            await task
        self.assertEqual(self.started, [protocol_id])
        origin = self.events(protocol_id, "protocol_registered")[0]["converted_from"]
        self.assertEqual(origin["media_type"], "image/jpeg")

    async def test_a_name_google_does_not_give_comes_from_the_file_id(self) -> None:
        self.google.file(drive_download(), pdf_bytes(), content_type="application/pdf")
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["protocol"]["source_filename"],
                         f"google-drive-{FILE_ID[:12]}.pdf")


class GoogleDocsLinkTests(LinkServerCase):
    async def test_a_docs_link_is_exported_as_a_pdf_and_registered(self) -> None:
        export_url = f"https://{DOC_EXPORT_HOST}/export/abc/def/1792455435000/1/*/{DOC_ID}?format=pdf"
        self.google.redirect(doc_export(), export_url)
        self.google.file(export_url, pdf_bytes(title="doc"), content_type="application/pdf",
                         filename="Digestion protocol.pdf")
        response = await self.import_link(DOC_LINK)
        self.assertEqual(response.status_code, 201, response.text)
        protocol_id = response.json()["protocol"]["protocol_id"]
        self.assertEqual(response.json()["protocol"]["source_filename"], "Digestion protocol.pdf")
        linked = self.events(protocol_id, "protocol_source_linked")[0]
        self.assertEqual((linked["link_kind"], linked["file_id"]), ("document", DOC_ID))
        self.assertEqual(linked["final_host"], DOC_EXPORT_HOST)
        self.assertEqual([str(request.url) for request in self.google.requests],
                         [doc_export(), export_url])

    async def test_a_docs_link_cannot_be_a_reagent_table(self) -> None:
        response = await self.import_link(DOC_LINK, protocol_id="protocol-0123456789abcdef")
        self.assertEqual(response.status_code, 415, response.text)
        self.assertEqual(self.google.requests, [])


class GoogleSheetsLinkTests(LinkServerCase):
    async def register_protocol(self) -> str:
        async with self.client() as client:
            response = await client.post("/api/protocols?filename=bench.pdf", content=pdf_bytes(),
                                         headers={"Content-Type": "application/pdf"})
        return response.json()["protocol"]["protocol_id"]

    async def test_a_sheets_link_is_attached_as_the_protocol_s_reagent_table(self) -> None:
        protocol_id = await self.register_protocol()
        export_url = f"https://{SHEET_EXPORT_HOST}/export/abc/def/1792455415000/1/*/{SHEET_ID}?format=xlsx"
        self.google.redirect(sheet_export(), export_url)
        self.google.file(export_url, xlsx_bytes(),
                         content_type=pdf_module.DOCX_MEDIA_TYPE.replace(
                             "wordprocessingml.document", "spreadsheetml.sheet"),
                         filename="Reagents.xlsx")
        response = await self.import_link(SHEET_LINK, protocol_id=protocol_id)
        self.assertEqual(response.status_code, 201, response.text)
        table = response.json()["reagent_table"]
        self.assertEqual(table["filename"], "Reagents.xlsx")
        async with self.client() as client:
            listed = (await client.get(f"/api/protocols/{protocol_id}/reagent-tables")).json()
        self.assertEqual([item["table_id"] for item in listed["reagent_tables"]], [table["table_id"]])
        linked = self.events(protocol_id, "protocol_source_linked")
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["attached_as"], "reagent_table")
        self.assertEqual(linked[0]["table_id"], table["table_id"])
        self.assertEqual(linked[0]["link_kind"], "spreadsheet")
        self.assertEqual(linked[0]["url"], SHEET_LINK.split("#", 1)[0])

    async def test_a_sheets_link_without_a_protocol_is_refused_before_any_request(self) -> None:
        response = await self.import_link(SHEET_LINK)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.google.requests, [])

    async def test_an_unknown_protocol_is_refused_before_any_request(self) -> None:
        response = await self.import_link(SHEET_LINK, protocol_id="protocol-0123456789abcdef")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.google.requests, [])

    async def test_a_drive_workbook_needs_a_protocol_too(self) -> None:
        self.google.file(drive_download(), xlsx_bytes(), filename="reagents.xlsx")
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 422, response.text)


class SignInTests(LinkServerCase):
    signed_in = False

    async def test_nobody_signed_in_is_refused_before_any_request(self) -> None:
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.google.requests, [])
        self.assertEqual(self.resolved, [])

    async def test_the_route_is_not_open_without_a_session_in_accounts_mode(self) -> None:
        self.assertNotIn("/api/protocols/import-link", server_module._ACCOUNT_OPEN_PATHS)
        self.assertFalse("/api/protocols/import-link".startswith(
            server_module._ACCOUNT_OPEN_PREFIXES))


if __name__ == "__main__":
    unittest.main()
