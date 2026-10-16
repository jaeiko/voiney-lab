"""One card per item a person is asked about (lane RV2, decision a).

Human decision 2026-10-16: review as little as possible, as well as possible.
A person is asked about items, not documents, one card at a time by buttons
-- no checkbox list, no table to edit. An analysis whose every executed item
passed has no card and runs as before. The server builds each card: the
question, the item as the analysis read it, where it stands in the source,
and the only buttons the screen may draw, each one a review decision the
server takes (or "다시 분석").
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from tests.test_lane_rv_review_pending import MADE_UP, _CatalogCase
from voiney_lab import server
from voiney_lab.experiment_protocol_analysis import AnalysisPiece
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog, review_cards

STEP_TWO_LINES = {"page": 1, "first_line": 4, "last_line": 4}


class _CardCase(_CatalogCase):
    def draft(self) -> dict:
        return self.catalog.review_draft(self.protocol_id)

    def decide(self, request: dict, **extra) -> dict:
        return self.catalog.record_review_decision(self.protocol_id, {**request, **extra})

    @staticmethod
    def choice(card: dict, choice_id: str) -> dict:
        return next(choice for choice in card["choices"] if choice["choice_id"] == choice_id)


class AnalysisThatPassedHasNoCardTests(_CardCase):
    def test_every_executed_item_passed_so_it_runs_and_asks_nothing(self) -> None:
        entry, model = self.analyze(self.answer())
        self.assertEqual(model.calls, 1)
        self.assertTrue(entry.available_for_execution)
        self.catalog.load_executable_fixture(self.protocol_id)
        draft = self.draft()
        self.assertEqual(draft["cards"], [])
        self.assertIsNone(draft["next_card"])
        self.assertTrue(draft["registrable"])


class OneCardPerItemTests(_CardCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(step_two=MADE_UP))

    def test_the_made_up_step_is_the_one_card_and_says_what_is_asked(self) -> None:
        draft = self.draft()
        (card,) = draft["cards"]
        self.assertEqual(draft["next_card"], 0)
        self.assertEqual((card["item_id"], card["position"], card["total"], card["progress_ko"]),
                         ("step:s2", 1, 1, "1 / 1"))
        self.assertEqual(card["type"], "evidence")
        self.assertEqual(card["shown"], MADE_UP)
        self.assertEqual(card["reasons_ko"], ["원문에 없음"])
        self.assertIn("원문의 어느 줄", card["question_ko"])
        self.assertFalse(card["decided"])
        # A step is never left empty: no "원문에 없어요"; it is read again.
        self.assertEqual([choice["choice_id"] for choice in card["choices"]],
                         ["confirm_lines", "fix", "reanalysis"])
        self.assertEqual(self.choice(card, "confirm_lines")["needs"], "source_lines")
        self.assertTrue(self.choice(card, "confirm_lines")["primary"])
        self.assertEqual(self.choice(card, "reanalysis")["route"], "reanalysis")
        self.assertIsNone(self.choice(card, "reanalysis")["request"])

    def test_the_card_s_button_is_a_decision_the_server_takes(self) -> None:
        card = self.draft()["cards"][0]
        result = self.decide(self.choice(card, "confirm_lines")["request"],
                             source_lines=STEP_TWO_LINES)
        self.assertTrue(result["registrable"])
        (after,) = result["cards"]
        self.assertTrue(after["decided"])
        self.assertEqual(after["decision"]["action"], "confirm")
        self.assertIsNone(result["next_card"])
        # Decided cards stay, so "← 이전" goes back to them.
        self.assertEqual(self.draft()["cards"][0]["item_id"], "step:s2")

    def test_only_items_that_wait_for_a_person_are_cards(self) -> None:
        draft = self.draft()
        self.assertGreater(len(draft["items"]), len(draft["cards"]))
        self.assertEqual([card["item_id"] for card in draft["cards"]], draft["needs_review"])


class ChoicesFollowWhatTheServerAllowsTests(_CardCase):
    def test_a_warning_the_source_does_not_hold_is_confirmed_by_lines_or_left_empty(self) -> None:
        answer = json.loads(self.answer())
        answer["protocol"]["sections"][0]["steps"][1]["warnings"][0].update(
            source_text="Wear a face shield.",
            evidence={"source_page_number": 1, "source_excerpt": "Wear a face shield."})
        self.analyze(json.dumps(answer))
        (card,) = self.draft()["cards"]
        self.assertEqual(card["kind"], "warning")
        # A safety statement's words are never changed: no "직접 고칠게요".
        self.assertEqual([choice["choice_id"] for choice in card["choices"]],
                         ["confirm_lines", "clear"])
        self.assertTrue(card["memo_allowed"])
        result = self.decide(self.choice(card, "clear")["request"])
        self.assertTrue(result["registrable"])

    def test_a_failed_piece_s_pages_ask_whether_they_hold_no_step(self) -> None:
        pieces = (AnalysisPiece(1, 3, 1, 1), AnalysisPiece(2, 3, 2, 2), AnalysisPiece(3, 3, 3, 3))
        broken = self.answer(pages=(2,)).replace('"steps":', '"stepz":', 1)
        answers = {1: [self.answer(pages=(1,))], 2: [broken, broken], 3: [self.answer(pages=(3,))]}

        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            return answers[json.loads(input_json)["pages"][0]["source_page_number"]].pop(0)

        model = Mock()
        model.analyze.side_effect = model_answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "e" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=pieces):
            self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "e" * 32)
        (card,) = self.draft()["cards"]
        self.assertEqual((card["type"], card["item_id"]), ("failed_pages", "pages:2-2"))
        self.assertIn("2–2쪽 분석 실패", card["question_ko"])
        self.assertEqual([(choice["choice_id"], choice["label_ko"]) for choice in card["choices"]],
                         [("confirm", "이 쪽엔 단계 없음"), ("reanalysis", "다시 분석")])
        self.assertTrue(self.decide(self.choice(card, "confirm")["request"])["registrable"])

    def test_a_claim_under_a_verified_quote_is_confirmed_without_lines(self) -> None:
        answer = json.loads(self.answer())
        answer["protocol"]["sections"][0]["steps"][0]["instruction_source_text"] = (
            "1. Add 3 mL of buffer A.")
        self.analyze(json.dumps(answer))
        (card,) = self.draft()["cards"]
        self.assertEqual(card["type"], "claim")
        self.assertEqual(card["question_ko"], "이 단계가 원문과 같나요?")
        self.assertEqual([choice["choice_id"] for choice in card["choices"]],
                         ["confirm", "fix", "reanalysis"])
        self.assertEqual(self.choice(card, "fix")["needs"], "text")
        result = self.decide(self.choice(card, "fix")["request"], text="1. Add 2 mL of buffer A.")
        self.assertTrue(result["registrable"])

    def test_cards_are_built_from_items_alone(self) -> None:
        item = {"item_id": "value:x", "kind": "value", "kind_ko": "값", "needs_review": True,
                "allowed_actions": ["confirm", "fix", "clear", "memo", "reset"],
                "source": {"verified": True}, "reasons": [], "text": "4000 rpm",
                "decision": None, "current": "4000 rpm"}
        (card,) = review_cards([item, {**item, "item_id": "value:y", "needs_review": False}])
        self.assertEqual(card["question_ko"], "이 값이 원문과 같나요?")
        self.assertEqual([choice["choice_id"] for choice in card["choices"]],
                         ["confirm", "fix", "clear"])


class ReviewDraftRouteTests(_CardCase):
    def test_the_route_gives_the_cards(self) -> None:
        self.analyze(self.answer(step_two=MADE_UP))
        self.store.close()

        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog):
                draft = server.get_protocol_review_draft(self.protocol_id)
                card = draft["cards"][0]
                result = server.post_protocol_review_decision(self.protocol_id, {
                    **self.choice(card, "confirm_lines")["request"],
                    "source_lines": STEP_TWO_LINES, "target": draft["target"]})
        finally:
            self.store = initialize_protocol_store(self.settings)
            self.catalog = ProtocolCatalog(self.store)
        self.assertEqual(card["item_id"], "step:s2")
        self.assertTrue(result["cards"][0]["decided"])


if __name__ == "__main__":
    unittest.main()
