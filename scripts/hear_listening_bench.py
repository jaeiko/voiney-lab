#!/usr/bin/env python3
"""Listening bench (lane HEAR): fake microphone frames through the real voice socket.

Drives ``voice_socket`` the way the current screen does during a run --
including the screen's own "목소리 맞추기" request after the audio is ready --
with synthetic 20 ms frames, a fake speech-to-text that writes the words a
frame pattern stands for, and a fake speech output. No provider is called.

It counts what lane HEAR's brief asks to measure before and after the fix:

* wearer_dropped -- the wearer's utterances that never reached routing
  (no ``transcript`` event carried their words), over all wearer utterances:
  a normal voice, a quieter voice (head turned, 14 dB under the voice read
  for the measurement), and a word said while an answer plays;
* split -- sentences said with a pause in the middle (1.3 s, and once 2.7 s
  after words that end unfinished) whose two halves did not reach routing
  together as one transcript;
* paused_replies -- answers with sound to words other than a resume word
  while the run is paused;
* noise_questions -- "시끄러운 곳 모드" questions said during the run.

Run it against any checkout to compare (the module only uses names every
checkout since lane SP1 has)::

    PYTHONPATH=<checkout>/src python scripts/hear_listening_bench.py

It prints one JSON object. Frames are sent about SPEED times faster than
real time, and the session's clock follows the frames (20 ms each), so
every pause and window is measured in audio time.
"""

from __future__ import annotations

import array
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

#: The run's settings: fake-backed speech providers, no model router, no
#: workspace or report store -- set for the run only.
BENCH_ENVIRONMENT = {
    "VOINEY_LAB_STT_PROVIDER": "xai",
    "VOINEY_LAB_TTS_PROVIDER": "xai",
    "VOINEY_LAB_USAGE_SCOPE": "demo",
    "VOINEY_LAB_LLM_ROUTER_ENABLED": "false",
    "VOINEY_LAB_WORKSPACE_ENABLED": "false",
    "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "false",
}

from voiney_lab.audio import FRAME_BYTES
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.language import Transcription
from voiney_lab.replay_turns import _demo_fixture
from voiney_lab.server import ListenerSession, voice_socket
from voiney_lab.tools import ToolContext
from voiney_lab.vad import EndpointDetector, VadConfig

SAMPLES = FRAME_BYTES // 2
SPEED = 10.0
SILENT = bytes(FRAME_BYTES)
#: The wearer's voice when the measurement sentence is read, and the noise
#: under the line (fractions of full scale; 0.1 reads about -23 dBFS).
WEARER = 0.1
QUIETER = 0.02   # about 14 dB under WEARER: past the "보통" 12 dB margin
NOISE = 0.008    # about 22 dB under WEARER
STT_SECONDS = 0.8
ANSWER_FRAMES = 150  # 3 s of answer audio
CALIBRATION_SENTENCE_FALLBACK = "오늘 실험을 시작합니다"

#: Each word is a run of voiced frames of its own length; the fake STT writes
#: the words of the runs it finds and nothing for a run of any other length.
WORDS: dict[int, str] = {
    25: "현재 단계 알려줘",
    50: "다음 단계 알려줘",
    75: "다시 말해줘",
    100: "지금 이 단계에서",
    125: "시약 양 알려줘",
    150: "다시 시작",
    175: "ok so thank you",
    230: CALIBRATION_SENTENCE_FALLBACK,
}
#: Lengths 25 frames apart, so a word heard with its start cut off is never
#: read as another word: a run of any other length is written as nothing.
LENGTH_OF = {text: length for length, text in WORDS.items()}


def tone(amplitude: float, phase: int) -> bytes:
    values = array.array("h", (
        int(round(32767 * amplitude * math.sin(2 * math.pi * 220 * (phase * SAMPLES + i) / 16000)))
        for i in range(SAMPLES)))
    # Never a silent sample, so every frame of a run is "voiced".
    return array.array("h", (v if v else 1 for v in values)).tobytes()


def voiced(frame: bytes) -> bool:
    return any(frame)


def runs(pcm: bytes) -> list[int]:
    lengths: list[int] = []
    count = 0
    for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
        if voiced(pcm[i:i + FRAME_BYTES]):
            count += 1
        elif count:
            lengths.append(count)
            count = 0
    if count:
        lengths.append(count)
    return lengths


def fake_transcribe(pcm: bytes, *args, **kwargs) -> Transcription:
    time.sleep(STT_SECONDS / SPEED)
    words = [WORDS[length] for length in runs(pcm) if length in WORDS]
    text = " ".join(words)
    return Transcription(text, "ko", duration_seconds=len(pcm) / 32000)


