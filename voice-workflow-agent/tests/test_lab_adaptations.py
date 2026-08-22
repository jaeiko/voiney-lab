from __future__ import annotations

import hashlib
import sqlite3

import pytest

from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.workspace_store import (
    MIGRATION_1_TO_2,
    MIGRATION_2_TO_3,
    SCHEMA,
    WORKSPACE_DATABASE_FILENAME,
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceSettings,
    initialize_workspace_store,
)


def _principal(name: str, tenant: str, role: Role) -> Principal:
    return Principal(
        principal_id=f"principal-{name}",
        subject=f"test:{name}",
        organization_id=tenant,
        display_name=name.title(),
        roles=frozenset({role}),
        authentication_method="test",
    )


def _store(tmp_path):
    return initialize_workspace_store(WorkspaceSettings(True, tmp_path))


def _base_revision(store, researcher, *, source_status="In development"):
    family = store.create_protocol_family(
        researcher, title="Local protein digestion"
    )
    source = store.register_source(
        researcher,
        connector_kind="protocols_io",
        external_id="protocols.io/example",
        version_identity="v1",
        source_hash=hashlib.sha256(b"original-protocol").hexdigest(),
        canonical_url="https://www.protocols.io/example",
        metadata={"source_status": source_status},
    )
    revision = store.add_protocol_revision(
        researcher,
        family_id=family.family_id,
        source_id=source.source_id,
        change_summary="Immutable original import",
        content={
            "steps": [
                {"step_id": "step-1", "instruction": "Use centrifuge A"},
                {"step_id": "step-2", "instruction": "Add reagent X"},
            ],
            "warnings": ["Follow the approved hazard controls"],
        },
    )
    return family, source, revision


def _changes():
    return (
        {
            "kind": "equipment_difference",
            "protocol_step_id": "step-1",
            "summary": "Use the locally qualified centrifuge",
            "rationale": "The original model is not installed in this lab.",
            "original_value": "centrifuge A",
            "adapted_value": "qualified centrifuge B",
        },
        {
            "kind": "reagent_substitution",
            "protocol_step_id": "step-2",
            "summary": "Local reagent catalog identity",
            "rationale": "Equivalent substitution requires reviewer verification.",
            "original_value": "reagent X",
            "adapted_value": "reagent X, local catalog 42",
        },
        {
            "kind": "troubleshooting_tip",
            "protocol_step_id": "step-2",
            "summary": "Record unexpected cloudiness",
            "rationale": "Preserve a local review cue without changing completion.",
            "original_value": None,
            "adapted_value": "Pause and record a deviation for reviewer follow-up.",
        },
    )


def test_schema_v3_migrates_adaptation_table_and_preserves_lineage(tmp_path):
    path = tmp_path / WORKSPACE_DATABASE_FILENAME
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executescript(
        "BEGIN IMMEDIATE;\n" + MIGRATION_1_TO_2 + "\nCOMMIT;"
    )
    connection.executescript(
        "BEGIN IMMEDIATE;\n" + MIGRATION_2_TO_3 + "\nCOMMIT;"
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
        "INSERT INTO protocol_families VALUES(?,?,?,?,?)",
        ("family-a", "tenant-a", "Original", "principal-a", now),
    )
    connection.execute(
        "INSERT INTO protocol_sources VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "source-a",
            "tenant-a",
            "local_pdf",
            "upload:a.pdf",
            "sha-a",
            "a" * 64,
            None,
            "{}",
            now,
        ),
    )
    connection.execute(
        "INSERT INTO protocol_lineage_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "revision-a",
            "family-a",
            "tenant-a",
            1,
            None,
            "source-a",
            "principal-a",
            now,
            "original",
            "b" * 64,
            "a" * 64,
            "en",
            "original",
            '{"steps":[]}',
        ),
    )
    connection.commit()
    connection.close()

    store = _store(tmp_path)
    try:
        assert store._connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone()[0] == 4
        assert store._connection.execute(
            "SELECT content_json FROM protocol_lineage_revisions WHERE revision_id='revision-a'"
        ).fetchone()[0] == '{"steps":[]}'
        assert store._connection.execute(
            "SELECT name FROM sqlite_master WHERE name='protocol_adaptation_revisions'"
        ).fetchone()[0] == "protocol_adaptation_revisions"
    finally:
        store.close()


