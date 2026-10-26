"""Lane KO2, decision 2 (2026-10-26): the translation guide's five refinements.

The person read 30 sentences translated with lane SAY's 해요체 guide
(``~/reports/say/results/style_live/comparison.md``) and adopted it, with five
refinements written into ``protocol_translation.STYLE_GUIDE`` (version 4):

(a) no Korean ending on an English word ("resuspension해요", "pooling해요"):
    the Korean verb labs use ("재부유해요", "모아요") or the word in Hangul;
(b) one Korean word for one action in a protocol ("세척해요", never mixed with
    "워싱해요");
(c) the source's authors ("we", "our") in the third person ("이 논문의
    저자들은"), never "우리"/"저희" -- heard as Voiney Lab speaking;
(d) a settings line repeating the step's values ("800 rpm, 37°C, 00:15:00")
    kept once, no value said more often than the source says it;
(e) no awkward or imprecise words ("흡입 제거" -> "피펫으로 빨아들여",
    "희끄무레한" -> "하얗게"); names as labs say them (AMBIC, acetonitrile).

The check of numbers and units is unchanged. One check is added for (c): a
reading that speaks as "we" is not shown or said (``first_person``). The
examples in the guide are general lab words, not one protocol's (lane rules
10). No model is called here.
"""

from __future__ import annotations

import unittest

from voiney_lab import protocol_translation as pt
from voiney_lab import server as server_module


class GuideTests(unittest.TestCase):
    def test_the_guide_holds_the_five_refinements(self) -> None:
        guide = pt.STYLE_GUIDE
        for asked in (
            # (a)
            "Never put a Korean verb ending on an English word",
            "'resuspension해요'", "'재부유해요'", "write the word in Hangul",
            # (b)
            "one Korean word for one action", "'세척해요'", "'워싱해요'",
            # (c)
            "third person", "'이 논문의 저자들은'", "never '우리' or '저희'",
            # (d)
            "a line of settings", "keep that line once", "more often than the source",
            # (e)
            "'피펫으로 빨아들여요'", "'흡입 제거'", "'하얗게'", "'희끄무레한'",
        ):
            with self.subTest(asked=asked):
                self.assertIn(asked, guide)
        # Lane GE's and SAY's rules stay.
        for kept in ("해요체", "exactly as written", "'2 parts' stays '2 parts'",
                     "Keep every negation and add none", "no Korean added in brackets"):
            with self.subTest(kept=kept):
                self.assertIn(kept, guide)
        self.assertEqual(pt.STYLE_VERSION, 4)

    def test_the_guide_names_no_test_protocol(self) -> None:
        # Lane rules 10: general lab words, not the in-gel sample's.
        for word in ("in-gel", "AMBIC", "Evotips", "800 rpm", "gel plug", "CUT&Tag"):
            with self.subTest(word=word):
                self.assertNotIn(word, pt.STYLE_GUIDE)

    def test_every_translation_prompt_carries_the_guide(self) -> None:
        for prompt in (
            pt.REVISION_TRANSLATION_PROMPT,
            server_module.READER_TRANSLATION_PROMPT,
            server_module.SPOT_TRANSLATION_PHRASE_PROMPT,
        ):
            self.assertIn(pt.STYLE_GUIDE, prompt)


class FirstPersonTests(unittest.TestCase):
    SOURCE = "We previously introduced CUT&Tag, a profiling method."

    def test_a_reading_that_speaks_as_we_is_refused(self) -> None:
        for korean in (
            "저희는 이전에 CUT&Tag를 소개했어요. 프로파일링 방법이에요.",
            "우리는 이전에 CUT&Tag를 소개했어요. 프로파일링 방법이에요.",
            "우리 연구실은 이전에 CUT&Tag를 소개했어요. 프로파일링 방법이에요.",
            "이전에 저희가 CUT&Tag를 소개했어요. 프로파일링 방법이에요.",
        ):
            with self.subTest(korean=korean):
                self.assertEqual(pt.statement_issue(self.SOURCE, korean), "first_person")

    def test_the_authors_in_the_third_person_pass(self) -> None:
        for korean in (
            "이 논문의 저자들은 이전에 CUT&Tag를 소개했어요. 프로파일링 방법이에요.",
            "이 프로토콜을 쓴 사람들은 이전에 CUT&Tag를 소개했어요. 프로파일링 방법이에요.",
        ):
            with self.subTest(korean=korean):
                self.assertIsNone(pt.statement_issue(self.SOURCE, korean))
        # A word that only begins like the pronoun is not one.
        self.assertIsNone(pt.statement_issue(
            "Steep the leaves for 5 min.", "잎을 5 min 동안 우려내요."))

    def test_a_stored_machine_row_that_speaks_as_we_is_not_shown(self) -> None:
        unit = pt.TranslationUnit(pt.PURPOSE_FACT_KEY, "purpose", self.SOURCE, ("CUT&Tag",))
        self.assertEqual(
            pt.check_translation(unit, "우리는 이전에 CUT&Tag를 소개했어요. 프로파일링 방법이에요."),
            "first_person")


class NumbersCheckUnchangedTests(unittest.TestCase):
    def test_a_value_said_more_often_than_the_source_is_still_refused(self) -> None:
        # The settings line's 800 rpm moved into the sentence and kept at the end:
        # said twice where the source says it once (lane SAY's comparison, row 5).
        source = ("3 Wash the band with 500 µL of solution A. Incubate the gel plug for 15min "
                  "at 37°C with gently agitation 800 rpm, 37°C, 00:15:00")
        twice = ("3단계: 500 µL의 solution A로 밴드를 세척해요. 37°C에서 800 rpm으로 흔들면서 "
                 "15min 동안 gel plug를 둬요. 800 rpm, 37°C, 00:15:00.")
        once = ("3단계: 500 µL의 solution A로 밴드를 세척해요. 37°C에서 부드럽게 흔들면서 "
                "15min 동안 gel plug를 둬요. 800 rpm, 37°C, 00:15:00.")
        self.assertEqual(pt.statement_issue(source, twice, step_label="3"), "quantities_changed")
        self.assertIsNone(pt.statement_issue(source, once, step_label="3"))


if __name__ == "__main__":
    unittest.main()
