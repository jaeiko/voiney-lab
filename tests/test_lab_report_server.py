"""The server's part of the researcher's report (lane RP, decisions of 2026-10-06).

Fake sessions and a temporary report store only; no provider is called.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import asyncio
import os

import httpx

from voiney_lab import server
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSpeechMode
from voiney_lab.experiment_reports import ExperimentReportStore


def _session(store: ExperimentReportStore) -> tuple[SimpleNamespace, SimpleNamespace]:
    fixture = SimpleNamespace(
        protocol_id="protocol-lab", title="Test digestion protocol", revision_id="rev-test-1",
        source_pdf_sha256="9" * 64, development_only=True,
        draft=SimpleNamespace(readiness=SimpleNamespace(status=SimpleNamespace(value="guidance_ready"))),
    )
    session = SimpleNamespace(experiment_report_store=store, experiment_report_id=None,
                              session_id="session-lab-server")
    return session, SimpleNamespace(fixture=fixture)


class ExperimenterTests(unittest.TestCase):
    """Decision 5: 실험자 is the display name of who started the experiment."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ExperimentReportStore(Path(self.temporary.name) / "r.sqlite")
        self.addCleanup(self.temporary.cleanup)
        patcher = mock.patch.object(server, "_scope_tenant_resource")
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_as(self, principal: object | None, *, workspace: bool = True) -> dict:
        session, curated = _session(self.store)
        token = server._REQUEST_PRINCIPAL.set(principal)
        try:
            with mock.patch.object(server, "_workspace_settings",
                                   return_value=SimpleNamespace(enabled=workspace)):
                return server._open_experiment_report(session, curated)
        finally:
            server._REQUEST_PRINCIPAL.reset(token)

    def test_the_signed_in_display_name_is_recorded_once(self) -> None:
        report = self.open_as(SimpleNamespace(display_name="  Local   Lab Admin "))
        names = [event["payload"]["display_name"] for event in report["events"]
                 if event["event_type"] == "experimenter_recorded"]
        self.assertEqual(names, ["Local Lab Admin"])
        again = self.open_as(SimpleNamespace(display_name="Someone Else"))
        self.assertEqual([event["payload"]["display_name"] for event in again["events"]
                          if event["event_type"] == "experimenter_recorded"], ["Local Lab Admin"])
        self.assertIn("| 실험자 | Local Lab Admin |",
                      self.store.export_markdown(report["report_id"], fixture=None).decode())

    def test_nobody_is_named_without_a_workspace_or_a_principal(self) -> None:
        for principal, workspace in ((SimpleNamespace(display_name="Admin"), False), (None, True)):
            with self.subTest(workspace=workspace):
                report = self.open_as(principal, workspace=workspace)
                self.assertNotIn("experimenter_recorded",
                                 [event["event_type"] for event in report["events"]])



def _plan(action: CuratedProtocolAction, *, completion: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        action=action, state_changed=True, intent_kind="control", answer_origin="current_protocol",
        reported_observation=False, observation_predicate=None, observation_outcome=None,
        reported_completion=completion, speech_mode=CuratedProtocolSpeechMode.CONTROL,
        anomaly_text=None, anomaly_category=None, reported_anomaly=False, evidence_ids=(),
        step_label="1",
    )


class PreparedAtTheEndTests(unittest.TestCase):
    """Decision 9: the server starts the report's prose when the experiment ends."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ExperimentReportStore(Path(self.temporary.name) / "r.sqlite")
        for target in ("_scope_tenant_resource", "_record_workspace_metric"):
            patcher = mock.patch.object(server, target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def session(self, *, active: bool, status: str) -> tuple[SimpleNamespace, SimpleNamespace]:
        session, curated = _session(self.store)
        step = SimpleNamespace(step_id="step-1", source_label="1")
        curated.fixture.steps = [step]
        curated.current_index = 0
        curated.active = active
        curated._workflow_status = status
        curated.experiment_timer_status = lambda: None
        curated.timer_status = lambda: None
        return session, curated

    def record(self, plan: SimpleNamespace, *, active: bool, status: str) -> mock.MagicMock:
        session, curated = self.session(active=active, status=status)
        with mock.patch.object(server, "_prepare_report_prose", return_value="preparing") as prepare:
            server._record_experiment_report_plan(
                session, curated, plan, turn_id=1, generation=1, pre_transition_index=0)
        return prepare

    def test_stopping_starts_the_preparation(self) -> None:
        prepare = self.record(_plan(CuratedProtocolAction.STOP), active=False, status="stopped")
        prepare.assert_called_once()
        self.assertEqual(self.store.get_report(prepare.call_args.args[1])["status"], "stopped")

    def test_completing_the_last_step_starts_the_preparation(self) -> None:
        prepare = self.record(_plan(CuratedProtocolAction.NEXT, completion=True),
                              active=False, status="completed")
        prepare.assert_called_once()
        self.assertEqual(self.store.get_report(prepare.call_args.args[1])["status"], "completed")

    def test_an_ordinary_step_does_not(self) -> None:
        prepare = self.record(_plan(CuratedProtocolAction.NEXT, completion=True),
                              active=True, status="in_progress")
        prepare.assert_not_called()

    def test_without_a_report_model_the_end_leaves_the_servers_sentences_ready(self) -> None:
        session, curated = self.session(active=False, status="stopped")
        with mock.patch.dict(os.environ, {"VOINEY_LAB_REPORT_WRITER_ENABLED": "false"}):
            report = server._record_experiment_report_plan(
                session, curated, _plan(CuratedProtocolAction.STOP),
                turn_id=1, generation=1, pre_transition_index=0)
        self.assertEqual(self.store.get_prose(report["report_id"])["writer"], "서버")


class ProseRouteTests(unittest.TestCase):
    """GET and POST /api/experiment-reports/{id}/prose (decision 9)."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ExperimentReportStore(Path(self.temporary.name) / "r.sqlite")
        report = self.store.open_report(
            session_id="session-route", protocol_id="protocol-lab", protocol_title="Test",
            protocol_revision="rev-test-1", protocol_sha256="9" * 64,
            readiness_status="guidance_ready", development_only=True)
        self.report_id = report["report_id"]
        patches = [
            mock.patch.dict(os.environ, {
                "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "true",
                "VOINEY_LAB_EXPERIMENT_REPORT_DB": str(self.store.path),
                "VOINEY_LAB_REPORT_WRITER_ENABLED": "false"}),
            mock.patch.object(server, "_scope_tenant_resource"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, method: str) -> httpx.Response:
        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url="http://testserver") as http:
                return await http.request(method, f"/api/experiment-reports/{self.report_id}/prose")
        return asyncio.run(run())

    def test_the_status_follows_the_experiment(self) -> None:
        response = self.call("GET")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "not_finished")
        self.store.finalize(self.report_id, status="stopped", event_key="end")
        self.assertEqual(self.call("GET").json()["state"], "not_prepared")
        again = self.call("POST")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["state"], "ready")
        self.assertIn("서버 문장", again.json()["label"])

    def test_an_unknown_report_is_not_found(self) -> None:
        self.report_id = "ER-UNKNOWN"
        self.assertEqual(self.call("GET").status_code, 404)


if __name__ == "__main__":
    unittest.main()
