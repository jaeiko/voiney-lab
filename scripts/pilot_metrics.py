#!/usr/bin/env python3
"""Export the pilot's metrics as CSV, by period and account (lane LOG, decision 4).

Four kinds, each one row per thing:

- ``experiments``: one experiment record -- start and end, steps and how they
  were passed (voice, a tapped button), turns and the router's results, timer
  notices and the time to the next words, connections lost, records,
  deviations and anomalies, whether the report was made, the rating.
- ``analyses``: one analysis that ended -- its result (passed, review_pending,
  notice, failed), how long it took, its review cards, the time until the
  review was registered, whether the protocol then ran.
- ``days``: one day (Korea) -- the two above added up, for the admin screen's
  table.
- ``notes``: the one-line memos under the end-of-experiment ratings. These are
  words the experimenters typed for the team, so they come only when asked
  for by this kind; no other kind and no admin screen shows them.

The columns are defined in ``docs/PILOT_GUIDE.md`` ("무엇을 재는가").

It reads the pilot data folder (``data/runtime/pilot``, the folder
``scripts/run_pilot.sh`` writes) or the three files named one by one, and only
ever reads copies of them: the folder is left exactly as it was, so it can run
while the pilot server is running. Nothing is sent anywhere.

    python scripts/pilot_metrics.py days --from 2026-10-20 --to 2026-10-26
    python scripts/pilot_metrics.py experiments --account oidc:1a2b... --out week.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date
from pathlib import Path

from voiney_lab.pilot_metrics import (
    ANALYSIS_COLUMNS,
    DAY_COLUMNS,
    EXPERIMENT_COLUMNS,
    NOTE_COLUMNS,
    PilotSources,
    analysis_rows,
    day_rows,
    experiment_rows,
    note_rows,
)

ROOT = Path(__file__).resolve().parents[1]
PILOT_DATA_DIR = ROOT / "data" / "runtime" / "pilot"

KINDS = {
    "experiments": (EXPERIMENT_COLUMNS, experiment_rows),
    "analyses": (ANALYSIS_COLUMNS, analysis_rows),
    "days": (DAY_COLUMNS, day_rows),
    "notes": (NOTE_COLUMNS, note_rows),
}


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"날짜는 YYYY-MM-DD 로 씁니다: {value}") from exc


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(
        description="파일럿 지표를 CSV 로 내보냅니다 (읽기만 합니다; 사본을 읽어 원본 폴더를 바꾸지 않습니다).")
    built.add_argument("kind", choices=sorted(KINDS), help="experiments, analyses, days, notes")
    built.add_argument("--data-dir", type=Path, default=None,
                       help=f"파일럿 데이터 폴더 (기본 {PILOT_DATA_DIR.relative_to(ROOT)})")
    built.add_argument("--metrics-db", type=Path, default=None, help="pilot_metrics.sqlite 를 따로 지정")
    built.add_argument("--reports-db", type=Path, default=None, help="experiment_reports.sqlite 를 따로 지정")
    built.add_argument("--workspace-db", type=Path, default=None,
                       help="commercial_workspace.sqlite 를 따로 지정")
    built.add_argument("--from", dest="since", type=_day, default=None, help="이날부터 (한국 날짜)")
    built.add_argument("--to", dest="until", type=_day, default=None, help="이날까지 (한국 날짜)")
    built.add_argument("--account", default=None, help="이 계정 id 만")
    built.add_argument("--out", type=Path, default=None, help="CSV 파일 (없으면 화면에 씀)")
    return built


def sources(arguments: argparse.Namespace) -> PilotSources:
    named = (arguments.metrics_db, arguments.reports_db, arguments.workspace_db)
    if any(named):
        if arguments.data_dir is not None:
            raise SystemExit("--data-dir 와 파일 하나씩 지정하기는 함께 쓰지 않습니다.")
        return PilotSources(*named)
    return PilotSources.from_data_dir(arguments.data_dir or PILOT_DATA_DIR)


def render(columns: tuple[str, ...], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in columns})
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.since and arguments.until and arguments.since > arguments.until:
        raise SystemExit("--from 이 --to 보다 늦습니다.")
    columns, read = KINDS[arguments.kind]
    rows = read(
        sources(arguments), since=arguments.since, until=arguments.until,
        account=arguments.account,
    )
    text = render(columns, rows)
    if arguments.out is None:
        sys.stdout.write(text)
    else:
        # Excel opens a UTF-8 CSV with its byte-order mark as Korean text.
        arguments.out.write_text(text, encoding="utf-8-sig")
        print(f"{arguments.kind}: {len(rows)}줄 -> {arguments.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
