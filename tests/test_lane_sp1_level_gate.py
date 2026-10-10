"""Lane SP1, decision 1 (2026-10-10): the wearer's voice level, measured once, gates what is heard.

The server's VAD only tells a voice from no voice; the STT writes down every
voice it is given, the neighbour's "다음" included. Decision 1 measures the
wearer's level once a session from a short sentence they read (never stored)
and takes an utterance more than a margin quieter for another person's: it
is not sent to speech recognition and not kept, and the session listens
again. The margin comes from the "민감도" setting; its default from the
synthetic bench. A session that measured nothing behaves as before.
"""

from __future__ import annotations

import array
import asyncio
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from voiney_lab.audio import FRAME_BYTES
from voiney_lab.language import Transcription
from voiney_lab.protocol import ProtocolError, parse_control
from voiney_lab.server import (
    ToolContext,
    VOICE_CALIBRATION_PROMPT,
    VOICE_CALIBRATION_SENTENCE,
    ListenerSession,
    LockedSender,
    _prompt_voice_calibration,
    run_turn_safely,
    voice_calibration_asked,
    voice_calibration_skipped,
    voice_socket,
)
from voiney_lab.vad import (
    LEVEL_FLOOR_DB,
    SENSITIVITY_MARGINS_DB,
    EndpointDetector,
    SpeakerLevelReference,
    TurnState,
    VadConfig,
    frame_level_db,
    level_reference_from,
    utterance_level_db,
    voiced_median_level_db,
)

SAMPLES = FRAME_BYTES // 2


def tone_frame(amplitude: float, phase: int = 0) -> bytes:
    """One 20 ms frame of a 440 Hz tone at ``amplitude`` of full scale."""

    values = array.array("h", (
        int(round(32767 * amplitude * math.sin(2 * math.pi * 440 * (phase * SAMPLES + i) / 16000)))
        for i in range(SAMPLES)))
    return values.tobytes()


def tone_db(amplitude: float) -> float:
    """The RMS level a sine at ``amplitude`` reads in dBFS."""

    return 20 * math.log10(amplitude / math.sqrt(2))


SILENT = bytes(FRAME_BYTES)


def voiced_if_loud(frame: bytes) -> bool:
    return any(frame)


#: A small VAD shape, as tests/test_own_speech_echo.py uses it: speech starts
#: at 2 voiced of 3 frames, ends after 2 silent frames, needs 2 voiced frames.
CONFIG = VadConfig(
    onset_voiced_frames=2, onset_window_frames=3, prefix_frames=3,
    endpoint_silence_frames=2, minimum_voiced_frames=2,
    maximum_utterance_frames=40, cooldown_ms=0,
    playback_onset_voiced_frames=2, playback_onset_window_frames=3,
    listening_onset_voiced_frames=2, listening_onset_window_frames=3,
    listening_resume_voiced_frames=2, listening_resume_window_frames=3,
)


def utterance(amplitude: float, voiced_frames: int = 6) -> bytes:
    return b"".join(tone_frame(amplitude, i) for i in range(voiced_frames)) + SILENT * 3


class LevelFunctionTests(unittest.TestCase):
    def test_a_silent_frame_sits_on_the_floor_and_a_tone_reads_its_rms(self) -> None:
        self.assertEqual(frame_level_db(SILENT), LEVEL_FLOOR_DB)
        self.assertAlmostEqual(frame_level_db(tone_frame(0.1)), tone_db(0.1), delta=0.15)
        self.assertAlmostEqual(frame_level_db(tone_frame(0.5)), tone_db(0.5), delta=0.15)
        self.assertEqual(frame_level_db(b""), LEVEL_FLOOR_DB)

    def test_the_utterance_level_is_the_median_of_the_voiced_frames_only(self) -> None:
        frames = [(tone_frame(0.1), True), (tone_frame(0.01), True), (tone_frame(0.3), True), (SILENT, False), (tone_frame(0.9), False)]
        self.assertAlmostEqual(voiced_median_level_db(frames), tone_db(0.1), delta=0.15)
        self.assertIsNone(voiced_median_level_db([(SILENT, False)]))
        pcm = b"".join(frame for frame, _ in frames)
        self.assertAlmostEqual(
            utterance_level_db(pcm, lambda frame: frame_level_db(frame) > tone_db(0.5)),
            tone_db(0.9), delta=0.15)
        self.assertIsNone(utterance_level_db(SILENT * 3, voiced_if_loud))

    def test_a_commit_carries_the_level_the_bytes_give_again(self) -> None:
        detector = EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True)
        committed = None
        for frame in [tone_frame(0.2, i) for i in range(6)] + [SILENT] * 3:
            result = detector.process(frame)
            if result.utterance is not None:
                committed = result
        self.assertIsNotNone(committed)
        self.assertAlmostEqual(committed.voiced_level_db, tone_db(0.2), delta=0.15)
        self.assertAlmostEqual(
            committed.voiced_level_db, utterance_level_db(committed.utterance, voiced_if_loud), delta=1e-9)

    def test_a_rejected_commit_has_no_level(self) -> None:
        detector = EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True)
        results = [detector.process(frame) for frame in [tone_frame(0.2), tone_frame(0.2, 1)] + [SILENT] * 3]
        # Two voiced frames of a three-frame window start speech but the
        # prefix keeps them; the commit still counts them -- so use a shape
        # the detector rejects: speech started by two voiced frames, then
        # silence, with minimum_voiced_frames above what was voiced.
        rejected = [r for r in results if r.rejected]
        if rejected:
            self.assertIsNone(rejected[0].voiced_level_db)
        detector = EndpointDetector(
            VadConfig(**{**CONFIG.__dict__, "minimum_voiced_frames": 5}),
            classifier=voiced_if_loud, listening_onset=True)
        results = [detector.process(frame) for frame in [tone_frame(0.2), tone_frame(0.2, 1)] + [SILENT] * 3]
        rejected = [r for r in results if r.rejected]
        self.assertEqual(len(rejected), 1)
        self.assertIsNone(rejected[0].voiced_level_db)


