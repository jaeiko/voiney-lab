"""Shared helpers for the lane NET tests: the page with ``connection.js`` under Node.

``run_connection_script(body)`` loads the production ``<script>`` block of
``static/index.html`` and then ``static/connection.js`` -- as the browser
loads them, one after the other -- into Node with the fake DOM of
``test_screen_cleanup`` and a fake clock, and runs ``body``. ``advance(ms)``
moves the clock and fires every timer that falls due on the way; ``settle()``
lets promises run. ``NetWS`` is a socket the test opens, feeds and closes;
``openRun()`` opens one through the page's own ``init()`` and puts an
experiment in the run view on it, as the server's ``session.ready`` and
first protocol state would.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.test_screen_cleanup import INDEX, PRELUDE, ROOT

CONNECTION = ROOT / "src" / "voiney_lab" / "static" / "connection.js"

CLOCK = r"""
let fakeNow=Date.UTC(2026,9,18,2,0,0);
Date.now=()=>fakeNow;
const fakeTimers=new Map();let fakeTimerId=0;
globalThis.setTimeout=(fn,ms=0,...args)=>{const id=++fakeTimerId;fakeTimers.set(id,{at:fakeNow+Math.max(0,Number(ms)||0),fn,args,every:0});return id;};
globalThis.setInterval=(fn,ms=0,...args)=>{const id=++fakeTimerId,every=Math.max(1,Number(ms)||0);fakeTimers.set(id,{at:fakeNow+every,fn,args,every});return id;};
globalThis.clearTimeout=globalThis.clearInterval=id=>{fakeTimers.delete(id);};
const settle=async(times=3)=>{for(let i=0;i<times;i++)await new Promise(resolve=>setImmediate(resolve));};
async function advance(ms){
 const end=fakeNow+ms;
 for(;;){
  let next=null;for(const [id,item] of fakeTimers)if(item.at<=end&&(!next||item.at<next[1].at))next=[id,item];
  if(!next)break;
  const [id,item]=next;fakeNow=Math.max(fakeNow,item.at);
  if(item.every)item.at+=item.every;else fakeTimers.delete(id);
  try{item.fn(...item.args);}catch(error){console.error(error);throw error;}
  await settle();
 }
 fakeNow=end;await settle();
}
"""

SOCKETS = r"""
const opened=[];
class NetWS{
 static CONNECTING=0;static OPEN=1;static CLOSING=2;static CLOSED=3;
 constructor(url){this.url=url;this.readyState=0;this.sent=[];this.listeners={};this.closedWith=null;opened.push(this);}
 addEventListener(kind,fn){(this.listeners[kind]||=[]).push(fn);}
 emit(kind,event={}){for(const fn of this.listeners[kind]||[])fn(event);const own=this["on"+kind];if(typeof own==="function")own(event);}
 open(){this.readyState=1;this.emit("open");}
 async deliver(message){const data=typeof message==="string"||message instanceof ArrayBuffer?message:JSON.stringify(message);this.emit("message",{data});await settle();}
 send(value){if(this.readyState!==1)throw new Error("send on a socket that is not open");this.sent.push(value);}
 texts(type){return this.sent.filter(item=>typeof item==="string").map(item=>JSON.parse(item)).filter(item=>!type||item.type===type);}
 close(code,reason){if(this.readyState>=2)return;this.closedWith={code,reason};this.readyState=3;this.emit("close",{code});}
 drop(){this.readyState=3;this.emit("close",{code:1006});}
}
globalThis.WebSocket=NetWS;
const fire=(kind,event={})=>{for(const fn of pageListeners[kind]||[])fn(event);};
const CONFIGURATION_ID=41,PROTOCOL_ID="protocol-net",REVISION_ID="pdf-1-analysis-1",EXPERIMENT_ID="experiment-net-1";
let stateRevision=0;
function runState(extra={}){return{attached:true,protocol_id:PROTOCOL_ID,revision_id:REVISION_ID,display_name:"In-gel digestion",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"4",current_step_id:"step-4",total_steps:12,at_final_step:false,block_reason:null,revision:++stateRevision,display_summary:"Incubate at 37 °C.",primary_summary:"37 °C에서 둡니다.",source_language:"en",spoken_summary:null,warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[4],workflow_status:"running",...extra};}
async function openRun(extra={}){
 document.body=document.body||new Element("body");
 const pending=init(sessionGeneration),ws=opened.at(-1);ws.open();await pending;
 acceptedSessionConfiguration={configuration_id:CONFIGURATION_ID,server_generation:1,mode:"cascade",language:"ko",input_language:"ko",protocol_id:PROTOCOL_ID,revision_id:REVISION_ID,experiment_session_id:EXPERIMENT_ID,experiment_session_version:3};
 currentExperimentSessionId=EXPERIMENT_ID;currentExperimentSessionVersion=3;currentExperimentSessionStatus="in_progress";
 sessionActive=true;
 await ws.deliver({type:"protocol.fixture.state",configuration_id:CONFIGURATION_ID,action:"start",state:runState(extra)});
 return ws;
}
"""


def run_connection_script(body: str) -> subprocess.CompletedProcess:
    html = INDEX.read_text(encoding="utf-8")
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = (
        PRELUDE + CLOCK + script + "\n" + CONNECTION.read_text(encoding="utf-8")
        + SOCKETS + "\n(async()=>{\n" + body
        + "\n})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1)});\n"
    )
    return subprocess.run(
        ["node", "-"], cwd=ROOT, text=True, input=harness, capture_output=True,
    )
