"""The server does not take its own voice for the researcher's (lane XO, 6b).

On 2026-10-05, with ElevenLabs STT, a laptop speaker's sound came back
through its microphone: the agent's sentence "활성 프로토콜이 확인하는 내용을
먼저 정리했습니다. 추가 설명은 …" was transcribed, committed as a barge-in
(playback stopped, "중단됨"), and answered. The xAI request carries
vad_threshold and an empty transcript sends a candidate back to playback;
ElevenLabs and Google take no such threshold.

The server now remembers what it has just said. A transcript heard during
playback, or within ECHO_TAIL_SECONDS after it, that is mostly one of those
sentences is dropped as an echo and logged ("메아리로 버림"); nothing changes.
Short transcripts, and transcripts with a pause or end word the sentence did
not have, are kept. The threshold comes from ~/reports/lane_xo_echo_calibration.md.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_server_helpers import Decisions, frame
from voiney_lab.language import Transcription
from voiney_lab.server import (
    ECHO_MEMORY_SECONDS,
    ECHO_MIN_CHARACTERS,
    ECHO_OVERLAP_THRESHOLD,
    ECHO_TAIL_SECONDS,
    ListenerSession,
    _SPEAKING_SESSION,
    echo_overlap,
    said,
    voice_socket,
)
from voiney_lab.vad import EndpointDetector, TurnState, VadConfig

SAID = (
    "활성 프로토콜이 확인하는 내용을 먼저 정리했습니다. "
    "추가 설명은 검증 가능한 읽기 전용 근거가 있을 때만 분리해 안내합니다."
)
#: As the researcher's microphone returned it on 2026-10-05.
HEARD = "활성 프로토콜이 확인하는 내용을 먼저 보겠습니다. 추가 설명은 검증 가능한 근거가 있을 때만"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class EchoMatchTests(unittest.TestCase):
    def test_the_incident_transcript_is_an_echo_of_what_was_said(self) -> None:
        self.assertGreaterEqual(echo_overlap(HEARD, SAID), ECHO_OVERLAP_THRESHOLD)

    def test_a_researcher_reusing_the_agents_words_is_not(self) -> None:
        for said, heard in (
            ("3단계부터 6단계까지의 절차 요약입니다. 3단계: 밴드를 Solution A 500 µL로 세척합니다.",
             "3단계부터 6단계까지 요약해줘"),
            ("현재 4단계를 완료했고 다음 단계로 이동할지 명확히 말씀해 주세요.",
             "어… 음… 다음 단계로 넘어가 주세요"),
        ):
            with self.subTest(heard=heard):
                self.assertLess(echo_overlap(heard, said), ECHO_OVERLAP_THRESHOLD)

    def test_the_settings_are_the_calibrated_ones(self) -> None:
        self.assertEqual((ECHO_MIN_CHARACTERS, ECHO_OVERLAP_THRESHOLD), (12, 0.8))


class OwnSpeechMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.session = ListenerSession(clock=self.clock)
        self.session.remember_spoken(SAID)

    def test_during_playback_the_echo_is_found(self) -> None:
        echo = self.session.own_speech_echo(HEARD)
        self.assertIsNotNone(echo)
        self.assertEqual(echo.spoken, SAID)

    def test_a_short_reply_is_never_an_echo(self) -> None:
        self.session.remember_spoken("다음 단계로 넘어갈까요?")
        for heard in ("다음 단계", "네", "잠깐만", "다음 단계로 넘어갈까요"):
            with self.subTest(heard=heard):
                self.assertIsNone(self.session.own_speech_echo(heard))

    def test_a_pause_word_the_agent_did_not_say_keeps_the_transcript(self) -> None:
        self.assertIsNone(self.session.own_speech_echo("잠깐 멈춰 " + HEARD))
        self.assertIsNone(self.session.own_speech_echo(HEARD + " 실험 종료"))
        # A control word the agent itself said is still its own voice.
        paused = "일시정지했어요. '다시 시작'이라고 하시면 이어서 할게요."
        self.session.remember_spoken(paused)
        self.assertIsNotNone(self.session.own_speech_echo("일시정지했어요 다시 시작이라고 하시면 이어서"))

    def test_an_ordinary_turn_is_checked_only_just_after_playback(self) -> None:
        # No playback has ended: an ordinary turn is not an echo.
        self.assertIsNone(self.session.own_speech_echo(HEARD, heard_from=self.clock.now))
        self.session.last_playback_ended_at = self.clock.now
        self.assertIsNotNone(self.session.own_speech_echo(
            HEARD, heard_from=self.clock.now + ECHO_TAIL_SECONDS))
        self.assertIsNone(self.session.own_speech_echo(
            HEARD, heard_from=self.clock.now + ECHO_TAIL_SECONDS + 0.5))

    def test_old_sentences_are_forgotten(self) -> None:
        self.clock.now += ECHO_MEMORY_SECONDS + 1
        self.assertIsNone(self.session.own_speech_echo(HEARD))

    def test_said_remembers_for_the_session_bound_to_the_connection(self) -> None:
        session = ListenerSession(clock=self.clock)
        token = _SPEAKING_SESSION.set(session)
        try:
            self.assertEqual(said("안녕하세요 반갑습니다"), "안녕하세요 반갑습니다")
        finally:
            _SPEAKING_SESSION.reset(token)
        self.assertEqual([text for _at, text in session.spoken_recently], ["안녕하세요 반갑습니다"])
        # With no session bound, nothing is kept and the text passes through.
        self.assertEqual(said("다른 문장"), "다른 문장")
        self.assertEqual(len(session.spoken_recently), 1)

    def test_every_tts_call_remembers_its_sentence(self) -> None:
        import inspect

        import voiney_lab.server as server

        source = inspect.getsource(server)
        self.assertNotRegex(source, r"to_thread\(\s*synthesize,(?!\s*said\()")


class Socket:
    def __init__(self, messages) -> None:
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


class BargeInEchoTests(unittest.TestCase):
    """Through voice_socket, as tests/test_server_helpers.py drives barge-in."""

    CONFIG = VadConfig(
        onset_voiced_frames=2, onset_window_frames=3, prefix_frames=3,
        endpoint_silence_frames=2, minimum_voiced_frames=2,
        maximum_utterance_frames=30, cooldown_ms=0,
        playback_onset_voiced_frames=2, playback_onset_window_frames=3,
    )

    def _run(self, heard: str) -> tuple[list[dict], ListenerSession]:
        session = ListenerSession(EndpointDetector(self.CONFIG, classifier=lambda _: False))
        session.start()
        session.active_turn_id = 1
        session.turn_generations[1] = session.generation
        session.detector.state = TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        session.remember_spoken(SAID)
        session._interrupt_detector = EndpointDetector(
            self.CONFIG, classifier=Decisions([False, True, True, True, False, False]))
        socket = Socket([{"bytes": frame(11) * 6}, {"type": "websocket.disconnect", "code": 1000}])
        with patch("voiney_lab.server.ListenerSession", return_value=session), patch(
            "voiney_lab.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch("voiney_lab.server.VadConfig.from_settings", return_value=self.CONFIG), patch(
            "voiney_lab.server.transcribe", return_value=Transcription(heard, "ko"),
        ), patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320), self.assertLogs(
            "voiney_lab", level="INFO",
        ) as logs:
            asyncio.run(voice_socket(socket))
        self.logs = "\n".join(logs.output)
        return socket.text, session

    def test_the_agents_own_voice_does_not_interrupt_it(self) -> None:
        sent, _session = self._run(HEARD)
        kinds = [item["type"] for item in sent]
        self.assertNotIn("barge_in_committed", kinds)
        self.assertNotIn("assistant.interrupted", kinds)
        rejected = next(item for item in sent if item["type"] == "barge_in_rejected")
        self.assertEqual(rejected["reason"], "self_echo")
        self.assertIn("메아리로 버림", self.logs)

    def test_a_researcher_talking_over_it_still_interrupts(self) -> None:
        sent, _session = self._run("잠깐 멈춰 " + HEARD)
        kinds = [item["type"] for item in sent]
        self.assertIn("barge_in_committed", kinds)
        self.assertNotIn("메아리로 버림", self.logs)


class OrdinaryTurnEchoTests(unittest.TestCase):
    def test_an_echo_just_after_playback_is_dropped_without_a_turn(self) -> None:
        from voiney_lab.server import run_turn_safely

        clock = Clock()
        session = ListenerSession(clock=clock)
        session.active = True
        session.remember_spoken(SAID)
        session.last_playback_ended_at = clock.now
        clock.now += 1.0
        session.active_turn_id = 2
        session.turn_generations[2] = session.generation
        session.turn_committed_at[2] = clock.now
        session.detector.state = TurnState.PROCESSING
        socket = Socket([])
        with patch(
            "voiney_lab.server.transcribe", return_value=Transcription(HEARD, "ko"),
        ), patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320) as tts, self.assertLogs(
            "voiney_lab", level="INFO",
        ) as logs:
            asyncio.run(run_turn_safely(socket, session, b"\0\0" * 160, 2, 25))
        kinds = [item["type"] for item in socket.text]
        rejected = next(item for item in socket.text if item["type"] == "speech.rejected")
        self.assertEqual(rejected["reason"], "self_echo")
        self.assertNotIn("transcript", kinds)
        self.assertNotIn("reply.delta", kinds)
        tts.assert_not_called()
        self.assertEqual(session.state, TurnState.COOLDOWN)
        self.assertIn("메아리로 버림", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
