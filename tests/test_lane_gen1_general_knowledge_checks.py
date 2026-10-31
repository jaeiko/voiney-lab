"""GEN1: general knowledge never tells a supposition as having happened.

The 10/31 voice test: for "여기서 침전이 생기면" the reply went on with "AI 일반
지식" -- "solution A의 성분이 … 침전이 생겼을 수 있어요" -- as if the precipitate
were there. A supplemental model's sentence about a problem may not name a
cause as certain, and of a supposition it may not say the supposed thing
happened. The check is at the provider boundary (the fake transport below) and
again on the server's side (``possible_cause_violations``,
``outside_pdf_violations``). Contract-tested only.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from voiney_lab.answer_checks import (
    outside_pdf_violations,
    possible_cause_violations,
    supposition_violations,
)
from voiney_lab.external_references import SupplementalKnowledgeSettings, XaiSupplementalKnowledge

#: The 10/31 sentence, in the shape it was said.
TOLD_AS_HAPPENED = "solution A의 성분이 반응해서 침전이 생겼을 수 있어요."
#: The same knowledge, kept as a supposition.
KEPT_SUPPOSED = "용액의 성분이 반응하면 침전이 생길 수 있어요."


class SuppositionCheckTests(unittest.TestCase):
    def test_a_supposition_is_not_told_as_having_happened(self) -> None:
        for question in ("여기서 침전이 생기면", "혹시 거품이 나면 어떡해?", "만약 색이 변하면 어떻게 돼?"):
            with self.subTest(question=question):
                self.assertIn(
                    "supposed_told_as_happened",
                    supposition_violations(TOLD_AS_HAPPENED, question=question, problem=True),
                )
                self.assertEqual(
                    supposition_violations(KEPT_SUPPOSED, question=question, problem=True), ())

    def test_a_reported_problem_may_have_possible_past_causes(self) -> None:
        self.assertEqual(
            supposition_violations("온도가 높았을 수 있어요.", question="침전이 생겼어", problem=True), ())

    def test_a_cause_is_never_said_as_certain_about_a_problem(self) -> None:
        for answer in ("온도가 높기 때문이에요.", "원인은 시약의 오염이에요.", "It is caused by heat."):
            with self.subTest(answer=answer):
                self.assertIn(
                    "cause_asserted",
                    supposition_violations(answer, question="침전이 생겼어", problem=True),
                )
        self.assertEqual(
            supposition_violations("온도가 높았기 때문일 수 있어요.", question="침전이 생겼어", problem=True), ())

    def test_a_purpose_explanation_may_say_because(self) -> None:
        # Not a problem: why a reagent is used is no cause of an event.
        self.assertEqual(supposition_violations("단백질을 자르기 때문이에요.", question="트립신은 왜 써?"), ())

    def test_the_server_side_checks_apply_it(self) -> None:
        self.assertIn(
            "supposed_told_as_happened",
            possible_cause_violations(
                TOLD_AS_HAPPENED, sources_text="", protocol_text="solution A", problem="여기서 침전이 생기면"),
        )
        self.assertIn(
            "cause_asserted",
            possible_cause_violations(
                "온도 때문이에요. 시약이 오래됐을 수 있어요.", sources_text="", protocol_text=""),
        )
        self.assertIn(
            "supposed_told_as_happened",
            outside_pdf_violations(
                "일반적으로 거품이 생겼어요", question="혹시 거품이 나면 왜 그래?", term="거품",
                protocol_terms=("거품",)),
        )


class _Responses:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.requests: list[dict] = []

    async def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(output_text=self.answer, id="fake-1")


def _explainer(answer: str) -> tuple[XaiSupplementalKnowledge, _Responses]:
    responses = _Responses(answer)
    client = SimpleNamespace(responses=responses)
    return XaiSupplementalKnowledge(client, SupplementalKnowledgeSettings(True, "fake-supplement", 3.0)), responses


class ProviderBoundaryTests(unittest.TestCase):
    def test_possible_causes_of_a_supposition_told_as_happened_are_rejected(self) -> None:
        explainer, responses = _explainer(TOLD_AS_HAPPENED)
        result = asyncio.run(explainer.explain_possible_causes(
            "여기서 침전이 생기면", step_text="Mix solution A.", language="ko"))
        self.assertEqual(len(responses.requests), 1)
        self.assertEqual(result["status"], "response_rejected")

    def test_possible_causes_kept_as_a_supposition_are_said(self) -> None:
        explainer, _ = _explainer(KEPT_SUPPOSED)
        result = asyncio.run(explainer.explain_possible_causes(
            "여기서 침전이 생기면", step_text="Mix solution A.", language="ko"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["answer"], KEPT_SUPPOSED)

    def test_possible_causes_naming_a_cause_as_certain_are_rejected(self) -> None:
        explainer, _ = _explainer("온도가 너무 높았기 때문이에요.")
        result = asyncio.run(explainer.explain_possible_causes(
            "침전이 생겼어", step_text="Mix solution A.", language="ko"))
        self.assertEqual(result["status"], "response_rejected")

    def test_an_outside_pdf_explanation_of_a_supposition_told_as_happened_is_rejected(self) -> None:
        explainer, _ = _explainer("침전이 생겼어요")
        result = asyncio.run(explainer.explain_outside_pdf(
            "혹시 침전이 생기면 왜 그래?", subject="solution A", kind="purpose", language="ko"))
        self.assertEqual(result["status"], "response_rejected")


if __name__ == "__main__":
    unittest.main()
