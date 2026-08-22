"""Bounded, content-free runtime telemetry derived from canonical events."""

from __future__ import annotations

import math
import threading
from collections import Counter, defaultdict, deque
from typing import Any


_TIMING_KEYS = (
    "stt",
    "utterance_to_status_ms",
    "first_grok_token_ms",
    "first_sentence_ms",
    "first_audio_ms",
    "tool_ms",
    "total_ms",
    "playback_completion",
)


def _percentile(values: tuple[float, ...], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 2)


class RuntimeMetrics:
    """Aggregate event metadata without retaining text, audio, or identities."""

    def __init__(self, *, sample_limit: int = 512) -> None:
        self._lock = threading.Lock()
        self._sample_limit = max(32, int(sample_limit))
        self._counters: Counter[str] = Counter()
        self._routes: Counter[str] = Counter()
        self._intents: Counter[str] = Counter()
        self._answer_origins: Counter[str] = Counter()
        self._tools: Counter[str] = Counter()
        self._timings: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._sample_limit)
        )
        self._pending_routes: dict[tuple[int | None, int | None, int], dict[str, Any]] = {}

    @staticmethod
    def _turn_key(fields: dict[str, Any]) -> tuple[int | None, int | None, int] | None:
        turn_id = fields.get("turn_id")
        if not isinstance(turn_id, int) or isinstance(turn_id, bool):
            return None
        configuration_id = fields.get("configuration_id")
        generation = fields.get("generation")
        return (
            configuration_id if isinstance(configuration_id, int) else None,
            generation if isinstance(generation, int) else None,
            turn_id,
        )

    def observe(self, kind: str, fields: dict[str, Any]) -> None:
        """Observe a canonical event by copying only an explicit safe allowlist."""

        key = self._turn_key(fields)
        with self._lock:
            if kind == "turn.route_decision" and key is not None:
                self._pending_routes[key] = {
                    "route": str(fields.get("runtime_router") or "unknown"),
                    "intent": str(fields.get("intent") or "unknown"),
                    "answer_origin": str(fields.get("answer_origin") or "unknown"),
                    "state_mutation": fields.get("state_mutation") is True,
                    "fallback": bool(fields.get("fallback_reason")),
                }
                if len(self._pending_routes) > 2048:
                    self._pending_routes.pop(next(iter(self._pending_routes)))
            elif kind == "tool.call":
                tool = str(fields.get("tool") or "unknown")
                self._counters["tool_calls"] += 1
                self._tools[tool] += 1
                if fields.get("image_search_enabled") is True:
                    self._counters["image_search_calls"] += 1
            elif kind == "tool.result":
                if fields.get("status") in {"error", "failed", "blocked"}:
                    self._counters["tool_failures"] += 1
            elif kind == "speech.rejected":
                self._counters["speech_rejected"] += 1
            elif kind in {"cascade.playback.clear", "playback.cancelled"}:
                self._counters["barge_in_cancellations"] += 1
            elif kind == "playback.completed":
                value = fields.get("playback_completion_ms")
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    self._timings["playback_completion"].append(float(value))
            elif kind == "turn.done":
                self._counters["turns_completed"] += 1
                pending = self._pending_routes.pop(key, {}) if key is not None else {}
                route = str(fields.get("route") or pending.get("route") or "unknown")
                self._routes[route] += 1
                self._intents[str(pending.get("intent") or "unknown")] += 1
                self._answer_origins[str(
                    pending.get("answer_origin") or "unknown"
                )] += 1
                if pending.get("state_mutation") is True:
                    self._counters["mutating_turns"] += 1
                if pending.get("fallback") is True:
                    self._counters["fallback_turns"] += 1
                timings = fields.get("timings_ms")
                if isinstance(timings, dict):
                    for timing_key in _TIMING_KEYS:
                        value = timings.get(timing_key)
                        if (
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and value >= 0
                        ):
                            self._timings[timing_key].append(float(value))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            timing_values = {
                key: tuple(values) for key, values in self._timings.items()
            }
            return {
                "counters": dict(sorted(self._counters.items())),
                "routes": dict(sorted(self._routes.items())),
                "intents": dict(sorted(self._intents.items())),
                "answer_origins": dict(sorted(self._answer_origins.items())),
                "tools": dict(sorted(self._tools.items())),
                "latency_ms": {
                    key: {
                        "samples": len(values),
                        "average": round(sum(values) / len(values), 2),
                        "p95": _percentile(values, 0.95),
                    }
                    for key, values in sorted(timing_values.items())
                    if values
                },
                "retention": {
                    "maximum_samples_per_timing": self._sample_limit,
                    "raw_audio": False,
                    "transcripts": False,
                    "identifiers": False,
                },
            }


RUNTIME_METRICS = RuntimeMetrics()
