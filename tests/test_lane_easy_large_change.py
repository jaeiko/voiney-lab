"""A large change is asked first (lane EASY, human decision 2026-10-24, item 1).

A value said aloud follows confirm_mode as before (readback applies at once
and reads back -- a question slows the bench), except that a new value below
half or above twice the source's is asked about once before it applies,
whatever confirm_mode says ("5단계 온도를 37°C에서 14°C로 바꿀까요?"), for time,
temperature, speed, volume and concentration alike. A value chosen in the
start wizard is confirmed once more on the screen by the same rule: the
server refuses it until the screen sends that confirmation.
"""


from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.identity_support import development_principal
from tests.lane_ra_support import agitation_fixture, session_at
from tests.test_lane_easy_run_changes_screen import CHANGES
from tests.test_lane_on_wizard_screen import run
from tests.test_lane_rv_review_pending import _CatalogCase
from tests.test_voice_pause_resume_persistence import _QueuedSocket
from voiney_lab import server
from voiney_lab.curated_protocol import CuratedProtocolAction
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol import ProtocolError, parse_control
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.start_check import (
    RUN_CHANGES_APPLIED_EVENT,
    RunChangeError,
    RunChanges,
    StartCheck,
    check_run_change,
    run_change_is_large,
    step_changeable_values,
)


def timed_session(confirm_mode: str = "readback"):
    fixture = agitation_fixture()
    fixture = replace(fixture, timer_manifest={fixture.steps[0].step_id: 900})
    session = session_at(fixture, step_index=0)
    session.confirm_mode = confirm_mode
    return session


class TheLargeChangeRuleTests(unittest.TestCase):
    def test_below_half_or_above_twice_is_large_for_every_kind(self) -> None:
        values = step_changeable_values(
            "s1", "1", "1. Add 100 µL of 25 mM AMBIC at 37 °C for 15 min, spin at 800 rpm.")
        volume, molar, temperature, time, speed = values
        for value, number, unit, large in (
            (time, "31", "min", True), (time, "30", "min", False), (time, "7", "min", True),
            (time, "7.5", "min", False), (time, "1", "h", True), (time, "20", "min", False),
            (temperature, "14", "°C", True), (temperature, "40", "°C", False),
            (temperature, "75", "°C", True), (speed, "1700", "rpm", True), (speed, "1000", "rpm", False),
            (volume, "250", "µL", True), (volume, "0.15", "mL", False), (molar, "10", "mM", True),
        ):
            with self.subTest(value=value.shown, new=f"{number} {unit}"):
                self.assertEqual(run_change_is_large(check_run_change(value, number, unit)), large)

    def test_a_temperature_at_or_below_zero_is_always_asked(self) -> None:
        (cold,) = step_changeable_values("s1", "1", "1. Store at -20 °C.")
        self.assertTrue(run_change_is_large(check_run_change(cold, "-18", "°C")))
        (cool,) = step_changeable_values("s1", "1", "1. Keep at 4 °C.")
        self.assertTrue(run_change_is_large(check_run_change(cool, "0", "°C")))


class SaidAloudTests(unittest.TestCase):
    def say(self, session, words: str, turn_id: int):
        return session.plan(words, turn_id=turn_id, language="ko", configuration_id=1, generation=1)

    def test_readback_still_applies_a_close_value_at_once(self) -> None:
        session = timed_session("readback")
        plan = self.say(session, "1단계 온도 40도로 바꿔 줘", 5)
        self.assertEqual(plan.action, CuratedProtocolAction.CHANGE_VALUE)
        self.assertEqual([change.shown for change in session.run_changes()], ["40°C"])

    def test_a_large_value_is_asked_first_whatever_the_mode(self) -> None:
        for mode in ("readback", "quiet", "confirm"):
            with self.subTest(mode=mode):
                session = timed_session(mode)
                asked = self.say(session, "1단계 온도 14도로 바꿔 줘", 5)
                self.assertFalse(asked.state_changed)
                self.assertEqual(asked.intent_kind, "run_value_change_confirmation_required")
                self.assertEqual(
                    asked.display_text,
                    "1단계 온도를 37°C에서 14°C로 바꿀까요? 원문 값과 많이 달라요. 맞으면 '네'라고 해 주세요.")
                self.assertIn("삼십칠 도에서 십사 도로", asked.speech_text)
                self.assertEqual(session.run_changes(), ())
                done = self.say(session, "네", 6)
                self.assertEqual(done.action, CuratedProtocolAction.CHANGE_VALUE)
                self.assertTrue(done.step_record["change"]["large"])
                self.assertEqual([change.shown for change in session.run_changes()], ["14°C"])

    def test_a_no_to_a_large_value_changes_nothing(self) -> None:
        session = timed_session("readback")
        self.say(session, "1단계 15분 대신 1시간으로 할게", 5)
        declined = self.say(session, "아니요", 6)
        self.assertEqual(declined.speech_text, "바꾸지 않았어요.")
        self.assertEqual(session.run_changes(), ())
        self.assertEqual(session.timer_seconds_for_step(0), 900)


