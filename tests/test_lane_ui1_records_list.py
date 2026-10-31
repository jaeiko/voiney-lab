"""The records list the screens read (lane UI1, item 3; person's test of 2026-10-31).

What happened: a run started at 13:40 KST on 10/31 was not found in the list,
and the list was not in time order. The run had been made in a workspace
experiment created on 10/28 and left ready (never started); the voice session
took it up again 3 days later (``session_recovered``), so the list showed the
10/28 creation time, and the list was ordered by ``updated_at`` -- that row at
the top with a 10/28 time, then 10/29 rows, then 10/28 rows again.

The list now names when each run's protocol started (``run_started_at``: the
latest record's start, else the experiment's own), is ordered by it with the
newest first, counts the values not yet confirmed (``unconfirmed_count``, a
stopped run with unconfirmed values stays listed), and says the time zone
the screen shows times in (``timezone``, VOINEY_LAB_REPORT_TIMEZONE, default
Asia/Seoul) -- the zone the notebook's Word and PDF use.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.test_lane_on_auth_api import Device, _configure, _invite
from voiney_lab import server as srv
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.workspace_store import initialize_workspace_store


def _set(path, sql: str, *values) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(sql, values)
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def lab(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORT_DB", str(tmp_path / "reports.sqlite"))
    monkeypatch.delenv("VOINEY_LAB_REPORT_TIMEZONE", raising=False)
    monkeypatch.setattr(srv, "_prepare_report_prose", lambda *a, **kw: "ready")
    device = Device()
    device.sign_up(_invite(tmp_path, organization_id="lab-a", admin=False), "owner")
    owner = srv._account_for_cookie(device.session_cookie).principal()
    workspace = initialize_workspace_store(srv._workspace_settings())
    workspace.bootstrap_principal(owner)
    store = ExperimentReportStore(tmp_path / "reports.sqlite")
    # (session, created, updated, report start or None, protocol title, field, status, unconfirmed values)
    plan = [
        # Made on 10/28 and left ready; its protocol ran on 10/31 04:40 UTC, stopped with two values unconfirmed.
        ("session-taken-up", "2026-10-28T07:38:40+00:00", "2026-10-31T04:48:23+00:00", "2026-10-31T04:40:22+00:00",
         "In-gel digestion", "bio", "stopped", 2),
        ("session-chem", "2026-10-29T13:45:13+00:00", "2026-10-29T13:51:40+00:00", "2026-10-29T13:45:25+00:00",
         "EAM 2.3 Analytical Solution Preparation", "chemistry", "completed", 0),
        ("session-battery", "2026-10-29T07:51:04+00:00", "2026-10-29T07:52:41+00:00", "2026-10-29T07:51:20+00:00",
         "Electrode slurry coating", "materials", "stopped", 1),
        ("session-early", "2026-10-28T05:24:16+00:00", "2026-10-28T05:25:11+00:00", None,
         "In-gel digestion", "bio", None, 0),
        # Run on 10/27 and touched again on 10/30 (stopped late): updated last but one, run first of all.
        ("session-touched-later", "2026-10-27T02:40:49+00:00", "2026-10-30T02:47:54+00:00", "2026-10-27T02:41:00+00:00",
         "Soil nutrient analysis (EU RADIANT SOP 005)", "chemistry", "stopped", 0),
    ]
    for session, created, updated, run_start, title, _field, status, unconfirmed in plan:
        workspace.start_experiment(owner, session_id=session, protocol_id=f"protocol-{session}",
                                   protocol_revision_id="revision-one")
        if run_start is None:
            continue
        report = store.open_report(session_id=session, protocol_id=f"protocol-{session}", protocol_title=title,
                                   protocol_revision="revision-one", protocol_sha256="a" * 64,
                                   readiness_status="ready", development_only=False)
        workspace.bind_resource(owner, "experiment_report", report["report_id"])
        for number in range(unconfirmed):
            store.append_event(report["report_id"], event_key=f"measurement-{number}", event_type="observation",
                               user_wording=f"{number + 2} mL", category="measurement",
                               confirmation_state="user_reported")
        store.finalize(report["report_id"], status=status, event_key="finished")
        _set(store.path, "UPDATE experiment_reports SET started_at=? WHERE report_id=?", run_start, report["report_id"])
    workspace.close()
    for session, created, updated, *_ in plan:
        _set(tmp_path / "commercial_workspace.sqlite",
             "UPDATE experiment_sessions SET started_at=?, updated_at=? WHERE session_id=?", created, updated, session)
    return device, store


def test_the_run_taken_up_later_is_listed_by_when_its_protocol_started_newest_first(lab) -> None:
    device, _ = lab
    answer = device.get("/api/workspace/experiments")
    assert answer.status_code == 200, answer.text
    rows = answer.json()["experiments"]
    assert [row["session_id"] for row in rows] == [
        "session-taken-up", "session-chem", "session-battery", "session-early", "session-touched-later"]
    assert rows[0]["run_started_at"] == "2026-10-31T04:40:22+00:00"
    # The experiment's own creation time is kept as it was; a row with no record starts at it.
    assert rows[0]["started_at"].startswith("2026-10-28T07:38:40")
    assert rows[3]["run_started_at"].startswith("2026-10-28T05:24:16")


def test_a_stopped_run_with_unconfirmed_values_stays_listed_with_its_count(lab) -> None:
    device, _ = lab
    rows = {row["session_id"]: row for row in device.get("/api/workspace/experiments").json()["experiments"]}
    assert rows["session-taken-up"]["unconfirmed_count"] == 2
    assert rows["session-taken-up"]["report_status"] == "stopped"
    assert rows["session-chem"]["unconfirmed_count"] == 0
    assert rows["session-battery"]["unconfirmed_count"] == 1
    # No record yet: nothing to count, said as unknown rather than zero.
    assert rows["session-early"]["unconfirmed_count"] is None


def test_the_list_names_the_time_zone_the_notebook_uses(lab, monkeypatch) -> None:
    device, _ = lab
    assert device.get("/api/workspace/experiments").json()["timezone"] == "Asia/Seoul"
    monkeypatch.setenv("VOINEY_LAB_REPORT_TIMEZONE", "Europe/Berlin")
    assert device.get("/api/workspace/experiments").json()["timezone"] == "Europe/Berlin"
    monkeypatch.setenv("VOINEY_LAB_REPORT_TIMEZONE", "Not/AZone")
    assert device.get("/api/workspace/experiments").json()["timezone"] == "Asia/Seoul"


def test_a_record_this_member_may_not_read_is_never_counted(lab) -> None:
    device, store = lab
    # The chemistry run's record moved to another lab (one record per session): the list does not read it -- the count
    # is unknown and the time is the experiment's own -- and still lists the experiment.
    report_id = store.list_reports(session_id="session-chem")[0]["report_id"]
    _set(store.path, "UPDATE experiment_reports SET organization_id='lab-b', owner_principal_id='someone', "
                     "ownership_migrated=1 WHERE report_id=?", report_id)
    rows = {row["session_id"]: row for row in device.get("/api/workspace/experiments").json()["experiments"]}
    assert rows["session-chem"]["unconfirmed_count"] is None
    assert rows["session-chem"]["report_status"] is None
    assert rows["session-chem"]["run_started_at"].startswith("2026-10-29T13:45:13")
    assert rows["session-taken-up"]["unconfirmed_count"] == 2
