#!/usr/bin/env python3
"""Offline, provider-free evaluation of Candidate A routing invariants."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    classify_curated_control_intent,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.language import Transcription, classify_input_event


ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return round(ordered[index], 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-pdf",
        type=Path,
        default=ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf",
    )
    args = parser.parse_args()
    fixture_path = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
    fixture = load_curated_protocol_fixture(
        fixture_path, fixture_path.with_suffix(".provenance.json"), args.source_pdf
    )
    corpus = json.loads((
        ROOT / "tests/fixtures/candidate_a_grounded_voice_eval.json"
    ).read_text(encoding="utf-8"))
    route_total = route_passed = whole_total = whole_passed = 0
    stop_total = stop_passed = noise_total = noise_passed = 0
    unintended_mutations = 0
    latencies: list[float] = []
    failures: list[dict[str, str]] = []
    for index, case in enumerate(corpus["cases"], 1):
        transcript = case["transcript"]
        started = time.perf_counter()
        if "input_rejected" in case:
            rejected = not classify_input_event(
                Transcription(transcript, "ko")
            ).accepted
            latencies.append((time.perf_counter() - started) * 1000)
            noise_total += int(case["category"] == "noise")
            noise_passed += int(case["category"] == "noise" and rejected)
            if rejected != case["input_rejected"]:
                failures.append({"category": case["category"], "transcript": transcript})
            continue
        session = CuratedProtocolSession(fixture)
        session.active = True
        session.current_index = 5
        opening = session.current_index
        intent = classify_curated_control_intent(transcript, language="ko")
        plan = session.plan(transcript, turn_id=index, language="ko")
        latencies.append((time.perf_counter() - started) * 1000)
        route_total += 1
        correct = (
            intent.action.value == case.get("expected_action", intent.action.value)
            and intent.action.value != case.get("forbidden_action")
            and intent.protocol_scope == case.get("expected_scope")
        )
        route_passed += int(correct)
        if case["category"] in {
            "total_steps", "current_position", "remaining_steps", "overview",
            "preparation", "safety", "specific_step",
        }:
            whole_total += 1
            whole_passed += int(correct)
        if case["category"] in {"stop", "negative_stop"}:
            stop_total += 1
            stop_passed += int(correct)
        expected_mutation = bool(case.get("mutates", False))
        if plan.state_changed != expected_mutation:
            unintended_mutations += int(not expected_mutation and plan.state_changed)
            correct = False
        if not expected_mutation and session.current_index != opening:
            unintended_mutations += 1
            correct = False
        if not correct:
            failures.append({"category": case["category"], "transcript": transcript})
    result = {
        "provider_request_count": 0,
        "cases": len(corpus["cases"]),
        "route_accuracy": route_passed / route_total if route_total else 1.0,
        "whole_protocol_scope_accuracy": (
            whole_passed / whole_total if whole_total else 1.0
        ),
        "stop_intent_accuracy": stop_passed / stop_total if stop_total else 1.0,
        "noise_false_accept_count": noise_total - noise_passed,
        "unintended_mutation_count": unintended_mutations,
        "route_latency_ms": {
            "p50": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95": percentile(latencies, 0.95),
        },
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
