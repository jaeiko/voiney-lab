"""Lane AN3, decision B: a long document whose parts number their steps differently.

Lane P3's human decision (2026-10-05): an empty step label means the source
prints no number, and the steps of one reading are all numbered or all
unnumbered. Lane RV2 found that both long documents of the regression set
(197 and 315 pages) failed on it: a manual numbers its steps in one part and
not in another, and the pieces read them that way.

Human decision 2026-10-16: the mix is allowed for a document joined from
pieces. The server joins the pieces in page order and keeps the numbers the
source prints (none where it prints none); where the numbering changes, the
last step before and the first step after are one card -- does the second
follow the first? [이어져요] lets the joined order run; [아니요] does not run the
document and sends it to be read again (the server never reorders steps).
The rule still holds within one reading: a document read at once, or one
piece. Every model here is fake.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from tests.test_lane_rv_review_pending import PAGES, _CatalogCase, write_pages_pdf
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf
from voiney_lab.experiment_protocol_analysis import (
    AnalysisPiece,
    ProtocolAnalysisResponseError,
    REVIEW_REASON_NUMBERING_SEAM,
)

PIECES = (AnalysisPiece(1, 3, 1, 1), AnalysisPiece(2, 3, 2, 2), AnalysisPiece(3, 3, 3, 3))
#: Lane P3's documents: the middle part of this manual prints no step numbers.
MIXED_PAGES = [PAGES[0], ["Section wash", "Wash the pellet twice.", "Spin at 500 x g for 5 min."],
               PAGES[2]]


def unnumbered(protocol: dict) -> None:
    """The piece read its steps without their numbers (quotes without them)."""

    for section in protocol["sections"]:
        for step in section["steps"]:
            step["source_label"] = ""
            step["instruction_source_text"] = step["instruction_source_text"].split(". ", 1)[1]
            step["evidence"]["source_excerpt"] = step["instruction_source_text"]


class _PiecesCase(_CatalogCase):
    PAGES = MIXED_PAGES

    def setUp(self) -> None:
        super().setUp()
        if self.PAGES is not PAGES:
            clear_protocol_pdf_cache()
            write_pages_pdf(self.pdf, self.PAGES, title="Protocol Gamma")
            self.protocol_id = self.catalog.register(
                self.pdf, source_filename="gamma-mixed.pdf", media_type="application/pdf",
            ).entry.protocol_id
            self.sha256 = extract_protocol_pdf(self.pdf).sha256

    def piece(self, page: int, edit=None) -> str:
        payload = json.loads(self.answer(pages=(page,)))
        if edit is not None:
            edit(payload["protocol"])
        return json.dumps(payload)

    def run_pieces(self, *answers: str):
        by_page = {index: [answer] for index, answer in enumerate(answers, start=1)}

        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            return by_page[json.loads(input_json)["pages"][0]["source_page_number"]].pop(0)

        model = Mock()
        model.analyze.side_effect = model_answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "e" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=PIECES):
            try:
                return self.catalog.analyze(
                    self.protocol_id, model, analysis_id="analysis-" + "e" * 32)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                return exc

    def mixed(self):
        return self.run_pieces(self.piece(1), self.piece(2, unnumbered), self.piece(3))


class TheRuleHoldsWithinOneReadingTests(_PiecesCase):
    def test_the_domain_no_longer_refuses_a_mix_and_the_reading_rule_still_does(self) -> None:
        self.mixed()
        protocol, _, _, _ = self.catalog.review_pending_analysis(self.protocol_id)
        self.assertIs(domain.validate_protocol(protocol), protocol)
        with self.assertRaises(domain.ProtocolValidationError) as caught:
            domain.validate_step_labelling(protocol)
        self.assertIs(caught.exception.code, domain.ProtocolValidationCode.MISSING_SOURCE_LABEL)
        self.assertIn("sections[1].steps[0]", str(caught.exception))

    def test_a_document_read_at_once_with_both_kinds_fails_as_before(self) -> None:
        payload = json.loads(self.answer(pages=(1, 2, 3)))
        unnumbered({"sections": payload["protocol"]["sections"][1:2]})
        error, model = self.analyze(json.dumps(payload), json.dumps(payload))
        self.assertIsInstance(error, ProtocolAnalysisResponseError)
        self.assertIsInstance(error.__cause__, domain.ProtocolValidationError)
        self.assertIs(error.__cause__.code, domain.ProtocolValidationCode.MISSING_SOURCE_LABEL)
        self.assertEqual(model.calls, 2)  # the one automatic retry, as before

    def test_a_piece_with_both_kinds_fails_as_a_piece(self) -> None:
        def half(protocol: dict) -> None:
            unnumbered({"sections": [{"steps": protocol["sections"][0]["steps"][:1]}]})

        entry = self.run_pieces(self.piece(1), self.piece(2, half), self.piece(3))
        self.assertEqual(entry.analysis_status, "review_pending")
        failed = [event.payload for event in self.store.list_events(self.protocol_id)
                  if event.event_type == "protocol_chunk_analysis_failed"]
        self.assertEqual([payload["ordinal"] for payload in failed], [2])


class SeamCardsTests(_PiecesCase):
    def test_each_place_the_numbering_changes_is_one_card(self) -> None:
        entry = self.mixed()
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(self.events("protocol_analysis_failed"), [])
        self.assertEqual(entry.review_pending["by_reason"], {REVIEW_REASON_NUMBERING_SEAM: 2})
        self.assertEqual(entry.review_pending["message_ko"],
                         "검토 대기 · 확인 필요 2건(번호 방식이 바뀌는 경계 2)")
        cards = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual([card["item_id"] for card in cards],
                         ["seam:p1.s2+p2.s3", "seam:p2.s4+p3.s5"])
        card = cards[0]
        self.assertEqual(card["type"], "numbering_seam")
        self.assertEqual(card["question_ko"], "번호를 붙인 부분과 붙이지 않은 부분이 만나는 곳이에요. "
                                              "앞 단계 다음에 뒤 단계가 이어지나요?")
        self.assertEqual([(choice["label_ko"], choice["request"], choice["route"])
                          for choice in card["choices"]], [
            ("이어져요", {"item_id": "seam:p1.s2+p2.s3", "action": "confirm"}, None),
            ("아니요", None, "reanalysis")])
        before, after = card["steps"]
        self.assertEqual((before["label"], before["label_shown"], before["label_printed"]),
                         ("2", "2", True))
        self.assertEqual((after["label"], after["label_shown"], after["label_printed"]),
                         ("", "3", False))
        self.assertEqual(after["text"], "Wash the pellet twice.")
        self.assertEqual(after["lines"], {"page": 2, "first_line": 2, "last_line": 2})


    def test_following_on_at_every_seam_runs_the_joined_order_with_its_numbers(self) -> None:
        self.mixed()
        for card in self.catalog.review_draft(self.protocol_id)["cards"]:
            self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.revision_id, "pdf-1-review-1")
        self.assertEqual([step.source_label for step in fixture.steps],
                         ["1", "2", "3", "4", "5", "6"])
        review = self.catalog.review(self.protocol_id)
        shown = [(step["source_label"], step.get("source_label_printed", True))
                 for section in review["sections"] for step in section["steps"]]
        self.assertEqual(shown, [("1", True), ("2", True), ("3", False), ("4", False),
                                 ("5", True), ("6", True)])

    def test_no_at_a_seam_does_not_run_the_document(self) -> None:
        self.mixed()
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertEqual(draft["cards"][0]["choices"][1]["request"], None)
        self.catalog.record_review_decision(self.protocol_id, draft["cards"][1]["choices"][0]["request"])
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertFalse(draft["registrable"])
        self.assertEqual([blocker["item_id"] for blocker in draft["registration_blockers"]],
                         ["seam:p1.s2+p2.s3"])
        self.assertFalse(self.catalog.get_entry(self.protocol_id).available_for_execution)


class PiecesNumberedAlikeTests(_PiecesCase):
    PAGES = PAGES

    def test_pieces_that_number_alike_have_no_seam(self) -> None:
        entry = self.run_pieces(self.piece(1), self.piece(2), self.piece(3))
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(self.catalog.review_draft(self.protocol_id)["cards"], [])


if __name__ == "__main__":
    unittest.main()