class ReferenceTests(unittest.TestCase):
    def test_the_margin_draws_the_line_and_no_level_passes(self) -> None:
        reference = SpeakerLevelReference(-20.0, 12.0)
        self.assertEqual(reference.threshold_db, -32.0)
        self.assertTrue(reference.accepts(-20.0))
        self.assertTrue(reference.accepts(-32.0))
        self.assertFalse(reference.accepts(-32.1))
        self.assertTrue(reference.accepts(None))

    def test_the_sensitivity_sets_the_margin(self) -> None:
        self.assertLess(SENSITIVITY_MARGINS_DB["high"], SENSITIVITY_MARGINS_DB["normal"])
        self.assertLess(SENSITIVITY_MARGINS_DB["normal"], SENSITIVITY_MARGINS_DB["low"])
        reference = level_reference_from(-24.0, "high")
        self.assertEqual((reference.margin_db, reference.sensitivity), (SENSITIVITY_MARGINS_DB["high"], "high"))
        low = reference.with_sensitivity("low")
        self.assertEqual((low.reference_db, low.margin_db, low.sensitivity), (-24.0, SENSITIVITY_MARGINS_DB["low"], "low"))
        unknown = reference.with_sensitivity("loud")
        self.assertEqual((unknown.margin_db, unknown.sensitivity), (SENSITIVITY_MARGINS_DB["normal"], "normal"))
        with self.assertRaises(ValueError):
            SpeakerLevelReference(float("nan"), 12.0)
        with self.assertRaises(ValueError):
            SpeakerLevelReference(-20.0, 99.0)


