"""Lane SP2, decision 1 (2026-10-13): the end of the wearer's words is found by level too.

In babble, or beside a vacuum pump's pulsing, the WebRTC VAD hears "a voice"
without a break, so it never finds the endpoint silence and the utterance
runs to the 15 s forced commit -- the wearer's command and the room's talk
in one piece (lane SP1's bench, section 3-2). With the wearer's level
measured (lane SP1, decision 1), the detector also ends an utterance by
level: once the wearer's level was heard (as many frames over the line as a
listening onset needs voiced), a stretch as long as the endpoint time spent
more than the margin below the wearer ends it while the VAD keeps hearing a
voice there. What the room's voice said before the
wearer's first frame, beyond one prefix of lead-in, is dropped. The listener
draws the line only while the room's noise floor sits well under it. A
session that measured nothing ends utterances as before; so does a quiet
room, and an utterance that never reached the wearer's level.
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
from voiney_lab.server import LEVEL_ENDPOINT_FLOOR_GAP_DB, ListenerSession, voice_socket
from voiney_lab.vad import (
    NOISE_FLOOR_MINIMUM_FRAMES,
    SENSITIVITY_MARGINS_DB,
    EndpointDetector,
    TurnState,
    VadConfig,
)

SAMPLES = FRAME_BYTES // 2


def tone_frame(amplitude: float, phase: int = 0) -> bytes:
    """One 20 ms frame of a 440 Hz tone at ``amplitude`` of full scale."""

    values = array.array("h", (
        int(round(32767 * amplitude * math.sin(2 * math.pi * 440 * (phase * SAMPLES + i) / 16000)))
        for i in range(SAMPLES)))
    return values.tobytes()


SILENT = bytes(FRAME_BYTES)
#: The wearer at about -23 dBFS; the room's "voice" (babble, a pump) at about
#: -43 dBFS: more than the normal margin (12 dB) under a -20 dBFS reference.
LOUD = [tone_frame(0.1, i) for i in range(80)]
QUIET = [tone_frame(0.01, i) for i in range(80)]
REFERENCE_DB = -20.0
THRESHOLD_DB = REFERENCE_DB - SENSITIVITY_MARGINS_DB["normal"]


def voiced_if_loud(frame: bytes) -> bool:
    """A VAD that hears a voice in any sound at all -- babble keeps it on."""

    return any(frame)


#: Speech starts at 2 voiced of 3 frames, ends after 6 quiet frames, resumes
#: at 2 of 3, keeps 3 frames of lead-in, and is forced at 60 frames.
CONFIG = VadConfig(
    onset_voiced_frames=2, onset_window_frames=3, prefix_frames=3, barge_in_prefix_frames=3,
    endpoint_silence_frames=6, minimum_voiced_frames=2,
    maximum_utterance_frames=60, cooldown_ms=0,
    playback_onset_voiced_frames=2, playback_onset_window_frames=3,
    listening_onset_voiced_frames=2, listening_onset_window_frames=3,
    listening_resume_voiced_frames=2, listening_resume_window_frames=3,
)


def detector(threshold_db: float | None) -> EndpointDetector:
    found = EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True)
    found.wearer_threshold_db = threshold_db
    return found


def run(found: EndpointDetector, frames: list[bytes]) -> list[tuple[int, object]]:
    """The frames' commits, with the index of the frame that made each."""

    out = []
    for index, frame in enumerate(frames):
        result = found.process(frame)
        if result.utterance is not None or result.rejected:
            out.append((index, result))
    return out


