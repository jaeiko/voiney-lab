"""A quantity or concentration asked with no target is a front rule (lane M1, decision 5a).

Decision of 2026-10-04: "얼마나 넣어?" and "농도가 어떻게 돼?" name no
substance. When the current step's source gives a value to two or more
substances the server asks back, "이 단계에는 A와 B가 있어요. 어느 쪽을
말씀하세요?". Nothing changes, and no model is asked.

Lane XO, decision 9 (2026-10-05): the candidates are the current step's and
the next step's source values. One step with one value is said as a sentence
("지금 4단계에서는 trypsin solution을 25uL 넣어요."); two steps with values
are both said and the researcher is asked which. Values and units are the
source's own; a value that is not an amount added is read as the source
words it.
"""

from __future__ import annotations

import asyncio
import unittest

from tests.protocol_vocabulary_support import SOURCE_PDF, build_fixture, in_gel_fixture
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    CuratedProtocolAction,
    CuratedProtocolSession,
)
from voiney_lab.llm_router import LlmRouterSettings, route_turn_with_llm_router
from voiney_lab.runtime_routing import route_curated_runtime_turn

STEPS = (
    "1 Wash the column with 500 µL of Buffer 1 and spin it for 1 minute.",
    "2 Prepare a solution of 1.5mg/mL of DTT and 10mg/mL of iodoacetamide.",
    "3 Remove and discard the supernatant.",
    "4 Add 25uL of the above trypsin solution and wait for 10 minutes.",
    "5 Store the tube at 4 degrees.",
)
MATERIALS = (
    "DTT Sigma-Aldrich Catalog #D0632",
    "Iodoacetamide Sigma-Aldrich Catalog #I1149",
    "Trypsin Promega Catalog #V5113",
)
QUANTITY_QUESTIONS = ("얼마나 넣어?", "얼마나 넣어야 돼?", "몇 µL 넣어?", "how much do I add?")
CONCENTRATION_QUESTIONS = ("농도가 어떻게 돼?", "농도는?", "what is the concentration?")


def _fixture():
    return build_fixture(
        protocol_id="quantity-target-test",
        title="Fictional quantity protocol",
        steps=STEPS,
        materials=MATERIALS,
    )


def _session(step_index: int, fixture=None) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture or _fixture())
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = step_index
    return session


def _front(session, said, turn_id=2, language="ko"):
    return session.front_plan(
        said, turn_id=turn_id, language=language, configuration_id=1, generation=1,
    )


def _projection(session):
    return (
        session.active, session.current_index, session.workflow_status,
        session.state()["revision"],
    )


