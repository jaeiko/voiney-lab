"""A quantity or concentration asked with no target is a front rule (lane M1, decision 5a).

Decision of 2026-10-04: "얼마나 넣어?" and "농도가 어떻게 돼?" name no
substance. When the current step's source gives a value to two or more
substances the server asks back, "이 단계에는 A와 B가 있어요. 어느 쪽을
말씀하세요?"; when it gives one, the server says that value as the source
words it. The candidates come only from the current step's own source text.
Nothing changes, and no model is asked.
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

    def test_one_substance_with_a_value_is_answered_as_the_source_words_it(self) -> None:
        for index, phrase in ((0, "500 µL of Buffer 1"), (3, "25uL of the above trypsin solution")):
            for said in QUANTITY_QUESTIONS[:2]:
                with self.subTest(step=index + 1, said=said):
                    session = _session(index)
                    before = _projection(session)
                    plan = _front(session, said)
                    self.assertIsNotNone(plan)
                    self.assertEqual(session.last_front_rule, "quantity_target")
                    self.assertIs(plan.action, CuratedProtocolAction.QUESTION)
                    self.assertEqual(plan.speech_text, f"{index + 1}단계 원문: {phrase}")
                    self.assertIn(phrase, STEPS[index])
                    self.assertEqual(plan.evidence_ids, ("current_step",))
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(_projection(session), before)

    def test_english_is_answered_in_english(self) -> None:
        session = _session(0)
        plan = _front(session, "how much do I add?", language="en")
        self.assertEqual(plan.speech_text, "Step 1 source: 500 µL of Buffer 1")
        plan = _front(_session(1), "what is the concentration?", language="en")
        self.assertEqual(
            plan.speech_text, "This step has DTT and iodoacetamide. Which one do you mean?",
        )

    def test_a_concentration_question_reads_only_concentrations(self) -> None:
        # Step 1 gives a volume and no concentration: nothing to say, so the
        # turn is handed on exactly as before.
        session = _session(0)
        self.assertIsNone(_front(session, "농도가 어떻게 돼?"))

    def test_a_step_without_values_is_handed_on(self) -> None:
        for said in ("얼마나 넣어?", "농도가 어떻게 돼?"):
            with self.subTest(said=said):
                session = _session(2)
                self.assertIsNone(_front(session, said))
                self.assertIsNone(session.last_front_rule)

    def test_candidates_come_only_from_the_current_step(self) -> None:
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
                    "이 단계에는 DTT, iodoacetamide와 AMBIC가 있어요. 어느 쪽을 말씀하세요?",
                )

    def test_step_3_says_the_volume_of_solution_a(self) -> None:
        plan = _front(_session(2, in_gel_fixture()), "얼마나 넣어?")
        self.assertEqual(plan.speech_text, "3단계 원문: 500 µL of solution A")


if __name__ == "__main__":
    unittest.main()
