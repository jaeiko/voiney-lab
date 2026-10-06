"""Pages without a usable text layer are OCR'd at upload (lane PX, decision 3).

Human decision of 2026-10-06: only the pages whose text layer is missing or
unreadable (``page_ocr_reason``: no visible character, a glyph with no
Unicode mapping, or 5% private-use/unassigned characters) are read by the
configured OCR engines, automatically, when the PDF is uploaded; their text
is marked as OCR text; and the structured analysis starts as soon as it is
in. There is no separate OCR approval step: the result is accepted under a
recorded automatic authority and the one confirmation a person gives before
execution covers it. The "numbers need checking" mark the two engines put on
a page stays visible. Every OCR engine and analysis model here is fake.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from tests.test_pdf_text_engine import _write_raw_pages
from tests.test_protocol_catalog import _dedicated_to_thread, write_text_pdf
from tests.test_protocol_ocr import FakeOcrProvider
from tests.test_screen_cleanup import run_page_script
from voiney_lab import experiment_protocol as domain
from voiney_lab import server as server_module
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import AUTOMATIC_OCR_AUTHORITY, ProtocolCatalog
from voiney_lab.protocol_ocr import OcrPage, OcrResult, ProtocolOcrUnavailableError
from voiney_lab.protocol_ocr_providers import TEXT_LAYER

ROOT = Path(__file__).resolve().parents[1]


class MixedProvider:
    """Reads the marked pages only; keeps the text layer of the others."""

    def __init__(self, *, numeric_review_pages: tuple[int, ...] = ()) -> None:
        self.calls = 0
        self.numeric_review_pages = numeric_review_pages

    def recognize(self, source_pdf, *, source_sha256, page_count):
        self.calls += 1
        extraction = extract_protocol_pdf(source_pdf)
        pages = []
        for page in extraction.pages:
            if page.ocr_required:
                pages.append(OcrPage(
                    page.source_page_number,
                    f"Fictional OCR text of page {page.source_page_number}. Add 5 mL buffer.",
                    0.93, provider="clova", provider_version="clova-general-v2",
                    numeric_review_required=page.source_page_number in self.numeric_review_pages,
                ))
            else:
                pages.append(OcrPage(
                    page.source_page_number, page.text,
                    provider=TEXT_LAYER, provider_version="pymupdf-test",
                ))
        return OcrResult(
            source_sha256=source_sha256, provider="clova", provider_version="clova-general-v2",
            pages=tuple(pages), languages=("en",),
        )


class _CatalogCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)
        self.mixed = self.root / "mixed.pdf"
        _write_raw_pages(self.mixed, (
            "BT /F1 12 Tf 72 720 Td (Fictional protocol. 1 Add buffer to the tube.) Tj ET",
            None,
        ))

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def register(self, path: Path, name: str):
        return self.catalog.register(path, source_filename=name, media_type="application/pdf").entry


class CatalogAutomaticAcceptanceTests(_CatalogCase):
    def test_upload_time_ocr_is_accepted_under_a_recorded_automatic_authority(self) -> None:
        entry = self.register(self.mixed, "mixed.pdf")
        self.assertEqual(entry.analysis_status, "structured_analysis_ready")
        self.assertEqual(self.catalog.ocr_status(entry.protocol_id)["state"], "ocr_required")
        provider = MixedProvider()
        status = self.catalog.run_ocr(
            entry.protocol_id, provider, ocr_id="ocr-auto", accepted_automatically=True)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(status["state"], "accepted_for_analysis")
        self.assertTrue(status["accepted_for_analysis"])
        self.assertFalse(status["review_required"])
        self.assertFalse(status["executable"])
        review = status["review"]
        self.assertEqual(review["authority"], AUTOMATIC_OCR_AUTHORITY)
        self.assertEqual(review["decision"], "accepted")
        self.assertEqual(review["ocr_page_numbers"], [2])
        self.assertNotIn("actor_principal_id", review)
        # The ledger holds the acceptance as an event of its own.
        kinds = [event.event_type for event in self.store.list_events(entry.protocol_id)]
        self.assertIn("protocol_ocr_reviewed", kinds)

    def test_only_the_ocr_page_is_marked_as_ocr_text_and_the_gate_clears(self) -> None:
        entry = self.register(self.mixed, "mixed.pdf")
        self.catalog.run_ocr(
            entry.protocol_id, MixedProvider(), ocr_id="ocr-auto", accepted_automatically=True)
        revision = self.catalog._latest_protocol_revision(entry.protocol_id)
        analysed = self.catalog._extraction_for_analysis(revision, extract_protocol_pdf(self.mixed))
        self.assertEqual(analysed.ocr_required_page_numbers, ())
        self.assertFalse(analysed.pages[0].ocr_derived)
        self.assertIn("1 Add buffer to the tube.", analysed.pages[0].text)
        self.assertTrue(analysed.pages[1].ocr_derived)
        self.assertIn("Fictional OCR text of page 2.", analysed.pages[1].text)
        # The source bytes and the page text layer are untouched.
        self.assertEqual(extract_protocol_pdf(self.mixed).ocr_required_page_numbers, (2,))

    def test_the_numeric_review_mark_stays_visible(self) -> None:
        entry = self.register(self.mixed, "mixed.pdf")
        status = self.catalog.run_ocr(
            entry.protocol_id, MixedProvider(numeric_review_pages=(2,)),
            ocr_id="ocr-auto", accepted_automatically=True)
        marked = [page for page in status["pages"] if page["numeric_review_required"]]
        self.assertEqual([page["source_page_number"] for page in marked], [2])
        self.assertEqual(status["review"]["numeric_review_page_numbers"], [2])
        self.assertEqual(status["pages"][1]["provider"], "clova")
        self.assertEqual(status["pages"][0]["provider"], TEXT_LAYER)

    def test_text_layer_pages_keep_their_geometry_after_an_ocr_acceptance(self) -> None:
        # Lane PA's page-boundary rule reads the footer band and the text
        # blocks. Measured 2026-10-06 on ANKOM: with them dropped for every
        # page after an OCR acceptance, step 21 ("21 Flush procedure:" ending
        # page 19, its sentence opening page 20) was refused, while the same
        # response passed on the raw extraction.
        cut = self.root / "cut-with-scan.pdf"
        line = lambda y, text: f"BT /F1 12 Tf 72 {y} Td ({text}) Tj ET "  # noqa: E731
        _write_raw_pages(cut, (
            line(720, "Peptide extraction") + line(706, "24 Spin down the digest. Keep the solution, which will contain the")
            + line(20, "example.org | protocol 1/3"),
            line(720, "peptides. Pool the peptides.") + line(706, "25 Dry the extracted peptides.")
            + line(20, "example.org | protocol 2/3"),
            None,
        ))
        entry = self.register(cut, "cut-with-scan.pdf")
        self.catalog.run_ocr(entry.protocol_id, MixedProvider(), ocr_id="ocr-cut", accepted_automatically=True)
        revision = self.catalog._latest_protocol_revision(entry.protocol_id)
        raw = extract_protocol_pdf(cut)
        analysed = self.catalog._extraction_for_analysis(revision, raw)
        for number in (1, 2):
            with self.subTest(page=number):
                self.assertEqual(analysed.pages[number - 1].bottom_band_offset, raw.pages[number - 1].bottom_band_offset)
                self.assertIsNotNone(analysed.pages[number - 1].bottom_band_offset)
                self.assertEqual(analysed.pages[number - 1].blocks, raw.pages[number - 1].blocks)
                self.assertFalse(analysed.pages[number - 1].ocr_derived)
        self.assertIsNone(analysed.pages[2].bottom_band_offset)
        self.assertEqual(analysed.pages[2].blocks, ())
        self.assertTrue(analysed.pages[2].ocr_derived)
        from voiney_lab import experiment_protocol_analysis as analysis_module

        statement = "Keep the solution, which will contain the peptides."
        self.assertIsNotNone(analysis_module._statement_across_page_end(statement, analysed, 1))
        verified = analysis_module._verified_evidence(
            domain.SourceEvidence(1, statement), analysed)
        self.assertEqual(verified.continued_on_page_number, 2)

    def test_without_the_flag_a_result_still_waits_for_a_person(self) -> None:
        entry = self.register(self.mixed, "mixed.pdf")
        status = self.catalog.run_ocr(entry.protocol_id, FakeOcrProvider(), ocr_id="ocr-manual")
        self.assertEqual(status["state"], "review_required")
        self.assertFalse(status["accepted_for_analysis"])


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

    def tearDown(self) -> None:
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

    def _patches(self, provider):
        return (
            patch.object(server_module, "_open_protocol_catalog", self._open_catalog),
            patch.object(server_module, "_protocol_ocr_provider", provider),
            patch.object(server_module, "_begin_background_analysis", self._begin_analysis),
            patch("fastapi.routing.run_in_threadpool", side_effect=_dedicated_to_thread),
            patch.object(server_module.asyncio, "to_thread", side_effect=_dedicated_to_thread),
        )

    async def _upload(self, client, path: Path, name: str):
        return await client.post(
            f"/api/protocols?filename={name}", content=path.read_bytes(),
            headers={"Content-Type": "application/pdf"})

    async def test_an_upload_with_pages_without_text_runs_ocr_then_starts_the_analysis(self) -> None:
        scanned = self.root / "scanned.pdf"
        write_text_pdf(scanned, None, title="Scanned")
        provider = MixedProvider(numeric_review_pages=(1,))
        patches = self._patches(lambda: provider)
        for item in patches:
            item.start()
        try:
            transport = httpx.ASGITransport(app=server_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await self._upload(client, scanned, "scanned.pdf")
                self.assertEqual(response.status_code, 201, response.text)
                payload = response.json()
                protocol_id = payload["protocol"]["protocol_id"]
                self.assertEqual(payload["protocol"]["analysis_status"], "ocr_required")
                self.assertEqual(payload["ocr"]["state"], "queued")
                self.assertTrue(payload["ocr"]["automatic"])
                # The chain runs on the loop; with a fake engine it may be
                # done before the response is read.
                task = server_module._PROTOCOL_OCR_TASKS.get(protocol_id)
                if task is not None:
                    await task
                status = await client.get(f"/api/protocols/{protocol_id}/ocr")
                self.assertEqual(status.json()["state"], "accepted_for_analysis")
                self.assertEqual(status.json()["review"]["authority"], AUTOMATIC_OCR_AUTHORITY)
                self.assertTrue(status.json()["pages"][0]["numeric_review_required"])
                self.assertEqual(self.started, [protocol_id])
                run = await client.get(f"/api/protocols/{protocol_id}/analysis/status")
                self.assertEqual(run.json()["state"], "structured_analysis_ready")
                # The same PDF again: the OCR is in, nothing runs twice.
                again = await self._upload(client, scanned, "scanned-again.pdf")
                self.assertTrue(again.json()["deduplicated"])
                self.assertEqual(again.json()["ocr"]["state"], "accepted_for_analysis")
                self.assertFalse(again.json()["ocr"]["automatic"])
                self.assertEqual(again.json()["ocr"]["ocr_page_numbers"], [1])
        finally:
            for item in patches:
                item.stop()
        self.assertEqual(provider.calls, 1)

    async def test_a_readable_document_with_one_blank_page_is_ocr_d_before_its_analysis(self) -> None:
        mixed = self.root / "mixed.pdf"
        _write_raw_pages(mixed, (
            "BT /F1 12 Tf 72 720 Td (Fictional protocol. 1 Add buffer to the tube.) Tj ET",
            None,
        ))
        provider = MixedProvider()
        patches = self._patches(lambda: provider)
        for item in patches:
            item.start()
        try:
            transport = httpx.ASGITransport(app=server_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await self._upload(client, mixed, "mixed.pdf")
                payload = response.json()
                protocol_id = payload["protocol"]["protocol_id"]
                self.assertEqual(payload["protocol"]["analysis_status"], "structured_analysis_ready")
                self.assertTrue(payload["ocr"]["automatic"])
                task = server_module._PROTOCOL_OCR_TASKS.get(protocol_id)
                if task is not None:
                    await task
                self.assertEqual(self.started, [protocol_id])
                # While an OCR chain runs, an analysis request is deferred to
                # it: the analysis must read the OCR text, not the empty page.
                pending: asyncio.Future = asyncio.get_running_loop().create_future()
                server_module._PROTOCOL_OCR_TASKS[protocol_id] = pending
                deferred = await client.post(f"/api/protocols/{protocol_id}/analysis")
                self.assertEqual(deferred.status_code, 202, deferred.text)
                self.assertEqual(deferred.json()["analysis_request_deferred"], "ocr_in_progress")
                self.assertEqual(self.started, [protocol_id])
                pending.cancel()
        finally:
            for item in patches:
                item.stop()

    async def test_without_an_ocr_provider_the_upload_says_where_it_is_blocked(self) -> None:
        scanned = self.root / "scanned.pdf"
        write_text_pdf(scanned, None, title="Scanned")

        def unavailable():
            raise ProtocolOcrUnavailableError("not configured")

        patches = self._patches(unavailable)
        for item in patches:
            item.start()
        try:
            transport = httpx.ASGITransport(app=server_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await self._upload(client, scanned, "scanned.pdf")
                payload = response.json()
                self.assertEqual(payload["ocr"]["state"], "ocr_required")
                self.assertFalse(payload["ocr"]["automatic"])
                self.assertEqual(payload["ocr"]["blocked"], "protocol_ocr_not_configured")
                self.assertEqual(server_module._PROTOCOL_OCR_TASKS, {})
                self.assertEqual(self.started, [])
        finally:
            for item in patches:
                item.stop()

    async def test_a_document_with_a_text_layer_everywhere_needs_no_ocr(self) -> None:
        plain = self.root / "plain.pdf"
        write_text_pdf(plain, "Protocol Plain\nSection preparation\n1. Add solution.", title="Plain")
        provider = MixedProvider()
        patches = self._patches(lambda: provider)
        for item in patches:
            item.start()
        try:
            transport = httpx.ASGITransport(app=server_module.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                payload = (await self._upload(client, plain, "plain.pdf")).json()
                self.assertEqual(payload["ocr"], {"state": "not_required", "automatic": False})
                self.assertEqual(server_module._PROTOCOL_OCR_TASKS, {})
        finally:
            for item in patches:
                item.stop()
        self.assertEqual(provider.calls, 0)


# The page harness: a fake server answering the upload, OCR, analysis, status,
# review and catalog calls, recording every call.
PAGE = r"""
const calls=[];let statusQueue=[],ocrQueue=[];
let reviewExtra={};
const reviewFor=(id,extra={})=>({protocol_id:id,title:"Scan",revision_id:"pdf-1",analysis_status:"ocr_required",available_for_execution:false,readiness:{status:"analysis_required",reasons:[]},source:{filename:"scan.pdf",sha256:"a".repeat(64),page_count:2},sections:[],ocr:{state:"ocr_required"},...extra});
function serve(upload){
 globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url}`);
  if(url.startsWith("/api/protocols?filename="))return json(upload);
  const id=upload.protocol.protocol_id;
  if(url===`/api/protocols/${id}/ocr`&&(options.method||"GET")==="GET")return json(ocrQueue.length?ocrQueue.shift():{state:"in_progress"});
  if(url===`/api/protocols/${id}/analysis`&&options.method==="POST")return json({...upload.protocol,analysis_status:"analyzing",analysis_request_accepted:true,analysis_run:{state:"analysis_pending"}});
  if(url===`/api/protocols/${id}/analysis/status`)return json(statusQueue.length?statusQueue.shift():{state:"analyzing"});
  if(url===`/api/protocols/${id}/review`)return json(reviewFor(id,reviewExtra));
  if(url==="/api/protocols")return json({protocols:[{...upload.protocol,title:"Scan",revision_id:"pdf-1",available_for_execution:false}]});
  if(url.startsWith("/api/workspace/"))return json({protocols:[],experiments:[],revisions:[]});
  throw new Error(`unexpected ${options.method||"GET"} ${url}`);};
}
async function upload(protocol,extra={}){
 node("protocol-pdf").files=[{name:"scan.pdf",type:"application/pdf"}];
 serve({deduplicated:false,protocol:{protocol_id:"p-scan",source_filename:"scan.pdf",...protocol},...extra});
 await registerSelectedProtocol();
 stopProtocolAnalysisPolling();stopProtocolOcrPolling();
}
const analysisPosts=()=>calls.filter(call=>call==="POST /api/protocols/p-scan/analysis").length;
const status=()=>node("protocol-upload-status").textContent;
"""


class PageTests(unittest.TestCase):
    def run_page(self, body: str) -> None:
        result = run_page_script(PAGE + body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_automatic_ocr_upload_follows_the_ocr_then_the_analysis(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"ocr_required",available_for_execution:false},{ocr:{state:"queued",automatic:true,ocr_id:"ocr-1"}});
assert(status().includes("OCR 중")&&status().includes("분석을 바로 시작"),`no OCR line: ${status()}`);
assert(analysisPosts()===0,`the page started an analysis itself: ${calls}`);
currentProtocolReviewId="p-scan";
ocrQueue=[{state:"accepted_for_analysis",accepted_for_analysis:true,review:{authority:"automatic_upload_ocr"},pages:[{source_page_number:1,provider:"clova"},{source_page_number:2,provider:"pdf-text-layer"}]}];
statusQueue=[{state:"analyzing",requested_at:new Date().toISOString()}];
await pollProtocolOcrStatus("p-scan");stopProtocolAnalysisPolling();
assert(status().includes("OCR 끝")&&status().includes("p.1")&&status().includes("분석을 시작"),`no OCR-finished line: ${status()}`);
assert(!status().includes("승인"),`the page still asks for an OCR approval: ${status()}`);
assert(calls.some(call=>call==="GET /api/protocols/p-scan/analysis/status"),`the analysis is not being followed: ${calls}`);
assert(analysisPosts()===0,`the page started a second analysis: ${calls}`);
""")

    def test_without_an_ocr_provider_the_page_says_where_it_stopped_and_what_to_do(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"ocr_required",available_for_execution:false},{ocr:{state:"ocr_required",automatic:false,blocked:"protocol_ocr_not_configured"}});
assert(status().startsWith("막힘 · OCR")&&status().includes("VOINEY_LAB_OCR_PROVIDERS")&&status().includes("할 일"),`no blocked line: ${status()}`);
assert(analysisPosts()===0,`an analysis was started without OCR: ${calls}`);
""")

    def test_the_review_names_the_ocr_pages_their_marks_and_the_automatic_acceptance(self) -> None:
        self.run_page(r"""
renderProtocolReview({protocol_id:"p-scan",title:"Scan",revision_id:"pdf-1-analysis-1",analysis_status:"review_required",available_for_execution:false,
 source:{filename:"scan.pdf",sha256:"a".repeat(64),page_count:2},readiness:{status:"analysis_required",reasons:[]},sections:[],
 ocr:{state:"accepted_for_analysis",accepted_for_analysis:true,provider:"clova-google",provider_version:"v2_v1",review:{authority:"automatic_upload_ocr",decision:"accepted"},
  pages:[{source_page_number:1,provider:"pdf-text-layer",text:"kept"},{source_page_number:2,provider:"clova",confidence:0.93,numeric_review_required:true,text:"Add 5 mL buffer."}],warnings:[]}});
const shown=visibleText(node("protocol-review-content"));
assert(shown.includes("원문 p.2 · OCR 출처 · clova"),`OCR page not named: ${shown}`);
assert(shown.includes("숫자 확인 필요"),`numeric mark missing: ${shown}`);
assert(!shown.includes("원문 p.1 · OCR 출처"),`a text-layer page is shown as OCR: ${shown}`);
assert(shown.includes("자동 OCR")&&shown.includes("실행 전 사람 확인 한 번"),`automatic acceptance not said: ${shown}`);
assert(!shown.includes("별도로 시작"),`the old separate-analysis line is still there: ${shown}`);
""")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
