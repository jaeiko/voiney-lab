"""Three gaps lane RP found on the rules' path, closed by rule (lane R7).

Decisions of 2026-10-06 (the professor's advice of 9/22: what is mechanical is
a rule; a state change needs an explicit request, a deterministic check and a
confirmation; no safety guidance the source and the approved safety material
do not give):

1. Spilling, knocking over or overflowing -- "흘렸어", "엎질렀어", "쏟았어",
   "넘쳤어", with or without what it was -- is recorded as an anomaly.
   "튜브를 흘렸어" used to be a question about tubes. A question about it
   ("흘려도 돼?") stays a question, and the emergency gate is unchanged.
2. In a repeat, "2단계로 돌아가" / "2단계부터 다시 할게" goes back, after
   "N단계로 돌아갈까요?", only to an earlier step of the repeat the source
   states at the current step; the return is recorded with its round (by
   confirmed returns). Anywhere else nothing moves and the reason is said.
3. "N단계부터 시작해줘" before the experiment or at its first step asks
   "1~(N-1)단계는 건너뛰고 N단계부터 시작할까요?"; a yes starts at N and the
   skipped steps are recorded.

The earlier repeat policy holds: nothing here says how many rounds to run or
that a round was enough, the hand-over record carries no count, and the step
that states the repeat still waits on the person's observation.
"""

from __future__ import annotations

import dataclasses
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol_vocabulary_support import SOURCE_PDF, build_fixture, in_gel_fixture
from voiney_lab import experiment_protocol as domain
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    CuratedProtocolAction,
    CuratedProtocolFixtureError,
    CuratedProtocolSession,
)
from voiney_lab.emergency import recognize_emergency
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.identity import Principal, Role
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

STEPS = (
    "1 Cut the stained band into pieces and place them in a 1.5 mL tube.",
    "2 Wash the pieces with 500 µL of Solution A.",
    "3 Remove and discard Solution A.",
    "4 Wash the pieces with 500 µL of Solution B.",
    "5 Repeat steps 2-4 until the band is clear.",
    "6 Dry the pieces in the speedvac.",
)


def _fixture(*, with_repeat: bool = True):
    base = build_fixture(protocol_id="lane-r7-wash", title="Fictional wash", steps=STEPS)
    if not with_repeat:
        return base
    protocol = base.draft.protocol
    anchor = protocol.sections[0].steps[4]
    protocol = dataclasses.replace(protocol, constructs=(
        domain.RepeatUntil(
            repetition_id="repeat-2-4",
            condition_source_text="until the band is clear",
            repeated_step_ids=("step-2", "step-3", "step-4"),
            evidence=domain.SourceEvidence(1, anchor.instruction_source_text),
            step_id="step-5",
        ),
    ))
    domain.validate_protocol(protocol)
    draft = dataclasses.replace(
        base.draft, protocol=protocol, readiness=domain.assess_readiness(protocol),
    )
    return dataclasses.replace(base, draft=draft)


class _Turns:
    """One session, its turns numbered, read the way the server reads them."""

    def open(self, step_index: int | None, *, fixture=None) -> CuratedProtocolSession:
        self.session = CuratedProtocolSession(fixture or _fixture())
        self.session.activate_configured()
        self.turn_id = 0
        if step_index is not None:
            self.say("프로토콜 시작해줘")
            self.session.current_index = step_index
        return self.session

    def say(self, text: str, *, front: bool = False):
        self.turn_id += 1
        if front:
            return self.session.front_plan(
                text, turn_id=self.turn_id, language="ko",
                configuration_id=1, generation=1,
            )
        return route_curated_runtime_turn(
            self.session, text, turn_id=self.turn_id, language="ko",
            configuration_id=1, generation=1,
        ).plan

    def label(self) -> str | None:
        session = self.session
        return session.fixture.steps[session.current_index].source_label if session.active else None

    def projection(self):
        session = self.session
        return (
            session.active, session.current_index, session.workflow_status,
            session.state()["revision"],
        )


