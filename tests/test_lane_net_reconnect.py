"""Lane NET, decision 4 (2026-10-18): the page reconnects and continues the same run.

The page's session.start that continues a run after a lost connection
carries "reconnect" (when the connection was lost, on the server's clock,
and why). The server then binds the same durable experiment to the new
connection and keeps its status -- a pause or a block is lifted only by
the experimenter -- restores the run, puts back the step timer the record
shows still running (same start, same end), and gives the timer's moments
the lost connection missed in the words "몇 분 남았어?" gets: "연결이 끊긴
동안 2단계 10분 타이머는 2분 전에 끝났어요." A moment that fell due before
the loss is not given again. A reconnection is not greeted as a new
session, and the wearer's measured voice is kept. The experimenter's own
"실험 이어하기" is unchanged: it lifts a pause and restores no timer.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import voiney_lab.server as server_module
from tests.lane_n_support import VoiceNotesHarness, error_message, ready_event
from voiney_lab.vad import level_reference_from
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store


class _Clock:
    """One clock for the step timer and the server's own notes."""

    def __init__(self) -> None:
        self.now = time.time()

    def advance(self, minutes: float) -> None:
        self.now += minutes * 60


def _events(socket, kind: str) -> list[dict]:
    return [item for item in socket.sent if item["type"] == kind]


class ReconnectHarness(VoiceNotesHarness):
    """A run, its lost connection and the page's reconnection, on one clock (also lane NET decision 5)."""

    def setUp(self) -> None:
        super().setUp()
        self.clock = _Clock()

    def clocked(self, scenario, *, start: dict | None = None):
        return self.voice(
            scenario,
            patch("voiney_lab.curated_protocol.time.time", side_effect=lambda: self.clock.now),
            patch.object(server_module, "_wall_clock", lambda: self.clock.now),
            start=start,
        )

    def run_to_timer(self, *after: str | float, heard_last: bool = True) -> tuple[str, dict]:
        """A run at step 2 with its 10-minute timer started; then the connection goes."""

        async def scenario(socket, listener, say):
            turn = 0
            for item in ("프로토콜 시작해줘", "1단계 완료했어", "타이머 시작해줘", *after):
                if isinstance(item, (int, float)):
                    self.clock.advance(item)
                    continue
                turn += 1
                await say(turn, item)
            if heard_last:
                # The page's last ping before the loss: the server heard it now.
                socket.control({"type": "client.ping", "seq": 1, "sent_at_ms": self.clock.now * 1000})
                await asyncio.sleep(0.05)

        first = self.clocked(scenario)
        self.started_at = next(
            item["state"]["timers"]["step"]["started_at"]
            for item in reversed(_events(first, "protocol.fixture.state"))
            if item["state"].get("timers", {}).get("step", {}).get("state") == "running")
        session_id = self.listener.session_id
        return session_id, self.experiment(session_id)

    def reconnect(self, session_id: str, durable: dict, *, scenario=None, reconnect: dict | None = None):
        async def nothing(socket, listener, say):
            await asyncio.sleep(0.05)

        start = {
            "experiment_session_id": session_id,
            "experiment_session_version": int(durable["version"]),
        }
        if reconnect is not False:
            start["reconnect"] = reconnect or {
                "lost_at_ms": self.clock.now * 1000, "reason": "browser_offline"}
        return self.clocked(scenario or nothing, start=start)

    @staticmethod
    def restored(socket) -> dict:
        return _events(socket, "protocol.fixture.state")[0]["state"]

    async def wait_for_notice(self, socket, listener, say) -> None:
        await socket.wait_for(lambda item: item["type"] == "protocol.timer.notice")


