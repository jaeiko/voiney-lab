"""Local deterministic noise, SNR mixing and the product PCM resampler (no assets)."""
from __future__ import annotations

import io
import math
import random
import wave

from voiney_lab.audio import pcm16_to_samples, samples_to_pcm16, resample_linear

NOISES = ('fan', 'centrifuge', 'ultrasonic', 'babble', 'keyboard')


def read_wav(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), 'rb') as w:
        if w.getsampwidth() != 2 or w.getnchannels() not in (1, 2) or w.getnframes() > w.getframerate() * 60:
            raise ValueError('needs bounded mono/stereo PCM16 WAV')
        samples = pcm16_to_samples(w.readframes(w.getnframes()))
        if w.getnchannels() == 2:
            samples = [round((samples[i] + samples[i+1])/2) for i in range(0, len(samples), 2)]
        return samples_to_pcm16(resample_linear(samples, w.getframerate(), 16000))


def noise(kind: str, length: int, seed: int = 41) -> list[float]:
    """Procedural approximations, owned by this project; babble is not intelligible speech."""
    if kind not in NOISES:
        raise ValueError('unknown noise')
    rng = random.Random(seed)
    out = []
    low = 0.0
    for i in range(length):
        t = i / 16000
        white = rng.uniform(-1, 1)
        low = .96 * low + .04 * white
        if kind == 'fan':
            v = low + .12 * math.sin(2*math.pi*90*t)
        elif kind == 'centrifuge':
            v = .5 * math.sin(2*math.pi*(170*t + 4*t*t)) + .2*white
        elif kind == 'ultrasonic':
            v = .4*math.sin(2*math.pi*6200*t) + .2*white
        elif kind == 'babble':
            v = sum(math.sin(2*math.pi*f*t) * (1+math.sin(2*math.pi*r*t)) for f,r in ((171,3),(243,4.3),(419,2.7))) / 6
        else:
            v = white * math.exp(-((i % 4400)/100))
        out.append(v)
    return out


def mix(pcm: bytes, kind: str, snr_db: float, seed: int = 41, tail_samples: int = 0) -> tuple[bytes, float]:
    if not math.isfinite(snr_db) or not -20 <= snr_db <= 80:
        raise ValueError('SNR out of range')
    signal = pcm16_to_samples(pcm)
    if not signal:
        raise ValueError('empty signal')
    if not 0<=tail_samples<=16000*10:
        raise ValueError('noise tail out of range')
    bed = noise(kind, len(signal)+tail_samples, seed)
    # The clean utterance defines SNR; appended noise-only tail does not dilute it.
    power = sum(v*v for v in signal)/len(signal)
    npower = sum(v*v for v in bed[:len(signal)])/len(signal)
    if power == 0 or npower == 0:
        raise ValueError('SNR needs nonzero signal and noise')
    scale = math.sqrt(power / (npower * 10**(snr_db/10)))
    combined = [(signal[i] if i<len(signal) else 0) + n*scale for i,n in enumerate(bed)]
    peak = max(abs(v) for v in combined)
    gain = min(1.0, 30000/max(1, peak))
    return samples_to_pcm16(v*gain for v in combined), gain


def change_speed(pcm: bytes, rate: float) -> bytes:
    """Local resampling: speed AND pitch change. No claim of native TTS rate support."""
    if not math.isfinite(rate) or not .5 <= rate <= 2:
        raise ValueError('speed out of range')
    return samples_to_pcm16(resample_linear(pcm16_to_samples(pcm), round(16000*rate), 16000))


def oracle_audio(silent: bool = False) -> bytes:
    # A carrier for deterministic VAD/STT fixtures, not synthetic spoken Korean.
    return samples_to_pcm16(0 if silent else 4000*math.sin(2*math.pi*220*i/16000) for i in range(16000))
