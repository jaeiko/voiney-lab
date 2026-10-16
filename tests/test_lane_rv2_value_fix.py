"""A fixed value is asked again where the run says it (lane RV2, decision d).

Lane RV, judgement 11: the run speaks the step sentence and its timer, not
the value record, so fixing a value could leave the step saying the old one.
Human decision 2026-10-16: when a person fixes a value, every item that
states the old value -- the step sentence, its sub-actions, the step's timer;
for an OCR value every item on its page that states it -- waits for a person
again. The server proposes the sentence with the new value in the old one's
place when the old value stands in it exactly once; otherwise a person writes
it. Nothing registers until each is decided. A safety statement is never
linked: its words never change.
"""

from __future__ import annotations

import json
import unittest

from tests.test_lane_rv2_auto_resolve import SCAN_TEXT, _OcrCase, _TwoEngineOcr, delta_answer
from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab.protocol_catalog import ProtocolReviewItemsPendingError

STEP_TWO = "2. Incubate for 10 min at 37 C."


def with_values(answer: str, *, step_two: str = STEP_TWO, warning: str | None = None) -> str:
    """Step 2 with a sub-action that states its temperature and its time."""

    payload = json.loads(answer)
    ev = {"source_page_number": 1, "source_excerpt": step_two}
    step = payload["protocol"]["sections"][0]["steps"][1]
    step.update(instruction_source_text=step_two, evidence=ev)
    step["sub_actions"] = [{
        "action_id": "a2", "instruction_source_text": step_two, "evidence": ev,
        "quantities": [{"source_text": "37 C"}],
        "estimated_duration": {"source_text": "10 min"},
    }]
    if warning is not None:
        step["warnings"][0].update(source_text=warning, evidence={
            "source_page_number": 1, "source_excerpt": warning})
    return json.dumps(payload)


