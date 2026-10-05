"""Gaps the 2026-10-06 voice tests found, closed by rule (lane R6).

Decisions of 2026-10-06 (two voice tests, router gpt-6-luna, no xAI):

1. A quantity asked of a named step ("N단계 얼마나 넣어?", "N단계에 대해서도
   얼마나 넣어야 될지 알려줘", "다음 단계는 얼마나 넣어?") is answered by a front
   rule from that step's source values, in lane XO's sentence. Several
   substances are asked back; a step with no value is read as written.
2. Another step's substances are said with the source's values ("다음 2단계는
   Solution A(25mM AMBIC 2 : acetonitrile 1)와 Solution B(25mM AMBIC)를
   만들어요."); past a length the names are said and the values left to the
   screen. Every number said is the source's.
3. A quantity the source answers is never given model knowledge.
4. The two-step question back ("어느 쪽인지 말씀해 주세요") ended the turn as
   "차단됨": it was planned with speech_mode "blocked". It is a question, not a
   refusal.
6. Where the source gives no meaning or purpose, a short outside-PDF
   explanation (D4) may follow the rules' answer: said after it, "PDF에는 따로
   설명이 없어요. 일반적으로는 …", and shown as "AI 일반 지식". The term answer
   reads as a sentence: "탈색(destained)은 원문 7단계에 나와요."
7. "2단계" and "이 단계" sound alike. Away from step 2 a read-only question
   naming them is answered for the current step, with how to name step 2.
8. With web references off, nothing ends in "웹 참고 자료 확인 제한".
"""

from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol_vocabulary_support import SOURCE_PDF, build_fixture, in_gel_fixture
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    CuratedProtocolAction,
    CuratedProtocolSession,
    CuratedProtocolSpeechMode,
)
from voiney_lab.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
)
from voiney_lab.language import Transcription
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

STEPS = (
    "1 Place the sample in a tube containing approximately 200 µL 25mM AMBIC.",
    "2 Prepare two wash solutions: Solution A: 2 parts of 25mM ammonium bicarbonate "
    "(AMBIC) mixed with 1 part acetonitrile Solution B: 25mM ammonium bicarbonate (AMBIC)",
    "3 Wash the band with 500 µL of solution A.",
    "4 Remove and discard solution A.",
    "5 Prepare a solution of 1.5mg/mL of DTT 10 millimolar (mM) and 10mg/mL of "
    "iodoacetamide 60 millimolar (mM) in 25mM AMBIC",
    "6 Add enough volume of the DTT solution to fully cover the gel band.",
)
MATERIALS = (
    "Ammonium bicarbonate Sigma-Aldrich Catalog #09830",
    "Acetonitrile VWR Catalog #BJLC015",
    "DTT Sigma-Aldrich Catalog #D0632",
    "Iodoacetamide Sigma-Aldrich Catalog #I1149",
)


def _fixture():
    return build_fixture(
        protocol_id="lane-r6-test", title="Fictional wash protocol",
        steps=STEPS, materials=MATERIALS,
    )


def _session(step_index: int, fixture=None) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture or _fixture())
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = step_index
    return session


def _front(session, said, turn_id=2, language="ko"):
    return session.front_plan(
        said, turn_id=turn_id, language=language, configuration_id=1, generation=1,
    )


def _projection(session):
    return (
        session.active, session.current_index, session.workflow_status,
        session.state()["revision"],
    )


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


