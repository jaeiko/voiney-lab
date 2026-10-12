"""The analysis notice stands in the box that holds the button (lane RN, 4).

Human decision 4 of 2026-10-11. Until now the start, progress, pass and
failure of an analysis were written into the upload box above and into the
folded "개발 상세 정보" chip; the box a person presses "분석 다시 시도" or
"다시 분석" in ("올린 PDF 분석 결과") showed nothing, which is how the press
looked dead (decision 1). Now:

* the same notice stands in that box too, with the time taken counted up
  every second while the run goes, and the pass or the failure with its
  reason when it ends;
* the wording of an automatic retry is the server's: the run status carries
  the catalog's pipeline line, so a retry sent for an answer cut at the
  output limit (lane AQ, decision 4) says so and names the reasoning level
  it asked, instead of the screen's own "형식에 맞지 않아" sentence;
* picking another protocol takes the line away, coming back restores it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_lane_rn_retry_no_reaction import SERVER as PRESS_SERVER
from tests.test_protocol_catalog import write_text_pdf
from tests.test_screen_cleanup import run_page_script
from voiney_lab import server
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

TEXT = "Protocol Alpha\nSection preparation\n1. Add solution.\nWear gloves."
FIRST = "analysis-" + "a" * 32


class RunStatusCarriesThePipelineLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, root / "catalog")
        pdf = root / "alpha.pdf"
        write_text_pdf(pdf, TEXT, title="Protocol Alpha")
        store = initialize_protocol_store(self.settings)
        try:
            self.protocol_id = ProtocolCatalog(store).register(
                pdf, source_filename="alpha.pdf", media_type="application/pdf").entry.protocol_id
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

    def append(self, event_id: str, event_type: str, payload: dict) -> None:
        catalog, opened = self.open_catalog()
        try:
            opened.append_event(event_id, self.protocol_id, 1, event_type, payload)
        finally:
            opened.close()

    def status(self) -> dict:
        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog):
            return server.get_protocol_analysis_status(self.protocol_id)

    def test_the_status_says_the_stage_line_while_the_run_goes(self) -> None:
        catalog, opened = self.open_catalog()
        try:
            catalog.request_analysis(self.protocol_id, FIRST)
        finally:
            opened.close()
        status = self.status()
        self.assertEqual(status["state"], "analysis_pending")
        self.assertEqual(status["pipeline"]["stage"], "analysis")
        self.assertFalse(status["pipeline"]["blocked"])
        self.assertIn("분석 중", status["pipeline"]["message"])

    def test_a_retry_for_a_cut_answer_is_worded_by_the_server(self) -> None:
        catalog, opened = self.open_catalog()
        try:
            catalog.request_analysis(self.protocol_id, FIRST)
        finally:
            opened.close()
        self.append(f"analysis-started-{FIRST}", "protocol_analysis_started",
                    {"status": "analyzing", "analysis_id": FIRST})
        self.append("analysis-failed-1", "protocol_analysis_failed",
                    {"status": "failed", "failure_code": "protocol_analysis_invalid_response",
                     "truncated": True})
        self.append(f"analysis-retry-{FIRST}-retry-1", "protocol_analysis_retry_started",
                    {"status": "analyzing", "analysis_id": f"{FIRST}-retry-1", "retry_of": FIRST,
                     "attempt": 1, "limit": 1, "reason_code": "protocol_analysis_invalid_response",
                     "authority": "automatic_invalid_response_retry", "truncated": True,
                     "reasoning_effort": "medium", "previous_reasoning_effort": "high"})
        status = self.status()
        self.assertEqual(status["state"], "analyzing")
        self.assertEqual(status["automatic_retry"]["state"], "in_progress")
        self.assertTrue(status["automatic_retry"]["truncated"])
        message = status["pipeline"]["message"]
        self.assertIn("출력 한도에서 잘려", message)
        self.assertIn("high→medium", message)
        self.assertIn("다시 시도 중(1/1)", message)
        self.assertNotIn("형식에 맞지 않아", message)
        self.append("analysis-failed-2", "protocol_analysis_failed",
                    {"status": "failed", "failure_code": "protocol_analysis_invalid_response",
                     "truncated": True})
        failed = self.status()
        self.assertEqual(failed["state"], "analysis_failed")
        self.assertIn("출력 한도에서 잘려", failed["pipeline"]["message"])
        self.assertIn("자동 재시도(1/1)도 실패", failed["pipeline"]["message"])
        self.assertTrue(failed["pipeline"]["blocked"])


class NoticeInTheBoxTests(unittest.TestCase):
    def run_page(self, body: str) -> None:
        result = run_page_script(PRESS_SERVER + body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_press_puts_the_running_line_and_the_time_taken_in_the_box(self) -> None:
        self.run_page(r"""
