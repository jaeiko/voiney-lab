"""Frame-based WebRTC VAD and endpointing for M3 Listener.

Lane SP1 (2026-10-10), decision 1: beside "is this a voice", the committed
utterance carries how loud it was -- the median level of its voiced frames
in dBFS -- so the listener can tell the wearer's voice, measured once at
the session's start, from a voice farther from the microphone.

Lane SP2 (2026-10-13), decision 1: in babble or beside a pulsing pump the
VAD hears "a voice" without a break, so it never finds the 1 s of silence
and the utterance runs to the 15 s bound. With the wearer's level measured,
the end is found by level as well: once the wearer's level has been heard
(as many frames within half the margin of the reference as a listening
onset needs voiced), a stretch as long as the endpoint silence spent more
than the margin below the wearer, while the VAD keeps hearing a voice
there, ends the utterance. The VAD's own silence still ends it as before, so in a quiet room
nothing changes. The listener draws the line only in a room that keeps the
VAD hearing a voice (babble, a pump, TV talk -- most of what it hears under
the wearer's line is voiced) and whose noise floor (``NoiseFloor``,
decision 2's measurement) sits well under the line. A
session that measured nothing ends utterances as before.
"""

from __future__ import annotations

from array import array
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
import logging
import math
import statistics

import numpy as np
import webrtcvad

from voiney_lab.audio import FRAME_BYTES, FRAME_MS, SAMPLE_RATE
from voiney_lab.configuration import (
    CascadeVadSettings,
    milliseconds_to_frames,
)

VAD_END_SILENCE_MS = 1000
log = logging.getLogger("voiney_lab.vad")

#: Lane HEAR2 (2026-10-29): WebRTC VAD can call a low fan-like room sound
#: speech continuously, so neither its ordinary silence nor the calibrated
#: wearer-level endpoint can help when the optional speaker filter is off.
#: After one second in which the VAD called at least 60% of frames voiced, the
#: listening detector takes the lower fifth of those voiced levels as the
#: local room floor.  Only after a frame actually rises 6 dB over it does the
#: relative gate arm; a steady wearer voice therefore cannot define and then
#: cut itself off.  The PCM is unchanged; this only qualifies the VAD decision
#: used for onset/end counting.  A fixed absolute dB line would discard a
#: wearer who moved farther from the microphone.
ADAPTIVE_NOISE_WINDOW_FRAMES = 50
ADAPTIVE_NOISE_MIN_VOICED_SHARE = 0.6
ADAPTIVE_NOISE_FLOOR_PERCENTILE = 20.0
ADAPTIVE_NOISE_RISE_DB = 6.0

#: LISTEN3 (2026-10-29): a short impact can satisfy WebRTC's six-of-ten
#: resume rule after the wearer has stopped. A bounded acoustic window keeps
#: those candidates from reopening the endpoint. WebRTC still owns onset and
#: per-frame verdicts; this check is used only for a would-be resume and for
#: rejecting a committed impact-only lump before STT.
TRANSIENT_SPEECH_WINDOW_FRAMES = 20
TRANSIENT_SPEECH_MIN_FRAMES = 8
TRANSIENT_PERIODICITY_MIN = 0.60
TRANSIENT_APERIODIC_PERIODICITY_MIN = 0.30
TRANSIENT_MID_BAND_MIN = 0.08
TRANSIENT_APERIODIC_MID_BAND_MIN = 0.50
TRANSIENT_LOW_BAND_MIN = 0.05
TRANSIENT_SPECTRAL_FLATNESS_MAX = 0.30
_TRANSIENT_WINDOW_STEP_FRAMES = 5
_TRANSIENT_MIN_SIGNAL_RMS = 8.0
_TRANSIENT_FOLLOWUP_FRAMES = 100
_TRANSIENT_FOLLOWUP_MIN_VOICED = 4