class StepTargetedQuantityTests(unittest.TestCase):
    """Decision 1: the named step's own source value, by a front rule."""

    def test_a_named_step_is_answered_from_its_source(self) -> None:
        for said in (
            "3단계 얼마나 넣어?", "3단계는 얼마나 넣어?",
            "3단계에 대해서도 얼마나 넣어야 될지 알려줘", "세 번째 단계 얼마나 넣어?",
        ):
            with self.subTest(said=said):
                session = _session(0)
                before = _projection(session)
                plan = _front(session, said)
                self.assertIsNotNone(plan, "handed on to the model")
                self.assertEqual(session.last_front_rule, "quantity_target")
                self.assertEqual(plan.speech_text, "3단계에서는 solution A를 500 µL 넣어요.")
                self.assertEqual(plan.target_step, "3")
                self.assertFalse(plan.state_changed)
                self.assertEqual(_projection(session), before)

    def test_the_next_step_is_answered_from_its_source(self) -> None:
        plan = _front(_session(1), "다음 단계는 얼마나 넣어?")
        self.assertEqual(plan.speech_text, "다음 3단계에서는 solution A를 500 µL 넣어요.")

    def test_a_step_with_no_value_is_read_as_written(self) -> None:
        plan = _front(_session(0), "6단계 얼마나 넣어?")
        self.assertEqual(
            plan.speech_text,
            "6단계 원문에는 'Add enough volume of the DTT solution to fully cover the "
            "gel band.'라고 되어 있어요.",
        )
        self.assertIs(plan.action, CuratedProtocolAction.QUESTION)

    def test_a_step_that_does_not_exist_is_not_taken(self) -> None:
        self.assertIsNone(_front(_session(0), "9단계 얼마나 넣어?"))

    def test_a_named_step_with_several_substances_is_asked_back_with_values(self) -> None:
        plan = _front(_session(2), "두 번째 단계 얼마나 넣어?")
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_PARAMETER)
        self.assertEqual(
            plan.speech_text,
            "2단계는 Solution A(25mM AMBIC 2 : acetonitrile 1)와 Solution B(25mM AMBIC)를 "
            "만들어요. 어느 쪽을 말씀하세요?",
        )
        self.assertIsNot(plan.speech_mode, CuratedProtocolSpeechMode.BLOCKED)


class NextStepValuesTests(unittest.TestCase):
    """Decision 2: the next step's substances with the source's values."""

    def test_the_next_steps_solutions_are_said_with_their_values(self) -> None:
        session = _session(0)
        plan = _front(session, "얼마나 넣어?")
        self.assertEqual(session.last_front_rule, "quantity_target")
        self.assertEqual(
            plan.speech_text,
            "지금 1단계라면 25mM AMBIC 약 200 µL, 다음 2단계는 Solution A(25mM AMBIC 2 : "
            "acetonitrile 1)와 Solution B(25mM AMBIC)를 만들어요. 어느 쪽인지 말씀해 주세요.",
        )
        source = STEPS[0] + " " + STEPS[1]
        for number in _numbers(plan.speech_text.replace("1단계", "").replace("2단계", "")):
            self.assertIn(number, _numbers(source))

    def test_too_many_values_are_left_to_the_screen(self) -> None:
        plan = _front(_session(3), "얼마나 넣어?")
        self.assertEqual(
            plan.speech_text,
            "다음 5단계에는 DTT, iodoacetamide와 AMBIC이 있어요. 자세한 값은 화면에 있어요. "
            "어느 쪽을 말씀하세요?",
        )
        self.assertIn("DTT(1.5mg/mL, 10 millimolar (mM))", plan.display_text)
        self.assertIn("AMBIC(25mM)", plan.display_text)


class SourceAnsweredQuantityTests(unittest.TestCase):
    """Decision 3: a quantity the source answers gets no model knowledge."""

    def test_quantity_questions_the_source_answers_are_known(self) -> None:
        session = _session(0)
        for said in ("얼마나 넣어?", "3단계 얼마나 넣어?", "다음 단계는 얼마나 넣어?"):
            with self.subTest(said=said):
                self.assertTrue(session.quantity_answered_by_source(said))
        self.assertFalse(session.quantity_answered_by_source("AMBIC이 뭐야?"))

    def test_no_outside_pdf_explanation_is_offered_for_a_quantity(self) -> None:
        session = _session(0)
        plan = _front(session, "3단계 얼마나 넣어?")
        self.assertIsNone(session.outside_pdf_gap(plan, "3단계 얼마나 넣어?"))


class QuestionBackIsNotBlockedTests(unittest.TestCase):
    """Decision 4: a question back is not a refusal."""

    def test_the_two_step_question_back_is_not_blocked(self) -> None:
        for step_index, said in ((0, "얼마나 넣어?"), (1, "얼마나 넣어?"), (3, "얼마나 넣어?")):
            with self.subTest(step=step_index + 1):
                plan = _front(_session(step_index), said)
                self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_PARAMETER)
                self.assertIs(plan.speech_mode, CuratedProtocolSpeechMode.CONTROL)


