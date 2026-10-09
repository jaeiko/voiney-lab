"""Lane VX, decision 1 (field interviews, 2026-10-09): "다음 단계" by the form of the words.

Interview B: "'실험 시작', '다음 단계 알려줘'처럼 단순한 명령은 바로 수행하고,
중요한 데이터만 확인해 달라." Lane XO asked "N단계 완료하셨나요?" for every
"다음 단계"; lane CF kept that and added "방금 완료 취소" to take a step back.

* Words that move on ("다음", "다음 단계로", "넘어가", "다음으로 가자", "N단계
  끝났어") complete the current step without a question and present the next
  one, led by "3단계 완료로 기록했어요." -- and, the first time in a session,
  "잘못 넘어갔으면 '방금 완료 취소'라고 해 주세요."
* Words that ask ("다음 단계 알려줘", "다음 단계 뭐야", "다음엔 뭐 해") read the
  next step and change nothing; nothing is asked.
* The question is still asked, as before, when the current step's endpoint is
  still to be observed (a repeat-until), a timer running at the step has not
  run out, a question is open, or the experimenter chose the 바로 확인 way.
"""

from __future__ import annotations

import unittest

from tests.lane_cf_support import RecordedSession, Session, index_of, wash_session
from voiney_lab.curated_protocol import FRONT_RULES, forward_step_request
from voiney_lab.runtime_routing import route_curated_runtime_turn

FIRST_HINT = "잘못 넘어갔으면 '방금 완료 취소'라고 해 주세요."


class ForwardWordsTests(unittest.TestCase):

    def test_words_that_move_on(self) -> None:
        for said in (
            "다음", "다음 단계", "다음 단계로", "다음으로", "넘어가", "넘어가자", "넘어가 주세요",
            "넘어갈게", "다음으로 가자", "다음으로 넘어가", "다음 단계로 가", "다음 단계로 가자",
            "다음 단계로 넘어가줘", "다음 단계로 넘어가 주세요", "다음 단계 진행해줘",
            "다음 단계로 이동해줘", "다음 단계 진행", "그럼 다음", "이제 다음 단계",
            "어… 음… 다음 단계로 넘어가 주세요", "넘어가자 넘어가", "다음 단계로 넘어가자.",
            "완료했으니 다음으로 넘어가", "다 했어 다음 단계로 넘어가자",
        ):
            with self.subTest(said=said):
                self.assertTrue(forward_step_request(said))

    def test_words_that_ask_or_hedge_do_not_move_on(self) -> None:
        for said in (
            "다음 단계 알려줘", "다음 단계 뭐야", "다음엔 뭐 해", "다음 단계는?",
            "다음 단계로 넘어가도 돼?", "다음 단계로 넘어가도 돼", "넘어갈까?",
            "다음 단계 진행할까?", "어떻게 되면 다음으로 넘어가?", "다음 단계 안내해줘",
            "완료했어 다음 단계 알려줘", "탈색 아직 덜 됐는데 그냥 다음 단계 가자",
            "다음 거", "다음 단꼐", "완료했어", "끝났어", "3단계 완료했어", "next",
            "다음 단계에서 쓰는 시약 뭐야", "넘어가면 안 돼",
        ):
            with self.subTest(said=said):
                self.assertFalse(forward_step_request(said))

    def test_the_rule_is_described(self) -> None:
        self.assertIn("forward_step", FRONT_RULES)


