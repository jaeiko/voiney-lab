"""Lane SAFE2 (2026-10-31): a transcript the recognizer got wrong cannot change the wrong state.

The person's live measurement of 2026-10-31 (30 development sentences,
quiet and centrifuge 10 dB, Chirp 3) had two wrongly changed states, both in
the centrifuge condition of the soil protocol:

* "타이머 재개" (the step timer paused) came back with its first word
  garbled and "재개" intact (2 of 5 characters wrong): the words fell
  through to the run's resume, which changed the status projection and the
  revision although nothing was paused;
* "관찰 기록해 줘: 측정값 100 mL" came back with 8 of 14 characters wrong,
  the number among them, and was stored as an observation at once.

The live rows keep error counts, never the transcript, so the garbles here
are built to those counts. Every check here crosses the production
``ListenerSession.accept_chunk`` -> ``run_turn`` boundary through the SIM
runner (the oracle carrier and a fake speech-to-text that returns the
garbled text), on the ten public protocols of four fields, or runs the
state machine itself with the rules' route. No provider is called.

Chirp 3 also writes a short command twice ("다음. 다음.", "네. 네.": the
person's probe of 14 clips, 7 doubled; the sound holds it once). A doubled
state-changing command changes the state once, or asks once.
"""

from __future__ import annotations

import asyncio
import unittest

from scripts.sim.corpus import Case, load_corpus
from scripts.sim.runner import run_case
from tests.test_lane_hear2_timer_commands import FIELDS, T0, TimerTurns
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession

PROTOCOLS = load_corpus()
FIELD_OF = {protocol.code: protocol.field for protocol in PROTOCOLS}


def timed_index(protocol) -> int | None:
    session = CuratedProtocolSession(protocol.fixture)
    return next((i for i in range(len(protocol.fixture.steps)) if session.timer_seconds_for_step(i)), None)


def heard(protocol, text: str, *, setup: str = "active", index: int = 1) -> dict:
    """One utterance through the production voice boundary, the recognizer returning ``text``."""

    case = Case(f"{protocol.code}.safe2", "safe2", text, index, setup)
    return asyncio.run(run_case(protocol, case))


class LiveRowOneTests(unittest.TestCase):
    """'타이머 재개' heard with its first word garbled, the step timer paused."""

    #: Two letters off the timer word (as the live row's error count has it),
    #: and one letter off ("다이머": read as the timer word, asked about once).
    GARBLED = ("다이모 재개", "타이밍 재개", "다이머 재개")

    def test_a_garbled_resume_on_an_unpaused_run_changes_nothing_but_asks_about_the_paused_timer(self) -> None:
        for protocol in PROTOCOLS:
            index = timed_index(protocol)
            if index is None:
                continue
            for text in self.GARBLED:
                with self.subTest(protocol=protocol.code, field=FIELD_OF[protocol.code], text=text):
                    result = heard(protocol, text, setup="timer_paused", index=index)
                    self.assertEqual(result["changed_fields"], ["confirmation"], result)
                    self.assertEqual(result["step_after"], result["step_before"])
                    self.assertEqual(result["action"], "clarify_completion", result["action"])
                    self.assertIn("타이머를 다시 시작할까요", result["spoken"])
                    if text != "다이머 재개":
                        self.assertIn("일시정지 상태가 아니에요", result["spoken"])

    def test_a_timer_command_with_its_timer_word_one_letter_off_asks_once_and_never_pauses_the_run(self) -> None:
        for protocol in PROTOCOLS:
            index = timed_index(protocol)
            if index is None:
                continue
            for text in ("다이머 일시정지", "다이머 잠깐 멈춰", "타이모 취소"):
                with self.subTest(protocol=protocol.code, field=FIELD_OF[protocol.code], text=text):
                    result = heard(protocol, text, setup="timer_running", index=index)
                    self.assertEqual(result["changed_fields"], ["confirmation"], result)
                    self.assertEqual(result["action"], "clarify_completion", result)
                    self.assertIn("타이머를", result["spoken"])
            for text in ("타이머 잠깐 멈", "타이머 잠깐 멈줘"):
                with self.subTest(protocol=protocol.code, field=FIELD_OF[protocol.code], text=text):
                    result = heard(protocol, text, setup="timer_running", index=index)
                    self.assertEqual(result["action"], "timer_pause", result)
                    self.assertEqual(set(result["changed_fields"]) - {"timer"}, set(), result)

    def test_a_resume_word_on_an_unpaused_run_with_no_timer_changes_nothing(self) -> None:
        for protocol in PROTOCOLS[:4]:
            for text in ("재개", "다시 시작", "다이모 재개"):
                with self.subTest(protocol=protocol.code, text=text):
                    result = heard(protocol, text, setup="active", index=1)
                    self.assertEqual(result["changed_fields"], [], result)
                    self.assertIn("일시정지 상태가 아니에요", result["spoken"])


