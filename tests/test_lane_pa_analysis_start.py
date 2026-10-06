"""Analysis starts on upload, and a run that ended is not called again (lane PA).

Human decision 2 as changed on 2026-10-06:

* Uploading a PDF starts its analysis with the analysis role of the .env
  (VOINEY_LAB_ANALYSIS_PROVIDER, VOINEY_LAB_ANALYSIS_MODEL and
  VOINEY_LAB_ANALYSIS_REASONING). The development launcher no longer clears the
  analysis model.
* Uploading the same PDF again (same SHA-256) never calls an analysis that
  already ended. A failed one is retried only when a person presses
  "분석 다시 시도"; there is no automatic retry.
* While the analysis runs the screen says "분석 중" with the time taken; when
  it ends it says 통과 or 실패, with the reason, in Korean.
* After a pass the execution rules are unchanged. Where the server allows a
  development activation (test mode outside an operational scope), one press
  of "이 프로토콜로 시작" activates the protocol and starts the experiment; in
  an operational scope the button is not offered and only review and approval
  make a protocol runnable.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from tests.test_launcher_settings_win import NO_XAI_ROLES, _LauncherCopy
from tests.test_protocol_catalog import write_text_pdf
from tests.test_screen_cleanup import run_page_script
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

ROOT = Path(__file__).resolve().parents[1]


class DevLauncherKeepsTheAnalysisRoleTests(_LauncherCopy):
    SCRIPT = "run_dev.sh"

    def test_the_launcher_does_not_clear_the_analysis_model(self) -> None:
        text = (ROOT / "scripts" / self.SCRIPT).read_text(encoding="utf-8")
        self.assertEqual(
            re.findall(r"^\s*export (VOINEY_LAB_\w*_MODEL)=", text, re.M), [],
        )

    def test_the_dotenv_analysis_role_is_the_one_used(self) -> None:
        self._dotenv(
            "".join(f"{name}={value}\n" for name, value in NO_XAI_ROLES.items()
                    if name != "VOINEY_LAB_ANALYSIS_PROVIDER")
            + "VOINEY_LAB_ANALYSIS_PROVIDER=anthropic\n"
            "VOINEY_LAB_ANALYSIS_MODEL=claude-sonnet-5-5\n"
            "VOINEY_LAB_ANALYSIS_REASONING=high\n"
        )
        result = self._run("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("analysis_role: anthropic claude-sonnet-5-5", result.stdout)


class RunStatusTimeTests(unittest.TestCase):
    def test_the_run_status_says_when_the_analysis_was_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = initialize_protocol_store(ProtocolPersistenceSettings(True, root / "catalog"))
            try:
                catalog = ProtocolCatalog(store)
                pdf = root / "p.pdf"
                write_text_pdf(pdf, "Simple Protocol\n1. Add 500 uL buffer.", title="Simple Protocol")
                entry = catalog.register(
                    pdf, source_filename="p.pdf", media_type="application/pdf",
                ).entry
                self.assertIsNone(
                    catalog.analysis_run_status(entry.protocol_id).public_dict()["requested_at"]
                )
                catalog.request_analysis(entry.protocol_id, "analysis-" + "a" * 32)
                status = catalog.analysis_run_status(entry.protocol_id).public_dict()
                self.assertEqual(status["state"], "analysis_pending")
                self.assertRegex(status["requested_at"], r"^\d{4}-\d\d-\d\dT")
            finally:
                store.close()


# The page harness: a fake server answering the upload, analysis, status,
# review and catalog calls, recording every call.
SERVER = r"""
const calls=[];let statusQueue=[];
const reviewFor=(id,extra={})=>({protocol_id:id,title:"ANKOM",revision_id:"pdf-1",analysis_status:"analysis_failed",available_for_execution:false,readiness:{status:"analysis_required",reasons:[]},source:{filename:"ankom.pdf",sha256:"a".repeat(64),page_count:40},sections:[],analysis_failure:{code:"protocol_analysis_invalid_evidence",retryable:true,action:"실패 원인을 확인한 뒤 '분석 다시 시도'를 누르세요."},...extra});
let reviewExtra={};
function serve(upload){
 globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url}`);
  if(url.startsWith("/api/protocols?filename="))return json(upload);
  const id=upload.protocol.protocol_id;
  if(url===`/api/protocols/${id}/analysis`&&options.method==="POST")return json({...upload.protocol,analysis_status:"analyzing",analysis_request_accepted:true,analysis_run:{state:"analysis_pending"}});
  if(url===`/api/protocols/${id}/analysis/status`)return json(statusQueue.length?statusQueue.shift():{state:"analyzing"});
  if(url===`/api/protocols/${id}/review`)return json(reviewFor(id,reviewExtra));
  if(url===`/api/protocols/${id}/activate-development`&&options.method==="POST")return json({protocol_id:id,status:"active_development",available_for_execution:true});
  if(url==="/api/protocols")return json({protocols:[{...upload.protocol,title:"ANKOM",revision_id:"pdf-1",available_for_execution:reviewExtra.available_for_execution===true}]});
  if(url.startsWith("/api/workspace/"))return json({protocols:[],experiments:[],revisions:[]});
  throw new Error(`unexpected ${options.method||"GET"} ${url}`);};
}
async function upload(protocol,deduplicated){
 node("protocol-pdf").files=[{name:"ankom.pdf",type:"application/pdf"}];
 serve({deduplicated,protocol:{protocol_id:"p-ankom",source_filename:"ankom.pdf",...protocol}});
 await registerSelectedProtocol();
 stopProtocolAnalysisPolling();
}
const posted=()=>calls.filter(call=>call==="POST /api/protocols/p-ankom/analysis").length;
"""


class UploadStartsAnalysisTests(unittest.TestCase):
    def run_page(self, body: str) -> None:
        result = run_page_script(SERVER + body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_new_pdf_starts_its_analysis_on_upload(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"structured_analysis_ready",available_for_execution:false},false);
assert(posted()===1,`a new upload did not start its analysis: ${calls}`);
assert(node("protocol-upload-status").textContent.includes("분석 중"),`no 분석 중 line: ${node("protocol-upload-status").textContent}`);
""")

    def test_the_same_pdf_again_does_not_call_an_analysis_that_failed(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"analysis_failed",available_for_execution:false},true);
assert(posted()===0,`a failed analysis was called again by a re-upload: ${calls}`);
const status=node("protocol-upload-status").textContent;
assert(status.includes("분석 실패")&&status.includes("분석 근거가 원문과 맞지 않음")&&status.includes("분석 다시 시도"),`failure and its reason not shown: ${status}`);
assert(!node("protocol-analysis-retry").hidden,"the retry button is not offered");
""")

    def test_the_same_pdf_again_does_not_call_an_analysis_that_passed(self) -> None:
        self.run_page(r"""
for(const state of ["review_required","approved","active_development"]){
 calls.length=0;reviewExtra={analysis_status:state,analysis_failure:null};
 await upload({analysis_status:state,available_for_execution:false},true);
 assert(posted()===0,`${state}: an ended analysis was called again: ${calls}`);
}
""")

    def test_a_person_pressing_retry_calls_the_analysis_once(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"analysis_failed",available_for_execution:false},true);
node("protocol-analysis-retry").dispatch("click");
await new Promise(resolve=>setTimeout(resolve,20));stopProtocolAnalysisPolling();
assert(posted()===1,`retry did not call the analysis exactly once: ${calls}`);
""")

    def test_while_running_the_screen_says_analysing_and_the_time_taken(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"structured_analysis_ready",available_for_execution:false},false);
currentProtocolReviewId="p-ankom";
statusQueue=[{state:"analyzing",lifecycle_state:"analyzing",requested_at:new Date(Date.now()-65000).toISOString()}];
await pollProtocolAnalysisStatus("p-ankom");
const progress=node("protocol-analysis-progress").textContent;
assert(/분석 중/.test(progress)&&/걸린 시간 1분 0[5-7]초/.test(progress),`no running line with the time taken: ${progress}`);
assert(!/analyzing|analysis_pending|lifecycle/.test(progress),`an English state code is on screen: ${progress}`);
""")

    def test_the_end_says_passed_or_failed_with_the_reason(self) -> None:
        self.run_page(r"""
await upload({analysis_status:"structured_analysis_ready",available_for_execution:false},false);
currentProtocolReviewId="p-ankom";
statusQueue=[{state:"analysis_failed",failure_code:"protocol_analysis_invalid_evidence",requested_at:new Date(Date.now()-30000).toISOString()}];
await pollProtocolAnalysisStatus("p-ankom");
let status=node("protocol-upload-status").textContent;
assert(status.includes("분석 실패")&&status.includes("이유")&&status.includes("분석 근거가 원문과 맞지 않음"),`failure line: ${status}`);
assert(!status.includes("protocol_analysis_invalid_evidence"),`the raw code is on screen: ${status}`);
reviewExtra={analysis_status:"review_required",analysis_failure:null,development_activation_allowed:true};
currentProtocolReviewId="p-ankom";
statusQueue=[{state:"review_required",requested_at:new Date(Date.now()-90000).toISOString()}];
await pollProtocolAnalysisStatus("p-ankom");
status=node("protocol-upload-status").textContent;
assert(status.includes("분석 통과"),`pass line: ${status}`);
""")

    def test_one_press_starts_the_experiment_where_activation_is_allowed(self) -> None:
        self.run_page(r"""
let started=null;startSession=async()=>{started=node("protocol-id").value};
reviewExtra={analysis_status:"review_required",analysis_failure:null,development_activation_allowed:true};
await upload({analysis_status:"review_required",available_for_execution:false},true);
const button=node("protocol-development-activate");
assert(!button.hidden,"the start button is not offered where the server allows it");
button.dispatch("click");reviewExtra={...reviewExtra,development_activation_allowed:false,available_for_execution:true};
await new Promise(resolve=>setTimeout(resolve,30));
assert(calls.includes("POST /api/protocols/p-ankom/activate-development"),`no activation: ${calls}`);
assert(started==="p-ankom",`the experiment did not start on the activated protocol: ${started}`);
""")

    def test_the_start_button_is_named_for_what_it_does(self) -> None:
        html = (ROOT / "src" / "voiney_lab" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<button id="protocol-development-activate" type="button" hidden>이 프로토콜로 시작</button>', html)

    def test_no_start_button_where_the_server_does_not_allow_activation(self) -> None:
        self.run_page(r"""
let started=null;startSession=async()=>{started="yes"};
reviewExtra={analysis_status:"review_required",analysis_failure:null,development_activation_allowed:false};
await upload({analysis_status:"review_required",available_for_execution:false},true);
assert(node("protocol-development-activate").hidden,"a start button without the server's permission");
assert(started===null,"an experiment started without a press");
""")


if __name__ == "__main__":
    unittest.main()
