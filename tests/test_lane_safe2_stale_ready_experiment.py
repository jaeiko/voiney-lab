"""Lane SAFE2, item 4 (person's decision 2026-10-31): an old, never-started experiment is not taken up.

The 10/31 test's run was made inside a workspace experiment created on
10/28 and left ``ready``; the voice session took it up three days later
(``session_recovered``), so the records list showed the wrong time (lane
UI1 fixed the list). Now a ``ready`` experiment older than
``VOINEY_LAB_READY_EXPERIMENT_MAX_AGE_HOURS`` (default 12) is left where it
is: the voice session makes a new experiment, and the active list the home
offers to continue leaves it out. A run in progress or paused is continued
as before, whatever its age. No provider is called; the workspace is a
temporary SQLite file.
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

import pytest

from tests.lane_n_support import PROTOCOL_ID, VoiceNotesHarness, error_message, ready_event
from tests.test_lane_on_auth_api import Device, _configure, _invite
from voiney_lab import server as srv
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store


def _age(path, session_id: str, hours: float) -> None:
    moment = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE experiment_sessions SET started_at=?, updated_at=? WHERE session_id=?",
                           (moment, moment, session_id))
        connection.commit()
    finally:
        connection.close()


class VoiceSessionTests(VoiceNotesHarness, unittest.TestCase):
    """The production path: session.start naming the experiment to continue."""

    def _ready_experiment(self, session_id: str, *, hours_old: float) -> dict:
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            state = store.start_experiment(
                self.principal, session_id=session_id, protocol_id=PROTOCOL_ID,
                protocol_revision_id=self.fixture.revision_id,
                current_step_id=self.fixture.steps[0].step_id,
                current_step_label=self.fixture.steps[0].source_label,
            )
        finally:
            store.close()
        _age(self.workspace_dir / "commercial_workspace.sqlite", session_id, hours_old)
        return state

    def test_a_ready_experiment_older_than_the_limit_is_left_and_a_new_one_made(self) -> None:
        stale = self._ready_experiment("stale-ready", hours_old=13)
        socket = self.turns(
            "프로토콜 시작해줘",
            start={"experiment_session_id": "stale-ready", "experiment_session_version": int(stale["version"])},
        )
        self.assertIsNone(error_message(socket))
        ready = ready_event(socket)
        self.assertNotEqual(ready.get("experiment_session_id"), "stale-ready")
        self.assertFalse(ready.get("experiment_recovered"))
        self.assertTrue(ready.get("stale_ready_replaced"))
        self.assertEqual(self.experiment("stale-ready")["status"], "ready", "the old shell is left as it was")
        self.assertEqual(self.experiment()["status"], "in_progress")
        self.assertNotIn(
            "session_recovered",
            [event["event_type"] for event in self.experiment("stale-ready")["events"]])

    def test_a_ready_experiment_within_the_limit_is_still_taken_up(self) -> None:
        fresh = self._ready_experiment("fresh-ready", hours_old=1)
        socket = self.turns(
            "프로토콜 시작해줘",
            start={"experiment_session_id": "fresh-ready", "experiment_session_version": int(fresh["version"])},
        )
        self.assertIsNone(error_message(socket))
        ready = ready_event(socket)
        self.assertEqual(ready.get("experiment_session_id"), "fresh-ready")
        self.assertTrue(ready.get("experiment_recovered"))
        self.assertFalse(ready.get("stale_ready_replaced"))
        self.assertEqual(self.experiment("fresh-ready")["status"], "in_progress")

    def test_a_run_in_progress_is_continued_whatever_its_age(self) -> None:
        self.turns("프로토콜 시작해줘", "1단계 완료했어")
        session_id = self.listener.session_id
        before = self.experiment(session_id)
        self.assertEqual(before["status"], "in_progress")
        _age(self.workspace_dir / "commercial_workspace.sqlite", session_id, 40)
        socket = self.turns(
            "2단계 완료했어",
            start={"experiment_session_id": session_id, "experiment_session_version": int(before["version"])},
        )
        self.assertIsNone(error_message(socket))
        self.assertTrue(ready_event(socket).get("experiment_recovered"))
        self.assertEqual(ready_event(socket).get("experiment_session_id"), session_id)
        self.assertEqual(
            [item["step_id"] for item in self.experiment(session_id)["completed_steps"]], ["step-1", "step-2"])

    def test_the_limit_is_a_setting_read_in_hours(self) -> None:
        with pytest.MonkeyPatch.context() as patched:
            patched.setenv("VOINEY_LAB_READY_EXPERIMENT_MAX_AGE_HOURS", "48")
            self.environment["VOINEY_LAB_READY_EXPERIMENT_MAX_AGE_HOURS"] = "48"
            day_old = self._ready_experiment("day-old", hours_old=30)
            socket = self.turns(
                "프로토콜 시작해줘",
                start={"experiment_session_id": "day-old", "experiment_session_version": int(day_old["version"])},
            )
        self.assertEqual(ready_event(socket).get("experiment_session_id"), "day-old")
        self.assertEqual(srv.ready_experiment_max_age_hours({"VOINEY_LAB_READY_EXPERIMENT_MAX_AGE_HOURS": "48"}), 48)
        self.assertEqual(srv.ready_experiment_max_age_hours({}), 12)
        for bad in ("0", "-1", "abc", "100000"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                srv.ready_experiment_max_age_hours({"VOINEY_LAB_READY_EXPERIMENT_MAX_AGE_HOURS": bad})


@pytest.fixture
def lab(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    device = Device()
    device.sign_up(_invite(tmp_path, organization_id="lab-a", admin=False), "owner")
    owner = srv._account_for_cookie(device.session_cookie).principal()
    workspace = initialize_workspace_store(srv._workspace_settings())
    workspace.bootstrap_principal(owner)
    for session, hours in (("ready-old", 36), ("ready-new", 2), ("running-old", 36)):
        workspace.start_experiment(owner, session_id=session, protocol_id=f"protocol-{session}",
                                   protocol_revision_id="revision-one")
    workspace.transition_experiment(owner, "running-old", action="resume", expected_version=1, event_key="go")
    workspace.close()
    for session, hours in (("ready-old", 36), ("ready-new", 2), ("running-old", 36)):
        _age(tmp_path / "commercial_workspace.sqlite", session, hours)
    return device


def test_the_active_list_the_home_continues_from_leaves_out_a_stale_ready_experiment(lab) -> None:
    active = lab.get("/api/workspace/experiments?active_only=true")
    assert active.status_code == 200, active.text
    assert {row["session_id"] for row in active.json()["experiments"]} == {"ready-new", "running-old"}
    everything = lab.get("/api/workspace/experiments")
    assert everything.status_code == 200
    assert {row["session_id"] for row in everything.json()["experiments"]} == {"ready-old", "ready-new", "running-old"}
