"""General command forms found in the frozen development set."""
from __future__ import annotations

import asyncio

import pytest

from scripts.sim.corpus import generate_cases, load_corpus
from scripts.sim.metrics import score_case
from scripts.sim.runner import run_case
from scripts.sim.split import partition_cases

PHRASES = ('문제가 생기면 다음으로 가도 돼?', '시료가 이상하면 어떻게 하지?',
           '3단계로 이동', '다음 단계에서 뭘 해?', '한 단계 뒤로',
           '시료가 이상해졌어', '이 과정 목적 알려줘',
           '타이머 계속 돌려 줘', '타이머 맞춰 줘')


@pytest.mark.parametrize('text', PHRASES)
@pytest.mark.parametrize('field', ['biology', 'chemistry', 'materials'])
def test_development_phrasing_at_server_boundary(text: str, field: str) -> None:
    pairs = partition_cases(tuple((p, c) for p in load_corpus() for c in generate_cases(p))).development
    p, c = next((p, c) for p, c in pairs if p.field == field and c.text == text)
    result = asyncio.run(run_case(p, c))
    assert score_case(c, result)['understood'], (c.code, result['action'], result['intent_kind'])
