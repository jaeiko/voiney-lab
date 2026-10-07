"""An experiment opened with a later start can be continued (lane N, decision 9).

Lane R7 (decision 3) let "N단계부터 시작해줘" skip the steps before N once
the researcher said yes. The durable record kept the skip, but the voice
session's recovery passed only the completed steps, and the recovery refuses
a run whose earlier steps are not all complete -- so after the connection
dropped "실험 이어하기" failed, and so did continuing from a checkpoint of
such a run. Now the recovery is handed the skipped steps from the record
(the start's own event, or the copy a checkpoint restart carries), and
accepts exactly the steps before the start in place of completions.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.lane_n_support import VoiceNotesHarness, error_message, notes_fixture, ready_event
from voiney_lab.curated_protocol import CuratedProtocolFixtureError, CuratedProtocolSession
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

STEPS = tuple(f"step-{index}" for index in range(1, 7))


class RestoreRuleTests(unittest.TestCase):
    """The rule itself: the steps before a later start, and nothing else."""

    def session(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(notes_fixture())
        session.activate_configured()
        return session

    def test_the_steps_before_a_later_start_stand_in_for_completions(self) -> None:
        session = self.session()
        session.restore_experiment_progress(
            current_step_id="step-4", completed_step_ids=("step-3",),
            skipped_step_ids=("step-1", "step-2"),
        )
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 3)

    def test_without_them_the_recovery_is_refused_as_before(self) -> None:
        with self.assertRaises(CuratedProtocolFixtureError):
            self.session().restore_experiment_progress(
                current_step_id="step-4", completed_step_ids=("step-3",))

    def test_skipped_steps_must_be_exactly_the_steps_before_the_start(self) -> None:
        for skipped, completed in (
            (("step-2",), ("step-1", "step-3")),          # not from the first step
            (("step-1", "step-3"), ("step-2",)),          # not a run of steps
            (("step-1", "step-2", "step-3", "step-4"), ()),  # past the current step
            (("step-1", "step-2"), ("step-2", "step-3")),  # skipped and completed
        ):
            with self.subTest(skipped=skipped), self.assertRaises(CuratedProtocolFixtureError):
                self.session().restore_experiment_progress(
                    current_step_id="step-4", completed_step_ids=completed,
                    skipped_step_ids=skipped,
                )


class ServedRecoveryTests(VoiceNotesHarness, unittest.TestCase):
    """The production path: session.start with the experiment to continue."""

    def resume(self, session_id: str, version: int, *said: str):
        return self.turns(*said, start={
            "experiment_session_id": session_id, "experiment_session_version": version,
        })

    @staticmethod
    def restored(socket) -> dict:
        return next(
            item["state"] for item in socket.sent if item["type"] == "protocol.fixture.state")

    def test_a_later_start_is_continued_after_the_connection_dropped(self) -> None:
        self.turns("3단계부터 시작해줘", "응", "3단계 완료했어")
        session_id = self.listener.session_id
        durable = self.experiment(session_id)
        self.assertEqual(durable["status"], "in_progress")
        self.assertEqual(durable["current_step_id"], "step-4")

        socket = self.resume(session_id, int(durable["version"]), "4단계 완료했어")

        self.assertIsNone(error_message(socket))
        self.assertTrue(ready_event(socket).get("experiment_recovered"))
        restored = self.restored(socket)
        self.assertTrue(restored["active"])
        self.assertEqual(restored["current_step_label"], "4")
        continued = self.experiment(session_id)
        self.assertEqual(
            [item["step_id"] for item in continued["completed_steps"]], ["step-3", "step-4"])
        self.assertEqual(continued["current_step_id"], "step-5")

    def test_a_checkpoint_of_a_later_start_is_continued(self) -> None:
        self.turns("3단계부터 시작해줘", "응", "3단계 완료했어", "실험 종료해", "응")
        stopped = self.experiment(self.listener.session_id)
        self.assertEqual(stopped["status"], "stopped")
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            places = store.experiment_timeline(
                self.principal, stopped["session_id"])["recovery"]["checkpoints"]
            self.assertEqual([place["step_id"] for place in places], ["step-4"])
            self.assertEqual(places[0]["skipped_step_ids"], ["step-1", "step-2"])
            new = store.restart_experiment_from_checkpoint(
                self.principal, stopped["session_id"], checkpoint_step_id="step-4",
                expected_version=int(stopped["version"]),
            )
        finally:
            store.close()
        self.assertIn(
            "steps_skipped_carried_over", [event["event_type"] for event in new["events"]])
        html = (Path(__file__).resolve().parents[1] / "src/voiney_lab/static/index.html").read_text(
            encoding="utf-8")
        self.assertIn('steps_skipped_carried_over:"건너뛴 단계 이어받음"', html)

        socket = self.resume(str(new["session_id"]), int(new["version"]))

        self.assertIsNone(error_message(socket))
        self.assertTrue(ready_event(socket).get("experiment_recovered"))
        restored = self.restored(socket)
        self.assertTrue(restored["active"])
        self.assertEqual(restored["current_step_label"], "4")


if __name__ == "__main__":
    unittest.main()