class ListenerGateTests(unittest.TestCase):
    """The listener drops a quieter voice before any STT and listens again."""

    def session(self, *, reference_db: float | None = -20.0) -> ListenerSession:
        session = ListenerSession(EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True))
        session.start()
        if reference_db is not None:
            session.set_level_reference(reference_db)
        return session

    def events(self, session: ListenerSession, pcm: bytes) -> list:
        out = []
        for i in range(0, len(pcm), FRAME_BYTES):
            out.extend(session.accept_chunk(pcm[i:i + FRAME_BYTES]))
        return out

    def test_the_wearers_own_level_is_heard_as_before(self) -> None:
        session = self.session()
        kinds = [e.kind for e in self.events(session, utterance(0.1))]
        self.assertEqual(kinds, ["speech.start", "speech.end"])
        self.assertEqual(session.state, TurnState.PROCESSING)

    def test_a_much_quieter_voice_is_ignored_and_the_session_listens_again(self) -> None:
        session = self.session()
        events = self.events(session, utterance(0.01))
        self.assertEqual([e.kind for e in events], ["speech.start", "speech.ignored"])
        ignored = events[-1]
        self.assertEqual(ignored.reason, "other_speaker_level")
        self.assertIsNone(ignored.result.utterance, "the audio is not kept")
        self.assertAlmostEqual(ignored.diagnostics["level_db"], tone_db(0.01), delta=0.2)
        self.assertEqual(ignored.diagnostics["reference_db"], -20.0)
        self.assertEqual(ignored.diagnostics["margin_db"], SENSITIVITY_MARGINS_DB["normal"])
        self.assertEqual(ignored.diagnostics["during_playback"], False)
        self.assertEqual(ignored.diagnostics["ignored_count"], 1)
        self.assertEqual(ignored.turn_id, 1)
        self.assertIsNone(session.active_turn_id)
        # Cooldown, then (cooldown_ms is 0 here) listening on the next chunk.
        self.assertIn(session.state, (TurnState.COOLDOWN, TurnState.IDLE))
        self.assertEqual(session.ignored_speech_in_last(60), 1)
        # The next utterance at the wearer's level is a turn again.
        kinds = [e.kind for e in self.events(session, utterance(0.1))]
        self.assertEqual(kinds, ["speech.start", "speech.end"])
        self.assertEqual([e.turn_id for e in self.events(session, b"")], [])

    def test_the_line_is_the_reference_minus_the_margin(self) -> None:
        margin = SENSITIVITY_MARGINS_DB["normal"]
        just_inside = 10 ** ((-20.0 - margin + 0.5) / 20) * math.sqrt(2)
        just_outside = 10 ** ((-20.0 - margin - 0.5) / 20) * math.sqrt(2)
        session = self.session()
        self.assertEqual([e.kind for e in self.events(session, utterance(just_inside))][-1], "speech.end")
        session = self.session()
        self.assertEqual([e.kind for e in self.events(session, utterance(just_outside))][-1], "speech.ignored")

    def test_without_a_measurement_nothing_is_ignored(self) -> None:
        session = self.session(reference_db=None)
        self.assertEqual([e.kind for e in self.events(session, utterance(0.001))], ["speech.start", "speech.end"])

    def test_the_calibration_sentence_itself_is_never_ignored(self) -> None:
        session = self.session()
        session.arm_voice_calibration()
        self.assertEqual([e.kind for e in self.events(session, utterance(0.001))], ["speech.start", "speech.end"])

    def test_a_new_session_measures_again(self) -> None:
        session = self.session()
        session.ignored_speech_at.append(session.clock())
        session.start()
        self.assertIsNone(session.level_reference)
        self.assertEqual(session.ignored_speech_in_last(60), 0)
        session = self.session()
        session.stop()
        self.assertIsNone(session.level_reference)

    def test_the_sensitivity_changes_the_margin_of_a_measured_reference(self) -> None:
        session = self.session()
        self.assertEqual(session.set_speaker_sensitivity("low"), "low")
        self.assertEqual(session.level_reference.margin_db, SENSITIVITY_MARGINS_DB["low"])
        self.assertEqual(session.set_speaker_sensitivity("nonsense"), "normal")
        self.assertEqual(session.level_reference.reference_db, -20.0)

    def test_a_quieter_voice_while_the_agent_speaks_does_not_interrupt_it(self) -> None:
        session = self.session()
        session.active_turn_id = 1
        session.turn_generations[1] = session.generation
        session.detector.state = TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        events = self.events(session, utterance(0.01))
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, ["barge_in_candidate", "barge_in_rejected", "speech.ignored"])
        self.assertEqual(events[1].reason, "other_speaker_level")
        self.assertEqual(events[2].diagnostics["during_playback"], True)
        self.assertEqual(session.state, TurnState.AGENT_SPEAKING)
        # The wearer still interrupts.
        events = self.events(session, utterance(0.1))
        self.assertEqual([e.kind for e in events], ["barge_in_candidate", "barge_in_audio_ready"])


class CalibrationWordsTests(unittest.TestCase):
    def test_the_request_and_the_skip_are_whole_commands(self) -> None:
        for words in ("목소리 다시 맞춰 줘", "목소리 맞춰 줘", "내 목소리 다시 맞춰 주세요", "목소리 다시 재 줘", "목소리 등록해 줘"):
            self.assertTrue(voice_calibration_asked(words), words)
        for words in ("목소리가 어때?", "목소리 다시 맞춰 줘?", "다음", "목소리 크게 해 줘"):
            self.assertFalse(voice_calibration_asked(words), words)
        for words in ("건너뛰기", "건너뛸게", "넘어가", "괜찮아요", "아니", "skip"):
            self.assertTrue(voice_calibration_skipped(words), words)
        for words in ("다음", "목소리를 맞출게요. 지금 이 문장을 평소처럼 읽어 주세요.", "응"):
            self.assertFalse(voice_calibration_skipped(words), words)


