"""Lane HEAR2, decision 4 (2026-10-26): a dropped sound is shown with its reason, never said.

The person's test: a little noise made the server say "잘 안 들렸어요. 다시
말씀해 주세요." again and again (lane HEAR's rule: two drops in 30 s, said
once until words were heard), which was hard to bear. Every long stretch
of room noise the VAD takes for speech comes back from the provider as an
empty transcript, so the sentence came with the noise.

Now nothing is said for a drop, however many come in a row. Each
``speech.dropped`` event carries ``reason_ko``, the reason in plain words
for the screen ("너무 짧은 소리였어요", "말이 아닌 소리였어요", …), beside the
short notice; the log and the metrics keep the code as before.
"""

from __future__ import annotations

import unittest

from tests.test_lane_hear_drops import DropHarness, utterance
from voiney_lab import server as server_module
from voiney_lab.server import DROP_REASON_WORDS_KO, drop_reason_words


class DropWordsTests(DropHarness):
    def test_three_empty_transcripts_in_a_row_say_nothing_and_each_names_its_reason(self) -> None:
        session, clock = self.listener()
        sent = self.run_socket(session, clock, [
            {"bytes": utterance(0.1)}, {"sleep": 0.3},
            {"bytes": utterance(0.1)}, {"sleep": 0.3},
            {"advance": 2.0},
            {"bytes": utterance(0.1)}, {"sleep": 0.5},
        ])
        dropped = self.of(sent, "speech.dropped")
        self.assertEqual([m["reason"] for m in dropped], ["empty_transcript"] * 3)
        for message in dropped:
            self.assertEqual(message["notice"], "잘 안 들렸어요")
            self.assertEqual(message["reason_ko"], "받아 적은 말이 없어요")
        self.assertEqual([m for m in self.of(sent, "server.words") if m["words_kind"] == "drop_notice"], [],
                         "a drop is shown, never said (decision 4)")
        self.assertFalse(hasattr(server_module, "DROP_NOTICE_WORDS"), "the spoken drop notice is gone")

    def test_every_reason_the_server_drops_for_has_plain_words(self) -> None:
        for reason in (
            "empty_transcript", "self_echo", "minimum_voiced_frames", "language_unclear",
            "filler_only", "non_lexical_event", "non_speech", "keyterm_echo",
            "transcription_failed", "provider_no_speech_probability", "provider_low_confidence",
            "other_speaker_level", "paused", "stale_candidate", "barge_in_rejected", "stt_failed",
        ):
            with self.subTest(reason=reason):
                words = drop_reason_words(reason)
                self.assertTrue(words and words == DROP_REASON_WORDS_KO[reason], words)
                self.assertNotIn("_", words, "a code reached the words")
        self.assertEqual(drop_reason_words("something_new"), DROP_REASON_WORDS_KO["unknown"])


if __name__ == "__main__":
    unittest.main()
