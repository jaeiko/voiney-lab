"""The report writer really calls the report role's model (lane RP, decision 1).

Until lane RP ``experiment_reports`` used ``asyncio.wait_for`` without
importing asyncio: the NameError was caught and every report fell back to the
server's own prose, so the report model was never called in production. These
tests use a fake client only.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from voiney_lab import experiment_reports as er


class _FakeClient:
    def __init__(self, reply: dict | None = None, delay: float = 0.0) -> None:
        self.calls: list[dict] = []
        self.reply = reply or {}
        self.delay = delay

        async def create(**kwargs):
            self.calls.append(kwargs)
            if self.delay:
                await asyncio.sleep(self.delay)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(self.reply, ensure_ascii=False)))])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class ReportWriterCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = er.ExperimentReportStore(Path(self.temporary.name) / "r.sqlite")
        report = self.store.open_report(
            session_id="session-call-1", protocol_id="protocol-call",
            protocol_title="Call protocol", protocol_revision="rev-1",
            protocol_sha256="7" * 64, readiness_status="guidance_ready",
            development_only=True,
        )
        self.store.append_event(
            report["report_id"], event_key="turn-1", event_type="step_completed",
            step_id="step-1", step_label="1",
        )
        self.doc = self.store.get_report(report["report_id"])
        er._NARRATIVE_CACHE.clear()

    def tearDown(self) -> None:
        er._NARRATIVE_CACHE.clear()
        self.temporary.cleanup()

    def test_the_model_is_called_once_and_its_prose_is_used(self) -> None:
        client = _FakeClient({"purpose": "모델이 쓴 목적 문장이다[1]."})
        brain = er.ReportWriterBrain(
            client=client, model="fake-model", timeout_seconds=5, search_client=None)
        narrative = asyncio.run(brain.generate_narrative(self.doc, list(self.doc["events"])))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["model"], "fake-model")
        self.assertEqual(narrative.purpose, "모델이 쓴 목적 문장이다[1].")

    def test_a_model_slower_than_the_limit_falls_back_to_the_servers_prose(self) -> None:
        client = _FakeClient({"purpose": "늦은 답[1]."}, delay=1.0)
        brain = er.ReportWriterBrain(
            client=client, model="fake-model", timeout_seconds=0.05, search_client=None)
        narrative = asyncio.run(brain.generate_narrative(self.doc, list(self.doc["events"])))
        deterministic = brain.build_deterministic_narrative(self.doc, list(self.doc["events"]))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(narrative.purpose, deterministic.purpose)
        self.assertEqual(narrative.section_origin["purpose"], "대체")


if __name__ == "__main__":
    unittest.main()
