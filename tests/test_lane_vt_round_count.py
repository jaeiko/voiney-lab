"""Lane VT, decision 4 (2026-10-09): each round of a source-fixed repeat is counted aloud.

Where the source states a fixed count (lane CB's fixed repetition --
headspace 16, "Repeat steps 12-15 twice more", three rounds), each time a
round ends the server says "<n>회 중 <k>회째 끝났어요." before what it said
already: the next-round question (lane CB, decision 3) or, at the last round,
"12~15단계 3회를 모두 마쳤어요." A count a person gave or left open, and a
repeat-until, are not counted: the source confirms no number for them. It is
said under "먼저 알려 주기: 모두" and "필요한 것만", not under "끄기" (the card
still shows the round). Nothing it says changes what the run does.
"""

from __future__ import annotations

import unittest

from tests.lane_cb_support import Turns, index_of
from tests.lane_n_support import notes_fixture


class SourceCountTests(Turns, unittest.TestCase):

    def at_16(self, mode: str | None = None):
        self.open(index_of("12"))
        if mode is not None:
            self.session.apply_experimenter_settings({"proactive_mode": mode})
        self.complete("12", "13", "14", "15")
        self.assertEqual(self.label(), "16")

    def test_each_round_end_is_counted(self) -> None:
        self.at_16()
        first = self.say("16단계 완료했어")
        self.assertEqual(
            first.display_text,
            "3회 중 1회째 끝났어요. 12~15단계를 한 번 더 해야 해요(2/3회차). 12단계로 돌아갈까요?")
        self.assertEqual(
            first.speech_text,
            "3회 중 1회째 끝났어요. 12~15단계를 한 번 더 해야 해요(3회 중 2회차). 12단계로 돌아갈까요?")
        self.assertFalse(first.state_changed)
        self.say("응")
        self.complete("12", "13", "14", "15")
        second = self.say("16단계 완료했어")
        self.assertTrue(second.display_text.startswith("3회 중 2회째 끝났어요. 12~15단계를 한 번 더"))
        self.say("네")
        self.complete("12", "13", "14", "15")
        done = self.say("16단계 완료했어")
        self.assertTrue(done.state_changed)
        self.assertEqual(self.label(), "17")
        self.assertIn("3회 중 3회째 끝났어요. 12~15단계 3회를 모두 마쳤어요. 17단계로 이동했습니다.",
                      done.speech_text)
        self.assertEqual(done.step_record["round"], 3)

    def test_moving_on_by_next_counts_the_same(self) -> None:
        self.at_16()
        plan = self.say("다음 단계")
        self.assertTrue(plan.display_text.startswith("3회 중 1회째 끝났어요. "))
        self.assertEqual(self.label(), "16")

    def test_the_setting_decides_whether_it_is_said(self) -> None:
        for mode, said in (("all", True), ("needed", True), ("off", False)):
            with self.subTest(mode=mode):
                self.at_16(mode)
                plan = self.say("16단계 완료했어")
                self.assertEqual(plan.speech_text.startswith("3회 중 1회째 끝났어요. "), said)
                self.assertTrue(plan.display_text.endswith(
                    "12~15단계를 한 번 더 해야 해요(2/3회차). 12단계로 돌아갈까요?"))


class NotCountedTests(Turns, unittest.TestCase):

    def test_a_count_a_person_gave_is_not_counted(self) -> None:
        self.open(index_of("18"))
        self.say("18단계 완료했어")
        self.say("세 번")
        self.complete("19", "20")
        plan = self.say("21단계 완료했어")
        self.assertNotIn("회째 끝났어요", plan.display_text)
        self.assertNotIn("회째 끝났어요", plan.speech_text)

    def test_a_count_left_open_is_not_counted(self) -> None:
        self.open(index_of("18"))
        self.say("18단계 완료했어")
        self.say("아직 몰라")
        self.complete("19", "20")
        plan = self.say("21단계 완료했어")
        self.assertNotIn("회째 끝났어요", plan.speech_text)

    def test_a_repeat_until_is_not_counted(self) -> None:
        self.open(3, fixture=notes_fixture(timer_seconds=0))
        self.say("4단계 완료했어")
        plan = self.say("5단계 완료했어")
        self.assertNotIn("회째 끝났어요", plan.speech_text)


if __name__ == "__main__":
    unittest.main()
