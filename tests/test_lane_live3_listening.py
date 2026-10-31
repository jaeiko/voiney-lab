"""LIVE3 (2026-10-31): continuing bench impacts must not stall, split or chain a turn.

The person's test on 2026-10-31 (MacBook, /app/, typing after speaking):
the screen stayed at "요청 이해 중" for about 20 s, a note came back with a
phrase written twice, and a question lost its last word. Replaying the
session's diagnostic clips showed three mechanisms, each with its
regression here:

* a bench impact heard while the turn was being answered opened the
  interrupt detector like speech and was joined to the turn before it
  (lane HEAR, decision 4), cancelling and restarting that turn until the
  kept audio reached the 10 s bound -- the stall and the 14 s clip whose
  words the recognizer wrote twice;
* lane HEAR2's relative gate took its floor from the wearer's own first
  second of speech, so the quieter end of a sentence counted as silence
  and was trimmed -- the lost last word;
* the same gate split synthetic sentences in noise into pieces that
  lane LISTEN3 then dropped as ``transient_noise``.

Everything here crosses the production ``ListenerSession.accept_chunk``
boundary or the production ``EndpointDetector`` with a deterministic
classifier; the carrier is a harmonic tone, not Korean speech.
"""

from __future__ import annotations

from array import array
import math
import unittest

from scripts.sim.audio import impact_pcm
from voiney_lab.audio import FRAME_BYTES
from voiney_lab.server import ListenerSession
from voiney_lab.vad import (
    ADAPTIVE_NOISE_WINDOW_FRAMES, EndpointDetector, TurnState, VadConfig,
)

SAMPLES = FRAME_BYTES // 2
SILENCE = bytes(FRAME_BYTES)


def carrier(count: int, *, amplitude: float = 0.18, start: int = 0) -> list[bytes]:
    """A two-harmonic carrier the acoustic checks take for speech shape."""

    frames = []
    for frame_index in range(start, start + count):
        base = frame_index * SAMPLES
        frames.append(array("h", (
            round(32767 * amplitude * (
                math.sin(2 * math.pi * 190 * (base + sample) / 16000)
                + 0.35 * math.sin(2 * math.pi * 380 * (base + sample) / 16000)
            ) / 1.35)
            for sample in range(SAMPLES)
        )).tobytes())
    return frames


def impacts(kind: str, *, bursts: int, gap_frames: int = 5) -> list[bytes]:
    """Repeated bench impacts: ``bursts`` pulse trains with short gaps between."""

    pcm = impact_pcm(kind, 30, frames=10)
    burst = [pcm[offset:offset + FRAME_BYTES] for offset in range(0, len(pcm), FRAME_BYTES)]
    frames: list[bytes] = []
    for _ in range(bursts):
        frames.extend(burst)
        frames.extend([SILENCE] * gap_frames)
    return frames


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Curated:
    _revision = 0
    workflow_status = "active"
    experiment_ended = False

    def reset(self) -> None:
        return None


def production_session(clock: Clock) -> ListenerSession:
    """The production listening lineage: WebRTC-equivalent gates on a deterministic classifier."""

    detector = EndpointDetector(
        VadConfig(), classifier=lambda frame: any(frame), listening_onset=True,
    )
    detector.enable_transient_noise_gate(True)
    session = ListenerSession(detector, clock=clock, curated_protocol_session=_Curated())
    session.start()
    session.accept_configuration(1, "cascade", "ko", "p")
    return session


def feed(session: ListenerSession, frames: list[bytes], clock: Clock | None = None) -> list:
    events = []
    for frame in frames:
        events.extend(session.accept_chunk(frame))
        if clock is not None:
            clock.now += 0.02
    return events


