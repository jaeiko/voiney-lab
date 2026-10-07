"""A negation kept as an avoidance, or an avoidance kept as a negation (lane AN, 3).

Human decision of 2026-10-07: when the source sentence and its Korean both
hold a negation or avoidance expression -- English without, no, not, never,
avoid, eliminate, prevent …; Korean 않, 없, 말, 못, 지 않도록, 않게 … -- the
negation is kept. When only one of them holds one, the Korean is refused as
before. The words are one table in ``protocol_translation``. Numbers, units
and the names to keep are checked exactly as before.

Measured 2026-10-06 (lane PX): "leaving one agar plate without any inoculum
as an uninoculated control" ↔ "접종원을 전혀 넣지 않은 비접종 대조군" and
"gently shaking the bags to eliminate clumping" ↔ "시료가 뭉치지 않도록"
were refused as ``negation_changed``; both say the same negation or
avoidance.
"""

from __future__ import annotations

import re
import unittest

from voiney_lab.curated_protocol import reader_translation_issue
from voiney_lab.protocol_translation import (
    NEGATION_VOCABULARY,
    GlossaryEntry,
    TranslationUnit,
    check_translation,
    negation_or_avoidance,
)

HEADSPACE_21 = (
    "21 Repeat steps 19-20 for the required number of bacterial isolates/replicates, "
    "leaving one agar plate without any inoculum as an uninoculated control.")
HEADSPACE_21_KO = (
    "21단계: 필요한 수의 세균 분리주/반복에 대해 19-20단계를 반복하고, 한천 평판 하나는 "
    "접종원을 전혀 넣지 않은 비접종 대조군으로 남겨 둡니다.")
ANKOM_6 = (
    "Spread the sample uniformly inside the filter bags by gently shaking the bags to "
    "eliminate clumping.")
ANKOM_6_KO = "6단계: bag을 부드럽게 흔들어 시료가 뭉치지 않도록 filter bag 안에 시료를 고르게 펼칩니다."


def step(source: str, label: str, terms: tuple[str, ...] = ()) -> TranslationUnit:
    return TranslationUnit(
        f"step-{label}/current_step", "step", source, terms,
        step_index=int(label) - 1, step_label=label)


class KeptNegationTests(unittest.TestCase):
    def test_the_two_sentences_refused_on_2026_10_06_pass(self) -> None:
        glossary = (GlossaryEntry("agar", "한천", False),)
        for unit, korean in (
            (step(HEADSPACE_21, "21", ("agar",)), HEADSPACE_21_KO),
            (step(ANKOM_6, "6", ("filter bags",)), ANKOM_6_KO),
        ):
            with self.subTest(source=unit.source_text[:30]):
                # The narrower check on its own still says the negation changed.
                self.assertEqual(
                    reader_translation_issue(
                        unit.source_text, korean, step_label=unit.step_label),
                    "negation_changed")
                self.assertEqual(check_translation(unit, korean, glossary), "passed")

    def test_an_avoidance_rendered_as_a_prevention_passes(self) -> None:
        unit = TranslationUnit(
            "protocol/protocol_purpose", "purpose",
            "See the Warning section for tips on avoiding keratin contamination.")
        self.assertEqual(
            check_translation(unit, "케라틴 오염을 방지하기 위한 요령은 Warning 항목을 참고합니다."),
            "passed")


