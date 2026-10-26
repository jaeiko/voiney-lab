"""Lane HEAR, decision 3 (2026-10-25): no sound is dropped in silence.

The person's test: words were ignored again and again with nothing said or
shown. Whatever drops a sound -- quieter than the measured voice, the
agent's own voice coming back, too short, the STT writing nothing, a
language it could not tell, anything else -- now leaves a reason code with
its level (dB) and length (ms) in the log and the metrics (never the sound
or the words), and a small "잘 안 들렸어요" on the screen. Two drops of what
may have been the wearer's words within 30 s are said once: "잘 안 들렸어요.
다시 말씀해 주세요." -- once, until words are heard again. While paused,
nothing is said (decision 7).

With the filter on, a voice dropped twice in a row just under the line --
within NEAR_LINE_DB, where the wearer's own quieter words fall -- lowers the
line to the "낮음" margin, and if it is already there turns the filter off
for this session; either is said and shown.
"""

from __future__ import annotations

import array
import asyncio
import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from voiney_lab.audio import FRAME_BYTES
from voiney_lab.language import Transcription
from voiney_lab.server import ListenerSession, voice_socket
from voiney_lab.vad import SENSITIVITY_MARGINS_DB, EndpointDetector, VadConfig

SAMPLES = FRAME_BYTES // 2
SILENT = bytes(FRAME_BYTES)
CONFIG = VadConfig(
    onset_voiced_frames=2, onset_window_frames=3, prefix_frames=3,
    endpoint_silence_frames=2, minimum_voiced_frames=2,
    maximum_utterance_frames=40, cooldown_ms=0,
    playback_onset_voiced_frames=2, playback_onset_window_frames=3,
    listening_onset_voiced_frames=2, listening_onset_window_frames=3,
    listening_resume_voiced_frames=2, listening_resume_window_frames=3,
)


def tone_frame(amplitude: float, phase: int = 0) -> bytes:
    return array.array("h", (
        int(round(32767 * amplitude * math.sin(2 * math.pi * 440 * (phase * SAMPLES + i) / 16000))) or 1
        for i in range(SAMPLES))).tobytes()


def at_db(level_db: float) -> float:
    """The amplitude of a sine that reads ``level_db`` dBFS."""

    return 10 ** (level_db / 20) * math.sqrt(2)


def utterance(amplitude: float, voiced_frames: int = 6) -> bytes:
    return b"".join(tone_frame(amplitude, i) for i in range(voiced_frames)) + SILENT * 3


def voiced_if_loud(frame: bytes) -> bool:
    return any(frame)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Socket:
    """Feeds frames; {"sleep": s} waits and {"advance": s} moves the session clock."""

    def __init__(self, messages, clock: Clock) -> None:
        self.sent: list[dict] = []
        self.messages = list(messages)
        self.clock = clock
        self.inbox: list[dict] = []

    async def accept(self):
        return None

    async def receive(self):
        while True:
            if self.inbox:
                return self.inbox.pop(0)
            if not self.messages:
                await asyncio.sleep(0.3)
                if self.inbox:
                    continue
                return {"type": "websocket.disconnect", "code": 1000}
            item = self.messages.pop(0)
            if "sleep" in item:
                await asyncio.sleep(item["sleep"])
                continue
            if "advance" in item:
                self.clock.now += item["advance"]
                continue
            await asyncio.sleep(0.005)
            return item

    async def send_text(self, value):
        message = json.loads(value)
        self.sent.append(message)
        if message.get("type") == "audio.segment.start":
            # The page says the audio (an answer's or the server's own words) ended.
            self.inbox.append({"text": json.dumps({"type": "playback.ended", "turn_id": message["turn_id"]})})

    async def send_bytes(self, value):
        return None

    async def close(self, **kwargs):
        return None