class RelativeGateFloorTests(unittest.TestCase):
    """The relative gate's floor is room sound, never the wearer's softer frames."""

    def detector(self) -> EndpointDetector:
        detector = EndpointDetector(
            VadConfig(), classifier=lambda frame: any(frame), listening_onset=True,
        )
        detector.enable_adaptive_noise_gate(True)
        detector.enable_transient_noise_gate(True)
        return detector

    @staticmethod
    def commit(detector: EndpointDetector, frames: list[bytes]):
        for frame in frames:
            result = detector.process(frame)
            if result.utterance is not None or result.rejected:
                return result
        return None

    def test_a_sentence_that_ends_quietly_keeps_its_last_word(self) -> None:
        # A soft start, a loud middle and a soft last word, then silence.
        spoken = (
            carrier(10, amplitude=0.02)
            + carrier(40, amplitude=0.2, start=10)
            + carrier(15, amplitude=0.025, start=50)
        )
        result = self.commit(self.detector(), spoken + [SILENCE] * 60)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNotNone(result.utterance, result.rejection_reason)
        self.assertEqual(result.endpoint, "silence")
        self.assertEqual(result.total_frames, len(spoken), "the quiet last word was trimmed")

    def test_steady_room_sound_still_defines_the_floor(self) -> None:
        # Lane HEAR2's case: a constant room sound the classifier calls speech
        # from the first frame has no speech shape and no level spread.
        room = array("h", [184] * SAMPLES).tobytes()
        rise = array("h", [420] * SAMPLES).tobytes()
        result = self.commit(
            self.detector(), [room] * ADAPTIVE_NOISE_WINDOW_FRAMES + [rise] + [room] * 699,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.forced)
        self.assertLess(result.total_frames, 200)

    def test_a_varying_sound_without_speech_shape_defines_no_floor(self) -> None:
        # Room frames whose level swings more than the steady spread -- a
        # bench being used, not a fan -- are not a floor the wearer is cut by.
        swinging = [
            array("h", [184 if index % 2 else 1200] * SAMPLES).tobytes()
            for index in range(ADAPTIVE_NOISE_WINDOW_FRAMES)
        ]
        detector = self.detector()
        for frame in swinging:
            detector.process(frame)
        self.assertIsNone(detector._adaptive_noise_threshold_db)


class ImpactsWhileAnsweringTests(unittest.TestCase):
    """Impacts heard while the turn is being answered neither cancel nor chain it."""

    def test_typing_after_the_words_leaves_the_turn_being_answered(self) -> None:
        clock = Clock()
        session = production_session(clock)
        events = feed(session, carrier(30) + [SILENCE] * 55, clock)
        ended = [event for event in events if event.kind == "speech.end"]
        self.assertEqual(len(ended), 1, events)
        self.assertEqual(session.state, TurnState.PROCESSING)
        turn = session.active_turn_id

        for kind in ("typing", "pipette", "glass"):
            events = feed(session, impacts(kind, bursts=4) + [SILENCE] * 55, clock)
            kinds = [event.kind for event in events]
            self.assertNotIn("assistant.interrupted", kinds, (kind, kinds))
            self.assertNotIn("speech.end", kinds, (kind, kinds))
            self.assertEqual(session.state, TurnState.PROCESSING, kind)
            self.assertEqual(session.active_turn_id, turn, kind)
            rejected = [event for event in events if event.kind == "barge_in_rejected"]
            self.assertTrue(rejected, (kind, kinds))
            self.assertTrue(
                all(event.reason == "transient_noise" for event in rejected),
                [event.reason for event in rejected],
            )

    def test_words_soon_after_the_end_still_join_the_turn(self) -> None:
        clock = Clock()
        session = production_session(clock)
        first = carrier(30)
        feed(session, first + [SILENCE] * 55, clock)
        self.assertEqual(session.state, TurnState.PROCESSING)
        clock.now += 0.4
        second = carrier(40, start=100)
        events = feed(session, second + [SILENCE] * 55, clock)
        interrupted = [event for event in events if event.kind == "assistant.interrupted"]
        self.assertEqual([event.reason for event in interrupted], ["continued_speech"])
        ended = [event for event in events if event.kind == "speech.end"]
        self.assertEqual(len(ended), 1, [event.kind for event in events])
        utterance = ended[0].result.utterance
        assert utterance is not None
        self.assertTrue(utterance.startswith(b"".join(first)), "the first half was not put in front")
        self.assertIn(b"".join(second[:20]), utterance)
        # The first half, the five silent frames heard before the second
        # half began (its prefix) and the second half: nothing twice.
        self.assertEqual(ended[0].result.total_frames, len(first) + 5 + len(second))
        self.assertEqual(len(utterance), ended[0].result.total_frames * FRAME_BYTES)
        self.assertTrue(utterance.endswith(b"".join(second)), "the second half was not kept whole")

    def test_typing_then_words_within_the_window_join_once_the_words_show(self) -> None:
        clock = Clock()
        session = production_session(clock)
        first = carrier(30)
        feed(session, first + [SILENCE] * 55, clock)
        turn = session.active_turn_id
        # Two bursts of typing, then the wearer goes on within the window.
        events = feed(session, impacts("typing", bursts=2, gap_frames=2), clock)
        self.assertNotIn("assistant.interrupted", [event.kind for event in events])
        self.assertEqual(session.active_turn_id, turn)
        events = feed(session, carrier(40, start=100) + [SILENCE] * 55, clock)
        kinds = [event.kind for event in events]
        self.assertIn("assistant.interrupted", kinds, kinds)
        ended = [event for event in events if event.kind == "speech.end"]
        self.assertEqual(len(ended), 1, kinds)
        assert ended[0].result.utterance is not None
        self.assertTrue(ended[0].result.utterance.startswith(b"".join(first)))


if __name__ == "__main__":
    unittest.main()
