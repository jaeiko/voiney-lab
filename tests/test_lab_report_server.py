"""The server's part of the researcher's report (lane RP, decisions of 2026-10-06).

Fake sessions and a temporary report store only; no provider is called.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from voiney_lab import server
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


if __name__ == "__main__":
    unittest.main()
