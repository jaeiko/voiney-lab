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
const review={protocol_id:"protocol-x",title:"In-gel",revision_id:"pdf-1-analysis-1",lifecycle_state:"blocked",readiness_status:"analysis_required",available_for_execution:false,analysis_available:true,
 source:{filename:"in-gel.pdf",sha256:"a".repeat(64),page_count:9},
 readiness:{status:"analysis_required",label:"Protocol 분석 필요",reasons:[
  {code:"unresolved_ambiguity",message:"A source ambiguity remains unresolved."},
  {code:"no_executable_steps",message:"The structured Protocol contains no executable source steps."},
  {code:"reason_from_a_newer_server",message:"Something new."}]},
 analysis_failure:{code:"protocol_analysis_invalid_evidence",retryable:true,action:"Review the failure code and explicitly retry analysis."},
 constructs:[{construct_type:"SourceAmbiguity",source_text:"Two volumes are stated.",resolved:false,evidence:{source_page_number:3,source_excerpt:"20 µL or 30 µL"}}],
 execution_blockers:[{code:"no_executable_steps",message:"The structured Protocol contains no executable source steps.",message_ko:"실행할 단계를 원문에서 찾지 못했습니다.",kind:"blocking",source_page_number:null,source_excerpt:null,step_id:null}],
 execution_notices:[
  {code:"unresolved_ambiguity",message:"A source ambiguity remains unresolved.",message_ko:"원문에 서로 다른 두 서술이 있습니다.",kind:"source_note",source_page_number:3,source_excerpt:"20 µL or 30 µL",step_id:"step-4"},
  {code:"unsupported_parallel_background_work",message:"Parallel work.",message_ko:"동시 작업은 아직 안내 기능이 없습니다.",kind:"no_guidance_yet",source_page_number:5,source_excerpt:"Meanwhile, prepare the gel.",step_id:"step-6"}],
 safety_notices:[{step_label:"4",step_id:"step-4",source_page_number:3,source_text:"Wear gloves and work in a fume hood.",primary_text:"장갑을 끼고 흄후드에서 작업하세요."}],
 sections:[]};
