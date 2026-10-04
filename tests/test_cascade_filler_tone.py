"""The waiting cue is a short browser tone by default, not a spoken sentence.

``VOINEY_LAB_CASCADE_FILLER_MODE=tone`` (the default) sends ``filler.tone`` and the browser
makes the sound; nothing is synthesized, so the cue never waits on a TTS call.
``VOINEY_LAB_CASCADE_FILLER_MODE=phrase`` brings back the old sentence unchanged.
The scheduler is driven directly and through ``run_turn_safely``, and the page
script is driven in Node with a fake Web Audio context.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab.cascade_filler import (
    FILLER_PHRASES,
    CascadeFiller,
    cascade_filler_mode,
)
from voiney_lab.configuration import ConfigurationError
from voiney_lab.language import Transcription
from voiney_lab.server import ListenerSession, run_turn_safely
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.test_screen_cleanup import run_page_script

ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def make_filler(*, mode, current=True, with_tone=True):
    gate = asyncio.Event()
    record = {"events": [], "audio": [], "clears": [], "tones": [], "calls": []}

    async def sleep(_):
        await gate.wait()

    async def synthesize(text, language):
        record["calls"].append((text, language))
        return b"\x00\x00" * 320

    async def send_audio(turn, generation, pcm):
        record["audio"].append((turn, generation))

    async def send_event(kind, **fields):
        record["events"].append((kind, fields))

    async def send_clear(turn, generation):
        record["clears"].append((turn, generation))

    async def send_tone(turn, generation):
        record["tones"].append((turn, generation))

    filler = CascadeFiller(
        turn_id=3, generation=7, language="ko", delay_ms=700,
        synthesize=synthesize, send_audio=send_audio, send_event=send_event,
        send_clear=send_clear, is_current=lambda turn, generation: current,
        clock=FakeClock(), sleep=sleep, mode=mode,
        send_tone=send_tone if with_tone else None,
    )
    return filler, gate, record


async def settle():
    for _ in range(3):
        await asyncio.sleep(0)


class FillerModeSettingTests(unittest.TestCase):
    def test_tone_is_the_default_and_phrase_brings_the_sentence_back(self):
        self.assertEqual(cascade_filler_mode({}), "tone")
        self.assertEqual(cascade_filler_mode({"VOINEY_LAB_CASCADE_FILLER_MODE": ""}), "tone")
        self.assertEqual(
            cascade_filler_mode({"VOINEY_LAB_CASCADE_FILLER_MODE": " Phrase "}), "phrase")
        self.assertEqual(cascade_filler_mode({"VOINEY_LAB_CASCADE_FILLER_MODE": "tone"}), "tone")

    def test_an_unknown_mode_is_refused_not_guessed(self):
        with self.assertRaises(ConfigurationError):
            cascade_filler_mode({"VOINEY_LAB_CASCADE_FILLER_MODE": "beep"})

    def test_a_tone_filler_cannot_be_built_without_a_way_to_send_it(self):
        with self.assertRaises(ValueError):
            make_filler(mode="tone", with_tone=False)
        with self.assertRaises(ValueError):
            make_filler(mode="chime")


class ToneFillerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_turn_gets_one_tone_and_no_synthesis(self):
        filler, gate, record = make_filler(mode="tone")
        filler.start()
        await settle()
        gate.set()
        await settle()
        self.assertEqual(record["tones"], [(3, 7)])
        self.assertEqual(record["calls"], [])
        self.assertEqual(record["audio"], [])
        self.assertEqual(filler.snapshot.outcome, "played")
        self.assertTrue(all(fields["cue"] == "tone"
                            for kind, fields in record["events"]))
        await filler.primary_ready()
        await filler.primary_ready()
        # The answer still clears whatever is left of the tone, exactly once.
        self.assertEqual(record["clears"], [(3, 7)])
        self.assertEqual(record["tones"], [(3, 7)])

    async def test_answer_before_the_delay_sends_no_tone(self):
        filler, _, record = make_filler(mode="tone")
        filler.start()
        await settle()
        await filler.primary_ready()
        self.assertEqual(record["tones"], [])
        self.assertEqual(record["clears"], [])
        self.assertEqual(filler.snapshot.outcome, "cancelled")

    async def test_a_superseded_turn_sends_no_tone(self):
        filler, gate, record = make_filler(mode="tone", current=False)
        filler.start()
        gate.set()
        await settle()
        self.assertEqual(record["tones"], [])
        self.assertEqual(record["events"], [])

    async def test_phrase_mode_still_says_the_old_sentence(self):
        filler, gate, record = make_filler(mode="phrase")
        filler.start()
        await settle()
        gate.set()
        await settle()
        self.assertEqual(record["calls"], [(FILLER_PHRASES["ko"], "ko")])
        self.assertEqual(record["audio"], [(3, 7)])
        self.assertEqual(record["tones"], [])
        self.assertEqual(record["events"][-1][1]["cue"], "phrase")


class Socket:
    def __init__(self) -> None:
        self.text = []
        self.binary = []

    async def send_text(self, value):
        self.text.append(json.loads(value))

    async def send_bytes(self, value):
        self.binary.append(value)


class ToneThroughTheTurnTests(unittest.TestCase):
    """``run_turn_safely`` is the production boundary that owns the filler."""

    def run_slow_turn(self, environment):
        session = ListenerSession(tool_context=ToolContext(
            Path("/trusted/catalog.sqlite"), None, "ko", "test_only"))
        session.active = True
        session.language_mode = "auto"
        session.active_turn_id = 1
        session.detector.state = TurnState.PROCESSING
        socket = Socket()

        def slow_empty_transcript(*_args, **_kwargs):
            time.sleep(0.4)  # past VOINEY_LAB_CASCADE_FILLER_DELAY_MS=100
            return Transcription("", "ko")

        with patch.dict("os.environ", {
            "VOINEY_LAB_CASCADE_FILLER_DELAY_MS": "100", **environment,
        }), patch(
            "voiney_lab.server.transcribe", side_effect=slow_empty_transcript,
        ), patch(
            "voiney_lab.server.synthesize", return_value=b"\x00\x00" * 320,
        ) as tts:
            asyncio.run(run_turn_safely(socket, session, b"\0\0", 1, 1))
        return socket, tts

    def test_default_turn_sends_a_tone_and_synthesizes_nothing(self):
        socket, tts = self.run_slow_turn({"VOINEY_LAB_CASCADE_FILLER_MODE": ""})
        kinds = [item["type"] for item in socket.text]
        self.assertIn("filler.tone", kinds)
        self.assertNotIn("filler.audio.start", kinds)
        self.assertEqual(socket.binary, [])
        tts.assert_not_called()
        tone = next(item for item in socket.text if item["type"] == "filler.tone")
        self.assertEqual((tone["turn_id"], tone["generation"]), (1, 0))
        played = [item for item in socket.text
                  if item["type"] == "turn.filler" and item["outcome"] == "played"]
        self.assertEqual([item["cue"] for item in played], ["tone"])

    def test_phrase_setting_brings_back_the_spoken_sentence(self):
        socket, tts = self.run_slow_turn({"VOINEY_LAB_CASCADE_FILLER_MODE": "phrase"})
        kinds = [item["type"] for item in socket.text]
        self.assertIn("filler.audio.start", kinds)
        self.assertNotIn("filler.tone", kinds)
        tts.assert_called_once_with(FILLER_PHRASES["ko"], "ko")


TONE_PAGE = r"""
const made=[];
function param(){const calls=[];return{calls,setValueAtTime(v,t){calls.push(["set",v,t])},linearRampToValueAtTime(v,t){calls.push(["ramp",v,t])}}}
playContext={state:"running",currentTime:10,destination:{name:"destination"},resume:async()=>{},
 createOscillator(){const osc={kind:"osc",type:null,frequency:param(),connected:[],starts:[],stops:[],onended:null,connect(n){this.connected.push(n)},disconnect(){},start(t){this.starts.push(t)},stop(t){this.stops.push(t)}};made.push(osc);return osc},
 createGain(){const gain={kind:"gain",gain:param(),connected:[],connect(n){this.connected.push(n)},disconnect(){}};made.push(gain);return gain},
 createBuffer:()=>({getChannelData:()=>new Float32Array(320)}),createBufferSource:()=>({connect(){},start(){},stop(){},disconnect(){}})};