def fake_synthesize(text: str, language: str | None = None) -> bytes:
    return b"\x01\x00" * (SAMPLES * ANSWER_FRAMES)


@dataclass
class Script:
    items: list[tuple[str, object]] = field(default_factory=list)
    marks: list[tuple[str, int, dict]] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return sum(1 for kind, _ in self.items if kind == "bytes")

    def silence(self, seconds: float) -> None:
        for _ in range(int(round(seconds / 0.02))):
            self.items.append(("bytes", SILENT))

    def say(self, text: str, amplitude: float = WEARER, *, kind: str = "wearer", **info) -> None:
        self.marks.append((kind, self.frames, {"text": text, **info}))
        for i in range(LENGTH_OF[text]):
            self.items.append(("bytes", tone(amplitude, i)))

    def noise(self, frames: int = 40, amplitude: float = NOISE) -> None:
        self.marks.append(("noise", self.frames, {}))
        for i in range(frames):
            self.items.append(("bytes", tone(amplitude, i)))

    def control(self, message: dict) -> None:
        self.items.append(("text", message))


class BenchSocket:
    """The page: frames at audio pace, playback.ended after each answer's audio."""

    def __init__(self, script: Script, session: ListenerSession, clock: AudioClock) -> None:
        self.script = list(script.items)
        self.session = session
        self.clock = clock
        self.sent: list[tuple[int, dict]] = []
        self.inbox: list[dict] = []
        self.frames_sent = 0
        self.paused = False

    async def accept(self):
        return None

    async def receive(self):
        if self.inbox:
            return {"type": "websocket.receive", "text": json.dumps(self.inbox.pop(0))}
        if not self.script:
            await asyncio.sleep(2.0 / SPEED)
            if self.inbox:
                return {"type": "websocket.receive", "text": json.dumps(self.inbox.pop(0))}
            return {"type": "websocket.disconnect", "code": 1000}
        kind, value = self.script.pop(0)
        if kind == "text":
            message = dict(value)  # type: ignore[arg-type]
            message.setdefault("configuration_id", self.session.accepted_configuration_id)
            message.setdefault("generation", self.session.generation)
            return {"type": "websocket.receive", "text": json.dumps(message)}
        await asyncio.sleep(0.02 / SPEED)
        self.frames_sent += 1
        self.clock.frames = self.frames_sent
        return {"type": "websocket.receive", "bytes": value}

    async def send_text(self, value):
        message = json.loads(value)
        self.sent.append((self.frames_sent, message))
        if message.get("type") == "audio.segment.start":
            frames = int(message.get("frame_count") or 0)
            turn_id = message.get("turn_id")
            asyncio.get_running_loop().create_task(self._ended(turn_id, frames))

    async def _ended(self, turn_id, frames: int) -> None:
        await asyncio.sleep(frames * 0.02 / SPEED)
        if isinstance(turn_id, int) and turn_id > 0:
            self.inbox.append({"type": "playback.ended", "turn_id": turn_id})

    async def send_bytes(self, value):
        return None

    async def close(self, **kwargs):
        return None


class AudioClock:
    """The session's clock is the audio's: 20 ms a frame the page has sent."""

    def __init__(self) -> None:
        self.frames = 0

    def __call__(self) -> float:
        return 1000.0 + self.frames * 0.02


def make_session(clock: AudioClock) -> ListenerSession:
    fixture = _demo_fixture()
    workflow = CuratedProtocolSession(fixture)
    session = ListenerSession(
        EndpointDetector(VadConfig(), classifier=voiced, listening_onset=True),
        tool_context=ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only"),
        curated_protocol_session=workflow,
        clock=clock,
    )
    session.start()
    session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
    workflow.active = True
    workflow.current_index = 0
    return session


def build_script() -> Script:
    script = Script()
    script.silence(1.0)
    # The current screen asks for the measurement once the audio is ready.
    script.control({"type": "client.voice_calibration", "action": "start"})
    script.silence(3.0)
    script.say(CALIBRATION_SENTENCE_FALLBACK, kind="calibration")
    script.silence(5.0)
    for _ in range(3):
        script.say("현재 단계 알려줘", trial="normal")
        script.silence(6.0)
    for _ in range(3):
        script.say("다음 단계 알려줘", QUIETER, trial="quieter")
        script.silence(6.0)
    # Words said while the answer plays.
    # Said 1.6 s after the question's end was found: the answer is playing.
    script.say("현재 단계 알려줘", trial="normal")
    script.silence(2.6)
    script.say("다시 말해줘", trial="during_answer")
    script.silence(6.0)
    for _ in range(3):
        script.say("지금 이 단계에서", kind="split_first")
        script.silence(1.3)
        script.say("시약 양 알려줘", kind="split_second")
        script.silence(7.0)
    # A longer pause after words that end unfinished ("...에서"): the second
    # half starts 1.7 s after the first half's end was found.
    script.say("지금 이 단계에서", kind="split_first")
    script.silence(2.7)
    script.say("시약 양 알려줘", kind="split_second")
    script.silence(8.0)
    for _ in range(4):
        script.noise()
        script.silence(8.0)
    script.control({"type": "workflow.pause"})
    script.silence(2.0)
    script.marks.append(("pause_on", script.frames, {}))
    script.say("다음 단계 알려줘", kind="paused_other")
    script.silence(6.0)
    script.say("ok so thank you", kind="paused_other")
    script.silence(6.0)
    script.marks.append(("pause_end_check", script.frames, {}))
    script.say("다시 시작", trial="resume")
    script.silence(6.0)
    return script


