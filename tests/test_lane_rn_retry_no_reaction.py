"""Why "분석 다시 시도" looked dead, and what the screen and server do now (lane RN, 1).

What the server log of 2026-10-11 and a copy of the catalog ledger say about
PMC8250384 (``protocol-7de8…``, failed on 10/10 with
``protocol_analysis_invalid_response``):

* 02:42:39 one press reached the server: a ``protocol_analysis_requested``
  event, a model call, and at 02:44:38 a new failure
  (``protocol_analysis_invalid_evidence``, page 7). The press worked.
* Within the next minute the same button was pressed seven more times. Every
  POST answered 202, but the server found the run still going and started
  nothing (``analysis_request_deduplicated``); the ledger holds one request.
* The screen wrote "분석 중" into the upload box above and into the folded
  "개발 상세 정보" chip -- never into "올린 PDF 분석 결과", the box that holds
  the button. That box kept its old "분석 실패" lines and its enabled button
  for the whole two minutes, so each press looked like nothing happened; each
  press also set the clock back to 0분 00초.
* Picking another protocol before the run ended dropped the watch; coming
  back loaded the review once, which again read "분석 실패" (now for the new
  reason), with no line saying a run had ended.

Fixed here: a request that finds a run going is reported as such without
restarting the clock, the button is disabled while a run goes, picking a
protocol whose run goes resumes the watch, and the server reserves the run
slot before it records the request so two presses at once start one run.
Decision 4 adds the notice line inside the box itself.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from tests.test_screen_cleanup import run_page_script
from voiney_lab import server
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

ANALYZE = "voiney_lab.protocol_catalog.analyze_protocol_extraction"
TEXT = "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves."


class _ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        self.pdf = self.root / "alpha.pdf"
        write_text_pdf(self.pdf, TEXT, title="Protocol Alpha")
        store = initialize_protocol_store(self.settings)
        try:
            self.protocol_id = ProtocolCatalog(store).register(
                self.pdf, source_filename="alpha.pdf", media_type="application/pdf",
            ).entry.protocol_id
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

    def event_types(self) -> list[str]:
        catalog, opened = self.open_catalog()
        try:
            return [event.event_type for event in opened.list_events(self.protocol_id)]
        finally:
            opened.close()


class RequestWhileARunGoesTests(_ServerCase):
    def test_a_second_request_while_the_run_goes_starts_nothing_and_says_so(self) -> None:
        release = threading.Event()
        seen: dict = {}

        def slow_analysis(*_args, **_kwargs):
            release.wait(timeout=10)
            return self.draft()

        async def scenario():
            first = await server.trigger_protocol_analysis(self.protocol_id)
            # The run is in its worker thread, waiting on the fake model.
            for _ in range(200):
                if "protocol_analysis_started" in self.event_types():
                    break
                await asyncio.sleep(0.02)
            second = await server.trigger_protocol_analysis(self.protocol_id)
            seen["status_while_running"] = server.get_protocol_analysis_status(self.protocol_id)
            release.set()
            await server._PROTOCOL_ANALYSIS_TASKS[self.protocol_id]
            await asyncio.sleep(0)
            seen["status_after"] = server.get_protocol_analysis_status(self.protocol_id)
            return first, second

        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server, "_protocol_analysis_model", return_value=Mock()), \
                patch(ANALYZE, side_effect=slow_analysis) as analyze:
            first, second = asyncio.run(scenario())
        self.assertTrue(first["analysis_request_accepted"])
        self.assertTrue(second["analysis_request_deduplicated"])
        self.assertNotIn("analysis_request_accepted", second)
        self.assertEqual(second["analysis_run"]["state"], "analyzing")
        self.assertEqual(seen["status_while_running"]["state"], "analyzing")
        # The status says a worker holds the run, so the screen can tell a
        # run that goes from one a restart left half-written in the ledger.
        self.assertTrue(first["analysis_run"]["worker_running"])
        self.assertTrue(second["analysis_run"]["worker_running"])
        self.assertTrue(seen["status_while_running"]["worker_running"])
        self.assertEqual(seen["status_after"]["state"], "review_required")
        self.assertFalse(seen["status_after"]["worker_running"])
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(self.event_types().count("protocol_analysis_requested"), 1)

    def test_two_requests_at_once_start_one_run(self) -> None:
        # Two presses whose requests cross: the slot is taken before the
        # ledger is written, so the second one finds a run going.
        async def to_thread(function, /, *args, **kwargs):
            await asyncio.sleep(0)
            return function(*args, **kwargs)

        async def scenario():
            answers = await asyncio.gather(
                server.trigger_protocol_analysis(self.protocol_id),
                server.trigger_protocol_analysis(self.protocol_id),
            )
            # The deduplicated projection now yields to a worker. The fake
            # analysis may finish (and release its slot) before both replies.
            task = server._PROTOCOL_ANALYSIS_TASKS.get(self.protocol_id)
            if task is not None:
                await task
            await asyncio.sleep(0)
            return answers

        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server.asyncio, "to_thread", side_effect=to_thread), \
                patch.object(server, "_protocol_analysis_model", return_value=Mock()), \
                patch(ANALYZE, side_effect=lambda *a, **k: self.draft()) as analyze:
            answers = asyncio.run(scenario())
            after = server.get_protocol_analysis_status(self.protocol_id)
        accepted = [answer for answer in answers if answer.get("analysis_request_accepted")]
        deduplicated = [answer for answer in answers if answer.get("analysis_request_deduplicated")]
        self.assertEqual((len(accepted), len(deduplicated)), (1, 1))
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(self.event_types().count("protocol_analysis_requested"), 1)
        self.assertEqual(after["state"], "review_required")
        self.assertFalse(after["worker_running"])
        self.assertNotIn(self.protocol_id, server._PROTOCOL_ANALYSIS_TASKS)

    def test_a_run_a_restart_left_half_written_reads_as_not_running(self) -> None:
        catalog, opened = self.open_catalog()
        try:
            catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
            opened.append_event(
                "analysis-started-analysis-" + "a" * 32, self.protocol_id, 1,
                "protocol_analysis_started",
                {"status": "analyzing", "analysis_id": "analysis-" + "a" * 32})
        finally:
            opened.close()
        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog):
            status = server.get_protocol_analysis_status(self.protocol_id)
        self.assertEqual(status["state"], "analyzing")
        self.assertFalse(status["worker_running"])

    def test_a_request_that_fails_to_record_frees_the_slot(self) -> None:
        def broken_catalog():
            raise RuntimeError("catalog down")

        async def scenario():
            with self.assertRaises(Exception):
                await server.trigger_protocol_analysis(self.protocol_id)

        with patch.object(server, "_open_protocol_catalog", side_effect=broken_catalog):
            asyncio.run(scenario())
        self.assertNotIn(self.protocol_id, server._PROTOCOL_ANALYSIS_TASKS)


# The page harness: a failed protocol under review, a server that accepts
# the first analysis request and reports the run as already going for the
# next ones, as the live server did on 2026-10-11.
SERVER = r"""
const calls=[];let statusQueue=[];let posted=0;
let reviewExtra={};
const reviewFor=(id)=>({protocol_id:id,title:"PMC8250384",revision_id:"pdf-1",analysis_status:"analysis_failed",lifecycle_state:"blocked",available_for_execution:false,readiness:{status:"analysis_required",reasons:[]},source:{filename:"pmc.pdf",sha256:"a".repeat(64),page_count:17},sections:[],analysis_failure:{code:"protocol_analysis_invalid_response",retryable:true,action:"'분석 다시 시도'를 누르면 새로 분석합니다."},...reviewExtra});
const requestedAt=new Date(Date.now()-65000).toISOString();
globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url}`);
 if(url==="/api/protocols/p-pmc/analysis"&&options.method==="POST"){posted+=1;
  if(posted===1)return json({protocol_id:"p-pmc",analysis_status:"analysis_failed",lifecycle_state:"analysis_pending",analysis_request_accepted:true,analysis_run:{state:"analysis_pending",requested_at:new Date().toISOString()}});
  return json({protocol_id:"p-pmc",analysis_status:"analysis_failed",lifecycle_state:"analyzing",analysis_request_deduplicated:true,analysis_run:{state:"analyzing",requested_at:requestedAt}});}
 if(url==="/api/protocols/p-pmc/analysis/status")return json(statusQueue.length?statusQueue.shift():{state:"analyzing",lifecycle_state:"analyzing",requested_at:requestedAt});
 if(url==="/api/protocols/p-pmc/review")return json(reviewFor("p-pmc"));
 if(url==="/api/protocols")return json({protocols:[{protocol_id:"p-pmc",title:"PMC8250384",revision_id:"pdf-1",analysis_status:"analysis_failed",lifecycle_state:reviewExtra.lifecycle_state||"blocked",available_for_execution:false}]});
 if(url.startsWith("/api/workspace/"))return json({protocols:[],experiments:[],revisions:[]});
 throw new Error(`unexpected ${options.method||"GET"} ${url}`);};
const press=async()=>{node("protocol-analysis-retry").dispatch("click");await new Promise(resolve=>setTimeout(resolve,20));};
"""


class RetryPressOnScreenTests(unittest.TestCase):
    def run_page(self, body: str) -> None:
        result = run_page_script(SERVER + body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_second_press_while_the_run_goes_keeps_the_clock_and_says_so(self) -> None:
        self.run_page(r"""
await loadProtocolReview("p-pmc");
await press();
const firstStart=protocolAnalysisStartedAt;
await new Promise(resolve=>setTimeout(resolve,30));
await press();
stopProtocolAnalysisPolling();
assert(posted===2,`two presses did not post twice: ${calls}`);
assert(protocolAnalysisStartedAt===firstStart,`the second press set the clock back: ${firstStart} -> ${protocolAnalysisStartedAt}`);
const status=node("protocol-upload-status").textContent,progress=node("protocol-analysis-progress").textContent;
assert(status.includes("이미 분석 중"),`the second press does not say the run already goes: ${status}`);
assert(/걸린 시간 1분 0[5-7]초/.test(progress),`the time taken is not the run's own: ${progress}`);
assert(!/deduplicated|analyzing/.test(status+progress),`a code is on screen: ${status} ${progress}`);
""")

    def test_the_button_is_disabled_while_the_run_goes_and_back_when_it_ends(self) -> None:
        self.run_page(r"""
