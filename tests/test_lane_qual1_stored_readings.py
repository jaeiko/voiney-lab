"""A stored reading does not turn an English section heading into spoken guidance."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scripts.sim.corpus import Case, load_corpus
from scripts.sim.metrics import english_copy, protected_scientific_names
from scripts.sim.runner import run_case
from scripts.sim.translations import attach_translations


@pytest.mark.parametrize('field', ['biology', 'chemistry', 'materials'])
def test_english_section_titles_stay_in_the_source_area(field: str) -> None:
    protocols, _ = attach_translations(load_corpus(), Path('data/fixtures/evaluation/sim/stored_korean.json'))
    p = next(p for p in protocols if p.field == field)
    title = p.fixture.draft.protocol.sections[0].title_source_text
    result = asyncio.run(run_case(p, Case('stored-reading', 'mixed', '이 단계 설명하고 다음으로 넘어가')))
    assert title not in result['spoken']
    assert result['changed_fields'] == ['confirmation']


def test_english_copy_keeps_source_identified_scientific_names() -> None:
    name = 'Alpha Beta Research Reagent'
    assert not english_copy(f'{name}를 사용해요.', f'Use {name}.', protected_names=(name,))
    assert english_copy('Use Alpha Beta Research Reagent.', f'Use {name}.', protected_names=(name,))
    assert protected_scientific_names([f'{name}를 사용해요.'], 'Use Alpha\nBeta Research Reagent.') == (name,)