def measure(script: Script, sent: list[tuple[int, dict]]) -> dict[str, object]:
    transcripts = [(at, m.get("text", "")) for at, m in sent if m.get("type") == "transcript"]
    marks = script.marks

    def heard_after(start: int, words: str, end: int | None) -> bool:
        return any(start <= at and (end is None or at < end) and words in text for at, text in transcripts)

    wearer = [(i, at, info) for i, (kind, at, info) in enumerate(marks) if kind == "wearer"]
    dropped = 0
    for index, at, info in wearer:
        following = next((m[1] for m in marks[index + 1:] if m[0] in {"wearer", "split_first", "noise", "paused_other", "calibration"}), None)
        if not heard_after(at, info["text"], following):
            dropped += 1
    firsts = [(i, at) for i, (kind, at, _) in enumerate(marks) if kind == "split_first"]
    split = 0
    for index, at in firsts:
        following = next((m[1] for m in marks[index + 2:] if m[0] != "split_second"), None)
        joined = "지금 이 단계에서 시약 양 알려줘"
        if not heard_after(at, joined, following):
            split += 1
    pause_on = next(at for kind, at, _ in marks if kind == "pause_on")
    pause_end = next(at for kind, at, _ in marks if kind == "pause_end_check")
    paused_replies = sum(
        1 for at, m in sent
        if m.get("type") == "audio.segment.start" and pause_on <= at < pause_end)
    noise_questions = sum(
        1 for _, m in sent
        if m.get("type") == "server.words" and m.get("words_kind") == "ambient_suggestion")
    resumed = any(
        m.get("type") == "protocol.fixture.state" or "workflow_status" in json.dumps(m)
        for at, m in sent if at >= pause_end) and heard_after(pause_end, "다시 시작", None)
    return {
        "wearer_utterances": len(wearer),
        "wearer_dropped": dropped,
        "wearer_dropped_rate": round(dropped / len(wearer), 3) if wearer else None,
        "split_sentences": len(firsts),
        "split": split,
        "split_rate": round(split / len(firsts), 3) if firsts else None,
        "paused_replies": paused_replies,
        "noise_questions": noise_questions,
        "resume_heard": heard_after(pause_end, "다시 시작", None),
        "resumed_signal": resumed,
        "calibration_measured": any(
            m.get("type") == "speaker.calibration" and m.get("state") == "measured" for _, m in sent),
    }


def run_bench() -> tuple[dict[str, object], list[tuple[int, dict]]]:
    clock = AudioClock()
    session = make_session(clock)
    script = build_script()
    socket = BenchSocket(script, session, clock)
    with patch.dict(os.environ, BENCH_ENVIRONMENT), patch(
        "voiney_lab.server.ListenerSession", return_value=session), patch(
        "voiney_lab.server.VoiceVadSettings.from_environment",
        return_value=SimpleNamespace(cascade=object()),
    ), patch("voiney_lab.server.VadConfig.from_settings", return_value=VadConfig()), patch(
        "voiney_lab.server.transcribe", side_effect=fake_transcribe,
    ), patch("voiney_lab.server.synthesize", side_effect=fake_synthesize), patch(
        "voiney_lab.server.VOICE_CALIBRATION_SENTENCE", CALIBRATION_SENTENCE_FALLBACK, create=True,
    ):
        asyncio.run(voice_socket(socket))
    return measure(script, socket.sent), socket.sent


def main(argv: list[str] | None = None) -> int:
    import logging
    logging.disable(logging.CRITICAL)
    result, sent = run_bench()
    if argv and "--events" in argv:
        for at, message in sent:
            keep = {k: message[k] for k in ("type", "turn_id", "text", "reason", "state", "words_kind") if k in message}
            print(at, json.dumps(keep, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
