"""Record ownership at the HTTP and voice boundaries; no external calls."""
from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_lane_on_auth_api import Device, _Socket, _configure, _invite
from voiney_lab import accounts as acc
from voiney_lab import server as srv
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.workspace_store import initialize_workspace_store


@pytest.fixture
def records(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORT_DB", str(tmp_path / "reports.sqlite"))
    monkeypatch.setattr(srv, "_prepare_report_prose", lambda *a, **kw: "ready")
    devices = {}
    for name, lab, admin in (("owner", "lab-a", False), ("peer", "lab-a", False),
                              ("admin", "lab-a", True), ("outside", "lab-b", True)):
        device = Device()
        device.sign_up(_invite(tmp_path, organization_id=lab, admin=admin), name)
        devices[name] = device
    owner = srv._account_for_cookie(devices["owner"].session_cookie).principal()
    workspace = initialize_workspace_store(srv._workspace_settings())
    workspace.bootstrap_principal(owner)
    workspace.start_experiment(owner, session_id="session-owned", protocol_id="protocol-shared",
                               protocol_revision_id="revision-one")
    store = ExperimentReportStore(tmp_path / "reports.sqlite")
    report = store.open_report(session_id="session-owned", protocol_id="protocol-shared",
        protocol_title="Fictional source", protocol_revision="revision-one",
        protocol_sha256="a" * 64, readiness_status="ready", development_only=True)
    rid = report["report_id"]
    workspace.bind_resource(owner, "experiment_report", rid)
    workspace.close()
    store.append_event(rid, event_key="measurement-one", event_type="observation",
                       user_wording="3 mL", category="measurement", confirmation_state="user_reported")
    store.finalize(rid, status="completed", event_key="finished")
    return devices, store, rid


@pytest.mark.parametrize("who,status", [("owner", 200), ("peer", 404), ("admin", 200), ("outside", 404)])
@pytest.mark.parametrize("suffix", [".json", "/review", "/prose", "/pi-confirmations"])
def test_read_permissions(records, who, status, suffix):
    devices, _, rid = records
    response = devices[who].get(f"/api/experiment-reports/{rid}{suffix}")
    assert response.status_code == status, response.text


@pytest.mark.parametrize("who", ["peer", "admin", "outside"])
@pytest.mark.parametrize("operation", ["prose", "correct", "feedback"])
def test_only_owner_can_write(records, who, operation):
    devices, store, rid = records
    before = store.get_report(rid)["events"]
    if operation == "feedback":
        path, body = "/api/pilot/feedback", {"report_id": rid, "rating": 4}
    elif operation == "prose":
        path, body = f"/api/experiment-reports/{rid}/prose", {}
    else:
        items = devices["owner"].get(f"/api/experiment-reports/{rid}/review").json()["items"]
        assert items
        path = f"/api/experiment-reports/{rid}/review/{items[0]['item_id']}"
        body = {"action": "correct", "text": "9 mL"}
    response = devices[who].post(path, json=body)
    assert response.status_code == 404, response.text
    assert store.get_report(rid)["events"] == before


def test_admin_can_only_append_pi_confirmation(records):
    devices, store, rid = records
    before = store.get_report(rid)["events"]
    response = devices["admin"].post(f"/api/experiment-reports/{rid}/pi-confirmations", json={"comment": "확인함"})
    assert response.status_code == 201, response.text
    after = store.get_report(rid)["events"]
    assert after[:-1] == before
    assert after[-1]["event_type"] == "pi_confirmed"
    assert devices["outside"].post(f"/api/experiment-reports/{rid}/pi-confirmations", json={}).status_code == 404


def test_legacy_report_owner_is_taken_from_session_not_binding(records):
    devices, store, rid = records
    assert devices["owner"].get(f"/api/experiment-reports/{rid}.json").status_code == 200
    report = store.get_report(rid)
    owner = srv._account_for_cookie(devices["owner"].session_cookie).principal()
    assert report["organization_id"] == owner.organization_id
    assert report["owner_principal_id"] == owner.principal_id
    assert store.backfill_ownership(lambda *args: (_ for _ in ()).throw(AssertionError("already migrated"))) == {
        "owned": 0, "admin_only": 0, "unscoped": 0}


def test_unknown_legacy_owner_is_only_visible_to_its_lab_admin(records):
    devices, store, _ = records
    report = store.open_report(session_id="session-no-owner", protocol_id="protocol-shared",
        protocol_title="Fictional legacy", protocol_revision="revision-one",
        protocol_sha256="a" * 64, readiness_status="ready", development_only=True)
    owner = srv._account_for_cookie(devices["owner"].session_cookie).principal()
    workspace = initialize_workspace_store(srv._workspace_settings())
    workspace.bind_resource(owner, "experiment_report", report["report_id"])
    workspace.close()
    for who, status in (("owner", 404), ("peer", 404), ("admin", 200), ("outside", 404)):
        assert devices[who].get(f"/api/experiment-reports/{report['report_id']}.json").status_code == status
    assert store.get_report(report["report_id"])["owner_principal_id"] is None


def test_schema_one_upgrades_without_rewriting_events(tmp_path):
    import sqlite3
    store = ExperimentReportStore(tmp_path / "legacy.sqlite")
    report = store.open_report(session_id="legacy-session", protocol_id="legacy-protocol",
        protocol_title="Legacy", protocol_revision="legacy-revision", protocol_sha256="b" * 64,
        readiness_status="ready", development_only=False)
    store.append_event(report["report_id"], event_key="kept", event_type="observation", user_wording="exact 3 µL")
    with sqlite3.connect(store.path) as connection:
        before = connection.execute("SELECT * FROM experiment_report_events").fetchall()
        for column in ("organization_id", "owner_principal_id", "ownership_migrated"):
            connection.execute(f"ALTER TABLE experiment_reports DROP COLUMN {column}")
        connection.execute("UPDATE experiment_report_metadata SET schema_version=1")
    assert store.backfill_ownership(lambda *args: ("lab-a", "account-owner")) == {
        "owned": 1, "admin_only": 0, "unscoped": 0}
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT * FROM experiment_report_events").fetchall() == before
        assert connection.execute("SELECT schema_version FROM experiment_report_metadata").fetchone()[0] == 2
    assert store.get_report(report["report_id"])["owner_principal_id"] == "account-owner"


def test_reopening_report_cannot_change_owner(tmp_path):
    store = ExperimentReportStore(tmp_path / "reports.sqlite")
    fields = dict(session_id="session-one", protocol_id="protocol-one", protocol_title="Fictional",
        protocol_revision="revision-one", protocol_sha256="c" * 64, readiness_status="ready",
        development_only=True, organization_id="lab-a", owner_principal_id="owner")
    first = store.open_report(**fields)
    with pytest.raises(ValueError, match="ownership"):
        store.open_report(**{**fields, "owner_principal_id": "peer"})
    assert store.get_report(first["report_id"])["owner_principal_id"] == "owner"


@pytest.mark.parametrize("who,allowed", [("owner", True), ("peer", False), ("admin", True), ("outside", False)])
@pytest.mark.parametrize("kind", ["experiment.report.get", "experiment.report.status.get"])
def test_socket_read_uses_same_permissions(records, who, allowed, kind):
    devices, _, rid = records

    class Socket(_Socket):
        def __init__(self):
            super().__init__({acc.SESSION_COOKIE_NAME: devices[who].session_cookie})
            self.once = False

        async def receive(self):
            if not self.once:
                self.once = True
                return {"type": "websocket.receive", "text": json.dumps({"type": kind, "report_id": rid})}
            return {"type": "websocket.disconnect", "code": 1000}

    socket = Socket()
    asyncio.run(srv.voice_socket(socket))
    results = [e for e in socket.sent if e["type"].startswith("experiment.report.")]
    assert len(results) == 1, socket.sent
    assert results[0]["type"] == ("experiment.report.state" if allowed else "experiment.report.error")
    if not allowed:
        assert results[0]["code"] == "report_not_found"
        assert "3 mL" not in json.dumps(socket.sent)
