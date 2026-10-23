"""Lane VOX, decisions 2 and 5 (2026-10-23): the guidance said sentence by sentence.

The protocol guidance used to be synthesized whole before its first sound,
so a long step reading was heard only after its whole audio was made. Now
the first sentence is made and plays at once and the later ones are made
while it plays, each sent as the next audio segment of the same turn.
Sentence ends are ``brain.SentenceChunker``'s, cut only where cleaning
leaves each piece as it is, so a number keeps its decimals and its unit and
the pieces said one after another are the very words the whole would have
been. The screen text, the turn and generation fences and barge-in
cancellation stay as they were.

The clock is a fake that only the fake TTS moves (0.2 s + 10 ms a
character); each sentence gives one second of audio. No provider is called.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import voiney_lab.server as server_module
from tests.protocol_vocabulary_support import build_fixture
from voiney_lab.audio import FRAME_BYTES
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.language import Transcription, clean_speech_text
from voiney_lab.server import ListenerSession, cancel_cascade_generation, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import EndpointDetector, TurnState

STEPS = (
    "1 Cut the stained band into pieces and place them in a 1.5 mL tube.",
    "2 Wash the pieces with 500 µL of Solution A for 10 min. "
    "Remove Solution A and add 0.5 mL of Solution B. "
    "Keep the tube at -20 °C until the next step.",
    "3 Dry the pieces in the speedvac.",
)
STEP_2_SENTENCES = [
    "2 Wash the pieces with 500 µL of Solution A for 10 min.",
    "Remove Solution A and add 0.5 mL of Solution B.",
    "Keep the tube at -20 °C until the next step.",
]
READ_THE_STEP = "지금 단계 읽어 줘"
#: One second of 16 kHz PCM16: fifty 20 ms frames.
ONE_SECOND = b"\0\0" * 16000
T0 = 1000.0


def latency(text: str) -> float:
    return 0.2 + 0.01 * len(text)


class Socket:
    """Records each event with the fake clock's time when it was sent."""

    def __init__(self, clock: "FakeClock") -> None:
        self.clock = clock
        self.text: list[dict] = []
        self.at: list[float] = []
        self.binary: list[bytes] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))
        self.at.append(self.clock.value)

    async def send_bytes(self, value: bytes) -> None:
        self.binary.append(value)

    def of(self, kind: str) -> list[dict]:
        return [item for item in self.text if item["type"] == kind]

    def when(self, kind: str, **fields) -> list[float]:
        return [
            at for at, item in zip(self.at, self.text)
            if item["type"] == kind and all(item.get(k) == v for k, v in fields.items())
        ]


class FakeClock:
    def __init__(self) -> None:
        self.value = T0

    def now(self) -> float:
        return self.value