class _FixCase(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        entry, _ = self.analyze(with_values(self.answer()))
        self.assertTrue(entry.available_for_execution, getattr(entry, "review_pending", entry))

    def draft(self) -> dict:
        return self.catalog.review_draft(self.protocol_id)

    def decide(self, **request) -> dict:
        return self.catalog.record_review_decision(self.protocol_id, request)

    def card(self, item_id: str, draft: dict | None = None) -> dict:
        return next(card for card in (draft or self.draft())["cards"] if card["item_id"] == item_id)


class FixingAValueTests(_FixCase):
    def test_the_sentences_that_state_the_old_value_are_asked_with_the_server_s_proposal(self) -> None:
        self.assertEqual(self.draft()["cards"], [])
        result = self.decide(item_id="action:a2/quantities/0", action="fix", text="25 C")
        self.assertFalse(result["registrable"])
        cards = result["cards"]
        self.assertEqual([card["item_id"] for card in cards], ["step:s2", "action:a2"])
        step = cards[0]
        self.assertEqual(step["type"], "value_changed")
        self.assertEqual(step["proposal"], "2. Incubate for 10 min at 25 C.")
        self.assertEqual(step["change"], {"from": "37 C", "to": "25 C",
                                          "source_item": "action:a2/quantities/0"})
        self.assertIn("37 C → 25 C", step["question_ko"])
        self.assertEqual([(choice["label_ko"], choice["needs"]) for choice in step["choices"]],
                         [("이렇게 바꿔요", None), ("다시 고칠게요", "text")])
        self.assertEqual(step["choices"][0]["request"],
                         {"item_id": "step:s2", "action": "fix",
                          "text": "2. Incubate for 10 min at 25 C."})

    def test_nothing_registers_until_each_is_decided_and_the_version_says_the_new_value(self) -> None:
        self.decide(item_id="action:a2/quantities/0", action="fix", text="25 C")
        with self.assertRaises(ProtocolReviewItemsPendingError) as pending:
            self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        self.assertEqual(set(pending.exception.item_ids), {"step:s2", "action:a2"})
        for card in self.draft()["cards"]:
            if not card["decided"]:
                self.decide(**card["choices"][0]["request"])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertIn("25 C", fixture.steps[1].instruction_source_text)
        self.assertNotIn("37 C", fixture.steps[1].instruction_source_text)

    def test_a_time_fixed_asks_the_step_s_timer_with_its_seconds(self) -> None:
        result = self.decide(item_id="action:a2/estimated_duration", action="fix", text="15 min")
        cards = {card["item_id"]: card for card in result["cards"]}
        self.assertEqual(set(cards), {"step:s2", "action:a2", "timer:s2"})
        self.assertEqual(cards["step:s2"]["proposal"], "2. Incubate for 15 min at 37 C.")
        timer = cards["timer:s2"]
        self.assertEqual(timer["proposal"], "15분")
        self.assertEqual(timer["choices"][0]["request"],
                         {"item_id": "timer:s2", "action": "fix", "seconds": 900})
        self.assertEqual(timer["choices"][1]["needs"], "seconds")

    def test_a_decision_made_before_the_fix_no_longer_counts(self) -> None:
        self.decide(item_id="step:s2", action="fix", text="2. Incubate for 10 min at 37 C!")
        self.decide(item_id="action:a2/quantities/0", action="fix", text="25 C")
        card = self.card("step:s2")
        self.assertFalse(card["decided"])
        # The proposal is made from the sentence as the analysis read it.
        self.assertEqual(card["proposal"], "2. Incubate for 10 min at 25 C.")
        self.assertFalse(self.draft()["registrable"])

    def test_taking_the_value_fix_back_asks_nothing_again(self) -> None:
        self.decide(item_id="action:a2/quantities/0", action="fix", text="25 C")
        result = self.decide(item_id="action:a2/quantities/0", action="reset")
        self.assertEqual(result["cards"], [])
        self.assertTrue(result["registrable"])


class WhereTheOldValueIsNotOnceTests(_OcrCase):
    def test_a_value_stated_twice_in_a_sentence_is_written_by_a_person(self) -> None:
        twice = "2. Spin the tube at 4000 rpm for 5 min, rest 5 min."
        self.analyze_after_ocr(
            _TwoEngineOcr(differ=True, text=SCAN_TEXT.replace(
                "2. Spin the tube at 4000 rpm for 5 min.", twice)),
            delta_answer(self.sha256, step_two=twice))
        result = self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "ocr_value:2:5min", "action": "fix", "text": "6 min"})
        card = next(card for card in result["cards"] if card["item_id"] == "step:s2")
        self.assertEqual(card["type"], "value_changed")
        self.assertIsNone(card["proposal"])
        self.assertEqual([(choice["label_ko"], choice["needs"]) for choice in card["choices"]],
                         [("다시 고칠게요", "text")])


class SafetyStatementsAreNeverLinkedTests(_CatalogCase):
    def test_a_warning_that_states_the_old_value_is_not_asked(self) -> None:
        self.analyze(with_values(self.answer()))
        result = self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "action:a2/quantities/0", "action": "fix", "text": "25 C"})
        kinds = {card["kind"] for card in result["cards"]}
        self.assertNotIn("warning", kinds)


class FixingAnOcrValueTests(_OcrCase):
    def test_every_item_on_its_page_that_states_it_is_asked_again(self) -> None:
        self.analyze_after_ocr(_TwoEngineOcr(differ=True))
        result = self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "ocr_value:2:4000rpm", "action": "fix", "text": "400 rpm"})
        cards = {card["item_id"]: card for card in result["cards"]}
        self.assertTrue(cards["ocr_value:2:4000rpm"]["decided"])
        self.assertEqual(cards["step:s2"]["type"], "value_changed")
        self.assertEqual(cards["step:s2"]["proposal"], "2. Spin the tube at 400 rpm for 5 min.")
        for card in result["cards"]:
            if not card["decided"]:
                self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertIn("400 rpm", fixture.steps[1].instruction_source_text)


if __name__ == "__main__":
    unittest.main()