class StepHomophoneTests(unittest.TestCase):
    """Decision 7: "2단계" and "이 단계" are said alike."""

    LEAD = "지금 {label}단계 기준으로 답할게요. "
    HINT = " 2단계를 물으신 거면 '두 번째 단계'라고 해 주세요."

    def test_a_read_only_question_away_from_step_2_is_the_current_steps(self) -> None:
        for said, rest in (
            ("2단계 얼마나 넣어?", "지금 3단계에서는 solution A를 500 µL 넣어요."),
            ("이 단계 얼마나 넣어?", "지금 3단계에서는 solution A를 500 µL 넣어요."),
            ("이단계 얼마나 넣어?", "지금 3단계에서는 solution A를 500 µL 넣어요."),
            ("2단계에 대해서도 얼마나 넣어야 될지 알려줘",
             "지금 3단계에서는 solution A를 500 µL 넣어요."),
        ):
            with self.subTest(said=said):
                session = _session(2)
                before = _projection(session)
                plan = _front(session, said)
                self.assertIsNotNone(plan)
                self.assertEqual(session.last_front_rule, "step_homophone")
                self.assertEqual(
                    plan.speech_text, self.LEAD.format(label="3") + rest + self.HINT,
                )
                self.assertFalse(plan.state_changed)
                self.assertEqual(_projection(session), before)

    def test_a_step_lookup_is_the_current_steps(self) -> None:
        session = _session(0)
        plan = _front(session, "2단계 뭐야?")
        self.assertEqual(session.last_front_rule, "step_homophone")
        self.assertTrue(plan.speech_text.startswith(f"지금 1단계 기준으로 답할게요. {STEPS[0]}"))
        self.assertTrue(plan.speech_text.endswith(self.HINT.strip()))

    def test_two_beon_jjae_and_next_are_step_2_and_the_next_step(self) -> None:
        plan = _front(_session(0), "두 번째 단계 뭐야?")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.target_step, "2")
        self.assertIn(STEPS[1], plan.speech_text)
        self.assertNotIn("기준으로 답할게요", plan.speech_text)
        plan = _front(_session(0), "다음 단계는 얼마나 넣어?")
        self.assertNotIn("기준으로 답할게요", plan.speech_text)

    def test_at_step_2_it_is_step_2(self) -> None:
        session = _session(1)
        plan = _front(session, "2단계 뭐야?")
        self.assertNotEqual(session.last_front_rule, "step_homophone")
        self.assertNotIn("기준으로 답할게요", plan.speech_text)

    def test_a_word_that_moves_the_protocol_is_not_read_this_way(self) -> None:
        session = _session(0)
        plan = session.plan("2단계 완료했어", turn_id=2, language="ko",
                            configuration_id=1, generation=1)
        self.assertNotEqual(session.last_front_rule, "step_homophone")
        self.assertFalse(plan.state_changed)
        # The existing step-mismatch question already asks about the current step.
        self.assertEqual(plan.speech_text, "현재 진행 중인 단계는 1단계입니다. 1단계를 완료하셨다는 뜻인가요?")

    def test_the_rule_is_described(self) -> None:
        self.assertIn("step_homophone", FRONT_RULES)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelVoiceTestTurnsTests(unittest.TestCase):
    """The turns of the 2026-10-06 voice tests, on the in-gel protocol."""

    def test_step_1_says_step_2s_solutions_with_their_values(self) -> None:
        plan = _front(_session(0, in_gel_fixture()), "얼마나 넣어?")
        self.assertEqual(
            plan.speech_text,
            "지금 1단계라면 25mM AMBIC 약 200 µL, 다음 2단계는 Solution A(25mM AMBIC 2 : "
            "acetonitrile 1)와 Solution B(25mM AMBIC)를 만들어요. 어느 쪽인지 말씀해 주세요.",
        )
        self.assertIs(plan.speech_mode, CuratedProtocolSpeechMode.CONTROL)

    def test_two_beon_jjae_step_quantity_is_step_2(self) -> None:
        plan = _front(_session(0, in_gel_fixture()), "두 번째 단계 얼마나 넣어?")
        self.assertEqual(
            plan.speech_text,
            "다음 2단계는 Solution A(25mM AMBIC 2 : acetonitrile 1)와 Solution B(25mM AMBIC)를 "
            "만들어요. 어느 쪽을 말씀하세요?",
        )

    def test_the_term_answer_reads_as_a_sentence(self) -> None:
        session = _session(6, in_gel_fixture())
        plan = session.plan("탈색이 뭐야?", turn_id=2, language="ko",
                            configuration_id=1, generation=1)
        self.assertTrue(plan.speech_text.startswith("탈색(destained)은 원문 7단계에 나와요."))
        self.assertNotIn("이 프로토콜 원문에서", plan.speech_text)


