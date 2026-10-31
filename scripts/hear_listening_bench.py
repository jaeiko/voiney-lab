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

Lane HEAR2 (2026-10-26) adds what the person's second test asked to measure:

* timer_stage_unanswered -- at a step whose timer runs, the wearer's
  question, timer question, timer command and note that got no reply;
* timer_paused_the_timer -- "타이머 일시정지해 줘" paused the timer (not the run);
* noise_chain_answer_s -- in 24 s of room noise the VAD keeps taking for
  speech (bursts with pauses just over the end-of-speech silence), how
  long after the wearer's word its words reached routing; noise_chain_joins
  the continuations joined meanwhile;
* stt_failure_shown / stt_failure_said -- with the speech-to-text provider
  failing (401 quota), how many failures the screen was told of as such
  and how often it was said aloud.

Run it against any checkout to compare (the module only uses names every
checkout since lane SP1 has)::

    PYTHONPATH=<checkout>/src python scripts/hear_listening_bench.py

It prints one JSON object. Frames are sent about SPEED times faster than
real time, and the session's clock follows the frames (20 ms each), so
every pause and window is measured in audio time.
"""

from __future__ import annotations

import argparse
import array
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
import sys
import time
import wave
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

import dataclasses

from voiney_lab.audio import FRAME_BYTES
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.language import Transcription
from voiney_lab.replay_turns import _demo_fixture
from voiney_lab.server import ListenerSession, voice_socket
from voiney_lab.tools import ToolContext
from voiney_lab.vad import EndpointDetector, VadConfig, TurnState

SAMPLES = FRAME_BYTES // 2
SPEED = 10.0
SILENT = bytes(FRAME_BYTES)
#: The wearer's voice when the measurement sentence is read, and the noise
#: under the line (fractions of full scale; 0.1 reads about -23 dBFS).
WEARER = 0.1
QUIETER = 0.02   # about 14 dB under WEARER: past the "보통" 12 dB margin
NOISE = 0.008    # about 22 dB under WEARER
STT_SECONDS = 0.8
#: LIVE3 (2026-10-31): the fake speech-to-text takes this many frames of
#: audio time, not STT_SECONDS of wall time. The bench's clock is the frames
#: the page has sent; a worker measured on the wall clock finished early or
#: late under machine load, and a transcript that landed after the next
#: utterance's mark counted the earlier utterance as dropped (the CI
#: ``wearer_dropped=1`` flake). The frame pump also waits for a worker whose
#: frames are up before it sends the next frame.
STT_FRAMES = int(round(STT_SECONDS / 0.02))
#: A worker never waits longer than this on the wall clock (a stopped pump).
STT_WALL_LIMIT_SECONDS = 30.0
#: The server validates a barge-in candidate's words inside the frame loop,
#: so no frame arrives while that transcription runs (in production the
#: frames queue on the socket meanwhile). A worker whose clock has not moved
#: for this long on the wall returns: the transcription then takes no audio
#: time, as the stalled pump makes it take none in production either.
PUMP_STALL_SECONDS = 0.25
ANSWER_FRAMES = 150  # 3 s of answer audio
#: The clock the fake providers read (set by ``run_bench``), and the frame
#: deadlines of transcriptions under way.
_CLOCK: "AudioClock | None" = None
_PENDING_STT: list[int] = []
_PENDING_LOCK = __import__("threading").Lock()
#: A delay a test may put in front of the worker to stand for machine load.
_WORKER_WALL_DELAY_SECONDS = 0.0
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
    # Lane HEAR2: the timer stage and the outage.
    200: "타이머 시작해 줘",
    275: "타이머 몇 분 남았어",
    300: "타이머 일시정지해 줘",
    325: "메모해 줘 색이 노랗다",
    350: "다음 단계 뭐야",
}
#: Lane HEAR2: the fake provider fails these utterances (by their length) the
#: way ElevenLabs did in the person's test.
FAILING_LENGTHS: set[int] = set()
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


def _wait_for_stt_frames() -> None:
    """Return once STT_FRAMES of audio have passed on the bench clock (LIVE3).

    The page registers a transcription's deadline when it sees the turn's
    ``speech.end`` (``BenchSocket.send_text``), so the frames the worker
    thread takes to start never move the deadline; the worker adopts the
    earliest one. A barge-in's validation has no such event and takes its
    deadline from the clock when it starts.
    """

    clock = _CLOCK
    if clock is None:
        time.sleep(STT_SECONDS / SPEED)
        return
    if _WORKER_WALL_DELAY_SECONDS:
        time.sleep(_WORKER_WALL_DELAY_SECONDS)
    with _PENDING_LOCK:
        if _PENDING_STT:
            deadline = min(_PENDING_STT)
        else:
            deadline = clock.frames + STT_FRAMES
            _PENDING_STT.append(deadline)
    try:
        started = time.monotonic()
        last_frames, last_change = clock.frames, started
        while clock.frames < deadline and time.monotonic() - started < STT_WALL_LIMIT_SECONDS:
            if clock.frames != last_frames:
                last_frames, last_change = clock.frames, time.monotonic()
            elif time.monotonic() - last_change >= PUMP_STALL_SECONDS:
                break
            time.sleep(0.0005)
    finally:
        with _PENDING_LOCK:
            if deadline in _PENDING_STT:
                _PENDING_STT.remove(deadline)


def _forget_stt_deadline(deadline: int) -> None:
    with _PENDING_LOCK:
        if deadline in _PENDING_STT:
            _PENDING_STT.remove(deadline)


def stt_due(frames_sent: int) -> bool:
    """Whether a transcription's frames are up while it has not returned yet."""

    with _PENDING_LOCK:
        return any(deadline <= frames_sent for deadline in _PENDING_STT)


