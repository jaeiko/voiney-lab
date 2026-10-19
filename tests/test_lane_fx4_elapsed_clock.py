"""Lane FX4, decision 1 (2026-10-19): a restored run keeps the experiment's own start.

Lane NET left a gap: a run restored after a lost connection, or continued
by the experimenter ("실험 이어하기"), counted the experiment's elapsed
clock from 0 again (``restore_experiment_progress`` set the start to now).
Both now keep the moment the experiment first started, as the durable
record holds it -- the experiment's "protocol_started" event, or, for a run
opened from a checkpoint (no such event), the session's own start. The step
timer's restoration is unchanged, and the silence before the first words
after a restoration still counts from the restoration.
"""

from __future__ import annotations

import time
import unittest
from datetime import datetime

import voiney_lab.server as server_module
from tests.lane_n_support import error_message, notes_fixture
from tests.test_lane_net_reconnect import ReconnectHarness
from voiney_lab.curated_protocol import CuratedProtocolSession


def _moment(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def _protocol_started_at(durable: dict) -> float:
    return _moment(next(
        str(event["created_at"]) for event in durable["events"]
        if event["event_type"] == "protocol_started"))


class RestoredRunClockTests(ReconnectHarness, unittest.TestCase):

    def experiment_clock(self, socket) -> dict:
        return self.restored(socket)["timers"]["experiment"]

    def test_a_reconnection_keeps_the_experiments_start(self) -> None:
        session_id, durable = self.run_to_timer()
        started = _protocol_started_at(self.experiment(session_id))
        self.clock.advance(12)
        socket = self.reconnect(session_id, durable)
        self.assertIsNone(error_message(socket))
        clock = self.experiment_clock(socket)
        self.assertEqual(clock["state"], "running")
        self.assertAlmostEqual(_moment(clock["started_at"]), started, delta=1)
        # Twelve minutes on the run's clock since the start, not 0.
        self.assertGreaterEqual(clock["elapsed_seconds"], 12 * 60 - 1)
        # The step timer is restored as before (lane NET, decision 4).
        self.assertEqual(self.restored(socket)["timers"]["step"]["started_at"], self.started_at)

    def test_the_experimenters_own_continue_keeps_it_too(self) -> None:
        session_id, durable = self.run_to_timer("잠깐 멈춰")
        started = _protocol_started_at(self.experiment(session_id))
        self.clock.advance(30)
        socket = self.reconnect(session_id, durable, reconnect=False)
        self.assertIsNone(error_message(socket))
        clock = self.experiment_clock(socket)
        self.assertAlmostEqual(_moment(clock["started_at"]), started, delta=1)
        self.assertGreaterEqual(clock["elapsed_seconds"], 30 * 60 - 1)
        # "실험 이어하기" still restores no step timer (unchanged).
        self.assertEqual(self.restored(socket)["timers"]["step"]["state"], "not_started")


class StoredStartTests(unittest.TestCase):
    """Where the server reads the start from (``_workspace_started_at``)."""

    def test_the_protocol_started_event_first_then_the_sessions_start(self) -> None:
        state = {
            "started_at": "2026-10-19T01:00:00+00:00",
            "events": [
                {"event_type": "session_created", "created_at": "2026-10-19T01:00:00+00:00"},
                {"event_type": "protocol_started", "created_at": "2026-10-19T01:02:00+00:00"},
                {"event_type": "protocol_started", "created_at": "2026-10-19T01:09:00+00:00"},
            ],
        }
        self.assertEqual(
            server_module._workspace_started_at(state),
            _moment("2026-10-19T01:02:00+00:00"))
        # A run opened from a checkpoint has no protocol_started event.
        self.assertEqual(
            server_module._workspace_started_at({**state, "events": []}),
            _moment("2026-10-19T01:00:00+00:00"))
        for broken in ({}, {"started_at": "yesterday"}, None):
            with self.subTest(state=broken):
                self.assertIsNone(server_module._workspace_started_at(broken))


class RestoreStartTests(unittest.TestCase):
    """``CuratedProtocolSession.restore_experiment_progress(started_at=...)``."""

    def session(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(notes_fixture())
        session.activate_configured()
        return session

    def restore(self, session: CuratedProtocolSession, **extra) -> None:
        session.restore_experiment_progress(
            current_step_id=session.fixture.steps[1].step_id,
            completed_step_ids=(session.fixture.steps[0].step_id,),
            **extra,
        )

    def test_the_given_start_is_kept_and_none_is_now(self) -> None:
        session = self.session()
        started = time.time() - 3600
        self.restore(session, started_at=started)
        status = session.experiment_timer_status()
        self.assertGreaterEqual(status["elapsed_seconds"], 3599)
        self.assertAlmostEqual(_moment(status["started_at"]), started, delta=0.001)
        other = self.session()
        self.restore(other)
        self.assertLessEqual(other.experiment_timer_status()["elapsed_seconds"], 1)

    def test_a_start_after_now_is_taken_as_now(self) -> None:
        session = self.session()
        before = time.time()
        self.restore(session, started_at=before + 3600)
        self.assertLessEqual(_moment(session.experiment_timer_status()["started_at"]), time.time())

    def test_the_silence_before_the_first_words_counts_from_the_restoration(self) -> None:
        session = self.session()
        self.restore(session, started_at=time.time() - 3600)
        silence = session.note_heard()
        self.assertIsNotNone(silence)
        self.assertLess(silence, 5)


if __name__ == "__main__":
    unittest.main()
