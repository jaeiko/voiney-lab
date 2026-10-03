"""An ended experiment takes no pause or resume, and the screen is told it ended.

Lane R part 2-b found it (decision 2 of 2026-10-03, its report §5, "함께 찾은
것" 2): after an experiment ended by voice ("실험 종료" + "네", or the last
step completed) the screen still believed the session was running, so its
pause button stayed on. Pressed, ``pause_workflow()`` succeeded on the
ended session, the workspace refused to pause a stopped experiment, and the
rollback called ``resume_workflow()`` -- which, finding the session
inactive, set it running again from step 1. The durable record stayed
stopped, so the two disagreed.

Decided for lane R3:

* An ended (stopped or completed) experiment refuses pause and resume, in
  the session and in the server, and the server's rollback never revives an
  experiment through ``resume_workflow()``.
* When an experiment ends -- the end confirmed by voice, or the last step
  completed -- the server tells the screen with an ``experiment.ended``
  event. The screen's buttons are the people's screen work; this lane fixes
  only the event's name and content.

The voice tests run a real ``voice_socket`` session over a scripted socket,
with the workspace and the experiment report store on, like the voice test
that found the pause bug.
"""

from __future__ import annotations

import asyncio
import unittest

from tests.protocol_vocabulary_support import SOURCE_PDF, miniprep_fixture
from tests.test_voice_pause_resume_persistence import VoiceSessionHarness
from voiney_lab.curated_protocol import EXPERIMENT_ENDED_START_REPLY, CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn


def _turn(session: CuratedProtocolSession, text: str, turn_id: int):
    return route_curated_runtime_turn(
        session, text, turn_id=turn_id, language="ko",
        configuration_id=1, generation=1,
    ).plan


def _projection(session: CuratedProtocolSession) -> tuple:
    return (
        session.active, session.current_index, session.workflow_status,
        session.pause_timer_status()["state"],
        session._experiment_started_at, session._experiment_ended_at,
    )