class ResumeQuestionTests(TimerTurns, unittest.TestCase):
    """The yes to the question resumes the timer; a no leaves it paused; nothing else moves."""

    def test_yes_resumes_the_paused_timer_on_four_fixtures(self) -> None:
        for field, fixture, index, seconds in FIELDS:
            with self.subTest(field=field):
                session = self.start(fixture, index)
                self.clock.now = T0 + 120
                self.say("타이머 일시정지해 줘")
                self.assertEqual(self.timer()["state"], "paused")
                revision = session.state()["revision"]
                plan = self.say("다이모 재개")
                self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION, plan.speech_text)
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.timer()["state"], "paused")
                self.assertEqual(session.workflow_status, "active")
                self.assertEqual(session.state()["revision"], revision)
                plan = self.say("네")
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_RESUME, plan.speech_text)
                self.assertTrue(plan.state_changed)
                self.assertEqual(self.timer()["state"], "running")
                self.assertEqual(self.timer()["remaining_seconds"], seconds - 120)

    def test_yes_to_the_near_miss_timer_word_pauses_the_timer_only(self) -> None:
        for field, fixture, index, seconds in FIELDS:
            with self.subTest(field=field):
                session = self.start(fixture, index)
                self.clock.now = T0 + 30
                plan = self.say("다이머 일시정지")
                self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION, plan.speech_text)
                self.assertEqual(self.timer()["state"], "running")
                self.assertEqual(session.workflow_status, "active")
                plan = self.say("네")
                self.assertIs(plan.action, CuratedProtocolAction.TIMER_PAUSE, plan.speech_text)
                self.assertEqual(self.timer()["state"], "paused")
                self.assertEqual(self.timer()["remaining_seconds"], seconds - 30)
                self.assertEqual(session.workflow_status, "active")

    def test_no_leaves_the_timer_paused(self) -> None:
        field, fixture, index, seconds = FIELDS[1]
        session = self.start(fixture, index)
        self.clock.now = T0 + 60
        self.say("타이머 일시정지해 줘")
        self.say("타이밍 재개")
        plan = self.say("아니요")
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.timer()["state"], "paused")
        self.assertEqual(self.timer()["remaining_seconds"], seconds - 60)
        self.assertEqual(session.workflow_status, "active")


class LiveRowTwoTests(unittest.TestCase):
    """A measurement note whose value the recognizer lost or bent waits for one confirmation."""

    GARBLED = (
        "관찰 기록해 줘 측정 갑 뱅 미리",     # the lead word split and the value gone
        "관찰 기록해 줘 측정 값 100 mL",      # the lead word split, the value intact
        "관찰 기록해 줘 측정값 뱅 밀리",      # the value gone
        "관찰 기록해 줘 측정갑 100 mL",       # the lead word one letter off
    )

    def test_the_garbled_note_is_not_stored_but_asked_about(self) -> None:
        for protocol in PROTOCOLS:
            for text in self.GARBLED:
                with self.subTest(protocol=protocol.code, field=FIELD_OF[protocol.code], text=text):
                    result = heard(protocol, text)
                    self.assertEqual(result["changed_fields"], ["confirmation"], result)
                    self.assertEqual(result["intent_kind"], "note_confirmation_required")
                    self.assertIn("기록할까요", result["spoken"])

    def test_a_named_measurement_or_a_plain_observation_is_still_stored_at_once(self) -> None:
        for protocol in PROTOCOLS[:4]:
            for text in ("기록해 줘 pH 7.2", "관찰 기록해 줘 상층액 투명함", "기록해 줘 온도 삼십칠 도"):
                with self.subTest(protocol=protocol.code, text=text):
                    result = heard(protocol, text)
                    self.assertEqual(result["intent_kind"], "record_note", result)

    def test_the_gate_reads_the_lead_word_with_spacing_particles_and_one_letter_off(self) -> None:
        from voiney_lab.curated_protocol import contextless_measurement_needs_confirmation

        for text in ("측정값 100 mL", "측정 값 100 mL", "측정치는 37도", "측정갑 100 mL", "측정 갑 뱅 미리",
                     "수치 7.2", "측정 결과 뱅 밀리"):
            self.assertTrue(contextless_measurement_needs_confirmation(text), text)
        for text in ("pH 7.2", "온도 37도", "상층액 투명함", "2번 셀 질량 12.3 mg", "침전이 생겼어"):
            self.assertFalse(contextless_measurement_needs_confirmation(text), text)


