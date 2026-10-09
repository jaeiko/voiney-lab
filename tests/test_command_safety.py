"""Pause, end and completion commands, as decided on 2026-10-02 (lane R0).

The lane R evaluation set (227 utterances, rule path only) found six turns
that changed state wrongly, all high risk: "다 했어" and "4단계 완료" moved on
with no question asked, "중지" ended the session where a pause was meant, and
"그만할래" and "여기서 끝낼게" ended it with no "종료" said. Twelve pause words
were not understood at all ("잠깐만", "잠깐!", "실험 중지", "스톱", "멈처",
"pause 해줘", …), nor were "실험 종료" and "오늘은 여기서 종료하자", nor "응응"
as a yes.

What the people running the pilot decided:

1. The pause words pause at once, ahead of every other reading. A question
   about pausing ("멈춰도 돼?", "중지해야 돼?") does not. "중지" no longer
   ends the session.
2. Wanting to stop without saying "종료" ("그만할래", "여기서 끝낼게", "실험
   끝내자", "오늘은 여기까지") pauses and says how to end: "실험을 끝내려면
   '실험 종료'라고 말씀해 주세요."
3. Only a command with "종료" in it ends the session, and only after one
   question, "실험을 종료할까요?": yes ends it, no leaves everything as it was.
   That question rolls back with the rest of the turn.
4. A completion that names its step ("현재 단계 완료했어", "4단계 완료" at 4)
   completes it as before. One that names nothing ("완료했어", "다 했어", …)
   asks "N단계 완료하셨나요?" once; at a step with an observed endpoint it
   asks for the observation instead, as before. The completion question
   "다음 단계" opens is worded the same way.
5. "응응", "응 응" and "어 응" say yes to the completion question.
6. A pause while a completion, observation or end question is open keeps
   the question; resuming asks it again.
"""

from __future__ import annotations

import unittest

from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture, miniprep_fixture
from tests.test_voice_pause_resume_persistence import VoiceSessionHarness
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.runtime_routing import route_curated_runtime_turn

PAUSE_WORDS = (
    "잠깐", "잠깐만", "잠깐만요", "잠깐만예", "잠깐!", "잠깐만!",
    "멈춰", "멈처", "멈춰봐", "멈춰 줘", "잠깐 멈춰 줘",
    "정지", "일시정지", "일시 정지", "중지", "중지해줘", "실험 중지",
    "스톱", "stop", "pause", "pause 해줘", "잠깐 스톱",
)
NOT_A_PAUSE = ("멈춰도 돼?", "중지해야 돼?", "멈춰도 돼", "중지해야 돼", "잠깐 멈춰도 돼?")
END_WITHOUT_THE_WORD = ("그만할래", "그만하자", "여기서 끝낼게", "실험 끝내자", "오늘은 여기까지")
END_HINT = "실험을 끝내려면 '실험 종료'라고 말씀해 주세요."
END_COMMANDS = (
    "종료", "실험 종료", "프로토콜 종료", "종료해줘", "프로토콜 종료해줘",
    "오늘은 여기서 종료하자", "end session",
)
END_QUESTION = "실험을 종료할까요?"
UNTARGETED_COMPLETION = (
    "완료했어", "완료", "끝났어", "끝냈어", "마쳤어", "다 했어", "다 했어요",
    "다했다", "완료했어요", "완료했습니다", "끝났어요", "다 했습니다",
)
TARGETED_COMPLETION = ("현재 단계 완료했어", "이번 단계 끝났어", "4단계 완료", "4단계 완료했어")
DOUBLED_YES = ("응응", "응 응", "어 응")


def _turn(session: CuratedProtocolSession, text: str, turn_id: int):
    return route_curated_runtime_turn(
        session, text, turn_id=turn_id, language="ko",
        configuration_id=1, generation=1,
    ).plan


