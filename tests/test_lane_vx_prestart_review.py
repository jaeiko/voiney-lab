"""Lane VX, decisions 2 and 3 (field interviews, 2026-10-09), what lane CF left.

Decision 2: while the questions before the start are asked, "그냥 시작해",
"질문은 나중에" and "바로 시작" leave every question still to ask for its step
("나중에") and start the experiment. Lane CF re-asked nothing and did not start.

Decision 3: a value the experimenter said "네" to in the 바로 확인 way is not
asked again in the end-of-experiment review; the report keeps it as "실험자
확인". A correction made later takes the confirmation back, as a correction
of a reviewed value does (lane N, decision 3).
"""

from __future__ import annotations

import unittest

from tests.lane_cf_support import CONDITION_42, RANGE_21, Session, VoiceNotesHarness, index_of, shown
from voiney_lab import experiment_reports as er

BRANCH_QUESTION = (
    f"(2/2) 42단계에는 원문 조건이 있어요: “{CONDITION_42}”. 이 조건에 해당하나요? "
    "맞으면 '네', 아니면 '아니요', 지금 모르면 '나중에'라고 해 주세요."
)
COUNT_QUESTION = (
    f"(1/2) 19~20단계는 원문이 횟수를 정하지 않아요: “{RANGE_21}”. 몇 번(몇 개) 하시나요? "
    "아직 모르면 '아직 몰라', 지금 정하지 않으려면 '나중에'라고 해 주세요."
)


class StartNowTests(Session, unittest.TestCase):

    def asking(self) -> None:
        self.open_with(None, question_timing="before_start")
        self.say("프로토콜 시작해줘")
        self.assertFalse(self.session.active)

    def test_just_start_leaves_every_question_for_its_step(self) -> None:
        self.asking()
        plan = self.say("그냥 시작해")
        self.assertEqual(self.session.last_front_rule, "prestart_question")
        self.assertTrue(plan.state_changed)
        self.assertTrue(self.session.active)
        self.assertEqual(self.label(), "1")
        self.assertTrue(plan.speech_text.startswith(
            "남은 질문은 그 단계에서 여쭤볼게요. 실험을 시작합니다. 현재 1단계입니다."), plan.speech_text)
        self.assertEqual(plan.step_record["deferred"], ["repeat-19-20", "branch-42"])
        self.assertEqual(plan.step_record["answers"], [])
        # Left for its step, each is asked there, as lane CB asks it.
        self.session.current_index = index_of("18")
        self.assertIn("몇 번(몇 개) 하시나요?", self.say("18단계 완료했어").display_text)
        self.session.current_index = index_of("41")
        self.say("41단계 완료했어")
        self.assertEqual(self.label(), "42")
        self.assertIn("이 조건에 해당하나요", self.session.open_server_question()["text"])

    def test_an_answer_given_first_is_kept(self) -> None:
        self.asking()
        self.assertEqual(self.say("세 번").display_text, f"3회로 들었어요. {BRANCH_QUESTION}")
        plan = self.say("질문은 나중에")
        self.assertTrue(self.session.active)
        self.assertTrue(plan.speech_text.startswith(
            "남은 질문은 그 단계에서 여쭤볼게요. 시작 전 답을 기록했어요. 실험을 시작합니다."))
        self.assertEqual([item["count"] for item in plan.step_record["registered"]], [3])
        self.assertEqual(plan.step_record["deferred"], ["branch-42"])

    def test_every_way_of_saying_it(self) -> None:
        for said in ("그냥 시작해", "그냥 시작해줘", "그냥 시작하자", "그냥 시작", "바로 시작",
                     "바로 시작해", "바로 시작해 줘", "바로 시작하자", "질문은 나중에",
                     "질문은 나중에 해 줘", "질문은 다 나중에", "질문 나중에 하고 시작해",
                     "일단 시작해", "질문 말고 그냥 시작해"):
            with self.subTest(said=said):
                self.asking()
                self.say(said)
                self.assertTrue(self.session.active)
                self.assertEqual(self.label(), "1")

    def test_asked_as_a_question_it_does_not_start(self) -> None:
        for said in ("그냥 시작해도 돼?", "바로 시작할까?", "질문은 나중에 해도 돼?"):
            with self.subTest(said=said):
                self.asking()
                self.say(said)
                self.assertFalse(self.session.active)
                self.assertEqual(self.session.open_server_question()["kind"], "prestart")

    def test_with_nothing_asked_the_words_change_nothing_new(self) -> None:
        # Outside the questions before the start the words are what they were.
        self.open_with(None, question_timing="during")
        plan = self.say("바로 시작")
        self.assertNotEqual(self.session.last_front_rule, "prestart_question")
        self.assertNotIn("남은 질문", plan.speech_text)