class DoubledCommandTests(unittest.TestCase):
    """A command the recognizer wrote twice changes the state once, or asks once (ten protocols)."""

    def test_the_phrase_collapses_only_when_the_whole_is_one_phrase_repeated(self) -> None:
        from voiney_lab.curated_protocol import collapse_repeated_phrase

        for text, expected in (("다음. 다음.", "다음"), ("네. 네.", "네"), ("타이머 시작 타이머 시작", "타이머 시작"),
                               ("종료. 종료.", "종료"), ("타이머 일시정지 타이머 일시정지", "타이머 일시정지"),
                               ("다음 다음 다음", "다음"), ("3단계 완료했어 3단계 완료했어", "3단계 완료했어")):
            self.assertEqual(collapse_repeated_phrase(text), expected, text)
        for text in ("안녕 안녕하세요", "100 100 mL", "다음 단계 알려줘", "관찰 기록해 줘 pH 7.2 pH 7.2 맞아"):
            self.assertEqual(collapse_repeated_phrase(text), text)

    def test_doubled_timer_commands_touch_only_the_timer(self) -> None:
        for protocol in PROTOCOLS:
            index = timed_index(protocol)
            if index is None:
                continue
            with self.subTest(protocol=protocol.code, text="타이머 일시정지 타이머 일시정지"):
                result = heard(protocol, "타이머 일시정지 타이머 일시정지", setup="timer_running", index=index)
                self.assertEqual(result["action"], "timer_pause", result)
                self.assertNotIn("pause", result["changed_fields"])
                self.assertNotIn("workflow", result["changed_fields"])
            with self.subTest(protocol=protocol.code, text="타이머 재개 타이머 재개"):
                result = heard(protocol, "타이머 재개 타이머 재개", setup="timer_paused", index=index)
                self.assertEqual(result["action"], "timer_resume", result)
                self.assertEqual(set(result["changed_fields"]) - {"timer"}, set(), result)
            with self.subTest(protocol=protocol.code, text="타이머 취소 타이머 취소"):
                result = heard(protocol, "타이머 취소 타이머 취소", setup="timer_running", index=index)
                self.assertEqual(result["action"], "timer_cancel", result)
                self.assertEqual(set(result["changed_fields"]) - {"timer"}, set(), result)

    def test_doubled_moves_completions_and_the_end_change_the_state_once(self) -> None:
        for protocol in PROTOCOLS:
            with self.subTest(protocol=protocol.code, text="다음. 다음."):
                result = heard(protocol, "다음. 다음.", index=1)
                self.assertEqual(result["step_after"], result["step_before"] + 1, result)
            with self.subTest(protocol=protocol.code, text="다음 단계로. 다음 단계로."):
                result = heard(protocol, "다음 단계로. 다음 단계로.", index=1)
                self.assertEqual(result["step_after"], result["step_before"] + 1, result)
            with self.subTest(protocol=protocol.code, text="종료. 종료."):
                result = heard(protocol, "종료. 종료.", index=1)
                self.assertEqual(result["changed_fields"], ["confirmation"], result)
                self.assertEqual(result["intent_kind"], "stop_confirmation_required")
            with self.subTest(protocol=protocol.code, text="완료했어. 완료했어."):
                result = heard(protocol, "완료했어. 완료했어.", index=1)
                self.assertEqual(result["changed_fields"], ["confirmation"], result)
            label = protocol.fixture.steps[1].source_label
            with self.subTest(protocol=protocol.code, text=f"{label}단계 완료했어 {label}단계 완료했어"):
                result = heard(protocol, f"{label}단계 완료했어 {label}단계 완료했어", index=1)
                self.assertIn(result["action"], {"next", "clarify_completion"}, result)
                self.assertLessEqual(result["step_after"], result["step_before"] + 1)
            with self.subTest(protocol=protocol.code, text="실험 일시정지. 실험 일시정지."):
                result = heard(protocol, "실험 일시정지. 실험 일시정지.", index=1)
                self.assertEqual(result["action"], "pause", result)

    def test_a_doubled_note_is_stored_once_with_its_words_once(self) -> None:
        for protocol in PROTOCOLS[:4]:
            with self.subTest(protocol=protocol.code):
                result = heard(protocol, "기록해 줘 pH 7.2 기록해 줘 pH 7.2")
                self.assertEqual(result["intent_kind"], "record_note", result)
                self.assertEqual(result["changed_fields"], ["record"], result)


class SimHarnessStateTests(unittest.TestCase):
    """The SIM runner starts the run the way the product does, so a no-op is a no-op there too."""

    def test_a_bare_resume_on_a_running_experiment_changes_nothing_in_the_sim(self) -> None:
        for protocol in PROTOCOLS[:3]:
            with self.subTest(protocol=protocol.code):
                result = heard(protocol, "재개", setup="active", index=1)
                self.assertEqual(result["changed_fields"], [], result)


if __name__ == "__main__":
    unittest.main()
