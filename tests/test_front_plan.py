"""front_plan(): the front rules, read without waiting on a model (lane R).

front_plan() is plan() stopped once the rules have read the turn. For a turn
a front rule owns (FRONT_RULES: F2-F9 and the decisions of 2026-10-02 -- D2 a
completion naming the current step, D3 "그거", D5 "종료", D9 an endpoint
stated at a repeat-until step) it returns the plan plan() makes. For any other
turn it returns None and the session is exactly as it was, so plan() -- or a
validated tool proposal -- can take the turn as if nothing had read it.

The pairs below check both halves on twin sessions: one that only ever calls
plan(), and one that asks front_plan() first and calls plan() only when the
front hands the turn on. Their plans and their whole session state must agree
turn by turn.
"""

from __future__ import annotations

import copy
import re
import unittest
from unittest import mock

from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture, miniprep_fixture
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    CuratedProtocolAction,
    CuratedProtocolSession,
)

#: Attributes that describe the last call rather than the session.
_TELEMETRY = frozenset({"_last_front_rule", "_vocabulary_cache", "fixture", "safety_pack"})


_WALL_CLOCK = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _without_wall_clock(value: object) -> object:
    """Records stamp the wall clock; the twins differ only by when they ran."""

    if isinstance(value, str) and _WALL_CLOCK.match(value):
        return "<time>"
    if isinstance(value, dict):
        return {key: _without_wall_clock(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_without_wall_clock(item) for item in value)
    return value


def _state(session: CuratedProtocolSession) -> dict[str, object]:
    return _without_wall_clock(copy.deepcopy({
        key: value for key, value in vars(session).items() if key not in _TELEMETRY
    }))


def _view(plan) -> tuple[object, ...] | None:
    if plan is None:
        return None
    return (
        plan.action, plan.intent_kind, plan.state_changed, plan.step_label,
        plan.speech_text, plan.display_text,
    )


class _Twins:
    """Two sessions on one fixture: plan() only, and front_plan() first."""

    fixture = None

    def setUp(self) -> None:
        # Both twins read one clock, so their experiment and pause times agree.
        clock = mock.patch("time.time", return_value=1_790_000_000.0)
        clock.start()
        self.addCleanup(clock.stop)

    def _twins(self, step_index: int | None) -> tuple[CuratedProtocolSession, CuratedProtocolSession]:
        pair = (CuratedProtocolSession(self.fixture), CuratedProtocolSession(self.fixture))
        for session in pair:
            session.activate_configured()
            if step_index is not None:
                session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
                session.current_index = step_index
        return pair

    def _say(self, rules_only, front_first, text, turn_id, *, quality=None):
        expected = rules_only.plan(
            text, turn_id=turn_id, language="ko", transcript_quality=quality,
            configuration_id=1, generation=1,
        )
        before = _state(front_first)
        front = front_first.front_plan(
            text, turn_id=turn_id, language="ko", transcript_quality=quality,
            configuration_id=1, generation=1,
        )
        rule = front_first.last_front_rule
        if front is None:
            self.assertEqual(_state(front_first), before, f"front_plan touched the session: {text!r}")
            self.assertIsNone(rule)
            actual = front_first.plan(
                text, turn_id=turn_id, language="ko", transcript_quality=quality,
                configuration_id=1, generation=1,
            )
        else:
            self.assertIn(rule, FRONT_RULES)
            actual = front
        self.assertEqual(_view(actual), _view(expected), text)
        self.assertEqual(_state(front_first), _state(rules_only), text)
        return front, rule

    def assert_front(self, script, expected_rule, *, step_index, quality=None):
        rules_only, front_first = self._twins(step_index)
        *prior, last = script
        for turn_id, text in enumerate(prior, start=2):
            self._say(rules_only, front_first, text, turn_id)
        front, rule = self._say(
            rules_only, front_first, last, len(prior) + 2, quality=quality
        )
        self.assertIsNotNone(front, f"no front rule took {last!r}")
        self.assertEqual(rule, expected_rule, last)
        return front, front_first

    def assert_handed_on(self, script, *, step_index):
        rules_only, front_first = self._twins(step_index)
        *prior, last = script
        for turn_id, text in enumerate(prior, start=2):
            self._say(rules_only, front_first, text, turn_id)
        front, rule = self._say(rules_only, front_first, last, len(prior) + 2)
        self.assertIsNone(front, f"{last!r} was taken by front rule {rule}")
        return front_first


class MiniprepFrontRuleTests(_Twins, unittest.TestCase):
    """Miniprep step 4 has no observed endpoint; no licensed PDF needed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = miniprep_fixture()

    def test_pause_words_are_front_f3(self) -> None:
        for said in ("잠깐", "잠깐만", "멈춰", "중지", "실험 중지", "스톱", "pause 해줘",
                     "그만할래", "여기서 끝낼게"):
            with self.subTest(said=said):
                front, session = self.assert_front((said,), "pause", step_index=3)
                self.assertIs(front.action, CuratedProtocolAction.PAUSE)
                self.assertEqual(session.workflow_status, "paused")

    def test_end_commands_are_front_f3_and_ask_once(self) -> None:
        for said in ("실험 종료", "종료해줘", "end session"):
            with self.subTest(said=said):
                front, session = self.assert_front((said,), "end_command", step_index=3)
                self.assertFalse(front.state_changed)
                self.assertTrue(session.active)

    def test_yes_and_no_to_an_open_question_are_front_f4(self) -> None:
        for question, reply in (
            ("다음 단계", "네"), ("다음 단계", "응응"), ("다음 단계", "아니"),
            ("실험 종료", "네"), ("실험 종료", "아니"), ("다 했어", "예"),
        ):
            with self.subTest(question=question, reply=reply):
                self.assert_front((question, reply), "yes_no_open_question", step_index=3)

    def test_timer_reference_repeat_and_cancel_are_front_f6_to_f9(self) -> None:
        for said, rule in (
            ("타이머 얼마나 남았어", "timer_remaining"),
            ("얼마나 남았어?", "timer_remaining"),
            ("그거 뭐야", "coreference_clarify"),
            ("그거 얼마나 넣어?", "coreference_clarify"),
            ("다시 말해줘", "repeat_last_reply"),
            ("소리가 안 나", "repeat_last_reply"),
            ("검색 취소해", "cancel_background_job"),
        ):
            with self.subTest(said=said):
                self.assert_front((said,), rule, step_index=3)

    def test_an_unreliable_transcript_is_front_f2(self) -> None:
        self.assert_front(("다음 단계로 넘어가",), "stt_unreliable", step_index=3,
                          quality="low_confidence")
        self.assert_front(("ありがとうございます",), "stt_unreliable", step_index=3)

    def test_a_completion_naming_the_current_step_is_front_d2(self) -> None:
        for said in ("현재 단계 완료했어", "4단계 완료", "이번 단계 끝났어"):
            with self.subTest(said=said):
                front, session = self.assert_front((said,), "targeted_completion", step_index=3)
                self.assertTrue(front.state_changed)
                self.assertEqual(session.current_index, 4)

    def test_everything_else_is_handed_on_untouched(self) -> None:
        for said in (
            "다 했어", "완료했어", "끝났어", "다음 단계", "다음 단계로 넘어가자", "넘어가",
            "5단계 완료", "자 이제 다음 거 하자", "지금 몇 단계야", "이 단계 왜 해?",
            "자세히 알려줘", "타이머 시작해줘", "버퍼 1은 뭐야?", "어디까지 했지?",
            "오늘 점심 뭐 먹지", "메모해줘 튜브 라벨 A-17",
        ):
            with self.subTest(said=said):
                self.assert_handed_on((said,), step_index=3)

    def test_before_the_start_only_front_words_are_taken(self) -> None:
        self.assert_handed_on(("프로토콜 시작해줘",), step_index=None)
        self.assert_handed_on(("자 이제 1단계부터 해볼까",), step_index=None)
        self.assert_front(("잠깐",), "pause", step_index=None)

    def test_an_unanswered_question_is_put_back(self) -> None:
        # "버퍼 1은 뭐야?" does not answer "4단계 완료하셨나요?"; plan() lets the
        # question go, but front_plan() hands the turn on with it still open.
        session = self.assert_handed_on(("다음 단계", "버퍼 1은 뭐야?"), step_index=3)
        self.assertIsNone(session.pending_completion_confirmation)

    def test_a_question_left_open_survives_a_front_hand_on(self) -> None:
        rules_only, front_first = self._twins(3)
        self._say(rules_only, front_first, "다음 단계", 2)
        self.assertIsNotNone(front_first.pending_completion_confirmation)
        self.assertIsNone(front_first.front_plan(
            "버퍼 1은 뭐야?", turn_id=3, language="ko", configuration_id=1, generation=1,
        ))
        self.assertIsNotNone(front_first.pending_completion_confirmation)

    def test_while_paused_questions_and_resume_are_handed_on(self) -> None:
        self.assert_handed_on(("잠깐", "버퍼 1은 뭐야?"), step_index=3)
        self.assert_handed_on(("잠깐", "재개"), step_index=3)
        self.assert_front(("잠깐", "잠깐"), "pause", step_index=3)
        self.assert_front(("잠깐", "실험 종료"), "end_command", step_index=3)

    def test_a_planned_turn_is_answered_from_the_replay(self) -> None:
        session = CuratedProtocolSession(self.fixture)
        session.activate_configured()
        session.plan("시작", turn_id=1, language="ko")
        first = session.plan("버퍼 1은 뭐야?", turn_id=2, language="ko")
        self.assertIs(session.front_plan("잠깐", turn_id=2, language="ko"), first)

    def test_every_rule_is_described(self) -> None:
        self.assertEqual(set(FRONT_RULES), {
            "stt_unreliable", "pause", "end_command", "yes_no_open_question",
            "observation_reply", "timer_remaining", "coreference_clarify",
            "repeat_last_reply", "cancel_background_job", "targeted_completion",
        })


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelObservationFrontTests(_Twins, unittest.TestCase):
    """In-gel step 7 repeats until the gel is destained (F5 and D9)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()
        cls.step_7 = next(i for i, s in enumerate(cls.fixture.steps) if s.source_label == "7")

    def test_any_reply_to_the_endpoint_question_is_front_f5(self) -> None:
        for reply in (
            "완전히 탈색됐어", "탈색이 안 됐어", "네", "아니", "결과 나왔어", "색이 좀 변했어",
            "탈색됐는데 튜브가 터졌어", "다음 단계", "그거 뭐야", "버퍼는 뭐야?",
            "탈색 아직 덜 됐는데 그냥 다음 단계 가자",
        ):
            with self.subTest(reply=reply):
                self.assert_front(("7단계 완료했어", reply), "observation_reply",
                                  step_index=self.step_7)

    def test_the_question_itself_is_front_d2(self) -> None:
        front, session = self.assert_front(("7단계 완료했어",), "targeted_completion",
                                           step_index=self.step_7)
        self.assertFalse(front.state_changed)
        self.assertIsNotNone(session.pending_observation_confirmation)

    def test_an_endpoint_stated_with_no_question_open_is_front_d9(self) -> None:
        for said in ("탈색이 됐어", "탈색 완료", "젤이 투명해", "아직 색이 남아 있어",
                     "완전히 탈색 안 됐어", "완전히 탈색됐는데 튜브가 터졌어",
                     "어, 젤이 완전히 이제 젤 밴드가 투명해졌어. 탈색이 됐어. 이제 이번 단계도 완료했어."):
            with self.subTest(said=said):
                self.assert_front((said,), "observation_reply", step_index=self.step_7)

    def test_no_endpoint_and_no_question_is_handed_on(self) -> None:
        # The endpoint phrase families read none of these as a verdict, so the
        # rules do not judge them and the turn is handed on: "탈색이 안 됐어"
        # is a question about the step to them, and "탈색이 됐는데 흘렸어" a
        # problem report.
        for said in ("어 지금", "완료했어", "다 했어", "탈색 아직 덜 됐는데 그냥 다음 단계 가자",
                     "이 단계 왜 해?", "탈색이 안 됐어", "탈색이 됐는데 흘렸어"):
            with self.subTest(said=said):
                self.assert_handed_on((said,), step_index=self.step_7)


if __name__ == "__main__":
    unittest.main()