def _speech_shape_evidence(frames: Iterable[bytes]) -> bool:
    """Whether one bounded PCM window has a speech-like acoustic shape.

    Raw words and protocol state are not involved. The pitch-period search
    is limited to 40--200 samples (400--80 Hz); the spectrum check uses only
    aggregate power ratios. A constant nonzero signal is rejected: it has no
    speech spectrum and can represent clipped input or a synthetic impact.
    """

    selected = tuple(frames)
    if len(selected) < TRANSIENT_SPEECH_MIN_FRAMES:
        return False
    samples = np.frombuffer(b"".join(selected), dtype="<i2").astype(
        np.float64, copy=False
    )
    if not samples.size:
        return False
    rms = float(np.sqrt(np.mean(samples * samples)))
    if rms < _TRANSIENT_MIN_SIGNAL_RMS:
        return False
    centered = samples - float(np.mean(samples))
    power = float(np.dot(centered, centered))
    if power <= 1e-6 * len(centered):
        return False

    periodicity = 0.0
    maximum_lag = min(200, len(centered) // 3)
    for lag in range(40, maximum_lag + 1, 4):
        left = centered[:-lag]
        right = centered[lag:]
        denominator = math.sqrt(
            float(np.dot(left, left)) * float(np.dot(right, right))
        )
        if denominator:
            periodicity = max(
                periodicity, float(np.dot(left, right)) / denominator
            )

    spectrum = np.abs(np.fft.rfft(centered * np.hanning(len(centered))))
    spectrum += 1e-12
    spectral_power = spectrum * spectrum
    total_power = float(np.sum(spectral_power))
    if total_power <= 0:
        return False
    frequencies = np.fft.rfftfreq(len(centered), 1 / SAMPLE_RATE)
    low_band = float(
        np.sum(spectral_power[(frequencies >= 60) & (frequencies < 1000)])
        / total_power
    )
    mid_band = float(
        np.sum(spectral_power[(frequencies >= 250) & (frequencies < 3000)])
        / total_power
    )
    flatness = math.exp(float(np.mean(np.log(spectrum)))) / float(
        np.mean(spectrum)
    )
    return low_band >= TRANSIENT_LOW_BAND_MIN and (
        periodicity >= TRANSIENT_PERIODICITY_MIN
        and mid_band >= TRANSIENT_MID_BAND_MIN
        or periodicity >= TRANSIENT_APERIODIC_PERIODICITY_MIN
        and mid_band >= TRANSIENT_APERIODIC_MID_BAND_MIN
        and flatness <= TRANSIENT_SPECTRAL_FLATNESS_MAX
    )


def _contains_speech_shape(frames: list[tuple[bytes, bool]]) -> bool:
    """Scan an utterance in bounded overlapping windows for speech evidence."""

    pcm = [frame for frame, _voiced in frames]
    if len(pcm) <= TRANSIENT_SPEECH_WINDOW_FRAMES:
        return _speech_shape_evidence(pcm)
    starts = list(
        range(
            0,
            len(pcm) - TRANSIENT_SPEECH_WINDOW_FRAMES + 1,
            _TRANSIENT_WINDOW_STEP_FRAMES,
        )
    )
    last = len(pcm) - TRANSIENT_SPEECH_WINDOW_FRAMES
    if starts[-1] != last:
        starts.append(last)
    return any(
        _speech_shape_evidence(
            pcm[start : start + TRANSIENT_SPEECH_WINDOW_FRAMES]
        )
        for start in starts
    )

#: The level of a frame with no signal at all, and of one that is all zeros.
LEVEL_FLOOR_DB = -100.0
_FULL_SCALE = 32768.0

#: Lane SP1, decision 1: how much quieter than the wearer's measured level a
#: voice may be before it is taken for someone else's, by the "민감도"
#: setting: 높음 filters at a small difference, 낮음 only at a large one. The
#: values come from the synthetic bench (lane SP1 report, section 3-3):
#: 12 dB (보통) keeps the wearer's own sentences, read at any level the bench
#: varied them (±6 dB), inside the line even when the reference sentence was
#: read beside a loud machine, and takes every neighbour at 1 m on a boom
#: microphone out; 9 dB (높음) also takes a neighbour at 1 m out on an
#: earbud microphone in a quiet room while still keeping the wearer there;
#: 6 dB lost the wearer's quieter sentences, so it is not offered.
SENSITIVITY_MARGINS_DB: dict[str, float] = {"high": 9.0, "normal": 12.0, "low": 18.0}
DEFAULT_SENSITIVITY = "normal"


def frame_level_db(frame: bytes) -> float:
    """The RMS level of one PCM16 frame in dBFS; ``LEVEL_FLOOR_DB`` when silent."""

    samples = array("h")
    samples.frombytes(frame[: len(frame) - len(frame) % 2])
    if not samples:
        return LEVEL_FLOOR_DB
    mean_square = math.sumprod(samples, samples) / len(samples)
    if mean_square <= 0:
        return LEVEL_FLOOR_DB
    return max(LEVEL_FLOOR_DB, 10 * math.log10(mean_square) - 20 * math.log10(_FULL_SCALE))


def voiced_median_level_db(frames: Iterable[tuple[bytes, bool]]) -> float | None:
    """The median level of the frames the VAD called voiced; None without one."""

    levels = [frame_level_db(frame) for frame, voiced in frames if voiced]
    if not levels:
        return None
    return float(statistics.median(levels))


def _median_level(levels: Iterable[float | None]) -> float | None:
    """The median of the levels measured (None entries are silent frames)."""

    voiced = [level for level in levels if level is not None]
    return float(statistics.median(voiced)) if voiced else None


def utterance_level_db(pcm: bytes, classifier: Callable[[bytes], bool]) -> float | None:
    """The median voiced-frame level of a committed utterance's PCM, in dBFS.

    The same number ``EndpointDetector`` puts in its commit, computed again
    from the bytes with the same classifier (for a calibration sample or a
    measurement outside the detector).
    """

    frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)]
    return voiced_median_level_db((frame, bool(classifier(frame))) for frame in frames)


