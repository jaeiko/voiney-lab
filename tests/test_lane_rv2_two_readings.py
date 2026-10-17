"""A page two pieces read differently is an item, not a failed document (lane RV2, decision e).

A long document is read in pieces that share one page. Before, two pieces
reading a step of that page differently failed the whole document. Human
decision 2026-10-16: the passage is one item ("두 가지로 읽힘"); the choices
are the two pieces' readings, with the page's lines beside them, and the
pieces that passed are kept. The earlier piece's reading stands until a
person picks the other one.

Measured on the stored long-document runs (lane RV2 replay), neither long
document failed this way: one failed because the join carried the pieces'
emptied section titles under ids the joined analysis no longer used (fixed
here), and both because some pieces numbered their steps and others did not
-- a property lane P3's human decision gives the whole source. That refusal
is now named for what it is; whether to allow it is a person's decision.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab.experiment_protocol_analysis import AnalysisPiece

PIECES = (AnalysisPiece(1, 2, 1, 2), AnalysisPiece(2, 2, 2, 3))


class _TwoPiecesCase(_CatalogCase):
    def piece_answer(self, pages: tuple[int, ...], edit=None) -> str:
        payload = json.loads(self.answer(pages=pages))
        if edit is not None:
            edit(payload["protocol"])
        return json.dumps(payload)

    def run_two(self, first: str, second: str):
        answers = {1: [first], 2: [second]}

        def model_answer(*, system_prompt: str, input_json: str, response_schema: dict) -> str:
            return answers[json.loads(input_json)["pages"][0]["source_page_number"]].pop(0)

        model = Mock()
        model.analyze.side_effect = model_answer
        self.catalog.request_analysis(self.protocol_id, "analysis-" + "e" * 32)
        with patch("voiney_lab.protocol_catalog.analysis_pieces", return_value=PIECES):
            try:
                return self.catalog.analyze(
                    self.protocol_id, model, analysis_id="analysis-" + "e" * 32)
            except Exception as exc:  # noqa: BLE001 - the caller looks at it
                return exc

    @staticmethod
    def step(protocol: dict, step_id: str) -> dict:
        return next(step for section in protocol["sections"] for step in section["steps"]
                    if step["step_id"] == step_id)

    def register(self, card: dict, choice_id: str):
        choice = next(choice for choice in card["choices"] if choice["choice_id"] == choice_id)
        self.catalog.record_review_decision(self.protocol_id, choice["request"])
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        return self.catalog.load_executable_fixture(self.protocol_id)


def partial_step_four(protocol: dict) -> None:
    step = _TwoPiecesCase.step(protocol, "s4")
    step.update(instruction_source_text="4. Spin at 500 x g",
                evidence={"source_page_number": 2, "source_excerpt": "4. Spin at 500 x g"})


def other_step_four(protocol: dict) -> None:
    step = _TwoPiecesCase.step(protocol, "s4")
    step.update(instruction_source_text="Spin at 500 x g for 5 min.",
                evidence={"source_page_number": 2, "source_excerpt": "Spin at 500 x g for 5 min."})


class TwoQuotesOfOneStepTests(_TwoPiecesCase):
    def setUp(self) -> None:
        super().setUp()
        self.entry = self.run_two(self.piece_answer((1, 2), partial_step_four),
                              self.piece_answer((2, 3), other_step_four))

    def test_the_passage_is_one_card_and_the_pieces_that_passed_are_kept(self) -> None:
        self.assertEqual(self.entry.analysis_status, "review_pending")
        self.assertIn("두 가지로 읽힘 1", self.entry.review_pending["message_ko"])
        protocol, _, flags, _ = self.catalog.review_pending_analysis(self.protocol_id)
        labels = [step.source_label for section in protocol.sections for step in section.steps]
        self.assertEqual(labels, ["1", "2", "3", "4", "5", "6"])
        (flag,) = flags
        self.assertEqual((flag.item_id, flag.reason, flag.reason_ko),
                         ("step:p1.s4", "read_two_ways", "두 가지로 읽힘"))
        (card,) = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertEqual(card["type"], "two_readings")
        self.assertEqual(card["question_ko"], "두 조각이 2쪽을 다르게 읽었어요. 어느 쪽이 맞나요?")
        self.assertEqual([choice["label_ko"] for choice in card["choices"]],
                         ["이쪽으로 읽기", "저쪽으로 읽기", "직접 고칠게요"])
        mine, other = card["readings"]
        self.assertEqual([step["text"] for step in mine], ["4. Spin at 500 x g"])
        self.assertEqual([step["text"] for step in other], ["Spin at 500 x g for 5 min."])
        self.assertEqual(other[0]["lines"], {"page": 2, "first_line": 3, "last_line": 3})

    def test_the_other_reading_chosen_is_the_step_that_runs(self) -> None:
        card = self.catalog.review_draft(self.protocol_id)["cards"][0]
        fixture = self.register(card, "choose_b")
        self.assertEqual([step.instruction_source_text for step in fixture.steps][3],
                         "Spin at 500 x g for 5 min.")
        self.assertEqual(fixture.steps[3].step_id, "p1.s4")
        (registered,) = self.events("protocol_review_version_registered")
        (change,) = registered["changes"]
        self.assertEqual((change["action"], change["original"], change["reviewed"]),
                         ("choose", "4. Spin at 500 x g", "Spin at 500 x g for 5 min."))

    def test_this_reading_chosen_keeps_the_earlier_piece_s(self) -> None:
        card = self.catalog.review_draft(self.protocol_id)["cards"][0]
        fixture = self.register(card, "choose_a")
        self.assertEqual(fixture.steps[3].instruction_source_text, "4. Spin at 500 x g")

    def test_a_choice_names_a_reading(self) -> None:
        from voiney_lab.protocol_catalog import ProtocolReviewError
        for request in ({"item_id": "step:p1.s4", "action": "choose"},
                        {"item_id": "step:p1.s4", "action": "choose", "reading": "c"},
                        {"item_id": "step:p1.s1", "action": "confirm", "reading": "a"}):
            with self.subTest(request=request), self.assertRaises(ProtocolReviewError):
                self.catalog.record_review_decision(self.protocol_id, request)


class TheOtherReadingNamesWhatTheJoinRenamedTests(_TwoPiecesCase):
    def test_a_step_it_depends_on_is_the_one_the_join_kept(self) -> None:
        def depends_on_three(protocol: dict) -> None:
            other_step_four(protocol)
            self.step(protocol, "s4")["dependencies"] = [{"step_id": "s3", "action_id": None}]

        self.run_two(self.piece_answer((1, 2), partial_step_four),
                     self.piece_answer((2, 3), depends_on_three))
        card = self.catalog.review_draft(self.protocol_id)["cards"][0]
        fixture = self.register(card, "choose_b")
        step = fixture.steps[3]
        self.assertEqual(step.instruction_source_text, "Spin at 500 x g for 5 min.")
        self.assertEqual([target.step_id for target in step.dependencies], ["p1.s3"])


class OneStepOverSeveralTests(_TwoPiecesCase):
    def test_the_other_reading_replaces_the_steps_it_reads_as_one(self) -> None:
        def three_and_four(protocol: dict) -> None:
            section = protocol["sections"][0]
            both = "3. Wash the pellet twice.\n4. Spin at 500 x g for 5 min."
            section["steps"][0].update(instruction_source_text=both, evidence={
                "source_page_number": 2, "source_excerpt": both})
            section["steps"] = section["steps"][:1]

        entry = self.run_two(self.piece_answer((1, 2)), self.piece_answer((2, 3), three_and_four))
        self.assertEqual(entry.analysis_status, "review_pending")
        (card,) = self.catalog.review_draft(self.protocol_id)["cards"]
        mine, other = card["readings"]
        self.assertEqual([step["step_id"] for step in mine], ["p1.s3", "p1.s4"])
        self.assertEqual(len(other), 1)
        fixture = self.register(card, "choose_b")
        self.assertEqual([step.step_id for step in fixture.steps],
                         ["p1.s1", "p1.s2", "p1.s3", "p2.s5", "p2.s6"])


class JoinBookkeepingTests(_TwoPiecesCase):
    def test_a_section_title_a_later_piece_emptied_does_not_refuse_the_join(self) -> None:
        def unprinted_titles(protocol: dict) -> None:
            for section in protocol["sections"]:
                section["title_source_text"] = section["title_source_text"] + " (cont.)"

        entry = self.run_two(self.piece_answer((1, 2)), self.piece_answer((2, 3), unprinted_titles))
        self.assertTrue(entry.available_for_execution, entry)
        (ready,) = self.events("protocol_analysis_ready")
        # Section "wash" was merged into the earlier piece's (its title is
        # there); section "finish" kept its emptied title, by its joined id.
        self.assertEqual(ready["cleared_fields"], ["sections.p2.finish.title_source_text"])

    def test_pieces_that_number_their_steps_differently_are_joined_with_a_seam_card(self) -> None:
        def unlabelled(protocol: dict) -> None:
            for section in protocol["sections"]:
                for step in section["steps"]:
                    step["source_label"] = ""
                    for field in ("instruction_source_text",):
                        step[field] = step[field].split(". ", 1)[1]
                    step["evidence"]["source_excerpt"] = step["instruction_source_text"]

        entry = self.run_two(self.piece_answer((1, 2)), self.piece_answer((2, 3), unlabelled))
        # Lane AN3, decision B: no longer refused; where the numbering changes
        # is a card of its own (the unnumbered quotes of numbered lines are
        # cards for their evidence, as any such quote).
        self.assertEqual(entry.analysis_status, "review_pending")
        self.assertEqual(self.events("protocol_analysis_failed"), [])
        cards = self.catalog.review_draft(self.protocol_id)["cards"]
        self.assertIn(("seam:p1.s4+p2.s5", "numbering_seam"),
                      [(card["item_id"], card["type"]) for card in cards])


if __name__ == "__main__":
    unittest.main()
