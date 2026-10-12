"""Lane UI0, decision 5 (2026-10-12): the screen stays on while the experiment runs.

While the view is "실험 중" the page asks the browser for a screen wake lock,
once, where the browser has one; a browser without it, or a refusal, is
passed over in silence and changes nothing on the screen. The lock is let go
when the run view ends, and asked for again when the page comes back into
view after the browser let it go.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import run_page_script

SETUP = r"""
const configuration_id=84;
acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:5,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1"};socket=new WS();sessionActive=true;
document.body=new Element("body");document.visibilityState="visible";
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
let revision=0;
const state=(extra={})=>({attached:true,protocol_id:"protocol-x",revision_id:"pdf-1-analysis-1",display_name:"In-gel digestion",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"4",current_step_id:"step-4",total_steps:12,at_final_step:false,block_reason:null,revision:++revision,display_summary:"Add buffer.",primary_summary:"완충액을 넣습니다.",source_language:"en",spoken_summary:null,warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[4],workflow_status:"running",...extra});
async function step(extra={}){await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state(extra)});}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
const log=[];
class FakeLock{constructor(){this.released=false;this.listeners={};}addEventListener(kind,fn){(this.listeners[kind]||=[]).push(fn);}async release(){if(this.released)return;this.released=true;log.push("release");for(const fn of this.listeners.release||[])fn();}}
let refuse=false,lastLock=null;
function giveWakeLock(){Object.defineProperty(globalThis,"navigator",{value:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})},wakeLock:{request:async kind=>{log.push(`request ${kind}`);if(refuse)throw Object.assign(new Error("not allowed"),{name:"NotAllowedError"});lastLock=new FakeLock();return lastLock;}}},configurable:true});}
"""


class WakeLockTests(unittest.TestCase):

    def test_the_run_view_holds_the_screen_on_once_and_lets_it_go(self) -> None:
        result = run_page_script(SETUP + r"""
giveWakeLock();
await step();await settle();
assert(JSON.stringify(log)==='["request screen"]',`entering the run view: ${log}`);
await step({current_step_label:"5",current_step_id:"step-5"});renderState("LISTENING");await settle();
assert(log.length===1,`asked again while held: ${log}`);
await say({type:"experiment.ended",configuration_id,status:"completed",report:"saved"});await settle();
assert(JSON.stringify(log)==='["request screen","release"]',`the end did not let it go: ${log}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_stopped_session_lets_it_go_and_a_new_run_asks_again(self) -> None:
        result = run_page_script(SETUP + r"""
giveWakeLock();
await step();await settle();
await say({type:"session.stopped",configuration_id});await settle();
assert(JSON.stringify(log)==='["request screen","release"]',`stopped: ${log}`);
sessionActive=true;await step();await settle();
assert(log.filter(item=>item==="request screen").length===2,`a new run did not ask: ${log}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_lock_is_asked_for_again_when_the_page_comes_back(self) -> None:
        result = run_page_script(SETUP + r"""
giveWakeLock();
await step();await settle();
document.visibilityState="hidden";await lastLock.release();
assert(!(await onScreenVisibilityChange()),"asked while hidden");
document.visibilityState="visible";
assert(await onScreenVisibilityChange(),"not asked again on coming back");
assert(log.filter(item=>item==="request screen").length===2,`requests: ${log}`);
await say({type:"session.stopped",configuration_id});await settle();
document.visibilityState="hidden";document.visibilityState="visible";
assert(!(await onScreenVisibilityChange()),"asked again after the run view ended");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_without_the_api_or_with_a_refusal_nothing_happens(self) -> None:
        result = run_page_script(SETUP + r"""
await step();await settle();
const before=node("status").textContent;
assert(await keepScreenOn(true)===false,"a browser without the lock reported one");
giveWakeLock();refuse=true;
assert(await keepScreenOn(true)===false,"a refusal reported a lock");
assert(node("status").textContent===before&&document.body.dataset.view==="during",`a refusal changed the screen: ${node("status").textContent}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_lock_granted_after_the_run_view_ended_is_let_go_at_once(self) -> None:
        result = run_page_script(SETUP + r"""
let grant;Object.defineProperty(globalThis,"navigator",{value:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})},wakeLock:{request:()=>new Promise(resolve=>{grant=resolve;})}},configurable:true});
await step();await settle();
await say({type:"session.stopped",configuration_id});
const late=new FakeLock();grant(late);await settle();await settle();
assert(late.released,"a lock granted after the end was kept");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