# --- Decision 1 --------------------------------------------------------------


class SpillIsRecordedAsAnAnomalyTests(_Turns, unittest.TestCase):
    """Decision 1: spilling words are a problem at the bench, recorded."""

    SPILLS = (
        "튜브를 흘렸어", "흘렸어", "아 흘렸다", "용액을 조금 흘렸어", "피펫팅하다가 흘렸어",
        "엎질렀어", "시약을 엎질렀어", "엎질러졌어", "튜브를 엎었어", "튜브가 엎어졌어",
        "쏟았어", "튜브를 쏟았어", "쏟아졌어", "쏟아버렸어",
        "넘쳤어", "용액이 넘쳤어", "넘쳐버렸어", "용액이 흘러넘쳤어",
        "Solution A를 흘렸어", "조금 흘린 것 같아", "흘렸어요", "쏟았습니다",
        "넘치고 있어", "용액이 쏟아지고 있어",
        # Said as having happened, with a question after it.
        "흘렸는데 어떡해", "튜브를 쏟았는데 어떻게 해?", "용액을 쏟았는데 어떻게 하지",
        "흘렸는데 괜찮아?",
    )
    QUESTIONS = (
        "흘려도 돼?", "쏟으면 어떡해?", "엎지르면 어떻게 해?", "넘치면 어떡해?",
        "흘렸어?", "흘리지 않게 조심해야 돼?", "흘린 거 아니야", "안 흘렸어",
        "쏟지 않도록 해야 해?", "시료를 흘렸어?", "흘렸는지 모르겠어", "흘렸어 아니야",
        "튜브가 넘치고 있어?",
    )

    def test_spilling_with_or_without_what_spilled_is_an_anomaly(self) -> None:
        for said in self.SPILLS:
            with self.subTest(said=said):
                self.open(2)
                before = self.projection()
                plan = self.say(said)
                self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
                self.assertTrue(plan.reported_anomaly)
                self.assertEqual(plan.anomaly_text, said)
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.projection(), before)

    def test_a_question_or_a_denial_about_spilling_is_not_recorded(self) -> None:
        for said in self.QUESTIONS:
            with self.subTest(said=said):
                self.open(2)
                plan = self.say(said)
                self.assertIsNot(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
                self.assertFalse(plan.reported_anomaly)
                self.assertFalse(plan.state_changed)

    def test_the_anomaly_reply_adds_no_safety_guidance(self) -> None:
        # Recorded, and nothing said about what to do: no guidance the source
        # or the approved safety material does not give.
        self.open(2)
        plan = self.say("튜브를 흘렸어")
        self.assertEqual(
            plan.speech_text,
            "현재 3단계의 이상 사항 기록 요청을 확인했습니다. "
            "실험 기록 저장이 성공한 뒤에만 기록 완료를 확인합니다.",
        )

    def test_spilling_under_an_open_endpoint_question_keeps_the_question(self) -> None:
        # As "튜브가 터졌어" already did: recorded, the question stays open and
        # no longer takes a bare yes.
        self.open(4)
        self.say("5단계 완료했어")
        self.assertIsNotNone(self.session.pending_observation_confirmation)
        plan = self.say("튜브를 흘렸어")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        held = self.session.pending_observation_confirmation
        self.assertIsNotNone(held)
        self.assertFalse(held.accepts_yes_no)
        self.assertEqual(self.label(), "5")

    def test_a_denied_spill_beside_an_open_anomaly_stays_its_detail(self) -> None:
        # Lane Q's exhaustive check, step 7 after "시료를 흘렸어": words that
        # deny something and say "흘렸어" were added to the open anomaly, and
        # still are. The new spilling words did not make them a new report.
        self.open(4)
        self.say("5단계 완료했어")
        self.say("시료를 흘렸어")
        plan = self.say("흘렸어 탈색되지 않았어 젤이 투명한가요")
        self.assertEqual(plan.intent_kind, "enrich_pending_anomaly")
        self.assertTrue(plan.reported_anomaly)

    def test_the_emergency_gate_is_unchanged(self) -> None:
        self.assertIsNotNone(recognize_emergency("불이 났어"))
        self.assertIsNotNone(recognize_emergency("용액 누출됐어"))
        for said in ("튜브를 흘렸어", "흘렸어", "용액이 넘쳤어"):
            with self.subTest(said=said):
                self.assertIsNone(recognize_emergency(said))

    def test_the_anomaly_is_written_to_the_experiment_record(self) -> None:
        self.open(2)
        before = self.session.current_index
        plan = self.say("튜브를 흘렸어")
        events = _report_events(self.session, plan, before, turn_id=self.turn_id)
        self.assertEqual(
            [(e["event_type"], e["step_label"], e["user_wording"]) for e in events],
            [("anomaly", "3", "튜브를 흘렸어")],
        )


# --- Decision 2 --------------------------------------------------------------


class ReturnWithinARepeatTests(_Turns, unittest.TestCase):
    """Decision 2: back to an earlier step of the repeat stated here, on a yes."""

    def test_a_return_is_asked_about_and_moves_only_on_a_yes(self) -> None:
        for said in (
            "2단계로 돌아가", "2단계부터 다시 할게", "2단계로 돌아갈게",
            "다시 2단계로", "2단계부터 다시", "2단계로 다시 가자",
            "2단계부터 다시 시작할게", "두 번째 단계로 돌아가",
        ):
            with self.subTest(said=said):
                self.open(4)
                before = self.projection()
                asked = self.say(said)
                self.assertEqual(self.session.last_front_rule, "repeat_return")
                self.assertIs(asked.action, CuratedProtocolAction.CLARIFY_COMPLETION)
                self.assertEqual(asked.speech_text, "2단계로 돌아갈까요?")
                self.assertFalse(asked.state_changed)
                self.assertEqual(self.projection(), before)
                moved = self.say("응")
                self.assertEqual(self.session.last_front_rule, "yes_no_open_question")
                self.assertIs(moved.action, CuratedProtocolAction.NEXT)
                self.assertTrue(moved.state_changed)
                self.assertFalse(moved.reported_completion)
                self.assertEqual(moved.intent_kind, "repeat_return_confirmed")
                self.assertEqual(self.label(), "2")
                self.assertTrue(moved.speech_text.startswith("2단계로 돌아왔습니다."))

    def test_the_return_is_recorded_with_its_round_by_confirmed_returns(self) -> None:
        self.open(4)
        self.say("2단계로 돌아가")
        first = self.say("네")
        self.assertEqual(first.step_record, {
            "kind": "repeat_return",
            "repetition_id": "repeat-2-4",
            "repeated_step_labels": ["2", "4"],
            "stated_at_step": "5",
            "from_step": "5",
            "to_step": "2",
            "returns_confirmed": 1,
            "round": 2,
            "round_counted_from": "confirmed_returns",
            "source_text": "until the band is clear",
            "source_page_number": 1,
        })
        for step in ("2", "3", "4"):
            self.say(f"{step}단계 완료했어")
        self.assertEqual(self.label(), "5")
        self.say("3단계로 돌아가")
        second = self.say("응")
        self.assertEqual(self.label(), "3")
        self.assertEqual(
            (second.step_record["returns_confirmed"], second.step_record["round"]), (2, 3),
        )

    def test_nothing_said_counts_rounds_or_judges_the_repeat(self) -> None:
        self.open(4)
        asked = self.say("2단계로 돌아가")
        moved = self.say("응")
        for spoken in (asked.speech_text, moved.speech_text, moved.display_text):
            for phrase in ("회차", "회째", "번째 반복", "충분", "완료되었", "보통"):
                with self.subTest(phrase=phrase):
                    self.assertNotIn(phrase, spoken)

    def test_the_hand_over_record_still_carries_no_count(self) -> None:
        self.open(4)
        self.say("2단계로 돌아가")
        self.say("응")
        disclosure = self.session.human_led_repeat_disclosure(1)
        self.assertIsNotNone(disclosure)
        for key in ("round", "rounds", "iteration", "repeat_count"):
            self.assertNotIn(key, disclosure)
        record = self.session.repeat_interval_record("repeat-2-4")
        self.assertNotIn("rounds", record)
        self.assertNotIn("completed_at", record)

    def test_the_step_stating_the_repeat_still_waits_on_the_observation(self) -> None:
        self.open(4)
        self.say("2단계로 돌아가")
        self.say("응")
        for step in ("2", "3", "4"):
            self.say(f"{step}단계 완료했어")
        plan = self.say("5단계 완료했어")
        self.assertEqual(plan.intent_kind, "observation_confirmation_required")
        self.assertEqual(self.label(), "5")

    def test_a_step_done_again_is_said_to_be_done_again(self) -> None:
        self.open(1)
        for step in ("2", "3", "4"):
            first_time = self.say(f"{step}단계 완료했어")
            self.assertIsNone(first_time.step_record)
        self.say("2단계로 돌아가")
        self.say("응")
        again = self.say("2단계 완료했어")
        self.assertTrue(again.state_changed)
        self.assertEqual(again.step_record, {
            "kind": "repeat_round_completion", "repetition_id": "repeat-2-4",
            "round": 2, "round_counted_from": "confirmed_returns",
            "completed_before": True,
        })

    def test_a_no_keeps_the_step(self) -> None:
        self.open(4)
        self.say("2단계로 돌아가")
        before = self.projection()
        plan = self.say("아니")
        self.assertIs(plan.action, CuratedProtocolAction.DECLINE_COMPLETION)
        self.assertEqual(plan.speech_text, "알겠습니다. 2단계로 돌아가지 않았습니다. 지금 5단계입니다.")
        self.assertEqual(self.projection(), before)

    def test_anything_else_lets_the_question_go(self) -> None:
        self.open(4)
        self.say("2단계로 돌아가")
        self.say("타이머 얼마나 남았어?")
        plan = self.say("응")
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.label(), "5")

    def test_a_return_outside_the_repeat_is_refused_with_the_reason(self) -> None:
        cases = (
            (4, "1단계로 돌아가",
             "원문이 5단계에서 말하는 반복 구간은 2~4단계예요. 1단계는 그 안의 앞 단계가 "
             "아니어서 이동하지 않았어요. 지금 5단계를 유지합니다."),
            (4, "6단계로 돌아가",
             "원문이 5단계에서 말하는 반복 구간은 2~4단계예요. 6단계는 그 안의 앞 단계가 "
             "아니어서 이동하지 않았어요. 지금 5단계를 유지합니다."),
            (2, "2단계로 돌아가",
             "원문은 5단계에서 2~4단계를 반복하라고 해요. 앞 단계로 돌아가기는 5단계에서만 "
             "할 수 있어서 이동하지 않았어요. 지금 3단계를 유지합니다."),
            (5, "2단계로 돌아가",
             "원문은 6단계에서 반복 구간을 말하지 않아요. 그래서 2단계로 이동하지 않았어요. "
             "지금 6단계를 유지합니다."),
            (4, "5단계로 돌아가", "지금이 5단계예요. 단계를 옮기지 않았어요."),
            (4, "9단계로 돌아가",
             "이 프로토콜은 1~6단계예요. 9단계는 없어서 이동하지 않았어요. 지금 5단계를 유지합니다."),
        )
        for index, said, reason in cases:
            with self.subTest(said=said, at=index + 1):
                self.open(index)
                before = self.projection()
                plan = self.say(said)
                self.assertEqual(self.session.last_front_rule, "repeat_return")
                self.assertIs(plan.action, CuratedProtocolAction.DECLINE_COMPLETION)
                self.assertEqual(plan.speech_text, reason)
                self.assertEqual(self.projection(), before)
                self.assertFalse(self.say("응").state_changed)
                self.assertEqual(self.projection(), before)

    def test_a_protocol_with_no_repeat_moves_nowhere(self) -> None:
        self.open(4, fixture=_fixture(with_repeat=False))
        plan = self.say("2단계로 돌아가")
        self.assertEqual(
            plan.speech_text,
            "원문은 5단계에서 반복 구간을 말하지 않아요. 그래서 2단계로 이동하지 않았어요. "
            "지금 5단계를 유지합니다.",
        )
        self.assertEqual(self.label(), "5")

    def test_a_question_about_going_back_asks_nothing(self) -> None:
        for said in ("2단계로 돌아가도 돼?", "2단계로 돌아가야 해?", "2단계로 돌아갈까?"):
            with self.subTest(said=said):
                self.open(4)
                self.say(said)
                self.assertNotEqual(self.session.last_front_rule, "repeat_return")
                self.assertFalse(self.session.awaiting_server_confirmation)
                self.assertFalse(self.say("응").state_changed)
                self.assertEqual(self.label(), "5")

    def test_an_open_note_question_does_not_take_the_request_as_a_note(self) -> None:
        self.open(4)
        self.say("관찰 기록")
        self.assertTrue(self.session.awaiting_server_confirmation)
        plan = self.say("2단계로 돌아가")
        self.assertFalse(plan.reported_observation)
        self.assertEqual(plan.speech_text, "2단계로 돌아갈까요?")

    def test_while_paused_the_pause_answers(self) -> None:
        self.open(4)
        self.say("잠깐 멈춰")
        plan = self.say("2단계로 돌아가")
        self.assertIs(plan.action, CuratedProtocolAction.PAUSE)
        self.assertFalse(self.session.awaiting_server_confirmation)

    def test_the_front_rules_take_the_request_and_the_yes(self) -> None:
        self.open(4)
        asked = self.say("2단계로 돌아가", front=True)
        self.assertIsNotNone(asked)
        self.assertEqual(self.session.last_front_rule, "repeat_return")
        moved = self.say("응", front=True)
        self.assertIsNotNone(moved)
        self.assertEqual(self.session.last_front_rule, "yes_no_open_question")
        self.assertEqual(self.label(), "2")
        self.assertIn("repeat_return", FRONT_RULES)
        self.assertIn("start_at_step", FRONT_RULES)

    def test_a_return_rolls_back_with_its_turn(self) -> None:
        self.open(4)
        self.say("2단계로 돌아가")
        checkpoint = self.session._checkpoint()
        self.say("응")
        self.assertEqual(self.session._repeat_returns, {"repeat-2-4": 1})
        self.session._restore(checkpoint)
        self.assertEqual(self.label(), "5")
        self.assertEqual(self.session._repeat_returns, {})
        self.assertIsNotNone(self.session._pending_step_move)

    def test_a_run_inside_a_later_round_can_be_resumed(self) -> None:
        fixture = _fixture()
        steps = [step.step_id for step in fixture.steps]
        session = CuratedProtocolSession(fixture)
        session.restore_experiment_progress(
            current_step_id="step-2", completed_step_ids=tuple(steps[:4]),
        )
        self.assertEqual(session.current_index, 1)
        # Every earlier step must still be complete, as for any run.
        with self.assertRaises(CuratedProtocolFixtureError):
            CuratedProtocolSession(fixture).restore_experiment_progress(
                current_step_id="step-3", completed_step_ids=("step-1", "step-3", "step-4"),
            )
        # And what is complete past the current step lies in its repeat.
        with self.assertRaises(CuratedProtocolFixtureError):
            CuratedProtocolSession(fixture).restore_experiment_progress(
                current_step_id="step-2", completed_step_ids=tuple(steps[:5]) + ("step-6",),
            )


