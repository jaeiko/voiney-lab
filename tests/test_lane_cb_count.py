"""Lane CB, decision 2 (2026-10-07): a count the source leaves to the person is asked on entry.

Headspace step 21 reads "Repeat steps 19-20 for the required number of
bacterial isolates/replicates". On first entering step 19 the server asks
how many; a number is read back (lane N's form, not asked about) and the
range's repetition is registered with that count, its source "operator";
"아직 몰라" registers the repetition with no count, and the end of each round
asks whether to do one more (decision 3). Before the answer nothing moves on:
trying to does ask again. Nothing defaults the count.
"""

from __future__ import annotations

import unittest

from tests.lane_cb_support import FIRST_HINT, RANGE_21, Recorded, Turns, index_of
from voiney_lab.curated_protocol import FRONT_RULES, CuratedProtocolAction

ASK_19 = (
    f"19~20단계는 원문이 횟수를 정하지 않아요: “{RANGE_21}”. 몇 번(몇 개) 하시나요? "
    "아직 모르면 '아직 몰라'라고 해 주세요."
)
FIRST_19 = (
    "먼저 19~20단계를 몇 번(몇 개) 하실지 말씀해 주세요. "
    "아직 모르면 '아직 몰라'라고 해 주세요. 단계를 넘기지 않았어요."
)
UNKNOWN_19 = "알겠어요. 횟수는 정하지 않았어요. 19~20단계를 한 번 할 때마다 한 번 더 하실지 여쭤볼게요."


