"""Lane HEAR2, decision 3 (2026-10-26): the step timer takes voice commands.

The person's test: "타이머 일시정지해 줘" was never understood. The
speech-to-text had failed (lane HEAR2 report, section 1); but had the words
arrived, the front rules would have read "일시정지" as a pause of the whole
run (``_FRONT_PAUSE_COMMAND`` runs before every other rule) and the timer
would have gone on.

Now "타이머 일시정지해 줘/멈춰", "타이머 다시 시작/계속", "타이머 취소/꺼 줘"
and "타이머 처음부터 다시" are the front rule ``timer_control``, read before
the run's pause words; "타이머 몇 분 남았어" stays the time-left rule (F6)
and says when the timer is paused. The state machine gets the one pause it
lacked: a paused timer keeps its remaining time, its notices sleep, and a
resume shifts its start by the pause so every deadline is right again. A
cancel clears it; a restart runs the same value (the source's, or today's
changed value) from now. Every one is a state change the record keeps,
read back as the other commands are, asked about once in 시끄러운 곳 mode,
and nothing when asked as a question. The run's own pause words still
pause the run and only the run.

Person's decision 10: nothing here reads a protocol's words or numbers;
checked on four fixtures of three fields -- a wash (biology), a soil
extraction (chemistry), an electrode coating (materials/battery) and the
headspace-shaped protocol.
"""

from __future__ import annotations

import dataclasses
import unittest
from unittest.mock import patch

from tests.lane_cb_support import Turns, headspace_fixture
from tests.lane_n_support import notes_fixture
from tests.protocol_vocabulary_support import build_fixture
from voiney_lab.curated_protocol import FRONT_RULES, CuratedProtocolAction

T0 = 1_792_900_000.0


def soil_fixture():
    """A soil extraction (chemistry, analysis): a 5-minute shake at step 3, a 10-minute warm at step 4."""

    base = build_fixture(
        protocol_id="lane-hear2-soil", title="Soil extraction with a shaker table (fictional)",
        steps=(
            "1 Weigh 3 g of soil into a 125 mL Erlenmeyer flask.",
            "2 Add 30 mL of Ringer's solution made with reagent water.",
            "3 Shake on the shaker table for 5 minutes.",
            "4 Warm the extract on the heating block at 40 °C for 10 minutes.",
            "5 Keep the filtrate in the fridge.",
        ),
        materials=("Ringer's solution", "Reagent water"),
        equipment=("Shaker table", "Heating block", "Erlenmeyer flask"),
    )
    return dataclasses.replace(base, timer_manifest={"step-3": 300, "step-4": 600})


def coating_fixture():
    """An electrode coating (materials/battery): a 30-minute drying at step 3."""

    base = build_fixture(
        protocol_id="lane-hear2-coating", title="Electrode coating for a coin cell (fictional)",
        steps=(
            "1 Mix 94 wt% NMC powder, 3 wt% carbon black and 3 wt% PVDF binder in NMP.",
            "2 Coat the slurry on aluminium foil with a doctor blade at 150 um.",
            "3 Dry the coated foil in the vacuum oven at 80 C for 30 minutes.",
            "4 Press the electrode and punch 14 mm discs.",
        ),
        materials=("NMC powder", "Carbon black", "PVDF binder", "NMP"),
        equipment=("Doctor blade", "Vacuum oven", "Disc punch"),
    )
    return dataclasses.replace(base, timer_manifest={"step-3": 1800})


#: (field, fixture, the index of a step with a timer, its seconds)
FIELDS = (
    ("biology wash", notes_fixture, 1, 600),
    ("chemistry soil", soil_fixture, 2, 300),
    ("materials coating", coating_fixture, 2, 1800),
    ("headspace", lambda: dataclasses.replace(headspace_fixture(), timer_manifest={"step-5": 900}), 4, 900),
)


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TimerTurns(Turns):
    """A session at a timed step, its timer started at T0, the clock the test moves."""

    def start(self, fixture, index: int):
        self.clock = Clock()
        self.timing = patch("voiney_lab.curated_protocol.time.time", self.clock)
        self.timing.start()
        self.addCleanup(self.timing.stop)
        session = self.open(index, fixture=fixture())
        started, _duration, _ = session.start_timer(now=T0)
        assert started, "the step has no timer"
        return session

    def timer(self) -> dict:
        return self.session.timer_status(now=self.clock.now)