class DetectorTests(unittest.TestCase):
    def test_without_a_line_a_voice_that_never_stops_runs_to_the_forced_bound(self) -> None:
        frames = QUIET[:20] + LOUD[:6] + QUIET[:40]
        commits = run(detector(None), frames)
        self.assertEqual(len(commits), 1)
        index, result = commits[0]
        self.assertTrue(result.forced)
        self.assertEqual(result.endpoint, "forced")
        self.assertEqual(result.total_frames, CONFIG.maximum_utterance_frames)

    def test_with_the_line_the_wearers_words_end_when_the_room_stays_under_it(self) -> None:
        frames = QUIET[:20] + LOUD[:6] + QUIET[:40]
        commits = run(detector(THRESHOLD_DB), frames)
        self.assertEqual(len(commits), 1)
        index, result = commits[0]
        self.assertFalse(result.forced)
        self.assertEqual(result.endpoint, "level")
        # Six quiet frames after the last loud one, not 40 more.
        self.assertEqual(index, 20 + 6 + CONFIG.endpoint_silence_frames - 1)
        # The room before the wearer, beyond one prefix of lead-in, is
        # dropped; the quiet stretch after is trimmed as silence is.
        lead = CONFIG.prefix_frames - 1
        self.assertEqual(result.total_frames, lead + 6)
        self.assertEqual(result.utterance, b"".join(QUIET[18:20] + LOUD[:6]))
        self.assertEqual(result.prefix_frames_retained, lead)
        # The level the gate compares is the wearer's.
        self.assertGreater(result.voiced_level_db, THRESHOLD_DB)

    def test_a_short_dip_under_the_line_does_not_end_the_words(self) -> None:
        frames = QUIET[:3] + LOUD[:5] + QUIET[:2] + LOUD[5:10] + QUIET[:10]
        commits = run(detector(THRESHOLD_DB), frames)
        self.assertEqual(len(commits), 1)
        index, result = commits[0]
        self.assertEqual(result.endpoint, "level")
        self.assertEqual(result.utterance, b"".join(QUIET[1:3] + LOUD[:5] + QUIET[:2] + LOUD[5:10]))

    def test_an_utterance_that_never_reaches_the_wearer_ends_as_before(self) -> None:
        for frames in (QUIET[:70], QUIET[:6] + [SILENT] * 8):
            with self.subTest(frames=len(frames)):
                before = run(detector(None), frames)
                after = run(detector(THRESHOLD_DB), frames)
                self.assertEqual(
                    [(i, r.utterance, r.forced, r.endpoint, r.voiced_level_db) for i, r in before],
                    [(i, r.utterance, r.forced, r.endpoint, r.voiced_level_db) for i, r in after])
        self.assertEqual(run(detector(THRESHOLD_DB), QUIET[:6] + [SILENT] * 8)[0][1].endpoint, "silence")

    def test_no_line_never_ends_the_words_by_level(self) -> None:
        # The line taken away in mid-utterance: only the VAD may end it.
        found = detector(THRESHOLD_DB)
        commits = run(found, QUIET[:3] + LOUD[:6])
        self.assertEqual(commits, [])
        found.wearer_threshold_db = None
        commits = run(found, QUIET[:60])
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0][1].endpoint, "forced")

    def test_a_quiet_filler_between_pauses_does_not_end_the_words(self) -> None:
        # "... 음 ..." said softly between two short pauses: under the line
        # for longer than the endpoint time, but the VAD heard mostly silence
        # there, so only the VAD may end the words -- and it does not.
        frames = LOUD[:6] + [SILENT] * 2 + QUIET[:3] + [SILENT] * 2 + LOUD[6:10] + [SILENT] * 8
        before = run(detector(None), frames)
        after = run(detector(THRESHOLD_DB), frames)
        self.assertEqual(len(after), 1)
        self.assertEqual(
            [(i, r.utterance, r.endpoint) for i, r in before],
            [(i, r.utterance, r.endpoint) for i, r in after])
        self.assertEqual(after[0][1].endpoint, "silence")

    def test_a_neighbours_louder_moments_are_not_the_wearers_level(self) -> None:
        # A voice under the line with a frame over it now and then (a
        # neighbour, a TV): the wearer's level needs as many frames over the
        # line as a listening onset needs voiced, so nothing changes.
        frames = QUIET[:3] + [LOUD[k] if k % 3 == 0 else QUIET[k] for k in range(30)] + [SILENT] * 8
        before = run(detector(None), frames)
        after = run(detector(THRESHOLD_DB), frames)
        self.assertEqual(len(after), 1)
        self.assertEqual(
            [(i, r.utterance, r.endpoint, r.voiced_level_db) for i, r in before],
            [(i, r.utterance, r.endpoint, r.voiced_level_db) for i, r in after])

    def test_a_soft_start_in_a_quiet_room_is_kept(self) -> None:
        # A soft start, a pause, then the wearer's level: the VAD heard
        # mostly silence before it, so nothing is dropped.
        frames = QUIET[:2] + [SILENT] * 4 + LOUD[:6] + [SILENT] * 8
        before = run(detector(None), frames)
        after = run(detector(THRESHOLD_DB), frames)
        self.assertEqual([r.utterance for _, r in before], [r.utterance for _, r in after])
        self.assertTrue(after[0][1].utterance.startswith(QUIET[0]))

    def test_in_a_quiet_room_the_wearers_words_end_where_they_always_did(self) -> None:
        frames = LOUD[:12] + [SILENT] * 8
        before = run(detector(None), frames)
        after = run(detector(THRESHOLD_DB), frames)
        self.assertEqual(len(before), 1)
        self.assertEqual(
            [(i, r.utterance, r.forced, r.voiced_level_db) for i, r in before],
            [(i, r.utterance, r.forced, r.voiced_level_db) for i, r in after])
        self.assertEqual((before[0][1].endpoint, after[0][1].endpoint), ("silence", "silence"))

    def test_the_forced_bound_counts_from_the_wearers_start(self) -> None:
        # 50 frames of room, then the wearer: without dropping the room the
        # utterance would be forced 10 frames into the wearer's words.
        frames = QUIET[:50] + LOUD[:20] + QUIET[:10]
        index, result = run(detector(THRESHOLD_DB), frames)[0]
        self.assertFalse(result.forced)
        self.assertEqual(result.endpoint, "level")
        self.assertEqual(result.utterance, b"".join(QUIET[48:50] + LOUD[:20]))
        index, result = run(detector(None), frames)[0]
        self.assertTrue(result.forced)


