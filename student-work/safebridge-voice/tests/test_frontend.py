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
const ids=Object.fromEntries(["start","stop","log","status","state","last-report-id","last-report-state"].map(x=>[x,new Element()]));
globalThis.document={getElementById:id=>ids[id],createElement:()=>new Element()}; globalThis.location={protocol:"http:",host:"test"};
class WS{static OPEN=1;static CLOSING=2;constructor(){this.readyState=1;this.sent=[]}send(x){this.sent.push(x)}close(){this.readyState=3}}
globalThis.WebSocket=WS; globalThis.navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})}}; globalThis.AudioContext=class{};
""" + script + r"""
(async()=>{
renderState("IDLE");assert(visibleState==="IDLE"&&!ids.start.disabled&&ids.stop.disabled,"initial controls");
const oldSocket=socket;await onMessage({data:JSON.stringify({type:"speech.start",turn_id:1})},0,oldSocket);await onMessage({data:JSON.stringify({type:"transcript",turn_id:1,text:"old question"})},0,oldSocket);await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"old answer"})},0,oldSocket);const oldNode=ids.log.children[0];
await stopSession();assert(visibleState==="IDLE"&&ids.log.children.length===1,"stop state or transcript");socket=new WS();ensureAudio=async()=>{};await startSession();assert(visibleState==="CONNECTING"&&ids.start.disabled&&!ids.stop.disabled,"start not connecting");assert(ids.log.children.length===0&&turns.size===0,"start reset incomplete");
const newSocket=socket,generation=sessionGeneration;await onMessage({data:JSON.stringify({type:"session.started",state:"IDLE"})},generation,newSocket);assert(visibleState==="LISTENING"&&ids.status.textContent==="듣고 있습니다…","ack not listening");await onMessage({data:JSON.stringify({type:"turn.processing",turn_id:1})},generation,newSocket);assert(visibleState==="THINKING","processing not thinking");
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:1})},generation,newSocket);await onMessage({data:JSON.stringify({type:"transcript",turn_id:1,text:"new question"})},generation,newSocket);await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"new answer"})},generation,newSocket);await onMessage({data:JSON.stringify({type:"tool.result",turn_id:1,tool:"create_safety_report",status:"success",report_id:"SR-20260722-A1B2C3",report_status:"queued_for_handoff",elapsed_ms:2})},generation,newSocket);const newNode=ids.log.children[0];assert(newNode!==oldNode&&newNode.querySelector(".transcript").textContent==="new question","transcript reset");assert(newNode.querySelector(".tools").textContent.includes("안전 보고 접수"),"tool result missing");assert(ids["last-report-id"].textContent==="SR-20260722-A1B2C3"&&ids["last-report-state"].textContent==="관리자 인계 대기","report card missing");
let ended;playContext={resume:async()=>{},createBuffer:()=>({getChannelData:()=>new Float32Array(1)}),createBufferSource:()=>({connect(){},start(){},set onended(fn){ended=fn},get onended(){return ended}}),destination:{}};activeTurn=1;queued.set(0,new ArrayBuffer(2));await pump(generation);assert(visibleState==="SPEAKING","playback not speaking");await onMessage({data:JSON.stringify({type:"audio.complete",turn_id:1})},generation,newSocket);await onMessage({data:JSON.stringify({type:"turn.done",turn_id:1,timings_ms:{stt:1,first_audio_ms:2,total_ms:3},segment_count:1,output_frames:1})},generation,newSocket);assert(visibleState==="SPEAKING","done changed queued playback");ended();await Promise.resolve();assert(visibleState==="LISTENING","drained playback not listening");const lateEnded=ended;await stopSession();lateEnded();await Promise.resolve();assert(visibleState==="IDLE","late playback callback changed state");
await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"late"})},0,oldSocket);assert(newNode.querySelector(".reply").textContent==="new answer ","stale text accepted");
for(const state of ["IDLE","CONNECTING","LISTENING","THINKING","SPEAKING","ERROR"]){renderState(state);await stopSession();assert(visibleState==="IDLE"&&!ids.start.disabled&&ids.stop.disabled,"stop from "+state);}
await onMessage({data:JSON.stringify({type:"state.changed",state:"AGENT_SPEAKING"})},generation,newSocket);assert(visibleState==="IDLE","old speaking event accepted");
for(let i=0;i<3;i++){socket=new WS();await startSession();assert(visibleState==="CONNECTING","restart connecting");await onMessage({data:JSON.stringify({type:"session.started",state:"IDLE"})},sessionGeneration,socket);assert(visibleState==="LISTENING"&&ids.start.disabled&&!ids.stop.disabled&&ids.status.textContent==="듣고 있습니다…","restart active controls");await stopSession();assert(visibleState==="IDLE"&&!ids.start.disabled&&ids.stop.disabled&&ids.status.textContent==="준비됨 · 세션 시작을 한 번 눌러주세요.","restart idle controls");}
})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(["node", "-e", harness], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__": unittest.main()
