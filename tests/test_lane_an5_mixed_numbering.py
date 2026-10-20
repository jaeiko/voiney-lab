"""A source that numbers some steps and not others, read at once (lane AN5, decision 1).

Lane P3's rule (2026-10-05) held the steps of one reading to one kind: all
numbered or all unnumbered. Lane AN3 (decision B, 2026-10-16) let a document
joined from pieces hold both, with a card at each seam. Lane AN4 found the
rule the largest single cause of failed answers: Word protocols and manuals
print numbered steps ("a."-"f.", "1."-"6.") beside paragraph steps
("Priming and loading", "Data analysis"), the model read them as printed and
the answer was refused, sent again, and often refused again (9 of the 23
automatic retries, most of the failed Word documents).

Human decision 2026-10-20 (the recommended option): one reading -- a
document read at once, or one piece -- may hold both kinds too. The printed
numbers stay as printed (none where none is printed); the steps run in the
order read, the screen numbering an unnumbered step by its place; and every
place the numbering changes is one "순서 확인" card, lane AN3's card. A step
the source numbers is still never read without its number. Every model here
is fake.
"""

from __future__ import annotations

import json
import unittest

from tests.test_lane_an3_numbering_seams import _PiecesCase, unnumbered
from tests.test_lane_rv_review_pending import PAGES, gamma_response
from voiney_lab.experiment_protocol import ProtocolValidationCode
from voiney_lab.experiment_protocol_analysis import (
    REVIEW_REASON_NUMBERING_SEAM,
    ProtocolAnalysisResponseError,
    parse_protocol_analysis_response,
)
from voiney_lab.protocol_catalog import protocol_with_display_step_labels


def _read_at_once(sha256: str, *, unnumbered_sections: slice = slice(1, 2)) -> str:
    payload = json.loads(gamma_response(sha256, pages=(1, 2, 3)))
    unnumbered({"sections": payload["protocol"]["sections"][unnumbered_sections]})
    return json.dumps(payload)


class OneReadingMayHoldBothKindsTests(_PiecesCase):
    def test_a_document_read_at_once_with_both_kinds_waits_on_one_card_per_seam(self) -> None:
        entry, model = self.analyze(_read_at_once(self.sha256))
        self.assertEqual(model.calls, 1)  # no automatic retry
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(self.events("protocol_analysis_failed"), [])
        self.assertEqual(self.events("protocol_analysis_retry_started"), [])
        self.assertEqual(entry.review_pending["by_reason"], {REVIEW_REASON_NUMBERING_SEAM: 2})
        cards = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual([card["item_id"] for card in cards], ["seam:s2+s3", "seam:s4+s5"])
        self.assertEqual({card["type"] for card in cards}, {"numbering_seam"})
        self.assertEqual(cards[0]["question_ko"],
                         "번호를 붙인 부분과 붙이지 않은 부분이 만나는 곳이에요. "
                         "앞 단계 다음에 뒤 단계가 이어지나요?")
        before, after = cards[0]["steps"]
        self.assertEqual((before["label"], before["label_shown"], before["label_printed"]),
                         ("2", "2", True))
        self.assertEqual((after["label"], after["label_shown"], after["label_printed"]),
                         ("", "3", False))

    def test_the_printed_numbers_stay_and_the_screen_numbers_the_rest_by_place(self) -> None:
        self.analyze(_read_at_once(self.sha256))
        protocol, _, _, _ = self.catalog.review_pending_analysis(self.protocol_id)
        self.assertEqual([step.source_label for section in protocol.sections for step in section.steps],
                         ["1", "2", "", "", "5", "6"])
        shown = protocol_with_display_step_labels(protocol)
        self.assertEqual([step.source_label for section in shown.sections for step in section.steps],
                         ["1", "2", "3", "4", "5", "6"])

    def test_following_on_at_every_seam_registers_a_version_that_runs_in_the_order_read(self) -> None:
        self.analyze(_read_at_once(self.sha256))
        for card in self.catalog.review_draft(self.protocol_id)["cards"]:
            self.catalog.record_review_decision(self.protocol_id, card["choices"][0]["request"])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual([step.source_label for step in fixture.steps], ["1", "2", "3", "4", "5", "6"])

    def test_no_at_a_seam_keeps_the_document_from_running(self) -> None:
        self.analyze(_read_at_once(self.sha256))
        draft = self.catalog.review_draft(self.protocol_id)
        self.assertEqual(draft["cards"][0]["choices"][1]["route"], "reanalysis")
        self.assertFalse(draft["registrable"])
        self.assertFalse(self.catalog.get_entry(self.protocol_id).available_for_execution)



