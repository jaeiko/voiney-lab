from __future__ import annotations

import asyncio
import hashlib

import httpx

from voiney_lab.identity import AuthenticationRequiredError, Principal, Role
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.server import app
from voiney_lab import server as server_module
from voiney_lab.workspace_store import (
    WorkspaceSettings,
    initialize_workspace_store,
)


def _profiles():
    """Several identities for the tenant-scoping tests.

    Lane DI (2026-10-08): the server resolves one development identity and
    reads no client-chosen profile, so these tests install a fake resolver
    (``_configure``) and name the identity per request (``_request``).
    The profile ids are historical; every identity is an experimenter.
    """

    return [
        {
            "profile_id": "admin-a",
            "principal_id": "principal-admin-a",
            "organization_id": "tenant-a",
            "display_name": "Admin A",
        },
        {
            "profile_id": "reviewer-a",
            "principal_id": "principal-reviewer-a",
            "organization_id": "tenant-a",
            "display_name": "Reviewer A",
        },
        {
            "profile_id": "researcher-a",
            "principal_id": "principal-researcher-a",
            "organization_id": "tenant-a",
            "display_name": "Researcher A",
        },
        {
            "profile_id": "reviewer-b",
            "principal_id": "principal-reviewer-b",
            "organization_id": "tenant-b",
            "display_name": "Reviewer B",
        },
    ]


def _principal(profile_id: str) -> Principal:
    profile = next(item for item in _profiles() if item["profile_id"] == profile_id)
    return Principal(
        principal_id=profile["principal_id"],
        subject=f"dev:{profile_id}",
        organization_id=profile["organization_id"],
        display_name=profile["display_name"],
        roles=frozenset({Role.RESEARCHER}),
        authentication_method="development",
    )


_CURRENT_PROFILE: list[str | None] = [None]


class _FakeResolver:
    def resolve(self, authorization):
        profile = _CURRENT_PROFILE[0]
        if profile is None or not any(item["profile_id"] == profile for item in _profiles()):
            raise AuthenticationRequiredError("The test named no configured identity.")
        return _principal(profile)


def _configure(monkeypatch, tmp_path, *, scope="demo"):
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", scope)
    monkeypatch.setattr(server_module, "_identity_resolver", lambda: _FakeResolver())
    for name in (
        "VOINEY_LAB_OIDC_ISSUER",
        "VOINEY_LAB_OIDC_AUDIENCE",
        "VOINEY_LAB_OIDC_JWKS_URL",
    ):
        monkeypatch.delenv(name, raising=False)


async def _request(
    method, path, *, profile=None, json_body=None, content=None, headers=None
):
    request_headers = dict(headers or {})
    _CURRENT_PROFILE[0] = profile
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


def test_workspace_session_routes_and_unknown_identities_are_refused(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    # Lane DI (2026-10-08): the bench is the one workspace, whatever the
    # profile's roles say; the reviewer and admin screens are gone.
    reviewer = asyncio.run(_request("GET", "/api/workspace/session", profile="reviewer-a"))
    assert reviewer.status_code == 200
    assert reviewer.json()["workspaces"] == ["researcher"]
    invented = asyncio.run(
        _request("GET", "/api/workspace/session", profile="invented-admin")
    )
    assert invented.status_code == 401
    assert invented.json() == {"detail": "authentication_required"}
    admin = asyncio.run(_request("GET", "/api/workspace/session", profile="admin-a"))
    assert admin.status_code == 200
    assert admin.json()["workspaces"] == ["researcher"]
    for route in ("/api/workspace/admin/security", "/api/workspace/admin/memberships",
                  "/api/workspace/admin/analytics", "/api/workspace/reviewer/inbox",
                  "/api/workspace/protocol-adaptations"):
        gone = asyncio.run(_request("GET", route, profile="admin-a"))
        assert gone.status_code == 404, route


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
        expected_version=experiment["version"],
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


def test_experiment_timeline_api_records_manual_observation_and_evidence(
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
        expected_version=experiment["version"],
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
    assert body["recovery"] == {
        "eligible": True,
        "last_event_type": None,
        "restored": {
            "protocol_id": "in-gel-digestion",
            "protocol_revision_id": "approved-revision-1",
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
    assert body["separation"]["approved_protocol_knowledge_unchanged"] is True
    evidence_event = next(
        item for item in body["timeline"]
        if item["event_type"] == "evidence_attached"
    )
    assert "storage_reference" not in evidence_event["evidence"]

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
    downloaded = asyncio.run(
        _request(
            "GET",
            (
                f"/api/workspace/experiments/{experiment['session_id']}/evidence/"
                f"{uploaded.json()['evidence_id']}"
            ),
            profile="researcher-a",
        )
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == evidence_bytes
    assert downloaded.headers["content-type"] == "image/jpeg"
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert downloaded.headers["x-evidence-sha256"] == hashlib.sha256(
        evidence_bytes
    ).hexdigest()
    assert downloaded.headers["x-evidence-interpretation"] == "not_interpreted"
    assert downloaded.headers["content-disposition"].startswith(
        'attachment; filename="evidence-'
    )
    assert "sample.jpg" not in downloaded.headers["content-disposition"]
    hidden_download = asyncio.run(
        _request(
            "GET",
            (
                f"/api/workspace/experiments/{experiment['session_id']}/evidence/"
                f"{uploaded.json()['evidence_id']}"
            ),
            profile="reviewer-b",
        )
    )
    assert hidden_download.status_code == 404

    stored_files[0].write_bytes(b"\xff\xd8\xff" + b"opaque-tamper!!")
    corrupted = asyncio.run(
        _request(
            "GET",
            (
                f"/api/workspace/experiments/{experiment['session_id']}/evidence/"
                f"{uploaded.json()['evidence_id']}"
            ),
            profile="researcher-a",
        )
    )
    assert corrupted.status_code == 400
    assert corrupted.json() == {"detail": "workspace_error"}

    stored_files[0].unlink()
    missing = asyncio.run(
        _request(
            "GET",
            (
                f"/api/workspace/experiments/{experiment['session_id']}/evidence/"
                f"{uploaded.json()['evidence_id']}"
            ),
            profile="researcher-a",
        )
    )
    assert missing.status_code == 404
    linked_target = tmp_path / "evidence-target.jpg"
    linked_target.write_bytes(evidence_bytes)
    stored_files[0].symlink_to(linked_target)
    linked = asyncio.run(
        _request(
            "GET",
            (
                f"/api/workspace/experiments/{experiment['session_id']}/evidence/"
                f"{uploaded.json()['evidence_id']}"
            ),
            profile="researcher-a",
        )
    )
    assert linked.status_code == 400
    assert linked.json() == {"detail": "workspace_error"}


def test_cross_tenant_report_idor(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
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
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORT_DB", str(report_path))
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
