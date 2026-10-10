"""Lane VF, decision 5 (2026-10-10): the time left, asked with no target named.

In the voice test of 2026-10-10 "지금 몇 분 남았어?" was answered "PDF에서 확인할
수 없어요." -- the front rule read only a handful of exact wordings, so the
words went to the model, which looks in the protocol and finds no such
number -- while "지금 타이머 몇 분 남았어?" was answered from the timer. The
time left is the server's to say (decision of 2026-10-02: the front rules
take system information). Now any ordinary way of asking it with no target
named -- "몇 분 남았어?", "얼마나 남았어?", "언제 끝나?", "몇 분 더 기다려야
해?" and their variants -- is read by the rule and answered from the timer as
it stands: running, "3단계 타이머 13분 57초 남았어요."; run out, "3단계 15분
타이머는 2분 전에 끝났어요."; none, "지금 도는 타이머는 없어요." and the step's
verified source time where it has one. Nothing changes state. Tested on the
synthetic wash protocol, the bound-timer catalog and the in-gel PDF.
"""

from __future__ import annotations

import unittest

from tests.lane_cb_support import Turns
from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture
from tests.test_lane_vt_timer_end import wash
from tests.test_lane_vx_bound_timers import _Catalog
from voiney_lab.curated_protocol import CuratedProtocolAction

#: The ordinary ways of asking, with nothing named.
TIME_LEFT = (
    "몇 분 남았어?", "지금 몇 분 남았어?", "아직 몇 분 남았어", "몇 분이나 남았지", "몇 분 더 남았어",
    "얼마나 남았어?", "얼마 남았어?", "얼마나 더 남았어요", "지금 얼마나 남았어", "시간 얼마나 남았지",
    "남은 시간 얼마야", "남은 시간 알려 줘", "언제 끝나?", "타이머 언제 끝나", "이거 언제 끝나요", "언제쯤 끝나",
    "몇 분 더 기다려야 해?", "얼마나 기다려야 돼", "끝나려면 얼마나 남았어", "타이머 몇 분 남았노",
    "Time 얼마나 남았어?", "how much longer", "when does it end",
)
#: Not this: a named thing, the steps, an observation, the endpoint, a pause.
NOT_TIME_LEFT = (
    "몇 단계 남았어?", "아직 색이 남았어", "언제 끝난 걸로 봐?", "남은 단계 요약해줘", "용액 A 얼마나 넣어?",
    "BSA 몇 분 남았어?", "결과 기다려 봐야 해?", "쫌만 기다려 봐", "몇 분 동안 해?", "몇 분 지났어?", "언제 끝내?",
)


class _Wash(Turns, unittest.TestCase):
    """The synthetic wash protocol (lane N): a 10-minute timer at step 2."""

    def projection(self):
        session = self.session
        return (session.active, session.current_index, session.workflow_status,
                session.state()["revision"], session._timer_started_at)


class TheRuleTests(_Wash):

    def test_every_ordinary_wording_is_the_front_timer_read(self) -> None:
        for said in TIME_LEFT:
            with self.subTest(said=said):
                session = self.open(1, fixture=wash())
                session.start_timer()
                before = self.projection()
                plan = self.say(said)
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_STATUS, plan.speech_text)
                self.assertEqual(session.last_front_rule, "timer_remaining")
                self.assertFalse(plan.state_changed)
                self.assertNotIn("PDF에서 확인할 수 없어요", plan.speech_text)
                self.assertEqual(self.projection(), before)

    def test_what_names_a_thing_or_asks_something_else_is_not_this(self) -> None:
        for said in NOT_TIME_LEFT:
            with self.subTest(said=said):
                session = self.open(1, fixture=wash())
                session.start_timer()
                plan = self.say(said)
                self.assertNotEqual(session.last_front_rule, "timer_remaining", plan.speech_text)
                self.assertFalse(plan.speech_text.startswith(("2단계 타이머 ", "지금 도는 타이머")), plan.speech_text)


