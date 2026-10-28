"""Read-only record projections use the existing report and tenant gates; no providers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
import base64
import io

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


def test_owner_can_restore_an_earlier_domain_and_inventory_value(
    records: Records, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.lane_rp3_support import cell_protocol
    from voiney_lab import experiment_reports as er

    devices, store, rid = records
    monkeypatch.setattr(er, 'report_protocol_fixture', lambda report: (cell_protocol(), ''))
    for domain in ('biology', 'chemistry', 'biology'):
        response = devices['owner'].post(
            f'/api/experiment-reports/{rid}/notebook/domain', json={'domain': domain},
        )
        assert response.status_code == 200, response.text
    assert devices['owner'].get(
        f'/api/experiment-reports/{rid}/notebook'
    ).json()['notebook']['domain']['code'] == 'biology'

    base = {
        'item_name': 'MTT reagent Sigma-Aldrich Catalog #M5655',
        'vendor': 'Sigma-Aldrich', 'catalog_number': 'M5655', 'stage': 'after',
    }
    for lot in ('LOT-A', 'LOT-B', 'LOT-A'):
        response = devices['owner'].post(
            f'/api/experiment-reports/{rid}/notebook/inventory',
            json={**base, 'lot_number': lot},
        )
        assert response.status_code == 201, response.text
    notebook = devices['owner'].get(
        f'/api/experiment-reports/{rid}/notebook'
    ).json()['notebook']
    assert next(item for item in notebook['inventory'] if item['name'] == 'MTT reagent')['lot_number'] == 'LOT-A'
    events = store.get_report(rid)['events']
    assert [event['payload']['domain'] for event in events
            if event['event_type'] == 'notebook_domain_selected'] == [
        'biology', 'chemistry', 'biology',
    ]
    assert [event['payload']['lot_number'] for event in events
            if event['event_type'] == 'notebook_inventory_recorded'] == [
        'LOT-A', 'LOT-B', 'LOT-A',
    ]


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


def test_lab_admin_can_restore_an_earlier_notebook_template(records: Records) -> None:
    devices, _, _ = records
    sections = devices['admin'].get('/api/notebook-template').json()['sections']

    def renamed(name: str):
        return [{**item, 'name': name if item['id'] == 'purpose' else item['name']}
                for item in sections]

    first, second = renamed('연구 목적'), renamed('실험 목표')
    for version in (first, second, first):
        response = devices['admin'].request(
            'PUT', '/api/notebook-template', json={'sections': version},
        )
        assert response.status_code == 200, response.text
    assert devices['owner'].get('/api/notebook-template').json()['sections'] == first


def test_same_protocol_comparison_requires_owner_opt_in_and_stays_read_only(
    records: Records, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.lane_rp3_support import cell_protocol
    from voiney_lab import experiment_reports as er
    from voiney_lab.workspace_store import initialize_workspace_store

    devices, store, owner_report_id = records
    monkeypatch.setattr(er, 'report_protocol_fixture', lambda report: (cell_protocol(), ''))
    # Resolve the legacy fixture's owner before selecting same-lab candidates.
    assert devices['owner'].get(f'/api/experiment-reports/{owner_report_id}/notebook').status_code == 200
    peer = srv._account_for_cookie(devices['peer'].session_cookie).principal()
    workspace = initialize_workspace_store(srv._workspace_settings())
    workspace.bootstrap_principal(peer)
    workspace.start_experiment(
        peer, session_id='session-peer-comparison', protocol_id='protocol-shared',
        protocol_revision_id='revision-two',
    )
    peer_report = store.open_report(
        session_id='session-peer-comparison', protocol_id='protocol-shared',
        protocol_title='Fictional source', protocol_revision='revision-two',
        protocol_sha256='a' * 64, readiness_status='ready', development_only=True,
        organization_id=peer.organization_id, owner_principal_id=peer.principal_id,
    )
    peer_report_id = peer_report['report_id']
    workspace.bind_resource(peer, 'experiment_report', peer_report_id)
    workspace.close()
    store.append_event(
        peer_report_id, event_key='peer-started', event_type='session_started',
        payload={'run_changes': []},
    )
    store.append_event(
        peer_report_id, event_key='peer-note', event_type='observation', step_id='p-3',
        step_label='3', user_wording='A1 well 탁함', category='note',
    )
    store.finalize(peer_report_id, status='completed', event_key='peer-finished')

    before = devices['owner'].get(
        f'/api/experiment-reports/{owner_report_id}/comparisons'
    )
    assert before.status_code == 200, before.text
    assert [item['record_scope'] for item in before.json()['records']] == ['mine']
    assert before.json()['permission']['judgment'] == 'none'

    shared = devices['peer'].post(
        f'/api/experiment-reports/{peer_report_id}/sharing', json={'shared': True},
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()['shared'] is True
    compared = devices['owner'].get(
        f'/api/experiment-reports/{owner_report_id}/comparisons'
    )
    assert compared.status_code == 200, compared.text
    records_json = compared.json()['records']
    assert [item['record_scope'] for item in records_json] == ['mine', 'shared']
    other = records_json[1]
    assert any(note['text'] == 'A1 well 탁함' for note in other['notes'])
    assert other['label'].startswith('공유 기록 ')
    forbidden = {'owner_principal_id', 'report_id', 'purpose', 'result', 'interpretation',
                 'recommendation', 'event_key'}
    assert forbidden.isdisjoint(other)
    assert all(forbidden.isdisjoint(row) for rows in (
        other['value_rows'], other['method_rows'], other['timeline'], other['notes'],
    ) for row in rows)
    assert compared.json()['permission'] == {
        'scope': 'same_organization_same_protocol',
        'other_records': 'opt_in_read_only',
        'withdrawal': 'latest_append_only_event',
        'judgment': 'none',
    }

    before_events = store.get_report(peer_report_id)['events']
    for who in ('owner', 'admin', 'outside'):
        assert devices[who].post(
            f'/api/experiment-reports/{peer_report_id}/sharing', json={'shared': False},
        ).status_code == 404
    assert store.get_report(peer_report_id)['events'] == before_events
    assert devices['admin'].get(
        f'/api/experiment-reports/{owner_report_id}/comparisons'
    ).status_code == 404
    assert devices['outside'].get(
        f'/api/experiment-reports/{owner_report_id}/comparisons'
    ).status_code == 404

    withdrawn = devices['peer'].post(
        f'/api/experiment-reports/{peer_report_id}/sharing', json={'shared': False},
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()['shared'] is False
    after = devices['owner'].get(
        f'/api/experiment-reports/{owner_report_id}/comparisons'
    ).json()['records']
    assert [item['record_scope'] for item in after] == ['mine']
    share_events = [event for event in store.get_report(peer_report_id)['events']
                    if event['event_type'] == 'notebook_share_changed']
    assert [event['payload']['shared'] for event in share_events] == [True, False]
    assert devices['peer'].post(
        f'/api/experiment-reports/{peer_report_id}/sharing', json={'shared': True},
    ).json()['shared'] is True
    share_events = [event for event in store.get_report(peer_report_id)['events']
                    if event['event_type'] == 'notebook_share_changed']
    assert [event['payload']['shared'] for event in share_events] == [True, False, True]


def test_word_export_embeds_authenticated_record_photo(
    records: Records, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docx import Document
    from fastapi import Response
    from tests.lane_rp3_support import cell_protocol
    from voiney_lab import experiment_reports as er

    devices, store, report_id = records
    monkeypatch.setattr(er, 'report_protocol_fixture', lambda report: (cell_protocol(), ''))
    assert devices['owner'].get(f'/api/experiment-reports/{report_id}/notebook').status_code == 200
    store.append_event(
        report_id, event_key='photo-evidence-word', event_type='photo_attached',
        step_id='p-1', step_label='1', user_wording='1단계 배양 사진',
        payload={
            'evidence_id': 'evidence-word-photo', 'media_type': 'image/png',
            'interpretation_status': 'not_interpreted',
        },
    )
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    )
    requested = []

    def evidence(session_id, evidence_id):
        requested.append((session_id, evidence_id))
        return Response(content=png, media_type='image/png')

    monkeypatch.setattr(srv, 'download_workspace_experiment_evidence', evidence)
    response = devices['owner'].get(f'/api/experiment-reports/{report_id}.docx')
    assert response.status_code == 200, response.text
    document = Document(io.BytesIO(response.content))
    assert len(document.inline_shapes) == 1
    assert requested == [('session-owned', 'evidence-word-photo')]
