"""A long document is analysed whole, in pieces (lane AN, decision 2: LD).

Human decision 2026-10-11/13. A document of more than 60 pages used to be
refused before any call (lane EV2, decision 4). It is now read in pieces of
at most 15 pages that share one page with the next, each with the analysis
prompt as it is and every check one analysis gets; the pieces are joined in
page order and checked again against the whole document. A step both pieces
read on the shared page is kept once when one reading covers the other and
refused when the two differ. A piece that fails is sent once more by itself;
failing again, it fails the document ("일부 조각 분석 실패 · n–m쪽") --
nothing passes on part of it.
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.test_lane_ev2_hollow_pass import write_pages_pdf
from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_OUTPUT_BUDGET_TOKENS,
    ANALYSIS_PIECE_MAX_PAGES,
    MAX_ANALYSIS_PAGES,
    MAX_PIECED_ANALYSIS_PAGES,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisIncompleteError,
    ProtocolAnalysisPieceConflictError,
    ProtocolAnalysisPieceFailedError,
    ProtocolAnalysisTooManyPagesError,
    analysis_pieces,
    analyze_protocol_piece,
    join_piece_analyses,
    prepare_protocol_piece_request,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

TITLE = "Plasmid preparation"


def extraction(*pages: str) -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="long.pdf", byte_size=1, sha256="c" * 64,
        media_type="application/pdf", page_count=len(pages), encrypted=False,
        metadata=ProtocolPdfMetadata(None, None, None, None, None, None, None),
        pages=tuple(ProtocolPdfPage(n, text, False) for n, text in enumerate(pages, start=1)),
    )


def long_document(page_count: int, steps_per_page: int = 2) -> ProtocolPdfExtraction:
    """A title on page 1 and numbered steps, ``steps_per_page`` per page."""

    pages = []
    label = 1
    for number in range(1, page_count + 1):
        lines = [TITLE] if number == 1 else []
        for _ in range(steps_per_page):
            lines.append(f"{label}. Add {label} mL of buffer {label} to tube {label}.")
            label += 1
        pages.append("\n".join(lines))
    return extraction(*pages)


_LINE = re.compile(r"^(\d+)\. (.*)$", re.MULTILINE)


class PieceModel:
    """Answers each piece with every numbered line of its pages, quoted exactly.

    ``broken`` maps a piece's first page to how many of its answers are not
    JSON; ``skip`` names step labels left out of every answer; ``edit``
    rewrites one step's quote in the answer of the piece starting at a page.
    """

    def __init__(
        self,
        source: ProtocolPdfExtraction,
        *,
        broken: dict[int, int] | None = None,
        skip: frozenset[str] = frozenset(),
        edit: dict[tuple[int, str], str] | None = None,
        hold: threading.Event | None = None,
    ) -> None:
        self.source = source
        self.calls: list[tuple[int, int]] = []
        self.requests: list[dict] = []
        self.broken = dict(broken or {})
        self.skip = skip
        self.edit = dict(edit or {})
        self.hold = hold
        self.lock = threading.Lock()

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        request = json.loads(input_json)
        pages = [page["source_page_number"] for page in request["pages"]]
        with self.lock:
            self.calls.append((pages[0], pages[-1]))
            self.requests.append(request)
            broken = self.broken.get(pages[0], 0) > 0
            if broken:
                self.broken[pages[0]] -= 1
        if self.hold is not None and pages[0] > 1:
            self.hold.wait(10)
        if broken:
            return "not json"
        steps = []
        for page in request["pages"]:
            for match in _LINE.finditer(page["text"]):
                if match[1] in self.skip:
                    continue
                quote = self.edit.get((pages[0], match[1]), match[0])
                steps.append({
                    "step_id": f"step-{match[1]}", "source_label": match[1],
                    "instruction_source_text": re.sub(r"^\d+\. ", "", quote),
                    "evidence": {"source_page_number": page["source_page_number"],
                                 "source_excerpt": quote},
                })
        first = request["pages"][0]
        title_line = first["text"].splitlines()[0]
        return json.dumps({
            "analysis_schema_version": 1, "pdf_sha256": self.source.sha256,
            "capability_policy_id": "p1-conservative",
            "protocol": {
                "protocol_id": "protocol-long",
                "metadata": {"title": title_line, "original_language": "en",
                             "evidence": {"source_page_number": first["source_page_number"],
                                          "source_excerpt": title_line}},
                "before_start": [], "materials": [], "equipment": [],
                "sections": [{
                    "section_id": "procedure", "title_source_text": title_line,
                    "evidence": {"source_page_number": first["source_page_number"],
                                 "source_excerpt": title_line},
                    "steps": steps,
                }],
                "constructs": [], "description": None,
            },
        })


class ThePlanTests(unittest.TestCase):
    def test_sixty_pages_are_one_analysis(self):
        self.assertEqual(MAX_ANALYSIS_PAGES, 60)
        self.assertEqual(analysis_pieces(long_document(60)), ())

    def test_more_pages_are_pieces_of_at_most_fifteen_sharing_one_page(self):
        self.assertEqual(ANALYSIS_PIECE_MAX_PAGES, 15)
        pieces = analysis_pieces(long_document(197, steps_per_page=1))
        self.assertEqual(len(pieces), 14)
        self.assertEqual((pieces[0].first_page, pieces[-1].last_page), (1, 197))
        for before, after in zip(pieces, pieces[1:]):
            self.assertEqual(after.first_page, before.last_page)
        self.assertLessEqual(max(p.last_page - p.first_page + 1 for p in pieces), ANALYSIS_PIECE_MAX_PAGES)
        self.assertEqual(min(p.last_page - p.first_page + 1 for p in pieces), 15)
        self.assertEqual([p.index for p in pieces], list(range(1, 15)))
        self.assertEqual({p.count for p in pieces}, {14})

    def test_a_shorter_document_too_dense_for_one_answer_is_pieces_too(self):
        dense = "\n".join(f"{n}. " + "Add buffer and mix well. " * 20 for n in range(1, 21))
        source = extraction(*([dense] * 30))
        self.assertGreater(
            analysis_module.estimated_output_tokens(source.pages), ANALYSIS_OUTPUT_BUDGET_TOKENS)
        pieces = analysis_pieces(source)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(
                analysis_module.estimated_output_tokens(
                    source.pages[piece.first_page - 1:piece.last_page]),
                ANALYSIS_OUTPUT_BUDGET_TOKENS)

    def test_one_page_is_never_pieces(self):
        dense = "\n".join(f"{n}. " + "Add buffer and mix well. " * 60 for n in range(1, 80))
        self.assertEqual(analysis_pieces(extraction(dense)), ())

    def test_the_piece_request_holds_its_pages_and_names_the_part(self):
        source = long_document(61)
        piece = analysis_pieces(source)[1]
        request = json.loads(prepare_protocol_piece_request(source, piece).as_json())
        self.assertEqual(
            [page["source_page_number"] for page in request["pages"]],
            list(range(piece.first_page, piece.last_page + 1)))
        self.assertEqual(request["pdf"]["page_count"], 61)
        self.assertEqual(request["document_part"], {
            "index": 2, "count": 5, "first_page": 13, "last_page": 25})
        # A whole-document request is unchanged: no document_part.
        whole = json.loads(analysis_module.prepare_protocol_analysis_request(
            long_document(5)).as_json())
        self.assertNotIn("document_part", whole)


class APieceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = long_document(61)
        self.pieces = analysis_pieces(self.source)

    def test_a_piece_is_checked_against_the_whole_document_with_its_own_page_numbers(self):
        draft = analyze_protocol_piece(self.source, self.pieces[1], PieceModel(self.source))
        steps = draft.protocol.sections[0].steps
        self.assertEqual((steps[0].source_label, steps[-1].source_label), ("25", "50"))
        self.assertEqual(steps[0].evidence.source_page_number, 13)

    def test_a_quote_from_outside_the_piece_is_refused(self):
        inner = PieceModel(self.source)

        class CitesPageOne:
            def analyze(self, **request):
                reply = json.loads(inner.analyze(**request))
                step = reply["protocol"]["sections"][0]["steps"][0]
                step["evidence"] = {"source_page_number": 1,
                                    "source_excerpt": "1. Add 1 mL of buffer 1 to tube 1."}
                step["source_label"] = "1"
                step["instruction_source_text"] = "Add 1 mL of buffer 1 to tube 1."
                return json.dumps(reply)

        model = CitesPageOne()
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            analyze_protocol_piece(self.source, self.pieces[1], model)
        self.assertEqual(raised.exception.diagnostic.reason_code, "page_outside_piece")

    def test_a_quote_not_on_its_page_is_refused_as_in_one_analysis(self):
        model = PieceModel(self.source, edit={(13, "25"): "25. Add 25 mL of buffer 9 to tube 25."})
        with self.assertRaises(ProtocolAnalysisEvidenceError) as raised:
            analyze_protocol_piece(self.source, self.pieces[1], model)
        self.assertEqual(raised.exception.diagnostic.reason_code, "quote_not_found")

    def test_a_hollow_piece_is_refused(self):
        source = long_document(61, steps_per_page=3)
        piece = analysis_pieces(source)[0]
        model = PieceModel(source, skip=frozenset(str(n) for n in range(4, 49)))
        with self.assertRaises(ProtocolAnalysisIncompleteError):
            analyze_protocol_piece(source, piece, model)


class TheJoinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = long_document(61)
        self.pieces = analysis_pieces(self.source)

    def drafts(self, model):
        return [analyze_protocol_piece(self.source, piece, model) for piece in self.pieces]

    def test_the_pieces_join_in_page_order_with_the_shared_pages_read_once(self):
        joined = join_piece_analyses(self.source, self.pieces, self.drafts(PieceModel(self.source)))
        steps = [step for section in joined.protocol.sections for step in section.steps]
        self.assertEqual([step.source_label for step in steps], [str(n) for n in range(1, 123)])
        self.assertEqual(len({step.step_id for step in steps}), 122)
        self.assertEqual(steps[0].step_id, "p1.step-1")
        # Steps 25-26 are on page 13, which pieces 1 and 2 share: piece 1's reading stays.
        self.assertEqual([s.step_id for s in steps if s.source_label in {"25", "26"}],
                         ["p1.step-25", "p1.step-26"])
        self.assertEqual(steps[-1].step_id, "p5.step-122")
        self.assertEqual(joined.protocol.metadata.title, TITLE)

    def test_a_shared_step_read_whole_by_the_later_piece_replaces_the_shorter_reading(self):
        model = PieceModel(self.source, edit={(1, "25"): "25. Add 25 mL of buffer 25"})
        joined = join_piece_analyses(self.source, self.pieces, self.drafts(model))
        step = next(step for section in joined.protocol.sections for step in section.steps
                    if step.source_label == "25")
        self.assertEqual(step.evidence.source_excerpt, "25. Add 25 mL of buffer 25 to tube 25.")
        self.assertEqual(step.step_id, "p1.step-25")

    def test_two_readings_of_a_shared_step_that_differ_are_refused(self):
        model = PieceModel(self.source, edit={
            (1, "25"): "25. Add 25 mL of buffer 25",
            (13, "25"): "Add 25 mL of buffer 25 to tube 25.",
        })
        with self.assertRaises(ProtocolAnalysisPieceConflictError) as raised:
            join_piece_analyses(self.source, self.pieces, self.drafts(model))
        self.assertEqual(raised.exception.reason, "overlap_step_differs")
        self.assertEqual(raised.exception.code, "protocol_analysis_piece_conflict")

    def test_the_joined_analysis_is_held_to_the_whole_documents_numbered_steps(self):
        # Each piece let through on its own (its bound turned off), the join
        # still counts every numbered step the document prints.
        model = PieceModel(self.source, skip=frozenset(str(n) for n in range(5, 123)))
        with mock.patch.object(analysis_module, "HOLLOW_STEP_SHARE", 0.0):
            drafts = self.drafts(model)
        with self.assertRaises(ProtocolAnalysisIncompleteError) as raised:
            join_piece_analyses(self.source, self.pieces, drafts)
        self.assertEqual((raised.exception.source_numbered_steps, raised.exception.analysis_steps),
                         (122, 4))


def page_texts(page_count: int, steps_per_page: int = 2) -> list[str]:
    return [page.text for page in long_document(page_count, steps_per_page).pages]


class _CatalogCase(unittest.TestCase):
    ANALYSIS = "analysis-" + "d" * 32

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def register(self, pages: list[str]) -> str:
        pdf = self.root / "long.pdf"
        write_pages_pdf(pdf, pages)
        self.extraction = extract_protocol_pdf(pdf)
        return self.catalog.register(
            pdf, source_filename="long.pdf", media_type="application/pdf").entry.protocol_id

    def analyze(self, protocol_id: str, model) -> Exception | None:
        self.catalog.request_analysis(protocol_id, self.ANALYSIS)
        try:
            self.catalog.analyze(protocol_id, model, analysis_id=self.ANALYSIS)
        except Exception as exc:  # noqa: BLE001 - the test reads it
            return exc
        return None

    def events(self, protocol_id: str, event_type: str) -> list:
        return [event for event in self.store.list_events(protocol_id)
                if event.event_type == event_type]


class CatalogPieceTests(_CatalogCase):
    """The catalog runs the pieces, records them, retries a piece once, and joins."""

    def test_a_document_over_sixty_pages_is_read_in_pieces_and_passes(self):
        protocol_id = self.register(page_texts(61))
        self.assertEqual(self.catalog.get_entry(protocol_id).analysis_status,
                         "chunked_analysis_required")
        model = PieceModel(self.extraction)
        self.assertIsNone(self.analyze(protocol_id, model))
        self.assertEqual(sorted(model.calls), [(1, 13), (13, 25), (25, 37), (37, 49), (49, 61)])
        entry = self.catalog.get_entry(protocol_id)
        self.assertEqual(entry.step_count, 122)
        (plan,) = self.events(protocol_id, "protocol_chunk_plan_created")
        self.assertEqual([(c["source_page_start"], c["source_page_end"]) for c in plan.payload["chunks"]],
                         [(1, 13), (13, 25), (25, 37), (37, 49), (49, 61)])
        self.assertEqual(len(self.events(protocol_id, "protocol_chunk_analysis_completed")), 5)
        (ready,) = self.events(protocol_id, "protocol_analysis_ready")
        self.assertEqual(len(ready.payload["pieces"]), 5)
        status = self.catalog.analysis_run_status(protocol_id).public_dict()
        self.assertEqual((status["state"], status["total_chunks"], status["completed_chunks"]),
                         ("review_required", 5, 5))
        self.assertEqual(status["merge_status"], "complete")

    def test_a_piece_that_fails_is_sent_once_more_by_itself(self):
        protocol_id = self.register(page_texts(61))
        model = PieceModel(self.extraction, broken={13: 1})
        self.assertIsNone(self.analyze(protocol_id, model))
        self.assertEqual(model.calls.count((13, 25)), 2)
        self.assertEqual(model.calls.count((1, 13)), 1)
        retries = [event.payload for event in self.events(protocol_id, "protocol_chunk_analysis_started")
                   if event.payload.get("attempt") == 2]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["chunk_id"], "piece-2")
        self.assertEqual(retries[0]["retry_of_failure_code"], "protocol_analysis_invalid_response")
        self.assertEqual(self.catalog.get_entry(protocol_id).step_count, 122)

    def test_a_piece_that_fails_again_fails_the_whole_document_and_names_its_pages(self):
        protocol_id = self.register(page_texts(61))
        model = PieceModel(self.extraction, broken={25: 2})
        error = self.analyze(protocol_id, model)
        self.assertIsInstance(error, ProtocolAnalysisPieceFailedError)
        self.assertEqual(error.pages_ko, "25–37쪽")
        self.assertEqual(model.calls.count((25, 37)), 2)
        self.assertEqual(self.catalog.get_entry(protocol_id).revision_id, "pdf-1")  # nothing stored
        self.assertFalse(self.catalog.get_entry(protocol_id).available_for_execution)
        (failed,) = self.events(protocol_id, "protocol_analysis_failed")
        self.assertEqual(failed.payload["failure_code"], "protocol_analysis_piece_failed")
        self.assertEqual(failed.payload["failed_pieces"], [{
            "index": 3, "count": 5, "first_page": 25, "last_page": 37,
            "failure_code": "protocol_analysis_invalid_response"}])
        pipeline = self.catalog.pipeline_status(protocol_id)
        self.assertTrue(pipeline["blocked"])
        self.assertTrue(pipeline["message"].startswith("일부 조각 분석 실패 · 25–37쪽"))
        self.assertIn("분석 다시 시도", pipeline["action"])
        status = self.catalog.analysis_run_status(protocol_id).public_dict()
        self.assertEqual(status["state"], "chunk_analysis_failed")
        self.assertEqual(status["failure_code"], "protocol_analysis_piece_failed")
        self.assertEqual(status["failed_chunks"], 1)
        self.assertEqual(self.catalog.get_entry(protocol_id).analysis_status, "chunk_analysis_failed")
        self.assertTrue(self.catalog.review(protocol_id)["analysis_failure"]["retryable"])

    def test_two_pieces_reading_the_shared_page_differently_fail_the_document(self):
        protocol_id = self.register(page_texts(61))
        model = PieceModel(self.extraction, edit={
            (1, "25"): "25. Add 25 mL of buffer 25",
            (13, "25"): "Add 25 mL of buffer 25 to tube 25.",
        })
        error = self.analyze(protocol_id, model)
        self.assertIsInstance(error, ProtocolAnalysisPieceConflictError)
        (failed,) = self.events(protocol_id, "protocol_analysis_failed")
        self.assertEqual(failed.payload["failure_code"], "protocol_analysis_piece_conflict")
        self.assertEqual(self.catalog.get_entry(protocol_id).analysis_status, "merge_conflict")
        self.assertIn("서로 다르게 읽은", self.catalog.pipeline_status(protocol_id)["message"])

    def test_more_pages_than_the_pieces_read_is_refused_before_any_call(self):
        pages = page_texts(MAX_PIECED_ANALYSIS_PAGES + 1, steps_per_page=1)
        protocol_id = self.register(pages)
        model = PieceModel(self.extraction)
        error = self.analyze(protocol_id, model)
        self.assertIsInstance(error, ProtocolAnalysisTooManyPagesError)
        self.assertEqual(model.calls, [])
        self.assertIn(f"{MAX_PIECED_ANALYSIS_PAGES}쪽", self.catalog.pipeline_status(protocol_id)["message"])

    def test_the_progress_line_says_which_pieces_are_being_read(self):
        protocol_id = self.register(page_texts(61))
        hold = threading.Event()
        model = PieceModel(self.extraction, hold=hold)
        settings = ProtocolPersistenceSettings(True, self.root / "catalog")
        outcome: list[object] = []

        def run() -> None:
            store = initialize_protocol_store(settings)
            try:
                catalog = ProtocolCatalog(store)
                catalog.request_analysis(protocol_id, self.ANALYSIS)
                catalog.analyze(protocol_id, model, analysis_id=self.ANALYSIS)
                outcome.append("passed")
            except Exception as exc:  # noqa: BLE001
                outcome.append(exc)
            finally:
                store.close()

        worker = threading.Thread(target=run)
        worker.start()
        try:
            line = ""
            for _ in range(200):
                line = self.catalog.pipeline_status(protocol_id)["message"]
                if "끝난 조각 1/5" in line:
                    break
                threading.Event().wait(0.05)
            self.assertIn("긴 문서를 조각 5개로 나눠 분석 중입니다", line)
            self.assertIn("조각 2·3·4·5/5 분석 중", line)
            self.assertIn("끝난 조각 1/5", line)
        finally:
            hold.set()
            worker.join(30)
        self.assertEqual(outcome, ["passed"])


class AnOldPageRefusalTests(_CatalogCase):
    """A document lane EV2 refused for its 60+ pages may be asked again now."""

    def test_the_old_refusal_is_said_as_such_and_may_be_retried(self):
        protocol_id = self.register(page_texts(61))
        self.catalog.request_analysis(protocol_id, self.ANALYSIS)
        # The ledger of a refusal made before lane AN (no piece event).
        self.store.append_event(
            "analysis-failed-old", protocol_id, 1, "protocol_analysis_failed",
            {"status": "failed", "failure_code": "protocol_analysis_too_many_pages"},
        )
        review = self.catalog.review(protocol_id)
        self.assertTrue(review["analysis_failure"]["retryable"])
        self.assertIn("조각으로 나눠", review["analysis_failure"]["action"])
        self.assertIn("예전 한도(한 번에 60쪽)", review["pipeline"]["message"])
        self.assertIn("분석 다시 시도", review["pipeline"]["action"])
        # Asked again, it is read in pieces.
        model = PieceModel(self.extraction)
        self.assertIsNone(self.analyze(protocol_id, model))
        self.assertEqual(self.catalog.get_entry(protocol_id).step_count, 122)
