"""An analysis with items the source does not hold waits for a person (lane RV, decision a).

Human decision 2026-10-12, review scope recommended 2026-10-14: a quote or
claim the server cannot find in the source no longer throws the whole
analysis away. The item is kept as the model read it and marked "확인 필요"
with its reason -- 근거를 못 찾음 / OCR 흐림 / 애매한 반복 / 원문에 없음 / 조각
실패 쪽 -- and the analysis is kept as one waiting for review ("검토 대기"),
which never runs. A response that breaks the structure, and an analysis
with far fewer steps than the source numbers, fail as before. A long
document keeps the pieces that passed; a failed piece's pages become one
item, "확인 필요 · n–m쪽 분석 실패".
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from tests.test_lane_ev2_cross_page import THAW, evidence, note, response, source, step
from voiney_lab import experiment_protocol as domain
from voiney_lab import server
from voiney_lab.experiment_protocol_analysis import (
    AnalysisPiece,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
    ProtocolEvidenceDiagnostic,
    REVIEW_REASON_AMBIGUOUS_REPEAT,
    REVIEW_REASON_EVIDENCE_NOT_FOUND,
    REVIEW_REASON_NOT_IN_SOURCE,
    REVIEW_REASON_OCR_UNCLEAR,
    REQUOTE_SYSTEM_PROMPT,
    _review_reason,
    parse_protocol_analysis_response,
    review_item_id,
)
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    ProtocolCatalog,
    ProtocolCatalogUnavailableError,
)

MADE_UP = "1. Spin the tube at 20,000 x g for an hour."
#: Lane MISS (human decision 2026-10-23): an analysis that waits for review
#: also asks about the source lines that read like an instruction and that no
#: quote covers. Gamma's answer reads page 1 only, and MADE_UP stands where
#: page 1's step 2 is printed, so such an analysis also asks about those lines.
MISSED_WITH_MADE_UP = ["missed:1:4-4", "missed:2:2-3", "missed:3:2-3"]
MISSED_PAGES_2_3 = ["missed:2:2-3", "missed:3:2-3"]


def other_cards(cards: list[dict]) -> list[dict]:
    """The cards other than lane MISS's possibly-missed ones."""

    return [card for card in cards if card["type"] != "possibly_missed"]


def parse_for_review(payload: dict, extraction=None):
    return parse_protocol_analysis_response(
        json.dumps(payload), extraction or source(), review_unverified=True)