class DropHarness(unittest.TestCase):
    def listener(self) -> tuple[ListenerSession, Clock]:
        clock = Clock()
        session = ListenerSession(
            EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True), clock=clock)
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-hear")
        return session, clock

    def run_socket(self, session, clock, messages, *, heard=lambda pcm: "", metrics=None):
        socket = Socket(messages, clock)

        def transcribe(pcm, *args, **kwargs):
            return Transcription(heard(pcm), "ko")

        recorded = metrics if metrics is not None else []
        with patch("voiney_lab.server.ListenerSession", return_value=session), patch(
            "voiney_lab.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch("voiney_lab.server.VadConfig.from_settings", return_value=CONFIG), patch(
            "voiney_lab.server.transcribe", side_effect=transcribe,
        ), patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320), patch(
            "voiney_lab.server._record_workspace_metric",
            side_effect=lambda **kwargs: recorded.append(kwargs),
        ), self.assertLogs("voiney_lab", level="INFO") as logs:
            asyncio.run(voice_socket(socket))
        self.logs = "\n".join(logs.output)
        return socket.sent

    @staticmethod
    def of(sent, kind):
        return [m for m in sent if m["type"] == kind]


class DropRecordTests(DropHarness):
    def test_an_empty_transcript_is_recorded_shown_and_said_after_the_second(self) -> None:
        session, clock = self.listener()
        metrics: list[dict] = []
        sent = self.run_socket(session, clock, [
            {"bytes": utterance(0.1)}, {"sleep": 0.2},
            {"advance": 5.0},
            {"bytes": utterance(0.1)}, {"sleep": 0.5},
            {"advance": 5.0},
            {"bytes": utterance(0.1)}, {"sleep": 0.5},
        ], metrics=metrics)
        dropped = self.of(sent, "speech.dropped")
        self.assertEqual([m["reason"] for m in dropped], ["empty_transcript"] * 3)
        for message in dropped:
            self.assertEqual(message["notice"], "잘 안 들렸어요")
            self.assertGreater(message["duration_ms"], 0)
            self.assertIsInstance(message["level_db"], float)
            self.assertFalse(message["paused"])
        said = [m for m in self.of(sent, "server.words") if m["words_kind"] == "drop_notice"]
        self.assertEqual([m["text"] for m in said], ["잘 안 들렸어요. 다시 말씀해 주세요."],
                         "said once after the second drop, not again for the third")
        codes = [m["dimensions"].get("reason_code") for m in metrics if m.get("metric_name") == "speech_dropped"]
        self.assertEqual(codes, ["empty_transcript"] * 3)
        self.assertIn("speech.dropped reason=empty_transcript", self.logs)
        self.assertIn("level_db=", self.logs)

    def test_two_drops_further_apart_than_the_window_are_not_said(self) -> None:
        session, clock = self.listener()
        sent = self.run_socket(session, clock, [
            {"bytes": utterance(0.1)}, {"sleep": 0.2},
            {"advance": 31.0},
            {"bytes": utterance(0.1)}, {"sleep": 0.5},
        ])
        self.assertEqual(len(self.of(sent, "speech.dropped")), 2)
        self.assertEqual([m for m in self.of(sent, "server.words") if m["words_kind"] == "drop_notice"], [])

    def test_words_heard_again_let_the_notice_be_said_again(self) -> None:
        session, clock = self.listener()
        texts = iter(["", "", "다음", "", ""])
        sent = self.run_socket(session, clock, [
            {"bytes": utterance(0.1)}, {"sleep": 0.2},
            {"bytes": utterance(0.1)}, {"sleep": 0.5},
            {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.8},
            {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.2},
            {"bytes": utterance(0.1)}, {"sleep": 0.5},
        ], heard=lambda pcm: next(texts, ""))
        said = [m for m in self.of(sent, "server.words") if m["words_kind"] == "drop_notice"]
        self.assertEqual(len(said), 2, [m["type"] for m in sent])

    def test_the_agents_own_voice_and_a_too_short_sound_are_shown_not_said(self) -> None:
        session, clock = self.listener()
        session.remember_spoken("지금 1단계를 진행하고 있어요")
        short = VadConfig(**{**CONFIG.__dict__, "minimum_voiced_frames": 5})
        session.detector = EndpointDetector(short, classifier=voiced_if_loud, listening_onset=True)
        session._cascade_vad_config = short
        sent = self.run_socket(session, clock, [
            {"bytes": utterance(0.1, voiced_frames=2)}, {"sleep": 0.1},
            {"bytes": utterance(0.1)}, {"sleep": 0.3},
            {"bytes": utterance(0.1)}, {"sleep": 0.5},
        ], heard=lambda pcm: "지금 1단계를 진행하고 있어요")
        reasons = [m["reason"] for m in self.of(sent, "speech.dropped")]
        self.assertIn("minimum_voiced_frames", reasons)
        self.assertGreaterEqual(reasons.count("self_echo"), 1, reasons)
        self.assertEqual([m for m in self.of(sent, "server.words") if m["words_kind"] == "drop_notice"], [])


