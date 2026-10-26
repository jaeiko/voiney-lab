"""Plain lab Korean for translations and explanations (lane SAY, decisions 3 and 4, 2026-10-25).

The person's voice test of 2026-10-25: the purpose read "이것은 시설에서 사용하는
인겔 소화(in-gel digestion) 프로토콜이며, 귀하의 인겔 소화물을 준비하는 데
사용하는 것을 환영합니다…" -- word-for-word, "귀하", 합니다체.

Decision 3: the translation style (``protocol_translation.STYLE_GUIDE``) is
the Korean lab members say -- 해요체, short sentences, reagent and equipment
names as labs say them (English ones in English, no Korean in brackets), no
"귀하", "~하십시오" or translationese -- and numbers, units and names stay as
the source writes them, held by the same check as before. Decision 4: the
purpose, background and explanation answers speak the same way.

The model's own output under the new guide is compared with the stored
readings in the lane report (live calls); here the prompts and the server's
fixed sentences are checked, with no call.
"""

from __future__ import annotations

import inspect
import re
import unittest
from dataclasses import replace

from tests.lane_n_support import notes_fixture
from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab import external_references
from voiney_lab import protocol_translation as pt
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import PURPOSE_FACT_KEY, CuratedProtocolSession
from voiney_lab.runtime_routing import route_curated_runtime_turn

FORMAL = re.compile(r"(?:합니다|습니다|십시오|하십시오|귀하|당신)")


def _session(fixture, index: int = 0) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    route_curated_runtime_turn(session, "프로토콜 시작해줘", turn_id=1, language="ko",
                               configuration_id=1, generation=1)
    session._notebook_question = None
    session.current_index = index
    return session


def _say(session: CuratedProtocolSession, words: str):
    return route_curated_runtime_turn(session, words, turn_id=2, language="ko",
                                      configuration_id=1, generation=1).plan


class StyleGuideTests(unittest.TestCase):
    def test_the_guide_asks_for_lab_korean(self) -> None:
        guide = pt.STYLE_GUIDE
        self.assertIn("해요체", guide)
        for asked in ("short sentences", "one action per sentence", "Never use 합니다체",
                      "'~하십시오'", "'귀하'", "translationese", "no Korean added in brackets"):
            with self.subTest(asked=asked):
                self.assertIn(asked, guide)
        # Lane GE's rules stay: the numbers, units and names as written.
        for kept in ("exactly as written", "never convert, round, add or drop",
                     "'2 parts' stays '2 parts'", "Keep every negation and add none"):
            with self.subTest(kept=kept):
                self.assertIn(kept, guide)
        self.assertNotIn("formal polite Korean declarative style", guide)
        self.assertEqual(pt.PROMPT_VERSION, "revision_translation_v3")

    def test_every_translation_prompt_carries_the_guide(self) -> None:
        self.assertIn(pt.STYLE_GUIDE, pt.REVISION_TRANSLATION_PROMPT)
        self.assertIn(pt.STYLE_GUIDE, server_module.READER_TRANSLATION_PROMPT)
        self.assertIn(pt.STYLE_GUIDE, server_module.SPOT_TRANSLATION_PHRASE_PROMPT)
        # The glossary keeps a name as labs say it, not "Korean(English)".
        self.assertNotIn("Korean(English)", pt.GLOSSARY_PROMPT)
        self.assertIn("with no Korean added in brackets", pt.GLOSSARY_PROMPT)

    def test_the_check_is_the_same_for_the_new_style(self) -> None:
        source = ("2 Prepare two wash solutions: Solution A: 2 parts of 25mM ammonium "
                  "bicarbonate (AMBIC) (made up in HPLC water) mixed with 1 part acetonitrile")
        unit = pt.TranslationUnit("step-2/current_step", "step", source, (), step_index=1, step_label="2")
        # 해요체 with every value kept passes; the stored reading that read
        # "2 parts" as "2부피" (the voice test's step 2) does not.
        new = ("2단계: 세척 용액 두 가지를 준비해요. Solution A는 25mM ammonium bicarbonate "
               "(AMBIC)(HPLC water로 만든 것) 2 parts에 acetonitrile 1 part를 섞어요.")
        old = ("2단계: 두 가지 세척 용액을 준비합니다: Solution A: (HPLC water로 제조한) 25mM "
               "ammonium bicarbonate (AMBIC) 2부피와 acetonitrile 1부피를 혼합")
        self.assertEqual(pt.check_translation(unit, new), "passed")
        self.assertEqual(pt.check_translation(unit, old), "quantities_changed")

    def test_the_general_explanation_asks_for_haeyo(self) -> None:
        text = inspect.getsource(external_references)
        self.assertNotIn("해요체 or 합니다체", text)
        self.assertIn("never 합니다체", text)


class ExplanationWordsTests(unittest.TestCase):
    def test_the_purpose_answer_is_haeyo(self) -> None:
        plan = _say(_session(miniprep_fixture()), "이 실험 목적이 뭐야?")
        self.assertEqual(plan.speech_text, "원문에 적힌 실험 목적을 화면에 띄웠어요.")
        step_id, _, fact_id = PURPOSE_FACT_KEY.partition("/")
        korean = "오늘 쓰는 세척 연습용 프로토콜이에요. 단계마다 부피가 달라요."
        fixture = replace(miniprep_fixture(), machine_localizations={PURPOSE_FACT_KEY: korean})
        plan = _say(_session(fixture), "이 실험 목적이 뭐야?")
        self.assertEqual(plan.speech_text, "원문에 적힌 실험 목적이에요. 오늘 쓰는 세척 연습용 프로토콜이에요.")
        self.assertIn(korean, plan.display_text)

    def test_why_a_step_is_done_is_answered_in_haeyo(self) -> None:
        for words in ("이 단계 왜 해?", "이 단계 목적이 뭐야?"):
            with self.subTest(words=words):
                plan = _say(_session(notes_fixture(), 1), words)
                said = plan.speech_text or ""
                self.assertIsNone(FORMAL.search(said.split("원문 · ")[0]), said)


if __name__ == "__main__":
    unittest.main()