@dataclass(frozen=True)
class SpeakerLevelReference:
    """The wearer's measured level and the margin under it that still counts as the wearer.

    Measured once a session from a short sentence the wearer reads (lane
    SP1, decision 1); never stored. An utterance whose median voiced level
    is more than ``margin_db`` below ``reference_db`` is taken for another
    person's voice: it is not sent to speech recognition and not kept.
    """

    reference_db: float
    margin_db: float
    sensitivity: str = DEFAULT_SENSITIVITY

    def __post_init__(self) -> None:
        if not math.isfinite(self.reference_db):
            raise ValueError("reference_db must be a finite dBFS level")
        if not (0 <= self.margin_db <= 60):
            raise ValueError("margin_db must be between 0 and 60 dB")

    @property
    def threshold_db(self) -> float:
        return self.reference_db - self.margin_db

    def accepts(self, level_db: float | None) -> bool:
        """Whether an utterance of ``level_db`` is the wearer's; one with no level is."""

        return level_db is None or level_db >= self.threshold_db

    def with_sensitivity(self, sensitivity: str) -> "SpeakerLevelReference":
        margin = SENSITIVITY_MARGINS_DB.get(sensitivity, SENSITIVITY_MARGINS_DB[DEFAULT_SENSITIVITY])
        chosen = sensitivity if sensitivity in SENSITIVITY_MARGINS_DB else DEFAULT_SENSITIVITY
        return SpeakerLevelReference(self.reference_db, margin, chosen)


def level_reference_from(level_db: float, sensitivity: str = DEFAULT_SENSITIVITY) -> SpeakerLevelReference:
    """A reference at the calibration sentence's level, with the setting's margin."""

    return SpeakerLevelReference(level_db, SENSITIVITY_MARGINS_DB[DEFAULT_SENSITIVITY]).with_sensitivity(sensitivity)


#: Lane SP2, decision 2: the noise floor is the median level of the frames
#: heard while listening over the last 30 s -- what is there between and
#: under the words, which in babble is babble and beside a fan the fan. Not
#: "the frames the gate did not take for the wearer's": when the room is as
#: loud as the wearer the gate takes the room for the wearer, and the floor
#: would never see it. A median, not a mean, so the wearer's own sentences (a
#: minority of a working half-minute) do not lift it; not a lower percentile,
#: which would hear TV talk by its pauses. From the synthetic bench (lane
#: SP2 report).
NOISE_FLOOR_WINDOW_FRAMES = 1500
NOISE_FLOOR_PERCENTILE = 50.0
NOISE_FLOOR_MINIMUM_FRAMES = 500
_FLOOR_BIN_DB = 0.5
_FLOOR_BINS = int(-LEVEL_FLOOR_DB / _FLOOR_BIN_DB)
#: How often (in frames) the percentile is taken again; between, the last value stands.
_FLOOR_REFRESH_FRAMES = 50


class NoiseFloor:
    """The room's noise floor over a sliding window of frame levels (lane SP2, decision 2).

    Levels go into 0.5 dB bins, so adding a frame and taking the percentile
    stay cheap however long the window. Kept for the session only, never
    stored.
    """

    def __init__(self, window_frames: int = NOISE_FLOOR_WINDOW_FRAMES,
                 percentile: float = NOISE_FLOOR_PERCENTILE,
                 minimum_frames: int = NOISE_FLOOR_MINIMUM_FRAMES) -> None:
        if not 0 < minimum_frames <= window_frames:
            raise ValueError("minimum_frames must be between 1 and window_frames")
        if not 0 < percentile < 100:
            raise ValueError("percentile must be between 0 and 100")
        self.window_frames = window_frames
        self.percentile = percentile
        self.minimum_frames = minimum_frames
        self._window: deque[tuple[int, bool]] = deque()
        self._counts = [0] * _FLOOR_BINS
        #: Of the frames in each bin, how many the VAD called a voice.
        self._voiced_counts = [0] * _FLOOR_BINS
        self._since_refresh = 0
        self._level: float | None = None

    @property
    def frames(self) -> int:
        return len(self._window)

    def clear(self) -> None:
        self._window.clear()
        self._counts = [0] * _FLOOR_BINS
        self._voiced_counts = [0] * _FLOOR_BINS
        self._since_refresh = 0
        self._level = None

    def add(self, level_db: float, voiced: bool = False) -> None:
        index = min(_FLOOR_BINS - 1, max(0, int((level_db - LEVEL_FLOOR_DB) / _FLOOR_BIN_DB)))
        self._window.append((index, voiced))
        self._counts[index] += 1
        self._voiced_counts[index] += voiced
        if len(self._window) > self.window_frames:
            old, was_voiced = self._window.popleft()
            self._counts[old] -= 1
            self._voiced_counts[old] -= was_voiced
        self._since_refresh += 1
        if self._level is None or self._since_refresh >= _FLOOR_REFRESH_FRAMES:
            self._refresh()

    def level_db(self) -> float | None:
        """The floor in dBFS, or None before ``minimum_frames`` were heard."""

        return self._level

    def voiced_share_below(self, line_db: float) -> float | None:
        """Of the frames heard under ``line_db`` in the window, the share the VAD called a voice.

        Lane SP2, decision 1: how much the room itself -- what is under the
        wearer's line -- keeps the VAD hearing a voice: babble or a pump most
        of the time, TV talk more than half, a fan or a quiet room hardly at
        all. None before ``minimum_frames`` were heard.
        """

        if len(self._window) < self.minimum_frames:
            return None
        top = min(_FLOOR_BINS, max(0, int((line_db - LEVEL_FLOOR_DB) / _FLOOR_BIN_DB)))
        frames = sum(self._counts[:top])
        return sum(self._voiced_counts[:top]) / frames if frames else 0.0

    def _refresh(self) -> None:
        self._since_refresh = 0
        if len(self._window) < self.minimum_frames:
            self._level = None
            return
        rank = max(1, math.ceil(len(self._window) * self.percentile / 100))
        seen = 0
        for index, count in enumerate(self._counts):
            seen += count
            if seen >= rank:
                self._level = LEVEL_FLOOR_DB + (index + 0.5) * _FLOOR_BIN_DB
                return


