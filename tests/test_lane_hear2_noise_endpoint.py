"""Lane HEAR2: low, continuous room sound must not hold one turn for 15 s.

These tests cross the production ``ListenerSession.accept_chunk`` boundary.
The classifier deliberately behaves like the 2026-10-29 diagnostic capture:
it calls both the room and the wearer voice, while their PCM levels remain
different.  The listener must retain the wearer and find the end inside the
room sound without changing the configured 1000 ms endpoint silence.
"""

from __future__ import annotations

from array import array
import unittest

from voiney_lab.audio import FRAME_BYTES
from voiney_lab.server import ListenerSession
from voiney_lab.vad import EndpointDetector, VadConfig


def level_frame(sample: int) -> bytes:
    return array("h", [sample] * (FRAME_BYTES // 2)).tobytes()


ROOM = level_frame(184)  # about -45 dBFS
WEARER_AT_ONE_METRE = level_frame(412)  # about -38 dBFS


class AdaptiveNoiseEndpointTests(unittest.TestCase):
    def session(self) -> ListenerSession:
        session = ListenerSession(
            EndpointDetector(
                VadConfig(), classifier=lambda _frame: True,
                listening_onset=True,
            )
        )
        session.start()
        return session

    @staticmethod
    def feed(session: ListenerSession, frames: list[bytes]):
        events = []
        for frame in frames:
            events.extend(session.accept_chunk(frame))
            if any(event.kind == "speech.end" for event in events):
                break
        return events

    def test_continuous_low_room_sound_ends_before_the_forced_bound(self) -> None:
        session = self.session()
        events = self.feed(session, [ROOM] * 750)
        ended = [event for event in events if event.kind == "speech.end"]
        self.assertEqual(len(ended), 1, events)
        self.assertFalse(ended[0].result.forced)
        self.assertEqual(ended[0].result.endpoint, "silence")
        self.assertLess(ended[0].result.total_frames, 200)

    def test_a_quieter_wearer_is_kept_and_the_room_after_them_ends_the_turn(self) -> None:
        session = self.session()
        events = self.feed(
            session,
            [ROOM] * 60 + [WEARER_AT_ONE_METRE] * 25 + [ROOM] * 100,
        )
        ended = [event for event in events if event.kind == "speech.end"]
        self.assertEqual(len(ended), 1, events)
        result = ended[0].result
        self.assertFalse(result.forced)
        self.assertIsNotNone(result.utterance)
        assert result.utterance is not None
        self.assertIn(WEARER_AT_ONE_METRE, result.utterance)
        self.assertLess(result.total_frames, 150)


if __name__ == "__main__":
    unittest.main()
