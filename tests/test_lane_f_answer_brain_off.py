"""Lane F, decision 1 (path (b)): the answer brain off, translation kept.

The voice turn's Answer role is off by default in run_dev.sh
(``VOINEY_LAB_ANSWER_BRAIN_ENABLED=false``; a person's value wins). The two
translation features no longer follow that switch: the reader's automatic
reading ("이 단계 읽어줘") and generating an authorized revision's
translations are on with the translation role's own settings (its key) and,
for the revision, the workspace -- whatever the answer brain is. Source and
Visual roles, and the Answer role when it is on, are unchanged. Provider
calls here are fake.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.language import Transcription
from voiney_lab.multi_brain import MultiBrainSettings
from voiney_lab.server import ListenerSession, run_turn
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

from tests.protocol_vocabulary_support import MINIPREP_STEPS, miniprep_fixture

GOOD_READING = "1단계: 세포 덩어리를 Tris-HCl buffer로 만든 Buffer 1 250 µL에 다시 풀어 줍니다."

#: A run_dev.sh run: the multi-brain on, the answer brain at the launcher's
#: default, a translation key present. Nothing else about models is set.
DEV_ENV = {
    "VOINEY_LAB_MULTI_BRAIN_ENABLED": "true",
    "VOINEY_LAB_ANSWER_BRAIN_ENABLED": "false",
    "VOINEY_LAB_TRANSLATION_PROVIDER": "xai",
    "XAI_API_KEY": "offline",
}
#: The same with no key for the translation role.
NO_TRANSLATION_KEY = {
    "VOINEY_LAB_TRANSLATION_PROVIDER": "xai",
    "XAI_API_KEY": "",
}


class _Model:
    """An AsyncOpenAI stand-in: a Korean reading, or a brain's JSON."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def __call__(self, *args, **kwargs):
        return self

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"korean": GOOD_READING}, ensure_ascii=False)))])

    def schemas(self) -> list[str]:
        return [
            call.get("response_format", {}).get("json_schema", {}).get("name", "")
            for call in self.calls
        ]


class _Socket:
    def __init__(self) -> None:
        self.text: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.text.append(json.loads(value))

    async def send_bytes(self, value: bytes) -> None:
        pass


def _settings(environment: dict[str, str]) -> MultiBrainSettings:
    with patch.dict(os.environ, environment):
        return MultiBrainSettings.from_environment()


def _turn(settings: MultiBrainSettings, transcript: str):
    fixture = miniprep_fixture()
    workflow = CuratedProtocolSession(fixture)
    workflow.active = True
    workflow.current_index = 0
    session = ListenerSession(
        tool_context=ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only"),
        curated_protocol_session=workflow, multi_brain_settings=settings,
    )
    session.active = True
    session.accept_configuration(41, "cascade", "ko", fixture.protocol_id)
    session.active_turn_id = 1
    session.next_turn_id = 2
    session.turn_generations[1] = session.generation
    session.detector.state = TurnState.PROCESSING
    model = _Model()
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
    ), patch(
        "voiney_lab.server.AsyncOpenAI", model,
    ), patch(
        "voiney_lab.server.require_env", return_value="offline",
    ):
        asyncio.run(run_turn(socket, session, b"\0\0", 1, session.generation))
    return model, spoken


class SettingsTests(unittest.TestCase):
    def test_translation_follows_the_translation_key_not_the_answer_brain(self) -> None:
        settings = _settings(DEV_ENV)
        self.assertFalse(settings.answer_brain_enabled)
        self.assertTrue(settings.translation_enabled)
        # Source and Visual keep following the multi-brain switch.
        self.assertTrue(settings.source_brain_enabled)
        self.assertTrue(settings.visual_brain_enabled)
        self.assertTrue(settings.enabled)
        self.assertFalse(_settings({**DEV_ENV, **NO_TRANSLATION_KEY}).translation_enabled)
        # With the answer brain on, without a translation key, translation
        # is off too: a reading that could not be asked for is not tried.
        self.assertFalse(_settings({
            **DEV_ENV, **NO_TRANSLATION_KEY,
            "VOINEY_LAB_ANSWER_BRAIN_ENABLED": "true",
        }).translation_enabled)

    def test_settings_made_in_code_keep_their_old_default(self) -> None:
        self.assertTrue(MultiBrainSettings(True).translation_enabled)
        self.assertFalse(MultiBrainSettings(False).translation_enabled)


class ReaderTranslationTests(unittest.TestCase):
    def test_with_the_answer_brain_off_the_step_is_still_translated(self) -> None:
        model, spoken = _turn(_settings(DEV_ENV), "이 단계 읽어줘")
        self.assertEqual(model.schemas(), ["protocol_step_reader_translation_v1"])
        self.assertEqual(spoken, ["자동 번역입니다. " + GOOD_READING])

    def test_without_a_translation_key_the_source_is_read_as_before(self) -> None:
        model, spoken = _turn(_settings({**DEV_ENV, **NO_TRANSLATION_KEY}), "이 단계 읽어줘")
        self.assertEqual(model.calls, [])
        self.assertEqual(spoken, [MINIPREP_STEPS[0]])


class VoiceTurnTests(unittest.TestCase):
    def test_with_the_answer_brain_off_the_voice_turn_does_not_call_it(self) -> None:
        model, _ = _turn(_settings(DEV_ENV), "이 단계 배경 지식 알려줘")
        self.assertNotIn("candidate_a_answer_brain_v1", model.schemas())
        # The Source role is unchanged.
        self.assertIn("candidate_a_source_brain_v1", model.schemas())

    def test_with_the_answer_brain_on_it_is_called_as_before(self) -> None:
        model, _ = _turn(
            _settings({**DEV_ENV, "VOINEY_LAB_ANSWER_BRAIN_ENABLED": "true"}),
            "이 단계 배경 지식 알려줘")
        self.assertIn("candidate_a_answer_brain_v1", model.schemas())
        self.assertIn("candidate_a_source_brain_v1", model.schemas())


class RevisionTranslationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = getattr(server_module.app.state, "revision_translation_runner", None)
        server_module.app.state.revision_translation_runner = None
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        server_module.app.state.revision_translation_runner = self.saved
        self.temp.cleanup()

    def _authorize(self, environment: dict[str, str]):
        fixture = object()
        with patch.dict(os.environ, {
            **environment, "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": self.temp.name,
        }), patch.object(
            server_module, "_revision_translation_fixture", return_value=fixture,
        ), patch.object(server_module, "_start_revision_translation") as start:
            server_module._translate_authorized_revision(object(), "protocol-1")
        return fixture, start

    def test_with_the_answer_brain_off_an_authorized_revision_is_translated(self) -> None:
        fixture, start = self._authorize(DEV_ENV)
        start.assert_called_once_with(fixture)

    def test_without_a_translation_key_nothing_starts(self) -> None:
        _, start = self._authorize({**DEV_ENV, **NO_TRANSLATION_KEY})
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
