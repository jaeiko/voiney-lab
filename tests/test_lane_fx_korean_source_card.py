"""A step written in Korean needs no translation notice (lane FX, decision 3).

Human decision of 2026-10-07: when a step's source is Korean (``is_korean``),
the card does not say "한국어 번역이 없어 원문으로 보여 드립니다." -- the
source is already the reader's language. Since lane AN such a step is never
sent for translation, so the notice was on every step of a Korean protocol
(glycolysis, 13 of 13 steps). The state says the current step's source
language; the page reads it to choose the card's line.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from tests.test_lane_px_translation import NO_NOTE, PENDING_NOTE
from tests.test_revision_translations import KOREAN_STEP_5, rich_fixture
from tests.test_screen_cleanup import run_page_script
from tests.test_screen_text_rules import PAGE_SETUP
from voiney_lab.curated_protocol import CuratedProtocolSession

#: Glycolysis (lane AN), step 2-9: Korean with a long English instrument name.
GLYCOLYSIS_9 = "9. Seahorse XF glycolysis stress test 를 실행합니다."
AUTO_NOTE = "한국어는 자동 번역입니다. 정확한 내용은 원문을 확인하세요."


def session_at(fixture, index: int) -> CuratedProtocolSession:
    curated = CuratedProtocolSession(fixture)
    curated.active = True
    curated.current_index = index
    return curated


class StateSaysTheSourceLanguageTests(unittest.TestCase):
    def test_a_korean_step_is_korean_and_an_english_one_english(self) -> None:
        fixture = rich_fixture()
        self.assertEqual(fixture.steps[4].instruction_source_text, KOREAN_STEP_5)
        korean = session_at(fixture, 4).state()
        self.assertEqual(korean["source_language"], "ko")
        self.assertIsNone(korean["primary_summary"])
        self.assertEqual(korean["display_summary"], KOREAN_STEP_5)
        self.assertEqual(session_at(fixture, 0).state()["source_language"], "en")

    def test_korean_with_a_long_english_name_is_korean(self) -> None:
        fixture = rich_fixture()
        section = fixture.draft.protocol.sections[0]
        steps = list(section.steps)
        steps[3] = replace(steps[3], instruction_source_text=GLYCOLYSIS_9)
        protocol = replace(fixture.draft.protocol, sections=(replace(section, steps=tuple(steps)),))
        fixture = replace(fixture, draft=replace(fixture.draft, protocol=protocol))
        self.assertEqual(session_at(fixture, 3).state()["source_language"], "ko")


class CardLineTests(unittest.TestCase):
    KOREAN = r"""
const koreanStep={...baseState,current_step_label:"5",current_step_id:"step-5",display_summary:"5 RT-PCR로 산물을 확인한다.",primary_summary:null,source_language:"ko"};
const note=node("procedure-translation-note");
"""

    def test_a_korean_step_has_no_line(self) -> None:
        result = run_page_script(PAGE_SETUP + self.KOREAN + r"""
await send(koreanStep,{safety_items:[],translation_source:"none",translation_pending:false});
assert(note.textContent===""&&note.hidden,"a line on a Korean step: "+note.textContent);
assert(node("procedure-primary").textContent==="5 RT-PCR로 산물을 확인한다.","the Korean source is not the body");
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_korean_step_has_no_line_while_the_rest_is_translated(self) -> None:
        # Nothing is coming for a Korean step, so "being prepared" is not said either.
        result = run_page_script(PAGE_SETUP + self.KOREAN + r"""
await send(koreanStep,{safety_items:[],translation_source:"none",translation_pending:true});
assert(note.textContent===""&&note.hidden,"a pending line on a Korean step: "+note.textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_english_step_keeps_its_lines(self) -> None:
        result = run_page_script(PAGE_SETUP + self.KOREAN + r"""
await send({...baseState,primary_summary:null},{safety_items:[],translation_source:"none",translation_pending:false});
assert(note.textContent===""" + repr(NO_NOTE) + r"""&&!note.hidden,"no-translation line missing: "+note.textContent);
await send({...baseState,primary_summary:null,revision:2},{safety_items:[],translation_source:"none",translation_pending:true});
assert(note.textContent===""" + repr(PENDING_NOTE) + r""","pending line missing: "+note.textContent);
await send({...koreanStep,revision:3},{safety_items:[],translation_source:"none",translation_pending:false});
assert(note.hidden,"the line stayed on the Korean step");
await send({...baseState,primary_summary:null,revision:4},{safety_items:[],translation_source:"none",translation_pending:false});
assert(note.textContent===""" + repr(NO_NOTE) + r""","the line did not come back on an English step: "+note.textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_automatic_translation_line_is_unchanged(self) -> None:
        # Once a machine translation was shown, the card head says so, as before.
        result = run_page_script(PAGE_SETUP + self.KOREAN + r"""
await send(koreanStep,{safety_items:[],translation_source:"machine",translation_pending:false});
assert(note.textContent===""" + repr(AUTO_NOTE) + r"""&&!note.hidden,"automatic-translation line changed: "+note.textContent);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
