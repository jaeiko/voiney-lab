"""LIVE2: a named live workload must not depend on corpus ordering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.sim.corpus import load_corpus
from scripts.sim.split import select_cases


MANIFEST = (
    Path(__file__).parents[1]
    / "scripts"
    / "sim"
    / "manifests"
    / "stt5_representative_40.json"
)


def _arguments() -> argparse.Namespace:
    return argparse.Namespace(
        split="all",
        split_seed=1027,
        holdout_fraction=0.3,
        emergency_matrix=False,
        protocol=None,
        family=None,
        limit=None,
        case_ids=MANIFEST,
    )


def test_stt5_manifest_selects_the_same_ids_after_protocol_reordering() -> None:
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["case_ids"]

    ordinary, ordinary_metadata = select_cases(load_corpus(), _arguments())
    reordered, reordered_metadata = select_cases(
        list(reversed(load_corpus())), _arguments(),
    )

    assert [case.code for _protocol, case in ordinary] == expected
    assert [case.code for _protocol, case in reordered] == expected
    assert ordinary_metadata["case_id_manifest"]["name"] == (
        "stt5-representative-40"
    )
    assert reordered_metadata["case_id_manifest"] == ordinary_metadata[
        "case_id_manifest"
    ]


def test_live_plan_expands_the_fixed_twenty_ids_to_forty_cases(capsys) -> None:
    from scripts.sim import live

    assert live.main([
        "--case-ids", str(MANIFEST),
        "--voices", "ko-KR-Chirp3-HD-Charon",
        "--speeds", "1",
        "--conditions", "quiet,centrifuge:10",
        "--distances", "30",
        "--denoise", "on",
        "--max-calls", "200",
        "--max-usd", "20",
    ]) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["cases"] == 40
    assert plan["split"]["case_id_manifest"]["cases"] == 20
    assert plan["external_requests_this_dry_run"] == 0
