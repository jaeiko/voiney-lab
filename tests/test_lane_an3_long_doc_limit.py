"""Lane AN3, round 2, decision ④: a long document's card limit, and no re-analysis.

Human decision 2026-10-18: a long document is not analysed again by itself
when it asks about many items -- the one live re-analysis (lane AN3, stage ②,
usgs 88 pages) cost USD 0.91 and changed nothing. Its card limit is instead 12
plus its number of pieces (a seam card and an "분석 못 한 쪽" card are one press
each). A document read at once keeps lane RV2's one re-analysis. Every model
here is fake.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

from tests.test_lane_rv2_many_items import with_made_up_notes
from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab.experiment_protocol_analysis import AnalysisPiece
from voiney_lab.protocol_catalog import ProtocolAnalysisTooManyItemsError

THREE = (AnalysisPiece(1, 3, 1, 1), AnalysisPiece(2, 3, 2, 2), AnalysisPiece(3, 3, 3, 3))


class _Pieces(_CatalogCase):
    def run_pieces(self, pieces, answers: dict[int, list[str]], env: dict | None = None):
        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            return answers[json.loads(input_json)["pages"][0]["source_page_number"]].pop(0)

        model = Mock()
        model.analyze.side_effect = model_answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "e" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=pieces), \
                patch.dict(os.environ, env or {}):
            try:
                entry = self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "e" * 32)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                entry = exc
        return entry, model


class LongDocumentLimitTests(_Pieces):
    def test_items_over_12_but_within_12_plus_pieces_wait_for_review_without_reanalysis(self) -> None:
        entry, model = self.run_pieces(THREE, {
            1: [with_made_up_notes(self.answer(pages=(1,)), 15)],
            2: [self.answer(pages=(2,))], 3: [self.answer(pages=(3,))]})
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(entry.review_pending["needs_review_count"], 15)
        # Three pieces and, lane AN4 decision 1, the re-ask of piece 1's items.
        self.assertEqual(model.analyze.call_count, 4)
        self.assertEqual(self.events("protocol_analysis_auto_reanalysis"), [])
        self.assertEqual(len(self.catalog.review_draft(self.protocol_id)["cards"]), 15)

    def test_items_over_12_plus_pieces_end_with_the_guidance_and_no_reanalysis(self) -> None:
        error, model = self.run_pieces(THREE, {
            1: [with_made_up_notes(self.answer(pages=(1,)), 16)],
            2: [self.answer(pages=(2,))], 3: [self.answer(pages=(3,))]})
        self.assertIsInstance(error, ProtocolAnalysisTooManyItemsError)
        # Three pieces and, lane AN4 decision 1, the re-ask of piece 1's items.
        self.assertEqual(model.analyze.call_count, 4)
        self.assertEqual(self.events("protocol_analysis_auto_reanalysis"), [])
        (failed,) = self.events("protocol_analysis_failed")
        self.assertEqual((failed["review_items"]["items"], failed["review_items"]["limit"]), (16, 15))
        self.assertIsNone(failed["review_items"]["items_after"])
        message = self.catalog.pipeline_status(self.protocol_id)["message"]
        self.assertIn("확인할 항목이 16건이라(카드는 15건까지)", message)
        self.assertNotIn("한 번 더 분석", message)

    def test_the_limit_moves_with_the_setting(self) -> None:
        entry, _ = self.run_pieces(THREE, {
            1: [with_made_up_notes(self.answer(pages=(1,)), 6)],
            2: [self.answer(pages=(2,))], 3: [self.answer(pages=(3,))]},
            env={"VOINEY_LAB_REVIEW_MAX_ITEMS": "2"})
        self.assertIsInstance(entry, ProtocolAnalysisTooManyItemsError)  # 6 > 2 + 3
        (failed,) = self.events("protocol_analysis_failed")
        self.assertEqual(failed["review_items"]["limit"], 5)


if __name__ == "__main__":
    unittest.main()
