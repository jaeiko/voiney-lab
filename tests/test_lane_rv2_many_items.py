"""Too many items for cards: read once more, or the guidance alone (lane RV2, decision c).

Lane RV's replay: items per review-pending document were 1 at least, 3 in the
middle and 64 at most, and the 64-item documents were nearly all "원문에 없음"
-- steps the model rewrote rather than quoted, where analysing again does
better than a person checking each. Human decision 2026-10-16: an analysis
with more items than the setting (12 by default) makes no cards; it is
analysed once more by itself, one reasoning level up, within a per-document
bound on calls, and the reason is recorded. Still too many: "이 문서는 다시
분석하거나 절차가 있는 쪽만 올리는 편이 나아요", and no cards. Every model here
is fake.
"""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_lane_rv2_auto_resolve import SCAN_TEXT, _OcrCase, _TwoEngineOcr, delta_answer
from tests.test_lane_rv_review_pending import _CatalogCase, _RawModel
from voiney_lab.experiment_protocol_analysis import (
    AnalysisPiece,
    OpenAICompatibleProtocolAnalysisModel,
    REQUOTE_SYSTEM_PROMPT,
)
from voiney_lab.protocol_catalog import (
    REVIEW_MAX_ITEMS,
    ProtocolAnalysisTooManyItemsError,
    escalated_analysis_model,
    review_max_items,
    review_reanalysis_max_calls,
)


def with_made_up_notes(answer: str, count: int) -> str:
    """Step 1 with ``count`` notes the source does not print: one item each."""

    payload = json.loads(answer)
    step = payload["protocol"]["sections"][0]["steps"][0]
    step["notes"] = [
        {"statement_id": f"made-up-{index}", "source_text": f"Nobody printed note {index}.",
         "evidence": {"source_page_number": 1, "source_excerpt": f"Nobody printed note {index}."}}
        for index in range(count)
    ]
    return json.dumps(payload)


