"""Lane F, decision 3: reply.complete carries the delta's display_document.

``reply.complete`` carries the same ``display_document`` as the turn's
``reply.delta``, so the screen need not keep the delta's copy. No provider is
called.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.external_references import ExternalReferenceSettings
from voiney_lab.language import Transcription
from voiney_lab.multi_brain import MultiBrainSettings
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.protocol_vocabulary_support import miniprep_fixture


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


def _listener(index: int) -> ListenerSession:
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
    return session


def _turn(session: ListenerSession, transcript: str, turn_id: int = 1):
    session.active_turn_id = turn_id
    session.next_turn_id = turn_id + 1
    session.turn_generations[turn_id] = session.generation
    session.detector.state = TurnState.PROCESSING
    socket = _Socket()

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    with patch(
        "voiney_lab.server.transcribe", return_value=Transcription(transcript, "ko"),
    ), patch(
        "voiney_lab.server.synthesize", return_value=b"\0\0",
    ), patch(
        "voiney_lab.server.asyncio.to_thread", side_effect=immediate,
    ), patch(
        "voiney_lab.server.AsyncOpenAI", return_value=SimpleNamespace(),
    ), patch(
        "voiney_lab.server.require_env", return_value="offline",
    ):
        asyncio.run(run_turn(socket, session, b"\0\0", turn_id, session.generation))
    return socket.text


class ReplyCompleteDocumentTests(unittest.TestCase):
    def test_reply_complete_carries_the_delta_display_document(self) -> None:
        session = _listener(0)
        events = _turn(session, "현재 단계 알려줘")
        delta = next(item for item in events if item["type"] == "reply.delta")
        complete = next(item for item in events if item["type"] == "reply.complete")
        self.assertIsNotNone(delta["display_document"])
        self.assertIn("display_document", complete)
        self.assertEqual(complete["display_document"], delta["display_document"])
        self.assertEqual(complete["text"], delta["text"])

    def test_a_turn_with_no_document_says_so_in_both(self) -> None:
        session = _listener(0)
        events = _turn(session, "다음 단계로 넘어가줘")
        delta = next(item for item in events if item["type"] == "reply.delta")
        complete = next(item for item in events if item["type"] == "reply.complete")
        self.assertIn("display_document", complete)
        self.assertEqual(complete["display_document"], delta.get("display_document"))


if __name__ == "__main__":
    unittest.main()
