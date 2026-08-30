"""End-to-end WebSocket integration test exercising Candidate A workflow hardening."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import nullcontext
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
    ListenerSession,
    ServerConfig,
    Transcription,
    TurnState,
    run_turn,
    voice_socket,
)
import voice_workflow_agent.server as server_module
from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.workspace_store import (
    WorkspaceConflictError,
    WorkspaceSettings,
    initialize_workspace_store,
)

ROOT = Path(__file__).resolve().parents[1]


class _ScriptedSocket:
    """Minimal WebSocket double that replays a fixed message sequence."""

    def __init__(self, messages, *, dev_profile="researcher-a"):
        self.sent = []
        self.headers = {"x-voice-dev-profile": dev_profile}
        self.query_params = {}
        self._messages = iter(
            (*({"text": json.dumps(m)} for m in messages),
             {"type": "websocket.disconnect", "code": 1000})
        )

    async def accept(self):
        pass

    async def send_text(self, value):
        self.sent.append(json.loads(value))

    async def send_bytes(self, value):
        pass

    async def receive(self):
        return next(self._messages)

    def ready_event(self):
        return next(item for item in self.sent if item["type"] == "session.ready")

    def error_message(self):
        return next(
            (item.get("message") for item in self.sent if item["type"] == "error"),
            None,
        )


class _HoldingSocket(_ScriptedSocket):
    """Hold one configured WebSocket open while a Voice turn is exercised."""

    def __init__(self, start_payload, *, dev_profile="researcher-a"):
        super().__init__([], dev_profile=dev_profile)
        self._start_payload = start_payload
        self._start_sent = False
        self.ready = asyncio.Event()
        self.disconnect = asyncio.Event()

    async def send_text(self, value):
        await super().send_text(value)
        if self.sent[-1]["type"] == "session.ready":
            self.ready.set()

    async def receive(self):
        if not self._start_sent:
            self._start_sent = True
            return {"text": json.dumps(self._start_payload)}
        await self.disconnect.wait()
        return {"type": "websocket.disconnect", "code": 1000}


class CandidateAWebSocketIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_path = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
        cls.provenance_path = ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
        cls.pdf_path = (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf")
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

    def test_candidate_a_session_starts_without_tenant_resource_binding(self) -> None:
        """Regression: bootstrap_development_fixture() never calls
        bind_resource() for the shared curated fixture, so it must still be
        selectable under tenant RBAC. A real, unbound tenant protocol_id must
        still be rejected."""

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
            # Deliberately no bind_resource() call: the curated fixture's own
            # bootstrap never binds it either, so this must still work.
            store.close()
            environment = {
                "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "true",
                "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR": str(workspace_dir),
                "VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo",
                "VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES": json.dumps([profile]),
            }
            patches = (
                patch(
                    "voice_workflow_agent.server.server_config",
                    return_value=config,
                ),
                patch(
                    "voice_workflow_agent.server.ExperimentReportSettings.from_environment",
                    return_value=ExperimentReportSettings(False),
                ),
                patch(
                    "voice_workflow_agent.server.load_curated_protocol_fixture",
                    return_value=self.fixture,
                ),
            )

            allowed = Socket({
                "type": "session.start",
                "configuration_id": 1,
                "mode": "cascade",
                "language": "ko",
                "protocol_id": protocol_id,
            })
            with patch.dict("os.environ", environment, clear=False):
                with patches[0], patches[1], patches[2]:
                    asyncio.run(voice_socket(allowed))
            ready = next(
                item for item in allowed.sent if item["type"] == "session.ready"
            )
            self.assertEqual(ready["protocol_id"], protocol_id)
            self.assertFalse(any(
                item.get("message") == "invalid session configuration"
                for item in allowed.sent
            ))

            denied = Socket({
                "type": "session.start",
                "configuration_id": 2,
                "mode": "cascade",
                "language": "ko",
                "protocol_id": "some-other-tenants-protocol",
            })
            with patch.dict("os.environ", environment, clear=False):
                with patches[0], patches[1], patches[2]:
                    asyncio.run(voice_socket(denied))
            self.assertTrue(any(
                item.get("message") == "invalid session configuration"
                for item in denied.sent
            ))
            self.assertFalse(any(
                item["type"] in ("session.ready", "session.started")
                for item in denied.sent
            ))

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

    def test_safe_completion_paraphrases_mutate_once_and_questions_stay_read_only(
        self,
    ) -> None:
        for utterance in (
            "1단계 끝났어",
            "이 단계 완료했어",
            "이 단계 마쳤어",
            "다 했어",
        ):
            with self.subTest(utterance=utterance):
                session = CuratedProtocolSession(self.fixture)
                session.activate_configured()
                started = session.plan(
                    "프로토콜 시작해줘", turn_id=1, language="ko"
                )
                completed = session.plan(
                    utterance, turn_id=2, language="ko"
                )
                self.assertEqual(started.action, CuratedProtocolAction.START)
                self.assertEqual(completed.action, CuratedProtocolAction.NEXT)
                self.assertTrue(completed.state_changed)
                self.assertEqual(session.current_index, 1)

        for utterance in (
            "이 단계 완료 조건이 뭐야?",
            "다음 단계는 뭐야?",
        ):
            with self.subTest(utterance=utterance):
                session = CuratedProtocolSession(self.fixture)
                session.activate_configured()
                session.plan("프로토콜 시작해줘", turn_id=1, language="ko")
                opening = (
                    session.active,
                    session.current_index,
                    session.state()["revision"],
                    session.workflow_status,
                )
                plan = session.plan(utterance, turn_id=2, language="ko")
                self.assertNotEqual(plan.action, CuratedProtocolAction.NEXT)
                self.assertFalse(plan.state_changed)
                self.assertEqual(
                    (
                        session.active,
                        session.current_index,
                        session.state()["revision"],
                        session.workflow_status,
                    ),
                    opening,
                )

    # -- Explicit reload/resume contract (client sends experiment_session_id
    # only when the user picked an open experiment) -----------------------

    def _bootstrap_tenant(self, workspace_dir):
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
        store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
        store.bootstrap_principal(principal)
        store.close()
        environment = {
            "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "true",
            "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR": str(workspace_dir),
            "VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo",
            "VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES": json.dumps([profile]),
        }
        return environment, principal

    def _run_socket(self, environment, config, socket, *, captured=None):
        listener_context = nullcontext()
        if captured is not None:
            def listener_factory(*args, **kwargs):
                listener = ListenerSession(*args, **kwargs)
                captured.append(listener)
                return listener

            listener_context = patch(
                "voice_workflow_agent.server.ListenerSession",
                side_effect=listener_factory,
            )
        with patch.dict("os.environ", environment, clear=False), patch(
            "voice_workflow_agent.server.server_config", return_value=config
        ), patch(
            "voice_workflow_agent.server.ExperimentReportSettings.from_environment",
            return_value=ExperimentReportSettings(False),
        ), patch(
            "voice_workflow_agent.server.load_curated_protocol_fixture",
            return_value=self.fixture,
        ), listener_context:
            asyncio.run(voice_socket(socket))
        return socket

    @staticmethod
    def _run_curated_turn(listener, socket, transcript, *, turn_id):
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        listener.active_turn_id = turn_id
        listener.detector.state = TurnState.PROCESSING
        with patch(
            "voice_workflow_agent.server.synthesize",
            return_value=b"\0\0",
        ), patch(
            "voice_workflow_agent.server.asyncio.to_thread",
            side_effect=immediate,
        ), patch(
            "voice_workflow_agent.server.AsyncOpenAI",
            side_effect=AssertionError("LLM must not run for workflow control"),
        ):
            asyncio.run(run_turn(
                socket, listener, b"\0\0", turn_id, 1,
                accepted_transcription=Transcription(transcript, "ko"),
                accepted_stt_ms=1,
            ))

    def _timed_workspace_listener(self, workspace_dir, principal):
        curated = CuratedProtocolSession(self.fixture)
        listener = ListenerSession(curated_protocol_session=curated)
        listener.start()
        listener.accept_configuration(
            41,
            "cascade",
            "ko",
            self.fixture.protocol_id,
            self.fixture.revision_id,
        )
        curated.active = True
        curated.current_index = 2
        curated._workflow_status = "active"
        step = self.fixture.steps[2]
        store = initialize_workspace_store(
            WorkspaceSettings(True, workspace_dir)
        )
        created = store.start_experiment(
            principal,
            session_id=listener.session_id,
            protocol_id=self.fixture.protocol_id,
            protocol_revision_id=self.fixture.revision_id,
            current_step_id=step.step_id,
            current_step_label=step.source_label,
            voice_connection_id=listener.voice_connection_id,
        )
        running = store.record_experiment_progress(
            principal,
            listener.session_id,
            expected_version=created["version"],
            expected_voice_connection_id=listener.voice_connection_id,
            event_key="setup-protocol-started",
            event_type="protocol_started",
            step_id=step.step_id,
            step_label=step.source_label,
            payload={"authority": "test_setup"},
        )
        listener.experiment_state_version = running["version"]
        store.close()
        return curated, listener, running

    @staticmethod
    def _offline_config():
        placeholder = Path("/tmp/offline-session-contract")
        return ServerConfig(
            placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
            None, None, placeholder, placeholder, placeholder,
        )

    def test_reload_reselect_recovers_same_experiment_session_id(self) -> None:
        """A: selecting an open experiment after reload and starting Voice
        recovers the exact same durable experiment_session_id."""
        protocol_id = "candidate-a-curated-development-v1"
        config = self._offline_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, _ = self._bootstrap_tenant(workspace_dir)

            first = self._run_socket(environment, config, _ScriptedSocket([{
                "type": "session.start", "configuration_id": 1,
                "mode": "cascade", "language": "ko", "protocol_id": protocol_id,
            }]))
            ready = first.ready_event()
            session_id = ready["experiment_session_id"]
            version = ready["experiment_session_version"]

            # Simulate: reload happened (fresh WS connection), the user
            # explicitly reselected the open experiment in
            # #experiment-session-select, and the fixed client sent its
            # exact id/version on the next session.start.
            second = self._run_socket(environment, config, _ScriptedSocket([{
                "type": "session.start", "configuration_id": 1,
                "mode": "cascade", "language": "ko", "protocol_id": protocol_id,
                "experiment_session_id": session_id,
                "experiment_session_version": version,
            }]))
            recovered = second.ready_event()
            self.assertEqual(recovered["experiment_session_id"], session_id)
            self.assertTrue(recovered["experiment_recovered"])

    def test_recovery_preserves_step_and_exact_revision(self) -> None:
        """B: recovering an in-progress experiment restores the same step
        and exact protocol revision without advancing anything."""
        protocol_id = "candidate-a-curated-development-v1"
        config = self._offline_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            created = store.start_experiment(
                principal, protocol_id=protocol_id,
                protocol_revision_id=self.fixture.revision_id,
                current_step_id="candidate-a-step-01", current_step_label="1",
            )
            session_id = created["session_id"]
            in_progress = store.record_experiment_progress(
                principal, session_id, event_key="e1",
                expected_version=created["version"],
                event_type="protocol_started",
                step_id="candidate-a-step-01", step_label="1",
            )
            advanced = store.record_experiment_progress(
                principal, session_id, event_key="e2",
                expected_version=in_progress["version"],
                event_type="step_advanced",
                step_id="candidate-a-step-01", step_label="1",
                next_step_id="candidate-a-step-02", next_step_label="2",
                mark_completed=True,
            )
            self.assertEqual(advanced["status"], "in_progress")
            self.assertEqual(advanced["current_step_label"], "2")
            version = advanced["version"]
            store.close()

            captured = []
            second = _HoldingSocket({
                "type": "session.start", "configuration_id": 1,
                "mode": "cascade", "language": "ko", "protocol_id": protocol_id,
                "experiment_session_id": session_id,
                "experiment_session_version": version,
            })

            def listener_factory(*args, **kwargs):
                listener = ListenerSession(*args, **kwargs)
                captured.append(listener)
                return listener

            async def recovered_current_step_scenario():
                task = asyncio.create_task(voice_socket(second))
                await asyncio.wait_for(second.ready.wait(), timeout=5)
                listener = captured[0]
                listener.active_turn_id = 1
                listener.detector.state = TurnState.PROCESSING

                async def immediate(function, *args, **kwargs):
                    return function(*args, **kwargs)

                with patch(
                    "voice_workflow_agent.server.synthesize",
                    return_value=b"\0\0",
                ), patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=immediate,
                ), patch(
                    "voice_workflow_agent.server.AsyncOpenAI",
                    side_effect=AssertionError(
                        "LLM must not run for current-step control"
                    ),
                ):
                    await run_turn(
                        second, listener, b"\0\0", 1, 1,
                        accepted_transcription=Transcription(
                            "현재 단계 알려줘", "ko"
                        ),
                        accepted_stt_ms=1,
                    )
                second.disconnect.set()
                await task

            with patch.dict("os.environ", environment, clear=False), patch(
                "voice_workflow_agent.server.server_config", return_value=config
            ), patch(
                "voice_workflow_agent.server.ExperimentReportSettings.from_environment",
                return_value=ExperimentReportSettings(False),
            ), patch(
                "voice_workflow_agent.server.load_curated_protocol_fixture",
                return_value=self.fixture,
            ), patch(
                "voice_workflow_agent.server.ListenerSession",
                side_effect=listener_factory,
            ):
                asyncio.run(recovered_current_step_scenario())
            recovered = second.ready_event()
            self.assertEqual(recovered["experiment_session_id"], session_id)
            self.assertTrue(recovered["experiment_recovered"])
            self.assertEqual(recovered["revision_id"], self.fixture.revision_id)
            restored = next(
                item["state"] for item in second.sent
                if item["type"] == "protocol.fixture.state"
            )
            self.assertTrue(restored["active"])
            self.assertEqual(restored["current_step_id"], "candidate-a-step-02")
            self.assertEqual(restored["current_step_label"], "2")

            decision = next(
                item for item in second.sent
                if item["type"] == "turn.route_decision"
            )
            spoken_state = next(
                item["state"] for item in reversed(second.sent)
                if item["type"] == "protocol.fixture.state"
            )
            reply = next(
                item["text"] for item in second.sent
                if item["type"] == "reply.complete"
            )
            self.assertEqual(decision["action"], "current")
            self.assertFalse(decision["state_mutation"])
            self.assertEqual(spoken_state["current_step_id"], "candidate-a-step-02")
            self.assertEqual(spoken_state["current_step_label"], "2")
            self.assertIn(self.fixture.steps[1].instruction_source_text, reply)

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            row = store.get_experiment(principal, session_id)
            all_sessions = store.list_experiments(principal)
            store.close()
            self.assertEqual(row["current_step_label"], "2")
            self.assertEqual(row["current_step_id"], "candidate-a-step-02")
            self.assertEqual(row["protocol_revision_id"], self.fixture.revision_id)
            self.assertEqual(row["status"], "in_progress")
            self.assertEqual(len(all_sessions), 1)

    def test_start_completion_survives_stale_audio_cancellation_and_advances_once(
        self,
    ) -> None:
        """A committed START remains authoritative if its old audio generation
        is cancelled; the next completion turn must advance Step 1 exactly once."""

        protocol_id = "candidate-a-curated-development-v1"
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)
            curated = CuratedProtocolSession(self.fixture)
            listener = ListenerSession(curated_protocol_session=curated)
            listener.start()
            curated.activate_configured()
            listener.accept_configuration(
                1, "cascade", "ko", protocol_id, self.fixture.revision_id,
            )
            first_step = self.fixture.steps[0]
            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            created = store.start_experiment(
                principal,
                session_id=listener.session_id,
                protocol_id=protocol_id,
                protocol_revision_id=self.fixture.revision_id,
                current_step_id=first_step.step_id,
                current_step_label=first_step.source_label,
                voice_connection_id=listener.voice_connection_id,
            )
            listener.experiment_state_version = created["version"]
            store.close()
            socket = _ScriptedSocket([])
            synthesis_started = asyncio.Event()
            never_release = asyncio.Event()

            def blocked_synthesize(text, language):
                raise AssertionError("controlled to_thread must own synthesis")

            async def controlled_thread(function, *args, **kwargs):
                if function is blocked_synthesize:
                    synthesis_started.set()
                    await never_release.wait()
                return function(*args, **kwargs)

            async def cancel_after_durable_start():
                listener.active_turn_id = 1
                listener.detector.state = TurnState.PROCESSING
                task = asyncio.create_task(run_turn(
                    socket, listener, b"\0\0", 1, 1,
                    accepted_transcription=Transcription(
                        "프로토콜 시작해줘", "ko"
                    ),
                    accepted_stt_ms=1,
                ))
                await asyncio.wait_for(synthesis_started.wait(), timeout=5)
                current = initialize_workspace_store(
                    WorkspaceSettings(True, workspace_dir)
                )
                persisted = current.get_experiment(principal, listener.session_id)
                current.close()
                self.assertEqual(persisted["status"], "in_progress")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            token = server_module._REQUEST_PRINCIPAL.set(principal)
            try:
                with patch.dict("os.environ", environment, clear=False), patch(
                    "voice_workflow_agent.server.synthesize",
                    new=blocked_synthesize,
                ), patch(
                    "voice_workflow_agent.server.asyncio.to_thread",
                    side_effect=controlled_thread,
                ):
                    asyncio.run(cancel_after_durable_start())
                self.assertTrue(curated.active)
                self.assertEqual(curated.workflow_status, "active")
                self.assertEqual(curated.current_index, 0)

                with patch.dict("os.environ", environment, clear=False):
                    self._run_curated_turn(
                        listener, socket, "현재 단계를 완료했어", turn_id=2,
                    )
                    listener.playback_ended(2)
                    version_after_completion = listener.experiment_state_version
                    self._run_curated_turn(
                        listener, socket, "현재 단계를 완료했어", turn_id=2,
                    )

                store = initialize_workspace_store(
                    WorkspaceSettings(True, workspace_dir)
                )
                completed = store.get_experiment(principal, listener.session_id)
                store.close()
            finally:
                server_module._REQUEST_PRINCIPAL.reset(token)

            self.assertEqual(curated.current_index, 1)
            self.assertEqual(completed["status"], "in_progress")
            self.assertEqual(completed["current_step_id"], "candidate-a-step-02")
            self.assertEqual(completed["current_step_label"], "2")
            self.assertEqual(
                [item["step_id"] for item in completed["completed_steps"]],
                ["candidate-a-step-01"],
            )
            self.assertEqual(completed["version"], version_after_completion)

    def test_workspace_rejection_rolls_back_without_false_report_event(self) -> None:
        """The durable experiment is the mutation authority: an auxiliary
        report must not claim START when the workspace commit was rejected."""

        protocol_id = "candidate-a-curated-development-v1"
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)
            curated = CuratedProtocolSession(self.fixture)
            listener = ListenerSession(curated_protocol_session=curated)
            listener.start()
            curated.activate_configured()
            listener.accept_configuration(
                1, "cascade", "ko", protocol_id, self.fixture.revision_id,
            )
            first_step = self.fixture.steps[0]
            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            created = store.start_experiment(
                principal,
                session_id=listener.session_id,
                protocol_id=protocol_id,
                protocol_revision_id=self.fixture.revision_id,
                current_step_id=first_step.step_id,
                current_step_label=first_step.source_label,
                voice_connection_id=listener.voice_connection_id,
            )
            listener.experiment_state_version = created["version"]
            store.close()
            listener.experiment_report_store = ExperimentReportStore(
                Path(tmpdir) / "reports.sqlite"
            )
            socket = _ScriptedSocket([])

            token = server_module._REQUEST_PRINCIPAL.set(principal)
            try:
                with patch.dict("os.environ", environment, clear=False), patch(
                    "voice_workflow_agent.server._record_workspace_experiment_progress",
                    side_effect=WorkspaceConflictError("synthetic stale version"),
                ):
                    self._run_curated_turn(
                        listener, socket, "프로토콜 시작해줘", turn_id=1,
                    )
            finally:
                server_module._REQUEST_PRINCIPAL.reset(token)

            current = initialize_workspace_store(
                WorkspaceSettings(True, workspace_dir)
            )
            durable = current.get_experiment(principal, listener.session_id)
            current.close()
            self.assertFalse(curated.active)
            self.assertEqual(curated.workflow_status, "ready")
            self.assertEqual(durable["status"], "ready")
            self.assertEqual(durable["version"], created["version"])
            self.assertEqual(listener.experiment_report_store.list_reports(), [])
            self.assertTrue(any(
                item["type"] == "experiment.session.error"
                and item["code"] == "workspace_conflict"
                for item in socket.sent
            ))

    def test_timer_start_new_turn_is_non_mutating_and_keeps_original_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)
            curated, listener, before = self._timed_workspace_listener(
                workspace_dir, principal
            )
            listener.experiment_report_store = ExperimentReportStore(
                Path(tmpdir) / "reports.sqlite"
            )
            socket = _ScriptedSocket([])
            turn_id = 6
            event_key = (
                f"voice-{listener.generation}-{turn_id}-start_timer"
            )
            second_turn_id = 7
            second_event_key = (
                f"voice-{listener.generation}-{second_turn_id}-start_timer"
            )
            current_time = 2_000_000_000.0

            token = server_module._REQUEST_PRINCIPAL.set(principal)
            try:
                with patch.dict("os.environ", environment, clear=False), patch(
                    "voice_workflow_agent.curated_protocol.time.time",
                    return_value=current_time,
                ):
                    self._run_curated_turn(
                        listener,
                        socket,
                        "Timer를 시작해줘.",
                        turn_id=turn_id,
                    )
            finally:
                server_module._REQUEST_PRINCIPAL.reset(token)

            store = initialize_workspace_store(
                WorkspaceSettings(True, workspace_dir)
            )
            persisted = store.get_experiment(principal, listener.session_id)
            store.close()
            timer_events = [
                event for event in persisted["events"]
                if event["event_key"] == event_key
            ]
            self.assertEqual(len(timer_events), 1)
            timer_event = timer_events[0]
            self.assertEqual(timer_event["event_type"], "timer_started")
            self.assertEqual(timer_event["step_id"], "candidate-a-step-03")
            self.assertEqual(timer_event["step_label"], "3")
            self.assertEqual(
                timer_event["payload"]["timer"],
                curated._replay[turn_id].timer_payload,
            )
            self.assertEqual(set(timer_event["payload"]), {
                "authority",
                "intent_kind",
                "configuration_id",
                "turn_id",
                "generation",
                "timer",
            })
            self.assertEqual(set(timer_event["payload"]["timer"]), {
                "state",
                "duration_seconds",
                "remaining_seconds",
                "elapsed_seconds",
                "step_index",
                "step_id",
                "step_label",
                "deadline_at",
                "started_at",
            })
            self.assertEqual(timer_event["payload"]["timer"]["state"], "running")
            self.assertEqual(
                timer_event["payload"]["timer"]["duration_seconds"], 900
            )
            self.assertEqual(persisted["protocol_id"], self.fixture.protocol_id)
            self.assertEqual(
                persisted["protocol_revision_id"], self.fixture.revision_id
            )
            self.assertEqual(persisted["version"], before["version"] + 1)
            self.assertEqual(listener.experiment_state_version, persisted["version"])
            self.assertEqual(curated.timer_status()["state"], "running")
            self.assertFalse(any(
                item["type"] == "experiment.session.error"
                for item in socket.sent
            ))
            reply = next(
                item for item in socket.sent if item["type"] == "reply.complete"
            )
            self.assertIn("타이머를 시작했습니다", reply["text"])
            self.assertEqual(listener.experiment_report_store.list_reports(), [])

            listener.playback_ended(turn_id)
            token = server_module._REQUEST_PRINCIPAL.set(principal)
            try:
                with patch.dict("os.environ", environment, clear=False), patch(
                    "voice_workflow_agent.curated_protocol.time.time",
                    return_value=current_time + 120,
                ):
                    self._run_curated_turn(
                        listener,
                        socket,
                        "Timer를 시작해줘.",
                        turn_id=second_turn_id,
                    )
            finally:
                server_module._REQUEST_PRINCIPAL.reset(token)

            store = initialize_workspace_store(
                WorkspaceSettings(True, workspace_dir)
            )
            replayed = store.get_experiment(principal, listener.session_id)
            store.close()
            self.assertEqual(replayed["version"], persisted["version"])
            self.assertEqual(
                sum(
                    event["event_type"] == "timer_started"
                    for event in replayed["events"]
                ),
                1,
            )
            self.assertFalse(any(
                event["event_key"] == second_event_key
                for event in replayed["events"]
            ))
            second_plan = curated._replay[second_turn_id]
            self.assertFalse(second_plan.state_changed)
            self.assertEqual(
                second_plan.timer_payload["started_at"],
                timer_event["payload"]["timer"]["started_at"],
            )
            self.assertEqual(
                second_plan.timer_payload["deadline_at"],
                timer_event["payload"]["timer"]["deadline_at"],
            )
            self.assertLess(
                second_plan.timer_payload["remaining_seconds"],
                timer_event["payload"]["timer"]["remaining_seconds"],
            )
            decisions = [
                item for item in socket.sent
                if item["type"] == "turn.route_decision"
            ]
            self.assertEqual(decisions[-1]["action"], "start_timer")
            self.assertFalse(decisions[-1]["state_mutation"])
            replies = [
                item["text"] for item in socket.sent
                if item["type"] == "reply.complete"
            ]
            self.assertIn("이미 진행 중입니다", replies[-1])
            self.assertEqual(listener.experiment_report_store.list_reports(), [])

    def test_timer_start_from_stale_recovered_voice_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)
            curated, listener, before = self._timed_workspace_listener(
                workspace_dir, principal
            )
            listener.experiment_report_store = ExperimentReportStore(
                Path(tmpdir) / "reports.sqlite"
            )
            store = initialize_workspace_store(
                WorkspaceSettings(True, workspace_dir)
            )
            recovered = store.resume_experiment(
                principal,
                listener.session_id,
                expected_version=before["version"],
                protocol_id=self.fixture.protocol_id,
                protocol_revision_id=self.fixture.revision_id,
                voice_connection_id="voice-recovered-elsewhere",
            )
            store.close()
            socket = _ScriptedSocket([])

            token = server_module._REQUEST_PRINCIPAL.set(principal)
            try:
                with patch.dict("os.environ", environment, clear=False):
                    self._run_curated_turn(
                        listener,
                        socket,
                        "Timer를 시작해줘.",
                        turn_id=7,
                    )
            finally:
                server_module._REQUEST_PRINCIPAL.reset(token)

            store = initialize_workspace_store(
                WorkspaceSettings(True, workspace_dir)
            )
            unchanged = store.get_experiment(principal, listener.session_id)
            store.close()
            self.assertEqual(unchanged["version"], recovered["version"])
            self.assertFalse(any(
                event["event_type"] == "timer_started"
                for event in unchanged["events"]
            ))
            self.assertEqual(curated.timer_status()["state"], "not_started")
            self.assertEqual(
                listener.experiment_state_version,
                before["version"],
            )
            self.assertTrue(any(
                item["type"] == "experiment.session.error"
                and item["code"] == "workspace_conflict"
                for item in socket.sent
            ))
            reply = next(
                item for item in socket.sent if item["type"] == "reply.complete"
            )
            self.assertIn("상태 변경을 확정하지 않았습니다", reply["text"])
            self.assertEqual(listener.experiment_report_store.list_reports(), [])

    def test_stopped_experiment_is_not_resumed(self) -> None:
        """C: once an experiment is stopped, a fresh Voice start (no
        recovery fields - matching what the fixed client now sends for a
        non-resumable selection) creates a new experiment instead of
        touching the old one. resume_experiment also independently refuses
        to resume a stopped session as a defense-in-depth backstop."""
        protocol_id = "candidate-a-curated-development-v1"
        config = self._offline_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            created = store.start_experiment(
                principal, protocol_id=protocol_id,
                protocol_revision_id=self.fixture.revision_id,
            )
            stopped_id = created["session_id"]
            store.transition_experiment(
                principal, stopped_id, action="stop",
                expected_version=created["version"], event_key="stop-1",
            )
            with self.assertRaises(WorkspaceConflictError):
                store.resume_experiment(
                    principal, stopped_id, expected_version=created["version"],
                    protocol_id=protocol_id,
                    protocol_revision_id=self.fixture.revision_id,
                    voice_connection_id="voice-x",
                )
            completed_created = store.start_experiment(
                principal, protocol_id=protocol_id,
                protocol_revision_id=self.fixture.revision_id,
                current_step_id="candidate-a-step-01", current_step_label="1",
            )
            completed_running = store.record_experiment_progress(
                principal, completed_created["session_id"],
                expected_version=completed_created["version"],
                event_key="completed-start", event_type="protocol_started",
                step_id="candidate-a-step-01", step_label="1",
            )
            completed = store.transition_experiment(
                principal, completed_created["session_id"], action="complete",
                expected_version=completed_running["version"],
                event_key="completed-terminal",
            )
            with self.assertRaises(WorkspaceConflictError):
                store.resume_experiment(
                    principal, completed["session_id"],
                    expected_version=completed["version"],
                    protocol_id=protocol_id,
                    protocol_revision_id=self.fixture.revision_id,
                    voice_connection_id="voice-y",
                )
            store.close()

            fresh = self._run_socket(environment, config, _ScriptedSocket([{
                "type": "session.start", "configuration_id": 1,
                "mode": "cascade", "language": "ko", "protocol_id": protocol_id,
            }]))
            ready = fresh.ready_event()
            self.assertNotEqual(ready["experiment_session_id"], stopped_id)

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            untouched = store.get_experiment(principal, stopped_id)
            store.close()
            self.assertEqual(untouched["status"], "stopped")

    def test_two_fresh_starts_create_distinct_experiment_sessions(self) -> None:
        """D + requirement 10: two separate fresh (no recovery fields)
        Voice starts each create their own experiment; a still-open earlier
        one is left untouched, so legitimate parallel runs on the same
        protocol/revision are not prohibited."""
        protocol_id = "candidate-a-curated-development-v1"
        config = self._offline_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)

            payload = {
                "type": "session.start", "configuration_id": 1,
                "mode": "cascade", "language": "ko", "protocol_id": protocol_id,
            }
            first = self._run_socket(environment, config, _ScriptedSocket([payload]))
            second = self._run_socket(environment, config, _ScriptedSocket([payload]))
            first_id = first.ready_event()["experiment_session_id"]
            second_id = second.ready_event()["experiment_session_id"]
            self.assertNotEqual(first_id, second_id)

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            statuses = {
                row["session_id"]: row["status"]
                for row in store.list_experiments(principal)
            }
            store.close()
            self.assertEqual(statuses.get(first_id), "ready")
            self.assertEqual(statuses.get(second_id), "ready")

    def test_resume_with_mismatched_protocol_revision_fails_closed(self) -> None:
        """E: if the experiment selected for resume was bound to a
        different protocol/revision than what's currently selected, the
        server refuses to recover it rather than silently attaching to the
        wrong experiment."""
        config = self._offline_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            other = store.start_experiment(
                principal, protocol_id="a-different-protocol",
                protocol_revision_id="a-different-revision",
            )
            store.close()

            mismatched = self._run_socket(environment, config, _ScriptedSocket([{
                "type": "session.start", "configuration_id": 1,
                "mode": "cascade", "language": "ko",
                "protocol_id": "candidate-a-curated-development-v1",
                "experiment_session_id": other["session_id"],
                "experiment_session_version": other["version"],
            }]))
            self.assertFalse(any(
                item["type"] in ("session.ready", "session.started")
                for item in mismatched.sent
            ))
            self.assertIsNotNone(mismatched.error_message())

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            untouched = store.get_experiment(principal, other["session_id"])
            store.close()
            self.assertEqual(untouched["version"], other["version"])
            self.assertEqual(untouched["protocol_id"], "a-different-protocol")

    def test_two_open_experiments_are_never_auto_selected_by_server(self) -> None:
        """F: with two open experiments already open on the exact
        protocol/revision, a fresh (no recovery fields) Voice start must
        not silently guess which one to resume - it creates a third,
        leaving both originals completely untouched."""
        protocol_id = "candidate-a-curated-development-v1"
        config = self._offline_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            open_a = store.start_experiment(
                principal, protocol_id=protocol_id,
                protocol_revision_id=self.fixture.revision_id,
            )
            open_b = store.start_experiment(
                principal, protocol_id=protocol_id,
                protocol_revision_id=self.fixture.revision_id,
            )
            store.close()

            fresh = self._run_socket(environment, config, _ScriptedSocket([{
                "type": "session.start", "configuration_id": 1,
                "mode": "cascade", "language": "ko", "protocol_id": protocol_id,
            }]))
            new_id = fresh.ready_event()["experiment_session_id"]
            self.assertNotIn(new_id, {open_a["session_id"], open_b["session_id"]})

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            row_a = store.get_experiment(principal, open_a["session_id"])
            row_b = store.get_experiment(principal, open_b["session_id"])
            store.close()
            self.assertEqual(row_a["version"], open_a["version"])
            self.assertEqual(row_b["version"], open_b["version"])
            self.assertEqual(row_a["status"], "ready")
            self.assertEqual(row_b["status"], "ready")

    def test_raw_disconnect_leaves_in_progress_experiment_unchanged(self) -> None:
        """G: a raw WebSocket disconnect (no explicit session.stop control
        message) must not stop, pause, or advance a durable in-progress
        experiment - it is left exactly as it was."""
        protocol_id = "candidate-a-curated-development-v1"
        config = self._offline_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_dir = Path(tmpdir) / "workspace"
            environment, principal = self._bootstrap_tenant(workspace_dir)

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            created = store.start_experiment(
                principal, protocol_id=protocol_id,
                protocol_revision_id=self.fixture.revision_id,
                current_step_id="candidate-a-step-01", current_step_label="1",
            )
            session_id = created["session_id"]
            in_progress = store.record_experiment_progress(
                principal, session_id, event_key="e1",
                expected_version=created["version"],
                event_type="protocol_started",
                step_id="candidate-a-step-01", step_label="1",
            )
            version_before = in_progress["version"]
            store.close()

            # Bind this connection to the in-progress experiment (recovery),
            # then the mock socket ends with a raw disconnect sentinel only
            # - no session.stop control message is ever sent.
            self._run_socket(environment, config, _ScriptedSocket([{
                "type": "session.start", "configuration_id": 1,
                "mode": "cascade", "language": "ko", "protocol_id": protocol_id,
                "experiment_session_id": session_id,
                "experiment_session_version": version_before,
            }]))

            store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
            after = store.get_experiment(principal, session_id)
            store.close()
            self.assertEqual(after["status"], "in_progress")
            self.assertEqual(after["current_step_label"], "1")


if __name__ == "__main__":
    unittest.main()