class TurnState(str, Enum):
    IDLE = "IDLE"
    USER_SPEAKING = "USER_SPEAKING"
    PROCESSING = "PROCESSING"
    AGENT_SPEAKING = "AGENT_SPEAKING"
    COOLDOWN = "COOLDOWN"


@dataclass(frozen=True)
class VadConfig:
    mode: int = 3
    onset_voiced_frames: int = 4
    onset_window_frames: int = 6
    prefix_frames: int = 15
    barge_in_prefix_frames: int = 40
    endpoint_silence_frames: int = VAD_END_SILENCE_MS // FRAME_MS
    minimum_voiced_frames: int = 12
    maximum_utterance_frames: int = 750
    cooldown_ms: int = 300
    playback_onset_voiced_frames: int = 12
    playback_onset_window_frames: int = 15
    listening_onset_voiced_frames: int = 8
    listening_onset_window_frames: int = 12
    listening_resume_voiced_frames: int = 6
    listening_resume_window_frames: int = 10

    @classmethod
    def from_settings(cls,settings:CascadeVadSettings)->"VadConfig":
        return cls(
            mode=settings.mode,
            onset_voiced_frames=settings.onset_voiced_frames,
            onset_window_frames=settings.onset_window_frames,
            prefix_frames=milliseconds_to_frames(settings.prefix_ms),
            barge_in_prefix_frames=milliseconds_to_frames(
                settings.barge_in_prefix_ms),
            endpoint_silence_frames=milliseconds_to_frames(
                settings.endpoint_silence_ms),
            minimum_voiced_frames=milliseconds_to_frames(
                settings.minimum_speech_ms),
            maximum_utterance_frames=milliseconds_to_frames(
                settings.maximum_utterance_ms),
            cooldown_ms=settings.cooldown_ms,
            playback_onset_voiced_frames=(
                settings.playback_onset_voiced_frames),
            playback_onset_window_frames=(
                settings.playback_onset_window_frames),
            listening_onset_voiced_frames=(
                settings.listening_onset_voiced_frames),
            listening_onset_window_frames=(
                settings.listening_onset_window_frames),
            listening_resume_voiced_frames=(
                settings.listening_resume_voiced_frames),
            listening_resume_window_frames=(
                settings.listening_resume_window_frames),
        )

    def __post_init__(self) -> None:
        if self.mode not in range(4):
            raise ValueError("WebRTC VAD mode must be 0, 1, 2, or 3")
        if not 0 < self.onset_voiced_frames <= self.onset_window_frames:
            raise ValueError("invalid onset threshold")
        if not (0 < self.playback_onset_voiced_frames
                <= self.playback_onset_window_frames):
            raise ValueError("invalid playback onset threshold")
        if not (0 < self.listening_onset_voiced_frames
                <= self.listening_onset_window_frames):
            raise ValueError("invalid listening onset threshold")
        if not (0 < self.listening_resume_voiced_frames
                <= self.listening_resume_window_frames):
            raise ValueError("invalid listening resume threshold")
        for name in ("prefix_frames", "barge_in_prefix_frames", "endpoint_silence_frames", "minimum_voiced_frames",
                     "maximum_utterance_frames"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.cooldown_ms < 0:
            raise ValueError("cooldown_ms cannot be negative")


class WebRtcVadClassifier:
    """Validate the M3 frame contract before calling the native VAD."""

    def __init__(self, mode: int = 3) -> None:
        self._vad = webrtcvad.Vad(mode)

    def __call__(self, frame: bytes) -> bool:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"VAD needs exactly {FRAME_BYTES} bytes ({FRAME_MS} ms)")
        return self._vad.is_speech(frame, SAMPLE_RATE)


@dataclass(frozen=True)
class EndpointResult:
    speech_started: bool = False
    utterance: bytes | None = None
    rejected: bool = False
    forced: bool = False
    voiced_frames: int = 0
    total_frames: int = 0
    prefix_frames_retained: int = 0
    rejection_reason: str | None = None
    #: Lane SP1, decision 1: the committed utterance's median voiced-frame
    #: level in dBFS (None until a commit, or when no frame was voiced).
    voiced_level_db: float | None = None
    #: Lane SP2, decision 1: what ended a commit -- "silence" (the VAD heard
    #: none for the endpoint time), "level" (voiced, but quieter than the
    #: wearer for that long) or "forced" (the maximum utterance length).
    endpoint: str | None = None


#: Lane SP2, decision 1: the share of the last endpoint-silence's frames the
#: VAD must call voiced for the level to end an utterance -- "while the VAD
#: keeps hearing a voice". Babble at SNR 20 meets it in 89 % of 1 s windows,
#: a pulsing pump in all; fan, centrifuge and a quiet room in none, so there
#: the VAD's own silence ends the words exactly as before, and a quiet filler
#: ("음") between two pauses never ends them early (lane SP2 report).
LEVEL_ENDPOINT_VOICED_SHARE = 0.6


