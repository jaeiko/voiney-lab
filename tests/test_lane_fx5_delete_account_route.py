"""Lane FX5, decision 2 (2026-10-20): an account deletes itself -- ``POST /api/auth/delete-account``.

The signed-in account sends its password again; the server checks it (a wrong
one counts toward the login lock), refuses the lab's last administrator, then
switches the account off, deletes what ``scripts/pilot_data_delete.py
--account`` deletes (``tests/test_lane_fx5_account_erasure.py`` holds the
scope), deletes the account, and appends one record of categories and counts
-- no name, id or password -- to ``deletion_log.jsonl`` beside the pilot
folder's other records. What the lab shares stays under "(삭제된 계정)".
The door is accounts; the fake-free parts are the product's own stores in a
temporary folder.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.test_lane_fx5_account_erasure import ALLOWED_PRINCIPAL_PLACES, scan
from tests.test_lane_on_auth_api import PASSWORD, Device, _configure, _invite, _store
from voiney_lab import accounts as acc
from voiney_lab import server as server_module
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.pilot_metrics import FeedbackAnswer, PilotMetricsStore, TurnRecord
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

ROUTE = "/api/auth/delete-account"


def _pilot(monkeypatch, tmp_path) -> Path:
    """The pilot layout: the workspace folder under a pilot folder that also holds the reports and the log."""

    pilot = tmp_path / "pilot"
    workspace = pilot / "workspace"
    workspace.mkdir(parents=True)
    _configure(monkeypatch, workspace)
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORT_DB", str(pilot / "experiment_reports.sqlite"))
    return workspace


def _person(workspace: Path, name: str, *, admin: bool = False, lab: str = "lab-a") -> Device:
    device = Device()
    device.sign_up(_invite(workspace, admin=admin, organization_id=lab), name)
    assert device.get("/api/workspace/session").status_code == 200  # the identity row is made
    return device


def _leave_data(workspace: Path, name: str, session_id: str) -> None:
    """One experiment with its record, an observation, a metrics row and a rating, as the product writes them."""

    accounts = _store(workspace)
    store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=workspace))
    try:
        principal = accounts.account_by_name(name).principal()
        store.start_experiment(
            principal, protocol_id="protocol-1", protocol_revision_id="pdf-1", session_id=session_id,
            current_step_id="step-1", current_step_label="1")
        store.transition_experiment(
            principal, session_id, action="resume", expected_version=1, event_key=f"{session_id}-go")
        reports = ExperimentReportStore(workspace.parent / "experiment_reports.sqlite")
        report = reports.open_report(
            session_id=session_id, protocol_id="protocol-1", protocol_title="Shared", protocol_revision="pdf-1",
            protocol_sha256="a" * 64, readiness_status="guidance_ready", development_only=False)
        reports.append_event(report["report_id"], event_key="experimenter", event_type="experimenter_recorded",
                             payload={"display_name": name})
        store.bind_resource(principal, "experiment_report", report["report_id"])
        store.record_observation(
            principal, session_id, event_key=f"{session_id}-obs", content="sample looks cloudy",
            category="appearance", capture_source="manual")
        metrics = PilotMetricsStore(workspace / "pilot_metrics.sqlite")
        metrics.record_turn(TurnRecord(
            account_id=principal.principal_id, session_id=session_id, report_id=report["report_id"], turn_id=1,
            router="front", reason="none", action="repeat", source="voice", state_changed=False,
            step_advanced=False, clarified=False, first_audio_ms=300, total_ms=900))
        metrics.record_feedback(
            account_id=principal.principal_id,
            answer=FeedbackAnswer(report_id=report["report_id"], rating=4, skipped=False, note="메모 1234"))
    finally:
        store.close()
        accounts.close()


def _count(path: Path, table: str, where: str = "", parameters=()) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table} {where}", parameters).fetchone()[0]
    finally:
        connection.close()


def _log(workspace: Path) -> list[dict]:
    path = workspace.parent / acc.DELETION_LOG_NAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _sessions(workspace: Path) -> int:
    return _count(workspace / "commercial_workspace.sqlite", "experiment_sessions")


# --- The door and the password ------------------------------------------------


def test_nobody_signed_in_and_the_dev_door_are_refused(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    response = Device().post(ROUTE, json={"password": PASSWORD})
    assert (response.status_code, response.json()) == (401, {"detail": "authentication_required"})
    _configure(monkeypatch, workspace, mode=None)
    response = Device().post(ROUTE, json={"password": PASSWORD})
    assert (response.status_code, response.json()) == (409, {"detail": "accounts_disabled"})


def test_a_wrong_password_deletes_nothing_and_counts_toward_the_login_lock(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    member = _person(workspace, "최수진")
    _leave_data(workspace, "최수진", "exp-m")
    for _ in range(acc.LOGIN_FAILURE_LIMIT):
        response = member.post(ROUTE, json={"password": "wrong wrong wrong"})
        assert (response.status_code, response.json()) == (401, {"detail": "login_refused"})
    locked = member.post(ROUTE, json={"password": PASSWORD})
    assert locked.status_code == 429 and locked.json() == {"detail": "login_locked"}
    assert "retry-after" in locked.headers
    assert _sessions(workspace) == 1
    assert _store(workspace).account_by_name("최수진") is not None
    assert _log(workspace) == []


def test_a_missing_password_is_a_malformed_request(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    member = _person(workspace, "최수진")
    for body in ({}, {"password": None}, {"password": ""}, {"password": 12345678}):
        response = member.post(ROUTE, json=body)
        assert (response.status_code, response.json()) == (422, {"detail": "password_invalid"}), body
    assert _store(workspace).account_by_name("최수진") is not None


# --- The deletion -----------------------------------------------------------------


def test_the_right_password_deletes_the_account_and_its_data_and_signs_it_out(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    member = _person(workspace, "최수진")
    other = _person(workspace, "강이수")
    _leave_data(workspace, "최수진", "exp-m")
    _leave_data(workspace, "강이수", "exp-o")
    principal_id = _store(workspace).account_by_name("최수진").principal().principal_id
    account_id = _store(workspace).account_by_name("최수진").account_id
    response = member.post(ROUTE, json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] is True
    assert set(body["removed"]) >= {"workspace_records", "experiment_records", "pilot_metrics", "account_store"}
    assert "최수진" not in response.text and account_id not in response.text
    cookie = next(value for key, value in response.headers.multi_items()
                  if key.lower() == "set-cookie" and value.startswith(acc.SESSION_COOKIE_NAME + "="))
    assert 'Max-Age=0' in cookie or 'expires=' in cookie.lower()
    # Its data is gone -- the identity row stays only where the lab's settings point at it, without the name --
    # and the other account's is not.
    left = scan(workspace.parent, {"name": "최수진", "principal": principal_id})
    assert left["name"] == set(), left
    assert left["principal"] <= ALLOWED_PRINCIPAL_PLACES, left
    connection = sqlite3.connect(workspace / "commercial_workspace.sqlite")
    try:
        row = connection.execute(
            "SELECT display_name,subject FROM principals WHERE principal_id=?", (principal_id,)).fetchone()
    finally:
        connection.close()
    assert row is None or (row[0] == "(삭제된 계정)" and "최수진" not in row[1]), "kept only blank"
    # Signed out, and the name and password no longer log in.
    assert member.get("/api/auth/session").json()["signed_in"] is False
    refused = Device().post("/api/auth/login", json={"name": "최수진", "password": PASSWORD})
    assert (refused.status_code, refused.json()) == (401, {"detail": "login_refused"})
    assert _count(workspace / "pilot_metrics.sqlite", "pilot_feedback", "WHERE note='메모 1234'") == 1, "only 강이수's"
    assert _count(workspace / "commercial_workspace.sqlite", "experiment_sessions", "WHERE session_id='exp-m'") == 0
    assert _count(workspace / "commercial_workspace.sqlite", "experiment_sessions", "WHERE session_id='exp-o'") == 1
    assert other.get("/api/workspace/experiments").status_code == 200
    assert other.get("/api/auth/session").json()["account"]["name"] == "강이수"


def test_the_deletion_is_recorded_by_category_and_count_without_a_name_id_or_password(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    member = _person(workspace, "최수진")
    _leave_data(workspace, "최수진", "exp-m")
    account = _store(workspace).account_by_name("최수진")
    assert member.post(ROUTE, json={"password": PASSWORD}).status_code == 200
    (record,) = _log(workspace)
    assert (record["scope"], record["via"], record["result"]) == ("account", "app", "deleted")
    assert record["deleted_at"].endswith("+00:00")
    metrics = next(item for item in record["categories"] if item["category"] == "pilot_metrics")
    assert metrics["rows"] == {"pilot_turns": 1, "pilot_feedback": 1}
    raw = (workspace.parent / acc.DELETION_LOG_NAME).read_text(encoding="utf-8")
    for secret in ("최수진", account.account_id, account.principal().principal_id, PASSWORD, "메모 1234"):
        assert secret not in raw


def test_a_second_request_with_the_same_session_is_refused_as_signed_out(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    member = _person(workspace, "최수진")
    token = member.session_cookie
    assert member.post(ROUTE, json={"password": PASSWORD}).status_code == 200
    replay = Device(headers={"cookie": f"{acc.SESSION_COOKIE_NAME}={token}"}).post(ROUTE, json={"password": PASSWORD})
    assert (replay.status_code, replay.json()) == (401, {"detail": "authentication_required"})
    assert len(_log(workspace)) == 1


# --- The lab's last administrator --------------------------------------------------


def test_the_last_administrator_of_a_lab_cannot_delete_the_account(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    admin = _person(workspace, "관리자", admin=True)
    _person(workspace, "최수진")
    _person(workspace, "다른연구실관리자", admin=True, lab="lab-b")  # another lab's administrator does not count
    _leave_data(workspace, "관리자", "exp-a")
    response = admin.post(ROUTE, json={"password": PASSWORD})
    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "last_admin"
    assert "관리자" in body["message_ko"] and "마지막" in body["message_ko"]
    assert "비밀번호" not in response.text
    assert _store(workspace).account_by_name("관리자") is not None
    assert _sessions(workspace) == 1
    assert admin.get("/api/auth/session").json()["signed_in"] is True, "refused, still signed in"
    assert _log(workspace) == []


def test_a_second_administrator_who_is_switched_off_does_not_count(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    first = _person(workspace, "관리자", admin=True)
    _person(workspace, "부관리자", admin=True)
    second = _store(workspace).account_by_name("부관리자")
    assert first.post(f"/api/auth/accounts/{second.account_id}/disabled", json={"disabled": True}).status_code == 200
    response = first.post(ROUTE, json={"password": PASSWORD})
    assert (response.status_code, response.json()["detail"]) == (409, "last_admin")


def test_with_another_active_administrator_one_may_go_and_then_the_other_is_the_last(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    first = _person(workspace, "관리자", admin=True)
    second = _person(workspace, "부관리자", admin=True)
    assert first.post(ROUTE, json={"password": PASSWORD}).status_code == 200
    response = second.post(ROUTE, json={"password": PASSWORD})
    assert (response.status_code, response.json()["detail"]) == (409, "last_admin")
    assert [item.name for item in _store(workspace).list_accounts()] == ["부관리자"]


# --- Shared things stay -----------------------------------------------------------


def test_the_lab_s_shared_protocol_stays_and_names_a_deleted_account(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    _person(workspace, "관리자", admin=True)
    member = _person(workspace, "최수진")
    accounts = _store(workspace)
    store = initialize_workspace_store(WorkspaceSettings(enabled=True, data_dir=workspace))
    try:
        store.bind_resource(accounts.account_by_name("최수진").principal(), "protocol_catalog", "protocol-shared")
    finally:
        store.close()
        accounts.close()
    assert member.post(ROUTE, json={"password": PASSWORD}).status_code == 200
    connection = sqlite3.connect(workspace / "commercial_workspace.sqlite")
    try:
        (owned,) = connection.execute(
            "SELECT COUNT(*) FROM resource_bindings WHERE resource_type='protocol_catalog'").fetchone()
        (name,) = connection.execute(
            "SELECT display_name FROM principals WHERE display_name LIKE '%삭제된 계정%'").fetchone()
    finally:
        connection.close()
    assert (owned, name) == (1, "(삭제된 계정)")


# --- When a store fails -------------------------------------------------------------


def test_a_failure_in_the_middle_leaves_the_account_switched_off_and_says_so(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    member = _person(workspace, "최수진")

    def broken(locations, account_id):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(server_module.accounts_module, "erase_account_data", broken)
    response = member.post(ROUTE, json={"password": PASSWORD})
    assert (response.status_code, response.json()) == (500, {"detail": "account_delete_incomplete"})
    account = _store(workspace).account_by_name("최수진")
    assert account is not None and account.disabled, "no sign-in until the operator finishes it"
    login = Device().post("/api/auth/login", json={"name": "최수진", "password": PASSWORD})
    assert login.status_code == 403
    (record,) = _log(workspace)
    assert record["result"] == "failed" and record["via"] == "app"
    assert "최수진" not in json.dumps(record, ensure_ascii=False)


def test_the_route_reads_where_the_stores_are_from_the_server_s_settings(monkeypatch, tmp_path):
    workspace = _pilot(monkeypatch, tmp_path)
    protocol = tmp_path / "pilot"
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_DATA_DIR", str(protocol))
    locations = server_module._account_data_locations()  # noqa: SLF001
    assert locations.workspace_dir == workspace
    assert locations.reports_db == protocol / "experiment_reports.sqlite"
    assert locations.catalog_db == protocol / "protocol_workspace.sqlite"
    assert locations.log_path == protocol / acc.DELETION_LOG_NAME
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "false")
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_ENABLED", "false")
    locations = server_module._account_data_locations()  # noqa: SLF001
    assert (locations.reports_db, locations.catalog_db) == (None, None)