class TimerWordTests(TimerTurns, unittest.TestCase):
    def test_the_rule_is_described(self) -> None:
        self.assertIn("timer_control", FRONT_RULES)

    def test_pause_resume_restart_and_cancel_on_four_fixtures(self) -> None:
        for field, fixture, index, seconds in FIELDS:
            with self.subTest(field=field):
                session = self.start(fixture, index)
                label = session.fixture.steps[index].source_label
                self.clock.now = T0 + 120
                plan = self.say("타이머 일시정지해 줘")
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_PAUSE, plan.speech_text)
                self.assertTrue(plan.state_changed)
                self.assertEqual(session.last_front_rule, "timer_control")
                self.assertEqual(session.workflow_status, "active", "the run itself was paused")
                self.assertEqual(self.timer()["state"], "paused")
                self.assertEqual(self.timer()["remaining_seconds"], seconds - 120)
                self.assertIsNone(self.timer()["deadline_at"])
                self.assertIn("멈췄어요", plan.speech_text)
                self.assertIn(f"{label}단계", plan.speech_text)
                self.assertIsNotNone(plan.timer_payload)
                self.assertEqual(plan.timer_payload["state"], "paused")
                # Frozen while paused.
                self.clock.now = T0 + 400
                self.assertEqual(self.timer()["remaining_seconds"], seconds - 120)
                plan = self.say("타이머 몇 분 남았어")
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_STATUS)
                self.assertIn("멈춰", plan.speech_text)
                self.assertFalse(plan.state_changed)
                plan = self.say("타이머 다시 시작해 줘")
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_RESUME, plan.speech_text)
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.timer()["state"], "running")
                self.assertEqual(self.timer()["remaining_seconds"], seconds - 120)
                self.clock.now = T0 + 460
                self.assertEqual(self.timer()["remaining_seconds"], seconds - 180, "the pause did not count")
                plan = self.say("타이머 처음부터 다시")
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_RESTART, plan.speech_text)
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.timer()["state"], "running")
                self.assertEqual(self.timer()["remaining_seconds"], seconds)
                self.assertEqual(self.timer()["duration_seconds"], seconds, "the same value, the source's")
                plan = self.say("타이머 취소해 줘")
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_CANCEL, plan.speech_text)
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.timer()["state"], "not_started")
                self.assertIn("취소", plan.speech_text)

    def test_every_wording_reads_the_same_way(self) -> None:
        wordings = {
            CuratedProtocolAction.TIMER_PAUSE: ("타이머 멈춰", "타이머 잠깐 멈춰 줘", "타이머 일시 정지", "타이머 정지해 줘", "타이머 좀 멈춰 주세요", "타이머 스톱"),
            CuratedProtocolAction.TIMER_RESUME: ("타이머 계속", "타이머 재개해 줘", "타이머 이어서 해 줘", "타이머 다시 시작"),
            CuratedProtocolAction.TIMER_CANCEL: ("타이머 꺼 줘", "타이머 취소", "타이머 없애 줘", "타이머 끄기"),
            CuratedProtocolAction.TIMER_RESTART: ("타이머 처음부터 다시 해 줘", "타이머 리셋해 줘", "타이머 처음부터"),
        }
        for action, said_words in wordings.items():
            for said in said_words:
                with self.subTest(said=said):
                    session = self.start(notes_fixture, 1)
                    if action is CuratedProtocolAction.TIMER_RESUME:
                        session.pause_timer(now=T0)
                    self.clock.now = T0 + 30
                    plan = self.say(said)
                    self.assertIs(plan.action, action, f"{said}: {plan.speech_text}")
                    self.assertTrue(plan.state_changed, said)

    def test_a_question_changes_nothing(self) -> None:
        for said in ("타이머 일시정지할까?", "타이머 멈췄어?", "타이머 취소해도 돼?", "타이머 다시 시작했어?"):
            with self.subTest(said=said):
                session = self.start(notes_fixture, 1)
                self.clock.now = T0 + 30
                plan = self.say(said)
                self.assertFalse(plan.state_changed, plan.speech_text)
                self.assertNotIn(plan.action, {
                    CuratedProtocolAction.TIMER_PAUSE, CuratedProtocolAction.TIMER_RESUME,
                    CuratedProtocolAction.TIMER_CANCEL, CuratedProtocolAction.TIMER_RESTART,
                    CuratedProtocolAction.PAUSE})
                self.assertEqual(self.timer()["state"], "running")
                self.assertEqual(session.workflow_status, "active")

    def test_with_no_timer_running_nothing_changes_and_it_is_said(self) -> None:
        for said in ("타이머 일시정지해 줘", "타이머 다시 시작해 줘", "타이머 취소해 줘", "타이머 처음부터 다시"):
            with self.subTest(said=said):
                self.clock = Clock()
                self.open(0, fixture=notes_fixture())
                before = self.projection()
                plan = self.say(said)
                self.assertFalse(plan.state_changed, plan.speech_text)
                self.assertIn("지금 도는 타이머는 없어요", plan.speech_text)
                self.assertEqual(self.projection(), before)
                self.assertEqual(self.session.timer_status()["state"], "not_started")

    def test_resume_while_running_and_pause_while_paused_change_nothing(self) -> None:
        session = self.start(notes_fixture, 1)
        self.clock.now = T0 + 30
        plan = self.say("타이머 다시 시작해 줘")
        self.assertFalse(plan.state_changed, plan.speech_text)
        self.assertIn("돌고 있어요", plan.speech_text)
        self.assertEqual(self.timer()["state"], "running")
        session.pause_timer(now=T0 + 40)
        self.clock.now = T0 + 50
        plan = self.say("타이머 멈춰")
        self.assertFalse(plan.state_changed, plan.speech_text)
        self.assertIn("이미", plan.speech_text)
        self.assertEqual(self.timer()["remaining_seconds"], 560)

    def test_the_runs_own_pause_words_still_pause_the_run_and_only_the_run(self) -> None:
        session = self.start(notes_fixture, 1)
        self.clock.now = T0 + 30
        plan = self.say("일시정지해 줘")
        self.assertIs(plan.action, CuratedProtocolAction.PAUSE)
        self.assertEqual(session.workflow_status, "paused")
        self.assertEqual(self.timer()["state"], "running", "the run's pause leaves the timer running, as before")
        # While the run is paused only its resume words are heard.
        plan = self.say("타이머 멈춰")
        self.assertIsNot(plan.action, CuratedProtocolAction.TIMER_PAUSE)
        self.assertEqual(self.timer()["state"], "running")

    def test_in_a_noisy_room_a_timer_command_is_asked_about_once(self) -> None:
        session = self.start(notes_fixture, 1)
        session.apply_experimenter_settings({"ambient_mode": "noisy"})
        self.clock.now = T0 + 30
        plan = self.say("타이머 멈춰")
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION, plan.speech_text)
        self.assertIn("타이머를 멈출까요", plan.speech_text)
        self.assertEqual(self.timer()["state"], "running")
        plan = self.say("네")
        self.assertIs(plan.action, CuratedProtocolAction.TIMER_PAUSE, plan.speech_text)
        self.assertTrue(plan.state_changed)
        self.assertEqual(self.timer()["state"], "paused")
        plan = self.say("타이머 취소해 줘")
        self.assertIn("취소할까요", plan.speech_text)
        self.assertEqual(self.timer()["state"], "paused")
        plan = self.say("아니요")
        self.assertEqual(self.timer()["state"], "paused")


