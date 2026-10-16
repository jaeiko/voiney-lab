"""Lane FX3, decision 6: the end of the words still waits 1.0 s of silence; 1.2 s is a setting.

The person chose to try 1.2 s (2026-10-16) after lane SP2's pause bench. The
default moved to 1200 ms and lane SP2's bench, run before and after, agreed
with that report: a long sentence with fillers split in two 61 % -> 46 % of
the time, every turn ended about 0.2 s later. A wider run of the same bench
then showed what the pause bench had no room for: with TV talk in the room,
the short pauses between the TV's sentences (1.0-1.2 s) no longer ended the
wearer's words, and up to two thirds of the commands ran to the 15 s bound.
The person decided the same day to keep 1000 ms (lane FX3 report, section on
decision 6). These tests pin the default, and that both ends of the words --
the VAD's silence and lane SP2's level endpoint -- wait the one setting, so
VOINEY_LAB_CASCADE_VAD_ENDPOINT_SILENCE_MS=1200 tries 1.2 s whole.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_lane_sp2_level_endpoint import CONFIG, LOUD, QUIET, THRESHOLD_DB, voiced_if_loud
from voiney_lab.configuration import CascadeVadSettings
from voiney_lab.server import ListenerSession
from voiney_lab.setting_names import BY_NAME
from voiney_lab.vad import VAD_END_SILENCE_MS, EndpointDetector, VadConfig

ROOT = Path(__file__).resolve().parents[1]
NAME = "VOINEY_LAB_CASCADE_VAD_ENDPOINT_SILENCE_MS"


def _commit_index(config: VadConfig, frames: list[bytes], *, line: float | None = None) -> tuple[int, str]:
    found = EndpointDetector(config, classifier=voiced_if_loud, listening_onset=True)
    found.wearer_threshold_db = line
    for index, frame in enumerate(frames):
        result = found.process(frame)
        if result.utterance is not None:
            return index, result.endpoint
    raise AssertionError("no commit")


class DefaultTests(unittest.TestCase):
    def test_every_place_that_states_the_default_says_1000(self) -> None:
        self.assertEqual(VAD_END_SILENCE_MS, 1000)
        self.assertEqual(CascadeVadSettings().endpoint_silence_ms, 1000)
        self.assertEqual(CascadeVadSettings.from_environment({}).endpoint_silence_ms, 1000)
        self.assertEqual(BY_NAME[NAME].default, "1000")
        example = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        self.assertIn(f"{NAME}=1000", example)
        self.assertEqual(VadConfig().endpoint_silence_frames, 50)
        self.assertEqual(ListenerSession().detector.config.endpoint_silence_frames, 50)

    def test_the_setting_takes_1200(self) -> None:
        settings = CascadeVadSettings.from_environment({NAME: "1200"})
        self.assertEqual(settings.endpoint_silence_ms, 1200)
        self.assertEqual(VadConfig.from_settings(settings).endpoint_silence_frames, 60)


class SameTimeForBothEndsTests(unittest.TestCase):
    """The VAD's silence and lane SP2's level both wait the endpoint time."""

    def _config(self, ms: int) -> VadConfig:
        settings = CascadeVadSettings.from_environment({NAME: str(ms)})
        frames = VadConfig.from_settings(settings).endpoint_silence_frames
        return VadConfig(**{**CONFIG.__dict__, "endpoint_silence_frames": frames,
                            "maximum_utterance_frames": 400})

    def test_silence_ends_the_words_after_the_set_time(self) -> None:
        silent = [b"\0" * len(QUIET[0])] * 100
        for ms, frames in ((1200, 60), (1000, 50)):
            with self.subTest(ms=ms):
                index, kind = _commit_index(self._config(ms), LOUD[:10] + silent)
                self.assertEqual(kind, "silence")
                self.assertEqual(index, 10 + frames - 1)

    def test_the_level_ends_the_words_after_the_same_time(self) -> None:
        room = (QUIET * 2)[:100]
        for ms, frames in ((1200, 60), (1000, 50)):
            with self.subTest(ms=ms):
                index, kind = _commit_index(self._config(ms), QUIET[:20] + LOUD[:6] + room, line=THRESHOLD_DB)
                self.assertEqual(kind, "level")
                self.assertEqual(index, 20 + 6 + frames - 1)


if __name__ == "__main__":
    unittest.main()
