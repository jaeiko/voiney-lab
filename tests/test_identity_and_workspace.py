from __future__ import annotations

import hashlib
import sqlite3

import pytest

from voice_workflow_agent.identity import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    DevIdentityProvider,
    IdentityConfigurationError,
    IdentityResolver,
    OidcSettings,
    Permission,
    Principal,
    Role,
    principal_from_oidc_claims,
    require_permission,
)
from voice_workflow_agent.workspace_store import (
    ApprovalReplayError,
    TranslationIntegrityError,
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceSettings,
    initialize_workspace_store,
)


def _principal(
    name: str,
    organization_id: str,
    *roles: Role,
) -> Principal:
    return Principal(
        principal_id=f"principal-{name}",
        subject=f"test:{name}",
        organization_id=organization_id,
        display_name=name,
        roles=frozenset(roles),
        authentication_method="test",
    )


@pytest.fixture
def workspace(tmp_path):
    store = initialize_workspace_store(
        WorkspaceSettings(enabled=True, data_dir=tmp_path, analytics_retention_days=90)
    )
    try:
        yield store
    finally:
        store.close()


def _revision(store, principal: Principal, *, status: str = "published"):
    family = store.create_protocol_family(principal, title="ANKOM fiber analysis")
    source = store.register_source(
        principal,
        connector_kind="protocols_io",
        external_id="10.17504/protocols.io.yinfude",
        version_identity="v1",
        source_hash=hashlib.sha256(b"immutable source").hexdigest(),
        canonical_url="https://www.protocols.io/view/yinfude",
        metadata={
            "doi": "10.17504/protocols.io.yinfude",
            "source_status": status,
            "authors": ["Source Author"],
            "license": "CC BY",
        },
    )
    revision = store.add_protocol_revision(
        principal,
        family_id=family.family_id,
        source_id=source.source_id,
        change_summary="Initial immutable import",
        content={
            "steps": ["Incubate 10 min", "Add 72% sulfuric acid"],
            "warnings": ["Concentrated acid"],
        },
    )
    return family, source, revision


def test_rbac_permissions_are_centralized_and_default_deny():
    researcher = _principal("researcher", "tenant-a", Role.RESEARCHER)
    reviewer = _principal("reviewer", "tenant-a", Role.REVIEWER)

    require_permission(researcher, Permission.PROTOCOL_EXECUTE)
    require_permission(reviewer, Permission.PROTOCOL_APPROVE)
    with pytest.raises(AuthorizationDeniedError):
        require_permission(researcher, Permission.PROTOCOL_APPROVE)
    with pytest.raises(AuthorizationDeniedError):
        require_permission(reviewer, Permission.CONNECTOR_MANAGE)


def test_operational_identity_requires_oidc_and_dev_profiles_are_allowlisted():
    with pytest.raises(IdentityConfigurationError):
        IdentityResolver(usage_scope="operational", oidc_settings=None)

    resolver = IdentityResolver(
        usage_scope="development",
        oidc_settings=None,
        dev_provider=DevIdentityProvider.from_environment({}),
    )
    assert resolver.resolve(None).principal_id == "dev-local-admin"
    with pytest.raises(AuthenticationRequiredError):
        resolver.resolve(None, dev_profile_id="client-invented-admin")


def test_oidc_claims_accept_opaque_subject_but_hash_local_identifier():
    settings = OidcSettings(
        issuer="https://identity.example.test",
        audience="voice-workflow",
        jwks_url="https://identity.example.test/.well-known/jwks.json",
    )
    principal = principal_from_oidc_claims(
        {
            "sub": "auth0|user/3f52a+opaque=value",
            "organization_id": "tenant-a",
            "roles": ["researcher", "untrusted-client-role"],
            "name": "Researcher A",
        },
        settings,
    )

    assert principal.subject == "auth0|user/3f52a+opaque=value"
    assert principal.principal_id.startswith("oidc:")
    assert "opaque" not in principal.principal_id
    assert principal.roles == frozenset({Role.RESEARCHER})

    other_issuer = principal_from_oidc_claims(
        {
            "sub": "auth0|user/3f52a+opaque=value",
            "organization_id": "tenant-a",
            "roles": ["researcher"],
        },
        OidcSettings(
            issuer="https://other-identity.example.test",
            audience="voice-workflow",
            jwks_url="https://other-identity.example.test/.well-known/jwks.json",
        ),
    )
    assert other_issuer.principal_id != principal.principal_id


