"""Values the screen needs from the server (lane R6, decision 5).

Lane U (PR #34) section 7 asked for them, and the screen code is left for
lane U2:

* the experiment record state and each /api/workspace/experiments item carry
  ``protocol_title`` and ``day_sequence`` (the Nth started that UTC day,
  ``day_sequence_date``);
* (the approval record and the refused-finding codes of lane R6 left with
  the approval flow, lane DI, 2026-10-08);
* a turn whose playback was cut by a barge-in candidate still gets its end
  state: "complete" when the candidate is rejected, "cancelled" when the
  next turn is committed.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.identity import Principal, Role
from voiney_lab.server import (
    ListenerSession,
    _public_experiment_report_state,
    _send_playback_terminal,
    cancel_cascade_generation,
)
from voiney_lab.vad import TurnState
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

SHA = "a" * 64


class ExperimentRecordNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = ExperimentReportStore(Path(self._temp.name) / "reports.sqlite")

    def _open(self, session_id: str) -> dict:
        return self.store.open_report(
            session_id=session_id, protocol_id="in-gel", protocol_title="In-gel digestion",
            protocol_revision="rev-1", protocol_sha256=SHA,
            readiness_status="guidance_ready", development_only=True,
        )

    def test_the_record_state_names_its_protocol_and_its_place_that_day(self) -> None:
        first = self._open("session-1")
        second = self._open("session-2")
        public = _public_experiment_report_state(second)
        self.assertEqual(public["protocol_title"], "In-gel digestion")
        self.assertEqual(public["day_sequence"], 2)
        self.assertEqual(public["day_sequence_date"], second["started_at"][:10])
        self.assertEqual(public["timezone"], "UTC")
        self.assertEqual(_public_experiment_report_state(first)["day_sequence"], 1)
        listed = {item["session_id"]: item for item in self.store.list_reports()}
        self.assertEqual(listed["session-2"]["day_sequence"], 2)
        self.assertEqual(listed["session-1"]["protocol_title"], "In-gel digestion")


def _principal(name: str, role: Role = Role.RESEARCHER) -> Principal:
    return Principal(
        principal_id=f"principal-{name}", subject=f"test:{name}",
        organization_id="tenant-a", display_name=f"Dr. {name}",
        roles=frozenset({role}), authentication_method="test",
    )


class ExperimentListNameTests(unittest.TestCase):
    def test_each_item_names_its_protocol_and_its_place_that_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = initialize_workspace_store(WorkspaceSettings(True, Path(temp)))
            first, second = _principal("a"), _principal("b")
            reviewer = _principal("c", Role.REVIEWER)
            for principal in (first, second, reviewer):
                store.bootstrap_principal(principal)
            store.start_experiment(
                first, session_id="experiment-1", protocol_id="in-gel",
                protocol_revision_id="rev-1")
            store.start_experiment(
                second, session_id="experiment-2", protocol_id="in-gel",
                protocol_revision_id="rev-1")
            items = {
                item["session_id"]: item for item in store.list_experiments(reviewer)
            }
            store.close()
        self.assertEqual(items["experiment-1"]["day_sequence"], 1)
        self.assertEqual(items["experiment-2"]["day_sequence"], 2)
        self.assertEqual(
            items["experiment-2"]["day_sequence_date"],
            items["experiment-2"]["started_at"][:10],
        )
        # Not a workspace revision: the server fills the title from the catalog.
        self.assertIn("protocol_title", items["experiment-1"])
        self.assertIsNone(items["experiment-1"]["protocol_title"])


class Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))


class InterruptedPlaybackEndStateTests(unittest.TestCase):
    def _playing(self) -> ListenerSession:
        session = ListenerSession()
        session.start()
        session.active_turn_id = 1
        session.turn_generations[1] = session.generation
        session.detector.state = TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        for state in ("listening", "transcribing", "routing", "synthesizing", "playing"):
            self.assertIsNotNone(session.advance_turn_progress(1, session.generation, state))
        return session

    def test_a_rejected_candidate_still_ends_the_turn_complete(self) -> None:
        session = self._playing()
        generation = session.generation
        session._interrupt_candidate_identity = (1, generation)
        self.assertFalse(session.playback_ended(1))
        # The candidate is rejected; the playback.ended it held back is taken.
        session._reset_interrupt_input()
        self.assertTrue(session.resume_deferred_playback_end(1, generation))
        self.assertFalse(session.resume_deferred_playback_end(1, generation))
        socket = Socket()
        asyncio.run(_send_playback_terminal(socket, session, 1, cooldown_ms=0))
        state, changed = socket.text
        self.assertEqual((state["type"], state["state"]), ("turn.state", "complete"))
        self.assertEqual(changed["type"], "state.changed")

    def test_a_committed_next_turn_ends_the_old_turn_cancelled(self) -> None:
        session = self._playing()
        socket = Socket()
        interruption = SimpleNamespace(
            kind="assistant.interrupted", turn_id=1, generation=session.generation,
            superseding_turn_id=2, superseding_generation=session.generation + 1,
            reason="confirmed_speech", latency_ms=None,
        )
        asyncio.run(cancel_cascade_generation(socket, session, None, interruption))
        kinds = [(item["type"], item.get("state")) for item in socket.text]
        self.assertEqual(
            kinds, [("cascade.playback.clear", "cancelled"), ("turn.state", "cancelled")],
        )


if __name__ == "__main__":
    unittest.main()