class _AtStep4:
    """Miniprep at step 4, which has no observed endpoint."""

    def _at_step_4(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(miniprep_fixture())
        _turn(session, "시작", 1)
        session.current_index = 3
        return session


class PauseWordTests(_AtStep4, unittest.TestCase):
    """Decision 1."""

    def test_each_pause_word_pauses_and_keeps_the_place(self) -> None:
        for word in PAUSE_WORDS:
            with self.subTest(word=word):
                session = self._at_step_4()
                plan = _turn(session, word, 2)
                self.assertEqual(plan.action, CuratedProtocolAction.PAUSE)
                self.assertTrue(plan.state_changed)
                self.assertEqual(session.workflow_status, "paused")
                self.assertTrue(session.active)
                self.assertEqual(session.current_index, 3)

    def test_a_question_about_pausing_does_not_pause(self) -> None:
        for question in NOT_A_PAUSE:
            with self.subTest(question=question):
                session = self._at_step_4()
                plan = _turn(session, question, 2)
                self.assertNotEqual(plan.action, CuratedProtocolAction.PAUSE)
                self.assertNotEqual(plan.action, CuratedProtocolAction.STOP)
                self.assertEqual(session.workflow_status, "active")
                self.assertTrue(session.active)


class EndRequestTests(_AtStep4, unittest.TestCase):
    """Decisions 2 and 3."""

    def test_wanting_to_end_without_the_word_pauses_and_says_how(self) -> None:
        for said in END_WITHOUT_THE_WORD:
            with self.subTest(said=said):
                session = self._at_step_4()
                plan = _turn(session, said, 2)
                self.assertEqual(plan.action, CuratedProtocolAction.PAUSE)
                self.assertEqual(session.workflow_status, "paused")
                self.assertTrue(session.active)
                self.assertIsNone(session.experiment_timer_status().get("ended_at"))
                self.assertIn(END_HINT, plan.speech_text)
                self.assertIn(END_HINT, plan.display_text)

    def test_an_end_command_asks_once_and_yes_ends(self) -> None:
        for said in END_COMMANDS:
            with self.subTest(said=said):
                session = self._at_step_4()
                asked = _turn(session, said, 2)
                self.assertFalse(asked.state_changed)
                self.assertEqual(asked.speech_text, END_QUESTION)
                self.assertTrue(session.active)
                self.assertEqual(session.workflow_status, "active")
                ended = _turn(session, "네", 3)
                self.assertEqual(ended.action, CuratedProtocolAction.STOP)
                self.assertTrue(ended.state_changed)
                self.assertFalse(session.active)

    def test_no_to_the_end_question_changes_nothing(self) -> None:
        session = self._at_step_4()
        _turn(session, "실험 종료", 2)
        kept = _turn(session, "아니", 3)
        self.assertFalse(kept.state_changed)
        self.assertNotEqual(kept.action, CuratedProtocolAction.STOP)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 3)
        # The question was answered; a later yes answers nothing.
        later = _turn(session, "네", 4)
        self.assertNotEqual(later.action, CuratedProtocolAction.STOP)
        self.assertTrue(session.active)

    def test_the_end_question_is_asked_while_paused_too(self) -> None:
        session = self._at_step_4()
        _turn(session, "잠깐", 2)
        asked = _turn(session, "실험 종료", 3)
        self.assertEqual(asked.speech_text, END_QUESTION)
        self.assertTrue(session.active)
        ended = _turn(session, "네", 4)
        self.assertEqual(ended.action, CuratedProtocolAction.STOP)
        self.assertFalse(session.active)

    def test_the_end_question_rolls_back_with_the_turn(self) -> None:
        session = self._at_step_4()
        _turn(session, "실험 종료", 2)
        before_yes = session._checkpoint()
        _turn(session, "네", 3)
        self.assertFalse(session.active)
        # The server rolls the whole turn back when the record refuses it.
        session._restore(before_yes)
        self.assertTrue(session.active)
        ended = _turn(session, "네", 3)
        self.assertEqual(ended.action, CuratedProtocolAction.STOP)
        self.assertFalse(session.active)


