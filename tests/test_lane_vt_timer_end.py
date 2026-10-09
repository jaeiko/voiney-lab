"""Lane VT, decision 1 (2026-10-09): the server says a timer has ended.

Until now the server's step timer was read only when asked. The moment it
runs out ("expired" in ``timer_status``) the server now says so first:

* an exact or approximate timer: "15분 타이머가 끝났어요.", "약 16시간 타이머가
  끝났어요."; a minimum or a maximum in lane VX's words ("최소 시간 2시간이
  지났어요.", "최대 시간 2시간이 됐어요.");
* then one line on the next step: "다음은 4단계, <the first sentence of its
  Korean>" -- only a Korean that passed lane TS's check -- or, without one,
  "다음은 4단계예요. 화면에서 확인해 주세요.";
* on the screen at once, with a short sound; said when "먼저 알려 주기" has it
  said, once the experimenter has finished speaking and no answer is playing,
  through the same playback and echo memory as every answer.

It is told once per timer and changes nothing: no step moves, nothing is
completed, the timer is left as it is. The clock is passed in (``now``); no
test waits.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.lane_cb_support import Turns
from tests.lane_n_support import notes_fixture
from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture
from tests.test_lane_vx_bound_timers import _Catalog
from tests.test_screen_cleanup import run_page_script
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.server import (
    ListenerSession,
    LockedSender,
    _deliver_timer_notice,
    _timer_notice_tick,
)
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

T0 = 1_000_000.0
#: The wash's step 3 in a Korean that passes lane TS's check.
STEP_3_KOREAN = {"step-3/current_step": "3단계: Solution A를 제거해 폐기합니다."}


def wash(**changes):
    return dataclasses.replace(notes_fixture(), **changes)


class Notices(Turns):
    """A session at a step with its timer started at T0."""

    def at_timer(self, index: int, *, fixture=None, mode: str | None = None) -> CuratedProtocolSession:
        session = self.open(index, fixture=fixture)
        if mode is not None:
            session.apply_experimenter_settings({"proactive_mode": mode})
        started, duration, _ = session.start_timer(now=T0)
        assert started, "the step has no timer"
        self.duration = duration
        return session

    @staticmethod
    def ends(session: CuratedProtocolSession, now: float):
        """The timer-end notices due at ``now`` (decision 2's last minute aside)."""

        return tuple(n for n in session.due_timer_notices(now=now) if n.kind == "timer_ended")

    def state_of(self, session: CuratedProtocolSession):
        return (session.active, session.current_index, session.workflow_status,
                session.state()["revision"], session._timer_started_at)


class TheEndTests(Notices, unittest.TestCase):

    def test_it_is_told_once_the_moment_the_timer_runs_out(self) -> None:
        session = self.at_timer(1, fixture=wash(localizations=STEP_3_KOREAN))
        before = self.state_of(session)
        self.assertEqual(self.ends(session, T0 + 599), ())
        (notice,) = session.due_timer_notices(now=T0 + 600)
        self.assertEqual(notice.kind, "timer_ended")
        self.assertEqual(
            notice.display_text,
            "10분 타이머가 끝났어요. 다음은 3단계, Solution A를 제거해 폐기합니다.")
        self.assertEqual((notice.step_label, notice.next_step_label), ("2", "3"))
        self.assertTrue(notice.spoken)
        self.assertTrue(notice.chime)
        self.assertEqual(notice.timer["name"], "10분")
        self.assertEqual(notice.translation_status, "verified_sidecar")
        # Once: not again a second later, nor an hour later.
        self.assertEqual(session.due_timer_notices(now=T0 + 601), ())
        self.assertEqual(session.due_timer_notices(now=T0 + 4200), ())
        # Read-only: the step, the record's revision and the timer are as they were.
        self.assertEqual(self.state_of(session), before)
        self.assertEqual(session.timer_status(now=T0 + 600)["state"], "expired")

    def test_without_a_checked_korean_the_screen_is_named(self) -> None:
        refused = {"step-3/current_step": "3단계: Solution A를 제거하지 마세요."}
        for fixture in (wash(), wash(localizations=refused)):
            with self.subTest(localizations=fixture.localizations):
                session = self.at_timer(1, fixture=fixture)
                (notice,) = session.due_timer_notices(now=T0 + 600)
                self.assertEqual(
                    notice.display_text,
                    "10분 타이머가 끝났어요. 다음은 3단계예요. 화면에서 확인해 주세요.")
                self.assertEqual(notice.translation_status, "not_applicable")

    def test_the_last_steps_timer_names_no_next_step(self) -> None:
        session = self.at_timer(5, fixture=wash(timer_manifest={"step-6": 300}))
        (notice,) = session.due_timer_notices(now=T0 + 300)
        self.assertEqual(notice.display_text, "5분 타이머가 끝났어요.")
        self.assertIsNone(notice.next_step_label)

    def test_moving_on_before_the_end_leaves_nothing_to_tell(self) -> None:
        session = self.at_timer(1, fixture=wash())
        self.say("2단계 완료했어")
        self.assertEqual(session.current_index, 2)
        self.assertEqual(session.due_timer_notices(now=T0 + 600), ())

    def test_a_timer_started_again_is_told_again(self) -> None:
        session = self.at_timer(1, fixture=wash())
        self.assertEqual(len(session.due_timer_notices(now=T0 + 600)), 1)
        session.start_timer(now=T0 + 700)
        self.assertEqual(self.ends(session, T0 + 1299), ())
        self.assertEqual(len(session.due_timer_notices(now=T0 + 1300)), 1)

    def test_a_pause_does_not_stop_the_timer_so_its_end_is_told(self) -> None:
        # The bench reaction goes on in a pause, and so does the server's
        # timer (it was so before this lane: timer_status reads no pause).
        session = self.at_timer(1, fixture=wash())
        self.assertTrue(session.pause_workflow(now=T0 + 100))
        (notice,) = session.due_timer_notices(now=T0 + 600)
        self.assertEqual(notice.kind, "timer_ended")
        self.assertEqual(session.pause_timer_status(now=T0 + 600)["state"], "paused")

    def test_nothing_is_told_before_the_start_or_after_the_end(self) -> None:
        session = CuratedProtocolSession(wash())
        session.activate_configured()
        self.assertEqual(session.due_timer_notices(now=T0 + 600), ())
        session = self.at_timer(1, fixture=wash())
        self.say("종료")
        self.say("네")
        self.assertTrue(session.experiment_ended)
        self.assertEqual(session.due_timer_notices(now=T0 + 600), ())


class TheWordsByKindTests(_Catalog, Notices):
    """Lane VX's minimum, approximate and maximum timers (no Korean: the screen is named)."""

    def test_each_kind_in_its_words(self) -> None:
        cases = (
            (0, 7200, "최소 시간 2시간이 지났어요. 다음은 2단계예요. 화면에서 확인해 주세요."),
            (1, 57600, "약 16시간 타이머가 끝났어요. 다음은 3단계예요. 화면에서 확인해 주세요."),
            (2, 7200, "최대 시간 2시간이 됐어요. 다음은 4단계예요. 화면에서 확인해 주세요."),
            (4, 600, "10분 타이머가 끝났어요. 다음은 6단계예요. 화면에서 확인해 주세요."),
        )
        for index, seconds, words in cases:
            with self.subTest(step=index + 1):
                session = self.at_timer(index, fixture=self.fixture)
                self.assertEqual(self.duration, seconds)
                self.assertEqual(self.ends(session, T0 + seconds - 1), ())
                (notice,) = session.due_timer_notices(now=T0 + seconds)
                self.assertEqual(notice.display_text, words)
                self.assertNotIn("끝났" if index in (0, 2) else "지났", notice.display_text)


class TheSettingTests(Notices, unittest.TestCase):

    def test_every_choice_shows_it_and_off_does_not_say_it(self) -> None:
        for mode, spoken in (("all", True), ("needed", True), ("off", False)):
            with self.subTest(mode=mode):
                session = self.at_timer(1, fixture=wash(), mode=mode)
                (notice,) = session.due_timer_notices(now=T0 + 600)
                self.assertEqual(notice.spoken, spoken)
                self.assertTrue(notice.chime)


@unittest.skipUnless(SOURCE_PDF.exists(), "the licensed in-gel PDF is not present")
class InGelTests(Notices, unittest.TestCase):

    def test_the_in_gel_timers_say_the_next_steps_korean(self) -> None:
        fixture = in_gel_fixture()
        session = self.at_timer(2, fixture=fixture)
        (notice,) = session.due_timer_notices(now=T0 + 900)
        self.assertEqual(
            notice.display_text,
            "15분 타이머가 끝났어요. 다음은 4단계, 젤 밴드가 들어 있는 튜브에서 Solution A를 제거해 폐기합니다.")
        # Step 9's reviewed Korean does not pass lane TS's check.
        session = self.at_timer(7, fixture=fixture)
        (notice,) = session.due_timer_notices(now=T0 + 900)
        self.assertEqual(
            notice.display_text, "15분 타이머가 끝났어요. 다음은 9단계예요. 화면에서 확인해 주세요.")


class _Server(unittest.TestCase):
    """The server's side: a listener with a voice session, a recording socket."""

    def setUp(self) -> None:
        self.now = [100.0]
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        self.listener = ListenerSession(tool_context=context, clock=lambda: self.now[0])
        self.listener.start()
        self.listener.accept_configuration(41, "cascade", "ko", "lane-n-wash")
        self.listener.greeting_audio_ready = True
        self.listener.curated_protocol_session = CuratedProtocolSession(wash(localizations=STEP_3_KOREAN))
        self.curated = self.listener.curated_protocol_session
        self.curated.activate_configured()
        self.curated.plan("프로토콜 시작해줘", turn_id=1, language="ko", configuration_id=41, generation=1)
        self.curated.current_index = 1
        self.curated.start_timer(now=T0)
        self.sent: list[dict] = []
        self.binary: list[bytes] = []
        test = self

        class Socket:
            async def send_text(self, value: str) -> None:
                test.sent.append(json.loads(value))

            async def send_bytes(self, value: bytes) -> None:
                test.binary.append(value)

        self.sender = LockedSender(Socket())
        self.synthesized: list[str] = []

        def synthesize(text, *_args, **_kwargs):
            self.synthesized.append(text)
            return b"\0\0" * 320

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        for item in (
            patch("voiney_lab.server.synthesize", side_effect=synthesize),
            patch("voiney_lab.server.asyncio.to_thread", side_effect=immediate),
        ):
            item.start()
            self.addCleanup(item.stop)

    def types(self) -> list[str]:
        return [item["type"] for item in self.sent]

    def deliver(self, notice, *, on_wait=None, wait_seconds: float = 120.0) -> bool:
        async def sleep(seconds: float) -> None:
            self.now[0] += seconds
            if on_wait is not None:
                on_wait()

        token = server_module._SPEAKING_SESSION.set(self.listener)
        try:
            return asyncio.run(_deliver_timer_notice(
                self.listener, self.sender, self.curated, notice, sleep=sleep,
                wait_seconds=wait_seconds))
        finally:
            server_module._SPEAKING_SESSION.reset(token)


class DeliveryTests(_Server):

    def test_shown_with_its_sound_then_said_as_the_servers_own_turn(self) -> None:
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.assertTrue(self.deliver(notice))
        self.assertEqual(self.types(), [
            "protocol.timer.notice", "protocol.notice.speech", "reply.delta", "state.changed",
            "audio.segment.start", "audio.segment.end", "reply.complete", "audio.complete", "turn.done",
        ])
        shown = self.sent[0]
        self.assertEqual(shown["text"], notice.display_text)
        self.assertTrue(shown["chime"])
        self.assertEqual(shown["notice_kind"], "timer_ended")
        self.assertEqual(shown["step_label"], "2")
        speech = self.sent[1]
        self.assertEqual(speech["turn_id"], 2_000_000_101)
        self.assertEqual(self.sent[-1]["route"], "server_notice")
        self.assertEqual(self.synthesized, [notice.speech_text])
        # The same playback state and echo memory as an answer.
        self.assertEqual(self.listener.state, TurnState.AGENT_SPEAKING)
        self.assertEqual(self.listener.active_turn_id, 2_000_000_101)
        self.assertIn(notice.speech_text, [text for _, text in self.listener.spoken_recently])
        self.assertIsNotNone(self.listener.own_speech_echo("10분 타이머가 끝났어요 다음은 3단계"))
        # The workflow is untouched.
        self.assertEqual(self.curated.current_index, 1)

    def test_it_waits_for_the_experimenter_to_finish(self) -> None:
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.listener.active_turn_id = 7
        self.listener.detector.state = TurnState.USER_SPEAKING
        waits = []

        def finish_after_three() -> None:
            waits.append(self.now[0])
            if len(waits) == 3:
                self.listener.active_turn_id = None
                self.listener.detector.state = TurnState.IDLE

        self.assertTrue(self.deliver(notice, on_wait=finish_after_three))
        self.assertEqual(len(waits), 3)
        # Shown at once; said only after the experimenter finished.
        self.assertEqual(self.types()[0], "protocol.timer.notice")
        self.assertEqual(self.types()[1], "protocol.notice.speech")

    def test_an_answer_playing_is_not_cut_off(self) -> None:
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.listener.active_turn_id = 9
        self.listener.detector.state = TurnState.AGENT_SPEAKING
        self.assertFalse(self.deliver(notice, wait_seconds=5.0))
        self.assertEqual(self.types(), ["protocol.timer.notice"])
        self.assertEqual(self.synthesized, [])
        self.assertEqual(self.listener.active_turn_id, 9)

    def test_off_shows_it_with_its_sound_and_says_nothing(self) -> None:
        self.curated.apply_experimenter_settings({"proactive_mode": "off"})
        (notice,) = self.curated.due_timer_notices(now=T0 + 600)
        self.assertFalse(self.deliver(notice))
        self.assertEqual(self.types(), ["protocol.timer.notice"])
        self.assertTrue(self.sent[0]["chime"])
        self.assertFalse(self.sent[0]["spoken"])
        self.assertEqual(self.synthesized, [])

    def test_the_tick_gives_it_once(self) -> None:
        async def ticks():
            first = await _timer_notice_tick(self.listener, self.sender, now=T0 + 500)
            second = await _timer_notice_tick(self.listener, self.sender, now=T0 + 600)
            third = await _timer_notice_tick(self.listener, self.sender, now=T0 + 601)
            return first, second, third

        first, second, third = asyncio.run(ticks())
        self.assertEqual((len(first), len(second), len(third)), (0, 1, 0))
        self.assertEqual(self.types().count("protocol.timer.notice"), 1)

    def test_the_tick_waits_for_the_page_to_play_sound(self) -> None:
        self.listener.greeting_audio_ready = False
        self.assertEqual(asyncio.run(_timer_notice_tick(self.listener, self.sender, now=T0 + 600)), ())
        # Still due once the page can play it.
        self.listener.greeting_audio_ready = True
        self.assertEqual(len(asyncio.run(_timer_notice_tick(self.listener, self.sender, now=T0 + 601))), 1)


class PageTests(unittest.TestCase):

    def test_the_notice_is_shown_with_a_sound_and_said_as_a_turn(self) -> None:
        result = run_page_script(r"""
acceptedSessionConfiguration={configuration_id:41,mode:"cascade",language:"ko",protocol_id:"p",revision_id:"r"};
let tones=0;playContext={currentTime:0,createOscillator(){return {type:"",frequency:{setValueAtTime(){}},connect(){},start(){tones++;},stop(){},onended:null};},createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){}},connect(){},disconnect(){}};},destination:{}};
await onMessage({data:JSON.stringify({type:"protocol.timer.notice",configuration_id:41,generation:1,notice_kind:"timer_ended",text:"10분 타이머가 끝났어요. 다음은 3단계예요. 화면에서 확인해 주세요.",chime:true,spoken:true,step_label:"2"})},sessionGeneration,socket);
assert(node("timer-notice").textContent.includes("10분 타이머가 끝났어요"),"the notice was not shown");
assert(node("timer-notice-container").hidden===false,"the notice row stayed hidden");
assert(tones===1,"no sound for the timer's end");
await onMessage({data:JSON.stringify({type:"protocol.notice.speech",configuration_id:41,turn_id:2000000101,generation:1,notice_id:"n1",notice_kind:"timer_ended",text:"10분 타이머가 끝났어요."})},sessionGeneration,socket);
assert(activeTurn===2000000101,"the notice's turn is not the one played");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
