"""Lane HEAR2, decision 2 (2026-10-26): a continuation chain has a bound.

The person's test, 03:12:12-03:12:32: room noise the VAD took for speech
every 100-400 ms kept starting "new speech" within lane HEAR's 1.5 s join
window, so the turn was cancelled and started again 101 times (turn
55 -> 156) while the kept audio grew to 31.9 s and the screen said "요청
이해 중" and "듣는 중" over and over. The person's words inside were answered
only when the noise paused.

Now the audio a continuation may join to is bounded
(``CONTINUATION_JOIN_MAX_SECONDS``): once the kept turn's sound is that
long, speech that starts after its end no longer joins it -- the turn is
transcribed as it stands and the new speech is heard as before, a
candidate of its own. Short chains join as lane HEAR made them.
"""

from __future__ import annotations

import unittest

from tests.test_lane_hear_drops import SILENT
from tests.test_lane_hear_join import ContinuationTests, run, runs
from voiney_lab.audio import FRAME_BYTES
from voiney_lab.server import CONTINUATION_JOIN_MAX_SECONDS, CONTINUATION_JOIN_SECONDS


class JoinCapTests(ContinuationTests):
    def test_the_chain_stops_joining_once_the_kept_sound_reaches_the_bound(self) -> None:
        self.assertGreaterEqual(CONTINUATION_JOIN_MAX_SECONDS, 6.0, "the bound is not shorter than a long sentence")
        self.assertLess(CONTINUATION_JOIN_MAX_SECONDS, 15.0, "the bound is under the 15 s utterance limit")
        session = self.listener()
        self.first_half(session)
        joins = 0
        kept_seconds = 0.0
        # The harness's 0.8 s utterance bound ends the joined sound again and
        # again within one feed, so one feed may hold several joins; the
        # refusal shows as a candidate in the same batch.
        for _ in range(80):
            self.clock.now += CONTINUATION_JOIN_SECONDS - 0.5
            events = self.feed(session, run(11) + SILENT * 3)
            kinds = [e.kind for e in events]
            joins += kinds.count("assistant.interrupted")
            ends = [e for e in events if e.kind == "speech.end"]
            if ends:
                kept_seconds = len(ends[-1].result.utterance) / 32000
            if "barge_in_candidate" in kinds:
                self.assertNotIn("assistant.interrupted", kinds[kinds.index("barge_in_candidate"):],
                                 "a join after the refusal")
                break
            self.assertTrue(joins, kinds)
        else:
            self.fail("the chain never stopped joining")
        self.assertGreaterEqual(joins, 3, "short chains still join")
        self.assertGreaterEqual(kept_seconds, CONTINUATION_JOIN_MAX_SECONDS - 0.5, kept_seconds)
        self.assertLess(kept_seconds, CONTINUATION_JOIN_MAX_SECONDS + 1.0, "the bound is not far overshot")

    def test_a_short_chain_joins_as_before(self) -> None:
        session = self.listener()
        first = self.first_half(session)
        self.clock.now += CONTINUATION_JOIN_SECONDS - 0.5
        events = self.feed(session, run(11) + SILENT * 3)
        self.assertEqual([e.kind for e in events][:2], ["assistant.interrupted", "speech.start"])
        end = next(e for e in events if e.kind == "speech.end")
        self.assertEqual(runs(end.result.utterance), [7, 11])
        self.assertEqual(len(end.result.utterance) % FRAME_BYTES, 0)
        self.assertNotEqual(end.turn_id, first.turn_id)


if __name__ == "__main__":
    unittest.main()
