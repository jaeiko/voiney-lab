"""Experiment report service tests use isolated temporary SQLite files only."""

from __future__ import annotations

import json
import csv
import io
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

    def test_report_listing_and_csv_export_are_stable_and_utf8(self):
        report = self.open()
        self.store.append_event(
            report["report_id"], event_key="turn-4-completed",
            event_type="step_completed", step_id="candidate-a-step-04",
            step_label="4", user_wording="현재 단계를 완료했어요.",
            payload={
                "completion_source": "user_command",
                "pre_transition_step_id": "candidate-a-step-04",
                "post_transition_step_id": "candidate-a-step-05",
            },
        )
        listed = self.store.list_reports(session_id="session-test-1")
        self.assertEqual([item["report_id"] for item in listed], [report["report_id"]])
        content = self.store.export_csv(report["report_id"])
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "step_completed")
        self.assertEqual(rows[0]["step_label"], "4")
        self.assertEqual(rows[0]["user_wording"], "현재 단계를 완료했어요.")
        self.assertNotIn("chain_of_thought", content.decode("utf-8-sig"))

    def test_docx_export_uses_human_readable_labels_and_blank_student_fields(self):
        report = self.open()
        self.store.append_event(
            report["report_id"], event_key="turn-3-completed",
            event_type="step_completed", step_id="candidate-a-step-03",
            step_label="3", user_wording="현재 단계를 완료했어요.",
            payload={
                "timer": {
                    "source_duration_seconds": 900,
                    "elapsed_seconds": 20,
                    "remaining_seconds": 880,
                },
            },
        )
        content = self.store.export_docx(report["report_id"])
        self.assertGreater(len(content), 100)
        self.assertTrue(content.startswith(b"PK"))
        from docx import Document
        document = Document(io.BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("Title:", text)
        self.assertIn("Course:", text)
        self.assertIn("Student number:", text)
        self.assertIn("Name:", text)
        self.assertIn("Advisor:", text)
        self.assertIn("I. Purpose", text)
        self.assertIn("II. Materials and Methods", text)
        self.assertIn("III. Results", text)
        self.assertIn("IV. Discussion", text)
        self.assertIn("V. Conclusion", text)
        self.assertIn("Step 3 완료", text)
        self.assertIn("타이머 총 15:00", text)
        self.assertIn("경과 00:20", text)
        self.assertIn("잔여 14:40", text)
        self.assertNotIn("Student number: 20", text)
        self.assertNotIn("홍길동", text)
        self.assertNotIn("chain_of_thought", text)


if __name__ == "__main__":
    unittest.main()
