"""Lane SP2, decision 2 (2026-10-13): a room too loud for the level line is named, once.

Lane HEAR, decision 9 (2026-10-25): it is no longer said during a run --
the condition is recorded as a code and numbers only (``noise.condition
kind=floor_near_line``); the tests of when it is due stand as they were.

The listener measures the room's noise floor all session (see
test_lane_sp2_noise_floor.py). When that floor comes near the wearer's
measured level -- the point where the level line stops ending utterances,
less than LEVEL_ENDPOINT_FLOOR_GAP_DB under the line -- the server says, as a
suggestion only, "주변이 시끄러워요. 붐 마이크나 시끄러운 곳 모드를 권해요."
It shares lane SP1's ten-minute gap with that lane's own suggestion, opens no
question and changes no setting. A session that measured nothing is never
told.
"""

from __future__ import annotations

import array
import asyncio
import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from voiney_lab.audio import FRAME_BYTES
from voiney_lab.language import Transcription
from voiney_lab.server import (
    AMBIENT_SUGGESTION_GAP_SECONDS,
    AMBIENT_SUGGESTION_IGNORED_COUNT,
    LEVEL_ENDPOINT_FLOOR_GAP_DB,
    ListenerSession,
    _record_noise_condition,
    voice_socket,
)
from voiney_lab.vad import (
    NOISE_FLOOR_MINIMUM_FRAMES,
    SENSITIVITY_MARGINS_DB,
    EndpointDetector,
    VadConfig,
    frame_level_db,
)

SAMPLES = FRAME_BYTES // 2


def tone_frame(amplitude: float, phase: int = 0) -> bytes:
    values = array.array("h", (
        int(round(32767 * amplitude * math.sin(2 * math.pi * 440 * (phase * SAMPLES + i) / 16000)))
        for i in range(SAMPLES)))
    return values.tobytes()


#: A steady room sound at about -36 dBFS that the stand-in VAD never calls a voice.
ROOM = tone_frame(0.022)
ROOM_DB = frame_level_db(ROOM)
REFERENCE_DB = -28.0
#: Where the room is near enough: the line (reference minus the normal
#: margin) less the gap.
NEAR_DB = REFERENCE_DB - SENSITIVITY_MARGINS_DB["normal"] - LEVEL_ENDPOINT_FLOOR_GAP_DB


def voiced_above_minus_30(frame: bytes) -> bool:
    return frame_level_db(frame) >= -30.0


CONFIG = VadConfig(
    onset_voiced_frames=2, onset_window_frames=3, prefix_frames=3,
    endpoint_silence_frames=2, minimum_voiced_frames=2,
    maximum_utterance_frames=40, cooldown_ms=0,
    playback_onset_voiced_frames=2, playback_onset_window_frames=3,
    listening_onset_voiced_frames=2, listening_onset_window_frames=3,
    listening_resume_voiced_frames=2, listening_resume_window_frames=3,
)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _Curated:
    """Just what the server needs of a curated session for a suggestion and the timer watcher."""

    def __init__(self) -> None:
        self.ambient_mode = "quiet"
        self.ambient_suggestion_open = False

    def open_ambient_suggestion(self) -> None:
        self.ambient_suggestion_open = True

    def reset(self) -> None:
        return None

    def next_timer_notice_due(self):
        return None


class SuggestionDueTests(unittest.TestCase):
    def session(self, *, floor_db: float | None, reference_db: float | None = REFERENCE_DB) -> ListenerSession:
        session = ListenerSession(clock=Clock())
        session.start()
        session.curated_protocol_session = _Curated()
        if reference_db is not None:
            session.set_level_reference(reference_db)
        if floor_db is not None:
            for _ in range(NOISE_FLOOR_MINIMUM_FRAMES):
                session.noise_floor.add(floor_db, True)
        return session

    def test_a_floor_near_the_wearer_makes_it_due(self) -> None:
        self.assertTrue(self.session(floor_db=-35.0).noise_floor_suggestion_due())
        session = self.session(floor_db=NEAR_DB + 0.6)
        self.assertTrue(session.noise_floor_suggestion_due())
        # The same point turns the level line off (decision 1).
        self.assertTrue(session.room_near_the_line())
        self.assertIsNone(session.endpoint_threshold_db())

    def test_a_floor_far_under_the_wearer_does_not(self) -> None:
        session = self.session(floor_db=NEAR_DB - 1.0)
        self.assertFalse(session.noise_floor_suggestion_due())
        self.assertFalse(session.room_near_the_line())
        self.assertIsNotNone(session.endpoint_threshold_db())
        self.assertFalse(self.session(floor_db=-70.0).noise_floor_suggestion_due())

    def test_the_point_follows_the_sensitivity(self) -> None:
        session = self.session(floor_db=NEAR_DB - 3.0)
        self.assertFalse(session.noise_floor_suggestion_due())
        session.set_speaker_sensitivity("low")
        self.assertTrue(session.noise_floor_suggestion_due(), "a lower line meets the room sooner")

    def test_not_before_enough_of_the_room_was_heard(self) -> None:
        session = self.session(floor_db=None)
        for _ in range(NOISE_FLOOR_MINIMUM_FRAMES - 1):
            session.noise_floor.add(-30.0)
        self.assertFalse(session.noise_floor_suggestion_due())
        self.assertIsNone(session.room_near_the_line())
        session.noise_floor.add(-30.0)
        self.assertTrue(session.noise_floor_suggestion_due())

    def test_only_a_measured_quiet_session_with_nothing_open_is_told(self) -> None:
        self.assertFalse(self.session(floor_db=-30.0, reference_db=None).noise_floor_suggestion_due())
        session = self.session(floor_db=-30.0)
        session.arm_voice_calibration()
        self.assertFalse(session.noise_floor_suggestion_due(), "while the sentence is awaited")
        session = self.session(floor_db=-30.0)
        session.curated_protocol_session.ambient_mode = "noisy"
        self.assertFalse(session.noise_floor_suggestion_due(), "already noisy")
        session = self.session(floor_db=-30.0)
        session.curated_protocol_session.open_ambient_suggestion()
        self.assertFalse(session.noise_floor_suggestion_due(), "a suggestion is open")
        session = self.session(floor_db=-30.0)
        session.curated_protocol_session = None
        self.assertFalse(session.noise_floor_suggestion_due())

    def test_one_gap_of_ten_minutes_for_both_suggestions(self) -> None:
        session = self.session(floor_db=-30.0)
        session.ambient_suggested_at = session.clock()
        self.assertFalse(session.noise_floor_suggestion_due(), "lane SP1's suggestion just now")
        session.clock.now += AMBIENT_SUGGESTION_GAP_SECONDS + 1
        self.assertTrue(session.noise_floor_suggestion_due())
        # The floor's record holds lane SP1's back the same way (lane HEAR,
        # decision 9: recorded, no longer said).
        with patch("voiney_lab.server._record_workspace_metric"):
            _record_noise_condition(session, "floor_near_line")
        for _ in range(AMBIENT_SUGGESTION_IGNORED_COUNT):
            session.ignored_speech_at.append(session.clock())
        self.assertFalse(session.noisy_mode_suggestion_due())
        self.assertFalse(session.noise_floor_suggestion_due())


