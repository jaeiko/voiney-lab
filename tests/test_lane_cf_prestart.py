"""Lane CF, decision 4 (field interviews, 2026-10-07/08): conditions and counts before the start.

"이 프로토콜로 시작" pressed, or "프로토콜 시작해줘" said: the source
conditions and the person-decided repeat counts lane CB asks about during the
run are asked first, one after another, before step 1. What is answered is
not asked again during the run; what is left ("나중에") is asked at its step,
as lane CB does. The setting "실험 중에 묻기" keeps lane CB's way.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.lane_cf_support import CONDITION_42, RANGE_21, RecordedSession, Session, index_of
from voiney_lab import experiment_reports as er
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import experimenter_setting_request

INDEX_HTML = Path(__file__).resolve().parents[1] / "src/voiney_lab/static/index.html"

INTRO = (
    "시작 전에 여쭤볼 게 2개 있어요. 답하신 것은 실험 중에 다시 묻지 않아요. "
    "지금 모르면 '나중에'라고 해 주세요."
)
COUNT_QUESTION = (
    f"(1/2) 19~20단계는 원문이 횟수를 정하지 않아요: “{RANGE_21}”. 몇 번(몇 개) 하시나요? "
    "아직 모르면 '아직 몰라', 지금 정하지 않으려면 '나중에'라고 해 주세요."
)
BRANCH_QUESTION = (
    f"(2/2) 42단계에는 원문 조건이 있어요: “{CONDITION_42}”. 이 조건에 해당하나요? "
    "맞으면 '네', 아니면 '아니요', 지금 모르면 '나중에'라고 해 주세요."
)


class PrestartRuleTests(Session, unittest.TestCase):

    def before_start(self) -> None:
        self.open_with(None, question_timing="before_start")

    def test_the_start_asks_the_questions_first(self) -> None:
        self.before_start()
        plan = self.say("프로토콜 시작해줘")
        self.assertFalse(self.session.active)
        self.assertFalse(plan.state_changed)
        self.assertEqual(plan.display_text, f"{INTRO} {COUNT_QUESTION}")
        self.assertEqual(self.session.last_front_rule, "start_command")
        self.assertEqual(self.session.open_server_question(), {"kind": "prestart", "text": COUNT_QUESTION})
        plan = self.say("세 번")
        self.assertEqual(self.session.last_front_rule, "prestart_question")
        self.assertEqual(plan.display_text, f"3회로 들었어요. {BRANCH_QUESTION}")
        self.assertFalse(self.session.active)
        plan = self.say("아니요")
        self.assertTrue(plan.state_changed)
        self.assertEqual(plan.action.value, "start")
        self.assertTrue(self.session.active)
        self.assertEqual(self.label(), "1")
        self.assertTrue(plan.speech_text.startswith(
            "조건에 해당하지 않는다고 들었어요. 시작 전 답을 기록했어요. "))
        record = plan.step_record
        self.assertEqual(record["kind"], "prestart_answers")
        self.assertEqual([item["answer"] for item in record["answers"]], ["no"])
        self.assertEqual([item["count"] for item in record["registered"]], [3])
        self.assertEqual(record["deferred"], [])
        self.assertEqual(self.session.branch_answers()["branch-42"]["asked"], "before_start")
        self.assertEqual(self.session.registered_repetitions()["repeat-19-20"]["decided"], "before_start")

    def test_what_was_answered_is_not_asked_again(self) -> None:
        self.before_start()
        for said in ("프로토콜 시작해줘", "두 번", "아니요"):
            self.say(said)
        self.session.current_index = index_of("18")
        plan = self.say("18단계 완료했어")
        self.assertEqual(self.label(), "19")
        self.assertNotIn("몇 번", plan.display_text)
        self.assertIsNone(self.session.open_server_question())
        self.session.current_index = index_of("41")
        plan = self.say("41단계 완료했어")
        # The "no" given before the start passes over step 42 on arrival.
        self.assertEqual(self.label(), "43")
        self.assertNotIn(CONDITION_42, plan.display_text)

    def test_later_leaves_the_question_for_its_step(self) -> None:
        self.before_start()
        self.say("프로토콜 시작해줘")
        plan = self.say("나중에")
        self.assertEqual(plan.display_text, f"19~20단계 횟수는 그 단계에서 여쭤볼게요. {BRANCH_QUESTION}")
        plan = self.say("네")
        self.assertTrue(self.session.active)
        self.assertEqual(plan.step_record["deferred"], ["repeat-19-20"])
        self.session.current_index = index_of("18")
        plan = self.say("18단계 완료했어")
        self.assertIn("몇 번(몇 개) 하시나요?", plan.display_text)
        self.session.current_index = index_of("41")
        plan = self.say("41단계 완료했어")
        self.assertEqual(self.label(), "42")
        self.assertNotIn("이 조건에 해당하나요", plan.display_text)

    def test_not_knowing_a_condition_leaves_it_for_its_step(self) -> None:
        self.before_start()
        self.say("프로토콜 시작해줘")
        self.say("세 번")
        plan = self.say("모르겠어")
        self.assertTrue(self.session.active)
        self.assertEqual(plan.step_record["deferred"], ["branch-42"])
        self.assertNotIn("branch-42", self.session.branch_answers())

    def test_all_later_starts_at_once(self) -> None:
        self.before_start()
        self.say("프로토콜 시작해줘")
        plan = self.say("다 나중에")
        self.assertTrue(self.session.active)
        self.assertEqual(plan.step_record["deferred"], ["repeat-19-20", "branch-42"])

    def test_a_start_or_other_words_while_asking(self) -> None:
        self.before_start()
        self.say("프로토콜 시작해줘")
        plan = self.say("프로토콜 시작해줘")
        self.assertFalse(self.session.active)
        self.assertEqual(plan.display_text, f"먼저 시작 전 질문에 답해 주세요. {COUNT_QUESTION}")
        self.say("Porapak 튜브가 뭐야?")
        self.assertFalse(self.session.active)
        self.assertEqual(self.session.open_server_question()["kind"], "prestart")
        questions = self.session._open_questions(turn_id=self.turn_id + 1, configuration_id=1, generation=1)
        self.assertEqual(questions.first_open, "prestart")

    def test_asked_during_the_run_when_set_so(self) -> None:
        self.open_with(None, question_timing="during")
        plan = self.say("프로토콜 시작해줘")
        self.assertTrue(self.session.active)
        self.assertNotIn("시작 전에 여쭤볼", plan.display_text)
        self.session.current_index = index_of("18")
        self.assertIn("몇 번(몇 개) 하시나요?", self.say("18단계 완료했어").display_text)

    def test_a_protocol_with_nothing_to_ask_starts_at_once(self) -> None:
        from tests.lane_cf_support import notes_fixture

        self.open_with(None, fixture=notes_fixture(), question_timing="before_start")
        self.assertIsNone(self.session.open_prestart_questions())
        self.say("프로토콜 시작해줘")
        self.assertTrue(self.session.active)

    def test_the_button_opens_the_questions_and_the_greeting_asks_the_first(self) -> None:
        self.before_start()
        intro = self.session.open_prestart_questions()
        self.assertEqual(intro, f"{INTRO} {COUNT_QUESTION}")
        holder = type("Listener", (), {"curated_protocol_session": self.session})()
        greeting = server_module._session_greeting_text(holder, "ko")
        self.assertTrue(greeting.endswith(f"준비되었습니다. {INTRO} {COUNT_QUESTION}"))
        plan = self.say("세 번")
        self.assertEqual(self.session.last_front_rule, "prestart_question")
        self.assertEqual(plan.display_text, f"3회로 들었어요. {BRANCH_QUESTION}")


class TimingSettingTests(Session, unittest.TestCase):
    """The setting that keeps lane CB's way: by voice and on the screen."""

    def test_the_words_that_choose_the_time(self) -> None:
        cases = {
            "분기 질문은 실험 중에 물어봐 줘": {"question_timing": "during"},
            "조건 질문은 그 단계에서 물어봐 줘": {"question_timing": "during"},
            "질문은 시작 전에 물어봐 줘": {"question_timing": "before_start"},
            "횟수 질문은 시작 전에 한 번에 물어봐 줘": {"question_timing": "before_start"},
        }
        for said, expected in cases.items():
            with self.subTest(said=said):
                self.assertEqual(experimenter_setting_request(said), expected)

    def test_said_before_the_start_it_changes_the_time(self) -> None:
        self.open_with(None, question_timing="before_start")
        plan = self.say("분기 질문은 실험 중에 물어봐 줘")
        self.assertEqual(self.session.question_timing, "during")
        self.assertEqual(plan.setting_change, {"question_timing": "during"})
        self.assertFalse(self.session.active)
        self.assertEqual(
            plan.display_text,
            "조건 분기와 반복 횟수는 실험 중에 그 단계에서 여쭤볼게요.")
        plan = self.say("프로토콜 시작해줘")
        self.assertTrue(self.session.active)

    def test_the_screen_offers_both(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        for marker in ('id="question-timing"', ">시작 전에 묻기", ">실험 중에 묻기",
                       'saveExperimenterSetting("question_timing"'):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)


class PrestartReportTests(RecordedSession, unittest.TestCase):

    def setUp(self) -> None:
        self.start_recording()

    def test_the_answers_are_recorded_with_the_start(self) -> None:
        self.open_with(None, question_timing="before_start")
        for said in ("프로토콜 시작해줘", "세 번", "네"):
            self.record(said)
        kinds = [kind for kind, _, _ in self.events()]
        self.assertEqual(kinds[kinds.index("session_started") + 1:], ["repeat_registered", "branch_answered"])
        answered = [record for kind, _, record in self.events() if kind == "branch_answered"][0]
        self.assertEqual((answered["answer"], answered["asked"]), ("yes", "before_start"))
        markdown = er.render_markdown(er.narrative_from_sections(
            er.build_report_facts(self.report(), fixture=self.session.fixture)))
        self.assertIn(f"| 42 | 조건: “{CONDITION_42}” → 예 (사람이 답함 · 시작 전에 답함) |", markdown)
        self.assertIn("| 19~20 | 반복 횟수 3회 — 사람이 답함 · 시작 전에 답함 |", markdown)


if __name__ == "__main__":
    unittest.main()
