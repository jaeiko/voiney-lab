"""Explanation plus an explicit move stages completion on the server line."""
from __future__ import annotations

import asyncio

import pytest

from scripts.sim.corpus import Case, load_corpus
from scripts.sim.runner import run_case, snapshot
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn


COMMANDS = ('이 단계 설명하고 다음으로 넘어가', '왜 하는지 알려주고 다음 단계로 가 줘',
            '이 과정 설명해 주고 다음 단계로 넘어가자', '주의사항 설명하고 다음으로 진행해 줘')


@pytest.mark.parametrize('field', ['biology', 'chemistry', 'materials'])
def test_combined_commands_stage_only_completion(field: str) -> None:
    p = next(p for p in load_corpus() if p.field == field)
    for n, text in enumerate(COMMANDS):
        result = asyncio.run(run_case(p, Case(f'combined.{n}', 'mixed', text)))
        assert result['intent_kind'] == 'next_step_confirmation_required'
        assert result['changed_fields'] == ['confirmation']
        assert result['step_after'] == 0


def test_only_a_later_confirmation_can_move_and_preview_stays_read_only() -> None:
    p = next(p for p in load_corpus() if p.field == 'chemistry')
    s = CuratedProtocolSession(p.fixture)
    s.activate_configured()
    s.active = True
    s._workflow_status = 'running'
    def say(text: str, turn: int):
        return route_curated_runtime_turn(s, text, turn_id=turn, language='ko', configuration_id=1, generation=1).plan
    before = snapshot(s)
    say('이 단계 왜 하는지 알려주고 다음 단계도 알려줘', 1)
    assert snapshot(s) == before
    plan = say(COMMANDS[0], 2)
    assert s.pending_completion_confirmation is not None
    assert '완료' in plan.speech_text
    assert s.current_index == 0
    say('네', 3)
    assert s.current_index == 1


def test_a_polite_mixed_request_still_stages_confirmation() -> None:
    p = load_corpus()[0]
    result = asyncio.run(run_case(p, Case('polite', 'mixed', '왜 하는지 설명하고 다음 단계로 가 줄래?')))
    assert result['changed_fields'] == ['confirmation']
    assert result['intent_kind'] == 'next_step_confirmation_required'
