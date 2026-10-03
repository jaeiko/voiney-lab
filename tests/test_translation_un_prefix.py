"""An "un-" word read as "~하지 않은" / "비-" is not a changed negation (lane R3, decision 9).

``reader_translation_issue`` refuses a reading that adds or drops a
negation. The source side only knows negation words ("not", "no", "avoid",
...), so "uninoculated" or "unlabelled" counted as no negation, while their
faithful Korean -- "접종하지 않은", "라벨을 붙이지 않은" -- counted as one, and
the reading was refused as ``negation_changed``. Decided 2026-10-03: a
"~하지 않은" (or a "비-" word) that renders an "un-" word of the source is
not a negation change. Readings that really add or drop one are refused as
before.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import reader_translation_issue


class UnPrefixTests(unittest.TestCase):
    def test_an_un_word_read_as_not_done_is_the_same_meaning(self) -> None:
        for source, reading in (
            ("Keep one uninoculated plate as a control.",
             "대조군으로 접종하지 않은 plate 하나를 둡니다."),
            ("Discard unlabelled tubes.", "라벨을 붙이지 않은 튜브는 버립니다."),
            ("Discard unlabeled tubes.", "라벨이 붙지 않은 튜브는 버립니다."),
            ("Store the unopened vial at 4 °C.", "개봉하지 않은 vial을 4 °C에 보관합니다."),
            ("Use an untreated sample as a reference.", "처리되지 않은 시료를 기준으로 씁니다."),
            ("Keep one uninoculated plate as a control.", "대조군으로 비접종 plate 하나를 둡니다."),
        ):
            with self.subTest(source=source):
                self.assertIsNone(reader_translation_issue(source, reading))

    def test_a_real_negation_beside_an_un_word_is_still_checked(self) -> None:
        # The source says "do not"; the reading drops it, keeping only the
        # rendering of "unlabelled".
        self.assertEqual(reader_translation_issue(
            "Do not use unlabelled tubes.", "라벨을 붙이지 않은 튜브를 사용합니다."),
            "negation_changed")
        # Both kept: no change.
        self.assertIsNone(reader_translation_issue(
            "Do not use unlabelled tubes.", "라벨을 붙이지 않은 튜브를 사용하지 마십시오."))
        # One "un-" word covers one "~지 않은", not a second, added negation.
        self.assertEqual(reader_translation_issue(
            "Keep the unopened vial.", "개봉하지 않은 vial을 흔들지 않습니다."),
            "negation_changed")

    def test_words_that_only_begin_with_un_are_not_negations(self) -> None:
        for source, reading in (
            ("Incubate until the gel is white.", "젤이 하얗게 되지 않은 동안 배양합니다."),
            ("Mix until uniform.", "균일해지지 않은 상태로 섞습니다."),
            ("Spin the unit for 1 min.", "1 min 동안 돌리지 않은 unit을 돌립니다."),
        ):
            with self.subTest(source=source):
                self.assertEqual(reader_translation_issue(source, reading), "negation_changed")

    def test_the_old_true_refusals_stand(self) -> None:
        for source, reading in (
            ("Do not vortex the lysate.", "lysate를 볼텍스합니다."),
            ("Mix the tube.", "튜브를 섞지 마십시오."),
            ("Avoid light.", "빛에 둡니다."),
            ("Mix the tube.", "튜브를 섞지 않습니다."),
        ):
            with self.subTest(source=source):
                self.assertEqual(reader_translation_issue(source, reading), "negation_changed")


if __name__ == "__main__":
    unittest.main()
