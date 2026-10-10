"""Lane VF, decision 3 (2026-10-10): a timer notice said on time.

In the voice test of 2026-10-10 the one-minute notice began about ten
seconds after it fell due: most of that was the experimenter speaking, which
stays as it is, but a second of it was the sentence being synthesized only
once the session was quiet. Now a spoken notice's sentence is made about
fifteen seconds before it falls due and the watcher wakes for the due moment
itself, so when no one is speaking the screen shows the notice within a
second of due and the voice begins within two. The log carries, per notice,
its due moment and how late it was shown and said, with how long it waited
for quiet and how long its audio took.

The clock is a fake the test moves; the watcher is the real coroutine; the
TTS is a fake with a chosen latency.
"""

from __future__ import annotations

import unittest

from tests.test_lane_vf_notice_delivery import T0, _Connection
from tests.test_lane_vt_timer_end import STEP_3_KOREAN, wash
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession


class OnTimeTests(_Connection):

    def test_the_sentence_is_made_before_it_is_due_and_said_the_moment_it_is(self) -> None:
        # A TTS this slow would miss "two seconds after due" if it ran at due.
        self.synth_seconds = 3.0
        with self.assertLogs("voiney_lab", level="INFO") as logged:
            self.watch(T0 + 700)
        # The fake TTS notes the moment it returns: three seconds after it
        # was asked, within a second of fifteen seconds before each due moment.
        made = [t for t, _ in self.synthesized]
        self.assertEqual(len(made), 2, self.synthesized)
        self.assertLessEqual(T0 + 528, made[0])
        self.assertLess(made[0], T0 + 529.1)
        self.assertLessEqual(T0 + 588, made[1])
        self.assertLess(made[1], T0 + 589.1)
        # Shown at the due moment itself, said at once.
        (minute_shown,) = self.shown("timer_last_minute")
        (end_shown,) = self.shown("timer_ended")
        self.assertLess(abs(minute_shown - (T0 + 540)), 0.1)
        self.assertLess(abs(end_shown - (T0 + 600)), 0.1)
        (minute_said,) = self.said("timer_last_minute")
        (end_said,) = self.said("timer_ended")
        self.assertLess(minute_said - (T0 + 540), 0.5)
        self.assertLess(end_said - (T0 + 600), 0.5)
        said_lines = [line for line in logged.output if "timer notice said" in line]
        self.assertEqual(len(said_lines), 2, said_lines)
        for line in said_lines:
            self.assertIn("prepared=True", line)
            self.assertIn("synth_ms=0", line)
            self.assertIn("waited_ms=0", line)
            self.assertIn("due=1970-01-12T13:5", line)
        self.assertEqual(
            len([line for line in logged.output if "timer notice prepared" in line and "synth_ms=3000" in line]),
            2, logged.output)

    def test_while_the_experimenter_speaks_it_still_waits_and_is_said_after(self) -> None:
        self.synth_seconds = 3.0
        self.speaking = [(T0 + 599, T0 + 620)]
        with self.assertLogs("voiney_lab", level="INFO") as logged:
            self.watch(T0 + 700)
        (end_said,) = self.said("timer_ended")
        self.assertLessEqual(T0 + 620, end_said)
        self.assertLess(end_said, T0 + 621)
        # The audio was ready: nothing was synthesized once it was quiet.
        self.assertTrue(all(t < T0 + 600 for t, _ in self.synthesized), self.synthesized)
        (line,) = [line for line in logged.output if "timer notice said" in line and "kind=timer_ended" in line]
        self.assertIn("prepared=True", line)
        self.assertIn("synth_ms=0", line)
        self.assertRegex(line, r"waited_ms=20[0-9]{3}")
        self.assertRegex(line, r"delay_ms=20[0-9]{3}")

    def test_a_changed_sentence_is_made_again_when_it_is_said(self) -> None:
        # A stored machine translation: prepared with "자동 번역입니다." first
        # while nothing announced it; an answer announces it before the
        # notice is due, so the sentence to be said differs and is made again.
        curated = CuratedProtocolSession(wash(machine_localizations=STEP_3_KOREAN))
        curated.activate_configured()
        curated.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=41, generation=1)
        curated.current_index = 1
        curated.apply_experimenter_settings({"proactive_mode": "needed"})
        curated.start_timer(now=T0)
        self.listener.curated_protocol_session = self.curated = curated
        parent = self.clock.on_advance

        def an_answer_announces_at_590(now: float) -> None:
            parent(now)
            if now >= T0 + 590:
                self.listener.auto_translation_announced = True

        self.clock.on_advance = an_answer_announces_at_590
        with self.assertLogs("voiney_lab", level="INFO") as logged:
            self.watch(T0 + 610)
        texts = [text for _, text in self.synthesized]
        self.assertEqual(len(texts), 2, texts)
        self.assertTrue(texts[0].startswith("자동 번역입니다. 10분 타이머가 끝났어요. 다음은 3단계, "), texts)
        self.assertTrue(texts[1].startswith("10분 타이머가 끝났어요. 다음은 3단계, "), texts)
        self.assertEqual(self.synthesized[1][0], T0 + 600)
        (line,) = [line for line in logged.output if "timer notice said" in line]
        self.assertIn("prepared=False", line)
        self.assertEqual(self.listener.timer_notice_audio, {})

    def test_the_watcher_wakes_for_an_off_second_due_moment(self) -> None:
        self.curated.start_timer(now=T0 + 0.37)
        self.watch(T0 + 700)
        (minute_shown,) = self.shown("timer_last_minute")
        (end_shown,) = self.shown("timer_ended")
        self.assertLess(abs(minute_shown - (T0 + 540.37)), 0.05)
        self.assertLess(abs(end_shown - (T0 + 600.37)), 0.05)

    def test_prepared_audio_is_bounded_and_forgotten_with_the_session(self) -> None:
        self.watch(T0 + 586.5)
        self.assertEqual(len(self.listener.timer_notice_audio), 1)
        for index in range(server_module.TIMER_NOTICE_PREPARED_MAX + 2):
            self.listener.timer_notice_audio[f"other-{index}"] = ("x", [b"\0" * 640])
        (notice,) = self.curated.upcoming_timer_notices(now=T0 + 586.5, within=15.0)
        self.listener.timer_notice_audio.pop(notice.notice_id, None)
        self.run_clock(
            lambda: server_module._prepare_timer_notice_audio(self.listener, self.curated, notice), T0 + 587)
        self.assertLessEqual(len(self.listener.timer_notice_audio), server_module.TIMER_NOTICE_PREPARED_MAX)
        self.assertIn(notice.notice_id, self.listener.timer_notice_audio)
        self.listener.start()
        self.assertEqual(self.listener.timer_notice_audio, {})
        self.assertEqual(self.listener.timer_notice_pending, {})


if __name__ == "__main__":
    unittest.main()
