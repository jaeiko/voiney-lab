"""A slow turn says what the server is really doing, once, after the tone.

In tone mode a turn still pending at ``VOINEY_LAB_CASCADE_FILLER_STATUS_DELAY_MS`` (1.5 s)
hears one sentence naming its own progress state -- the state its Turn card
shows -- and nothing for states that name no work. A session never hears the
same sentence twice in a row, and each sentence is synthesized once per session.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab.cascade_filler import (
    FILLER_PHRASES,
    FILLER_STATUS_PHRASES,
    CascadeFiller,
    FillerSessionMemory,
    cascade_filler_status_delay_ms,
)
from voiney_lab.configuration import ConfigurationError
from voiney_lab.language import Transcription
from voiney_lab.server import (
    TURN_PROGRESS_TRANSITIONS,
    ListenerSession,
    run_turn_safely,
)
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.test_cascade_filler_tone import TONE_PAGE, Socket
from tests.test_screen_cleanup import run_page_script

ROOT = Path(__file__).resolve().parents[1]
CHECKING = FILLER_STATUS_PHRASES["ko"]["checking_protocol"]
ROUTING = FILLER_STATUS_PHRASES["ko"]["routing"]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class Sleeper:
    """Each sleep waits until the test releases it, in order."""

    def __init__(self) -> None:
        self.requested: list[float] = []
        self._gates: list[asyncio.Event] = []

    async def __call__(self, seconds: float) -> None:
        gate = asyncio.Event()
        self.requested.append(seconds)
        self._gates.append(gate)
        await gate.wait()

    def release(self) -> None:
        self._gates[len(self.requested) - 1].set()


async def settle():
    for _ in range(4):
        await asyncio.sleep(0)


class Turn:
    """One tone-mode filler with a status sentence, and what it sent."""

    def __init__(self, *, activity, memory=None, synthesize=None, mode="tone"):
        self.activity = activity
        self.memory = memory if memory is not None else FillerSessionMemory()
        self.sleeper = Sleeper()
        self.events, self.audio, self.clears, self.tones, self.calls = (
            [], [], [], [], [])

        async def default_synthesize(text, language):
            self.calls.append((text, language))
            return b"\x00\x00" * 320

        async def send_audio(turn, generation, pcm):
            self.audio.append((turn, generation))

        async def send_event(kind, **fields):
            self.events.append(fields)

        async def send_clear(turn, generation):
            self.clears.append((turn, generation))

        async def send_tone(turn, generation):
            self.tones.append((turn, generation))

        self.filler = CascadeFiller(
            turn_id=3, generation=7, language="ko", delay_ms=700,
            synthesize=synthesize or default_synthesize,
            send_audio=send_audio, send_event=send_event,
            send_clear=send_clear, is_current=lambda turn, generation: True,
            clock=FakeClock(), sleep=self.sleeper, mode=mode,
            send_tone=send_tone, status_delay_ms=1500,
            activity=lambda: self.activity, memory=self.memory,
        )

    async def run_past_status_delay(self):
        self.filler.start()
        await settle()
        self.sleeper.release()  # the tone delay
        await settle()
        self.sleeper.release()  # the status delay
        await settle()

    def status_events(self):
        return [item for item in self.events if item.get("cue") == "status"]


class StatusSentenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_answer_within_the_status_delay_hears_only_the_tone(self):
        turn = Turn(activity="checking_protocol")
        turn.filler.start()
        await settle()
        turn.sleeper.release()
        await settle()
        self.assertEqual(turn.tones, [(3, 7)])
        # The second wait is what is left of 1.5 s after the 0.7 s tone delay.
        self.assertEqual(len(turn.sleeper.requested), 2)
        self.assertAlmostEqual(turn.sleeper.requested[1], 0.8)
        await turn.filler.primary_ready()
        self.assertEqual(turn.calls, [])
        self.assertEqual(turn.audio, [])
        self.assertEqual(turn.status_events(), [])

    async def test_a_slow_turn_says_what_it_is_doing_once(self):
        turn = Turn(activity="checking_protocol")
        await turn.run_past_status_delay()
        self.assertEqual(turn.calls, [(CHECKING, "ko")])
        self.assertEqual(turn.audio, [(3, 7)])
        played = turn.status_events()
        self.assertEqual(len(played), 1)
        self.assertEqual(played[0]["outcome"], "played")
        self.assertEqual(played[0]["activity"], "checking_protocol")
        self.assertEqual(played[0]["text"], CHECKING)
        self.assertEqual(turn.memory.last_status_phrase, CHECKING)
        await turn.filler.primary_ready()
        self.assertEqual(turn.clears, [(3, 7)])
        self.assertEqual(turn.audio, [(3, 7)])

    async def test_a_state_that_names_no_work_says_nothing(self):
        for activity in (None, "listening", "synthesizing", "playing",
                         "complete", "blocked", "error", "not_a_state"):
            with self.subTest(activity=activity):
                turn = Turn(activity=activity)
                await turn.run_past_status_delay()
                self.assertEqual(turn.tones, [(3, 7)])
                self.assertEqual(turn.calls, [])
                self.assertEqual(turn.audio, [])
                self.assertIsNone(turn.memory.last_status_phrase)

    async def test_the_same_sentence_is_not_said_twice_in_a_row(self):
        memory = FillerSessionMemory()
        first = Turn(activity="checking_protocol", memory=memory)
        await first.run_past_status_delay()
        repeat = Turn(activity="checking_protocol", memory=memory)
        await repeat.run_past_status_delay()
        self.assertEqual(repeat.audio, [])
        self.assertEqual(repeat.tones, [(3, 7)])
        other = Turn(activity="routing", memory=memory)
        await other.run_past_status_delay()
        self.assertEqual(other.audio, [(3, 7)])
        again = Turn(activity="checking_protocol", memory=memory)
        await again.run_past_status_delay()
        self.assertEqual(again.audio, [(3, 7)])
        self.assertEqual(memory.last_status_phrase, CHECKING)

    async def test_each_sentence_is_synthesized_once_per_session(self):
        memory = FillerSessionMemory()
        calls = []

        async def synthesize(text, language):
            calls.append(text)
            return b"\x00\x00" * 320

        for activity in ("checking_protocol", "routing", "checking_protocol"):
            turn = Turn(activity=activity, memory=memory, synthesize=synthesize)
            await turn.run_past_status_delay()
            self.assertEqual(turn.audio, [(3, 7)])
        self.assertEqual(calls, [CHECKING, ROUTING])

    async def test_a_sentence_that_became_untrue_while_it_was_made_is_dropped(self):
        turn = None

        async def synthesize(text, language):
            turn.activity = "synthesizing"  # the answer's voice is now being made
            return b"\x00\x00" * 320

        turn = Turn(activity="checking_protocol", synthesize=synthesize)
        await turn.run_past_status_delay()
        self.assertEqual(turn.audio, [])
        self.assertEqual(turn.status_events(), [])
        self.assertIsNone(turn.memory.last_status_phrase)

    async def test_an_answer_that_arrives_while_the_sentence_is_made_wins(self):
        started = asyncio.Event()
        never = asyncio.Event()

        async def synthesize(text, language):
            started.set()
            await never.wait()
            return b"\x00\x00" * 320

        turn = Turn(activity="checking_protocol", synthesize=synthesize)
        await turn.run_past_status_delay()
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(turn.filler.primary_ready(), timeout=1)
        self.assertEqual(turn.audio, [])
        self.assertEqual(turn.clears, [(3, 7)])
        self.assertIsNone(turn.memory.last_status_phrase)

    async def test_a_failed_sentence_is_reported_and_leaves_the_turn_alone(self):
        async def synthesize(text, language):
            raise RuntimeError("fake TTS failure")

        turn = Turn(activity="composing", synthesize=synthesize)
        await turn.run_past_status_delay()
        self.assertEqual(turn.audio, [])
        self.assertEqual([item["outcome"] for item in turn.status_events()],
                         ["failed"])
        self.assertEqual(turn.filler.snapshot.outcome, "played")
        await turn.filler.primary_ready()
        self.assertEqual(turn.clears, [(3, 7)])

    async def test_phrase_mode_keeps_its_one_sentence(self):
        turn = Turn(activity="checking_protocol", mode="phrase")
        turn.filler.start()
        await settle()
        turn.sleeper.release()
        await settle()
        self.assertEqual(turn.calls, [(FILLER_PHRASES["ko"], "ko")])
        self.assertEqual(len(turn.sleeper.requested), 1)
        self.assertEqual(turn.status_events(), [])


class StatusSentenceVocabularyTests(unittest.TestCase):
    def test_the_status_delay_setting_is_bounded(self):
        self.assertEqual(cascade_filler_status_delay_ms({}), 1500)
        self.assertEqual(
            cascade_filler_status_delay_ms(
                {"VOINEY_LAB_CASCADE_FILLER_STATUS_DELAY_MS": "2000"}), 2000)
        for value in ("199", "10001", "soon"):
            with self.assertRaises(ConfigurationError):
                cascade_filler_status_delay_ms(
                    {"VOINEY_LAB_CASCADE_FILLER_STATUS_DELAY_MS": value})

    def test_sentences_name_only_real_server_states_in_every_language(self):
        states = set(FILLER_STATUS_PHRASES["ko"])
        for language in FILLER_PHRASES:
            self.assertEqual(set(FILLER_STATUS_PHRASES[language]), states)
        self.assertLessEqual(states, set(TURN_PROGRESS_TRANSITIONS))
        for silent in ("listening", "synthesizing", "playing"):
            self.assertNotIn(silent, states)

    def test_each_korean_sentence_says_what_the_turn_card_says(self):
        html = (ROOT / "src" / "voiney_lab" / "static" / "index.html").read_text(
            encoding="utf-8")
        labels = dict(re.findall(
            r'(\w+):"([^"]+)"',
            html.split("const TURN_STATE_LABELS={", 1)[1].split("};", 1)[0],
        ))
        for state, sentence in FILLER_STATUS_PHRASES["ko"].items():
            with self.subTest(state=state):
                subject = labels[state].split()[0]
                self.assertIn(subject, sentence)


class StatusThroughTheTurnTests(unittest.TestCase):
    """``run_turn_safely`` feeds the filler the turn's real progress state."""

    def test_a_slow_turn_says_its_state_and_the_next_one_does_not_repeat_it(self):
        session = ListenerSession(tool_context=ToolContext(
            Path("/trusted/catalog.sqlite"), None, "ko", "test_only"))
        session.active = True
        session.language_mode = "auto"
        socket = Socket()

        def slow_empty_transcript(*_args, **_kwargs):
            time.sleep(0.6)  # still transcribing at the 250 ms status delay
            return Transcription("", "ko")

        with patch.dict("os.environ", {
            "VOINEY_LAB_CASCADE_FILLER_MODE": "tone",
            "VOINEY_LAB_CASCADE_FILLER_DELAY_MS": "100",
            "VOINEY_LAB_CASCADE_FILLER_STATUS_DELAY_MS": "250",
        }), patch(
            "voiney_lab.server.transcribe", side_effect=slow_empty_transcript,
        ), patch(
            "voiney_lab.server.synthesize", return_value=b"\x00\x00" * 320,
        ) as tts:
            for turn_id in (1, 2):
                session.active_turn_id = turn_id
                session.detector.state = TurnState.PROCESSING
                asyncio.run(run_turn_safely(socket, session, b"\0\0", turn_id, 1))
        said = FILLER_STATUS_PHRASES["ko"]["transcribing"]
        tts.assert_called_once_with(said, "ko")
        status = [item for item in socket.text
                  if item["type"] == "turn.filler" and item.get("cue") == "status"]
        self.assertEqual(
            [(item["turn_id"], item["outcome"], item["activity"], item["text"])
             for item in status],
            [(1, "played", "transcribing", said)],
        )
        starts = [item for item in socket.text if item["type"] == "filler.audio.start"]
        self.assertEqual([item["turn_id"] for item in starts], [1])
        tones = [item["turn_id"] for item in socket.text if item["type"] == "filler.tone"]
        self.assertEqual(tones, [1, 2])
        self.assertEqual(session.filler_memory.last_status_phrase, said)


