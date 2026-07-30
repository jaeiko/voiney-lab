"""End-to-end server/Brain/Procedure integration against fresh SQLite files."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent.brain import (
    REPORT_CONFIRMATION_CLARIFICATION_TEXT,
    ConversationHistory,
)
from voice_workflow_agent.document_store import ingest_manifest, ingest_manifest_file
from voice_workflow_agent.language import Transcription
from voice_workflow_agent.procedure_definitions import load_procedure_definitions
from voice_workflow_agent.procedure_store import ProcedureStore
from voice_workflow_agent.procedures import ProcedureController
from voice_workflow_agent.server import (
    ListenerSession, ServerConfig, run_turn, voice_socket,
)
from voice_workflow_agent.tools import ToolContext
from voice_workflow_agent.vad import TurnState

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "procedure_demo"
PROCEDURE_ID = "fictional-wet-lab-workflow-demo-ko"
ROUTES = {
    "deterministic_emergency",
    "deterministic_procedure",
    "language_clarification",
    "brain",
}
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
        self.now = [1000.0]
        self.controller = ProcedureController(
            definitions, self.store, clock=lambda: self.now[0])
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
        with patch("voice_workflow_agent.server.transcribe",
                   return_value=Transcription(transcript, "ko")), \
             patch("voice_workflow_agent.server.synthesize", return_value=b"\0\0"), \
             patch("voice_workflow_agent.server.asyncio.to_thread",
                   side_effect=immediate), \
             patch("voice_workflow_agent.server.AsyncOpenAI", return_value=client), \
             patch("voice_workflow_agent.server.require_env", return_value="test"):
            asyncio.run(run_turn(socket, self.session, b"\0\0", turn_id, 1))
        routes = [item["route"] for item in socket.text if item["type"] == "turn.done"]
        self.assertTrue(set(routes).issubset(ROUTES))
        return socket, client

    def start(self):
        socket = self.turn(
            "가상 샘플 점검 워크플로를 시작해 주세요",
            "start_procedure", json.dumps({"procedure_id": PROCEDURE_ID}),
        )[0]
        if (self.controller.attached_session_id and not
                self.store.list_observations(
                    self.controller.attached_session_id,"demo-label-check")):
            self.controller.record_observation("demo-label-check","A-17")
        return socket

    def complete(self, step_id, transcript="현재 단계를 완료했습니다"):
        if transcript.rstrip(".!?。？！") in {
            "현재 단계를 완료했습니다","이 단계를 완료했습니다","현재 단계 완료했습니다"
        }:
            if step_id=="demo-mix-timer":
                started=self.controller.start_timer(step_id)
                if started.get("status")=="success":
                    self.now[0]+=10
            if step_id=="demo-indicator-observation":
                self.controller.record_observation(step_id,"green")
        return self.turn(
            transcript, "complete_current_step",
            json.dumps({"expected_step_id": step_id}),
        )[0]

    def assert_sanitized_error(self, socket, expected_code):
        events = self.procedure_events(socket)
        self.assertEqual(
            (events[0]["type"],events[0].get("code")),
            ("procedure.error",expected_code),
        )
        self.assertTrue(all(
            item["type"]=="procedure.state" for item in events[1:]))
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
        socket = self.complete("demo-label-check")
        after = self.snapshot()
        self.assertEqual([e["type"] for e in self.procedure_events(socket)],
                         ["procedure.step_completed", "procedure.state"])
        self.assertEqual(after[0][0]["current_step_index"], 1)
        self.assertEqual(len(after[1]), len(before[1]) + 1)
        self.assertEqual(after[1][-1]["event_type"], "step_completed")

    def test_natural_completion_and_timer_start_are_server_owned(self):
        self.start()
        completed,completion_client=self.turn(
            "현재 단계를 완료했어.",
            "complete_current_step",
            json.dumps({"expected_step_id":"demo-mix-timer"}),
            answer="이전 단계가 아직 진행 중입니다.",
        )
        self.assertEqual(completion_client.chat.completions.calls,[])
        self.assertEqual(
            next(item for item in completed.text
                 if item["type"]=="tool.result")["completed_step_id"],
            "demo-label-check",
        )

        timer,timer_client=self.turn(
            "고정 타이머를 시작해줘.",
            "start_step_timer",
            json.dumps({"expected_step_id":"demo-label-check"}),
            answer="현재 단계와 일치하지 않습니다.",
        )
        self.assertEqual(timer_client.chat.completions.calls,[])
        result=next(item for item in timer.text
                    if item["type"]=="tool.result")
        self.assertEqual(result["tool"],"start_step_timer")
        self.assertEqual(result["timer_step_id"],"demo-mix-timer")
        self.assertEqual(result["status"],"success")
        reply=next(item for item in timer.text
                   if item["type"]=="reply.complete")["text"]
        self.assertIn("고정 10초 타이머를 시작했습니다",reply)
        sessions,_=self.snapshot()
        self.assertEqual(sessions[0]["current_step_index"],1)
        self.assertEqual(
            self.controller.current()["state"]["timer"]["state"],
            "running",
        )
        self.assertEqual(
            [item["type"] for item in self.procedure_events(timer)],
            ["procedure.timer_started","procedure.state"],
        )

    def test_observations_are_server_owned_on_first_cascade_utterance(self):
        self.turn(
            "가상 샘플 점검 워크플로를 시작해 주세요",
            "start_procedure",json.dumps({"procedure_id":PROCEDURE_ID}),
        )
        label,label_client=self.turn(
            "가상 라벨은 A-170이야.",
            "get_current_step","{}",
            answer="모델이 선택한 응답은 사용되면 안 됩니다.",
        )
        self.assertEqual(label_client.chat.completions.calls,[])
        label_result=next(
            item for item in label.text if item["type"]=="tool.result")
        self.assertEqual(
            label_result["tool"],"record_step_observation")
        self.assertEqual(label_result["observation"]["value"],"A-170")
        self.assertEqual(
            [item["type"] for item in self.procedure_events(label)],
            ["procedure.observation_recorded","procedure.state"],
        )
        self.assertEqual(
            next(item for item in label.text
                 if item["type"]=="turn.done")["route"],
            "deterministic_procedure",
        )

        self.complete("demo-label-check")
        self.controller.start_timer("demo-mix-timer")
        self.now[0]+=10
        self.complete("demo-mix-timer")
        display,display_client=self.turn(
            "가상 표시창 색깔은 빨간색이야.",
            "get_current_step","{}",
            answer="현재 단계만 다시 읽겠습니다.",
        )
        self.assertEqual(display_client.chat.completions.calls,[])
        display_result=next(
            item for item in display.text if item["type"]=="tool.result")
        self.assertEqual(
            display_result["tool"],"record_step_observation")
        self.assertEqual(
            display_result["observation"]["value"],"빨간색")
        self.assertEqual(
            [item["type"] for item in self.procedure_events(display)],
            ["procedure.observation_recorded","procedure.state"],
        )
        observations=self.store.list_observations(
            self.controller.attached_session_id)
        self.assertEqual(
            [item["value"] for item in observations],
            ["A-170","빨간색"],
        )

    def test_timer_completion_is_server_owned_and_uses_fresh_deadline(self):
        self.start()
        self.complete("demo-label-check")
        started=self.controller.start_timer("demo-mix-timer")
        self.assertEqual(started["status"],"success")
        before=self.snapshot()

        early,early_client=self.turn(
            "현재 단계를 완료했습니다",
            answer="Do you want to confirm completion?",
        )
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(early_client.chat.completions.calls,[])
        result=next(item for item in early.text
                    if item["type"]=="tool.result")
        self.assertEqual(result["code"],"timer_not_elapsed")
        self.assertEqual(result["remaining_seconds"],10)
        reply=next(item for item in early.text
                   if item["type"]=="reply.complete")["text"]
        self.assertIn("현재 단계를 완료했습니다",reply)
        self.assertIn("0초가 된 뒤",reply)
        self.assertNotIn("확인",reply)
        self.assertEqual(
            [item["type"] for item in self.procedure_events(early)],
            ["procedure.error","procedure.state"],
        )

        self.now[0]+=10
        status,status_client=self.turn(
            "왜 0초인데 안 끝나요?",
            answer="이미 완료되었습니다.",
        )
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(status_client.chat.completions.calls,[])
        self.assertEqual(
            next(item for item in status.text
                 if item["type"]=="tool.call")["tool"],
            "get_current_step",
        )
        status_reply=next(item for item in status.text
                          if item["type"]=="reply.complete")["text"]
        self.assertIn("현재 단계를 완료했습니다",status_reply)
        self.assertNotIn("완료하고 다음",status_reply)

        completed,completed_client=self.turn(
            "현재 단계를 완료했습니다",
            answer="ordinary model text without a Tool",
        )
        self.assertEqual(completed_client.chat.completions.calls,[])
        sessions,events=self.snapshot()
        self.assertEqual(sessions[0]["current_step_index"],2)
        self.assertEqual(
            [event["event_type"] for event in events].count("step_completed"),2)
        self.assertEqual(
            [item["type"] for item in self.procedure_events(completed)],
            ["procedure.step_completed","procedure.state"],
        )
        self.assertEqual(
            next(item for item in completed.text
                 if item["type"]=="tool.call")["tool"],
            "complete_current_step",
        )

    def test_forced_unauthorized_completion_calls_fail_closed_without_mutation(self):
        self.start()
        for transcript in UNAUTHORIZED:
            with self.subTest(transcript=transcript):
                before = self.snapshot()
                socket = self.complete("demo-label-check", transcript)
                self.assertEqual(self.snapshot(), before)
                self.assert_sanitized_error(socket, "explicit_confirmation_required")

    def test_emergency_preserves_complete_procedure_and_conversation_state(self):
        self.start()
        self.complete("demo-label-check")
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
        with patch("voice_workflow_agent.brain.execute_tool", return_value={
            "status": "success", "report_id": "SR-20260725-A1B2C3",
            "report_status": "queued_for_handoff",
        }):
            self.turn("보고서를 제출해 주세요")
        self.assertEqual(self.snapshot(), before)
        # Even an adversarial forced Tool call cannot bypass the server-owned
        # pending-report confirmation branch.
        self.session.history.pending_report = dict(pending)
        socket = self.complete("demo-label-check", "현재 단계를 완료했습니다")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.controller.attached_session_id, attachment)
        self.assertEqual(self.procedure_events(socket), [])
        self.assertEqual(self.session.history.pending_report, pending)
        reply = next(
            item for item in socket.text if item["type"] == "reply.complete"
        )
        self.assertEqual(
            reply["text"],
            REPORT_CONFIRMATION_CLARIFICATION_TEXT["ko"],
        )
        self.session.history.pending_report = dict(pending)
        timer_status,_ = self.turn("왜 0초인데 안 끝나요?")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.procedure_events(timer_status), [])
        self.assertEqual(
            next(item for item in timer_status.text
                 if item["type"]=="reply.complete")["text"],
            REPORT_CONFIRMATION_CLARIFICATION_TEXT["ko"],
        )

    def test_confirmed_report_links_current_step_and_blocks_workflow(self):
        self.start()
        pending = {
            "location":"F","summary":"가상 표시창이 빨간색임",
            "urgency":"urgent","exposure_status":"unknown","language":"ko",
        }
        self.session.history.pending_report=dict(pending)
        with patch(
            "voice_workflow_agent.tools.create_safety_report",
            return_value={
                "status":"success","report_id":"SR-20260725-A1B2C3",
                "report_status":"queued_for_handoff",
            },
        ) as create:
            socket,_=self.turn("보고서를 제출해 주세요")

        workflow=create.call_args.kwargs["workflow_context"]
        self.assertEqual(workflow["procedure_id"],PROCEDURE_ID)
        self.assertEqual(workflow["step_id"],"demo-label-check")
        state=self.controller.current()["state"]
        self.assertEqual(state["status"],"blocked_for_handoff")
        self.assertEqual(
            state["handoff"]["report_id"],"SR-20260725-A1B2C3")
        self.assertEqual(
            [item["type"] for item in self.procedure_events(socket)],
            ["procedure.blocked_for_handoff","procedure.state"])
        self.assertIn(
            "관리자 인계를 위해 이 단계에서 차단",
            next(item for item in socket.text
                 if item["type"]=="reply.delta")["text"])
        blocked=self.complete("demo-label-check")
        self.assert_sanitized_error(
            blocked,"procedure_blocked_for_handoff")

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
        self.complete("demo-label-check")
        self.complete("demo-mix-timer")
        socket = self.complete("demo-indicator-observation")
        self.assertEqual([e["type"] for e in self.procedure_events(socket)],
                         ["procedure.step_completed", "procedure.completed",
                          "procedure.state"])
        sessions, events = self.snapshot()
        self.assertEqual((sessions[0]["status"], sessions[0]["current_step_index"]),
                         ("completed", 3))
        self.assertIsNotNone(sessions[0]["completed_at"])
        self.assertEqual([e["event_type"] for e in events].count("completed"), 1)
        before = self.snapshot()
        replay = self.complete("demo-indicator-observation")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([e["type"] for e in self.procedure_events(replay)],
                         ["procedure.state"])
        # Older-step replay is rejected by the real controller as stale.  The
        # server authorization boundary intentionally cannot authorize an old
        # step after completion.
        stale = self.controller.complete("demo-label-check")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(stale["status"],"error")
        self.assertEqual(stale["code"],"stale_step")
        self.assertEqual(stale["state"]["status"],"completed")

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
        mismatch = self.complete(
            "demo-mix-timer","현재 단계를 완료해 주세요")
        self.assertEqual(self.snapshot(), before)
        self.assert_sanitized_error(mismatch, "explicit_confirmation_required")
        self.store._connection.execute("""
            CREATE TRIGGER fail_real_transition BEFORE INSERT ON procedure_step_events
            WHEN NEW.event_type='step_completed'
            BEGIN SELECT RAISE(ABORT,'synthetic database path /tmp/private.sqlite'); END
        """)
        before = self.snapshot()
        failed = self.complete("demo-label-check")
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

        with patch("voice_workflow_agent.server.server_config", return_value=config), \
             patch("voice_workflow_agent.server.ProcedureController", side_effect=construct):
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