class _Client:
    """An OpenAI-style client answering in turn, recording each reasoning level."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.levels: list[str | None] = []
        self.reasks = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        if request["messages"][0]["content"] == REQUOTE_SYSTEM_PROMPT:
            # Lane AN4, decision 1: the refused items are asked about once
            # more; this provider finds nothing for them, and ``levels`` and
            # the answers stay the analysis calls'.
            self.reasks += 1
            message = SimpleNamespace(content=json.dumps({"answers": []}))
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])
        self.levels.append(request.get("reasoning_effort"))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        message = SimpleNamespace(content=answer)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


class _ManyCase(_CatalogCase):
    def analyze_with(self, client: _Client, *, level: str = "low", env: dict | None = None):
        model = OpenAICompatibleProtocolAnalysisModel(client, "model-test", level)
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
        with patch.dict(os.environ, env or {}):
            try:
                return self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "a" * 32)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                return exc


class TooManyItemsTests(_ManyCase):
    def test_twelve_items_are_cards_and_nothing_is_sent_again(self) -> None:
        client = _Client(with_made_up_notes(self.answer(), 12))
        entry = self.analyze_with(client)
        self.assertEqual(client.levels, ["low"])
        # Lane MISS: and pages 2-3's steps, never counted toward the bound.
        self.assertEqual(entry.review_pending["needs_review_count"], 14)
        self.assertEqual(len(self.catalog.review_draft(self.protocol_id)["cards"]), 14)
        self.assertEqual(self.events("protocol_analysis_auto_reanalysis"), [])

    def test_thirteen_are_read_once_more_one_level_up_and_its_cards_kept(self) -> None:
        client = _Client(with_made_up_notes(self.answer(), 13), with_made_up_notes(self.answer(), 2))
        entry = self.analyze_with(client)
        self.assertEqual(client.levels, ["low", "medium"])
        self.assertEqual(entry.review_pending["needs_review_count"], 4)  # lane MISS: +2
        (pending,) = self.events("protocol_analysis_review_pending")
        self.assertEqual(pending["analysis_id"], "analysis-" + "a" * 32 + "-more-1")
        started, ended = self.events("protocol_analysis_auto_reanalysis")
        self.assertEqual(
            {key: started[key] for key in ("status", "reason", "items", "limit", "planned_calls",
                                          "call_cap", "reasoning_effort",
                                          "previous_reasoning_effort")},
            {"status": "analyzing", "reason": "too_many_review_items", "items": 13,
             "limit": 12, "planned_calls": 1, "call_cap": 16, "reasoning_effort": "medium",
             "previous_reasoning_effort": "low"})
        self.assertEqual(started["by_reason"], {"not_in_source": 13})
        self.assertEqual((ended["status"], ended["items_after"]), ("completed", 2))
        self.assertEqual(self.events("protocol_analysis_failed"), [])

    def test_a_re_analysis_that_passes_runs(self) -> None:
        client = _Client(with_made_up_notes(self.answer(), 20), self.answer())
        entry = self.analyze_with(client)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(self.events("protocol_analysis_review_pending"), [])

    def test_still_too_many_makes_no_card_and_gives_the_guidance(self) -> None:
        client = _Client(with_made_up_notes(self.answer(), 30), with_made_up_notes(self.answer(), 14))
        error = self.analyze_with(client)
        self.assertIsInstance(error, ProtocolAnalysisTooManyItemsError)
        self.assertEqual(self.events("protocol_analysis_review_pending"), [])
        (failed,) = self.events("protocol_analysis_failed")
        self.assertEqual(failed["failure_code"], "protocol_analysis_too_many_review_items")
        self.assertEqual(failed["review_items"], {
            "items": 30, "limit": 12, "items_after": 14, "by_reason": {"not_in_source": 30}})
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertEqual(draft["cards"], [])
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual(line["message"],
                         "확인할 항목이 30건이라(카드는 12건까지) 검토 카드를 만들지 않았어요. "
                         "한 번 더 분석해도 14건이었어요. 이 문서는 다시 분석하거나 절차가 있는 쪽만 "
                         "올리는 편이 나아요.")
        self.assertIn("절차가 있는 쪽만", line["action"])
        self.assertFalse(self.catalog.get_entry(self.protocol_id).available_for_execution)

    def test_a_re_analysis_that_fails_leaves_the_guidance(self) -> None:
        client = _Client(with_made_up_notes(self.answer(), 13), RuntimeError("provider down"))
        error = self.analyze_with(client)
        self.assertIsInstance(error, ProtocolAnalysisTooManyItemsError)
        _, ended = self.events("protocol_analysis_auto_reanalysis")
        self.assertEqual(ended["status"], "failed")
        (failed,) = self.events("protocol_analysis_failed")
        self.assertIsNone(failed["review_items"]["items_after"])

    def test_a_kept_repaired_answer_with_too_many_items_ends_in_the_guidance(self) -> None:
        # Found in the replay (augmented/epa_3050b__lowq): a repaired answer
        # with items whose retry broke is kept under the first attempt's id,
        # whose failure is already in the ledger; the guidance is its own event.
        many = with_made_up_notes(self.answer(), 13)
        broken = self.answer().replace('"steps":', '"stepz":', 1)
        error, model = self.analyze(many[:-1], broken, many)
        self.assertIsInstance(error, ProtocolAnalysisTooManyItemsError)
        self.assertEqual(model.calls, 3)
        codes = [failed["failure_code"] for failed in self.events("protocol_analysis_failed")]
        self.assertEqual(codes[-1], "protocol_analysis_too_many_review_items")
        self.assertIn("확인할 항목이 13건이라", self.catalog.pipeline_status(self.protocol_id)["message"])

    def test_the_setting_moves_the_bound(self) -> None:
        client = _Client(with_made_up_notes(self.answer(), 13))
        entry = self.analyze_with(client, env={"VOINEY_LAB_REVIEW_MAX_ITEMS": "20"})
        self.assertEqual(client.levels, ["low"])
        self.assertEqual(entry.review_pending["needs_review_count"], 15)  # lane MISS: +2

    def test_a_call_cap_of_zero_sends_nothing_again(self) -> None:
        client = _Client(with_made_up_notes(self.answer(), 13))
        error = self.analyze_with(client, env={"VOINEY_LAB_REVIEW_REANALYSIS_MAX_CALLS": "0"})
        self.assertIsInstance(error, ProtocolAnalysisTooManyItemsError)
        self.assertEqual(client.levels, ["low"])
        (skipped,) = self.events("protocol_analysis_auto_reanalysis")
        self.assertEqual((skipped["status"], skipped["planned_calls"], skipped["call_cap"]),
                         ("skipped_over_call_cap", 1, 0))


class ManyOcrValuesTests(_OcrCase):
    def test_one_page_of_ocr_values_is_one_card_not_the_guidance(self) -> None:
        values = ", ".join(f"{index} mL" for index in range(1, 15))
        step = f"2. Add {values}."
        scan = SCAN_TEXT.replace("2. Spin the tube at 4000 rpm for 5 min.", step)
        answer = delta_answer(self.sha256, step_two=step)
        self.catalog.run_ocr(self.protocol_id, _TwoEngineOcr(engines=("google",), text=scan),
                             ocr_id="ocr-" + "a" * 32)
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
        # Lane AN3, decision A3: the page's 15 values count once, so the
        # analysis waits for review (the guidance with OCR pages:
        # tests/test_lane_an3_ocr_two_engines.py).
        entry = self.catalog.analyze(self.protocol_id, _RawModel(answer, answer),
                                     analysis_id="analysis-" + "a" * 32)
        self.assertEqual(self.events("protocol_analysis_failed"), [])
        self.assertEqual(entry.review_pending["by_reason"], {"ocr_value": 1})


class LongDocumentTests(_CatalogCase):
    PIECES = (AnalysisPiece(1, 3, 1, 1), AnalysisPiece(2, 3, 2, 2), AnalysisPiece(3, 3, 3, 3))

    def test_a_long_document_is_not_analysed_again_and_may_ask_one_more_item_a_piece(self) -> None:
        many = with_made_up_notes(self.answer(pages=(1,)), 13)
        answers = {1: [many], 2: [self.answer(pages=(2,))], 3: [self.answer(pages=(3,))]}

        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            return answers[json.loads(input_json)["pages"][0]["source_page_number"]].pop(0)

        model = Mock()
        model.analyze.side_effect = model_answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "e" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=self.PIECES), \
                patch.dict(os.environ, {"VOINEY_LAB_REVIEW_REANALYSIS_MAX_CALLS": "5"}):
            entry = self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "e" * 32)
        # Lane AN3, round 2, decision ④: no re-analysis for a long document;
        # its limit is 12 plus its 3 pieces, so 13 items wait for review.
        self.assertEqual(entry.review_pending["needs_review_count"], 13)
        self.assertEqual(self.events("protocol_analysis_auto_reanalysis"), [])
        # Three pieces and, lane AN4 decision 1, the re-ask of piece 1's items.
        self.assertEqual(model.analyze.call_count, 4)


class SettingsAndLevelsTests(unittest.TestCase):
    def test_the_settings_read_whole_numbers_in_range(self) -> None:
        self.assertEqual(review_max_items({}), REVIEW_MAX_ITEMS)
        self.assertEqual(review_max_items({"VOINEY_LAB_REVIEW_MAX_ITEMS": "8"}), 8)
        for bad in ("0", "-1", "x", "501", ""):
            self.assertEqual(review_max_items({"VOINEY_LAB_REVIEW_MAX_ITEMS": bad}), 12)
        self.assertEqual(review_reanalysis_max_calls({}), 16)
        self.assertEqual(review_reanalysis_max_calls({"VOINEY_LAB_REVIEW_REANALYSIS_MAX_CALLS": "0"}), 0)

    def test_one_level_up_and_the_top_stays(self) -> None:
        for level, raised in (("none", "low"), ("low", "medium"), ("medium", "high"),
                              ("high", "xhigh"), ("xhigh", "xhigh")):
            model = OpenAICompatibleProtocolAnalysisModel(object(), "m", level)
            escalated, recorded = escalated_analysis_model(model)
            self.assertEqual((escalated.reasoning_effort, recorded["reasoning_effort"]), (raised, raised))
        other = Mock()
        self.assertIs(escalated_analysis_model(other)[0], other)


if __name__ == "__main__":
    unittest.main()
