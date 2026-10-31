"""Lane SAFE2, item 1 (a): a continuation join puts back the quieter words the end trimmed.

The 2026-10-31 live row "관찰 기록해 줘: 측정값 100 mL" in centrifuge noise:
the offline replay of the same cached audio through the merged listening
code showed the sentence split in two by the relative gate (its quieter
middle second counted as silence), the two pieces joined (lane HEAR,
decision 4) -- and the join dropping the frames between them: the
recognizer got the first 89 frames and the last 49 of a 162-frame sentence,
and wrote 8 of 14 characters wrong, the number among them. The pre-LIVE3
code lost 45 frames the same way (three pieces).

Now an end carries the frames it trimmed that the classifier itself still
called voiced -- the ones the relative gate or the wearer's level overruled;
nothing in a quiet room -- and a join puts them back between the two
pieces, so one transcription hears the whole sentence. Nothing about when
a turn ends, when it joins, or how an impact is told from speech changes:
lane LIVE3's and LISTEN3's rules are as they were, and in a quiet room the
joined utterance is exactly what it was. Checks cross the production
``EndpointDetector`` and the ``ListenerSession.accept_chunk`` -> ``run_turn``
boundary with a deterministic classifier; the carrier is a tone and a
noise bed, not Korean speech.
"""

from __future__ import annotations

import asyncio
import random
import unittest

from scripts.sim.audio import oracle_audio
from scripts.sim.corpus import generate_cases, load_corpus
from scripts.sim.runner import run_case
from tests.test_lane_live3_listening import Clock, feed, production_session
from voiney_lab.audio import FRAME_BYTES, samples_to_pcm16


def noise_bed(frames: int, amplitude: int, seed: int = 7) -> bytes:
    """Steady room noise the classifier calls voiced and the relative gate learns as the floor."""

    rng = random.Random(seed)
    return samples_to_pcm16(amplitude * rng.uniform(-1, 1) for _ in range(frames * 320))


def frames_of(pcm: bytes) -> list[bytes]:
    return [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]


def quiet_words(seed: int = 11) -> bytes:
    """The sentence's quieter second in steady noise: the words at a sixtieth of their level over the bed.

    The classifier still calls these frames voiced; the relative gate, whose
    floor the bed set, counts them as silence -- the live row's shape.
    """

    bed = noise_bed(50, 60, seed=seed)
    words = oracle_audio()
    return samples_to_pcm16(
        int.from_bytes(bed[i:i + 2], "little", signed=True) + int.from_bytes(words[i:i + 2], "little", signed=True) // 60
        for i in range(0, len(bed), 2)
    )


class TrimmedFramesTests(unittest.TestCase):
    """The production listening lineage (lane LIVE3's deterministic classifier and gates)."""

    def test_an_end_in_steady_noise_carries_the_quieter_words_it_trimmed(self) -> None:
        session = production_session(Clock())
        bed, words, quieter = noise_bed(50, 60), oracle_audio(), quiet_words()
        events = feed(session, frames_of(bed + words + quieter))
        ended = [event for event in events if event.kind == "speech.end"]
        self.assertEqual(len(ended), 1, [event.kind for event in events])
        self.assertEqual(ended[0].result.utterance, bed + words, "the end still trims the quieter frames")
        self.assertEqual(ended[0].result.trailing, quieter, "the trimmed frames the classifier called voiced travel with the end")

    def test_an_end_in_silence_carries_nothing_and_a_tail_is_at_most_the_ends_second(self) -> None:
        session = production_session(Clock())
        events = feed(session, frames_of(oracle_audio() + bytes(FRAME_BYTES * 60)))
        ended = [event for event in events if event.kind == "speech.end"]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].result.utterance, oracle_audio())
        self.assertEqual(ended[0].result.trailing, b"")
        session = production_session(Clock())
        events = feed(session, frames_of(noise_bed(50, 60) + oracle_audio() + noise_bed(120, 60, seed=3)))
        ended = [event for event in events if event.kind == "speech.end"]
        self.assertEqual(len(ended), 1)
        self.assertLessEqual(len(ended[0].result.trailing), 50 * FRAME_BYTES, "a tail is bounded by the endpoint silence")


class VoiceBoundaryTests(unittest.TestCase):
    """Through the SIM runner: a sentence split by its quieter second in noise reaches speech recognition whole."""

    def setUp(self) -> None:
        self.protocol = load_corpus()[0]
        self.case = next(c for c in generate_cases(self.protocol) if c.family == "complete" and c.text == "다음")
        self.words = oracle_audio()

    def heard(self, pcm: bytes) -> dict:
        return asyncio.run(run_case(self.protocol, self.case, pcm=pcm, classifier=lambda frame: any(frame)))

    def test_in_steady_noise_the_quiet_middle_is_sent_too(self) -> None:
        pcm = noise_bed(50, 60) + self.words + noise_bed(50, 60, seed=11) + self.words
        result = self.heard(pcm)
        self.assertEqual(result["endpoint_count"], 2, result["drop_reasons"])
        self.assertEqual(result["continuation_joins"], 1)
        self.assertEqual(result["stt_calls"], 1)
        # The one transcription hears every frame: both halves and the quiet second between.
        self.assertEqual(result["endpoint_audio_frames"][-1], len(pcm) // FRAME_BYTES)
        self.assertEqual(result["action"], "next")

    def test_in_a_quiet_room_the_joined_utterance_is_as_it_was(self) -> None:
        pcm = self.words + bytes(FRAME_BYTES * 50) + self.words
        result = self.heard(pcm)
        self.assertEqual(result["endpoint_count"], 2)
        self.assertEqual(result["continuation_joins"], 1)
        self.assertEqual(result["stt_calls"], 1)
        # Lane HEAR's join as before: the two halves, no silence put between them.
        self.assertEqual(result["endpoint_audio_frames"][-1], 2 * len(self.words) // FRAME_BYTES)


if __name__ == "__main__":
    unittest.main()
