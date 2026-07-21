"""JSON control-message helpers for the M3 browser/server protocol."""

from __future__ import annotations

import json
from typing import Any


class ProtocolError(ValueError):
    pass


def parse_control(raw: str) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("control message must be valid JSON") from exc
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("control message needs a string type")
    if message["type"] in ("session.start", "session.stop"):
        return {"type": message["type"]}
    if message["type"] == "playback.ended":
        turn_id = message.get("turn_id")
        if not isinstance(turn_id, int) or isinstance(turn_id, bool) or turn_id <= 0:
            raise ProtocolError("playback.ended needs a positive integer turn_id")
        return {"type": "playback.ended", "turn_id": turn_id}
    raise ProtocolError(f"unknown control type: {message['type']}")


def event(event_type: str, **fields: Any) -> str:
    return json.dumps({"type": event_type, **fields}, separators=(",", ":"))


def audio_start(stream: str, frame_count: int, turn_id: int,
                sample_rate: int = 16_000) -> str:
    if not stream or frame_count < 0 or turn_id <= 0:
        raise ProtocolError("invalid outbound audio metadata")
    return event("audio.start", stream=stream, turn_id=turn_id, encoding="pcm_s16le",
                 sample_rate=sample_rate, channels=1, frame_ms=20,
                 frame_count=frame_count)
