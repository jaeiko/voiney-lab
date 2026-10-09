"""Lane CB, decisions 4-5 (2026-10-07): the round on the card, the condition's answer in the report.

The '지금 할 일' card shows the round ("2/3회차") beside the step and the
server question that is open (the condition, the count, the next round);
the report's step table carries the condition's answer ("조건: … → 예"), the
count a person gave with its source, and the rounds each step was done in.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.lane_cb_support import CONDITION_42, Recorded, Turns, headspace_fixture, index_of
from voiney_lab import experiment_reports as er
from voiney_lab import server as server_module

INDEX_HTML = Path(__file__).resolve().parents[1] / "src/voiney_lab/static/index.html"


class ScreenFieldsTests(Turns, unittest.TestCase):
    """Decision 4: what the page draws beside the state."""

    def test_the_round_and_the_open_question_travel_with_the_screen_fields(self) -> None:
        self.open(index_of("12"))
        fields = server_module.curated_screen_fields(self.session)
        self.assertEqual(fields["repeat_round"]["words"], "1/3회차")
        self.assertIsNone(fields["open_question"])
        self.complete("12", "13", "14", "15")
        self.say("16단계 완료했어")
        fields = server_module.curated_screen_fields(self.session)
        self.assertEqual(fields["open_question"]["kind"], "repeat_round")
        self.assertEqual(
            fields["open_question"]["text"],
            # Lane VT, decision 4: the round that ended is counted first.
            "3회 중 1회째 끝났어요. 12~15단계를 한 번 더 해야 해요(2/3회차). 12단계로 돌아갈까요?",
        )
        self.say("응")
        fields = server_module.curated_screen_fields(self.session)
        self.assertEqual(fields["repeat_round"], {
            "repetition_id": "repeat-12-15", "range": "12~15", "round": 2, "required": 3,
            "words": "2/3회차", "value_source": "source",
        })
        self.assertIsNone(fields["open_question"])

    def test_the_condition_question_is_on_the_card(self) -> None:
        self.open(index_of("41"))
        self.say("41단계 완료했어")
        fields = server_module.curated_screen_fields(self.session)
        self.assertEqual(fields["open_question"]["kind"], "branch")
        self.assertIn(CONDITION_42, fields["open_question"]["text"])
        self.assertIsNone(fields["repeat_round"])

    def test_outside_a_repeat_nothing_is_shown(self) -> None:
        self.open(index_of("11"))
        fields = server_module.curated_screen_fields(self.session)
        self.assertIsNone(fields["repeat_round"])
        self.assertIsNone(fields["open_question"])


class CardMarkupTests(unittest.TestCase):
    """The page draws the two fields on the '지금 할 일' card and nowhere else."""

    def test_the_card_has_a_place_for_the_open_question_and_the_round(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="procedure-open-question"', html)
        self.assertIn("curatedScreenFields?.open_question", html)
        self.assertIn("curatedScreenFields?.repeat_round", html)
        # The round is written beside the step on the progress line.
        self.assertRegex(html, r'procedure-progress"\)\.textContent=.*roundWords')
        self.assertIn("curatedScreenFields.repeat_round.words", html)


class ReportTests(Recorded, unittest.TestCase):
    """Decision 5: the answers and the rounds in the report's method table."""

    def setUp(self) -> None:
        self.start_recording()

    def facts(self) -> er.ReportFacts:
        return er.build_report_facts(self.report(), fixture=self.session.fixture)

    def markdown(self) -> str:
        return er.render_markdown(er.narrative_from_sections(self.facts()))

    def test_the_condition_answer_is_a_row_under_its_step(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("41")
        self.record("41단계 완료했어")
        self.record("네")
        facts = self.facts()
        self.assertEqual(len(facts.branch_answers), 1)
        answer = facts.branch_answers[0]
        self.assertEqual((answer.step_label, answer.answer, answer.value_source), ("42", "예", "사람이 답함"))
        self.assertIn(f"| 42 | 조건: “{CONDITION_42}” → 예 (사람이 답함) |", self.markdown())

    def test_a_no_names_the_skipped_step(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("41")
        self.record("41단계 완료했어")
        self.record("아니요")
        self.assertIn(
            f"| 42 | 조건: “{CONDITION_42}” → 아니요 (사람이 답함) · 42단계 건너뜀 |", self.markdown())

    def test_the_count_and_the_rounds_are_in_the_table(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("18")
        self.record("18단계 완료했어")
        self.record("세 번")
        self.record("19단계 완료했어")
        self.record("20단계 완료했어")
        self.record("21단계 완료했어")
        self.record("응")
        self.record("19단계 완료했어")
        self.record("20단계 완료했어")
        self.record("21단계 완료했어")
        self.record("아니")
        facts = self.facts()
        self.assertEqual(len(facts.repetitions), 1)
        self.assertEqual((facts.repetitions[0].count, facts.repetitions[0].value_source), (3, "사람이 답함"))
        markdown = self.markdown()
        self.assertIn("| 19~20 | 반복 횟수 3회 — 사람이 답함 |", markdown)
        self.assertRegex(markdown, r"\| 19 \| .*\| 1회차 \d\d:\d\d, 2회차 \d\d:\d\d \|")
        self.assertIn(
            "21단계: 19~20단계를 3회 하기로 했으나(사람이 답함) 2회차까지만 하고 22단계로 넘어갔다"
            "(회차는 말로 확인한 돌아가기 기준).", facts.deviations)
        # Lane N's value review is left as it was: the answers and the counts
        # are not asked about before the report (outside this lane's scope).
        kinds = {item["kind"] for item in er.report_review_items(self.report()["events"])}
        self.assertEqual(kinds & {"조건", "반복"}, set())

    def test_an_open_count_closed_by_rounds_is_in_the_table(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("18")
        self.record("18단계 완료했어")
        self.record("아직 몰라")
        self.record("19단계 완료했어")
        self.record("20단계 완료했어")
        self.record("21단계 완료했어")
        self.record("아니")
        markdown = self.markdown()
        self.assertIn("| 19~20 | 반복 횟수 1회 — 사람이 답함(회차마다 물음) |", markdown)
        self.assertNotIn("넘어갔다", "\n".join(self.facts().deviations))

    def test_the_writer_facts_are_as_lane_n_left_them(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("41")
        self.record("41단계 완료했어")
        self.record("네")
        payload = er._writer_facts(self.facts())
        self.assertNotIn("조건 답", payload)
        self.assertIn("원문과 다르게 한 점", payload)
