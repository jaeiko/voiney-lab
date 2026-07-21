import json
import unittest

from protocol import ProtocolError, audio_start, event, parse_control


class ProtocolTests(unittest.TestCase):
    def test_session_controls(self):
        self.assertEqual(parse_control('{"type":"session.start","extra":1}'), {"type": "session.start"})
        self.assertEqual(parse_control('{"type":"session.stop"}'), {"type": "session.stop"})

    def test_playback_ended_requires_positive_integer_turn_id(self):
        self.assertEqual(parse_control('{"type":"playback.ended","turn_id":7}'),
                         {"type": "playback.ended", "turn_id": 7})
        for value in (None, 0, -1, True, "1"):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_control(json.dumps({"type": "playback.ended", "turn_id": value}))

    def test_bad_messages_are_rejected(self):
        for raw in ("no", "[]", '{}', '{"type":"capture.start"}', '{"type":"wat"}'):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                parse_control(raw)

    def test_event_is_compact_json(self):
        self.assertEqual(event("ready", sample_rate=16000), '{"type":"ready","sample_rate":16000}')

    def test_audio_start_describes_exact_contract_and_turn(self):
        self.assertEqual(json.loads(audio_start("tts", 3, 4)), {
            "type": "audio.start", "stream": "tts", "turn_id": 4,
            "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1,
            "frame_ms": 20, "frame_count": 3,
        })

    def test_invalid_audio_metadata_is_rejected(self):
        for args in (("", 1, 1), ("tts", -1, 1), ("tts", 1, 0)):
            with self.subTest(args=args), self.assertRaises(ProtocolError):
                audio_start(*args)


if __name__ == "__main__":
    unittest.main()