class CompletionTests(_AtStep4, unittest.TestCase):
    """Decisions 4 and 5."""

    def test_a_completion_naming_nothing_asks_once(self) -> None:
        for said in UNTARGETED_COMPLETION:
            with self.subTest(said=said):
                session = self._at_step_4()
                asked = _turn(session, said, 2)
                self.assertEqual(asked.action, CuratedProtocolAction.CLARIFY_COMPLETION)
                self.assertFalse(asked.state_changed)
                self.assertEqual(asked.speech_text, "4단계 완료하셨나요?")
                self.assertEqual(session.current_index, 3)
                confirmed = _turn(session, "네", 3)
                self.assertEqual(confirmed.action, CuratedProtocolAction.NEXT)
                self.assertTrue(confirmed.state_changed)
                self.assertEqual(session.current_index, 4)

    def test_no_to_the_completion_question_keeps_the_step(self) -> None:
        session = self._at_step_4()
        _turn(session, "다 했어", 2)
        kept = _turn(session, "아니", 3)
        self.assertEqual(kept.action, CuratedProtocolAction.DECLINE_COMPLETION)
        self.assertEqual(session.current_index, 3)

    def test_a_completion_naming_this_step_completes_it(self) -> None:
        for said in TARGETED_COMPLETION:
            with self.subTest(said=said):
                session = self._at_step_4()
                moved = _turn(session, said, 2)
                self.assertEqual(moved.action, CuratedProtocolAction.NEXT)
                self.assertTrue(moved.state_changed)
                self.assertEqual(session.current_index, 4)

    def test_a_completion_naming_another_step_does_not_move(self) -> None:
        session = self._at_step_4()
        plan = _turn(session, "5단계 완료했어", 2)
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.current_index, 3)

    def test_next_step_moves_on_and_says_what_it_recorded(self) -> None:
        # Lane VX, decision 1: "다음 단계" moves on without the question
        # lane XO asked, and says what it recorded.
        session = self._at_step_4()
        moved = _turn(session, "다음 단계", 2)
        self.assertEqual(moved.action, CuratedProtocolAction.NEXT)
        self.assertTrue(moved.state_changed)
        self.assertEqual(session.current_index, 4)
        self.assertTrue(moved.speech_text.startswith("4단계 완료로 기록했어요."))

    def test_the_question_read_back_is_not_a_yes(self) -> None:
        session = self._at_step_4()
        _turn(session, "다 했어", 2)
        echo = _turn(session, "4단계 완료하셨나요", 3)
        self.assertFalse(echo.state_changed)
        self.assertEqual(session.current_index, 3)

    def test_doubled_yes_answers_the_completion_question(self) -> None:
        for yes in DOUBLED_YES:
            with self.subTest(yes=yes):
                session = self._at_step_4()
                # Lane VX, decision 1: "다음 단계" no longer asks; "다 했어" does.
                _turn(session, "다 했어", 2)
                confirmed = _turn(session, yes, 3)
                self.assertEqual(confirmed.action, CuratedProtocolAction.NEXT)
                self.assertEqual(session.current_index, 4)


