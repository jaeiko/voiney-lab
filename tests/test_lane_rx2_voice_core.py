"""Lane RX2, person's decision 4 (2026-10-25): one voice code for both screens.

The socket, the microphone, the answer's audio, the server's clock and the
screen lock moved unchanged out of ``static/index.html``'s own script into
``static/voice-core.js``, a classic script that page loads first; the old
screen's tests run both (``tests/static_page.py``) and pass as before. The new
screen (``web/src/voice/engine.ts``) loads the same file and defines the
names it calls (``VOICE_CORE_HOST_NAMES``).
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.static_page import INDEX, ROOT, VOICE_CORE, inline_script

ENGINE = ROOT / "web" / "src" / "voice" / "engine.ts"

# What moved: every function and top-level name of the voice, socket, clock and screen-lock code.
MOVED_FUNCTIONS = (
    "sendControl", "init", "serverNowMs", "formatElapsedClock", "stepTimerRemainingSeconds", "browserNowMs",
    "keepScreenOn", "onScreenVisibilityChange", "keepScreenOnForStart",
    "resamplePcm16", "readStoredMicDevice", "storeMicDevice", "listMicDevices", "chosenMicDeviceId", "renderMicDevices",
    "currentMicTrack", "openMicStream", "reportMicSettings", "agcMode", "storeAgcMode", "renderAgcStatus",
    "applyAutoGainControl", "agcForCalibration", "onMicTrackEnded", "watchMicTrack", "showMicRecovery", "hideMicRecovery",
    "micErrorText", "micDeviceKind", "homeScreenApp", "micBlockedWords", "micStartError", "micReconnectNeed",
    "scheduleMicReconnect", "autoReconnectMic", "reconnectMic", "ensureAudio", "watchAudioContext", "resumeAudioWithin",
    "sendAudioReady", "audioHeld", "renderAudioUnlock", "afterAudioResumed", "resumeHeldAudio", "primeRunStart",
    "restPrimedAudio", "onUserGesture", "onAudioVisibilityChange", "closeAudio",
    "clearFillerPlayback", "setCascadeGain", "resetCascadeInterruption", "provisionalCascadeInterruption",
    "rejectCascadeInterruption", "resetCascadePlayback", "clearPlayback", "interruptCascadePlayback",
    "cascadeTurnKey", "cascadeTurnInterrupted", "currentCascadeMessage",
    "join", "playLastReplayableAudio", "keepReplayableAudio", "pump", "playFiller", "playTimerNoticeTone", "playFillerTone",
)
MOVED_NAMES = (
    "socket", "playContext", "sessionActive", "sessionGeneration", "playbackEpoch", "visibleState", "micSignalSeen",
    "interruptedCascadeTurns", "cascadeInterruption", "nextConfigurationId", "pendingSessionConfiguration",
    "acceptedSessionConfiguration", "queued", "audioRecoveryTurns", "ACCOUNT_SOCKET_CLOSE_CODE", "screenWakeLock",
    "SCREEN_WAKE_START_MS", "MIC_DEVICE_STORAGE_KEY", "AGC_MODE_STORAGE_KEY", "AUDIO_RESUME_WAIT_MS",
    "REPLAY_AUDIO_MAX_BYTES", "FILLER_TONE", "TIMER_NOTICE_TONE",
)

# Names any browser gives a classic script.
BROWSER = {
    "globalThis", "document", "navigator", "localStorage", "location", "WebSocket", "AudioContext", "AudioWorkletNode",
    "Promise", "Math", "Number", "String", "Boolean", "Object", "Array", "Date", "JSON", "Error", "Set", "Map", "WeakSet",
    "Int16Array", "Float32Array", "Uint8Array", "ArrayBuffer", "setTimeout", "clearTimeout", "setInterval",
    "clearInterval", "isFinite", "parseInt",
}
KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "typeof", "await", "new", "async"}
# A Promise executor's own parameters.
PARAMETERS = {"resolve", "reject"}


def _code(text: str) -> str:
    """The script with comments and string contents taken out, for name scanning."""
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r'"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`', "''", text)


def _defined(text: str) -> set[str]:
    code = _code(text)
    names = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", code))
    for match in re.finditer(r"\b(?:let|const|var)\s+([^;]+)", code):
        for part in re.split(r",(?![^{(\[]*[})\]])", match.group(1)):
            found = re.match(r"\s*([A-Za-z_$][\w$]*)", part)
            if found:
                names.add(found.group(1))
    return names


def _top_level_defines(text: str, name: str) -> bool:
    """Whether a line of the script (its top level, unindented) defines ``name``."""
    escaped = re.escape(name)
    for line in text.split("\n"):
        if re.match(rf"(?:async\s+)?function\s+{escaped}\(", line):
            return True
        declared = re.match(r"(?:let|const|var)\s+(.*)", line)
        if declared and re.search(rf"(?:^|,)\s*{escaped}\s*(?:=|,|;)", declared.group(1)):
            return True
    return False


def _host_names() -> list[str]:
    block = VOICE_CORE.read_text(encoding="utf-8").split("var VOICE_CORE_HOST_NAMES=Object.freeze([", 1)[1].split("]);", 1)[0]
    return re.findall(r'"([^"]+)"', block)


class SharedModuleTests(unittest.TestCase):
    def test_the_server_serves_the_shared_file(self) -> None:
        from voiney_lab.server import app

        with TestClient(app) as client:
            response = client.get("/voice-core.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertIn("async function ensureAudio(", response.text)

    def test_the_old_screen_loads_it_before_its_own_script_and_connection_js_after(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        core = html.index('<script src="/voice-core.js"></script>')
        own = html.index("<script>")
        self.assertLess(core, own)
        self.assertLess(own, html.index('<script src="/connection.js"></script>'))
        self.assertEqual(html.count('<script src="/voice-core.js"></script>'), 1)

    def test_every_moved_name_is_defined_once_and_in_the_shared_file(self) -> None:
        core = _defined(VOICE_CORE.read_text(encoding="utf-8"))
        page = inline_script(INDEX.read_text(encoding="utf-8"))
        for name in (*MOVED_FUNCTIONS, *MOVED_NAMES):
            self.assertIn(name, core, name)
            self.assertFalse(_top_level_defines(page, name), f"{name} is still defined in index.html")
        # The split lines keep the rest of their names on the page.
        self.assertIn("const turns=new Map(),provisionalTurns=new Map()", page)
        self.assertIn("let accountRecheck=null;", page)

    def test_the_shared_file_reaches_only_its_own_names_the_browsers_and_the_hosts(self) -> None:
        text = VOICE_CORE.read_text(encoding="utf-8")
        code = _code(text)
        known = _defined(text) | BROWSER | KEYWORDS | PARAMETERS | set(_host_names())
        called = set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", code))
        self.assertEqual(sorted(called - known), [])

    def test_every_host_name_is_defined_by_the_old_screen(self) -> None:
        page = inline_script(INDEX.read_text(encoding="utf-8"))
        for name in _host_names():
            self.assertTrue(_top_level_defines(page, name), name)

    def test_it_is_not_strict_as_the_page_script_was_not(self) -> None:
        self.assertNotIn('"use strict"', VOICE_CORE.read_text(encoding="utf-8"))
        self.assertNotIn('"use strict"', inline_script(INDEX.read_text(encoding="utf-8")))

    def test_it_loads_alone_beside_a_page_that_defines_only_the_host_names(self) -> None:
        names = _host_names()
        stubs = "".join(f"globalThis[{name!r}]=(...args)=>{{calls.push([{name!r},...args]);return null;}};\n" for name in names)
        harness = (
            "const calls=[];const assert=(ok,m)=>{if(!ok)throw new Error(m)};\n"
            "globalThis.document={addEventListener(){},getElementById:()=>null,visibilityState:'visible'};\n"
            "class WS{static OPEN=1;constructor(){this.readyState=1;this.sent=[]}send(v){this.sent.push(v)}}globalThis.WebSocket=WS;\n"
            + stubs + VOICE_CORE.read_text(encoding="utf-8") +
            "\nsocket=new WS();sendControl('session.stop');assert(JSON.parse(socket.sent[0]).type==='session.stop','sendControl');\n"
            "assert(formatElapsedClock(75)==='1:15'&&formatElapsedClock(3725)==='1:02:05','clock');\n"
            "assert(resamplePcm16(new Float32Array([0,.5,-.5,1]),32000,16000).length===2,'resample');\n"
            "assert(typeof init==='function'&&typeof ensureAudio==='function'&&typeof pump==='function','engine');\n"
            "assert(VOICE_CORE_HOST_NAMES.includes('onMessage'),'host names');\n"
            "console.log('ok');\n"
        )
        result = subprocess.run(["node", "-"], cwd=ROOT, text=True, input=harness, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()


# --- The new screen's side (web/src/voice/engine.ts) ---------------------------

CONNECTION = ROOT / "src" / "voiney_lab" / "static" / "connection.js"
# What connection.js reaches for on its page beyond voice-core.js (its forgetTurns, its restore and give-up).
CONNECTION_HOST_NAMES = (
    "turns", "provisionalTurns", "pendingFillers", "toolEvents", "visualEvents", "researchEvents", "brainEvents",
    "greetingEvents", "webEvents", "drawingEvents", "turnProgress", "turnServerGenerations", "turnHoldEvidence",
    "interruptedTurns", "runTurns", "runCardKey", "curatedProtocolState", "curatedStateRevision",
    "currentExperimentSessionId", "currentExperimentSessionVersion", "currentExperimentSessionStatus",
    "RESUMABLE_EXPERIMENT_STATUSES", "renderState", "renderRunView", "removeProvisionalTurns",
    "invalidateCuratedProtocolState", "stopWorkflowTimer", "stopReportPolling", "workspaceFetch",
)


def _engine_defines(name: str) -> bool:
    text = ENGINE.read_text(encoding="utf-8")
    host = text.split("export function installVoiceHost", 1)[1].split("\nlet loading", 1)[0]
    escaped = re.escape(name)
    if name in {"turns", "provisionalTurns", "pendingFillers", "toolEvents", "visualEvents", "researchEvents", "brainEvents", "webEvents", "drawingEvents", "turnProgress", "turnServerGenerations", "turnHoldEvidence", "runTurns"}:
        return f'"{name}"' in text.split("const TURN_MAPS", 1)[1].split("\n", 1)[0]
    if name in {"greetingEvents", "interruptedTurns"}:
        return f'"{name}"' in text.split("const TURN_SETS", 1)[1].split("\n", 1)[0]
    return re.search(rf"(?:^|[\s,{{])(?:{escaped}\s*:|{escaped},|{escaped}\n)", host, re.M) is not None or (name == "$" and "$: (id: string)" in host)


def _minified(text: str) -> str:
    """A line of code without spaces, the host's ``g.`` and TypeScript's ``: any`` -- to compare the same conditions."""
    return re.sub(r"(?<![\w$])g\.", "", re.sub(r"\s+", "", text)).replace(":any", "")


class NewScreenHostTests(unittest.TestCase):
    def test_the_new_screen_defines_every_name_the_shared_files_reach_for(self) -> None:
        for name in (*_host_names(), *CONNECTION_HOST_NAMES):
            self.assertTrue(_engine_defines(name), name)

    def test_the_connection_names_are_the_ones_connection_js_uses(self) -> None:
        text = _code(CONNECTION.read_text(encoding="utf-8"))
        for name in CONNECTION_HOST_NAMES:
            self.assertRegex(text, rf"(?<![\w$.]){re.escape(name)}\b", name)

    def test_no_voice_calibration_during_a_run_on_either_screen(self) -> None:
        # Lane HEAR decision 1 (2026-10-25): the start of a run no longer asks for the measurement (voice-core's sendAudioReady).
        core = VOICE_CORE.read_text(encoding="utf-8")
        ready = core.split("function sendAudioReady(){", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn("requestVoiceCalibration", ready)
        self.assertIn("mic_id:micIdFor(currentMicTrack())", ready)

    def test_the_audio_and_fence_lines_read_as_the_old_screens(self) -> None:
        page = _minified(inline_script(INDEX.read_text(encoding="utf-8")))
        engine = _minified(ENGINE.read_text(encoding="utf-8"))
        old_lines = [
            'if(m.type==="filler.audio.start"){constaccepted=acceptedSessionConfiguration,valid=accepted&&accepted.mode==="cascade"&&m.configuration_id===accepted.configuration_id&&currentCascadeMessage(m)&&!cascadeTurnInterrupted(m)&&Number.isInteger(m.frame_count)&&m.frame_count>0;if(valid&&!playing&&!activeSource){receivingFiller=m;fillerFrames=[];}}',
            'if(m.type==="filler.audio.end"&&receivingFiller&&m.turn_id===receivingFiller.turn_id&&m.generation===receivingFiller.generation){constmeta=receivingFiller,data=join(fillerFrames);receivingFiller=null;fillerFrames=[];awaitplayFiller(data,meta,generation);}',
            'if(m.type==="filler.audio.clear")clearFillerPlayback(m.turn_id,m.generation);',
            'if(m.type==="state.changed"&&sessionActive&&!cascadeTurnInterrupted(m)&&(!Number.isInteger(m.turn_id)||currentCascadeMessage(m))&&m.state!=="AGENT_SPEAKING")renderState(m.state==="PROCESSING"?"THINKING":"LISTENING");',
            'if(m.type==="turn.processing"&&sessionActive&&!cascadeTurnInterrupted(m)&&(activeTurn===null||currentCascadeMessage(m)))renderState("THINKING");',
            'if(m.type==="audio.segment.end"&&!cascadeTurnInterrupted(m)&&receiving&&currentCascadeMessage(m)&&m.segment_index===receiving.segment_index&&(!Number.isInteger(m.generation)||m.generation===receiving.generation)){queued.set(m.segment_index,join(receivedFrames));receiving=null;receivedFrames=[];pump(generation);}',
            'if(m.type==="audio.complete"&&!cascadeTurnInterrupted(m)&&currentCascadeMessage(m)){audioComplete=true;pump(generation);}',
            'valid=accepted&&accepted.mode==="cascade"&&m.configuration_id===accepted.configuration_id&&Number.isInteger(m.turn_id)&&Number.isInteger(m.generation)&&Number.isInteger(m.superseding_turn_id)&&Number.isInteger(m.superseding_generation)&&m.reason==="confirmed_speech"&&m.state==="cancelled";',
            'valid=pending&&Number.isInteger(m.configuration_id)&&m.configuration_id===pending.configuration_id&&m.mode===pending.mode&&m.language===pending.language&&serverInputLanguage===pendingInputLanguage&&m.protocol_id===pending.protocol_id&&revisionValid;',
        ]
        for line in old_lines:
            line = _minified(line)
            self.assertTrue(line in page, f"index.html no longer has: {line[:80]}")
            self.assertTrue(line in engine, f"engine.ts differs from index.html in: {line[:80]}")


WEB_STYLES = ROOT / "web" / "src" / "styles"
PALETTE = {
    "#1c1c1a", "#55554f", "#ffffff", "#efefec", "#d4d4cf", "#76766f",
    "#bf2229", "#fbeaea", "#f2a900", "#7a4f00", "#fff4d6", "#5b3cc4", "#efebfb",
}


class NewStylesFollowTheDesignSystemTests(unittest.TestCase):
    """The design system (docs/UI_SPEC.md section 6) on lane RX2's stylesheets, as lane RX0's test reads its own."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.screens = (WEB_STYLES / "screens.css").read_text(encoding="utf-8")
        cls.run_css = (WEB_STYLES / "run.css").read_text(encoding="utf-8")
        cls.css = cls.screens + "\n" + cls.run_css
        cls.tokens = (WEB_STYLES / "tokens.css").read_text(encoding="utf-8")

    def test_colours_from_the_palette_only(self) -> None:
        colours = {match.lower() for match in re.findall(r"#[0-9a-fA-F]{3,8}\b", self.css)}
        self.assertEqual(colours - PALETTE, set())

    def test_no_shadow_but_the_one_taken_off_connection_js_band(self) -> None:
        shadows = re.findall(r"(box-shadow|text-shadow|drop-shadow)\s*:?\s*([^;]*)", self.css)
        self.assertEqual([shadow for shadow in shadows if shadow != ("box-shadow", "none")], [])

    def test_corners_and_weights(self) -> None:
        for value in re.findall(r"border-radius:\s*([^;]+);", self.css):
            self.assertRegex(value.strip(), r"^var\(--radius(-small)?\)$|^0$")
        self.assertEqual({int(w) for w in re.findall(r"font-weight:\s*(\d+)", self.css)} - {400, 600, 700}, set())

    def test_danger_is_for_stop_alone(self) -> None:
        self.assertNotIn("--danger", self.screens)
        for rule in re.findall(r"([^{}]+)\{[^}]*--danger[^}]*\}", self.run_css):
            self.assertRegex(rule.strip(), r"run-stop|connection-band|run-timer-offline", rule)

    def test_the_run_scale_is_decision_3(self) -> None:
        phone = self.tokens.split("@media (pointer: coarse)", 1)[0]
        tablet = self.tokens.split("@media (pointer: coarse) and (min-width: 721px) and (min-height: 600px)", 1)[1]
        for name, value in (("--run-step", "2.5rem"), ("--run-step-line", "3rem"), ("--run-timer", "6rem"), ("--run-next", "1.25rem"), ("--run-caption", "1.375rem"), ("--run-status", "1rem"), ("--run-target", "64px")):
            self.assertRegex(phone, rf"{name}:\s*{re.escape(value)};", name)
        for name, value in (("--run-step", "3.25rem"), ("--run-step-min", "2.5rem"), ("--run-timer", "8rem"), ("--run-target", "80px")):
            self.assertRegex(tablet, rf"{name}:\s*{re.escape(value)};", name)
