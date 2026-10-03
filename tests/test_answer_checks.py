"""Server checks on a model-written answer (lane R, design §5-2).

The number and mutation-claim checks moved out of ``multi_brain`` unchanged;
the old definitions are copied here so the move is pinned. The outside-PDF,
display-label and server-value checks are new and not wired to a route yet.
"""

from __future__ import annotations

import re
import unittest

from voiney_lab import answer_checks, multi_brain
from voiney_lab.answer_checks import (
    MUTATION_CLAIM,
    NUMERIC,
    ServerValues,
    claims_mutation,
    claims_state_change,
    display_label_violations,
    introduces_numbers,
    numbers_in,
    outside_pdf_violations,
    server_value_violations,
)

#: ``multi_brain`` before the move, verbatim.
_OLD_NUMERIC = re.compile(
    r"(?:\d{2}:\d{2}:\d{2}|\d+(?:\.\d+)?\s*"
    r"(?:mg/mL|ng/uL|mm3|mm³|µL|uL|mL|ml|mM|°C|rpm|min|v/v|C|h|%))",
    re.I,
)
_OLD_FORBIDDEN = re.compile(
    r"(?:I|제가|내가).{0,40}(?:saved|recorded|persisted|저장|기록)"
    r"|(?:저장|기록)(?:했|됐|되었|했습니다)"
    r"|(?:step|단계).*(?:completed|완료 처리)",
    re.I,
)


def _old_numbers(value: str) -> frozenset[str]:
    return frozenset(
        item.casefold().replace(" ", "").replace("μ", "µ")
        for item in _OLD_NUMERIC.findall(value)
    )


SAMPLES = (
    "HPLC water is used to prepare 25 mM AMBIC.",
    "트립신 용액 25 µL 를 넣고 10 min 둡니다.",
    "25 μL, 25µL, 25 uL, 0.5 mL, 56 °C, 37C, 3 h, 50% v/v, 00:15:00",
    "13,000 rpm 으로 1 min 돌립니다. 2 mg/mL, 10 ng/uL, 1 mm3",
    "숫자가 없는 문장입니다.",
    "제가 완료를 기록했습니다.",
    "I have recorded the observation.",
    "기록됐어요",
    "Step 4 completed.",
    "4단계 완료 처리했습니다.",
    "기록은 서버가 저장이 성공한 뒤에만 확인합니다.",
)


class MovedChecksTests(unittest.TestCase):
    def test_numbers_are_read_exactly_as_multi_brain_read_them(self) -> None:
        self.assertEqual(NUMERIC.pattern, _OLD_NUMERIC.pattern)
        self.assertEqual(NUMERIC.flags, _OLD_NUMERIC.flags)
        for sample in SAMPLES:
            with self.subTest(sample=sample):
                self.assertEqual(numbers_in(sample), _old_numbers(sample))

    def test_mutation_claim_is_the_answer_brain_pattern(self) -> None:
        self.assertEqual(MUTATION_CLAIM.pattern, _OLD_FORBIDDEN.pattern)
        self.assertEqual(MUTATION_CLAIM.flags, _OLD_FORBIDDEN.flags)
        for sample in SAMPLES:
            with self.subTest(sample=sample):
                self.assertEqual(
                    claims_mutation(sample), _OLD_FORBIDDEN.search(sample) is not None
                )

    def test_multi_brain_uses_these_checks(self) -> None:
        self.assertIs(multi_brain.numbers_in, answer_checks.numbers_in)
        self.assertIs(multi_brain.claims_mutation, answer_checks.claims_mutation)
        self.assertFalse(hasattr(multi_brain, "_NUMERIC"))
        self.assertFalse(hasattr(multi_brain, "_numbers"))

    def test_an_answer_may_only_say_the_numbers_its_evidence_says(self) -> None:
        evidence = "Solution A contains 25 mM AMBIC. Incubate 15 min at 56 °C."
        self.assertFalse(introduces_numbers("AMBIC은 25 mM 입니다.", evidence))
        self.assertFalse(introduces_numbers("56 °C에서 15 min.", evidence))
        self.assertTrue(introduces_numbers("AMBIC은 50 mM 입니다.", evidence))
        self.assertTrue(introduces_numbers("37 °C에서 둡니다.", evidence))


