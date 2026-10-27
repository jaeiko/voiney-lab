"""Model comparisons are opt-in and capped; stored readings keep source identity."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from scripts.sim.corpus import load_corpus


def test_router_dry_run_has_explicit_request_and_cost_caps(tmp_path, capsys) -> None:
    from scripts.sim import run
    assert run.main(['--configured-router', '--split', 'development', '--limit', '2',
                     '--max-calls', '3', '--max-usd', '1', '--output', str(tmp_path/'unused.json')]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['external_requests_this_dry_run'] == 0
    assert plan['router_turns_at_most'] == 2
    assert plan['call_cap'] == 3
    assert plan['reserved_usd_cap'] == 1
    assert not (tmp_path/'unused.json').exists()


def test_stored_translation_rejects_wrong_identity_and_scientific_value(tmp_path) -> None:
    from scripts.sim.translations import attach_translations
    p = load_corpus()[0]
    step = p.fixture.steps[0]
    row = dict(protocol=p.code, source_sha256='wrong', key=f'{step.step_id}/current_step',
               source=step.instruction_source_text, korean='기존 배지를 제거해요.')
    path = tmp_path/'readings.json'
    def write():
        path.write_text(json.dumps({'schema': 1, 'readings': [row]}))
    write()
    with pytest.raises(ValueError):
        attach_translations((p,), path)
    row['source_sha256'] = p.fixture.source_pdf_sha256
    row['korean'] = '999 mL의 배지를 제거해요.'
    write()
    with pytest.raises(ValueError):
        attach_translations((p,), path)
    row['korean'] = '1단계: 기존 배지를 멸균 흡인 피펫으로 제거해요.'
    write()
    attached, coverage = attach_translations((p,), path)
    assert attached[0].fixture.localized_fact(step.step_id, 'current_step') == row['korean']
    assert coverage['attached'] == 1


def test_only_explicit_router_comparison_allows_the_treatment_to_change() -> None:
    from scripts.sim.compare import compare
    report = dict(schema=1, mode='offline', corpus_sha256='c', workload_sha256='w',
                  router='off', rows=[], overall=dict(cases=0, understood=0, wrong_state=0,
                  english_copy=0, duplicate_label=0, incomplete=0, interrupted=0))
    routed = {**report, 'mode': 'oracle_model', 'router': 'configured'}
    with pytest.raises(ValueError):
        compare(report, routed)
    assert compare(report, routed, allow_router_change=True)['comparable']


def test_budget_counts_the_sdk_httpx2_transport(tmp_path) -> None:
    from contextlib import ExitStack
    from scripts.sim.live import Budget, BudgetExceeded
    httpx2 = pytest.importorskip('httpx2')
    budget = Budget(1, .25, .25, tmp_path/'calls.jsonl')
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json={}))
    with httpx2.Client(transport=transport) as client, ExitStack() as stack:
        budget.guard(stack)
        assert client.get('https://contract.invalid').status_code == 200
        with pytest.raises(BudgetExceeded):
            client.get('https://contract.invalid')
    assert budget.calls == 1


def test_model_cli_uses_the_existing_server_proposal_gate(tmp_path, monkeypatch) -> None:
    from scripts.sim import run
    from scripts.sim.corpus import Case
    from scripts.sim.runner import import_server
    from tests.router_fakes import FakeRouterClient, tool_reply
    p = load_corpus()[0]
    c = Case('router-cli', 'router_proposal', '이제 다음 거 하자',
             actions=('clarify_completion',), allowed=('confirmation',))
    client = FakeRouterClient(tool_reply(('change_state', {'action': 'next', 'evidence': '다음 거 하자'})))
    monkeypatch.setattr(run, 'prepare_cases', lambda args: ([(p, c)], {'name': 'development'}, {}, {}))
    monkeypatch.setattr(run, 'output_dir', lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(import_server(), '_role_client', lambda role: client)
    monkeypatch.setenv('VOINEY_LAB_LLM_ROUTER_ENABLED', 'true')
    monkeypatch.setenv('VOINEY_LAB_ROUTER_PROVIDER', 'xai')
    monkeypatch.setenv('VOINEY_LAB_ROUTER_MODEL', 'fake-router')
    output = tmp_path/'oracle.json'
    assert run.main(['--configured-router', '--execute', '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert report['router'] == 'configured'
    assert report['overall']['wrong_state'] == 0
    assert report['rows'][0]['changed_fields'] == ['confirmation']
    assert len(client.requests) == 1
