"""Nothing scripted is said while waiting (lane XO, decision 7 of 2026-10-05).

A fixed sentence read out while a turn waits ("요청을 확인하고 있습니다.")
sounds like a script being read, and on a laptop speaker it came back as the
researcher's turn. By default a waiting turn has the tone and the screen only;
``VOINEY_LAB_CASCADE_FILLER_STATUS_SPEECH_ENABLED=true`` brings the status
sentence back (tests/test_cascade_filler_status.py covers that path).

A protocol answer with nothing more specific to say used to speak a sentence
about itself ("활성 프로토콜이 확인하는 내용을 먼저 정리했습니다. 추가 설명은
…"); it now reads the step as the screen shows it (a person's decision).
"""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.protocol_vocabulary_support import build_fixture
from tests.test_cascade_filler_tone import Socket
from voiney_lab.cascade_filler import (
    FILLER_STATUS_SPEECH_ENV,
    cascade_filler_status_speech_enabled,
)
from voiney_lab.configuration import ConfigurationError
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.language import Transcription
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import ListenerSession, run_turn_safely
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState


class StatusSpeechSettingTests(unittest.TestCase):
    def test_off_unless_asked_for(self) -> None:
        self.assertFalse(cascade_filler_status_speech_enabled({}))
        self.assertFalse(cascade_filler_status_speech_enabled({FILLER_STATUS_SPEECH_ENV: "false"}))
        self.assertTrue(cascade_filler_status_speech_enabled({FILLER_STATUS_SPEECH_ENV: "true"}))
        with self.assertRaises(ConfigurationError):
            cascade_filler_status_speech_enabled({FILLER_STATUS_SPEECH_ENV: "sometimes"})

    def test_a_slow_turn_hears_the_tone_and_no_sentence(self) -> None:
        session = ListenerSession(tool_context=ToolContext(
            Path("/trusted/catalog.sqlite"), None, "ko", "test_only"))
        session.active = True
        session.language_mode = "auto"
        socket = Socket()

        def slow_empty_transcript(*_args, **_kwargs):
            time.sleep(0.6)  # still transcribing well past the status delay
            return Transcription("", "ko")

        with patch.dict("os.environ", {
            "VOINEY_LAB_CASCADE_FILLER_MODE": "tone",
            "VOINEY_LAB_CASCADE_FILLER_DELAY_MS": "100",
            "VOINEY_LAB_CASCADE_FILLER_STATUS_DELAY_MS": "250",
        }), patch(
            "voiney_lab.server.transcribe", side_effect=slow_empty_transcript,
        ), patch(
            "voiney_lab.server.synthesize", return_value=b"\x00\x00" * 320,
        ) as tts:
            # The default: the setting is absent (patch.dict restores it).
            os.environ.pop(FILLER_STATUS_SPEECH_ENV, None)
            session.active_turn_id = 1
            session.detector.state = TurnState.PROCESSING
            asyncio.run(run_turn_safely(socket, session, b"\0\0", 1, 1))
        tts.assert_not_called()
        self.assertEqual(
            [item["turn_id"] for item in socket.text if item["type"] == "filler.tone"], [1])
        self.assertFalse([
            item for item in socket.text
            if item["type"] == "turn.filler" and item.get("cue") == "status"
        ])
        self.assertFalse([item for item in socket.text if item["type"] == "filler.audio.start"])


class StepReadingInsteadOfAScriptTests(unittest.TestCase):
    STEPS = (
        "1 Wash the column with 500 µL of Buffer 1 and spin it for 1 minute. Discard the flow-through.",
        "2 Store the tube at 4 degrees.",
    )

    def test_the_answer_reads_the_step_not_a_sentence_about_itself(self) -> None:
        fixture = build_fixture(
            protocol_id="step-reading-test", title="Fictional reading protocol",
            steps=self.STEPS,
        )
        session = CuratedProtocolSession(fixture)
        session.activate_configured()
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        plan = route_curated_runtime_turn(
            session, "왜 이렇게 해?", turn_id=2, language="ko",
            configuration_id=1, generation=1,
        ).plan
        self.assertNotIn("활성 프로토콜이 확인하는 내용을", plan.speech_text)
        self.assertNotIn("분리해 안내합니다", plan.speech_text)
        self.assertTrue(plan.speech_text.startswith("1단계 내용입니다. "), plan.speech_text)
        self.assertIn("Wash the column with 500 µL of Buffer 1", plan.speech_text)
        self.assertTrue(plan.speech_text.endswith("나머지는 화면에 있습니다."), plan.speech_text)
        self.assertFalse(plan.state_changed)


if __name__ == "__main__":
    unittest.main()