class OnePieceMayHoldBothKindsTests(_PiecesCase):
    #: The middle page numbers its first step and not its second.
    PAGES = [PAGES[0], ["Section wash", "3. Wash the pellet twice.", "Spin at 500 x g for 5 min."],
             PAGES[2]]

    def test_a_piece_with_both_kinds_is_kept_and_its_seams_are_cards(self) -> None:
        def half(protocol: dict) -> None:
            unnumbered({"sections": [{"steps": protocol["sections"][0]["steps"][1:]}]})

        entry = self.run_pieces(self.piece(1), self.piece(2, half), self.piece(3))
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(self.events("protocol_chunk_analysis_failed"), [])
        cards = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual([card["item_id"] for card in cards],
                         ["seam:p2.s3+p2.s4", "seam:p2.s4+p3.s5"])


class OneKindStaysAsItWasTests(_PiecesCase):
    PAGES = PAGES

    def test_a_document_numbered_throughout_has_no_seam_and_runs(self) -> None:
        entry, model = self.analyze(gamma_response(self.sha256, pages=(1, 2, 3)))
        self.assertTrue(entry.available_for_execution)
        self.assertEqual(model.calls, 1)
        self.assertEqual(self.catalog.review_draft(self.protocol_id)["cards"], [])


class TheParsedAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = _PiecesCase("run")
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        revision = self.case.catalog._latest_protocol_revision(self.case.protocol_id)
        self.extraction = self.case.catalog._source_extraction(revision)

    def test_one_seam_flag_per_change_of_numbering(self) -> None:
        draft = parse_protocol_analysis_response(
            _read_at_once(self.case.sha256), self.extraction, review_unverified=True)
        seams = [flag for flag in draft.review_flags if flag.reason == REVIEW_REASON_NUMBERING_SEAM]
        self.assertEqual([flag.item_id for flag in seams], ["seam:s2+s3", "seam:s4+s5"])
        self.assertEqual([flag.reason_code for flag in seams], ["step_labelling_differs"] * 2)
        self.assertEqual(len(draft.review_flags), 2)

    def test_without_cards_the_strict_reading_still_refuses_both_kinds(self) -> None:
        # Nothing reads a document this way to run it; without a card to ask
        # on, the rule of one kind per reading stands.
        with self.assertRaises(ProtocolAnalysisResponseError) as caught:
            parse_protocol_analysis_response(_read_at_once(self.case.sha256), self.extraction)
        self.assertIs(caught.exception.__cause__.code, ProtocolValidationCode.MISSING_SOURCE_LABEL)

    def test_a_step_the_page_numbers_is_still_refused_without_its_number(self) -> None:
        payload = json.loads(_read_at_once(self.case.sha256))
        step = payload["protocol"]["sections"][0]["steps"][1]
        step["source_label"] = ""  # the page prints "2." before it, and so does its quote
        draft = parse_protocol_analysis_response(
            json.dumps(payload), self.extraction, review_unverified=True)
        refused = [flag for flag in draft.review_flags if flag.reason != REVIEW_REASON_NUMBERING_SEAM]
        self.assertEqual([(flag.field, flag.reason_code) for flag in refused],
                         [("source_label", "source_label_not_found")])


if __name__ == "__main__":
    unittest.main()