class ItemsForAPersonTests(unittest.TestCase):
    """The parse: refused items are kept and named, the rest verified."""

    def test_a_quote_the_source_does_not_hold_is_an_item_for_a_person(self) -> None:
        draft = parse_for_review(response(step("1", THAW, THAW), step("2", MADE_UP, MADE_UP)))
        (flag,) = draft.review_flags
        self.assertEqual(flag.item_id, "step:step-2-1")
        self.assertEqual(flag.field, "evidence")
        self.assertEqual(flag.reason, REVIEW_REASON_NOT_IN_SOURCE)
        self.assertEqual(flag.reason_ko, "원문에 없음")
        self.assertEqual(flag.reason_code, "quote_not_found")
        # Kept as the model read it; the verified step is the page's own text.
        kept = draft.protocol.sections[0].steps[1]
        self.assertEqual(kept.instruction_source_text, MADE_UP)
        self.assertEqual(kept.evidence.source_excerpt, MADE_UP)
        self.assertEqual(draft.protocol.sections[0].steps[0].evidence.source_excerpt, THAW)
        self.assertEqual(draft.verified_evidence_count, 3)

    def test_without_review_the_same_response_is_refused_as_before(self) -> None:
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse_protocol_analysis_response(
                json.dumps(response(step("1", THAW, THAW), step("2", MADE_UP, MADE_UP))),
                source())

    def test_an_analysis_that_passes_every_check_has_no_item(self) -> None:
        draft = parse_for_review(response(step("1", THAW, THAW)))
        self.assertEqual(draft.review_flags, ())
        plain = parse_protocol_analysis_response(json.dumps(response(step("1", THAW, THAW))), source())
        self.assertEqual(draft.protocol, plain.protocol)
        self.assertEqual(draft.readiness, plain.readiness)

    def test_a_quote_printed_on_another_page_is_evidence_not_found(self) -> None:
        wrong_page = step("1", THAW, THAW, page=2)
        (flag,) = parse_for_review(response(wrong_page)).review_flags
        self.assertEqual(flag.reason, REVIEW_REASON_EVIDENCE_NOT_FOUND)
        self.assertEqual(flag.matching_source_pages, (1,))

    def test_a_claim_the_page_does_not_print_under_a_verified_quote(self) -> None:
        draft = parse_for_review(response(step("1", "1. Thaw it at 4 C.", THAW)))
        (flag,) = draft.review_flags
        self.assertEqual((flag.item_id, flag.field), ("step:step-1-1", "instruction_source_text"))
        self.assertEqual(flag.reason, REVIEW_REASON_NOT_IN_SOURCE)
        # The quote itself was found: it is the page's text.
        self.assertEqual(draft.protocol.sections[0].steps[0].evidence.source_excerpt, THAW)

    def test_a_note_s_quote_is_its_own_item_and_the_step_stays_verified(self) -> None:
        draft = parse_for_review(response(
            step("1", THAW, THAW, notes=[note("Keep cold.", "Keep cold.")])))
        (flag,) = draft.review_flags
        self.assertEqual(flag.item_id, "statement:note-1")

    def test_a_refused_quote_on_an_ocr_page_is_ocr_unclear(self) -> None:
        extraction = source()
        pages = list(extraction.pages)
        pages[0] = replace(pages[0], ocr_derived=True)
        extraction = replace(extraction, pages=tuple(pages))
        (flag,) = parse_for_review(
            response(step("1", THAW, THAW), step("2", MADE_UP, MADE_UP)), extraction).review_flags
        self.assertEqual(flag.reason, REVIEW_REASON_OCR_UNCLEAR)

    def test_an_ambiguous_match_is_an_ambiguous_repeat(self) -> None:
        error = ProtocolAnalysisEvidenceError("x", diagnostic=ProtocolEvidenceDiagnostic(
            validation_stage="source_evidence_verification",
            reason_code="ambiguous_source_match", mismatch_class="ambiguous_normalized_span",
            page_number=1, matching_source_pages=(1,)))
        self.assertEqual(_review_reason(error, source()), REVIEW_REASON_AMBIGUOUS_REPEAT)

    def test_a_field_execution_never_reads_is_still_emptied_not_flagged(self) -> None:
        # Lane EV2, decision 2 is kept: a title the source does not print is
        # emptied and recorded; it is not an item.
        payload = response(step("1", THAW, THAW))
        payload["protocol"]["metadata"]["title"] = "A Title Nobody Printed"
        draft = parse_for_review(payload)
        self.assertEqual(draft.review_flags, ())
        self.assertIn("metadata.title", draft.protocol.cleared_fields)

    def test_nothing_verified_is_still_a_refused_analysis(self) -> None:
        payload = response(step("1", MADE_UP, MADE_UP))
        payload["protocol"]["metadata"]["evidence"] = evidence("Nobody printed this title")
        payload["protocol"]["sections"][0]["evidence"] = evidence("Nor this heading")
        with self.assertRaises(ProtocolAnalysisEvidenceError):
            parse_for_review(payload)

    def test_a_broken_response_still_fails(self) -> None:
        broken = json.dumps(response(step("1", THAW, THAW))).replace('"steps":', '"stepz":', 1)
        with self.assertRaises(ProtocolAnalysisResponseError):
            parse_protocol_analysis_response(broken, source(), review_unverified=True)

    def test_an_answer_whose_shape_fails_beside_its_items_is_refused_as_before(self) -> None:
        # Decision a: an analysis a person could not review fails as before.
        # The answer that resolves an ambiguity itself (a shape every analysis
        # is refused for) beside a made-up step was refused for the step's
        # quote, which is not sent again; reading on for review must not turn
        # it into a broken-response failure that is.
        payload = response(step("1", THAW, THAW), step("2", MADE_UP, MADE_UP))
        payload["protocol"]["constructs"] = [{
            "type": "source_ambiguity", "ambiguity_id": "which-tube",
            "source_text": THAW, "evidence": evidence(THAW),
            "resolved": True, "resolution_source_text": "The model decided.",
        }]
        with self.assertRaises(ProtocolAnalysisEvidenceError) as plain:
            parse_protocol_analysis_response(json.dumps(payload), source())
        with self.assertRaises(ProtocolAnalysisEvidenceError) as kept:
            parse_for_review(payload)
        self.assertEqual(kept.exception.diagnostic.reason_code, plain.exception.diagnostic.reason_code)
        self.assertEqual(kept.exception.diagnostic.field_path, plain.exception.diagnostic.field_path)

    def test_values_are_named_by_their_record_field_and_place(self) -> None:
        payload = response(step("1", THAW, THAW))
        payload["protocol"]["sections"][0]["steps"][0]["sub_actions"] = [{
            "action_id": "thaw-it", "instruction_source_text": THAW, "evidence": evidence(THAW),
            "quantities": [{"source_text": "37 C"}],
        }]
        draft = parse_for_review(payload)
        (flag,) = draft.review_flags
        self.assertEqual(flag.item_id, "action:thaw-it/quantities/0")
        self.assertEqual(flag.field, "source_text")
        self.assertEqual(
            review_item_id(
                draft.protocol,
                "protocol.sections[0].steps[0].sub_actions[0].quantities[0].source_text"),
            "action:thaw-it/quantities/0")


