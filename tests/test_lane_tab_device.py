"""Lane TAB decision 3 (2026-10-23): what a tablet's browser holds back until a touch.

Three places where Safari and Chrome on a tablet can stop a run, read
through the page's own script (contract-tested in node; a person checks
the real devices with docs/PILOT_COMPARISON.md's tablet list):

1. The screen kept on: the start press itself asks for the screen wake lock
   (a browser may grant it only during a touch), holds it while the start is
   under way and lets it go when no run follows; a tap during the run asks
   again when the browser let the lock go.
2. The sound: the start press makes and resumes the playback context in the
   touch; when the browser still holds a context back, the session goes on,
   says so on the screen and asks for one touch, and that touch resumes the
   sound and only then tells the server the audio is ready (once).
3. The microphone refused: the words say where to turn it back on for the
   device in hand (iPad Safari, the home-screen app, Android Chrome and its
   system setting); the desktop keeps its sentence. A refusal at the start no
   longer shows the browser's English message, and a page opened without
   HTTPS says so instead of a script error.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, run_page_script as _run_page_script

IPAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
ANDROID = "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"

def run_page_script(body: str):
    """The page script with the body; a body that never reaches its end (an await the browser never settles) fails."""
    result = _run_page_script(body + '\nconsole.log("TEST-END");')
    if result.returncode == 0 and "TEST-END" not in result.stdout:
        result.returncode = 3
        result.stderr = "the test body did not reach its end (a promise never settled)\n" + result.stderr
    return result


SETUP = r"""
const configuration_id=91;
document.body=new Element("body");document.visibilityState="visible";
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
const later=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const log=[];
class FakeLock{constructor(){this.released=false;this.listeners={};}addEventListener(kind,fn){(this.listeners[kind]||=[]).push(fn);}async release(){if(this.released)return;this.released=true;log.push("release");for(const fn of this.listeners.release||[])fn();}}
let lastLock=null;
// The browser's own: a wake lock, a microphone, and audio contexts that run only once a touch let them.
let touched=false,contexts=[],gum=async()=>({getTracks:()=>[{stop(){}}],getAudioTracks:()=>[{stop(){},getSettings:()=>({})}]});
function device(userAgent,extra={}){Object.defineProperty(globalThis,"navigator",{value:{userAgent,maxTouchPoints:userAgent.includes("Macintosh")?5:0,...extra,mediaDevices:{getSupportedConstraints:()=>({}),getUserMedia:(...args)=>gum(...args),enumerateDevices:async()=>[]},wakeLock:{request:async kind=>{log.push(`request ${kind}`);lastLock=new FakeLock();return lastLock;}}},configurable:true});}
globalThis.AudioContext=class{constructor(){this.state="suspended";this.sampleRate=48000;this.destination={};this.resumes=0;this.audioWorklet={addModule:async()=>{}};contexts.push(this);}
 resume(){this.resumes++;if(!touched)return new Promise(()=>{});this.state="running";return Promise.resolve();}
 suspend(){this.suspends=(this.suspends||0)+1;this.state="suspended";return Promise.resolve();}
 createMediaStreamSource(){return {connect(){},disconnect(){}};}createGain(){return {gain:{value:1},connect(){},disconnect(){}};}close(){this.state="closed";return Promise.resolve();}};
globalThis.AudioWorkletNode=class{constructor(){this.port={onmessage:null};}disconnect(){}};
const sent=()=>socket.sent.filter(item=>typeof item==="string").map(item=>JSON.parse(item));
let revision=0;
const state=(extra={})=>({attached:true,protocol_id:"p-tab",revision_id:"pdf-1",display_name:"In-gel digestion",development_only:false,readiness_status:"guidance_ready",active:true,current_step_label:"4",current_step_id:"step-4",total_steps:6,at_final_step:false,block_reason:null,revision:++revision,display_summary:"Incubate.",primary_summary:"둡니다.",source_language:"en",spoken_summary:null,warning_texts:[],warning_presentations:[],visual_assets:[],visual_status:"unavailable",source_page_refs:[4],workflow_status:"running",...extra});
async function say(message){await onMessage({data:JSON.stringify(message)},sessionGeneration,socket);}
// The start sheet at its last step, with a protocol that can run; the start itself waits for the server.
let starts=0;
function sheetAtStart(){
 protocolCatalog.set("p-tab",{protocol_id:"p-tab",title:"In-gel digestion",available_for_execution:true});
 node("protocol-id").value="p-tab";lastProtocolReview={protocol_id:"p-tab",available_for_execution:true};currentProtocolReviewId="p-tab";
 startReviewedProtocol=()=>{starts++;return new Promise(()=>{});};
 wizard.step="start";wizard.busy=false;
}
// The server accepted the start: session.ready, as the socket brings it.
async function ready(){
 socket=new WS();pendingSessionConfiguration={configuration_id,mode:"cascade",language:"ko",protocol_id:"p-tab"};
 await say({type:"session.ready",configuration_id,generation:2,mode:"cascade",language:"ko",protocol_id:"p-tab",revision_id:"pdf-1"});
}
"""


class ScreenOnTests(unittest.TestCase):

    def test_the_start_press_asks_for_the_screen_in_the_touch_and_lets_it_go_when_no_run_follows(self) -> None:
        result = run_page_script(SETUP + f"device({IPAD!r});" + r"""
