from __future__ import annotations

import json

import pytest

from voice_workflow_agent.voice_evaluation import (
    EvaluationCase,
    EvaluationCondition,
    VoiceEvaluationError,
    evaluate_cases,
    main,
    word_error_counts,
)


def _case(**overrides):
    values = {
        "case_id": "ko-fan-001",
        "source_kind": "synthetic",
        "condition": EvaluationCondition(
            noise_profile="fan",
            signal_to_noise_db=10.0,
            mask="surgical",
            microphone_distance_cm=60,
            language="ko",
            accent_or_dialect="seoul",
            speaking_rate="normal",
        ),
        "reference_transcript": "현재 단계를 완료해 주세요",
        "recognized_transcript": "현재 단계를 완료해 주세요",
        "expected_intent": "complete_step",
        "recognized_intent": "complete_step",
        "expected_mutation": True,
        "actual_mutation": True,
        "expected_vad_start_ms": 100,
        "actual_vad_start_ms": 120,
        "expected_vad_end_ms": 1000,
        "actual_vad_end_ms": 1040,
        "endpoint_latency_ms": 420,
        "barge_in_latency_ms": 110,
        "correction_or_repeat": False,
    }
    values.update(overrides)
    return EvaluationCase(**values)


def test_word_error_counts_supports_korean_tokens():
    assert word_error_counts("현재 단계를 완료", "현재 단계를 중지") == (1, 3)


def test_evaluation_computes_required_aggregate_metrics_without_audio():
    result = evaluate_cases(
        [
            _case(),
            _case(
                case_id="ko-conversation-002",
                condition=EvaluationCondition(
                    noise_profile="background_conversation",
                    signal_to_noise_db=5,
                    mask="none",
                    microphone_distance_cm=120,
                    language="ko",
                    accent_or_dialect="busan",
                    speaking_rate="fast",
                ),
                recognized_transcript="I get it",
                recognized_intent="learning",
                expected_mutation=False,
                actual_mutation=True,
                correction_or_repeat=True,
                endpoint_latency_ms=900,
                barge_in_latency_ms=250,
            ),
        ]
    )

    metrics = result["metrics"]
    assert metrics["semantic_intent_accuracy"] == 0.5
    assert metrics["workflow_command_accuracy"] == 0.5
    assert metrics["false_mutation_rate"] == 0.5
    assert metrics["correction_repeat_rate"] == 0.5
    assert metrics["endpoint_latency_ms_p95"] == 876.0
    assert result["privacy"]["raw_audio_loaded"] is False


def test_field_recording_requires_consent_and_bounded_retention():
    with pytest.raises(VoiceEvaluationError, match="consent"):
        evaluate_cases([_case(source_kind="consented_field_recording")])
    result = evaluate_cases(
        [
            _case(
                source_kind="consented_field_recording",
                consent_id="consent-study-1",
                retention_days=30,
            )
        ]
    )
    assert result["case_count"] == 1


def test_cli_reads_manifest_and_prints_aggregates(tmp_path, capsys):
    case = _case()
    payload = {
        **case.__dict__,
        "condition": case.condition.__dict__,
    }
    manifest = tmp_path / "results.json"
    manifest.write_text(json.dumps([payload], ensure_ascii=False), encoding="utf-8")
    assert main([str(manifest)]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["metrics"]["word_error_rate"] == 0.0