class TimerStateTests(TimerTurns, unittest.TestCase):
    def test_notices_sleep_while_paused_and_come_at_the_shifted_end(self) -> None:
        session = self.start(notes_fixture, 1)
        session.pause_timer(now=T0 + 120)
        self.assertEqual(session.due_timer_notices(now=T0 + 700), ())
        self.assertIsNone(session.next_timer_notice_due())
        self.assertTrue(session.resume_timer(now=T0 + 700))
        status = session.timer_status(now=T0 + 700)
        self.assertEqual(status["remaining_seconds"], 480)
        self.assertEqual(session.due_timer_notices(now=T0 + 1100), ())
        ended = session.due_timer_notices(now=T0 + 1180)
        self.assertEqual([n.kind for n in ended], ["timer_ended"])
        self.assertEqual(session.timer_status(now=T0 + 1180)["state"], "expired")

    def test_a_cancelled_timer_gives_no_notice_and_a_restart_counts_from_now(self) -> None:
        session = self.start(notes_fixture, 1)
        self.assertTrue(session.cancel_timer())
        self.assertEqual(session.timer_status(now=T0 + 10)["state"], "not_started")
        self.assertEqual(session.due_timer_notices(now=T0 + 700), ())
        self.assertFalse(session.cancel_timer(), "nothing to cancel twice")
        self.assertFalse(session.restart_timer(now=T0 + 20), "nothing to restart")
        session.start_timer(now=T0 + 20)
        self.assertTrue(session.restart_timer(now=T0 + 320))
        self.assertEqual(session.timer_status(now=T0 + 320)["remaining_seconds"], 600)
        self.assertEqual(session.timer_status(now=T0 + 920)["state"], "expired")

    def test_a_refused_save_rolls_the_pause_back(self) -> None:
        session = self.start(notes_fixture, 1)
        checkpoint = session._checkpoint()  # noqa: SLF001 - the rollback the server uses
        self.clock.now = T0 + 30
        self.say("타이머 멈춰")
        self.assertEqual(self.timer()["state"], "paused")
        session._restore(checkpoint)  # noqa: SLF001
        self.assertEqual(self.timer()["state"], "running")
        self.assertEqual(self.timer()["remaining_seconds"], 570)

    def test_the_state_the_screen_reads_carries_the_pause(self) -> None:
        session = self.start(notes_fixture, 1)
        session.pause_timer(now=T0 + 100)
        timer = session.state()["timers"]["step"]
        self.assertEqual(timer["state"], "paused")
        self.assertEqual(timer["remaining_seconds"], 500)
        self.assertIsNone(timer["deadline_at"])
        self.assertTrue(timer["paused_at"])


if __name__ == "__main__":
    unittest.main()