def test_protocol_lineage_diff_translation_approval_and_revocation(workspace):
    admin = _principal("admin", "tenant-a", Role.LAB_ADMIN)
    workspace.bootstrap_principal(admin)
    family, source, first = _revision(workspace, admin)
    second = workspace.add_protocol_revision(
        admin,
        family_id=family.family_id,
        source_id=source.source_id,
        parent_revision_id=first.revision_id,
        change_summary="Clarify acid warning",
        content={
            "steps": ["Incubate 10 min", "Add 72% sulfuric acid in hood"],
            "warnings": ["Concentrated acid; use approved controls"],
        },
    )

    difference = workspace.revision_diff(admin, second.revision_id)
    assert difference["parent_revision_id"] == first.revision_id
    assert any("hood" in line for line in difference["lines"])
    assert difference["review_context"]["protocol_title"] == "ANKOM fiber analysis"
    assert difference["review_context"]["version_label"] == "v2"
    assert difference["review_context"]["requester_display_name"] == "admin"
    assert difference["review_context"]["change_reason"] == "Clarify acid warning"
    assert difference["change_summary"]["changed_fields"] == ["steps", "warnings"]
    assert difference["experimental_impact"]["status"] == "not_assessed"
    assert difference["risk"] == {
        "level": "not_assessed",
        "source_signal": None,
        "summary": "No reviewed risk level is stored for this revision.",
    }
    assert difference["decision_state"]["allowed_actions"] == [
        "approved",
        "rejected",
    ]
    assert difference["history"] == []

    translation_id = workspace.add_translation(
        admin,
        revision_id=second.revision_id,
        language="ko",
        original_text="Incubate 10 min, then add 72% sulfuric acid.",
        translated_text="10 min 동안 반응한 뒤 72% 황산을 추가합니다.",
        status="reviewed",
    )
    assert translation_id.startswith("translation-")
    with pytest.raises(TranslationIntegrityError):
        workspace.add_translation(
            admin,
            revision_id=second.revision_id,
            language="ko",
            original_text="Add 72% sulfuric acid.",
            translated_text="70% 황산을 추가합니다.",
        )

    approved = workspace.record_approval(
        admin,
        revision_id=second.revision_id,
        action="approved",
        comment="Hazards and source evidence reviewed.",
        idempotency_key="approve-second-v1",
    )
    assert approved.actor_role == "lab_admin"
    assert workspace.revision_operational_state(admin, second.revision_id)[
        "available_for_new_operational_sessions"
    ]
    approved_packet = workspace.revision_diff(admin, second.revision_id)
    assert approved_packet["decision_state"]["allowed_actions"] == ["revoked"]
    assert approved_packet["history"][0]["actor_display_name"] == "admin"
    assert approved_packet["history"][0]["affected_version"] == "v2"
    inbox_item = next(
        item
        for item in workspace.source_inbox(admin)
        if item["revision_id"] == second.revision_id
    )
    assert inbox_item["status"] == "resolved"
    assert inbox_item["protocol_title"] == "ANKOM fiber analysis"
    assert inbox_item["version_label"] == "v2"
    assert inbox_item["request_reason"] == "Clarify acid warning"
    assert inbox_item["risk_level"] == "not_assessed"
    with pytest.raises(ApprovalReplayError):
        workspace.record_approval(
            admin,
            revision_id=second.revision_id,
            action="approved",
            comment="Replay attempt.",
            idempotency_key="approve-second-v1",
        )
    with pytest.raises(WorkspaceConflictError, match="stale"):
        workspace.record_approval(
            admin,
            revision_id=second.revision_id,
            action="approved",
            comment="A second approval must not overwrite current state.",
            idempotency_key="approve-second-v2",
        )

    workspace.record_approval(
        admin,
        revision_id=second.revision_id,
        action="revoked",
        comment="Superseded pending a new revision.",
        idempotency_key="revoke-second-v1",
        replacement_revision_id=first.revision_id,
    )
    state = workspace.revision_operational_state(admin, second.revision_id)
    assert state["state"] == "revoked"
    assert state["available_for_new_operational_sessions"] is False
    assert len(state["history"]) == 2
    with pytest.raises(WorkspaceConflictError, match="stale"):
        workspace.record_approval(
            admin,
            revision_id=second.revision_id,
            action="approved",
            comment="A revoked revision needs a new revision, not a new decision.",
            idempotency_key="approve-revoked-v1",
        )

    third = workspace.add_protocol_revision(
        admin,
        family_id=family.family_id,
        source_id=source.source_id,
        parent_revision_id=second.revision_id,
        change_summary="Requester must clarify the exposure control",
        content=second.content,
    )
    workspace.record_approval(
        admin,
        revision_id=third.revision_id,
        action="rejected",
        comment="Exposure control is not sufficiently specified.",
        idempotency_key="request-revision-third-v1",
    )
    with pytest.raises(WorkspaceConflictError, match="stale"):
        workspace.record_approval(
            admin,
            revision_id=third.revision_id,
            action="approved",
            comment="A rejected immutable revision cannot later be approved.",
            idempotency_key="approve-rejected-third-v1",
        )