class OperatorCountTests(Turns, unittest.TestCase):
    def enter_19(self):
        self.open(index_of("18"))
        return self.say("18단계 완료했어")

    def test_entering_the_range_asks_how_many(self) -> None:
        plan = self.enter_19()
        self.assertEqual(self.label(), "19")
        self.assertTrue(plan.state_changed)
        # Lane VX, decision 1: a named completion says what it recorded first.
        self.assertTrue(plan.speech_text.startswith(
            "18단계 완료로 기록했어요." + FIRST_HINT + " 19단계로 이동했습니다."))
        self.assertTrue(plan.speech_text.endswith(ASK_19), plan.speech_text)
        self.assertIn(ASK_19, plan.display_text)
        question = self.session.open_server_question()
        self.assertEqual((question["kind"], question["text"]), ("repeat_count", ASK_19))

    def test_a_number_is_read_back_and_registered(self) -> None:
        for said, count, spoken in (
            ("세 번", 3, "세 번"), ("3번", 3, "세 번"), ("3개", 3, "세 번"), ("3", 3, "세 번"),
            ("세 개", 3, "세 번"), ("3회", 3, "세 번"), ("3번 할게", 3, "세 번"),
            ("다섯 개 할 거야", 5, "다섯 번"), ("12개", 12, "열두 번"), ("두 번이요", 2, "두 번"),
        ):
            with self.subTest(said=said):
                self.enter_19()
                before = self.projection()
                plan = self.say(said)
                self.assertEqual(self.session.last_front_rule, "repeat_count")
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.projection(), before)
                self.assertEqual(
                    plan.display_text,
                    f"{count}회로 기록했어요(사람이 답함). 19~20단계를 {count}회 하고, "
                    f"지금은 1/{count}회차예요.",
                )
                self.assertEqual(
                    plan.speech_text,
                    f"{spoken}으로 기록했어요. 19~20단계를 {spoken} 하고, 지금은 {count}회 중 1회차예요.",
                )
                record = plan.step_record
                self.assertEqual(record["kind"], "repeat_registered")
                self.assertEqual(record["repetition_id"], "repeat-19-20")
                self.assertEqual(record["repeated_step_labels"], ["19", "20"])
                self.assertEqual(record["stated_at_step"], "21")
                self.assertEqual(record["count"], count)
                self.assertEqual(record["value_source"], "operator")
                self.assertEqual(record["decided"], "at_entry")
                self.assertEqual(record["source_text"], RANGE_21)
                self.assertIsNone(self.session.open_server_question())
                self.assertEqual(
                    self.session.registered_repetitions()["repeat-19-20"]["count"], count,
                )

    def test_not_yet_known_registers_the_repeat_without_a_count(self) -> None:
        for said in ("아직 몰라", "아직 모르겠어", "모르겠어요", "미정이야"):
            with self.subTest(said=said):
                self.enter_19()
                plan = self.say(said)
                self.assertEqual(self.session.last_front_rule, "repeat_count")
                self.assertEqual(plan.speech_text, UNKNOWN_19)
                self.assertEqual(plan.display_text, UNKNOWN_19)
                self.assertFalse(plan.state_changed)
                record = plan.step_record
                self.assertEqual(record["kind"], "repeat_registered")
                self.assertIsNone(record["count"])
                self.assertEqual(record["value_source"], "operator")
                self.assertEqual(record["decided"], "per_round")
                self.assertIsNone(self.session.open_server_question())
                self.assertIsNone(self.session.registered_repetitions()["repeat-19-20"]["count"])

    def test_moving_on_before_answering_asks_again(self) -> None:
        for said in ("19단계 완료했어", "다음 단계", "완료했어"):
            with self.subTest(said=said):
                self.enter_19()
                before = self.projection()
                plan = self.say(said)
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.projection(), before)
                self.assertEqual(plan.speech_text, FIRST_19)
                self.assertEqual(self.session.open_server_question()["kind"], "repeat_count")

    def test_after_the_answer_the_range_is_walked(self) -> None:
        self.enter_19()
        self.say("세 번")
        plan = self.say("19단계 완료했어")
        self.assertTrue(plan.state_changed)
        self.assertEqual(self.label(), "20")
        self.assertEqual(plan.speech_text, "19단계 완료로 기록했어요. 20단계로 이동했습니다. 안내를 화면에 표시했습니다.")
        self.say("20단계 완료했어")
        self.assertEqual(self.label(), "21")

    def test_zero_and_a_bare_yes_are_not_a_count(self) -> None:
        for said in ("0번", "네", "응", "많이"):
            with self.subTest(said=said):
                self.enter_19()
                self.say(said)
                self.assertEqual(self.session.open_server_question()["kind"], "repeat_count")
                self.assertEqual(self.session.registered_repetitions(), {})

    def test_the_question_stays_open_under_other_turns(self) -> None:
        self.enter_19()
        self.say("현재 단계 알려줘")
        self.assertEqual(self.session.open_server_question()["kind"], "repeat_count")
        self.say("3번")
        self.assertIsNone(self.session.open_server_question())

    def test_a_count_supplied_on_the_screen_is_not_asked_again(self) -> None:
        self.open(index_of("18"))
        self.session.provide_operator_repetition_count(
            "repeat-19-20", 4, actor_principal_id="p", actor_role="researcher",
        )
        plan = self.say("18단계 완료했어")
        self.assertEqual(self.label(), "19")
        self.assertEqual(
            plan.speech_text,
            "18단계 완료로 기록했어요." + FIRST_HINT + " 19단계로 이동했습니다. 안내를 화면에 표시했습니다.")
        self.assertIsNone(self.session.open_server_question())

    def test_the_answer_rolls_back_with_its_turn(self) -> None:
        self.enter_19()
        checkpoint = self.session._checkpoint()
        self.say("세 번")
        self.assertEqual(self.session.registered_repetitions()["repeat-19-20"]["count"], 3)
        self.session._restore(checkpoint)
        self.assertEqual(self.session.registered_repetitions(), {})
        self.assertEqual(self.session.open_server_question()["kind"], "repeat_count")

    def test_nothing_defaults_the_count(self) -> None:
        self.enter_19()
        self.assertEqual(self.session.registered_repetitions(), {})
        self.assertEqual(self.session.operator_repetition_counts(), {})

    def test_the_fixed_range_asks_nothing(self) -> None:
        self.open(index_of("11"))
        plan = self.say("11단계 완료했어")
        self.assertEqual(
            plan.speech_text,
            "11단계 완료로 기록했어요." + FIRST_HINT + " 12단계로 이동했습니다. 안내를 화면에 표시했습니다.")
        self.assertIsNone(self.session.open_server_question())

    def test_the_front_rule_is_described(self) -> None:
        self.assertIn("repeat_count", FRONT_RULES)


class ExperimentRecordTests(Recorded, unittest.TestCase):
    def setUp(self) -> None:
        self.start_recording()

    def test_the_count_is_its_own_event_at_the_step_asked(self) -> None:
        self.open(None)
        self.record("프로토콜 시작해줘")
        self.session.current_index = index_of("18")
        self.record("18단계 완료했어")
        self.record("세 번")
        kinds = [(kind, label) for kind, label, _ in self.events()]
        self.assertEqual(kinds[-1], ("repeat_registered", "19"))
        record = self.events()[-1][2]
        self.assertEqual((record["count"], record["value_source"]), (3, "operator"))
