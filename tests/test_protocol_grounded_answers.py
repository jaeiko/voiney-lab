"""Answers come from the registered PDF, never from sentences written for in-gel.

``curated_protocol.py`` held prose written for one document, the Candidate A
in-gel digestion protocol, and some of it reached every protocol: "시작" told
any protocol's user to cut a stained protein band into an AMBIC tube, "이
실험 목적이 뭐야?" described in-gel digestion and Evotips, a question naming a
1.5 mL tube got in-gel's 200 µL of 25 mM AMBIC, and "튜브가 뭐야?" crashed on
a step attribute that does not exist. Where the prose was gated on the
protocol id, in-gel itself heard sentences its PDF does not contain.

Now every protocol, in-gel included, is answered from its own statements:
the steps that name an entity and the first of them (its reviewed Korean
translation where the fixture carries one), a definition only where the PDF
writes the long form, the purpose and sections as the PDF states them, and
the protocol's own material names as entities.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.language import Transcription
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.protocol_vocabulary_support import (
    SOURCE_PDF,
    build_fixture,
    in_gel_fixture,
    miniprep_fixture,
)

#: Distinctive phrases of the removed in-gel prose. None is in either PDF or
#: in-gel's reviewed Korean translation.
REMOVED_PROSE = (
    "휘발성 약알칼리성",
    "라이신(Lys)과 아르기닌",
    "이황화 결합",
    "카르바미도메틸화",
    "마이크로센트리퓨지 튜브",
    "젤 안의 단백질을 소화해",
    "밴드 절단, 탈색, 환원",
    "무엇을 제거하나요",
    "고정 반복 횟수",
    "중탄산 암모늄",
    "중탄산암모늄",
    "지정된 AMBIC 용액이 담긴 튜브",
    "1.5 mL 튜브에는 약 200 µL",
    "Evotip과 질량분석",
    "SDS-PAGE 젤에서 분석할 밴드",
    "Candidate A",
    "후보 A",
)

#: Words of in-gel's document that a protocol without them must never hear.
IN_GEL_WORDS = ("AMBIC", "젤", "밴드", "Evotip", "트립신", "질량분석", "acetonitrile")

QUESTIONS = (
    "시작",
    "현재 단계 알려줘",
    "이 실험 목적이 뭐야?",
    "전체 흐름 알려줘",
    "튜브가 뭐야?",
    "1.5 mL 튜브에는 뭘 넣어?",
    "AMBIC가 뭐야?",
    "이 단계 자세히 설명해줘",
    "왜 해야 돼?",
    "이거 버려도 돼?",
)


def _texts(plan) -> str:
    return "\n".join(
        item or "" for item in (plan.display_text, plan.speech_text, plan.primary_text)
    )


def _run(fixture, utterances, *, index: int | None = None):
    session = CuratedProtocolSession(fixture)
    if index is not None:
        session.active = True
        session.current_index = index
    plans = []
    for turn_id, utterance in enumerate(utterances, 1):
        plans.append(route_curated_runtime_turn(
            session, utterance, turn_id=turn_id, language="ko").plan)
    return session, plans


class AnotherProtocolHearsOnlyItsOwnPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = miniprep_fixture()

    def test_no_in_gel_prose_or_word_reaches_another_protocol(self) -> None:
        for index in range(len(self.fixture.steps)):
            for utterance in QUESTIONS:
                with self.subTest(step=index + 1, utterance=utterance):
                    _, (plan,) = _run(self.fixture, (utterance,), index=index)
                    text = _texts(plan)
                    for phrase in (*REMOVED_PROSE, *IN_GEL_WORDS):
                        self.assertNotIn(phrase, text)

    def test_start_and_preview_name_no_other_documents_step(self) -> None:
        _, (preview,) = _run(self.fixture, ("현재 단계 알려줘",))
        self.assertEqual(
            preview.speech_text,
            "아직 실험 시작 전입니다. 1단계 안내를 화면에 표시했습니다. 지금 실험을 시작할까요?",
        )
        _, (start,) = _run(self.fixture, ("시작",))
        self.assertEqual(start.action, CuratedProtocolAction.START)
        self.assertEqual(
            start.speech_text, "실험을 시작합니다. 현재 1단계입니다. 안내를 화면에 표시했습니다."
        )
        self.assertIn(self.fixture.steps[0].instruction_source_text, start.display_text)

    def test_purpose_and_overview_are_the_pdfs_own(self) -> None:
        session, (purpose,) = _run(self.fixture, ("이 실험 목적이 뭐야?",), index=0)
        self.assertEqual(purpose.action, CuratedProtocolAction.PROTOCOL_QUERY)
        self.assertIn("A fictional protocol for vocabulary tests.", purpose.display_text)
        self.assertEqual(purpose.speech_text, "원문에 적힌 실험 목적을 화면에 표시했습니다.")

    def test_a_tube_question_answers_from_the_pdf_instead_of_crashing(self) -> None:
        # The crash: ``current_step.text`` on a step without a reviewed
        # Korean translation, i.e. on every protocol but in-gel.
        for index in range(len(self.fixture.steps)):
            with self.subTest(step=index + 1):
                session, (plan,) = _run(self.fixture, ("튜브가 뭐야?",), index=index)
                self.assertEqual(plan.action, CuratedProtocolAction.LAB_DOMAIN_QA)
                self.assertFalse(plan.state_changed)
                self.assertIn("tube: 이 프로토콜 원문에서 2단계에 나옵니다.", plan.display_text)
                self.assertIn(self.fixture.steps[1].instruction_source_text, plan.display_text)
                self.assertEqual(session.current_index, index)

    def test_a_1_5_ml_tube_question_gets_no_in_gel_quantities(self) -> None:
        _, (plan,) = _run(self.fixture, ("1.5 mL 튜브에는 뭘 넣어?",), index=0)
        self.assertNotIn("200 µL", _texts(plan))
        self.assertFalse(any(
            claim.target_id in {"place_gel_in_tube", "vessel_capacity"}
            for claim in plan.claim_requests
        ))

    def test_its_own_materials_are_answered_from_its_steps(self) -> None:
        for utterance, label, step in (
            ("lysozyme이 뭐야?", "lysozyme", "2"),
            ("Tris-HCl buffer는 왜 넣어?", "Tris-HCl buffer", "1"),
            ("microcentrifuge가 뭐야?", "microcentrifuge", "3"),
        ):
            with self.subTest(utterance=utterance):
                _, (plan,) = _run(self.fixture, (utterance,), index=0)
                self.assertEqual(plan.action, CuratedProtocolAction.RELATED_QUESTION)
                self.assertEqual(plan.requested_entities, (label,))
                self.assertIn(f"{label}: 이 프로토콜 원문에서 {step}단계", plan.display_text)
                index = int(step) - 1
                self.assertIn(
                    self.fixture.steps[index].instruction_source_text, plan.display_text
                )

    def test_a_definition_only_where_the_pdf_writes_the_long_form(self) -> None:
        _, (plan,) = _run(self.fixture, ("PBS가 뭐야?",), index=0)
        self.assertIn(
            "원문에서 PBS는 phosphate-buffered saline의 약어입니다.", plan.speech_text
        )
        _, (lysozyme,) = _run(self.fixture, ("lysozyme이 뭐야?",), index=0)
        self.assertNotIn("약어", lysozyme.display_text)
        # Not defined by the PDF, so the definition is left to references.
        unresolved = {
            claim.target_id for claim in lysozyme.claim_requests
            if claim.admission_status.value == "research_required"
        }
        self.assertIn("lysozyme", unresolved)

    def test_a_shared_in_gel_name_reads_this_protocols_statement(self) -> None:
        fixture = build_fixture(
            protocol_id="fictional-rinse",
            title="Fictional column rinse",
            steps=(
                "1 Rinse the column with 1 mL acetonitrile.",
                "2 Prepare Solution A by dissolving the salt in water.",
            ),
        )
        _, (plan,) = _run(fixture, ("acetonitrile이 뭐야?",), index=1)
        text = _texts(plan)
        self.assertIn("1 Rinse the column with 1 mL acetonitrile.", text)
        for phrase in REMOVED_PROSE:
            self.assertNotIn(phrase, text)
        self.assertNotIn("Solution A의 1 part", text)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelHearsItsOwnPdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()

    def test_no_removed_prose_at_any_step(self) -> None:
        for index in range(len(self.fixture.steps)):
            for utterance in (*QUESTIONS, "Solution A가 뭐야?", "DTT는 왜 넣어?",
                              "AMBIC와 HPLC water의 차이가 뭐야?", "트립신이 뭐야?"):
                with self.subTest(step=index + 1, utterance=utterance):
                    _, (plan,) = _run(self.fixture, (utterance,), index=index)
                    text = _texts(plan)
                    for phrase in REMOVED_PROSE:
                        self.assertNotIn(phrase, text)

    def test_start_reads_the_reviewed_first_step(self) -> None:
        _, (start,) = _run(self.fixture, ("시작",))
        reviewed = self.fixture.localized_fact(self.fixture.steps[0].step_id, "current_step")
        self.assertTrue(reviewed.startswith("1단계: "))
        self.assertEqual(
            start.speech_text,
            "실험을 시작합니다. 현재 1단계입니다. " + reviewed.removeprefix("1단계: "),
        )

    def test_ambic_is_defined_by_the_pdf_itself(self) -> None:
        _, (plan,) = _run(self.fixture, ("AMBIC가 뭐야?",), index=2)
        self.assertIn("원문에서 AMBIC는 ammonium bicarbonate의 약어입니다.", plan.speech_text)
        claim = next(c for c in plan.claim_requests if c.target_id == "ambic")
        self.assertEqual(claim.admission_status.value, "local_supported")

    def test_the_tube_is_where_the_pdf_introduces_it(self) -> None:
        _, (plan,) = _run(self.fixture, ("튜브가 뭐야?",), index=3)
        self.assertIn("1.5 mL 튜브", plan.speech_text)
        self.assertNotIn("Solution A를 제거해 폐기합니다", plan.speech_text)

    def test_step_three_alternative_is_labelled_from_its_own_wording(self) -> None:
        _, (plan,) = _run(self.fixture, ("이 단계 자세히 설명해줘",), index=2)
        self.assertIn("원문이 허용한 대안", plan.display_text)
        _, (eleven,) = _run(self.fixture, ("이 단계 자세히 설명해줘",), index=10)
        # Step 11's note ("You usually need around 50uL") offers no alternative.
        self.assertNotIn("원문이 허용한 대안", eleven.display_text)


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


class ServerBoundaryTests(unittest.TestCase):
    """The same answers through the server's own turn, as a student hears them."""

    def _turn(self, fixture, transcript: str, *, index: int):
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        workflow = CuratedProtocolSession(fixture)
        workflow.active = True
        workflow.current_index = index
        session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
        session.active = True
        session.active_turn_id = 1
        session.next_turn_id = 2
        session.turn_generations[1] = session.generation
        session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
        session.detector.state = TurnState.PROCESSING
        spoken: list[str] = []
        socket = _Socket()

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        def synthesize(text, language=None):
            spoken.append(text)
            return b"\0\0"

        with patch(
            "voiney_lab.server.transcribe", return_value=Transcription(transcript, "ko"),
        ), patch(
            "voiney_lab.server.synthesize", side_effect=synthesize,
        ), patch(
            "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, session.generation))
        return spoken, socket.text

    def test_another_protocols_tube_question_is_answered_and_spoken(self) -> None:
        fixture = miniprep_fixture()
        spoken, events = self._turn(fixture, "튜브가 뭐야?", index=0)
        self.assertEqual(len(spoken), 1)
        self.assertTrue(spoken[0].startswith("tube: 이 프로토콜 원문에서 2단계에 나옵니다."))
        reply = next(item for item in events if item["type"] == "reply.delta")
        self.assertIn(fixture.steps[1].instruction_source_text, reply["text"])
        self.assertFalse(any(item["type"] == "error" for item in events))
        for word in IN_GEL_WORDS:
            self.assertNotIn(word, spoken[0])


if __name__ == "__main__":
    unittest.main()
