"""Lane CF, decision 7 (2026-10-08): someone else's words are not written down.

At the bench more than one person talks. A note is recorded only when the
words ask for one -- "기록해 줘", "메모해 줘", "실험노트에 적어 줘" (lane N's
note rule) -- and a value said without them, by the experimenter or anyone
near the microphone, is not. The check found nothing to change; these tests
keep it so.
"""

from __future__ import annotations

import unittest

from tests.lane_cf_support import wash_session


class ExplicitNoteTests(unittest.TestCase):
    """The words that start a note, and words that do not."""

    def started(self):
        turns = wash_session()
        turns.say("프로토콜 시작해줘")
        turns.say("1단계 완료했어")
        return turns

    def test_values_said_without_asking_for_a_note_are_not_recorded(self) -> None:
        for said in ("0.5 mL 넣었어", "pH 7.2 나왔네", "영 점 오 밀리리터", "튜브 라벨은 A-170이야",
                     "적어야 하나?", "기록해야 돼?"):
            with self.subTest(said=said):
                turns = self.started()
                plan = turns.say(said)
                self.assertIsNone(plan.note_record)
                self.assertFalse(plan.reported_observation)

    def test_the_explicit_words_record(self) -> None:
        for said in ("기록해 줘 0.5 mL 넣었어", "메모해 줘 0.5 mL 넣었어", "실험노트에 적어 줘 0.5 mL 넣었어",
                     "0.5 mL 넣었다고 기록해 줘"):
            with self.subTest(said=said):
                turns = self.started()
                plan = turns.say(said)
                self.assertIsNotNone(plan.note_record)



if __name__ == "__main__":
    unittest.main()