def write_pages_pdf(path: Path, pages: list[list[str]], *, title: str) -> None:
    """A PDF whose pages print these lines, one text line each."""

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for lines in pages:
        page = writer.add_blank_page(width=612, height=792)
        content = DecodedStreamObject()
        ops = ["BT /F1 11 Tf 14 TL 72 740 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({escaped}) Tj T*")
        ops.append("ET")
        content.set_data(" ".join(ops).encode("ascii"))
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_metadata({"/Title": title})
    with path.open("wb") as stream:
        writer.write(stream)


PAGES = [
    ["Protocol Gamma", "Section preparation", "1. Add 2 mL of buffer A.",
     "2. Incubate for 10 min at 37 C.", "Wear gloves."],
    ["Section wash", "3. Wash the pellet twice.", "4. Spin at 500 x g for 5 min."],
    ["Section finish", "5. Store the tube at 4 C.", "6. Record the volume."],
]


class _RawModel:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.reasks = 0

    def analyze(self, *, system_prompt: str, input_json: str, response_schema: dict) -> str:
        if system_prompt == REQUOTE_SYSTEM_PROMPT:
            # Lane AN4, decision 1: the refused items are asked about once
            # more; this model finds nothing for them, and ``calls`` and the
            # answers stay the analysis calls'.
            self.reasks += 1
            return json.dumps({"answers": []})
        self.calls += 1
        return self.answers.pop(0)


def gamma_response(sha256: str, *, step_two: str = "2. Incubate for 10 min at 37 C.",
                   pages: tuple[int, ...] = (1,), prefix: str = "") -> str:
    """The protocol of PAGES as a model would answer for the pages given."""

    ev = lambda excerpt, page: {"source_page_number": page, "source_excerpt": excerpt}
    sections = []
    if 1 in pages:
        sections.append({
            "section_id": f"{prefix}prep", "title_source_text": "Section preparation",
            "evidence": ev("Section preparation", 1),
            "steps": [
                {"step_id": f"{prefix}s1", "source_label": "1",
                 "instruction_source_text": "1. Add 2 mL of buffer A.",
                 "evidence": ev("1. Add 2 mL of buffer A.", 1)},
                {"step_id": f"{prefix}s2", "source_label": "2",
                 "instruction_source_text": step_two, "evidence": ev(step_two, 1),
                 "warnings": [{"statement_id": f"{prefix}gloves", "source_text": "Wear gloves.",
                               "evidence": ev("Wear gloves.", 1)}]},
            ],
        })
    if 2 in pages:
        sections.append({
            "section_id": f"{prefix}wash", "title_source_text": "Section wash",
            "evidence": ev("Section wash", 2),
            "steps": [
                {"step_id": f"{prefix}s3", "source_label": "3",
                 "instruction_source_text": "3. Wash the pellet twice.",
                 "evidence": ev("3. Wash the pellet twice.", 2)},
                {"step_id": f"{prefix}s4", "source_label": "4",
                 "instruction_source_text": "4. Spin at 500 x g for 5 min.",
                 "evidence": ev("4. Spin at 500 x g for 5 min.", 2)},
            ],
        })
    if 3 in pages:
        sections.append({
            "section_id": f"{prefix}finish", "title_source_text": "Section finish",
            "evidence": ev("Section finish", 3),
            "steps": [
                {"step_id": f"{prefix}s5", "source_label": "5",
                 "instruction_source_text": "5. Store the tube at 4 C.",
                 "evidence": ev("5. Store the tube at 4 C.", 3)},
                {"step_id": f"{prefix}s6", "source_label": "6",
                 "instruction_source_text": "6. Record the volume.",
                 "evidence": ev("6. Record the volume.", 3)},
            ],
        })
    first = min(pages)
    title_page = 1 if 1 in pages else first
    title_quote = "Protocol Gamma" if 1 in pages else PAGES[first - 1][0]
    return json.dumps({
        "analysis_schema_version": 1, "pdf_sha256": sha256,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "gamma",
            "metadata": {"title": title_quote, "original_language": "en",
                         "evidence": ev(title_quote, title_page)},
            "before_start": [], "materials": [], "equipment": [],
            "sections": sections, "constructs": [], "description": None,
        },
    })


