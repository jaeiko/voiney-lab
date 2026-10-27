#!/usr/bin/env python3
"""Bounded wrapper over scripts/stt_compare.py for human-reviewed recording references."""
from __future__ import annotations
import argparse
import json
import io
import sys
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'src'))
from scripts import stt_compare
from scripts.sim.live import Budget
from scripts.sim.run import output_dir


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    p.add_argument('references',type=Path)
    p.add_argument('--providers',default='google_cloud_v2,google_cloud')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--max-calls',type=int,default=50)
    p.add_argument('--max-usd',type=float,default=5)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        providers=tuple(a.providers.split(','))
        if any(n not in {'google_cloud_v2','google_cloud','elevenlabs'} for n in providers):
            raise ValueError('unknown provider')
        refs=stt_compare.load_references(a.references)
        if not refs or any(not r['reference'].strip() for r in refs.values()):
            raise ValueError('review references before comparison')
        if any(not (a.directory/f'{identity}.json').is_file() or not (a.directory/f'{identity}.wav').is_file() or '/' in identity or '\\' in identity for identity in refs):
            raise ValueError('missing or invalid recording identity')
        if not a.execute:
            print(json.dumps({'recognition_requests':len(refs)*len(providers),'oauth_requests':'additional',
                              'max_calls':a.max_calls,'max_reserved_usd':a.max_usd,'external_requests':0}))
            return 0
        if a.output.exists():
            raise ValueError('output already exists')
        import fcntl
        output_dir(a.output.parent)
        with a.output.with_suffix('.lock').open('w') as lock, ExitStack() as stack:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            budget=Budget(a.max_calls,a.max_usd,.10,a.output.with_suffix('.ledger.jsonl'))
            budget.guard(stack)
            with redirect_stderr(io.StringIO()):
                scores,calls=stt_compare.compare(a.directory,refs,providers=providers)
            result={name:score.summary() for name,score in scores.items()}
            result.update(recognition_attempts=calls,http_attempts=budget.calls,reserved_usd=round(budget.usd,4),budget_stopped=budget.stopped)
            with a.output.open('x') as f:
                json.dump(result,f,ensure_ascii=False,indent=2)
            print(json.dumps({'result_written':True,'http_attempts':budget.calls,'budget_stopped':budget.stopped}))
            return 3 if budget.stopped else 0
    except Exception as exc:
        print(f'diagnostics: {type(exc).__name__}',file=sys.stderr);return 2

if __name__=='__main__':
    raise SystemExit(main())
