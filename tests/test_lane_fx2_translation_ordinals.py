"""Lane FX2, decision 2 (2026-10-12): a rank or count word is no changed number.

``reader_translation_issue`` refused every Korean reading that writes a
rank the source words in letters: "primary antibody" read "1차 항체" holds a
1 the source has no digit for, so the reading was refused as
``quantities_changed`` and the step was read in English. Measured in lane
GE (§10), all seven refusals of Western blot's first ten steps were of this
shape.

Now a rank or count word of the source (primary/secondary/tertiary,
first/second/third, single/double/triple, a count such as the "two" of
"two-color" or the "one" of "one falcon tube") lets the reading hold its
Korean form (1차/2차/3차, 첫 번째/두 번째, 2중/2배, 2색/이색, 한 Falcon tube)
-- one word, one form, of the same number. Every other difference in the
numbers is refused as before: 1:1000 read 1:100, 5 min read 50 min, 2 times
read 3회, secondary read 3차, primary read 2차, a rank the source does not
have, and a count added beside a rank that has its form.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import reader_translation_issue
from voiney_lab.protocol_translation import TranslationUnit, check_translation


class RankAndCountWordsTests(unittest.TestCase):

    def test_a_rank_or_count_word_may_be_read_in_its_korean_form(self) -> None:
        for source, korean in (
            ("Dilute the primary antibody in Odyssey Blocking Buffer.",
             "Odyssey Blocking Buffer에 1차 항체를 희석합니다."),
            ("Incubate the blot in secondary antibody solution for 30-60 minutes.",
             "2차 항체 용액에서 블롯을 30-60 minutes 동안 인큐베이션합니다."),
            ("Add the same amount of Tween 20 to the secondary antibody as was added "
             "to the primary antibody.",
             "1차 항체에 넣은 것과 같은 양의 Tween 20을 2차 항체에 넣습니다."),
            ("For two-color detection, dilute the primary antibodies together.",
             "2색 검출의 경우 1차 항체들을 함께 희석합니다."),
            ("For two-color detection, dilute the primary antibodies together.",
             "이색 검출의 경우 1차 항체들을 함께 희석합니다."),
            ("Repeat the wash a second time.", "세척을 두 번째로 반복합니다."),
            ("Discard the first supernatant.", "첫 번째 상등액을 버립니다."),
            ("Discard the first supernatant.", "1차 상등액을 버립니다."),
            ("Rinse with double distilled water.", "2차 증류수로 헹굽니다."),
            ("Use a tertiary amine.", "3차 아민을 사용합니다."),
            ("Discard the 1st wash.", "첫 번째 세척액을 버립니다."),
            # A count before a name the reading keeps in English.
            ("Inoculate one falcon tube with the colony.", "콜로니를 한 Falcon tube에 접종합니다."),
        ):
            with self.subTest(korean=korean):
                self.assertIsNone(reader_translation_issue(source, korean))

    def test_any_other_change_of_number_is_refused_as_before(self) -> None:
        for source, korean in (
            ("Dilute the primary antibody 1:1000.", "1차 항체를 1:100으로 희석합니다."),
            ("Incubate the secondary antibody for 5 min.", "2차 항체를 50 min 동안 반응시킵니다."),
            ("Wash the membrane 2 times.", "멤브레인을 3회 세척합니다."),
            ("Add the secondary antibody.", "3차 항체를 넣습니다."),
            ("Add the primary antibody.", "2차 항체를 넣습니다."),
            # A rank the source does not state.
            ("Dilute the antibody in blocking buffer.", "블로킹 버퍼에 2차 항체를 희석합니다."),
            # One rank word, one form: a count added beside it is a number.
            ("Add the secondary antibody and wash.", "2차 항체를 넣고 2회 세척합니다."),
            # The 배 of "배양" is no multiple: the stray 2 before it is a number.
            ("Dilute the secondary antibody and incubate in the secondary antibody.",
             "2차 항체를 희석하고 항체에서 2 배양합니다."),
            # The count of "two-color" read as another number.
            ("For two-color detection, dilute together.", "삼색 검출의 경우 함께 희석합니다."),
            ("Inoculate one falcon tube with the colony.", "콜로니를 두 Falcon tubes에 접종합니다."),
        ):
            with self.subTest(korean=korean):
                self.assertEqual(reader_translation_issue(source, korean), "quantities_changed")

    def test_the_stored_translation_check_follows(self) -> None:
        unit = TranslationUnit(
            "step-3/current_step", "step",
            "Dilute primary antibody in Odyssey Blocking Buffer. Optimal dilution varies "
            "by antibody and should be determined empirically.",
            required_terms=("Odyssey Blocking Buffer",), step_index=2, step_label="3",
        )
        self.assertEqual(check_translation(
            unit, "3단계: Odyssey Blocking Buffer에 1차 항체를 희석합니다. 최적 희석 배율은 "
                  "항체마다 다르며 실험으로 정합니다."), "passed")
        self.assertEqual(check_translation(
            unit, "3단계: Odyssey Blocking Buffer에 2차 항체를 희석합니다. 최적 희석 배율은 "
                  "항체마다 다르며 실험으로 정합니다."), "quantities_changed")


if __name__ == "__main__":
    unittest.main()
