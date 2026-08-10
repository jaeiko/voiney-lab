#!/usr/bin/env python3
"""Deterministic, no-network regression evaluation for Candidate A voice routes."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)


ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile_value
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-pdf",
        type=Path,
        default=Path("/home/student/protocol-test-files/in-gel-digestion.pdf"),
    )
    args = parser.parse_args()
    fixture = load_curated_protocol_fixture(
        ROOT / "data/development_protocols/candidate_a_curated_analysis.json",
        ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json",
        args.source_pdf,
    )
    dataset = json.loads((
        ROOT / "data/evaluation/candidate_a_real_voice_hardening.json"
    ).read_text(encoding="utf-8"))
    correct = 0
    mutation_false_positives = 0
    double_transitions = 0
    entity_total = entity_correct = 0
    visual_total = visual_correct = 0
    audio_total = audio_correct = 0
    related_dead_ends = 0
    unsupported_claims = 0
    latencies: list[float] = []
    results = []
    for turn_id, case in enumerate(dataset["cases"], 1):
        session = CuratedProtocolSession(fixture)
        session.active = True
        session.current_index = case["step_index"]
        opening_index = session.current_index
        started = time.perf_counter()
        plan = session.plan(case["text"], turn_id=turn_id, language="ko")
        latencies.append((time.perf_counter() - started) * 1000)
        changed = session.current_index != opening_index or not session.active
        route_ok = (
            plan.intent_kind == case["intent"]
            and plan.action.value == case["action"]
            and changed == case["mutates"]
        )
        correct += int(route_ok)
        mutation_false_positives += int(changed and not case["mutates"])
        double_transitions += int(session.current_index - opening_index > 1)
        if expected := case.get("resolved_entity"):
            entity_total += 1
            entity_correct += int(f"resolved_entity:{expected}" in plan.limitations)
        if expected_visual := case.get("visual"):
            visual_total += 1
            source = fixture.visual_for_step(case["step_index"])
            actual_visual = "source_crop" if source is not None else "generated_eligible"
            visual_correct += int(actual_visual == expected_visual)
        if case["intent"] == "audio_playback_help":
            audio_total += 1
            audio_correct += int(route_ok)
        if case["intent"] in {"related_question", "related_safety_question"}:
            related_dead_ends += int(
                "답변할 근거를 충분히 찾지 못했습니다" in plan.display_text
            )
        if case["intent"] in {"step_elaboration", "expected_result_explanation"}:
            unsupported_claims += sum(
                term in plan.display_text.casefold()
                for term in ("피펫으로", "use a pipette", "ppe를 착용", "자동 완료합니다")
            )
        results.append({
            "id": case["id"],
            "intent": plan.intent_kind,
            "action": plan.action.value,
            "state_changed": changed,
            "pass": route_ok,
        })
    report = {
        "dataset_version": dataset["version"],
        "case_count": len(results),
        "route_accuracy": correct / len(results),
        "completion_false_negative_count": sum(
            1 for case, result in zip(dataset["cases"], results)
            if case["intent"] in {"report_completion", "completion_and_next"}
            and not result["pass"]
        ),
        "unintended_state_mutation_count": mutation_false_positives,
        "double_transition_count": double_transitions,
        "contextual_entity_accuracy": (
            entity_correct / entity_total if entity_total else None
        ),
        "original_vs_generated_visual_decision_accuracy": (
            visual_correct / visual_total if visual_total else None
        ),
        "audio_recovery_accuracy": (
            audio_correct / audio_total if audio_total else None
        ),
        "related_question_dead_end_count": related_dead_ends,
        "unsupported_claim_count": unsupported_claims,
        "external_citation_validity": "covered_by_fake_provider_contract_tests",
        "route_latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(percentile(latencies, 0.95), 3),
        },
        "provider_calls": 0,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if correct == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