class ReconnectTests(ReconnectHarness, unittest.TestCase):

    def test_the_same_run_continues_with_its_timer_and_says_what_ended_meanwhile(self) -> None:
        session_id, durable = self.run_to_timer()
        self.clock.advance(12)
        socket = self.reconnect(session_id, durable, scenario=self.wait_for_notice)

        self.assertIsNone(error_message(socket))
        ready = ready_event(socket)
        self.assertTrue(ready.get("experiment_recovered"))
        self.assertTrue(ready.get("connection_restored"))
        state = self.restored(socket)
        self.assertTrue(state["active"])
        self.assertEqual(state["current_step_label"], "2")
        timer = state["timers"]["step"]
        self.assertEqual(timer["state"], "expired")
        self.assertEqual(timer["started_at"], self.started_at)
        notice = _events(socket, "protocol.timer.notice")[0]
        self.assertTrue(
            notice["text"].startswith("연결이 끊긴 동안 2단계 10분 타이머는 2분 전에 끝났어요."),
            notice["text"])
        self.assertIn("다음은 3단계", notice["text"])
        self.assertEqual(notice["notice_kind"], "timer_ended")
        continued = self.experiment(session_id)
        self.assertEqual(continued["status"], "in_progress")
        last = continued["events"][-1]
        self.assertEqual(last["event_type"], "session_recovered")
        self.assertEqual(last["payload"], {"previous_status": "in_progress", "reconnected": True})

    def test_a_last_minute_under_way_is_said_with_the_time_left(self) -> None:
        session_id, durable = self.run_to_timer()
        self.clock.advance(9.5)
        socket = self.reconnect(session_id, durable, scenario=self.wait_for_notice)
        notice = _events(socket, "protocol.timer.notice")[0]
        self.assertTrue(notice["text"].startswith("2단계 타이머 30초 남았어요."), notice["text"])
        self.assertEqual(notice["notice_kind"], "timer_last_minute")
        self.assertEqual(self.restored(socket)["timers"]["step"]["state"], "running")

    def test_what_fell_due_before_the_loss_is_not_said_again(self) -> None:
        session_id, durable = self.run_to_timer(11)
        self.clock.advance(1)
        socket = self.reconnect(session_id, durable)
        self.assertEqual(_events(socket, "protocol.timer.notice"), [])
        self.assertEqual(self.restored(socket)["timers"]["step"]["state"], "expired")

    def test_a_pause_stays_a_pause_and_the_timer_runs_through_it(self) -> None:
        session_id, durable = self.run_to_timer("잠깐 멈춰")
        self.assertEqual(durable["status"], "paused")
        socket = self.reconnect(session_id, durable)
        self.assertIsNone(error_message(socket))
        state = self.restored(socket)
        self.assertEqual(state["workflow_status"], "paused")
        self.assertEqual(state["timers"]["step"]["started_at"], self.started_at)
        continued = self.experiment(session_id)
        self.assertEqual(continued["status"], "paused")
        self.assertIsNotNone(continued["paused_at"])

    def test_the_experimenters_continue_needs_an_explicit_resume(self) -> None:
        session_id, durable = self.run_to_timer("잠깐 멈춰")

        async def resume(socket, listener, say):
            self.assertEqual(listener.curated_protocol_session.workflow_status, "paused")
            socket.control({"type": "workflow.resume", "configuration_id": listener.accepted_configuration_id,
                            "generation": listener.generation})
            await socket.wait_for(lambda event: event["type"] == "protocol.fixture.state" and event.get("action") == "resume")

        socket = self.reconnect(session_id, durable, reconnect=False, scenario=resume)
        self.assertIsNone(error_message(socket))
        self.assertNotIn("connection_restored", ready_event(socket))
        state = self.restored(socket)
        self.assertEqual(state["workflow_status"], "paused")
        self.assertEqual(state["timers"]["step"]["state"], "not_started")
        continued = self.experiment(session_id)
        self.assertEqual(continued["status"], "in_progress")
        self.assertEqual(continued["events"][-1]["event_type"], "session_in_progress")

    def test_a_reconnection_is_not_greeted_and_keeps_the_measured_voice(self) -> None:
        async def measured(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            listener.level_reference = level_reference_from(-31.5)
            socket.control({"type": "client.ping", "seq": 1, "sent_at_ms": 1.0})
            await asyncio.sleep(0.05)

        self.clocked(measured)
        session_id = self.listener.session_id
        durable = self.experiment(session_id)
        seen: dict = {}

        async def audio_ready(socket, listener, say):
            ready = ready_event(socket)
            socket.control({
                "type": "client.audio_ready", "configuration_id": ready["configuration_id"],
                "generation": ready["generation"], "audio_context_state": "running",
                "sample_rate": 48000,
            })
            await asyncio.sleep(0.2)
            seen["level"] = listener.level_reference

        socket = self.reconnect(session_id, durable, scenario=audio_ready)
        self.assertEqual(_events(socket, "session.greeting"), [])
        self.assertIsNotNone(seen["level"])
        self.assertEqual(seen["level"].reference_db, -31.5)

    def test_after_a_server_restart_the_pages_moment_is_used(self) -> None:
        session_id, durable = self.run_to_timer()
        server_module._HEARD_CONNECTIONS.pop(session_id, None)
        lost = self.clock.now + 30
        self.clock.advance(12)
        socket = self.reconnect(
            session_id, durable, scenario=self.wait_for_notice,
            reconnect={"lost_at_ms": lost * 1000, "reason": "server_silent"})
        notice = _events(socket, "protocol.timer.notice")[0]
        self.assertTrue(notice["text"].startswith("연결이 끊긴 동안 "), notice["text"])


class ReattachStoreTests(unittest.TestCase):
    """workspace_store.resume_experiment(reattach=True): the status stays."""

    def setUp(self) -> None:
        import tempfile

        from tests.identity_support import development_principal

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = initialize_workspace_store(WorkspaceSettings(True, __import__("pathlib").Path(tmp.name)))
        self.addCleanup(self.store.close)
        self.principal = development_principal()
        self.store.bootstrap_principal(self.principal)

    def started(self, status: str) -> dict:
        state = self.store.start_experiment(
            self.principal, protocol_id="protocol-net", protocol_revision_id="rev-1",
            current_step_id="step-1", current_step_label="1", voice_connection_id="voice-a")
        state = self.store.record_experiment_progress(
            self.principal, state["session_id"], expected_version=state["version"],
            event_key="start", event_type="protocol_started", step_id="step-1", step_label="1")
        if status != "in_progress":
            state = self.store.transition_experiment(
                self.principal, state["session_id"], action={"paused": "pause", "blocked": "block"}[status],
                expected_version=state["version"], event_key=f"to-{status}")
        return state

    def resume(self, state: dict, **kwargs) -> dict:
        return self.store.resume_experiment(
            self.principal, state["session_id"], expected_version=state["version"],
            protocol_id="protocol-net", protocol_revision_id="rev-1",
            voice_connection_id="voice-b", **kwargs)

    def test_a_reattach_keeps_each_status_and_says_it_reconnected(self) -> None:
        for status in ("in_progress", "paused", "blocked"):
            with self.subTest(status=status):
                state = self.started(status)
                resumed = self.resume(state, reattach=True)
                self.assertEqual(resumed["status"], status)
                self.assertEqual(resumed["paused_at"], state["paused_at"])
                self.assertEqual(resumed["last_voice_connection_id"], "voice-b")
                self.assertEqual(resumed["version"], state["version"] + 1)
                event = resumed["events"][-1]
                self.assertEqual(event["event_type"], "session_recovered")
                self.assertEqual(event["payload"], {"previous_status": status, "reconnected": True})

    def test_recovery_preserves_a_pause_until_an_explicit_resume(self) -> None:
        paused = self.started("paused")
        recovered = self.resume(paused)
        self.assertEqual(recovered["status"], "paused")
        self.assertEqual(recovered["paused_at"], paused["paused_at"])
        self.assertEqual(recovered["events"][-1]["event_type"], "session_recovered")
        self.assertEqual(recovered["events"][-1]["payload"], {"previous_status": "paused"})
        resumed = self.store.transition_experiment(
            self.principal, recovered["session_id"], action="resume",
            expected_version=recovered["version"], event_key="explicit-resume",
        )
        self.assertEqual(resumed["status"], "in_progress")
        self.assertIsNone(resumed["paused_at"])


if __name__ == "__main__":
    unittest.main()
