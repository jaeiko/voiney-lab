#!/usr/bin/env python3
"""Run source-derived cases through the real server; offline by default."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import asdict
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'src'))

from scripts.sim.corpus import load_corpus, generate_cases, CORPUS
from scripts.sim.runner import run_case
from scripts.sim.metrics import score_case, aggregate
from scripts.sim.split import add_split_arguments, select_cases


def output_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    reports = Path.home()/'reports/sim'
    if not path.is_relative_to(reports.resolve()):
        raise ValueError('output/cache must be under ~/reports/sim/')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')
    overall = report['overall']
    lines = ['|구분|말 수|이해|정확도|잘못 바뀐 상태|영어 원문|단계 이름 겹침|', '|---|---:|---:|---:|---:|---:|---:|']
    for dim, groups in report['tables'].items():
        for key,s in groups.items():
            lines.append(f'|{dim}: {key}|{s["cases"]}|{s["understood"]}|{s["accuracy"]:.1%}|{s["wrong_state"]}|{s["english_copy"]}|{s["duplicate_label"]}|')
    path.with_suffix('.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'result':str(path), **overall}, ensure_ascii=False))


async def evaluate(args: argparse.Namespace) -> dict:
    protocols = load_corpus(args.corpus)
    catalog_counts = {}
    if args.catalog_copy:
        from scripts.sim.catalog import load_catalog
        extra, catalog_counts = load_catalog(args.catalog_copy)
        protocols += extra
    cases, split = select_cases(protocols, args)
    if not cases:
        raise ValueError('no matching cases')
    rows = []
    for p,c in cases:
        result = await run_case(p,c)
        row = score_case(c,result,source=' '.join(page.text for page in p.fixture.draft.extraction.pages))
        row.update(protocol=p.code, noise='not_measured', snr='not_measured',voice='oracle_text',speed='not_measured')
        rows.append(row)
        if len(rows) % 25 == 0:
            print(json.dumps({'sim_completed':len(rows),'sim_total':len(cases)}),file=sys.stderr,flush=True)
    return {
        'schema':1, 'mode':'offline', 'stt_measured':False, 'acoustics_measured':False,
        'latency_measured':'in_process_fake_providers_no_playback', 'router':'off',
        'utc':datetime.now(timezone.utc).isoformat(),
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'corpus_sha256':hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
        'workload_sha256':hashlib.sha256(json.dumps([(p.fixture.fixture_sha256,asdict(c)) for p,c in cases],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        'catalog':catalog_counts, 'provider_requests':0, 'split':split,
        **aggregate(rows), 'rows':[] if args.split == 'holdout' else rows,
        'rows_suppressed':args.split == 'holdout',
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=CORPUS)
    parser.add_argument('--catalog-copy', type=Path)
    parser.add_argument('--protocol', action='append')
    parser.add_argument('--family', action='append')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', type=Path, default=Path.home()/'reports/sim/offline.json')
    parser.add_argument('--fail-on-wrong-state', action='store_true')
    add_split_arguments(parser)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    try:
        output_dir(args.output.parent)
        report = asyncio.run(evaluate(args))
        save_report(args.output.expanduser(),report)
    except (ValueError,OSError) as exc:
        print(f'sim: {type(exc).__name__}',file=sys.stderr)
        return 2
    return 1 if args.fail_on_wrong_state and report['overall']['wrong_state'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
