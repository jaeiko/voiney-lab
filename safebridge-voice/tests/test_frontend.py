import subprocess, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class FrontendSessionTests(unittest.TestCase):
    def test_session_restart_and_stale_event_isolation(self):
        html = (ROOT / "src" / "safebridge_voice" / "static" / "index.html").read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert=(ok,message)=>{if(!ok)throw new Error(message)};
class Element{constructor(){this.children=[];this.fields={};this.textContent="";this.disabled=false}set innerHTML(v){for(const c of ["transcript","reply","tools","stats","error"])this.fields[c]={textContent:c==="transcript"?"듣는 중…":""}}querySelector(s){return this.fields[s.slice(1)]}prepend(n){this.children.unshift(n)}replaceChildren(){this.children=[]}addEventListener(){}}
const ids=Object.fromEntries(["start","stop","log","status","state","last-report-id","last-report-state","pending-report","language-status","language-mode","manual-language","new-user","language-confirmation","procedure-title","procedure-meta","procedure-progress","procedure-step-title","procedure-instruction"].map(x=>[x,new Element()]));
ids["language-mode"].value="auto";ids["manual-language"].value="ko";
globalThis.document={getElementById:id=>ids[id],createElement:()=>new Element()}; globalThis.location={protocol:"http:",host:"test"};
class WS{static OPEN=1;static CLOSING=2;constructor(){this.readyState=1;this.sent=[]}send(x){this.sent.push(x)}close(){this.readyState=3}}
globalThis.WebSocket=WS; globalThis.navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})}}; globalThis.AudioContext=class{};
""" + script + r"""
(async()=>{
renderState("IDLE");assert(visibleState==="IDLE"&&!ids.start.disabled&&ids.stop.disabled,"initial controls");
const oldSocket=socket;await onMessage({data:JSON.stringify({type:"speech.start",turn_id:1})},0,oldSocket);await onMessage({data:JSON.stringify({type:"transcript",turn_id:1,text:"old question"})},0,oldSocket);await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"old answer"})},0,oldSocket);const oldNode=ids.log.children[0];
await stopSession();assert(visibleState==="IDLE"&&ids.log.children.length===1,"stop state or transcript");socket=new WS();ensureAudio=async()=>{};await startSession();assert(visibleState==="CONNECTING"&&ids.start.disabled&&!ids.stop.disabled,"start not connecting");assert(ids.log.children.length===0&&turns.size===0,"start reset incomplete");
const newSocket=socket,generation=sessionGeneration;await onMessage({data:JSON.stringify({type:"session.started",state:"IDLE"})},generation,newSocket);assert(visibleState==="LISTENING"&&ids.status.textContent==="듣고 있습니다…","ack not listening");await onMessage({data:JSON.stringify({type:"turn.processing",turn_id:1})},generation,newSocket);assert(visibleState==="THINKING","processing not thinking");
assert(JSON.parse(newSocket.sent.at(-1)).type==="session.set_language_mode","auto mode not requested");
await onMessage({data:JSON.stringify({type:"session.language_state",mode:"auto",language:null})},generation,newSocket);assert(ids["language-status"].textContent.includes("Automatic"),"auto state missing");
await onMessage({data:JSON.stringify({type:"session.turn_language_resolved",turn_id:1,language:"en"})},generation,newSocket);assert(ids["language-status"].textContent.includes("English"),"resolved language missing");
await onMessage({data:JSON.stringify({type:"session.language_confirmation_required",turn_id:1,reason:"language_unresolved",languages:["ko","en"]})},generation,newSocket);assert(ids["language-confirmation"].hidden===false&&!ids["language-status"].textContent.includes("source_path"),"confirmation missing");
ids["language-mode"].value="manual";ids["manual-language"].value="en";requestLanguageMode();assert(JSON.parse(newSocket.sent.at(-1)).mode==="manual"&&JSON.parse(newSocket.sent.at(-1)).language==="en","manual event invalid");assert(acknowledgedLanguageMode==="auto","manual state changed before ack");
await onMessage({data:JSON.stringify({type:"session.language_state",mode:"manual",language:"en"})},generation,newSocket);assert(acknowledgedLanguageMode==="manual"&&ids["manual-language"].hidden===false,"manual ack missing");
const ps={attached:true,procedure_id:"demo",title:"FICTIONAL NON-OPERATIONAL Demo",version:"1",status:"active",total_step_count:2,completed_step_count:0,current_step_number:1,current_step_id:"one",current_step_title:"One",approved_current_instruction:"Approved fictional instruction."};
await onMessage({data:JSON.stringify({type:"procedure.state",state:ps})},generation,newSocket);assert(ids["procedure-title"].textContent===ps.title&&ids["procedure-instruction"].textContent===ps.approved_current_instruction,"procedure event not rendered");
await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"completed step two"})},generation,newSocket);assert(procedureState.completed_step_count===0,"reply text mutated procedure");
await onMessage({data:JSON.stringify({type:"procedure.state",state:{attached:true,title:"bad"}})},generation,newSocket);assert(procedureState.completed_step_count===0,"malformed procedure state accepted");
for(const bad of [
 {...ps,current_step_number:2},
 {...ps,current_step_id:""},
 {...ps,completed_step_count:2},
 {...ps,status:"completed",completed_step_count:2},
 {...ps,status:"completed",completed_step_count:2,current_step_number:null,current_step_id:null,current_step_title:null,approved_current_instruction:"not null"}
]){await onMessage({data:JSON.stringify({type:"procedure.state",state:bad})},generation,newSocket);assert(procedureState.status==="active"&&procedureState.completed_step_count===0,"inconsistent procedure state accepted");}
const done={...ps,status:"completed",completed_step_count:2,current_step_number:null,current_step_id:null,current_step_title:null,approved_current_instruction:null};
await onMessage({data:JSON.stringify({type:"procedure.state",state:done})},generation,newSocket);assert(procedureState.status==="completed","canonical completed state rejected");
newUser();assert(JSON.parse(newSocket.sent.at(-1)).type==="session.reset","new user reset missing");
await onMessage({data:JSON.stringify({type:"procedure.state",state:{attached:false}})},generation,newSocket);assert(procedureState.attached===false&&ids["procedure-title"].textContent.includes("없음"),"procedure detach not rendered");
ids["language-mode"].value="auto";requestLanguageMode();assert(acknowledgedLanguageMode==="manual","request changed acknowledged mode");
await onMessage({data:JSON.stringify({type:"error",message:"invalid language mode"})},generation,newSocket);assert(ids["language-mode"].value==="manual"&&ids["manual-language"].value==="en"&&ids["language-status"].textContent.includes("Manual")&&ids["language-status"].textContent.includes("English"),"failed request did not roll back acknowledged language UI");assert(visibleState==="ERROR","failed request did not use safe error state");
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:1})},generation,newSocket);await onMessage({data:JSON.stringify({type:"transcript",turn_id:1,text:"new question"})},generation,newSocket);await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"new answer"})},generation,newSocket);
const draft={location:"Lab A",summary:"spill",urgency:"urgent",exposure_status:"unknown",material_or_equipment:"acetone"};
await onMessage({data:JSON.stringify({type:"tool.result",turn_id:1,tool:"create_safety_report",status:"awaiting_user_confirmation",report:draft})},generation,newSocket);assert(ids["pending-report"].hidden===false&&ids["pending-report"].textContent.includes("acetone"),"draft not visible");
await onMessage({data:JSON.stringify({type:"tool.call",turn_id:1,tool:"create_safety_report",status:"submitting",report:draft})},generation,newSocket);assert(ids["last-report-state"].textContent==="보고서 제출 중","submitting state missing");
await onMessage({data:JSON.stringify({type:"tool.result",turn_id:1,tool:"create_safety_report",status:"submission_failed",report:draft})},generation,newSocket);assert(ids["pending-report"].hidden===false&&ids["last-report-state"].textContent.includes("다시 승인하거나 취소"),"failed draft hidden");
await onMessage({data:JSON.stringify({type:"tool.result",turn_id:1,tool:"create_safety_report",status:"cancelled",report:draft})},generation,newSocket);assert(ids["pending-report"].hidden===true&&ids["last-report-id"].textContent==="아직 접수된 보고 없음","cancel did not reset draft UI");
await onMessage({data:JSON.stringify({type:"tool.result",turn_id:1,tool:"create_safety_report",status:"awaiting_user_confirmation",report:draft})},generation,newSocket);await onMessage({data:JSON.stringify({type:"tool.result",turn_id:1,tool:"create_safety_report",status:"confirmed",report:draft,report_id:"SR-20260722-A1B2C3",report_status:"queued_for_handoff",elapsed_ms:2})},generation,newSocket);const newNode=ids.log.children[0];assert(newNode!==oldNode&&newNode.querySelector(".transcript").textContent==="new question","transcript reset");assert(newNode.querySelector(".tools").textContent.includes("안전 보고 접수"),"tool result missing");assert(ids["last-report-id"].textContent==="SR-20260722-A1B2C3"&&ids["last-report-state"].textContent==="관리자 인계 대기"&&ids["pending-report"].hidden===true,"report card missing");
let ended;playContext={resume:async()=>{},createBuffer:()=>({getChannelData:()=>new Float32Array(1)}),createBufferSource:()=>({connect(){},start(){},set onended(fn){ended=fn},get onended(){return ended}}),destination:{}};activeTurn=1;queued.set(0,new ArrayBuffer(2));await pump(generation);assert(visibleState==="SPEAKING","playback not speaking");await onMessage({data:JSON.stringify({type:"audio.complete",turn_id:1})},generation,newSocket);await onMessage({data:JSON.stringify({type:"turn.done",turn_id:1,route:"brain",timings_ms:{stt:1,first_audio_ms:2,total_ms:3},segment_count:1,output_frames:1})},generation,newSocket);assert(newNode.querySelector(".stats").textContent.includes("경로 brain")&&newNode.querySelector(".stats").textContent.includes("첫 음성 2 ms"),"server route or first audio missing");assert(visibleState==="SPEAKING","done changed queued playback");ended();await Promise.resolve();assert(visibleState==="LISTENING","drained playback not listening");await onMessage({data:JSON.stringify({type:"turn.done",turn_id:1,route:"deterministic_emergency",timings_ms:{stt:1,total_ms:3},segment_count:0,output_frames:0})},generation,newSocket);assert(newNode.querySelector(".stats").textContent.includes("경로 deterministic_emergency")&&newNode.querySelector(".stats").textContent.includes("첫 음성 측정 안 됨")&&!newNode.querySelector(".stats").textContent.includes("undefined"),"missing first audio display unsafe");const lateEnded=ended;await stopSession();lateEnded();await Promise.resolve();assert(visibleState==="IDLE","late playback callback changed state");
await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"late"})},0,oldSocket);assert(newNode.querySelector(".reply").textContent==="new answer ","stale text accepted");
for(const state of ["IDLE","CONNECTING","LISTENING","THINKING","SPEAKING","ERROR"]){renderState(state);await stopSession();assert(visibleState==="IDLE"&&!ids.start.disabled&&ids.stop.disabled,"stop from "+state);}
await onMessage({data:JSON.stringify({type:"state.changed",state:"AGENT_SPEAKING"})},generation,newSocket);assert(visibleState==="IDLE","old speaking event accepted");
for(let i=0;i<3;i++){socket=new WS();await startSession();assert(visibleState==="CONNECTING","restart connecting");await onMessage({data:JSON.stringify({type:"session.started",state:"IDLE"})},sessionGeneration,socket);assert(visibleState==="LISTENING"&&ids.start.disabled&&!ids.stop.disabled&&ids.status.textContent==="듣고 있습니다…","restart active controls");await stopSession();assert(visibleState==="IDLE"&&!ids.start.disabled&&ids.stop.disabled&&ids.status.textContent==="준비됨 · 세션 시작을 한 번 눌러주세요.","restart idle controls");}
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(["node", "-e", harness], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__": unittest.main()
