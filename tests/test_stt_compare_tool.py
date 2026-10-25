"""Lane HEAR (2026-10-25): the offline provider comparison tool scores without calling anyone.

``scripts/stt_compare.py`` reads the opt-in STT diagnostic folder and a
reference file a person wrote, and scores word and character error, numbers
with their units and protocol terms. By default it scores the transcript
recorded at the time; providers are called only with ``--live``, which this
test replaces with a fake transcriber (no provider is reached).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from voiney_lab.audio import pcm_to_wav

ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("stt_compare", ROOT / "scripts" / "stt_compare.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SttCompareToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = load_tool()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        pcm = b"\x01\x00" * 16000
        for identity, raw in (("t1", "젤 플러그를 37도에서 15분 두세요"), ("t2", "")):
            (self.dir / f"{identity}.wav").write_bytes(pcm_to_wav(pcm))
            (self.dir / f"{identity}.json").write_text(json.dumps(
                {"raw_transcript": raw, "keyterms": ["AMBIC", "젤 플러그"]}, ensure_ascii=False), encoding="utf-8")
        self.refs = self.dir / "refs.jsonl"
        self.refs.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in (
            {"identity": "t1", "reference": "젤 플러그를 37도에서 15분 두세요"},
            {"identity": "t2", "reference": "AMBIC 1.5 mL 넣었어요"},
        )), encoding="utf-8")

    def test_the_recorded_transcripts_are_scored_without_any_request(self) -> None:
        scores, calls = self.tool.compare(self.dir, self.tool.load_references(self.refs))
        self.assertEqual(calls, 0)
        summary = scores["recorded"].summary()
        self.assertEqual(summary["turns"], 2)
        self.assertEqual(summary["empty_transcripts"], 1)
        self.assertEqual(summary["number_units"], 3)
        self.assertEqual(summary["number_unit_accuracy"], round(2 / 3, 4))
        self.assertEqual(summary["terms"], 2)
        self.assertEqual(summary["term_accuracy"], 0.5)
        self.assertGreater(summary["wer"], 0)

    def test_providers_are_called_once_a_turn_only_when_asked(self) -> None:
        heard = []

        def fake(pcm, name, language, keyterms):
            heard.append((name, language, keyterms))
            return "AMBIC 1.5 mL 넣었어요" if name == "elevenlabs" else ""

        scores, calls = self.tool.compare(
            self.dir, self.tool.load_references(self.refs),
            providers=("google_cloud", "elevenlabs"), transcriber=fake)
        self.assertEqual(calls, 4)
        self.assertEqual({name for name, *_ in heard}, {"google_cloud", "elevenlabs"})
        self.assertTrue(all(language == "ko" and "AMBIC" in keyterms for _, language, keyterms in heard))
        self.assertEqual(scores["google_cloud"].summary()["empty_transcripts"], 2)
        self.assertEqual(scores["elevenlabs"].summary()["empty_transcripts"], 0)

    def test_the_error_rates_and_number_units(self) -> None:
        self.assertEqual(self.tool.edit_distance(["a", "b", "c"], ["a", "c"]), 1)
        self.assertEqual(self.tool.number_units("37 ℃ 에서 1.5 mL"), [("37", "°c"), ("1.5", "ml")])
        self.assertEqual(self.tool.number_units("37도"), [("37", "°c")])

    def test_the_command_refuses_an_unknown_provider(self) -> None:
        with self.assertRaises(SystemExit):
            self.tool.main([str(self.dir), str(self.refs), "--live", "whisper"])


if __name__ == "__main__":
    unittest.main()
