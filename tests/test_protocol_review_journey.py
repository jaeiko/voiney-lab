"""The reviewer-to-researcher journey, exercised through the HTTP boundary.

One reviewer decision must produce one coherent outcome. These tests hold the
product to that: a reviewer never sees an "approve" that would not make the
protocol runnable, resolving a source ambiguity produces a traceable revision,
and the researcher's selector reflects the decision without a second hidden
approval step.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from voice_workflow_agent.curated_protocol import load_curated_protocol_fixture
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.protocol_catalog import ProtocolCatalog
from voice_workflow_agent.server import app
from voice_workflow_agent.workspace_store import (
    WorkspaceSettings,
    initialize_workspace_store,
)


REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY / "data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = REPOSITORY / (
    "data/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = REPOSITORY / "data/runtime/candidate-a-source/in-gel-digestion.pdf"
AMBIGUITY_ID = "candidate-a-step-20-repeat-range"
RESOLVED_RANGE = [
    "candidate-a-step-17",
    "candidate-a-step-18",
    "candidate-a-step-19",
    "candidate-a-step-20",
]

PROFILES = [
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
]


async def _request(method, path, *, profile=None, json_body=None):
    headers = {"X-Voice-Dev-Profile": profile} if profile else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, headers=headers, json=json_body)


def request(method, path, **kwargs):
    return asyncio.run(_request(method, path, **kwargs))


@pytest.fixture()
def catalog_server(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED", "true")
    monkeypatch.setenv(
        "VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR", str(tmp_path / "catalog")
    )
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv(
        "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR", str(tmp_path / "workspace")
    )
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_USAGE_SCOPE", "demo")
    monkeypatch.setenv(
        "VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES", json.dumps(PROFILES)
    )
    monkeypatch.setenv("VOICE_WORKFLOW_AGENT_MOSS_ENABLED", "false")
    for name in (
        "VOICE_WORKFLOW_AGENT_OIDC_ISSUER",
        "VOICE_WORKFLOW_AGENT_OIDC_AUDIENCE",
        "VOICE_WORKFLOW_AGENT_OIDC_JWKS_URL",
        "VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE",
    ):
        monkeypatch.delenv(name, raising=False)

    fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)
    settings = ProtocolPersistenceSettings(True, tmp_path / "catalog")
    store = initialize_protocol_store(settings)
    try:
        ProtocolCatalog(store).bootstrap_development_fixture(fixture)
    finally:
        store.close()

    workspace = initialize_workspace_store(
        WorkspaceSettings(True, tmp_path / "workspace")
    )
    try:
        for profile in PROFILES:
            workspace.bootstrap_principal(
                Principal(
                    principal_id=profile["principal_id"],
                    subject=f"dev:{profile['profile_id']}",
                    organization_id=profile["organization_id"],
                    display_name=profile["display_name"],
                    roles=frozenset(Role(value) for value in profile["roles"]),
                    authentication_method="development",
                )
            )
        reviewer = next(
            item for item in PROFILES if item["profile_id"] == "reviewer-a"
        )
        workspace.bind_resource(
            Principal(
                principal_id=reviewer["principal_id"],
                subject="dev:reviewer-a",
                organization_id=reviewer["organization_id"],
                display_name=reviewer["display_name"],
                roles=frozenset({Role.REVIEWER}),
                authentication_method="development",
            ),
            "protocol_catalog",
            fixture.protocol_id,
        )
    finally:
        workspace.close()
    return fixture.protocol_id


def _queue(profile="reviewer-a"):
    response = request("GET", "/api/protocols/review-queue", profile=profile)
    assert response.status_code == 200, response.text
    return response.json()["protocols"]


def _review(protocol_id, profile="reviewer-a"):
    response = request(
        "GET", f"/api/protocols/{protocol_id}/review", profile=profile
    )
    assert response.status_code == 200, response.text
    return response.json()


def _resolve(protocol_id, profile="reviewer-a", **overrides):
    body = {
        "issue_id": AMBIGUITY_ID,
        "interpretation": "원문의 ‘1718’은 17–18단계를 뜻합니다.",
        "rationale": "8페이지 원문과 파싱된 17·18단계를 대조해 확인했습니다.",
        "repeated_step_ids": RESOLVED_RANGE,
    }
    body.update(overrides)
    return request(
        "POST",
        f"/api/protocols/{protocol_id}/resolutions",
        profile=profile,
        json_body=body,
    )


def _approve(protocol_id, revision_id, profile="reviewer-a"):
    return request(
        "POST",
        f"/api/protocols/{protocol_id}/revisions/{revision_id}/approve",
        profile=profile,
    )


def test_queue_shows_one_status_vocabulary_and_no_internal_codes(catalog_server):
    rows = _queue()
    assert len(rows) == 1
    row = rows[0]
    assert row["protocol_id"] == catalog_server
    assert row["execution_readiness"]["state"] == "needs_clarification"
    assert row["execution_readiness"]["display_label"] == "원문 해석 확인 필요"
    assert row["needs_resolution_count"] == 1
    assert row["human_checkpoint_count"] == 2
    # Every label a reviewer reads is Korean product copy, not a lifecycle code.
    for label in row["display_labels"].values():
        assert "_" not in label


def test_reviewer_cannot_approve_while_a_true_ambiguity_remains(catalog_server):
    review = _review(catalog_server)
    assert review["execution_readiness"]["can_approve_for_execution"] is False
    assert "approve_for_execution" not in review["reviewer_actions"]

    refused = _approve(catalog_server, review["revision_id"])
    assert refused.status_code == 403
    assert _review(catalog_server)["available_for_execution"] is False


def test_human_checkpoints_are_informational_not_blockers(catalog_server):
    review = _review(catalog_server)
    checkpoints = review["human_checkpoints"]
    assert {item["gate_step_label"] for item in checkpoints} == {"7", "9"}
    for checkpoint in checkpoints:
        assert checkpoint["blocks_execution"] is False
        assert checkpoint["display_label"] == "연구자 확인 단계"
        assert "실험 중 연구자가 직접 확인합니다" in checkpoint["display_detail"]
    blockers = review["execution_readiness"]["blockers"]
    assert [item["code"] for item in blockers] == ["unresolved_ambiguity"]


def test_full_reviewer_to_researcher_journey(catalog_server):
    before = _review(catalog_server)

    resolved = _resolve(catalog_server)
    assert resolved.status_code == 201, resolved.text
    packet = resolved.json()
    assert packet["revision_id"] != before["revision_id"]
    assert packet["execution_readiness"]["state"] == "ready_for_execution_approval"
    assert packet["needs_resolution"] == []
    assert {item["gate_step_label"] for item in packet["human_checkpoints"]} == {
        "7",
        "9",
        "20",
    }

    approved = _approve(catalog_server, packet["revision_id"])
    assert approved.status_code == 200, approved.text
    entry = approved.json()
    assert entry["approval_status"] == "approved"
    assert entry["available_for_execution"] is True
    assert entry["approval"]["actor_principal_id"] == "principal-reviewer-a"
    assert entry["approval"]["actor_role"] == "reviewer"

    # The researcher's selector reflects the decision with no second approval.
    catalog = request("GET", "/api/protocols", profile="researcher-a")
    assert catalog.status_code == 200
    selectable = [
        item
        for item in catalog.json()["protocols"]
        if item["protocol_id"] == catalog_server
    ]
    assert selectable and selectable[0]["available_for_execution"] is True
    assert selectable[0]["development_only"] is False


def test_revocation_is_offered_only_after_approval_and_is_reversible(catalog_server):
    packet = _resolve(catalog_server).json()
    revision_id = packet["revision_id"]
    _approve(catalog_server, revision_id)

    revoked = request(
        "POST",
        f"/api/protocols/{catalog_server}/revisions/{revision_id}/revoke",
        profile="reviewer-a",
        json_body={"comment": "새 세척 시약 도입으로 재검토합니다."},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["available_for_execution"] is False

    review = _review(catalog_server)
    assert review["execution_readiness"]["state"] == "approval_revoked"
    assert review["execution_readiness"]["can_approve_for_execution"] is True
    assert "revoke_execution_approval" not in review["reviewer_actions"]

    reapproved = _approve(catalog_server, revision_id)
    assert reapproved.status_code == 200
    assert reapproved.json()["available_for_execution"] is True


def test_role_boundaries_hold_for_every_new_reviewer_action(catalog_server):
    assert request(
        "GET", "/api/protocols/review-queue", profile="researcher-a"
    ).status_code == 403
    assert _resolve(catalog_server, profile="researcher-a").status_code == 403
    review = _review(catalog_server)
    assert (
        _approve(catalog_server, review["revision_id"], profile="researcher-a")
        .status_code
        == 403
    )
    assert request("GET", "/api/protocols/review-queue").status_code == 401


def test_resolution_rejects_a_range_the_server_cannot_execute(catalog_server):
    refused = _resolve(
        catalog_server,
        repeated_step_ids=["candidate-a-step-17", "candidate-a-step-19"],
    )
    assert refused.status_code == 422
    assert _review(catalog_server)["execution_readiness"]["state"] == (
        "needs_clarification"
    )


def test_health_and_readiness_endpoints_still_answer(catalog_server):
    assert request("GET", "/healthz").status_code == 200
    assert request("GET", "/readyz").status_code in {200, 503}


def _workspace_revision_for(protocol_id, tmp_path, *, source_status="Published"):
    """Register a governance lineage revision linked to the catalog protocol."""

    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    reviewer = Principal(
        principal_id="principal-reviewer-a",
        subject="dev:reviewer-a",
        organization_id="tenant-a",
        display_name="Reviewer A",
        roles=frozenset({Role.REVIEWER, Role.LAB_ADMIN}),
        authentication_method="development",
    )
    store.bootstrap_principal(reviewer)
    family = store.create_protocol_family(reviewer, title="In-gel digestion")
    source = store.register_source(
        reviewer,
        connector_kind="local_pdf",
        external_id="upload:in-gel-digestion.pdf",
        version_identity="v1",
        source_hash="0" * 64,
        canonical_url=None,
        metadata={
            "source_status": source_status,
            "risk_state": "review_required",
            "catalog_protocol_id": protocol_id,
        },
    )
    revision = store.add_protocol_revision(
        reviewer,
        family_id=family.family_id,
        source_id=source.source_id,
        content={"steps": ["Exact source import"], "warnings": []},
        change_summary="Exact source import",
    )
    store.close()
    return revision.revision_id


def test_governance_approval_of_a_linked_revision_also_authorises_execution(
    catalog_server, tmp_path
):
    revision_id = _workspace_revision_for(catalog_server, tmp_path / "workspace")

    # While a true ambiguity remains, the reviewer is refused before anything is
    # written, so "approved here / unapproved there" cannot happen.
    refused = request(
        "POST",
        f"/api/workspace/reviewer/revisions/{revision_id}/decision",
        profile="reviewer-a",
        json_body={
            "action": "approved",
            "comment": "원문 확인 완료",
            "idempotency_key": "decision-1",
        },
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "protocol_not_ready_for_execution_approval"
    assert _review(catalog_server)["available_for_execution"] is False

    _resolve(catalog_server)
    accepted = request(
        "POST",
        f"/api/workspace/reviewer/revisions/{revision_id}/decision",
        profile="reviewer-a",
        json_body={
            "action": "approved",
            "comment": "해석 확정 후 실행 승인",
            "idempotency_key": "decision-2",
        },
    )
    assert accepted.status_code == 200, accepted.text
    payload = accepted.json()
    assert payload["state"]["state"] == "approved"
    assert payload["execution"]["state"] == "approved_for_execution"
    assert _review(catalog_server)["available_for_execution"] is True

    revoked = request(
        "POST",
        f"/api/workspace/reviewer/revisions/{revision_id}/decision",
        profile="reviewer-a",
        json_body={
            "action": "revoked",
            "comment": "재검토가 필요합니다.",
            "idempotency_key": "decision-3",
        },
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["execution"]["state"] == "approval_revoked"
    assert _review(catalog_server)["available_for_execution"] is False
