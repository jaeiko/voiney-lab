"""Lane VT, decision 3 (2026-10-09): "그거" asked for a value, with nothing it names.

"그거 얼마나 넣어?", "그거 몇 분이야?", "그거 몇 도야?" -- an amount, a time or
a temperature asked of "그거" (이거, 저거) with nothing recent it points at --
were asked back with no help ("어떤 물질이나 용액을 말씀하시는지 …"), and "그거 몇
분이야?" was taken for the step's time with no word of what it was. Now the
current step's values of that kind are counted, the source's own:

* one: said with what it belongs to -- "이 단계의 solution A는 500 µL예요.";
* two or three: one question to pick -- "Buffer A요, Buffer B요?" -- and the
  next words pick one, whose value is then said;
* more: "화면의 목록에서 골라 주세요" with the list on the screen;
* none: "지금 단계에는 넣는 양이 없어요. 어떤 것을 말씀하시나요?".

A question that names its target ("Buffer A 얼마나 넣어?", "BSA 몇 mL?") goes
the way it went, and so does a "그거" the last turn named. It is a front rule
(professor's 10/2 note: a vague reference is the rules' to ask back), and no
workflow state changes.
"""

from __future__ import annotations

import dataclasses
import unittest

from tests.lane_cb_support import Turns
from tests.lane_n_support import notes_fixture
from tests.protocol_vocabulary_support import SOURCE_PDF, build_fixture, in_gel_fixture
from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession, vague_value_question

STEPS = (
    "1 Add 20 µL of Buffer A and 30 µL of Buffer B to the tube.",
    "2 Incubate the tube at 37 °C for 10 min, then heat it at 95 °C for 5 min.",
    "3 Add 1 µL of Enzyme A, 2 µL of Enzyme B, 3 µL of Enzyme C and 4 µL of Enzyme D.",
    "4 Leave the plate at room temperature.",
    "5 Record the result.",
)


def mixed_fixture():
    base = build_fixture(
        protocol_id="lane-vt-mixed", title="Fictional mix", steps=STEPS,
        materials=("Buffer A", "Buffer B", "Enzyme A", "Enzyme B", "Enzyme C", "Enzyme D"),
    )
    table = domain.StepTimerTable(verified=(
        domain.VerifiedStepTimer("step-2", "2", None, "for 10 min", "10 min", (600,), 1),
        domain.VerifiedStepTimer("step-2", "2", None, "for 5 min", "5 min", (300,), 1),
    ))
    return dataclasses.replace(
        base, timer_table=table,
        timer_choices={"step-2": (domain.TimerChoice(600, "10 min", "for 10 min"),
                                  domain.TimerChoice(300, "5 min", "for 5 min"))},
    )


class WordsTests(unittest.TestCase):

    def test_the_three_kinds(self) -> None:
        for said, kind in (
            ("그거 얼마나 넣어?", "amount"), ("그건 얼마나 넣어야 돼?", "amount"),
            ("이거 몇 µl 넣어?", "amount"), ("그거 양이 얼마야?", "amount"),
            ("그거 몇 분이야?", "time"), ("그거 몇 시간 돌려?", "time"), ("이거 얼마나 걸려?", "time"),
            ("그거 몇 도야?", "temperature"), ("이거 몇 도에서 하는 거여?", "temperature"),
            ("그거 온도가 어떻게 돼?", "temperature"),
        ):
            with self.subTest(said=said):
                self.assertEqual(vague_value_question(said)[0], kind)

    def test_other_words_are_not_this(self) -> None:
        for said in ("얼마나 넣어?", "몇 분이야?", "그거 뭐야", "아까 그거 다시 알려줘",
                     "그거 몇 분 남았어?", "그거 농도가 어떻게 돼?", "BSA 몇 mL?"):
            with self.subTest(said=said):
                self.assertIsNone(vague_value_question(said))


class _Rule(Turns):

    def ask(self, said: str, *, front: bool = False):
        before = self.projection()
        plan = self.say(said, front=front)
        self.assertEqual(self.projection(), before, "a state changed")
        self.assertFalse(plan.state_changed)
        return plan


class OneValueTests(_Rule, unittest.TestCase):

    def test_one_amount_is_said_with_its_substance(self) -> None:
        self.open(1, fixture=notes_fixture())
        plan = self.ask("그거 얼마나 넣어?")
        self.assertEqual(self.session.last_front_rule, "reference_value")
        self.assertIs(plan.action, CuratedProtocolAction.QUESTION)
        self.assertEqual(plan.display_text, "이 단계의 Solution A는 500 µL예요.")
        self.assertEqual(plan.speech_text, "이 단계의 Solution A는 오백 마이크로리터예요.")
        self.assertEqual(plan.evidence_ids, ("current_step",))

    def test_one_time_is_said_with_what_it_is_for(self) -> None:
        self.open(1, fixture=notes_fixture())
        plan = self.ask("그거 몇 분이야?")
        self.assertEqual(plan.display_text, "이 단계의 세척 시간은 10분이에요.")

    def test_one_temperature_and_room_temperature(self) -> None:
        self.open(3, fixture=mixed_fixture())
        plan = self.ask("그거 몇 도야?")
        self.assertEqual(plan.display_text, "이 단계의 두는 온도는 실온이에요.")

    def test_none_of_the_kind_is_said_so_and_asked(self) -> None:
        self.open(2, fixture=notes_fixture())
        for said, words in (
            ("그거 얼마나 넣어?", "지금 단계에는 넣는 양이 없어요. 어떤 것을 말씀하시나요?"),
            ("그거 몇 분이야?", "지금 단계에는 정해진 시간이 없어요. 어떤 것을 말씀하시나요?"),
            ("그거 몇 도야?", "지금 단계에는 정해진 온도가 없어요. 어떤 것을 말씀하시나요?"),
        ):
            with self.subTest(said=said):
                plan = self.ask(said)
                self.assertEqual(plan.display_text, words)
                self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_PARAMETER)
                self.assertEqual(self.session.last_front_rule, "reference_value")

    def test_the_front_rule_plans_it_as_the_rules_do(self) -> None:
        self.open(1, fixture=notes_fixture())
        front = self.ask("그거 얼마나 넣어?", front=True)
        self.assertEqual(front.display_text, "이 단계의 Solution A는 500 µL예요.")
        self.assertEqual(self.session.last_front_rule, "reference_value")


