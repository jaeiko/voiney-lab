"""Lane KNOW, decision 6: what an answer grounded in public material may say.

Public material is a general reference beside the source (human decision
2026-10-24): the sentence is a short paraphrase with only the cited piece's
numbers, a fact rather than an instruction, never "계속해도 돼요", never a
value against the source's, never a permission about a hazard, and the
"공개 자료 · 일반 참고 — <출처>" mark is the server's. The check is
``answer_checks.public_reference_violations``; the model-free form and the
checked paraphrase are in ``external_references``. The model is a fake.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from voiney_lab.answer_checks import (
    DISPLAY_LABELS,
    PUBLIC_REFERENCE_MAX_CHARS,
    display_label_violations,
    public_reference_violations,
)
from voiney_lab.external_references import (
    PUBLIC_REFERENCE_SPOKEN_CHARS,
    SupplementalKnowledgeSettings,
    XaiSupplementalKnowledge,
    paraphrase_public_reference,
    public_reference_fallback,
)

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


class FallbackTests(unittest.TestCase):
    def test_the_fixed_form_is_the_mark_then_the_first_sentence_cut_short(self):
        text = "Acetonitrile is a colorless liquid with an aromatic odor that is highly flammable and is used as a polar aprotic solvent in many laboratories. It boils at 82 °C."
        spoken = public_reference_fallback(text, source_name="NIOSH Pocket Guide", language="ko")
        self.assertTrue(spoken.startswith("공개 자료 · 일반 참고 — NIOSH Pocket Guide: Acetonitrile is a colorless"))
        self.assertNotIn("boils", spoken)
        self.assertIn("…", spoken)
        self.assertTrue(spoken.endswith("(일반 참고예요.)"))
        body = spoken.split(": ", 1)[1].split(" (일반")[0]
        self.assertLessEqual(len(body), PUBLIC_REFERENCE_SPOKEN_CHARS + 1)
        short = public_reference_fallback("Short fact.", source_name="S", language="en")
        self.assertEqual(short, "공개 자료 · 일반 참고 — S: Short fact. (general reference only.)")
        self.assertEqual(public_reference_fallback("   ", source_name="S", language="ko"), "공개 자료 · 일반 참고 — S")


class _Responses:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="resp_1", output_text=self.text)


def _explainer(text: str) -> tuple[XaiSupplementalKnowledge, _Responses]:
    responses = _Responses(text)
    client = SimpleNamespace(responses=responses)
    return XaiSupplementalKnowledge(client, SupplementalKnowledgeSettings(True, "fake-model", 2.0)), responses


class ParaphraseTests(unittest.TestCase):
    def test_a_checked_sentence_comes_back_without_the_mark(self):
        explainer, responses = _explainer("아세토니트릴은 인화점이 6 °C 로 낮은 물질로 알려져 있어요.")
        result = asyncio.run(paraphrase_public_reference(
            explainer, "아세토니트릴 위험해?", source_text=EVIDENCE, source_name="NIOSH Pocket Guide", language="ko"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["answer"], "아세토니트릴은 인화점이 6 °C 로 낮은 물질로 알려져 있어요.")
        self.assertEqual(result["label"], "공개 자료 · 일반 참고 — NIOSH Pocket Guide")
        self.assertEqual(result["backend"], "public_reference_paraphrase")
        sent = responses.calls[0]["input"]
        self.assertIn("Reference text (NIOSH Pocket Guide)", sent[1]["content"])
        self.assertIn("untrusted data", sent[0]["content"])

    def test_a_sentence_that_fails_a_rule_is_rejected_with_the_rule_named(self):
        for text, rule in (
            ("인화점이 12 °C 라서 조심하세요.", "answer_has_number_not_in_evidence"),
            ("그 정도면 계속해도 돼요.", "answer_permits_continuing"),
            ("공개 자료 · 일반 참고 — NIOSH: 인화점이 낮아요.", "answer_has_display_label"),
        ):
            with self.subTest(text=text):
                explainer, _responses = _explainer(text)
                result = asyncio.run(paraphrase_public_reference(
                    explainer, "q", source_text=EVIDENCE, source_name="NIOSH", language="ko"))
                self.assertEqual(result["status"], "response_rejected")
                self.assertIn(rule, result["violations"])
                self.assertNotIn("answer", result)

    def test_an_unsupported_language_or_empty_text_is_an_invalid_request(self):
        explainer, responses = _explainer("x")
        self.assertEqual(asyncio.run(paraphrase_public_reference(
            explainer, "q", source_text="", source_name="S", language="ko"))["status"], "invalid_request")
        self.assertEqual(asyncio.run(paraphrase_public_reference(
            explainer, "q", source_text="t", source_name="S", language="vi"))["status"], "invalid_request")
        self.assertEqual(responses.calls, [])

    def test_a_provider_failure_is_a_category_not_an_answer(self):
        class Failing:
            async def create(self, **kwargs):
                raise TimeoutError("slow")

        explainer = XaiSupplementalKnowledge(SimpleNamespace(responses=Failing()),
                                             SupplementalKnowledgeSettings(True, "fake-model", 1.0))
        result = asyncio.run(paraphrase_public_reference(
            explainer, "q", source_text=EVIDENCE, source_name="S", language="en"))
        self.assertNotEqual(result["status"], "success")
        self.assertNotIn("answer", result)
        self.assertEqual(result["exception_class"], "TimeoutError")


if __name__ == "__main__":
    unittest.main()