await loadProtocolReview("p-pmc");
const box=node("protocol-review-analysis-notice");
assert(box.hidden,"the box line shows before any run");
await press();
assert(!box.hidden&&box.textContent.includes("분석 중")&&/걸린 시간 \d+분 \d\d초/.test(box.textContent),`no running line in the box: ${box.hidden} ${box.textContent}`);
statusQueue=[{state:"analyzing",lifecycle_state:"analyzing",requested_at:new Date(Date.now()-125000).toISOString(),worker_running:true}];
await pollProtocolAnalysisStatus("p-pmc");
assert(/걸린 시간 2분 0[5-7]초/.test(box.textContent),`the box does not count the run's time: ${box.textContent}`);
statusQueue=[{state:"analysis_failed",failure_code:"protocol_analysis_invalid_evidence",requested_at:new Date(Date.now()-125000).toISOString(),worker_running:false}];
await pollProtocolAnalysisStatus("p-pmc");
stopProtocolAnalysisPolling();
assert(!box.hidden&&box.textContent.includes("분석 실패")&&box.textContent.includes("분석 근거가 원문과 맞지 않음")&&/걸린 시간 2분 0[5-7]초/.test(box.textContent),`the box does not say how the run ended: ${box.textContent}`);
assert(!/protocol_analysis_invalid_evidence|analysis_failed|analyzing/.test(box.textContent),`a code is in the box: ${box.textContent}`);
""")

    def test_a_pass_stands_in_the_box_too(self) -> None:
        self.run_page(r"""
await loadProtocolReview("p-pmc");
await press();
reviewExtra={analysis_status:"review_required",lifecycle_state:"ready",available_for_execution:true,analysis_available:true,analysis_failure:null};
statusQueue=[{state:"review_required",failure_code:null,requested_at:new Date(Date.now()-90000).toISOString(),worker_running:false}];
await pollProtocolAnalysisStatus("p-pmc");
stopProtocolAnalysisPolling();
const box=node("protocol-review-analysis-notice");
assert(!box.hidden&&box.textContent.includes("분석 통과")&&/걸린 시간 1분 (29|30|31)초/.test(box.textContent),`the pass is not in the box: ${box.textContent}`);
""")

    def test_a_retry_for_a_cut_answer_uses_the_server_s_words(self) -> None:
        self.run_page(r"""
await loadProtocolReview("p-pmc");
await press();
const serverLine="분석 중입니다 · 다시 시도 중(1/1). 첫 응답이 출력 한도에서 잘려 추론 수준을 한 단계 낮춰(high→medium) 같은 요청을 자동으로 한 번 다시 보냈습니다.";
statusQueue=[{state:"analyzing",lifecycle_state:"analyzing",requested_at:new Date(Date.now()-65000).toISOString(),worker_running:true,
 automatic_retry:{attempt:1,limit:1,state:"in_progress",reason_code:"protocol_analysis_invalid_response",truncated:true,reasoning_effort:"medium",previous_reasoning_effort:"high"},
 pipeline:{stage:"analysis",stage_ko:"분석",blocked:false,message:serverLine,action:null}}];
