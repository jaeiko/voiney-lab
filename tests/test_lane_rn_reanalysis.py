"""A passed analysis can be analysed again, beside the old one (lane RN, 2).

Human decision 2 of 2026-10-11. Lane PA decided that a passed analysis is
never called again by an upload; that stays. Lane EV2 then changed what a
repeat count means, so every analysis stored before it reads one round
short, and the same will happen each time the analyser improves. So:

* "다시 분석" (the same place as "분석 다시 시도") asks once -- "분석을 새로
  만들어요. 진행 중인 실험은 그대로입니다" -- and sends an explicit request
  to its own route; the upload route keeps lane PA's rule.
* The ledger is append-only: the request is one more event, a pass is one
  more analysis revision, and the old analysis and its events stay.
* A re-analysis that fails does not cover the pass before it: the revision
  stays executable on the analysis it had, and the failure is said as the
  re-analysis's own.
* An experiment already running holds the fixture it started with; the new
  analysis is for the next one. The server alone says which analysis a
  start loads (the latest stored one).
* A second request while a re-analysis runs starts nothing (decision 1).
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.aged_store import curated_fixture_for, draft_for
from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from tests.test_screen_cleanup import run_page_script
from voiney_lab import server
from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisEvidenceError
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog, ProtocolCatalogError

ANALYZE = "voiney_lab.protocol_catalog.analyze_protocol_extraction"
TEXT = "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves."
FIRST = "analysis-" + "a" * 32
SECOND = "analysis-" + "b" * 32


class _CatalogCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.ready: list[str] = []
        self.catalog = ProtocolCatalog(
            self.store, on_analysis_ready=lambda _catalog, pid: self.ready.append(pid))
        self.pdf = self.root / "alpha.pdf"
        write_text_pdf(self.pdf, TEXT, title="Protocol Alpha")
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="alpha.pdf", media_type="application/pdf",
        ).entry.protocol_id
        self.draft = analysis_draft(self.pdf, self.protocol_id, "Protocol Alpha")

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def analyze(self, side_effect, analysis_id: str, *, reanalysis: bool = False):
        self.catalog.request_analysis(self.protocol_id, analysis_id, reanalysis=reanalysis)
        with patch(ANALYZE, side_effect=side_effect) as analyze:
            try:
                self.catalog.analyze(self.protocol_id, Mock(), analysis_id=analysis_id)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                return analyze, exc
        return analyze, None

    def pass_once(self) -> None:
        _, error = self.analyze([self.draft], FIRST)
        self.assertIsNone(error)

    def events(self) -> list:
        return list(self.store.list_events(self.protocol_id))

    def event_types(self) -> list[str]:
        return [event.event_type for event in self.events()]

    def status(self) -> dict:
        return self.catalog.analysis_run_status(self.protocol_id).public_dict()


class ExplicitRequestTests(_CatalogCase):
    def test_a_plain_request_on_a_passed_analysis_records_nothing(self) -> None:
        # Lane PA's rule, as before: an upload's request never calls a
        # passed analysis again.
        self.pass_once()
        before = self.event_types()
        entry = self.catalog.request_analysis(self.protocol_id, SECOND)
        self.assertEqual(self.event_types(), before)
        self.assertEqual(entry.revision_id, "pdf-1-analysis-1")
        self.assertEqual(self.status()["state"], "review_required")

    def test_an_explicit_re_analysis_request_is_one_more_event_naming_the_old_analysis(self) -> None:
        self.pass_once()
        before = self.events()
        entry = self.catalog.request_analysis(self.protocol_id, SECOND, reanalysis=True)
        after = self.events()
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(len(after), len(before) + 1)
        request = after[-1]
        self.assertEqual(request.event_type, "protocol_analysis_requested")
        self.assertEqual(request.payload["analysis_id"], SECOND)
        self.assertTrue(request.payload["reanalysis"])
        self.assertEqual(request.payload["previous_analysis_revision_number"], 1)
        # The pass stays executable while the request waits.
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.revision_id, "pdf-1-analysis-1")
        status = self.status()
        self.assertEqual(status["state"], "analysis_pending")
        self.assertTrue(status["reanalysis"])
        self.assertTrue(status["available_for_execution"])

    def test_a_re_analysis_request_before_any_analysis_is_a_plain_request(self) -> None:
        self.catalog.request_analysis(self.protocol_id, FIRST, reanalysis=True)
        request = self.events()[-1]
        self.assertEqual(request.event_type, "protocol_analysis_requested")
        self.assertNotIn("reanalysis", request.payload)
        self.assertFalse(self.status()["reanalysis"])


class PassedAgainTests(_CatalogCase):
    def test_a_re_analysis_that_passes_is_a_new_revision_beside_the_old(self) -> None:
        self.pass_once()
        old_events = self.events()
        old_fixture = self.catalog.load_executable_fixture(self.protocol_id)
        _, error = self.analyze([self.draft], SECOND, reanalysis=True)
        self.assertIsNone(error)
        revisions = self.store.list_analysis_revisions(self.protocol_id, 1)
        self.assertEqual([item.analysis_revision_number for item in revisions], [1, 2])
        self.assertEqual([item.analysis_id for item in revisions], [FIRST, SECOND])
        # Append-only: the old analysis and every old event stay as they were.
        self.assertEqual(self.events()[:len(old_events)], old_events)
        self.assertEqual(
            self.store.get_analysis_revision(self.protocol_id, 1, 1).payload_sha256,
            old_fixture.fixture_sha256)
        # The server's choice of the executable analysis: the latest stored.
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertEqual(entry.revision_id, "pdf-1-analysis-2")
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(
            self.catalog.load_executable_fixture(self.protocol_id).revision_id,
            "pdf-1-analysis-2")
        # The experiment that started on the old one holds what it loaded.
        self.assertEqual(old_fixture.revision_id, "pdf-1-analysis-1")
        status = self.status()
        self.assertEqual(status["state"], "review_required")
        self.assertIsNone(status["failure_code"])
        self.assertTrue(status["reanalysis"])
        review = self.catalog.review(self.protocol_id)
        self.assertIsNone(review["analysis_failure"])
        self.assertIsNone(review["reanalysis"])
        self.assertEqual(review["pipeline"]["stage"], "ready")
        # The translation starts for each passed analysis.
        self.assertEqual(self.ready, [self.protocol_id, self.protocol_id])


class FailedAgainTests(_CatalogCase):
    def test_a_re_analysis_that_fails_leaves_the_passed_analysis_executable(self) -> None:
        self.pass_once()
        _, error = self.analyze(
            [ProtocolAnalysisEvidenceError("Evidence quote was invalid.")], SECOND,
            reanalysis=True)
        self.assertIsInstance(error, ProtocolAnalysisEvidenceError)
        self.assertNotIn("protocol_analysis_retry_started", self.event_types())
        entry = self.catalog.get_entry(self.protocol_id)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual((entry.revision_id, entry.lifecycle_state), ("pdf-1-analysis-1", "ready"))
        self.assertEqual(
            self.catalog.load_executable_fixture(self.protocol_id).revision_id,
            "pdf-1-analysis-1")
        status = self.status()
        self.assertEqual(status["state"], "analysis_failed")
        self.assertEqual(status["failure_code"], "protocol_analysis_invalid_evidence")
        self.assertTrue(status["reanalysis"])
        self.assertTrue(status["available_for_execution"])
        review = self.catalog.review(self.protocol_id)
        self.assertTrue(review["available_for_execution"])
        # The failure is the re-analysis's own, not the executable analysis's.
        self.assertIsNone(review["analysis_failure"])
        again = review["reanalysis"]
        self.assertEqual(again["state"], "analysis_failed")
        self.assertEqual(again["failure"]["code"], "protocol_analysis_invalid_evidence")
        self.assertTrue(again["failure"]["retryable"])
        self.assertIn("다시 분석", again["failure"]["action"])
        self.assertRegex(again["requested_at"], r"^\d{4}-\d\d-\d\dT")
        line = review["pipeline"]
        self.assertEqual((line["stage"], line["blocked"]), ("ready", False))
        self.assertIn("다시 분석", line["message"])
        self.assertIn("실패", line["message"])
        self.assertIn("계속 실행", line["message"])
        self.assertEqual(line["failure_code"], "protocol_analysis_invalid_evidence")
        self.assertEqual(self.ready, [self.protocol_id])

    def test_a_failed_analysis_before_any_pass_reads_as_before(self) -> None:
        _, error = self.analyze(
            [ProtocolAnalysisEvidenceError("Evidence quote was invalid.")], FIRST)
        self.assertIsInstance(error, ProtocolAnalysisEvidenceError)
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["analysis_failure"]["code"], "protocol_analysis_invalid_evidence")
        self.assertIsNone(review["reanalysis"])
        self.assertFalse(self.status()["reanalysis"])
        self.assertFalse(self.status()["available_for_execution"])


class WhileItRunsTests(_CatalogCase):
    def test_the_passed_analysis_stays_executable_while_the_re_analysis_runs(self) -> None:
        self.pass_once()
        seen: dict = {}

        def during(*_args, **_kwargs):
            seen["entry"] = self.catalog.get_entry(self.protocol_id)
            seen["status"] = self.status()
            seen["review"] = self.catalog.review(self.protocol_id)
            seen["pipeline"] = self.catalog.pipeline_status(self.protocol_id)
            seen["fixture"] = self.catalog.load_executable_fixture(self.protocol_id)
            return self.draft

        _, error = self.analyze(during, SECOND, reanalysis=True)
        self.assertIsNone(error)
        self.assertTrue(seen["entry"].available_for_execution)
        self.assertEqual(seen["entry"].revision_id, "pdf-1-analysis-1")
        self.assertEqual(seen["fixture"].revision_id, "pdf-1-analysis-1")
        self.assertEqual(seen["status"]["state"], "analyzing")
        self.assertTrue(seen["status"]["reanalysis"])
        self.assertIsNone(seen["review"]["analysis_failure"])
        self.assertEqual(seen["review"]["reanalysis"]["state"], "analyzing")
        self.assertIsNone(seen["review"]["reanalysis"]["failure"])
        line = seen["pipeline"]
        self.assertEqual((line["stage"], line["blocked"]), ("ready", False))
        self.assertIn("다시 분석 중", line["message"])
        self.assertIn("계속 실행", line["message"])


class DevelopmentFixtureTests(unittest.TestCase):
    def test_the_development_fixture_is_not_analysed_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clear_protocol_pdf_cache()
            store = initialize_protocol_store(ProtocolPersistenceSettings(True, root / "catalog"))
            try:
                catalog = ProtocolCatalog(store)
                pdf = root / "source.pdf"
                write_text_pdf(
                    pdf, "Protocol Test\nSection preparation\n1. Add solution.\nWear gloves.",
                    title="Protocol Test")
                fixture = curated_fixture_for(
                    draft_for(pdf, "dev-fixture-v1"), marker=b"fixture", source_pdf=pdf)
                catalog.bootstrap_development_fixture(fixture)
                before = [event.event_type for event in store.list_events("dev-fixture-v1")]
                with self.assertRaises(ProtocolCatalogError) as refused:
                    catalog.request_analysis("dev-fixture-v1", SECOND, reanalysis=True)
                self.assertEqual(refused.exception.code, "reanalysis_not_allowed")
                self.assertEqual(
                    [event.event_type for event in store.list_events("dev-fixture-v1")], before)
            finally:
                store.close()
                clear_protocol_pdf_cache()


class ReanalysisRouteTests(unittest.TestCase):
    """The route the button calls runs the re-analysis off the request, with
    the model the server configured; the upload route keeps lane PA's rule."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, root / "catalog")
        self.pdf = root / "alpha.pdf"
        write_text_pdf(self.pdf, TEXT, title="Protocol Alpha")
        store = initialize_protocol_store(self.settings)
        try:
            self.protocol_id = ProtocolCatalog(store).register(
                self.pdf, source_filename="alpha.pdf", media_type="application/pdf").entry.protocol_id
        finally:
            store.close()
        server._PROTOCOL_ANALYSIS_TASKS.clear()

    def tearDown(self) -> None:
        server._PROTOCOL_ANALYSIS_TASKS.clear()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def open_catalog(self):
        opened = initialize_protocol_store(self.settings)
        return ProtocolCatalog(opened), opened

    def draft(self):
        return analysis_draft(self.pdf, self.protocol_id, "Protocol Alpha")

    def revisions(self) -> list[str]:
        catalog, opened = self.open_catalog()
        try:
            return [item.analysis_id for item in opened.list_analysis_revisions(self.protocol_id, 1)]
        finally:
            opened.close()

    def requests(self) -> int:
        catalog, opened = self.open_catalog()
        try:
            return sum(
                event.event_type == "protocol_analysis_requested"
                for event in opened.list_events(self.protocol_id))
        finally:
            opened.close()

    def test_the_re_analysis_route_runs_again_where_the_upload_route_does_not(self) -> None:
        async def to_thread(function, /, *args, **kwargs):
            return function(*args, **kwargs)

        async def scenario():
            first = await server.trigger_protocol_analysis(self.protocol_id)
            await server._PROTOCOL_ANALYSIS_TASKS[self.protocol_id]
            await asyncio.sleep(0)
            plain = await server.trigger_protocol_analysis(self.protocol_id)
            again = await server.trigger_protocol_reanalysis(self.protocol_id)
            await server._PROTOCOL_ANALYSIS_TASKS[self.protocol_id]
            await asyncio.sleep(0)
            return first, plain, again, server.get_protocol_analysis_status(self.protocol_id)

        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server.asyncio, "to_thread", side_effect=to_thread), \
                patch.object(server, "_protocol_analysis_model", return_value=Mock()), \
                patch(ANALYZE, side_effect=lambda *a, **k: self.draft()) as analyze:
            first, plain, again, status = asyncio.run(scenario())
        self.assertTrue(first["analysis_request_accepted"])
        self.assertTrue(plain["analysis_request_deduplicated"])
        self.assertTrue(again["analysis_request_accepted"])
        self.assertTrue(again["analysis_run"]["reanalysis"])
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(self.requests(), 2)
        self.assertEqual(status["state"], "review_required")
        self.assertTrue(status["reanalysis"])
        self.assertEqual(len(self.revisions()), 2)

    def test_a_second_re_analysis_request_while_it_runs_starts_nothing(self) -> None:
        release = threading.Event()

        def slow(*_args, **_kwargs):
            release.wait(timeout=10)
            return self.draft()

        async def scenario():
            with patch(ANALYZE, side_effect=lambda *a, **k: self.draft()):
                await server.trigger_protocol_analysis(self.protocol_id)
                await server._PROTOCOL_ANALYSIS_TASKS[self.protocol_id]
                await asyncio.sleep(0)
            with patch(ANALYZE, side_effect=slow) as analyze:
                first = await server.trigger_protocol_reanalysis(self.protocol_id)
                for _ in range(200):
                    if self.requests() == 2 and analyze.call_count:
                        break
                    await asyncio.sleep(0.02)
                second = await server.trigger_protocol_reanalysis(self.protocol_id)
                release.set()
                await server._PROTOCOL_ANALYSIS_TASKS[self.protocol_id]
                await asyncio.sleep(0)
                return first, second, analyze.call_count

        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server, "_protocol_analysis_model", return_value=Mock()):
            first, second, calls = asyncio.run(scenario())
        self.assertTrue(first["analysis_request_accepted"])
        self.assertTrue(second["analysis_request_deduplicated"])
        self.assertTrue(second["analysis_run"]["worker_running"])
        self.assertEqual(calls, 1)
        self.assertEqual(self.requests(), 2)


