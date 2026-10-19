"""Lane AN3, round 2, decision ③: numbers in a safety statement, by both engines.

Human decision 2026-10-18: a number in a safety statement on a scanned page is
compared between the two OCR engines like any value used in execution. Read
alike, it is settled; read differently, it is on the page's card with the
piece of the page and the two engines' readings as its only buttons -- no
words of a person's own. Choosing a reading copies the source's characters as
that engine read them into the statement; it does not rewrite the statement,
and the statement itself still cannot be edited (lane RV2, decision g).
Every OCR engine and model here is fake; nothing is "live-tested".
"""

from __future__ import annotations

import json
import unittest

from tests.test_lane_an3_ocr_two_engines import KEEP, SCAN, _ScanCase, provider, scan_answer
from voiney_lab.protocol_catalog import ProtocolReviewError

HEAT = "Caution: keep below 37 °C."
WARNED = SCAN + [HEAT]
MISREAD = [line.replace("37 °C", "31 °C") for line in WARNED]


def warned_answer(sha256: str) -> str:
    payload = json.loads(scan_answer(sha256, scan=WARNED))
    step = next(step for section in payload["protocol"]["sections"] for step in section["steps"]
                if step["instruction_source_text"] == KEEP)
    step["warnings"] = [{"statement_id": "heat", "source_text": HEAT,
                         "evidence": {"source_page_number": 2, "source_excerpt": HEAT}}]
    return json.dumps(payload)


class SafetyNumberTests(_ScanCase):
    def analyze_warned(self, other: list[str]):
        return self.analyze(provider(WARNED, other), warned_answer(self.sha256))

    def safety_value(self) -> dict:
        (card,) = self.cards()
        return next(value for value in card["values"] if value["item_id"] == "ocr_value:2:37°c")

    def test_a_safety_number_both_engines_read_alike_is_settled(self) -> None:
        entry = self.analyze_warned(WARNED)
        self.assertTrue(entry.available_for_execution)
        (ready,) = self.events("protocol_analysis_ready")
        settled = {item["token"]: item for item in ready["auto_resolved"] if item["kind"] == "ocr_value"}
        self.assertEqual(settled["37°c"]["items"], ["statement:heat"])

    def test_a_safety_number_read_differently_offers_the_engines_readings_only(self) -> None:
        self.analyze_warned(MISREAD)
        value = self.safety_value()
        self.assertEqual(value["reason_ko"], "두 OCR 엔진이 이 값을 다르게 읽음")
        self.assertEqual([(choice["label_ko"], choice.get("engine")) for choice in value["choices"]],
                         [("37 °C", "Google"), ("31 °C", "CLOVA")])
        self.assertEqual([choice["request"] for choice in value["choices"]], [
            {"item_id": "ocr_value:2:37°c", "action": "confirm"},
            {"item_id": "ocr_value:2:37°c", "action": "fix", "text": "31 °C"}])
        self.assertTrue(value["value_boxes"], "no piece of the page for the safety number")

    def test_words_of_a_persons_own_are_refused_for_a_safety_number(self) -> None:
        self.analyze_warned(MISREAD)
        with self.assertRaises(ProtocolReviewError):
            self.catalog.record_review_decision(self.protocol_id, {
                "item_id": "ocr_value:2:37°c", "action": "fix", "text": "30 °C"})

    def test_the_statement_itself_still_cannot_be_edited(self) -> None:
        self.analyze_warned(MISREAD)
        with self.assertRaises(ProtocolReviewError):
            self.catalog.record_review_decision(self.protocol_id, {
                "item_id": "statement:heat", "action": "fix", "text": "Caution: keep below 31 °C."})

    def test_the_chosen_reading_is_copied_into_the_statement_of_the_version(self) -> None:
        self.analyze_warned(MISREAD)
        chosen = self.safety_value()["choices"][1]["request"]
        result = self.catalog.record_review_decision(self.protocol_id, chosen)
        self.assertNotIn("statement:heat", [card["item_id"] for card in result["cards"]])
        (card,) = [card for card in result["cards"] if card["type"] == "ocr_page"]
        self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        review = self.catalog.review(self.protocol_id)
        warnings = [warning["source_text"] for section in review["sections"]
                    for step in section["steps"] for warning in step.get("warnings") or ()]
        self.assertEqual(warnings, ["Caution: keep below 31 °C."])
        (version,) = self.events("protocol_review_version_registered")
        (change,) = [change for change in version["changes"] if change["item_id"] == "statement:heat"]
        self.assertEqual({key: change[key] for key in ("kind", "action", "original", "reviewed", "origin")}, {
            "kind": "warning", "action": "fix", "original": HEAT,
            "reviewed": "Caution: keep below 31 °C.", "origin": "source"})


if __name__ == "__main__":
    unittest.main()
