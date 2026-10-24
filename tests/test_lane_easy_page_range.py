"""Analysing only part of a document (lane EASY, decision 1).

Human decision 2026-10-24: a chemistry lab runs from a paper's Methods and
will not write a separate protocol, so the upload and "다시 분석" may name a
page range. The server analyses those pages alone -- only they go to the
model, each with its own page number, the source-evidence check and the
check for lines no quote covers read them alone -- and keeps the range in
the ledger. No range is the whole document, as before. The rule that takes
unnumbered narrative paragraphs as steps (mixed numbering) is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from tests.test_lane_rv_review_pending import _CatalogCase, gamma_response
from voiney_lab import server
from voiney_lab.experiment_protocol_analysis import (
    ProtocolAnalysisPageRangeError,
    REQUOTE_SYSTEM_PROMPT,
    analysis_pieces,
    checked_page_range,
)
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    ProtocolAnalysisPageRangeInvalidError,
    ProtocolCatalog,
)


class _SeeingModel:
    """Answers in turn and keeps the page numbers each analysis call was sent."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.pages: list[list[int]] = []

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        if system_prompt == REQUOTE_SYSTEM_PROMPT:
            return json.dumps({"answers": []})
        self.pages.append([page["source_page_number"] for page in json.loads(input_json)["pages"]])
        return self.answers.pop(0)


class _RangeCase(_CatalogCase):
    def run_analysis(self, model, *, page_range=None, analysis_id="analysis-" + "e" * 32,
                     reanalysis=False):
        self.catalog.request_analysis(
            self.protocol_id, analysis_id, reanalysis=reanalysis, page_range=page_range)
        return self.catalog.analyze(self.protocol_id, model, analysis_id=analysis_id)


class OnlyTheChosenPagesAreAnalysedTests(_RangeCase):
    def test_the_model_is_sent_the_chosen_pages_with_their_own_numbers(self) -> None:
        model = _SeeingModel(self.answer(pages=(2, 3)))
        entry = self.run_analysis(model, page_range=(2, 3))
        self.assertEqual(model.pages, [[2, 3]])
        self.assertTrue(entry.available_for_execution)
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual([step.source_label for step in fixture.steps], ["3", "4", "5", "6"])

    def test_the_request_and_the_result_keep_the_range_in_the_ledger(self) -> None:
        self.run_analysis(_SeeingModel(self.answer(pages=(2, 3))), page_range=(2, 3))
        (request,) = self.events("protocol_analysis_requested")
        self.assertEqual(request["page_range"], {
            "first_page": 2, "last_page": 3, "page_count": 3, "pages_ko": "2–3쪽"})
        (ready,) = self.events("protocol_analysis_ready")
        self.assertEqual(ready["page_range"]["pages_ko"], "2–3쪽")
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["analysis_page_range"]["first_page"], 2)
        self.assertEqual(review["analysis_page_range"]["last_page"], 3)

    def test_lines_outside_the_range_are_never_possibly_missed(self) -> None:
        # Page 1 holds steps 1-2 and nobody analysed it: it is not a gap.
        self.run_analysis(_SeeingModel(self.answer(pages=(2, 3))), page_range=(2, 3))
        (ready,) = self.events("protocol_analysis_ready")
        self.assertNotIn("possibly_missed", ready)
        notices = self.catalog.review(self.protocol_id)["execution_notices"]
        self.assertNotIn("source_text_possibly_missed", [item.get("code") for item in notices])

    def test_a_gap_inside_the_range_is_still_found(self) -> None:
        # Only page 2 answered: page 3's two steps stand unquoted inside the range.
        entry = self.run_analysis(_SeeingModel(self.answer(pages=(2,))), page_range=(2, 3))
        self.assertTrue(entry.available_for_execution)
        (ready,) = self.events("protocol_analysis_ready")
        pages = {place["page"] for place in ready["possibly_missed"]["places"]}
        self.assertEqual(pages, {3})

    def test_a_quote_on_a_page_outside_the_range_is_an_item_for_a_person(self) -> None:
        # The model answered page 1 too, though it was sent pages 2-3 only.
        entry = self.run_analysis(_SeeingModel(self.answer(pages=(1, 2, 3))), page_range=(2, 3))
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertFalse(entry.available_for_execution)
        (pending,) = self.events("protocol_analysis_review_pending")
        self.assertEqual(pending["page_range"]["pages_ko"], "2–3쪽")
        self.assertTrue(pending["flags"])

    def test_no_range_reads_every_page_as_before(self) -> None:
        model = _SeeingModel(self.answer(pages=(1, 2, 3)))
        entry = self.run_analysis(model)
        self.assertEqual(model.pages, [[1, 2, 3]])
        self.assertTrue(entry.available_for_execution)
        (request,) = self.events("protocol_analysis_requested")
        self.assertNotIn("page_range", request)
        self.assertIsNone(self.catalog.review(self.protocol_id)["analysis_page_range"])

    def test_a_re_analysis_may_name_a_range_over_a_whole_analysis(self) -> None:
        self.run_analysis(_SeeingModel(self.answer(pages=(1, 2, 3))))
        model = _SeeingModel(self.answer(pages=(2, 3)))
        self.run_analysis(model, page_range=(2, 3), analysis_id="analysis-" + "f" * 32,
                          reanalysis=True)
        self.assertEqual(model.pages, [[2, 3]])
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual([step.source_label for step in fixture.steps], ["3", "4", "5", "6"])
        self.assertEqual(self.catalog.review(self.protocol_id)["analysis_page_range"]["pages_ko"],
                         "2–3쪽")