class TheWizardConfirmsALargeValueTests(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(pages=(1, 2, 3)))
        self.changes = RunChanges(self.catalog)
        self.options = self.changes.options(self.protocol_id)
        self.item = next(value["item_id"] for value in self.options["values"]
                         if value["step_label"] == "2" and value["kind"] == "time")

    def test_a_large_value_is_refused_until_it_is_confirmed(self) -> None:
        with self.assertRaises(RunChangeError) as caught:
            self.changes.choose(self.protocol_id, [{"item_id": self.item, "value": "30분"}],
                                target=self.options["target"], actor_principal_id="person-a")
        self.assertEqual(caught.exception.code, "run_change_large_unconfirmed")
        (asked,) = caught.exception.large
        self.assertEqual(asked["item_id"], self.item)
        self.assertEqual(asked["question_ko"], "2단계 시간을 10분에서 30분으로 바꿀까요? 원문 값과 많이 달라요.")
        self.assertEqual(self.events("protocol_run_changes_chosen"), [])
        chosen = self.changes.choose(
            self.protocol_id, [{"item_id": self.item, "value": "30분"}], target=self.options["target"],
            actor_principal_id="person-a", confirm_large=[self.item])
        (event,) = self.events("protocol_run_changes_chosen")
        self.assertEqual(event["confirmed_large"], [self.item])
        self.assertEqual(chosen["changes"][0]["shown_ko"], "30분")

    def test_a_close_value_needs_no_second_look(self) -> None:
        chosen = self.changes.choose(self.protocol_id, [{"item_id": self.item, "value": "15분"}],
                                     target=self.options["target"], actor_principal_id="person-a")
        self.assertEqual(chosen["changes"][0]["shown_ko"], "15분")

    def test_the_route_sends_the_question_with_its_code(self) -> None:
        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog), \
                patch.object(server, "_scope_catalog_resource"), \
                patch.object(server, "_start_check_principal_id", return_value="person-a"), \
                self.assertRaises(server.HTTPException) as caught:
            server.post_protocol_run_changes(self.protocol_id, {
                "target": self.options["target"], "changes": [{"item_id": self.item, "value": "30분"}]})
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail["code"], "run_change_large_unconfirmed")
        self.assertEqual(caught.exception.detail["large"][0]["item_id"], self.item)


class TheScreenConfirmsALargeValueTests(unittest.TestCase):
    def test_a_large_typed_value_is_confirmed_on_the_screen_then_sent_again(self) -> None:
        result = run(CHANGES + r"""
await toChanges();
const box=changeInput("s2#1");box.value="30분";box.dispatch("input");await settle();
const question="2단계 시간을 10분에서 30분으로 바꿀까요? 원문 값과 많이 달라요.";
choiceAnswer=call=>Array.isArray(call.body.confirm_large)&&call.body.confirm_large.includes("s2#1")?{...KEPT,change_set_id:"run-changes-9"}:json({detail:{code:"run_change_large_unconfirmed",message_ko:question,large:[{item_id:"s2#1",question_ko:question}]}},false);
primary().dispatch("click");await settle(8);
assert(wizard.step==="changes",`step: ${wizard.step}`);
const text=walk(body()).map(el=>el.textContent).join(" ");
assert(text.includes(question),text);
const yes=walk(body()).find(el=>el.id==="wizard-changes-large-yes");
assert(yes&&yes.textContent==="네, 이 값으로 할게요","no confirm button");
yes.dispatch("click");await settle(8);
const sent=posts().filter(call=>call.url.endsWith("/start-check/changes"));
assert(sent.length===2&&JSON.stringify(sent[1].body.confirm_large)===JSON.stringify(["s2#1"]),JSON.stringify(sent.map(c=>c.body)));
assert(wizard.step==="start"&&wizard.chosenChanges.changes.length===1,`${wizard.step} ${JSON.stringify(wizard.chosenChanges)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
