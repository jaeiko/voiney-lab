from __future__ import annotations

import sqlite3

import pytest

from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.workspace_store import (
    MIGRATION_1_TO_2,
    MIGRATION_2_TO_3,
    MIGRATION_3_TO_4,
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
        ).fetchone()[0] == 6
        assert store._connection.execute(
            "SELECT name FROM organizations WHERE organization_id='tenant-a'"
        ).fetchone()[0] == "Existing tenant"
        assert store._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='experiment_sessions'"
        ).fetchone()[0] == "experiment_sessions"
    finally:
        store.close()


def test_schema_v2_migrates_observation_tables_and_preserves_sessions(tmp_path):
    path = tmp_path / WORKSPACE_DATABASE_FILENAME
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executescript(
        "BEGIN IMMEDIATE;\n" + MIGRATION_1_TO_2 + "\nCOMMIT;"
    )
    now = "2026-08-01T00:00:00+00:00"
    connection.execute(
        "INSERT INTO organizations VALUES(?,?,?)", ("tenant-a", "A", now)
    )
    connection.execute(
        "INSERT INTO principals VALUES(?,?,?,?)",
        ("principal-a", "test:a", "A", now),
    )
    connection.execute(
        "INSERT INTO experiment_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "experiment-v2",
            "tenant-a",
            "principal-a",
            "protocol-a",
            "revision-a",
            "ready",
            "step-1",
            "1",
            1,
            now,
            None,
            None,
            now,
            None,
        ),
    )
    connection.commit()
    connection.close()

    store = _store(tmp_path)
    try:
        assert store._connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone()[0] == 6
        assert store._connection.execute(
            "SELECT protocol_revision_id FROM experiment_sessions WHERE session_id='experiment-v2'"
        ).fetchone()[0] == "revision-a"
        names = {
            row[0]
            for row in store._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"experiment_observations", "experiment_evidence"} <= names
    finally:
        store.close()