class OutsidePdfGapTests(unittest.TestCase):
    """Decision 6: where an outside-PDF explanation may follow."""

    def test_a_meaning_the_source_does_not_give_has_a_gap(self) -> None:
        session = _session(2)
        plan = session.plan("acetonitrile이 뭐야?", turn_id=2, language="ko",
                            configuration_id=1, generation=1)
        envelope = session.protocol_answer_envelope(plan, language="ko")
        plan = plan.__class__(**{
            **plan.__dict__,
            "unresolved_dimensions": envelope.source_plan.unresolved_dimensions,
        })
        gap = session.outside_pdf_gap(plan, "acetonitrile이 뭐야?")
        self.assertIsNotNone(gap)
        self.assertEqual(gap["kind"], "term")

    def test_a_step_purpose_question_has_a_gap(self) -> None:
        session = _session(2)
        plan = session.plan("현재 단계 왜 하는 거야?", turn_id=2, language="ko",
                            configuration_id=1, generation=1)
        if plan.action is CuratedProtocolAction.QUESTION:
            self.assertEqual(session.outside_pdf_gap(plan, "현재 단계 왜 하는 거야?")["kind"], "step")

    def test_a_method_or_quantity_question_has_none(self) -> None:
        session = _session(2)
        for said in ("얼마나 넣어?", "acetonitrile 어떻게 넣어?", "acetonitrile 안전해?"):
            with self.subTest(said=said):
                plan = session.plan(said, turn_id=10 + len(said), language="ko",
                                    configuration_id=1, generation=1)
                self.assertIsNone(session.outside_pdf_gap(plan, said))

    def test_the_d4_checks_drop_numbers_methods_and_length(self) -> None:
        session = _session(2)
        gap = {"subject": "acetonitrile", "term": "acetonitrile", "kind": "term"}
        question = "acetonitrile이 뭐야?"
        self.assertEqual(session.outside_pdf_explanation_violations(
            "유기 용매로, 젤 조각의 물을 빼는 데 흔히 쓰여요.", question=question, gap=gap,
        ), ())
        for bad in (
            "보통 500 µL을 써요.", "젤을 덮을 만큼 넣으세요.", "독성이 있어 위험해요.",
            "가" * 121,
        ):
            with self.subTest(bad=bad):
                self.assertTrue(session.outside_pdf_explanation_violations(
                    bad, question=question, gap=gap,
                ))


class Socket:
    def __init__(self):
        self.text: list[dict] = []
        self.binary: list[bytes] = []

    async def send_text(self, value: str) -> None:
        import json
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        self.binary.append(value)


