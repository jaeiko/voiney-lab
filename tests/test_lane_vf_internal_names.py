"""Lane VF, decision 9 (2026-10-10): no internal identifier reaches the experimenter.

In the voice test of 2026-10-10 an answer read "현재 3단계에서 800 rpm는
agitation_speed에 연결된 값입니다." -- a parameter's role identifier pasted into
a sentence. The sentence now names the role in words, and, so that no other
path can do the same, the server's output stage puts every English
snake_case identifier in a shown or spoken sentence into the experimenter's
words or takes it out. These tests fail if one gets through: on the
synthetic protocols, on the in-gel PDF, and at the output stage itself.
"""

from __future__ import annotations

import inspect
import re
import unittest
from dataclasses import replace

from tests.lane_cb_support import Turns, headspace_fixture
from tests.protocol_vocabulary_support import SOURCE_PDF, build_fixture, in_gel_fixture
from tests.test_lane_vt_timer_end import wash
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import hide_internal_names, plan_without_internal_names

SNAKE = re.compile(r"(?<![A-Za-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![A-Za-z0-9_])")

#: A protocol whose steps carry every parameter role the frame knows.
STEPS = (
    "1 Cut the stained protein band out of the gel and place it in a 1.5 mL tube.",
    "2 Add 500 µL of 25 mM ammonium bicarbonate and shake at 800 rpm for 15 min at 37°C.",
    "3 Remove and discard the supernatant.",
)
QUESTIONS = (
    "rpm 얼마야?", "회전 속도 얼마야?", "교반 속도가 뭐야?", "800 rpm은 뭐야?", "온도 몇 도야?", "농도 얼마야?",
    "부피 얼마야?", "시간 얼마나 해?", "왜 800 rpm이야?", "이 단계 설명해 줘", "지금 몇 단계야?", "다음 단계 뭐야?",
    "그거 뭐야?", "용액 얼마나 넣어?", "타이머 시작했어?", "몇 분 남았어?", "그림 보여줘", "써모믹서가 뭐야?",
    "이 단계 왜 해?", "완료 기준이 뭐야?", "뭐라고 써 있어?", "이전 단계 뭐였어?", "남은 단계 요약해줘",
    "프로토콜 이름이 뭐야?", "너 뭐 할 수 있어?", "기록 보여줘", "일시정지", "다시 시작", "실험 종료할까?",
)


def _sentences(plan) -> list[str]:
    return [value for value in (plan.display_text, plan.speech_text, plan.primary_text) if isinstance(value, str)]


class ScrubTests(unittest.TestCase):

    def test_a_known_identifier_becomes_its_words(self) -> None:
        self.assertEqual(
            hide_internal_names("현재 3단계에서 800 rpm는 agitation_speed에 연결된 값입니다."),
            "현재 3단계에서 800 rpm는 부드러운 교반 속도에 연결된 값입니다.")
        self.assertEqual(hide_internal_names("용액은 solution_a를 넣어요"), "용액은 용액 A를 넣어요")
        self.assertEqual(hide_internal_names("In step 3, 800 rpm is bound to agitation_speed.", "en"),
                         "In step 3, 800 rpm is bound to gentle-agitation speed.")

    def test_an_unknown_identifier_is_taken_out_with_its_particle(self) -> None:
        self.assertEqual(hide_internal_names("step_timer_status 가 돌고 있어요"), "돌고 있어요")
        self.assertEqual(hide_internal_names("보고서 event_name_ko 열을 보세요."), "보고서 열을 보세요.")
        self.assertEqual(hide_internal_names("값은 some_role에 연결돼요."), "값은 연결돼요.")

    def test_the_protocols_own_text_values_and_links_are_left_alone(self) -> None:
        for text in (
            "값 500 µL 그대로예요", "PDF에서 확인할 수 없어요.", "원문 ‘15min’에 따라 15분 타이머를 시작했습니다.",
            "SDS-PAGE 젤 밴드", "Solution A", "https://example.com/a_b?x=1", "Thermomixer 800 rpm, 37 °C",
            "3단계 타이머 13분 57초 남았어요.", "CamelCase, kebab-case, UPPER_CASE 는 그대로",
        ):
            with self.subTest(text=text):
                self.assertEqual(hide_internal_names(text), text)

    def test_a_plan_is_cleaned_in_every_shown_or_spoken_field_only(self) -> None:
        turns = Turns()
        turns.open(0, fixture=wash())
        plan = turns.say("지금 몇 단계야?")
        dirty = replace(plan, display_text="agitation_speed 값", speech_text="solution_b 넣어요",
                        primary_text="step_timer_status 상태", intent_kind="step_timer_status")
        clean = plan_without_internal_names(dirty)
        self.assertEqual((clean.display_text, clean.speech_text, clean.primary_text), ("부드러운 교반 속도 값", "용액 B 넣어요", "상태"))
        self.assertEqual(clean.intent_kind, "step_timer_status")
        self.assertIs(plan_without_internal_names(plan), plan)


class _NoLeak(Turns, unittest.TestCase):

    def assert_clean(self, fixture, step_index: int, *, started: bool = True) -> None:
        for said in QUESTIONS:
            with self.subTest(said=said):
                self.open(step_index if started else None, fixture=fixture)
                plan = self.say(said)
                for text in _sentences(plan):
                    self.assertIsNone(SNAKE.search(text), f"{said!r} -> {text!r}")


class SyntheticProtocolTests(_NoLeak):

    def test_the_role_sentence_names_the_role_in_words(self) -> None:
        fixture = build_fixture(protocol_id="lane-vf-roles", title="Roles", steps=STEPS)
        self.open(1, fixture=fixture)
        plan = self.say("rpm 얼마야?")
        texts = " ".join(_sentences(plan))
        self.assertIn("800 rpm", texts)
        self.assertNotIn("agitation_speed", texts)
        self.assertIsNone(SNAKE.search(texts), texts)

    def test_no_identifier_in_any_answer_on_the_synthetic_protocols(self) -> None:
        self.assert_clean(build_fixture(protocol_id="lane-vf-roles", title="Roles", steps=STEPS), 1)
        self.assert_clean(wash(), 1)
        self.assert_clean(headspace_fixture(), 11)


@unittest.skipUnless(SOURCE_PDF.exists(), "the licensed in-gel PDF is not present")
class InGelTests(_NoLeak):

    def test_step_3_as_in_the_voice_test(self) -> None:
        fixture = in_gel_fixture()
        self.open(2, fixture=fixture)
        plan = self.say("rpm 얼마야?")
        texts = " ".join(_sentences(plan))
        self.assertNotIn("agitation_speed", texts)
        self.assertIsNone(SNAKE.search(texts), texts)
        self.assert_clean(fixture, 2)


class OutputStageTests(unittest.TestCase):

    def test_the_server_cleans_the_plan_right_before_it_is_shown_and_said(self) -> None:
        source = inspect.getsource(server_module.run_turn)
        cleaned = source.index("plan=plan_without_internal_names(plan,turn_language)")
        self.assertLess(cleaned, source.index("pcm=await asyncio.to_thread(synthesize,said(speech_text),turn_language)"))
        self.assertLess(cleaned, source.index('"reply.delta",turn_id=turn_id,segment_index=0,text=display_text'))


if __name__ == "__main__":
    unittest.main()
