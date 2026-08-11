"""Experiment report service tests use isolated temporary SQLite files only."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.experiment_reports import ExperimentReportStore


class ExperimentReportStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ExperimentReportStore(
            Path(self.temporary.name) / "reports.sqlite"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def open(self):
        return self.store.open_report(
            session_id="session-test-1",
            protocol_id="candidate-a-curated-development-v1",
            protocol_title="Candidate A development fixture",
            protocol_revision="revision-test-1",
            protocol_sha256="6" * 64,
            readiness_status="analysis_required",
            development_only=True,
        )

    def test_one_report_per_session_and_idempotent_events(self):
        first = self.open()
        second = self.open()
        self.assertEqual(first["report_id"], second["report_id"])
        one = self.store.append_event(
            first["report_id"], event_key="turn-1-step",
            event_type="step_completed", step_id="step-1", step_label="1",
            payload={"operation_id": "operation-1"},
        )
        duplicate = self.store.append_event(
            first["report_id"], event_key="turn-1-step",
            event_type="step_completed", step_id="step-1", step_label="1",
            payload={"operation_id": "operation-1"},
        )
        self.assertTrue(one["event_inserted"])
        self.assertFalse(duplicate["event_inserted"])
        self.assertEqual(len(duplicate["events"]), 1)

    def test_anomaly_and_finalization_are_counted_exactly_once(self):
        report = self.open()
        for _ in range(2):
            report = self.store.append_event(
                report["report_id"], event_key="anomaly-turn-2",
                event_type="anomaly", step_id="step-4", step_label="4",
                user_wording="예상과 다르게 색이 남아 있어.",
                category="protocol_block", severity="unknown",
                confirmation_state="user_reported",
            )
        self.assertEqual(report["anomaly_count"], 1)
        finalized = self.store.finalize(
            report["report_id"], status="stopped", event_key="stop-1"
        )
        repeated = self.store.finalize(
            report["report_id"], status="stopped", event_key="stop-1"
        )
        self.assertEqual(finalized["finalization_version"], 1)
        self.assertEqual(repeated["finalization_version"], 1)
        self.assertEqual(repeated["status"], "stopped")

    def test_markdown_and_json_exports_exclude_hidden_reasoning(self):
        report = self.open()
        self.store.append_event(
            report["report_id"], event_key="source-turn-3",
            event_type="source_consulted", source_tier="approved_lab_corpus",
            citation_identities=("a" * 64,), payload={"summary": "cited source"},
        )
        exported = json.loads(self.store.export_json(report["report_id"]))
        markdown = self.store.export_markdown(report["report_id"]).decode()
        self.assertEqual(exported["readiness_status"], "analysis_required")
        self.assertTrue(exported["development_only"])
        self.assertIn("source_consulted", markdown)
        self.assertNotIn("chain_of_thought", json.dumps(exported))


if __name__ == "__main__":
    unittest.main()
