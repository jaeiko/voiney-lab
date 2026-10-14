"""The review tab's items and decisions (lane RV, decision b).

Human decision 2026-10-12 and the review scope recommended 2026-10-14: a
person reviews the analysis item by item -- confirm it, fix it (a value, a
unit, a typo in a step sentence), leave it empty because the source does not
hold it, or enter a value the source does not print; a memo beside any item.
Steps are not added or removed, and a safety statement's words are not
changed. A value a person wrote is "검토자 입력", one the analysis produced
and the source does not confirm is "AI 제안". Every decision is one more
ledger event with the original value; the source and the analysis are never
changed.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from tests.test_lane_rv_review_pending import MADE_UP, _CatalogCase
from voiney_lab import server
from voiney_lab.experiment_protocol_pdf import ProtocolPdfPage
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    ProtocolReviewError,
    ProtocolReviewTargetChangedError,
    _ocr_page_unclear,
    value_kind_ko,
)

STEP_TWO = "2. Incubate for 10 min at 37 C."


class _ReviewCase(_CatalogCase):
    def draft(self) -> dict:
        return self.catalog.review_draft(self.protocol_id)

    def item(self, item_id: str, draft: dict | None = None) -> dict:
        return next(item for item in (draft or self.draft())["items"] if item["item_id"] == item_id)

    def decide(self, **request) -> dict:
        return self.catalog.record_review_decision(self.protocol_id, request)


class AReviewPendingAnalysisTests(_ReviewCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer(step_two=MADE_UP))

    def test_the_flagged_item_is_an_ai_suggestion_waiting_with_its_reason(self) -> None:
        draft = self.draft()
        self.assertEqual(draft["target"]["kind"], "review_pending")
        self.assertEqual(draft["needs_review"], ["step:s2"])
        self.assertEqual(draft["counts"]["undecided"], 1)
        self.assertFalse(draft["registrable"])
        self.assertEqual(draft["registration_blockers"][0]["item_id"], "step:s2")
        flagged = self.item("step:s2", draft)
        self.assertEqual((flagged["origin"], flagged["origin_ko"]), ("ai_suggestion", "AI 제안"))
        self.assertTrue(flagged["needs_review"])
        self.assertEqual(flagged["reasons"][0]["reason_ko"], "원문에 없음")
        self.assertFalse(flagged["source"]["verified"])
        self.assertIsNone(flagged["source"]["lines"])
        self.assertEqual(flagged["text"], MADE_UP)
        verified = self.item("step:s1", draft)
        self.assertEqual((verified["origin"], verified["origin_ko"]), ("source", "원문"))
        self.assertEqual(verified["source"]["lines"], {"page": 1, "first_line": 3, "last_line": 3})
        self.assertEqual(verified["kind_ko"], "단계")

    def test_a_refused_quote_is_confirmed_only_with_the_lines_a_person_points_at(self) -> None:
        with self.assertRaises(ProtocolReviewError):
            self.decide(item_id="step:s2", action="confirm")
        with self.assertRaises(ProtocolReviewError):
            self.decide(item_id="step:s2", action="confirm",
                        source_lines={"page": 1, "first_line": 40})
        result = self.decide(item_id="step:s2", action="confirm",
                             source_lines={"page": 1, "first_line": 4, "last_line": 4})
        self.assertTrue(result["registrable"])
        decision = result["item"]["decision"]
        self.assertEqual(decision["source_lines"]["excerpt"], STEP_TWO)
        self.assertEqual(result["item"]["origin_after"], "ai_suggestion")
        self.assertFalse(result["item"]["differs_from_original"])

    def test_fixing_a_step_sentence_makes_it_a_reviewer_input(self) -> None:
        result = self.decide(item_id="step:s2", action="fix", text=STEP_TWO,
                             source_lines={"page": 1, "first_line": 4, "last_line": 4},
                             reviewer_name="  김 재준 ")
        item = result["item"]
        self.assertEqual(item["current"], STEP_TWO)
        self.assertEqual((item["origin_after"], item["origin_after_ko"]),
                         ("reviewer_input", "검토자 입력"))
        self.assertTrue(item["differs_from_original"])
        self.assertEqual(item["decision"]["reviewer_name"], "김 재준")
        (event,) = [e.payload for e in self.store.list_events(self.protocol_id)
                    if e.event_type == "protocol_review_decision_recorded"]
        # The original value and where it came from stay in the record.
        self.assertEqual(event["original"], {"text": MADE_UP, "origin": "ai_suggestion"})
        self.assertEqual(event["target"]["kind"], "review_pending")

    def test_a_step_is_never_left_empty_and_a_warning_s_words_never_change(self) -> None:
        with self.assertRaises(ProtocolReviewError):
            self.decide(item_id="step:s2", action="clear")
        with self.assertRaises(ProtocolReviewError):
            self.decide(item_id="statement:gloves", action="fix", text="Wear two gloves.")
        # A verified warning is the source's own: it is not left empty either.
        with self.assertRaises(ProtocolReviewError):
            self.decide(item_id="statement:gloves", action="clear")
        memo = self.decide(item_id="statement:gloves", action="memo", memo="니트릴 장갑")
        self.assertEqual(memo["item"]["memo"], "니트릴 장갑")
        self.assertIsNone(memo["item"]["decision"])

    def test_reset_takes_a_draft_decision_back(self) -> None:
        self.decide(item_id="step:s2", action="confirm",
                    source_lines={"page": 1, "first_line": 4, "last_line": 4})
        result = self.decide(item_id="step:s2", action="reset")
        self.assertIsNone(result["item"]["decision"])
        self.assertFalse(result["registrable"])
        # Both events stay in the ledger.
        actions = [e.payload["action"] for e in self.store.list_events(self.protocol_id)
                   if e.event_type == "protocol_review_decision_recorded"]
        self.assertEqual(actions, ["confirm", "reset"])

    def test_a_decision_read_against_an_older_analysis_is_refused(self) -> None:
        seen = self.draft()["target"]
        self.analyze(self.answer(step_two="2. Incubate for 1O min at 37 C."),
                     analysis_id="analysis-" + "f" * 32, reanalysis=True)
        with self.assertRaises(ProtocolReviewTargetChangedError):
            self.decide(item_id="step:s1", action="confirm", target=seen)

    def test_unknown_items_fields_and_values_are_refused(self) -> None:
        for request in (
            {"item_id": "step:nope", "action": "confirm"},
            {"item_id": "step:s1", "action": "approve"},
            {"item_id": "step:s1", "action": "fix", "text": ""},
            {"item_id": "step:s1", "action": "fix", "text": "1. Add 2 mL of buffer A."},
            {"item_id": "step:s1", "action": "confirm", "extra": 1},
            {"item_id": "step:s1", "action": "confirm", "reviewer_name": "   "},
            {"item_id": "step:s1", "action": "confirm",
             "source_lines": {"page": 1, "first_line": 3}},
        ):
            with self.subTest(request=request), self.assertRaises(ProtocolReviewError):
                self.decide(**request)


class AnAnalysisThatPassedTests(_ReviewCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer())

    def test_every_item_is_the_source_s_and_nothing_waits(self) -> None:
        draft = self.draft()
        self.assertEqual(draft["target"]["kind"], "analysis")
        self.assertEqual(draft["target"]["revision_id"], "pdf-1-analysis-1")
        self.assertEqual(draft["needs_review"], [])
        self.assertTrue(draft["registrable"])
        self.assertTrue(all(item["origin"] == "source" for item in draft["items"]))

    def test_a_timer_is_fixed_entered_or_left_out_in_whole_seconds(self) -> None:
        timer = self.item("timer:s2")
        self.assertEqual(timer["text"], "10 min")
        self.assertEqual(timer["seconds"], [600])
        self.assertEqual(timer["value_kind_ko"], "시간")
        self.assertEqual(timer["allowed_actions"], ["fix", "clear", "memo", "reset"])
        fixed = self.decide(item_id="timer:s2", action="fix", seconds=900)["item"]
        self.assertEqual(fixed["current"], "15분")
        self.assertEqual(fixed["origin_after"], "reviewer_input")
        with self.assertRaises(ProtocolReviewError):
            self.decide(item_id="timer:s2", action="fix", seconds=0)
        cleared = self.decide(item_id="timer:s2", action="clear")["item"]
        self.assertIsNone(cleared["current"])

    def test_a_value_s_kind_is_named_by_its_unit(self) -> None:
        for words, kind in (
            ("37 °C", "온도"), ("4 C", "온도"), ("10 min", "시간"), ("2 mL", "부피"),
            ("500 x g", "원심·회전"), ("4000 rpm", "원심·회전"), ("50 mM", "농도"),
            ("1 %", "농도"), ("3 times", "횟수"), ("20 µg", "질량"), ("buffer A", None),
        ):
            with self.subTest(words=words):
                self.assertEqual(value_kind_ko(words), kind)


class SourcePageLinesTests(_ReviewCase):
    def test_every_line_has_its_number_and_the_value_lines_are_marked(self) -> None:
        self.analyze(self.answer())
        page = self.catalog.review_source_page(self.protocol_id, 1)
        self.assertEqual([line["number"] for line in page["lines"]], [1, 2, 3, 4, 5])
        self.assertEqual(page["lines"][3]["text"], STEP_TWO)
        self.assertEqual([line["has_value"] for line in page["lines"]],
                         [False, False, True, True, False])
        self.assertEqual(page["unclear_line_count"], 0)
        self.assertFalse(page["ocr"])

    def test_an_unclear_ocr_page_marks_its_value_lines(self) -> None:
        ocr_page = ProtocolPdfPage(1, "a", False, ocr_derived=True)
        text_page = ProtocolPdfPage(1, "a", False)
        self.assertTrue(_ocr_page_unclear(ocr_page, {"numeric_review_required": True}))
        self.assertTrue(_ocr_page_unclear(ocr_page, {"confidence": 0.6}))
        self.assertFalse(_ocr_page_unclear(ocr_page, {"confidence": 0.95}))
        self.assertFalse(_ocr_page_unclear(text_page, {"numeric_review_required": True}))
        lines = ProtocolCatalog._line_views(
            ProtocolPdfPage(1, "Add 2 mL\nMix well", False, ocr_derived=True),
            {"numeric_review_required": True})
        self.assertEqual([line["unclear"] for line in lines], [True, False])


class ReviewRoutesTests(_ReviewCase):
    def call(self, function, *args, **kwargs):
        self.store.close()

        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog):
                return function(*args, **kwargs)
        finally:
            self.store = initialize_protocol_store(self.settings)
            self.catalog = ProtocolCatalog(self.store)

    def test_the_routes_read_and_record_through_the_catalog(self) -> None:
        self.analyze(self.answer(step_two=MADE_UP))
        draft = self.call(server.get_protocol_review_draft, self.protocol_id)
        self.assertEqual(draft["needs_review"], ["step:s2"])
        result = self.call(
            server.post_protocol_review_decision, self.protocol_id,
            {"item_id": "step:s2", "action": "confirm", "target": draft["target"],
             "source_lines": {"page": 1, "first_line": 4, "last_line": 4}})
        self.assertTrue(result["registrable"])
        page = self.call(server.get_protocol_review_source_page, self.protocol_id, 2)
        self.assertEqual(page["lines"][1]["text"], "3. Wash the pellet twice.")
        with self.assertRaises(HTTPException) as refused:
            self.call(server.post_protocol_review_decision, self.protocol_id,
                      {"item_id": "step:s2", "action": "clear"})
        self.assertEqual((refused.exception.status_code, refused.exception.detail),
                         (422, "review_decision_invalid"))
        image = self.call(server.get_protocol_review_source_page_image, self.protocol_id, 1)
        self.assertEqual(image.media_type, "image/png")
        self.assertTrue(image.body.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
