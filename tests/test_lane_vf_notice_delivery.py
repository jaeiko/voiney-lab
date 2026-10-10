"""Lane VF, decisions 1-2 (2026-10-10): a timer notice is neither lost nor in the way.

What the voice test of 2026-10-10 found, read from the server log (lane VF,
decision 1): the one-minute notice of the step-3 timer was said about ten
seconds late because the experimenter was speaking until then, and the end
notice was never said at all -- not because the session was busy, but
because the experimenter interrupted an answer while the notice waited to be
said, and a new generation made the waiting coroutine give the notice up for
good, without a word in the log. Meanwhile one notice's wait (up to two
minutes) held the watcher itself, so nothing else was looked at.

Decision 2: each notice is shown at once and then handed to a task of its
own, so the watcher keeps looking every second; a barge-in no longer loses
a notice (only the session or the timer going away does); when a timer's end
comes while its last minute is still unsaid, the last minute keeps its line
on the screen and loses its voice; and when the page never reports that a
notice's playback ended, the server returns the session to listening once
the audio's own length and a grace have passed.

The clock is a fake the test moves; the watcher is the real coroutine on a
real event loop; the TTS is a fake with a chosen latency.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_lane_vt_timer_end import STEP_3_KOREAN, T0, wash
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.server import (
    ListenerSession,
    LockedSender,
    _deliver_timer_notice,
    _settle_timer_notices,
    _timer_notice_tick,
    _watch_timer_notices,
)
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

#: The fake TTS gives one second of audio (fifty 20 ms frames) per sentence.
AUDIO_SECONDS = 1.0


class FakeClock:
    """A clock the test moves; coroutines sleeping on it wake in time order."""

    def __init__(self, start: float) -> None:
        self.value = start
        self._waiting: list[tuple[float, int, asyncio.Future]] = []
        self._sequence = 0
        self.on_advance = None

    def now(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        future = asyncio.get_running_loop().create_future()
        self._sequence += 1
        heapq.heappush(self._waiting, (self.value + max(0.0, float(seconds)), self._sequence, future))
        await future

    async def run_until(self, until: float) -> None:
        """Move time to ``until``, waking each sleeper at its moment (one caller at a time)."""

        while True:
            for _ in range(20):
                await asyncio.sleep(0)
            if not self._waiting:
                # A woken coroutine may be about to sleep again.
                for _ in range(20):
                    await asyncio.sleep(0)
            if not self._waiting or self._waiting[0][0] > until:
                break
            target, _, future = heapq.heappop(self._waiting)
            self._advance(target)
            if not future.done():
                future.set_result(None)
        self._advance(until)
        for _ in range(20):
            await asyncio.sleep(0)

    def _advance(self, target: float) -> None:
        if target > self.value:
            self.value = target
        if self.on_advance is not None:
            self.on_advance(self.value)


class _Connection(unittest.TestCase):
    """A voice connection on a fake clock: the listener, a recording socket, a fake TTS."""

    def setUp(self) -> None:
        self.clock = FakeClock(T0)
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        self.listener = ListenerSession(tool_context=context, clock=self.clock.now)
        self.listener.start()
        self.listener.accept_configuration(41, "cascade", "ko", "lane-n-wash")
        self.listener.greeting_audio_ready = True
        self.listener.curated_protocol_session = CuratedProtocolSession(wash(localizations=STEP_3_KOREAN))
        self.curated = self.listener.curated_protocol_session
        self.curated.activate_configured()
        self.curated.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=41, generation=1)
        self.curated.current_index = 1
        self.curated.start_timer(now=T0)
        self.sent: list[tuple[float, dict]] = []
        self.playing_since: float | None = None
        self.page_plays = True
        test = self

        class Socket:
            async def send_text(self, value: str) -> None:
                message = json.loads(value)
                test.sent.append((test.clock.value, message))
                if message["type"] == "audio.segment.end":
                    test.playing_since = test.clock.value

            async def send_bytes(self, value: bytes) -> None:
                pass

        self.sender = LockedSender(Socket())
        self.synth_seconds = 0.0
        self.synthesized: list[tuple[float, str]] = []

        def synthesize(text, *_args, **_kwargs):
            self.synthesized.append((self.clock.value, text))
            return b"\0\0" * 320 * int(AUDIO_SECONDS * 50)

        async def to_thread(function, *args, **kwargs):
            if function is synthesize and self.synth_seconds:
                await self.clock.sleep(self.synth_seconds)
            return function(*args, **kwargs)

        for item in (
            patch("voiney_lab.server.synthesize", side_effect=synthesize),
            patch("voiney_lab.server.asyncio.to_thread", side_effect=to_thread),
        ):
            item.start()
            self.addCleanup(item.stop)
        #: When the experimenter speaks, as (from, to) on the fake clock.
        self.speaking: list[tuple[float, float]] = []
        #: When the experimenter interrupts an answer (a new generation).
        self.barge_ins: list[float] = []
        self._barged: set[float] = set()
        self.clock.on_advance = self._page_and_experimenter

    def _page_and_experimenter(self, now: float) -> None:
        """What happens around the server as time passes."""

        session = self.listener
        # Microphone chunks keep arriving in production; each one lets a
        # cooldown end (refresh_cooldown), as the server does.
        session.refresh_cooldown()
        for moment in self.barge_ins:
            if now >= moment and moment not in self._barged:
                self._barged.add(moment)
                session.generation += 1
        if (
            self.page_plays and self.playing_since is not None
            and session.state is TurnState.AGENT_SPEAKING
            and now >= self.playing_since + AUDIO_SECONDS
        ):
            session.playback_ended(session.active_turn_id)
            self.playing_since = None
        inside = any(start <= now < end for start, end in self.speaking)
        if inside and session.active_turn_id is None and session.state in (TurnState.IDLE, TurnState.COOLDOWN):
            session.active_turn_id = 7
            session.detector.state = TurnState.USER_SPEAKING
        elif not inside and session.active_turn_id == 7 and session.state is TurnState.USER_SPEAKING:
            session.active_turn_id = None
            session.detector.state = TurnState.IDLE

    def run_clock(self, body, until: float):
        """Run ``body`` (a coroutine function) beside the clock up to ``until``."""

        async def main():
            token = server_module._SPEAKING_SESSION.set(self.listener)
            try:
                task = asyncio.create_task(body())
                await self.clock.run_until(until)
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    return None
                return task.result()
            finally:
                server_module._SPEAKING_SESSION.reset(token)

        return asyncio.run(main())

    def watch(self, until: float) -> None:
        self.run_clock(
            lambda: _watch_timer_notices(self.listener, self.sender, clock=self.clock.now, sleep=self.clock.sleep),
            until,
        )

    def shown(self, kind: str) -> list[float]:
        return [t for t, m in self.sent if m["type"] == "protocol.timer.notice" and m["notice_kind"] == kind]

    def said(self, kind: str) -> list[float]:
        return [t for t, m in self.sent if m["type"] == "protocol.notice.speech" and m["notice_kind"] == kind]


class TheCauseTests(_Connection):
    """Decision 1: what the log of 2026-10-10 showed, reproduced and fixed."""

    def test_a_barge_in_while_the_notice_waits_no_longer_loses_it(self) -> None:
        # 02:48:10 the end fell due while the experimenter spoke; 02:48:23 a
        # barge-in opened a new generation; the end was never said.
        self.speaking = [(T0 + 599, T0 + 630)]
        self.barge_ins = [T0 + 613]
        self.clock._advance(T0 + 600)
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        said = self.run_clock(
            lambda: _deliver_timer_notice(
                self.listener, self.sender, self.curated, notice, sleep=self.clock.sleep, now=T0 + 600),
            T0 + 700,
        )
        self.assertTrue(said)
        self.assertEqual(self.shown("timer_ended"), [T0 + 600])
        (said_at,) = self.said("timer_ended")
        self.assertLessEqual(T0 + 630, said_at)
        self.assertLess(said_at, T0 + 631)
        speech = next(m for _, m in self.sent if m["type"] == "protocol.notice.speech")
        self.assertEqual(speech["generation"], self.listener.generation)
        self.assertEqual(self.curated.current_index, 1)

    def test_a_notice_given_up_says_why_in_the_log(self) -> None:
        self.speaking = [(T0 + 599, T0 + 800)]
        self.clock._advance(T0 + 600)
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        with self.assertLogs("voiney_lab", level="INFO") as logged:
            said = self.run_clock(
                lambda: _deliver_timer_notice(
                    self.listener, self.sender, self.curated, notice, sleep=self.clock.sleep, now=T0 + 600),
                T0 + 760,
            )
        self.assertFalse(said)
        self.assertTrue(any(
            f"timer notice not said notice_id={notice.notice_id} kind=timer_ended reason=not_quiet" in line
            for line in logged.output), logged.output)
        self.assertTrue(any("timer notice shown notice_id=" in line and "delay_ms=0" in line
                            for line in logged.output), logged.output)


class TheWatcherTests(_Connection):
    """Decision 2: the real watcher on a fake clock."""

    def test_each_notice_once_in_order_and_the_end_said_the_moment_it_is_quiet(self) -> None:
        self.speaking = [(T0 + 539, T0 + 575)]
        self.watch(T0 + 700)
        self.assertEqual([m["notice_kind"] for _, m in self.sent if m["type"] == "protocol.timer.notice"],
                         ["timer_last_minute", "timer_ended"])
        (minute_shown,) = self.shown("timer_last_minute")
        (end_shown,) = self.shown("timer_ended")
        self.assertLessEqual(T0 + 540, minute_shown)
        self.assertLess(minute_shown, T0 + 541.1)
        self.assertLessEqual(T0 + 600, end_shown)
        self.assertLess(end_shown, T0 + 601.1)
        # The last minute waited its thirty seconds through speech and stayed on the screen.
        self.assertEqual(self.said("timer_last_minute"), [])
        # The end was said when it fell due: the session was quiet.
        (end_said,) = self.said("timer_ended")
        self.assertLess(end_said - end_shown, 0.5)
        self.assertIn("10분 타이머가 끝났어요.", self.synthesized[-1][1])
        # The page reported the playback's end; the session listens again.
        self.assertTrue(server_module._session_quiet(self.listener))
        self.assertEqual(self.curated.current_index, 1)

    def test_the_watcher_keeps_looking_while_a_notice_waits_to_be_said(self) -> None:
        self.speaking = [(T0 + 599, T0 + 700)]
        ticks: list[float] = []
        real_tick = server_module._timer_notice_tick

        async def counted(session, sender, **kwargs):
            ticks.append(kwargs.get("now"))
            return await real_tick(session, sender, **kwargs)

        with patch.object(server_module, "_timer_notice_tick", side_effect=counted):
            self.watch(T0 + 760)
        (end_shown,) = self.shown("timer_ended")
        self.assertLess(end_shown, T0 + 601.1)
        # One look a second through the hundred seconds the end waited.
        waiting = [t for t in ticks if T0 + 601 <= t <= T0 + 699]
        self.assertGreaterEqual(len(waiting), 95, len(waiting))
        # Said the moment the experimenter finished.
        (end_said,) = self.said("timer_ended")
        self.assertLessEqual(T0 + 700, end_said)
        self.assertLess(end_said, T0 + 701)

    def test_the_end_takes_the_voice_of_an_unsaid_last_minute_and_the_screen_keeps_it(self) -> None:
        self.speaking = [(T0 + 539, T0 + 610)]
        with patch.object(server_module, "TIMER_LAST_MINUTE_SPEAK_WAIT_SECONDS", 120.0), \
             self.assertLogs("voiney_lab", level="INFO") as logged:
            self.watch(T0 + 700)
        self.assertEqual(len(self.shown("timer_last_minute")), 1)
        self.assertEqual(len(self.shown("timer_ended")), 1)
        self.assertEqual(self.said("timer_last_minute"), [])
        (end_said,) = self.said("timer_ended")
        self.assertLessEqual(T0 + 610, end_said)
        self.assertLess(end_said, T0 + 611)
        self.assertEqual([text for _, text in self.synthesized if "끝났어요" in text][:1],
                         [self.synthesized[0][1]])
        self.assertTrue(any("kind=timer_last_minute reason=superseded_by_end" in line for line in logged.output),
                        logged.output)

    def test_the_session_listens_again_when_the_page_never_reports_the_playbacks_end(self) -> None:
        self.page_plays = False
        states: list[tuple[float, str]] = []
        parent = self.clock.on_advance

        def note_state(now: float) -> None:
            parent(now)
            states.append((now, self.listener.state.value))

        self.clock.on_advance = note_state
        self.watch(T0 + 610)
        (end_said,) = self.said("timer_ended")
        self.assertEqual(end_said, T0 + 600)
        # Speaking through the audio's second and a little after it ...
        self.assertIn("AGENT_SPEAKING", {state for now, state in states if T0 + 600 < now < T0 + 602.5})
        # ... then, with no word from the page, the server ends the turn.
        ended = [(t, m) for t, m in self.sent if m["type"] == "state.changed" and m.get("cooldown_ms") is not None]
        # Once for the last minute (said at 540), once for the end.
        self.assertEqual([m["turn_id"] for _, m in ended], [2_000_000_101, 2_000_000_102])
        self.assertLessEqual(T0 + 600 + AUDIO_SECONDS + 2.0, ended[1][0])
        self.assertLess(ended[1][0], T0 + 604)
        self.assertNotIn("AGENT_SPEAKING", {state for now, state in states if now > T0 + 604})
        self.assertIsNone(self.listener.active_turn_id)
        self.assertTrue(server_module._session_quiet(self.listener))

    def test_a_notice_whose_timer_is_gone_is_given_up_quietly(self) -> None:
        self.speaking = [(T0 + 599, T0 + 640)]
        restarted: list[float] = []
        parent = self.clock.on_advance

        def restart_at_620(now: float) -> None:
            parent(now)
            if now >= T0 + 620 and not restarted:
                restarted.append(now)
                self.curated.start_timer(now=T0 + 620)

        self.clock.on_advance = restart_at_620
        with self.assertLogs("voiney_lab", level="INFO") as logged:
            self.watch(T0 + 700)
        self.assertEqual(len(self.shown("timer_ended")), 1)
        self.assertEqual(self.said("timer_ended"), [])
        self.assertTrue(any("kind=timer_ended reason=timer_changed" in line for line in logged.output), logged.output)
        # The restarted timer's own notices are still to come.
        self.assertEqual(self.curated.next_timer_notice_due(), T0 + 620 + 540)


class TheTickTests(_Connection):

    def test_the_tick_shows_at_once_and_returns_before_the_notice_is_said(self) -> None:
        self.speaking = [(T0 + 599, T0 + 650)]

        async def tick_then_settle():
            self.clock._advance(T0 + 600)
            notices = await _timer_notice_tick(self.listener, self.sender, now=T0 + 600, sleep=self.clock.sleep)
            self.assertEqual([n.kind for n in notices], ["timer_ended"])
            self.assertEqual(self.shown("timer_ended"), [T0 + 600])
            self.assertEqual(self.said("timer_ended"), [])
            self.assertEqual(len(self.listener.timer_notice_tasks), 1)
            # The clock runs on outside; the notice's own task says it.
            await _settle_timer_notices(self.listener)
            return notices

        self.run_clock(tick_then_settle, T0 + 700)
        (end_said,) = self.said("timer_ended")
        self.assertLessEqual(T0 + 650, end_said)
        self.assertLess(end_said, T0 + 651)
        self.assertEqual(self.listener.timer_notice_tasks, set())


if __name__ == "__main__":
    unittest.main()
