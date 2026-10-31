#!/usr/bin/env python3
"""Pick a fixed diagnostic sample of development cases, even across fields, families and lengths (LIVE3).

The sample is a case-id manifest (``scripts/sim/split.py``) for
``scripts/sim/live.py --case-ids``: the same public case ids every run,
whatever order the corpus generates them in. Only development cases are
eligible; the holdout split is never read here. Selection is outcome-blind:
nothing about how a case is routed or scored is consulted.

    python scripts/sim/diagnostic_manifest.py --check   # the committed manifest matches
    python scripts/sim/diagnostic_manifest.py --write   # rewrite it (a deliberate change)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from scripts.sim.corpus import Case, Protocol, generate_cases, generate_development_cases, load_corpus
from scripts.sim.split import SPLIT_SEED, partition_cases

MANIFEST = ROOT / 'scripts/sim/manifests/live3_dev_diag_30.json'
MANIFEST_NAME = 'live3-dev-diag-30'
SELECTION_SEED = 1031
#: How many cases each field contributes: the seven biology protocols share
#: twelve; chemistry, materials and soil six each.
FIELD_QUOTAS = {'biology': 12, 'chemistry': 6, 'materials': 6, 'soil': 6}
#: Four kinds of utterance, so commands, questions, records and the rest are
#: all in the sample.
FAMILY_GROUPS = {
    'command': {
        'complete', 'previous', 'jump', 'timer_start', 'timer_pause', 'timer_resume',
        'timer_cancel', 'timer_restart', 'pause', 'resume', 'source', 'value_inside',
        'value_outside', 'value_change',
    },
    'question': {
        'current', 'detail', 'repeat', 'preview', 'timer_remaining', 'safety', 'purpose',
        'problem_question', 'step_question', 'term',
    },
    'record': {'observation', 'problem_report', 'safe1_observation', 'safe1_purpose', 'safe1_result'},
    'other': {'ambiguous', 'chat', 'mixed', 'completion_reask', 'repository_eval'},
}
#: Three lengths in characters of the spoken text.
LENGTH_BUCKETS = (('short', 0, 6), ('medium', 7, 12), ('long', 13, 10**6))


def family_group(family: str) -> str:
    for group, families in FAMILY_GROUPS.items():
        if family in families:
            return group
    return 'other'


def length_bucket(text: str) -> str:
    size = len(unicodedata.normalize('NFKC', text).strip())
    for name, low, high in LENGTH_BUCKETS:
        if low <= size <= high:
            return name
    return 'long'


def development_pairs() -> list[tuple[Protocol, Case]]:
    protocols = load_corpus()
    pairs = tuple((p, c) for p in protocols for c in generate_cases(p))
    partition = partition_cases(pairs, seed=SPLIT_SEED, holdout_fraction=.3)
    regressions = [(p, c) for p in protocols for c in generate_development_cases(p)]
    return list(partition.development) + regressions


def select(pairs: list[tuple[Protocol, Case]], *, seed: int = SELECTION_SEED) -> list[str]:
    """Round-robin over (field, family group, length) cells in a seeded order; one text once."""

    def rank(code: str) -> str:
        return hashlib.sha256(f'{seed}:{code}'.encode()).hexdigest()

    cells: dict[tuple[str, str, str], list[tuple[Protocol, Case]]] = defaultdict(list)
    for p, c in pairs:
        if c.family in {'noise', 'emergency'} or not c.text.strip():
            continue
        cells[(p.field, family_group(c.family), length_bucket(c.text))].append((p, c))
    for members in cells.values():
        members.sort(key=lambda pair: rank(pair[1].code))
    chosen: list[str] = []
    seen_text: set[str] = set()
    protocol_counts: Counter[str] = Counter()
    lengths = [name for name, _, _ in LENGTH_BUCKETS]
    for field, quota in FIELD_QUOTAS.items():
        if not any(key[0] == field for key in cells):
            raise ValueError(f'no development cases for field {field}')
        cursors: dict[tuple[str, str, str], int] = defaultdict(int)
        length_turn: dict[str, int] = defaultdict(int)
        taken = 0
        stalled = 0
        while taken < quota:
            # One from each kind of utterance in turn, the length rotating
            # inside the kind, so no kind or length is starved by a small quota.
            progressed = False
            for group in FAMILY_GROUPS:
                if taken >= quota:
                    break
                for _attempt in range(len(lengths)):
                    length = lengths[length_turn[group] % len(lengths)]
                    length_turn[group] += 1
                    key = (field, group, length)
                    members = cells.get(key, [])
                    picked = False
                    while cursors[key] < len(members):
                        p, c = members[cursors[key]]
                        cursors[key] += 1
                        text = ' '.join(unicodedata.normalize('NFKC', c.text).casefold().split())
                        # Biology has seven protocols: spread its twelve over them.
                        if text in seen_text or (field == 'biology' and protocol_counts[p.code] >= 2):
                            continue
                        seen_text.add(text)
                        protocol_counts[p.code] += 1
                        chosen.append(c.code)
                        taken += 1
                        picked = True
                        break
                    if picked:
                        progressed = True
                        break
            stalled = 0 if progressed else stalled + 1
            if stalled > 1:
                raise ValueError(f'field {field} has too few distinct development cases')
    return chosen


def manifest_document(case_ids: list[str]) -> str:
    return json.dumps({'schema': 1, 'name': MANIFEST_NAME, 'case_ids': case_ids}, ensure_ascii=False, indent=2) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--write', action='store_true')
    args = parser.parse_args(argv)
    document = manifest_document(select(development_pairs()))
    if args.write:
        MANIFEST.write_text(document, encoding='utf-8')
        print(json.dumps({'written': str(MANIFEST), 'cases': document.count('\n') - 4}))
        return 0
    if not MANIFEST.exists() or MANIFEST.read_text(encoding='utf-8') != document:
        print(json.dumps({'manifest_matches': False, 'path': str(MANIFEST)}))
        return 1
    print(json.dumps({'manifest_matches': True, 'path': str(MANIFEST)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