# --- Decision 3 --------------------------------------------------------------


class StartAtAStepTests(_Turns, unittest.TestCase):
    """Decision 3: a later start, asked about, with the skipped steps kept."""

    def test_before_the_experiment_a_later_start_is_asked_about(self) -> None:
        for said in ("4단계부터 시작해줘", "4단계부터 시작할게", "4단계부터 할게",
                     "네 번째 단계부터 시작해줘", "프로토콜 4단계부터 시작해줘"):
            with self.subTest(said=said):
                self.open(None)
                asked = self.say(said)
                self.assertEqual(self.session.last_front_rule, "start_at_step")
                self.assertIs(asked.action, CuratedProtocolAction.CLARIFY_COMPLETION)
                self.assertEqual(asked.speech_text, "1~3단계는 건너뛰고 4단계부터 시작할까요?")
                self.assertFalse(self.session.active)
                started = self.say("응")
                self.assertIs(started.action, CuratedProtocolAction.START)
                self.assertTrue(started.state_changed)
                self.assertTrue(self.session.active)
                self.assertEqual(self.label(), "4")
                self.assertTrue(started.speech_text.startswith(
                    "1~3단계는 건너뛰고 4단계부터 시작합니다. 현재 4단계입니다."
                ))
                self.assertEqual(started.step_record, {
                    "kind": "start_at_step", "start_step": "4",
                    "skipped_step_labels": ["1", "2", "3"],
                    "skipped_step_ids": ["step-1", "step-2", "step-3"],
                    "experiment_started": True,
                })

    def test_one_skipped_step_is_named_alone(self) -> None:
        self.open(None)
        self.assertEqual(
            self.say("2단계부터 시작해줘").speech_text, "1단계는 건너뛰고 2단계부터 시작할까요?",
        )

    def test_at_the_first_step_a_later_start_is_asked_about(self) -> None:
        self.open(0)
        asked = self.say("4단계부터 시작해줘")
        self.assertEqual(asked.speech_text, "1~3단계는 건너뛰고 4단계부터 시작할까요?")
        started = self.say("네")
        self.assertEqual(self.label(), "4")
        self.assertFalse(started.step_record["experiment_started"])

    def test_past_the_first_step_the_start_is_not_chosen_again(self) -> None:
        self.open(2)
        before = self.projection()
        plan = self.say("5단계부터 시작해줘")
        self.assertEqual(self.session.last_front_rule, "start_at_step")
        self.assertEqual(
            plan.speech_text,
            "시작 단계는 실험을 시작하기 전이나 1단계에서만 고를 수 있어요. 지금 3단계를 유지합니다.",
        )
        self.assertEqual(self.projection(), before)

    def test_a_start_at_the_first_step_is_the_ordinary_start(self) -> None:
        self.open(None)
        plan = self.say("1단계부터 시작해줘")
        self.assertEqual(self.session.last_front_rule, "start_command")
        self.assertIs(plan.action, CuratedProtocolAction.START)
        self.assertEqual(self.label(), "1")

    def test_a_step_that_does_not_exist_is_said_so(self) -> None:
        self.open(None)
        plan = self.say("9단계부터 시작해줘")
        self.assertEqual(plan.speech_text, "이 프로토콜은 1~6단계예요. 9단계는 없어서 이동하지 않았어요.")
        self.assertFalse(self.session.active)

    def test_a_no_starts_nothing(self) -> None:
        self.open(None)
        self.say("4단계부터 시작해줘")
        plan = self.say("아니요")
        self.assertIs(plan.action, CuratedProtocolAction.DECLINE_COMPLETION)
        self.assertFalse(self.session.active)
        self.assertIsNone(self.session._experiment_started_at)

    def test_a_question_about_a_later_start_asks_nothing(self) -> None:
        self.open(None)
        self.say("4단계부터 시작해도 돼?")
        self.assertFalse(self.session.awaiting_server_confirmation)
        self.assertFalse(self.say("응").state_changed)
        self.assertFalse(self.session.active)

    def test_an_ended_experiment_is_not_started_later(self) -> None:
        self.open(2)
        self.say("실험 종료")
        self.say("네")
        self.say("4단계부터 시작해줘")
        self.assertFalse(self.session.awaiting_server_confirmation)
        self.assertFalse(self.say("응").state_changed)
        self.assertFalse(self.session.active)


