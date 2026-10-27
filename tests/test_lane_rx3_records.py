"""Read-only record projections use the existing report and tenant gates; no providers."""
from unittest.mock import Mock

import pytest
from fastapi.encoders import jsonable_encoder

from tests.test_lane_own_reports import records  # noqa: F401
from voiney_lab import server as srv
from voiney_lab.pilot_metrics import FeedbackAnswer


@pytest.mark.parametrize('who,status', [('owner', 200), ('admin', 200), ('peer', 404), ('outside', 404)])
def test_notebook_is_the_export_projection_without_writes(records, monkeypatch, who, status):
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


def test_list_rating_is_latest_owners_answer_and_never_the_note(records, monkeypatch):
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
def test_notebook_read_keeps_export_tables_for_three_fields(records, monkeypatch, tmp_path, field):
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
