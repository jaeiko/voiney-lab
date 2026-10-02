"""A voice pause is resumed by voice, with the experiment record turned on.

2026-10-01 voice test (in-gel, ``run_dev.sh --test-mode``, experiment record on):
"정지" paused, "재개" got no answer, "재개해줘" was refused with "실험 세션을
저장하지 못해 상태 변경을 확정하지 않았습니다" and the red "다른 화면이나 음성
명령으로 변경되었습니다" banner, and "완료했어" then got the scope reminder.

Three things lined up. A voice pause never reached the durable experiment
record (its plan said ``state_changed=False``), so the record stayed
``in_progress`` and the voice resume that followed -- the one action that did
say it changed state -- asked the record for ``in_progress -> in_progress``,
which the workspace refuses as ``workspace_conflict``; the browser maps that
code to the banner. The turn's rollback then restored the workflow status but
not the pause itself, which the checkpoint did not hold, so the session was
neither paused nor resumed and the paused-guard no longer caught "완료했어".
And a bare "재개" was read from the workflow-command table as START, which the
paused guard answers silently.

These run through a real ``voice_socket`` session over a scripted socket with
the workspace and the experiment report store both enabled from the
environment, as the voice test had them. As in the neighbouring WebSocket
tests, ``run_turn`` is handed the transcript where the browser's audio and the
STT provider would have produced it; the bench pause/resume buttons go through
the socket itself.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.development_activation import development_activation_recorded
from voiney_lab.curated_protocol import load_curated_protocol_fixture
from voiney_lab.identity import Principal, Role
from voiney_lab.server import (
    ListenerSession,
    ServerConfig,
    Transcription,
    TurnState,
    run_turn,
    voice_socket,
)
import voiney_lab.server as server_module
from voiney_lab.workspace_store import (
    WorkspaceConflictError,
    WorkspaceSettings,
    initialize_workspace_store,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"
PROTOCOL_ID = "candidate-a-curated-development-v1"
RESUME_PHRASES = ("재개", "재개해줘", "다시 시작할게")
PROFILE = {
    "profile_id": "researcher-a",
    "principal_id": "principal-researcher-a",
    "organization_id": "tenant-a",
    "display_name": "Researcher A",
    "roles": ["researcher"],
}


class _QueuedSocket:
    """A WebSocket double the test can keep feeding while turns run."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.headers = {"x-voice-dev-profile": PROFILE["profile_id"]}
        self.query_params: dict[str, str] = {}
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def accept(self) -> None:
        pass

    async def send_text(self, value: str) -> None:
        self.sent.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass

    async def close(self, **_kwargs) -> None:
        pass

    async def receive(self) -> dict:
        return await self.inbox.get()

    def control(self, payload: dict) -> None:
        self.inbox.put_nowait({"text": json.dumps(payload)})

    def disconnect(self) -> None:
        self.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})

    async def wait_for(self, predicate) -> dict:
        for _ in range(500):
            for item in self.sent:
                if predicate(item):
                    return item
            await asyncio.sleep(0.01)
        raise AssertionError("expected socket event did not arrive")

    def for_turn(self, turn_id: int, kind: str) -> list[dict]:
        return [
            item for item in self.sent
            if item["type"] == kind and item.get("turn_id") == turn_id
        ]

    def reply(self, turn_id: int) -> str:
        return self.for_turn(turn_id, "reply.complete")[-1]["text"]


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class VoicePauseResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)

    def setUp(self) -> None:
        activation = development_activation_recorded()
        activation.__enter__()
        self.addCleanup(activation.__exit__, None, None, None)
        self._fresh_tenant()

    def _fresh_tenant(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace_dir = Path(tmp.name) / "workspace"
        self.report_db = Path(tmp.name) / "reports.sqlite"
        self.principal = Principal(
            principal_id=PROFILE["principal_id"],
            subject="dev:researcher-a",
            organization_id=PROFILE["organization_id"],
            display_name=PROFILE["display_name"],
            roles=frozenset({Role.RESEARCHER}),
            authentication_method="development",
        )
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        store.bootstrap_principal(self.principal)
        store.bind_resource(self.principal, "protocol_catalog", PROTOCOL_ID)
        store.close()
        self.environment = {
            "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED": "true",
            "VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR": str(self.workspace_dir),
            "VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED": "true",
            "VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB": str(self.report_db),
            "VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo",
            "VOICE_WORKFLOW_AGENT_DEV_AUTH_PROFILES": json.dumps([PROFILE]),
        }

    def _session(self, scenario, *extra_patches):
        """Open one voice session, run ``scenario(socket, listener, say)``, close."""

        placeholder = Path("/tmp/offline-session-contract")
        config = ServerConfig(
            placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
            None, None, placeholder, placeholder, placeholder,
        )
        captured: list[ListenerSession] = []

        def listener_factory(*args, **kwargs):
            listener = ListenerSession(*args, **kwargs)
            captured.append(listener)
            return listener

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def run() -> _QueuedSocket:
            socket = _QueuedSocket()
            # The server runs each turn inside the socket's own context, where
            # the authenticated principal is bound; this coroutine stands in
            # for that context.
            token = server_module._REQUEST_PRINCIPAL.set(self.principal)
            try:
                connection = asyncio.create_task(voice_socket(socket))
                socket.control({
                    "type": "session.start", "configuration_id": 1,
                    "mode": "cascade", "language": "ko",
                    "protocol_id": PROTOCOL_ID,
                })
                await socket.wait_for(lambda item: item["type"] == "session.ready")
                listener = captured[0]

                async def say(turn_id: int, text: str) -> None:
                    listener.active_turn_id = turn_id
                    listener.detector.state = TurnState.PROCESSING
                    await run_turn(
                        socket, listener, b"\0\0", turn_id, listener.generation,
                        accepted_transcription=Transcription(text, "ko"),
                        accepted_stt_ms=1,
                    )
                    listener.playback_ended(turn_id)

                await scenario(socket, listener, say)
                socket.disconnect()
                await connection
            finally:
                server_module._REQUEST_PRINCIPAL.reset(token)
            return socket

        patches = [
            patch.dict("os.environ", self.environment, clear=False),
            patch("voiney_lab.server.server_config", return_value=config),
            patch(
                "voiney_lab.server.load_curated_protocol_fixture",
                return_value=self.fixture,
            ),
            patch("voiney_lab.server.ListenerSession", side_effect=listener_factory),
            patch("voiney_lab.server.synthesize", return_value=b"\0\0"),
            patch("voiney_lab.server.asyncio.to_thread", side_effect=immediate),
            patch(
                "voiney_lab.server.AsyncOpenAI",
                side_effect=AssertionError("no model call for workflow control"),
            ),
            *extra_patches,
        ]
        for item in patches:
            item.start()
        try:
            return asyncio.run(run()), captured[0]
        finally:
            for item in reversed(patches):
                item.stop()

    def _durable(self, listener: ListenerSession) -> dict:
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            return store.get_experiment(self.principal, listener.session_id)
        finally:
            store.close()

    def _snapshot(self, listener: ListenerSession) -> dict:
        """What the session and the durable record say, before the socket
        closes and the server resets the session."""

        curated = listener.curated_protocol_session
        durable = self._durable(listener)
        return {
            "step_index": curated.current_index,
            "workflow_status": curated.workflow_status,
            "pause_state": curated.pause_timer_status()["state"],
            "durable_status": durable["status"],
            "durable": durable,
        }

    @staticmethod
    def _errors(socket: _QueuedSocket) -> list[dict]:
        return [
            item for item in socket.sent
            if item["type"] in {"experiment.session.error", "error"}
        ]

    def test_pause_resume_next_complete_runs_to_the_end_for_every_resume_phrase(self):
        for phrase in RESUME_PHRASES:
            with self.subTest(resume=phrase):
                self._fresh_tenant()
                seen = {}

                async def scenario(socket, listener, say):
                    await say(1, "프로토콜 시작해줘")
                    await say(2, "정지")
                    seen["paused"] = self._snapshot(listener)
                    await say(3, phrase)
                    await say(4, "다음")
                    await say(5, "완료했어")
                    seen["end"] = self._snapshot(listener)

                socket, _ = self._session(scenario)
                # The reported symptoms first: a resume turn that ends silent
                # on the paused notice ("재개"), or one refused with
                # workspace_conflict ("재개해줘", "다시 시작할게").
                decision = socket.for_turn(3, "turn.route_decision")[-1]
                self.assertEqual(decision["action"], "resume")
                self.assertTrue(decision["state_mutation"])
                resumed = socket.for_turn(3, "turn.done")[-1]
                self.assertEqual(resumed["result_kind"], "resume")
                self.assertEqual(resumed["segment_count"], 1)
                self.assertEqual(self._errors(socket), [])
                spoken = socket.for_turn(3, "reply.delta")[-1]["speech_text"]
                self.assertIn("워크플로를 재개합니다. 현재 1단계입니다", spoken)
                self.assertIn("1단계", socket.reply(3))
                self.assertNotIn("저장하지 못해", socket.reply(3))
                self.assertEqual(
                    socket.for_turn(5, "turn.done")[-1]["result_kind"], "next"
                )
                # The cause: the voice pause reached the durable record.
                self.assertEqual(seen["paused"]["pause_state"], "paused")
                self.assertEqual(seen["paused"]["durable_status"], "paused")
                end = seen["end"]
                self.assertEqual(end["step_index"], 1)
                self.assertEqual(end["workflow_status"], "active")
                self.assertEqual(end["pause_state"], "active")
                self.assertEqual(end["durable_status"], "in_progress")
                self.assertEqual(end["durable"]["current_step_label"], "2")
                self.assertEqual(
                    [item["step_id"] for item in end["durable"]["completed_steps"]],
                    ["candidate-a-step-01"],
                )
                transitions = [
                    event["event_type"] for event in end["durable"]["events"]
                    if event["event_type"].startswith("session_")
                ]
                self.assertEqual(
                    transitions,
                    ["session_started", "session_paused", "session_in_progress"],
                )

    def test_completion_said_while_paused_asks_for_resume_and_records_nothing(self):
        """The paused design (b8c246f): anything but resume/stop/pause is
        answered by the paused guard -- an on-screen 'say 실험 재개 or press
        resume' notice, not spoken -- and the state does not move. The
        completion is taken only after a resume.

        What is checked is the server's decision. The notice itself does not
        reach the browser today: a silent turn calls complete_without_playback
        before its reply is sent, so the reply and turn.done are dropped, and
        the page renders a reply only for a turn that played audio. That needs
        static/ and is reported, not pinned here.
        """

        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "정지")
            await say(3, "다음")
            await say(4, "완료했어")
            seen["paused"] = self._snapshot(listener)
            await say(5, "재개해줘")
            await say(6, "다음")
            await say(7, "완료했어")
            seen["end"] = self._snapshot(listener)

        socket, _ = self._session(scenario)
        for turn_id in (3, 4):
            decision = socket.for_turn(turn_id, "turn.route_decision")[-1]
            self.assertEqual(decision["action"], "pause")
            self.assertFalse(decision["state_mutation"])
        self.assertEqual(seen["paused"]["step_index"], 0)
        self.assertEqual(seen["paused"]["pause_state"], "paused")
        self.assertEqual(seen["paused"]["durable_status"], "paused")
        self.assertEqual(seen["paused"]["durable"]["completed_steps"], [])
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(seen["end"]["step_index"], 1)
        self.assertEqual(
            [item["step_id"] for item in seen["end"]["durable"]["completed_steps"]],
            ["candidate-a-step-01"],
        )

    def test_a_refused_resume_leaves_the_session_fully_paused(self):
        """A resume the record refuses is rolled back whole: the session is
        still paused, so the paused guard -- not the scope reminder -- answers
        the "완료했어" that follows, and nothing is completed."""

        real_transition = server_module._transition_workspace_experiment
        seen = {}

        def refuse_resume(session, *, action, event_key, reason=None):
            if action == "resume":
                raise WorkspaceConflictError("synthetic refusal")
            return real_transition(
                session, action=action, event_key=event_key, reason=reason
            )

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "정지")
            await say(3, "재개해줘")
            seen["refused"] = self._snapshot(listener)
            await say(4, "완료했어")
            seen["end"] = self._snapshot(listener)

        socket, _ = self._session(
            scenario,
            patch(
                "voiney_lab.server._transition_workspace_experiment",
                side_effect=refuse_resume,
            ),
        )
        self.assertIn("실험 세션을 저장하지 못해", socket.reply(3))
        self.assertEqual(seen["refused"]["workflow_status"], "paused")
        self.assertEqual(seen["refused"]["pause_state"], "paused")
        decision = socket.for_turn(4, "turn.route_decision")[-1]
        self.assertEqual(decision["action"], "pause")
        self.assertFalse(decision["state_mutation"])
        self.assertEqual(seen["end"]["step_index"], 0)
        self.assertEqual(seen["end"]["durable_status"], "paused")
        self.assertEqual(seen["end"]["durable"]["completed_steps"], [])

    def test_bench_resume_after_a_voice_pause_and_a_repeated_resume_are_accepted(self):
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "정지")
            socket.control({"type": "workflow.resume"})
            await socket.wait_for(
                lambda item: item["type"] == "protocol.fixture.state"
                and item.get("action") == "resume"
                or item["type"] == "error"
            )
            await say(3, "재개해줘")
            seen["end"] = self._snapshot(listener)

        socket, _ = self._session(scenario)
        self.assertEqual(self._errors(socket), [])
        self.assertNotIn("저장하지 못해", socket.reply(3))
        self.assertEqual(seen["end"]["workflow_status"], "active")
        self.assertEqual(seen["end"]["durable_status"], "in_progress")

if __name__ == "__main__":
    unittest.main()
