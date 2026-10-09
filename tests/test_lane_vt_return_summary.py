"""Lane VT, decision 5 (2026-10-09): coming back after a long silence.

When the experimenter has said nothing for more than ten minutes and then
speaks, an answer that is not a command is led by one line on where the run
stands: "지금 3단계예요." -- "지금 2단계예요(타이머 5분 남았어요)." while the
step timer runs. A command (a move, a timer, a record, a setting, a start or
an end) is carried out alone, at once. Only under "먼저 알려 주기: 모두". The
line comes from the server's own state and changes nothing. The clock is
passed in; no test waits.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.lane_cb_support import Turns
from tests.lane_n_support import VoiceNotesHarness, notes_fixture, shown

T0 = 1_000_000.0


class SessionTests(Turns, unittest.TestCase):

    def opened(self, index: int = 2, *, mode: str | None = None):
        session = self.open(index, fixture=notes_fixture())
        if mode is not None:
            session.apply_experimenter_settings({"proactive_mode": mode})
        session._experiment_started_at = T0
        return session

    def heard(self, said: str, at: float):
        silence = self.session.note_heard(now=at)
        plan = self.say(said)
        return self.session.with_return_summary(plan, silence=silence, now=at)

    def test_a_question_after_ten_minutes_is_led_by_the_step(self) -> None:
        self.opened()
        self.heard("이 단계 왜 해?", T0 + 30)
        before = self.projection()
        plan = self.heard("얼마나 넣어?", T0 + 30 + 601)
        self.assertTrue(plan.display_text.startswith("지금 3단계예요. "), plan.display_text)
        self.assertTrue(plan.speech_text.startswith("지금 3단계예요. "))
        self.assertEqual(self.projection(), before)
        # Not again for the next words.
        plan = self.heard("얼마나 넣어?", T0 + 30 + 620)
        self.assertFalse(plan.display_text.startswith("지금 3단계예요."))

    def test_ten_minutes_exactly_is_not_long(self) -> None:
        self.opened()
        self.heard("이 단계 왜 해?", T0)
        plan = self.heard("얼마나 넣어?", T0 + 600)
        self.assertFalse(plan.display_text.startswith("지금"))

    def test_a_running_timer_says_its_time_left(self) -> None:
        session = self.opened(1)
        self.heard("타이머 시작해줘", T0)
        session._timer_started_at = T0
        plan = self.heard("이 단계 왜 해?", T0 + 900)
        self.assertEqual(session.timer_status(now=T0 + 900)["state"], "expired")
        self.assertTrue(plan.display_text.startswith("지금 2단계예요. "))
        session.start_timer(now=T0 + 1000)
        session._timer_duration_seconds = 3600
        plan = self.heard("이 단계 왜 해?", T0 + 1000 + 700)
        self.assertTrue(plan.display_text.startswith("지금 2단계예요(타이머 48분 남았어요). "),
                        plan.display_text)

    def test_the_silence_runs_from_the_start_when_nothing_was_said(self) -> None:
        self.opened()
        plan = self.heard("얼마나 넣어?", T0 + 700)
        self.assertTrue(plan.display_text.startswith("지금 3단계예요. "))

    def test_a_command_is_carried_out_alone(self) -> None:
        for said in ("다음", "3단계 완료했어", "타이머 시작해줘", "확인 질문 켜 줘",
                     "실험노트에 적어 줘, 0.5 mL", "일시정지", "종료"):
            with self.subTest(said=said):
                self.opened()
                self.heard("이 단계 왜 해?", T0)
                plan = self.heard(said, T0 + 700)
                self.assertFalse(plan.display_text.startswith("지금 3단계예요"), plan.display_text)
                self.assertFalse(plan.speech_text.startswith("지금 3단계예요"))

    def test_only_under_all(self) -> None:
        for mode, led in (("all", True), ("needed", False), ("off", False)):
            with self.subTest(mode=mode):
                self.opened(mode=mode)
                self.heard("이 단계 왜 해?", T0)
                plan = self.heard("얼마나 넣어?", T0 + 700)
                self.assertEqual(plan.display_text.startswith("지금 3단계예요. "), led)

    def test_nothing_before_the_start(self) -> None:
        from voiney_lab.curated_protocol import CuratedProtocolSession

        session = CuratedProtocolSession(notes_fixture())
        session.activate_configured()
        self.assertIsNone(session.note_heard(now=T0))
        self.assertIsNone(session.note_heard(now=T0 + 900))


class VoiceSessionTests(VoiceNotesHarness, unittest.TestCase):
    """The server reads the silence from its clock and leads the answer it says."""

    def test_the_server_leads_the_spoken_answer(self) -> None:
        now = [T0]

        async def scenario(socket, listener, say):
            for turn_id, (at, text) in enumerate((
                (T0, "프로토콜 시작해줘"), (T0 + 20, "1단계 완료했어"),
                (T0 + 20 + 700, "얼마나 넣어?"), (T0 + 20 + 1400, "2단계 완료했어"),
            ), 1):
                now[0] = at
                await say(turn_id, text)

        with patch("voiney_lab.server._wall_clock", side_effect=lambda: now[0]):
            socket = self.voice(scenario)
        replies = shown(socket)
        self.assertTrue(replies[3].startswith("지금 2단계예요. "), replies[3])
        self.assertTrue(" ".join(self.spoken[3]).startswith("지금 2단계예요. "), self.spoken[3])
        # The command after a second long silence is carried out alone.
        self.assertFalse(replies[4].startswith("지금"), replies[4])
        self.assertIn("3단계로 이동했습니다", " ".join(self.spoken[4]))


if __name__ == "__main__":
    unittest.main()