class SentenceSpeechCase(unittest.TestCase):

    def setUp(self) -> None:
        fixture = build_fixture(
            protocol_id="lane-vox-wash", title="Fictional wash", steps=STEPS,
            materials=("Solution A", "Solution B"), equipment=("Speedvac",),
        )
        self.clock = FakeClock()
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        workflow = CuratedProtocolSession(fixture)
        workflow.active = True
        workflow.current_index = 1
        session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        session.endpoint_at = self.clock.value
        self.session = session
        self.socket = Socket(self.clock)
        #: Each text the TTS was asked for, in order.
        self.asked: list[str] = []
        #: The call (1-based) that sets ``held`` and waits for ``release``.
        self.hold_at: int | None = None
        #: The call (1-based) that fails.
        self.fail_at: int | None = None

    def synthesize(self, text, *_args, **_kwargs) -> bytes:
        if self.fail_at is not None and len(self.asked) == self.fail_at:
            raise RuntimeError("offline TTS failure")
        return ONE_SECOND

    async def to_thread(self, function, *args, **kwargs):
        if function is server_module.synthesize:
            self.asked.append(args[0])
            if self.hold_at is not None and len(self.asked) == self.hold_at:
                self.held.set()
                await self.release.wait()
            self.clock.value += latency(args[0])
        return function(*args, **kwargs)

    def patches(self):
        return (
            patch("voiney_lab.server.synthesize", side_effect=self.synthesize),
            patch("voiney_lab.server.asyncio.to_thread", side_effect=self.to_thread),
        )

    async def turn(self, words: str = READ_THE_STEP) -> None:
        # As run_turn_safely does: the session speaking now, for said().
        server_module._SPEAKING_SESSION.set(self.session)
        await run_turn(
            self.socket, self.session, b"\0\0", 1, 1,
            accepted_transcription=Transcription(words, "ko"), accepted_stt_ms=0,
            clock=self.clock.now,
        )

    def run_turn_now(self, words: str = READ_THE_STEP) -> None:
        first, second = self.patches()
        with first, second:
            asyncio.run(self.turn(words))

    def accept_barge_in(self):
        """The words of a confirmed barge-in, through the listener's own path."""

        session = self.session
        decisions = iter(
            [False, True, True, True, True, False]
            + [True] * 8
            + [False] * session.detector.config.endpoint_silence_frames
        )
        session._interrupt_detector = EndpointDetector(
            session.detector.config, classifier=lambda _: next(decisions),
        )
        frames = 14 + session.detector.config.endpoint_silence_frames
        candidates = session.accept_chunk(b"\0" * FRAME_BYTES * frames)
        ready = next(item for item in candidates if item.kind == "barge_in_audio_ready")
        return [*candidates, *session.commit_interrupt_candidate(ready)]

    def run_held(self, between) -> None:
        """Run the turn, call ``between()`` while the hold_at-th sentence is being made."""

        async def scenario() -> None:
            self.held = asyncio.Event()
            self.release = asyncio.Event()
            first, second = self.patches()
            with first, second:
                task = asyncio.create_task(self.turn())
                try:
                    await asyncio.wait_for(self.held.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    task.cancel()
                    self.fail(f"the TTS was never asked for sentence {self.hold_at}: {self.asked}")
                await between(task)
                self.release.set()
                for _ in range(20):
                    await asyncio.sleep(0)
                if not task.done():
                    await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(scenario())


class FirstSoundTests(SentenceSpeechCase):

    def test_a_long_step_reading_is_heard_after_its_first_sentence_only(self) -> None:
        self.run_turn_now()
        (delta,) = self.socket.of("reply.delta")
        self.assertEqual(clean_speech_text(delta["speech_text"]), " ".join(STEP_2_SENTENCES))
        # Each sentence asked for once, in order, nothing else.
        self.assertEqual(self.asked, STEP_2_SENTENCES)
        playing = [
            item for item in self.socket.of("turn.state")
            if item["state"] == "playing"
        ]
        first_sentence_ms = round(latency(STEP_2_SENTENCES[0]) * 1000)
        whole_ms = round(latency(" ".join(STEP_2_SENTENCES)) * 1000)
        self.assertEqual(
            playing[0]["timings_ms"]["time_to_playable_audio"], first_sentence_ms)
        self.assertLess(first_sentence_ms, whole_ms)
        (done,) = self.socket.of("turn.done")
        self.assertEqual(done["timings_ms"]["first_audio_ms"], first_sentence_ms)
        # The first segment leaves as soon as the first sentence is made.
        first_end = self.socket.when("audio.segment.end", segment_index=0)[0]
        self.assertAlmostEqual(first_end - T0, latency(STEP_2_SENTENCES[0]))

    def test_the_sentences_are_one_turns_segments_in_order(self) -> None:
        self.run_turn_now()
        starts = self.socket.of("audio.segment.start")
        self.assertEqual([item["segment_index"] for item in starts], [0, 1, 2])
        self.assertEqual({item["turn_id"] for item in starts}, {1})
        self.assertEqual({item["generation"] for item in starts}, {self.session.generation})
        self.assertEqual([item["frame_count"] for item in starts], [50, 50, 50])
        ends = self.socket.of("audio.segment.end")
        self.assertEqual([item["segment_index"] for item in ends], [0, 1, 2])
        self.assertEqual(len(self.socket.binary), 150)
        (complete,) = self.socket.of("audio.complete")
        self.assertEqual(complete["segment_count"], 3)
        (done,) = self.socket.of("turn.done")
        self.assertEqual(done["segment_count"], 3)
        self.assertEqual(done["output_frames"], 150)
        kinds = [item["type"] for item in self.socket.text]
        last_end = max(i for i, kind in enumerate(kinds) if kind == "audio.segment.end")
        self.assertLess(last_end, kinds.index("audio.complete"))
        self.assertLess(kinds.index("audio.complete"), kinds.index("turn.done"))
        # Each later sentence is sent once it is made, never before.
        for index, sentence_end in enumerate(
            sum(latency(text) for text in STEP_2_SENTENCES[:n + 1]) for n in range(3)
        ):
            sent = self.socket.when("audio.segment.end", segment_index=index)[0]
            self.assertAlmostEqual(sent - T0, sentence_end)

    def test_the_screen_text_is_what_it_was_when_said_whole(self) -> None:
        self.run_turn_now()
        split = (self.socket.of("reply.delta"), self.socket.of("reply.complete"))
        self.setUp()
        with patch("voiney_lab.server.speech_sentences", side_effect=lambda text: (text,)):
            self.run_turn_now()
        whole = (self.socket.of("reply.delta"), self.socket.of("reply.complete"))
        self.assertEqual(self.asked, [whole[0][0]["speech_text"]])
        self.assertEqual(len(split[0]), 1)
        self.assertEqual(len(split[1]), 1)
        for key in ("text", "speech_text", "primary_text", "source_texts", "segment_index"):
            self.assertEqual(split[0][0].get(key), whole[0][0].get(key), key)
        self.assertEqual(split[1][0]["text"], whole[1][0]["text"])

    def test_the_whole_answer_is_remembered_as_said_for_the_echo_check(self) -> None:
        self.run_turn_now()
        spoken = [text for _, text in self.session.spoken_recently]
        (delta,) = self.socket.of("reply.delta")
        self.assertEqual(spoken, [delta["speech_text"]])
        # An echo across two sentences is still the agent's own voice.
        self.session.last_playback_ended_at = self.session.clock()
        echo = self.session.own_speech_echo(
            "Solution A for 10 min. Remove Solution A and add",
            heard_from=self.session.clock())
        self.assertIsNotNone(echo)

    def test_a_one_sentence_reply_is_said_as_before(self) -> None:
        self.session.curated_protocol_session.current_index = 0
        self.run_turn_now()
        (delta,) = self.socket.of("reply.delta")
        self.assertEqual(self.asked, [delta["speech_text"]])
        self.assertEqual(
            [item["segment_index"] for item in self.socket.of("audio.segment.start")], [0])
        self.assertEqual(self.socket.of("audio.complete")[0]["segment_count"], 1)


class StopTests(SentenceSpeechCase):

    def test_a_barge_in_stops_the_sentences_still_to_be_made_and_sent(self) -> None:
        self.hold_at = 2

        async def barge_in(task) -> None:
            self.assertIs(self.session.state, TurnState.AGENT_SPEAKING)
            events = self.accept_barge_in()
            interrupted = next(item for item in events if item.kind == "assistant.interrupted")
            await cancel_cascade_generation(self.socket, self.session, task, interrupted)

        with self.assertLogs("voiney_lab", level="INFO") as logged:
            self.run_held(barge_in)
        # The second sentence was being made; the third was never asked for.
        self.assertEqual(self.asked, STEP_2_SENTENCES[:2])
        self.assertEqual(
            [item["segment_index"] for item in self.socket.of("audio.segment.start")], [0])
        self.assertEqual(self.socket.of("audio.complete"), [])
        self.assertEqual(self.socket.of("turn.done"), [])
        (clear,) = self.socket.of("cascade.playback.clear")
        self.assertEqual(clear["state"], "cancelled")
        # The turn still counts in the pilot metrics, as it did when the
        # barge-in could only come after the turn had ended.
        self.assertTrue(
            any("pilot.turn turn_id=1 " in line for line in logged.output), logged.output)
        self.assertEqual(self.session.curated_protocol_session.current_index, 1)

    def test_a_piece_of_an_older_generation_is_dropped(self) -> None:
        self.hold_at = 2

        async def move_on(task) -> None:
            # The session moved to a new generation while the sentence was made.
            self.session.generation += 1

        self.run_held(move_on)
        self.assertEqual(self.asked, STEP_2_SENTENCES[:2])
        self.assertEqual(
            [item["segment_index"] for item in self.socket.of("audio.segment.start")], [0])
        self.assertEqual(self.socket.of("audio.complete"), [])


class FailureTests(SentenceSpeechCase):

    def test_a_first_sentence_that_cannot_be_made_fails_the_turn_as_before(self) -> None:
        self.fail_at = 1
        with self.assertRaisesRegex(RuntimeError, "offline TTS failure"):
            self.run_turn_now()
        self.assertEqual(len(self.asked), 1)
        kinds = {item["type"] for item in self.socket.text}
        for kind in ("audio.segment.start", "reply.delta", "reply.complete", "turn.done"):
            self.assertNotIn(kind, kinds)
        self.assertEqual(self.session.curated_protocol_session.current_index, 1)

    def test_a_later_sentence_that_cannot_be_made_ends_the_answer_there(self) -> None:
        self.fail_at = 2
        with self.assertLogs("voiney_lab", level="ERROR") as logged:
            self.run_turn_now()
        self.assertTrue(
            any("curated protocol sentence TTS failed" in line for line in logged.output))
        # Nothing more is asked for after the failure.
        self.assertEqual(self.asked, STEP_2_SENTENCES[:2])
        self.assertEqual(
            [item["segment_index"] for item in self.socket.of("audio.segment.start")], [0])
        # The same signals as a turn whose audio could not be made ...
        (error,) = self.socket.of("error")
        self.assertEqual(error["message"], "voice turn processing failed")
        self.assertEqual(error["turn_id"], 1)
        self.assertEqual(
            [item["state"] for item in self.socket.of("turn.state")][-1], "error")
        # ... while the turn still ends, so the page ends its playback.
        self.assertEqual(self.socket.of("audio.complete")[0]["segment_count"], 1)
        (done,) = self.socket.of("turn.done")
        self.assertEqual(done["segment_count"], 1)
        self.assertEqual(done["output_frames"], 50)
        # Its own voice is still playing: the session does not listen to it
        # as a new turn, and the page's playback.ended ends the turn.
        self.assertIs(self.session.state, TurnState.AGENT_SPEAKING)
        self.assertTrue(self.session.playback_ended(1))


class FollowUpTests(unittest.TestCase):
    """What is said after the answer comes after every one of its sentences."""

    def test_a_web_explanation_is_the_segment_after_the_answer(self) -> None:
        from tests.test_lane_wv_web_lookup import (
            GOOD, _client, _curated, _response, _Sender, _Session,
        )
        from voiney_lab.runtime_routing import route_curated_runtime_turn

        curated = _curated()
        plan = route_curated_runtime_turn(
            curated, "써모믹서가 어떻게 생겼어?", turn_id=2, language="ko",
            configuration_id=1, generation=1).plan

        async def no_photo(term, **kwargs):
            return None

        def look_up(speak_by: float):
            session, sender = _Session(), _Sender()
            client = _client(_response(GOOD, citations=(("https://www.eppendorf.com/x", "ThermoMixer"),)))
            with patch.object(server_module, "AsyncOpenAI", return_value=client), \
                    patch.object(server_module, "require_env", return_value="fake"), \
                    patch.object(server_module, "find_commons_photo", no_photo), \
                    patch.object(server_module, "synthesize", return_value=b"\0\0" * 320):
                asyncio.run(server_module._run_web_lookup(
                    session=session, sender=sender, turn_id=2, generation=1, curated=curated,
                    plan=plan, transcript="써모믹서가 어떻게 생겼어?", language="ko",
                    speak_by=speak_by, clock=lambda: 0.0, answer_segments=3,
                ))
            complete = [f["segment_count"] for k, f in sender.events if k == "audio.complete"]
            return sender.segments, complete

        # The answer was three sentences (segments 0-2): the web's words are segment 3.
        self.assertEqual(look_up(1e12), ([(2, 3, 1)], [4]))
        # Too late to be said: the held audio.complete counts the answer alone.
        self.assertEqual(look_up(-1.0), ([], [3]))


class SentenceTests(unittest.TestCase):
    """``speech_sentences``: numbers whole, the same words as the whole."""

    def sentences(self, text: str) -> tuple[str, ...]:
        return server_module.speech_sentences(text)

    def assertSameWords(self, text: str) -> None:
        pieces = self.sentences(text)
        if len(pieces) == 1:
            self.assertEqual(pieces, (text,))
            return
        self.assertEqual(" ".join(pieces), clean_speech_text(text))
        for piece in pieces:
            self.assertEqual(clean_speech_text(piece), piece)

    def test_decimals_units_and_abbreviations_end_no_sentence(self) -> None:
        self.assertEqual(
            self.sentences("pH 7.5 buffer is used. Add 1.5 mL of it."),
            ("pH 7.5 buffer is used.", "Add 1.5 mL of it."))
        self.assertEqual(
            self.sentences("See Fig. 2 for the setup. Then start the run."),
            ("See Fig. 2 for the setup.", "Then start the run."))
        self.assertEqual(
            self.sentences("약 200 µL의 25mM AMBIC을 넣어요. 37°C에서 15분 둡니다."),
            ("약 200 µL의 25mM AMBIC을 넣어요.", "37°C에서 15분 둡니다."))

    def test_a_piece_cleaning_would_change_is_joined_to_the_one_before(self) -> None:
        # Alone, "0.5 mL ..." would lose "0." and "-20 °C ..." its minus sign
        # as list marks; joined, they are said as the whole would say them.
        self.assertEqual(
            self.sentences("Add 1 mL. 0.5 mL of B follows. Then mix."),
            ("Add 1 mL. 0.5 mL of B follows.", "Then mix."))
        self.assertEqual(
            self.sentences("Mix well. -20 °C is the storage temperature. Done here."),
            ("Mix well. -20 °C is the storage temperature.", "Done here."))
        # A whole whose own first words cleaning would change again is said whole.
        text = "1. 2.5 mL 넣어요. 다음입니다."
        self.assertEqual(self.sentences(text), (text,))

    def test_no_space_no_sentence_end(self) -> None:
        self.assertEqual(
            self.sentences("Add.5 mL now. Then stop."), ("Add.5 mL now.", "Then stop."))

    def test_one_sentence_and_short_words_stay_as_they_are(self) -> None:
        for text in ("한 문장입니다.", "네.", "", "현재 총 25단계 중 1단계입니다.", "네. 좋아요."):
            self.assertEqual(self.sentences(text), (text,))

    def test_every_piece_is_said_exactly_as_the_whole_would_be(self) -> None:
        for text in (
            *STEPS,
            "실험을 시작합니다. 현재 1단계입니다. 염색된 단백질 밴드에서 작은 1 mm³ 플러그를 "
            "자르거나 밴드 전체를 절취해 더 작은 조각으로 나누고, 약 200 µL의 25mM AMBIC이 담긴 "
            "1.5 mL 튜브에 넣습니다. 오늘 실험 목적이나 시료를 한 줄로 말씀해 주세요. 없으면 "
            "'넘어가'라고 하세요.",
            "다음 단계 미리보기 · 2단계: 두 세척 용액을 준비합니다. Solution A는 25mM ammonium "
            "bicarbonate(AMBIC) 2 parts와 acetonitrile 1 part를 혼합합니다.",
            "**굵게** 1단계입니다. 다음은 2단계입니다.",
            "- 첫째 줄입니다.\n- 둘째 줄입니다. [1] 끝.",
            "3단계 타이머 14분 59초 남았어요. 1.5 mL? 네! 0.5 mL.",
        ):
            with self.subTest(text=text[:30]):
                self.assertSameWords(text)


if __name__ == "__main__":
    unittest.main()
