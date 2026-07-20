"""JSON control-message helpers for the browser/server WebSocket protocol."""

from __future__ import annotations

import json
from typing import Any

MODES = {"clean", "phone", "compare"}


class ProtocolError(ValueError):
    pass


def parse_control(raw: str) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("control message must be valid JSON") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("control message needs a string type")
    if message["type"] == "capture.start":
        mode = message.get("mode", "clean")
        if mode not in MODES:
            raise ProtocolError(f"mode must be one of {sorted(MODES)}")
        return {"type": "capture.start", "mode": mode}
    if message["type"] == "capture.stop":
        return {"type": "capture.stop"}
    raise ProtocolError(f"unknown control type: {message['type']}")


def event(event_type: str, **fields: Any) -> str:
    return json.dumps({"type": event_type, **fields}, separators=(",", ":"))


def audio_start(stream: str, frame_count: int, sample_rate: int = 16_000) -> str:
    if not stream or frame_count < 0:
        raise ProtocolError("invalid outbound audio metadata")
    return event(
        "audio.start",
        stream=stream,
        encoding="pcm_s16le",
        sample_rate=sample_rate,
        channels=1,
        frame_ms=20,
        frame_count=frame_count,
    )

