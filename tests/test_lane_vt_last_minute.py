"""Lane VT, decision 2 (2026-10-09): one minute before a timer ends.

A timer of five minutes or more is told its last minute once, one minute
before it ends: "1분 남았어요." ("최소 시간까지 1분 남았어요." for a minimum,
"최대 시간까지 1분 남았어요." for a maximum), then the next step's number and
the first sentence of its checked Korean, as at the end. The next step's
materials would be named only where the analysis holds them by step; it holds
the protocol's materials as one list, so they are not named. It is said only
under "먼저 알려 주기: 모두"; the screen shows it under every choice, with no
sound. Not told once the end has come, and nothing in the workflow changes.
"""

from __future__ import annotations

import asyncio
import dataclasses
import unittest

from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture
from tests.test_lane_vt_timer_end import STEP_3_KOREAN, T0, Notices, _Server, wash
from tests.test_lane_vx_bound_timers import _Catalog
from voiney_lab.server import _timer_notice_tick
from voiney_lab.vad import TurnState


class TheLastMinuteTests(Notices, unittest.TestCase):

    def test_it_is_told_once_a_minute_before_the_end(self) -> None:
        session = self.at_timer(1, fixture=wash(localizations=STEP_3_KOREAN))
        before = self.state_of(session)
        self.assertEqual(session.due_timer_notices(now=T0 + 539), ())
        (notice,) = session.due_timer_notices(now=T0 + 540)
        self.assertEqual(notice.kind, "timer_last_minute")
        self.assertEqual(notice.display_text, "1분 남았어요. 다음은 3단계, Solution A를 제거해 폐기합니다.")
        self.assertTrue(notice.spoken)
        self.assertFalse(notice.chime)
        self.assertEqual(session.due_timer_notices(now=T0 + 541), ())
        self.assertEqual(session.due_timer_notices(now=T0 + 599), ())
        (end,) = session.due_timer_notices(now=T0 + 600)
        self.assertEqual(end.kind, "timer_ended")
        self.assertEqual(session.due_timer_notices(now=T0 + 601), ())
        self.assertEqual(self.state_of(session), before)

    def test_only_a_timer_of_five_minutes_or_more(self) -> None:
        for seconds, warned in ((240, False), (299, False), (300, True), (3600, True)):
            with self.subTest(seconds=seconds):
                session = self.at_timer(1, fixture=wash(timer_manifest={"step-2": seconds}))
                kinds = [
                    notice.kind
                    for now in range(int(T0), int(T0) + seconds + 2)
                    for notice in session.due_timer_notices(now=float(now))
                ]
                self.assertEqual(
                    kinds, ["timer_last_minute", "timer_ended"] if warned else ["timer_ended"])

    def test_not_told_once_the_end_has_come(self) -> None:
        session = self.at_timer(1, fixture=wash())
        (notice,) = session.due_timer_notices(now=T0 + 610)
        self.assertEqual(notice.kind, "timer_ended")
        self.assertEqual(session.due_timer_notices(now=T0 + 611), ())

    def test_the_protocols_material_list_is_not_named_as_the_next_steps(self) -> None:
        # The wash's materials (Solution A, Solution B) are the protocol's
        # list; the analysis does not hold them by step.
        session = self.at_timer(1, fixture=wash())
        (notice,) = session.due_timer_notices(now=T0 + 540)
        self.assertEqual(notice.display_text, "1분 남았어요. 다음은 3단계예요. 화면에서 확인해 주세요.")
        self.assertNotIn("준비물", notice.display_text)

    def test_only_all_says_it_and_every_choice_shows_it(self) -> None:
        for mode, spoken in (("all", True), ("needed", False), ("off", False)):
            with self.subTest(mode=mode):
                session = self.at_timer(1, fixture=wash(), mode=mode)
                (notice,) = session.due_timer_notices(now=T0 + 540)
                self.assertEqual((notice.kind, notice.spoken, notice.chime),
                                 ("timer_last_minute", spoken, False))


class TheWordsByKindTests(_Catalog, Notices):

    def test_a_minimum_and_a_maximum_say_which_time(self) -> None:
        for index, seconds, words in (
            (0, 7200, "최소 시간까지 1분 남았어요. 다음은 2단계예요. 화면에서 확인해 주세요."),
            (1, 57600, "1분 남았어요. 다음은 3단계예요. 화면에서 확인해 주세요."),
            (2, 7200, "최대 시간까지 1분 남았어요. 다음은 4단계예요. 화면에서 확인해 주세요."),
            (4, 600, "1분 남았어요. 다음은 6단계예요. 화면에서 확인해 주세요."),
        ):
            with self.subTest(step=index + 1):
                session = self.at_timer(index, fixture=self.fixture)
                (notice,) = session.due_timer_notices(now=T0 + seconds - 60)
                self.assertEqual(notice.display_text, words)


@unittest.skipUnless(SOURCE_PDF.exists(), "the licensed in-gel PDF is not present")
class InGelWalkTests(Notices, unittest.TestCase):
    """Every in-gel timer, second by second: a last minute and an end, each once."""

    def test_each_timer_twice_and_no_more(self) -> None:
        fixture = in_gel_fixture()
        seen = []
        for index, step in enumerate(fixture.steps):
            seconds = fixture.timer_manifest.get(step.step_id)
            if not seconds:
                continue
            session = self.at_timer(index, fixture=fixture)
            for now in range(int(T0), int(T0) + seconds + 120, 1 if seconds <= 3600 else 30):
                for notice in session.due_timer_notices(now=float(now)):
                    seen.append((step.source_label, notice.kind, int(notice.due_at - T0)))
        expected = []
        for step in fixture.steps:
            seconds = fixture.timer_manifest.get(step.step_id)
            if seconds:
                expected += [(step.source_label, "timer_last_minute", seconds - 60),
                             (step.source_label, "timer_ended", seconds)]
        self.assertEqual(seen, expected)
        self.assertEqual(len(seen), 20)


class DeliveryTests(_Server):

    def test_said_when_quiet_with_no_sound(self) -> None:
        notices = asyncio.run(_timer_notice_tick(self.listener, self.sender, now=T0 + 540))
        self.assertEqual([n.kind for n in notices], ["timer_last_minute"])
        shown = self.sent[0]
        self.assertEqual((shown["type"], shown["notice_kind"], shown["chime"]),
                         ("protocol.timer.notice", "timer_last_minute", False))
        self.assertIn("protocol.notice.speech", self.types())
        self.assertEqual(self.synthesized, [notices[0].speech_text])

    def test_a_late_last_minute_is_left_on_the_screen(self) -> None:
        # Busy for longer than the last minute's wait: it is not said late.
        self.listener.active_turn_id = 7
        self.listener.detector.state = TurnState.USER_SPEAKING
        (notice,) = self.curated.due_timer_notices(now=T0 + 540)
        self.assertFalse(self.deliver(notice, wait_seconds=30.0))
        self.assertEqual(self.types(), ["protocol.timer.notice"])
        self.assertEqual(self.synthesized, [])


if __name__ == "__main__":
    unittest.main()