class TheAnswerTests(_Wash):

    def test_a_running_timer_says_its_time_left(self) -> None:
        session = self.open(1, fixture=wash())
        session.start_timer()
        session._timer_started_at -= 123
        for said in ("지금 몇 분 남았어?", "언제 끝나?", "얼마나 더 기다려야 해?"):
            with self.subTest(said=said):
                plan = self.say(said)
                self.assertEqual(plan.speech_text, "2단계 타이머 7분 57초 남았어요.")
                self.assertEqual(plan.display_text, plan.speech_text)

    def test_a_timer_that_ran_out_says_how_long_ago(self) -> None:
        session = self.open(1, fixture=wash())
        session.start_timer()
        session._timer_started_at -= 600 + 134
        plan = self.say("지금 몇 분 남았어?")
        self.assertEqual(plan.speech_text, "2단계 10분 타이머는 2분 전에 끝났어요.")
        session._timer_started_at += 100
        plan = self.say("얼마나 남았어?")
        self.assertEqual(plan.speech_text, "2단계 10분 타이머는 34초 전에 끝났어요.")
        session._timer_started_at -= 3600 + 90
        plan = self.say("언제 끝나?")
        self.assertEqual(plan.speech_text, "2단계 10분 타이머는 1시간 2분 전에 끝났어요.")
        self.assertFalse(plan.state_changed)

    def test_no_timer_running_says_so_and_the_verified_source_time(self) -> None:
        session = self.open(1, fixture=wash())
        plan = self.say("몇 분 남았어?")
        self.assertTrue(plan.speech_text.startswith("지금 도는 타이머는 없어요. 2단계 타이머는 10분이에요."), plan.speech_text)
        self.assertIn("타이머를 시작하려면", plan.speech_text)
        self.assertEqual(session.timer_status()["state"], "not_started")
        # A step whose source states no time: the first sentence alone.
        session = self.open(0, fixture=wash())
        plan = self.say("몇 분 남았어?")
        self.assertEqual(plan.speech_text, "지금 도는 타이머는 없어요.")
        self.assertFalse(plan.state_changed)

    def test_in_english(self) -> None:
        session = self.open(1, fixture=wash())
        session.start_timer()
        session._timer_started_at -= 60
        plan = self.session.front_plan("how much longer", turn_id=5, language="en", configuration_id=1, generation=1)
        self.assertEqual(plan.speech_text, "Step 2 timer: 9 min left.")


class TheBoundTimerTests(_Catalog):
    """Lane VX's catalog: a minimum, an approximate and a maximum timer."""

    def test_a_minimum_and_a_maximum_that_ran_out_keep_their_words(self) -> None:
        session = self.turns.open(0, fixture=self.fixture)
        self.turns.say("타이머 시작해줘")
        session._timer_started_at -= 7200 + 61
        plan = self.turns.say("지금 몇 분 남았어?")
        self.assertEqual(plan.speech_text, "1단계 최소 시간 2시간은 1분 전에 지났어요.")
        session = self.turns.open(2, fixture=self.fixture)
        self.turns.say("타이머 시작해줘")
        session._timer_started_at -= 7200 + 61
        plan = self.turns.say("얼마나 남았어?")
        self.assertEqual(plan.speech_text, "3단계 최대 시간 2시간은 1분 전에 됐어요.")
        session = self.turns.open(1, fixture=self.fixture)
        self.turns.say("타이머 시작해줘")
        session._timer_started_at -= 57600 + 125
        plan = self.turns.say("언제 끝나?")
        self.assertEqual(plan.speech_text, "2단계 약 16시간 타이머는 2분 전에 끝났어요.")
        self.assertFalse(plan.state_changed)


@unittest.skipUnless(SOURCE_PDF.exists(), "the licensed in-gel PDF is not present")
class InGelTests(_Wash):

    def test_step_3_as_in_the_voice_test(self) -> None:
        session = self.open(2, fixture=in_gel_fixture())
        plan = self.say("지금 몇 분 남았어?")
        self.assertTrue(plan.speech_text.startswith("지금 도는 타이머는 없어요. 3단계 타이머는 15분이에요."), plan.speech_text)
        session.start_timer()
        session._timer_started_at -= 63
        for said in ("지금 몇 분 남았어?", "지금 타이머 몇 분 남았어?", "몇 분 더 기다려야 해?"):
            with self.subTest(said=said):
                plan = self.say(said)
                self.assertEqual(plan.speech_text, "3단계 타이머 13분 57초 남았어요.")
        session._timer_started_at -= 900
        before = self.projection()
        plan = self.say("얼마나 남았어?")
        self.assertEqual(plan.speech_text, "3단계 15분 타이머는 1분 전에 끝났어요.")
        self.assertEqual(self.projection(), before)


if __name__ == "__main__":
    unittest.main()
