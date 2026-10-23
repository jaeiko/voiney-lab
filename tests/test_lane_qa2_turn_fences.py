"""Reproduce cancellation, takeover and malformed-control failures at /ws."""
import asyncio
import unittest
from unittest.mock import patch

from tests.lane_n_support import VoiceNotesHarness
from voiney_lab import server as srv
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store


class TurnFenceTests(VoiceNotesHarness, unittest.TestCase):
    def test_pause_during_a_read_only_turn_survives_cancellation(self):
        entered = None
        armed = False

        async def thread(function, *args, **kwargs):
            if armed and function is srv.synthesize:
                entered.set()
                await asyncio.Event().wait()
            return function(*args, **kwargs)

        async def scenario(socket, listener, say):
            nonlocal entered, armed
            entered = asyncio.Event()
            await say(1, "프로토콜 시작해줘")
            await say(2, "넘어가")
            cur = listener.curated_protocol_session
            cur._clarify_candidates = ["1단계 설명해 줘"]
            listener.cooldown_until = 0
            listener._restore_primary_detector(srv.TurnState.IDLE)
            listener.calibration_pending = False
            listener.next_turn_id = 50
            armed = True
            socket.control({"type": "client.clarify_choice", "candidate": "1단계 설명해 줘",
                            "configuration_id": 1, "generation": listener.generation, "turn_id": 2})
            await asyncio.wait_for(entered.wait(), 3)
            socket.control({"type": "workflow.pause", "configuration_id": 1, "generation": listener.generation})
            await socket.wait_for(lambda e: e["type"] == "protocol.fixture.state" and e.get("action") == "pause")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(cur.workflow_status, "paused")
            self.assertEqual(self.experiment(listener.session_id)["status"], "paused")
            armed = False
            socket.control({"type": "workflow.resume", "configuration_id": 1, "generation": listener.generation})
            await socket.wait_for(lambda e: e["type"] == "protocol.fixture.state" and e.get("action") == "resume")
            await say(51, "1단계 완료했어")
            self.assertEqual(self.experiment(listener.session_id)["current_step_id"], "step-2")

        self.voice(scenario, patch.object(srv.asyncio, "to_thread", side_effect=thread))

    def test_cancel_after_workspace_commit_keeps_memory_and_report(self):
        original = srv._record_workspace_experiment_progress
        committed = None
        release = None
        armed = False

        async def thread(function, *args, **kwargs):
            result = function(*args, **kwargs)
            if armed and function is original:
                committed.set()
                await release.wait()
            return result

        async def scenario(socket, listener, say):
            nonlocal committed, release, armed
            committed, release = asyncio.Event(), asyncio.Event()
            await say(1, "프로토콜 시작해줘")
            await say(2, "넘어가")
            armed = True
            turn = asyncio.create_task(say(3, "1단계 완료했어"))
            await asyncio.wait_for(committed.wait(), 3)
            turn.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            self.assertEqual(listener.curated_protocol_session.current_index, 1)
            self.assertEqual(self.experiment(listener.session_id)["current_step_id"], "step-2")
            self.assertTrue(any(e["event_type"] == "step_completed" and e["step_label"] == "1"
                                for e in self.report()["events"]))
        self.voice(scenario, patch.object(srv.asyncio, "to_thread", side_effect=thread))

    def test_report_only_commit_also_survives_turn_cancellation(self):
        self.environment["VOINEY_LAB_WORKSPACE_ENABLED"] = "false"
        original = srv._record_experiment_report_plan
        committed = None
        release = None
        armed = False

        async def thread(function, *args, **kwargs):
            result = function(*args, **kwargs)
            if armed and function is original:
                committed.set()
                await release.wait()
            return result

        async def scenario(socket, listener, say):
            nonlocal committed, release, armed
            committed, release = asyncio.Event(), asyncio.Event()
            await say(1, "프로토콜 시작해줘")
            await say(2, "넘어가")
            armed = True
            turn = asyncio.create_task(say(3, "1단계 완료했어"))
            await asyncio.wait_for(committed.wait(), 3)
            turn.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            self.assertEqual(listener.curated_protocol_session.current_index, 1)
            self.assertTrue(any(e["event_type"] == "step_completed" and e["step_label"] == "1"
                                for e in self.report()["events"]))
        self.voice(scenario, patch.object(srv.asyncio, "to_thread", side_effect=thread))

    def test_old_connection_cannot_reclaim_a_session_with_a_memo(self):
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "넘어가")
            await say(3, "1단계 완료했어")
            store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
            try:
                state = store.get_experiment(self.principal, listener.session_id)
                store.resume_experiment(self.principal, listener.session_id, expected_version=state["version"],
                    protocol_id=state["protocol_id"], protocol_revision_id=state["protocol_revision_id"],
                    voice_connection_id="replacement-tab", reattach=True)
            finally:
                store.close()
            before = self.experiment(listener.session_id)
            report_before = self.report()["events"]
            await say(4, "기록해 줘, 젤 조각 색이 옅어")
            await say(5, "2단계 완료했어")
            after = self.experiment(listener.session_id)
            self.assertEqual(after["version"], before["version"])
            self.assertEqual(after["current_step_id"], "step-2")
            self.assertEqual(self.report()["events"], report_before)
            self.assertEqual(
                [(item["turn_id"], item["code"]) for item in socket.sent
                 if item["type"] == "experiment.session.error"],
                [(4, "workspace_conflict"), (5, "workspace_conflict")],
            )
        self.voice(scenario)

    def test_bad_controls_leave_the_connection_and_checkpoint_alive(self):
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "넘어가")
            before = listener.curated_protocol_session._checkpoint()
            for text in ('{"type":"report.status.get"}', '{bad', '{"type":"client.clarify_choice","candidate":42}'):
                offset = len(socket.sent)
                socket.inbox.put_nowait({"type": "websocket.receive", "text": text})
                await socket.wait_for(lambda e: e["type"] == "error" and e in socket.sent[offset:])
                self.assertTrue(listener.active)
                self.assertEqual(listener.curated_protocol_session._checkpoint(), before)
            await say(3, "1단계 완료했어")
            self.assertEqual(self.experiment(listener.session_id)["current_step_id"], "step-2")
        self.voice(scenario)