def test_in_development_source_cannot_be_operationally_approved(workspace):
    reviewer = _principal("reviewer", "tenant-a", Role.REVIEWER)
    workspace.bootstrap_principal(reviewer)
    _, _, revision = _revision(workspace, reviewer, status="In development")

    with pytest.raises(WorkspaceError, match="in-development"):
        workspace.record_approval(
            reviewer,
            revision_id=revision.revision_id,
            action="approved",
            comment="This must remain a review draft.",
            idempotency_key="unsafe-auto-approval",
        )


def test_cross_tenant_ids_are_non_enumerable_across_sensitive_resources(workspace):
    tenant_a = _principal("tenant-a-admin", "tenant-a", Role.LAB_ADMIN)
    tenant_b = _principal("tenant-b-admin", "tenant-b", Role.LAB_ADMIN)
    workspace.bootstrap_principal(tenant_a)
    workspace.bootstrap_principal(tenant_b)
    _, _, revision = _revision(workspace, tenant_a)
    connector = workspace.configure_connector(
        tenant_a,
        connector_kind="google_drive",
        display_name="Approved protocol folder",
        credential_reference="secret://tenant-a/google-drive",
        allowed_roots=("drive-folder-1",),
    )
    workspace.bind_resource(tenant_a, "report", "report-a")

    for operation in (
        lambda: workspace.get_revision(tenant_b, revision.revision_id),
        lambda: workspace.get_connector(tenant_b, connector.connector_id),
        lambda: workspace.require_resource(tenant_b, "report", "report-a"),
    ):
        with pytest.raises(WorkspaceNotFoundError):
            operation()


def test_connector_secrets_never_enter_metadata_and_append_only_rows_hold(workspace):
    admin = _principal("admin", "tenant-a", Role.LAB_ADMIN)
    workspace.bootstrap_principal(admin)
    connector = workspace.configure_connector(
        admin,
        connector_kind="github",
        display_name="Protocol repository",
        credential_reference="secret://tenant-a/github-app-installation",
        allowed_roots=("lab/protocols@main",),
    )
    assert connector.credential_reference == "secret://tenant-a/github-app-installation"
    row = workspace._connection.execute(
        "SELECT * FROM connector_configurations WHERE connector_id=?",
        (connector.connector_id,),
    ).fetchone()
    assert "token" not in dict(row)

    _, source, revision = _revision(workspace, admin)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        workspace._connection.execute(
            "UPDATE protocol_lineage_revisions SET content_json='{}' WHERE revision_id=?",
            (revision.revision_id,),
        )
    workspace._connection.rollback()
    assert workspace.get_revision(admin, revision.revision_id).source_hash == source.source_hash


