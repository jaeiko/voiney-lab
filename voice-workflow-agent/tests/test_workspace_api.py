from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx

from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.experiment_reports import ExperimentReportStore
from voice_workflow_agent.server import app
from voice_workflow_agent import server as server_module
from voice_workflow_agent.workspace_store import (
    WorkspaceSettings,
    initialize_workspace_store,
)


def _profiles():
    return [
        {
            "profile_id": "admin-a",
            "principal_id": "principal-admin-a",
            "organization_id": "tenant-a",
            "display_name": "Admin A",
            "roles": ["lab_admin"],
        },
        {
            "profile_id": "reviewer-a",
            "principal_id": "principal-reviewer-a",
            "organization_id": "tenant-a",
            "display_name": "Reviewer A",
            "roles": ["reviewer"],
        },
        {
            "profile_id": "researcher-a",
            "principal_id": "principal-researcher-a",
            "organization_id": "tenant-a",
            "display_name": "Researcher A",
            "roles": ["researcher"],
        },
        {
            "profile_id": "reviewer-b",
            "principal_id": "principal-reviewer-b",
            "organization_id": "tenant-b",
            "display_name": "Reviewer B",
            "roles": ["reviewer"],
        },
    ]


def _principal(profile_id: str) -> Principal:
    profile = next(item for item in _profiles() if item["profile_id"] == profile_id)
    return Principal(
        principal_id=profile["principal_id"],
        subject=f"dev:{profile_id}",
        organization_id=profile["organization_id"],
        display_name=profile["display_name"],
        roles=frozenset(Role(value) for value in profile["roles"]),
        authentication_method="development",
    )


def _configure(monkeypatch, tmp_path, *, scope="demo"):
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_USAGE_SCOPE", scope)
    monkeypatch.setenv(
        "VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES", json.dumps(_profiles())
    )
    for name in (
        "VOICE_WORKFLOW_AGENT_OIDC_ISSUER",
        "VOICE_WORKFLOW_AGENT_OIDC_AUDIENCE",
        "VOICE_WORKFLOW_AGENT_OIDC_JWKS_URL",
    ):
        monkeypatch.delenv(name, raising=False)


async def _request(
    method, path, *, profile=None, json_body=None, content=None, headers=None
):
    request_headers = dict(headers or {})
    if profile:
        request_headers["X-Voice-Dev-Profile"] = profile
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(
            method,
            path,
            headers=request_headers,
            json=json_body,
            content=content,
        )


def _create_revision(tmp_path, *, source_status="Published"):
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    admin = _principal("admin-a")
    reviewer = _principal("reviewer-a")
    outsider = _principal("reviewer-b")
    for principal in (admin, reviewer, outsider):
        store.bootstrap_principal(principal)
    family = store.create_protocol_family(admin, title="ANKOM Fiber Analysis")
    source = store.register_source(
        admin,
        connector_kind="protocols_io",
        external_id="10.17504/protocols.io.yinfude",
        version_identity="v1",
        source_hash=hashlib.sha256(b"ankom").hexdigest(),
        canonical_url="https://www.protocols.io/view/yinfude",
        metadata={"source_status": source_status, "risk_state": "hazard_review"},
    )
    revision = store.add_protocol_revision(
        admin,
        family_id=family.family_id,
        source_id=source.source_id,
        content={"steps": ["Use 72% sulfuric acid"], "warnings": ["Acid"]},
        change_summary="Exact source import",
    )
    store.close()
    return revision


