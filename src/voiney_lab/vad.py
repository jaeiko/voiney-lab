"""Frame-based WebRTC VAD and endpointing for M3 Listener.

Lane SP1 (2026-10-10), decision 1: beside "is this a voice", the committed
utterance carries how loud it was -- the median level of its voiced frames
in dBFS -- so the listener can tell the wearer's voice, measured once at
the session's start, from a voice farther from the microphone.
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

import webrtcvad

from voiney_lab.audio import FRAME_BYTES, FRAME_MS, SAMPLE_RATE
from voiney_lab.configuration import (
    CascadeVadSettings,
    milliseconds_to_frames,
)

VAD_END_SILENCE_MS = 1000
log = logging.getLogger("voiney_lab.vad")

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
        self._resume_voiced_frames=0
        self._utterance: list[tuple[bytes, bool]] = []
        self.voiced_frames = 0
        self.consecutive_silence_frames = 0
        self._committed = False
        self._prefix_frames_retained = 0

    @property
    def buffered_frames(self) -> int:
        return len(self._utterance)

    def reset(self, state: TurnState = TurnState.IDLE) -> None:
        self.state = state
        self._prefix.clear()
        self._prefix_levels.clear()
        self._onset.clear()
        self._resume.clear()
        self._resume_voiced_frames=0
        self._utterance.clear()
        self._utterance_levels.clear()
        self.voiced_frames = 0
        self.consecutive_silence_frames = 0
        self._committed = False
        self._prefix_frames_retained = 0

    def process(self, frame: bytes) -> EndpointResult:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"audio frame must be exactly {FRAME_BYTES} bytes")
        if self.state not in (TurnState.IDLE, TurnState.USER_SPEAKING):
            return EndpointResult()

        voiced = bool(self.classifier(frame))
        level = frame_level_db(frame) if voiced else None
        if self.state == TurnState.IDLE:
            self._prefix.append((frame, voiced))
            self._prefix_levels.append(level)
            self._onset.append(voiced)
            if (len(self._onset) == self.onset_window_frames
                    and sum(self._onset) >= self.onset_voiced_frames):
                self.state = TurnState.USER_SPEAKING
                self._utterance = list(self._prefix)
                self._utterance_levels = list(self._prefix_levels)
                self._prefix_frames_retained = len(self._utterance)
                self.voiced_frames = sum(flag for _, flag in self._utterance)
                self.consecutive_silence_frames = self._trailing_silence()
                self._prefix.clear()
                self._prefix_levels.clear()
                self._onset.clear()
                self._resume.clear()
                self._resume_voiced_frames=0
                log.info("speech.started grace_ms=%s", self.config.endpoint_silence_frames * FRAME_MS)
                return EndpointResult(speech_started=True, voiced_frames=self.voiced_frames,
                                      total_frames=len(self._utterance),
                                      prefix_frames_retained=self._prefix_frames_retained)
            return EndpointResult()

        self._utterance.append((frame, voiced))
        self._utterance_levels.append(level)
        if not self.consecutive_silence_frames:
            if voiced:
                self.voiced_frames += 1
            else:
                self.consecutive_silence_frames = 1
                self._resume.clear()
                self._resume.append(False)
                self._resume_voiced_frames=0
                log.info("silence.started grace_ms=%s", self.config.endpoint_silence_frames * FRAME_MS)
        else:
            self.consecutive_silence_frames += 1
            self._resume.append(voiced)
            if voiced:
                self._resume_voiced_frames += 1

        forced = len(self._utterance) >= self.config.maximum_utterance_frames
        endpoint = self.consecutive_silence_frames >= self.config.endpoint_silence_frames
        if forced or endpoint:
            log.info("speech.ended input_audio_ms=%s grace_ms=%s", (len(self._utterance) - self.consecutive_silence_frames) * FRAME_MS, self.config.endpoint_silence_frames * FRAME_MS)
            return self._commit(forced=forced)
        if (self.consecutive_silence_frames
                and len(self._resume) == self.config.listening_resume_window_frames
                and sum(self._resume) >= self.config.listening_resume_voiced_frames):
            self.voiced_frames += self._resume_voiced_frames
            self.consecutive_silence_frames = self._trailing_resume_silence()
            self._resume.clear()
            self._resume_voiced_frames=0
            log.info("speech.resumed")
        return EndpointResult()

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

    def _commit(self, forced: bool) -> EndpointResult:
        if self._committed:
            return EndpointResult()
        self._committed = True
        trim = self.consecutive_silence_frames
        kept = self._utterance[:-trim] if trim else self._utterance[:]
        kept_levels = self._utterance_levels[:-trim] if trim else self._utterance_levels[:]
        accepted = self.voiced_frames >= self.config.minimum_voiced_frames
        result = EndpointResult(
            utterance=b"".join(frame for frame, _ in kept)
            if accepted else None,
            rejected=not accepted,
            forced=forced,
            voiced_frames=self.voiced_frames,
            total_frames=len(kept),
            prefix_frames_retained=min(
                self._prefix_frames_retained,len(kept)),
            rejection_reason="minimum_voiced_frames"
            if not accepted else None,
            voiced_level_db=_median_level(kept_levels) if accepted else None,
        )
        if result.rejected:
            self.reset()
        else:
            # This transition and detachment make the accepted commit exactly once.
            self.state = TurnState.PROCESSING
            self._utterance.clear()
            self._utterance_levels.clear()
            self._prefix.clear()
            self._prefix_levels.clear()
            self._onset.clear()
        return result
