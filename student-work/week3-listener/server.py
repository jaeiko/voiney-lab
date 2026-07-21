"""M3 Listener: hands-free, endpointed PCM WebSocket voice cascade."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

from audio import FRAME_BYTES, FrameBuffer, clean_path, pcm_to_wav
from protocol import ProtocolError, audio_start, event, parse_control
from vad import EndpointDetector, EndpointResult, TurnState, VadConfig

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("listener")
app = FastAPI(title="M3 Listener")
STATIC_DIR = Path(__file__).with_name("static")

SYSTEM_PROMPT = (
    "You are a friendly voice assistant. Your response is spoken aloud, so answer "
    "in one to three short conversational sentences without markdown."
)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def api_url(path: str) -> str:
    return f"{os.environ.get('XAI_BASE_URL', 'https://api.x.ai/v1').rstrip('/')}/{path.lstrip('/')}"


class StageTimer:
    def __init__(self) -> None:
        self.timings_ms: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings_ms[name] = round((time.perf_counter() - started) * 1000)


def transcribe(pcm: bytes) -> str:
    response = requests.post(
        api_url("stt"),
        headers={"Authorization": f"Bearer {require_env('XAI_API_KEY')}"},
        files={"file": ("utterance.wav", pcm_to_wav(pcm), "audio/wav")},
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get("text", "").strip()


def chat(transcript: str) -> str:
    client = OpenAI(base_url=api_url(""), api_key=require_env("XAI_API_KEY"))
    response = client.chat.completions.create(
        model=require_env("CHAT_MODEL"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    )
    reply = response.choices[0].message.content
    if not reply or not reply.strip():
        raise RuntimeError("Grok returned no reply")
    return reply.strip()


def validate_tts_pcm(response: requests.Response) -> bytes:
    if not response.ok:
        detail = response.text[:500] if response.content else "empty response"
        raise RuntimeError(f"xAI TTS failed ({response.status_code}): {detail}")
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        raise RuntimeError(f"xAI TTS returned JSON instead of requested raw PCM: {response.text[:500]}")
    pcm = response.content
    if not pcm:
        raise RuntimeError("xAI TTS returned empty audio")
    if len(pcm) % 2:
        raise RuntimeError("xAI TTS PCM must contain an even number of bytes")
    return pcm


def synthesize(reply: str) -> bytes:
    response = requests.post(
        api_url("tts"),
        headers={"Authorization": f"Bearer {require_env('XAI_API_KEY')}"},
        json={
            "text": reply,
            "voice_id": require_env("TTS_VOICE"),
            "language": "auto",
            "output_format": {"codec": "pcm", "sample_rate": 16_000},
        },
        timeout=120,
    )
    return validate_tts_pcm(response)


def frame_complete_audio(pcm: bytes) -> list[bytes]:
    buffer = FrameBuffer()
    frames = buffer.push(pcm)
    tail = buffer.finish(pad=True)
    if tail is not None:
        frames.append(tail)
    return frames


@dataclass(frozen=True)
class ListenerEvent:
    kind: str
    turn_id: int
    result: EndpointResult


class ListenerSession:
    """Server-authoritative turn state, including deterministic cooldown."""

    def __init__(self, detector: EndpointDetector | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.detector = detector or EndpointDetector()
        self.clock = clock
        self.active = False
        self.framer = FrameBuffer()
        self.next_turn_id = 1
        self.active_turn_id: int | None = None
        self.cooldown_until = 0.0

    @property
    def state(self) -> TurnState:
        return self.detector.state

    def start(self) -> None:
        self.active = True
        self.active_turn_id = None
        self.cooldown_until = 0.0
        self.framer = FrameBuffer()
        self.detector.reset()

    def stop(self) -> None:
        self.active = False
        self.active_turn_id = None
        self.cooldown_until = 0.0
        self.framer = FrameBuffer()
        self.detector.reset()

    def refresh_cooldown(self) -> bool:
        if self.state == TurnState.COOLDOWN and self.clock() >= self.cooldown_until:
            self.active_turn_id = None
            self.framer = FrameBuffer()
            self.detector.reset()
            return True
        return False

    def accept_chunk(self, chunk: bytes) -> list[ListenerEvent]:
        if not self.active:
            return []
        self.refresh_cooldown()
        if self.state not in (TurnState.IDLE, TurnState.USER_SPEAKING):
            return []
        events: list[ListenerEvent] = []
        for frame in self.framer.push(chunk):
            if self.state not in (TurnState.IDLE, TurnState.USER_SPEAKING):
                break
            result = self.detector.process(frame)
            if result.speech_started:
                self.active_turn_id = self.next_turn_id
                self.next_turn_id += 1
                events.append(ListenerEvent("speech.start", self.active_turn_id, result))
            if result.rejected:
                turn_id = self.active_turn_id or 0
                events.append(ListenerEvent("speech.rejected", turn_id, result))
                self.active_turn_id = None
                self.framer = FrameBuffer()
            elif result.utterance is not None:
                if self.active_turn_id is None:
                    raise RuntimeError("committed utterance has no turn_id")
                events.append(ListenerEvent("speech.end", self.active_turn_id, result))
                self.framer = FrameBuffer()
        return events

    def start_playback(self, turn_id: int) -> bool:
        if self.state != TurnState.PROCESSING or turn_id != self.active_turn_id:
            return False
        self.detector.state = TurnState.AGENT_SPEAKING
        return True

    def playback_ended(self, turn_id: int) -> bool:
        if self.state != TurnState.AGENT_SPEAKING or turn_id != self.active_turn_id:
            return False
        self.detector.reset(TurnState.COOLDOWN)
        self.cooldown_until = self.clock() + self.detector.config.cooldown_ms / 1000
        self.framer = FrameBuffer()
        return True

    def cascade_failed(self, turn_id: int) -> None:
        if turn_id == self.active_turn_id:
            self.active_turn_id = None
            self.framer = FrameBuffer()
            self.detector.reset()

    def reject_empty_transcript(self, turn_id: int) -> bool:
        if self.state != TurnState.PROCESSING or turn_id != self.active_turn_id:
            return False
        self.active_turn_id = None
        self.framer = FrameBuffer()
        self.detector.reset(TurnState.COOLDOWN)
        self.cooldown_until = self.clock() + self.detector.config.cooldown_ms / 1000
        return True


async def send_audio(websocket: WebSocket, stream: str, pcm: bytes, turn_id: int) -> int:
    frames = frame_complete_audio(pcm)
    await websocket.send_text(audio_start(stream, len(frames), turn_id))
    for frame in frames:
        await websocket.send_bytes(frame)
    await websocket.send_text(event("audio.end", stream=stream, turn_id=turn_id))
    return len(frames)


async def run_turn(websocket: WebSocket, session: ListenerSession, source_pcm: bytes,
                   turn_id: int, input_frames: int, voiced_frames: int = 0) -> None:
    selected_pcm = clean_path(source_pcm)
    timer = StageTimer()
    await websocket.send_text(event("turn.processing", turn_id=turn_id,
                                    input_frames=input_frames))
    with timer.stage("stt"):
        transcript = await asyncio.to_thread(transcribe, selected_pcm)
    if not transcript.strip():
        if session.reject_empty_transcript(turn_id):
            await websocket.send_text(event(
                "speech.rejected", turn_id=turn_id, reason="empty_transcript",
                voiced_frames=voiced_frames, total_frames=input_frames,
                duration_ms=input_frames * 20,
            ))
            await websocket.send_text(event(
                "state.changed", state=session.state.value, turn_id=turn_id,
                cooldown_ms=session.detector.config.cooldown_ms,
            ))
        return
    await websocket.send_text(event("transcript", turn_id=turn_id, text=transcript))
    with timer.stage("llm"):
        reply = await asyncio.to_thread(chat, transcript)
    await websocket.send_text(event("reply", turn_id=turn_id, text=reply))
    with timer.stage("tts"):
        reply_pcm = await asyncio.to_thread(synthesize, reply)
    if not session.start_playback(turn_id):
        return
    await websocket.send_text(event("state.changed", state=session.state.value, turn_id=turn_id))
    outbound_frames = await send_audio(websocket, "tts", reply_pcm, turn_id)
    await websocket.send_text(event(
        "turn.done", turn_id=turn_id, timings_ms=timer.timings_ms,
        input_frames=input_frames, output_frames=outbound_frames,
    ))


async def run_turn_safely(websocket: WebSocket, session: ListenerSession, source_pcm: bytes,
                          turn_id: int, input_frames: int, voiced_frames: int = 0) -> None:
    try:
        await run_turn(websocket, session, source_pcm, turn_id, input_frames, voiced_frames)
    except asyncio.CancelledError:
        session.cascade_failed(turn_id)
        raise
    except Exception as exc:
        log.exception("voice turn failed")
        session.cascade_failed(turn_id)
        await websocket.send_text(event("error", turn_id=turn_id, message=str(exc)))
        await websocket.send_text(event("state.changed", state=session.state.value,
                                        turn_id=turn_id))


@app.websocket("/ws")
async def voice_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    config = VadConfig()
    session = ListenerSession(EndpointDetector(config))
    cascade_task: asyncio.Task[None] | None = None
    await websocket.send_text(event(
        "ready", sample_rate=16_000, frame_ms=20, frame_bytes=FRAME_BYTES,
        vad_mode=config.mode, endpoint_silence_ms=config.endpoint_silence_frames * 20,
        prefix_padding_ms=config.prefix_frames * 20,
    ))
    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                became_idle = session.refresh_cooldown()
                if became_idle:
                    await websocket.send_text(event("state.changed", state=TurnState.IDLE.value))
                for item in session.accept_chunk(message["bytes"]):
                    if item.kind == "speech.start":
                        log.info("[SPEECH START] turn_id=%s", item.turn_id)
                        await websocket.send_text(event(
                            "speech.start", turn_id=item.turn_id,
                            state=TurnState.USER_SPEAKING.value,
                            voiced_frames=item.result.voiced_frames,
                            total_frames=item.result.total_frames,
                            duration_ms=item.result.total_frames * 20,
                        ))
                    elif item.kind == "speech.rejected":
                        log.info("[SPEECH REJECTED] turn_id=%s voiced_frames=%s",
                                 item.turn_id, item.result.voiced_frames)
                        await websocket.send_text(event(
                            "speech.rejected", turn_id=item.turn_id,
                            voiced_frames=item.result.voiced_frames,
                            total_frames=item.result.total_frames,
                            duration_ms=item.result.total_frames * 20,
                            reason=item.result.rejection_reason,
                        ))
                        await websocket.send_text(event("state.changed", state=TurnState.IDLE.value))
                    elif item.kind == "speech.end":
                        duration = item.result.total_frames * 20 / 1000
                        log.info("[SPEECH END after %.1f s -> sending to Grok] turn_id=%s",
                                 duration, item.turn_id)
                        await websocket.send_text(event(
                            "speech.end", turn_id=item.turn_id,
                            duration_ms=item.result.total_frames * 20,
                            total_frames=item.result.total_frames,
                            voiced_frames=item.result.voiced_frames,
                            forced=item.result.forced,
                            state=TurnState.PROCESSING.value,
                        ))
                        if cascade_task is not None and not cascade_task.done():
                            raise RuntimeError("duplicate active cascade")
                        cascade_task = asyncio.create_task(run_turn_safely(
                            websocket, session, item.result.utterance or b"",
                            item.turn_id, item.result.total_frames,
                            item.result.voiced_frames,
                        ))
                continue
            if message.get("text") is None:
                continue
            control = parse_control(message["text"])
            if control["type"] == "session.start":
                if session.active:
                    raise ProtocolError("session already active")
                session.start()
                await websocket.send_text(event("session.started", state=session.state.value))
            elif control["type"] == "session.stop":
                if cascade_task is not None and not cascade_task.done():
                    cascade_task.cancel()
                session.stop()
                await websocket.send_text(event("session.stopped", state=session.state.value))
            elif control["type"] == "playback.ended":
                if session.playback_ended(control["turn_id"]):
                    await websocket.send_text(event(
                        "state.changed", state=session.state.value,
                        turn_id=control["turn_id"],
                        cooldown_ms=config.cooldown_ms,
                    ))
    except WebSocketDisconnect:
        if cascade_task is not None and not cascade_task.done():
            cascade_task.cancel()
        session.stop()
        log.info("browser disconnected")
    except ProtocolError as exc:
        if cascade_task is not None and not cascade_task.done():
            cascade_task.cancel()
        session.stop()
        await websocket.send_text(event("error", message=str(exc)))
        await websocket.close(code=1008)
    except Exception as exc:
        if cascade_task is not None and not cascade_task.done():
            cascade_task.cancel()
        session.stop()
        log.exception("listener session failed")
        await websocket.send_text(event("error", message=str(exc)))


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