def hear_room(session: ListenerSession, floor_db: float = -45.0, *, voiced: bool = True) -> None:
    """The listener has heard enough of a room whose noise floor is ``floor_db``.

    By default a babbling room: its voice (QUIET, -43 dBFS) at the floor,
    well under the wearer's line (-32), and voiced. ``voiced=False`` is a
    quiet room or a fan: the VAD finds its silence itself.
    """

    for _ in range(NOISE_FLOOR_MINIMUM_FRAMES):
        session.noise_floor.add(floor_db, voiced)


class ListenerTests(unittest.TestCase):
    def session(self, *, reference_db: float | None = REFERENCE_DB, floor_db: float | None = -45.0,
                voiced_room: bool = True) -> ListenerSession:
        session = ListenerSession(EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True))
        session.start()
        if reference_db is not None:
            session.set_level_reference(reference_db)
        if floor_db is not None:
            hear_room(session, floor_db, voiced=voiced_room)
        return session

    def feed(self, session: ListenerSession, frames: list[bytes]) -> list:
        """The listener's events, up to the first utterance it ends (later frames would interrupt)."""

        out = []
        for frame in frames:
            out.extend(session.accept_chunk(frame))
            if out and out[-1].kind in {"speech.end", "speech.ignored", "barge_in_audio_ready"}:
                break
        return out

    def test_the_line_waits_for_a_floor_well_under_it(self) -> None:
        session = self.session(floor_db=None)
        self.assertIsNone(session.endpoint_threshold_db(), "the room not yet heard")
        hear_room(session, THRESHOLD_DB - LEVEL_ENDPOINT_FLOOR_GAP_DB + 3.0)
        self.assertIsNone(session.endpoint_threshold_db(), "the room too near the line")
        session = self.session(floor_db=THRESHOLD_DB - LEVEL_ENDPOINT_FLOOR_GAP_DB - 0.5)
        self.assertEqual(session.endpoint_threshold_db(), THRESHOLD_DB)

    def test_the_log_says_when_the_level_starts_and_stops_ending_turns(self) -> None:
        session = self.session()
        with self.assertLogs("voiney_lab", level="INFO") as logs:
            session.accept_chunk(SILENT)
            session.accept_chunk(SILENT)
        said = "\n".join(logs.output)
        self.assertEqual(said.count("level endpoint on"), 1)
        self.assertIn(f"threshold_db={THRESHOLD_DB} onset_db={REFERENCE_DB - SENSITIVITY_MARGINS_DB['normal'] / 2} floor_db=", said)
        for _ in range(NOISE_FLOOR_MINIMUM_FRAMES * 2):
            session.noise_floor.add(THRESHOLD_DB)
        with self.assertLogs("voiney_lab", level="INFO") as logs:
            session.accept_chunk(SILENT)
        self.assertIn("level endpoint off threshold_db=None onset_db=None floor_db=", "\n".join(logs.output))

    def test_a_voice_between_the_lines_never_starts_the_levels_end(self) -> None:
        # Loud enough for the gate, not as loud as the wearer (a neighbour at
        # 1 m on an earbud, a TV): the utterance runs as before.
        session = self.session()
        middle = [tone_frame(0.05, i) for i in range(10)]
        events = self.feed(session, QUIET[:20] + middle + QUIET[:40])
        self.assertEqual([e.kind for e in events], ["speech.start", "speech.ignored"])
        self.assertTrue(events[-1].result.forced)

    def test_a_room_that_does_not_keep_the_vad_on_ends_words_as_before(self) -> None:
        # A quiet room or a fan: the VAD finds the silence itself, so the
        # level never ends the wearer's words -- not even a quieter phrase
        # of the wearer's own that the VAD hears as a voice.
        session = self.session(floor_db=-80.0, voiced_room=False)
        self.assertFalse(session.room_keeps_the_vad_on())
        self.assertIsNone(session.endpoint_threshold_db())
        events = self.feed(session, LOUD[:6] + QUIET[:8] + LOUD[6:12] + [SILENT] * 8)
        self.assertEqual([e.kind for e in events], ["speech.start", "speech.end"])
        self.assertEqual(events[-1].result.endpoint, "silence")
        self.assertEqual(events[-1].result.utterance, b"".join(LOUD[:6] + QUIET[:8] + LOUD[6:12]))
        # The same room voiced -- babble -- turns the level on.
        session = self.session(floor_db=-80.0, voiced_room=True)
        self.assertTrue(session.room_keeps_the_vad_on())
        self.assertEqual(session.endpoint_threshold_db(), THRESHOLD_DB)

    def test_an_utterance_keeps_the_lines_it_began_with(self) -> None:
        # The floor rises past the guard while the wearer speaks: the
        # utterance keeps its line and ends by level; the next one has none.
        session = self.session()
        events = self.feed(session, QUIET[:20] + LOUD[:6])
        self.assertEqual([e.kind for e in events], ["speech.start"])
        for _ in range(NOISE_FLOOR_MINIMUM_FRAMES * 3):
            session.noise_floor.add(THRESHOLD_DB)
        self.assertIsNone(session.endpoint_threshold_db())
        events = self.feed(session, QUIET[:10])
        self.assertEqual([e.kind for e in events], ["speech.end"])
        self.assertEqual(events[-1].result.endpoint, "level")
        session.refresh_cooldown()
        session.detector.reset()
        session.accept_chunk(QUIET[0])
        self.assertIsNone(session.detector.wearer_threshold_db, "the next utterance has no line")

    def test_a_room_near_the_line_ends_the_turn_as_before(self) -> None:
        # As before: the room's voice runs the utterance to the forced bound,
        # and lane SP1's gate takes the whole of it for another voice.
        session = self.session(floor_db=THRESHOLD_DB - 2.0)
        events = self.feed(session, QUIET[:20] + LOUD[:6] + QUIET[:40])
        self.assertEqual([e.kind for e in events], ["speech.start", "speech.ignored"])
        self.assertTrue(events[-1].result.forced)

    def test_the_line_is_the_gates_and_only_with_a_measurement(self) -> None:
        session = self.session(reference_db=None, floor_db=-80.0)
        self.assertIsNone(session.endpoint_threshold_db())
        session.set_level_reference(REFERENCE_DB)
        self.assertEqual(session.endpoint_threshold_db(), THRESHOLD_DB)
        session.set_speaker_sensitivity("low")
        self.assertEqual(session.endpoint_threshold_db(), REFERENCE_DB - SENSITIVITY_MARGINS_DB["low"])
        session.arm_voice_calibration()
        self.assertIsNone(session.endpoint_threshold_db(), "the sentence read to measure ends as before")
        session.set_level_reference(-30.0)
        self.assertEqual(session.endpoint_threshold_db(), -30.0 - SENSITIVITY_MARGINS_DB["low"])
        session.start()
        self.assertIsNone(session.endpoint_threshold_db(), "a new session measures again")

    def test_the_wearers_turn_ends_inside_the_rooms_voice(self) -> None:
        session = self.session()
        events = self.feed(session, QUIET[:20] + LOUD[:6] + QUIET[:10])
        self.assertEqual([e.kind for e in events], ["speech.start", "speech.end"])
        end = events[-1]
        self.assertFalse(end.result.forced)
        self.assertEqual(end.result.endpoint, "level")
        self.assertEqual(end.result.utterance, b"".join(QUIET[18:20] + LOUD[:6]))
        self.assertEqual(session.state, TurnState.PROCESSING)

    def test_without_a_measurement_the_turn_still_waits_for_the_forced_bound(self) -> None:
        session = self.session(reference_db=None)
        events = self.feed(session, QUIET[:20] + LOUD[:6] + QUIET[:10])
        self.assertEqual([e.kind for e in events], ["speech.start"])
        events = self.feed(session, QUIET[10:40])
        self.assertEqual([e.kind for e in events], ["speech.end"])
        self.assertTrue(events[0].result.forced)

    def test_while_the_sentence_is_awaited_the_turn_ends_as_before(self) -> None:
        session = self.session()
        session.arm_voice_calibration()
        events = self.feed(session, QUIET[:20] + LOUD[:6] + QUIET[:40])
        self.assertEqual([e.kind for e in events], ["speech.start", "speech.end"])
        self.assertTrue(events[-1].result.forced)

    def test_a_neighbours_voice_alone_is_still_one_ignored_utterance(self) -> None:
        session = self.session()
        events = self.feed(session, QUIET[:8] + [SILENT] * 8)
        self.assertEqual([e.kind for e in events], ["speech.start", "speech.ignored"])
        self.assertEqual(events[-1].result.endpoint, "silence")

    def test_an_interruption_during_playback_ends_by_level_too(self) -> None:
        session = self.session()
        session.active_turn_id = 1
        session.turn_generations[1] = session.generation
        session.detector.state = TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        events = self.feed(session, QUIET[:20] + LOUD[:6] + QUIET[:10])
        self.assertEqual([e.kind for e in events], ["barge_in_candidate", "barge_in_audio_ready"])
        self.assertFalse(events[-1].result.forced)
        self.assertEqual(events[-1].result.endpoint, "level")


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


