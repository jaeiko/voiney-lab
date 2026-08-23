"""Runtime metrics remain bounded and exclude conversation content."""

from __future__ import annotations

import json

from voice_workflow_agent.runtime_metrics import RuntimeMetrics


def test_runtime_metrics_aggregate_canonical_events_without_content() -> None:
    metrics = RuntimeMetrics(sample_limit=32)
    identity = {"configuration_id": 4, "generation": 2, "turn_id": 7}
    metrics.observe("turn.route_decision", {
        **identity,
        "normalized_text": "private operator request",
        "runtime_router": "curated_protocol",
        "intent": "learning",
        "answer_origin": "current_protocol",
        "state_mutation": False,
        "fallback_reason": "local_specialized_answer_unavailable",
    })
    metrics.observe("tool.call", {
        **identity,
        "tool": "search_authoritative_web",
        "image_search_enabled": True,
        "query": "private chemical query",
    })
    metrics.observe("turn.done", {
        **identity,
        "route": "curated_protocol",
        "timings_ms": {"stt": 100, "first_audio_ms": 450, "total_ms": 900},
    })
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["turns_completed"] == 1
    assert snapshot["counters"]["image_search_calls"] == 1
    assert snapshot["counters"]["fallback_turns"] == 1
    assert snapshot["routes"] == {"curated_protocol": 1}
    assert snapshot["intents"] == {"learning": 1}
    assert snapshot["latency_ms"]["first_audio_ms"]["p95"] == 450
    encoded = json.dumps(snapshot)
    assert "private" not in encoded
    assert '"turn_id"' not in encoded
    assert snapshot["retention"]["maximum_samples_per_timing"] == 32