class EndpointDetector:
    """Turn exact PCM frames into one bounded, exactly-once utterance commit."""

    def __init__(self, config: VadConfig | None = None,
                 classifier: Callable[[bytes], bool] | None = None,
                 *,listening_onset:bool=False) -> None:
        self.config = config or VadConfig()
        self.classifier = classifier or WebRtcVadClassifier(self.config.mode)
        self.listening_onset=bool(listening_onset)
        self.onset_voiced_frames=(
            self.config.listening_onset_voiced_frames
            if self.listening_onset else self.config.onset_voiced_frames)
        self.onset_window_frames=(
            self.config.listening_onset_window_frames
            if self.listening_onset else self.config.onset_window_frames)
        self.state = TurnState.IDLE
        #: Lane SP2: the level of the last frame processed (voiced or not)
        #: and the VAD's verdict on it, for the listener's noise floor; None
        #: after a frame the detector did not process.
        self.last_frame_level_db: float | None = None
        self.last_frame_voiced = False
        self._prefix: deque[tuple[bytes, bool]] = deque(maxlen=self.config.prefix_frames)
        #: Lane SP1, decision 1: each voiced frame's level, measured as it
        #: arrives (about 25 µs a frame) so a commit only takes a median;
        #: None for a frame the VAD called silent. Kept beside _prefix and
        #: _utterance, one entry per frame.
        self._prefix_levels: deque[float | None] = deque(maxlen=self.config.prefix_frames)
        self._utterance_levels: list[float | None] = []
        self._onset: deque[bool] = deque(maxlen=self.onset_window_frames)
        self._resume: deque[bool] = deque(
            maxlen=self.config.listening_resume_window_frames)
        self._resume_audio: deque[bytes] = deque(
            maxlen=self.config.listening_resume_window_frames)
        self._resume_voiced_frames=0
        self._utterance: list[tuple[bytes, bool]] = []
        self.voiced_frames = 0
        self.consecutive_silence_frames = 0
        self._committed = False
        self._prefix_frames_retained = 0
        #: Lane SP2, decision 1: the line under the wearer's measured level
        #: the listener sets while the level can part the wearer from the
        #: room; None ends utterances by the VAD alone, as before.
        self.wearer_threshold_db: float | None = None
        #: The wearer's own level, the line the wearer's words must reach to
        #: begin (the listener sets the reference minus half the margin; None
        #: uses ``wearer_threshold_db``). Higher than the end line, so a
        #: neighbour or a TV between the two -- loud enough for the gate, not
        #: as loud as the wearer -- never starts the level's end.
        self.wearer_onset_db: float | None = None
        #: Whether this utterance has had the wearer's level -- as many frames
        #: at it, of the last listening-onset window, as a listening onset
        #: needs voiced (8 of 12), so a louder moment is not taken for the
        #: wearer; only then is its end looked for by level.
        self._wearer_heard = False
        self._loud_window: deque[bool] = deque(maxlen=self.config.listening_onset_window_frames)
        #: The level's own quiet stretch beside the VAD's: frames under the
        #: line, with the same resume rule on frames over it.
        self.level_quiet_frames = 0
        self._level_resume: deque[bool] = deque(
            maxlen=self.config.listening_resume_window_frames)
        #: The VAD's verdicts on the utterance's last endpoint-silence frames.
        self._recent_voiced: deque[bool] = deque(maxlen=self.config.endpoint_silence_frames)
        self._recent_voiced_count = 0
        #: Lane HEAR2: enabled by ``ListenerSession`` only when no explicit
        #: wearer-level reference or calibration sentence owns the level.
        self.adaptive_noise_gate = False
        self._adaptive_noise_window: deque[tuple[bool, float]] = deque(
            maxlen=ADAPTIVE_NOISE_WINDOW_FRAMES)
        self._adaptive_noise_threshold_db: float | None = None
        self._adaptive_noise_armed = False
        #: LISTEN3: production ListenerSession opts in. Direct unit users
        #: keep the historical WebRTC-only rule unless they enable it.
        self.transient_noise_gate = False
        #: After an impact-only rejection, keep a two-second recovery window
        #: for a short, quiet command. WebRTC must still mark at least four
        #: frames and the bounded acoustic window must carry speech evidence.
        self._transient_followup_remaining = 0
        self._transient_followup_audio: deque[bytes] = deque(
            maxlen=TRANSIENT_SPEECH_WINDOW_FRAMES
        )
        self._transient_followup_voiced: deque[bool] = deque(
            maxlen=TRANSIENT_SPEECH_WINDOW_FRAMES
        )
        self._speech_shape_confirmed = False

    @property
    def buffered_frames(self) -> int:
        return len(self._utterance)

    def reset(self, state: TurnState = TurnState.IDLE) -> None:
        self.state = state
        self._prefix.clear()
        self._prefix_levels.clear()
        self._onset.clear()
        self._resume.clear()
        self._resume_audio.clear()
        self._resume_voiced_frames=0
        self._utterance.clear()
        self._utterance_levels.clear()
        self.voiced_frames = 0
        self.consecutive_silence_frames = 0
        self._committed = False
        self._prefix_frames_retained = 0
        self._forget_level()
        self._forget_adaptive_noise()
        self._transient_followup_remaining = 0
        self._transient_followup_audio.clear()
        self._transient_followup_voiced.clear()
        self._speech_shape_confirmed = False

    def _forget_level(self) -> None:
        self._wearer_heard = False
        self._loud_window.clear()
        self.level_quiet_frames = 0
        self._level_resume.clear()
        self._recent_voiced.clear()
        self._recent_voiced_count = 0

    def _forget_adaptive_noise(self) -> None:
        self._adaptive_noise_window.clear()
        self._adaptive_noise_threshold_db = None
        self._adaptive_noise_armed = False

    def enable_adaptive_noise_gate(self, enabled: bool) -> None:
        """Use the turn-local relative level gate, or remove all of its state.

        A calibration sentence and a session with an explicit wearer level
        keep their existing, separately measured behaviour.  The listener
        calls this before frames are processed, including interruption frames.
        """

        enabled = bool(enabled)
        if self.adaptive_noise_gate and not enabled:
            self._forget_adaptive_noise()
        self.adaptive_noise_gate = enabled

    def enable_transient_noise_gate(self, enabled: bool) -> None:
        """Qualify resume candidates and impact-only commits before STT."""

        self.transient_noise_gate = bool(enabled)
        if not self.transient_noise_gate:
            self._resume_audio.clear()
            self._transient_followup_remaining = 0
            self._transient_followup_audio.clear()
            self._transient_followup_voiced.clear()
            self._speech_shape_confirmed = False

    def _adaptive_endpoint_voiced(self, voiced: bool, level_db: float) -> bool:
        """Qualify a VAD verdict against this turn's own low-level room sound."""

        if not self.adaptive_noise_gate:
            return voiced
        if self._adaptive_noise_threshold_db is None:
            self._adaptive_noise_window.append((voiced, level_db))
            if len(self._adaptive_noise_window) == ADAPTIVE_NOISE_WINDOW_FRAMES:
                levels = sorted(
                    level for was_voiced, level in self._adaptive_noise_window
                    if was_voiced
                )
                required = math.ceil(
                    ADAPTIVE_NOISE_MIN_VOICED_SHARE
                    * ADAPTIVE_NOISE_WINDOW_FRAMES)
                if len(levels) >= required:
                    rank = max(
                        0,
                        math.ceil(
                            len(levels)
                            * ADAPTIVE_NOISE_FLOOR_PERCENTILE / 100
                        ) - 1,
                    )
                    self._adaptive_noise_threshold_db = (
                        levels[rank] + ADAPTIVE_NOISE_RISE_DB)
        if (
            self._adaptive_noise_threshold_db is not None
            and not self._adaptive_noise_armed
        ):
            observed_peak = max(
                (
                    level for was_voiced, level in self._adaptive_noise_window
                    if was_voiced
                ),
                default=LEVEL_FLOOR_DB,
            )
            if voiced:
                observed_peak = max(observed_peak, level_db)
            if observed_peak >= self._adaptive_noise_threshold_db:
                self._adaptive_noise_armed = True
                log.info(
                    "adaptive noise gate on threshold_db=%.1f window_ms=%d",
                    self._adaptive_noise_threshold_db,
                    ADAPTIVE_NOISE_WINDOW_FRAMES * FRAME_MS,
                )
        return (
            voiced
            and (
                not self._adaptive_noise_armed
                or level_db >= self._adaptive_noise_threshold_db
            )
        )

    def _loud(self, level: float | None) -> bool:
        """Whether a voiced frame of ``level`` is at the wearer's level (lane SP2)."""

        return (
            self.wearer_threshold_db is not None
            and level is not None
            and level >= self.wearer_threshold_db
        )

    def _note_voiced(self, voiced: bool) -> None:
        if len(self._recent_voiced) == self._recent_voiced.maxlen:
            self._recent_voiced_count -= self._recent_voiced[0]
        self._recent_voiced.append(voiced)
        self._recent_voiced_count += voiced

    def _vad_keeps_hearing_a_voice(self) -> bool:
        return (
            len(self._recent_voiced) == self._recent_voiced.maxlen
            and self._recent_voiced_count
            >= LEVEL_ENDPOINT_VOICED_SHARE * self.config.endpoint_silence_frames
        )

    def _start_level_tracking(self) -> None:
        """The wearer's level was heard: from here the level may end the words too.

        The frames after the last one over the line begin the quiet stretch.
        """

        self._wearer_heard = True
        self.level_quiet_frames = 0
        self._level_resume.clear()
        for level in reversed(self._utterance_levels):
            if self._loud(level):
                break
            self.level_quiet_frames += 1

    def _at_wearer_level(self, level: float | None) -> bool:
        line = self.wearer_onset_db if self.wearer_onset_db is not None else self.wearer_threshold_db
        return line is not None and level is not None and level >= line

    def _wearer_level_reached(self, at_level: bool) -> bool:
        self._loud_window.append(at_level)
        return sum(self._loud_window) >= self.config.listening_onset_voiced_frames

    def _hear_wearer(self) -> None:
        """The wearer's level reached, in an utterance the room had begun.

        Lane SP2, decision 1. When the VAD had kept hearing a voice before
        the window's first frame at the wearer's level -- babble, a pump, a neighbour
        -- what came before it, beyond one prefix of lead-in, was not the
        wearer and is dropped: the 15 s bound counts from the wearer's start,
        and the level the gate compares and the STT hear are the wearer's
        words. A start the VAD heard in a quiet room (a soft "음", then a
        pause) is kept, as before.
        """

        first_loud = len(self._utterance) - len(self._loud_window) + self._loud_window.index(True)
        cut = max(0, first_loud - (self.config.prefix_frames - 1))
        lead = self._utterance[:cut]
        if lead and sum(flag for _, flag in lead) >= LEVEL_ENDPOINT_VOICED_SHARE * len(lead):
            del self._utterance[:cut]
            del self._utterance_levels[:cut]
            self._prefix_frames_retained = first_loud - cut
            self.voiced_frames = sum(flag for _, flag in self._utterance)
            self.consecutive_silence_frames = self._trailing_silence()
            self._resume.clear()
            self._resume_audio.clear()
            self._resume_voiced_frames = 0
            self._recent_voiced.clear()
            self._recent_voiced_count = 0
            for _, flag in self._utterance[-self.config.endpoint_silence_frames:]:
                self._note_voiced(flag)
        self._start_level_tracking()

    def process(self, frame: bytes) -> EndpointResult:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"audio frame must be exactly {FRAME_BYTES} bytes")
        self.last_frame_level_db = None
        if self.state not in (TurnState.IDLE, TurnState.USER_SPEAKING):
            return EndpointResult()

        raw_voiced = bool(self.classifier(frame))
        frame_level = frame_level_db(frame)
        self.last_frame_level_db = frame_level
        # The room floor records the classifier's original verdict.  The
        # adaptive gate changes only what may keep an utterance open.
        self.last_frame_voiced = raw_voiced
        voiced = self._adaptive_endpoint_voiced(raw_voiced, frame_level)
        level = frame_level if voiced else None
        if self.state == TurnState.IDLE:
            if self._transient_followup_remaining:
                self._transient_followup_remaining -= 1
                self._transient_followup_audio.append(frame)
                self._transient_followup_voiced.append(raw_voiced)
            else:
                self._transient_followup_audio.clear()
                self._transient_followup_voiced.clear()
            self._prefix.append((frame, voiced))
            self._prefix_levels.append(level)
            self._onset.append(voiced)
            ordinary_onset = (
                len(self._onset) == self.onset_window_frames
                and sum(self._onset) >= self.onset_voiced_frames
            )
            followup_onset = (
                len(self._transient_followup_audio)
                == TRANSIENT_SPEECH_WINDOW_FRAMES
                and sum(self._transient_followup_voiced)
                >= _TRANSIENT_FOLLOWUP_MIN_VOICED
                and _speech_shape_evidence(self._transient_followup_audio)
            )
            if ordinary_onset or followup_onset:
                self.state = TurnState.USER_SPEAKING
                self._speech_shape_confirmed = followup_onset
                self._utterance = list(self._prefix)
                self._utterance_levels = list(self._prefix_levels)
                self._prefix_frames_retained = len(self._utterance)
                self.voiced_frames = sum(flag for _, flag in self._utterance)
                self.consecutive_silence_frames = self._trailing_silence()
                self._forget_level()
                for _, flag in self._utterance[-self.config.endpoint_silence_frames:]:
                    self._note_voiced(flag)
                if self.wearer_threshold_db is not None and any(
                        self._wearer_level_reached(self._at_wearer_level(lv)) for lv in list(self._utterance_levels)):
                    self._start_level_tracking()
                self._prefix.clear()
                self._prefix_levels.clear()
                self._onset.clear()
                self._resume.clear()
                self._resume_audio.clear()
                self._resume_voiced_frames=0
                self._transient_followup_remaining = 0
                self._transient_followup_audio.clear()
                self._transient_followup_voiced.clear()
                log.info("speech.started grace_ms=%s", self.config.endpoint_silence_frames * FRAME_MS)
                return EndpointResult(speech_started=True, voiced_frames=self.voiced_frames,
                                      total_frames=len(self._utterance),
                                      prefix_frames_retained=self._prefix_frames_retained)
            return EndpointResult()

        self._utterance.append((frame, voiced))
        self._utterance_levels.append(level)
        self._note_voiced(voiced)
        if not self.consecutive_silence_frames:
            if voiced:
                self.voiced_frames += 1
            else:
                self.consecutive_silence_frames = 1
                self._resume.clear()
                self._resume.append(False)
                self._resume_audio.clear()
                self._resume_audio.append(frame)
                self._resume_voiced_frames=0
                log.info("silence.started grace_ms=%s", self.config.endpoint_silence_frames * FRAME_MS)
        else:
            self.consecutive_silence_frames += 1
            self._resume.append(voiced)
            self._resume_audio.append(frame)
            if voiced:
                self._resume_voiced_frames += 1
        loud = self._loud(level)
        if self._wearer_heard and self.wearer_threshold_db is not None:
            self._track_level(loud)
        elif self.wearer_threshold_db is not None and self._wearer_level_reached(self._at_wearer_level(level)):
            self._hear_wearer()

        forced = len(self._utterance) >= self.config.maximum_utterance_frames
        endpoint = self.consecutive_silence_frames >= self.config.endpoint_silence_frames
        # Lane SP2, decision 1: the VAD still hears a voice, but it has been
        # quieter than the wearer for the whole endpoint time.
        by_level = (
            self._wearer_heard
            and self.wearer_threshold_db is not None
            and not loud
            and self.level_quiet_frames >= self.config.endpoint_silence_frames
            and self._vad_keeps_hearing_a_voice()
        )
        if forced or endpoint or by_level:
            kind = "forced" if forced else "silence" if endpoint else "level"
            trim = self.level_quiet_frames if kind == "level" else self.consecutive_silence_frames
            log.info("speech.ended input_audio_ms=%s grace_ms=%s endpoint=%s", (len(self._utterance) - trim) * FRAME_MS, self.config.endpoint_silence_frames * FRAME_MS, kind)
            return self._commit(forced=forced, trim=trim, kind=kind)
        if (self.consecutive_silence_frames
                and len(self._resume) == self.config.listening_resume_window_frames
                and sum(self._resume) >= self.config.listening_resume_voiced_frames):
            if (
                self.transient_noise_gate
                and not _speech_shape_evidence(self._resume_audio)
            ):
                return EndpointResult()
            self.voiced_frames += self._resume_voiced_frames
            self.consecutive_silence_frames = self._trailing_resume_silence()
            self._resume.clear()
            self._resume_audio.clear()
            self._resume_voiced_frames=0
            log.info("speech.resumed")
        return EndpointResult()

    def prepend_utterance(self, frames: Iterable[bytes]) -> int:
        """Put words already committed in front of the utterance under way (lane HEAR, decision 4).

        A sentence the endpoint cut at a pause: the first half's frames go
        before this second half's, with the VAD's verdict and level of each,
        so the commit is one utterance -- one transcription of the whole
        sentence. Only while speaking; returns the frames put in front.
        """

        if self.state is not TurnState.USER_SPEAKING:
            raise ValueError("only an utterance under way takes words in front")
        before: list[tuple[bytes, bool]] = []
        for frame in frames:
            if len(frame) != FRAME_BYTES:
                continue
            raw_voiced = bool(self.classifier(frame))
            level = frame_level_db(frame)
            before.append((
                frame,
                raw_voiced and (
                    not self._adaptive_noise_armed
                    or level >= self._adaptive_noise_threshold_db
                ),
            ))
        levels = [frame_level_db(frame) if voiced else None for frame, voiced in before]
        self._utterance[:0] = before
        self._utterance_levels[:0] = levels
        self.voiced_frames += sum(1 for _, voiced in before if voiced)
        return len(before)

    def _track_level(self, loud: bool) -> None:
        """Count the level's quiet stretch; ``loud`` is a frame over the wearer's line."""

        if not self.level_quiet_frames:
            if not loud:
                self.level_quiet_frames = 1
                self._level_resume.clear()
                self._level_resume.append(False)
            return
        self.level_quiet_frames += 1
        self._level_resume.append(loud)
        if (len(self._level_resume) == self.config.listening_resume_window_frames
                and sum(self._level_resume) >= self.config.listening_resume_voiced_frames):
            count = 0
            for flag in reversed(self._level_resume):
                if flag:
                    break
                count += 1
            self.level_quiet_frames = count
            self._level_resume.clear()

    def _trailing_resume_silence(self) -> int:
        count=0
        for voiced in reversed(self._resume):
            if voiced:
                break
            count+=1
        return count

    def _trailing_silence(self) -> int:
        count = 0
        for _, voiced in reversed(self._utterance):
            if voiced:
                break
            count += 1
        return count

    def _commit(self, forced: bool, trim: int | None = None, kind: str | None = None) -> EndpointResult:
        if self._committed:
            return EndpointResult()
        self._committed = True
        if trim is None:
            trim = self.consecutive_silence_frames
        kept = self._utterance[:-trim] if trim else self._utterance[:]
        kept_levels = self._utterance_levels[:-trim] if trim else self._utterance_levels[:]
        # An end by level leaves the VAD's count over the room's voice after
        # the wearer's words; count what is kept.
        voiced_frames = (
            sum(flag for _, flag in kept) if kind == "level" else self.voiced_frames)
        accepted = (
            voiced_frames >= self.config.minimum_voiced_frames
            or self._speech_shape_confirmed
        )
        transient_only = bool(
            self.transient_noise_gate
            and kept
            and not self._speech_shape_confirmed
            and not _contains_speech_shape(kept)
        )
        if transient_only:
            accepted = False
        result = EndpointResult(
            utterance=b"".join(frame for frame, _ in kept)
            if accepted else None,
            rejected=not accepted,
            forced=forced,
            voiced_frames=voiced_frames,
            total_frames=len(kept),
            prefix_frames_retained=min(
                self._prefix_frames_retained,len(kept)),
            rejection_reason=(
                "transient_noise"
                if transient_only
                else "minimum_voiced_frames" if not accepted else None
            ),
            voiced_level_db=_median_level(kept_levels) if accepted else None,
            endpoint=kind or ("forced" if forced else "silence"),
        )
        if result.rejected:
            self.reset()
            if transient_only and self.transient_noise_gate:
                self._transient_followup_remaining = _TRANSIENT_FOLLOWUP_FRAMES
        else:
            # This transition and detachment make the accepted commit exactly once.
            self.state = TurnState.PROCESSING
            self._utterance.clear()
            self._utterance_levels.clear()
            self._prefix.clear()
            self._prefix_levels.clear()
            self._onset.clear()
            self._forget_level()
        return result
