import math
import unittest
import wave
from io import BytesIO

from audio import (
    FRAME_BYTES,
    FrameBuffer,
    clean_path,
    mulaw_decode,
    mulaw_decode_byte,
    mulaw_encode,
    mulaw_encode_sample,
    pcm16_to_samples,
    pcm_to_wav,
    phone_path,
    resample_linear,
    samples_to_pcm16,
)


class PcmTests(unittest.TestCase):
    def test_pcm_round_trip_and_clamping(self):
        pcm = samples_to_pcm16([-40_000, -1, 0, 1, 40_000])
        self.assertEqual(pcm16_to_samples(pcm), [-32768, -1, 0, 1, 32767])

    def test_odd_pcm_is_rejected(self):
        with self.assertRaises(ValueError):
            pcm16_to_samples(b"\x00")

    def test_clean_path_is_identity(self):
        pcm = samples_to_pcm16([-100, 0, 100])
        self.assertEqual(clean_path(pcm), pcm)


class FramingTests(unittest.TestCase):
    def test_partial_chunks_make_exact_frames(self):
        buffer = FrameBuffer()
        self.assertEqual(buffer.push(b"a" * 100), [])
        frames = buffer.push(b"b" * (FRAME_BYTES * 2))
        self.assertEqual([len(frame) for frame in frames], [FRAME_BYTES, FRAME_BYTES])
        self.assertEqual(buffer.partial_bytes, 100)
        self.assertEqual(buffer.finish(), b"b" * 100)

    def test_finish_can_pad_one_frame(self):
        buffer = FrameBuffer(4)
        buffer.push(b"ab")
        self.assertEqual(buffer.finish(pad=True), b"ab\x00\x00")


class MulawTests(unittest.TestCase):
    def test_known_g711_values(self):
        self.assertEqual(mulaw_encode_sample(0), 0xFF)
        self.assertEqual(mulaw_decode_byte(0xFF), 0)
        self.assertEqual(mulaw_decode_byte(0x7F), 0)
        self.assertEqual(mulaw_decode_byte(0x00), -32124)
        self.assertEqual(mulaw_decode_byte(0x80), 32124)

    def test_codec_preserves_sign_and_bounds_error(self):
        source = [-30_000, -10_000, -1_000, 0, 1_000, 10_000, 30_000]
        decoded = pcm16_to_samples(mulaw_decode(mulaw_encode(samples_to_pcm16(source))))
        self.assertEqual([value < 0 for value in decoded], [True, True, True, False, False, False, False])
        for expected, actual in zip(source, decoded):
            self.assertLessEqual(abs(expected - actual), max(130, abs(expected) * 0.04))

    def test_all_bytes_decode_and_reencode_stably(self):
        encoded = bytes(range(256))
        reencoded = mulaw_encode(mulaw_decode(encoded))
        # Positive/negative zero have two encodings; both canonically encode to 0xff/0x7f.
        differences = [i for i, (a, b) in enumerate(zip(encoded, reencoded)) if a != b]
        self.assertEqual(differences, [127])


class ProcessingTests(unittest.TestCase):
    def test_linear_resample_length_and_endpoints(self):
        self.assertEqual(resample_linear([0, 1000, 2000, 3000], 4, 8),
                         [0, 500, 1000, 1500, 2000, 2500, 3000, 3000])

    def test_phone_path_keeps_duration_and_changes_signal(self):
        samples = [round(12_000 * math.sin(2 * math.pi * 440 * i / 16_000)) for i in range(1600)]
        pcm = samples_to_pcm16(samples)
        processed = phone_path(pcm)
        self.assertEqual(len(processed), len(pcm))
        self.assertNotEqual(processed, pcm)

    def test_wav_header_matches_pcm_contract(self):
        pcm = samples_to_pcm16([0] * 320)
        data = pcm_to_wav(pcm)
        with wave.open(BytesIO(data), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16_000))
            self.assertEqual(wav.readframes(320), pcm)


if __name__ == "__main__":
    unittest.main()
