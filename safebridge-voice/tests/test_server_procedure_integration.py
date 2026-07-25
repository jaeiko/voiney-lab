"""End-to-end server/Brain/Procedure integration against fresh SQLite files."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from safebridge_voice.brain import ConversationHistory
from safebridge_voice.document_store import ingest_manifest, ingest_manifest_file
from safebridge_voice.language import Transcription
from safebridge_voice.procedure_definitions import load_procedure_definitions
from safebridge_voice.procedure_store import ProcedureStore
from safebridge_voice.procedures import ProcedureController
from safebridge_voice.server import (
    ListenerSession, ServerConfig, run_turn, voice_socket,
)
from safebridge_voice.tools import ToolContext
from safebridge_voice.vad import TurnState

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "procedure_demo"
PROCEDURE_ID = "fictional-color-card-demo-ko"
ROUTES = {"deterministic_emergency", "language_clarification", "brain"}
UNAUTHORIZED = (
    "아직 현재 단계를 완료하지 않았습니다",
    "현재 단계를 완료하면 어떻게 되나요",
    "현재 단계를 완료하지 마세요",
    "다음 단계가 무엇인가요",
    "1단계를 다시 설명해 주세요",
    "현재 단계를 완료했습니다 그리고 다음 단계도 완료해 주세요",
    "현재 단계로 완료했습니다",
)


class Stream:
    def __init__(self, items): self.items = iter(items)
    def __aiter__(self): return self
    async def __anext__(self):
        try: return next(self.items)
        except StopIteration: raise StopAsyncIteration


class ForcedCompletions:
    def __init__(self, name=None, arguments=None, answer="가상 응답입니다."):
        self.name, self.arguments, self.answer = name, arguments, answer
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1 and self.name:
            return Stream([{"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "forced-call",
                "function": {"name": self.name, "arguments": self.arguments},
            }]}}]}])
        return Stream([{"choices": [{"delta": {"content": self.answer}}]}])


class ForcedClient:
    def __init__(self, name=None, arguments=None, answer="가상 응답입니다."):
        self.model = "test-model"
        self.chat = type("Chat", (), {})()
        self.chat.completions = ForcedCompletions(name, arguments, answer)


class Socket:
    def __init__(self):
        self.text, self.binary = [], []
    async def send_text(self, value): self.text.append(json.loads(value))
    async def send_bytes(self, value): self.binary.append(value)


class ProcedureServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        self.catalog = directory / "approved.sqlite"
        self.database = directory / "sessions.sqlite"
        ingest_manifest_file(DEMO / "approved_document.ko.json", self.catalog)
        definitions = load_procedure_definitions(
            DEMO / "procedures.ko.json", self.catalog,
            facility_id="DEMO-FACILITY", language="ko", usage_scope="test_only",
        )
        self.store = ProcedureStore(self.database)
        self.controller = ProcedureController(definitions, self.store)
        context = ToolContext(
            self.catalog, "DEMO-FACILITY", "ko", "test_only", "ko",
            self.controller,
        )
        self.session = ListenerSession(tool_context=context)
        self.session.active = True
        self.next_turn = 1

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def snapshot(self):
        sessions = [dict(row) for row in self.store._connection.execute(
            "SELECT * FROM procedure_sessions ORDER BY session_id")]
        events = [dict(row) for row in self.store._connection.execute(
            "SELECT * FROM procedure_step_events ORDER BY event_id")]
        return sessions, events

    def procedure_events(self, socket):
        return [item for item in socket.text
                if item["type"].startswith("procedure.")]

    def turn(self, transcript, name=None, arguments=None, answer="가상 응답입니다."):
        turn_id = self.next_turn
        self.next_turn += 1
        self.session.active_turn_id = turn_id
        self.session.detector.state = TurnState.PROCESSING
        socket = Socket()
        client = ForcedClient(name, arguments, answer)
        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)
        with patch("safebridge_voice.server.transcribe",
                   return_value=Transcription(transcript, "ko")), \
             patch("safebridge_voice.server.synthesize", return_value=b"\0\0"), \
             patch("safebridge_voice.server.asyncio.to_thread",
                   side_effect=immediate), \
             patch("safebridge_voice.server.AsyncOpenAI", return_value=client), \
             patch("safebridge_voice.server.require_env", return_value="test"):
            asyncio.run(run_turn(socket, self.session, b"\0\0", turn_id, 1))
        routes = [item["route"] for item in socket.text if item["type"] == "turn.done"]
        self.assertTrue(set(routes).issubset(ROUTES))
        return socket, client

    def start(self):
        return self.turn(
            "가상 색상 카드 데모를 시작해 주세요",
            "start_procedure", json.dumps({"procedure_id": PROCEDURE_ID}),
        )[0]

    def complete(self, step_id, transcript="현재 단계를 완료했습니다"):
        return self.turn(
            transcript, "complete_current_step",
            json.dumps({"expected_step_id": step_id}),
        )[0]

    def assert_sanitized_error(self, socket, expected_code):
        events = self.procedure_events(socket)
        self.assertEqual([(item["type"], item.get("code")) for item in events],
                         [("procedure.error", expected_code)])
        visible = json.dumps(events, ensure_ascii=False)
        for secret in (str(self.catalog), str(self.database),
                       self.controller.attached_session_id or "", "sqlite", "sql",
                       "configuration"):
            if secret:
                self.assertNotIn(secret, visible.casefold()
                                 if secret in ("sqlite", "sql", "configuration")
                                 else visible)

    def test_successful_start_uses_real_stack_and_sanitized_public_events(self):
        socket = self.start()
        sessions, events = self.snapshot()
        self.assertEqual(len(sessions), 1)
        self.assertEqual([event["event_type"] for event in events], ["started"])
        self.assertEqual([e["type"] for e in self.procedure_events(socket)],
                         ["procedure.started", "procedure.state"])
        done = next(e for e in socket.text if e["type"] == "turn.done")
        self.assertEqual(done["route"], "brain")
        visible = json.dumps(socket.text, ensure_ascii=False)
        for hidden in (sessions[0]["session_id"], str(self.catalog), str(self.database)):
            self.assertNotIn(hidden, visible)

    def test_duplicate_start_and_current_step_are_read_only(self):
        self.start()
        before = self.snapshot()
        duplicate = self.start()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([e["type"] for e in self.procedure_events(duplicate)],
                         ["procedure.state"])
        lookup, _ = self.turn("현재 단계를 알려 주세요", "get_current_step", "{}")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([e["type"] for e in self.procedure_events(lookup)],
                         ["procedure.state"])

    def test_server_authorized_completion_advances_exactly_one_step(self):
        self.start()
        before = self.snapshot()
        socket = self.complete("blue-card")
        after = self.snapshot()
        self.assertEqual([e["type"] for e in self.procedure_events(socket)],
                         ["procedure.step_completed", "procedure.state"])
        self.assertEqual(after[0][0]["current_step_index"], 1)
        self.assertEqual(len(after[1]), len(before[1]) + 1)
        self.assertEqual(after[1][-1]["event_type"], "step_completed")

    def test_forced_unauthorized_completion_calls_fail_closed_without_mutation(self):
        self.start()
        for transcript in UNAUTHORIZED:
            with self.subTest(transcript=transcript):
                before = self.snapshot()
                socket = self.complete("blue-card", transcript)
                self.assertEqual(self.snapshot(), before)
                self.assert_sanitized_error(socket, "explicit_confirmation_required")

    def test_emergency_preserves_complete_procedure_and_conversation_state(self):
        self.start()
        self.complete("blue-card")
        before = self.snapshot()
        attachment = self.controller.attached_session_id
        pending = {"location": "F", "summary": "fictional", "urgency": "routine",
                   "exposure_status": "no", "language": "ko"}
        self.session.history.pending_report = dict(pending)
        self.session.last_confirmed_language = "ko"
        socket, client = self.turn("도와줘")
        self.assertEqual(next(e for e in socket.text if e["type"] == "turn.done")["route"],
                         "deterministic_emergency")
        self.assertEqual(client.chat.completions.calls, [])
        self.assertEqual(self.controller.attached_session_id, attachment)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.session.history.pending_report, pending)
        self.assertEqual(self.session.last_confirmed_language, "ko")

    def test_pending_report_paths_preserve_procedure_and_block_completion(self):
        self.start()
        pending = {"location": "F", "summary": "fictional", "urgency": "routine",
                   "exposure_status": "no", "language": "ko"}
        before = self.snapshot()
        attachment = self.controller.attached_session_id
        # Cancellation and ambiguous confirmation are handled by the real Brain branch.
        self.session.history.pending_report = dict(pending)
        self.turn("보고서를 취소해 주세요")
        self.assertEqual(self.snapshot(), before)
        self.session.history.pending_report = dict(pending)
        self.turn("아마도요")
        self.assertEqual(self.snapshot(), before)
        # A correction goes through the model, but cannot acquire completion authority.
        self.session.history.pending_report = dict(pending)
        self.turn("위치를 고쳐 주세요", answer="수정할 내용을 알려 주세요.")
        self.assertEqual(self.snapshot(), before)
        # Approval exercises the real approval path; only report submission is external.
        self.session.history.pending_report = dict(pending)
        with patch("safebridge_voice.brain.execute_tool", return_value={
            "status": "success", "report_id": "SR-20260725-A1B2C3",
            "report_status": "queued_for_handoff",
        }):
            self.turn("보고서를 제출해 주세요")
        self.assertEqual(self.snapshot(), before)
        # Even an adversarial forced Tool call is rejected while confirmation is pending.
        self.session.history.pending_report = dict(pending)
        socket = self.complete("blue-card", "현재 단계를 완료했습니다")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.controller.attached_session_id, attachment)
        self.assert_sanitized_error(socket, "explicit_confirmation_required")

    def test_ordinary_conversation_and_real_approved_retrieval_are_read_only(self):
        self.start()
        before = self.snapshot()
        self.turn("오늘 기분이 어때요", answer="잘 지내고 있습니다.")
        self.assertEqual(self.snapshot(), before)
        retrieval_catalog = Path(self.temporary.name) / "retrieval.sqlite"
        payload = json.loads(
            (ROOT / "tests" / "fixtures" /
             "fictional_ingestion_manifest.json").read_text(encoding="utf-8"))
        document = payload["documents"][0]
        document["usage_scope"] = "operational"
        document["source_authority"] = "supplier"
        document["language"] = "ko"
        for alias in document["aliases"]:
            alias["language"] = "ko"
        ingest_manifest(payload, retrieval_catalog)
        self.session.tool_context = ToolContext(
            retrieval_catalog, "DEMO-FACILITY", "ko", "operational", "ko",
            self.controller,
        )
        socket, _ = self.turn(
            "CLI-TEST-100 응급 처치 문서를 찾아 주세요",
            "search_approved_safety_manual",
            json.dumps({"query": "CLI-TEST-100", "topic": "first_aid"}),
        )
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.procedure_events(socket))
        result = next(e for e in socket.text if e["type"] == "tool.result")
        self.assertEqual(result["status"], "success")

    def test_final_completion_is_atomic_and_replays_are_idempotent_or_stale(self):
        self.start()
        self.complete("blue-card")
        self.complete("yellow-card")
        socket = self.complete("green-card")
        self.assertEqual([e["type"] for e in self.procedure_events(socket)],
                         ["procedure.step_completed", "procedure.completed",
                          "procedure.state"])
        sessions, events = self.snapshot()
        self.assertEqual((sessions[0]["status"], sessions[0]["current_step_index"]),
                         ("completed", 3))
        self.assertIsNotNone(sessions[0]["completed_at"])
        self.assertEqual([e["event_type"] for e in events].count("completed"), 1)
        before = self.snapshot()
        replay = self.complete("green-card")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([e["type"] for e in self.procedure_events(replay)],
                         ["procedure.state"])
        # Older-step replay is rejected by the real controller as stale.  The
        # server authorization boundary intentionally cannot authorize an old
        # step after completion.
        stale = self.controller.complete("blue-card")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(stale, {"status": "error", "code": "stale_step"})

    def test_real_errors_and_sqlite_rollback_are_sanitized(self):
        cases = (
            ("malformed", "start_procedure", "{", "invalid_arguments"),
            ("missing", "start_procedure", "{}", "invalid_arguments"),
            ("unavailable", "start_procedure",
             json.dumps({"procedure_id": "missing"}), "procedure_not_available"),
            ("no-active", "get_current_step", "{}", "no_active_procedure"),
        )
        for transcript, name, arguments, code in cases:
            with self.subTest(case=transcript):
                before = self.snapshot()
                socket, _ = self.turn(transcript, name, arguments)
                self.assertEqual(self.snapshot(), before)
                self.assert_sanitized_error(socket, code)
        self.start()
        before = self.snapshot()
        mismatch = self.complete("yellow-card")
        self.assertEqual(self.snapshot(), before)
        self.assert_sanitized_error(mismatch, "explicit_confirmation_required")
        self.store._connection.execute("""
            CREATE TRIGGER fail_real_transition BEFORE INSERT ON procedure_step_events
            WHEN NEW.event_type='step_completed'
            BEGIN SELECT RAISE(ABORT,'synthetic database path /tmp/private.sqlite'); END
        """)
        before = self.snapshot()
        failed = self.complete("blue-card")
        self.assertEqual(self.snapshot(), before)
        self.assert_sanitized_error(failed, "step_mismatch")

    def test_actual_websocket_session_reset_detaches_but_preserves_sqlite(self):
        config = ServerConfig(
            self.catalog, "DEMO-FACILITY", "test_only", frozenset({"ko"}), "ko",
            DEMO / "procedures.ko.json", self.database,
        )
        captured = []
        real_controller = ProcedureController

        class WebSocket:
            def __init__(self):
                self.sent = []
                self.messages = iter((
                    {"text": '{"type":"session.start","language":"ko"}'},
                    {"text": '{"type":"session.reset"}'},
                    {"type": "websocket.disconnect"},
                ))
            async def accept(self): pass
            async def send_text(self, value): self.sent.append(json.loads(value))
            async def receive(self): return next(self.messages)

        socket = WebSocket()
        def construct(definitions, store):
            controller = real_controller(definitions, store)
            controller.start(PROCEDURE_ID, facility_id="DEMO-FACILITY",
                             language="ko", usage_scope="test_only")
            captured.append(controller)
            return controller

        with patch("safebridge_voice.server.server_config", return_value=config), \
             patch("safebridge_voice.server.ProcedureController", side_effect=construct):
            asyncio.run(voice_socket(socket))
        self.assertEqual(len(captured), 1)
        self.assertIsNone(captured[0].attached_session_id)
        states = [e for e in socket.sent if e["type"] == "procedure.state"]
        self.assertEqual(states[-1]["state"], {"attached": False})
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT status,current_step_index FROM procedure_sessions").fetchone()
            events = connection.execute(
                "SELECT event_type FROM procedure_step_events ORDER BY event_id").fetchall()
        self.assertEqual(row, ("active", 0))
        self.assertEqual(events, [("started",)])
        language = [e for e in socket.sent if e["type"] == "session.language_state"]
        self.assertEqual(language[-1], language[0])

    def test_route_contract_is_closed(self):
        self.start()
        socket, _ = self.turn("일반 질문입니다", answer="일반 응답입니다.")
        routes = [e["route"] for e in socket.text if e["type"] == "turn.done"]
        self.assertTrue(routes and set(routes).issubset(ROUTES))


if __name__ == "__main__":
    unittest.main()
