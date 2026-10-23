"""Drive links run the production page's existing registration/analysis flow."""
from __future__ import annotations

import unittest

from tests.test_lane_ia_upload import run_flow
from tests.test_screen_cleanup import INDEX


class ProtocolLinkScreenTests(unittest.TestCase):
    def test_public_link_reuses_analysis_progress_and_never_starts_the_experiment(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
serve([["/api/protocols/import-link",call=>{catalogEntry=REGISTERED("structured_analysis_ready");return {protocol:catalogEntry,deduplicated:false};}],...flowAnswers()]);
const url="https://docs.google.com/document/d/synthetic-public/view";
node("protocol-link-url").value=`  ${url}  `;
await registerProtocolLink();await settle();
const imported=calls.filter(call=>call.url==="/api/protocols/import-link");
assert(imported.length===1&&imported[0].method==="POST","one server import");
assert(JSON.stringify(imported[0].body)===JSON.stringify({url}),"the browser chose an authority or rewrote the link");
assert(step()==="progress","the existing analysis progress was not shown");
assert(calls.filter(call=>call.url==="/api/protocols/p-9/analysis"&&call.method==="POST").length===1,"analysis was not requested once");
catalogEntry=REGISTERED("ready");statusAnswer={state:"review_required",worker_running:false,requested_at:statusAnswer.requested_at};
await pollProtocolAnalysisStatus("p-9");await settle();
assert(step()==="result"&&node("upload-result-title").textContent==="바로 쓸 수 있어요","server result was not rendered");
assert(sessionActive===false&&acceptedSessionConfiguration===null,"import started a voice experiment");
assert(node("protocol-link-url").value==="","the successful link stayed in the input");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_link_refusal_is_server_text_and_the_address_can_be_corrected(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
const message="이 파일은 비공개라 가져올 수 없어요. 파일을 내려받아 올려 주세요.";
serve([["/api/protocols/import-link",json({detail:{code:"link_private",message}},false)],...flowAnswers()]);
const url="https://drive.google.com/file/d/synthetic-private/view";node("protocol-link-url").value=url;
await registerProtocolLink();
assert(step()==="pick","the refusal left the page waiting");
assert(node("protocol-upload-status").textContent===message,"the server's explanation was replaced");
assert(node("protocol-link-url").value===url&&!node("protocol-link-import").disabled,"the address cannot be corrected");
assert(!calls.some(call=>call.url.endsWith("/analysis")&&call.method==="POST"),"a refused link started analysis");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_server_text_is_never_inserted_as_html(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
const message='<img src=x onerror="throw new Error()">';
serve([["/api/protocols/import-link",json({detail:{code:"link_private",message}},false)],...flowAnswers()]);
node("protocol-link-url").value="https://drive.google.com/file/d/synthetic/view";
await registerProtocolLink();
assert(node("protocol-upload-status").textContent===message,"the text was not preserved");
assert(node("protocol-upload-status").children.length===0,"the response became HTML");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_busy_and_running_inputs_cannot_submit_an_import(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
const before=calls.length;node("protocol-link-url").value=" ";await registerProtocolLink();
assert(calls.length===before,"an empty address was submitted");
let finish;
serve([["/api/protocols/import-link",()=>new Promise(resolve=>{finish=resolve;})],...flowAnswers()]);
node("protocol-link-url").value="https://drive.google.com/file/d/synthetic/view";
const pending=registerProtocolLink();await settle();
assert(node("protocol-link-import").disabled&&node("protocol-link-url").disabled,"import controls stayed active");
await registerProtocolLink();node("protocol-pdf").files=[{name:"second.pdf",type:"application/pdf"}];await registerSelectedProtocol();
assert(calls.filter(call=>call.url==="/api/protocols/import-link").length===1,"duplicate import");
assert(!calls.some(call=>call.url.includes("?filename=")),"a file upload raced the import");
finish(json({detail:{code:"link_timeout",message:"가져오지 못했어요."}},false));await pending;
startRun();const checkpoint=JSON.stringify(curatedProtocolState);renderState("LISTENING");
const count=calls.length;await registerProtocolLink();
assert(calls.length===count&&JSON.stringify(curatedProtocolState)===checkpoint,"an import changed a running experiment");
assert(node("protocol-link-import").disabled,"running import button");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_response_from_an_older_browser_generation_is_discarded(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
let finish;
serve([["/api/protocols/import-link",()=>new Promise(resolve=>{finish=resolve;})],...flowAnswers()]);
node("protocol-link-url").value="https://drive.google.com/file/d/synthetic/view";
const pending=registerProtocolLink();await settle();sessionGeneration++;
finish(json({protocol:REGISTERED("structured_analysis_ready"),deduplicated:false}));await pending;
assert(uploadFlow.protocolId===null&&!protocolCatalog.has("p-9"),"a stale response populated the new session");
assert(!calls.some(call=>call.url.endsWith("/analysis")&&call.method==="POST"),"stale analysis request");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_network_and_non_json_failures_release_the_form_without_registration(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
const url="https://drive.google.com/file/d/synthetic/view";
for(const answer of [()=>{throw new Error("synthetic network failure");},()=>({ok:false,status:502,headers:{get:()=>"text/html"},json:async()=>{throw new Error("not JSON");}})]){
 serve([["/api/protocols/import-link",answer],...flowAnswers()]);node("protocol-link-url").value=url;
 await registerProtocolLink();
 assert(step()==="pick"&&!isRegisteringProtocol&&!node("protocol-link-import").disabled,"the failed request left a busy form");
 assert(node("protocol-link-url").value===url&&uploadFlow.protocolId===null,"failure registered or lost the input");
 assert(node("protocol-upload-status").textContent.length>0,"no recovery message");
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_ready_protocol_is_shown_without_reanalysis(self) -> None:
        result = run_flow(r"""
await boot(flowAnswers());go("#/upload");
const suggestion="같은 파일의 검토본을 확인해 주세요.";
serve([["/api/protocols/import-link",()=>{catalogEntry=REGISTERED("ready");return {protocol:catalogEntry,deduplicated:true,review_suggestion:{message_ko:suggestion}};}],...flowAnswers()]);
node("protocol-link-url").value="https://drive.google.com/file/d/synthetic/view";
await registerProtocolLink();
assert(step()==="result","the existing protocol was not shown");
assert(node("protocol-duplicate-notice").textContent===suggestion,"duplicate suggestion was lost");
assert(!calls.some(call=>call.url.endsWith("/analysis")&&call.method==="POST"),"duplicate analysis");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_link_form_has_labels_and_uses_the_existing_upload_status(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        form = html.split('<form id="protocol-link-form"', 1)[1].split('</form>', 1)[0]
        self.assertIn('for="protocol-link-url"', form)
        self.assertIn('type="url"', form)
        self.assertIn('type="submit"', form)
        self.assertIn('aria-describedby="protocol-link-help protocol-upload-status"', form)
