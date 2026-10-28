"""Self-review: complete record permission matrix and legacy data preservation."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_lane_on_auth_api import Device, _invite
from tests.test_lane_own_reports import records
from voiney_lab import server as srv
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.pilot_metrics import PILOT_METRICS_FILENAME, PilotMetricsStore
from voiney_lab.workspace_store import initialize_workspace_store


ACTORS = ("owner", "peer", "admin", "outside", "outside-peer")
EXPORTS = ("json", "md", "csv", "docx", "pdf", "meeting-docx", "meeting-pdf", "xlsx")


def actor(records, who):
    devices, store, _ = records
    if who == "outside-peer":
        device = Device()
        device.sign_up(_invite(store.path.parent, organization_id="lab-b"), "outside-peer")
        return device
    return devices[who]


@pytest.mark.parametrize("who", ACTORS)
def test_every_export_format_has_the_same_permissions(records, monkeypatch, who):
    device = actor(records, who)
    _, store, rid = records
    before = store.get_report(rid)["events"]
    exported, prepared = [], []
    monkeypatch.setattr(srv, "_prepare_report_prose", lambda *a, **kw: prepared.append(1) or "ready")
    for suffix in EXPORTS:
        method = "export_" + suffix.replace("-", "_")
        if suffix == "md":
            method = "export_markdown"
        monkeypatch.setattr(ExperimentReportStore, method,
            lambda self, report_id, name=suffix, **kwargs:
                exported.append(name) or b"fictional export")
    for suffix in EXPORTS:
        response = device.get(f"/api/experiment-reports/{rid}.{suffix}")
        assert response.status_code == (200 if who in {"owner", "admin"} else 404), (suffix, response.text)
    assert exported == (list(EXPORTS) if who in {"owner", "admin"} else [])
    assert len(prepared) == (6 if who == "owner" else 0)
    assert store.get_report(rid)["events"] == before


@pytest.mark.parametrize("who", ACTORS)
def test_confirmation_correction_rating_and_regeneration_are_owner_only(records, monkeypatch, who):
    device = actor(records, who)
    devices, store, rid = records
    metrics = PilotMetricsStore(store.path.parent / PILOT_METRICS_FILENAME)
    assert devices["owner"].post("/api/pilot/feedback", json={"report_id": rid, "rating": 4}).status_code == 201
    feedback_before = metrics.feedback()
    events_before = store.get_report(rid)["events"]
    prepared = []
    monkeypatch.setattr(srv, "_prepare_report_prose", lambda *a, **kw: prepared.append(1) or "ready")
    for action in ("confirm", "correct"):
        items = devices["owner"].get(f"/api/experiment-reports/{rid}/review").json()["items"]
        assert items
        response = device.post(f"/api/experiment-reports/{rid}/review/{items[0]['item_id']}",
                               json={"action": action, "text": "9 mL"})
        assert response.status_code == (200 if who == "owner" else 404), response.text
    response = device.post("/api/pilot/feedback", json={"report_id": rid, "rating": 5})
    assert response.status_code == (201 if who == "owner" else 404), response.text
    response = device.post(f"/api/experiment-reports/{rid}/prose", json={})
    assert response.status_code == (200 if who == "owner" else 404), response.text
    events_after = store.get_report(rid)["events"]
    feedback_after = metrics.feedback()
    assert events_after[:len(events_before)] == events_before
    assert feedback_after[:len(feedback_before)] == feedback_before
    if who == "owner":
        assert any(e["event_type"] == "record_corrected" for e in events_after[len(events_before):])
        assert len(feedback_after) == len(feedback_before) + 1
        assert feedback_after[-1]["rating"] == 5
        assert prepared
    else:
        assert events_after == events_before
        assert feedback_after == feedback_before
        assert prepared == []


@pytest.mark.parametrize("who", ACTORS)
def test_pi_confirmation_requires_same_lab_admin_and_keeps_original_events(records, who):
    device = actor(records, who)
    _, store, rid = records
    before = store.get_report(rid)["events"]
    response = device.post(f"/api/experiment-reports/{rid}/pi-confirmations",
                           json={"name": "untrusted name", "comment": "확인함"})
    expected = 201 if who == "admin" else 403 if who == "owner" else 404
    assert response.status_code == expected, response.text
    after = store.get_report(rid)["events"]
    assert after[:len(before)] == before
    if who == "admin":
        assert len(after) == len(before) + 1
        assert after[-1]["payload"]["name"] == "admin"
    else:
        assert after == before


def test_an_admin_can_write_and_pi_confirm_their_own_record(records, monkeypatch):
    devices, store, _ = records
    admin = srv._account_for_cookie(devices["admin"].session_cookie).principal()
    workspace = initialize_workspace_store(srv._workspace_settings())
    try:
        workspace.bootstrap_principal(admin)
        workspace.start_experiment(admin, session_id="admin-owned-session", protocol_id="protocol-shared",
                                   protocol_revision_id="revision-one")
        report = store.open_report(session_id="admin-owned-session", protocol_id="protocol-shared",
            protocol_title="Fictional", protocol_revision="revision-one", protocol_sha256="c" * 64,
            readiness_status="ready", development_only=True,
            organization_id=admin.organization_id, owner_principal_id=admin.principal_id)
        workspace.bind_resource(admin, "experiment_report", report["report_id"])
    finally:
        workspace.close()
    rid = report["report_id"]
    store.append_event(rid, event_key="measurement", event_type="observation", user_wording="3 mL")
    store.finalize(rid, status="completed", event_key="completed")
    owned = ({**devices, "owner": devices["admin"]}, store, rid)
    test_confirmation_correction_rating_and_regeneration_are_owner_only(owned, monkeypatch, "owner")
    assert devices["admin"].post(f"/api/experiment-reports/{rid}/pi-confirmations", json={}).status_code == 201


def legacy_store(tmp_path):
    store = ExperimentReportStore(tmp_path / "legacy.sqlite")
    for index in range(2):
        report = store.open_report(session_id=f"session-{index}", protocol_id="protocol-one",
            protocol_title="Fictional", protocol_revision="revision-one", protocol_sha256="a" * 64,
            readiness_status="ready", development_only=True)
        store.append_event(report["report_id"], event_key="observation", event_type="observation",
                           user_wording="exact 3 µL", payload={"unchanged": index})
        store.finalize(report["report_id"], status="completed", event_key="completed")
        store.save_prose(report["report_id"], writer="server", reply={"note": "kept"}, failure="", event_count=2)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP INDEX experiment_report_owner_history")
        for column in ("organization_id", "owner_principal_id", "ownership_migrated"):
            connection.execute(f"ALTER TABLE experiment_reports DROP COLUMN {column}")
        connection.execute("UPDATE experiment_report_metadata SET schema_version=1")
        columns = [r[1] for r in connection.execute("PRAGMA table_info(experiment_reports)")]
        rows = connection.execute("SELECT * FROM experiment_reports ORDER BY report_id").fetchall()
        events = connection.execute("SELECT * FROM experiment_report_events ORDER BY id").fetchall()
        prose = connection.execute("SELECT * FROM experiment_report_prose ORDER BY report_id").fetchall()
    return store, columns, rows, events, prose


@pytest.mark.parametrize("scenario", ("retry_after_failure", "concurrent"))
def test_schema_one_upgrade_preserves_all_rows_events_and_prose(tmp_path, scenario):
    store, columns, rows, events, prose = legacy_store(tmp_path)
    if scenario == "retry_after_failure":
        calls = []
        def interrupted(*args):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("resolver unavailable")
            return "lab-a", "owner"
        with pytest.raises(RuntimeError, match="resolver unavailable"):
            store.backfill_ownership(interrupted)
        with sqlite3.connect(store.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM experiment_reports WHERE ownership_migrated=0").fetchone()[0] == 2
        assert store.backfill_ownership(lambda *a: ("lab-a", "owner"))["owned"] == 2
    else:
        def migrate(_):
            return ExperimentReportStore(store.path).backfill_ownership(lambda *a: ("lab-a", "owner"))["owned"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sum(pool.map(migrate, range(2))) == 2
    assert store.backfill_ownership(lambda *a: (None, None)) == {"owned": 0, "admin_only": 0, "unscoped": 0}
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(f"SELECT {','.join(columns)} FROM experiment_reports ORDER BY report_id").fetchall() == rows
        assert connection.execute("SELECT * FROM experiment_report_events ORDER BY id").fetchall() == events
        assert connection.execute("SELECT * FROM experiment_report_prose ORDER BY report_id").fetchall() == prose


def test_legacy_record_with_no_lab_is_quarantined_not_deleted(records):
    devices, store, _ = records
    report = store.open_report(session_id="unscoped-session", protocol_id="unscoped-protocol",
        protocol_title="Fictional", protocol_revision="revision-one", protocol_sha256="b" * 64,
        readiness_status="ready", development_only=True)
    rid = report["report_id"]
    for who in ACTORS:
        device = actor(records, who)
        assert device.get(f"/api/experiment-reports/{rid}.json").status_code == 404
    assert store.get_report(rid)["report_id"] == rid
    assert store.get_report(rid)["organization_id"] is None


def test_lab_only_legacy_record_is_admin_read_only_except_pi(records, monkeypatch):
    devices, store, _ = records
    report = store.open_report(session_id="lab-only-session", protocol_id="protocol-shared",
        protocol_title="Fictional", protocol_revision="revision-one", protocol_sha256="d" * 64,
        readiness_status="ready", development_only=True)
    rid = report["report_id"]
    store.append_event(rid, event_key="measurement", event_type="observation", user_wording="3 mL")
    store.finalize(rid, status="completed", event_key="completed")
    principal = srv._account_for_cookie(devices["owner"].session_cookie).principal()
    workspace = initialize_workspace_store(srv._workspace_settings())
    try:
        workspace.bind_resource(principal, "experiment_report", rid)
    finally:
        workspace.close()
    response = devices["admin"].get(f"/api/experiment-reports/{rid}/review")
    assert response.status_code == 200
    item_id = response.json()["items"][0]["item_id"]
    before = store.get_report(rid)["events"]
    prepared = []
    monkeypatch.setattr(srv, "_prepare_report_prose", lambda *a, **kw: prepared.append(1) or "ready")
    for who in ACTORS:
        device = actor(records, who)
        for action in ("confirm", "correct"):
            assert device.post(f"/api/experiment-reports/{rid}/review/{item_id}",
                               json={"action": action, "text": "9 mL"}).status_code == 404
        assert device.post(f"/api/experiment-reports/{rid}/prose", json={}).status_code == 404
        assert device.post("/api/pilot/feedback", json={"report_id": rid, "rating": 5}).status_code == 404
    assert store.get_report(rid)["events"] == before
    assert prepared == []
    assert store.get_report(rid)["owner_principal_id"] is None
    assert devices["admin"].post(f"/api/experiment-reports/{rid}/pi-confirmations", json={}).status_code == 201
    assert store.get_report(rid)["events"][:-1] == before