class StatusSentencePageTests(unittest.TestCase):
    def test_the_card_shows_the_sentence_until_the_answer_plays(self):
        result = run_page_script(TONE_PAGE + r"""
const timers=[];globalThis.setTimeout=(fn,ms)=>{timers.push({fn,ms});return timers.length};
await onMessage({data:JSON.stringify({type:"transcript",configuration_id,turn_id:1,generation:5,text:"이 단계 왜 해?"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.filler",configuration_id,turn_id:1,generation:5,outcome:"played",cue:"tone"})},generation,socket);
const status=turnNode(1,generation).querySelector(".filler-status");
await onMessage({data:JSON.stringify({type:"turn.filler",configuration_id,turn_id:1,generation:5,outcome:"played",cue:"status",activity:"checking_protocol",text:"절차를 확인하고 있습니다."})},generation,socket);
assert(status.textContent==="대기 안내 · 절차를 확인하고 있습니다."&&status.hidden===false,`status sentence missing: ${status.textContent}`);
assert(timers.length===1,"the status sentence got its own timer");
timers[0].fn();
assert(status.textContent==="대기 안내 · 절차를 확인하고 있습니다.","the tone's timer removed the status sentence");
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:1,state:"transcribing"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:2,state:"routing"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:3,state:"checking_protocol",route:"curated_protocol"})},generation,socket);
assert(status.textContent.includes("절차를 확인"),"a working state removed the sentence");
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:4,state:"synthesizing",route:"curated_protocol"})},generation,socket);
await onMessage({data:JSON.stringify({type:"turn.state",configuration_id,turn_id:1,generation:5,revision:5,state:"playing",route:"curated_protocol"})},generation,socket);
assert(status.textContent===""&&status.hidden===true,"the sentence outlived the wait");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
