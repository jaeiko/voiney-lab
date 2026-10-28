"""Today's values: a run that uses other values than the source (lane EASY, decision 2).

Human decision 2026-10-24. After the key values the start wizard offers
"오늘 원문과 다르게 할 값" (it may be skipped): a value the analysis read --
a time, a temperature, a speed, a volume, a concentration in a step's own
sentence -- and a new value a person types. During the run the same can be
said ("이번엔 3단계 15분 대신 20분") and is read back the confirm_mode way.
A changed value is said and shown as "20분(오늘 바꾼 값 · 원문 15분)", the
step's timer runs for it, and the notebook's "실제로 한 조건" writes "원문 →
실제". Only the same kind of unit is taken; safety sentences and completion
criteria are never offered; no model changes or proposes a value; which
analysis, step and value item changed is appended to the ledger, and a
put-back is an event of its own.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.identity_support import development_principal
from tests.lane_ra_support import agitation_fixture, session_at
from tests.lane_rp2_support import RecordWriter, protocol_double
from tests.lane_rp3_support import environment, temporary
from tests.test_lane_rv_review_pending import _CatalogCase
from tests.test_voice_pause_resume_persistence import _QueuedSocket
from voiney_lab import experiment_reports as er
from voiney_lab import server
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    CuratedProtocolAction,
    CuratedProtocolFixtureError,
    run_change_request,
)
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.llm_router import CHANGE_STATE_ACTIONS, RECORD_LOG
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.start_check import (
    RUN_CHANGES_APPLIED_EVENT,
    RUN_CHANGES_EVENT,
    RunChange,
    RunChangeError,
    RunChanges,
    StartCheck,
    check_run_change,
    parse_run_value,
    step_changeable_values,
)


STALE_ID = "run-changes-" + "a" * 24


def timed_session(confirm_mode: str = "readback"):
    """The agitation protocol at step 1 (15 min · 37°C · 800 rpm), its timer 15 min."""

    fixture = agitation_fixture()
    fixture = replace(fixture, timer_manifest={fixture.steps[0].step_id: 900})
    session = session_at(fixture, step_index=0)
    session.confirm_mode = confirm_mode
    return session


class TheValuesThatMayChangeTests(unittest.TestCase):
    def test_a_steps_time_temperature_speed_volume_and_concentration_are_listed(self) -> None:
        values = step_changeable_values(
            "s4", "4", "4. Add 100 µL of 25 mM AMBIC with 2 % SDS and 1 mg/mL trypsin; "
            "incubate at 37 °C for 15 min, then spin at 500 x g.")
        self.assertEqual([(value.kind, value.shown) for value in values], [
            ("volume", "100 µL"), ("concentration", "25 mM"), ("concentration", "2%"),
            ("concentration", "1 mg/mL"), ("temperature", "37°C"), ("time", "15분"), ("speed", "500 ×g")])
        self.assertEqual(values[0].item_id, "s4#1")

    def test_a_value_in_a_safety_sentence_or_statement_is_never_offered(self) -> None:
        values = step_changeable_values(
            "s1", "1", "1. Heat at 60 °C for 5 min. Do not exceed 70 °C.", ["Never heat above 95 °C."])
        self.assertEqual([value.shown for value in values], ["60°C", "5분"])
        values = step_changeable_values("s2", "2", "2. Boil for 10 min.", ["Boil for 10 min in a hood."])
        self.assertEqual(values, [])

    def test_a_typed_value_is_read_with_korean_and_english_units(self) -> None:
        for typed, read in (("20분", ("20", "min")), ("20 min", ("20", "min")), ("1.5 h", ("1.5", "h")),
                            ("40도", ("40", "°C")), ("40 °C", ("40", "°C")), ("1000 rpm", ("1000", "rpm")),
                            ("800 x g", ("800", "×g")), ("150 uL", ("150", "µL")), ("10 mM", ("10", "mM")),
                            ("-20 °C", ("-20", "°C"))):
            with self.subTest(typed=typed):
                self.assertEqual(parse_run_value(typed), read)
        for wrong in ("", "20", "fast", "5 g", "3 cups"):
            with self.subTest(wrong=wrong):
                self.assertIsNone(parse_run_value(wrong))

    def test_only_the_same_kind_of_unit_is_taken(self) -> None:
        temperature, time, speed = step_changeable_values("s1", "1", "1. Incubate at 37 °C for 15 min at 800 rpm.")
        self.assertEqual((time.kind, temperature.kind, speed.kind), ("time", "temperature", "speed"))
        change = check_run_change(time, "20", "min")
        self.assertEqual(change.words, "20분(오늘 바꾼 값 · 원문 15분)")
        self.assertEqual((change.source_seconds, change.seconds), (900, 1200))
        self.assertEqual(check_run_change(time, "1", "h").seconds, 3600)
        for value, number, unit, code in ((time, "40", "°C", "run_change_unit_kind"),
                                          (speed, "1000", "×g", "run_change_unit_kind"),
                                          (time, "15", "min", "run_change_same_as_source"),
                                          (time, "900", "s", "run_change_same_as_source"),
                                          (time, "0", "min", "run_change_number_invalid")):
            with self.subTest(number=number, unit=unit), self.assertRaises(RunChangeError) as caught:
                check_run_change(value, number, unit)
            self.assertEqual(caught.exception.code, code)
            self.assertTrue(caught.exception.message_ko)
        self.assertEqual(check_run_change(temperature, "-20", "°C").shown, "-20°C")


class TheWizardsChoiceTests(_CatalogCase):
    """The choice is a ledger event bound to the person and the exact analysis."""

    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        self.changes = RunChanges(self.catalog)
        self.options = self.changes.options(self.protocol_id)

    def item(self, label: str, kind: str) -> str:
        return next(value["item_id"] for value in self.options["values"]
                    if value["step_label"] == label and value["kind"] == kind)

    def test_the_list_holds_the_analysed_values_with_their_steps(self) -> None:
        shown = [(value["step_label"], value["kind_ko"], value["shown_ko"]) for value in self.options["values"]]
        self.assertIn(("2", "시간", "10분"), shown)
        self.assertIn(("2", "온도", "37°C"), shown)
        self.assertIn(("4", "속도", "500 ×g"), shown)
        self.assertIn(("1", "부피", "2 mL"), shown)
        self.assertEqual(self.options["target"]["kind"], "analysis")

    def test_a_choice_is_kept_with_its_analysis_and_used_by_one_start(self) -> None:
        principal = "person-a"
        chosen = self.changes.choose(
            self.protocol_id, [{"item_id": self.item("2", "time"), "value": "20분"}],
            target=self.options["target"], actor_principal_id=principal)
        self.assertEqual([item["words_ko"] for item in chosen["changes"]], ["20분(오늘 바꾼 값 · 원문 10분)"])
        (event,) = self.events(RUN_CHANGES_EVENT)
        self.assertEqual(event["actor_principal_id"], principal)
        self.assertEqual(event["target"]["kind"], "analysis")
        self.assertEqual(event["revision_id"], self.options["revision_id"])
        # Decision of 2026-10-24: a start finds a choice by the id it names, never by time.
        set_id = chosen["change_set_id"]
        revisions = (self.options["revision_id"],)
        changes = self.changes.for_start(
            self.protocol_id, set_id, actor_principal_id=principal, revision_ids=revisions)
        self.assertEqual([change.number for change in changes], ["20"])
        # Another account, or another revision, gets nothing of it.
        for actor, named in (("person-b", revisions), (principal, ("other-revision",))):
            with self.subTest(actor=actor, revisions=named), self.assertRaises(RunChangeError) as caught:
                self.changes.for_start(self.protocol_id, set_id, actor_principal_id=actor, revision_ids=named)
            self.assertEqual(caught.exception.code, "run_changes_stale")
        self.changes.mark_applied(self.protocol_id, set_id, experiment_session_id="exp-1",
                                  actor_principal_id=principal)
        with self.assertRaises(RunChangeError):
            self.changes.for_start(self.protocol_id, set_id, actor_principal_id=principal, revision_ids=revisions)
        self.assertEqual([change.number for change in self.changes.applied_to(self.protocol_id, "exp-1")], ["20"])
        self.assertEqual(len(self.events(RUN_CHANGES_APPLIED_EVENT)), 1)

    def test_a_choice_is_used_only_by_the_start_that_names_it(self) -> None:
        # Replaces the 12-hour rule (removed by the decision of 2026-10-24):
        # an unknown id is refused, and no other choice is looked up for it.
        self.changes.choose(self.protocol_id, [{"item_id": self.item("2", "time"), "value": "20분"}],
                            target=self.options["target"], actor_principal_id="person-a")
        with self.assertRaises(RunChangeError) as caught:
            self.changes.for_start(self.protocol_id, "run-changes-" + "0" * 24, actor_principal_id="person-a",
                                   revision_ids=(self.options["revision_id"],))
        self.assertEqual(caught.exception.code, "run_changes_stale")

    def test_skipping_the_step_is_a_choice_that_changes_nothing(self) -> None:
        self.changes.choose(self.protocol_id, [{"item_id": self.item("2", "time"), "value": "20분"}],
                            target=self.options["target"], actor_principal_id="person-a")
        skipped = self.changes.choose(self.protocol_id, [], target=self.options["target"],
                                      actor_principal_id="person-a")
        self.assertEqual(self.changes.for_start(
            self.protocol_id, skipped["change_set_id"], actor_principal_id="person-a",
            revision_ids=(self.options["revision_id"],)), ())

    def test_a_wrong_kind_an_unknown_item_or_a_stale_target_is_refused_with_its_sentence(self) -> None:
        cases = (
            ([{"item_id": self.item("2", "time"), "value": "40 °C"}], self.options["target"], "run_change_unit_kind"),
            ([{"item_id": "s9#1", "value": "20분"}], self.options["target"], "run_change_item_unknown"),
            ([{"item_id": self.item("2", "time"), "value": "스무"}], self.options["target"], "run_change_number_invalid"),
            ([{"item_id": self.item("2", "time"), "value": "20분"}], {"kind": "analysis", "analysis_revision_number": 9},
             "run_change_target_stale"),
        )
        for changes, target, code in cases:
            with self.subTest(code=code), self.assertRaises(RunChangeError) as caught:
                self.changes.choose(self.protocol_id, changes, target=target, actor_principal_id="person-a")
            self.assertEqual(caught.exception.code, code)
            self.assertTrue(caught.exception.message_ko.startswith(("2단계", "고른", "분석")))
        self.assertEqual(self.events(RUN_CHANGES_EVENT), [])
        with self.assertRaises(RunChangeError):
            self.changes.choose(self.protocol_id, [], target=self.options["target"], actor_principal_id=None)


class TheRunUsesTodaysValuesTests(unittest.TestCase):
    def test_the_timer_runs_for_the_changed_time_and_the_step_says_it(self) -> None:
        session = timed_session()
        value = next(item for item in session.changeable_values() if item.kind == "time")
        session.set_run_changes([check_run_change(value, "20", "min")], change_set_id="set-1")
        self.assertEqual(session.timer_seconds_for_step(0), 1200)
        plan = session.plan("타이머 시작해줘", turn_id=5, language="ko", configuration_id=1, generation=1)
        self.assertEqual(plan.action, CuratedProtocolAction.START_TIMER)
        self.assertEqual(session.timer_status()["duration_seconds"], 1200)
        self.assertIn("20분", plan.speech_text)
        state = session.state()
        self.assertEqual([item["words_ko"] for item in state["current_step_run_changes"]],
                         ["20분(오늘 바꾼 값 · 원문 15분)"])
        self.assertEqual(session.run_change_set_id, "set-1")

    def test_arriving_at_a_changed_step_says_the_value_and_a_time_question_names_it(self) -> None:
        fixture = agitation_fixture()
        session = session_at(fixture, step_index=0)
        value = next(item for item in session.changeable_values()
                     if item.step_label == "2" and item.kind == "volume")
        session.set_run_changes([check_run_change(value, "15", "µL")])
        plan = session.plan("다음", turn_id=5, language="ko", configuration_id=1, generation=1)
        self.assertEqual(session.current_index, 1)
        self.assertIn("이 단계는 바꾼 값으로 해요: 부피 15 µL(오늘 바꾼 값 · 원문 10 µL).", plan.speech_text)
        self.assertIn("15 µL(오늘 바꾼 값 · 원문 10 µL)", plan.display_text)
        timed = timed_session()
        time = next(item for item in timed.changeable_values() if item.kind == "time")
        timed.set_run_changes([check_run_change(time, "20", "min")])
        self.assertTrue(timed._source_time_answer("ko").endswith("오늘 바꾼 값: 20분(오늘 바꾼 값 · 원문 15분)."))

    def test_a_change_not_of_this_fixture_is_refused_whole(self) -> None:
        session = timed_session()
        stranger = RunChange("step-1#2", "step-1", "1", "time", "30", "min", "30 min", "20", "min")
        with self.assertRaises(CuratedProtocolFixtureError):
            session.set_run_changes([stranger])
        self.assertEqual(session.run_changes(), ())


class SaidDuringTheRunTests(unittest.TestCase):
    def say(self, session, words: str, turn_id: int):
        return session.plan(words, turn_id=turn_id, language="ko", configuration_id=1, generation=1)

    def test_readback_changes_at_once_and_reads_it_back(self) -> None:
        session = timed_session("readback")
        plan = self.say(session, "이번엔 1단계 15분 대신 20분", 5)
        self.assertEqual(plan.action, CuratedProtocolAction.CHANGE_VALUE)
        self.assertTrue(plan.state_changed)
        self.assertEqual(session.last_front_rule, "run_value_change")
        self.assertEqual(plan.speech_text, "1단계 시간을 바꿨어요: 20분(오늘 바꾼 값 · 원문 15분). "
                                           "되돌리려면 '1단계 원문대로'라고 말씀해 주세요.")
        self.assertEqual(plan.step_record["kind"], "run_value_changed")
        self.assertEqual(plan.step_record["change"]["item_id"], "step-1#2")
        self.assertEqual(plan.step_record["target"]["revision_id"], session.fixture.revision_id)
        self.assertEqual(session.timer_seconds_for_step(0), 1200)

    def test_confirm_mode_asks_first_and_only_a_yes_changes(self) -> None:
        session = timed_session("confirm")
        asked = self.say(session, "1단계 온도 40도로 바꿔 줘", 5)
        self.assertFalse(asked.state_changed)
        self.assertEqual(asked.speech_text, "1단계 온도 37°C를 40°C로 바꿀까요? 맞으면 '네'라고 해 주세요.")
        self.assertEqual(session.confirm_question()["kind"], "run_change")
        self.assertEqual(session.run_changes(), ())
        done = self.say(session, "네", 6)
        self.assertEqual(done.action, CuratedProtocolAction.CHANGE_VALUE)
        self.assertEqual([change.shown for change in session.run_changes()], ["40°C"])
        self.say(session, "1단계 속도 1000 rpm으로 할게", 7)
        declined = self.say(session, "아니요", 8)
        self.assertEqual(declined.speech_text, "바꾸지 않았어요.")
        self.assertEqual([change.shown for change in session.run_changes()], ["40°C"])

    def test_noisy_mode_asks_first_too(self) -> None:
        session = timed_session("readback")
        session.ambient_mode = "noisy"
        plan = self.say(session, "1단계 15분 대신 20분으로 할게", 5)
        self.assertEqual(plan.intent_kind, "run_value_change_confirmation_required")
        self.assertEqual(session.run_changes(), ())

    def test_a_wrong_kind_a_past_step_or_a_running_timer_changes_nothing(self) -> None:
        session = timed_session()
        refused = self.say(session, "1단계 속도 1000 x g로 할게", 5)
        self.assertFalse(refused.state_changed)
        self.assertIn("단위 종류가 달라요", refused.speech_text)
        self.say(session, "타이머 시작해줘", 6)
        running = self.say(session, "1단계 15분 대신 20분으로 할게", 7)
        self.assertIn("타이머가 돌고 있어", running.speech_text)
        self.assertEqual(session.run_changes(), ())
        session.current_index = 2
        past = self.say(session, "1단계 온도 40도로 바꿔 줘", 8)
        self.assertIn("이미 지나간 단계예요", past.speech_text)
        unknown = self.say(session, "3단계 15분 대신 20분으로 할게", 9)
        self.assertIn("찾지 못했어요", unknown.speech_text)
        self.assertEqual(session.run_changes(), ())

    def test_putting_a_value_back_is_its_own_event(self) -> None:
        session = timed_session()
        self.say(session, "1단계 15분 대신 20분", 5)
        back = self.say(session, "1단계 원문대로", 6)
        self.assertEqual(back.action, CuratedProtocolAction.CHANGE_VALUE)
        self.assertEqual(back.step_record["kind"], "run_value_reverted")
        self.assertEqual([item["item_id"] for item in back.step_record["reverted"]], ["step-1#2"])
        self.assertEqual(back.speech_text, "1단계를 원문 값으로 되돌렸어요: 시간 15분.")
        self.assertEqual(session.run_changes(), ())
        self.assertEqual(session.timer_seconds_for_step(0), 900)
        nothing = self.say(session, "1단계 원문대로", 7)
        self.assertEqual(nothing.speech_text, "1단계에는 오늘 바꾼 값이 없어요.")

    def test_an_open_notebook_question_does_not_take_a_change_as_its_answer(self) -> None:
        fixture = replace(agitation_fixture(), timer_manifest={"step-1": 900})
        session = session_at(fixture, step_index=None)
        session.confirm_mode = "readback"
        started = self.say(session, "시작", 1)
        self.assertIn("오늘 실험 목적이나 시료를", started.speech_text)
        # The words the end-to-end walk said (a volume, "…으로 할게"): taken as the purpose before.
        changed = self.say(session, "1단계 500마이크로리터 대신 600마이크로리터으로 할게", 2)
        self.assertEqual(changed.action, CuratedProtocolAction.CHANGE_VALUE)
        self.assertEqual([change.shown for change in session.run_changes()], ["600 µL"])
        self.assertIsNotNone(session.notebook_question_open())

    def test_before_the_start_words_change_nothing(self) -> None:
        fixture = replace(agitation_fixture(), timer_manifest={"step-1": 900})
        session = session_at(fixture, step_index=None)
        plan = self.say(session, "1단계 15분 대신 20분으로 할게", 2)
        self.assertFalse(plan.state_changed)
        self.assertIn("시작 화면", plan.speech_text)
        self.assertEqual(session.run_changes(), ())

    def test_questions_past_doings_notes_and_timer_commands_are_left_to_their_rules(self) -> None:
        for words in ("15분 대신 20분으로 할까?", "15분 대신 20분 했어", "노트에 적어 줘 15분 대신 20분",
                      "20분 타이머 시작해", "다음 단계로 가자", "몇 분이야?", "아까 바꿨어"):
            with self.subTest(words=words):
                self.assertIsNone(run_change_request(words))

    def test_a_turn_rolled_back_takes_its_change_back(self) -> None:
        session = timed_session()
        checkpoint = session._checkpoint()
        self.say(session, "1단계 15분 대신 20분", 5)
        self.assertTrue(session.run_changes())
        session._restore(checkpoint)
        self.assertEqual(session.run_changes(), ())
        self.assertEqual(session.timer_seconds_for_step(0), 900)

    def test_the_front_rule_is_named_and_no_model_tool_can_change_a_value(self) -> None:
        from voiney_lab.llm_router import ToolProposal

        self.assertIn("run_value_change", FRONT_RULES)
        self.assertNotIn("change_value", CHANGE_STATE_ACTIONS)
        self.assertNotIn("change_value", RECORD_LOG)
        said = "1단계 시간 20분으로 해 둬"
        for proposal in (ToolProposal(tool="change_state", action="change_value", evidence=said),
                         ToolProposal(tool="record_log", log_type="deviation", value="20분", evidence=said)):
            with self.subTest(proposal=proposal):
                session = timed_session()
                basis = session.proposal_basis(turn_id=5, generation=1)
                session.apply_tool_proposal(
                    [proposal], transcript=said, basis=basis, turn_id=5, language="ko",
                    configuration_id=1, generation=1)
                self.assertEqual(session.run_changes(), ())
                self.assertEqual(session.timer_seconds_for_step(0), 900)


class TheNotebookWritesSourceToActualTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary()
        self.env = environment()
        self.env.start()
        er._NARRATIVE_CACHE.clear()

    def tearDown(self) -> None:
        self.env.stop()
        er._NARRATIVE_CACHE.clear()
        self.temporary.cleanup()

    def change(self, label: str, kind: str, source: tuple[str, str, str], new: tuple[str, str], item: str) -> dict:
        from voiney_lab.start_check import RunChange as Change

        return Change(item, f"p-step-0{label}", label, kind, source[0], source[1], source[2], *new).public_dict()

    def run_record(self) -> RecordWriter:
        record = RecordWriter(self.temporary.name, session="session-easy")
        three = self.change("3", "time", ("15", "min", "15 min"), ("20", "min"), "p-step-03#2")
        record.event("00:30", "session_started", "1", payload={"run_changes": [three]})
        record.event("00:35", "step_completed", "1")
        two = self.change("2", "volume", ("500", "µL", "500 µL"), ("450", "µL"), "p-step-02#1")
        warm = self.change("2", "temperature", ("37", "°C", "37°C"), ("40", "°C"), "p-step-02#2")
        record.event("00:36", "run_value_changed", "2", payload={"step_record": {
            "kind": "run_value_changed", "step_id": "p-step-02", "step_label": "2", "change": two}})
        record.event("00:37", "run_value_changed", "2", payload={"step_record": {
            "kind": "run_value_changed", "step_id": "p-step-02", "step_label": "2", "change": warm}})
        record.event("00:38", "run_value_reverted", "2", payload={"step_record": {
            "kind": "run_value_reverted", "step_id": "p-step-02", "step_label": "2", "reverted": [warm]}})
        record.event("00:45", "step_completed", "2")
        record.event("01:05", "step_completed", "3")
        record.event("01:06", "step_completed", "4")
        record.event("01:07", "step_completed", "5")
        record.event("01:07", "workflow_completed", "5")
        record.finalize("01:07", "completed")
        return record

    def test_the_conditions_and_the_differences_write_source_then_todays_value(self) -> None:
        record = self.run_record()
        text = record.store.export_markdown(record.report_id, fixture=protocol_double()).decode()
        conditions = text.split("## 방법", 1)[1].split("## 관찰·특이사항", 1)[0]
        self.assertIn("양 500 µL → 450 µL (오늘 바꾼 값)", conditions)
        self.assertIn("시간 15 min → 20분 (오늘 바꾼 값)", conditions)
        self.assertNotIn("40°C", conditions)  # put back before the step was done
        differences = conditions.split("### 원문과 다르게 한 점·문제", 1)[1]
        self.assertIn("- 2단계 양: 원문 500 µL → 450 µL (오늘 바꾼 값)", differences)
        self.assertIn("- 3단계 시간: 원문 15 min → 20분 (오늘 바꾼 값)", differences)


class TheDurableRecordKeepsThemTests(unittest.TestCase):
    def test_a_recovery_reads_todays_values_from_the_workspace_events(self) -> None:
        first = RunChange("s1#1", "s1", "1", "time", "15", "min", "15 min", "20", "min").public_dict()
        second = RunChange("s2#1", "s2", "2", "volume", "10", "µL", "10 µL", "15", "µL").public_dict()
        state = {"events": [
            {"event_type": "protocol_started", "payload": {"run_changes": [first]}},
            {"event_type": "run_value_changed", "payload": {"step_record": {
                "kind": "run_value_changed", "change": second}}},
            {"event_type": "run_value_reverted", "payload": {"step_record": {
                "kind": "run_value_reverted", "reverted": [first]}}},
        ]}
        self.assertEqual([change.item_id for change in server._workspace_run_changes(state)], ["s2#1"])
        self.assertIsNone(server._workspace_run_changes({"events": []}))

    def test_a_change_reaches_the_report_and_the_workspace_as_its_own_event(self) -> None:
        session = timed_session()
        plan = session.plan("1단계 15분 대신 20분", turn_id=5, language="ko", configuration_id=1, generation=1)
        self.assertTrue(server._reportable(plan))


class TheStartAppliesTheChoiceTests(_CatalogCase):
    """The real socket: a start takes the person's choice once, a recovery its own."""

    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.principal = development_principal()

    def start_socket(self, run_change_set_id=None):
        placeholder = Path("/tmp/offline-easy")
        config = server.ServerConfig(placeholder, None, "test_only", frozenset({"ko", "en"}), "ko",
                                     placeholder, placeholder, placeholder)

        def catalog():
            store = initialize_protocol_store(self.settings)
            return ProtocolCatalog(store), store

        async def run():
            socket = _QueuedSocket()
            token = server._REQUEST_PRINCIPAL.set(self.principal)
            try:
                task = asyncio.create_task(server.voice_socket(socket))
                control = {"type": "session.start", "configuration_id": 1, "mode": "cascade",
                           "language": "ko", "protocol_id": self.protocol_id}
                if run_change_set_id is not None:
                    control["run_change_set_id"] = run_change_set_id
                socket.control(control)
                result = await socket.wait_for(lambda e: e["type"] in {
                    "session.ready", "session.configuration_required", "error"})
                socket.disconnect()
                await task
                return result, socket.sent
            finally:
                server._REQUEST_PRINCIPAL.reset(token)

        with patch.dict("os.environ", {
            "VOINEY_LAB_START_CHECK_ENABLED": "false",
            "VOINEY_LAB_WORKSPACE_ENABLED": "false", "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED": "false",
            "VOINEY_LAB_PROTOCOL_ENABLED": "true", "VOINEY_LAB_PROTOCOL_DATA_DIR": str(self.settings.data_dir),
            "VOINEY_LAB_USAGE_SCOPE": "demo",
        }), patch.object(server, "server_config", return_value=config), \
                patch.object(server, "_open_protocol_catalog", side_effect=catalog), \
                patch.object(server, "load_curated_protocol_fixture", return_value=self.fixture), \
                patch.object(server, "_candidate_fixture_execution_state",
                             return_value={"available_for_execution": True}), \
                patch.object(server, "_scope_catalog_resource"):
            return asyncio.run(run())

    def attached_state(self, sent) -> dict:
        states = [item for item in sent if item.get("type") == "protocol.fixture.state"]
        return states[-1]["state"] if states else {}

    def test_the_start_applies_the_choice_and_uses_it_once(self) -> None:
        changes = RunChanges(self.catalog)
        options = changes.options(self.protocol_id)
        item = next(value["item_id"] for value in options["values"]
                    if value["step_label"] == "2" and value["kind"] == "time")
        chosen = changes.choose(self.protocol_id, [{"item_id": item, "value": "20분"}], target=options["target"],
                                actor_principal_id=self.principal.principal_id)
        # Decision of 2026-10-24: the start names the choice it uses.
        result, sent = self.start_socket(chosen["change_set_id"])
        self.assertEqual(result["type"], "session.ready")
        self.assertEqual([change["words_ko"] for change in self.attached_state(sent).get("run_changes", [])],
                         ["20분(오늘 바꾼 값 · 원문 10분)"])
        (applied,) = self.events(RUN_CHANGES_APPLIED_EVENT)
        self.assertTrue(applied["experiment_session_id"])
        # The same choice is not used twice; a start naming none (start check off) gets none.
        again, _ = self.start_socket(chosen["change_set_id"])
        self.assertEqual(again["reason"], "run_changes_stale")
        _, plain = self.start_socket()
        self.assertEqual(self.attached_state(plain).get("run_changes"), [])

    def test_a_choice_whose_value_is_no_longer_the_fixtures_refuses_the_start(self) -> None:
        stale = RunChange("s2#1", "s2", "2", "time", "99", "min", "99 min", "20", "min").public_dict()
        revision = self.catalog._latest_protocol_revision(self.protocol_id)  # noqa: SLF001
        self.store.append_event(STALE_ID, self.protocol_id, revision.revision_number, RUN_CHANGES_EVENT, {
            "change_set_id": STALE_ID, "actor_principal_id": self.principal.principal_id,
            "revision_id": self.fixture.revision_id, "changes": [stale], "target": {},
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        result, _ = self.start_socket(STALE_ID)
        self.assertEqual(result["type"], "session.configuration_required")
        self.assertEqual(result["reason"], "run_changes_stale")
        self.assertTrue(result["message_ko"])


class TheConfiguredFixturesStartTests(_CatalogCase):
    """The development fixture runs from its configured file, whose revision id
    ("fixture-…") is not the catalog's ("pdf-1-analysis-1") the wizard chose on;
    the start still finds the choice, and checks each value against the fixture."""

    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        self.fixture = self.catalog.load_executable_fixture(self.protocol_id)

    def open_catalog(self):
        opened = initialize_protocol_store(self.settings)
        return ProtocolCatalog(opened), opened

    def test_a_choice_on_the_catalogs_revision_reaches_the_configured_fixtures_start(self) -> None:
        changes = RunChanges(self.catalog)
        options = changes.options(self.protocol_id)
        item = next(value["item_id"] for value in options["values"] if value["kind"] == "time")
        chosen = changes.choose(self.protocol_id, [{"item_id": item, "value": "20분"}], target=options["target"],
                                actor_principal_id="person-a")
        configured = replace(self.fixture, revision_id="fixture-0123456789abcdef0123")
        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server, "_start_check_principal_id", return_value="person-a"), \
                patch.dict("os.environ", {"VOINEY_LAB_PROTOCOL_ENABLED": "true",
                                          "VOINEY_LAB_PROTOCOL_DATA_DIR": str(self.settings.data_dir)}):
            (set_id, found), refused = server._start_run_changes(
                self.protocol_id, configured.revision_id, configured, chosen["change_set_id"])
        self.assertIsNone(refused)
        self.assertIsNotNone(set_id)
        self.assertEqual([change.shown for change in found], ["20분"])


class TheRoutesTests(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))

    def open_catalog(self):
        opened = initialize_protocol_store(self.settings)
        return ProtocolCatalog(opened), opened

    def test_the_list_and_the_choice_through_the_routes(self) -> None:
        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server, "_scope_catalog_resource"), \
                patch.object(server, "_start_check_principal_id", return_value="person-a"):
            options = server.get_protocol_run_changes(self.protocol_id)
            item = next(value["item_id"] for value in options["values"] if value["kind"] == "time")
            chosen = server.post_protocol_run_changes(
                self.protocol_id, {"target": options["target"], "changes": [{"item_id": item, "value": "20분"}]})
            self.assertEqual(chosen["changes"][0]["shown_ko"], "20분")
            with self.assertRaises(server.HTTPException) as caught:
                server.post_protocol_run_changes(
                    self.protocol_id, {"target": options["target"],
                                       "changes": [{"item_id": item, "value": "40도"}]})
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail["code"], "run_change_unit_kind")
        self.assertIn("단위 종류가 달라요", caught.exception.detail["message_ko"])


if __name__ == "__main__":
    unittest.main()
