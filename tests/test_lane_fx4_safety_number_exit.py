"""Lane FX4, decision 5 (2026-10-19): a way out for a safety number no reading gets right.

Lane AN3, round 2: a number in a safety statement takes one of the two OCR
engines' readings, never a person's own words. On a page read poorly both
engines can read it alike and wrongly, and its card then held one button --
that reading. Human decision: the card's server-sent buttons for a safety
number end with [원문과 달라요]. Pressed, the value stays as "원문 확인 필요",
the registration is refused, and the guidance is "이 쪽을 더 선명하게 다시
찍어 올리거나 다시 분석해 주세요". The safety statement is not changed.
Every OCR engine and model here is fake; nothing is "live-tested".
"""

from __future__ import annotations

import unittest

from tests.test_lane_an3_ocr_two_engines import _ScanCase
from tests.test_lane_an3_page_gate import _Confident
from tests.test_lane_an3_safety_numbers import HEAT, MISREAD, WARNED, warned_answer
from tests.test_lane_an3_ocr_two_engines import provider
from tests.test_protocol_ocr_providers import PNG
from voiney_lab.protocol_catalog import (
    ProtocolReviewError,
    ProtocolReviewNotReadyError,
)
from voiney_lab.protocol_ocr_providers import CLOVA, GOOGLE, DualEngineOcrProvider

SAFETY = "ocr_value:2:37°c"
STEP_VALUE = "ocr_value:2:4000rpm"
GUIDANCE = "이 쪽을 더 선명하게 다시 찍어 올리거나 다시 분석해 주세요."
DIFFERS = {"item_id": SAFETY, "action": "source_differs"}


def dim_page() -> DualEngineOcrProvider:
    """Both engines read the page alike, one of them below 0.90 (the page gate)."""

    return DualEngineOcrProvider(
        (_Confident(CLOVA, WARNED, 0.85), _Confident(GOOGLE, WARNED, 0.95)),
        render=lambda *a, **k: PNG)


class SafetyNumberExitTests(_ScanCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(dim_page(), warned_answer(self.sha256))

    def value(self, item_id: str) -> dict:
        (card,) = [card for card in self.cards() if card["type"] == "ocr_page"]
        return next(value for value in card["values"] if value["item_id"] == item_id)

    def draft(self) -> dict:
        return self.catalog.review_draft(self.protocol_id)

    def statement(self) -> str:
        (item,) = [item for item in self.draft()["items"] if item["item_id"] == "statement:heat"]
        self.assertIsNone(item["decision"])
        return str(item["current"])

    def test_the_card_offers_the_reading_and_then_the_way_out(self) -> None:
        value = self.value(SAFETY)
        self.assertEqual(value["reason_code"], "ocr_page_low_confidence")
        self.assertEqual([choice["label_ko"] for choice in value["choices"]],
                         ["37 °C", "원문과 달라요"])
        self.assertEqual(value["choices"][-1]["request"], DIFFERS)
        self.assertFalse(value["choices"][-1].get("primary"))
        # Still no words of a person's own for a safety number.
        self.assertNotIn("직접 입력", [choice["label_ko"] for choice in value["choices"]])
        # A step's number keeps its own buttons; the way out is the safety number's.
        self.assertEqual([choice["label_ko"] for choice in self.value(STEP_VALUE)["choices"]],
                         ["4000 rpm", "직접 입력"])

    def test_pressed_the_value_waits_for_the_source_and_registration_is_refused(self) -> None:
        result = self.catalog.record_review_decision(self.protocol_id, DIFFERS)
        self.assertFalse(result["registrable"])
        value = self.value(SAFETY)
        self.assertTrue(value["decided"])
        self.assertEqual(value["decision"]["action"], "source_differs")
        self.assertEqual(value["source_check"],
                         {"state_ko": "원문 확인 필요", "guidance_ko": GUIDANCE})
        self.assertEqual(value["current"], "37 °C")
        draft = self.draft()
        self.assertIn({"item_id": SAFETY, "message_ko": f"원문 확인 필요: {GUIDANCE}",
                       "source_check": True}, draft["registration_blockers"])
        # Everything else decided: still refused, in its own words.
        (card,) = [card for card in draft["cards"] if card["type"] == "ocr_page"]
        self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        for other in self.draft()["cards"]:
            if not other["decided"] and other["type"] != "ocr_page":
                self.catalog.record_review_decision(self.protocol_id, other["choices"][0]["request"])
        draft = self.draft()
        self.assertEqual(draft["counts"]["undecided"], 0)
        self.assertFalse(draft["registrable"])
        with self.assertRaises(ProtocolReviewNotReadyError) as refused:
            self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        self.assertEqual(refused.exception.code, "review_source_check_needed")
        self.assertEqual(refused.exception.item_ids, (SAFETY,))
        self.assertEqual(self.events("protocol_review_version_registered"), [])
        # The statement is the source's, as it was read.
        self.assertEqual(self.statement(), HEAT)

    def test_all_correct_on_the_page_leaves_the_pressed_value_as_it_is(self) -> None:
        self.catalog.record_review_decision(self.protocol_id, DIFFERS)
        (card,) = [card for card in self.cards() if card["type"] == "ocr_page"]
        self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        self.assertEqual(self.value(SAFETY)["decision"]["action"], "source_differs")

    def test_taking_it_back_asks_the_value_again(self) -> None:
        self.catalog.record_review_decision(self.protocol_id, DIFFERS)
        self.catalog.record_review_decision(self.protocol_id, {"item_id": SAFETY, "action": "reset"})
        value = self.value(SAFETY)
        self.assertFalse(value["decided"])
        self.assertNotIn("source_check", value)
        self.assertNotIn(SAFETY, [blocker["item_id"] for blocker in self.draft()["registration_blockers"]
                                  if blocker.get("source_check")])

    def test_only_a_safety_number_takes_it_and_it_carries_nothing(self) -> None:
        with self.assertRaises(ProtocolReviewError):
            self.catalog.record_review_decision(
                self.protocol_id, {"item_id": STEP_VALUE, "action": "source_differs"})
        with self.assertRaises(ProtocolReviewError):
            self.catalog.record_review_decision(
                self.protocol_id, {**DIFFERS, "text": "57 °C"})
        with self.assertRaises(ProtocolReviewError):
            self.catalog.record_review_decision(
                self.protocol_id, {"item_id": "statement:heat", "action": "source_differs"})


class ReadDifferentlyTests(_ScanCase):
    def test_two_readings_end_with_the_way_out_too(self) -> None:
        self.analyze(provider(WARNED, MISREAD), warned_answer(self.sha256))
        (card,) = self.cards()
        value = next(value for value in card["values"] if value["item_id"] == SAFETY)
        self.assertEqual([choice["label_ko"] for choice in value["choices"]],
                         ["37 °C", "31 °C", "원문과 달라요"])


if __name__ == "__main__":
    unittest.main()