# The page harness: a passed protocol under review, a server that records
# which route each press called.
SERVER = r"""
const calls=[];let statusQueue=[];let confirmAsked=[];let confirmAnswer=true;
globalThis.confirm=message=>{confirmAsked.push(message);return confirmAnswer;};
let reviewExtra={};
const passed=()=>({protocol_id:"p-head",title:"Headspace",revision_id:"pdf-1-analysis-1",analysis_status:"review_required",lifecycle_state:"ready",available_for_execution:true,analysis_available:true,readiness:{status:"guidance_ready",reasons:[]},source:{filename:"headspace.pdf",sha256:"a".repeat(64),page_count:12},sections:[],execution_blockers:[],execution_notices:[],safety_notices:[],analysis_failure:null,reanalysis:null,...reviewExtra});
const requestedAt=new Date(Date.now()-65000).toISOString();
globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url}`);
 if(url==="/api/protocols/p-head/reanalysis"&&options.method==="POST")return json({...passed(),analysis_request_accepted:true,analysis_run:{state:"analysis_pending",reanalysis:true,available_for_execution:true,worker_running:true,requested_at:new Date().toISOString()}});
 if(url==="/api/protocols/p-head/analysis"&&options.method==="POST")return json({...passed(),analysis_request_accepted:true,analysis_run:{state:"analysis_pending",reanalysis:false,worker_running:true,requested_at:new Date().toISOString()}});
 if(url==="/api/protocols/p-head/analysis/status")return json(statusQueue.length?statusQueue.shift():{state:"analyzing",reanalysis:true,available_for_execution:true,worker_running:true,requested_at:requestedAt});
 if(url==="/api/protocols/p-head/review")return json(passed());
 if(url==="/api/protocols")return json({protocols:[{...passed(),sections:undefined}]});
 if(url.startsWith("/api/workspace/"))return json({protocols:[],experiments:[],revisions:[]});
 throw new Error(`unexpected ${options.method||"GET"} ${url}`);};
const press=async()=>{node("protocol-analysis-retry").dispatch("click");await new Promise(resolve=>setTimeout(resolve,20));};
const posted=route=>calls.filter(call=>call===`POST /api/protocols/p-head/${route}`).length;
"""


