"""Lane F, decision 2: the written explanation is asked with its step.

The written supplemental explanation (``explain``) is asked with the source
text of the step the rules answered, as the spoken outside-PDF explanation is
(lane R6). The search query itself is unchanged. Provider calls here are fake.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
    plan_research_query,
    supplemental_explanation_query,
)
from voiney_lab.language import Transcription
from voiney_lab.multi_brain import MultiBrainSettings
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.protocol_vocabulary_support import MINIPREP_STEPS, miniprep_fixture


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


def _listener(index: int, *, supplemental: bool = False) -> ListenerSession:
    fixture = miniprep_fixture()
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    workflow.current_index = index
    session = ListenerSession(
        tool_context=ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only"),
        curated_protocol_session=workflow,
        multi_brain_settings=MultiBrainSettings(False),
    )
    session.active = True
    session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
    session.external_reference_settings = ExternalReferenceSettings(False)
    if supplemental:
        session.supplemental_knowledge_settings = SupplementalKnowledgeSettings(
            True, "offline-supplement", 2.0,
        )
    return session


def _turn(session: ListenerSession, transcript: str, turn_id: int = 1, *, supplement=None):
    session.active_turn_id = turn_id
    session.next_turn_id = turn_id + 1
    session.turn_generations[turn_id] = session.generation
    session.detector.state = TurnState.PROCESSING
    socket = _Socket()

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    patches = [
        patch("voiney_lab.server.transcribe", return_value=Transcription(transcript, "ko")),
        patch("voiney_lab.server.synthesize", return_value=b"\0\0"),
        patch("voiney_lab.server.asyncio.to_thread", side_effect=immediate),
        patch("voiney_lab.server.AsyncOpenAI", return_value=SimpleNamespace()),
        patch("voiney_lab.server.require_env", return_value="offline"),
    ]
    if supplement is not None:
        patches.append(patch("voiney_lab.server.XaiSupplementalKnowledge", supplement))
    for item in patches:
        item.start()
    try:
        asyncio.run(run_turn(socket, session, b"\0\0", turn_id, session.generation))
    finally:
        for item in reversed(patches):
            item.stop()
    return socket.text


def _recording_supplement():
    class Supplement:
        queries: list[str] = []

        def __init__(self, *args) -> None:
            pass

        async def explain(self, query, *, language):
            Supplement.queries.append(query)
            return {
                "status": "success",
                "answer": "컬럼 세척은 일반적으로 남은 불순물을 씻어 내는 과정입니다.",
                "backend": "xai_responses_supplemental_model_knowledge",
            }

        async def explain_outside_pdf(self, *args, **kwargs):
            raise AssertionError("the spoken path is not this test's")

    return Supplement


class SupplementalExplanationQueryTests(unittest.TestCase):
    def test_the_query_carries_the_step_source_text(self) -> None:
        query = supplemental_explanation_query(
            "laboratory protocol role in miniprep. Question: 3단계 왜 해?",
            step_label="3", step_text="  Wash the column\n with 500 µL ethanol. ",
        )
        self.assertEqual(
            query,
            "laboratory protocol role in miniprep. Question: 3단계 왜 해?\n"
            "Step 3 source text: Wash the column with 500 µL ethanol.",
        )

    def test_a_long_step_is_bounded_and_an_empty_one_adds_nothing(self) -> None:
        query = supplemental_explanation_query("Q", step_label="7", step_text="x" * 900)
        self.assertEqual(query, "Q\nStep 7 source text: " + "x" * 500)
        self.assertEqual(
            supplemental_explanation_query("Q", step_label="7", step_text=" \n "), "Q")

    def test_the_search_query_itself_is_unchanged(self) -> None:
        # The step text goes to the explanation only; plan_research_query,
        # which the web search also uses, still names entities only.
        query = plan_research_query(
            "3단계 왜 해?", protocol_title="Miniprep protocol",
            step_label="3", step_text=MINIPREP_STEPS[2], evidence_texts=(),
            requested_entity=None, question_kind="related_knowledge",
        )
        self.assertNotIn("source text", query)
        self.assertNotIn("ethanol", query)


class WrittenExplanationTurnTests(unittest.TestCase):
    def test_the_written_explanation_is_asked_with_the_current_step(self) -> None:
        # "이 단계 배경 지식 알려줘": a related question the spoken path does
        # not take, so the written explanation answers it.
        supplement = _recording_supplement()
        session = _listener(2, supplemental=True)
        events = _turn(session, "이 단계 배경 지식 알려줘", supplement=supplement)
        self.assertEqual(len(supplement.queries), 1)
        step = session.curated_protocol_session.fixture.steps[2]
        source = " ".join(step.instruction_source_text.split())
        self.assertIn(
            f"\nStep {step.source_label} source text: {source[:500]}",
            supplement.queries[0],
        )
        self.assertIn("Question: 이 단계 배경 지식 알려줘", supplement.queries[0])
        result = next(item for item in events if item["type"] == "research.result")
        self.assertEqual(result["answer_origin"], "supplemental_model_knowledge")
        # Read-only: the step does not move.
        self.assertEqual(session.curated_protocol_session.current_index, 2)


if __name__ == "__main__":
    unittest.main()