class _Socket:
    def __init__(self, messages=(), *, wait_for: str | None = None) -> None:
        self.text: list[dict] = []
        self.messages = list(messages)
        self.wait_for = wait_for

    async def accept(self):
        return None

    async def receive(self):
        message = self.messages.pop(0)
        if message.get("type") == "websocket.disconnect" and self.wait_for:
            # Let the suggestion task run before the connection ends.
            for _ in range(100):
                if any(m.get("type") == "server.words" for m in self.text):
                    break
                await asyncio.sleep(0.01)
        return message

    async def send_text(self, value):
        self.text.append(json.loads(value))

    async def send_bytes(self, value):
        return None

    async def close(self, **kwargs):
        return None


class SuggestTests(unittest.TestCase):
    def test_the_condition_is_recorded_as_numbers_and_nothing_is_said(self) -> None:
        session = ListenerSession(clock=Clock())
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp2")
        session.curated_protocol_session = _Curated()
        session.set_level_reference(REFERENCE_DB)
        for _ in range(NOISE_FLOOR_MINIMUM_FRAMES):
            session.noise_floor.add(-33.0)
        with patch("voiney_lab.server._record_workspace_metric") as metric, self.assertLogs(
            "voiney_lab", level="INFO",
        ) as logs:
            _record_noise_condition(session, "floor_near_line")
        self.assertEqual(session.ambient_suggested_at, session.clock())
        self.assertFalse(session.curated_protocol_session.ambient_suggestion_open, "no question is opened")
        self.assertEqual(session.curated_protocol_session.ambient_mode, "quiet", "nothing changes")
        self.assertIn("noise.condition kind=floor_near_line floor_db=-32.8 reference_db=-28.0", "\n".join(logs.output))
        self.assertEqual(metric.call_args.kwargs["dimensions"]["reason_code"], "floor_near_line")


class SocketTests(unittest.TestCase):
    """Through voice_socket: a loud room heard while listening is named once."""

    def _run(self, session: ListenerSession, frames: int) -> tuple[list[dict], object]:
        socket = _Socket(
            [{"bytes": ROOM * frames}, {"type": "websocket.disconnect", "code": 1000}],
            wait_for="server.words")
        with patch("voiney_lab.server.ListenerSession", return_value=session), patch(
            "voiney_lab.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch("voiney_lab.server.VadConfig.from_settings", return_value=CONFIG), patch(
            "voiney_lab.server.transcribe", return_value=Transcription("다음", "ko"),
        ) as stt, patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320 * 5):
            asyncio.run(voice_socket(socket))
        return socket.text, stt

    def session(self, reference_db: float | None) -> ListenerSession:
        session = ListenerSession(
            EndpointDetector(CONFIG, classifier=voiced_above_minus_30, listening_onset=True))
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp2")
        session.curated_protocol_session = _Curated()
        if reference_db is not None:
            session.set_level_reference(reference_db)
        return session

    def test_a_room_near_the_wearers_level_is_recorded_not_said(self) -> None:
        # Lane HEAR, decision 9 (2026-10-25): nothing is said during a run.
        with self.assertLogs("voiney_lab", level="INFO") as logs:
            sent, stt = self._run(self.session(ROOM_DB + 5.0), NOISE_FLOOR_MINIMUM_FRAMES + 50)
        self.assertEqual([m for m in sent if m["type"] == "server.words"], [])
        self.assertIn("noise.condition kind=floor_near_line", "\n".join(logs.output))
        stt.assert_not_called()

    def test_a_session_that_measured_nothing_is_not_told(self) -> None:
        sent, stt = self._run(self.session(None), NOISE_FLOOR_MINIMUM_FRAMES + 50)
        self.assertNotIn("server.words", [m["type"] for m in sent])
        stt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
