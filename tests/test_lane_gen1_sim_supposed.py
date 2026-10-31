"""GEN1: the SIM's per-field supposed questions and their misrecord count."""

from __future__ import annotations

from argparse import Namespace

from scripts.sim.corpus import SUPPOSED_QUESTIONS, generate_cases, generate_supposed_cases, load_corpus
from scripts.sim.metrics import score_case
from scripts.sim.split import partition_cases, select_cases

COMMON = dict(split_seed=1027, holdout_fraction=.3, emergency_matrix=False, protocol=None, family=None, limit=None)


def test_every_protocol_has_its_fields_supposed_questions_on_both_sides() -> None:
    protocols = load_corpus()
    assert {p.field for p in protocols} <= set(SUPPOSED_QUESTIONS)
    for protocol in protocols:
        development = generate_supposed_cases(protocol, 'development')
        holdout = generate_supposed_cases(protocol, 'holdout')
        assert development and holdout
        assert {c.text for c in development}.isdisjoint(c.text for c in holdout)
        assert all(c.family == 'supposed_question' and c.allowed == () for c in development + holdout)


def test_the_original_partition_is_unchanged_and_the_sides_stay_apart() -> None:
    protocols = load_corpus()
    base = partition_cases(tuple((p, c) for p in protocols for c in generate_cases(p)))
    development, dev_meta = select_cases(protocols, Namespace(split='development', **COMMON))
    holdout, hold_meta = select_cases(protocols, Namespace(split='holdout', **COMMON))
    assert {c.code for _, c in base.holdout} <= {c.code for _, c in holdout}
    supposed_dev = {c.code for _, c in development if c.family == 'supposed_question'}
    supposed_hold = {c.code for _, c in holdout if c.family == 'supposed_question'}
    assert supposed_dev and supposed_hold and supposed_dev.isdisjoint(supposed_hold)
    assert all('.supposed_development.' in code for code in supposed_dev)
    assert all('.supposed_holdout.' in code for code in supposed_hold)
    assert hold_meta['manifest_sha256'] == base.manifest == dev_meta['manifest_sha256']


def test_a_supposition_recorded_as_an_anomaly_is_counted() -> None:
    case = generate_supposed_cases(load_corpus()[0], 'development')[0]
    result = {
        'action': 'report_anomaly', 'intent_kind': 'problem_report', 'route': 'rules',
        'changed_fields': ['anomaly'], 'terminal': True, 'drop_reasons': [], 'spoken': '',
        'transcript': case.text, 'stt_calls': 1, 'step_before': case.index, 'step_after': case.index,
        'timings_ms': {},
    }
    row = score_case(case, result)
    assert row['supposed_misrecorded'] and row['wrong_state'] and not row['understood']
