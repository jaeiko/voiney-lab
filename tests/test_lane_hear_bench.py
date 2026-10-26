"""Lane HEAR (2026-10-25): the listening bench's four numbers after the fixes.

``scripts/hear_listening_bench.py`` drives the real voice socket the way the
current screen does during a run, with synthetic microphone frames, a fake
speech-to-text and a fake speech output (no provider is called). Before
lane HEAR it measured, on the same script: 3 of 9 wearer utterances
dropped, 4 of 4 sentences with a pause split, 3 answers with sound while
paused and 1 noise question (lane report, section 3). These are the
numbers after.
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


if __name__ == "__main__":
    unittest.main()
