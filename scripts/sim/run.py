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
from scripts.sim.audio import (
    IMPACT_DISTANCES_CM, IMPACT_NOISES, IMPACT_PATTERNS, append_continuous_impact,
    append_impact, continuous_impact_pcm, impact_pcm, oracle_audio, overlay_impact,
)
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
    lines = ['|구분|말 수|이해|정확도|잘못 바뀐 상태|영어 원문|단계 이름 겹침|응급 놓침|헛경보|가정 질문 이상 오기록|일어난 일+질문 미기록|', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for dim, groups in report['tables'].items():
        for key,s in groups.items():
            lines.append(f'|{dim}: {key}|{s["cases"]}|{s["understood"]}|{s["accuracy"]:.1%}|{s["wrong_state"]}|{s["english_copy"]}|{s["duplicate_label"]}|{s.get("emergency_miss", "미측정")}|{s.get("false_alarm", "미측정")}|{s.get("supposed_misrecorded", "미측정")}|{s.get("happened_unrecorded", "미측정")}|')
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
    if getattr(args, 'development_fixture', False):
        # Lane GEN1: the in-gel development fixture beside the public ten,
        # the same generic cases, outside their partition (never holdout),
        # to show its quality is the others' and not more.
        if args.split == 'holdout':
            raise ValueError('the development fixture is never holdout')
        from scripts.sim.corpus import development_fixture_protocol, generate_development_cases, generate_supposed_cases
        extra = development_fixture_protocol()
        added = [(extra, case) for case in (
            *generate_cases(extra), *generate_development_cases(extra),
            *generate_supposed_cases(extra, 'development'))
            if (not args.family or case.family in args.family)]
        cases = [*cases, *added]
        split = {**split, 'development_fixture_cases': len(added)}
    if getattr(args, 'stt_error_matrix', False):
        # Lane SAFE2: the garbles the live rows showed, as rules on each
        # case's own words, in place of the cases themselves. The spoken
        # words stay beside each variant for the runner's value checks.
        from scripts.sim.stt_errors import case_texts, variants
        spoken_words = case_texts(c for _p, c in cases)
        garbled = [(p, v, c) for p, c in cases for v in variants(c, other_texts=spoken_words)]
        if not garbled:
            raise ValueError('no case takes a transcription error')
        cases = [(p, v) for p, v, _spoken in garbled]
        split = {**split, 'stt_error_variants': len(cases),
                 'stt_error_originals': len({c.code for _p, _v, c in garbled})}
        args._spoken_by_code = {v.code: c.text for _p, v, c in garbled}
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


def router_cost_estimate(
    router: dict | None, *, input_rate: float, output_rate: float,
) -> dict:
    """Rough cost from returned usage; timed-out/missing usage stays explicit."""
    router = router or {}
    usage = router.get('usage') if isinstance(router.get('usage'), dict) else {}
    prompt = usage.get('prompt_tokens', 0)
    completion = usage.get('completion_tokens', 0)
    prompt = prompt if isinstance(prompt, int) and not isinstance(prompt, bool) else 0
    completion = completion if isinstance(completion, int) and not isinstance(completion, bool) else 0
    completed = int(router.get('model_completed_with_usage', 0) or 0)
    calls = int(router.get('model_calls', 0) or 0)
    return {
        'reported_usage_calls': completed,
        'usage_missing_calls': max(0, calls - completed),
        'input_usd_per_million_assumed': input_rate,
        'output_usd_per_million_assumed': output_rate,
        'reported_usage_usd_estimate': round(
            (prompt * input_rate + completion * output_rate) / 1e6, 4),
        'rates_verified': False,
    }


async def evaluate(args: argparse.Namespace) -> dict:
    cases, split, catalog_counts, translation_coverage = prepare_cases(args)
    conditions = (
        tuple((kind, distance) for kind in IMPACT_NOISES for distance in IMPACT_DISTANCES_CM)
        if args.impact_matrix else ((None, None),)
    )
    pattern = args.impact_pattern if args.impact_matrix else 'single'
    spoken_by_code = getattr(args, '_spoken_by_code', {})
    workload = [
        # The LISTEN3 single-burst workload keeps its identity; LIVE3's
        # patterns add theirs so results of different patterns never compare.
        (p.fixture.fixture_sha256, asdict(c), kind, distance)
        if pattern == 'single' else
        (p.fixture.fixture_sha256, asdict(c), kind, distance, pattern)
        for p, c in cases for kind, distance in conditions
    ]
    oracle_pcm = oracle_audio()
    oracle_frames = len(oracle_pcm) // 640
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
                for impact_kind, distance_cm in conditions:
                    pcm = None
                    if impact_kind is not None and distance_cm is not None:
                        if c.family == 'noise':
                            pcm = (
                                continuous_impact_pcm(impact_kind, distance_cm)
                                if pattern == 'continuous'
                                else impact_pcm(impact_kind, distance_cm, frames=20)
                            )
                        elif pattern == 'continuous':
                            pcm = append_continuous_impact(oracle_pcm, impact_kind, distance_cm)
                        elif pattern == 'mid_speech':
                            pcm = overlay_impact(oracle_pcm, impact_kind, distance_cm)
                        else:
                            pcm = append_impact(oracle_pcm, impact_kind, distance_cm)
                    result = await run_case(
                        p, c, router_settings=router,
                        offline=not args.configured_router,
                        role_client=role_client if args.configured_router else None,
                        classifier=lambda frame: any(frame), oracle_environment=True,
                        pcm=pcm,
                        transient_noise_gate=(
                            args.impact_matrix and args.impact_filter == 'current'),
                        spoken_text=spoken_by_code.get(c.code),
                    )
                    row = score_case(
                        c, result,
                        source=' '.join(page.text for page in p.fixture.draft.extraction.pages),
                    )
                    if c.code in spoken_by_code:
                        from scripts.sim.stt_errors import error_kind, original_code
                        # Lane SAFE2: which garble, of which spoken case. A
                        # confirmation is a right answer; a state the spoken
                        # words did not allow is the error this axis counts.
                        row.update(stt_error=error_kind(c.code), original_case=original_code(c.code))
                    row.update(
                        protocol=p.code,
                        field=p.field,
                        noise=impact_kind or 'not_measured',
                        distance_cm=(distance_cm if distance_cm is not None
                                     else 'not_measured'),
                        snr='not_measured', voice='oracle_text', speed='not_measured',
                    )
                    if args.impact_matrix:
                        wearer_case = c.family != 'noise'
                        wearer_end = oracle_frames if wearer_case else None
                        if pattern == 'mid_speech' and wearer_case:
                            wearer_end = result['source_frames']
                        end_frames = result['endpoint_stream_frames']
                        row.update(
                            impact_pattern=pattern,
                            impact_position=(
                                ('mid_speech' if pattern == 'mid_speech' else 'after_speech')
                                if wearer_case else 'noise_only'),
                            wearer_dropped=wearer_case and result['stt_calls'] == 0,
                            noise_sent_to_stt=(not wearer_case
                                               and result['stt_calls'] > 0),
                            impact_reopened=wearer_case and any(
                                frames > (result['source_frames'] if pattern == 'mid_speech' else oracle_frames)
                                for frames in result['endpoint_audio_frames']
                            ),
                            continuation_joins=result['continuation_joins'],
                            # LIVE3: how long after the wearer's last frame
                            # the (last) end was found; the configured
                            # endpoint silence is 1000 ms.
                            end_delay_ms=(
                                (max(end_frames) + 1 - wearer_end) * 20
                                if wearer_case and end_frames and wearer_end is not None else None),
                        )
                    rows.append(row)
                    if len(rows) % 25 == 0:
                        print(json.dumps(
                            {'sim_completed':len(rows),'sim_total':len(workload)},
                        ), file=sys.stderr, flush=True)
                if budget is not None and budget.stopped:
                    break
        finally:
            if client is not None and callable(getattr(client, 'close', None)):
                closing = client.close()
                if inspect.isawaitable(closing):
                    await closing
    aggregate_report = aggregate(rows)
    return {
        'schema':1, 'mode':'oracle_model' if args.configured_router else 'offline', 'stt_measured':False, 'acoustics_measured':False,
        'latency_measured':'in_process_fake_providers_no_playback', 'router':'configured' if args.configured_router else 'off',
        'router_first':bool(router and router.router_first),
        'router_settings':({'provider':router.provider, 'model':router.model,
                            'reasoning_level':router.reasoning, 'router_first':router.router_first,
                            'max_output_tokens':router.max_output_tokens,
                            'timeout_seconds':router.timeout_seconds} if router else None),
        'utc':datetime.now(timezone.utc).isoformat(),
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'corpus_sha256':hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
        'workload_sha256':hashlib.sha256(json.dumps(workload,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        'catalog':catalog_counts, 'provider_requests':budget.calls if budget else 0,
        'reserved_usd':round(budget.usd, 4) if budget else 0, 'split':split,
        'budget_stopped':bool(budget and budget.stopped), 'planned_cases':len(workload),
        'impact_matrix':args.impact_matrix,
        'impact_filter':args.impact_filter if args.impact_matrix else None,
        'impact_pattern':pattern if args.impact_matrix else None,
        'stt_error_matrix':bool(getattr(args, 'stt_error_matrix', False)),
        'translations_sha256':hashlib.sha256(args.translations.read_bytes()).hexdigest() if args.translations else None,
        'translation_coverage':translation_coverage,
        'router_cost_estimate':router_cost_estimate(
            aggregate_report['overall'].get('router'),
            input_rate=args.router_input_rate, output_rate=args.router_output_rate),
        **aggregate_report, 'rows':[] if args.split == 'holdout' else rows,
        'rows_suppressed':args.split == 'holdout',
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=CORPUS)
    parser.add_argument('--catalog-copy', type=Path)
    parser.add_argument('--development-fixture', action='store_true',
                        help='add the in-gel development fixture (licensed PDF, condition A) beside the public ten')
    parser.add_argument('--protocol', action='append')
    parser.add_argument('--family', action='append')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', type=Path, default=Path.home()/'reports/sim/offline.json')
    parser.add_argument('--fail-on-wrong-state', action='store_true')
    parser.add_argument('--fail-on-emergency-miss', action='store_true')
    parser.add_argument('--translations', type=Path, help='source-identified stored Korean readings; no model call')
    parser.add_argument('--impact-matrix', action='store_true',
                        help='procedural typing/glass/pipette impacts at 30 cm and 1 m')
    parser.add_argument('--impact-filter', choices=('legacy', 'current'), default='current',
                        help='endpoint treatment for --impact-matrix before/after comparisons')
    parser.add_argument('--impact-pattern', choices=IMPACT_PATTERNS, default='single',
                        help='where the impacts sit: one burst after the words (LISTEN3), '
                             'bursts that keep going after the words, or over the words (LIVE3)')
    parser.add_argument('--stt-error-matrix', action='store_true',
                        help='the transcription errors the live rows showed, as rules on each case\'s words (lane SAFE2)')
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
        if args.impact_matrix and args.configured_router:
            raise ValueError('--impact-matrix is offline-only')
        if args.stt_error_matrix and (args.impact_matrix or args.configured_router or args.emergency_matrix):
            raise ValueError('--stt-error-matrix is an offline axis of its own')
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


def main(argv: list[str] | None = None) -> int:
    # Product modules log content-free turn telemetry; the SIM report and its
    # explicit progress records are the CLI's output contract. Restore the
    # process-wide logging threshold because pytest calls this entry point in
    # process before tests that assert production logs.
    import logging
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return _main(argv)
    finally:
        logging.disable(previous_disable)


if __name__ == '__main__':
    raise SystemExit(main())
