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


def test_router_cost_estimate_uses_reported_usage_and_marks_missing_calls() -> None:
    from scripts.sim.run import router_cost_estimate

    estimate = router_cost_estimate(
        {'model_calls': 3, 'model_completed_with_usage': 2,
         'usage': {'prompt_tokens': 8000, 'completion_tokens': 500}},
        input_rate=5, output_rate=15,
    )
    assert estimate == {
        'reported_usage_calls': 2,
        'usage_missing_calls': 1,
        'input_usd_per_million_assumed': 5,
        'output_usd_per_million_assumed': 15,
        'reported_usage_usd_estimate': 0.0475,
        'rates_verified': False,
    }


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
    assert attached[0].fixture.translation_versions[row['key']] == 0


def test_only_explicit_router_comparison_allows_the_treatment_to_change() -> None:
    from scripts.sim.compare import compare
    report = dict(schema=1, mode='offline', corpus_sha256='c', workload_sha256='w',
                  router='off', rows=[], overall=dict(cases=0, understood=0, wrong_state=0,
                  english_copy=0, duplicate_label=0, incomplete=0, interrupted=0))
    routed = {**report, 'mode': 'oracle_model', 'router': 'configured'}
    with pytest.raises(ValueError):
        compare(report, routed)
    assert compare(report, routed, allow_router_change=True)['comparable']
    first = {**routed, 'router_settings': {'provider': 'xai', 'model': 'first'}}
    second = {**routed, 'router_settings': {'provider': 'xai', 'model': 'second'}}
    with pytest.raises(ValueError):
        compare(first, second)


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
    monkeypatch.setenv('VOINEY_LAB_MULTI_BRAIN_ENABLED', 'true')
    original_create = client.chat.completions.create
    async def isolated_create(**kwargs):
        import os
        assert os.environ.get('VOINEY_LAB_MULTI_BRAIN_ENABLED') != 'true'
        assert 'VOINEY_LAB_ROUTER_PROVIDER' not in os.environ
        return await original_create(**kwargs)
    client.chat.completions.create = isolated_create
    original_run_case = run.run_case
    async def controlled_case(*args, **kwargs):
        assert kwargs.get('oracle_environment') is True
        return await original_run_case(*args, **kwargs)
    monkeypatch.setattr(run, 'run_case', controlled_case)
    output = tmp_path/'oracle.json'
    assert run.main(['--configured-router', '--execute', '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert report['router'] == 'configured'
    assert report['router_settings']['model'] == 'fake-router'
    assert report['overall']['wrong_state'] == 0
    assert report['rows'][0]['changed_fields'] == ['confirmation']
    assert len(client.requests) == 1


def test_model_cli_counts_server_timeout_and_missing_usage(tmp_path, monkeypatch) -> None:
    from scripts.sim import run
    from scripts.sim.corpus import Case
    from scripts.sim.runner import import_server
    from tests.router_fakes import FakeRouterClient, tool_reply

    protocol = load_corpus()[0]
    case = Case('router-timeout', 'router_proposal', '이제 다음 거 하자',
                actions=('clarify_completion',), allowed=('confirmation',))
    delayed = tool_reply((
        'change_state', {'action': 'next', 'evidence': '다음 거 하자'}))
    client = FakeRouterClient((delayed, .3))
    monkeypatch.setattr(
        run, 'prepare_cases',
        lambda args: ([(protocol, case)], {'name': 'development'}, {}, {}))
    monkeypatch.setattr(run, 'output_dir', lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(import_server(), '_role_client', lambda role: client)
    monkeypatch.setenv('VOINEY_LAB_LLM_ROUTER_ENABLED', 'true')
    monkeypatch.setenv('VOINEY_LAB_ROUTER_PROVIDER', 'xai')
    monkeypatch.setenv('VOINEY_LAB_ROUTER_MODEL', 'fake-router')
    monkeypatch.setenv('VOINEY_LAB_LLM_ROUTER_TIMEOUT_SECONDS', '.2')
    output = tmp_path/'timeout.json'

    assert run.main(['--configured-router', '--execute', '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert report['overall']['wrong_state'] == 0
    assert report['overall']['router']['model_calls'] == 1
    assert report['overall']['router']['timeouts'] == 1
    assert report['overall']['router']['timeout_rate'] == 1.0
    assert report['router_cost_estimate']['usage_missing_calls'] == 1


def test_model_cli_counts_rate_limit_turn_that_has_no_route_decision(
    tmp_path, monkeypatch,
) -> None:
    from scripts.sim import run
    from scripts.sim.corpus import Case
    from scripts.sim.runner import import_server
    from tests.router_fakes import FakeRouterClient
    from voiney_lab.model_providers import ModelProviderError

    protocol = load_corpus()[0]
    case = Case('router-rate-limit', 'router_proposal', '이제 다음 거 하자',
                actions=(), allowed=())
    client = FakeRouterClient(ModelProviderError('openai', 'rate_limited', 429))
    monkeypatch.setattr(
        run, 'prepare_cases',
        lambda args: ([(protocol, case)], {'name': 'development'}, {}, {}))
    monkeypatch.setattr(run, 'output_dir', lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(import_server(), '_role_client', lambda role: client)
    monkeypatch.setenv('VOINEY_LAB_LLM_ROUTER_ENABLED', 'true')
    monkeypatch.setenv('VOINEY_LAB_ROUTER_PROVIDER', 'xai')
    monkeypatch.setenv('VOINEY_LAB_ROUTER_MODEL', 'fake-router')
    output = tmp_path/'rate-limit.json'

    assert run.main(['--configured-router', '--execute', '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert report['overall']['wrong_state'] == 0
    assert report['overall']['router']['model_calls'] == 1
    assert report['overall']['router']['handled_by'] == {'model_unavailable': 1}
    assert report['overall']['router']['fallback_reasons'] == {
        'model_unavailable:rate_limited': 1}
    assert report['router_cost_estimate']['usage_missing_calls'] == 1
