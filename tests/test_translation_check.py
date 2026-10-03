"""``reader_translation_issue``: the same counting rules on both sides.

Decided 2026-10-03 (PR #24): count words are counted alike in a source and
a reading, "Once" opening a sentence is a conjunction, every form of
"avoid" pairs with "피하", and only reagent, material and equipment names
must keep their original spelling. A reading that really changes a number,
a unit or a negation is still refused.
"""

from __future__ import annotations

import unittest

from voiney_lab.curated_protocol import CuratedProtocolSession, reader_translation_issue

from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture


class FalseRefusalsFixedTests(unittest.TestCase):
    def test_counts_are_counted_alike_on_both_sides(self) -> None:
        for source, reading in (
            ("Place one tablet into 500 mL water.", "500 mL water에 tablet 하나를 넣습니다."),
            ("Place one tablet into 500 mL water.", "500 mL water에 tablet 한 알을 넣습니다."),
            ("Keep four replicates per treatment.", "treatment당 four replicates를 둡니다."),
            ("Keep four replicates per treatment.", "treatment당 반복을 네 개 둡니다."),
            ("Prepare two wash solutions.", "두 세척 용액을 준비합니다."),
            ("This takes two cycles of washes.", "세척 두 번의 사이클이 걸립니다."),
            ("Repeat twice.", "2번 반복합니다."),
            ("Repeat twice.", "두 번 반복합니다."),
            ("Mix three times.", "세 번 섞습니다."),
        ):
            with self.subTest(source=source, reading=reading):
                self.assertIsNone(reader_translation_issue(source, reading))

    def test_once_opening_a_sentence_is_a_conjunction(self) -> None:
        for source in (
            "Once you complete the digestion, load the Evotip.",
            "Dry the gel. Once your gel is white, add trypsin.",
            "9 Once the tube is cool, spin it down.",
        ):
            with self.subTest(source=source):
                self.assertIsNone(reader_translation_issue(
                    source, "소화를 마치면 Evotip을 로드합니다. trypsin 튜브 gel"
                    if "Evotip" in source else
                    "젤이 하얘지면 trypsin을 넣습니다. 젤을 말립니다."
                    if "trypsin" in source else
                    "9 튜브가 식으면 스핀다운합니다."))
        # Repeated once is still a count of one.
        self.assertIsNone(reader_translation_issue("Repeat once.", "한 번 반복합니다."))
        self.assertEqual(
            reader_translation_issue("Repeat once.", "반복합니다."), "quantities_changed")

    def test_every_form_of_avoid_pairs_with_pihada(self) -> None:
        for source in (
            "Work cleanly to avoid contamination.",
            "This avoids contamination.",
            "Tips on avoiding contamination.",
            "Contamination is avoided by clean tools.",
        ):
            for reading in ("오염을 피하려면 깨끗하게 작업합니다.", "오염을 피합니다.",
                            "깨끗한 도구로 오염을 피했습니다."):
                with self.subTest(source=source, reading=reading):
                    self.assertIsNone(reader_translation_issue(source, reading))

    def test_general_nouns_may_be_korean_but_names_may_not(self) -> None:
        source = "Avoid keratin contamination of the gel plug during incubation with trypsin."
        terms = ("keratin", "contamination", "gel plug", "incubation", "trypsin")
        self.assertIsNone(reader_translation_issue(
            source, "trypsin과 배양하는 동안 젤 조각의 케라틴 오염을 피합니다.",
            required_terms=terms))
        self.assertEqual(reader_translation_issue(
            source, "트립신과 배양하는 동안 젤 조각의 케라틴 오염을 피합니다.",
            required_terms=terms), "term_missing")


class TrueRefusalsKeptTests(unittest.TestCase):
    def test_a_changed_number_unit_or_negation_is_still_refused(self) -> None:
        for source, reading, issue in (
            ("Add 500 mL water.", "water 50 mL를 넣습니다.", "quantities_changed"),
            ("Wash twice.", "한 번 세척합니다.", "quantities_changed"),
            ("Do not vortex the lysate.", "lysate를 볼텍스합니다.", "negation_changed"),
            ("Prepare 400 mL of LB agar (37 g per litre).",
             "400 mL의 LB agar를 준비합니다.", "quantities_changed"),
            ("Keep the tube at 4 °C.", "튜브를 40 °C에 둡니다.", "quantities_changed"),
            ("Place one tablet in water.", "water에 tablet 두 알을 넣습니다.",
             "quantities_changed"),
            ("Mix the tube.", "튜브를 섞지 마십시오.", "negation_changed"),
            ("Avoid light.", "빛에 둡니다.", "negation_changed"),
        ):
            with self.subTest(source=source, reading=reading):
                self.assertEqual(reader_translation_issue(source, reading), issue)

    def test_han_after_a_verb_and_yeol_before_a_noun_are_not_counts(self) -> None:
        self.assertIsNone(reader_translation_issue(
            "Wash the gel, then add 50 µL trypsin.",
            "젤을 세척을 한 후 trypsin 50 µL를 넣습니다."))
        self.assertIsNone(reader_translation_issue(
            "Place the tube on the heat block at 37 °C.",
            "튜브를 37 °C 열 블록에 놓습니다."))


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelUnchangedTests(unittest.TestCase):
    def test_the_safety_box_keeps_its_reviewed_warning(self) -> None:
        from voiney_lab.server import curated_safety_items

        fixture = in_gel_fixture()
        curated = CuratedProtocolSession(fixture)
        curated.active = True
        item = curated_safety_items(curated)[0]
        self.assertEqual(item["primary_text"], " ".join(
            fixture.localized_fact("candidate-a-step-01", "warning_1").split()))
        self.assertEqual(item["translation_check"], "passed")

    def test_a_reviewed_line_that_adds_a_negation_is_still_refused(self) -> None:
        fixture = in_gel_fixture()
        step = fixture.steps[8]
        self.assertEqual(reader_translation_issue(
            step.instruction_source_text,
            fixture.localized_fact(step.step_id, "current_step")), "negation_changed")


if __name__ == "__main__":
    unittest.main()