class EndedSessionControlTests(unittest.TestCase):
    def _ended_by_stop(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(miniprep_fixture())
        _turn(session, "시작", 1)
        session.current_index = 3
        _turn(session, "실험 종료", 2)
        _turn(session, "네", 3)
        self.assertEqual(session.workflow_status, "stopped")
        return session

    def _ended_by_completion(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(miniprep_fixture())
        _turn(session, "시작", 1)
        session.current_index = len(session.fixture.steps) - 1
        _turn(session, "완료했어", 2)
        _turn(session, "네", 3)
        self.assertEqual(session.workflow_status, "completed")
        return session

    def test_an_ended_experiment_refuses_pause_and_resume(self) -> None:
        for name, ended in (("stopped", self._ended_by_stop), ("completed", self._ended_by_completion)):
            with self.subTest(ended=name):
                session = ended()
                before = _projection(session)
                self.assertFalse(session.pause_workflow())
                self.assertEqual(_projection(session), before)
                self.assertFalse(session.resume_workflow())
                self.assertEqual(_projection(session), before)
                self.assertTrue(session.experiment_ended)

    def test_a_voice_pause_after_the_end_says_it_ended(self) -> None:
        for said in ("잠깐", "일시정지"):
            with self.subTest(said=said):
                session = self._ended_by_stop()
                before = _projection(session)
                plan = _turn(session, said, 4)
                self.assertFalse(plan.state_changed)
                self.assertEqual(plan.speech_text, EXPERIMENT_ENDED_START_REPLY["ko"])
                self.assertNotIn("일시정지했어요", plan.speech_text)
                self.assertEqual(_projection(session), before)

    def test_a_running_experiment_still_pauses_and_resumes(self) -> None:
        session = CuratedProtocolSession(miniprep_fixture())
        _turn(session, "시작", 1)
        session.current_index = 2
        self.assertFalse(session.experiment_ended)
        self.assertTrue(session.pause_workflow())
        self.assertEqual(session.workflow_status, "paused")
        self.assertTrue(session.resume_workflow())
        self.assertEqual((session.active, session.current_index, session.workflow_status),
                         (True, 2, "active"))

    def test_undoing_a_pause_never_starts_an_experiment(self) -> None:
        # The bench pause of a protocol that has not started, rolled back:
        # the rollback lifts the pause and starts nothing.
        session = CuratedProtocolSession(miniprep_fixture())
        session.activate_configured()
        self.assertTrue(session.pause_workflow())
        session.undo_pause()
        self.assertFalse(session.active)
        self.assertIsNone(session._experiment_started_at)
        self.assertEqual(session.pause_timer_status()["state"], "active")

    def test_a_new_session_after_the_end_pauses_again(self) -> None:
        session = self._ended_by_stop()
        session.reset()
        session.activate_configured()
        _turn(session, "시작", 10)
        self.assertFalse(session.experiment_ended)
        self.assertTrue(session.pause_workflow())


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class EndedExperimentBenchControlVoiceTests(VoiceSessionHarness, unittest.TestCase):
    """Over the WebSocket, with the workspace and the report store on."""

    async def _press(self, socket, kind: str) -> None:
        """Press a bench button and wait until the server has handled it."""

        before = len(socket.sent)
        socket.control({"type": kind})
        for _ in range(500):
            if any(
                item["type"] in {
                    "protocol.fixture.state", "error", "workflow.control.refused",
                }
                for item in socket.sent[before:]
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{kind} was not handled")

    def _run(self, *said, press=("workflow.pause",), at_last_step=False):
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            if at_last_step:
                # Walking 25 in-gel steps by voice is not what is tested.
                listener.curated_protocol_session.current_index = len(self.fixture.steps) - 1
            for turn_id, text in enumerate(said, 2):
                await say(turn_id, text)
            seen["ended"] = self._snapshot(listener)
            seen["ended_projection"] = _projection(listener.curated_protocol_session)
            for kind in press:
                await self._press(socket, kind)
                seen[kind] = self._snapshot(listener)
                seen[kind + ".projection"] = _projection(listener.curated_protocol_session)

        socket, _ = self._session(scenario)
        return socket, seen

    def test_the_pause_button_after_a_voice_end_does_not_revive_step_1(self) -> None:
        socket, seen = self._run("다음", "완료했어", "실험 종료", "네")
        ended = seen["ended"]
        self.assertEqual(ended["workflow_status"], "stopped")
        self.assertEqual(ended["durable_status"], "stopped")
        paused = seen["workflow.pause"]
        # The reported bug: the session ran again from step 1 while the
        # durable record stayed stopped.
        self.assertEqual(paused["workflow_status"], "stopped")
        self.assertEqual(paused["durable_status"], "stopped")
        self.assertEqual(seen["workflow.pause.projection"], seen["ended_projection"])
        self.assertFalse(listener_active(seen["workflow.pause.projection"]))
        refused = [item for item in socket.sent if item["type"] == "workflow.control.refused"]
        self.assertEqual(
            [(item["action"], item["reason"], item["status"]) for item in refused],
            [("pause", "experiment_ended", "stopped")],
        )

    def test_the_resume_button_after_the_last_step_does_nothing(self) -> None:
        socket, seen = self._run(
            "다음", "완료했어", press=("workflow.pause", "workflow.resume"), at_last_step=True,
        )
        self.assertEqual(seen["ended"]["workflow_status"], "completed")
        for kind in ("workflow.pause", "workflow.resume"):
            with self.subTest(kind):
                self.assertEqual(seen[kind]["workflow_status"], "completed")
                self.assertEqual(seen[kind]["durable_status"], "completed")
                self.assertEqual(seen[kind + ".projection"], seen["ended_projection"])
        refused = [
            (item["action"], item["reason"], item["status"])
            for item in socket.sent if item["type"] == "workflow.control.refused"
        ]
        self.assertEqual(refused, [
            ("pause", "experiment_ended", "completed"),
            ("resume", "experiment_ended", "completed"),
        ])

    def test_a_voice_end_tells_the_screen_the_experiment_ended(self) -> None:
        socket, _ = self._run("실험 종료", "네", press=())
        ended = [item for item in socket.sent if item["type"] == "experiment.ended"]
        self.assertEqual(len(ended), 1)
        event = ended[0]
        self.assertEqual(event["turn_id"], 3)
        self.assertEqual(event["status"], "stopped")
        self.assertEqual(event["step_label"], "1")
        self.assertEqual(event["step_count"], len(self.fixture.steps))
        self.assertEqual(event["report"], "saved")
        self.assertIsInstance(event["ended_at"], str)
        self.assertIsInstance(event["experiment_session_id"], str)
        self.assertEqual(event["bench_controls"], {"pause": False, "resume": False})
        # Sent once the turn's state is on the screen, never for the question.
        self.assertEqual(socket.for_turn(2, "experiment.ended"), [])
        order = [item["type"] for item in socket.sent if item.get("turn_id") == 3]
        self.assertLess(order.index("protocol.fixture.state"), order.index("experiment.ended"))

    def test_finishing_the_last_step_tells_the_screen_it_completed(self) -> None:
        last = len(self.fixture.steps)
        socket, _ = self._run("다음", "완료했어", press=(), at_last_step=True)
        ended = [item for item in socket.sent if item["type"] == "experiment.ended"]
        self.assertEqual([(item["status"], item["step_label"]) for item in ended],
                         [("completed", str(last))])

    def test_turns_that_end_nothing_send_no_end(self) -> None:
        socket, _ = self._run("다음", "완료했어", "실험 종료", "아니", press=())
        self.assertEqual([item for item in socket.sent if item["type"] == "experiment.ended"], [])


def listener_active(projection: tuple) -> bool:
    return bool(projection[0])


if __name__ == "__main__":
    unittest.main()