await pollProtocolAnalysisStatus("p-pmc");
const box=node("protocol-review-analysis-notice").textContent,status=node("protocol-upload-status").textContent,progress=node("protocol-analysis-progress").textContent;
for(const text of [box,status]){
 assert(text.includes("출력 한도에서 잘려")&&text.includes("high→medium"),`the server's wording is not used: ${text}`);
 assert(!text.includes("형식에 맞지 않아"),`the screen's own sentence is still used for a cut answer: ${text}`);
}
assert(progress.includes("다시 시도 중(1/1)")&&/걸린 시간 1분 0[5-7]초/.test(progress),`the progress chip lost the retry count or the time: ${progress}`);
const failedLine="분석 모델의 응답이 정해진 형식에 맞지 않아 결과를 쓰지 않았습니다. 첫 응답이 출력 한도에서 잘려 추론 수준을 한 단계 낮춰(high→medium) 같은 요청을 자동으로 한 번 다시 보냈지만 자동 재시도(1/1)도 실패했습니다.";
statusQueue=[{state:"analysis_failed",failure_code:"protocol_analysis_invalid_response",requested_at:new Date(Date.now()-125000).toISOString(),worker_running:false,
 automatic_retry:{attempt:1,limit:1,state:"failed",reason_code:"protocol_analysis_invalid_response",truncated:true,reasoning_effort:"medium",previous_reasoning_effort:"high"},
 pipeline:{stage:"analysis",stage_ko:"분석",blocked:true,message:failedLine,action:"실패 원인을 확인한 뒤 '분석 다시 시도'를 누르세요.",failure_code:"protocol_analysis_invalid_response"}}];
await pollProtocolAnalysisStatus("p-pmc");
stopProtocolAnalysisPolling();
const ended=node("protocol-review-analysis-notice").textContent;
assert(ended.includes("분석 실패")&&ended.includes("출력 한도에서 잘려")&&ended.includes("자동 재시도(1/1)도 실패")&&ended.includes("분석 다시 시도"),`the failed retry is not worded by the server: ${ended}`);
""")

    def test_without_the_server_s_line_the_screen_keeps_its_own_words(self) -> None:
        self.run_page(r"""
await loadProtocolReview("p-pmc");
await press();
statusQueue=[{state:"analyzing",lifecycle_state:"analyzing",requested_at:new Date(Date.now()-65000).toISOString(),worker_running:true,
 automatic_retry:{attempt:1,limit:1,state:"in_progress",reason_code:"protocol_analysis_invalid_response"}}];
await pollProtocolAnalysisStatus("p-pmc");
stopProtocolAnalysisPolling();
const status=node("protocol-upload-status").textContent;
assert(status.includes("다시 시도 중(1/1)")&&status.includes("형식"),`the fallback wording is gone: ${status}`);
""")

    def test_picking_another_protocol_takes_the_line_away_and_back(self) -> None:
        self.run_page(r"""
await loadProtocolReview("p-pmc");
await press();
stopProtocolAnalysisPolling();
const box=node("protocol-review-analysis-notice");
assert(!box.hidden,"no line after the press");
const other=globalThis.fetch;
globalThis.fetch=async(url,options={})=>{url=String(url);if(url==="/api/protocols/p-other/review")return json({protocol_id:"p-other",title:"Other",revision_id:"pdf-1",analysis_status:"structured_analysis_ready",lifecycle_state:"uploaded",available_for_execution:false,readiness:{status:"analysis_required",reasons:[]},source:{filename:"other.pdf",sha256:"b".repeat(64),page_count:3},sections:[]});return other(url,options);};
await loadProtocolReview("p-other");
assert(box.hidden,"another protocol's box still shows the first one's line");
await loadProtocolReview("p-pmc");
stopProtocolAnalysisPolling();
assert(!box.hidden&&box.textContent.includes("분석 중"),`coming back did not restore the line: ${box.hidden} ${box.textContent}`);
""")

    def test_the_box_has_the_line_in_the_page(self) -> None:
        html = Path(__file__).resolve().parents[1].joinpath(
            "src", "voiney_lab", "static", "index.html").read_text(encoding="utf-8")
        panel = html[html.index('id="protocol-review-panel"'): html.index('id="protocol-analysis-retry"')]
        self.assertIn('id="protocol-review-analysis-notice"', panel)


if __name__ == "__main__":
    unittest.main()
