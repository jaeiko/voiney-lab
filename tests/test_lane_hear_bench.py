"""Lane HEAR (2026-10-25): the listening bench's four numbers after the fixes.

``scripts/hear_listening_bench.py`` drives the real voice socket the way the
current screen does during a run, with synthetic microphone frames, a fake
speech-to-text and a fake speech output (no provider is called). Before
lane HEAR it measured, on the same script: 3 of 9 wearer utterances
dropped, 4 of 4 sentences with a pause split, 3 answers with sound while
paused and 1 noise question (lane report, section 3). These are the
numbers after. Lane HEAR2 added the timer stage, a noise chain and a
speech-to-text outage to the same script (its report, section 4).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_bench():
    spec = importlib.util.spec_from_file_location(
        "hear_listening_bench", ROOT / "scripts" / "hear_listening_bench.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # its dataclasses look their module up
    spec.loader.exec_module(module)
    return module


class ListeningBenchTests(unittest.TestCase):
    def test_no_wearer_word_dropped_no_sentence_split_nothing_said_in_a_pause_no_noise_question(self) -> None:
        bench = load_bench()
        logging.disable(logging.CRITICAL)
        try:
            result, _ = bench.run_bench()
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(result["wearer_utterances"], 9)
        self.assertEqual(result["wearer_dropped"], 0, result)
        self.assertEqual(result["split_sentences"], 4)
        self.assertEqual(result["split"], 0, result)
        self.assertEqual(result["paused_replies"], 0, result)
        self.assertEqual(result["noise_questions"], 0, result)
        self.assertTrue(result["resume_heard"], result)
        self.assertFalse(result["calibration_measured"], "a run measured the voice")
        # Lane HEAR2 (2026-10-26). Before (fe87ae5): 1 of 6 timer-stage
        # utterances unanswered ("타이머 일시정지해 줘" paused the run, and the
        # next words met the pause); the word said inside 30 s of room noise
        # never reached a transcript; the provider outage was 3 turn errors,
        # nothing shown or said.
        self.assertEqual(result["timer_stage_utterances"], 6)
        self.assertEqual(result["timer_stage_unanswered"], 0, result)
        self.assertTrue(result["timer_paused_the_timer"], result)
        self.assertFalse(result["timer_paused_the_run"], result)
        self.assertIsNotNone(result["noise_chain_answer_s"], "the word inside the noise was never heard")
        self.assertLess(result["noise_chain_answer_s"], 12.0, result)
        self.assertEqual(result["stt_failure_shown"], 3, result)
        self.assertEqual(result["stt_failure_said"], 1, result)
        self.assertEqual(result["stt_failure_turn_errors"], 0, result)


if __name__ == "__main__":
    unittest.main()