class _CatalogCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clear_protocol_pdf_cache()
        self.settings = ProtocolPersistenceSettings(True, root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.catalog = ProtocolCatalog(self.store)
        self.pdf = root / "gamma.pdf"
        write_pages_pdf(self.pdf, PAGES, title="Protocol Gamma")
        self.sha256 = extract_protocol_pdf(self.pdf).sha256
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="gamma.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        clear_protocol_pdf_cache()
        self.temp.cleanup()

    def answer(self, **kwargs) -> str:
        return gamma_response(self.sha256, **kwargs)

    def analyze(self, *answers: str, analysis_id: str = "analysis-" + "a" * 32,
                reanalysis: bool = False):
        self.catalog.request_analysis(self.protocol_id, analysis_id, reanalysis=reanalysis)
        model = _RawModel(*answers)
        try:
            return self.catalog.analyze(self.protocol_id, model, analysis_id=analysis_id), model
        except Exception as exc:  # noqa: BLE001 - the caller looks at it
            return exc, model

    def events(self, event_type: str) -> list[dict]:
        return [event.payload for event in self.store.list_events(self.protocol_id)
                if event.event_type == event_type]

    def confirm_possibly_missed(self) -> list[str]:
        """Lane MISS: [단계가 아니에요] on every possibly-missed card, as a
        person does before registering; the item ids answered."""

        answered = []
        for card in self.catalog.review_draft(self.protocol_id)["cards"]:
            if card["type"] == "possibly_missed" and not card["decided"]:
                self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
                answered.append(card["item_id"])
        return answered


class TheCatalogKeepsItForReviewTests(_CatalogCase):
    def test_an_analysis_with_an_item_waits_for_review_and_never_runs(self) -> None:
        entry, model = self.analyze(self.answer(step_two=MADE_UP))
        self.assertEqual(model.calls, 1)
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(entry.lifecycle_state, "review_pending")
        self.assertFalse(entry.available_for_execution)
        # Lane MISS: and the three places of the source no quote covers.
        self.assertEqual(entry.review_pending["needs_review_count"], 4)
        self.assertEqual(entry.review_pending["by_reason"],
                         {"not_in_source": 1, "possibly_missed": 3})
        self.assertEqual(
            entry.review_pending["message_ko"],
            "검토 대기 · 확인 필요 4건(원문에 없음 1 · 분석에 없는 원문일 수 있어요 3)")
        # Not an analysis revision: nothing that runs a protocol can read it.
        self.assertEqual(self.store.list_analysis_revisions(self.protocol_id, 1), ())
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_executable_fixture(self.protocol_id)
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.load_analysis_fixture(self.protocol_id)
        self.assertEqual(self.events("protocol_analysis_failed"), [])
        run = self.catalog.analysis_run_status(self.protocol_id)
        self.assertEqual(run.state, "review_pending")
        self.assertIsNone(run.failure_code)
        line = self.catalog.pipeline_status(self.protocol_id)
        self.assertEqual((line["stage"], line["stage_ko"], line["blocked"]), ("review", "검토", True))
        self.assertIn("검토 대기 · 확인 필요 4건", line["message"])
        self.assertIn("검토 완료하고 등록", line["action"])
        review = self.catalog.review(self.protocol_id)
        self.assertFalse(review["analysis_available"])
        self.assertEqual(review["review_pending"]["needs_review_count"], 4)
        self.assertIsNone(review["analysis_failure"])

    def test_the_kept_analysis_reads_back_with_its_pages_and_items(self) -> None:
        self.analyze(self.answer(step_two=MADE_UP))
        protocol, readiness, flags, _ = self.catalog.review_pending_analysis(self.protocol_id)
        self.assertEqual(protocol.protocol_id, self.protocol_id)
        self.assertEqual(protocol.metadata.pdf.page_count, 3)
        self.assertIn("1. Add 2 mL of buffer A.", protocol.metadata.pdf.pages[0].text)
        self.assertEqual(protocol.sections[0].steps[1].instruction_source_text, MADE_UP)
        self.assertEqual([flag.item_id for flag in flags], ["step:s2", *MISSED_WITH_MADE_UP])
        self.assertIsInstance(readiness, domain.ReadinessAssessment)
        (pending,) = self.events("protocol_analysis_review_pending")
        # The pages are named by their checksums, not copied.
        self.assertEqual(len(pending["source_pages"]), 3)
        self.assertNotIn("Wear gloves.", json.dumps(pending["protocol"]["fields"]["metadata"]))

    def test_an_analysis_that_passes_every_check_runs_as_before(self) -> None:
        entry, _ = self.analyze(self.answer())
        self.assertTrue(entry.available_for_execution)
        self.assertIsNone(entry.review_pending)
        self.assertEqual(self.events("protocol_analysis_review_pending"), [])

    def test_a_broken_answer_still_fails_after_its_retry(self) -> None:
        broken = self.answer().replace('"steps":', '"stepz":', 1)
        error, model = self.analyze(broken, broken)
        self.assertIsInstance(error, ProtocolAnalysisResponseError)
        self.assertEqual(model.calls, 2)
        self.assertEqual(self.catalog.get_entry(self.protocol_id).analysis_status, "analysis_failed")
        self.assertIsNone(self.catalog.get_entry(self.protocol_id).review_pending)

    def test_an_answer_whose_shape_fails_beside_its_items_fails_as_before_without_a_retry(self) -> None:
        answer = json.loads(self.answer(step_two=MADE_UP))
        answer["protocol"]["constructs"] = [{
            "type": "source_ambiguity", "ambiguity_id": "which-gloves",
            "source_text": "Wear gloves.",
            "evidence": {"source_page_number": 1, "source_excerpt": "Wear gloves."},
            "resolved": True, "resolution_source_text": "The model decided.",
        }]
        error, model = self.analyze(json.dumps(answer), json.dumps(answer))
        self.assertIsInstance(error, ProtocolAnalysisEvidenceError)
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.catalog.get_entry(self.protocol_id).analysis_status, "analysis_failed")
        (failed,) = self.events("protocol_analysis_failed")
        self.assertEqual(failed["failure_code"], "protocol_analysis_invalid_evidence")
        self.assertEqual(self.events("protocol_analysis_review_pending"), [])

    def test_a_plain_request_does_not_ask_again_over_a_review_pending_analysis(self) -> None:
        self.analyze(self.answer(step_two=MADE_UP))
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "b" * 32)
        self.assertEqual(len(self.events("protocol_analysis_requested")), 1)
        # "다시 분석" asks again; its pass replaces the item for a person.
        entry, _ = self.analyze(
            self.answer(), analysis_id="analysis-" + "c" * 32, reanalysis=True)
        self.assertTrue(entry.available_for_execution)
        self.assertIsNone(entry.review_pending)

    def test_a_re_analysis_that_waits_for_review_leaves_the_pass_running(self) -> None:
        first, _ = self.analyze(self.answer())
        entry, _ = self.analyze(
            self.answer(step_two=MADE_UP), analysis_id="analysis-" + "d" * 32, reanalysis=True)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(entry.revision_id, first.revision_id)
        self.assertEqual(entry.review_pending["needs_review_count"], 4)
        self.catalog.load_executable_fixture(self.protocol_id)
        review = self.catalog.review(self.protocol_id)
        self.assertEqual(review["reanalysis"]["state"], "review_pending")
        self.assertIn("다시 분석은 검토 대기 · 확인 필요 4건", review["pipeline"]["message"])
        self.assertIn("지금 분석으로 계속 실행할 수 있습니다", review["pipeline"]["message"])

    def test_a_repaired_answer_with_an_item_is_sent_again_and_kept_if_the_retry_fails(self) -> None:
        # Lane EV3, decision 1 kept: a repaired answer some check refused gets
        # the one retry; when that retry breaks, the first answer is kept for
        # a person instead of the analysis being thrown away.
        repaired = self.answer(step_two=MADE_UP)[:-1]
        broken = self.answer().replace('"steps":', '"stepz":', 1)
        entry, model = self.analyze(repaired, broken)
        self.assertEqual(model.calls, 2)
        self.assertEqual(entry.analysis_status, "review_pending")
        (pending,) = self.events("protocol_analysis_review_pending")
        self.assertEqual(pending["analysis_id"], "analysis-" + "a" * 32)
        self.assertEqual(pending["response_repaired"]["bracket_count"], 1)

    def test_a_repaired_answer_with_an_item_whose_retry_passes_runs(self) -> None:
        entry, model = self.analyze(self.answer(step_two=MADE_UP)[:-1], self.answer())
        self.assertEqual(model.calls, 2)
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(self.events("protocol_analysis_review_pending"), [])


