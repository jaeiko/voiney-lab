"""Current incidents bypass workflow planning; only development phrases are inspected."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from scripts.sim.corpus import load_corpus
from scripts.sim.emergency_cases import generate_emergency_cases
from scripts.sim.metrics import score_case
from scripts.sim.runner import run_case
from scripts.sim.split import partition_cases
from voiney_lab.emergency import recognize_emergency


def development():
    return partition_cases(tuple((p, c) for p in load_corpus() for c in generate_emergency_cases(p))).development


def test_development_current_incidents_and_negative_controls() -> None:
    # No holdout text or individual holdout outcome is loaded into assertions.
    seen = set()
    for _, case in development():
        if case.text in seen:
            continue
        seen.add(case.text)
        assert (recognize_emergency(case.text) is not None) == case.family.startswith('emergency_'), case.code


@pytest.mark.parametrize('field', ['biology', 'chemistry', 'materials'])
def test_incidents_precede_workflow_in_active_and_paused_turns(field: str) -> None:
    pairs = development()
    p = next(p for p, _ in pairs if p.field == field)
    families = sorted({c.family for owner, c in pairs if owner.code == p.code and c.family.startswith('emergency_')})
    assert len(families) == 10
    for family in families:
        case = next(c for owner, c in pairs if owner.code == p.code and c.family == family)
        for setup in ('active', 'paused'):
            result = asyncio.run(run_case(p, replace(case, setup=setup)))
            assert result['route'] == 'deterministic_emergency', case.code
            assert result['changed_fields'] == [], case.code
            assert score_case(case, result)['understood'], case.code