def test_privacy_safe_analytics_are_tenant_scoped_and_reject_free_text(workspace):
    tenant_a = _principal("tenant-a-admin", "tenant-a", Role.LAB_ADMIN)
    tenant_b = _principal("tenant-b-admin", "tenant-b", Role.LAB_ADMIN)
    workspace.bootstrap_principal(tenant_a)
    workspace.bootstrap_principal(tenant_b)
    workspace.record_analytics(
        tenant_a,
        category="voice",
        metric_name="turn_latency_ms",
        metric_value=321,
        dimensions={"intent": "complete_step", "status": "ok"},
    )
    workspace.record_analytics(
        tenant_b,
        category="voice",
        metric_name="turn_latency_ms",
        metric_value=999,
        dimensions={"status": "ok"},
    )

    summary = workspace.analytics_summary(tenant_a)
    assert summary["metrics"][0]["total"] == 321
    assert summary["privacy"]["transcripts"] is False
    with pytest.raises(WorkspaceError, match="privacy-safe"):
        workspace.record_analytics(
            tenant_a,
            category="voice",
            metric_name="turn",
            dimensions={"transcript": "raw spoken private protocol"},
        )


def test_pilot_metrics_roll_up_durable_workflow_and_retained_failure_events(workspace):
    admin = _principal("pilot-admin", "tenant-a", Role.LAB_ADMIN)
    outsider = _principal("other-admin", "tenant-b", Role.LAB_ADMIN)
    workspace.bootstrap_principal(admin)
    workspace.bootstrap_principal(outsider)
    experiment = workspace.start_experiment(
        admin,
        session_id="pilot-metrics-session",
        protocol_id="protocol-a",
        protocol_revision_id="revision-a",
        current_step_id="step-1",
        current_step_label="1",
        voice_connection_id="voice-a",
    )
    experiment = workspace.resume_experiment(
        admin,
        experiment["session_id"],
        expected_version=experiment["version"],
        protocol_id=experiment["protocol_id"],
        protocol_revision_id=experiment["protocol_revision_id"],
        voice_connection_id="voice-b",
    )
    experiment = workspace.record_experiment_progress(
        admin,
        experiment["session_id"],
        expected_version=experiment["version"],
        event_key="protocol-started",
        event_type="protocol_started",
        step_id="step-1",
        step_label="1",
    )
    experiment = workspace.record_experiment_progress(
        admin,
        experiment["session_id"],
        expected_version=experiment["version"],
        event_key="manual-step-completed",
        event_type="step_completed",
        step_id="step-1",
        step_label="1",
        next_step_id="step-2",
        next_step_label="2",
        mark_completed=True,
        payload={"capture_source": "manual"},
    )
    workspace.record_observation(
        admin,
        experiment["session_id"],
        event_key="manual-observation",
        content="synthetic observation",
        category="note",
        capture_source="manual",
        protocol_step_id="step-2",
    )
    workspace.record_evidence(
        admin,
        experiment["session_id"],
        event_key="manual-evidence",
        evidence_kind="document",
        original_filename="synthetic.txt",
        media_type="text/plain",
        byte_size=9,
        sha256=hashlib.sha256(b"synthetic").hexdigest(),
        storage_reference="evidence/synthetic.txt",
        protocol_step_id="step-2",
    )
    # Replaying the same idempotency keys returns the same durable records and
    # must not inflate the manual-fallback KPI.
    workspace.record_observation(
        admin,
        experiment["session_id"],
        event_key="manual-observation",
        content="synthetic observation",
        category="note",
        capture_source="manual",
        protocol_step_id="step-2",
    )
    workspace.record_evidence(
        admin,
        experiment["session_id"],
        event_key="manual-evidence",
        evidence_kind="document",
        original_filename="synthetic.txt",
        media_type="text/plain",
        byte_size=9,
        sha256=hashlib.sha256(b"synthetic").hexdigest(),
        storage_reference="evidence/synthetic.txt",
        protocol_step_id="step-2",
    )
    experiment = workspace.get_experiment(admin, experiment["session_id"])
    workspace.transition_experiment(
        admin,
        experiment["session_id"],
        action="complete",
        expected_version=experiment["version"],
        event_key="workflow-completed",
    )
    workspace.record_analytics(
        admin,
        category="voice",
        metric_name="command_failure",
        dimensions={"status": "rejected", "reason_code": "empty_transcript"},
    )
    workspace.record_analytics(
        admin,
        category="workflow",
        metric_name="mutation_failure",
        dimensions={"status": "rolled_back", "reason_code": "workspace_error"},
    )
    for metric_name in (
        "clarification_request",
        "repeat_request",
        "repeated_utterance",
        "stt_failure",
        "barge_in_ignored",
        "barge_in_confirmed",
        "playback_interruption",
        "unknown_speaker_mutation_rejection",
        "overlapping_speaker_ambiguity",
    ):
        workspace.record_analytics(
            admin,
            category="voice",
            metric_name=metric_name,
            dimensions={"status": "synthetic"},
        )
    for metric_name in (
        "ambiguous_mutation_command",
        "blocked_mutation",
    ):
        workspace.record_analytics(
            admin,
            category="workflow",
            metric_name=metric_name,
            dimensions={"status": "synthetic"},
        )
    for _ in range(2):
        workspace.record_analytics(
            admin,
            category="voice",
            metric_name="voice_turn",
            dimensions={"status": "accepted_endpoint"},
        )
    workspace.record_analytics(
        admin,
        category="voice",
        metric_name="successful_turn",
        dimensions={"status": "completed"},
    )
    for action in ("current", "next"):
        workspace.record_analytics(
            admin,
            category="workflow",
            metric_name="turn",
            metric_value=100,
            dimensions={"status": "read_only", "event_kind": action},
        )

    metrics = workspace.pilot_metrics_summary(admin)
    assert metrics["completed_workflows"] == 1
    assert metrics["completed_workflow_steps"] == 1
    assert metrics["voice_turns"] == 2
    assert metrics["successful_voice_turns"] == 1
    assert metrics["voice_turn_success_rate"] == 0.5
    assert metrics["clarification_requests"] == 1
    assert metrics["repeat_requests"] == 1
    assert metrics["repeated_utterances"] == 1
    assert metrics["stt_failures"] == 1
    assert metrics["ambiguous_state_changing_commands"] == 1
    assert metrics["blocked_mutation_attempts"] == 1
    assert metrics["ignored_barge_in_candidates"] == 1
    assert metrics["confirmed_barge_ins"] == 1
    assert metrics["playback_only_interruptions"] == 1
    assert metrics["unknown_speaker_mutation_rejections"] == 1
    assert metrics["overlapping_speaker_ambiguity_events"] == 1
    assert metrics["failed_commands"] == 1
    assert metrics["recovery_events"] == 1
    assert metrics["mutation_failures"] == 1
    assert metrics["persistence_failures"] == 1
    assert metrics["manual_fallback_actions"] == 3
    assert metrics["observation_captures"] == 1
    assert metrics["evidence_captures"] == 1
    assert metrics["completed_session_duration_seconds"]["samples"] == 1
    assert metrics["user_actions"] == 2
    assert metrics["workflow_completion_rate"] == 1.0
    assert metrics["details"]["durable_actions_by_type"]["session_completed"] == 1
    assert metrics["privacy"] == {
        "raw_audio": False,
        "transcripts": False,
        "identifiers": False,
        "free_text": False,
        "biometric_voiceprints": False,
    }
    assert workspace.pilot_metrics_summary(outsider)["completed_workflows"] == 0


