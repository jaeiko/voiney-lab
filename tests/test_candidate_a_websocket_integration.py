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
    CuratedProtocolSession,
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
from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.workspace_store import (
    WorkspaceSettings,
    initialize_workspace_store,
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

        async def immediate_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

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
                "voice_workflow_agent.server.synthesize",
                return_value=b"\x00\x00" * 320,
            ), patch(
                "voice_workflow_agent.server.asyncio.to_thread",
                side_effect=immediate_to_thread,
            ):
                asyncio.run(voice_socket(socket))

            # Verify session ready invariant
            ready = next(item for item in socket.sent if item["type"] == "session.ready")
            self.assertEqual(ready["protocol_id"], protocol_id)
            self.assertEqual(ready["mode"], "cascade")

            curated_state = next(item for item in socket.sent if item["type"] == "protocol.fixture.state")
            self.assertEqual(curated_state["action"], "attached")
            self.assertFalse(curated_state["state"]["active"])
            self.assertEqual(curated_state["state"]["workflow_status"], "ready")
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

    def test_workspace_voice_session_is_durable_and_ready_recovery_does_not_start_protocol(
        self,
    ) -> None:
        protocol_id = "candidate-a-curated-development-v1"
        placeholder = Path("/tmp/offline-session-contract")
        config = ServerConfig(
            placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
            None, None, placeholder, placeholder, placeholder,
        )

        class Socket:
            def __init__(self, start_payload):
                self.sent = []
                self.headers = {"x-voice-dev-profile": "researcher-a"}
                self.query_params = {}
                self.messages = iter((
                    {"text": json.dumps(start_payload)},
                    {"type": "websocket.disconnect", "code": 1000},
                ))

            async def accept(self):
                pass

            async def send_text(self, value):
                self.sent.append(json.loads(value))

            async def send_bytes(self, value):
                pass

            async def receive(self):
                return next(self.messages)

        profile = {
            "profile_id": "researcher-a",
            "principal_id": "principal-researcher-a",
            "organization_id": "tenant-a",
            "display_name": "Researcher A",
            "roles": ["researcher"],
        }
        principal = Principal(
            principal_id=profile["principal_id"],
            subject="dev:researcher-a",
            organization_id=profile["organization_id"],
            display_name=profile["display_name"],
            roles=frozenset({Role.RESEARCHER}),
            authentication_method="development",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            store = initialize_workspace_store(
                WorkspaceSettings(True, workspace_dir)
            )
            store.bootstrap_principal(principal)
            store.bind_resource(principal, "protocol_catalog", protocol_id)
            store.close()
            environment = {
                "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "true",
                "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR": str(workspace_dir),
                "VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo",
                "VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES": json.dumps([profile]),
            }
            initial = {
                "type": "session.start",
                "configuration_id": 1,
                "mode": "cascade",
                "language": "ko",
                "protocol_id": protocol_id,
            }
            first = Socket(initial)
            with patch.dict("os.environ", environment, clear=False), patch(
                "voice_workflow_agent.server.server_config", return_value=config
            ), patch(
                "voice_workflow_agent.server.ExperimentReportSettings.from_environment",
                return_value=ExperimentReportSettings(False),
            ), patch(
                "voice_workflow_agent.server.load_curated_protocol_fixture",
                return_value=self.fixture,
            ):
                asyncio.run(voice_socket(first))
            ready = next(item for item in first.sent if item["type"] == "session.ready")
            session_id = ready["experiment_session_id"]
            version = ready["experiment_session_version"]
            self.assertFalse(ready["experiment_recovered"])
            first_state = next(
                item["state"] for item in first.sent
                if item["type"] == "experiment.session.state"
            )
            self.assertEqual(first_state["status"], "ready")

            recovery = {
                **initial,
                "configuration_id": 2,
                "experiment_session_id": session_id,
                "experiment_session_version": version,
            }
            second = Socket(recovery)
            with patch.dict("os.environ", environment, clear=False), patch(
                "voice_workflow_agent.server.server_config", return_value=config
            ), patch(
                "voice_workflow_agent.server.ExperimentReportSettings.from_environment",
                return_value=ExperimentReportSettings(False),
            ), patch(
                "voice_workflow_agent.server.load_curated_protocol_fixture",
                return_value=self.fixture,
            ):
                asyncio.run(voice_socket(second))
            recovered = next(
                item for item in second.sent if item["type"] == "session.ready"
            )
            self.assertEqual(recovered["experiment_session_id"], session_id)
            self.assertTrue(recovered["experiment_recovered"])
            fixture_state = next(
                item["state"] for item in second.sent
                if item["type"] == "protocol.fixture.state"
            )
            self.assertFalse(fixture_state["active"])
            self.assertEqual(fixture_state["workflow_status"], "ready")

    def test_multi_turn_live_microphone_reproduction(self) -> None:
        """Reproduce exact live microphone turn sequence and assert authoritative state transitions."""
        session = CuratedProtocolSession(self.fixture)
        session.configure_ready()
        self.assertFalse(session.active)
        self.assertEqual(session.workflow_status, "preview")

        # Turn 0: Multilingual AGENT_META query from English STT
        p0 = session.plan("Then what's your function?", turn_id=1, language="ko")
        self.assertEqual(p0.action, CuratedProtocolAction.AGENT_META)
        self.assertFalse(session.active)
        self.assertIn("보이스 워크플로", p0.primary_text or "")

        # Turn 1: START with Korean object particle "실험을 시작해줘."
        p1 = session.plan("실험을 시작해줘.", turn_id=2, language="ko")
        self.assertEqual(p1.action, CuratedProtocolAction.START)
        self.assertTrue(session.active)
        self.assertEqual(session.workflow_status, "active")
        self.assertEqual(session.current_index, 0)
        timer_status = session.experiment_timer_status()
        self.assertEqual(timer_status["state"], "running")
        self.assertIsNotNone(timer_status["started_at"])

        # Turn 2: COMPLETE with particle "현재 단계로 완료했어."
        p2 = session.plan("현재 단계로 완료했어.", turn_id=3, language="ko")
        self.assertEqual(p2.action, CuratedProtocolAction.NEXT)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 1)  # Advanced to Step 2
        self.assertNotIn("아직 실험을 시작하지 않았습니다", p2.speech_text or "")

        # Turn 3: Multi-entity question with STT near-miss "뱀드" -> "단백질 밴드" + AMBIC
        p3 = session.plan(
            "여기서 염색된 단백질 뱀드가 무엇이며 그리고 AMBIC가 무엇인지 설명해 줄 수 있어?",
            turn_id=4, language="ko",
        )
        self.assertEqual(p3.action, CuratedProtocolAction.RELATED_QUESTION)
        self.assertIn("stained_protein_band", p3.requested_entities)
        self.assertIn("ambic", p3.requested_entities)
        self.assertIn("단백질 밴드", p3.primary_text or "")
        self.assertIn("AMBIC", p3.primary_text or "")
        self.assertEqual(session.current_index, 1)

    def test_acceptance_session_exact_turn_sequence(self) -> None:
        """Reproduce the exact acceptance session sequence:
        Turn 4: '그러면 실험 시작할게.' -> START
        Turn 7: 'AMBIC가 어떻게 생겼는지 그 사진으로 좀 그 외부 검색을 통해 찾아서 알려줄 수 있어, 뭐 인터넷 검색을 통해서라든지.' -> VISUAL_REQUEST
        Turn 9: '현재 단계를 완료했어.' -> NEXT
        """
        session = CuratedProtocolSession(self.fixture)
        session.configure_ready()
        self.assertFalse(session.active)
        self.assertEqual(session.workflow_status, "preview")

        # Turn 4: START with conversational lead-in "그러면 실험 시작할게."
        t4 = session.plan("그러면 실험 시작할게.", turn_id=4, language="ko")
        self.assertEqual(t4.action, CuratedProtocolAction.START)
        self.assertTrue(session.active)
        self.assertEqual(session.workflow_status, "active")
        self.assertEqual(session.current_index, 0)

        # Turn 7: AMBIC visual lookup query
        t7 = session.plan(
            "AMBIC가 어떻게 생겼는지 그 사진으로 좀 그 외부 검색을 통해 찾아서 알려줄 수 있어, 뭐 인터넷 검색을 통해서라든지.",
            turn_id=7, language="ko",
        )
        self.assertEqual(t7.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertIn("ambic", t7.requested_entities)
        self.assertIn("AMBIC", t7.speech_text or "")
        # CRITICAL INVARIANT: active workflow state MUST NOT BE LOST
        self.assertTrue(session.active)
        self.assertEqual(session.workflow_status, "active")
        self.assertEqual(session.current_index, 0)
        self.assertNotIn("아직 실험을 시작하지 않았습니다", t7.speech_text or "")

        # Turn 9: Step 1 completion
        t9 = session.plan("현재 단계를 완료했어.", turn_id=9, language="ko")
        self.assertEqual(t9.action, CuratedProtocolAction.NEXT)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 1)  # Advanced to Step 2
        self.assertNotIn("아직 실험을 시작하지 않았습니다", t9.speech_text or "")

    def test_candidate_a_sequential_natural_completion_and_question_guard(self) -> None:
        """Verify sequential natural Korean completion:
        1. '실험을 시작해 줘.' -> Step 1
        2. '현재 단계를 완료했어.' -> Step 2
        3. '현재 단계도 완료했어.' -> Step 3
        4. '이번 단계도 완료했어.' -> Step 4
        5. '현재 단계 완료 조건이 뭐야?' -> Step remains 4
        """
        session = CuratedProtocolSession(self.fixture)
        session.configure_ready()

        # 1. Start experiment
        t1 = session.plan("실험을 시작해 줘.", turn_id=1, language="ko")
        self.assertEqual(t1.action, CuratedProtocolAction.START)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 0)  # Step 1

        # 2. Step 1 -> Step 2: "현재 단계를 완료했어."
        t2 = session.plan("현재 단계를 완료했어.", turn_id=2, language="ko")
        self.assertEqual(t2.action, CuratedProtocolAction.NEXT)
        self.assertEqual(session.current_index, 1)  # Step 2

        # 3. Step 2 -> Step 3: "현재 단계도 완료했어."
        t3 = session.plan("현재 단계도 완료했어.", turn_id=3, language="ko")
        self.assertEqual(t3.action, CuratedProtocolAction.NEXT)
        self.assertEqual(session.current_index, 2)  # Step 3

        # 4. Step 3 -> Step 4: "이번 단계도 완료했어."
        t4 = session.plan("이번 단계도 완료했어.", turn_id=4, language="ko")
        self.assertEqual(t4.action, CuratedProtocolAction.NEXT)
        self.assertEqual(session.current_index, 3)  # Step 4

        # 5. Question: "현재 단계 완료 조건이 뭐야?" -> Step remains 4
        t5 = session.plan("현재 단계 완료 조건이 뭐야?", turn_id=5, language="ko")
        self.assertNotEqual(t5.action, CuratedProtocolAction.NEXT)
        self.assertEqual(session.current_index, 3)  # Still Step 4


if __name__ == "__main__":
    unittest.main()