class ChoiceTests(_Rule, unittest.TestCase):

    def test_two_are_asked_and_the_next_words_pick_one(self) -> None:
        self.open(0, fixture=mixed_fixture())
        plan = self.ask("그거 얼마나 넣어?")
        self.assertEqual(plan.display_text, "Buffer A요, Buffer B요?")
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_PARAMETER)
        picked = self.ask("버퍼 비")
        self.assertEqual(picked.display_text, "이 단계의 Buffer B는 30 µL예요.")
        self.assertEqual(self.session.last_front_rule, "reference_value")
        self.ask("그거 얼마나 넣어?")
        self.assertEqual(self.ask("첫 번째").display_text, "이 단계의 Buffer A는 20 µL예요.")

    def test_two_temperatures_and_two_times_by_what_they_are_for(self) -> None:
        self.open(1, fixture=mixed_fixture())
        self.assertEqual(self.ask("그거 몇 도야?").display_text, "배양 온도요, 가열 온도요?")
        self.assertEqual(self.ask("가열").display_text, "이 단계의 가열 온도는 95°C예요.")
        self.assertEqual(self.ask("그거 몇 분이야?").display_text, "배양 시간이요, 가열 시간이요?")
        self.assertEqual(self.ask("배양 시간").display_text, "이 단계의 배양 시간은 10분이에요.")

    def test_more_than_three_go_to_the_list_on_the_screen(self) -> None:
        self.open(2, fixture=mixed_fixture())
        plan = self.ask("그거 얼마나 넣어?")
        self.assertEqual(plan.speech_text, "이 단계에는 넣는 양이 4가지 있어요. 화면의 목록에서 골라 주세요.")
        self.assertIn("1. Enzyme A", plan.display_text)
        self.assertIn("4. Enzyme D", plan.display_text)
        self.assertEqual(self.ask("Enzyme C").display_text, "이 단계의 Enzyme C는 3 µL예요.")

    def test_only_the_next_words_pick(self) -> None:
        self.open(0, fixture=mixed_fixture())
        self.ask("그거 얼마나 넣어?")
        self.say("현재 단계 다시 알려줘")
        plan = self.say("Buffer A")
        self.assertNotEqual(plan.display_text, "이 단계의 Buffer A는 20 µL예요.")

    def test_words_that_pick_nothing_go_on_as_their_own_turn(self) -> None:
        self.open(0, fixture=mixed_fixture())
        self.ask("그거 얼마나 넣어?")
        plan = self.say("1단계 완료했어")
        self.assertTrue(plan.state_changed)
        self.assertEqual(self.session.current_index, 1)


class UnchangedTests(_Rule, unittest.TestCase):

    def test_a_named_target_goes_the_way_it_went(self) -> None:
        self.open(0, fixture=mixed_fixture())
        for said in ("Buffer A 얼마나 넣어?", "그거 Buffer A 얼마나 넣어?", "BSA 몇 mL?"):
            with self.subTest(said=said):
                self.say(said)
                self.assertNotEqual(self.session.last_front_rule, "reference_value")

    def test_other_references_are_asked_back_as_before(self) -> None:
        self.open(1, fixture=notes_fixture())
        for said in ("그거 뭐야", "아까 그거 다시 알려줘"):
            with self.subTest(said=said):
                self.say(said)
                self.assertEqual(self.session.last_front_rule, "coreference_clarify")

    def test_not_before_the_start_or_in_a_pause(self) -> None:
        session = CuratedProtocolSession(notes_fixture())
        session.activate_configured()
        session.plan("그거 얼마나 넣어?", turn_id=1, language="ko", configuration_id=1, generation=1)
        self.assertNotEqual(session.last_front_rule, "reference_value")
        self.open(1, fixture=notes_fixture())
        self.say("일시정지")
        self.say("그거 얼마나 넣어?")
        self.assertNotEqual(self.session.last_front_rule, "reference_value")


@unittest.skipUnless(SOURCE_PDF.exists(), "the licensed in-gel PDF is not present")
class InGelTests(_Rule, unittest.TestCase):

    def test_the_in_gel_steps(self) -> None:
        fixture = in_gel_fixture()
        for index, said, words in (
            (4, "그거 얼마나 넣어?", "이 단계의 solution B는 500 µL예요."),
            (4, "그거 몇 분이야?", "이 단계의 배양 시간은 15분이에요."),
            (2, "그거 몇 도야?", "이 단계의 배양 온도는 37°C예요."),
            (2, "이거 몇 도에서 하는 거여?", "이 단계의 배양 온도는 37°C예요."),
            (11, "그거 몇 분이야?", "이 단계의 배양 시간은 1시간이에요."),
            (7, "그거 몇 도야?", "이 단계의 세척 온도는 22°C예요."),
            (9, "그거 얼마나 넣어?", "DTT요, iodoacetamide요, AMBIC이요?"),
        ):
            with self.subTest(step=index + 1, said=said):
                self.open(index, fixture=fixture)
                self.assertEqual(self.ask(said).display_text, words)


if __name__ == "__main__":
    unittest.main()
