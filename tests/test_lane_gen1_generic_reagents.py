"""GEN1: what in-gel's tables gave one PDF, read off any protocol's own text.

Labelled reagents ("Buffer 2", "Reagent A"), a mixture stated in parts, a
labelled reagent an action handles, the "which one?" question when a step
names two, an instrument that stopped, and a research plan's wording were
tables written for the in-gel development document (Solution A/B, its AMBIC
ratio, thermomixer/speedvac, a hand-written reagent sheet). These protocols
are fictional, a chemistry one and a materials one, so nothing in-gel wrote
can answer them.
"""

from __future__ import annotations

import unittest

from scripts.sim.runner import snapshot
from tests.protocol_vocabulary_support import build_fixture
from voiney_lab.curated_protocol import (
    ClaimTargetType,
    CuratedProtocolAction,
    CuratedProtocolSession,
    hide_internal_names,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

CHEMISTRY = build_fixture(
    protocol_id="fictional-acetate-buffer-v1",
    title="Acetate buffer extraction",
    steps=(
        "1 Weigh the dried sample into a 50 mL vial.",
        "2 Prepare Buffer 2: 3 parts of 0.1 M sodium acetate buffer (SAB) mixed with 1 part ethanol.",
        "3 Add 10 mL of Buffer 2 to the vial and shake for 30 min.",
        "4 Remove and discard Buffer 2 from the vial.",
        "5 Evaporate the extract on the rotavap at 40 °C.",
    ),
    materials=("sodium acetate buffer", "ethanol"),
    equipment=("rotavap",),
)
MATERIALS = build_fixture(
    protocol_id="fictional-epoxy-film-v1",
    title="Epoxy film casting",
    steps=(
        "1 Clean the glass slide with isopropanol.",
        "2 Prepare Reagent A and Reagent B as supplied.",
        "3 Mix Reagent A with Reagent B and degas for 5 min.",
        "4 Cast the film and cure at 60 °C for 2 h.",
    ),
    materials=("Reagent A", "Reagent B", "isopropanol"),
    equipment=("vacuum oven",),
)


def _at(fixture, label: str) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture)
    session.active = True
    session._workflow_status = "running"
    session.current_index = next(
        index for index, step in enumerate(fixture.steps) if step.source_label == label)
    return session


def _turn(session: CuratedProtocolSession, words: str):
    return route_curated_runtime_turn(
        session, words, turn_id=1, language="ko", configuration_id=1, generation=1).plan


class GenericReagentTests(unittest.TestCase):
    def test_a_mixture_stated_in_parts_is_answered_in_the_source_words(self) -> None:
        session = _at(CHEMISTRY, "2")
        before = snapshot(session)
        plan = session.plan("Buffer 2 비율이 몇 대 몇이야?", turn_id=1, language="ko")
        self.assertTrue(any(c.target_type is ClaimTargetType.RATIO for c in plan.claim_requests))
        self.assertIn("Buffer 2의 비율은 0.1 M SAB 3 parts 대 ethanol 1 part입니다.", plan.primary_text or "")
        self.assertEqual(snapshot(session), before)

    def test_the_labelled_reagent_an_action_discards_is_named(self) -> None:
        session = _at(CHEMISTRY, "4")
        plan = session.plan("이 용액 폐기는 어떻게 해?", turn_id=1, language="ko")
        self.assertTrue(any(c.target_id == "buffer_2" for c in plan.claim_requests))
        self.assertIn("Buffer 2", plan.primary_text or "")
        self.assertNotIn("buffer_2", plan.primary_text or "")

    def test_two_labelled_reagents_in_a_step_are_asked_back(self) -> None:
        session = _at(MATERIALS, "3")
        before = snapshot(session)
        plan = session.plan("그 용액 어떻게 만들어?", turn_id=1, language="ko")
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_REFERENCE)
        self.assertIn("Reagent A와 Reagent B 중 어느 용액을 말씀하시나요?", plan.display_text)
        self.assertEqual(snapshot(session)["step"], before["step"])

    def test_an_instrument_that_stopped_is_recorded(self) -> None:
        session = _at(CHEMISTRY, "5")
        plan = _turn(session, "rotavap이 멈췄어")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertEqual(plan.anomaly_category, "equipment_issue")

    def test_a_research_plan_is_made_from_the_protocol(self) -> None:
        session = _at(CHEMISTRY, "2")
        plan = session.canonical_research_plan("SAB")
        self.assertEqual(plan["requested_label"], "SAB")
        self.assertEqual(plan["canonical_name"], "sodium acetate buffer")
        self.assertIn("(SAB)", plan["protocol_relation"])
        self.assertTrue(all("sodium acetate buffer" in query for query in plan["query_variants"]))

    def test_how_a_labelled_reagent_is_prepared_is_its_own_sentence(self) -> None:
        session = _at(CHEMISTRY, "2")
        plan = session.plan("버퍼 2는 어떻게 준비해", turn_id=1, language="ko")
        self.assertEqual(plan.intent_kind, "current_protocol_fact")
        self.assertEqual([fact.fact_id for fact in plan.facts], ["current_step"])
        self.assertIn("Prepare Buffer 2", plan.display_text)

    def test_a_labelled_reagent_key_is_put_in_words(self) -> None:
        self.assertEqual(hide_internal_names("buffer_2를 넣어요."), "버퍼 2를 넣어요.")
        self.assertEqual(hide_internal_names("Add reagent_a.", "en"), "Add reagent A.")


if __name__ == "__main__":
    unittest.main()
