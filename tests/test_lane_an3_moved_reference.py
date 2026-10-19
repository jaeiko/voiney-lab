"""Lane AN3, round 2, decision ⑤: a construct the join left naming another section.

When the pieces of a long document are joined, a step read on the page two
pieces share is kept once, under the earlier piece's id; a construct of the
later piece that names that step and its own section then names a step of
another section, and the whole document was refused (DANGLING_REFERENCE --
lane AN3's usgs re-analysis). Human decision 2026-10-18: the construct names
the section the step stands in, and only that construct is an item a person
checks. Every model here is fake.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from tests.test_lane_an3_long_doc_limit import _Pieces
from tests.test_lane_rv_review_pending import PAGES, gamma_response, write_pages_pdf
from voiney_lab.experiment_protocol_analysis import (
    AnalysisPiece,
    REVIEW_REASON_REFERENCE_MOVED,
    analyze_protocol_piece,
    join_piece_analyses,
)
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache, extract_protocol_pdf

#: The manual's third page prints the repeat of step 4 after the finishing steps.
MOVED_PAGES = [PAGES[0], PAGES[1], PAGES[2] + ["Repeat step 4 twice."]]
TWO = (AnalysisPiece(1, 2, 1, 2), AnalysisPiece(2, 2, 2, 3))


def later_piece(sha256: str) -> str:
    """The second piece reads step 4 (on the shared page 2) inside its own
    "finish" section, and its repeat names that section and step."""

    ev = lambda excerpt, page: {"source_page_number": page, "source_excerpt": excerpt}
    step = lambda step_id, words, page: {"step_id": step_id, "source_label": words.split(".", 1)[0],
                                         "instruction_source_text": words, "evidence": ev(words, page)}
    return json.dumps({
        "analysis_schema_version": 1, "pdf_sha256": sha256,
        "capability_policy_id": "p1-conservative",
        "protocol": {
            "protocol_id": "gamma",
            "metadata": {"title": "Section wash", "original_language": "en",
                         "evidence": ev("Section wash", 2)},
            "before_start": [], "materials": [], "equipment": [],
            "sections": [{
                "section_id": "finish", "title_source_text": "Section finish",
                "evidence": ev("Section finish", 3),
                "steps": [step("s4", "4. Spin at 500 x g for 5 min.", 2),
                          step("s5", "5. Store the tube at 4 C.", 3),
                          step("s6", "6. Record the volume.", 3)],
            }],
            "constructs": [{
                "type": "fixed_range_repetition", "repetition_id": "spin-twice",
                "start_step_id": "s4", "end_step_id": "s4",
                "range_source_text": "Repeat step 4 twice.", "repeat_count": 2,
                "evidence": ev("Repeat step 4 twice.", 3),
                "section_id": "finish", "step_id": "s4",
            }],
            "description": None,
        },
    })


class MovedReferenceTests(_Pieces):
    def setUp(self) -> None:
        super().setUp()
        clear_protocol_pdf_cache()
        write_pages_pdf(self.pdf, MOVED_PAGES, title="Protocol Gamma")
        self.sha256 = extract_protocol_pdf(self.pdf).sha256
        self.protocol_id = self.catalog.register(
            self.pdf, source_filename="gamma-moved.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def answers(self) -> dict[int, list[str]]:
        return {1: [gamma_response(self.sha256, pages=(1, 2))], 2: [later_piece(self.sha256)]}

    def test_the_join_points_the_construct_at_its_steps_section_and_asks_about_it(self) -> None:
        extraction = extract_protocol_pdf(self.pdf)
        drafts = []
        for piece, answer in zip(TWO, (self.answers()[1][0], self.answers()[2][0])):
            model = Mock()
            model.analyze.return_value = answer
            drafts.append(analyze_protocol_piece(extraction, piece, model, review_unverified=True))
        joined = join_piece_analyses(extraction, TWO, drafts, review_unverified=True)
        (construct,) = joined.protocol.constructs
        sections = {step.step_id: section.section_id
                    for section in joined.protocol.sections for step in section.steps}
        self.assertEqual((construct.step_id, construct.section_id), ("p1.s4", sections["p1.s4"]))
        (flag,) = [flag for flag in joined.review_flags if flag.reason == REVIEW_REASON_REFERENCE_MOVED]
        self.assertEqual(flag.item_id, "construct:p2.spin-twice")
        self.assertEqual(flag.extra, {"step_id": "p1.s4", "named_section": "p2.finish",
                                      "step_section": "p1.wash"})
        self.assertEqual(flag.reason_ko, "조각을 합친 뒤 가리키는 단계가 다른 절에 있음")

    def test_the_document_waits_for_review_and_a_confirmed_construct_runs(self) -> None:
        entry, _ = self.run_pieces(TWO, self.answers())
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(self.events("protocol_analysis_failed"), [])
        cards = self.catalog.review_draft(self.protocol_id)["cards"]
        card = next(card for card in cards if card["item_id"] == "construct:p2.spin-twice")
        self.assertEqual(card["type"], "reference_moved")
        self.assertIn("조각을 합친 뒤 이 구조가 가리키는 단계가 다른 절에 있어요", card["question_ko"])
        self.assertEqual([(choice["label_ko"], choice["request"], choice["route"]) for choice in card["choices"]],
                         [("맞아요", {"item_id": "construct:p2.spin-twice", "action": "confirm"}, None),
                          ("다시 분석", None, "reanalysis")])
        for each in cards:
            self.catalog.record_review_decision(self.protocol_id, each["choices"][0]["request"])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        fixture = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual(fixture.revision_id, "pdf-1-review-1")


if __name__ == "__main__":
    unittest.main()
