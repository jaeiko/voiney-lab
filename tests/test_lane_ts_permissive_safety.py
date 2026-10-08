"""A permissive safety sentence stands only on a source that permits it (lane TS, decision 3).

Human decision of 2026-10-08. Lane RT's filter
(``answer_checks.ungrounded_safety_instructions``) took out a safety
*instruction* with no source; a sentence that *permits* something risky --
"장갑 없이 만져도 돼요", "UV를 맨눈으로 봐도 돼요", "흄후드 안 써도 괜찮아요" --
gave no instruction and passed. Now a permissive sentence ("~없이 해도 돼요",
"~안 해도 돼요", "~해도 괜찮아요", "맨손으로", "맨눈으로", "필요 없어요", "독성이
없어요") about a hazard topic (protective equipment, ventilation, exposure,
UV and radiation, biological hazards, sharps, handling a chemical) is taken
out the same way, in the same place, unless the protocol's text or an
approved safety document itself permits it; "이 상황의 안전 안내는 원문에
없어요." is said instead. Where the source permits it, it stays (no false
removal). An outside-PDF explanation may hold no such sentence at all.
"""

from __future__ import annotations

import unittest

from voiney_lab.answer_checks import (
    outside_pdf_violations,
    permissive_hazard_topics,
    ungrounded_safety_instructions,
)
from voiney_lab.llm_router import RouterAnswer, without_ungrounded_safety

NOTE = "이 상황의 안전 안내는 원문에 없어요."
GROUNDING = "Cut the band into 1 mm cubes with a clean scalpel. Wear gloves."

#: (sentence, the topics it is about)
PERMISSIVE = (
    ("이 시약은 독성이 없어서 장갑 없이 만져도 돼요.", {"protective_equipment", "chemical_handling"}),
    ("UV를 맨눈으로 봐도 돼요.", {"uv_radiation", "protective_equipment"}),
    ("흄후드 안 써도 괜찮아요.", {"ventilation"}),
    ("이 단계는 맨손으로 해도 됩니다.", {"protective_equipment"}),
    ("보안경은 필요 없어요.", {"protective_equipment"}),
    ("phenol은 손으로 만져도 괜찮아요.", {"chemical_handling"}),
    ("트랜스일루미네이터는 보호 없이 봐도 무방해요.", {"uv_radiation"}),
    ("이 균은 병원성이 없어서 생물안전작업대 없이 다뤄도 돼요.", {"biohazard"}),
    ("메스는 뚜껑 없이 둬도 괜찮아요.", {"sharps"}),
    ("증기는 조금 들이마셔도 괜찮아요.", {"exposure"}),
    ("You can handle it without gloves.", {"protective_equipment"}),
    ("It's fine to look at the UV light with the naked eye.", {"uv_radiation", "protective_equipment"}),
    ("A fume hood is not necessary.", {"ventilation"}),
)
#: Permissive or plain sentences about no hazard topic, and sentences about
#: a topic that permit nothing: left alone.
LEFT_ALONE = (
    "이 단계는 실온에서 해도 돼요.",
    "15분 동안 둬도 됩니다.",
    "버퍼는 미리 만들어 둬도 괜찮아요.",
    "원문에는 장갑에 대한 언급이 없어요.",
    "젤 조각은 1 mm 크기로 자릅니다.",
    "온도는 괜찮아요.",
    "The tube can stay at room temperature.",
)


class TopicTests(unittest.TestCase):
    def test_a_permissive_sentence_names_its_hazard_topics(self) -> None:
        for sentence, topics in PERMISSIVE:
            with self.subTest(sentence=sentence):
                self.assertTrue(topics <= set(permissive_hazard_topics(sentence)),
                                permissive_hazard_topics(sentence))

    def test_other_sentences_name_none(self) -> None:
        for sentence in LEFT_ALONE:
            with self.subTest(sentence=sentence):
                self.assertEqual(permissive_hazard_topics(sentence), ())


