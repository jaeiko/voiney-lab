"""Local deterministic noise, SNR mixing and the product PCM resampler (no assets)."""
from __future__ import annotations

import io
import math
import random
import wave

from voiney_lab.audio import pcm16_to_samples, samples_to_pcm16, resample_linear

NOISES = (
    'fan', 'centrifuge', 'ultrasonic', 'babble', 'keyboard',
    'cleanbench_bsc_fan', 'fume_hood_exhaust', 'glovebox_circulation_pump',
    'fan_centrifuge', 'fan_adjacent_speech',
)

# LISTEN3 uses short procedural impact sounds, never recordings, to exercise
# the endpoint separately from steady room noise.  The labels describe the
# bench event being approximated; they are not acoustic measurements.
IMPACT_NOISES = ('typing', 'glass', 'pipette')
IMPACT_DISTANCES_CM = (30, 100)


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
        elif kind == 'keyboard':
            v = white * math.exp(-((i % 4400)/100))
        elif kind == 'cleanbench_bsc_fan':
            # Broad, steady cabinet fan with blade harmonics.
            v = 1.05*low + .10*math.sin(2*math.pi*92*t) + .04*math.sin(2*math.pi*184*t) + .03*white
        elif kind == 'fume_hood_exhaust':
            # Lower exhaust rumble with more air hiss than the cabinet profile.
            v = .82*low + .16*white + .12*math.sin(2*math.pi*57*t)
        elif kind == 'glovebox_circulation_pump':
            # A pump fundamental, harmonics and a slow load pulse.
            pulse = .65 + .35*math.sin(2*math.pi*2.2*t)
            v = .55*low + pulse*(.18*math.sin(2*math.pi*61*t) + .08*math.sin(2*math.pi*183*t)) + .05*white
        elif kind == 'fan_centrifuge':
            v = .70*low + .28*math.sin(2*math.pi*(170*t + 4*t*t)) + .10*white + .07*math.sin(2*math.pi*92*t)
        else:  # fan_adjacent_speech: deliberately unintelligible modulated tones.
            voices = sum(math.sin(2*math.pi*f*t) * (1+math.sin(2*math.pi*r*t)) for f,r in ((171,3),(243,4.3),(419,2.7))) / 6
            v = .65*low + .10*math.sin(2*math.pi*92*t) + .55*voices
        out.append(v)
    return out


def impact_pcm(kind: str, distance_cm: int, *, frames: int = 10) -> bytes:
    """Return a bounded deterministic pulse train for an impact SIM axis."""
    if kind not in IMPACT_NOISES:
        raise ValueError('unknown impact kind')
    if distance_cm not in IMPACT_DISTANCES_CM:
        raise ValueError('unsupported impact distance')
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= 100:
        raise ValueError('impact frame count out of range')
    rng = random.Random({'typing': 11, 'glass': 23, 'pipette': 37}[kind])
    gain = distance_gain(distance_cm)
    values = []
    for index in range(frames * 320):
        if kind == 'typing':
            within = index % 960
            value = rng.uniform(-1, 1) * math.exp(-within / 85)
        elif kind == 'glass':
            value = math.sin(2 * math.pi * 2550 * index / 16000) * math.exp(-index / 850)
        else:
            within = index % 1440
            value = math.sin(2 * math.pi * 3600 * index / 16000) * math.exp(-within / 45)
        values.append(round(32767 * .72 * gain * value))
    return samples_to_pcm16(values)


def append_impact(pcm: bytes, kind: str, distance_cm: int) -> bytes:
    """Place an impact after a half-second pause following wearer audio."""
    return pcm + bytes(16000) + impact_pcm(kind, distance_cm)


def distance_gain(distance_cm: float) -> float:
    """Free-field planning approximation, referenced to a 30 cm microphone."""
    if isinstance(distance_cm, bool) or not isinstance(distance_cm, (int, float)) or not math.isfinite(distance_cm) or not 10 <= distance_cm <= 300:
        raise ValueError('microphone distance out of range')
    return min(1.0, 30.0 / float(distance_cm))


def effective_snr_db(snr_db: float, distance_cm: float) -> float:
    if not math.isfinite(snr_db) or not -20 <= snr_db <= 80:
        raise ValueError('SNR out of range')
    return snr_db + 20 * math.log10(distance_gain(distance_cm))


def attenuate(pcm: bytes, signal_gain: float) -> bytes:
    if not math.isfinite(signal_gain) or not 0 < signal_gain <= 1:
        raise ValueError('signal gain out of range')
    return samples_to_pcm16(value * signal_gain for value in pcm16_to_samples(pcm))


def mix(pcm: bytes, kind: str, snr_db: float, seed: int = 41, tail_samples: int = 0, *, signal_gain: float = 1.0) -> tuple[bytes, float]:
    if not math.isfinite(snr_db) or not -20 <= snr_db <= 80:
        raise ValueError('SNR out of range')
    signal = pcm16_to_samples(pcm)
    if not signal:
        raise ValueError('empty signal')
    if not 0<=tail_samples<=16000*10:
        raise ValueError('noise tail out of range')
    if not math.isfinite(signal_gain) or not 0 < signal_gain <= 1:
        raise ValueError('signal gain out of range')
    bed = noise(kind, len(signal)+tail_samples, seed)
    # The clean utterance defines SNR; appended noise-only tail does not dilute it.
    power = sum(v*v for v in signal)/len(signal)
    npower = sum(v*v for v in bed[:len(signal)])/len(signal)
    if power == 0 or npower == 0:
        raise ValueError('SNR needs nonzero signal and noise')
    scale = math.sqrt(power / (npower * 10**(snr_db/10)))
    combined = [(signal[i]*signal_gain if i<len(signal) else 0) + n*scale for i,n in enumerate(bed)]
    peak = max(abs(v) for v in combined)
    gain = min(1.0, 30000/max(1, peak))
    return samples_to_pcm16(v*gain for v in combined), gain


def change_speed(pcm: bytes, rate: float) -> bytes:
    """Local resampling: speed AND pitch change. No claim of native TTS rate support."""
    if not math.isfinite(rate) or not .5 <= rate <= 2:
        raise ValueError('speed out of range')
    return samples_to_pcm16(resample_linear(pcm16_to_samples(pcm), round(16000*rate), 16000))


def oracle_audio(silent: bool = False) -> bytes:
    # A harmonic carrier for deterministic VAD/STT fixtures, not synthetic
    # spoken Korean. Two harmonics keep acoustic endpoint tests from treating
    # the old single-frequency test tone as a bench impact.
    return samples_to_pcm16(
        0 if silent else 4000 * (
            math.sin(2 * math.pi * 220 * i / 16000)
            + .35 * math.sin(2 * math.pi * 440 * i / 16000)
        ) / 1.35
        for i in range(16000)
    )