class FrozenQuestionTests(_AtStep4, unittest.TestCase):
    """Decision 6."""

    def test_a_completion_question_is_asked_again_after_the_pause(self) -> None:
        session = self._at_step_4()
        _turn(session, "다 했어", 2)
        _turn(session, "잠깐", 3)
        self.assertEqual(session.workflow_status, "paused")
        held = _turn(session, "네", 4)
        self.assertFalse(held.state_changed)
        self.assertEqual(session.current_index, 3)
        resumed = _turn(session, "재개", 5)
        self.assertEqual(resumed.action, CuratedProtocolAction.RESUME)
        self.assertTrue(resumed.speech_text.endswith("4단계 완료하셨나요?"))
        self.assertIsNotNone(session.pending_completion_confirmation)
        confirmed = _turn(session, "네", 6)
        self.assertEqual(confirmed.action, CuratedProtocolAction.NEXT)
        self.assertEqual(session.current_index, 4)

    def test_an_end_question_is_asked_again_after_the_pause(self) -> None:
        session = self._at_step_4()
        _turn(session, "실험 종료", 2)
        _turn(session, "잠깐만", 3)
        resumed = _turn(session, "재개해줘", 4)
        self.assertTrue(resumed.speech_text.endswith(END_QUESTION))
        self.assertTrue(session.active)
        ended = _turn(session, "네", 5)
        self.assertEqual(ended.action, CuratedProtocolAction.STOP)
        self.assertFalse(session.active)

    def test_a_question_asked_again_is_answered_no_as_before(self) -> None:
        session = self._at_step_4()
        # Lane VX, decision 1: "다음 단계" no longer asks; "다 했어" does.
        _turn(session, "다 했어", 2)
        _turn(session, "정지", 3)
        _turn(session, "재개", 4)
        kept = _turn(session, "아니", 5)
        self.assertEqual(kept.action, CuratedProtocolAction.DECLINE_COMPLETION)
        self.assertEqual(session.current_index, 3)

    def test_with_no_question_open_resuming_asks_nothing(self) -> None:
        session = self._at_step_4()
        _turn(session, "잠깐", 2)
        resumed = _turn(session, "재개", 3)
        self.assertNotIn("완료하셨나요", resumed.speech_text)
        self.assertIsNone(session.pending_completion_confirmation)

    def test_the_held_question_rolls_back_with_the_turn(self) -> None:
        session = self._at_step_4()
        _turn(session, "다 했어", 2)
        _turn(session, "잠깐", 3)
        before_resume = session._checkpoint()
        _turn(session, "재개", 4)
        session._restore(before_resume)
        self.assertEqual(session.workflow_status, "paused")
        resumed = _turn(session, "재개", 4)
        self.assertTrue(resumed.speech_text.endswith("4단계 완료하셨나요?"))
        confirmed = _turn(session, "네", 5)
        self.assertEqual(confirmed.action, CuratedProtocolAction.NEXT)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class ObservationStepTests(unittest.TestCase):
    """Decisions 4 and 6 at in-gel's endpoint steps."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()
        cls.index = {step.source_label: i for i, step in enumerate(cls.fixture.steps)}

    def _at(self, label: str) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        _turn(session, "프로토콜 시작해줘", 1)
        session.current_index = self.index[label]
        return session

    def test_a_completion_naming_nothing_asks_for_the_observation(self) -> None:
        for label in ("7", "9", "20"):
            for said in ("완료했어", "다 했어", "끝났어"):
                with self.subTest(step=label, said=said):
                    session = self._at(label)
                    asked = _turn(session, said, 2)
                    self.assertEqual(asked.intent_kind, "observation_confirmation_required")
                    self.assertIsNotNone(session.pending_observation_confirmation)
                    self.assertIsNone(session.pending_completion_confirmation)
                    self.assertEqual(session.current_index, self.index[label])

    def test_an_observation_question_is_asked_again_after_the_pause(self) -> None:
        session = self._at("7")
        opened = _turn(session, "7단계 완료했어", 2)
        self.assertEqual(opened.intent_kind, "observation_confirmation_required")
        _turn(session, "잠깐만", 3)
        resumed = _turn(session, "재개", 4)
        self.assertTrue(resumed.speech_text.endswith(opened.speech_text))
        self.assertIsNotNone(session.pending_observation_confirmation)
        confirmed = _turn(session, "탈색됐어", 5)
        self.assertTrue(confirmed.state_changed)
        self.assertEqual(session.current_index, self.index["8"])


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class CommandSafetyVoiceTests(VoiceSessionHarness, unittest.TestCase):
    """The same, over the WebSocket with the workspace and report store on."""

    def _run(self, *said):
        seen = {}

        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            for turn_id, text in enumerate(said, 2):
                await say(turn_id, text)
                seen[turn_id] = self._snapshot(listener)
            seen["report_id"] = listener.experiment_report_id

        socket, _ = self._session(scenario)
        return socket, seen

    def _events(self, report_id) -> list[str]:
        report = ExperimentReportStore(self.report_db).get_report(report_id)
        return [event["event_type"] for event in report["events"]]

    def test_stop_word_pauses_the_record_and_does_not_end_it(self) -> None:
        socket, seen = self._run("중지", "재개")
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(socket.for_turn(2, "turn.route_decision")[-1]["action"], "pause")
        self.assertEqual(seen[2]["workflow_status"], "paused")
        self.assertEqual(seen[2]["durable_status"], "paused")
        self.assertEqual(seen[3]["durable_status"], "in_progress")
        self.assertNotIn("session_stopped", self._events(seen["report_id"]))

    def test_wanting_to_end_pauses_and_says_how(self) -> None:
        socket, seen = self._run("그만할래")
        self.assertEqual(self._errors(socket), [])
        self.assertIn(END_HINT, socket.reply(2))
        self.assertEqual(seen[2]["durable_status"], "paused")
        self.assertNotIn("session_stopped", self._events(seen["report_id"]))

    def test_end_command_asks_then_ends_on_yes(self) -> None:
        socket, seen = self._run("실험 종료", "네")
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(socket.reply(2), END_QUESTION)
        self.assertFalse(socket.for_turn(2, "turn.route_decision")[-1]["state_mutation"])
        self.assertEqual(seen[2]["durable_status"], "in_progress")
        self.assertTrue(socket.for_turn(3, "turn.route_decision")[-1]["state_mutation"])
        self.assertEqual(seen[3]["durable_status"], "stopped")
        self.assertIn("session_stopped", self._events(seen["report_id"]))

    def test_bare_completion_asks_then_completes_on_doubled_yes(self) -> None:
        socket, seen = self._run("다 했어", "응응")
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(socket.reply(2), "1단계 완료하셨나요?")
        self.assertEqual(seen[2]["step_index"], 0)
        self.assertEqual(seen[2]["durable"]["current_step_label"], "1")
        self.assertEqual(seen[3]["step_index"], 1)
        self.assertEqual(seen[3]["durable"]["current_step_label"], "2")
        self.assertEqual(self._events(seen["report_id"]).count("step_completed"), 1)

    def test_the_completion_question_survives_a_pause(self) -> None:
        socket, seen = self._run("다 했어", "잠깐만", "재개", "네")
        self.assertEqual(self._errors(socket), [])
        self.assertEqual(seen[3]["durable_status"], "paused")
        self.assertTrue(socket.reply(4).endswith("1단계 완료하셨나요?"))
        self.assertEqual(seen[4]["durable_status"], "in_progress")
        self.assertEqual(seen[5]["step_index"], 1)
        self.assertEqual(seen[5]["durable"]["current_step_label"], "2")


if __name__ == "__main__":
    unittest.main()
