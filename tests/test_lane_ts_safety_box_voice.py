"""The safety box and the spoken warning use only a checked Korean (lane TS, decision 2).

Human decision of 2026-10-08:

* the screen shows a source safety warning and its Korean side by side --
  the source is not folded away under the Korean;
* the voice reads a warning's Korean only when it passes every check
  (``protocol_translation.statement_issue``); a warning whose Korean does not
  is introduced with "이 안전 주의는 번역 확인이 안 돼서 원문을 읽어 드릴게요.
  화면의 원문을 꼭 확인해 주세요." and its source is read.

The fixture is the fictional miniprep with one warning ("Do not vortex the
lysate."), so nothing here needs a licensed PDF.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from tests.test_revision_translations import WARNING, rich_fixture
from tests.test_screen_text_rules import PAGE_SETUP
from tests.test_screen_cleanup import run_page_script
from voiney_lab.curated_protocol import (
    SAFETY_SOURCE_READ_LEAD,
    CuratedProtocolSession,
)
from voiney_lab.server import curated_safety_items, fixture_safety_notices

LEAD = "이 안전 주의는 번역 확인이 안 돼서 원문을 읽어 드릴게요. 화면의 원문을 꼭 확인해 주세요."
GOOD = "lysate를 볼텍스하지 마세요."
#: Two negations where the source has one: "do not leave it un-vortexed".
FLIPPED = "lysate를 볼텍스하지 않은 채로 두지 마세요."
DROPPED = "lysate를 볼텍스합니다."


def session_with(korean: str | None, *, reviewed: bool = False) -> CuratedProtocolSession:
    fixture = rich_fixture()
    key = f"{fixture.steps[0].step_id}/warning_1"
    if korean is not None:
        if reviewed:
            fixture = replace(fixture, localizations={key: korean})
        else:
            fixture = replace(fixture, machine_localizations={key: korean})
    session = CuratedProtocolSession(fixture)
    session.active = True
    session.current_index = 0
    return session


class SpokenWarningTests(unittest.TestCase):
    def test_the_lead_is_the_decisions_words(self) -> None:
        self.assertEqual(SAFETY_SOURCE_READ_LEAD, LEAD)

    def test_a_checked_korean_is_read(self) -> None:
        for reviewed in (False, True):
            with self.subTest(reviewed=reviewed):
                session = session_with(GOOD, reviewed=reviewed)
                for warning_only in (True, False):
                    _display, speech, _evidence, _limits = session._step_learning_presentation(
                        language="ko", warning_only=warning_only)
                    self.assertIn(GOOD, speech)
                    self.assertNotIn(LEAD, speech)
                    self.assertNotIn(WARNING, speech)

    def test_a_korean_that_fails_the_check_is_not_read(self) -> None:
        for korean in (FLIPPED, DROPPED, None):
            for reviewed in (False, True):
                with self.subTest(korean=korean, reviewed=reviewed):
                    session = session_with(korean, reviewed=reviewed)
                    for warning_only in (True, False):
                        _display, speech, _evidence, _limits = (
                            session._step_learning_presentation(
                                language="ko", warning_only=warning_only))
                        self.assertIn(f"{LEAD} {WARNING}", speech)
                        if korean is not None:
                            self.assertNotIn(korean, speech)

    def test_an_english_session_is_unchanged(self) -> None:
        session = session_with(FLIPPED)
        _display, speech, _evidence, _limits = session._step_learning_presentation(
            language="en", warning_only=True)
        self.assertEqual(speech, f"The source warning for this step is: {WARNING}")


class SafetyBoxTests(unittest.TestCase):
    def test_a_flipped_or_dropped_korean_leaves_the_source_alone(self) -> None:
        for korean, check in ((FLIPPED, "negation_count_changed"),
                              (DROPPED, "negation_changed")):
            with self.subTest(korean=korean):
                item = curated_safety_items(session_with(korean, reviewed=True))[0]
                self.assertIsNone(item["primary_text"])
                self.assertEqual(item["translation_check"], check)
                self.assertEqual(item["source_text"], WARNING)

    def test_a_checked_korean_travels_with_its_source(self) -> None:
        item = curated_safety_items(session_with(GOOD))[0]
        self.assertEqual((item["primary_text"], item["source_text"]), (GOOD, WARNING))
        self.assertEqual(item["translation_check"], "passed")

    def test_the_start_screen_notice_uses_the_same_check(self) -> None:
        notice = fixture_safety_notices(session_with(FLIPPED).fixture)[0]
        self.assertIsNone(notice["primary_text"])
        self.assertEqual(notice["translation_check"], "negation_count_changed")


class SideBySidePageTests(unittest.TestCase):
    def test_the_source_stands_open_beside_the_korean(self) -> None:
        result = run_page_script(PAGE_SETUP + r"""
await send(baseState,{safety_items:items,translation_source:"reviewed"});
const [passed,negation,sds,,,korean]=rows();
for(const [row,ko,en] of [[passed,"맨손으로 젤을 만지지 마세요.","Do not touch the gel with bare hands."],[sds,"아세토니트릴은 인화성입니다. 열에서 멀리 두세요.","Acetonitrile is flammable. Keep away from heat."]]){
 const texts=kids(row,"safety-text");
 assert(texts.length===2,"not side by side: "+texts.length);
 assert(texts[0].textContent===ko,"Korean missing: "+texts[0].textContent);
 assert(texts[1].textContent===en&&texts[1].className.includes("safety-source")&&texts[1].lang==="en","source not shown open: "+texts[1].textContent);
 assert(kids(row,"source-toggle").length===0,"source folded away");
}
// A refused line: the source alone, as before.
assert(kids(negation,"safety-text").length===1&&kids(negation,"safety-text")[0].className.includes("source-as-body"),"refused line changed");
// A Korean document: once.
assert(kids(korean,"safety-text").length===1,"Korean document doubled");
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
