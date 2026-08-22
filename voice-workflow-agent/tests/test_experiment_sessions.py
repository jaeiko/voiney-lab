from __future__ import annotations

import sqlite3

import pytest

from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.workspace_store import (
    SCHEMA,
    WORKSPACE_DATABASE_FILENAME,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
    WorkspaceSettings,
    initialize_workspace_store,
)


def _principal(name: str, tenant: str, role: Role = Role.RESEARCHER) -> Principal:
    return Principal(
        principal_id=f"principal-{name}",
        subject=f"test:{name}",
        organization_id=tenant,
        display_name=name,
        roles=frozenset({role}),
        authentication_method="test",
    )


def _store(tmp_path):
    return initialize_workspace_store(WorkspaceSettings(True, tmp_path))


def test_schema_v1_migrates_forward_without_losing_workspace_identity(tmp_path):
    path = tmp_path / WORKSPACE_DATABASE_FILENAME
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute(
        "INSERT INTO organizations VALUES(?,?,?)",
        ("tenant-a", "Existing tenant", "2026-08-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    store = _store(tmp_path)
    try:
        assert store._connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone()[0] == 2
        assert store._connection.execute(
            "SELECT name FROM organizations WHERE organization_id='tenant-a'"
        ).fetchone()[0] == "Existing tenant"
        assert store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_sessions'"
        ).fetchone()[0] == "experiment_sessions"
    finally:
        store.close()


def test_experiment_lifecycle_persists_exact_revision_steps_and_events(tmp_path):
    researcher = _principal("researcher", "tenant-a")
    store = _store(tmp_path)
    store.bootstrap_principal(researcher)
    started = store.start_experiment(
        researcher,
        session_id="experiment-session-1",
        protocol_id="in-gel-digestion",
        protocol_revision_id="approved-revision-7",
        current_step_id="step-1",
        current_step_label="1",
        voice_connection_id="voice-connection-1",
    )
    assert started["status"] == "ready"
    assert started["owner_principal_id"] == researcher.principal_id
    assert started["protocol_revision_id"] == "approved-revision-7"
    assert [event["event_type"] for event in started["events"]] == [
        "session_started"
    ]

    running = store.record_experiment_progress(
        researcher,
        "experiment-session-1",
        event_key="turn-1-protocol-started",
        event_type="protocol_started",
        step_id="step-1",
        step_label="1",
        payload={"authority": "curated_protocol"},
    )
    assert running["status"] == "in_progress"
    progressed = store.record_experiment_progress(
        researcher,
        "experiment-session-1",
        event_key="turn-2-step-1-completed",
        event_type="step_completed",
        step_id="step-1",
        step_label="1",
        next_step_id="step-2",
        next_step_label="2",
        mark_completed=True,
        payload={"authority": "curated_protocol"},
    )
    assert progressed["current_step_id"] == "step-2"
    assert [item["step_id"] for item in progressed["completed_steps"]] == [
        "step-1"
    ]

    paused = store.transition_experiment(
        researcher,
        "experiment-session-1",
        action="pause",
        expected_version=progressed["version"],
        event_key="ui-pause-1",
    )
    assert paused["status"] == "paused"
    store.close()

    reopened = _store(tmp_path)
    try:
        recovered = reopened.resume_experiment(
            researcher,
            "experiment-session-1",
            expected_version=paused["version"],
            protocol_id="in-gel-digestion",
            protocol_revision_id="approved-revision-7",
            voice_connection_id="voice-connection-2",
        )
        assert recovered["status"] == "in_progress"
        assert recovered["current_step_id"] == "step-2"
        assert recovered["last_voice_connection_id"] == "voice-connection-2"
        assert recovered["completed_steps"][0]["step_id"] == "step-1"
        assert [event["event_type"] for event in recovered["events"]] == [
            "session_started",
            "protocol_started",
            "step_completed",
            "session_paused",
            "session_resumed",
        ]

        completed = reopened.transition_experiment(
            researcher,
            "experiment-session-1",
            action="complete",
            expected_version=recovered["version"],
            event_key="workflow-complete-1",
        )
        assert completed["status"] == "completed"
        assert completed["ended_at"] is not None
        with pytest.raises(WorkspaceConflictError):
            reopened.resume_experiment(
                researcher,
                "experiment-session-1",
                expected_version=completed["version"],
                protocol_id="in-gel-digestion",
                protocol_revision_id="approved-revision-7",
                voice_connection_id="voice-connection-3",
            )
    finally:
        reopened.close()


def test_recovery_requires_fresh_version_and_the_original_protocol_revision(tmp_path):
    researcher = _principal("researcher", "tenant-a")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        session = store.start_experiment(
            researcher,
            session_id="experiment-session-2",
            protocol_id="protocol-a",
            protocol_revision_id="revision-a",
        )
        session = store.record_experiment_progress(
            researcher,
            session["session_id"],
            event_key="protocol-started",
            event_type="protocol_started",
            step_id=None,
            step_label=None,
        )
        paused = store.transition_experiment(
            researcher,
            session["session_id"],
            action="pause",
            expected_version=session["version"],
            event_key="pause-once",
        )
        with pytest.raises(WorkspaceConflictError, match="exact protocol revision"):
            store.resume_experiment(
                researcher,
                session["session_id"],
                expected_version=paused["version"],
                protocol_id="protocol-a",
                protocol_revision_id="revision-b",
                voice_connection_id="voice-2",
            )
        with pytest.raises(WorkspaceConflictError, match="changed"):
            store.resume_experiment(
                researcher,
                session["session_id"],
                expected_version=session["version"],
                protocol_id="protocol-a",
                protocol_revision_id="revision-a",
                voice_connection_id="voice-2",
            )
    finally:
        store.close()


def test_researchers_cannot_enumerate_other_users_or_tenants(tmp_path):
    owner = _principal("owner", "tenant-a")
    colleague = _principal("colleague", "tenant-a")
    outsider = _principal("outsider", "tenant-b")
    reviewer = _principal("reviewer", "tenant-a", Role.REVIEWER)
    store = _store(tmp_path)
    try:
        for principal in (owner, colleague, outsider, reviewer):
            store.bootstrap_principal(principal)
        session = store.start_experiment(
            owner,
            protocol_id="protocol-a",
            protocol_revision_id="revision-a",
        )
        assert len(store.list_experiments(owner)) == 1
        assert store.list_experiments(colleague) == ()
        assert len(store.list_experiments(reviewer)) == 1
        with pytest.raises(WorkspaceNotFoundError):
            store.get_experiment(colleague, session["session_id"])
        with pytest.raises(WorkspaceNotFoundError):
            store.get_experiment(outsider, session["session_id"])
    finally:
        store.close()