def fake_transcribe(pcm: bytes, *args, **kwargs) -> Transcription:
    _wait_for_stt_frames()
    lengths = runs(pcm)
    if any(length in FAILING_LENGTHS for length in lengths):
        from voiney_lab.voice_providers import SpeechProviderError
        raise SpeechProviderError("elevenlabs STT failed (401): quota", status=401, code="quota_exceeded")
    words = [WORDS[length] for length in lengths if length in WORDS]
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
        self.changed = asyncio.Event()
        # Server events, rather than the mutable detector state, tell the
        # bench when a started turn has actually reached its terminal signal.
        # Under load the session may enter COOLDOWN before the final event is
        # delivered to this fake page.
        self.pending_turns: set[int] = set()
        self.saw_started_turn = False
        #: LIVE3: the page's playback receipts, due at a frame of the clock.
        self.due: list[tuple[int, dict]] = []
        #: LIVE3: the transcription deadline registered at each turn's end.
        self.turn_stt_deadlines: dict[int, int] = {}

    async def accept(self):
        return None

    def _deliver_due(self) -> dict | None:
        due = [item for item in self.due if item[0] <= self.frames_sent]
        if not due:
            return None
        self.due.remove(due[0])
        return due[0][1]

    async def receive(self):
        if self.inbox:
            return {"type": "websocket.receive", "text": json.dumps(self.inbox.pop(0))}
        message = self._deliver_due()
        if message is not None:
            return {"type": "websocket.receive", "text": json.dumps(message)}
        if not self.script:
            # Audio time can end before a worker scheduled under machine
            # load returns its final STT result. Drain that real turn and
            # the page's playback receipt before disconnecting. A wall-clock
            # deadline makes this bench depend on host load; the explicit
            # terminal events below are its completion authority.
            while True:
                self.changed.clear()
                if self.inbox:
                    return {"type": "websocket.receive", "text": json.dumps(self.inbox.pop(0))}
                with _PENDING_LOCK:
                    pending_stt = list(_PENDING_STT)
                if self.due or pending_stt:
                    # The audio has ended, but a receipt or a transcription
                    # is still due: the clock moves on to the nearest one
                    # (silence the page need not send).
                    nearest = min([at for at, _ in self.due] + pending_stt)
                    self.frames_sent = max(self.frames_sent, nearest)
                    self.clock.frames = self.frames_sent
                    message = self._deliver_due()
                    if message is not None:
                        return {"type": "websocket.receive", "text": json.dumps(message)}
                if stt_due(self.frames_sent):
                    await asyncio.sleep(0.0005)
                    continue
                if self.saw_started_turn:
                    pending = bool(self.pending_turns)
                else:
                    pending = self.session.detector.state in {
                        TurnState.PROCESSING, TurnState.AGENT_SPEAKING,
                    }
                if not pending:
                    return {"type": "websocket.disconnect", "code": 1000}
                await self.changed.wait()
        kind, value = self.script.pop(0)
        if kind == "text" and str(value.get("type", "")).startswith("bench."):  # type: ignore[union-attr]
            # Lane HEAR2: the bench's own directions, never sent to the server.
            if value["type"] == "bench.step":  # type: ignore[index]
                self.session.curated_protocol_session.current_index = int(value["index"])  # type: ignore[index]
            elif value["type"] == "bench.fail":  # type: ignore[index]
                FAILING_LENGTHS.update(int(length) for length in value["lengths"])  # type: ignore[index]
            return await self.receive()
        if kind == "text":
            message = dict(value)  # type: ignore[arg-type]
            message.setdefault("configuration_id", self.session.accepted_configuration_id)
            message.setdefault("generation", self.session.generation)
            return {"type": "websocket.receive", "text": json.dumps(message)}
        await asyncio.sleep(0.02 / SPEED)
        while stt_due(self.frames_sent):
            # A transcription's frames are up: its result comes before the
            # next frame, however slow the worker thread is (LIVE3).
            await asyncio.sleep(0.0005)
        self.frames_sent += 1
        self.clock.frames = self.frames_sent
        return {"type": "websocket.receive", "bytes": value}

    async def send_text(self, value):
        message = json.loads(value)
        self.sent.append((self.frames_sent, message))
        kind = message.get("type")
        turn_id = message.get("turn_id")
        if kind == "speech.end" and isinstance(turn_id, int) and _CLOCK is self.clock:
            # The turn's transcription starts now and takes STT_FRAMES of
            # audio: its deadline is fixed here, before any worker thread
            # runs (LIVE3).
            deadline = self.frames_sent + STT_FRAMES
            self.turn_stt_deadlines[turn_id] = deadline
            with _PENDING_LOCK:
                _PENDING_STT.append(deadline)
        elif isinstance(turn_id, int) and turn_id in self.turn_stt_deadlines and (
            kind in {"transcript", "speech.dropped", "speech.rejected", "cascade.playback.clear"}
            or (kind == "turn.state" and message.get("state") not in {"listening", "transcribing"})
        ):
            # The words were written down (or the turn ended without them):
            # a deadline no worker adopted is forgotten.
            _forget_stt_deadline(self.turn_stt_deadlines.pop(turn_id))
        if kind == "speech.start" and isinstance(turn_id, int):
            self.saw_started_turn = True
            # The production socket cancels its one previous turn task when
            # it announces a new primary speech start.  That supersession has
            # no separate terminal event when the worker was still pending.
            self.pending_turns.clear()
            self.pending_turns.add(turn_id)
        elif isinstance(turn_id, int) and (
            kind in {"speech.dropped", "speech.ignored"}
            or (
                kind in {"turn.state", "cascade.playback.clear"}
                and message.get("state") in {"complete", "blocked", "cancelled", "error"}
            )
        ):
            self.pending_turns.discard(turn_id)
        self.changed.set()
        if message.get("type") == "audio.segment.start":
            frames = int(message.get("frame_count") or 0)
            turn_id = message.get("turn_id")
            if isinstance(turn_id, int) and turn_id > 0:
                # The receipt falls due when the answer's frames have played
                # on the bench clock (LIVE3: audio time, not wall time).
                self.due.append((self.frames_sent + frames, {"type": "playback.ended", "turn_id": turn_id}))
                self.changed.set()

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
    # Lane HEAR2: the second step ("Mix the sample for 30 seconds") carries its
    # 30 s timer, so the timer stage can be measured.
    fixture = dataclasses.replace(fixture, timer_manifest={fixture.steps[1].step_id: 30})
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
    # LIVE3: measured on the audio clock, the calibration turn's answer
    # (0.8 s of transcription, 3 s of audio) and its 300 ms cooldown end
    # 5.2 s after the sentence; a word 5.0 s after it began inside the
    # cooldown, where the listener hears nothing, and lost its first frames.
    # On the wall clock the answer had ended early by a machine-dependent
    # amount, which is what made the first wearer utterance flaky in CI.
    script.silence(5.5)
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
    # --- Lane HEAR2: the timer stage (the person's third step) --------------
    script.control({"type": "bench.step", "index": 1})
    script.silence(2.0)
    script.marks.append(("timer_stage_begin", script.frames, {}))
    script.say("타이머 시작해 줘", kind="timer_stage")
    script.silence(6.0)
    for text in ("현재 단계 알려줘", "타이머 몇 분 남았어", "메모해 줘 색이 노랗다", "타이머 일시정지해 줘", "다음 단계 뭐야"):
        script.say(text, kind="timer_stage")
        script.silence(7.0)
    # --- Lane HEAR2: room noise the VAD keeps taking for speech ---------------
    # Bursts of 1.6 s with 1.1 s between: each burst ends by the 1 s silence
    # and the next starts within the 1.5 s join window, as the person's room
    # noise did. 80 frames is no word's length, so the fake STT writes nothing
    # for a burst. The wearer's words come after the fifth burst (13.5 s in);
    # the noise goes on 11 bursts more (30 s).
    script.marks.append(("noise_chain", script.frames, {}))
    for i in range(16):
        if i == 5:
            script.say("현재 단계 알려줘", kind="noise_chain_word")
            script.silence(1.1)
        script.noise(frames=80, amplitude=WEARER)
        script.silence(1.1)
    script.marks.append(("noise_chain_end", script.frames, {}))
    script.silence(14.0)
    # --- Lane HEAR2: the speech-to-text provider fails (401 quota) -------------
    script.marks.append(("stt_outage", script.frames, {}))
    script.control({"type": "bench.fail", "lengths": [LENGTH_OF["다음 단계 뭐야"]]})
    for _ in range(3):
        script.say("다음 단계 뭐야", kind="stt_outage")
        script.silence(5.0)
    script.silence(4.0)
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
    # --- Lane HEAR2 ---
    def transcript_after(start: int, words: str, end: int | None) -> int | None:
        for at, text in transcripts:
            if start <= at and (end is None or at < end) and words in text:
                return at
        return None

    timer_stage = [(i, at, info) for i, (kind, at, info) in enumerate(marks) if kind == "timer_stage"]
    replies = [(at, m) for at, m in sent if m.get("type") == "reply.complete"]
    unanswered = 0
    for index, at, info in timer_stage:
        following = next((m[1] for m in marks[index + 1:]), None)
        heard_at = transcript_after(at, info["text"], following)
        if heard_at is None or not any(heard_at <= r_at and (following is None or r_at < following + 50) for r_at, _ in replies):
            unanswered += 1
    operations = [m.get("operation") for _, m in sent if m.get("type") == "server.operation"]
    noise_start = next(at for kind, at, _ in marks if kind == "noise_chain")
    noise_end = next(at for kind, at, _ in marks if kind == "noise_chain_end")
    word = next((at, info) for kind, at, info in marks if kind == "noise_chain_word")
    word_end = word[0] + LENGTH_OF[word[1]["text"]]
    heard_at = transcript_after(word[0], word[1]["text"], None)
    # A continuation cancels the turn: the page gets cascade.playback.clear with reason continued_speech.
    joins = sum(1 for at, m in sent if m.get("type") == "cascade.playback.clear"
                and m.get("reason") == "continued_speech" and noise_start <= at < noise_end + 200)
    outage_start = next(at for kind, at, _ in marks if kind == "stt_outage")
    dropped_failed = sum(1 for at, m in sent if m.get("type") == "speech.dropped" and m.get("reason") == "stt_failed" and at >= outage_start)
    said_failed = sum(1 for at, m in sent if m.get("type") == "server.words" and m.get("words_kind") == "stt_failure")
    errors = sum(1 for at, m in sent if m.get("type") == "error" and at >= outage_start)
    return {
        "timer_stage_utterances": len(timer_stage),
        "timer_stage_unanswered": unanswered,
        "timer_paused_the_timer": "step_timer_paused" in operations,
        "timer_paused_the_run": "workflow_paused" in operations[operations.index("step_timer_started") + 1:] if "step_timer_started" in operations else None,
        "noise_chain_answer_s": round((heard_at - word_end) * 0.02, 1) if heard_at is not None else None,
        "noise_chain_joins": joins,
        "stt_failure_shown": dropped_failed,
        "stt_failure_said": said_failed,
        "stt_failure_turn_errors": errors,
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


def run_bench(
    script: Script | None = None, *, measured: bool = True,
) -> tuple[dict[str, object], list[tuple[int, dict]]]:
    """Drive the socket with the script and measure it; ``measured`` False returns the events only."""

    global _CLOCK
    clock = AudioClock()
    session = make_session(clock)
    script = build_script() if script is None else script
    socket = BenchSocket(script, session, clock)
    FAILING_LENGTHS.clear()
    _CLOCK = clock
    with _PENDING_LOCK:
        _PENDING_STT.clear()
    with patch.dict(os.environ, BENCH_ENVIRONMENT), patch(
        "voiney_lab.server.ListenerSession", return_value=session), patch(
        "voiney_lab.server.VoiceVadSettings.from_environment",
        return_value=SimpleNamespace(cascade=object()),
    ), patch("voiney_lab.server.VadConfig.from_settings", return_value=VadConfig()), patch(
        "voiney_lab.server.transcribe", side_effect=fake_transcribe,
    ), patch("voiney_lab.server.synthesize", side_effect=fake_synthesize), patch(
        "voiney_lab.server.VOICE_CALIBRATION_SENTENCE", CALIBRATION_SENTENCE_FALLBACK, create=True,
    ):
        try:
            asyncio.run(voice_socket(socket))
        finally:
            _CLOCK = None
    return (measure(script, socket.sent) if measured else {}), socket.sent


def _utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("diagnostic timestamps need an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def replay_diagnostic_wavs(
    directory: Path, *, started_at: datetime, ended_at: datetime,
) -> dict[str, object]:
    """Replay a bounded diagnostics window and return anonymous counts only."""
    if started_at.tzinfo is None or ended_at.tzinfo is None or ended_at < started_at:
        raise ValueError("invalid diagnostic interval")
    selected: list[tuple[float, Path, bool]] = []
    for metadata_path in directory.glob("*.json"):
        modified = datetime.fromtimestamp(metadata_path.stat().st_mtime, timezone.utc)
        if not started_at <= modified <= ended_at:
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        word_count = metadata.get("word_count")
        if isinstance(word_count, bool) or not isinstance(word_count, int) or word_count < 0:
            raise ValueError("diagnostic word_count must be a nonnegative integer")
        selected.append((metadata_path.stat().st_mtime, metadata_path, word_count > 0))

    counts = Counter()
    rejection_reasons = Counter()
    end_delays: list[int] = []
    for _, metadata_path, word_bearing in sorted(selected):
        wav_path = metadata_path.with_suffix(".wav")
        with wave.open(str(wav_path), "rb") as recording:
            if (
                recording.getnchannels() != 1
                or recording.getsampwidth() != 2
                or recording.getframerate() != 16000
                or recording.getnframes() > 16000 * 30
            ):
                raise ValueError("diagnostic WAV must be bounded 16 kHz mono PCM16")
            pcm = recording.readframes(recording.getnframes())
        # LIVE3: frame by frame on an audio clock (20 ms a frame), so the
        # frame at which each end was found is known and the join window is
        # measured in audio time. The session stays "being answered" after
        # the first end (no turn is run here), as the live server was while
        # the person typed: sound after the words meets the same join and
        # candidate rules.
        clock = AudioClock()
        session = ListenerSession(clock=clock)
        session.start()
        clip_frames = len(pcm) // FRAME_BYTES
        events = []
        end_frames: list[int] = []
        stream = pcm + bytes(FRAME_BYTES * 250)
        for index in range(0, len(stream) // FRAME_BYTES):
            clock.frames = index
            frame_events = session.accept_chunk(stream[index * FRAME_BYTES:(index + 1) * FRAME_BYTES])
            events.extend(frame_events)
            end_frames.extend(index for event in frame_events if event.kind == "speech.end")
        stt_bound = any(event.kind == "speech.end" for event in events)
        rejection_reasons.update(
            event.result.rejection_reason or "unknown"
            for event in events if event.kind == "speech.rejected"
        )
        label = "word_bearing" if word_bearing else "empty_transcript"
        counts[f"{label}_clips"] += 1
        counts[f"{label}_stt_bound"] += int(stt_bound)
        counts["continuation_joins"] += sum(
            1 for event in events
            if event.kind == "assistant.interrupted" and event.reason == "continued_speech")
        counts["candidates_rejected"] += sum(
            1 for event in events if event.kind == "barge_in_rejected")
        ended = [event.result.total_frames for event in events if event.kind == "speech.end"]
        if ended:
            counts["longest_utterance_frames"] = max(counts["longest_utterance_frames"], max(ended))
            # How long after the clip's last frame the last end was found;
            # a clip that ends the way it was recorded is found 50 frames
            # (the endpoint silence) after its end.
            end_delays.append((max(end_frames) + 1 - clip_frames) * 20)
            if word_bearing and len(ended) == 1 and not any(
                event.kind in {"speech.rejected", "barge_in_rejected"} for event in events
            ):
                # A clip the listener hears as one utterance: the frames of
                # the recording it would no longer keep -- its tail. 0 when
                # it keeps all. Clips with impacts between words are not
                # counted here; their dropped pieces are the impacts.
                counts["single_utterance_clips"] += 1
                counts["word_bearing_frames_lost"] += max(0, clip_frames - ended[0])

    return {
        "mode": "diagnostic_wav_replay",
        "clips": len(selected),
        "word_bearing_clips": counts["word_bearing_clips"],
        "word_bearing_stt_bound": counts["word_bearing_stt_bound"],
        "empty_transcript_clips": counts["empty_transcript_clips"],
        "empty_transcript_stt_bound": counts["empty_transcript_stt_bound"],
        "rejections": dict(sorted(rejection_reasons.items())),
        "continuation_joins": counts["continuation_joins"],
        "candidates_rejected": counts["candidates_rejected"],
        "longest_utterance_frames": counts["longest_utterance_frames"],
        "single_utterance_clips": counts["single_utterance_clips"],
        "word_bearing_frames_lost": counts["word_bearing_frames_lost"],
        "end_delay_ms_max": max(end_delays) if end_delays else None,
    }


def main(argv: list[str] | None = None) -> int:
    import logging
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", action="store_true")
    parser.add_argument("--diagnostics-dir", type=Path)
    parser.add_argument("--diagnostics-start-utc")
    parser.add_argument("--diagnostics-end-utc")
    args = parser.parse_args(argv)
    diagnostic_values = (
        args.diagnostics_dir, args.diagnostics_start_utc, args.diagnostics_end_utc,
    )
    if any(diagnostic_values):
        if not all(diagnostic_values) or args.events:
            parser.error("diagnostic replay needs its directory and both UTC bounds")
        result = replay_diagnostic_wavs(
            args.diagnostics_dir,
            started_at=_utc_timestamp(args.diagnostics_start_utc),
            ended_at=_utc_timestamp(args.diagnostics_end_utc),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    result, sent = run_bench()
    if args.events:
        for at, message in sent:
            keep = {k: message[k] for k in ("type", "turn_id", "text", "reason", "state", "words_kind") if k in message}
            print(at, json.dumps(keep, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