class ReanalysisOnScreenTests(unittest.TestCase):
    def run_page(self, body: str) -> None:
        result = run_page_script(SERVER + body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_passed_analysis_offers_re_analysis_after_one_confirmation(self) -> None:
        self.run_page(r"""
sessionActive=false;
await loadProtocolReview("p-head");
const button=node("protocol-analysis-retry");
assert(!button.hidden,"no re-analysis button on a passed analysis");
assert(button.textContent==="다시 분석",`the button is not named 다시 분석: ${button.textContent}`);
assert(!node("protocol-start").hidden,"the start button is gone from a passed analysis");
confirmAnswer=false;await press();
assert(confirmAsked.length===1&&confirmAsked[0].includes("분석을 새로 만들어요")&&confirmAsked[0].includes("진행 중인 실험은 그대로입니다"),`the confirmation is not the one decided: ${confirmAsked}`);
assert(posted("reanalysis")===0&&posted("analysis")===0,`a declined confirmation still posted: ${calls}`);
confirmAnswer=true;await press();
stopProtocolAnalysisPolling();
assert(confirmAsked.length===2,"the second press did not ask once");
assert(posted("reanalysis")===1&&posted("analysis")===0,`the re-analysis route was not called once: ${calls}`);
const status=node("protocol-upload-status").textContent;
assert(status.includes("다시 분석 중"),`no 다시 분석 중 line: ${status}`);
assert(!node("protocol-start").hidden,"the start button went away while the re-analysis runs");
""")

    def test_a_failed_analysis_keeps_its_retry_without_a_confirmation(self) -> None:
        self.run_page(r"""
reviewExtra={analysis_status:"analysis_failed",lifecycle_state:"blocked",available_for_execution:false,analysis_available:false,revision_id:"pdf-1",analysis_failure:{code:"protocol_analysis_invalid_response",retryable:true,action:"'분석 다시 시도'를 누르면 새로 분석합니다."}};
await loadProtocolReview("p-head");
const button=node("protocol-analysis-retry");
assert(!button.hidden&&button.textContent==="분석 다시 시도",`a failed analysis lost its retry button: ${button.hidden} ${button.textContent}`);
await press();
stopProtocolAnalysisPolling();
assert(confirmAsked.length===0,`a retry after a failure asked a confirmation: ${confirmAsked}`);
assert(posted("analysis")===1&&posted("reanalysis")===0,`the retry did not call the analysis route once: ${calls}`);
""")

    def test_a_failed_re_analysis_says_the_passed_analysis_still_runs(self) -> None:
        self.run_page(r"""
sessionActive=false;
reviewExtra={reanalysis:{state:"analysis_failed",requested_at:requestedAt,failure:{code:"protocol_analysis_invalid_evidence",retryable:true,action:"'다시 분석'을 눌러 다시 할 수 있습니다."},automatic_retry:null}};
await loadProtocolReview("p-head");
currentProtocolReviewId="p-head";protocolAnalysisStartedAt=Date.now()-30000;
statusQueue=[{state:"analysis_failed",failure_code:"protocol_analysis_invalid_evidence",reanalysis:true,available_for_execution:true,worker_running:false,requested_at:new Date(Date.now()-30000).toISOString()}];
await pollProtocolAnalysisStatus("p-head");
const status=node("protocol-upload-status").textContent;
assert(status.includes("다시 분석 실패")&&status.includes("분석 근거가 원문과 맞지 않음")&&status.includes("계속 실행"),`the failure line does not say the passed analysis still runs: ${status}`);
assert(!status.includes("protocol_analysis_invalid_evidence"),`the raw code is on screen: ${status}`);
assert(!node("protocol-start").hidden,"the start button is gone although the passed analysis still runs");
const panel=visibleText(node("protocol-review-content"));
assert(panel.includes("다시 분석")&&panel.includes("실패")&&panel.includes("계속 실행"),`the review panel does not say the re-analysis failed beside the pass: ${panel}`);
assert(!node("protocol-analysis-retry").hidden&&!node("protocol-analysis-retry").disabled,"the re-analysis button is not offered again");
""")

    def test_a_passed_re_analysis_says_so(self) -> None:
        self.run_page(r"""
reviewExtra={revision_id:"pdf-1-analysis-2"};
await loadProtocolReview("p-head");
currentProtocolReviewId="p-head";protocolAnalysisStartedAt=Date.now()-90000;
statusQueue=[{state:"review_required",failure_code:null,reanalysis:true,available_for_execution:true,worker_running:false,requested_at:new Date(Date.now()-90000).toISOString()}];
await pollProtocolAnalysisStatus("p-head");
const status=node("protocol-upload-status").textContent;
assert(status.includes("다시 분석 통과"),`a passed re-analysis does not say so: ${status}`);
""")

    def test_the_development_fixture_offers_no_re_analysis(self) -> None:
        self.run_page(r"""
reviewExtra={development_only:true};
await loadProtocolReview("p-head");
assert(node("protocol-analysis-retry").hidden,"the development fixture offers a re-analysis button");
""")

    def test_an_analysis_that_passed_but_cannot_run_offers_re_analysis_too(self) -> None:
        self.run_page(r"""
reviewExtra={available_for_execution:false,lifecycle_state:"blocked",execution_blockers:[{code:"no_executable_steps",message_ko:"실행할 단계를 원문에서 찾지 못했습니다.",kind:"blocking"}]};
await loadProtocolReview("p-head");
const button=node("protocol-analysis-retry");
assert(!button.hidden&&button.textContent==="다시 분석",`no re-analysis for a blocked pass: ${button.hidden} ${button.textContent}`);
""")


if __name__ == "__main__":
    unittest.main()
