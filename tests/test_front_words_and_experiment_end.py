"""Front words the rules missed, and how an experiment ends (lane R part 2-b).

The lane R evaluation set found front-rule turns the rules still sent on to
the model: "한 번 더 말해 줄래요", "뭐라고?", "방금 거 다시" (say it again,
F8), "시간 얼마나 남았지", "Time 얼마나 남았어?" (time left, F6) and "쫌만
기다려 봐" (a pause, F3). They are read by the front rules now. Near
sentences that ask something else ("뭐라고 써 있어?", "시간이 얼마나 걸려?",
"결과 기다려 봐야 해?") are left as they were.

Decision 2 of 2026-10-03, on an experiment that has ended:

* It is not started again by voice. A start said after the end ("시작해줘",
  and "재개", which used to begin step 1 again through resume_workflow())
  changes nothing and is answered "이 실험은 이미 끝났어요. 다음 실험은
  화면에서 프로토콜이나 세션을 골라 시작해 주세요." An explicit start of an
  experiment that never started is a front rule too.
* What is said when it ends tells how it ended and what was saved, as it
  happened: "모든 단계를 마쳐 실험이 끝났습니다." or "N단계에서 실험을
  종료했습니다.", then -- only once the report store has taken it -- "실험
  기록을 보고서로 저장했어요." / "지금까지의 기록을 보고서로 저장했어요." and
  the formats the screen can download. If the report could not be saved
  after the end was committed: "실험은 끝났지만 기록 저장에 실패했어요.
  화면에서 다시 시도해 주세요."
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.protocol_vocabulary_support import SOURCE_PDF, miniprep_fixture
from tests.test_voice_pause_resume_persistence import VoiceSessionHarness
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import (
    EXPERIMENT_ENDED_START_REPLY,
    CuratedProtocolAction,
    CuratedProtocolSession,
    CuratedProtocolSpeechMode,
)
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.runtime_routing import route_curated_runtime_turn

ENDED = EXPERIMENT_ENDED_START_REPLY["ko"]
SAY_IT_AGAIN = ("한 번 더 말해 줄래요", "뭐라고?", "방금 거 다시", "다시 한 번 말해 줘", "뭐라고요")
TIME_LEFT = ("시간 얼마나 남았지", "Time 얼마나 남았어?", "시간 몇 분 남았어")
WAIT = ("쫌만 기다려 봐", "좀 기다려 줘", "잠깐 기다려", "기다려")
NOT_THESE = (
    ("뭐라고 써 있어?", CuratedProtocolAction.REPEAT),
    ("결과 기다려 봐야 해?", CuratedProtocolAction.PAUSE),
)


def _turn(session: CuratedProtocolSession, text: str, turn_id: int):
    return route_curated_runtime_turn(
        session, text, turn_id=turn_id, language="ko",
        configuration_id=1, generation=1,
    ).plan


def _front(session: CuratedProtocolSession, text: str, turn_id: int):
    return session.front_plan(
        text, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
    )


def _projection(session: CuratedProtocolSession) -> tuple:
    return (
        session.active, session.current_index, session.workflow_status,
        session._experiment_started_at, session._experiment_ended_at,
    )


class _Miniprep:
    def _started(self, step_index: int = 3) -> CuratedProtocolSession:
        session = CuratedProtocolSession(miniprep_fixture())
        _turn(session, "시작", 1)
        session.current_index = step_index
        return session

    def _ended(self) -> CuratedProtocolSession:
        session = self._started()
        _turn(session, "실험 종료", 2)
        _turn(session, "네", 3)
        self.assertFalse(session.active)
        self.assertIsNotNone(session._experiment_ended_at)
        return session


class FrontWordTests(_Miniprep, unittest.TestCase):
    def test_say_it_again_is_the_front_repeat(self) -> None:
        for turn_id, said in enumerate(SAY_IT_AGAIN, 2):
            with self.subTest(said=said):
                session = self._started()
                _turn(session, "현재 단계 알려줘", 2)
                before = _projection(session)
                plan = _front(session, said, turn_id + 1)
                self.assertIsNotNone(plan)
                self.assertIs(plan.action, CuratedProtocolAction.REPEAT)
                self.assertEqual(session.last_front_rule, "repeat_last_reply")
                self.assertFalse(plan.state_changed)
                self.assertEqual(_projection(session), before)

    def test_time_left_is_the_front_timer_read(self) -> None:
        for said in TIME_LEFT:
            with self.subTest(said=said):
                session = self._started()
                plan = _front(session, said, 2)
                self.assertIsNotNone(plan)
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_STATUS)
                self.assertEqual(session.last_front_rule, "timer_remaining")
                self.assertFalse(plan.state_changed)

    def test_wait_pauses_at_once(self) -> None:
        for said in WAIT:
            with self.subTest(said=said):
                session = self._started()
                plan = _front(session, said, 2)
                self.assertIsNotNone(plan)
                self.assertIs(plan.action, CuratedProtocolAction.PAUSE)
                self.assertEqual(session.last_front_rule, "pause")
                self.assertEqual(session.workflow_status, "paused")
                self.assertEqual(session.current_index, 3)

    def test_near_sentences_keep_their_reading(self) -> None:
        for said, not_this in NOT_THESE:
            with self.subTest(said=said):
                session = self._started()
                plan = _turn(session, said, 2)
                self.assertIsNot(plan.action, not_this)
                self.assertEqual(session.workflow_status, "active")
        # Lane VX, decision 5: "시간이 얼마나 걸려?" asks the step's source
        # time (step_time), for a sidecar timer too -- not the time left.
        session = self._started()
        plan = _turn(session, "시간이 얼마나 걸려?", 2)
        self.assertEqual(plan.intent_kind, "step_duration_question")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.workflow_status, "active")


class EndedExperimentTests(_Miniprep, unittest.TestCase):
    def test_a_start_after_the_end_changes_nothing(self) -> None:
        for said in ("프로토콜 시작해줘", "시작하자", "실험 시작할게요", "재개", "재개해줘", "계속 진행"):
            with self.subTest(said=said):
                session = self._ended()
                before = _projection(session)
                plan = _turn(session, said, 4)
                self.assertEqual(plan.speech_text, ENDED)
                self.assertIs(plan.action, CuratedProtocolAction.INACTIVE)
                self.assertFalse(plan.state_changed)
                self.assertEqual(_projection(session), before)

    def test_the_start_after_the_end_is_a_front_rule(self) -> None:
        session = self._ended()
        plan = _front(session, "프로토콜 시작해줘", 4)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.speech_text, ENDED)
        self.assertEqual(session.last_front_rule, "start_command")
        self.assertFalse(session.active)

    def test_a_completed_experiment_is_not_started_again(self) -> None:
        session = self._started(step_index=len(miniprep_fixture().steps) - 1)
        last = session.fixture.steps[session.current_index].source_label
        done = _turn(session, f"{last}단계 완료", 2)
        self.assertEqual(session.workflow_status, "completed")
        self.assertTrue(done.state_changed)
        before = _projection(session)
        plan = _turn(session, "프로토콜 시작해줘", 3)
        self.assertEqual(plan.speech_text, ENDED)
        self.assertEqual(_projection(session), before)

    def test_an_experiment_never_started_is_started_by_a_front_rule(self) -> None:
        session = CuratedProtocolSession(miniprep_fixture())
        session.activate_configured()
        plan = _front(session, "프로토콜 시작해줘", 1)
        self.assertIsNotNone(plan)
        self.assertIs(plan.action, CuratedProtocolAction.START)
        self.assertEqual(session.last_front_rule, "start_command")
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 0)

    def test_a_new_session_on_the_screen_starts_again(self) -> None:
        session = self._ended()
        session.reset()
        plan = _turn(session, "프로토콜 시작해줘", 4)
        self.assertIs(plan.action, CuratedProtocolAction.START)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 0)

    def test_a_stop_says_the_step_it_ended_at(self) -> None:
        session = self._started()
        self.assertEqual(_turn(session, "실험 종료", 2).speech_text, "실험을 종료할까요?")
        plan = _turn(session, "네", 3)
        self.assertEqual(plan.speech_text, "4단계에서 실험을 종료했습니다.")
        self.assertIs(plan.speech_mode, CuratedProtocolSpeechMode.STOP)
        self.assertTrue(plan.state_changed)

    def test_finishing_the_last_step_says_every_step_is_done(self) -> None:
        session = self._started(step_index=len(miniprep_fixture().steps) - 1)
        last = session.fixture.steps[session.current_index].source_label
        plan = _turn(session, f"{last}단계 완료", 2)
        self.assertEqual(plan.speech_text, "모든 단계를 마쳐 실험이 끝났습니다.")
        self.assertTrue(plan.display_text.startswith("모든 단계를 마쳐 실험이 끝났습니다. "))
        self.assertTrue(plan.final_step)


class ReportSentenceTests(_Miniprep, unittest.TestCase):
    """The server's sentence once the report store has answered."""

    def test_saved_sentences_name_only_the_formats_the_screen_has(self) -> None:
        session = self._started(step_index=len(miniprep_fixture().steps) - 1)
        last = session.fixture.steps[session.current_index].source_label
        completed = _turn(session, f"{last}단계 완료", 2)
        acknowledged = server_module._acknowledge_report_persistence(completed, "ko")
        self.assertEqual(
            acknowledged.speech_text,
            "모든 단계를 마쳐 실험이 끝났습니다. 실험 기록을 보고서로 저장했어요. "
            "화면에서 Word나 마크다운 파일로 받을 수 있어요.",
        )
        self.assertTrue(acknowledged.display_text.startswith(acknowledged.speech_text + " "))
        with patch.object(server_module.importlib.util, "find_spec", return_value=None):
            markdown_only = server_module._acknowledge_report_persistence(completed, "ko")
        self.assertTrue(markdown_only.speech_text.endswith("화면에서 마크다운 파일로 받을 수 있어요."))
        self.assertNotIn("Word", markdown_only.speech_text)

        session = self._started()
        _turn(session, "실험 종료", 2)
        stopped = _turn(session, "네", 3)
        self.assertEqual(
            server_module._acknowledge_report_persistence(stopped, "ko").speech_text,
            "4단계에서 실험을 종료했습니다. 지금까지의 기록을 보고서로 저장했어요. "
            "화면에서 Word나 마크다운 파일로 받을 수 있어요.",
        )

    def test_turns_that_end_nothing_get_no_saved_sentence(self) -> None:
        session = self._started()
        asked = _turn(session, "실험 종료", 2)
        self.assertIs(server_module._acknowledge_report_persistence(asked, "ko"), asked)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class ExperimentEndVoiceTests(VoiceSessionHarness, unittest.TestCase):
    """Over the WebSocket, with the workspace and the report store on."""

    def _run(self, *said, extra_patches=()):
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            for turn_id, text in enumerate(said, 2):
                await say(turn_id, text)
                seen[turn_id] = self._snapshot(listener)
            seen["report_id"] = listener.experiment_report_id

        socket, _ = self._session(scenario, *extra_patches)
        return socket, seen

    def test_a_stop_says_it_saved_the_record_only_after_the_report_took_it(self) -> None:
        socket, seen = self._run("실험 종료", "네", "프로토콜 시작해줘")
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(
            socket.reply(3),
            "1단계에서 실험을 종료했습니다. 지금까지의 기록을 보고서로 저장했어요. "
            "화면에서 Word나 마크다운 파일로 받을 수 있어요.",
        )
        self.assertEqual(seen[3]["durable_status"], "stopped")
        report = ExperimentReportStore(self.report_db).get_report(seen["report_id"])
        self.assertEqual(report["status"], "stopped")
        # Said after the end, a start changes nothing, here or on record.
        self.assertEqual(socket.reply(4), ENDED)
        self.assertFalse(socket.for_turn(4, "turn.route_decision")[-1]["state_mutation"])
        self.assertEqual(seen[4]["durable_status"], "stopped")
        self.assertEqual(seen[4]["workflow_status"], "stopped")

    def test_a_report_that_failed_after_the_end_is_said_so(self) -> None:
        real = server_module._record_experiment_report_plan

        def failing_on_the_end(session, curated, plan, **kwargs):
            if plan.action is CuratedProtocolAction.STOP and plan.state_changed:
                raise RuntimeError("report store unavailable")
            return real(session, curated, plan, **kwargs)

        socket, seen = self._run(
            "실험 종료", "네",
            extra_patches=(patch(
                "voiney_lab.server._record_experiment_report_plan",
                side_effect=failing_on_the_end,
            ),),
        )
        self.assertEqual(
            socket.reply(3),
            "1단계에서 실험을 종료했습니다. 실험은 끝났지만 기록 저장에 실패했어요. "
            "화면에서 다시 시도해 주세요.",
        )
        self.assertNotIn("저장했어요", socket.reply(3))
        self.assertEqual(seen[3]["durable_status"], "stopped")


if __name__ == "__main__":
    unittest.main()
