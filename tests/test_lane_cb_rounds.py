"""Lane CB, decision 3 (2026-10-07): a fixed or registered repeat is guided round by round.

When the last step of a repeat's span is completed (the step whose text
states the repeat, when it follows the range, as lane R7's return reads it)
and rounds remain, the server asks "12~15단계를 한 번 더 해야 해요(2/3회차).
12단계로 돌아갈까요?"; a yes goes back the way lane R7's return does and the
round is recorded; a no moves on and is kept as a point done differently
from the source; with every round done the run moves on. A repetition whose
count the person left open ("아직 몰라") asks "한 번 더 하시나요?" after each
round. Nothing here judges whether a round was enough: the count is the
source's or the person's. A repeat-until (in-gel 7, 9, 20) is unchanged.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_cb_support import FIRST_HINT, Recorded, Turns, index_of
from tests.test_lane_r7_rule_gaps import _fixture as wash_fixture
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import FRONT_RULES, CuratedProtocolAction
from voiney_lab.identity import Principal, Role
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

# Lane VT, decision 4: the round that ended is counted first ("3회 중 1회째
# 끝났어요."), the count being the source's.
ROUND_2_OF_3 = "3회 중 1회째 끝났어요. 12~15단계를 한 번 더 해야 해요(2/3회차). 12단계로 돌아갈까요?"
ROUND_2_OF_3_SPOKEN = (
    "3회 중 1회째 끝났어요. 12~15단계를 한 번 더 해야 해요(3회 중 2회차). 12단계로 돌아갈까요?")
ROUND_3_OF_3 = "3회 중 2회째 끝났어요. 12~15단계를 한 번 더 해야 해요(3/3회차). 12단계로 돌아갈까요?"


class FixedRepeatRoundsTests(Turns, unittest.TestCase):
    """Headspace 16: "Repeat steps 12-15 twice more" -- three rounds by the source."""

    def at_16(self):
        self.open(index_of("12"))
        self.complete("12", "13", "14", "15")
        self.assertEqual(self.label(), "16")

    def test_completing_the_last_step_of_the_span_asks_for_the_next_round(self) -> None:
        self.at_16()
        before = self.projection()
        plan = self.say("16단계 완료했어")
        self.assertEqual(self.session.last_front_rule, "repeat_round")
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertEqual(plan.display_text, ROUND_2_OF_3)
        self.assertEqual(plan.speech_text, ROUND_2_OF_3_SPOKEN)
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.projection(), before)
        self.assertEqual(self.session.open_server_question()["kind"], "repeat_round")

    def test_a_completion_naming_no_step_is_asked_about_first_as_before(self) -> None:
        # Lane XO, decision 5 still asks "16단계 완료하셨나요?"; its yes then
        # meets the round question.
        for said in ("완료했어",):
            with self.subTest(said=said):
                self.at_16()
                asked = self.say(said)
                self.assertEqual(asked.speech_text, "16단계 완료하셨나요?")
                plan = self.say("응")
                self.assertEqual(plan.display_text, ROUND_2_OF_3)
                self.assertEqual(self.label(), "16")
        # Lane VX, decision 1: "다음 단계" moves on without the completion
        # question, so the round question is asked at once.
        self.at_16()
        plan = self.say("다음 단계")
        self.assertEqual(plan.display_text, ROUND_2_OF_3)
        self.assertFalse(plan.state_changed)
        self.assertEqual(self.label(), "16")

    def test_a_yes_goes_back_the_way_a_return_does_and_records_the_round(self) -> None:
        for said in ("응", "네", "돌아가", "한 번 더"):
            with self.subTest(said=said):
                self.at_16()
                self.say("16단계 완료했어")
                moved = self.say(said)
                self.assertEqual(self.session.last_front_rule, "yes_no_open_question")
                self.assertIs(moved.action, CuratedProtocolAction.NEXT)
                self.assertTrue(moved.state_changed)
                self.assertEqual(self.label(), "12")
                self.assertTrue(moved.speech_text.startswith(
                    "3회 중 2회차를 시작해요. 12단계로 돌아왔습니다. 안내를 화면에 표시했습니다."
                ), moved.speech_text)
                record = moved.step_record
                self.assertEqual(record["kind"], "repeat_return")
                self.assertEqual(record["repetition_id"], "repeat-12-15")
                self.assertEqual((record["returns_confirmed"], record["round"]), (1, 2))
                self.assertEqual(record["round_counted_from"], "confirmed_returns")
                self.assertEqual(record["rounds_required"], 3)
                self.assertEqual(record["count_source"], "source")
                self.assertTrue(record["guided"])

    def test_every_round_is_walked_and_then_the_run_moves_on(self) -> None:
        self.at_16()
        self.say("16단계 완료했어")
        self.say("응")
        self.complete("12", "13", "14", "15")
        third = self.say("16단계 완료했어")
        self.assertEqual(third.display_text, ROUND_3_OF_3)
        self.say("네")
        self.assertEqual(self.label(), "12")
        self.complete("12", "13", "14", "15")
        done = self.say("16단계 완료했어")
        self.assertTrue(done.state_changed)
        self.assertEqual(self.label(), "17")
        # Lane VX, decision 1: a named completion says what it recorded first.
        self.assertTrue(done.speech_text.startswith(
            "16단계 완료로 기록했어요. "
            "3회 중 3회째 끝났어요. 12~15단계 3회를 모두 마쳤어요. 17단계로 이동했습니다. 안내를 화면에 표시했습니다."
        ), done.speech_text)
        self.assertEqual(done.step_record["kind"], "repeat_round_completion")
        self.assertEqual(done.step_record["round"], 3)
        self.assertEqual(done.step_record["rounds_required"], 3)

    def test_a_no_moves_on_and_is_kept_as_done_differently(self) -> None:
        for said in ("아니", "아니요", "그만", "넘어갈게"):
            with self.subTest(said=said):
                self.at_16()
                self.say("16단계 완료했어")
                plan = self.say(said)
                self.assertEqual(self.session.last_front_rule, "yes_no_open_question")
                self.assertTrue(plan.state_changed)
                self.assertIs(plan.action, CuratedProtocolAction.NEXT)
                self.assertTrue(plan.reported_completion)
                self.assertEqual(self.label(), "17")
                self.assertTrue(plan.speech_text.startswith(
                    "알겠어요. 12~15단계는 1회차까지만 한 것으로 기록했어요(원문은 3회). "
                    "17단계로 이동했습니다. 안내를 화면에 표시했습니다."
                ), plan.speech_text)
                record = plan.step_record
                self.assertEqual(record["kind"], "repeat_declined")
                self.assertEqual(record["repetition_id"], "repeat-12-15")
                self.assertEqual(record["repeated_step_labels"], ["12", "15"])
                self.assertEqual(record["stated_at_step"], "16")
                self.assertEqual((record["rounds_done"], record["rounds_required"]), (1, 3))
                self.assertEqual(record["count_source"], "source")
                self.assertEqual(record["round_counted_from"], "confirmed_returns")
                self.assertFalse(record["completed_before"])

    def test_not_yet_or_anything_else_moves_nothing_and_lets_the_question_go(self) -> None:
        for said in ("아직", "메모해 줘 튜브 라벨 A-1", "현재 단계 알려줘"):
            with self.subTest(said=said):
                self.at_16()
                self.say("16단계 완료했어")
                before = self.projection()
                self.say(said)
                self.assertEqual(self.projection(), before)
                self.assertEqual(self.label(), "16")
                self.assertNotEqual((self.session.open_server_question() or {}).get("kind"), "repeat_round")

    def test_the_second_no_in_a_later_round_counts_the_rounds_done(self) -> None:
        self.at_16()
        self.say("16단계 완료했어")
        self.say("응")
        self.complete("12", "13", "14", "15")
        self.say("16단계 완료했어")
        plan = self.say("아니")
        self.assertTrue(plan.speech_text.startswith(
            "알겠어요. 12~15단계는 2회차까지만 한 것으로 기록했어요(원문은 3회)."))
        self.assertEqual(plan.step_record["rounds_done"], 2)
        self.assertEqual(plan.step_record["round"], 2)

    def test_a_return_asked_for_in_words_shares_the_round_count(self) -> None:
        self.at_16()
        self.say("12단계로 돌아가")
        moved = self.say("응")
        self.assertEqual(moved.step_record["round"], 2)
        self.assertEqual(moved.step_record.get("rounds_required"), 3)
        self.complete("12", "13", "14", "15")
        self.assertEqual(self.say("16단계 완료했어").display_text, ROUND_3_OF_3)

    def test_steps_inside_the_range_ask_nothing(self) -> None:
        self.open(index_of("12"))
        for label, following in (("12", "13"), ("13", "14"), ("14", "15"), ("15", "16")):
            plan = self.say(f"{label}단계 완료했어")
            # Lane VX, decision 1: what was recorded is said first; the hint
            # how to take it back only the first time in the session.
            hint = FIRST_HINT if label == "12" else ""
            self.assertEqual(
                plan.speech_text,
                f"{label}단계 완료로 기록했어요.{hint} {following}단계로 이동했습니다. 안내를 화면에 표시했습니다.")
            self.assertIsNone(self.session.open_server_question())

    def test_the_round_question_rolls_back_with_its_turn(self) -> None:
        self.at_16()
        self.say("16단계 완료했어")
        checkpoint = self.session._checkpoint()
        self.say("아니")
        self.assertEqual(self.label(), "17")
        self.session._restore(checkpoint)
        self.assertEqual(self.label(), "16")
        self.assertEqual(self.session.open_server_question()["kind"], "repeat_round")

    def test_the_front_rule_is_described(self) -> None:
        self.assertIn("repeat_round", FRONT_RULES)

    def test_round_words_are_on_the_session(self) -> None:
        self.at_16()
        self.assertEqual(self.session.repeat_round_status(), {
            "repetition_id": "repeat-12-15", "range": "12~15", "round": 1, "required": 3,
            "words": "1/3회차", "value_source": "source",
        })
        self.say("16단계 완료했어")
        self.say("응")
        self.assertEqual(self.session.repeat_round_status()["words"], "2/3회차")
        self.open(index_of("11"))
        self.assertIsNone(self.session.repeat_round_status())


class RegisteredRepeatRoundsTests(Turns, unittest.TestCase):
    """Headspace 21 (count answered or left open) and 42 (count by the branch)."""

    def at_21(self, answer: str):
        self.open(index_of("18"))
        self.say("18단계 완료했어")
        self.say(answer)
        self.complete("19", "20")
        self.assertEqual(self.label(), "21")

    def test_an_answered_count_is_guided_like_a_fixed_one(self) -> None:
        self.at_21("세 번")
        plan = self.say("21단계 완료했어")
        self.assertEqual(plan.display_text, "19~20단계를 한 번 더 해야 해요(2/3회차). 19단계로 돌아갈까요?")
        moved = self.say("응")
        self.assertEqual(self.label(), "19")
        self.assertEqual(moved.step_record["count_source"], "operator")
        self.assertEqual(moved.step_record["rounds_required"], 3)
        self.assertTrue(moved.speech_text.startswith("3회 중 2회차를 시작해요. 19단계로 돌아왔습니다."))

    def test_an_open_count_asks_after_each_round(self) -> None:
        self.at_21("아직 몰라")
        plan = self.say("21단계 완료했어")
        self.assertEqual(self.session.last_front_rule, "repeat_round")
        self.assertEqual(plan.display_text, "19~20단계를 1회 했어요. 한 번 더 하시나요? 하시면 19단계로 돌아갈게요.")
        self.assertEqual(plan.speech_text, "19~20단계를 한 번 했어요. 한 번 더 하시나요? 하시면 19단계로 돌아갈게요.")
        self.assertFalse(plan.state_changed)
        moved = self.say("응")
        self.assertEqual(self.label(), "19")
        self.assertTrue(moved.speech_text.startswith("2회차를 시작해요. 19단계로 돌아왔습니다."))
        self.assertEqual(moved.step_record["round"], 2)
        self.assertIsNone(moved.step_record["rounds_required"])
        self.assertEqual(moved.step_record["count_source"], "operator")
        self.complete("19", "20")
        again = self.say("21단계 완료했어")
        self.assertEqual(again.display_text, "19~20단계를 2회 했어요. 한 번 더 하시나요? 하시면 19단계로 돌아갈게요.")
        closed = self.say("아니")
        self.assertTrue(closed.state_changed)
        self.assertEqual(self.label(), "22")
        self.assertTrue(closed.speech_text.startswith(
            "알겠어요. 19~20단계는 2회 한 것으로 기록했어요(사람이 답함). "
            "22단계로 이동했습니다. 안내를 화면에 표시했습니다."
        ), closed.speech_text)
        record = closed.step_record
        self.assertEqual(record["kind"], "repeat_closed")
        self.assertEqual((record["count"], record["value_source"], record["decided"]), (2, "operator", "per_round"))
        self.assertEqual(record["round"], 2)
        self.assertEqual(self.session.registered_repetitions()["repeat-19-20"]["count"], 2)

    def test_a_yes_to_the_condition_asks_for_the_next_round_at_once(self) -> None:
        self.open(index_of("41"))
        self.say("41단계 완료했어")
        plan = self.say("네")
        self.assertEqual(
            plan.display_text,
            "조건에 해당한다고 기록했어요. 3회 중 1회째 끝났어요. "
            "36~41단계를 한 번 더 해야 해요(2/3회차). 36단계로 돌아갈까요?",
        )
        self.assertEqual(plan.step_record["kind"], "branch_answer")
        self.assertEqual(self.session.open_server_question()["kind"], "repeat_round")
        moved = self.say("응")
        self.assertEqual(self.label(), "36")
        self.assertEqual(moved.step_record["round"], 2)
        self.assertEqual(moved.step_record["rounds_required"], 3)
        self.complete("36", "37", "38", "39", "40", "41")
        self.assertEqual(self.label(), "42")
        self.assertIsNone(self.session.open_server_question())  # the condition was answered
        third = self.say("42단계 완료했어")
        self.assertEqual(
            third.display_text,
            "3회 중 2회째 끝났어요. 36~41단계를 한 번 더 해야 해요(3/3회차). 36단계로 돌아갈까요?")

    def test_an_unanswered_condition_holds_the_repeat(self) -> None:
        self.open(index_of("41"))
        self.say("41단계 완료했어")
        self.assertIsNone(self.session.repeat_round_status())
        self.say("아니요")
        self.assertEqual(self.label(), "43")
        self.assertIsNone(self.session.repeat_round_status())


class RepeatUntilIsUnchangedTests(Turns, unittest.TestCase):
    """In-gel's shape (lane R7's wash): the step stating a repeat-until waits on the observation."""

    def test_the_observation_is_asked_as_before_and_no_round_is_spoken(self) -> None:
        self.open(1, fixture=wash_fixture())
        self.complete("2", "3", "4")
        plan = self.say("5단계 완료했어")
        self.assertEqual(plan.intent_kind, "observation_confirmation_required")
        self.assertEqual(self.label(), "5")
        for phrase in ("회차", "번째", "충분"):
            self.assertNotIn(phrase, plan.speech_text)
        self.assertIsNone(self.session.repeat_round_status())
        self.assertNotEqual((self.session.open_server_question() or {}).get("kind"), "repeat_round")

    def test_a_return_within_a_repeat_until_is_as_before(self) -> None:
        self.open(4, fixture=wash_fixture())
        asked = self.say("2단계로 돌아가")
        self.assertEqual(asked.speech_text, "2단계로 돌아갈까요?")
        moved = self.say("응")
        self.assertTrue(moved.speech_text.startswith("2단계로 돌아왔습니다."))
        self.assertNotIn("guided", moved.step_record)
        self.assertNotIn("rounds_required", moved.step_record)


class ExperimentRecordTests(Recorded, unittest.TestCase):
    def setUp(self) -> None:
        self.start_recording()

    def run_to_16(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("12")
        for label in ("12", "13", "14", "15"):
            self.record(f"{label}단계 완료했어")

    def test_a_guided_return_is_the_return_event_with_the_count_source(self) -> None:
        self.run_to_16()
        self.record("16단계 완료했어")
        self.record("응")
        kind, label, record = self.events()[-1]
        self.assertEqual((kind, label), ("repeat_returned", "12"))
        self.assertEqual((record["round"], record["rounds_required"], record["count_source"]), (2, 3, "source"))

    def test_a_declined_round_is_the_completion_and_its_own_event(self) -> None:
        self.run_to_16()
        self.record("16단계 완료했어")
        self.record("아니")
        kinds = [(kind, label) for kind, label, _ in self.events()]
        self.assertEqual(kinds[-2:], [("step_completed", "16"), ("repeat_rounds_declined", "16")])
        record = self.events()[-1][2]
        self.assertEqual((record["rounds_done"], record["rounds_required"]), (1, 3))

    def test_a_closed_open_count_is_its_own_event(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("18")
        self.record("18단계 완료했어")
        self.record("아직 몰라")
        self.record("19단계 완료했어")
        self.record("20단계 완료했어")
        self.record("21단계 완료했어")
        self.record("아니")
        kinds = [(kind, label) for kind, label, _ in self.events()]
        self.assertEqual(kinds[-2:], [("step_completed", "21"), ("repeat_closed", "21")])
        self.assertEqual(self.events()[-1][2]["count"], 1)


class DurableSessionTests(Turns, unittest.TestCase):
    """With the workspace on: the guided return and the decline are mirrored."""

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
            principal_id="principal-cb", subject="dev:cb", organization_id="tenant-cb",
            display_name="cb", roles=frozenset({Role.RESEARCHER}),
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
            self.principal, protocol_id="lane-cb-headspace",
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

    def test_the_rounds_complete_their_steps_again_without_a_second_mark(self) -> None:
        self.begin()
        self.mirror("프로토콜 시작해줘")
        for label in range(1, 16):
            self.mirror(f"{label}단계 완료했어")
        self.mirror("16단계 완료했어")
        self.mirror("응")
        self.assertEqual(self.state()["current_step_label"], "12")
        for label in ("12", "13", "14", "15"):
            self.assertTrue(self.mirror(f"{label}단계 완료했어").state_changed)
        self.mirror("16단계 완료했어")
        self.mirror("아니")
        state = self.state()
        self.assertEqual(state["current_step_label"], "17")
        self.assertEqual([item["step_label"] for item in state["completed_steps"]][-2:], ["15", "16"])
        events = [(e["event_type"], e["step_label"]) for e in state["events"]]
        self.assertIn(("repeat_returned", "16"), events)
        self.assertEqual(events[-1], ("step_completed", "16"))
