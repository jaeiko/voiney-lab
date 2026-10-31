"""Lane STT1: the reading list, the recording page and the two person-run commands.

The 60-sentence list is drawn from the development split only, even across
the four fields, the four kinds of utterance and three lengths; the page
sends nothing anywhere; the scoring and lead-in commands plan without a
single external request and refuse malformed input.
"""

from __future__ import annotations

import json
import re
import struct
import tempfile
import unittest
import wave
from collections import Counter
from pathlib import Path

from scripts.sim.corpus import generate_cases, load_corpus
from scripts.sim.record_kit import build
from scripts.sim.split import load_case_id_manifest, partition_cases

KIT = Path(build.__file__).resolve().parent
PAGE = KIT / "index.html"


class ReadingListTests(unittest.TestCase):
    def test_the_committed_list_matches_the_builder(self) -> None:
        self.assertEqual(build.main(["--check"]), 0)

    def test_sixty_development_sentences_even_across_fields(self) -> None:
        rows = build.reading_list()
        manifest = load_case_id_manifest(build.MANIFEST)
        self.assertEqual([row["id"] for row in rows], list(manifest.case_ids))
        self.assertEqual(len(rows), 60)
        self.assertEqual(Counter(row["field"] for row in rows), {"biology": 24, "chemistry": 12, "materials": 12, "soil": 12})
        self.assertEqual(set(row["kind"] for row in rows), {"command", "question", "record", "other"})
        self.assertEqual(set(row["length"] for row in rows), {"short", "medium", "long"})
        self.assertEqual(len({row["text"] for row in rows}), 60)
        protocols = load_corpus()
        pairs = tuple((p, c) for p in protocols for c in generate_cases(p))
        holdout = {c.code for _p, c in partition_cases(pairs, seed=1027, holdout_fraction=.3).holdout}
        self.assertFalse(holdout & {row["id"] for row in rows})

    def test_the_sentence_file_is_what_the_page_reads(self) -> None:
        text = build.SENTENCES.read_text(encoding="utf-8")
        payload = json.loads(text.split("= ", 1)[1].rstrip(";\n"))
        self.assertEqual(payload["kit"], build.MANIFEST_NAME)
        self.assertEqual(payload["conditions"], ["quiet", "typing_after", "fan"])
        self.assertEqual(payload["microphones"], ["device", "lavalier"])
        self.assertEqual(len(payload["sentences"]), 60)