renderProtocolReview(review);
const hosts=["protocol-review-content","protocol-blockers","protocol-start-summary","protocol-start-overview","protocol-execution-blockers","protocol-safety-notices","protocol-execution-notices"].map(node);
const visible=hosts.map(visibleText).join(" "),dev=hosts.map(devText).join(" ");
for(const code of ["analysis_required","blocked","unresolved_ambiguity","no_executable_steps","unsupported_parallel_background_work","no_guidance_yet","source_note","protocol_analysis_invalid_evidence","SourceAmbiguity","reason_from_a_newer_server"]){
 assert(withoutCode(visible,code),`raw code ${code} is still on screen: ${visible}`);
 assert(!withoutCode(dev,code),`raw code ${code} is missing from 개발 상세 정보: ${dev}`);
}
for(const label of ["시작 전에 읽을 알림 있음","실행 불가 · 조치 필요","원문에 서로 다른 두 서술","실행할 원문 단계 없음","동시·백그라운드 작업 · 아직 안내 기능 없음","분석 근거가 원문과 맞지 않음","원문의 모호한 부분","실행을 막는 사유 1건","시작 전 알림 2건","원문의 안전 주의 1건","Wear gloves and work in a fume hood.","장갑을 끼고 흄후드에서 작업하세요."])assert(visible.includes(label),`label missing: ${label}`);
assert(visible.includes("확인되지 않은 상태"),"an unknown code was not marked as unconfirmed");
assert(visible.includes("A source ambiguity remains unresolved."),"the server's own reason message was dropped");
for(const word of ["검토자","승인","활성화"])assert(!visible.includes(word),`${word} is still on the screen: ${visible}`);
assert(node("protocol-start").hidden,"a blocked protocol offered the start button");
renderProtocolReview({...review,lifecycle_state:"ready",available_for_execution:true,readiness:{status:"guidance_ready",label:"안내 준비 완료",reasons:[]},execution_blockers:[],execution_notices:[],analysis_failure:null,constructs:[]});
const ready=visibleText(node("protocol-review-content"));
assert(ready.includes("안내 준비 완료")&&withoutCode(ready,"guidance_ready"),`guidance_ready not translated: ${ready}`);
assert(!node("protocol-start").hidden,"a runnable protocol did not offer the start button");
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
assert(again.status.textContent==="차단됨"&&again.error==="",`unconfirmable hold was relabelled: ${again.status.textContent}`);
await send({type:"protocol.fixture.state",action:"next",state:{...base,block_reason:null,revision:4}});
const failed=await turn(4,{blockReason:"endpoint_observation_not_reported",done:{result_kind:"next",speech_mode:"blocked"},terminal:"blocked",extra:[{type:"experiment.session.error",code:"workspace_error"}]});
assert(failed.status.textContent==="차단됨"&&failed.error!=="차단됨",`a failed save read as an observation hold: ${failed.status.textContent}`);
await send({type:"protocol.fixture.state",action:"next",state:{...base,block_reason:null,revision:5}});
const interval=await turn(5,{blockReason:"repeat_interval_open",done:{result_kind:"next",speech_mode:"blocked"},terminal:"blocked"});
assert(interval.status.textContent==="차단됨"&&interval.error==="",`another hold reason was relabelled: ${interval.status.textContent}`);
await send({type:"protocol.fixture.state",action:"next",state:{...base,block_reason:null,revision:6}});
const clarify=await turn(6,{blockReason:"endpoint_observation_not_reported",done:{result_kind:"clarify_reference",speech_mode:"blocked"},terminal:"blocked"});
assert(clarify.status.textContent==="차단됨",`a non-NEXT turn read as an observation hold: ${clarify.status.textContent}`);
const cancelled=await turn(7,{done:{result_kind:"current",speech_mode:"control"},terminal:"cancelled"});
assert(cancelled.status.textContent==="중단됨"&&cancelled.error==="",`cancelled turn changed: ${cancelled.status.textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class DryLabWorkflowLoadingTests(unittest.TestCase):
    """Item 3: opening the page does not ask for the dry-lab workflow list."""

    def test_page_open_skips_workflows_and_loads_them_on_demand(self):
        result = run_page_script(r"""
const calls=[];
globalThis.fetch=async url=>{url=String(url);calls.push(url);
 if(url==="/api/protocols")return json({protocols:[]});
 if(url==="/api/workspace/session")return json({workspaces:["researcher"]});
 if(url==="/api/workspace/connectors")return json({connectors:[]});
 if(url.startsWith("/api/workspace/protocol-library"))return json({protocols:[]});
 if(url==="/api/workspace/experiments")return json({experiments:[]});
 if(url.startsWith("/api/workspace/experiments/exp-1/timeline"))return json({timeline:[],session:{session_id:"exp-1",version:1,status:"in_progress",protocol_id:"p-1",current_step_label:"1"}});
 if(url.startsWith("/api/workspace/dry-lab/links"))return json({links:[]});
 if(url==="/api/workspace/dry-lab/workflows")return json({workflows:[{workflow_revision_id:"wf-1",engine:"snakemake",repository:"lab/flows",commit_sha:"a".repeat(40),source_path:"Snakefile",approval_state:"approved"}]});
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


class StartFromProtocolListTests(unittest.TestCase):
    """Item 5: picking a protocol in the run list shows its analysis result and its start."""

    def test_selected_protocol_loads_its_review_and_shows_the_start_when_it_may_run(self):
        result = run_page_script(r"""
const entry=(id,title,available,extra={})=>({protocol_id:id,title,revision_id:`${id}-rev`,readiness_status:"guidance_ready",analysis_status:"review_required",lifecycle_state:available?"ready":"blocked",available_for_execution:available,...extra});
const catalog=[entry("p-run","Runnable",true),entry("p-block","Blocked",false,{readiness_status:"analysis_required"})];
const reviews={"p-run":{available_for_execution:true,execution_blockers:[],execution_notices:[],safety_notices:[]},"p-block":{available_for_execution:false,execution_blockers:[{code:"no_executable_steps",message_ko:"실행할 단계를 원문에서 찾지 못했습니다.",kind:"blocking"}],execution_notices:[],safety_notices:[]}};
const held=new Map(),calls=[];
globalThis.fetch=async(url,options={})=>{url=String(url);calls.push(`${options.method||"GET"} ${url}`);
 if(url==="/api/protocols")return json({protocols:catalog});
 const reviewMatch=url.match(/^\/api\/protocols\/([^/]+)\/review$/);
 if(reviewMatch){const id=decodeURIComponent(reviewMatch[1]);const payload={...catalog.find(item=>item.protocol_id===id),...reviews[id],analysis_available:true,source:{filename:`${id}.pdf`,sha256:"a".repeat(64),page_count:1},readiness:{status:"guidance_ready",reasons:[]},sections:[]};if(held.has(id))await held.get(id);return json(payload);}
 if(url.startsWith("/api/workspace/"))return json({protocols:[]});
 throw new Error(`unexpected ${options.method||"GET"} ${url}`);};
const settle=()=>new Promise(resolve=>setTimeout(resolve,20));
const select=node("protocol-id"),panel=node("protocol-review-panel"),summary=node("protocol-start-summary"),start=node("protocol-start");
// The fake DOM is flat, so the start screen's inner hosts are read beside it.
const summaryText=()=>["protocol-start-summary","protocol-start-overview","protocol-execution-blockers","protocol-safety-notices","protocol-execution-notices"].map(id=>visibleText(node(id))).join(" ");
const pick=async id=>{select.value=id;select.dispatch("change");await settle();};
panel.hidden=true;summary.hidden=true;start.hidden=true;// as in the page markup
await loadProtocolCatalog();
const option=id=>select.children.find(item=>item.value===id);
assert(option("p-block")&&!option("p-block").disabled&&option("p-block").dataset.runnable==="false"&&option("p-run").dataset.runnable==="true","a protocol that cannot run is not pickable");
assert(node("protocol-upload-status").textContent.includes("실행 가능 항목만 시작할 수 있습니다"),"catalog status still says only runnable items can be picked");
await pick("p-block");
assert(calls.includes("GET /api/protocols/p-block/review"),`picking did not load the review: ${calls}`);
assert(!panel.hidden&&panel.open&&!summary.hidden&&start.hidden,"a blocked pick offered the start button or hid its result");
assert(summaryText().includes("실행을 막는 사유 1건"),`the blocker is not on the start screen: ${summaryText()}`);
assert(node("start").disabled,"a protocol that cannot run became startable");
await loadProtocolCatalog("p-block");
assert(select.value==="p-block","a refresh dropped the person's own pick");
let release;held.set("p-run",new Promise(resolve=>{release=resolve}));
select.value="p-run";select.dispatch("change");
await pick("p-block");
release();await settle();
assert(start.hidden&&lastProtocolReview.protocol_id==="p-block",`a late review of an earlier pick replaced the current one: ${lastProtocolReview?.protocol_id}`);
held.clear();
await pick("p-run");
assert(!panel.hidden&&!panel.open&&!summary.hidden&&!start.hidden&&!node("start").disabled,"a runnable pick did not keep its detail folded, show its start screen and stay startable");
select.value="p-run";await loadProtocolCatalog("p-block");
assert(select.value==="p-run","a refresh picked a protocol that cannot run on the person's behalf");
await pick("");
assert(panel.hidden&&summary.hidden&&start.hidden&&currentProtocolReviewId===null,"clearing the pick left a result and its start on screen");
// The start acts only on the protocol whose result is on screen, and makes no authority call.
let started=null;startSession=async()=>{started=node("protocol-id").value};
await pick("p-run");
currentProtocolReviewId="p-block";start.dispatch("click");await settle();
assert(started===null,"the start acted on a protocol whose result is not on screen");
await pick("p-run");
start.dispatch("click");await settle();
assert(started==="p-run",`the start did not begin the session on the reviewed pick: ${started}`);
assert(!calls.some(call=>call.startsWith("POST")),`an authority call was made: ${calls}`);
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
renderProtocolReview({protocol_id:"p-1",title:"In-gel",revision_id:"pdf-1-analysis-2",lifecycle_state:"ready",analysis_payload_sha256:"b".repeat(64),available_for_execution:true,analysis_available:true,
 source:{filename:"in-gel.pdf",sha256:"a".repeat(64),page_count:9},readiness:{status:"guidance_ready",reasons:[]},
 execution_blockers:[],execution_notices:[{code:"unresolved_ambiguity",message_ko:"원문에 서로 다른 두 서술이 있습니다.",kind:"source_note",source_page_number:4,source_excerpt:"20 µL or 30 µL",step_id:"step-4"}],
 safety_notices:[],sections:[]});
const text=["protocol-review-content","protocol-blockers","protocol-start-summary","protocol-start-overview","protocol-execution-blockers","protocol-safety-notices","protocol-execution-notices"].map(id=>visibleText(node(id))).join(" ");
for(const phrase of ["원문 파일","9쪽","처리 단계 · 분석 통과 · 실행 가능","시작 전 알림 1건","원문에 서로 다른 두 서술","원문에 적힌 안전 주의 없음","이 프로토콜로 시작"])assert(text.includes(phrase),`plain wording missing: ${phrase}`);
for(const gone of ["검토","승인","해제"])assert(!text.includes(gone),`reviewer wording still rendered: ${gone}`);
// Lane U decision 2: the revision id and the hashes sit in the developer details, not the body.
for(const hidden of ["pdf-1-analysis-2","SHA-256","a".repeat(64),"b".repeat(64)])assert(!text.includes(hidden),`identifier in the body: ${hidden}`);
const dev=["protocol-review-content"].map(id=>devText(node(id))).join(" ");assert(dev.includes("pdf-1-analysis-2")&&dev.includes("b".repeat(64)),"identifier missing from the developer details");
for(const term of ["리비전","해소","게이트","정본","세그먼트","페이로드","수명주기"])assert(!text.includes(term),`hard term still rendered: ${term}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
