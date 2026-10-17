"""Pages the provider's safety filter blocks are named and asked about (lane RV2, decision f).

Lane AN2 found that the 315-page document's empty answers were Gemini prompt
blocks (``block_reason`` SAFETY), blocked again on every resend; lane GM
loosens the filter and raises what is still blocked as a ``ModelProviderError``
of kind ``blocked``. Human decision 2026-10-16: the failure says so plainly --
"구글 안전 필터에 막혀 n–m쪽을 분석하지 못했어요" -- and a blocked piece's pages
are one item ("분석 못 한 쪽") a person answers with [이 쪽엔 단계 없음] or
[다시 분석]; the pieces that passed are kept. A blocked request is not sent
again. Every provider here is fake.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab.experiment_protocol_analysis import (
    AnalysisPiece,
    OpenAICompatibleProtocolAnalysisModel,
    ProtocolAnalysisModelError,
    blocked_pages_ko,
    safety_block_provider,
)
from voiney_lab.model_providers import ModelProviderError

PIECES = (AnalysisPiece(1, 3, 1, 1), AnalysisPiece(2, 3, 2, 2), AnalysisPiece(3, 3, 3, 3))


def blocked() -> ProtocolAnalysisModelError:
    try:
        raise ModelProviderError("google", "blocked")
    except ModelProviderError as cause:
        try:
            raise ProtocolAnalysisModelError("Protocol analysis model request failed.") from cause
        except ProtocolAnalysisModelError as error:
            return error


class _BlockedCase(_CatalogCase):
    def run_pieces(self, answers: dict[int, list]):
        calls: dict[int, int] = {}

        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            page = json.loads(input_json)["pages"][0]["source_page_number"]
            calls[page] = calls.get(page, 0) + 1
            answer = answers[page].pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        model = Mock()
        model.analyze.side_effect = model_answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "e" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=PIECES):
            try:
                result = self.catalog.analyze(
                    self.protocol_id, model, analysis_id="analysis-" + "e" * 32)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                result = exc
        return result, calls


class ABlockedPieceTests(_BlockedCase):
    def test_its_pages_are_one_card_named_for_the_filter_and_the_rest_is_kept(self) -> None:
        entry, calls = self.run_pieces({
            1: [self.answer(pages=(1,))], 2: [blocked()], 3: [self.answer(pages=(3,))]})
        self.assertEqual(calls, {1: 1, 2: 1, 3: 1})  # the blocked piece is not sent again
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertIn("구글 안전 필터에 막혀 2쪽을 분석하지 못했어요", entry.review_pending["message_ko"])
        (card,) = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual((card["type"], card["item_id"]), ("blocked_pages", "pages:2-2"))
        self.assertEqual(card["reasons_ko"], ["구글 안전 필터에 막혀 2쪽을 분석하지 못했어요"])
        self.assertEqual([choice["label_ko"] for choice in card["choices"]],
                         ["이 쪽엔 단계 없음", "다시 분석"])
        failed = [event.payload for event in self.store.list_events(self.protocol_id)
                  if event.event_type == "protocol_chunk_analysis_failed"]
        self.assertEqual([payload["failure_code"] for payload in failed],
                         ["protocol_analysis_blocked"])
        result = self.catalog.record_review_decision(
            self.protocol_id, card["choices"][0]["request"])
        self.assertTrue(result["registrable"])

    def test_a_piece_that_failed_otherwise_still_reads_as_a_failed_piece(self) -> None:
        broken = self.answer(pages=(2,)).replace('"steps":', '"stepz":', 1)
        entry, calls = self.run_pieces({
            1: [self.answer(pages=(1,))], 2: [broken, broken], 3: [self.answer(pages=(3,))]})
        self.assertEqual(calls[2], 2)
        (card,) = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual(card["type"], "failed_pages")
        self.assertIn("2–2쪽 분석 실패", entry.review_pending["message_ko"])

    def test_every_piece_blocked_fails_the_document_and_says_why(self) -> None:
        error, _ = self.run_pieces({1: [blocked()], 2: [blocked()], 3: [blocked()]})
        self.assertEqual(getattr(error, "code", None), "protocol_analysis_piece_failed")
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertIn("구글 안전 필터에 막혀 1쪽을 분석하지 못했어요", line["message"])
        self.assertIn("구글 안전 필터에 막혀 3쪽을 분석하지 못했어요", line["message"])


class AWholeDocumentBlockedTests(_CatalogCase):
    def test_the_adapter_s_block_fails_the_analysis_by_name_without_a_retry(self) -> None:
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=Mock(side_effect=ModelProviderError("google", "blocked")))))
        model = OpenAICompatibleProtocolAnalysisModel(client, "gemini-test", "low")
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "a" * 32)
        with self.assertRaises(ProtocolAnalysisModelError) as caught:
            self.catalog.analyze(self.protocol_id, model, analysis_id="analysis-" + "a" * 32)
        self.assertEqual(safety_block_provider(caught.exception), "google")
        self.assertEqual(client.chat.completions.create.call_count, 1)
        (failed,) = self.events("protocol_analysis_failed")
        self.assertEqual(failed["failure_code"], "protocol_analysis_blocked")
        self.assertEqual(failed["blocked"], {"provider": "google"})
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual(line["message"], "구글 안전 필터에 막혀 1–3쪽을 분석하지 못했어요.")
        self.assertIn("안전 필터", line["action"])

    def test_an_error_of_another_kind_is_not_a_block(self) -> None:
        try:
            raise ProtocolAnalysisModelError("x") from ModelProviderError("google", "server", 503)
        except ProtocolAnalysisModelError as error:
            self.assertIsNone(safety_block_provider(error))
        self.assertEqual(blocked_pages_ko(4, 9, "openai"), "모델 공급자의 안전 필터에 막혀 4–9쪽을 분석하지 못했어요")


if __name__ == "__main__":
    unittest.main()
