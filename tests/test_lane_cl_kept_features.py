"""What the clean-up must leave working (lane CL, 2026-10-10).

Lane CL deletes the approved-safety-manual and lab-reference searches, the
safety-report tools, the Moss connection and the report hand-off worker. The
general explanation the 10/10 voice test used ("HPLC water 가 뭐야?") runs on
the same research path the lab-reference search sat on, so it is checked here
through ``run_turn`` with nothing of the deleted code patched: the rules'
answer is said, then the short outside-PDF explanation, shown as "AI 일반
지식", and the workflow state is unchanged. The test passes before the
deletion and after it.

The other features of the 10/10 test have their own tests (the table in the
lane report); none of them touches the deleted code.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol_vocabulary_support import build_fixture
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
)
from voiney_lab.language import Transcription
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

STEPS = (
    "1 Prepare 50 mL of 25mM AMBIC in HPLC water.",
    "2 Wash the gel plug with 200 µL of the AMBIC solution.",
    "3 Remove and discard the wash solution.",
)
MATERIALS = (
    "Ammonium bicarbonate Sigma-Aldrich Catalog #09830",
    "HPLC water Fisher Scientific Catalog #W5-4",
)
EXPLANATION = "분석 장비에 쓰는 아주 깨끗한 물로, 불순물이 거의 없어요."


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


class _Explainer:
    calls: list[str] = []

    def __init__(self, *args) -> None:
        pass

    async def explain_outside_pdf(self, question, *, subject, kind, language):
        _Explainer.calls.append(question)
        return {"status": "success", "answer": EXPLANATION,
                "backend": "supplemental_outside_pdf_explanation"}

    async def explain(self, query, *, language):
        _Explainer.calls.append(query)
        return {"status": "success", "answer": EXPLANATION,
                "backend": "xai_responses_supplemental_model_knowledge"}


def _session() -> ListenerSession:
    fixture = build_fixture(
        protocol_id="lane-cl-kept", title="Fictional AMBIC wash",
        steps=STEPS, materials=MATERIALS,
    )
    # The catalog path is the server's trusted context; nothing is there.
    context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    workflow.current_index = 0
    session = ListenerSession(tool_context=context, curated_protocol_session=workflow)
    session.active = True
    session.active_turn_id = 1
    session.next_turn_id = 2
    session.turn_generations[1] = session.generation
    session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
    session.detector.state = TurnState.PROCESSING
    session.external_reference_settings = ExternalReferenceSettings(False)
    session.supplemental_knowledge_settings = SupplementalKnowledgeSettings(
        True, "offline-supplement", 2.0,
    )
    return session


def _projection(session: ListenerSession) -> tuple:
    curated = session.curated_protocol_session
    return (
        curated.active, curated.current_index, curated.workflow_status,
        curated.state()["revision"],
    )


class GeneralExplanationTests(unittest.TestCase):
    def test_hplc_water_is_explained_after_the_rules_answer(self) -> None:
        session = _session()
        before = _projection(session)
        socket = _Socket()
        _Explainer.calls = []

        async def immediate(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def unsupported(*args, **kwargs):
            return SimpleNamespace(
                intent="unsupported", primary_text="", evidence_ids=(),
                inference_labels=(), unsupported_parts=("definition",),
            )

        with patch(
            "voiney_lab.server.transcribe",
            return_value=Transcription("HPLC water가 뭐야?", "ko"),
        ), patch(
            "voiney_lab.server.synthesize", return_value=b"\0\0",
        ), patch(
            "voiney_lab.server.answer_curated_protocol_question",
            side_effect=unsupported,
        ), patch(
            "voiney_lab.server.XaiSupplementalKnowledge", _Explainer,
        ), patch(
            "voiney_lab.server.AsyncOpenAI", return_value=SimpleNamespace(),
        ), patch(
            "voiney_lab.server.require_env", return_value="offline",
        ), patch(
            "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
        ):
            asyncio.run(run_turn(socket, session, b"\0\0", 1, 1))

        self.assertEqual(_Explainer.calls, ["HPLC water가 뭐야?"])
        reply = next(item for item in socket.text if item["type"] == "reply.delta")
        self.assertIn("HPLC water", reply["text"])
        # The rules' answer is segment 0, the explanation segment 1.
        segments = [
            item["segment_index"] for item in socket.text
            if item["type"] == "audio.segment.start"
        ]
        self.assertEqual(segments, [0, 1])
        (result,) = [item for item in socket.text if item["type"] == "research.result"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source_label"], "AI 일반 지식")
        self.assertTrue(result["outside_pdf"])
        self.assertTrue(result["spoken"])
        self.assertTrue(result["primary_text"].startswith("PDF에는 따로 설명이 없어요."))
        self.assertIn(EXPLANATION, result["primary_text"])
        self.assertEqual(_projection(session), before)


if __name__ == "__main__":
    unittest.main()