class StateChangeClaimTests(unittest.TestCase):
    def test_saying_a_change_happened_is_caught(self) -> None:
        for said in (
            "5단계로 넘어갔습니다.", "다음 단계로 넘어가겠습니다.", "타이머를 시작했어요.",
            "15분 타이머를 시작했습니다.", "실험을 종료했습니다.", "일시정지했습니다.",
            "재개했습니다.", "4단계를 완료 처리했습니다.", "제가 관찰을 기록했습니다.",
            "I've started the timer.", "I advanced to step 5.",
        ):
            with self.subTest(said=said):
                self.assertTrue(claims_state_change(said))

    def test_talking_about_a_change_is_not_a_claim(self) -> None:
        for said in (
            "넘어가려면 '네'라고 말씀해 주세요.", "원문 타이머는 15분입니다.",
            "타이머는 화면에서 볼 수 있어요.", "다음 단계는 트립신을 넣는 단계입니다.",
            "실험을 끝내려면 '실험 종료'라고 말씀해 주세요.",
        ):
            with self.subTest(said=said):
                self.assertFalse(claims_state_change(said))

    def test_the_narrow_answer_brain_check_is_not_widened(self) -> None:
        # multi_brain keeps rejecting exactly what it rejected before.
        self.assertFalse(claims_mutation("5단계로 넘어갔습니다."))
        self.assertFalse(claims_mutation("타이머를 시작했어요."))


