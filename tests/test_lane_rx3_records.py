"""Read-only record projections use the existing report and tenant gates; no providers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.encoders import jsonable_encoder

from tests.test_lane_on_auth_api import Device
from voiney_lab.experiment_reports import ExperimentReportStore

from tests.test_lane_own_reports import records  # noqa: F401
from voiney_lab import server as srv
from voiney_lab.pilot_metrics import FeedbackAnswer


Records = tuple[dict[str, Device], ExperimentReportStore, str]


@pytest.mark.parametrize('who,status', [('owner', 200), ('admin', 200), ('peer', 404), ('outside', 404)])
def test_notebook_is_the_export_projection_without_writes(records: Records, monkeypatch: pytest.MonkeyPatch, who: str, status: int) -> None:
    devices, store, rid = records
    # Resolve the existing legacy ownership migration before measuring the read.
    assert devices["owner"].get(f"/api/experiment-reports/{rid}.json").status_code == 200
    before = store.get_report(rid)
    prepare = Mock(side_effect=AssertionError('read must not prepare prose'))
    monkeypatch.setattr(srv, '_prepare_report_prose', prepare)
    answer = devices[who].get(f'/api/experiment-reports/{rid}/notebook')
    assert answer.status_code == status
    if status == 200:
        assert answer.json()['notebook'] == jsonable_encoder(store._narrative(rid, None, None).facts.notebook)
        assert answer.json()['events'] == before['events']
        assert answer.json()['review']
        assert 'no-store' in answer.headers['cache-control']
    prepare.assert_not_called()
    assert store.get_report(rid) == before


def test_list_rating_is_latest_owners_answer_and_never_the_note(records: Records) -> None:
    devices, _, rid = records
    owner = srv._account_for_cookie(devices['owner'].session_cookie).principal()
    pilot = srv._pilot_store()
    pilot.record_feedback(account_id=owner.principal_id, answer=FeedbackAnswer(rid, 4, False, 'private synthetic feedback'))
    # Another account's row cannot substitute for the owner's rating.
    pilot.record_feedback(account_id='another-owner', answer=FeedbackAnswer(rid, 1, False, 'other private feedback'))
    for who in ('owner', 'admin'):
        answer = devices[who].get('/api/workspace/experiments')
        row = answer.json()['experiments'][0]
        assert row['rating'] == 4
        assert row['rating_state'] == 'rated'
        assert 'private' not in answer.text
    assert devices['peer'].get('/api/workspace/experiments').json()['experiments'] == []
    assert devices['outside'].get('/api/workspace/experiments').json()['experiments'] == []
    pilot.record_feedback(account_id=owner.principal_id, answer=FeedbackAnswer(rid, None, True, None))
    row = devices['owner'].get('/api/workspace/experiments').json()['experiments'][0]
    assert row['rating'] is None
    assert row['rating_state'] == 'unrated'


@pytest.mark.parametrize('field', ['cell', 'coating', 'headspace'])
def test_notebook_read_keeps_export_tables_for_three_fields(records: Records, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str) -> None:
    from tests.lane_rp3_support import (
        cell_protocol, cell_run, coating_protocol, coating_run, headspace_protocol, headspace_run,
    )
    from voiney_lab import experiment_reports as er
    devices, _, _ = records
    run, fixture = {
        'cell': (cell_run, cell_protocol),
        'coating': (coating_run, coating_protocol),
        'headspace': (headspace_run, headspace_protocol),
    }[field]
    # The synthetic source replaces only source lookup, never the notebook projector or HTTP gate.
    record = run(tmp_path / field)
    monkeypatch.setattr(er, 'report_protocol_fixture', lambda report: (fixture(), ''))
    owner = srv._account_for_cookie(devices['owner'].session_cookie).principal()
    from voiney_lab.workspace_store import initialize_workspace_store
    workspace = initialize_workspace_store(srv._workspace_settings())
    workspace.start_experiment(owner, session_id=record.store.get_report(record.report_id)['session_id'],
        protocol_id='synthetic-source', protocol_revision_id='synthetic-revision')
    workspace.close()
    monkeypatch.setenv('VOINEY_LAB_EXPERIMENT_REPORT_DB', str(record.store.path))
    expected = jsonable_encoder(record.store._narrative(record.report_id, None, None).facts.notebook)
    result = devices['owner'].get(f'/api/experiment-reports/{record.report_id}/notebook')
    assert result.status_code == 200
    assert result.json()['notebook']['condition_rows'] == expected['condition_rows']
    assert result.json()['notebook']['observations'] == expected['observations']
    assert expected['condition_rows']


def test_owner_records_methods_fields_and_domain_override_through_notebook_api(
    records: Records, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.lane_rp3_support import cell_protocol
    from voiney_lab import experiment_reports as er

    devices, store, rid = records
    monkeypatch.setattr(er, 'report_protocol_fixture', lambda report: (cell_protocol(), ''))
    before = store.get_report(rid)['events']
    domain = devices['owner'].post(
        f'/api/experiment-reports/{rid}/notebook/domain', json={'domain': 'biology'},
    )
    assert domain.status_code == 200, domain.text
    saved = devices['owner'].post(
        f'/api/experiment-reports/{rid}/notebook/inventory',
        json={
            'item_name': 'MTT reagent Sigma-Aldrich Catalog #M5655',
            'vendor': 'Sigma-Aldrich', 'catalog_number': 'M5655',
            'lot_number': 'LOT-24', 'stage': 'after',
        },
    )
    assert saved.status_code == 201, saved.text
    after = store.get_report(rid)['events']
    assert after[:len(before)] == before
    assert [event['event_type'] for event in after[-2:]] == [
        'notebook_domain_selected', 'notebook_inventory_recorded',
    ]
    notebook = devices['owner'].get(f'/api/experiment-reports/{rid}/notebook').json()['notebook']
    assert notebook['domain']['code'] == 'biology'
    assert notebook['domain']['selected_by'] == 'person'
    mtt = next(item for item in notebook['inventory'] if item['name'].startswith('MTT reagent'))
    assert mtt['lot_number'] == 'LOT-24'
    assert devices['owner'].post(
        f'/api/experiment-reports/{rid}/notebook/inventory',
        json={'item_name': 'invented reagent', 'vendor': 'x', 'stage': 'after'},
    ).status_code == 422
    for who in ('peer', 'admin', 'outside'):
        assert devices[who].post(
            f'/api/experiment-reports/{rid}/notebook/domain', json={'domain': 'chemistry'},
        ).status_code == 404


def test_lab_admin_sets_only_notebook_section_names_and_order(records: Records) -> None:
    devices, _, rid = records
    default = devices['owner'].get('/api/notebook-template')
    assert default.status_code == 200, default.text
    sections = default.json()['sections']
    assert [item['name'] for item in sections] == [
        '실험 정보', '목적', '방법', '관찰·특이사항', '결과', '해석·다음 할 일', '분야별 기록', '부록',
    ]
    changed = [
        {**next(item for item in sections if item['id'] == section_id), 'name': name}
        for section_id, name in (
            ('purpose', '연구 목적'), ('experiment_info', '기본 정보'), ('methods', '실험 방법'),
            ('observations', '관찰'), ('results', '결과'), ('interpretation_next', '해석·후속'),
            ('domain', '분야 기록'), ('appendix', '부록'),
        )
    ]
    assert devices['owner'].request('PUT', '/api/notebook-template', json={'sections': changed}).status_code == 403
    saved = devices['admin'].request('PUT', '/api/notebook-template', json={'sections': changed})
    assert saved.status_code == 200, saved.text
    assert devices['peer'].get('/api/notebook-template').json()['sections'] == changed
    markdown = devices['owner'].get(f'/api/experiment-reports/{rid}.md')
    assert markdown.status_code == 200
    headings = [line for line in markdown.text.splitlines() if line.startswith('## ')]
    assert headings[:3] == ['## 연구 목적', '## 기본 정보', '## 실험 방법']
    invalid = devices['admin'].request('PUT', '/api/notebook-template', json={'sections': changed[:-1]})
    assert invalid.status_code == 422
    assert devices['outside'].get('/api/notebook-template').json()['sections'] != changed