await loadProtocolReview("p-pmc");
const button=node("protocol-analysis-retry");
assert(!button.hidden&&!button.disabled,"the retry button is not offered for a failed analysis");
await press();
assert(button.disabled,"the button stays pressable while the run goes");
statusQueue=[{state:"analysis_failed",failure_code:"protocol_analysis_invalid_evidence",requested_at:requestedAt}];
await pollProtocolAnalysisStatus("p-pmc");
assert(!button.disabled&&!button.hidden,"the button is not offered again after the run ended");
""")

    def test_picking_a_protocol_whose_run_goes_resumes_the_watch(self) -> None:
        self.run_page(r"""
reviewExtra={lifecycle_state:"analyzing"};
node("protocol-id").value="p-pmc";
onProtocolSelectionChanged();
await new Promise(resolve=>setTimeout(resolve,30));
const watching=protocolAnalysisPollHandle!==null;
stopProtocolAnalysisPolling();
assert(watching,"a run that goes is not watched after the protocol is picked");
assert(calls.some(call=>call==="GET /api/protocols/p-pmc/analysis/status"),`the run status was not read: ${calls}`);
assert(node("protocol-analysis-retry").disabled,"the button is pressable while the run goes");
const status=node("protocol-upload-status").textContent;
assert(status.includes("분석 중"),`no 분석 중 line after picking the protocol: ${status}`);
""")

    def test_a_run_the_server_no_longer_holds_frees_the_button_and_says_so(self) -> None:
        self.run_page(r"""
reviewExtra={lifecycle_state:"analyzing"};
statusQueue=[{state:"analyzing",lifecycle_state:"analyzing",requested_at:requestedAt,worker_running:false}];
// Picking the protocol watches the run again; the first status read says no worker holds it.
await loadProtocolReview("p-pmc");
await new Promise(resolve=>setTimeout(resolve,30));
const watching=protocolAnalysisPollHandle!==null;
stopProtocolAnalysisPolling();
assert(!watching,"a run the server no longer holds is still watched");
assert(!node("protocol-analysis-retry").disabled,"the button stays off for a run the server no longer holds");
const status=node("protocol-upload-status").textContent;
assert(status.includes("멈춰")&&status.includes("분석 다시 시도"),`no line about the interrupted run: ${status}`);
assert(!/worker_running|analyzing/.test(status),`a code is on screen: ${status}`);
""")

    def test_a_press_still_posts_once_and_watches(self) -> None:
        self.run_page(r"""
await loadProtocolReview("p-pmc");
await press();
const watching=protocolAnalysisPollHandle!==null;
stopProtocolAnalysisPolling();
assert(posted===1,`one press did not post once: ${calls}`);
assert(watching,"the run is not watched after the press");
""")


if __name__ == "__main__":
    unittest.main()