class ConfirmedValueTests(unittest.TestCase):
    """report_review_items: a value confirmed when it was recorded."""

    @staticmethod
    def event(kind: str, key: str, wording: str = "", payload: dict | None = None) -> dict:
        return {"event_type": kind, "event_key": key, "step_label": "2",
                "user_wording": wording, "category": "measurement", "payload": payload or {}}

    def test_confirmed_when_recorded_is_confirmed(self) -> None:
        events = [
            self.event("observation", "a", "0.5 mL", {"experimenter_confirmed": True}),
            self.event("observation", "b", "pH 7.2"),
        ]
        items = er.report_review_items(events)
        self.assertEqual([(item["text"], item["confirmed"]) for item in items],
                         [("0.5 mL", True), ("pH 7.2", False)])

    def test_a_later_correction_takes_the_confirmation_back(self) -> None:
        events = [
            self.event("observation", "a", "0.5 mL", {"experimenter_confirmed": True}),
            self.event("record_corrected", "f", payload={"record_fix": {
                "target_event_key": "a", "text_before": "0.5 mL", "text_after": "0.6 mL"}}),
        ]
        item = er.report_review_items(events)[0]
        self.assertEqual((item["text"], item["confirmed"]), ("0.6 mL", False))

    def test_only_true_counts(self) -> None:
        for value in ("true", 1, "yes", None):
            with self.subTest(value=value):
                events = [self.event("observation", "a", "0.5 mL", {"experimenter_confirmed": value})]
                self.assertFalse(er.report_review_items(events)[0]["confirmed"])


class ServedConfirmedValueTests(VoiceNotesHarness, unittest.TestCase):
    """The production path: a 바로 확인 "네", the end review, the report."""

    def test_a_value_said_yes_to_is_not_asked_again_at_the_end(self) -> None:
        self.turns("확인 질문 켜 줘")
        socket = self.turns(
            "프로토콜 시작해줘", "1단계 완료했어", "실험노트에 적어 줘, 0.5 mL", "네",
            "확인 질문 꺼 줘", "실험노트에 적어 줘, pH 7.2", "실험 종료해", "응", "네")
        replies = shown(socket)
        self.assertEqual(replies[4], "0.5 mL로 기록했어요.")
        self.assertIn("보고서에 넣을 중요 값 1개를 확인할게요. 첫째, 2단계 측정, pH 7.2.", replies[8])
        self.assertNotIn("0.5 mL", replies[8])
        observation = next(
            e for e in self.report()["events"]
            if e["event_type"] == "observation" and e["user_wording"] == "0.5 mL")
        self.assertIs(observation["payload"]["experimenter_confirmed"], True)
        markdown = self.report_markdown()
        self.assertIn("| 2 | 측정 | 0.5 mL (실험자 확인) |", markdown)
        self.assertIn("| 2 | 측정 | pH 7.2 (실험자 확인) |", markdown)
        self.assertIn("보고서에 넣은 중요 값 2개를 실험자가 모두 확인했다", markdown)

    def test_a_value_stored_without_a_question_is_still_reviewed(self) -> None:
        socket = self.turns(
            "프로토콜 시작해줘", "1단계 완료했어", "실험노트에 적어 줘, 0.5 mL", "실험 종료해", "응")
        self.assertIn("보고서에 넣을 중요 값 1개를 확인할게요. 첫째, 2단계 측정, 0.5 mL.", shown(socket)[5])
        observation = next(e for e in self.report()["events"] if e["event_type"] == "observation")
        self.assertNotIn("experimenter_confirmed", observation["payload"])


if __name__ == "__main__":
    unittest.main()