class DisplayLabelTests(unittest.TestCase):
    def test_screen_block_labels_in_an_answer_are_found(self) -> None:
        found = display_label_violations(
            "답변 · 한국어 참고 번역\n2단계: 용액을 준비합니다.\n\n"
            "원문 · English\n2 Prepare two wash solutions.\n\n출처\ncurrent_step · 원문 p.3"
        )
        for label in ("답변 · 한국어", "한국어 참고 번역", "원문 · English", "원문 p.", "출처"):
            self.assertIn(label, found)
        self.assertEqual(display_label_violations("직접 답변\nHPLC water는 2단계에 나옵니다."), ("직접 답변",))
        self.assertIn("근거 경계", display_label_violations("본문\n\n근거 경계\n활성 프로토콜"))

    def test_the_outside_pdf_mark_is_the_servers_to_add(self) -> None:
        self.assertTrue(display_label_violations("PDF에는 따로 설명이 없어요. 장비예요."))
        self.assertTrue(display_label_violations("PDF 밖 설명이니 유의: 장비예요."))

    def test_plain_answers_carry_no_label(self) -> None:
        for answer in (
            "트립신은 단백질을 자르는 효소예요.",
            "원문에는 25 µL 라고 적혀 있습니다.",
            "출처를 보시려면 화면을 확인하세요.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(display_label_violations(answer), ())


class OutsidePdfTests(unittest.TestCase):
    TERMS = ("thermomixer", "써모믹서", "iodoacetamide", "요오드아세트아마이드", "SpeedVac", "트립신")

    def check(self, answer: str, question: str, term: str | None = "써모믹서") -> tuple[str, ...]:
        return outside_pdf_violations(
            answer, question=question, term=term, protocol_terms=self.TERMS
        )

    def test_what_a_word_means_may_be_explained(self) -> None:
        self.assertEqual(self.check("시료를 데우면서 흔들어 주는 장비예요.", "써모믹서가 뭐야?"), ())
        self.assertEqual(
            self.check("시료를 진공에서 말리는 장비예요.", "speedvac 이 뭐야", "speedvac"), ()
        )

    def test_why_a_reagent_is_used_may_be_explained_d4(self) -> None:
        self.assertEqual(
            self.check("단백질을 작은 조각으로 자르는 효소라서 넣어요.", "트립신은 왜 넣어?", "트립신"), ()
        )
        self.assertEqual(
            self.check("시스테인을 막아 다시 붙지 않게 하는 역할이에요.",
                       "요오드아세트아마이드 역할이 뭐야?", "요오드아세트아마이드"), ()
        )

    def test_the_term_must_belong_to_the_active_protocol(self) -> None:
        self.assertIn("term_not_in_active_protocol", self.check("장비예요.", "원심분리기가 뭐야?", "원심분리기"))
        self.assertIn("term_not_in_active_protocol", self.check("장비예요.", "이게 뭐야?", None))

    def test_quantity_method_safety_and_completion_questions_are_not_outside_pdf(self) -> None:
        for question, code in (
            ("써모믹서 온도는 몇 도야?", "question_asks_quantity"),
            ("써모믹서는 어떻게 써?", "question_asks_method"),
            ("써모믹서는 왜 위험해?", "question_asks_safety"),
            ("써모믹서는 왜 끝나고 꺼?", "question_asks_completion"),
        ):
            with self.subTest(question=question):
                self.assertIn(code, self.check("장비예요.", question))

    def test_a_question_that_is_neither_meaning_nor_purpose_is_refused(self) -> None:
        self.assertIn("question_not_meaning_or_purpose", self.check("장비예요.", "써모믹서 써도 돼"))

    def test_the_answer_has_no_number_and_fits_in_120_characters(self) -> None:
        self.assertIn("answer_has_number", self.check("37 °C로 데우는 장비예요.", "써모믹서가 뭐야?"))
        self.assertIn("answer_has_number", self.check("1분에 천 번 흔들어요 2번.", "써모믹서가 뭐야?"))
        self.assertIn("answer_too_long", self.check("가" * 121, "써모믹서가 뭐야?"))
        self.assertEqual(self.check("가" * 120, "써모믹서가 뭐야?"), ())
        self.assertIn("empty_answer", self.check("  ", "써모믹서가 뭐야?"))

    def test_the_answer_says_nothing_about_method_safety_or_completion(self) -> None:
        for answer, code in (
            ("시료를 넣으세요.", "answer_states_method"),
            ("뚜껑을 닫아야 합니다.", "answer_states_method"),
            ("독성이 있는 시약이에요.", "answer_states_safety"),
            ("색이 빠질 때까지 돌려요. 될 때까지 반복해요.", "answer_states_completion"),
        ):
            with self.subTest(answer=answer):
                self.assertIn(code, self.check(answer, "써모믹서가 뭐야?"))
        self.assertIn("answer_has_display_label", self.check("직접 답변\n장비예요.", "써모믹서가 뭐야?"))


class ServerValueTests(unittest.TestCase):
    VALUES = ServerValues(
        title="In-gel digestion",
        revision_id="rev-3",
        hashes=("ab12cd34ef56" * 5 + "abcd", "0f1e2d3c4b5a" * 5 + "6978"),
        step_count=25,
        current_step_label="4",
    )

    def test_server_values_said_exactly_pass(self) -> None:
        for text in (
            "현재 프로토콜은 총 25단계입니다.", "현재 4단계입니다.", "현재 단계는 4단계예요.",
            "원문 해시는 ab12cd34로 시작합니다.", "구조 해시 0F1E2D3C4B5A.",
            "실행 버전 rev-3입니다.", "제목: In-gel digestion", "We are currently at step 4.",
            "트립신 25 µL를 넣어요.",
        ):
            with self.subTest(text=text):
                self.assertEqual(server_value_violations(text, self.VALUES), ())

    def test_a_near_copy_of_a_server_value_is_caught(self) -> None:
        for text, code in (
            ("총 24단계입니다.", "step_count_not_server_value"),
            ("the protocol has 24 steps", "step_count_not_server_value"),
            ("지금 5단계예요.", "current_step_not_server_value"),
            ("해시는 deadbeef12 입니다.", "hash_not_server_value"),
            ("실행 버전 rev-4", "revision_not_server_value"),
            ("제목: In gel digestion", "title_not_server_value"),
        ):
            with self.subTest(text=text):
                self.assertIn(code, server_value_violations(text, self.VALUES))

    def test_no_current_step_before_the_protocol_starts(self) -> None:
        values = ServerValues("In-gel digestion", "rev-3", (), 25, None)
        self.assertIn(
            "current_step_not_server_value", server_value_violations("현재 1단계입니다.", values)
        )


if __name__ == "__main__":
    unittest.main()