def test_workspace_settings_require_explicit_absolute_storage(tmp_path):
    with pytest.raises(WorkspaceError, match="absolute"):
        WorkspaceSettings.from_environment(
            {
                "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "true",
                "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR": "relative/path",
            }
        )
    configured = WorkspaceSettings.from_environment(
        {
            "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "true",
            "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR": str(tmp_path),
            "VOICE_WORKFLOW_AGENT_ANALYTICS_RETENTION_DAYS": "30",
        }
    )
    assert configured.analytics_retention_days == 30


def test_knowledge_promotion_preserves_original_class_and_provenance(workspace):
    researcher = _principal("researcher", "tenant-a", Role.RESEARCHER)
    reviewer = _principal("reviewer", "tenant-a", Role.REVIEWER)
    workspace.bootstrap_principal(researcher)
    workspace.bootstrap_principal(reviewer)
    knowledge_id = workspace.add_knowledge(
        researcher,
        kind="historical_observation",
        body="Three prior runs needed an extra documented rinse.",
        provenance={"report_ids": ["report-a", "report-b", "report-c"]},
    )
    assert workspace.knowledge_entries(researcher)[0]["effective_kind"] == (
        "historical_observation"
    )
    with pytest.raises(AuthorizationDeniedError):
        workspace.promote_knowledge(
            researcher, knowledge_id=knowledge_id, comment="Self promote"
        )
    workspace.promote_knowledge(
        reviewer,
        knowledge_id=knowledge_id,
        comment="Reviewed as a lab annotation; not source SOP text.",
    )
    entry = workspace.knowledge_entries(researcher)[0]
    assert entry["kind"] == "historical_observation"
    assert entry["effective_kind"] == "approved_protocol_fact"
    assert entry["provenance"]["report_ids"] == ["report-a", "report-b", "report-c"]