class ControlTests(unittest.TestCase):
    def test_the_screen_control_is_parsed_strictly(self) -> None:
        self.assertEqual(
            parse_control(json.dumps({"type": "client.voice_calibration", "action": "start", "configuration_id": 41, "generation": 1, "extra": 1})),
            {"type": "client.voice_calibration", "action": "start", "configuration_id": 41, "generation": 1})
        self.assertEqual(parse_control(json.dumps({"type": "client.voice_calibration", "action": "skip", "configuration_id": 41, "generation": 0}))["action"], "skip")
        for bad in (
            {"type": "client.voice_calibration", "action": "measure", "configuration_id": 41, "generation": 1},
            {"type": "client.voice_calibration", "action": "start", "configuration_id": "41", "generation": 1},
            {"type": "client.voice_calibration", "action": "start", "configuration_id": 41, "generation": -1},
        ):
            with self.assertRaises(ProtocolError):
                parse_control(json.dumps(bad))


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


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class CalibrationTurnTests(unittest.TestCase):
    """The sentence read is a measurement, not a command: no routing, nothing changes."""

    def turn(self, heard: str, *, level: float | None, pending: bool = True, reference: float | None = None) -> tuple[list[dict], ListenerSession]:
        context = ToolContext(Path("/unused/offline-catalog"), None, "ko", "test_only")
        session = ListenerSession(tool_context=context, clock=Clock())
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp1")
        if reference is not None:
            session.set_level_reference(reference)
        if pending:
            session.arm_voice_calibration()
        session.active_turn_id = 2
        session.turn_generations[2] = session.generation
        session.turn_committed_at[2] = session.clock()
        session.detector.state = TurnState.PROCESSING
        socket = Socket()
        with patch("voiney_lab.server.transcribe", return_value=Transcription(heard, "ko")), patch(
            "voiney_lab.server.synthesize", return_value=b"\0\0" * 320,
        ), self.assertLogs("voiney_lab", level="INFO") as logs:
            asyncio.run(run_turn_safely(socket, session, b"\0\0" * 160, 2, 25, 20, voiced_level_db=level))
        self.logs = "\n".join(logs.output)
        return socket.text, session

    def test_the_sentence_read_sets_the_reference_and_is_not_routed(self) -> None:
        sent, session = self.turn(VOICE_CALIBRATION_SENTENCE, level=-25.0)
        kinds = [m["type"] for m in sent]
        self.assertNotIn("turn.route_decision", kinds)
        calibration = next(m for m in sent if m["type"] == "speaker.calibration")
        self.assertEqual(calibration["state"], "measured")
        self.assertEqual(calibration["level_db"], -25.0)
        self.assertEqual(calibration["margin_db"], SENSITIVITY_MARGINS_DB["normal"])
        self.assertEqual(calibration["threshold_db"], -25.0 - SENSITIVITY_MARGINS_DB["normal"])
        self.assertEqual(session.level_reference.reference_db, -25.0)
        self.assertFalse(session.calibration_pending)
        reply = next(m for m in sent if m["type"] == "reply.delta")
        self.assertIn("목소리를 맞췄어요", reply["text"])
        done = next(m for m in sent if m["type"] == "turn.done")
        self.assertEqual(done["route"], "voice_calibration")
        self.assertIn("voice calibration measured level_db=-25.0", self.logs)
        self.assertNotIn(VOICE_CALIBRATION_SENTENCE, self.logs, "no transcript in the log")

    def test_a_skip_word_leaves_no_reference(self) -> None:
        sent, session = self.turn("건너뛰기", level=-25.0)
        calibration = next(m for m in sent if m["type"] == "speaker.calibration")
        self.assertEqual(calibration["state"], "skipped")
        self.assertIsNone(session.level_reference)
        self.assertFalse(session.calibration_pending)
        self.assertIn("건너뛸게요", next(m for m in sent if m["type"] == "reply.delta")["text"])

    def test_no_voiced_level_asks_again(self) -> None:
        sent, session = self.turn(VOICE_CALIBRATION_SENTENCE, level=None)
        self.assertEqual(next(m for m in sent if m["type"] == "speaker.calibration")["state"], "prompted")
        self.assertTrue(session.calibration_pending)
        self.assertIsNone(session.level_reference)

    def test_asking_by_voice_arms_the_next_sentence(self) -> None:
        sent, session = self.turn("목소리 다시 맞춰 줘", level=-20.0, pending=False, reference=-30.0)
        calibration = next(m for m in sent if m["type"] == "speaker.calibration")
        self.assertEqual(calibration["state"], "prompted")
        self.assertEqual(calibration["sentence"], VOICE_CALIBRATION_SENTENCE)
        self.assertTrue(session.calibration_pending)
        self.assertEqual(session.level_reference.reference_db, -30.0, "the old reference stands until the new sentence")
        self.assertEqual(next(m for m in sent if m["type"] == "reply.delta")["text"], VOICE_CALIBRATION_PROMPT)
        self.assertNotIn("turn.route_decision", [m["type"] for m in sent])


