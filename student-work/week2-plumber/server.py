"""M2 Plumber: push-to-talk PCM WebSocket voice cascade."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

from audio import FRAME_BYTES, FrameBuffer, clean_path, pcm_to_wav, phone_path
from protocol import ProtocolError, audio_start, event, parse_control

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("plumber")
app = FastAPI(title="M2 Plumber")
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
    text = response.json().get("text", "").strip()
    if not text:
        raise RuntimeError("xAI STT returned no transcript")
    return text


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
    """Validate raw PCM by status/configuration, not an overly exact MIME string."""
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


async def send_audio(websocket: WebSocket, stream: str, pcm: bytes) -> int:
    frames = frame_complete_audio(pcm)
    await websocket.send_text(audio_start(stream, len(frames)))
    for frame in frames:
        await websocket.send_bytes(frame)
    await websocket.send_text(event("audio.end", stream=stream))
    return len(frames)


async def run_turn(websocket: WebSocket, source_pcm: bytes, mode: str, input_frames: int) -> None:
    clean_pcm = clean_path(source_pcm)
    phone_pcm = phone_path(source_pcm)
    if mode == "compare":
        await send_audio(websocket, "clean-preview", clean_pcm)
        await send_audio(websocket, "phone-preview", phone_pcm)
    selected_pcm = phone_pcm if mode == "phone" else clean_pcm
    timer = StageTimer()
    await websocket.send_text(event("turn.processing", mode=mode, input_frames=input_frames))
    if mode == "compare":
        with timer.stage("stt_clean"):
            clean_transcript = await asyncio.to_thread(transcribe, clean_pcm)
        with timer.stage("stt_phone"):
            phone_transcript = await asyncio.to_thread(transcribe, phone_pcm)
        transcript = clean_transcript
        await websocket.send_text(event(
            "comparison.result",
            clean_transcript=clean_transcript,
            phone_transcript=phone_transcript,
            clean_stt_ms=timer.timings_ms["stt_clean"],
            phone_stt_ms=timer.timings_ms["stt_phone"],
        ))
    else:
        with timer.stage("stt"):
            transcript = await asyncio.to_thread(transcribe, selected_pcm)
        await websocket.send_text(event("transcript", text=transcript))
    with timer.stage("llm"):
        reply = await asyncio.to_thread(chat, transcript)
    await websocket.send_text(event("reply", text=reply))
    with timer.stage("tts"):
        reply_pcm = await asyncio.to_thread(synthesize, reply)
    outbound_frames = await send_audio(websocket, "tts", reply_pcm)
    await websocket.send_text(event(
        "turn.done", mode=mode, timings_ms=timer.timings_ms,
        input_frames=input_frames, output_frames=outbound_frames,
    ))


@app.websocket("/ws")
async def voice_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text(event("ready", sample_rate=16_000, frame_ms=20, frame_bytes=FRAME_BYTES))
    capture: FrameBuffer | None = None
    utterance = bytearray()
    mode = "clean"
    input_frames = 0
    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                if capture is None:
                    raise ProtocolError("audio arrived before capture.start")
                for frame in capture.push(message["bytes"]):
                    utterance.extend(frame)
                    input_frames += 1
                continue
            if message.get("text") is None:
                continue
            control = parse_control(message["text"])
            if control["type"] == "capture.start":
                if capture is not None:
                    raise ProtocolError("capture already active")
                capture, utterance = FrameBuffer(), bytearray()
                mode, input_frames = control["mode"], 0
                await websocket.send_text(event("capture.started", mode=mode))
            elif control["type"] == "capture.stop":
                if capture is None:
                    raise ProtocolError("capture is not active")
                tail = capture.finish(pad=True)
                if tail is not None:
                    utterance.extend(tail)
                    input_frames += 1
                capture = None
                if not utterance:
                    raise ProtocolError("empty utterance")
                try:
                    await run_turn(websocket, bytes(utterance), mode, input_frames)
                except Exception as exc:
                    log.exception("voice turn failed")
                    await websocket.send_text(event("error", message=str(exc)))
    except WebSocketDisconnect:
        log.info("browser disconnected")
    except ProtocolError as exc:
        await websocket.send_text(event("error", message=str(exc)))
        await websocket.close(code=1008)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

