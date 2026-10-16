"""A safety statement is never rewritten in review (lane RV2, decision g).

Human decision 2026-10-16, pinned here: a safety statement ("안전 주의") cannot
be fixed by a reviewer -- a memo beside it is all a person writes; one the
source does not print (the AI made it up) may be left empty, and then it does
not run; one a person confirms runs in the source's own words. Safety
statements and completion criteria are never written by the AI: no card,
link or choice offers to change their words.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from tests.test_lane_rv2_value_fix import with_values
from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab import server
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog, ProtocolReviewError

MADE_UP_WARNING = "Wear a face shield."


def with_made_up_warning(answer: str) -> str:
    payload = json.loads(answer)
    payload["protocol"]["sections"][0]["steps"][1]["warnings"][0].update(
        source_text=MADE_UP_WARNING,
        evidence={"source_page_number": 1, "source_excerpt": MADE_UP_WARNING})
    return json.dumps(payload)


class _SafetyCase(_CatalogCase):
    def decide(self, **request) -> dict:
        return self.catalog.record_review_decision(self.protocol_id, request)

    def item(self, item_id: str) -> dict:
        return next(item for item in self.catalog.review_draft(self.protocol_id)["items"]
                    if item["item_id"] == item_id)


class AWarningThePageHoldsTests(_SafetyCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer())

    def test_its_words_are_never_fixed_nor_left_empty_only_a_memo(self) -> None:
        self.assertEqual(self.item("statement:gloves")["allowed_actions"],
                         ["confirm", "memo", "reset"])
        for request in ({"action": "fix", "text": "Wear two gloves."},
                        {"action": "clear"}, {"action": "enter", "text": "x"}):
            with self.subTest(request=request), self.assertRaises(ProtocolReviewError):
                self.decide(item_id="statement:gloves", **request)
        memo = self.decide(item_id="statement:gloves", action="memo", memo="니트릴 장갑")
        self.assertEqual(memo["item"]["memo"], "니트릴 장갑")
        self.assertEqual(memo["item"]["current"], "Wear gloves.")



class AFixedValueNeverReachesAWarningTests(_SafetyCase):
    def test_a_warning_stating_the_old_value_is_not_asked_to_change(self) -> None:
        stated = "Keep it below 37 C."
        payload = json.loads(with_values(self.answer()))
        payload["protocol"]["sections"][0]["steps"][1]["warnings"][0].update(
            source_text=stated, evidence={"source_page_number": 1, "source_excerpt": stated})
        self.analyze(json.dumps(payload))
        result = self.decide(item_id="action:a2/quantities/0", action="fix", text="25 C")
        warning = next(card for card in result["cards"] if card["item_id"] == "statement:gloves")
        # Its own card (the source does not print it), never a value change.
        self.assertEqual(warning["type"], "evidence")
        self.assertNotIn("value_changed", [reason for reason in (
            item["reason"] for item in self.item("statement:gloves")["reasons"])])
        self.assertIn("step:s2", [card["item_id"] for card in result["cards"]])


class AWarningTheAiMadeUpTests(_SafetyCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(with_made_up_warning(self.answer()))

    def test_its_card_offers_no_rewrite(self) -> None:
        (card,) = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual(card["kind"], "warning")
        self.assertNotIn("fix", [choice["choice_id"] for choice in card["choices"]])
        self.assertNotIn("fix", self.item("statement:gloves")["allowed_actions"])
        with self.assertRaises(ProtocolReviewError):
            self.decide(item_id="statement:gloves", action="fix", text="Wear gloves.")

    def test_left_empty_it_does_not_run(self) -> None:
        self.decide(item_id="statement:gloves", action="clear")
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.steps[1].warnings, ())

    def test_confirmed_it_runs_in_the_source_s_words(self) -> None:
        self.decide(item_id="statement:gloves", action="confirm",
                    source_lines={"page": 1, "first_line": 5, "last_line": 5})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.steps[1].warnings[0].source_text, "Wear gloves.")

    def test_the_route_refuses_a_rewrite(self) -> None:
        self.store.close()

        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog):
                with self.assertRaises(HTTPException) as refused:
                    server.post_protocol_review_decision(self.protocol_id, {
                        "item_id": "statement:gloves", "action": "fix",
                        "text": "Wear gloves."})
        finally:
            self.store = initialize_protocol_store(self.settings)
            self.catalog = ProtocolCatalog(self.store)
        self.assertEqual((refused.exception.status_code, refused.exception.detail),
                         (422, "review_decision_invalid"))


if __name__ == "__main__":
    unittest.main()