def test_schema_v4_adds_session_provenance_without_losing_legacy_writeback(tmp_path):
    path = tmp_path / WORKSPACE_DATABASE_FILENAME
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    for migration in (MIGRATION_1_TO_2, MIGRATION_2_TO_3, MIGRATION_3_TO_4):
        connection.executescript("BEGIN IMMEDIATE;\n" + migration + "\nCOMMIT;")
    now = "2026-08-01T00:00:00+00:00"
    connection.execute(
        "INSERT INTO organizations VALUES(?,?,?)", ("tenant-a", "A", now)
    )
    connection.execute(
        "INSERT INTO principals VALUES(?,?,?,?)",
        ("principal-a", "test:a", "A", now),
    )
    connection.execute(
        "INSERT INTO memberships VALUES(?,?,?,?,?)",
        ("tenant-a", "principal-a", "researcher", 1, now),
    )
    connection.execute(
        "INSERT INTO protocol_families VALUES(?,?,?,?,?)",
        ("family-a", "tenant-a", "Protocol", "principal-a", now),
    )
    connection.execute(
        """INSERT INTO protocol_sources VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            "source-a",
            "tenant-a",
            "local_pdf",
            "source.pdf",
            "v1",
            "a" * 64,
            None,
            "{}",
            now,
        ),
    )
    connection.execute(
        """INSERT INTO protocol_lineage_revisions VALUES(
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "lineage-a",
            "family-a",
            "tenant-a",
            1,
            None,
            "source-a",
            "principal-a",
            now,
            "Initial",
            "b" * 64,
            "a" * 64,
            "en",
            "original",
            "{}",
        ),
    )
    connection.execute(
        "INSERT INTO connector_configurations VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "connector-a",
            "tenant-a",
            "elabftw",
            "ELN",
            "secret://tenant/eln",
            None,
            '["https://eln.example.test"]',
            1,
            now,
        ),
    )
    connection.execute(
        """INSERT INTO eln_writeback_requests VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            "tenant-a",
            "legacy-request",
            "connector-a",
            "report-a",
            "lineage-a",
            "principal-a",
            "completed",
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()

    store = _store(tmp_path)
    try:
        assert store._connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone()[0] == 6
        legacy = store._connection.execute(
            """SELECT report_id,experiment_session_id
            FROM eln_writeback_requests WHERE idempotency_key='legacy-request'"""
        ).fetchone()
        assert tuple(legacy) == ("report-a", None)
        migrated_connector = store._connection.execute(
            """SELECT enabled,validation_status,last_checked_at,last_failure_code
            FROM connector_configurations WHERE connector_id='connector-a'"""
        ).fetchone()
        assert tuple(migrated_connector) == (1, "untested", None, None)
        assert store._connection.execute(
            "SELECT name FROM sqlite_master WHERE name='admin_audit_events'"
        ).fetchone()[0] == "admin_audit_events"
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
        expected_version=started["version"],
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
        expected_version=running["version"],
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
            expected_version=session["version"],
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


def test_progress_requires_fresh_version_but_exact_replay_is_idempotent(tmp_path):
    researcher = _principal("researcher", "tenant-a")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        created = store.start_experiment(
            researcher,
            session_id="experiment-progress-lock-1",
            protocol_id="protocol-a",
            protocol_revision_id="revision-a",
            current_step_id="step-1",
            current_step_label="1",
        )
        running = store.record_experiment_progress(
            researcher,
            created["session_id"],
            expected_version=created["version"],
            event_key="voice-1-start",
            event_type="protocol_started",
            step_id="step-1",
            step_label="1",
            payload={"authority": "curated_protocol"},
        )
        replayed = store.record_experiment_progress(
            researcher,
            created["session_id"],
            expected_version=created["version"],
            event_key="voice-1-start",
            event_type="protocol_started",
            step_id="step-1",
            step_label="1",
            payload={"authority": "curated_protocol"},
        )
        assert replayed["version"] == running["version"]
        assert [
            event["event_key"] for event in replayed["events"]
        ].count("voice-1-start") == 1

        with pytest.raises(WorkspaceConflictError, match="changed"):
            store.record_experiment_progress(
                researcher,
                created["session_id"],
                expected_version=created["version"],
                event_key="voice-2-complete",
                event_type="step_completed",
                step_id="step-1",
                step_label="1",
                next_step_id="step-2",
                next_step_label="2",
                mark_completed=True,
            )
        unchanged = store.get_experiment(researcher, created["session_id"])
        assert unchanged["version"] == running["version"]
        assert unchanged["current_step_id"] == "step-1"
        assert unchanged["completed_steps"] == []

        with pytest.raises(WorkspaceConflictError, match="idempotency key"):
            store.record_experiment_progress(
                researcher,
                created["session_id"],
                expected_version=running["version"],
                event_key="voice-1-start",
                event_type="step_advanced",
                step_id="step-1",
                step_label="1",
            )
    finally:
        store.close()


def test_progress_allows_same_voice_timeline_capture_but_fences_recovered_voice(tmp_path):
    researcher = _principal("researcher", "tenant-a")
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        created = store.start_experiment(
            researcher,
            session_id="experiment-progress-capture-1",
            protocol_id="protocol-a",
            protocol_revision_id="revision-a",
            current_step_id="step-1",
            current_step_label="1",
            voice_connection_id="voice-1",
        )
        running = store.record_experiment_progress(
            researcher,
            created["session_id"],
            expected_version=created["version"],
            expected_voice_connection_id="voice-1",
            event_key="voice-1-start",
            event_type="protocol_started",
            step_id="step-1",
            step_label="1",
        )
        store.record_observation(
            researcher,
            created["session_id"],
            event_key="manual-observation-between-turns",
            content="Opaque observation captured between voice turns.",
            category="note",
            capture_source="manual",
            protocol_step_id="step-1",
        )
        captured = store.get_experiment(researcher, created["session_id"])
        assert captured["version"] == running["version"] + 1

        progressed = store.record_experiment_progress(
            researcher,
            created["session_id"],
            expected_version=running["version"],
            expected_voice_connection_id="voice-1",
            event_key="voice-2-complete",
            event_type="step_completed",
            step_id="step-1",
            step_label="1",
            next_step_id="step-2",
            next_step_label="2",
            mark_completed=True,
        )
        assert progressed["current_step_id"] == "step-2"
        assert progressed["version"] == captured["version"] + 1

        recovered = store.resume_experiment(
            researcher,
            created["session_id"],
            expected_version=progressed["version"],
            protocol_id="protocol-a",
            protocol_revision_id="revision-a",
            voice_connection_id="voice-2",
        )
        timeline = store.experiment_timeline(researcher, created["session_id"])
        assert timeline["recovery"] == {
            "eligible": True,
            "last_event_type": "session_recovered",
            "restored": {
                "protocol_id": "protocol-a",
                "protocol_revision_id": "revision-a",
                "current_step_id": "step-2",
                "current_step_label": "2",
                "completed_step_count": 1,
            },
            "not_restored": [
                "pending_confirmations",
                "conversation_history",
                "active_timers",
            ],
            "next_action": "resume_voice_session",
        }
        with pytest.raises(WorkspaceConflictError, match="changed"):
            store.record_experiment_progress(
                researcher,
                created["session_id"],
                expected_version=progressed["version"],
                expected_voice_connection_id="voice-1",
                event_key="stale-voice-complete",
                event_type="step_completed",
                step_id="step-2",
                step_label="2",
                next_step_id="step-3",
                next_step_label="3",
                mark_completed=True,
            )
        unchanged = store.get_experiment(researcher, created["session_id"])
        assert unchanged["version"] == recovered["version"]
        assert unchanged["current_step_id"] == "step-2"
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


def test_observations_evidence_and_reviewer_actions_form_separate_timeline(tmp_path):
    researcher = _principal("researcher", "tenant-a")
    reviewer = _principal("reviewer", "tenant-a", Role.REVIEWER)
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        store.bootstrap_principal(reviewer)
        session = store.start_experiment(
            researcher,
            session_id="experiment-timeline-1",
            protocol_id="protocol-a",
            protocol_revision_id="revision-a",
            current_step_id="step-1",
            current_step_label="1",
        )
        store.record_experiment_progress(
            researcher,
            session["session_id"],
            expected_version=session["version"],
            event_key="protocol-start",
            event_type="protocol_started",
            step_id="step-1",
            step_label="1",
        )
        with pytest.raises(WorkspaceConflictError, match="idempotency key"):
            store.record_observation(
                researcher,
                session["session_id"],
                event_key="session-started",
                content="Must not overwrite the lifecycle event.",
                category="note",
                capture_source="manual",
            )
        observation = store.record_observation(
            researcher,
            session["session_id"],
            event_key="voice-1-observation",
            content="The sample looks different from the start.",
            category="appearance",
            capture_source="voice",
        )
        assert observation["knowledge_effect"] == "observation_only"
        replay = store.record_observation(
            researcher,
            session["session_id"],
            event_key="voice-1-observation",
            content="The sample looks different from the start.",
            category="appearance",
            capture_source="voice",
        )
        assert replay["observation_id"] == observation["observation_id"]

        evidence = store.record_evidence(
            researcher,
            session["session_id"],
            event_key="attachment-1",
            evidence_kind="image",
            original_filename="sample.jpg",
            media_type="image/jpeg",
            byte_size=1024,
            sha256="a" * 64,
            storage_reference="evidence/tenant/session/a.jpg",
        )
        assert evidence["interpretation_status"] == "not_interpreted"
        store.record_experiment_review_action(
            reviewer,
            session["session_id"],
            event_key="review-1",
            action="acknowledged",
            comment="Observation reviewed; no SOP change was made.",
        )

        timeline = store.experiment_timeline(researcher, session["session_id"])
        assert timeline["observation_count"] == 1
        assert timeline["evidence_count"] == 1
        assert timeline["recovery"] == {
            "eligible": True,
            "last_event_type": None,
            "restored": {
                "protocol_id": "protocol-a",
                "protocol_revision_id": "revision-a",
                "current_step_id": "step-1",
                "current_step_label": "1",
                "completed_step_count": 0,
            },
            "not_restored": [
                "pending_confirmations",
                "conversation_history",
                "active_timers",
            ],
            "next_action": "resume_voice_session",
        }
        assert timeline["separation"] == {
            "observations_are_instructions": False,
            "evidence_autonomously_interpreted": False,
            "approved_protocol_knowledge_unchanged": True,
        }
        events = [item["event_type"] for item in timeline["timeline"]]
        assert events == [
            "session_started",
            "protocol_started",
            "observation_recorded",
            "evidence_attached",
            "reviewer_action",
        ]
        observed = next(
            item for item in timeline["timeline"]
            if item["event_type"] == "observation_recorded"
        )
        assert observed["observation"]["content"].startswith("The sample")
        attached = next(
            item for item in timeline["timeline"]
            if item["event_type"] == "evidence_attached"
        )
        assert attached["evidence"]["interpretation_status"] == "not_interpreted"
        assert "storage_reference" not in attached["evidence"]

        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._connection.execute(
                "UPDATE experiment_observations SET content='instruction'"
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._connection.execute("DELETE FROM experiment_evidence")
    finally:
        store.close()