def test_adaptation_preserves_original_and_requires_reviewer_approval(tmp_path):
    researcher = _principal("researcher", "tenant-a", Role.RESEARCHER)
    reviewer = _principal("reviewer", "tenant-a", Role.REVIEWER)
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        store.bootstrap_principal(reviewer)
        _, _, original = _base_revision(store, researcher)

        with pytest.raises(WorkspaceConflictError):
            store.record_approval(
                reviewer,
                revision_id=original.revision_id,
                action="approved",
                comment="Development source must not execute directly.",
                idempotency_key="unsafe-original-approval",
            )

        adaptation = store.create_lab_adaptation(
            researcher,
            base_revision_id=original.revision_id,
            changes=_changes(),
            change_summary="Local equipment, reagent, and troubleshooting draft",
        )
        assert adaptation["base_revision_id"] == original.revision_id
        assert adaptation["review_state"] == "review_required"
        assert adaptation["executable"] is False
        assert adaptation["immutable"] is True
        assert adaptation["original_protocol_unchanged"] is True

        unchanged = store.get_revision(researcher, original.revision_id)
        assert unchanged.content == original.content
        adapted = store.get_revision(
            researcher, adaptation["adapted_revision_id"]
        )
        assert adapted.parent_revision_id == original.revision_id
        assert adapted.content["lab_adaptation"]["review_state"] == "review_required"
        assert adapted.content["steps"] == original.content["steps"]

        approved = store.record_approval(
            reviewer,
            revision_id=adapted.revision_id,
            action="approved",
            comment="Equipment qualification, substitution, and hazards reviewed.",
            idempotency_key="approve-adaptation-1",
        )
        assert approved.action == "approved"
        after = store.lab_adaptation(reviewer, adapted.revision_id)
        assert after["review_state"] == "approved"
        assert after["executable"] is True

        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            store._connection.execute(
                "UPDATE protocol_adaptation_revisions SET changes_json='[]'"
            )
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            store._connection.execute("DELETE FROM protocol_adaptation_revisions")
    finally:
        store.close()


def test_adaptation_validation_stale_parent_and_tenant_scope(tmp_path):
    researcher = _principal("researcher", "tenant-a", Role.RESEARCHER)
    outsider = _principal("outsider", "tenant-b", Role.REVIEWER)
    store = _store(tmp_path)
    try:
        store.bootstrap_principal(researcher)
        store.bootstrap_principal(outsider)
        family, source, original = _base_revision(
            store, researcher, source_status="Published"
        )
        with pytest.raises(WorkspaceError, match="kind"):
            store.create_lab_adaptation(
                researcher,
                base_revision_id=original.revision_id,
                changes=({**_changes()[0], "kind": "unsafe_override"},),
                change_summary="Unsafe kind",
            )
        with pytest.raises(WorkspaceError, match="before/after"):
            store.create_lab_adaptation(
                researcher,
                base_revision_id=original.revision_id,
                changes=(
                    {
                        **_changes()[0],
                        "adapted_value": _changes()[0]["original_value"],
                    },
                ),
                change_summary="No actual difference",
            )

        newer = store.add_protocol_revision(
            researcher,
            family_id=family.family_id,
            source_id=source.source_id,
            parent_revision_id=original.revision_id,
            change_summary="New source revision",
            content={"steps": [{"step_id": "step-1"}]},
        )
        assert newer.revision_number == 2
        with pytest.raises(WorkspaceConflictError, match="stale"):
            store.create_lab_adaptation(
                researcher,
                base_revision_id=original.revision_id,
                changes=_changes(),
                change_summary="Stale adaptation",
            )
        with pytest.raises(WorkspaceNotFoundError):
            store.lab_adaptation(outsider, newer.revision_id)
    finally:
        store.close()
