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
