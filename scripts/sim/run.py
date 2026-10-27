#!/usr/bin/env python3
"""Run source-derived cases through the real server; offline by default."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
from contextlib import ExitStack
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
    lines = ['|구분|말 수|이해|정확도|잘못 바뀐 상태|영어 원문|단계 이름 겹침|응급 놓침|헛경보|', '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for dim, groups in report['tables'].items():
        for key,s in groups.items():
            lines.append(f'|{dim}: {key}|{s["cases"]}|{s["understood"]}|{s["accuracy"]:.1%}|{s["wrong_state"]}|{s["english_copy"]}|{s["duplicate_label"]}|{s.get("emergency_miss", "미측정")}|{s.get("false_alarm", "미측정")}|')
    path.with_suffix('.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'result':str(path), **overall}, ensure_ascii=False))


def prepare_cases(args: argparse.Namespace) -> tuple[list, dict, dict, dict]:
    protocols = load_corpus(args.corpus)
    catalog_counts = {}
    if args.catalog_copy:
        from scripts.sim.catalog import load_catalog
        extra, catalog_counts = load_catalog(args.catalog_copy)
        protocols += extra
    translation_coverage = {}
    if args.translations:
        from scripts.sim.translations import attach_translations
        protocols, translation_coverage = attach_translations(protocols, args.translations)
    cases, split = select_cases(protocols, args)
    if not cases:
        raise ValueError('no matching cases')
    return cases, split, catalog_counts, translation_coverage


def router_plan(args: argparse.Namespace, cases: list) -> dict:
    # Planning assumptions only, not a provider quote or token measurement.
    return {'router_turns_at_most': len(cases), 'external_requests_this_dry_run': 0,
            'input_tokens_assumed_per_turn': 4000, 'output_tokens_at_most_per_turn': 400,
            'input_usd_per_million_assumed': args.router_input_rate,
            'output_usd_per_million_assumed': args.router_output_rate,
            'model_usd_assumed': round(len(cases) * (4000 * args.router_input_rate + 400 * args.router_output_rate) / 1e6, 4),
            'call_cap': args.max_calls, 'reserved_usd_cap': args.max_usd,
            'rates_verified': False, 'reserve_per_request': args.reserve_usd_per_request}


async def evaluate(args: argparse.Namespace) -> dict:
    cases, split, catalog_counts, translation_coverage = prepare_cases(args)
    rows = []
    router = None
    client = None
    budget = None
    with ExitStack() as stack:
        if args.configured_router:
            from scripts.sim.live import Budget
            from scripts.sim.runner import import_server
            from voiney_lab.llm_router import LlmRouterSettings
            router = LlmRouterSettings.from_environment()
            if not router.enabled:
                raise ValueError(router.unavailable_reason or 'VOINEY_LAB_LLM_ROUTER_ENABLED must be true')
            budget = Budget(args.max_calls, args.max_usd, args.reserve_usd_per_request,
                            args.output.with_suffix('.ledger.jsonl'))
            budget.guard(stack)
            client = import_server()._role_client(router.role_model)
        def role_client(role: object) -> object:
            if getattr(role, 'role', None) != 'router':
                raise RuntimeError('sim_role_unavailable')
            return client
        try:
            for p,c in cases:
                result = await run_case(p,c, router_settings=router, offline=not args.configured_router,
                                        role_client=role_client if args.configured_router else None,
                                        classifier=lambda frame: any(frame))
                row = score_case(c,result,source=' '.join(page.text for page in p.fixture.draft.extraction.pages))
                row.update(protocol=p.code, noise='not_measured', snr='not_measured',voice='oracle_text',speed='not_measured')
                rows.append(row)
                if len(rows) % 25 == 0:
                    print(json.dumps({'sim_completed':len(rows),'sim_total':len(cases)}),file=sys.stderr,flush=True)
                if budget is not None and budget.stopped:
                    break
        finally:
            if client is not None and callable(getattr(client, 'close', None)):
                closing = client.close()
                if inspect.isawaitable(closing):
                    await closing
    return {
        'schema':1, 'mode':'oracle_model' if args.configured_router else 'offline', 'stt_measured':False, 'acoustics_measured':False,
        'latency_measured':'in_process_fake_providers_no_playback', 'router':'configured' if args.configured_router else 'off',
        'router_first':bool(router and router.router_first),
        'utc':datetime.now(timezone.utc).isoformat(),
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'corpus_sha256':hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
        'workload_sha256':hashlib.sha256(json.dumps([(p.fixture.fixture_sha256,asdict(c)) for p,c in cases],sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        'catalog':catalog_counts, 'provider_requests':budget.calls if budget else 0, 'split':split,
        'budget_stopped':bool(budget and budget.stopped), 'planned_cases':len(cases),
        'translations_sha256':hashlib.sha256(args.translations.read_bytes()).hexdigest() if args.translations else None,
        'translation_coverage':translation_coverage,
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
    parser.add_argument('--fail-on-emergency-miss', action='store_true')
    parser.add_argument('--translations', type=Path, help='source-identified stored Korean readings; no model call')
    parser.add_argument('--configured-router', action='store_true', help='plan an oracle-STT model comparison using shell router settings')
    parser.add_argument('--execute', action='store_true', help='execute the configured-router plan with real model requests')
    parser.add_argument('--max-calls', type=int, default=20)
    parser.add_argument('--max-usd', type=float, default=5)
    parser.add_argument('--reserve-usd-per-request', type=float, default=.25)
    parser.add_argument('--router-input-rate', type=float, default=5, help='assumed USD per million input tokens')
    parser.add_argument('--router-output-rate', type=float, default=15, help='assumed USD per million output tokens')
    add_split_arguments(parser)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    try:
        import math
        if args.execute and not args.configured_router:
            raise ValueError('--execute requires --configured-router')
        if args.max_calls < 1 or any(not math.isfinite(x) or x <= 0 for x in (
            args.max_usd, args.reserve_usd_per_request, args.router_input_rate, args.router_output_rate)):
            raise ValueError('positive finite caps/rates required')
        if args.configured_router and not args.execute:
            cases, _, _, _ = prepare_cases(args)
            print(json.dumps(router_plan(args, cases), ensure_ascii=False))
            return 0
        output_dir(args.output.parent)
        if args.output.exists() or args.output.with_suffix('.md').exists():
            raise ValueError('output already exists')
        with ExitStack() as stack:
            if args.configured_router:
                import fcntl
                lock = stack.enter_context(args.output.with_suffix('.lock').open('w'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = asyncio.run(evaluate(args))
        save_report(args.output.expanduser(),report)
    except Exception as exc:
        from scripts.sim.live import failure_settings
        print(json.dumps({'error':type(exc).__name__, 'settings':failure_settings(exc)}),file=sys.stderr)
        return 2
    if report.get('budget_stopped'):
        return 3
    return 1 if ((args.fail_on_wrong_state and report['overall']['wrong_state'])
                 or (args.fail_on_emergency_miss and report['overall']['emergency_miss'])) else 0


if __name__ == '__main__':
    raise SystemExit(main())
