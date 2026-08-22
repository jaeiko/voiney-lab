"""Offline, aggregate-only noisy-lab voice evaluation harness."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class VoiceEvaluationError(ValueError):
    pass


NOISE_PROFILES = frozenset(
    {"clean_room", "fan", "centrifuge_like", "background_conversation"}
)
MASK_STATES = frozenset({"none", "surgical", "respirator"})
SOURCE_KINDS = frozenset({"synthetic", "consented_field_recording"})


@dataclass(frozen=True)
class EvaluationCondition:
    noise_profile: str
    signal_to_noise_db: float | None
    mask: str
    microphone_distance_cm: int
    language: str
    accent_or_dialect: str
    speaking_rate: str


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    source_kind: str
    condition: EvaluationCondition
    reference_transcript: str
    recognized_transcript: str
    expected_intent: str
    recognized_intent: str
    expected_mutation: bool
    actual_mutation: bool
    expected_vad_start_ms: float
    actual_vad_start_ms: float
    expected_vad_end_ms: float
    actual_vad_end_ms: float
    endpoint_latency_ms: float
    barge_in_latency_ms: float | None
    correction_or_repeat: bool
    consent_id: str | None = None
    retention_days: int | None = None


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def word_error_counts(reference: str, hypothesis: str) -> tuple[int, int]:
    expected = _tokens(reference)
    actual = _tokens(hypothesis)
    previous = list(range(len(actual) + 1))
    for expected_index, expected_token in enumerate(expected, start=1):
        current = [expected_index]
        for actual_index, actual_token in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[actual_index] + 1,
                    previous[actual_index - 1]
                    + int(expected_token != actual_token),
                )
            )
        previous = current
    return previous[-1], len(expected)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(ordered[lower], 3)
    return round(
        ordered[lower] * (upper - index) + ordered[upper] * (index - lower), 3
    )


def validate_case(case: EvaluationCase) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", case.case_id):
        raise VoiceEvaluationError("Evaluation case identifier is invalid.")
    if case.source_kind not in SOURCE_KINDS:
        raise VoiceEvaluationError("Evaluation source kind is invalid.")
    if case.condition.noise_profile not in NOISE_PROFILES:
        raise VoiceEvaluationError("Noise profile is invalid.")
    if case.condition.mask not in MASK_STATES:
        raise VoiceEvaluationError("Mask state is invalid.")
    if not 0 < case.condition.microphone_distance_cm <= 1000:
        raise VoiceEvaluationError("Microphone distance is invalid.")
    if case.source_kind == "consented_field_recording" and (
        not case.consent_id
        or case.retention_days is None
        or not 1 <= case.retention_days <= 365
    ):
        raise VoiceEvaluationError(
            "Field recordings require explicit consent and bounded retention."
        )
    for value in (
        case.expected_vad_start_ms,
        case.actual_vad_start_ms,
        case.expected_vad_end_ms,
        case.actual_vad_end_ms,
        case.endpoint_latency_ms,
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise VoiceEvaluationError("Evaluation timing is invalid.")


def evaluate_cases(cases: Iterable[EvaluationCase]) -> dict[str, object]:
    selected = tuple(cases)
    if not selected:
        raise VoiceEvaluationError("At least one evaluation case is required.")
    edits = words = correct_intents = correct_commands = false_mutations = repeats = 0
    endpoint_latencies: list[float] = []
    barge_in_latencies: list[float] = []
    vad_start_errors: list[float] = []
    vad_end_errors: list[float] = []
    dimensions: dict[str, int] = defaultdict(int)
    for case in selected:
        validate_case(case)
        case_edits, case_words = word_error_counts(
            case.reference_transcript, case.recognized_transcript
        )
        edits += case_edits
        words += case_words
        correct_intents += int(case.expected_intent == case.recognized_intent)
        correct_commands += int(case.expected_mutation == case.actual_mutation)
        false_mutations += int(not case.expected_mutation and case.actual_mutation)
        repeats += int(case.correction_or_repeat)
        endpoint_latencies.append(float(case.endpoint_latency_ms))
        if case.barge_in_latency_ms is not None:
            barge_in_latencies.append(float(case.barge_in_latency_ms))
        vad_start_errors.append(abs(case.actual_vad_start_ms - case.expected_vad_start_ms))
        vad_end_errors.append(abs(case.actual_vad_end_ms - case.expected_vad_end_ms))
        dimensions[
            "|".join(
                (
                    case.condition.noise_profile,
                    case.condition.mask,
                    str(case.condition.microphone_distance_cm),
                    case.condition.language,
                    case.condition.accent_or_dialect,
                    case.condition.speaking_rate,
                )
            )
        ] += 1
    count = len(selected)
    return {
        "case_count": count,
        "metrics": {
            "word_error_rate": round(edits / max(words, 1), 6),
            "semantic_intent_accuracy": round(correct_intents / count, 6),
            "workflow_command_accuracy": round(correct_commands / count, 6),
            "false_mutation_rate": round(false_mutations / count, 6),
            "vad_start_mean_absolute_error_ms": round(sum(vad_start_errors) / count, 3),
            "vad_end_mean_absolute_error_ms": round(sum(vad_end_errors) / count, 3),
            "endpoint_latency_ms_p50": _percentile(endpoint_latencies, 0.5),
            "endpoint_latency_ms_p95": _percentile(endpoint_latencies, 0.95),
            "barge_in_latency_ms_p50": _percentile(barge_in_latencies, 0.5),
            "barge_in_latency_ms_p95": _percentile(barge_in_latencies, 0.95),
            "correction_repeat_rate": round(repeats / count, 6),
        },
        "coverage": dict(sorted(dimensions.items())),
        "privacy": {
            "raw_audio_loaded": False,
            "raw_audio_persisted": False,
            "aggregate_output_only": True,
            "field_recording_requires_consent": True,
        },
    }


def case_from_mapping(value: Mapping[str, object]) -> EvaluationCase:
    condition = value.get("condition")
    if not isinstance(condition, dict):
        raise VoiceEvaluationError("Evaluation condition is absent.")
    try:
        return EvaluationCase(
            case_id=str(value["case_id"]),
            source_kind=str(value["source_kind"]),
            condition=EvaluationCondition(
                noise_profile=str(condition["noise_profile"]),
                signal_to_noise_db=(
                    float(condition["signal_to_noise_db"])
                    if condition.get("signal_to_noise_db") is not None
                    else None
                ),
                mask=str(condition["mask"]),
                microphone_distance_cm=int(condition["microphone_distance_cm"]),
                language=str(condition["language"]),
                accent_or_dialect=str(condition["accent_or_dialect"]),
                speaking_rate=str(condition["speaking_rate"]),
            ),
            reference_transcript=str(value["reference_transcript"]),
            recognized_transcript=str(value["recognized_transcript"]),
            expected_intent=str(value["expected_intent"]),
            recognized_intent=str(value["recognized_intent"]),
            expected_mutation=bool(value["expected_mutation"]),
            actual_mutation=bool(value["actual_mutation"]),
            expected_vad_start_ms=float(value["expected_vad_start_ms"]),
            actual_vad_start_ms=float(value["actual_vad_start_ms"]),
            expected_vad_end_ms=float(value["expected_vad_end_ms"]),
            actual_vad_end_ms=float(value["actual_vad_end_ms"]),
            endpoint_latency_ms=float(value["endpoint_latency_ms"]),
            barge_in_latency_ms=(
                float(value["barge_in_latency_ms"])
                if value.get("barge_in_latency_ms") is not None
                else None
            ),
            correction_or_repeat=bool(value["correction_or_repeat"]),
            consent_id=(str(value["consent_id"]) if value.get("consent_id") else None),
            retention_days=(
                int(value["retention_days"])
                if value.get("retention_days") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise VoiceEvaluationError("Evaluation case is malformed.") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute aggregate voice-quality metrics from a result manifest."
    )
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise VoiceEvaluationError("Evaluation manifest must be a list.")
        result = evaluate_cases(case_from_mapping(item) for item in payload)
    except (OSError, json.JSONDecodeError, VoiceEvaluationError) as exc:
        print(f"voice evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