cascadeGain=null;
const configuration_id=71;acceptedSessionConfiguration={configuration_id,mode:"cascade",language:"ko"};sessionActive=true;pipelineMode="cascade";
const generation=sessionGeneration;
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:1,generation:5})},generation,socket);
"""


class TonePageTests(unittest.TestCase):
    def test_tone_is_short_quiet_and_stopped_by_the_answer(self):
        result = run_page_script(TONE_PAGE + r"""
await onMessage({data:JSON.stringify({type:"filler.tone",configuration_id,turn_id:1,generation:5})},generation,socket);
const osc=made.find(item=>item.kind==="osc"),gain=made.find(item=>item.kind==="gain");
assert(osc&&gain,"no tone was made");
assert(osc.starts.length===1&&osc.stops.length===1,"tone was not started and scheduled to stop once");
const length=osc.stops[0]-osc.starts[0];
assert(length>=0.1&&length<=0.2,`tone length ${length}s is outside 0.1-0.2 s`);
const peak=Math.max(...gain.gain.calls.map(call=>call[1]));
assert(peak>0&&peak<=0.1,`tone peak gain ${peak} is not quiet`);
assert(osc.connected[0]===gain&&gain.connected[0]===playContext.destination,"tone is not routed through its envelope");
assert(fillerSource===osc,"tone is not the current waiting cue");
await onMessage({data:JSON.stringify({type:"audio.segment.start",turn_id:1,generation:5,segment_index:0,frame_count:1})},generation,socket);
assert(osc.stops.length===2&&fillerSource===null,"the answer did not stop the tone");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tone_is_not_played_over_an_answer_or_for_another_session(self):
        result = run_page_script(TONE_PAGE + r"""
await onMessage({data:JSON.stringify({type:"filler.tone",configuration_id:72,turn_id:1,generation:5})},generation,socket);
assert(made.length===0,"a tone for another configuration was played");
await onMessage({data:JSON.stringify({type:"filler.tone",configuration_id,turn_id:1,generation:4})},generation,socket);
assert(made.length===0,"a tone for a superseded generation was played");
playing=true;
await onMessage({data:JSON.stringify({type:"filler.tone",configuration_id,turn_id:1,generation:5})},generation,socket);
assert(made.length===0,"a tone was played over the answer");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_listening_label_is_brief_and_the_sentence_label_is_kept(self):
        result = run_page_script(TONE_PAGE + r"""
const timers=[];globalThis.setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length};
await onMessage({data:JSON.stringify({type:"transcript",configuration_id,turn_id:1,generation:5,text:"다음"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.filler",configuration_id,turn_id:1,generation:5,outcome:"played",cue:"tone"})},generation,socket);
const card=turnNode(1,generation),status=card.querySelector(".filler-status");
assert(status.textContent==="● 듣고 있어요"&&status.hidden===false,`listening label missing: ${status.textContent}`);
const timer=timers.find(item=>item.ms>1000&&item.ms<=1500);
assert(timer,`no brief timer: ${JSON.stringify(timers.map(item=>item.ms))}`);
timer.fn();
assert(status.textContent===""&&status.hidden===true,"listening label outlived its moment");
await onMessage({data:JSON.stringify({type:"turn.filler",configuration_id,turn_id:1,generation:5,outcome:"played",cue:"tone"})},generation,socket);
assert(status.textContent==="● 듣고 있어요","listening label not shown again");
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:1,state:"transcribing"})},generation,socket);
assert(status.textContent==="● 듣고 있어요","a working state removed the label");
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:2,state:"routing"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:3,state:"synthesizing"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:4,state:"playing"})},generation,socket);
assert(status.textContent===""&&status.hidden===true,"the answer did not remove the listening label");
await onMessage({data:JSON.stringify({type:"speech.start",turn_id:2,generation:6})},generation,socket);
await onMessage({data:JSON.stringify({type:"transcript",configuration_id,turn_id:2,generation:6,text:"다음"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.filler",configuration_id,turn_id:2,generation:6,outcome:"played",cue:"phrase"})},generation,socket);
const sentence=turnNode(2,generation).querySelector(".filler-status");
assert(sentence.textContent==="대기 안내 · 재생됨"&&sentence.hidden===false,"phrase mode label changed");
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:2,generation:6,revision:1,state:"transcribing"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:2,generation:6,revision:2,state:"routing"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:2,generation:6,revision:3,state:"synthesizing"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:2,generation:6,revision:4,state:"playing"})},generation,socket);
assert(sentence.textContent==="대기 안내 · 재생됨","phrase mode record was removed");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_empty_status_is_not_drawn(self):
        css = (ROOT / "src" / "voiney_lab" / "static" / "app.css").read_text(
            encoding="utf-8")
        self.assertIn(".turn .filler-status[hidden]{display:none}", css)


if __name__ == "__main__":
    unittest.main()