def test_asset_location_cards_are_versioned_reviewable_and_tenant_private(workspace):
    admin = _principal("admin", "tenant-a", Role.LAB_ADMIN)
    researcher = _principal("researcher", "tenant-a", Role.RESEARCHER)
    outsider = _principal("outsider", "tenant-b", Role.RESEARCHER)
    for principal in (admin, researcher, outsider):
        workspace.bootstrap_principal(principal)
    first = workspace.add_asset_card_version(
        admin,
        asset_id="trypsin",
        asset_kind="reagent",
        name="Trypsin",
        location={"building": "Science", "room": "302", "storage": "Freezer A", "shelf": "2"},
        barcode="TRY-001",
        sds_url="https://sds.example.test/trypsin",
    )
    second = workspace.add_asset_card_version(
        admin,
        asset_id="trypsin",
        asset_kind="reagent",
        name="Trypsin",
        location={"building": "Science", "room": "302", "storage": "Freezer B", "drawer": "1"},
        barcode="TRY-001",
        sds_url="https://sds.example.test/trypsin",
        review_status="reviewed",
    )
    assert [item["version_id"] for item in workspace.asset_card_history(researcher, "trypsin")] == [first, second]
    difference = workspace.asset_card_diff(researcher, "trypsin")
    assert difference["changes"]["location"]["before"]["storage"] == "Freezer A"
    assert difference["changes"]["location"]["after"]["storage"] == "Freezer B"
    with pytest.raises(WorkspaceNotFoundError):
        workspace.asset_card_history(outsider, "trypsin")


def test_protocol_library_supports_search_favorites_tags_and_quick_links(workspace):
    researcher = _principal("researcher", "tenant-a", Role.RESEARCHER)
    workspace.bootstrap_principal(researcher)
    family, _, revision = _revision(workspace, researcher)
    workspace.set_protocol_preference(
        researcher, family.family_id, favorite=True, tags=("fiber", "plant")
    )
    library = workspace.protocol_library(researcher, search="ANKOM")
    assert len(library) == 1
    assert library[0]["revision_id"] == revision.revision_id
    assert library[0]["favorite"] is True
    assert library[0]["tags"] == ["fiber", "plant"]
    assert library[0]["quick_link"].endswith(f"protocol={family.family_id}")
    assert workspace.protocol_library(researcher, search="not present") == ()
