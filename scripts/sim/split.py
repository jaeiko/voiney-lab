"""Deterministic, outcome-blind split; identical utterances never cross it."""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Sequence

from scripts.sim.corpus import Case, Protocol

Pair = tuple[Protocol, Case]
SPLIT_SEED = 1027


@dataclass(frozen=True)
class Partition:
    development: tuple[Pair, ...]
    holdout: tuple[Pair, ...]
    manifest: str


def partition_cases(pairs: Sequence[Pair], *, seed: int = SPLIT_SEED,
                    holdout_fraction: float = .3) -> Partition:
    """Meet protocol/family quotas, grouping repeated text before assigning it.

    Cells with >=2 cases also get a quota. Singleton cells are balanced at
    the protocol and family levels. A single-text family necessarily resides
    wholly on one side. No route result or expectation affects assignment.
    """
    if not math.isfinite(holdout_fraction) or not .3 <= holdout_fraction < 1:
        raise ValueError('holdout must be at least 30% and less than 100%')
    if not pairs or len({c.code for _, c in pairs}) != len(pairs):
        raise ValueError('nonempty unique case identities required')
    groups: dict[str, list[Pair]] = defaultdict(list)
    totals: Counter[tuple[str, ...]] = Counter()

    def strata(p: Protocol, c: Case) -> tuple[tuple[str, ...], ...]:
        return (('protocol', p.code), ('family', c.family), ('cell', p.code, c.family))

    for p, c in pairs:
        words = ' '.join(unicodedata.normalize('NFKC', c.text).casefold().split())
        groups[words].append((p, c))
        totals.update(strata(p, c))
    quotas = {key: math.ceil(count * holdout_fraction) for key, count in totals.items()
              if key[0] != 'cell' or count >= 2}
    covered: Counter[tuple[str, ...]] = Counter()
    contributions = {key: Counter(s for p, c in group for s in strata(p, c))
                     for key, group in groups.items()}
    held: set[str] = set()
    ranked = sorted(groups, key=lambda key: hashlib.sha256(f'{seed}:{key}'.encode()).digest())
    while any(covered[key] < quota for key, quota in quotas.items()):
        # Prefer coverage of still-unfilled strata; seeded order breaks ties.
        def benefit(words: str) -> float:
            return sum(min(count, max(0, quotas.get(key, 0) - covered[key])) / quotas[key]
                       for key, count in contributions[words].items() if key in quotas)
        chosen = max((key for key in ranked if key not in held), key=benefit)
        held.add(chosen)
        covered.update(contributions[chosen])
    development = tuple(sorted((pair for key, group in groups.items() if key not in held for pair in group),
                               key=lambda pair: pair[1].code))
    holdout = tuple(sorted((pair for key in held for pair in groups[key]), key=lambda pair: pair[1].code))
    manifest = hashlib.sha256(json.dumps(
        {'version': 1, 'seed': seed, 'fraction': holdout_fraction,
         'development': [c.code for _, c in development], 'holdout': [c.code for _, c in holdout]},
        sort_keys=True).encode()).hexdigest()
    return Partition(development, holdout, manifest)


def add_split_arguments(parser: object) -> None:
    parser.add_argument('--split', choices=('all', 'development', 'holdout'), default='all')
    parser.add_argument('--split-seed', type=int, default=SPLIT_SEED)
    parser.add_argument('--holdout-fraction', type=float, default=.3)
    parser.add_argument('--emergency-matrix', action='store_true', help='use synthetic incidents and negative controls')


def select_cases(protocols: Sequence[Protocol], args: object) -> tuple[list[Pair], dict]:
    from scripts.sim.corpus import generate_cases

    if getattr(args, 'emergency_matrix', False):
        from scripts.sim.emergency_cases import generate_emergency_cases
        generate_cases = generate_emergency_cases
    pairs = tuple((p, c) for p in protocols for c in generate_cases(p))
    partition = partition_cases(pairs, seed=args.split_seed, holdout_fraction=args.holdout_fraction)
    selected = pairs if args.split == 'all' else getattr(partition, args.split)
    result = [(p, c) for p, c in selected
              if (not args.protocol or p.code in args.protocol) and (not args.family or c.family in args.family)]
    if args.limit:
        result = result[:args.limit]
    return result, {'name': args.split, 'seed': args.split_seed, 'holdout_fraction': args.holdout_fraction,
                    'development_cases': len(partition.development), 'holdout_cases': len(partition.holdout),
                    'manifest_sha256': partition.manifest, 'text_grouped': True}
