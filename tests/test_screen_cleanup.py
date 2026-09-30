"""Researcher-screen cleanup, exercised through the production page script.

Every test here loads the whole ``<script>`` block of ``static/index.html`` into
Node with a small fake DOM and drives the production functions, so a label
that only exists in a helper cannot pass for one the page actually renders.
"""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "voiney_lab" / "static" / "index.html"

# A fake DOM just rich enough for the page script. Unlike the harnesses in
# test_frontend.py, assigning ``textContent`` drops the children, as a browser
# does, so a status line cannot keep a stale "개발 상세 정보" block.
PRELUDE = r"""
const assert=(ok,message)=>{if(!ok)throw new Error(message)};
const TURN_FIELDS=["transcript","reply","filler-status","turn-visual","turn-status","error","replay-status"];
class Element{
 constructor(tag="div"){this.tagName=tag;this.children=[];this._text="";this.hidden=false;this.disabled=false;this.value="";this.className="";this.dataset={};this.attributes={};this.listeners={};this.style={};this.open=false;this.files=[];}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join("")}
 set textContent(value){this._text=String(value??"");this.children=[]}
 get options(){return this.children}
 get firstChild(){return this.children[0]||null}
 appendChild(node){this.children.push(node);node.parentNode=this;return node}
 append(...items){for(const item of items)this.appendChild(typeof item==="string"?Object.assign(new Element("#text"),{_text:item}):item)}
 prepend(node){this.children.unshift(node);node.parentNode=this;return node}
 insertBefore(node){return this.prepend(node)}
 replaceChildren(...items){this.children=[];for(const item of items)this.appendChild(item)}
 remove(){this.removed=true;if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this)}
 setAttribute(name,value){this.attributes[name]=String(value)}
 getAttribute(name){return this.attributes[name]}
 removeAttribute(name){delete this.attributes[name]}
 addEventListener(kind,handler){(this.listeners[kind]||=[]).push(handler)}
 dispatch(kind){for(const handler of this.listeners[kind]||[])handler({target:this})}
 cloneNode(){return Object.assign(new Element(this.tagName),{_text:this._text,className:this.className})}
 scrollTo(){}
 set innerHTML(_){
  this.children=[];const title=new Element("b");this.appendChild(title);
  for(const name of TURN_FIELDS){const item=new Element();item.className=name;if(name==="transcript")item._text="듣는 중…";if(name==="turn-status")item._text="듣는 중…";this.appendChild(item);}
  const details=new Element("details");details.className="turn-diagnostics";const body=new Element();body.className="turn-diagnostics-body";
  for(const name of ["server-operation","tools","stats"]){const item=new Element();item.className=name;body.appendChild(item);}
  details.appendChild(body);this.appendChild(details);
 }
 querySelector(selector){
  const wanted=selector.replace(/^.*[ >]/,"").replace(/^\./,"");
  for(const child of this.children){if(String(child.className||"").split(" ").includes(wanted))return child;const found=child.querySelector(selector);if(found)return found;}
  return null;
 }
 querySelectorAll(){return []}
}
const ids=new Map();
globalThis.document={getElementById:id=>{if(!ids.has(id)){const node=new Element();node.id=id;ids.set(id,node);}return ids.get(id)},createElement:tag=>new Element(tag),querySelector:()=>null,querySelectorAll:()=>[]};
globalThis.location={protocol:"http:",host:"test",origin:"http://test"};
const pageListeners={};
globalThis.addEventListener=(kind,handler)=>{(pageListeners[kind]||=[]).push(handler)};
class WS{static OPEN=1;static CLOSING=2;constructor(){this.readyState=1;this.sent=[]}send(value){this.sent.push(value)}close(){this.readyState=3}}
globalThis.WebSocket=WS;
Object.defineProperty(globalThis,"navigator",{value:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})}},configurable:true});
globalThis.AudioContext=class{};
globalThis.fetch=async()=>{throw new Error("unexpected network call")};
const node=id=>document.getElementById(id);
const isDev=item=>String(item.className||"").split(" ").includes("dev-code-details");
function visibleText(item){if(isDev(item))return "";return item._text+" "+item.children.map(visibleText).join(" ")}
function devText(item){if(isDev(item))return item.textContent;return item.children.map(devText).join(" ")}
function withoutCode(text,code){return !new RegExp(`(^|[^A-Za-z_])${code}([^A-Za-z_]|$)`).test(text)}
const json=(payload,ok=true)=>({ok,headers:{get:()=>"application/json"},json:async()=>payload});
"""


