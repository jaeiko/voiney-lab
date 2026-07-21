import subprocess, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class FrontendSessionTests(unittest.TestCase):
    def test_session_restart_and_stale_event_isolation(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert=(ok,message)=>{if(!ok)throw new Error(message)};
class Element{constructor(){this.children=[];this.fields={};this.textContent="";this.disabled=false}set innerHTML(v){for(const c of ["transcript","reply","stats","error"])this.fields[c]={textContent:c==="transcript"?"Listening…":""}}querySelector(s){return this.fields[s.slice(1)]}prepend(n){this.children.unshift(n)}replaceChildren(){this.children=[]}addEventListener(){}}
const ids=Object.fromEntries(["start","stop","log","status","state"].map(x=>[x,new Element()]));
globalThis.document={getElementById:id=>ids[id],createElement:()=>new Element()}; globalThis.location={protocol:"http:",host:"test"};
class WS{static OPEN=1;static CLOSING=2;constructor(){this.readyState=1;this.sent=[]}send(x){this.sent.push(x)}close(){this.readyState=3}}
globalThis.WebSocket=WS; globalThis.navigator={mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})}}; globalThis.AudioContext=class{};
""" + script + r"""
(async()=>{const oldSocket=socket; await onMessage({data:JSON.stringify({type:"speech.start",turn_id:1})},0,oldSocket); await onMessage({data:JSON.stringify({type:"transcript",turn_id:1,text:"old question"})},0,oldSocket); await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"old answer"})},0,oldSocket); const oldNode=ids.log.children[0]; await stopSession(); assert(ids.log.children.length===1,"stop erased transcript"); socket=new WS();ensureAudio=async()=>{};await startSession();assert(ids.log.children.length===0&&turns.size===0,"start reset incomplete");const newSocket=socket,generation=sessionGeneration;await onMessage({data:JSON.stringify({type:"speech.start",turn_id:1})},generation,newSocket);await onMessage({data:JSON.stringify({type:"transcript",turn_id:1,text:"new question"})},generation,newSocket);await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"new answer"})},generation,newSocket);const newNode=ids.log.children[0];assert(newNode!==oldNode,"reused node");assert(newNode.querySelector(".transcript").textContent==="new question","contaminated transcript");assert(newNode.querySelector(".reply").textContent==="new answer ","appended reply");await onMessage({data:JSON.stringify({type:"reply.delta",turn_id:1,text:"late"})},0,oldSocket);await onMessage({data:JSON.stringify({type:"turn.done",turn_id:1,timings_ms:{stt:1,first_audio_ms:2,total_ms:3},segment_count:1,output_frames:1})},0,oldSocket);await onMessage({data:JSON.stringify({type:"audio.complete",turn_id:1})},0,oldSocket);assert(newNode.querySelector(".reply").textContent==="new answer ","stale text accepted");assert(newNode.querySelector(".stats").textContent==="","stale done accepted");assert(audioComplete===false,"stale audio completion accepted");for(let i=0;i<3;i++){await stopSession();socket=new WS();await startSession();await onMessage({data:JSON.stringify({type:"speech.start",turn_id:1})},sessionGeneration,socket);assert(ids.log.children.length===1&&turns.size===1,"unclean repeated restart")}})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = subprocess.run(["node", "-e", harness], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

if __name__ == "__main__": unittest.main()
