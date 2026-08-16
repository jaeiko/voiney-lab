"""End-to-end WebSocket integration test exercising Candidate A workflow hardening."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_reports import (
    ExperimentReportSettings,
    ExperimentReportStore,
)
from voice_workflow_agent.server import (
    ServerConfig,
    voice_socket,
)

ROOT = Path(__file__).resolve().parents[1]


class CandidateAWebSocketIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_path = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
        cls.provenance_path = ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
        cls.pdf_path = Path("/home/student/protocol-test-files/in-gel-digestion.pdf")
        cls.fixture = load_curated_protocol_fixture(
            cls.fixture_path,
            cls.provenance_path,
            cls.pdf_path,
        )

    def test_full_candidate_a_workflow_end_to_end(self) -> None:
        protocol_id = "candidate-a-curated-development-v1"
        placeholder = Path("/tmp/offline-session-contract")

        with tempfile.TemporaryDirectory() as tmpdir:
            reports_db = Path(tmpdir) / "reports.sqlite"
            report_settings = ExperimentReportSettings(enabled=True, database_path=reports_db)

            config = ServerConfig(
                placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
                None, None, placeholder, placeholder, placeholder,
            )

            class Socket:
                def __init__(self):
                    self.sent = []
                    self.messages = iter((
                        # 1. Start session configuration
                        {"text": json.dumps({
                            "type": "session.start",
                            "configuration_id": 1,
                            "mode": "cascade",
                            "language": "ko",
                            "protocol_id": protocol_id,
                        })},
                        # 2. Client audio ready (triggers greeting)
                        {"text": json.dumps({
                            "type": "client.audio_ready",
                            "configuration_id": 1,
                            "generation": 3,
                            "audio_context_state": "running",
                            "sample_rate": 16000,
                        })},
                        # 3. Request experiment report read-only state
                        {"text": json.dumps({
                            "type": "experiment.report.get",
                            "configuration_id": 1,
                        })},
                        # Disconnect
                        {"type": "websocket.disconnect", "code": 1000},
                    ))

                async def accept(self):
                    pass

                async def send_text(self, value):
                    self.sent.append(json.loads(value))

                async def send_bytes(self, value):
                    pass

                async def receive(self):
                    msg = next(self.messages)
                    if msg.get("type") == "websocket.disconnect":
                        await asyncio.sleep(0.05)
                    return msg

            socket = Socket()

            with patch(
                "voice_workflow_agent.server.server_config",
                return_value=config,
            ), patch(
                "voice_workflow_agent.server.ExperimentReportSettings.from_environment",
                return_value=report_settings,
            ), patch(
                "voice_workflow_agent.server.load_curated_protocol_fixture",
                return_value=self.fixture,
            ), patch(
                "voice_workflow_agent.server.ProcedureStore",
            ), patch(
                "voice_workflow_agent.server.load_procedure_definitions",
            ), patch(
                "voice_workflow_agent.server.NativeRealtimeSession",
            ), patch(
                "voice_workflow_agent.server.synthesize",
                return_value=b"\x00\x00" * 320,
            ):
                asyncio.run(voice_socket(socket))

            # Verify session ready invariant
            ready = next(item for item in socket.sent if item["type"] == "session.ready")
            self.assertEqual(ready["protocol_id"], protocol_id)
            self.assertEqual(ready["mode"], "cascade")

            curated_state = next(item for item in socket.sent if item["type"] == "protocol.fixture.state")
            self.assertEqual(curated_state["action"], "attached")
            self.assertTrue(curated_state["state"]["active"])
            self.assertEqual(curated_state["state"]["workflow_status"], "active")
            self.assertEqual(curated_state["state"]["current_step_label"], "1")

            # Verify greeting was sent
            greeting = next((item for item in socket.sent if item["type"] == "session.greeting"), None)
            self.assertIsNotNone(greeting)
            self.assertIn("Voice Workflow Agent", greeting["text"])

            # Verify experiment.report.state was returned without error
            report_state = next((item for item in socket.sent if item["type"] == "experiment.report.state"), None)
            self.assertIsNotNone(report_state)
            self.assertIn("report", report_state)
            self.assertEqual(report_state["report"]["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()
