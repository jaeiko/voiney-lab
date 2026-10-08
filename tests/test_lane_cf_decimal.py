"""Lane CF, decision 2 (field interviews, 2026-10-07/08): the decimal point.

"0.5 mL 와 5 mL 는 완전히 다르다." A value said the Korean way ("영 점 오",
"0점5", "점 오", "오 점 영") and the digits the STT wrote are read as one
value, and a value is read back with its decimal point. When the words have
a "점" the number has no decimal part for -- or the other way round, a written
point beside a spoken one -- nothing is stored and the value is asked for
again.
"""

from __future__ import annotations

import unittest

from tests.lane_cf_support import VoiceNotesHarness, shown, wash_session
from voiney_lab.curated_protocol import decimal_problem, measured_values, spoken_korean


class ValueReadingTests(unittest.TestCase):
    """Representative ways of saying one value, kept by test."""

    def test_the_ways_of_saying_half_a_millilitre_are_one_value(self) -> None:
        for said in (
            "0.5 mL", "0.5mL", "0.5 ml", "0점5 mL", "0점5mL", "0 점 5 밀리리터",
            "영 점 오 밀리리터", "영점오 밀리리터", "점 오 밀리리터", "공 점 오 mL",
            "영 점 오 mL", "0.5 밀리리터",
        ):
            with self.subTest(said=said):
                values = measured_values(said)
                self.assertEqual([value.text for value in values], ["0.5 mL"])
                self.assertEqual(values[0].number, "0.5")
                self.assertEqual(values[0].unit, "mL")

    def test_other_values(self) -> None:
        cases = {
            "오 점 영 밀리리터": "5.0 mL",
            "5 mL": "5 mL",
            "오 밀리리터": "5 mL",
            "십이 점 오 마이크로리터": "12.5 µL",
            "12.5 µL": "12.5 µL",
            "일 점 이 오 밀리리터": "1.25 mL",
            "pH 7.2": "pH 7.2",
            "pH 칠 점 이": "pH 7.2",
            "37도": "37도",
            "삼십칠 도": "37도",
            "이십 점 영 오 그램": "20.05 g",
            "0.05 mL": "0.05 mL",
        }
        for said, text in cases.items():
            with self.subTest(said=said):
                self.assertEqual([value.text for value in measured_values(said)], [text])

    def test_words_that_are_not_values(self) -> None:
        for said in ("튜브 라벨 A-170", "오늘은 조금 늦었어", "이 점이 이상해", "두 번째 튜브"):
            with self.subTest(said=said):
                self.assertEqual(measured_values(said), ())

    def test_the_reading_never_drops_the_point(self) -> None:
        self.assertEqual(spoken_korean("0.5 mL"), "영 점 오 밀리리터")
        self.assertEqual(spoken_korean("5.0 mL"), "오 점 영 밀리리터")
        self.assertEqual(spoken_korean("0.05 mL"), "영 점 영 오 밀리리터")
        self.assertEqual(spoken_korean("12.5 µL"), "십이 점 오 마이크로리터")

    def test_a_point_without_its_digits_or_two_points_is_a_problem(self) -> None:
        for said in ("5점 mL", "오 점 밀리리터", "0.5점 mL", "점 0.5 mL", "0.5 점 5 mL", "영 점 mL"):
            with self.subTest(said=said):
                self.assertTrue(decimal_problem(said))
        for said in ("0점5 mL", "점 오 밀리리터", "5 mL", "pH 7.2", "영 점 오 밀리리터",
                     "튜브 라벨 A-170", "이 점이 이상해", "0.5 mL"):
            with self.subTest(said=said):
                self.assertFalse(decimal_problem(said))


class NoteDecimalTests(unittest.TestCase):

    def started(self, mode: str = "readback"):
        turns = wash_session(confirm_mode=mode)
        turns.say("프로토콜 시작해줘")
        turns.say("1단계 완료했어")
        return turns

    def test_an_unclear_point_is_not_stored_and_is_asked_again(self) -> None:
        for mode in ("readback", "confirm", "quiet"):
            with self.subTest(mode=mode):
                turns = self.started(mode)
                plan = turns.say("실험노트에 적어 줘, 5점 mL")
                self.assertIsNone(plan.note_record)
                self.assertFalse(plan.reported_observation)
                self.assertEqual(
                    plan.display_text,
                    "소수점이 분명하지 않아 기록하지 않았어요. 값을 다시 말씀해 주세요. "
                    "예: '영 점 오 밀리리터'.")
                self.assertEqual(plan.intent_kind, "note_decimal_unclear")
                self.assertIsNone(turns.session.pending_note_confirmation)

    def test_the_value_spoken_in_korean_is_kept_as_said_and_read_as_digits(self) -> None:
        turns = self.started()
        plan = turns.say("실험노트에 적어 줘, 영 점 오 밀리리터")
        self.assertEqual(plan.note_record["content"], "영 점 오 밀리리터")
        self.assertEqual(plan.note_record["category"], "measurement")
        self.assertEqual([value["text"] for value in plan.note_record["values"]], ["0.5 mL"])

    def test_a_correction_with_an_unclear_point_is_not_offered(self) -> None:
        turns = self.started()
        turns.say("실험노트에 적어 줘, 0.5 mL")
        plan = turns.say("방금 기록 고쳐 줘, 0.5 mL가 아니라 5점 mL")
        self.assertIn("소수점이 분명하지 않아", plan.display_text)
        self.assertIsNone(turns.session._pending_record_fix)


class ServedDecimalTests(VoiceNotesHarness, unittest.TestCase):

    def test_every_way_of_saying_it_is_read_back_the_same(self) -> None:
        said = ("실험노트에 적어 줘, 0.5 mL", "실험노트에 적어 줘, 0점5 mL",
                "실험노트에 적어 줘, 영 점 오 밀리리터", "실험노트에 적어 줘, 점 오 밀리리터")
        socket = self.turns("프로토콜 시작해줘", "1단계 완료했어", *said)
        replies = shown(socket)
        for turn in range(3, 3 + len(said)):
            with self.subTest(turn=turn):
                self.assertEqual(replies[turn], "0.5 mL로 기록했어요.")
                self.assertEqual(self.spoken[turn], ["영 점 오 밀리리터로 기록했어요."])
        stored = [e["user_wording"] for e in self.report()["events"] if e["event_type"] == "observation"]
        # The words are kept as the STT gave them (lane N, decision 5).
        self.assertEqual(stored, ["0.5 mL", "0점5 mL", "영 점 오 밀리리터", "점 오 밀리리터"])

    def test_an_unclear_point_stores_nothing(self) -> None:
        socket = self.turns("프로토콜 시작해줘", "1단계 완료했어", "실험노트에 적어 줘, 5점 mL")
        self.assertIn("소수점이 분명하지 않아", shown(socket)[3])
        self.assertFalse([e for e in self.report()["events"] if e["event_type"] == "observation"])


if __name__ == "__main__":
    unittest.main()
