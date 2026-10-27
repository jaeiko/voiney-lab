#!/usr/bin/env python3
"""Build a private recording inventory, then align a human-checked sentence sequence.

Never use raw_transcript as ground truth. An empty/unfilled voice-test script
cannot produce references. Pass --sequence JSON written after listening:
[{"ordinal":1,"reference":"...","terms":[]}, ...].
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'src'))
from scripts.sim.run import output_dir


def inventory(directory: Path) -> list[dict]:
    records=[]
    for path in directory.glob('*.json'):
        wav=path.with_suffix('.wav')
        if not wav.is_file():
            continue
        data=json.loads(path.read_text())
        # Modification time plus numeric turn breaks ties; lexical turn-10
        # before turn-2 is never used for alignment.
        records.append((wav.stat().st_mtime_ns,int(data.get('turn_id',0)),{'identity':path.stem,'duration_ms':data.get('duration_ms'),'reference':'','terms':[]}))
    return [dict(row,ordinal=i) for i,(_,_,row) in enumerate(sorted(records,key=lambda r:(r[0],r[1])),1)]


def align(records: list[dict], sequence: list[dict]) -> list[dict]:
    if len(sequence)!=len(records) or not sequence:
        raise ValueError('sequence and recording counts must match; listen and mark every turn')
    by={row['ordinal']:row for row in sequence}
    if len(by)!=len(records) or set(by)!={r['ordinal'] for r in records}:
        raise ValueError('duplicate/missing ordinal')
    result=[]
    for record in records:
        row=by[record['ordinal']]
        if not isinstance(row.get('reference'),str) or not row['reference'].strip():
            raise ValueError('unreviewed empty reference')
        if not isinstance(row.get('terms',[]),list) or any(not isinstance(t,str) for t in row.get('terms',[])):
            raise ValueError('terms must be a list of strings')
        result.append({'identity':record['identity'],'reference':row['reference'],'terms':row.get('terms',[])})
    return result


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--recordings',type=Path,required=True)
    p.add_argument('--sequence',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        output_dir(a.output.parent)
        records=inventory(a.recordings)
        checked = align(records,json.loads(a.sequence.read_text())) if a.sequence else None
        with a.output.open('x',encoding='utf-8') as f:
            if a.sequence:
                for row in checked:
                    f.write(json.dumps(row,ensure_ascii=False)+'\n')
            else:
                json.dump(records,f,ensure_ascii=False,indent=2)
        print(json.dumps({'recordings':len(records),'references_ready':bool(a.sequence)}))
    except (ValueError,OSError,KeyError) as exc:
        print(f'references: {type(exc).__name__}',file=sys.stderr); return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