class PageTests(unittest.TestCase):
    def test_the_page_sends_nothing_and_records_sixteen_khz_mono_wav(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertNotRegex(page, r"https?://")
        for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket(", "<link"):
            self.assertNotIn(forbidden, page)
        self.assertIn('<script src="sentences.js"></script>', page)
        self.assertIn("getUserMedia", page)
        self.assertIn("TARGET_RATE = 16000", page)
        self.assertIn('"manifest.json"', page)
        # The product's microphone processing is asked for the same way.
        self.assertIn("echoCancellation: true, noiseSuppression: true, autoGainControl: true", page)
        for condition in ("quiet", "typing_after", "fan"):
            self.assertIn(f'value="{condition}"', page)
        for microphone in ("device", "lavalier"):
            self.assertIn(f'value="{microphone}"', page)


def _silent_wav(path: Path, seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(struct.pack("<h", 0) * int(16000 * seconds))


class RecordingsCommandTests(unittest.TestCase):
    def _bundle(self, root: Path, items: list[dict]) -> None:
        (root / "wav").mkdir(parents=True)
        for item in items:
            _silent_wav(root / item["file"])
        (root / "manifest.json").write_text(json.dumps({"schema": 1, "kit": build.MANIFEST_NAME, "items": items}), encoding="utf-8")

    def test_the_plan_names_the_requests_and_makes_none(self) -> None:
        from scripts.sim import recordings
        rows = build.reading_list()[:3]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "voice-eval" / "s1_quiet_device"
            self._bundle(root, [
                {"id": row["id"], "file": f"wav/{row['id']}__quiet__device__s1.wav", "condition": "quiet", "microphone": "device", "speaker": "s1"}
                for row in rows
            ])
            items = recordings.load_bundles(Path(tmp) / "voice-eval")
            self.assertEqual(len(items), 3)
            matched = recordings.match_cases(items)
            self.assertEqual([case.code for _item, _protocol, case in matched], [row["id"] for row in rows])
            import io
            from contextlib import redirect_stdout
            out = io.StringIO()
            with redirect_stdout(out):
                code = recordings.main(["--recordings", str(Path(tmp) / "voice-eval"), "--output", str(Path(tmp) / "never.json")])
            self.assertEqual(code, 0)
            plan = json.loads(out.getvalue())
            self.assertEqual(plan["recordings"], 3)
            self.assertEqual(plan["cases"], 12)
            self.assertEqual(plan["stt_requests_at_most"], 6)
            self.assertEqual(plan["external_requests_this_dry_run"], 0)

    def test_malformed_bundles_are_refused_before_any_request(self) -> None:
        from scripts.sim import recordings
        row = build.reading_list()[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "voice-eval" / "bad"
            self._bundle(root, [{"id": row["id"], "file": f"wav/{row['id']}__quiet__device__s1.wav", "condition": "quiet", "microphone": "device", "speaker": "s1"}])
            for broken in (
                {"file": "../escape.wav"}, {"condition": "street"}, {"microphone": "boom"}, {"speaker": "a b"}, {"id": "no.such.case"},
            ):
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                manifest["items"][0].update(broken)
                (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(broken), self.assertRaises(ValueError):
                    recordings.match_cases(recordings.load_bundles(Path(tmp) / "voice-eval"))
                self._bundle(Path(tmp) / "voice-eval" / f"again{len(broken)}{list(broken)[0]}", [{"id": row["id"], "file": f"wav/{row['id']}__quiet__device__s1.wav", "condition": "quiet", "microphone": "device", "speaker": "s1"}])
                (root / "manifest.json").write_text(json.dumps({"schema": 1, "kit": build.MANIFEST_NAME, "items": [{"id": row["id"], "file": f"wav/{row['id']}__quiet__device__s1.wav", "condition": "quiet", "microphone": "device", "speaker": "s1"}]}), encoding="utf-8")


class LeadInProbeTests(unittest.TestCase):
    def test_repeat_detection_and_a_dry_plan(self) -> None:
        from scripts.sim import lead_in_probe
        self.assertTrue(lead_in_probe.repeated("다음. 다음."))
        self.assertTrue(lead_in_probe.repeated("1번 튜브에 1번 튜브에"))
        self.assertFalse(lead_in_probe.repeated("다음 단계로"))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name, text, origin in (("a", "다음. 다음.", "barge_in"), ("b", "네", "barge_in"), ("c", "타이머 시작", "barge_in")):
                _silent_wav(folder / f"{name}.wav", .8)
                (folder / f"{name}.json").write_text(json.dumps({"duration_ms": 800, "raw_transcript": text, "audio_origin": origin}), encoding="utf-8")
            import io
            from contextlib import redirect_stdout
            out = io.StringIO()
            with redirect_stdout(out):
                code = lead_in_probe.main(["--diagnostics-dir", str(folder), "--lead-in-ms", "as_recorded,0,300"])
            self.assertEqual(code, 0)
            plan = json.loads(out.getvalue())
            self.assertEqual((plan["clips"], plan["repeated_as_stored"], plan["requests_at_most"]), (2, 1, 6))
            self.assertEqual(plan["external_requests_this_dry_run"], 0)
            trimmed = lead_in_probe.trim_lead_in(bytes(640 * 50), 300)
            self.assertEqual(len(trimmed), 640 * 50)


if __name__ == "__main__":
    unittest.main()
