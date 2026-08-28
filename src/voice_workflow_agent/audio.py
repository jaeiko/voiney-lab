"""Pure, dependency-free PCM helpers for the M2 Plumber audio path."""

from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import Iterable

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = SAMPLES_PER_FRAME * SAMPLE_WIDTH


def clamp_pcm16(value: float | int) -> int:
    return max(-32768, min(32767, round(value)))


def samples_to_pcm16(samples: Iterable[float | int]) -> bytes:
    values = [clamp_pcm16(sample) for sample in samples]
    return struct.pack(f"<{len(values)}h", *values)


def pcm16_to_samples(pcm: bytes) -> list[int]:
    if len(pcm) % SAMPLE_WIDTH:
        raise ValueError("PCM16 data must contain an even number of bytes")
    return list(struct.unpack(f"<{len(pcm) // SAMPLE_WIDTH}h", pcm))


def pcm16_rms(pcm: bytes) -> float:
    """Return one frame's RMS level normalised to ``0.0 .. 1.0``.

    Normalising by full scale keeps every level threshold in this repository
    comparable across capture hardware. This is digital amplitude only: it is
    never a sound-pressure level and must not be reported in dBA.
    """

    samples = pcm16_to_samples(pcm)
    if not samples:
        return 0.0
    total = sum(float(sample) * float(sample) for sample in samples)
    return math.sqrt(total / len(samples)) / 32768.0


class FrameBuffer:
    """Turn arbitrarily sized PCM chunks into exact 20 ms frames."""

    def __init__(self, frame_bytes: int = FRAME_BYTES) -> None:
        if frame_bytes <= 0:
            raise ValueError("frame_bytes must be positive")
        self.frame_bytes = frame_bytes
        self._partial = bytearray()

    @property
    def partial_bytes(self) -> int:
        return len(self._partial)

    def push(self, chunk: bytes) -> list[bytes]:
        self._partial.extend(chunk)
        frames = []
        while len(self._partial) >= self.frame_bytes:
            frames.append(bytes(self._partial[: self.frame_bytes]))
            del self._partial[: self.frame_bytes]
        return frames

    def finish(self, pad: bool = False) -> bytes | None:
        if not self._partial:
            return None
        tail = bytes(self._partial)
        self._partial.clear()
        if pad:
            return tail + bytes(self.frame_bytes - len(tail))
        return tail


def resample_linear(samples: list[int], source_rate: int, target_rate: int) -> list[int]:
    """Small, readable linear resampler suitable for this teaching demo."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if not samples or source_rate == target_rate:
        return samples.copy()
    output_length = round(len(samples) * target_rate / source_rate)
    output = []
    for index in range(output_length):
        position = index * source_rate / target_rate
        left = min(int(position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        output.append(clamp_pcm16(samples[left] * (1 - fraction) + samples[right] * fraction))
    return output


def mulaw_encode_sample(sample: int) -> int:
    """Encode signed PCM16 to an ITU-T G.711 μ-law byte."""
    bias = 0x84
    clip = 32635
    sample = clamp_pcm16(sample)
    sign = 0x80 if sample < 0 else 0
    magnitude = min(abs(sample), clip) + bias
    exponent = max(0, magnitude.bit_length() - 8)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def mulaw_decode_byte(encoded: int) -> int:
    """Decode one ITU-T G.711 μ-law byte to signed PCM16."""
    value = (~encoded) & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    magnitude = ((mantissa << 3) + 0x84) << exponent
    sample = magnitude - 0x84
    return -sample if sign else sample


def mulaw_encode(pcm: bytes) -> bytes:
    return bytes(mulaw_encode_sample(sample) for sample in pcm16_to_samples(pcm))


def mulaw_decode(encoded: bytes) -> bytes:
    return samples_to_pcm16(mulaw_decode_byte(value) for value in encoded)


def clean_path(pcm: bytes) -> bytes:
    """Validate and preserve 16 kHz mono PCM16."""
    pcm16_to_samples(pcm)
    return bytes(pcm)


def phone_path(pcm: bytes) -> bytes:
    """Simulate an 8 kHz μ-law phone hop, returning 16 kHz PCM16."""
    source = pcm16_to_samples(pcm)
    at_8khz = resample_linear(source, SAMPLE_RATE, 8_000)
    through_mulaw = pcm16_to_samples(mulaw_decode(mulaw_encode(samples_to_pcm16(at_8khz))))
    return samples_to_pcm16(resample_linear(through_mulaw, 8_000, SAMPLE_RATE))


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    pcm16_to_samples(pcm)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()