class PromptTests(unittest.TestCase):
    """The prompt from the screen is said as the server's own turn, then listened for."""

    def test_the_prompt_is_said_when_quiet_and_arms_the_measurement(self) -> None:
        session = ListenerSession(clock=Clock())
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp1")
        socket = Socket()
        with patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320 * 10):
            said = asyncio.run(_prompt_voice_calibration(LockedSender(socket), session))
        self.assertTrue(said)
        kinds = [m["type"] for m in socket.text]
        self.assertEqual(kinds[0], "speaker.calibration")
        self.assertEqual(socket.text[0]["state"], "prompting")
        words = next(m for m in socket.text if m["type"] == "server.words")
        self.assertEqual((words["words_kind"], words["text"]), ("voice_calibration", VOICE_CALIBRATION_PROMPT))
        self.assertGreaterEqual(words["turn_id"], 2_000_000_301)
        self.assertEqual(socket.text[-1]["type"], "speaker.calibration")
        self.assertEqual(socket.text[-1]["state"], "prompted")
        self.assertTrue(session.calibration_pending)
        self.assertEqual(session.state, TurnState.AGENT_SPEAKING)
        done = next(m for m in socket.text if m["type"] == "turn.done")
        self.assertEqual(done["route"], "server_words")

    def test_the_prompt_is_not_said_into_a_closed_session(self) -> None:
        session = ListenerSession(clock=Clock())
        socket = Socket()
        with patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320) as tts:
            said = asyncio.run(_prompt_voice_calibration(LockedSender(socket), session))
        self.assertFalse(said)
        self.assertFalse(session.calibration_pending)
        tts.assert_not_called()


class SocketTests(unittest.TestCase):
    """Through voice_socket: the ignored voice reaches the page as an event, never the STT."""

    def _run(self, session: ListenerSession, messages: list) -> tuple[list[dict], str]:
        from types import SimpleNamespace
        socket = Socket(messages + [{"type": "websocket.disconnect", "code": 1000}])
        with patch("voiney_lab.server.ListenerSession", return_value=session), patch(
            "voiney_lab.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch("voiney_lab.server.VadConfig.from_settings", return_value=CONFIG), patch(
            "voiney_lab.server.transcribe", return_value=Transcription("다음", "ko"),
        ) as stt, patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320), self.assertLogs(
            "voiney_lab", level="INFO",
        ) as logs:
            asyncio.run(voice_socket(socket))
        self.stt = stt
        return socket.text, "\n".join(logs.output)

    def test_a_quieter_voice_is_an_event_and_no_stt_call(self) -> None:
        session = ListenerSession(EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True))
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp1")
        session.set_level_reference(-20.0)
        sent, logs = self._run(session, [{"bytes": utterance(0.01)}])
        ignored = next(m for m in sent if m["type"] == "speech.ignored")
        self.assertEqual(ignored["reason"], "other_speaker_level")
        self.assertEqual(ignored["reference_db"], -20.0)
        self.assertAlmostEqual(ignored["level_db"], tone_db(0.01), delta=0.2)
        self.assertEqual(ignored["state"], "COOLDOWN")
        self.assertNotIn("speech.end", [m["type"] for m in sent])
        self.stt.assert_not_called()
        self.assertIn("speech.ignored reason=other_speaker_level level_db=", logs)

    def test_the_skip_control_answers_the_prompt(self) -> None:
        session = ListenerSession(EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True))
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp1")
        session.arm_voice_calibration()
        sent, logs = self._run(session, [{"text": json.dumps({
            "type": "client.voice_calibration", "action": "skip",
            "configuration_id": 41, "generation": session.generation})}])
        self.assertEqual(next(m for m in sent if m["type"] == "speaker.calibration")["state"], "skipped")
        self.assertFalse(session.calibration_pending)
        self.assertIn("voice calibration skipped on screen pending=True", logs)

    def test_a_stale_control_is_refused(self) -> None:
        session = ListenerSession(EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True))
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp1")
        session.arm_voice_calibration()
        sent, logs = self._run(session, [{"text": json.dumps({
            "type": "client.voice_calibration", "action": "skip",
            "configuration_id": 40, "generation": session.generation})}])
        self.assertNotIn("speaker.calibration", [m["type"] for m in sent])
        self.assertIn("client.voice_calibration rejected action=skip configuration_id=40", logs)


if __name__ == "__main__":
    unittest.main()
