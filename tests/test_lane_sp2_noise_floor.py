"""Lane SP2 (2026-10-13): the room's noise floor, measured all session.

Decision 2 asks for the noise floor from what the listener hears while
nobody speaks to it; decision 1 uses the same floor to decide whether the
wearer's level line may end utterances at all. The floor is the median of
the frame levels heard while listening over the last 30 s -- in babble the
babble, beside a fan the fan, in a quiet room the room -- never while the
agent speaks, kept for the session only and never stored.
"""

from __future__ import annotations

import array
import math
import unittest

from voiney_lab.audio import FRAME_BYTES
from voiney_lab.server import ListenerSession
from voiney_lab.vad import (
    NOISE_FLOOR_MINIMUM_FRAMES,
    NOISE_FLOOR_WINDOW_FRAMES,
    EndpointDetector,
    NoiseFloor,
    TurnState,
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


class NoiseFloorTests(unittest.TestCase):
    def test_the_floor_is_the_windows_percentile(self) -> None:
        floor = NoiseFloor(window_frames=1000, percentile=20.0, minimum_frames=500)
        for _ in range(350):
            floor.add(-60.0)
        for _ in range(149):
            floor.add(-20.0)
        self.assertIsNone(floor.level_db(), "not before minimum_frames")
        floor.add(-20.0)
        self.assertAlmostEqual(floor.level_db(), -60.0, delta=0.5)
        self.assertEqual(floor.frames, 500)

    def test_the_wearers_sentences_do_not_lift_it(self) -> None:
        # The wearer talking four tenths of a working half-minute.
        floor = NoiseFloor()
        for k in range(NOISE_FLOOR_WINDOW_FRAMES):
            floor.add(-18.0 if k % 10 < 4 else -52.0)
        self.assertAlmostEqual(floor.level_db(), -52.0, delta=0.5)

    def test_it_is_taken_again_once_a_second(self) -> None:
        floor = NoiseFloor(window_frames=1000, percentile=50.0, minimum_frames=100)
        for _ in range(100):
            floor.add(-60.0)
        self.assertAlmostEqual(floor.level_db(), -60.0, delta=0.5)
        for _ in range(149):
            floor.add(-20.0)
        self.assertAlmostEqual(floor.level_db(), -60.0, delta=0.5, msg="the last value stands between")
        floor.add(-20.0)
        self.assertAlmostEqual(floor.level_db(), -20.0, delta=0.5)

    def test_the_window_slides_and_a_clear_forgets(self) -> None:
        floor = NoiseFloor(window_frames=600, percentile=20.0, minimum_frames=100)
        for _ in range(600):
            floor.add(-70.0)
        self.assertAlmostEqual(floor.level_db(), -70.0, delta=0.5)
        for _ in range(600):
            floor.add(-30.0)
        self.assertAlmostEqual(floor.level_db(), -30.0, delta=0.5)
        self.assertEqual(floor.frames, 600)
        floor.clear()
        self.assertIsNone(floor.level_db())
        self.assertEqual(floor.frames, 0)

    def test_the_voiced_share_under_a_line(self) -> None:
        floor = NoiseFloor(window_frames=1000, percentile=50.0, minimum_frames=100)
        self.assertIsNone(floor.voiced_share_below(-30.0))
        for k in range(200):
            floor.add(-45.0, k % 4 != 0)   # babble under the line, 3 of 4 voiced
        for _ in range(100):
            floor.add(-20.0, True)         # the wearer, over the line
        self.assertAlmostEqual(floor.voiced_share_below(-30.0), 0.75)
        self.assertAlmostEqual(floor.voiced_share_below(-10.0), 250 / 300)
        self.assertEqual(floor.voiced_share_below(-60.0), 0.0, "nothing heard under it")
        floor.clear()
        self.assertIsNone(floor.voiced_share_below(-30.0))

    def test_out_of_range_levels_land_in_the_end_bins(self) -> None:
        floor = NoiseFloor(window_frames=100, percentile=50.0, minimum_frames=10)
        for _ in range(10):
            floor.add(-150.0)
        self.assertLess(floor.level_db(), -99.0)
        for _ in range(100):
            floor.add(5.0)
        self.assertGreater(floor.level_db(), -1.0)

    def test_bad_shapes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            NoiseFloor(window_frames=10, minimum_frames=11)
        with self.assertRaises(ValueError):
            NoiseFloor(percentile=0.0)


class ListenerFloorTests(unittest.TestCase):
    def session(self) -> ListenerSession:
        session = ListenerSession(
            EndpointDetector(CONFIG, classifier=voiced_above_minus_30, listening_onset=True), clock=Clock())
        session.start()
        return session

    def test_every_frame_heard_while_listening_measures_the_room(self) -> None:
        session = self.session()
        for _ in range(NOISE_FLOOR_MINIMUM_FRAMES):
            session.accept_chunk(ROOM)
        self.assertEqual(session.state, TurnState.IDLE)
        self.assertAlmostEqual(session.noise_floor.level_db(), ROOM_DB, delta=0.5)
        # The stand-in VAD never calls ROOM a voice: a room that does not
        # keep the VAD on.
        self.assertEqual(session.noise_floor.voiced_share_below(0.0), 0.0)

    def test_nothing_heard_while_the_agent_speaks_counts(self) -> None:
        session = self.session()
        session.active_turn_id = 1
        session.turn_generations[1] = session.generation
        session.detector.state = TurnState.PROCESSING
        self.assertTrue(session.start_playback(1))
        for _ in range(NOISE_FLOOR_MINIMUM_FRAMES + 10):
            session.accept_chunk(ROOM)
        self.assertEqual(session.noise_floor.frames, 0)
        self.assertIsNone(session.noise_floor.level_db())

    def test_a_new_session_measures_again(self) -> None:
        session = self.session()
        for _ in range(NOISE_FLOOR_MINIMUM_FRAMES):
            session.accept_chunk(ROOM)
        session.start()
        self.assertEqual(session.noise_floor.frames, 0)
        for _ in range(10):
            session.accept_chunk(ROOM)
        session.stop()
        self.assertEqual(session.noise_floor.frames, 0)


if __name__ == "__main__":
    unittest.main()
