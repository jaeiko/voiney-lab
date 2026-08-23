from __future__ import annotations

import hashlib

import pytest

from voice_workflow_agent.eln_connectors import (
    ELabFtwConnector,
    CompletedStep,
    ElnConfirmationRequiredError,
    ElnHttpResult,
    ElnWritebackError,
    ExperimentWriteback,
    Observation,
)
from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.workspace_store import (
    ApprovalReplayError,
    WorkspaceSettings,
    initialize_workspace_store,
)


class FakeElnTransport:
    def __init__(self, location="https://eln.example.test/api/v2/experiments/321"):
        self.location = location
        self.calls = []

    def post(self, url, *, headers, json_body):
        self.calls.append(("POST", url, dict(headers), dict(json_body)))
        return ElnHttpResult(201, {"location": self.location})

    def patch(self, url, *, headers, json_body):
        self.calls.append(("PATCH", url, dict(headers), dict(json_body)))
        return ElnHttpResult(204, {})


def _experiment(source_status="In development"):
    return ExperimentWriteback(
        report_id="report-123",
        protocol_id="protocol-ankom",
        protocol_revision_id="revision-ankom-v1",
        protocol_title="ANKOM leaf carbon fractions",
        protocol_version="V.1",
        protocol_source_url="https://www.protocols.io/view/yinfude",
        source_status=source_status,
        started_at="2026-08-22T09:00:00+00:00",
        ended_at="2026-08-22T12:00:00+00:00",
        completed_steps=(
            CompletedStep(
                "step-1",
                "2026-08-22T09:10:00+00:00",
                "Add unpublished proprietary reagent instructions",
            ),
        ),
        observations=(
            Observation("step-1", "2026-08-22T09:11:00+00:00", "sample dry"),
        ),
        timer_events=({"step_id": "step-1", "duration_seconds": 600},),
        deviations=("Timer restarted after documented pause.",),
        report_url="https://voice.example.test/reports/report-123",
    )


def _researcher():
    return Principal(
        principal_id="principal-researcher",
        subject="test:researcher",
        organization_id="tenant-a",
        display_name="Researcher",
        roles=frozenset({Role.RESEARCHER}),
        authentication_method="test",
    )


def test_elabftw_requires_confirmation_before_any_network_write():
    transport = FakeElnTransport()
    connector = ELabFtwConnector(
        server_configured_base_url="https://eln.example.test",
        api_key="server-side-key",
        transport=transport,
    )
    with pytest.raises(ElnConfirmationRequiredError):
        connector.write_completed_experiment(_experiment(), confirmed=False)
    assert transport.calls == []


def test_elabftw_real_create_then_patch_contract_excludes_raw_audio_and_unpublished_text():
    transport = FakeElnTransport()
    connector = ELabFtwConnector(
        server_configured_base_url="https://eln.example.test",
        api_key="server-side-key",
        transport=transport,
    )
    result = connector.write_completed_experiment(_experiment(), confirmed=True)

    assert [call[0] for call in transport.calls] == ["POST", "PATCH"]
    assert transport.calls[0][1] == "https://eln.example.test/api/v2/experiments"
    assert transport.calls[0][3] == {}
    assert transport.calls[1][1] == "https://eln.example.test/api/v2/experiments/321"
    payload = transport.calls[1][3]
    assert set(payload) == {"title", "date", "body"}
    assert payload["date"] == "2026-08-22"
    assert "revision-ankom-v1" in payload["body"]
    assert "sample dry" in payload["body"]
    assert "Timer restarted" in payload["body"]
    assert "unpublished proprietary reagent" not in payload["body"]
    assert "raw audio" in payload["body"].casefold()
    assert "server-side-key" not in str(payload)
    assert result.external_experiment_id == "321"
    assert len(result.request_sha256) == 64


def test_elabftw_rejects_cross_origin_location_to_prevent_followup_ssrf():
    transport = FakeElnTransport("https://attacker.example/api/v2/experiments/321")
    connector = ELabFtwConnector(
        server_configured_base_url="https://eln.example.test",
        api_key="server-side-key",
        transport=transport,
    )
    with pytest.raises(ElnWritebackError, match="unsafe"):
        connector.write_completed_experiment(_experiment(), confirmed=True)
    assert [call[0] for call in transport.calls] == ["POST"]


def test_writeback_audit_is_tenant_scoped_append_only_and_idempotent(tmp_path):
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    researcher = _researcher()
    admin = Principal(
        principal_id="principal-admin",
        subject="test:admin",
        organization_id="tenant-a",
        display_name="Admin",
        roles=frozenset({Role.LAB_ADMIN}),
        authentication_method="test",
    )
    store.bootstrap_principal(researcher)
    store.bootstrap_principal(admin)
    try:
        family = store.create_protocol_family(researcher, title="Protocol")
        source = store.register_source(
            researcher,
            connector_kind="local_pdf",
            external_id="source-pdf",
            version_identity="v1",
            source_hash=hashlib.sha256(b"pdf").hexdigest(),
            canonical_url=None,
            metadata={"source_status": "Published"},
        )
        revision = store.add_protocol_revision(
            researcher,
            family_id=family.family_id,
            source_id=source.source_id,
            content={"steps": ["One"]},
            change_summary="Initial",
        )
        experiment = store.start_experiment(
            researcher,
            session_id="experiment-report-123",
            protocol_id="protocol-a",
            protocol_revision_id="runtime-revision-a",
            current_step_id="step-1",
            current_step_label="1",
        )
        connector = store.configure_connector(
            admin,
            connector_kind="elabftw",
            display_name="Pilot eLabFTW",
            credential_reference="secret://tenant-a/elabftw",
            allowed_roots=("https://eln.example.test",),
        )
        store.bind_resource(researcher, "experiment_report", "report-123")
        writeback = store.record_eln_writeback(
            researcher,
            connector_id=connector.connector_id,
            experiment_session_id=experiment["session_id"],
            report_id="report-123",
            protocol_revision_id=revision.revision_id,
            external_experiment_id="321",
            request_sha256="a" * 64,
            idempotency_key="writeback-report-123-v1",
        )
        assert writeback.startswith("eln-writeback-")
        persisted = store._connection.execute(
            """SELECT report_id,experiment_session_id
            FROM eln_writeback_events WHERE writeback_id=?""",
            (writeback,),
        ).fetchone()
        assert tuple(persisted) == ("report-123", experiment["session_id"])
        with pytest.raises(ApprovalReplayError):
            store.record_eln_writeback(
                researcher,
                connector_id=connector.connector_id,
                experiment_session_id=experiment["session_id"],
                report_id="report-123",
                protocol_revision_id=revision.revision_id,
                external_experiment_id="322",
                request_sha256="b" * 64,
                idempotency_key="writeback-report-123-v1",
            )
    finally:
        store.close()