class UngroundedTests(unittest.TestCase):
    def test_a_permissive_hazard_sentence_with_no_source_is_taken_out(self) -> None:
        for sentence, _topics in PERMISSIVE:
            with self.subTest(sentence=sentence):
                self.assertEqual(
                    ungrounded_safety_instructions(f"젤을 자릅니다. {sentence}", GROUNDING),
                    (sentence,))

    def test_an_instruction_about_the_topic_is_no_permission(self) -> None:
        # The source tells the researcher to wear gloves: that does not
        # ground "you may do without them".
        self.assertEqual(
            ungrounded_safety_instructions("장갑 없이 만져도 돼요.", "Always wear gloves."),
            ("장갑 없이 만져도 돼요.",))

    def test_a_source_that_permits_it_keeps_it(self) -> None:
        for sentence, grounding in (
            ("이 단계는 장갑 없이 해도 돼요.", "Gloves are not required for this step."),
            ("흄후드 안 써도 괜찮아요.", "This step does not need a fume hood; work on the open bench."),
            ("이 버퍼는 독성이 없어서 손으로 만져도 돼요.",
             "The buffer is non-hazardous and can be handled without gloves."),
            ("UV를 맨눈으로 봐도 돼요.",
             "The blue-light transilluminator is safe to view without UV protection."),
            ("보안경은 필요 없어요.", "보안경은 필요하지 않습니다."),
            ("A fume hood is not necessary.", "A fume hood is not necessary for this buffer."),
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(ungrounded_safety_instructions(sentence, grounding), ())

    def test_sentences_about_no_hazard_are_left_alone(self) -> None:
        for sentence in LEFT_ALONE:
            with self.subTest(sentence=sentence):
                self.assertEqual(ungrounded_safety_instructions(sentence, GROUNDING), ())

    def test_lane_rt_instructions_are_still_read_as_before(self) -> None:
        self.assertEqual(
            ungrounded_safety_instructions("SDS를 확인하세요.", GROUNDING), ("SDS를 확인하세요.",))
        self.assertEqual(ungrounded_safety_instructions("장갑을 끼세요.", GROUNDING), ())


class RouterAnswerTests(unittest.TestCase):
    def test_the_answer_says_the_source_has_none_instead(self) -> None:
        answer = RouterAnswer(
            spoken="젤을 1 mm 크기로 자릅니다. 장갑 없이 만져도 돼요.",
            display="젤을 1 mm 크기로 자릅니다. 장갑 없이 만져도 돼요.",
            source_kind="pdf", evidence_ids=("step-3/current_step",), outside_pdf_term=None,
        )
        kept, removed = without_ungrounded_safety(answer, GROUNDING, language="ko")
        self.assertEqual(removed, ("장갑 없이 만져도 돼요.",))
        self.assertEqual(kept.spoken, f"젤을 1 mm 크기로 자릅니다. {NOTE}")
        self.assertNotIn("장갑 없이", kept.display)

    def test_a_grounded_permission_is_kept_word_for_word(self) -> None:
        answer = RouterAnswer(
            spoken="이 단계는 장갑 없이 해도 돼요.", display="",
            source_kind="pdf", evidence_ids=("step-3/note_1",), outside_pdf_term=None,
        )
        kept, removed = without_ungrounded_safety(
            answer, "Gloves are not required for this step.", language="ko")
        self.assertEqual((kept, removed), (answer, ()))


class OutsidePdfTests(unittest.TestCase):
    def test_an_outside_pdf_explanation_may_hold_no_permission(self) -> None:
        found = outside_pdf_violations(
            "트랜스일루미네이터는 맨눈으로 봐도 되는 장비예요.",
            question="트랜스일루미네이터가 뭐야?", term="transilluminator",
            protocol_terms=("transilluminator",),
        )
        self.assertIn("answer_states_safety", found)


if __name__ == "__main__":
    unittest.main()