class ARangeThatIsNotTheDocumentsTests(_RangeCase):
    def test_pages_past_the_end_are_refused_before_any_request_is_kept(self) -> None:
        with self.assertRaises(ProtocolAnalysisPageRangeInvalidError):
            self.catalog.request_analysis(self.protocol_id, "analysis-" + "1" * 32,
                                          page_range=(2, 5))
        self.assertEqual(self.events("protocol_analysis_requested"), [])

    def test_reversed_or_malformed_ranges_are_refused(self) -> None:
        extraction = extract_protocol_pdf(self.pdf)
        for wrong in ((3, 2), (0, 1), ("1", 2), (1,), (True, 2)):
            with self.subTest(wrong=wrong), self.assertRaises(ProtocolAnalysisPageRangeError):
                checked_page_range(extraction, wrong)
        self.assertEqual(checked_page_range(extraction, (1, 3)), (1, 3))
        self.assertIsNone(checked_page_range(extraction, None))


class ALongDocumentsRangeTests(unittest.TestCase):
    def test_a_short_range_of_a_long_document_is_one_analysis(self) -> None:
        from tests.test_lane_miss_possibly_missed import source

        long = source(*(f"{n}. Mix sample {n} for 1 min." for n in range(1, 121)))
        self.assertTrue(analysis_pieces(long))
        self.assertEqual(analysis_pieces(long, page_range=(40, 45)), ())

    def test_a_long_range_is_read_in_pieces_within_it(self) -> None:
        from tests.test_lane_miss_possibly_missed import source

        long = source(*(f"{n}. Mix sample {n} for 1 min." for n in range(1, 201)))
        pieces = analysis_pieces(long, page_range=(101, 190))
        self.assertGreater(len(pieces), 1)
        self.assertEqual(pieces[0].first_page, 101)
        self.assertEqual(pieces[-1].last_page, 190)


class TheRoutesTakeTheRangeTests(_CatalogCase):
    """The upload, the analysis and the re-analysis routes pass the range on."""

    def open_catalog(self):
        opened = initialize_protocol_store(self.settings)
        return ProtocolCatalog(opened), opened

    def test_the_re_analysis_route_passes_the_chosen_pages(self) -> None:
        seen = {}

        async def begin(protocol_id, **kwargs):
            seen.update(kwargs)
            return {"analysis_request_accepted": True}

        with patch.object(server, "_begin_background_analysis", side_effect=begin), \
                patch.object(server, "_analysis_deferred_for_ocr", return_value=None):
            asyncio.run(server.trigger_protocol_reanalysis(self.protocol_id, first_page=2, last_page=3))
        self.assertEqual(seen["page_range"], (2, 3))
        self.assertTrue(seen["reanalysis"])

    def test_the_analysis_route_keeps_the_range_in_the_request(self) -> None:
        async def to_thread(function, /, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server.asyncio, "to_thread", side_effect=to_thread), \
                patch.object(server, "_protocol_analysis_model", return_value=Mock()), \
                patch.object(server.ProtocolCatalog, "analyze", side_effect=RuntimeError("stop")):
            async def scenario():
                await server.trigger_protocol_analysis(self.protocol_id, first_page=1, last_page=2)
                task = server._PROTOCOL_ANALYSIS_TASKS.get(self.protocol_id)
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            asyncio.run(scenario())
        server._PROTOCOL_ANALYSIS_TASKS.clear()
        (request,) = self.events("protocol_analysis_requested")
        self.assertEqual(request["page_range"]["pages_ko"], "1–2쪽")

    def test_the_upload_time_ocr_starts_the_analysis_of_the_chosen_pages(self) -> None:
        seen = {}

        async def begin(protocol_id, **kwargs):
            seen.update(kwargs)
            return {"analysis_request_accepted": True}

        async def to_thread(function, /, *args, **kwargs):
            return function(*args, **kwargs)

        async def scenario():
            await server._schedule_ocr_then_analysis(
                self.protocol_id, Mock(), ocr_id="ocr-easy", principal=None, page_range=(2, 3))

        with patch.object(server, "_open_protocol_catalog", side_effect=self.open_catalog), \
                patch.object(server.asyncio, "to_thread", side_effect=to_thread), \
                patch.object(server.ProtocolCatalog, "run_ocr", return_value={"accepted_for_analysis": True}), \
                patch.object(server, "_begin_background_analysis", side_effect=begin):
            asyncio.run(scenario())
        server._PROTOCOL_OCR_TASKS.clear()
        self.assertEqual(seen["page_range"], (2, 3))

    def test_one_end_alone_or_a_reversed_range_is_refused_by_the_route(self) -> None:
        for first, last in ((2, None), (None, 3), (3, 2), (0, 1)):
            with self.subTest(first=first, last=last), self.assertRaises(HTTPException) as caught:
                asyncio.run(server.trigger_protocol_reanalysis(
                    self.protocol_id, first_page=first, last_page=last))
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(caught.exception.detail, "analysis_page_range_invalid")

    def test_a_range_past_the_document_is_a_422_with_its_code(self) -> None:
        error = server._catalog_http_error(ProtocolAnalysisPageRangeInvalidError("x"))
        self.assertEqual((error.status_code, error.detail), (422, "analysis_page_range_invalid"))


if __name__ == "__main__":
    unittest.main()