class QuantityTargetFrontRuleTests(unittest.TestCase):
    def test_two_substances_with_values_are_asked_back(self) -> None:
        self.assertIn("quantity_target", FRONT_RULES)
        for said in (*QUANTITY_QUESTIONS[:3], *CONCENTRATION_QUESTIONS[:2]):
            with self.subTest(said=said):
                session = _session(1)
                before = _projection(session)
                plan = _front(session, said)
                self.assertIsNotNone(plan, "handed on to the model")
                self.assertEqual(session.last_front_rule, "quantity_target")
                self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_PARAMETER)
                self.assertEqual(
                    plan.speech_text,
                    "이 단계에는 DTT와 iodoacetamide가 있어요. 어느 쪽을 말씀하세요?",
                )
                self.assertEqual(plan.requested_entities, ("DTT", "iodoacetamide"))
                self.assertFalse(plan.state_changed)
                self.assertEqual(_projection(session), before)

    def test_one_substance_with_a_value_is_said_as_a_sentence(self) -> None:
        # Step 4 gives trypsin a volume and step 5 gives nothing.
        for said in QUANTITY_QUESTIONS[:2]:
            with self.subTest(said=said):
                session = _session(3)
                before = _projection(session)
                plan = _front(session, said)
                self.assertIsNotNone(plan)
                self.assertEqual(session.last_front_rule, "quantity_target")
                self.assertIs(plan.action, CuratedProtocolAction.QUESTION)
                self.assertEqual(plan.speech_text, "지금 4단계에서는 trypsin solution을 25uL 넣어요.")
                self.assertIn("25uL", STEPS[3])
                self.assertEqual(plan.evidence_ids, ("current_step",))
                self.assertFalse(plan.state_changed)
                self.assertEqual(_projection(session), before)

    def test_the_current_and_the_next_step_are_both_said(self) -> None:
        # Step 1 gives Buffer 1 a volume; step 2 gives DTT and iodoacetamide one each.
        session = _session(0)
        before = _projection(session)
        plan = _front(session, "얼마나 넣어?")
        self.assertEqual(session.last_front_rule, "quantity_target")
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_PARAMETER)
        self.assertEqual(
            plan.speech_text,
            # Lane R6, decision 2: the next step's values are said with them.
            "지금 1단계라면 Buffer 1 500 µL, 다음 2단계에는 DTT(1.5mg/mL)와 "
            "iodoacetamide(10mg/mL)가 있어요. "
            "어느 쪽인지 말씀해 주세요.",
        )
        self.assertEqual(plan.source_pages, (1, 1))
        self.assertEqual(_projection(session), before)

    def test_only_the_next_step_having_a_value_says_the_next_step(self) -> None:
        plan = _front(_session(2), "얼마나 넣어?")
        self.assertEqual(plan.speech_text, "다음 4단계에서는 trypsin solution을 25uL 넣어요.")
        self.assertEqual(plan.evidence_ids, ())

    def test_english_is_answered_in_english(self) -> None:
        session = _session(3)
        plan = _front(session, "how much do I add?", language="en")
        self.assertEqual(plan.speech_text, "At step 4, add 25uL of trypsin solution.")
        plan = _front(_session(1), "what is the concentration?", language="en")
        self.assertEqual(
            plan.speech_text, "This step has DTT and iodoacetamide. Which one do you mean?",
        )

    def test_a_concentration_question_reads_only_concentrations(self) -> None:
        # Step 1 gives a volume and no concentration; step 2 gives two.
        plan = _front(_session(0), "농도가 어떻게 돼?")
        self.assertEqual(
            plan.speech_text,
            "다음 2단계에는 DTT(1.5mg/mL)와 iodoacetamide(10mg/mL)가 있어요. 어느 쪽을 말씀하세요?",
        )
        # Step 4 gives a volume, step 5 nothing: handed on.
        self.assertIsNone(_front(_session(3), "농도가 어떻게 돼?"))

    def test_a_step_without_values_is_handed_on(self) -> None:
        # Step 5, the last, has no value and no next step.
        for said in ("얼마나 넣어?", "농도가 어떻게 돼?"):
            with self.subTest(said=said):
                session = _session(4)
                self.assertIsNone(_front(session, said))
                self.assertIsNone(session.last_front_rule)

    def test_candidates_come_only_from_the_current_and_the_next_step(self) -> None:
        # Step 4's source names trypsin only; DTT and iodoacetamide (step 2)
        # are never offered there.
        plan = _front(_session(3), "얼마나 넣어?")
        self.assertNotIn("DTT", plan.speech_text)
        self.assertNotIn("iodoacetamide", plan.speech_text)

    def test_a_named_target_or_a_reference_is_not_this_rule(self) -> None:
        for said in ("DTT 얼마나 넣어?", "그거 얼마나 넣어?", "타이머 얼마나 남았어?"):
            with self.subTest(said=said):
                session = _session(1)
                _front(session, said)
                self.assertNotEqual(session.last_front_rule, "quantity_target")

    def test_not_while_paused_or_before_the_start(self) -> None:
        session = _session(1)
        session.plan("일시정지", turn_id=2, language="ko", configuration_id=1, generation=1)
        _front(session, "얼마나 넣어?", turn_id=3)
        self.assertNotEqual(session.last_front_rule, "quantity_target")
        idle = CuratedProtocolSession(_fixture())
        idle.activate_configured()
        _front(idle, "얼마나 넣어?")
        self.assertNotEqual(idle.last_front_rule, "quantity_target")

    def test_the_rules_path_gives_the_same_plan(self) -> None:
        session = _session(1)
        plan = route_curated_runtime_turn(
            session, "얼마나 넣어?", turn_id=2, language="ko",
            configuration_id=1, generation=1,
        ).plan
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_PARAMETER)
        self.assertEqual(session.last_front_rule, "quantity_target")

    def test_the_router_never_asks_the_model(self) -> None:
        session = _session(1)

        def no_model():
            raise AssertionError("the model was asked")

        async def rules():
            raise AssertionError("the rules' fallback was used")

        outcome = asyncio.run(route_turn_with_llm_router(
            session, "얼마나 넣어?", turn_id=2, language="ko",
            settings=LlmRouterSettings(enabled=True), client_factory=no_model,
            rule_route=rules, configuration_id=1, generation=1,
        ))
        self.assertEqual(outcome.handled_by, "front:quantity_target")
        self.assertFalse(outcome.model_called)


@unittest.skipUnless(SOURCE_PDF.is_file(), "needs the licensed in-gel source PDF")
class InGelQuantityTargetTests(unittest.TestCase):
    def test_step_10_asks_which_solution(self) -> None:
        for said in ("얼마나 넣어?", "농도가 어떻게 돼?"):
            with self.subTest(said=said):
                plan = _front(_session(9, in_gel_fixture()), said)
                self.assertEqual(
                    plan.speech_text,
                    "이 단계에는 DTT, iodoacetamide와 AMBIC이 있어요. 어느 쪽을 말씀하세요?",
                )

    def test_step_3_says_the_volume_of_solution_a(self) -> None:
        plan = _front(_session(2, in_gel_fixture()), "얼마나 넣어?")
        self.assertEqual(plan.speech_text, "지금 3단계에서는 solution A를 500 µL 넣어요.")

    def test_step_1_keeps_approximately_and_names_the_next_step(self) -> None:
        plan = _front(_session(0, in_gel_fixture()), "얼마나 넣어?")
        self.assertEqual(
            plan.speech_text,
            # Lane R6, decision 2: the solutions step 2 makes, with the
            # source's own numbers.
            "지금 1단계라면 25mM AMBIC 약 200 µL, 다음 2단계는 Solution A(25mM AMBIC 2 : "
            "acetonitrile 1)와 Solution B(25mM AMBIC)를 만들어요. 어느 쪽인지 말씀해 주세요.",
        )

    def test_a_ratio_is_read_as_the_source_words_it(self) -> None:
        plan = _front(_session(23, in_gel_fixture()), "얼마나 넣어?")
        self.assertEqual(
            plan.speech_text, "지금 24단계 원문에는 'formic acid (FA, 10% v/v)'라고 되어 있어요.",
        )


if __name__ == "__main__":
    unittest.main()
