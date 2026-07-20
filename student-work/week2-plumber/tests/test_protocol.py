import json
import unittest

from protocol import ProtocolError, audio_start, event, parse_control


class ProtocolTests(unittest.TestCase):
    def test_start_defaults_to_clean(self):
        self.assertEqual(parse_control('{"type":"capture.start"}'),
                         {"type": "capture.start", "mode": "clean"})

    def test_all_modes_are_valid(self):
        for mode in ("clean", "phone", "compare"):
            self.assertEqual(parse_control(json.dumps({"type": "capture.start", "mode": mode}))["mode"], mode)

    def test_stop_discards_extra_fields(self):
        self.assertEqual(parse_control('{"type":"capture.stop","extra":1}'), {"type": "capture.stop"})

    def test_bad_messages_are_rejected(self):
        for raw in ("no", "[]", '{}', '{"type":"capture.start","mode":"studio"}', '{"type":"wat"}'):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                parse_control(raw)

    def test_event_is_compact_json(self):
        self.assertEqual(event("ready", sample_rate=16000), '{"type":"ready","sample_rate":16000}')

    def test_audio_start_describes_exact_contract(self):
        message = json.loads(audio_start("tts", 3))
        self.assertEqual(message, {
            "type": "audio.start", "stream": "tts", "encoding": "pcm_s16le",
            "sample_rate": 16000, "channels": 1, "frame_ms": 20, "frame_count": 3,
        })

    def test_invalid_audio_metadata_is_rejected(self):
        with self.assertRaises(ProtocolError):
            audio_start("", -1)


if __name__ == "__main__":
    unittest.main()

