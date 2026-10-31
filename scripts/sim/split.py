"""Deterministic, outcome-blind split; identical utterances never cross it."""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from scripts.sim.corpus import Case, Protocol

Pair = tuple[Protocol, Case]
SPLIT_SEED = 1027


@dataclass(frozen=True)
class Partition:
    development: tuple[Pair, ...]
    holdout: tuple[Pair, ...]
    manifest: str


@dataclass(frozen=True)
class CaseIdManifest:
    name: str
    case_ids: tuple[str, ...]
    sha256: str


def load_case_id_manifest(path: Path) -> CaseIdManifest:
    """Read one bounded, versioned list of public SIM case identities."""

    raw = path.read_bytes()
    if not raw or len(raw) > 64 * 1024:
        raise ValueError("case-id manifest must be between 1 byte and 64 KiB")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("case-id manifest must be UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "name", "case_ids"}:
        raise ValueError("invalid case-id manifest schema")
    name = value["name"]
    case_ids = value["case_ids"]
    if value["schema"] != 1:
        raise ValueError("unsupported case-id manifest schema")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 80
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in name)
    ):
        raise ValueError("invalid case-id manifest name")
    if (
        not isinstance(case_ids, list)
        or not case_ids
        or len(case_ids) > 1000
        or any(
            not isinstance(case_id, str)
            or not case_id
            or len(case_id) > 200
            for case_id in case_ids
        )
        or len(set(case_ids)) != len(case_ids)
    ):
        raise ValueError("case-id manifest needs unique bounded case ids")
    return CaseIdManifest(
        name=name,
        case_ids=tuple(case_ids),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


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
    from scripts.sim.corpus import (
        generate_cases, generate_development_cases, generate_happened_cases, generate_supposed_cases,
    )

    if getattr(args, 'emergency_matrix', False):
        from scripts.sim.emergency_cases import generate_emergency_cases
        generate_cases = generate_emergency_cases
    pairs = tuple((p, c) for p in protocols for c in generate_cases(p))
    development_regressions = (
        ()
        if getattr(args, 'emergency_matrix', False)
        else tuple(
            (protocol, case)
            for protocol in protocols
            for case in generate_development_cases(protocol)
        )
    )
    # Lane GEN1: each field's supposed problems, fixed on their own side so
    # the original partition is the same as before they were added.
    supposed = {
        side: (
            ()
            if getattr(args, 'emergency_matrix', False)
            else tuple(
                (protocol, case)
                for protocol in protocols
                for case in generate_supposed_cases(protocol, side)
            )
        )
        for side in ('development', 'holdout')
    }
    # Lane SAFE2: each field's problems said and asked about, fixed per side
    # like the supposed questions; the original partition is as it was.
    happened = {
        side: (
            ()
            if getattr(args, 'emergency_matrix', False)
            else tuple(
                (protocol, case)
                for protocol in protocols
                for case in generate_happened_cases(protocol, side)
            )
        )
        for side in ('development', 'holdout')
    }
    partition = partition_cases(pairs, seed=args.split_seed, holdout_fraction=args.holdout_fraction)
    manifest_path = getattr(args, "case_ids", None)
    case_manifest = (
        load_case_id_manifest(manifest_path) if manifest_path is not None else None
    )
    if case_manifest is not None:
        if (
            args.split != "all"
            or getattr(args, "emergency_matrix", False)
            or args.protocol
            or args.family
            or args.limit is not None
        ):
            raise ValueError(
                "--case-ids cannot be combined with split, filters, or --limit"
            )
        indexed = {
            case.code: (protocol, case)
            for protocol, case in pairs + development_regressions
            + supposed['development'] + supposed['holdout']
            + happened['development'] + happened['holdout']
        }
        missing = [
            case_id for case_id in case_manifest.case_ids if case_id not in indexed
        ]
        if missing:
            raise ValueError("case-id manifest contains unknown case ids")
        selected = tuple(indexed[case_id] for case_id in case_manifest.case_ids)
    elif args.split == 'all':
        selected = (pairs + development_regressions + supposed['development'] + supposed['holdout']
                    + happened['development'] + happened['holdout'])
    elif args.split == 'development':
        selected = partition.development + development_regressions + supposed['development'] + happened['development']
    else:
        selected = partition.holdout + supposed['holdout'] + happened['holdout']
    result = [(p, c) for p, c in selected
              if (not args.protocol or p.code in args.protocol) and (not args.family or c.family in args.family)]
    if args.limit:
        result = result[:args.limit]
    metadata = {
        'name': args.split,
        'seed': args.split_seed,
        'holdout_fraction': args.holdout_fraction,
        'development_cases': len(partition.development),
        'holdout_cases': len(partition.holdout),
        'manifest_sha256': partition.manifest,
        'text_grouped': True,
    }
    if args.split != 'holdout':
        metadata['development_cases'] += (
            len(development_regressions) + len(supposed['development']) + len(happened['development']))
        metadata['development_regression_cases'] = len(development_regressions)
    if args.split != 'development':
        metadata['holdout_cases'] += len(supposed['holdout']) + len(happened['holdout'])
    metadata['supposed_cases'] = {side: len(supposed[side]) for side in supposed}
    metadata['happened_cases'] = {side: len(happened[side]) for side in happened}
    if case_manifest is not None:
        metadata["name"] = "case_ids"
        metadata["case_id_manifest"] = {
            "name": case_manifest.name,
            "sha256": case_manifest.sha256,
            "cases": len(case_manifest.case_ids),
        }
    return result, metadata
