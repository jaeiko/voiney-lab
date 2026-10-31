"""LIVE3: the SIM's continuing-impact axis and the fixed diagnostic sample.

Offline only: no provider is called. The impacts are the procedural pulse
trains of lane LISTEN3; the words are the oracle carrier.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scripts.sim.audio import (
    CONTINUOUS_IMPACT_SECONDS, IMPACT_PATTERNS, append_continuous_impact,
    continuous_impact_pcm, oracle_audio, overlay_impact,
)
from scripts.sim.corpus import generate_cases, load_corpus
from scripts.sim.metrics import LATE_END_MS, aggregate, score_case
from scripts.sim.runner import run_case
from voiney_lab.audio import FRAME_BYTES

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts/sim/manifests/live3_dev_diag_30.json"


def _wearer_case():
    protocol = load_corpus()[0]
    case = next(c for c in generate_cases(protocol) if c.family == "complete" and c.text == "다음")
    return protocol, case


def test_continuous_typing_after_the_words_ends_the_turn_once_within_the_endpoint_silence() -> None:
    protocol, case = _wearer_case()
    oracle = oracle_audio()
    for kind in ("typing", "pipette", "glass"):
        result = asyncio.run(run_case(
            protocol, case, pcm=append_continuous_impact(oracle, kind, 30),
            classifier=lambda frame: any(frame), transient_noise_gate=True,
        ))
        assert result["stt_calls"] == 1, (kind, result["drop_reasons"])
        assert result["endpoint_count"] == 1, (kind, result["endpoint_audio_frames"])
        assert result["continuation_joins"] == 0, kind
        assert result["endpoint_audio_frames"] == [len(oracle) // FRAME_BYTES], kind
        # The end is found on the frame that completes the endpoint silence.
        end_delay_ms = (result["endpoint_stream_frames"][0] + 1 - len(oracle) // FRAME_BYTES) * 20
        assert 1000 <= end_delay_ms <= LATE_END_MS, (kind, end_delay_ms)
        assert result["action"] == "next"


def test_typing_over_the_words_still_reaches_speech_recognition() -> None:
    protocol, case = _wearer_case()
    oracle = oracle_audio()
    for distance_cm in (30, 100):
        result = asyncio.run(run_case(
            protocol, case, pcm=overlay_impact(oracle, "typing", distance_cm),
            classifier=lambda frame: any(frame), transient_noise_gate=True,
        ))
        assert result["stt_calls"] == 1, (distance_cm, result["drop_reasons"])
        assert result["endpoint_audio_frames"] == [len(oracle) // FRAME_BYTES]
        assert result["continuation_joins"] == 0


def test_continuous_impacts_alone_never_reach_speech_recognition() -> None:
    protocol = load_corpus()[0]
    noise_case = next(c for c in generate_cases(protocol) if c.family == "noise")
    for kind in ("typing", "pipette", "glass"):
        result = asyncio.run(run_case(
            protocol, noise_case, pcm=continuous_impact_pcm(kind, 30),
            classifier=lambda frame: any(frame), transient_noise_gate=True,
        ))
        assert result["stt_calls"] == 0, kind
        assert result["changed_fields"] == []
        assert set(result["drop_reasons"]) <= {"transient_noise", "no_vad_endpoint"}, kind


def test_continuous_pattern_is_a_distinct_workload_with_end_delay_and_join_counts(tmp_path, monkeypatch) -> None:
    from scripts.sim import run
    from scripts.sim.compare import compare

    protocol, case = _wearer_case()
    monkeypatch.setattr(run, "prepare_cases", lambda args: ([(protocol, case)], {"name": "development"}, {}, {}))
    monkeypatch.setattr(run, "output_dir", lambda path: path.mkdir(parents=True, exist_ok=True))
    legacy = tmp_path / "legacy.json"
    current = tmp_path / "current.json"
    single = tmp_path / "single.json"
    assert run.main(["--impact-matrix", "--impact-pattern", "continuous", "--impact-filter", "legacy", "--output", str(legacy)]) == 0
    assert run.main(["--impact-matrix", "--impact-pattern", "continuous", "--impact-filter", "current", "--output", str(current)]) == 0
    assert run.main(["--impact-matrix", "--output", str(single)]) == 0
    before, after, one = (json.loads(path.read_text()) for path in (legacy, current, single))
    assert before["impact_pattern"] == after["impact_pattern"] == "continuous"
    assert one["impact_pattern"] == "single"
    assert before["workload_sha256"] == after["workload_sha256"] != one["workload_sha256"]
    assert before["overall"]["cases"] == after["overall"]["cases"] == 6
    # Without the gate the impacts chain the turn; with it the end is found once, in time.
    assert before["overall"]["continuation_joins"] > 0
    assert after["overall"]["continuation_joins"] == 0
    assert after["overall"]["late_end"] == 0
    assert after["overall"]["impact_reopened"] == 0
    assert after["overall"]["wearer_dropped"] == 0
    assert after["overall"]["wrong_state"] == 0
    assert after["overall"]["end_delay_ms"]["n"] == 6
    assert 1000 <= after["overall"]["end_delay_ms"]["p95"] <= LATE_END_MS
    assert compare(before, after)["regressions"] == []
    assert all(row["impact_pattern"] == "continuous" for row in after["rows"])
    assert set(IMPACT_PATTERNS) == {"single", "continuous", "mid_speech"}
    assert CONTINUOUS_IMPACT_SECONDS >= 3.0


def test_end_delay_aggregate_counts_only_measured_rows() -> None:
    protocol, case = _wearer_case()
    base = asyncio.run(run_case(protocol, case, classifier=lambda frame: any(frame)))
    rows = [score_case(case, base) for _ in range(3)]
    rows[0]["end_delay_ms"] = 1000
    rows[1]["end_delay_ms"] = LATE_END_MS + 20
    rows[2]["end_delay_ms"] = None
    summary = aggregate(rows)["overall"]
    assert summary["end_delay_ms"]["n"] == 2
    assert summary["late_end"] == 1
    assert summary["continuation_joins"] == 0


def test_diagnostic_manifest_is_the_generator_output_and_only_development_cases() -> None:
    from collections import Counter

    from scripts.sim.diagnostic_manifest import (
        FIELD_QUOTAS, development_pairs, family_group, length_bucket, manifest_document, select,
    )
    from scripts.sim.split import load_case_id_manifest, select_cases
    import argparse

    pairs = development_pairs()
    assert MANIFEST.read_text(encoding="utf-8") == manifest_document(select(pairs))
    manifest = load_case_id_manifest(MANIFEST)
    assert manifest.name == "live3-dev-diag-30"
    assert len(manifest.case_ids) == 30
    index = {case.code: (protocol, case) for protocol, case in pairs}
    assert set(manifest.case_ids) <= set(index), "a holdout or unknown case id"
    fields = Counter(index[code][0].field for code in manifest.case_ids)
    assert fields == Counter(FIELD_QUOTAS)
    groups = Counter(family_group(index[code][1].family) for code in manifest.case_ids)
    assert set(groups) == {"command", "question", "record", "other"}
    assert min(groups.values()) >= 5
    lengths = Counter(length_bucket(index[code][1].text) for code in manifest.case_ids)
    assert set(lengths) == {"short", "medium", "long"}
    assert min(lengths.values()) >= 5
    texts = {" ".join(index[code][1].text.split()).casefold() for code in manifest.case_ids}
    assert len(texts) == 30
    protocols = Counter(index[code][0].code for code in manifest.case_ids)
    assert sum(1 for code, count in protocols.items() if code.startswith("pio_")) >= 6
    selected, metadata = select_cases(load_corpus(), argparse.Namespace(
        split="all", split_seed=1027, holdout_fraction=0.3, emergency_matrix=False,
        protocol=None, family=None, limit=None, case_ids=MANIFEST,
    ))
    assert [case.code for _, case in selected] == list(manifest.case_ids)
    assert metadata["case_id_manifest"]["cases"] == 30
