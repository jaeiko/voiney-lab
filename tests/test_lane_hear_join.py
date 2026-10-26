"""Lane HEAR, decision 4 (2026-10-25): a sentence with a pause in it is one sentence.

The person's test: words were cut mid-sentence. The log shows why: 54 of 66
utterances were followed by new speech within 1.5 s of their end -- the
1 s of silence ended the first half, and the second half, heard while the
first was being answered, became an interruption that replaced it or was
thrown away with it.

Now speech that starts within CONTINUATION_JOIN_SECONDS of the last end
(FRAGMENT_JOIN_SECONDS when the words so far end unfinished, lane RA's
rule, on the rules path as well as the router's) is joined to it, while
its answer has not started playing and the turn has changed nothing: the
first half's turn is cancelled and its audio goes in front of the second
half's, so one transcription hears the whole sentence. Words started while
an answer plays still interrupt it, as before. The end of speech (1 s of
silence) and the 15 s bound are unchanged.

Also: speech that starts while an answer is being prepared and runs into
its playback is no longer cut when the playback starts, and speech under
way when the turn before it is dropped (nothing written down) is kept as
a turn of its own.
"""

from __future__ import annotations

import unittest

from tests.test_lane_hear_drops import CONFIG, Clock, SILENT, tone_frame, voiced_if_loud
from voiney_lab.audio import FRAME_BYTES
from voiney_lab.server import CONTINUATION_JOIN_SECONDS, ListenerSession
from voiney_lab.vad import EndpointDetector, TurnState


def run(frames: int, amplitude: float = 0.1) -> bytes:
    return b"".join(tone_frame(amplitude, i) for i in range(frames))


def runs(pcm: bytes) -> list[int]:
    lengths, count = [], 0
    for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
        if any(pcm[i:i + FRAME_BYTES]):
            count += 1
        elif count:
            lengths.append(count)
            count = 0
    if count:
        lengths.append(count)
    return lengths


class _Curated:
    def __init__(self) -> None:
        self._revision = 0
        self.workflow_status = "active"

    def reset(self) -> None:
        return None


class ContinuationTests(unittest.TestCase):
    def listener(self) -> ListenerSession:
        self.clock = Clock()
        session = ListenerSession(
            EndpointDetector(CONFIG, classifier=voiced_if_loud, listening_onset=True), clock=self.clock)
        session.start()
        session.accept_configuration(41, "cascade", "ko", "lane-hear")
        session.curated_protocol_session = _Curated()
        return session

    def feed(self, session: ListenerSession, pcm: bytes) -> list:
        events = []
        for i in range(0, len(pcm), FRAME_BYTES):
            events.extend(session.accept_chunk(pcm[i:i + FRAME_BYTES]))
        return events

    def first_half(self, session: ListenerSession):
        events = self.feed(session, run(7) + SILENT * 3)
        end = next(e for e in events if e.kind == "speech.end")
        self.assertEqual(session.state, TurnState.PROCESSING)
        return end

    def test_words_soon_after_the_end_are_joined_into_one_utterance(self) -> None:
        session = self.listener()
        first = self.first_half(session)
        self.clock.now += CONTINUATION_JOIN_SECONDS - 0.5
        events = self.feed(session, run(11) + SILENT * 3)
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, ["assistant.interrupted", "speech.start", "speech.end"], kinds)
        interrupted, start, end = events
        self.assertEqual((interrupted.turn_id, interrupted.generation), (first.turn_id, first.generation))
        self.assertEqual(interrupted.reason, "continued_speech")
        self.assertEqual(interrupted.superseding_turn_id, start.turn_id)
        self.assertNotEqual(start.turn_id, first.turn_id)
        self.assertEqual(end.turn_id, start.turn_id)
        self.assertEqual(runs(end.result.utterance), [7, 11], "the first half's audio leads the second's")
        self.assertGreater(end.generation, first.generation)

    def test_words_after_the_window_interrupt_as_before(self) -> None:
        session = self.listener()
        self.first_half(session)
        self.clock.now += CONTINUATION_JOIN_SECONDS + 0.5
        kinds = [e.kind for e in self.feed(session, run(11) + SILENT * 3)]
        self.assertEqual(kinds[0], "barge_in_candidate")
        self.assertNotIn("assistant.interrupted", kinds)

    def test_a_turn_that_changed_something_is_not_joined(self) -> None:
        session = self.listener()
        self.first_half(session)
        session.curated_protocol_session._revision += 1
        kinds = [e.kind for e in self.feed(session, run(11) + SILENT * 3)]
        self.assertEqual(kinds[0], "barge_in_candidate")

    def test_words_while_the_answer_plays_interrupt_as_before(self) -> None:
        session = self.listener()
        first = self.first_half(session)
        self.assertTrue(session.start_playback(first.turn_id))
        kinds = [e.kind for e in self.feed(session, run(11) + SILENT * 3)]
        self.assertEqual(kinds[0], "barge_in_candidate")
        self.assertNotIn("assistant.interrupted", kinds)

    def test_an_unfinished_first_half_waits_a_little_longer(self) -> None:
        session = self.listener()
        first = self.first_half(session)
        session.hold_fragment(first.turn_id, first.generation, "지금 이 단계에서", ended_at=self.clock())
        self.clock.now += CONTINUATION_JOIN_SECONDS + 0.3
        kinds = [e.kind for e in self.feed(session, run(11) + SILENT * 3)]
        self.assertEqual(kinds[:2], ["assistant.interrupted", "speech.start"], kinds)
        self.assertIsNone(session.held_fragment(), "the held words are in the joined audio now")

    def test_words_still_being_written_down_wait_a_little_longer_too(self) -> None:
        session = self.listener()
        first = self.first_half(session)
        session.advance_turn_progress(first.turn_id, first.generation, "transcribing")
        self.clock.now += CONTINUATION_JOIN_SECONDS + 0.3
        kinds = [e.kind for e in self.feed(session, run(11) + SILENT * 3)]
        self.assertEqual(kinds[:2], ["assistant.interrupted", "speech.start"], kinds)

    def test_speech_running_into_the_playback_is_not_cut(self) -> None:
        session = self.listener()
        first = self.first_half(session)
        self.clock.now += CONTINUATION_JOIN_SECONDS + 0.5  # too late to join
        events = self.feed(session, run(4))
        self.assertEqual([e.kind for e in events], ["barge_in_candidate"])
        self.assertTrue(session.start_playback(first.turn_id))
        events = self.feed(session, run(8, amplitude=0.1)[4 * FRAME_BYTES:] + run(4) + SILENT * 3)
        ready = [e for e in events if e.kind == "barge_in_audio_ready"]
        self.assertEqual(len(ready), 1, [e.kind for e in events])
        self.assertEqual(runs(ready[0].result.utterance), [12], "the start of the words was cut")

    def test_speech_under_way_when_the_turn_before_is_dropped_becomes_a_turn(self) -> None:
        session = self.listener()
        first = self.first_half(session)
        self.clock.now += CONTINUATION_JOIN_SECONDS + 0.5
        self.assertEqual([e.kind for e in self.feed(session, run(4))], ["barge_in_candidate"])
        self.assertTrue(session.reject_empty_transcript(first.turn_id))
        self.assertEqual(session.state, TurnState.USER_SPEAKING)
        events = self.feed(session, run(6) + SILENT * 3)
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, ["speech.start", "speech.end"], kinds)
        self.assertNotEqual(events[0].turn_id, first.turn_id)
        self.assertEqual(runs(events[-1].result.utterance), [10])


if __name__ == "__main__":
    unittest.main()