class ServerOutsidePdfExplanationTests(unittest.TestCase):
    """Decisions 3, 6 and 8 at the server boundary."""

    def _session(self, index: int, *, supplemental: bool) -> ListenerSession:
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        workflow = CuratedProtocolSession(_fixture())
        workflow.active = True
        workflow.current_index = index
        session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", workflow.fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        session.external_reference_settings = ExternalReferenceSettings(False)
        session.supplemental_knowledge_settings = SupplementalKnowledgeSettings(
            supplemental, "offline-supplement", 2.0,
        )
        return session

    def _turn(self, session, said, explainer):
        socket = Socket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def unsupported(*args, **kwargs):
            return SimpleNamespace(
                intent="unsupported", primary_text="", evidence_ids=(),
                inference_labels=(), unsupported_parts=("role",),
            )

        with patch(
            "voiney_lab.server.transcribe", return_value=Transcription(said, "ko"),
        ), patch(
            "voiney_lab.server.synthesize", return_value=b"\0\0",
        ), patch(
            "voiney_lab.server.answer_curated_protocol_question", side_effect=unsupported,
        ), patch(
            "voiney_lab.server.search_approved_lab_references",
            return_value={
                "status": "no_admissible_evidence", "answerable": False,
                "matches": [], "retrieval": {"backend": "sqlite"},
            },
        ), patch(
            "voiney_lab.server.XaiSupplementalKnowledge", explainer,
        ), patch(
            "voiney_lab.server.AsyncOpenAI", return_value=SimpleNamespace(),
        ), patch(
            "voiney_lab.server.require_env", return_value="offline",
        ), patch(
            "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))
        return socket

    @staticmethod
    def _explainer(answer: str, *, delay: float = 0.0):
        class Explainer:
            calls: list[str] = []

            def __init__(self, *args):
                pass

            async def explain_outside_pdf(self, question, *, subject, kind, language):
                Explainer.calls.append(question)
                await asyncio.sleep(delay)
                return {"status": "success", "answer": answer,
                        "backend": "supplemental_outside_pdf_explanation"}

            async def explain(self, query, *, language):
                Explainer.calls.append(query)
                return {"status": "success", "answer": answer,
                        "backend": "xai_responses_supplemental_model_knowledge"}

        return Explainer

    @staticmethod
    def _of(socket, kind):
        return [item for item in socket.text if item["type"] == kind]

    def test_an_explanation_is_said_after_the_rules_answer(self) -> None:
        session = self._session(2, supplemental=True)
        explainer = self._explainer("유기 용매로, 젤 조각의 물을 빼는 데 흔히 쓰여요.")
        socket = self._turn(session, "acetonitrile이 뭐야?", explainer)
        self.assertEqual(len(explainer.calls), 1)
        segments = [item["segment_index"] for item in self._of(socket, "audio.segment.start")]
        self.assertEqual(segments, [0, 1])
        complete = self._of(socket, "audio.complete")
        self.assertEqual([item["segment_count"] for item in complete], [2])
        (result,) = self._of(socket, "research.result")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source_label"], "AI 일반 지식")
        self.assertTrue(result["outside_pdf"])
        self.assertTrue(result["primary_text"].startswith(
            "PDF에는 따로 설명이 없어요. 일반적으로는 유기 용매로"))
        self.assertEqual(session.curated_protocol_session.current_index, 2)

    def test_an_explanation_that_fails_d4_is_dropped(self) -> None:
        session = self._session(2, supplemental=True)
        socket = self._turn(session, "acetonitrile이 뭐야?", self._explainer("보통 500 µL 넣으세요."))
        self.assertEqual(
            [item["segment_index"] for item in self._of(socket, "audio.segment.start")], [0])
        self.assertEqual(
            [item["segment_count"] for item in self._of(socket, "audio.complete")], [1])
        self.assertEqual(self._of(socket, "research.result"), [])
        self.assertEqual(self._of(socket, "research.state"), [])

    def test_a_late_explanation_leaves_the_rules_answer_alone(self) -> None:
        session = self._session(2, supplemental=True)
        socket = self._turn(
            session, "acetonitrile이 뭐야?",
            self._explainer("유기 용매로 흔히 쓰여요.", delay=0.6),
        )
        self.assertEqual(
            [item["segment_index"] for item in self._of(socket, "audio.segment.start")], [0])
        self.assertEqual(
            [item["segment_count"] for item in self._of(socket, "audio.complete")], [1])
        self.assertEqual(self._of(socket, "research.result"), [])

    def test_a_quantity_question_gets_no_model_knowledge(self) -> None:
        session = self._session(0, supplemental=True)
        explainer = self._explainer("일반적으로 많이 넣어요.")
        socket = self._turn(session, "3단계 얼마나 넣어?", explainer)
        self.assertEqual(explainer.calls, [])
        self.assertEqual(self._of(socket, "research.result"), [])
        self.assertEqual(
            [item["segment_count"] for item in self._of(socket, "audio.complete")], [1])

    def test_web_references_off_send_no_web_limit_notice(self) -> None:
        session = self._session(2, supplemental=False)
        socket = self._turn(session, "acetonitrile이 뭐야?", self._explainer("x"))
        self.assertEqual(self._of(socket, "research.state"), [])
        self.assertEqual(self._of(socket, "research.result"), [])
        self.assertFalse(any(
            "웹 참고 자료" in str(item.get("limitation", "")) for item in socket.text
        ))

    def test_the_question_back_turn_is_not_blocked(self) -> None:
        session = self._session(0, supplemental=False)
        socket = self._turn(session, "얼마나 넣어?", self._explainer("x"))
        (done,) = self._of(socket, "turn.done")
        self.assertEqual(done["speech_mode"], "control")
        self.assertNotEqual(session.turn_terminal_outcome(1, session.generation), "blocked")


if __name__ == "__main__":
    unittest.main()
