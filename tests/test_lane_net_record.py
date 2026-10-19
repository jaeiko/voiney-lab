"""Lane NET, decision 5 (2026-10-18): the server keeps a lost connection's gap in the record.

On the reconnected session the server appends one "connection_restored"
event to the experiment record, at the step the run stands on, in moments
and codes only (no words): when the lost connection was last heard and
whose note that is ("server" while the process ran through the loss,
"screen" after a restart), when the run was continued, the gap in seconds,
how many step timers ended inside the gap, why the page counted the
connection lost, and the experiment's status. Lane LOG's metrics read it.
Nothing for the experimenter's own "실험 이어하기" or for a run never started.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

import voiney_lab.server as server_module
from tests.lane_net_support import run_connection_script
from tests.test_lane_net_reconnect import ReconnectHarness
from voiney_lab.experiment_reports import ExperimentReportStore

KEYS = {"lost_at", "lost_at_source", "restored_at", "gap_seconds", "timers_ended", "reason", "status"}


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).isoformat()


class ConnectionRecordTests(ReconnectHarness, unittest.TestCase):
    """Decision 4's runs; only the record is looked at here."""

    def gaps(self) -> list[dict]:
        store = ExperimentReportStore(self.report_db)
        events = []
        for report in store.list_reports():
            events += [
                event for event in store.get_report(report["report_id"])["events"]
                if event["event_type"] == "connection_restored"]
        return events

    def test_the_gap_and_the_timer_ended_in_it_are_kept_as_moments_and_codes(self) -> None:
        session_id, durable = self.run_to_timer()
        heard = self.clock.now
        self.clock.advance(12)
        self.reconnect(session_id, durable)
        [event] = self.gaps()
        self.assertEqual(event["step_label"], "2")
        self.assertIsNone(event["user_wording"])
        payload = event["payload"]
        self.assertEqual(set(payload), KEYS)
        self.assertEqual(payload["lost_at"], _iso(heard))
        self.assertEqual(payload["restored_at"], _iso(self.clock.now))
        self.assertEqual(payload["gap_seconds"], 720.0)
        self.assertEqual(payload["timers_ended"], 1)
        self.assertEqual(payload["lost_at_source"], "server")
        self.assertEqual(payload["reason"], "browser_offline")
        self.assertEqual(payload["status"], "in_progress")

    def test_a_timer_that_ended_before_the_loss_is_not_counted(self) -> None:
        session_id, durable = self.run_to_timer(11)
        self.clock.advance(1)
        self.reconnect(session_id, durable)
        [event] = self.gaps()
        self.assertEqual(event["payload"]["timers_ended"], 0)
        self.assertEqual(event["payload"]["gap_seconds"], 60.0)

    def test_after_a_restart_the_pages_moment_is_kept_as_the_screens(self) -> None:
        session_id, durable = self.run_to_timer()
        server_module._HEARD_CONNECTIONS.pop(session_id, None)
        lost = self.clock.now + 30
        self.clock.advance(12)
        self.reconnect(session_id, durable, reconnect={"lost_at_ms": lost * 1000, "reason": "server_silent"})
        payload = self.gaps()[0]["payload"]
        self.assertEqual(payload["lost_at_source"], "screen")
        self.assertEqual(payload["lost_at"], _iso(lost))
        self.assertEqual(payload["reason"], "server_silent")
        self.assertEqual(payload["timers_ended"], 1)

    def test_an_unknown_reason_or_moment_is_kept_as_unknown(self) -> None:
        session_id, durable = self.run_to_timer()
        server_module._HEARD_CONNECTIONS.pop(session_id, None)
        self.clock.advance(12)
        self.reconnect(session_id, durable, reconnect={"lost_at_ms": "soon", "reason": "a story"})
        payload = self.gaps()[0]["payload"]
        self.assertEqual(
            (payload["lost_at"], payload["lost_at_source"], payload["gap_seconds"],
             payload["timers_ended"], payload["reason"]),
            (None, "unknown", None, None, None))

    def test_a_paused_run_keeps_its_status_in_the_record(self) -> None:
        session_id, durable = self.run_to_timer("잠깐 멈춰")
        self.reconnect(session_id, durable)
        self.assertEqual(self.gaps()[0]["payload"]["status"], "paused")

    def test_each_reconnection_is_kept_once(self) -> None:
        session_id, durable = self.run_to_timer()
        self.clock.advance(1)
        self.reconnect(session_id, durable)
        self.clock.advance(1)
        self.reconnect(session_id, self.experiment(session_id))
        self.assertEqual(len(self.gaps()), 2)
        self.assertEqual(len({event["event_key"] for event in self.gaps()}), 2)

    def test_the_experimenters_own_continue_and_a_run_never_started_keep_nothing(self) -> None:
        session_id, durable = self.run_to_timer()
        self.reconnect(session_id, durable, reconnect=False)
        self.assertEqual(self.gaps(), [])

        async def idle(socket, listener, say):
            socket.control({"type": "client.ping", "seq": 1, "sent_at_ms": 1.0})
            await asyncio.sleep(0.05)

        self.clocked(idle)
        never = self.listener.session_id
        self.reconnect(never, self.experiment(never))
        self.assertEqual(self.gaps(), [])


class RecordOnScreenTests(unittest.TestCase):

    def test_the_record_names_the_event(self) -> None:
        result = run_connection_script(r"""
assert(serverCodeText("experiment_report_event","connection_restored","이벤트")==="연결 끊김 뒤 다시 연결","the record's event has no name");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
