#!/usr/bin/env python3
"""Optional human-run single model call producing REVIEW PROPOSALS, never expectations."""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from contextlib import ExitStack
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from scripts.sim.corpus import load_corpus,generate_cases
from scripts.sim.live import Budget
from scripts.sim.run import output_dir

async def expand(prompt: str) -> str:
    from voiney_lab.model_providers import RoleModel,chat_client
    role=RoleModel.from_environment('router')
    client=chat_client(role,timeout=30,max_retries=0)
    response=await client.chat.completions.create(model=role.model,messages=[{'role':'user','content':prompt}],max_tokens=1000)
    return response.choices[0].message.content


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',required=True)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--max-usd',type=float,default=1)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        case=next(c for pro in load_corpus() for c in generate_cases(pro) if c.code==a.case)
        prompt=('다음 시험용 문장을 뜻, 긍정/부정, 숫자, 단위, 명령 대상을 바꾸지 않고 한국어 구어체로 3개 바꿔 쓰세요. '
                '추가 실험 단계·값·안전 조언은 쓰지 마세요. JSON 문자열 배열만 답하세요. 문장: '+case.text)
        if not a.execute:
            print(json.dumps({'model_calls':1,'max_reserved_usd':a.max_usd,'external_requests':0,'case':case.code},ensure_ascii=False))
            return 0
        if a.output.exists():
            raise ValueError('output already exists')
        import fcntl
        output_dir(a.output.parent)
        with a.output.with_suffix('.lock').open('w') as lock, ExitStack() as stack:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            budget=Budget(1,a.max_usd,a.max_usd,a.output.with_suffix('.ledger.jsonl'))
            budget.guard(stack)
            candidates=json.loads(asyncio.run(expand(prompt)))
            if not isinstance(candidates,list) or len(candidates)!=3 or any(not isinstance(c,str) or not 1<=len(c)<=300 for c in candidates):
                raise ValueError('invalid expansion')
            with a.output.open('x') as f:
                json.dump({'case':case.code,'reviewed':False,'proposals':candidates},f,ensure_ascii=False,indent=2)
        print('unreviewed_proposals_written')
    except Exception as exc:
        print(f'expand: {type(exc).__name__}',file=sys.stderr);return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
