"""Lane CB, decision 1 (2026-10-07): a source condition is asked, by rule, on entering its step.

Headspace step 42 reads "If using newly made Porapak tubes, repeat steps 36-41
twice more". On entering the step the server asks the source's own condition;
"네" takes the branch (here: the repeat of 36-41 is registered, with the count
the source states), "아니요" skips the branch's steps and moves on, "모르겠어"
reads the condition again and asks again. Nothing is decided before the
person answers: trying to move on asks the question again and moves nothing.
The answer is recorded with where its value came from ("operator"; the
place a lab default will take later is kept open).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.lane_cb_support import (
    CONDITION_42,
    CONDITION_43,
    Recorded,
    Turns,
    headspace_fixture,
    index_of,
)
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    CuratedProtocolAction,
    CuratedProtocolSession,
)
from voiney_lab.identity import Principal, Role
from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

ASK_42 = (
    f"이 단계에는 원문 조건이 있어요: “{CONDITION_42}”. 이 조건에 해당하나요? "
    "맞으면 '네', 아니면 '아니요', 모르면 '모르겠어'라고 해 주세요."
)
AGAIN_42 = (
    f"원문 조건을 다시 읽어 드릴게요: “{CONDITION_42}”. 이 조건에 해당하나요? "
    "맞으면 '네', 아니면 '아니요'라고 해 주세요."
)
FIRST_42 = (
    f"먼저 원문 조건에 답해 주세요: “{CONDITION_42}”. 이 조건에 해당하나요? "
    "맞으면 '네', 아니면 '아니요', 모르면 '모르겠어'라고 해 주세요. 단계를 넘기지 않았어요."
)


class ConditionalBranchTests(Turns, unittest.TestCase):
    """Decision 1 on the step whose text carries the condition."""

    def enter_42(self):
        self.open(index_of("41"))
        return self.say("41단계 완료했어")

    def test_entering_the_step_asks_the_source_condition(self) -> None:
        plan = self.enter_42()
        self.assertEqual(self.label(), "42")
        self.assertTrue(plan.state_changed)
        self.assertTrue(plan.speech_text.endswith(ASK_42), plan.speech_text)
        self.assertTrue(plan.speech_text.startswith("42단계로 이동했습니다."))
        self.assertIn(ASK_42, plan.display_text)
        question = self.session.open_server_question()
        self.assertEqual((question["kind"], question["text"]), ("branch", ASK_42))

    def test_a_yes_records_the_answer_and_registers_the_repeat_the_branch_holds(self) -> None:
        for said in ("네", "응", "맞아", "해당돼"):
            with self.subTest(said=said):
                self.enter_42()
                before = self.projection()
                plan = self.say(said)
                self.assertEqual(self.session.last_front_rule, "branch_condition")
                self.assertTrue(
                    plan.speech_text.startswith("조건에 해당한다고 기록했어요."), plan.speech_text,
                )
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.projection(), before)
                self.assertEqual(self.label(), "42")
                record = plan.step_record
                self.assertEqual(record["kind"], "branch_answer")
                self.assertEqual(record["branch_id"], "branch-42")
                self.assertEqual(record["step_label"], "42")
                self.assertEqual(record["condition_source_text"], CONDITION_42)
                self.assertEqual(record["answer"], "yes")
                self.assertEqual(record["value_source"], "operator")
                self.assertEqual(record["skipped_step_labels"], [])
                self.assertEqual(record["registered"], [{
                    "repetition_id": "repeat-36-41",
                    "repeated_step_labels": ["36", "41"],
                    "count": 3,
                    "value_source": "source",
                }])
                open_question = self.session.open_server_question()
                self.assertNotEqual((open_question or {}).get("kind"), "branch")
                self.assertEqual(self.session.branch_answers()["branch-42"]["answer"], "yes")

    def test_a_no_skips_the_branch_step_and_records_the_skip(self) -> None:
        for said in ("아니요", "아니", "아니야", "해당 안 돼"):
            with self.subTest(said=said):
                self.enter_42()
                plan = self.say(said)
                self.assertEqual(self.session.last_front_rule, "branch_condition")
                self.assertTrue(plan.state_changed)
                self.assertIs(plan.action, CuratedProtocolAction.NEXT)
                self.assertFalse(plan.reported_completion)
                self.assertEqual(self.label(), "43")
                self.assertTrue(plan.speech_text.startswith(
                    "조건에 해당하지 않는다고 기록했어요. 42단계는 건너뛰고 43단계로 이동했습니다. "
                    "안내를 화면에 표시했습니다."
                ), plan.speech_text)
                record = plan.step_record
                self.assertEqual(record["kind"], "branch_answer")
                self.assertEqual(record["answer"], "no")
                self.assertEqual(record["value_source"], "operator")
                self.assertEqual(record["skipped_step_labels"], ["42"])
                self.assertEqual(record["skipped_step_ids"], ["step-42"])
                self.assertEqual(record["registered"], [])
                self.assertIsNone(self.session.open_server_question())

    def test_dont_know_reads_the_condition_again_and_asks_again(self) -> None:
        for said in ("모르겠어", "잘 모르겠어요", "몰라", "글쎄"):
            with self.subTest(said=said):
                self.enter_42()
                before = self.projection()
                plan = self.say(said)
                self.assertEqual(self.session.last_front_rule, "branch_condition")
                self.assertEqual(plan.speech_text, AGAIN_42)
                self.assertEqual(plan.display_text, AGAIN_42)
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.projection(), before)
                self.assertIsNone(plan.step_record)
                self.assertEqual(self.session.open_server_question()["kind"], "branch")
                answered = self.say("응")
                self.assertEqual(answered.step_record["answer"], "yes")

    def test_moving_on_before_answering_asks_again_and_moves_nothing(self) -> None:
        for said in ("42단계 완료했어", "다음 단계", "완료했어", "끝났어", "다음 단계로 넘어가"):
            with self.subTest(said=said):
                self.enter_42()
                before = self.projection()
                plan = self.say(said)
                self.assertFalse(plan.state_changed, said)
                self.assertEqual(self.projection(), before)
                self.assertEqual(self.label(), "42")
                self.assertEqual(plan.speech_text, FIRST_42)
                self.assertEqual(self.session.open_server_question()["kind"], "branch")

    def test_the_question_stays_open_under_other_turns(self) -> None:
        self.enter_42()
        self.say("현재 단계 알려줘")
        self.assertEqual(self.session.open_server_question()["kind"], "branch")
        self.say("Porapak 튜브가 뭐야?")
        self.assertEqual(self.session.open_server_question()["kind"], "branch")
        answered = self.say("네")
        self.assertEqual(answered.step_record["answer"], "yes")

    def test_nothing_is_asked_where_the_source_states_no_condition(self) -> None:
        self.open(index_of("11"))
        plan = self.say("11단계 완료했어")
        self.assertEqual(self.label(), "12")
        self.assertEqual(plan.speech_text, "12단계로 이동했습니다. 안내를 화면에 표시했습니다.")
        self.assertIsNone(self.session.open_server_question())
        self.assertIsNone(plan.step_record)

    def test_a_later_start_at_the_conditional_step_asks_too(self) -> None:
        self.open(None)
        self.say("42단계부터 시작해줘")
        plan = self.say("응")
        self.assertEqual(self.label(), "42")
        self.assertTrue(plan.speech_text.endswith(ASK_42), plan.speech_text)
        self.assertEqual(self.session.open_server_question()["kind"], "branch")

    def test_the_answer_rolls_back_with_its_turn(self) -> None:
        self.enter_42()
        checkpoint = self.session._checkpoint()
        self.say("아니요")
        self.assertEqual(self.label(), "43")
        self.session._restore(checkpoint)
        self.assertEqual(self.label(), "42")
        self.assertEqual(self.session.branch_answers(), {})
        self.assertEqual(self.session.open_server_question()["kind"], "branch")

    def test_a_new_run_asks_again(self) -> None:
        self.enter_42()
        self.say("네")
        self.session.reset()
        self.assertEqual(self.session.branch_answers(), {})
        self.open(index_of("41"))
        self.assertTrue(self.say("41단계 완료했어").speech_text.endswith(ASK_42))

    def test_the_front_rule_is_described(self) -> None:
        self.assertIn("branch_condition", FRONT_RULES)

    def test_a_question_about_going_back_keeps_the_rule_path(self) -> None:
        # Lane R7's return at the step that states the repeat still works while
        # the condition is open: it is the person's explicit request.
        self.enter_42()
        asked = self.say("36단계로 돌아가")
        self.assertEqual(asked.speech_text, "36단계로 돌아갈까요?")
        moved = self.say("응")
        self.assertEqual(self.label(), "36")
        self.assertTrue(moved.state_changed)


class BranchOverLaterStepsTests(Turns, unittest.TestCase):
    """The condition at 43 governs 44-45: a no skips them when the run arrives."""

    def test_a_no_skips_the_branch_steps_on_arrival(self) -> None:
        self.open(index_of("43"), fixture=headspace_fixture(branch_later=True))
        self.say("43단계 완료했어")  # re-asks: the condition is still open
        self.assertEqual(self.label(), "43")
        answered = self.say("아니요")
        self.assertFalse(answered.state_changed)
        self.assertEqual(answered.step_record["skipped_step_labels"], [])
        self.assertTrue(answered.speech_text.startswith(
            "조건에 해당하지 않는다고 기록했어요. 44~45단계는 건너뛰어요."), answered.speech_text)
        moved = self.say("43단계 완료했어")
        self.assertTrue(moved.state_changed)
        self.assertEqual(self.label(), "46")
        self.assertTrue(moved.speech_text.startswith(
            "44~45단계는 조건에 해당하지 않아 건너뛰고 46단계로 이동했습니다."), moved.speech_text)
        self.assertEqual(moved.step_record["kind"], "branch_steps_skipped")
        self.assertEqual(moved.step_record["skipped_step_labels"], ["44", "45"])
        self.assertEqual(moved.step_record["branch_id"], "branch-43")

    def test_a_yes_walks_the_branch_steps(self) -> None:
        self.open(index_of("43"), fixture=headspace_fixture(branch_later=True))
        answered = self.say("네")
        self.assertTrue(answered.speech_text.startswith("조건에 해당한다고 기록했어요."))
        self.say("43단계 완료했어")
        self.assertEqual(self.label(), "44")


class ExperimentRecordTests(Recorded, unittest.TestCase):
    """The answer is its own event in the experiment report, with its value source."""

    def setUp(self) -> None:
        self.start_recording()

    def test_a_yes_is_recorded_at_the_step(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("41")
        self.record("41단계 완료했어")
        self.record("네")
        kinds = [(kind, label) for kind, label, _ in self.events()]
        self.assertEqual(kinds, [
            ("session_started", "1"), ("step_completed", "41"), ("branch_answered", "42"),
        ])
        record = self.events()[-1][2]
        self.assertEqual((record["answer"], record["value_source"]), ("yes", "operator"))
        self.assertEqual(record["registered"][0]["count"], 3)

    def test_a_no_records_the_answer_and_the_skipped_step(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("41")
        self.record("41단계 완료했어")
        self.record("아니요")
        kinds = [(kind, label) for kind, label, _ in self.events()]
        self.assertEqual(kinds[-1], ("branch_answered", "42"))
        record = self.events()[-1][2]
        self.assertEqual(record["skipped_step_labels"], ["42"])
        self.assertEqual(self.label(), "43")


class DurableSessionTests(Turns, unittest.TestCase):
    """With the workspace on: a skipped branch step is not completed, and the run resumes."""

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

    def test_a_skipped_step_is_not_completed_and_the_run_resumes_past_it(self) -> None:
        self.begin()
        # The count of 19-20 is given on the screen here, as before lane CB;
        # decision 2 asks it by voice.
        self.session.provide_operator_repetition_count(
            "repeat-19-20", 2, actor_principal_id="principal-cb", actor_role="researcher",
        )
        self.mirror("프로토콜 시작해줘")
        for label in range(1, 42):
            self.mirror(f"{label}단계 완료했어")
        self.mirror("아니요")
        state = self.state()
        self.assertEqual(state["current_step_label"], "43")
        self.assertEqual([item["step_label"] for item in state["completed_steps"]][-1], "41")
        self.assertEqual(state["events"][-1]["event_type"], "branch_steps_skipped")
        self.assertEqual(
            state["events"][-1]["payload"]["step_record"]["skipped_step_ids"], ["step-42"],
        )
        self.assertEqual(server_module._workspace_skipped_step_ids(state), ())
        skipped = server_module._workspace_branch_skipped_step_ids(state)
        self.assertEqual(skipped, ("step-42",))
        resumed = CuratedProtocolSession(self.session.fixture)
        resumed.restore_experiment_progress(
            current_step_id=str(state["current_step_id"]),
            completed_step_ids=tuple(item["step_id"] for item in state["completed_steps"]),
            branch_skipped_step_ids=skipped,
        )
        self.assertEqual(resumed.current_index, index_of("43"))