class LevelDropTests(DropHarness):
    def test_a_voice_under_the_line_is_a_drop_with_its_metric_recorded(self) -> None:
        session, clock = self.listener()
        session.set_level_reference(-20.0)
        metrics: list[dict] = []
        sent = self.run_socket(session, clock, [{"bytes": utterance(at_db(-50.0))}, {"sleep": 0.2}], metrics=metrics)
        dropped = self.of(sent, "speech.dropped")
        self.assertEqual([m["reason"] for m in dropped], ["other_speaker_level"])
        self.assertAlmostEqual(dropped[0]["level_db"], -50.0, delta=0.3)
        ignored = [m for m in metrics if m.get("metric_name") == "speech_ignored"]
        self.assertEqual(ignored[0]["dimensions"], {"reason_code": "other_speaker_level"},
                         "the analytics store only takes allowed dimension names")

    def test_two_near_the_line_lower_it_then_two_more_turn_the_filter_off(self) -> None:
        session, clock = self.listener()
        session.set_level_reference(-20.0)
        normal = SENSITIVITY_MARGINS_DB["normal"]
        low = SENSITIVITY_MARGINS_DB["low"]
        just_under = -20.0 - normal - 2.0
        sent = self.run_socket(session, clock, [
            {"bytes": utterance(at_db(just_under))}, {"sleep": 0.2},
            {"bytes": utterance(at_db(just_under))}, {"sleep": 0.6},
            {"bytes": utterance(at_db(-20.0 - low - 2.0))}, {"sleep": 0.2},
            {"bytes": utterance(at_db(-20.0 - low - 2.0))}, {"sleep": 0.6},
        ])
        filters = self.of(sent, "speaker.filter")
        self.assertEqual([m["state"] for m in filters], ["lowered", "off"])
        self.assertEqual(filters[0]["margin_db"], low)
        said = [m["text"] for m in self.of(sent, "server.words") if m["words_kind"] == "speaker_filter"]
        self.assertEqual(len(said), 2)
        self.assertIn("낮췄어요", said[0])
        self.assertIn("껐어요", said[1])

    def test_a_voice_far_under_the_line_does_not_move_it(self) -> None:
        session, clock = self.listener()
        session.set_level_reference(-20.0)
        sent = self.run_socket(session, clock, [
            {"bytes": utterance(at_db(-55.0))}, {"sleep": 0.2},
            {"bytes": utterance(at_db(-55.0))}, {"sleep": 0.2},
            {"bytes": utterance(at_db(-55.0))}, {"sleep": 0.4},
        ])
        self.assertEqual(self.of(sent, "speaker.filter"), [])
        self.assertEqual([m["reason"] for m in self.of(sent, "speech.dropped")], ["other_speaker_level"] * 3)


if __name__ == "__main__":
    unittest.main()