class ForwardTests(Session, unittest.TestCase):

    def test_moving_on_completes_the_step_and_says_so_once_with_the_hint(self) -> None:
        self.open_with(index_of("3"))
        plan = self.say("다음")
        self.assertTrue(plan.state_changed)
        self.assertTrue(plan.reported_completion)
        self.assertEqual(self.label(), "4")
        self.assertEqual(self.session.last_front_rule, "forward_step")
        self.assertTrue(plan.speech_text.startswith(
            f"3단계 완료로 기록했어요. {FIRST_HINT} 4단계로 이동했습니다."), plan.speech_text)
        self.assertIn(f"3단계 완료로 기록했어요. {FIRST_HINT}", plan.display_text)
        # The hint is said once in a session; the record line every time.
        plan = self.say("다음 단계로")
        self.assertEqual(self.label(), "5")
        self.assertTrue(plan.speech_text.startswith(
            "4단계 완료로 기록했어요. 5단계로 이동했습니다."), plan.speech_text)
        self.assertNotIn(FIRST_HINT, plan.speech_text)
        plan = self.say("넘어가")
        self.assertEqual(self.label(), "6")
        self.assertNotIn(FIRST_HINT, plan.speech_text)

    def test_every_form_that_moves_on_moves_one_step(self) -> None:
        for said in ("다음", "다음 단계", "다음 단계로", "넘어가", "다음으로 가자",
                     "다음 단계로 넘어가줘", "완료했으니 다음으로 넘어가"):
            with self.subTest(said=said):
                self.open_with(index_of("3"))
                plan = self.say(said)
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.label(), "4")
                self.assertTrue(plan.speech_text.startswith("3단계 완료로 기록했어요."))

    def test_a_named_completion_says_the_same_record_line(self) -> None:
        self.open_with(index_of("3"))
        plan = self.say("3단계 끝났어")
        self.assertEqual(self.label(), "4")
        self.assertEqual(self.session.last_front_rule, "targeted_completion")
        self.assertTrue(plan.speech_text.startswith(
            f"3단계 완료로 기록했어요. {FIRST_HINT} 4단계로 이동했습니다."), plan.speech_text)
        plan = self.say("4단계 완료했어")
        self.assertTrue(plan.speech_text.startswith("4단계 완료로 기록했어요. 5단계로 이동했습니다."))

    def test_moving_on_is_a_front_rule_that_needs_no_model(self) -> None:
        self.open_with(index_of("3"))
        plan = self.say("다음 단계", front=True)
        self.assertIsNotNone(plan)
        self.assertEqual(self.label(), "4")
        self.assertEqual(self.session.last_front_rule, "forward_step")

    def test_a_step_moved_on_by_mistake_is_taken_back(self) -> None:
        self.open_with(index_of("3"))
        self.say("다음")
        plan = self.say("방금 완료 취소")
        self.assertEqual(plan.display_text, "3단계 완료를 취소하고 3단계로 돌아갈까요?")
        self.say("응")
        self.assertEqual(self.label(), "3")

    def test_words_that_ask_read_the_next_step_and_change_nothing(self) -> None:
        for said in ("다음 단계 알려줘", "다음 단계 뭐야", "다음엔 뭐 해", "다음 단계는?",
                     "다음 단계 말해줘", "다음 단계 읽어줘", "다음 단계 안내해줘",
                     "다음 단계 안내해 줄래?", "다음은?", "다음 뭐야", "다음 거 알려줘",
                     "다음에 뭐 하면 돼"):
            with self.subTest(said=said):
                self.open_with(index_of("3"))
                before = self.projection()
                plan = self.say(said)
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.projection(), before)
                self.assertIn("다음 단계 미리보기 · 4단계", plan.display_text)
                self.assertNotIn("완료하셨나요", plan.speech_text)
                self.assertIsNone(self.session.pending_completion_confirmation)

    def test_the_reason_and_the_next_step_asked_together_ask_nothing(self) -> None:
        self.open_with(index_of("3"))
        before = self.projection()
        plan = self.say("이 단계 왜 하는지 알려주고 다음 단계도 알려줘.")
        self.assertEqual(plan.intent_kind, "learning_and_next_preview")
        self.assertFalse(plan.state_changed)
        self.assertIn("다음 단계는 4단계", plan.speech_text)
        self.assertNotIn("완료하셨나요", plan.speech_text)
        self.assertIsNone(self.session.pending_completion_confirmation)
        self.say("네")
        self.assertEqual(self.projection(), before)

    def test_a_question_about_moving_on_is_still_asked_back(self) -> None:
        for said in ("다음 단계로 넘어가도 돼?", "다음 단계 진행할까?"):
            with self.subTest(said=said):
                self.open_with(index_of("3"))
                plan = self.say(said)
                self.assertFalse(plan.state_changed)
                self.assertEqual(plan.display_text, "3단계 완료하셨나요?")

    def test_a_completion_naming_no_step_is_still_asked_about(self) -> None:
        # Lane XO, decision 5 stays: "완료했어", "끝났어" name no step.
        for said in ("완료했어", "끝났어", "다 했어"):
            with self.subTest(said=said):
                self.open_with(index_of("3"))
                plan = self.say(said)
                self.assertFalse(plan.state_changed)
                self.assertEqual(plan.display_text, "3단계 완료하셨나요?")


