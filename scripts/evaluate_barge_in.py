#!/usr/bin/env python
"""Run the synthetic interruption-gate acceptance sweep and print its metrics.

    python -B scripts/evaluate_barge_in.py            # human-readable summary
    python -B scripts/evaluate_barge_in.py --json     # machine-readable

Every number this prints comes from synthesised, constant-amplitude audio paired
with a scripted voice-activity verdict. The ratios are digital amplitude ratios,
not acoustic signal-to-noise measurements, and none of it is a dBA figure or a
claim about a real laboratory. It exists so a change to the interruption gate
can be judged reproducibly before anyone takes the product into a lab, not
instead of taking it into a lab.

Exit status is non-zero when any scenario misses its required outcome, so this
is usable as a gate in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_workflow_agent.barge_in_evaluation import (  # noqa: E402
    SCENARIOS,
    EvaluationReport,
    run_scenario,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true",
        help="emit the raw report instead of the readable summary")
    arguments = parser.parse_args(argv)

    report = EvaluationReport(
        results=[run_scenario(scenario) for scenario in SCENARIOS])
    summary = report.as_dict()

    if arguments.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if not _failures(summary) else 1

    print("Interruption-gate acceptance sweep (SYNTHETIC — not field-validated)")
    print("=" * 70)
    for row in summary["per_scenario"]:
        status = "pass" if row["passed"] else "FAIL"
        ratio = row["synthetic_signal_to_floor_ratio"]
        ratio_text = (
            f"synthetic ratio {ratio}x" if ratio is not None else "single level")
        print(
            f"[{status}] {row['scenario_id']:<32} "
            f"candidate={str(row['candidate']):<5} "
            f"confirmed={str(row['confirmed']):<5} {ratio_text}")
        if row["limitation"]:
            print(f"         limitation: {row['limitation']}")
    print("-" * 70)
    print(f"scenarios                      {summary['scenarios']}")
    print(f"false barge-in candidates      {summary['false_candidates']}")
    print(f"ignored noise scenarios        {summary['ignored_noise_scenarios']}")
    print(f"missed real interruptions      {summary['missed_interruptions']}")
    print(
        "unintended workflow mutations  "
        f"{summary['unintended_workflow_mutations']}")
    print("-" * 70)
    print(
        "Measurement basis: synthetic digital amplitude. NOT dBA, NOT acoustic "
        "SNR, NOT a real-lab result.")
    return 0 if not _failures(summary) else 1


def _failures(summary: dict[str, object]) -> int:
    return sum(1 for row in summary["per_scenario"] if not row["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