class LongDocumentPiecesTests(_CatalogCase):
    """The passed pieces are kept; a failed piece's pages are one item."""

    PIECES = (AnalysisPiece(1, 3, 1, 1), AnalysisPiece(2, 3, 2, 2), AnalysisPiece(3, 3, 3, 3))

    def run_pieces(self, answers_by_page: dict[int, list[str]]):
        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            pages = [page["source_page_number"] for page in json.loads(input_json)["pages"]]
            return answers_by_page[pages[0]].pop(0)

        model = Mock()
        model.analyze.side_effect = model_answer
        analysis_id = "analysis-" + "e" * 32
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=self.PIECES):
            try:
                return self.catalog.analyze(self.protocol_id, model, analysis_id=analysis_id)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                return exc

    def piece(self, page: int) -> str:
        return self.answer(pages=(page,))

    def test_a_failed_piece_s_pages_wait_for_a_person_and_the_rest_is_kept(self) -> None:
        broken = self.piece(2).replace('"steps":', '"stepz":', 1)
        entry = self.run_pieces({1: [self.piece(1)], 2: [broken, broken], 3: [self.piece(3)]})
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertFalse(entry.available_for_execution)
        self.assertEqual(entry.review_pending["failed_pages"], [
            {"first_page": 2, "last_page": 2, "failure_code": "protocol_analysis_invalid_response"}])
        self.assertIn("2–2쪽 분석 실패", entry.review_pending["message_ko"])
        protocol, _, flags, _ = self.catalog.review_pending_analysis(self.protocol_id)
        self.assertEqual([flag.item_id for flag in flags], ["pages:2-2"])
        self.assertEqual(flags[0].reason_ko, "2–2쪽 분석 실패")
        labels = [step.source_label for section in protocol.sections for step in section.steps]
        self.assertEqual(labels, ["1", "2", "5", "6"])
        run = self.catalog.analysis_run_status(self.protocol_id)
        self.assertEqual(run.state, "review_pending")
        self.assertEqual((run.completed_chunks, run.failed_chunks), (2, 1))

    def test_every_piece_failing_still_fails_the_document(self) -> None:
        broken = {page: [self.piece(page).replace('"steps":', '"stepz":', 1)] * 2
                  for page in (1, 2, 3)}
        error = self.run_pieces(broken)
        self.assertEqual(getattr(error, "code", None), "protocol_analysis_piece_failed")
        self.assertEqual(
            self.catalog.get_entry(self.protocol_id).analysis_status, "chunk_analysis_failed")

    def test_every_piece_passing_runs_as_before(self) -> None:
        entry = self.run_pieces({1: [self.piece(1)], 2: [self.piece(2)], 3: [self.piece(3)]})
        self.assertTrue(entry.available_for_execution)
        self.assertIsNone(entry.review_pending)


class ServerStartsNothingOverItTests(_CatalogCase):
    def test_the_upload_route_does_not_run_an_analysis_over_one_waiting_for_review(self) -> None:
        self.analyze(self.answer(step_two=MADE_UP))
        self.store.close()

        def open_catalog():
            opened = initialize_protocol_store(self.settings)
            return ProtocolCatalog(opened), opened

        async def to_thread(function, /, *args, **kwargs):
            return function(*args, **kwargs)

        server._PROTOCOL_ANALYSIS_TASKS.clear()
        try:
            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog), \
                    patch.object(server.asyncio, "to_thread", side_effect=to_thread), \
                    patch.object(server, "_protocol_analysis_model", return_value=Mock()), \
                    patch("voiney_lab.protocol_catalog.analyze_protocol_extraction") as analyze:
                public = asyncio.run(server.trigger_protocol_analysis(self.protocol_id))
        finally:
            server._PROTOCOL_ANALYSIS_TASKS.clear()
            self.store = initialize_protocol_store(self.settings)
        self.assertTrue(public["analysis_request_deduplicated"])
        self.assertEqual(public["analysis_run"]["state"], "review_pending")
        analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
