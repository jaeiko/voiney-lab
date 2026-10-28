"""A frozen, text-grouped holdout must not expose individual test cases."""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace

import pytest

from scripts.sim.corpus import generate_cases, load_corpus


def test_split_is_stable_balanced_and_keeps_repeated_words_together() -> None:
    from scripts.sim.split import partition_cases

    pairs = tuple((p, c) for p in load_corpus() for c in generate_cases(p))
    split = partition_cases(pairs)
    reversed_split = partition_cases(tuple(reversed(pairs)))
    assert {c.code for _, c in split.holdout} == {c.code for _, c in reversed_split.holdout}
    assert len(split.development) + len(split.holdout) == len(pairs)
    assert len(split.holdout) >= len(pairs) * .3
    assert {c.text for _, c in split.development}.isdisjoint(c.text for _, c in split.holdout)
    for dimension in (lambda p, c: p.code, lambda p, c: c.family):
        total = Counter(dimension(p, c) for p, c in pairs)
        held = Counter(dimension(p, c) for p, c in split.holdout)
        assert all(held[key] >= count * .3 for key, count in total.items())
    assert split.manifest == reversed_split.manifest
    assert split.development


def test_split_rejects_small_holdout_or_duplicate_identity() -> None:
    from scripts.sim.split import partition_cases

    p = load_corpus()[0]
    c = generate_cases(p)[0]
    with pytest.raises(ValueError):
        partition_cases(((p, c),), holdout_fraction=.29)
    with pytest.raises(ValueError):
        partition_cases(((p, c), (p, replace(c, text='duplicate identity'))))


def test_holdout_cli_only_publishes_aggregate_scores(tmp_path, monkeypatch) -> None:
    from scripts.sim import run

    monkeypatch.setattr(run, 'output_dir', lambda path: path.mkdir(parents=True, exist_ok=True))
    output = tmp_path / 'held.json'
    assert run.main(['--split', 'holdout', '--limit', '2', '--output', str(output)]) == 0
    import json
    report = json.loads(output.read_text())
    assert report['rows'] == []
    assert report['rows_suppressed'] is True
    assert report['overall']['cases'] == 2
    assert report['split']['name'] == 'holdout'
    assert report['split']['holdout_fraction'] >= .3
    assert 'protocol_family' in report['tables']


def test_holdout_comparison_does_not_mistake_suppressed_rows_for_no_work() -> None:
    from scripts.sim.compare import compare

    report = dict(schema=1, mode='offline', corpus_sha256='same', workload_sha256='work',
                  router='off', rows=[], rows_suppressed=True,
                  split={'name': 'holdout', 'manifest_sha256': 'frozen'},
                  overall=dict(cases=10, understood=7, wrong_state=0, english_copy=0,
                               duplicate_label=0, incomplete=0, interrupted=0))
    result = compare(report, report)
    assert result['cases'] == 10
    assert result['individual_regressions_measured'] is False
