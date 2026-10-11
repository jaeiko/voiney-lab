"""Lane SP1, decision 5 (2026-10-10): a machine sound the VAD took for speech is passed over in silence.

A vortex mixer, an alarm or a breath can start the VAD; the STT then writes
nothing, or a filler syllable or two. The empty case and the provider's own
non-speech labels were already passed over without an answer (checked here
so it stays so); the filler case was answered as an off-topic question
("현재 진행 중인 실험 절차와 관련 실험실 자료에 대한 질문을 도와드릴 수
있어요") -- a reply to a machine -- and now is not. "응", "네", "아니" and
every real word are not fillers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voiney_lab.language import Transcription
from voiney_lab.server import ListenerSession, ToolContext, run_turn_safely, transcript_is_filler_only, voice_socket
from voiney_lab.vad import EndpointDetector, TurnState, VadConfig


class FillerWordsTests(unittest.TestCase):
    def test_no_words_and_fillers_are_no_words(self) -> None:
        for text in ("", "   ", ".", "...", "음", "음.", "어 어", "흠", "아아", "음 음 음", "um", "Hmm.", "…", "?"):
            self.assertTrue(transcript_is_filler_only(text), repr(text))

    def test_answers_and_words_are_words(self) -> None:
        for text in ("응", "네", "아니", "음 완료", "다음", "아 네", "어 다음", "완료", "yes", "1", "a", "타이머 시작"):
            self.assertFalse(transcript_is_filler_only(text), repr(text))


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Socket:
    def __init__(self, messages=()) -> None:
        self.text: list[dict] = []
        self.messages = list(messages)

    async def accept(self):
        return None

    async def receive(self):
        return self.messages.pop(0)

    async def send_text(self, value):
        self.text.append(json.loads(value))

    async def send_bytes(self, value):
        return None

    async def close(self, **kwargs):
        return None


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


class OrdinaryTurnTests(unittest.TestCase):
    def turn(self, heard: str, **transcription) -> tuple[list[dict], ListenerSession, str]:
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        session = ListenerSession(tool_context=context, clock=Clock())
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp1")
        session.active_turn_id = 2
        session.turn_generations[2] = session.generation
        session.turn_committed_at[2] = session.clock()
        session.detector.state = TurnState.PROCESSING
        socket = Socket()
        lines = _Lines()
        logger = logging.getLogger("voiney_lab")
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(lines)
        self.addCleanup(logger.removeHandler, lines)
        self.addCleanup(logger.setLevel, previous_level)
        with patch(
            "voiney_lab.server.transcribe", return_value=Transcription(heard, "ko", **transcription),
        ), patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320) as tts:
            asyncio.run(run_turn_safely(socket, session, b"\0\0" * 160, 2, 25, 20))
        self.tts = tts
        return socket.text, session, "\n".join(lines.lines)

    def assert_passed_over(self, sent: list[dict], session: ListenerSession, reason: str) -> None:
        kinds = [m["type"] for m in sent]
        rejected = next(m for m in sent if m["type"] == "speech.rejected")
        self.assertEqual(rejected["reason"], reason)
        self.assertNotIn("reply.delta", kinds, "something was said back")
        self.assertNotIn("transcript", kinds)
        self.assertNotIn("turn.route_decision", kinds, "the sound was routed")
        self.tts.assert_not_called()
        self.assertEqual(session.state, TurnState.COOLDOWN)

    def test_nothing_written_is_passed_over_as_before(self) -> None:
        sent, session, _logs = self.turn("")
        self.assert_passed_over(sent, session, "empty_transcript")

    def test_the_providers_non_speech_label_is_passed_over_as_before(self) -> None:
        sent, session, _logs = self.turn("(기침)")
        self.assert_passed_over(sent, session, "non_lexical_event")
        sent, session, _logs = self.turn("소음")
        self.assert_passed_over(sent, session, "non_lexical_event")

    def test_a_filler_is_passed_over_in_silence(self) -> None:
        sent, session, logs = self.turn("음.")
        self.assert_passed_over(sent, session, "filler_only")
        self.assertIn("speech.rejected reason=filler_only", logs)
        self.assertNotIn("음.", logs, "no transcript in the log")
        sent, session, _logs = self.turn("어 어")
        self.assert_passed_over(sent, session, "filler_only")

    def test_a_short_answer_is_still_a_turn(self) -> None:
        sent, _session, _logs = self.turn("응")
        kinds = [m["type"] for m in sent]
        self.assertNotIn("speech.rejected", kinds)
        self.assertIn("transcript", kinds)


class BargeInTests(unittest.TestCase):
    """A filler heard over the agent's answer does not interrupt it either."""

    CONFIG = VadConfig(
        onset_voiced_frames=2, onset_window_frames=3, prefix_frames=3,
        endpoint_silence_frames=2, minimum_voiced_frames=2,
        maximum_utterance_frames=30, cooldown_ms=0,
        playback_onset_voiced_frames=2, playback_onset_window_frames=3,
    )

    def _run(self, heard: str) -> list[dict]:
        from collections import deque

        decisions = deque([False, True, True, True, False, False])

        def classifier(_frame):
            return decisions.popleft() if decisions else False

        session = ListenerSession(EndpointDetector(self.CONFIG, classifier=lambda _: False))
        session.start()
        session.active_turn_id = 1
        session.turn_generations[1] = session.generation
        session.detector.state = TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        session._interrupt_detector = EndpointDetector(self.CONFIG, classifier=classifier)
        frame = bytes([11]) * 640
        socket = Socket([{"bytes": frame * 6}, {"type": "websocket.disconnect", "code": 1000}])
        with patch("voiney_lab.server.ListenerSession", return_value=session), patch(
            "voiney_lab.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch("voiney_lab.server.VadConfig.from_settings", return_value=self.CONFIG), patch(
            "voiney_lab.server.transcribe", return_value=Transcription(heard, "ko"),
        ), patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320):
            asyncio.run(voice_socket(socket))
        return socket.text

    def test_a_filler_over_the_answer_is_no_interruption(self) -> None:
        sent = self._run("음")
        kinds = [m["type"] for m in sent]
        self.assertNotIn("barge_in_committed", kinds)
        self.assertEqual(next(m for m in sent if m["type"] == "barge_in_rejected")["reason"], "filler_only")

    def test_words_over_the_answer_still_interrupt(self) -> None:
        sent = self._run("잠깐 멈춰")
        self.assertIn("barge_in_committed", [m["type"] for m in sent])


if __name__ == "__main__":
    unittest.main()
