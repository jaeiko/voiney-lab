""""Regardless" is not a negation, in either check (lane FX, decision 1).

Human decision of 2026-10-07: "관계없이", "상관없이" and "무관하게", in any of
their forms, are not negations. They are out of the narrow check
(``curated_protocol._KOREAN_NEGATION``, which ``reader_translation_issue``
reads) and out of the wide table (``protocol_translation.NEGATION_VOCABULARY``).

Before it, the narrow check counted the 없이 of 관계없이 as a negation, so a
Korean that dropped the source's negation passed -- "Do not vortex the
tube, regardless of the volume." ↔ "부피와 관계없이 튜브를 볼텍스합니다."
(the researcher would be told the opposite of the source) -- while ANKOM's
note "All nine trays must be used regardless of the number of bags being
processed." ↔ "…bag의 수와 관계없이…" was refused as ``negation_changed``
(lane AN report, section 6).
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

VORTEX = "3 Do not vortex the tube, regardless of the volume."
#: The Korean without the source's negation, one per form of the decision.
DROPPED = (
    "3단계: 부피와 관계없이 튜브를 볼텍스합니다.",
    "3단계: 부피와 관계 없이 튜브를 볼텍스합니다.",
    "3단계: 부피와 상관없이 튜브를 볼텍스합니다.",
    "3단계: 부피와 상관 없이 튜브를 볼텍스합니다.",
    "3단계: 부피와 무관하게 튜브를 볼텍스합니다.",
)
ANKOM_NOTE = "NOTE: All nine trays must be used regardless of the number of bags being processed."
#: As stored on 2026-10-06 (lane PX store, ANKOM s7/note_1 and s31/note_1).
ANKOM_NOTE_KO = "노트: 처리하는 bag의 수와 관계없이 9개의 tray를 모두 사용해야 합니다."
FORMS = (
    "관계없이", "관계 없이", "관계없는", "관계없고", "관계없다",
    "상관없이", "상관 없이", "상관없는", "상관없습니다",
    "무관하게", "무관한", "무관하며",
)


def step(source: str, label: str, terms: tuple[str, ...] = ()) -> TranslationUnit:
    return TranslationUnit(
        f"step-{label}/current_step", "step", source, terms,
        step_index=int(label) - 1, step_label=label)


def note(source: str, label: str) -> TranslationUnit:
    return TranslationUnit(
        f"s{label}/note_1", "note", source, step_index=int(label) - 1, step_label=label)


class DroppedNegationTests(unittest.TestCase):
    def test_a_negation_dropped_behind_regardless_is_refused(self) -> None:
        for korean in DROPPED:
            with self.subTest(korean=korean):
                self.assertEqual(
                    reader_translation_issue(VORTEX, korean, step_label="3"),
                    "negation_changed")
                self.assertEqual(check_translation(step(VORTEX, "3"), korean), "negation_changed")

    def test_the_negation_kept_beside_regardless_passes(self) -> None:
        for korean in ("3단계: 부피와 관계없이 튜브를 볼텍스하지 마세요.",
                       "3단계: 부피와 상관없이 튜브를 볼텍스하지 않습니다.",
                       "3단계: 부피와 무관하게 튜브를 볼텍스하지 마십시오."):
            with self.subTest(korean=korean):
                self.assertIsNone(reader_translation_issue(VORTEX, korean, step_label="3"))
                self.assertEqual(check_translation(step(VORTEX, "3"), korean), "passed")


class RegardlessAloneTests(unittest.TestCase):
    def test_the_ankom_note_passes(self) -> None:
        for label in ("7", "31"):
            with self.subTest(step=label):
                self.assertIsNone(reader_translation_issue(ANKOM_NOTE, ANKOM_NOTE_KO))
                self.assertEqual(check_translation(note(ANKOM_NOTE, label), ANKOM_NOTE_KO), "passed")

    def test_every_form_alone_passes(self) -> None:
        source = "3 Add the buffer regardless of the sample volume."
        for form in ("관계없이", "관계 없이", "상관없이", "상관 없이", "무관하게"):
            korean = f"3단계: 시료 부피와 {form} 완충액을 넣습니다."
            with self.subTest(form=form):
                self.assertEqual(check_translation(step(source, "3"), korean), "passed")

    def test_the_names_are_still_checked(self) -> None:
        # Past the negation the name check runs as before: the stored Korean
        # keeps bag and tray in English, a Korean that drops them is refused.
        unit = TranslationUnit(
            "s7/note_1", "note", ANKOM_NOTE, ("trays", "bags"), step_index=6, step_label="7")
        glossary = (GlossaryEntry("bag", "bag", True), GlossaryEntry("tray", "tray", True))
        self.assertEqual(check_translation(unit, ANKOM_NOTE_KO, glossary), "passed")
        self.assertEqual(
            check_translation(
                unit, "노트: 처리하는 백의 수와 관계없이 9개의 트레이를 모두 사용해야 합니다.", glossary),
            "term_missing")


class BothChecksTests(unittest.TestCase):
    def test_neither_check_reads_a_negation_in_these_words(self) -> None:
        source = "3 Add the buffer regardless of the volume."
        for form in FORMS:
            korean = f"3단계: 부피와 {form} 완충액을 넣습니다."
            with self.subTest(form=form):
                self.assertFalse(negation_or_avoidance("ko", korean), korean)
                self.assertIsNone(reader_translation_issue(source, korean, step_label="3"))

    def test_a_real_negation_in_the_same_sentence_still_counts(self) -> None:
        for korean in ("부피와 관계없이 섞지 마세요", "상관없이 넣지 않습니다",
                       "무관하게 열지 못합니다", "기포 없이 넣습니다"):
            with self.subTest(korean=korean):
                self.assertTrue(negation_or_avoidance("ko", korean), korean)
        # "without" is still a negation the Korean must keep (기포 없이).
        source = "3 Add the buffer without vortexing."
        self.assertIsNone(
            reader_translation_issue(source, "3단계: 볼텍스 없이 완충액을 넣습니다.", step_label="3"))
        self.assertEqual(
            reader_translation_issue(source, "3단계: 볼텍스하며 완충액을 넣습니다.", step_label="3"),
            "negation_changed")

    def test_the_wide_table_names_the_three_words(self) -> None:
        patterns = [re.compile(pattern) for pattern in NEGATION_VOCABULARY["ko_not_negation"]]
        for word in ("관계없이", "상관없이", "무관하게"):
            with self.subTest(word=word):
                self.assertTrue(any(pattern.search(word) for pattern in patterns), word)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