class StillAskedTests(Session, unittest.TestCase):
    """The cases the decision keeps the question for, as it was asked before."""

    def test_the_confirm_way_asks(self) -> None:
        self.open_with(index_of("3"), confirm_mode="confirm")
        plan = self.say("다음")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.display_text, "3단계 완료하셨나요?")
        plan = self.say("네")
        self.assertTrue(plan.state_changed)
        self.assertEqual(self.label(), "4")
        # A yes to the question is not a word that moved on: no record line.
        self.assertFalse(plan.speech_text.startswith("3단계 완료로 기록했어요."))

    def test_an_open_question_asks(self) -> None:
        self.open_with(index_of("3"))
        self.complete("3", "4")
        self.say("이전 단계로 돌아가")
        plan = self.say("다음")
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.label(), "5")
        self.assertEqual(plan.display_text, "5단계 완료하셨나요?")

    def test_the_last_step_asks_because_there_is_no_next_step(self) -> None:
        last = len(self.open_with(index_of("3")).fixture.steps)
        self.session.current_index = last - 1
        plan = self.say("다음")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.display_text, f"{last}단계 완료하셨나요?")

    def test_an_unanswered_count_is_asked_again(self) -> None:
        self.open_with(index_of("18"))
        self.say("18단계 완료했어")
        self.assertEqual(self.label(), "19")
        plan = self.say("다음")
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.label(), "19")
        self.assertIn("몇 번(몇 개)", plan.speech_text)


class StillAskedWashTests(unittest.TestCase):
    """The wash: a 10-minute timer at step 2, a repeat-until stated at step 5."""

    def test_a_running_timer_asks_until_it_has_run_out(self) -> None:
        turns = wash_session()
        turns.say("프로토콜 시작해줘")
        turns.say("1단계 완료했어")
        turns.say("타이머 시작해줘")
        plan = turns.say("다음")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.display_text, "2단계 완료하셨나요?")
        turns.say("아니")
        # Once the timer has run out, the words move on.
        turns.session._timer_started_at -= 601
        plan = turns.say("다음")
        self.assertTrue(plan.state_changed)
        self.assertEqual(turns.label(), "3")
        self.assertTrue(plan.speech_text.startswith("2단계 완료로 기록했어요."))

    def test_a_timer_not_started_does_not_ask(self) -> None:
        turns = wash_session()
        turns.say("프로토콜 시작해줘")
        turns.say("1단계 완료했어")
        plan = turns.say("다음")
        self.assertTrue(plan.state_changed)
        self.assertEqual(turns.label(), "3")

    def test_a_repeat_until_step_asks_for_its_endpoint_as_before(self) -> None:
        turns = wash_session()
        turns.say("프로토콜 시작해줘")
        turns.session.current_index = 4
        plan = turns.say("다음")
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.display_text, "5단계 완료하셨나요?")
        plan = turns.say("네")
        self.assertFalse(plan.state_changed)
        self.assertIn("until the band is clear", plan.speech_text)

    def test_before_the_start_nothing_moves(self) -> None:
        turns = wash_session()
        plan = turns.say("다음")
        self.assertFalse(plan.state_changed)
        self.assertFalse(turns.session.active)

    def test_paused_nothing_moves(self) -> None:
        turns = wash_session()
        turns.say("프로토콜 시작해줘")
        turns.say("일시정지")
        plan = route_curated_runtime_turn(
            turns.session, "다음", turn_id=99, language="ko",
            configuration_id=1, generation=1,
        ).plan
        self.assertFalse(plan.state_changed)
        self.assertEqual(turns.label(), "1")


class ForwardRecordTests(RecordedSession, unittest.TestCase):

    def setUp(self) -> None:
        self.start_recording()

    def test_moving_on_is_recorded_as_a_completion(self) -> None:
        self.open_with(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("3")
        self.record("다음")
        self.record("다음 단계로")
        kinds = [(kind, label) for kind, label, _ in self.events()]
        self.assertEqual(kinds[-2:], [("step_completed", "3"), ("step_completed", "4")])


if __name__ == "__main__":
    unittest.main()