# --- The experiment record (server.py's mapping) ------------------------------


def _report_events(session, plan, pre_index, *, turn_id, store=None, listener=None):
    """Append ``plan`` as server.py does and return the report's events."""

    if listener is None:
        tmp = tempfile.TemporaryDirectory()
        store = ExperimentReportStore(Path(tmp.name) / "reports.sqlite")
        listener = SimpleNamespace(
            session_id="lane-r7-session", experiment_report_store=store,
            experiment_report_id=None, _tmp=tmp,
        )
    with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
        server_module._record_experiment_report_plan(
            listener, session, plan, turn_id=turn_id, generation=1,
            pre_transition_index=pre_index,
        )
    return listener.experiment_report_store.get_report(listener.experiment_report_id)["events"]


class ExperimentRecordTests(_Turns, unittest.TestCase):
    """The return, the later start and the round are written to the record."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.listener = SimpleNamespace(
            session_id="lane-r7-session",
            experiment_report_store=ExperimentReportStore(Path(tmp.name) / "r.sqlite"),
            experiment_report_id=None,
        )

    def record(self, said: str):
        before = self.session.current_index
        plan = self.say(said)
        if plan.state_changed:
            _report_events(self.session, plan, before, turn_id=self.turn_id,
                           listener=self.listener)
        return plan

    def events(self):
        report = self.listener.experiment_report_store.get_report(
            self.listener.experiment_report_id)
        return [
            (e["event_type"], e["step_label"], (e.get("payload") or {}).get("step_record"))
            for e in report["events"]
        ]

    def test_a_return_is_its_own_event_at_the_step_returned_to(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = 4
        self.record("2단계로 돌아가")
        self.record("응")
        kinds = [(kind, label) for kind, label, _ in self.events()]
        self.assertEqual(kinds, [("session_started", "1"), ("repeat_returned", "2")])
        record = self.events()[-1][2]
        self.assertEqual((record["round"], record["returns_confirmed"]), (2, 1))
        self.assertEqual(record["from_step"], "5")

    def test_a_later_start_records_the_start_and_the_skipped_steps(self) -> None:
        self.open(None)
        self.record("4단계부터 시작해줘")
        self.record("응")
        events = self.events()
        self.assertEqual(
            [(kind, label) for kind, label, _ in events],
            [("session_started", "4"), ("steps_skipped", "4")],
        )
        self.assertEqual(events[-1][2]["skipped_step_labels"], ["1", "2", "3"])

    def test_a_later_start_at_the_first_step_records_the_skip_alone(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.record("4단계부터 시작해줘")
        self.record("응")
        self.assertEqual(
            [(kind, label) for kind, label, _ in self.events()],
            [("session_started", "1"), ("steps_skipped", "4")],
        )


class DurableSessionTests(_Turns, unittest.TestCase):
    """With the workspace on, as the pilot runs: nothing is completed twice."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        directory = Path(tmp.name) / "workspace"
        environment = patch.dict(os.environ, {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": str(directory),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.principal = Principal(
            principal_id="principal-r7", subject="dev:r7", organization_id="tenant-r7",
            display_name="r7", roles=frozenset({Role.RESEARCHER}),
            authentication_method="development",
        )
        self.store = initialize_workspace_store(WorkspaceSettings(True, directory))
        self.addCleanup(self.store.close)
        self.store.bootstrap_principal(self.principal)
        token = server_module._REQUEST_PRINCIPAL.set(self.principal)
        self.addCleanup(server_module._REQUEST_PRINCIPAL.reset, token)

    def begin(self) -> None:
        self.open(None)
        state = self.store.start_experiment(
            self.principal, protocol_id="lane-r7-wash",
            protocol_revision_id=self.session.fixture.revision_id,
            current_step_id="step-1", current_step_label="1",
        )
        self.listener = SimpleNamespace(
            session_id=state["session_id"], experiment_state_version=state["version"],
            voice_connection_id=None, accepted_configuration_id=1,
        )

    def mirror(self, said: str):
        before = self.session.current_index
        plan = self.say(said)
        if plan.state_changed:
            state = server_module._record_workspace_experiment_progress(
                self.listener, self.session, plan, turn_id=self.turn_id,
                generation=1, pre_transition_index=before,
            )
            self.listener.experiment_state_version = state["version"]
        return plan

    def state(self):
        return self.store.get_experiment(self.principal, self.listener.session_id)

    def test_a_round_completes_its_steps_again_without_a_second_mark(self) -> None:
        self.begin()
        for said in ("프로토콜 시작해줘", "1단계 완료했어", "2단계 완료했어",
                     "3단계 완료했어", "4단계 완료했어", "2단계로 돌아가", "응"):
            self.mirror(said)
        self.assertEqual(self.state()["current_step_label"], "2")
        for said in ("2단계 완료했어", "3단계 완료했어"):
            plan = self.mirror(said)
            self.assertTrue(plan.state_changed)
        state = self.state()
        self.assertEqual(state["current_step_label"], "4")
        self.assertEqual(
            [item["step_label"] for item in state["completed_steps"]], ["1", "2", "3", "4"],
        )
        events = [(e["event_type"], e["step_label"]) for e in state["events"]]
        self.assertIn(("repeat_returned", "5"), events)
        self.assertEqual(events[-2:], [("step_completed", "2"), ("step_completed", "3")])
        # Stopped here, the run is resumed where it stood.
        resumed = CuratedProtocolSession(self.session.fixture)
        resumed.restore_experiment_progress(
            current_step_id=str(state["current_step_id"]),
            completed_step_ids=tuple(item["step_id"] for item in state["completed_steps"]),
        )
        self.assertEqual(resumed.current_index, 3)

    def test_a_later_start_moves_the_durable_session_to_that_step(self) -> None:
        self.begin()
        self.mirror("4단계부터 시작해줘")
        self.mirror("응")
        state = self.state()
        self.assertEqual(state["status"], "in_progress")
        self.assertEqual(state["current_step_label"], "4")
        self.assertEqual(state["completed_steps"], [])
        started = state["events"][-1]
        self.assertEqual(started["event_type"], "protocol_started")
        self.assertEqual(started["payload"]["step_record"]["skipped_step_labels"], ["1", "2", "3"])


# --- The real in-gel protocol (lane RP's scenario) ----------------------------


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelScenarioTests(_Turns, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()

    def test_tube_spilled_is_an_anomaly(self) -> None:
        self.open(12, fixture=self.fixture)
        plan = self.say("튜브를 흘렸어")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)

    def test_going_back_to_step_2_from_step_7(self) -> None:
        self.open(6, fixture=self.fixture)
        self.assertEqual(self.say("2단계부터 다시 할게").speech_text, "2단계로 돌아갈까요?")
        moved = self.say("응")
        self.assertEqual(self.label(), "2")
        self.assertEqual(moved.step_record["repeated_step_labels"], ["2", "7"])
        self.assertEqual(moved.step_record["source_text"], "7 Repeat steps 2-7 until the gel band is fully destained")

    def test_the_repeat_stated_at_step_20_returns_to_17(self) -> None:
        self.open(19, fixture=self.fixture)
        self.assertEqual(self.say("17단계로 돌아가").speech_text, "17단계로 돌아갈까요?")
        self.say("응")
        self.assertEqual(self.label(), "17")
        self.open(17, fixture=self.fixture)
        self.assertTrue(re.match(r"원문은 20단계에서 17~18단계를", self.say("17단계로 돌아가").speech_text))

    def test_starting_at_step_10(self) -> None:
        self.open(None, fixture=self.fixture)
        self.assertEqual(
            self.say("10단계부터 시작해줘").speech_text,
            "1~9단계는 건너뛰고 10단계부터 시작할까요?",
        )
        self.say("응")
        self.assertEqual(self.label(), "10")


if __name__ == "__main__":
    unittest.main()
