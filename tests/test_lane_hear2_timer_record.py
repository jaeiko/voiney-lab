"""Lane HEAR2, decision 3 (2026-10-26): a timer command is kept, named and restored.

Through one real voice session with the workspace and the report store on
(lane N's harness): a timer started, paused, resumed and cancelled by voice
leaves ``timer_paused``, ``timer_resumed`` and ``timer_cancelled`` beside
``timer_started`` in the workspace ledger (each with the timer as it then
stood) and in the experiment record, under their Korean names; the screen
is told the operation; and a connection lost after a pause is restored
with the timer paused where it was (lane NET's restore reads the last
timer event), running again after a resume, and with no timer after a
cancel.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests.lane_n_support import VoiceNotesHarness, notes_fixture
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.experiment_reports import EVENT_NAMES_KO


def _timer_events(events):
    return [(e["event_type"], (e.get("payload") or {}).get("timer") or {}) for e in events
            if str(e["event_type"]).startswith("timer_")]


class TimerRecordTests(VoiceNotesHarness, unittest.TestCase):
    fixture = notes_fixture()

    def test_each_command_is_kept_in_the_ledger_and_the_record_under_its_name(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘", "1단계 완료했어", "타이머 시작해 줘",
            "타이머 일시정지해 줘", "타이머 다시 시작해 줘", "타이머 취소해 줘",
        )
        ledger = _timer_events(self.experiment()["events"])
        self.assertEqual([kind for kind, _ in ledger],
                         ["timer_started", "timer_paused", "timer_resumed", "timer_cancelled"], ledger)
        started, paused, resumed, cancelled = (timer for _, timer in ledger)
        self.assertEqual(started["state"], "running")
        self.assertEqual(paused["state"], "paused")
        self.assertTrue(paused["paused_at"])
        self.assertLessEqual(paused["remaining_seconds"], 600)
        self.assertEqual(resumed["state"], "running")
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(cancelled["step_id"], started["step_id"])
        record = _timer_events(self.report()["events"])
        self.assertEqual([kind for kind, _ in record],
                         ["timer_started", "timer_paused", "timer_resumed", "timer_cancelled"], record)
        for kind in ("timer_paused", "timer_resumed", "timer_cancelled", "timer_restarted"):
            self.assertIn(kind, EVENT_NAMES_KO)
        operations = [m["operation"] for m in socket.sent if m["type"] == "server.operation"]
        for name in ("step_timer_started", "step_timer_paused", "step_timer_resumed", "step_timer_cancelled"):
            self.assertIn(name, operations, operations)
        words = [m["text"] for m in socket.sent if m["type"] == "reply.complete"]
        self.assertTrue(any("멈췄어요" in w for w in words), words)
        self.assertTrue(any("취소했어요" in w for w in words), words)

    def test_a_restart_is_kept_too_and_a_refused_command_leaves_the_record_alone(self) -> None:
        self.turns("프로토콜 시작해줘", "1단계 완료했어", "타이머 시작해 줘", "타이머 처음부터 다시", "타이머 다시 시작해 줘")
        ledger = _timer_events(self.experiment()["events"])
        self.assertEqual([kind for kind, _ in ledger], ["timer_started", "timer_restarted"], ledger)
        self.assertEqual(ledger[1][1]["state"], "running")
        record = _timer_events(self.report()["events"])
        self.assertEqual([kind for kind, _ in record], ["timer_started", "timer_restarted"], record)


class TimerRestoreTests(VoiceNotesHarness, unittest.TestCase):
    fixture = notes_fixture()

    def _session_at_step_two(self) -> CuratedProtocolSession:
        curated = CuratedProtocolSession(self.fixture)
        curated.activate_configured()
        curated.active = True
        curated.current_index = 1
        return curated

    def test_a_paused_timer_is_restored_paused_a_resumed_one_running_a_cancelled_one_not_at_all(self) -> None:
        self.turns("프로토콜 시작해줘", "1단계 완료했어", "타이머 시작해 줘", "타이머 일시정지해 줘")
        state = self.experiment()
        restored = server_module._restore_standing_timer(self._session_at_step_two(), state)
        self.assertIsNotNone(restored, state["events"][-1])
        self.assertEqual(restored["state"], "paused")
        paused = _timer_events(state["events"])[-1][1]
        self.assertEqual(restored["remaining_seconds"], paused["remaining_seconds"])
        self.turns("프로토콜 시작해줘", "1단계 완료했어", "타이머 시작해 줘", "타이머 일시정지해 줘", "타이머 다시 시작해 줘")
        state = self.experiment()
        curated = self._session_at_step_two()
        restored = server_module._restore_standing_timer(curated, state)
        self.assertEqual(restored["state"], "running")
        resumed = _timer_events(state["events"])[-1][1]
        self.assertEqual(
            curated.timer_status()["started_at"], resumed["started_at"],
            "the shifted start is the one restored")
        self.turns("프로토콜 시작해줘", "1단계 완료했어", "타이머 시작해 줘", "타이머 취소해 줘")
        self.assertIsNone(server_module._restore_standing_timer(self._session_at_step_two(), self.experiment()))

    def test_a_paused_timer_restored_much_later_has_lost_no_time(self) -> None:
        self.turns("프로토콜 시작해줘", "1단계 완료했어", "타이머 시작해 줘", "타이머 일시정지해 줘")
        state = self.experiment()
        curated = self._session_at_step_two()
        server_module._restore_standing_timer(curated, state)
        later = datetime.now(tz=timezone.utc).timestamp() + 3600
        self.assertEqual(curated.timer_status(now=later)["state"], "paused")
        self.assertEqual(curated.timer_status(now=later)["remaining_seconds"],
                         _timer_events(state["events"])[-1][1]["remaining_seconds"])


if __name__ == "__main__":
    unittest.main()