SCREEN_WAKE_START_MS=40;sheetAtStart();
void wizardPrimary();
// In the press itself, before anything was awaited.
assert(JSON.stringify(log)==='["request screen"]',`not asked in the touch: ${log}`);
assert(starts===1,"the start was not sent");
await settle();
assert(document.body.dataset.view!=="during"&&!log.includes("release"),`let go before the start could answer: ${log}`);
await later(80);
assert(JSON.stringify(log)==='["request screen","release"]',`a start with no run kept the screen on: ${log}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_run_that_follows_keeps_the_lock_asked_for_in_the_touch(self) -> None:
        result = run_page_script(SETUP + f"device({IPAD!r});" + r"""
SCREEN_WAKE_START_MS=40;sheetAtStart();
void wizardPrimary();await settle();
socket=new WS();acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:2,protocol_id:"p-tab",revision_id:"pdf-1"};sessionActive=true;
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state()});await settle();
assert(document.body.dataset.view==="during","no run view");
await later(80);
assert(JSON.stringify(log)==='["request screen"]',`the run asked again or let the lock go: ${log}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_tap_during_the_run_asks_again_for_a_lock_the_browser_let_go(self) -> None:
        result = run_page_script(SETUP + f"device({ANDROID!r});" + r"""
onUserGesture();
assert(log.length===0,`asked with no run: ${log}`);
socket=new WS();acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko",server_generation:2,protocol_id:"p-tab",revision_id:"pdf-1"};sessionActive=true;
await say({type:"protocol.fixture.state",configuration_id,action:"start",state:state()});await settle();
assert(JSON.stringify(log)==='["request screen"]',`the run view: ${log}`);
await lastLock.release();
onUserGesture();await settle();
assert(log.filter(item=>item==="request screen").length===2,`a tap did not ask again: ${log}`);
onUserGesture();await settle();
assert(log.filter(item=>item==="request screen").length===2,`asked again while held: ${log}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class SoundUnlockTests(unittest.TestCase):

    def test_the_start_press_makes_and_resumes_the_sound_in_the_touch(self) -> None:
        result = run_page_script(SETUP + f"device({IPAD!r});" + r"""
sheetAtStart();
assert(!playContext,"a sound context before any press");
touched=true;void wizardPrimary();
assert(playContext&&contexts.length===1&&playContext.resumes===1,`not made and resumed in the touch: ${contexts.length} ${playContext?.resumes}`);
assert(playContext.state==="running","the touch did not start the sound");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_start_that_does_not_go_lets_the_sound_made_in_its_press_rest(self) -> None:
        # Lane TAB self-review: a running context left on the sheet keeps a tablet's audio awake.
        result = run_page_script(SETUP + f"device({IPAD!r});" + r"""
sheetAtStart();touched=true;
startReviewedProtocol=async()=>{starts++;return START_NOT_SENT_WORDS.not_runnable;};
await wizardPrimary();await settle();
assert(starts===1&&playContext?.suspends===1&&playContext.state==="suspended",`the sound kept running after a refused start: ${playContext?.state} ${playContext?.suspends}`);
assert(node("audio-unlock").hidden!==false,"asked for a touch with no session");
// The next press resumes it, in the touch.
startReviewedProtocol=()=>{starts++;return new Promise(()=>{});};
void wizardPrimary();
assert(playContext.state==="running"&&contexts.length===1,`the next press did not resume the same sound: ${playContext.state} ${contexts.length}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_sound_held_back_does_not_stop_the_session_and_one_touch_starts_it(self) -> None:
        result = run_page_script(SETUP + f"device({IPAD!r});" + r"""
AUDIO_RESUME_WAIT_MS=20;
await ready();
// The browser held both contexts back: the session goes on, the screen asks for a touch, the server is not told yet.
assert(sessionActive===true,"the session waited for a sound the browser holds back");
assert(!sent().some(item=>item.type==="client.audio_ready"),"audio reported ready while held back");
assert(sent().every(item=>item.type!=="session.stop"),"the session was stopped");
const box=node("audio-unlock");
assert(box.hidden===false,"no word on the screen about the sound");
// One touch anywhere.
touched=true;onUserGesture();await settle();await settle();
const readies=sent().filter(item=>item.type==="client.audio_ready");
assert(readies.length===1&&readies[0].configuration_id===configuration_id&&readies[0].generation===2&&readies[0].audio_context_state==="running",`after the touch: ${JSON.stringify(readies)}`);
assert(box.hidden===true,"the word stayed after the sound started");
onUserGesture();await settle();await settle();
assert(sent().filter(item=>item.type==="client.audio_ready").length===1,"audio reported ready twice");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        # The word itself (the node harness does not read markup text).
        self.assertIn(
            '<div id="audio-unlock" class="audio-unlock" role="alert" hidden>\n              <span id="audio-unlock-text">소리가 꺼져 있어요. 화면을 한 번 눌러 주세요.</span>\n              <button id="audio-unlock-button" type="button">소리 켜기</button>',
            INDEX.read_text(encoding="utf-8"),
        )

    def test_sound_that_runs_at_once_reports_ready_as_before(self) -> None:
        result = run_page_script(SETUP + f"device({DESKTOP!r});" + r"""
touched=true;await ready();
assert(sessionActive===true,"no session");
assert(sent().filter(item=>item.type==="client.audio_ready").length===1,"audio not reported ready");
assert(node("audio-unlock").hidden!==false,"asked for a touch with the sound on");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_context_the_browser_interrupts_during_the_run_asks_for_a_touch_again(self) -> None:
        result = run_page_script(SETUP + f"device({IPAD!r});" + r"""
touched=true;await ready();
const box=node("audio-unlock");assert(box.hidden!==false,"held at the start");
// The browser interrupts the sound (a call, the screen locked) and the page comes back.
touched=false;for(const context of contexts)context.state="interrupted";
document.visibilityState="visible";onAudioVisibilityChange();await settle();
assert(box.hidden===false,"no word after the browser interrupted the sound");
touched=true;onUserGesture();await settle();await settle();
assert(box.hidden===true,"the touch did not bring the sound back");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class MicRefusedTests(unittest.TestCase):

    def _wizard_words(self, user_agent: str, message: str = "Permission denied", extra: str = "{}") -> str:
        result = run_page_script(SETUP + f"device({user_agent!r},{extra});" + f"""
gum=async()=>{{throw Object.assign(new Error({message!r}),{{name:"NotAllowedError"}});}};
assert(await wizardCheckMic()===false,"the check passed");
console.log("WORDS:"+wizard.note);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.split("WORDS:", 1)[1].split("\n", 1)[0].strip()

    def test_ipad_safari_says_where_to_allow_the_microphone(self) -> None:
        words = self._wizard_words(IPAD)
        self.assertEqual(words, "마이크가 막혀 있어요. 주소창의 페이지 메뉴(aA) → 웹사이트 설정 → 마이크를 ‘허용’으로 바꾼 뒤 새로 고쳐 주세요.")

    def test_the_home_screen_app_on_an_ipad_says_the_settings_app(self) -> None:
        words = self._wizard_words(IPAD, extra="{standalone:true}")
        self.assertEqual(words, "마이크가 막혀 있어요. 설정 앱 → Safari → 마이크를 ‘허용’으로 바꾼 뒤 앱을 다시 열어 주세요.")

    def test_android_chrome_says_the_site_permission_or_the_system_setting(self) -> None:
        self.assertEqual(self._wizard_words(ANDROID), "마이크가 막혀 있어요. 주소창 왼쪽 아이콘 → 권한 → 마이크를 ‘허용’으로 바꾼 뒤 새로 고쳐 주세요.")
        self.assertEqual(
            self._wizard_words(ANDROID, "Permission denied by system"),
            "마이크가 막혀 있어요. 기기 설정 → 애플리케이션 → Chrome → 권한 → 마이크를 ‘허용’으로 바꿔 주세요.",
        )

    def test_the_desktop_keeps_its_sentence(self) -> None:
        self.assertEqual(self._wizard_words(DESKTOP), "마이크를 쓸 수 없어요. 브라우저의 마이크 권한을 허용해 주세요.")

    def test_a_refusal_at_the_start_shows_the_words_not_the_browsers_message(self) -> None:
        result = run_page_script(SETUP + f"device({ANDROID!r});" + r"""
touched=true;gum=async()=>{throw Object.assign(new Error("Permission denied"),{name:"NotAllowedError"});};
await ready();
assert(sessionActive===false,"a session without a microphone");
assert(sent().some(item=>item.type==="session.stop"),"the server was not told to stop");
const status=node("status").textContent;
assert(status==="오류: 마이크가 막혀 있어요. 주소창 왼쪽 아이콘 → 권한 → 마이크를 ‘허용’으로 바꾼 뒤 새로 고쳐 주세요.",status);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_page_without_https_says_so_at_the_start(self) -> None:
        result = run_page_script(SETUP + f"device({IPAD!r});" + r"""
touched=true;Object.defineProperty(globalThis,"navigator",{value:{userAgent:navigator.userAgent,maxTouchPoints:5},configurable:true});
await ready();
const status=node("status").textContent;
assert(status==="오류: 이 주소에서는 마이크를 쓸 수 없어요. HTTPS 주소로 열어 주세요.",status);
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
