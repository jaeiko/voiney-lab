"""Lane KNOW, decision 6: what an answer grounded in public material may say.

Public material is a general reference beside the source (human decision
2026-10-24): the sentence is a short paraphrase with only the cited piece's
numbers, a fact rather than an instruction, never "계속해도 돼요", never a
value against the source's, never a permission about a hazard, and the
"공개 자료 · 일반 참고 — <출처>" mark is the server's. The check is
``answer_checks.public_reference_violations``. Since lane KNOW2 (person's
decision 2026-10-26, 1) no piece is read aloud or paraphrased: all that is
said is ``external_references.PUBLIC_SPOKEN``, which the check passes.
"""

from __future__ import annotations

import unittest

from voiney_lab.answer_checks import (
    DISPLAY_LABELS,
    PUBLIC_REFERENCE_MAX_CHARS,
    PUBLIC_REFERENCE_QUOTE_CHARS,
    display_label_violations,
    public_reference_violations,
)
from voiney_lab.external_references import PUBLIC_SPOKEN

EVIDENCE = (
    "Flash Point: 42 °F (6 °C) (Open Cup) [HSDB]\n"
    "Physical Description: Colorless liquid with an aromatic odor. [NIOSH]\n"
    "Incompatibilities & Reactivities: Strong oxidizers [NIOSH]"
)


class PublicReferenceViolationTests(unittest.TestCase):
    def test_a_short_fact_with_the_piece_s_number_passes(self):
        for answer in (
            "아세토니트릴은 인화점이 6 °C 로 낮은 물질로 알려져 있어요.",
            "아세토니트릴은 인화점이 낮은 물질로 알려져 있어요.",
            "Acetonitrile is reported to have a low flash point, about 6 °C.",
            "강한 산화제와 함께 두면 반응할 수 있는 물질로 알려져 있어요.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(public_reference_violations(answer, evidence=EVIDENCE), ())

    def test_a_number_the_piece_does_not_state_is_refused(self):
        self.assertIn("answer_has_number_not_in_evidence",
                      public_reference_violations("인화점이 12 °C 라고 알려져 있어요.", evidence=EVIDENCE))
        self.assertIn("answer_has_number_not_in_evidence",
                      public_reference_violations("Store it below 25 °C.", evidence=EVIDENCE))

    def test_an_instruction_is_refused_a_fact_is_not(self):
        for answer in ("후드 안에서 다루세요.", "장갑을 착용해야 합니다.", "Wear gloves when handling it.",
                       "You should keep it away from oxidizers.", "환기가 필요합니다."):
            with self.subTest(answer=answer):
                self.assertIn("answer_gives_instruction", public_reference_violations(answer, evidence=EVIDENCE))
        self.assertNotIn("answer_gives_instruction", public_reference_violations(
            "일반적으로 환기가 필요한 용매로 알려져 있어요.", evidence=EVIDENCE))

    def test_permission_to_continue_is_refused(self):
        for answer in ("그 정도면 계속해도 돼요.", "그대로 진행하셔도 됩니다.", "다음 단계로 넘어가도 괜찮아요.",
                       "You can continue with the digestion.", "It is safe to proceed.", "You may go on."):
            with self.subTest(answer=answer):
                self.assertIn("answer_permits_continuing", public_reference_violations(answer, evidence=EVIDENCE))

    def test_a_value_against_the_source_s_is_refused(self):
        for answer in ("원문 대신 6 °C 로 두세요.", "6 °C 로 바꾸는 편이 좋아요.", "Use 6 °C instead of the protocol's value.",
                       "Set the bath to 6 °C.", "The protocol's temperature is wrong."):
            with self.subTest(answer=answer):
                found = public_reference_violations(answer, evidence=EVIDENCE)
                self.assertTrue({"answer_overrides_source_value", "answer_gives_instruction"} & set(found), found)
        self.assertIn("answer_overrides_source_value",
                      public_reference_violations("6 °C 로 바꾸면 돼요.", evidence=EVIDENCE))

    def test_a_hazard_permission_and_a_state_claim_are_refused(self):
        self.assertIn("answer_permits_hazard",
                      public_reference_violations("장갑 없이 만져도 돼요.", evidence=EVIDENCE))
        self.assertIn("answer_claims_state_change",
                      public_reference_violations("다음 단계로 넘어갔어요.", evidence=EVIDENCE))
        self.assertIn("answer_claims_state_change",
                      public_reference_violations("이 단계 완료했나요?", evidence=EVIDENCE))

    def test_length_a_reading_of_the_source_a_url_and_the_mark_are_refused(self):
        long = "아세토니트릴은 " + "인화점이 낮은 물질로 알려져 있어요. " * 8
        self.assertGreater(len(long), PUBLIC_REFERENCE_MAX_CHARS)
        self.assertIn("answer_too_long", public_reference_violations(long, evidence=EVIDENCE))
        reading = "Physical Description: Colorless liquid with an aromatic odor. Incompatibilities & Reactivities: Strong oxidizers"
        self.assertIn("answer_reads_source_at_length", public_reference_violations(reading, evidence=EVIDENCE))
        self.assertIn("answer_has_url",
                      public_reference_violations("자세한 것은 https://example.org 를 보세요", evidence=EVIDENCE))
        self.assertIn("answer_has_display_label",
                      public_reference_violations("공개 자료 · 일반 참고 — NIOSH: 인화점이 낮아요.", evidence=EVIDENCE))
        self.assertEqual(public_reference_violations("   ", evidence=EVIDENCE), ("empty_answer",))

    def test_the_public_mark_is_a_display_label_everywhere(self):
        self.assertIn("공개 자료 ·", DISPLAY_LABELS)
        self.assertEqual(display_label_violations("공개 자료 · 일반 참고 — X"), ("공개 자료 ·", "일반 참고 —"))


class SpokenSentenceTests(unittest.TestCase):
    def test_only_the_fixed_sentence_is_said_and_it_breaks_no_rule(self):
        # Lane KNOW2 (person's decision 2026-10-26, 1) replaced the model-free
        # reading of a piece's first sentence and the model's paraphrase
        # (public_reference_fallback, paraphrase_public_reference, removed):
        # "공개 자료에 관련 내용이 있어요" only, the content on the screen.
        self.assertEqual(PUBLIC_SPOKEN["ko"], "공개 자료에 관련 내용이 있어요.")
        for sentence in PUBLIC_SPOKEN.values():
            with self.subTest(sentence=sentence):
                self.assertEqual(public_reference_violations(sentence, evidence=EVIDENCE), ())
                self.assertEqual(display_label_violations(sentence), ())
        self.assertLess(len(PUBLIC_SPOKEN["en"]), PUBLIC_REFERENCE_QUOTE_CHARS)


if __name__ == "__main__":
    unittest.main()