class SocketTests(unittest.TestCase):
    """Through voice_socket: the page hears an early, unforced end in the room's voice."""

    def _run(self, session: ListenerSession, frames: list[bytes]) -> tuple[list[dict], str]:
        socket = Socket([{"bytes": b"".join(frames)}, {"type": "websocket.disconnect", "code": 1000}])
        with patch("voiney_lab.server.ListenerSession", return_value=session), patch(
            "voiney_lab.server.VoiceVadSettings.from_environment",
            return_value=SimpleNamespace(cascade=object()),
        ), patch("voiney_lab.server.VadConfig.from_settings", return_value=CONFIG), patch(
            "voiney_lab.server.transcribe", return_value=Transcription("다음", "ko"),
        ), patch("voiney_lab.server.synthesize", return_value=b"\0\0" * 320), self.assertLogs(
            "voiney_lab", level="INFO",
        ) as logs:
            asyncio.run(voice_socket(socket))
        return socket.text, "\n".join(logs.output)

    def session(self, reference_db: float | None) -> ListenerSession:
        session = ListenerSession(EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True))
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-sp2")
        if reference_db is not None:
            session.set_level_reference(reference_db)
        hear_room(session)
        return session

    def test_a_measured_session_hears_the_end_by_level(self) -> None:
        sent, logs = self._run(self.session(REFERENCE_DB), QUIET[:20] + LOUD[:6] + QUIET[:10])
        end = next(m for m in sent if m["type"] == "speech.end")
        self.assertFalse(end["forced"])
        self.assertEqual(end["duration_ms"], (CONFIG.prefix_frames - 1 + 6) * 20)
        self.assertIn("endpoint=level", logs)

    def test_a_session_that_measured_nothing_is_forced_as_before(self) -> None:
        sent, logs = self._run(self.session(None), QUIET[:20] + LOUD[:6] + QUIET[:40])
        end = next(m for m in sent if m["type"] == "speech.end")
        self.assertTrue(end["forced"])
        self.assertEqual(end["duration_ms"], CONFIG.maximum_utterance_frames * 20)
        self.assertNotIn("endpoint=level", logs)


if __name__ == "__main__":
    unittest.main()