class OneSidedNegationTests(unittest.TestCase):
    def test_a_negation_only_in_the_korean_is_refused(self) -> None:
        self.assertEqual(
            check_translation(step("3 Mix the sample gently.", "3"), "3단계: 시료를 부드럽게 섞지 않습니다."),
            "negation_changed")

    def test_a_negation_dropped_from_the_korean_is_refused(self) -> None:
        self.assertEqual(
            check_translation(step("3 Do not vortex the sample.", "3"), "3단계: 시료를 볼텍스합니다."),
            "negation_changed")
        self.assertEqual(
            check_translation(step("3 Avoid bubbles when pipetting.", "3"), "3단계: 피펫팅할 때 기포를 만듭니다."),
            "negation_changed")

    def test_regardless_is_not_a_negation_on_either_side(self) -> None:
        # ANKOM, 2026-10-06: still refused -- the source holds no negation.
        unit = TranslationUnit(
            "step-7/note_1", "note",
            "NOTE: All nine trays must be used regardless of the number of bags being processed.",
            step_index=6, step_label="7")
        self.assertEqual(
            check_translation(unit, "참고: 처리하는 백의 개수와 관계없이 9개의 트레이를 모두 사용해야 합니다."),
            "negation_changed")

    def test_a_dropped_negation_is_not_covered_by_a_word_that_only_looks_negative(self) -> None:
        for korean in ("3단계: 부피와 상관없는 튜브를 볼텍스합니다.",
                       "3단계: 잘못된 튜브를 볼텍스합니다."):
            with self.subTest(korean=korean):
                self.assertEqual(
                    check_translation(step("3 Do not vortex the tube.", "3"), korean),
                    "negation_changed")


class OtherChecksStandTests(unittest.TestCase):
    def test_a_kept_negation_with_a_changed_number_is_refused(self) -> None:
        self.assertEqual(
            check_translation(
                step("3 Add 5 mL buffer without vortexing to eliminate bubbles.", "3"),
                "3단계: 기포가 생기지 않도록 볼텍스하지 않고 완충액 6 mL를 넣습니다."),
            "quantities_changed")

    def test_a_kept_negation_with_a_dropped_name_is_refused(self) -> None:
        unit = step(ANKOM_6, "6", ("filter bags",))
        glossary = (GlossaryEntry("filter bags", "filter bag", True),)
        self.assertEqual(check_translation(unit, ANKOM_6_KO, glossary), "passed")
        self.assertEqual(
            check_translation(
                unit, "6단계: 백을 부드럽게 흔들어 시료가 뭉치지 않도록 필터 백 안에 시료를 고르게 펼칩니다.",
                glossary),
            "term_missing")

    def test_the_narrower_check_tells_a_negation_before_a_name(self) -> None:
        # check_translation relies on this order: a negation_changed answer
        # means every check before it passed and the name check did not run.
        self.assertEqual(
            reader_translation_issue(
                "3 Do not vortex the Thermomixer tube.", "3단계: 튜브를 볼텍스합니다.",
                required_terms=("thermomixer",), step_label="3"),
            "negation_changed")


class OneTableTests(unittest.TestCase):
    def test_the_decision_words_are_in_the_table(self) -> None:
        for word in ("without", "no", "not", "never", "avoid", "avoiding", "eliminate",
                     "eliminating", "prevent", "prevents", "don't", "cannot"):
            with self.subTest(word=word):
                self.assertTrue(negation_or_avoidance("en", f"Then {word} it."), word)
        for text in ("넣지 않습니다", "기포 없이", "넣지 말고", "넣지 마세요", "열지 못합니다",
                     "뭉치지 않도록", "섞이지 않게", "오염을 방지합니다", "기포를 피합니다", "사용 금지"):
            with self.subTest(text=text):
                self.assertTrue(negation_or_avoidance("ko", text), text)

    def test_words_that_only_look_like_one_are_not(self) -> None:
        for text in ("Read the note.", "Use a nominal volume.", "Cat. Number 5", "Another tube."):
            with self.subTest(text=text):
                self.assertFalse(negation_or_avoidance("en", text), text)
        for text in ("부피와 관계없이", "상관없는 튜브", "끊임없이 저어 줍니다", "분말을 넣습니다",
                     "잘못 넣은 경우", "잘못된 튜브", "마지막 단계", "제거합니다"):
            with self.subTest(text=text):
                self.assertFalse(negation_or_avoidance("ko", text), text)

    def test_the_table_is_one_place(self) -> None:
        self.assertEqual(set(NEGATION_VOCABULARY), {"en", "ko", "ko_not_negation"})
        for patterns in NEGATION_VOCABULARY.values():
            for pattern in patterns:
                re.compile(pattern)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
