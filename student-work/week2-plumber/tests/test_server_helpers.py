import json
import unittest
from unittest.mock import patch

from audio import FRAME_BYTES, samples_to_pcm16
from server import frame_complete_audio, run_turn, validate_tts_pcm


class FakeResponse:
    def __init__(self, content=b"", status=200, content_type="audio/pcm", text=""):
        self.content = content
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = {"content-type": content_type}
        self.text = text


class ServerHelperTests(unittest.TestCase):
    def test_tts_accepts_successful_pcm_without_exact_content_type(self):
        self.assertEqual(validate_tts_pcm(FakeResponse(b"\x00\x00", content_type="application/octet-stream")), b"\x00\x00")

    def test_tts_rejects_errors_json_empty_and_odd_pcm(self):
        cases = [
            FakeResponse(b"bad", status=400, text="bad request"),
            FakeResponse(b'{}', content_type="application/json", text="{}"),
            FakeResponse(),
            FakeResponse(b"x"),
        ]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                validate_tts_pcm(response)

    def test_outbound_frames_are_always_twenty_ms(self):
        frames = frame_complete_audio(b"x" * (FRAME_BYTES + 2))
        self.assertEqual([len(frame) for frame in frames], [FRAME_BYTES, FRAME_BYTES])
        self.assertEqual(frames[1][:2], b"xx")


class FakeWebSocket:
    def __init__(self):
        self.text_messages = []
        self.binary_messages = []

    async def send_text(self, message):
        self.text_messages.append(json.loads(message))

    async def send_bytes(self, message):
        self.binary_messages.append(message)


class CompareTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_compare_transcribes_twice_but_runs_one_cascade(self):
        websocket = FakeWebSocket()
        source_pcm = samples_to_pcm16([1000, -1000] * 160)

        with (
            patch("server.transcribe", side_effect=["clean words", "phone words"]) as mock_stt,
            patch("server.chat", return_value="one reply") as mock_chat,
            patch("server.synthesize", return_value=source_pcm) as mock_tts,
        ):
            await run_turn(websocket, source_pcm, "compare", input_frames=1)

        self.assertEqual(mock_stt.call_count, 2)
        self.assertNotEqual(mock_stt.call_args_list[0].args[0], mock_stt.call_args_list[1].args[0])
        mock_chat.assert_called_once_with("clean words")
        mock_tts.assert_called_once_with("one reply")

        comparison = next(message for message in websocket.text_messages
                          if message["type"] == "comparison.result")
        self.assertEqual(comparison["clean_transcript"], "clean words")
        self.assertEqual(comparison["phone_transcript"], "phone words")
        self.assertIsInstance(comparison["clean_stt_ms"], int)
        self.assertIsInstance(comparison["phone_stt_ms"], int)

        done = next(message for message in websocket.text_messages
                    if message["type"] == "turn.done")
        self.assertIn("stt_clean", done["timings_ms"])
        self.assertIn("stt_phone", done["timings_ms"])


if __name__ == "__main__":
    unittest.main()