def test_workspace_session_routes_and_server_allowlisted_dev_identity(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    reviewer = asyncio.run(_request("GET", "/api/workspace/session", profile="reviewer-a"))
    assert reviewer.status_code == 200
    assert reviewer.json()["workspaces"] == ["researcher", "reviewer"]
    invented = asyncio.run(
        _request("GET", "/api/workspace/session", profile="invented-admin")
    )
    assert invented.status_code == 401
    assert invented.json() == {"detail": "authentication_required"}


def test_experiment_dashboard_api_is_tenant_scoped_and_completion_is_voice_owned(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    researcher = _principal("researcher-a")
    outsider = _principal("reviewer-b")
    store.bootstrap_principal(researcher)
    store.bootstrap_principal(outsider)
    experiment = store.start_experiment(
        researcher,
        session_id="experiment-dashboard-1",
        protocol_id="in-gel-digestion",
        protocol_revision_id="approved-revision-1",
        current_step_id="step-1",
        current_step_label="1",
    )
    experiment = store.record_experiment_progress(
        researcher,
        experiment["session_id"],
        event_key="protocol-started",
        event_type="protocol_started",
        step_id="step-1",
        step_label="1",
    )
    store.close()

    listed = asyncio.run(
        _request("GET", "/api/workspace/experiments", profile="researcher-a")
    )
    assert listed.status_code == 200
    assert listed.json()["experiments"][0]["session_id"] == experiment["session_id"]
    hidden = asyncio.run(
        _request(
            "GET",
            f"/api/workspace/experiments/{experiment['session_id']}",
            profile="reviewer-b",
        )
    )
    assert hidden.status_code == 404

    paused = asyncio.run(
        _request(
            "POST",
            f"/api/workspace/experiments/{experiment['session_id']}/transition",
            profile="researcher-a",
            json_body={
                "action": "pause",
                "expected_version": experiment["version"],
                "event_key": "dashboard-pause-1",
            },
        )
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    forbidden_completion = asyncio.run(
        _request(
            "POST",
            f"/api/workspace/experiments/{experiment['session_id']}/transition",
            profile="researcher-a",
            json_body={
                "action": "complete",
                "expected_version": paused.json()["version"],
                "event_key": "unsafe-dashboard-complete",
            },
        )
    )
    assert forbidden_completion.status_code == 400


def test_experiment_timeline_api_records_manual_observation_evidence_and_review(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    researcher = _principal("researcher-a")
    reviewer = _principal("reviewer-a")
    outsider = _principal("reviewer-b")
    for principal in (researcher, reviewer, outsider):
        store.bootstrap_principal(principal)
    experiment = store.start_experiment(
        researcher,
        session_id="experiment-timeline-api-1",
        protocol_id="in-gel-digestion",
        protocol_revision_id="approved-revision-1",
        current_step_id="step-1",
        current_step_label="1",
    )
    store.record_experiment_progress(
        researcher,
        experiment["session_id"],
        event_key="protocol-started",
        event_type="protocol_started",
        step_id="step-1",
        step_label="1",
    )
    store.close()

    observed = asyncio.run(
        _request(
            "POST",
            f"/api/workspace/experiments/{experiment['session_id']}/observations",
            profile="researcher-a",
            json_body={
                "idempotency_key": "manual-observation-1",
                "content": "Sample is slightly cloudy.",
                "category": "appearance",
                "protocol_step_id": "step-1",
            },
        )
    )
    assert observed.status_code == 201, observed.text
    assert observed.json()["knowledge_effect"] == "observation_only"

    evidence_bytes = b"\xff\xd8\xff" + b"opaque-evidence"
    uploaded = asyncio.run(
        _request(
            "POST",
            (
                f"/api/workspace/experiments/{experiment['session_id']}/evidence"
                "?filename=sample.jpg&idempotency_key=evidence-upload-1"
            ),
            profile="researcher-a",
            content=evidence_bytes,
            headers={"Content-Type": "image/jpeg"},
        )
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["interpretation_status"] == "not_interpreted"
    assert "storage_reference" not in uploaded.json()
    assert uploaded.json()["sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
    replayed_upload = asyncio.run(
        _request(
            "POST",
            (
                f"/api/workspace/experiments/{experiment['session_id']}/evidence"
                "?filename=sample.jpg&idempotency_key=evidence-upload-1"
            ),
            profile="researcher-a",
            content=evidence_bytes,
            headers={"Content-Type": "image/jpeg"},
        )
    )
    assert replayed_upload.status_code == 201, replayed_upload.text
    assert replayed_upload.json()["evidence_id"] == uploaded.json()["evidence_id"]

    unsupported = asyncio.run(
        _request(
            "POST",
            (
                f"/api/workspace/experiments/{experiment['session_id']}/evidence"
                "?filename=payload.bin&idempotency_key=evidence-upload-2"
            ),
            profile="researcher-a",
            content=b"untrusted",
            headers={"Content-Type": "application/octet-stream"},
        )
    )
    assert unsupported.status_code == 415

    reviewed = asyncio.run(
        _request(
            "POST",
            f"/api/workspace/reviewer/experiments/{experiment['session_id']}/actions",
            profile="reviewer-a",
            json_body={
                "idempotency_key": "review-action-1",
                "action": "acknowledged",
                "comment": "Reviewed as an observation only; SOP unchanged.",
            },
        )
    )
    assert reviewed.status_code == 201, reviewed.text
    assert reviewed.json()["recorded"] is True

    timeline = asyncio.run(
        _request(
            "GET",
            f"/api/workspace/experiments/{experiment['session_id']}/timeline",
            profile="researcher-a",
        )
    )
    assert timeline.status_code == 200
    body = timeline.json()
    assert body["observation_count"] == 1
    assert body["evidence_count"] == 1
    assert body["separation"]["approved_protocol_knowledge_unchanged"] is True
    evidence_event = next(
        item for item in body["timeline"]
        if item["event_type"] == "evidence_attached"
    )
    assert "storage_reference" not in evidence_event["evidence"]
    assert any(
        item["event_type"] == "reviewer_action" for item in body["timeline"]
    )

    hidden = asyncio.run(
        _request(
            "GET",
            f"/api/workspace/experiments/{experiment['session_id']}/timeline",
            profile="reviewer-b",
        )
    )
    assert hidden.status_code == 404

    stored_files = [
        path for path in (tmp_path / "evidence").rglob("*.jpg")
        if path.is_file()
    ]
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == evidence_bytes


def test_protocol_library_uses_authoritative_catalog_execution_state(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    researcher = _principal("researcher-a")
    store.bootstrap_principal(researcher)
    family = store.create_protocol_family(researcher, title="ANKOM PDF")
    source = store.register_source(
        researcher,
        connector_kind="local_pdf",
        external_id="upload:ankom.pdf",
        version_identity="source-sha",
        source_hash=hashlib.sha256(b"ankom-pdf").hexdigest(),
        canonical_url=None,
        metadata={
            "catalog_protocol_id": "protocol-ankom",
            "risk_state": "review_required",
        },
    )
    store.add_protocol_revision(
        researcher,
        family_id=family.family_id,
        source_id=source.source_id,
        content={"document": {"format": "pdf"}},
        change_summary="Local PDF registered",
    )
    store.close()

    class FakeCatalog:
        @staticmethod
        def get_entry(protocol_id):
            assert protocol_id == "protocol-ankom"
            return SimpleNamespace(
                available_for_execution=True,
                revision_id="pdf-1-analysis-1",
                approval_status="development_only",
                lifecycle_state="executable_draft",
            )

    class FakeStore:
        @staticmethod
        def close():
            return None

    monkeypatch.setattr(
        server_module, "_open_protocol_catalog", lambda: (FakeCatalog(), FakeStore())
    )
    monkeypatch.setattr(server_module, "_scope_catalog_resource", lambda _value: None)
    response = asyncio.run(
        _request("GET", "/api/workspace/protocol-library", profile="researcher-a")
    )
    assert response.status_code == 200, response.text
    item = response.json()["protocols"][0]
    assert item["executable"] is True
    assert item["catalog_revision_id"] == "pdf-1-analysis-1"
    assert item["approval_state"] == "development_only"
    assert item["risk_state"] == "executable_draft"


def test_role_separated_connector_api_never_returns_credential_reference(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    created = asyncio.run(
        _request(
            "POST",
            "/api/workspace/admin/connectors",
            profile="admin-a",
            json_body={
                "connector_kind": "google_drive",
                "display_name": "Shared protocol folder",
                "credential_reference": "secret://tenant-a/google-drive",
                "allowed_roots": ["folder:folder_123", "shared-drive:drive_123"],
            },
        )
    )
    assert created.status_code == 201, created.text
    assert "credential_reference" not in created.json()
    listed = asyncio.run(
        _request("GET", "/api/workspace/connectors", profile="researcher-a")
    )
    assert listed.status_code == 200
    serialized = json.dumps(listed.json())
    assert "secret://" not in serialized
    denied = asyncio.run(
        _request(
            "POST",
            "/api/workspace/admin/connectors",
            profile="researcher-a",
            json_body={
                "connector_kind": "github",
                "display_name": "Escalation attempt",
                "credential_reference": "secret://tenant-a/github",
                "allowed_roots": ["lab/repo@main:protocols"],
            },
        )
    )
    assert denied.status_code == 403


def test_reviewer_diff_approval_analytics_and_cross_tenant_idor(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    revision = _create_revision(tmp_path)
    difference = asyncio.run(
        _request(
            "GET",
            f"/api/workspace/reviewer/revisions/{revision.revision_id}/diff",
            profile="reviewer-a",
        )
    )
    assert difference.status_code == 200
    outsider = asyncio.run(
        _request(
            "GET",
            f"/api/workspace/reviewer/revisions/{revision.revision_id}/diff",
            profile="reviewer-b",
        )
    )
    assert outsider.status_code == 404
    researcher = asyncio.run(
        _request(
            "POST",
            f"/api/workspace/reviewer/revisions/{revision.revision_id}/decision",
            profile="researcher-a",
            json_body={
                "action": "approved",
                "comment": "Unauthorized",
                "idempotency_key": "researcher-escalation",
            },
        )
    )
    assert researcher.status_code == 403
    approved = asyncio.run(
        _request(
            "POST",
            f"/api/workspace/reviewer/revisions/{revision.revision_id}/decision",
            profile="reviewer-a",
            json_body={
                "action": "approved",
                "comment": "Source, structure, and hazards reviewed.",
                "idempotency_key": "reviewer-approval-v1",
            },
        )
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"]["available_for_new_operational_sessions"] is True
    analytics = asyncio.run(
        _request("GET", "/api/workspace/admin/analytics", profile="admin-a")
    )
    assert analytics.status_code == 200
    assert analytics.json()["metrics"][0]["metric_name"] == "review_decision"


def test_in_development_revision_fails_closed_and_operational_requires_oidc(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    revision = _create_revision(tmp_path, source_status="In development")
    blocked = asyncio.run(
        _request(
            "POST",
            f"/api/workspace/reviewer/revisions/{revision.revision_id}/decision",
            profile="reviewer-a",
            json_body={
                "action": "approved",
                "comment": "Should remain a draft",
                "idempotency_key": "development-approval-attempt",
            },
        )
    )
    assert blocked.status_code == 409
    assert blocked.json() == {"detail": "workspace_conflict"}

    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_USAGE_SCOPE", "operational")
    unauthenticated = asyncio.run(_request("GET", "/api/workspace/session"))
    assert unauthenticated.status_code == 503
    assert unauthenticated.json() == {"detail": "identity_configuration_invalid"}


def test_admin_membership_retention_and_cross_tenant_report_idor(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    membership = asyncio.run(
        _request(
            "PUT",
            "/api/workspace/admin/memberships/principal-pilot-user",
            profile="admin-a",
            json_body={
                "subject": "oidc:pilot-user",
                "display_name": "Pilot User",
                "role": "researcher",
                "active": True,
            },
        )
    )
    assert membership.status_code == 200, membership.text
    listed = asyncio.run(
        _request("GET", "/api/workspace/admin/memberships", profile="admin-a")
    )
    assert any(
        item["principal_id"] == "principal-pilot-user"
        for item in listed.json()["memberships"]
    )
    denied = asyncio.run(
        _request("GET", "/api/workspace/admin/memberships", profile="reviewer-a")
    )
    assert denied.status_code == 403

    retention = asyncio.run(
        _request(
            "PUT",
            "/api/workspace/admin/retention",
            profile="admin-a",
            json_body={"analytics_retention_days": 30},
        )
    )
    assert retention.status_code == 200
    assert retention.json()["analytics_retention_days"] == 30

    report_path = tmp_path / "reports.sqlite"
    report = ExperimentReportStore(report_path).open_report(
        session_id="session-tenant-a",
        protocol_id="protocol-tenant-a",
        protocol_title="Tenant A protocol",
        protocol_revision="pdf-1-analysis-1",
        protocol_sha256="a" * 64,
        readiness_status="guidance_ready",
        development_only=True,
    )
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    admin = _principal("admin-a")
    store.bootstrap_principal(admin)
    store.bind_resource(admin, "experiment_report", report["report_id"])
    store.close()
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED", "true")
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB", str(report_path))
    outsider = asyncio.run(
        _request(
            "GET",
            f"/api/experiment-reports/{report['report_id']}.json",
            profile="reviewer-b",
        )
    )
    assert outsider.status_code == 404
    owner = asyncio.run(
        _request(
            "GET",
            f"/api/experiment-reports/{report['report_id']}.json",
            profile="admin-a",
        )
    )
    assert owner.status_code == 200


def test_github_ping_webhook_hmac_and_delivery_replay_boundary(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("TEST_GITHUB_INSTALLATION_TOKEN", "installation-token")
    monkeypatch.setenv("TEST_GITHUB_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv(
        "VOICE_WORKFLOW_AGENT_SECRET_REFERENCES",
        json.dumps(
            {
                "secret://tenant-a/github": "TEST_GITHUB_INSTALLATION_TOKEN",
                "secret://tenant-a/github-webhook": "TEST_GITHUB_WEBHOOK_SECRET",
            }
        ),
    )
    created = asyncio.run(
        _request(
            "POST",
            "/api/workspace/admin/connectors",
            profile="admin-a",
            json_body={
                "connector_kind": "github",
                "display_name": "Protocol repository",
                "credential_reference": "secret://tenant-a/github",
                "webhook_secret_reference": "secret://tenant-a/github-webhook",
                "allowed_roots": ["lab/protocols@main:protocols"],
            },
        )
    )
    connector_id = created.json()["connector_id"]
    body = b'{"zen":"keep it logically awesome"}'
    signature = "sha256=" + hmac.new(
        b"webhook-secret", body, hashlib.sha256
    ).hexdigest()

    async def deliver(*, delivery="delivery-1", selected_signature=signature):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                f"/api/workspace/webhooks/github/{connector_id}",
                content=body,
                headers={
                    "X-Hub-Signature-256": selected_signature,
                    "X-GitHub-Delivery": delivery,
                    "X-GitHub-Event": "ping",
                    "Content-Type": "application/json",
                },
            )

    accepted = asyncio.run(deliver())
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["event"] == "ping"
    replay = asyncio.run(deliver())
    assert replay.status_code == 409
    tampered = asyncio.run(deliver(delivery="delivery-2", selected_signature="sha256=" + "0" * 64))
    assert tampered.status_code == 401


def test_elabftw_http_boundary_requires_confirmation_and_uses_server_report(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    report_path = tmp_path / "writeback-reports.sqlite"
    protocol_hash = hashlib.sha256(b"exact-protocol-source").hexdigest()
    reports = ExperimentReportStore(report_path)
    report = reports.open_report(
        session_id="session-writeback",
        protocol_id="protocol-writeback",
        protocol_title="Reviewed protocol",
        protocol_revision="pdf-1-analysis-1",
        protocol_sha256=protocol_hash,
        readiness_status="guidance_ready",
        development_only=False,
    )
    reports.append_event(
        report["report_id"],
        event_key="step-1-complete",
        event_type="step_completed",
        step_id="step-1",
        step_label="1",
    )
    reports.finalize(
        report["report_id"], status="completed", event_key="report-complete"
    )
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED", "true")
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB", str(report_path))
    monkeypatch.setenv("TEST_ELABFTW_KEY", "elab-api-key")
    monkeypatch.setenv(
        "VOICE_WORKFLOW_AGENT_SECRET_REFERENCES",
        json.dumps({"secret://tenant-a/elabftw": "TEST_ELABFTW_KEY"}),
    )

    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    admin = _principal("admin-a")
    researcher = _principal("researcher-a")
    for principal in (admin, researcher):
        store.bootstrap_principal(principal)
    family = store.create_protocol_family(admin, title="Reviewed protocol")
    source = store.register_source(
        admin,
        connector_kind="local_pdf",
        external_id="upload:reviewed.pdf",
        version_identity=protocol_hash,
        source_hash=protocol_hash,
        canonical_url=None,
        metadata={"source_status": "Published"},
    )
    revision = store.add_protocol_revision(
        admin,
        family_id=family.family_id,
        source_id=source.source_id,
        content={
            "execution_identity": {
                "protocol_id": "protocol-writeback",
                "source_sha256": protocol_hash,
            }
        },
        change_summary="Exact local PDF",
    )
    connector = store.configure_connector(
        admin,
        connector_kind="elabftw",
        display_name="Pilot eLabFTW",
        credential_reference="secret://tenant-a/elabftw",
        allowed_roots=("https://eln.example.test",),
    )
    store.bind_resource(researcher, "experiment_report", report["report_id"])
    store.close()

    calls = []

    class FakeConnector:
        def __init__(self, **configuration):
            assert configuration["server_configured_base_url"] == "https://eln.example.test"
            assert configuration["api_key"] == "elab-api-key"

        def write_completed_experiment(self, experiment, *, confirmed):
            calls.append((experiment, confirmed))
            return SimpleNamespace(
                external_experiment_id="321",
                location="https://eln.example.test/api/v2/experiments/321",
                request_sha256="c" * 64,
            )

    monkeypatch.setattr(server_module, "ELabFtwConnector", FakeConnector)
    unconfirmed = asyncio.run(
        _request(
            "POST",
            "/api/workspace/eln/elabftw/writeback",
            profile="researcher-a",
            json_body={
                "connector_id": connector.connector_id,
                "report_id": report["report_id"],
                "protocol_revision_id": revision.revision_id,
                "confirmed": False,
                "idempotency_key": "writeback-1",
            },
        )
    )
    assert unconfirmed.status_code == 409
    assert calls == []
    written = asyncio.run(
        _request(
            "POST",
            "/api/workspace/eln/elabftw/writeback",
            profile="researcher-a",
            json_body={
                "connector_id": connector.connector_id,
                "report_id": report["report_id"],
                "protocol_revision_id": revision.revision_id,
                "confirmed": True,
                "idempotency_key": "writeback-1",
            },
        )
    )
    assert written.status_code == 201, written.text
    assert written.json()["raw_audio_transmitted"] is False
    assert calls[0][0].report_id == report["report_id"]
    assert calls[0][0].completed_steps[0].step_id == "step-1"
    replay = asyncio.run(
        _request(
            "POST",
            "/api/workspace/eln/elabftw/writeback",
            profile="researcher-a",
            json_body={
                "connector_id": connector.connector_id,
                "report_id": report["report_id"],
                "protocol_revision_id": revision.revision_id,
                "confirmed": True,
                "idempotency_key": "writeback-1",
            },
        )
    )
    assert replay.status_code == 409
    assert len(calls) == 1
