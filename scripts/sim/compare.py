#!/usr/bin/env python3
"""Compare identical workloads; new/missing cases and incomparable modes fail closed."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path


def numeric_metrics(value: object, prefix: str = '') -> dict[str, float | int | None]:
    result = {}
    if isinstance(value, dict):
        for key, item in value.items():
            result.update(numeric_metrics(item, f'{prefix}.{key}' if prefix else key))
    elif value is None:
        result[prefix] = None
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError('non-finite metric')
        result[prefix] = value
    return result


def compare(before: dict, after: dict) -> dict:
    for key in ('schema','mode','corpus_sha256','workload_sha256','router','provider','model','split','rows_suppressed'):
        if before.get(key) != after.get(key):
            raise ValueError(f'incomparable {key}')
    def index(report: dict) -> dict:
        result = {}
        for r in report['rows']:
            key = tuple(str(r.get(k)) for k in ('case','protocol','noise','snr','voice','speed'))
            if key in result:
                raise ValueError('duplicate case-condition')
            result[key] = r
        return result
    a,b=index(before),index(after)
    if a.keys()!=b.keys():
        raise ValueError('workload differs (missing/added cases)')
    regressions=[]
    improvements=[]
    for key in sorted(a):
        bad=[name for name in ('wrong_state','english_copy','duplicate_label','incomplete','interrupted','cut_off') if not a[key].get(name,False) and b[key].get(name,False)]
        if a[key]['understood'] and not b[key]['understood']:
            bad.append('understood')
        if bad:
            regressions.append({'case':key[0],'condition':list(key[2:]),'metrics':bad})
        if not a[key]['understood'] and b[key]['understood']:
            improvements.append(key[0])
    delta={k:after['overall'][k]-before['overall'][k] for k in ('understood','wrong_state','english_copy','duplicate_label','incomplete','interrupted')}
    left, right = numeric_metrics(before['overall']), numeric_metrics(after['overall'])
    metrics = {}
    for key in sorted(left.keys() | right.keys()):
        old, new = left.get(key), right.get(key)
        metrics[key] = {'before':old, 'after':new,
                        'delta':round(new-old,8) if old is not None and new is not None else None}
    suppressed = bool(before.get('rows_suppressed'))
    return {'comparable':True,'cases':before['overall']['cases'] if suppressed else len(a),
            'individual_regressions_measured':not suppressed,'delta':delta,'metrics':metrics,
            'regressions':regressions,'improvements':improvements}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before',type=Path)
    parser.add_argument('after',type=Path)
    args=parser.parse_args(argv)
    try:
        result=compare(json.loads(args.before.read_text()),json.loads(args.after.read_text()))
    except (ValueError,KeyError,OSError):
        print(json.dumps({'comparable':False}))
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result['regressions'] else 0

if __name__=='__main__':
    raise SystemExit(main())