def run_page_script(body: str) -> subprocess.CompletedProcess:
    html = INDEX.read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = (
        PRELUDE + script
        + "\n(async()=>{\n" + body
        + "\n})().catch(error=>{console.error(error);process.exit(1)});\n"
    )
    return subprocess.run(
        ["node", "-"], cwd=ROOT, text=True, input=harness,
        capture_output=True,
    )


class ServerCodeTranslationTests(unittest.TestCase):
    """Item 1: status codes read as Korean; the codes stay in 개발 상세 정보."""

    def test_review_panel_translates_codes_and_keeps_them_in_dev_details(self):
        result = run_page_script(r"""
const review={protocol_id:"protocol-x",title:"In-gel",revision_id:"pdf-1-analysis-1",lifecycle_state:"blocked",readiness_status:"analysis_required",available_for_execution:false,
 source:{filename:"in-gel.pdf",sha256:"a".repeat(64),page_count:9},
 readiness:{status:"analysis_required",label:"Protocol 분석 필요",reasons:[
  {code:"unresolved_ambiguity",message:"A source ambiguity remains unresolved."},
  {code:"no_declared_safety_warnings",message:"A reviewer must confirm this Protocol's safety warnings before execution."},
  {code:"reason_from_a_newer_server",message:"Something new."}]},
 analysis_failure:{code:"protocol_analysis_invalid_evidence",retryable:true,action:"Review the failure code and explicitly retry analysis."},
 gates:{parsing:"passed",structural_readiness:"blocked",hazard_review:"review_required",human_approval:"pending",operational_authorization:"blocked"},
 constructs:[{construct_type:"SourceAmbiguity",source_text:"Two volumes are stated.",resolved:false,evidence:{source_page_number:3,source_excerpt:"20 µL or 30 µL"}}],
 outstanding_blockers:[
  {code:"unresolved_ambiguity",kind:"reviewer_can_clear",reviewer_action:"resolve_ambiguity",already_acknowledged:false,decision_options:["single_statement_is_authoritative","statements_are_distinct"],clearing_decision:"single_statement_is_authoritative",citable_segments:[{segment_id:"seg-1",segment_index:0,source_page_number:3,excerpt:"20 µL"}]},
  {code:"no_declared_safety_warnings",kind:"reviewer_can_clear",reviewer_action:"acknowledge_gate",already_acknowledged:false},
  {code:"unsupported_repeat_until",kind:"capability_required",reviewer_action:null,already_acknowledged:false}],
 reviewer_findings:[{kind:"gate_acknowledged",reason_code:"no_declared_safety_warnings",actor_principal_id:"reviewer-a",actor_role:"reviewer",recorded_at:"2026-09-30T00:00:00Z"}],
 sections:[]};
renderProtocolReview(review);
const hosts=["protocol-review-content","protocol-blockers","protocol-findings"].map(node);
const visible=hosts.map(visibleText).join(" "),dev=hosts.map(devText).join(" ");
for(const code of ["analysis_required","blocked","unresolved_ambiguity","no_declared_safety_warnings","statements_are_distinct","single_statement_is_authoritative","review_required","reviewer_can_clear","capability_required","gate_acknowledged","protocol_analysis_invalid_evidence","SourceAmbiguity","structural_readiness","reason_from_a_newer_server"]){
 assert(withoutCode(visible,code),`raw code ${code} is still on screen: ${visible}`);
 assert(!withoutCode(dev,code),`raw code ${code} is missing from 개발 상세 정보: ${dev}`);
}
for(const label of ["프로토콜 분석 필요","차단됨 · 조치 필요","원문의 모호한 부분이 해결되지 않음","안전 경고를 검토자가 확인해야 함","두 진술은 서로 다른 내용임","한 진술이 기준임","검토자가 해제 가능","분석 근거가 원문과 맞지 않음","원문의 모호한 부분","구조 실행 준비 · 차단","위험 검토 · 검토 필요","검토자"])assert(visible.includes(label),`label missing: ${label}`);
assert(visible.includes("확인되지 않은 상태"),"an unknown code was not marked as unconfirmed");
assert(visible.includes("A source ambiguity remains unresolved."),"the server's own reason message was dropped");
const select=node("protocol-blockers").children[0].children.find(item=>item.tagName==="select");
assert(select&&select.children.map(option=>option.value).join(",")==="single_statement_is_authoritative,statements_are_distinct","the decision sent to the server must stay the raw code");
renderProtocolReview({...review,lifecycle_state:"review_required",readiness:{status:"guidance_ready",label:"안내 준비 완료",reasons:[]},outstanding_blockers:[],reviewer_findings:[],analysis_failure:null,constructs:[]});
const ready=visibleText(node("protocol-review-content"));
assert(ready.includes("안내 준비 완료")&&withoutCode(ready,"guidance_ready"),`guidance_ready not translated: ${ready}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_records_lists_and_status_lines_translate_codes(self):
        result = run_page_script(r"""
renderExperimentReportList([{report_id:"ER-1",status:"in_progress"},{report_id:"ER-2",status:"status_from_a_newer_server"}]);
renderExperimentReportEvents([{event_type:"blocked",step_label:"3",created_at:"2026-09-30T00:00:00Z",payload:{timer:{completion_state:"not_started"}}}]);
const reports=[node("experiment-report-list"),node("experiment-report-events")];
const reportText=reports.map(visibleText).join(" "),reportDev=reports.map(devText).join(" ");
for(const code of ["in_progress","blocked","not_started","status_from_a_newer_server"]){assert(withoutCode(reportText,code),`report code ${code} on screen: ${reportText}`);assert(!withoutCode(reportDev,code),`report code ${code} not in dev details`);}
assert(reportText.includes("진행 중")&&reportText.includes("진행 차단")&&reportText.includes("시작 전")&&reportText.includes("확인되지 않은 상태"),`report labels missing: ${reportText}`);
renderExperimentTimeline({timeline:[{event_type:"observation_recorded",step_label:"2",created_at:"2026-09-30T00:00:00Z",observation:{category:"appearance",content:"시료가 탁함"}}]});
const timeline=node("experiment-event-timeline");
assert(visibleText(timeline).includes("외관 · 시료가 탁함")&&withoutCode(visibleText(timeline),"appearance")&&devText(timeline).includes("appearance"),`observation category not translated: ${visibleText(timeline)}`);
acceptedSessionConfiguration={configuration_id:1,mode:"cascade"};
await onMessage({data:JSON.stringify({type:"experiment.report.state",configuration_id:1,generation:0,report:{report_id:"ER-1",status:"completed",event_count:2,anomaly_count:0,blocker_count:0,reports:[],events:[]}})},sessionGeneration,socket);
const last=node("last-report-state");
assert(visibleText(last).includes("완료 · 이벤트 2")&&withoutCode(visibleText(last),"completed")&&devText(last).includes("completed"),`report status not translated: ${visibleText(last)}`);
await onMessage({data:JSON.stringify({type:"report.status",report_id:"SR-1",report_status:"lookup_failed",attempts:1})},sessionGeneration,socket);
assert(visibleText(last).includes("상태 조회 실패 · 시도 1회")&&withoutCode(visibleText(last),"lookup_failed")&&devText(last).includes("lookup_failed"),`safety report status not translated: ${visibleText(last)}`);
globalThis.fetch=async url=>{assert(String(url).startsWith("/api/workspace/protocol-library"),`unexpected ${url}`);return json({protocols:[{family_id:"f-1",title:"Local",revision_number:1,connector_kind:"local_pdf",owner:"Lab",department:"Bio",approval_state:null,risk_state:"review_required",tags:[]}]})};
await loadQuickProtocolLibrary();
const library=node("quick-library-results");
for(const code of ["local_pdf","review_required"]){assert(withoutCode(visibleText(library),code),`library code ${code} on screen: ${visibleText(library)}`);assert(devText(library).includes(code),`library code ${code} not in dev details`);}
assert(visibleText(library).includes("로컬 PDF")&&visibleText(library).includes("검토 필요"),`library labels missing: ${visibleText(library)}`);
node("protocols-io-connector").value="connector-1";node("protocols-io-identifier").value="10.17504/protocols.io.x";
globalThis.fetch=async()=>json({detail:"authorization_denied"},false);
await importProtocolsIo();
const status=node("protocols-io-status");
assert(visibleText(status).includes("이 작업을 할 권한이 없습니다.")&&withoutCode(visibleText(status),"authorization_denied")&&devText(status).includes("authorization_denied"),`error detail not translated: ${visibleText(status)}`);
globalThis.fetch=async()=>json({detail:"detail_from_a_newer_server"},false);
await importProtocolsIo();
assert(visibleText(status).includes("가져오기 실패")&&withoutCode(visibleText(status),"detail_from_a_newer_server")&&devText(status).includes("detail_from_a_newer_server"),`unknown detail was not kept in dev details: ${visibleText(status)}`);
globalThis.fetch=async()=>json({inbox_state:"new",revision_id:"rev-1"});
await importProtocolsIo();
assert(visibleText(status).includes("새 검토 초안 · rev-1")&&!devText(status).includes("authorization_denied"),`success status kept a stale code: ${status.textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class TurnCardStatusTests(unittest.TestCase):
    """Item 2: the turn card says what happened; the route marker is diagnostic."""

    def test_route_marker_moves_to_diagnostics_and_observation_holds_are_named(self):
        result = run_page_script(r"""
acceptedSessionConfiguration={configuration_id:5,mode:"cascade",language:"ko",protocol_id:"p-1",revision_id:"r-1"};sessionActive=true;
const base={attached:true,protocol_id:"p-1",revision_id:"r-1",display_name:"In-gel",development_only:true,readiness_status:"guidance_ready",active:true,current_step_label:"7",current_step_id:"step-7",total_steps:25,at_final_step:false,block_reason:null,revision:3,source_page_refs:[5],visual_assets:[],visual_status:"unavailable"};
const send=payload=>onMessage({data:JSON.stringify({configuration_id:5,...payload})},sessionGeneration,socket);
async function turn(id,{blockReason,done,terminal,extra=[]}){
 await send({type:"speech.start",turn_id:id,generation:id});
 await send({type:"transcript",turn_id:id,generation:id,text:"다음 단계로 넘어가 줘"});
 let revision=0;
 for(const state of ["routing","checking_protocol","synthesizing","playing"])await send({type:"turn.state",turn_id:id,generation:id,revision:++revision,state,route:"curated_protocol"});
 if(blockReason!==undefined)await send({type:"protocol.fixture.state",turn_id:id,generation:id,action:"next",state:{...base,block_reason:blockReason}});
 for(const item of extra)await send({...item,turn_id:id,generation:id});
 await send({type:"turn.done",turn_id:id,generation:id,route:"curated_protocol",timings_ms:{},segment_count:1,...done});
 await send({type:"turn.state",turn_id:id,generation:id,revision:++revision,state:terminal,route:"curated_protocol"});
 const card=turnNode(id,sessionGeneration);
 return{status:card.querySelector(".turn-status"),error:card.querySelector(".error").textContent,route:card.querySelector(".turn-route")?.textContent||""};
}
const done=await turn(1,{done:{result_kind:"current",speech_mode:"control"},terminal:"complete"});
assert(done.status.textContent==="완료"&&!done.error,`complete turn: ${done.status.textContent}|${done.error}`);
assert(done.route.includes("개발용 절차")&&done.route.includes("curated_protocol")&&done.route.includes("complete"),`route marker missing from diagnostics: ${done.route}`);
const gate=await turn(2,{blockReason:"endpoint_observation_not_reported",done:{result_kind:"next",speech_mode:"blocked"},terminal:"blocked"});
assert(gate.status.textContent==="관찰 필요"&&gate.status.className.includes("needs-observation")&&gate.error==="",`observation hold still reads as blocked: ${gate.status.textContent}|${gate.error}`);
assert(gate.route.includes("blocked")&&gate.route.includes("endpoint_observation_not_reported"),`raw state missing from diagnostics: ${gate.route}`);
// The reason was already set before this turn, so this turn cannot be told
// apart from a failed save that restored it: it stays "차단됨".
const again=await turn(3,{blockReason:"endpoint_observation_not_reported",done:{result_kind:"next",speech_mode:"blocked"},terminal:"blocked"});
assert(again.status.textContent==="차단됨"&&again.error==="차단됨",`unconfirmable hold was relabelled: ${again.status.textContent}`);
await send({type:"protocol.fixture.state",action:"next",state:{...base,block_reason:null,revision:4}});
const failed=await turn(4,{blockReason:"endpoint_observation_not_reported",done:{result_kind:"next",speech_mode:"blocked"},terminal:"blocked",extra:[{type:"experiment.session.error",code:"workspace_error"}]});
assert(failed.status.textContent==="차단됨"&&failed.error==="차단됨",`a failed save read as an observation hold: ${failed.status.textContent}`);
await send({type:"protocol.fixture.state",action:"next",state:{...base,block_reason:null,revision:5}});
const interval=await turn(5,{blockReason:"repeat_interval_open",done:{result_kind:"next",speech_mode:"blocked"},terminal:"blocked"});
assert(interval.status.textContent==="차단됨"&&interval.error==="차단됨",`another hold reason was relabelled: ${interval.status.textContent}`);
await send({type:"protocol.fixture.state",action:"next",state:{...base,block_reason:null,revision:6}});
const clarify=await turn(6,{blockReason:"endpoint_observation_not_reported",done:{result_kind:"clarify_reference",speech_mode:"blocked"},terminal:"blocked"});
assert(clarify.status.textContent==="차단됨",`a non-NEXT turn read as an observation hold: ${clarify.status.textContent}`);
const cancelled=await turn(7,{done:{result_kind:"current",speech_mode:"control"},terminal:"cancelled"});
assert(cancelled.status.textContent==="중단됨"&&cancelled.error==="중단됨",`cancelled turn changed: ${cancelled.status.textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class DryLabWorkflowLoadingTests(unittest.TestCase):
    """Item 3: opening the page does not ask for the dry-lab workflow list."""

    def test_page_open_skips_workflows_and_loads_them_on_demand(self):
        result = run_page_script(r"""
const calls=[];
globalThis.fetch=async url=>{url=String(url);calls.push(url);
 if(url==="/api/protocols")return json({protocols:[]});
 if(url==="/api/workspace/session")return json({workspaces:["researcher","reviewer"]});
 if(url==="/api/workspace/connectors")return json({connectors:[]});
 if(url.startsWith("/api/workspace/protocol-library"))return json({protocols:[]});
 if(url==="/api/workspace/experiments")return json({experiments:[]});
 if(url.startsWith("/api/workspace/experiments/exp-1/timeline"))return json({timeline:[],session:{session_id:"exp-1",version:1,status:"in_progress",protocol_id:"p-1",current_step_label:"1"}});
 if(url.startsWith("/api/workspace/dry-lab/links"))return json({links:[]});
 if(url==="/api/workspace/dry-lab/workflows")return json({workflows:[{workflow_revision_id:"wf-1",engine:"snakemake",repository:"lab/flows",commit_sha:"a".repeat(40),source_path:"Snakefile",approval_state:"approved"}]});
 if(url==="/api/workspace/reviewer/inbox")return json({items:[]});
 throw new Error(`unexpected ${url}`);};
const settle=()=>new Promise(resolve=>setTimeout(resolve,20));
const workflowCalls=()=>calls.filter(url=>url==="/api/workspace/dry-lab/workflows").length;
assert((pageListeners.load||[]).length===1,"the page load handler was not registered");
pageListeners.load[0]();await settle();
assert(calls.includes("/api/workspace/session")&&calls.includes("/api/protocols"),`page open did not load the workspace: ${calls}`);
assert(workflowCalls()===0,`page open still requested dry-lab workflows: ${calls}`);
await loadExperimentTimeline("exp-1");
assert(workflowCalls()===1,`an open experiment did not load the workflow picker: ${calls}`);
const picker=node("experiment-workflow-revision");
assert(picker.children.some(option=>option.value==="wf-1"),"approved workflow missing from the researcher picker");
await loadExperimentTimeline("exp-1");
assert(workflowCalls()===1,"the researcher picker refetched on every timeline refresh");
activateWorkspace("reviewer");await settle();
assert(workflowCalls()===2,"opening the reviewer workspace no longer loads its workflow list");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class DuplicateUploadNoticeTests(unittest.TestCase):
    """Item 4: a re-upload of a stored PDF names the file it matched."""

    def test_same_pdf_under_another_name_is_announced_whatever_its_state(self):
        result = run_page_script(r"""
const notice=node("protocol-duplicate-notice");
const review=protocol=>({protocol_id:protocol.protocol_id,title:"In-gel",revision_id:"pdf-1",analysis_status:protocol.analysis_status,available_for_execution:protocol.available_for_execution,readiness:{status:"analysis_required",reasons:[]},source:{filename:protocol.source_filename,sha256:"a".repeat(64),page_count:1},sections:[]});
async function upload(filename,response){
 node("protocol-pdf").files=[{name:filename,type:"application/pdf"}];
 const calls=[];
 globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url}`);
  if(url.startsWith("/api/protocols?filename="))return json(response);
  if(url===`/api/protocols/${response.protocol.protocol_id}/analysis`)return json({...response.protocol,analysis_status:"review_required",readiness_status:"analysis_required"});
  if(url.endsWith("/analysis/status"))return json({state:"review_required"});
  if(url===`/api/protocols/${response.protocol.protocol_id}/review`)return json(review(response.protocol));
  if(url==="/api/protocols")return json({protocols:[{...response.protocol,title:"In-gel",revision_id:"pdf-1"}]});
  throw new Error(`unexpected ${url}`);};
 await registerSelectedProtocol();
 stopProtocolAnalysisPolling();
 return calls;
}
const expected="이미 등록된 같은 파일입니다 (기존 이름: in-gel-digestion.pdf)";
for(const [label,state] of [["executable",{analysis_status:"approved",available_for_execution:true}],["awaiting review",{analysis_status:"review_required",available_for_execution:false}],["analysable",{analysis_status:"structured_analysis_ready",available_for_execution:false}],["OCR",{analysis_status:"ocr_required",available_for_execution:false}]]){
 const calls=await upload("renamed-copy.pdf",{deduplicated:true,protocol:{protocol_id:"p-1",source_filename:"in-gel-digestion.pdf",...state}});
 assert(calls.includes("GET /api/protocols"),`${label}: catalog was not refreshed: ${calls}`);
 assert(!notice.hidden&&notice.textContent===expected,`${label}: duplicate notice missing after the catalog refresh: ${notice.hidden}|${notice.textContent}|${node("protocol-upload-status").textContent}`);
}
await upload("fresh.pdf",{deduplicated:false,protocol:{protocol_id:"p-2",source_filename:"fresh.pdf",analysis_status:"review_required",available_for_execution:false}});
assert(notice.hidden&&notice.textContent==="","a first-time upload kept the previous duplicate notice");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class ActivationFromProtocolListTests(unittest.TestCase):
    """Item 5: picking a protocol in the run list reaches its activation."""

    def test_selected_protocol_loads_its_review_and_shows_activation_when_allowed(self):
        result = run_page_script(r"""
const entry=(id,title,available,extra={})=>({protocol_id:id,title,revision_id:`${id}-rev`,readiness_status:"guidance_ready",analysis_status:available?"approved":"review_required",lifecycle_state:available?"approved":"review_required",available_for_execution:available,...extra});
const catalog=[entry("p-run","Runnable",true),entry("p-act","Needs activation",false),entry("p-block","Blocked",false,{lifecycle_state:"blocked",readiness_status:"analysis_required"})];
const reviews={"p-run":{development_activation_allowed:false,available_for_execution:true},"p-act":{development_activation_allowed:true,available_for_execution:false},"p-block":{development_activation_allowed:false,available_for_execution:false}};
const held=new Map(),calls=[];
globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url}`);
 if(url==="/api/protocols")return json({protocols:catalog});
 const reviewMatch=url.match(/^\/api\/protocols\/([^/]+)\/review$/);
 if(reviewMatch){const id=decodeURIComponent(reviewMatch[1]);const payload={...catalog.find(item=>item.protocol_id===id),...reviews[id],source:{filename:`${id}.pdf`,sha256:"a".repeat(64),page_count:1},readiness:{status:"guidance_ready",reasons:[]},sections:[]};if(held.has(id))await held.get(id);return json(payload);}
 if(url==="/api/protocols/p-act/activate-development"&&options.method==="POST")return json({protocol_id:"p-act"});
 if(url.startsWith("/api/workspace/"))return json({protocols:[]});
 throw new Error(`unexpected ${options.method||"GET"} ${url}`);};
const settle=()=>new Promise(resolve=>setTimeout(resolve,20));
const select=node("protocol-id"),panel=node("protocol-review-panel"),activate=node("protocol-development-activate");
const pick=async id=>{select.value=id;select.dispatch("change");await settle();};
panel.hidden=true;activate.hidden=true;// as in the page markup
await loadProtocolCatalog();
const option=id=>select.children.find(item=>item.value===id);
assert(option("p-act")&&!option("p-act").disabled&&option("p-act").dataset.runnable==="false"&&option("p-run").dataset.runnable==="true","a protocol that cannot run is not pickable");
assert(node("protocol-upload-status").textContent.includes("실행 가능 항목만 시작할 수 있습니다"),"catalog status still says only runnable items can be picked");
assert(panel.hidden!==false&&activate.hidden,"a review opened before anything was picked");
await pick("p-act");
assert(calls.includes("GET /api/protocols/p-act/review"),`picking did not load the review: ${calls}`);
assert(!panel.hidden&&panel.open&&!activate.hidden&&!activate.disabled,"an activatable pick did not show the activation button");
assert(node("start").disabled,"a protocol that cannot run became startable");
await loadProtocolCatalog("p-act");
assert(select.value==="p-act","a refresh dropped the person's own pick");
await pick("p-block");
assert(activate.hidden&&!panel.hidden,"activation shown although the server did not allow it");
let release;held.set("p-act",new Promise(resolve=>{release=resolve}));
select.value="p-act";select.dispatch("change");
await pick("p-block");
release();await settle();
assert(activate.hidden&&lastProtocolReview.protocol_id==="p-block",`a late review of an earlier pick replaced the current one: ${lastProtocolReview?.protocol_id}`);
held.clear();
await pick("p-run");
assert(!panel.hidden&&!panel.open&&activate.hidden&&!node("start").disabled,"a runnable pick did not keep its review collapsed or stay startable");
select.value="p-run";await loadProtocolCatalog("p-block");
assert(select.value==="p-run","a refresh picked a protocol that cannot run on the person's behalf");
await pick("");
assert(panel.hidden&&activate.hidden&&currentProtocolReviewId===null,"clearing the pick left a review and its activation on screen");
await pick("p-act");
currentProtocolReviewId="p-block";activate.dispatch("click");await settle();
assert(!calls.includes("POST /api/protocols/p-block/activate-development")&&!calls.includes("POST /api/protocols/p-act/activate-development"),"activation acted on a protocol whose review is not on screen");
await pick("p-act");
activate.dispatch("click");await settle();
assert(calls.includes("POST /api/protocols/p-act/activate-development"),"activation of the reviewed pick was not sent");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class PlainScreenTermTests(unittest.TestCase):
    """Item 6: the screen no longer uses the jargon the pilot found hard."""

    HARD_TERMS = ("리비전", "해소", "게이트", "정본", "세그먼트", "페이로드", "수명주기")

    def test_hard_terms_are_gone_from_the_page_and_its_stylesheet(self):
        page = INDEX.read_text(encoding="utf-8") + (
            INDEX.parent / "app.css"
        ).read_text(encoding="utf-8")
        for term in self.HARD_TERMS:
            self.assertNotIn(term, page)

    def test_review_panel_renders_the_plain_words(self):
        result = run_page_script(r"""
renderProtocolReview({protocol_id:"p-1",title:"In-gel",revision_id:"pdf-1-analysis-2",lifecycle_state:"review_required",analysis_payload_sha256:"b".repeat(64),available_for_execution:false,
 source:{filename:"in-gel.pdf",sha256:"a".repeat(64),page_count:9},readiness:{status:"guidance_ready",reasons:[]},gates:{parsing:"passed"},
 outstanding_blockers:[{code:"unresolved_ambiguity",kind:"reviewer_can_clear",reviewer_action:"resolve_ambiguity",already_acknowledged:false,decision_options:["single_statement_is_authoritative"],clearing_decision:"single_statement_is_authoritative",citable_segments:[]}],
 reviewer_findings:[{kind:"ambiguity_resolved",actor_principal_id:"reviewer-a",actor_role:"reviewer"},{kind:"gate_acknowledged",reason_code:"no_declared_safety_warnings",actor_principal_id:"reviewer-a",actor_role:"reviewer"}],sections:[]});
const text=["protocol-review-content","protocol-blockers","protocol-findings"].map(id=>visibleText(node(id))).join(" ");
for(const phrase of ["원문과 분석 버전","버전 pdf-1-analysis-2","분석 결과 데이터 SHA-256","처리 단계 · 검토 필요","실행 전 확인 조건","어느 진술이 기준인지","근거 없는 해결은 서버가 거부합니다","선택할 근거 원문 구간이 없습니다","이 모호성을 해결","모호성 해결","확인 처리"])assert(text.includes(phrase),`plain wording missing: ${phrase}`);
for(const term of ["리비전","해소","게이트","정본","세그먼트","페이로드","수명주기"])assert(!text.includes(term),`hard term still rendered: ${term}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
