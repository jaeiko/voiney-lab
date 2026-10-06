"""An uploaded image goes on the experiment report (lane RP, decision 7).

Drives the evidence upload endpoint with a development profile and a
temporary workspace and report store; no provider is called.
"""

from __future__ import annotations

import asyncio

from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store
from tests.test_workspace_api import _configure, _principal, _request

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image"


def _experiment(monkeypatch, tmp_path, session_id: str, *, report: bool = True) -> ExperimentReportStore:
    _configure(monkeypatch, tmp_path)
    reports = tmp_path / "reports.sqlite"
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORT_DB", str(reports))
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    researcher = _principal("researcher-a")
    store.bootstrap_principal(researcher)
    experiment = store.start_experiment(
        researcher, session_id=session_id, protocol_id="in-gel-digestion",
        protocol_revision_id="approved-revision-1",
        current_step_id="step-20", current_step_label="20",
    )
    store.record_experiment_progress(
        researcher, session_id, expected_version=experiment["version"],
        event_key="protocol-started", event_type="protocol_started",
        step_id="step-20", step_label="20",
    )
    store.close()
    report_store = ExperimentReportStore(reports)
    if not report:
        return report_store
    report_store.open_report(
        session_id=session_id, protocol_id="in-gel-digestion", protocol_title="In-gel digestion",
        protocol_revision="approved-revision-1", protocol_sha256="9" * 64,
        readiness_status="guidance_ready", development_only=False,
    )
    return report_store


def _upload(session_id: str, query: str, body: bytes, media: str):
    return asyncio.run(_request(
        "POST", f"/api/workspace/experiments/{session_id}/evidence?{query}",
        profile="researcher-a", content=body, headers={"Content-Type": media},
    ))


def _photos(store: ExperimentReportStore) -> list[dict]:
    (summary,) = store.list_reports()
    return [event for event in store.get_report(summary["report_id"])["events"]
            if event["event_type"] == "photo_attached"]


def test_an_image_upload_puts_a_photo_on_the_report_with_its_caption(monkeypatch, tmp_path):
    store = _experiment(monkeypatch, tmp_path, "photo-session-1")
    query = "filename=gel.png&idempotency_key=photo-1&caption=" + "젤 조각이 하얗게 됨"
    response = _upload("photo-session-1", query, PNG, "image/png")
    assert response.status_code == 201, response.text
    assert _upload("photo-session-1", query, PNG, "image/png").status_code == 201
    (photo,) = _photos(store)
    assert photo["step_label"] == "20"
    assert photo["user_wording"] == "젤 조각이 하얗게 됨"
    assert photo["payload"]["evidence_id"] == response.json()["evidence_id"]
    (summary,) = store.list_reports()
    text = store.export_markdown(summary["report_id"], fixture=None).decode()
    assert "| 20 | 사진 | 사진 첨부 — 젤 조각이 하얗게 됨 |" in text


def test_without_a_caption_the_file_name_is_used_and_documents_are_not_photos(monkeypatch, tmp_path):
    store = _experiment(monkeypatch, tmp_path, "photo-session-2")
    assert _upload("photo-session-2", "filename=band.png&idempotency_key=photo-2",
                   PNG, "image/png").status_code == 201
    assert _upload("photo-session-2", "filename=notes.pdf&idempotency_key=doc-1",
                   b"%PDF-1.4 fake", "application/pdf").status_code == 201
    assert [photo["user_wording"] for photo in _photos(store)] == ["band.png"]


def test_the_upload_stands_when_no_report_is_open_or_reports_are_off(monkeypatch, tmp_path):
    store = _experiment(monkeypatch, tmp_path, "photo-session-3", report=False)
    response = _upload("photo-session-3", "filename=x.png&idempotency_key=photo-3", PNG, "image/png")
    assert response.status_code == 201, response.text
    assert store.list_reports() == []
    store = _experiment(monkeypatch, tmp_path / "off", "photo-session-4")
    monkeypatch.setenv("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "false")
    response = _upload("photo-session-4", "filename=y.png&idempotency_key=photo-4", PNG, "image/png")
    assert response.status_code == 201, response.text
    assert _photos(store) == []
