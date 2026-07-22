"""Frame-based WebRTC VAD and endpointing for M3 Listener."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import logging

import webrtcvad

from safebridge_voice.audio import FRAME_BYTES, FRAME_MS, SAMPLE_RATE

VAD_END_SILENCE_MS = 1000
log = logging.getLogger("safebridge.vad")


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
    endpoint_silence_frames: int = VAD_END_SILENCE_MS // FRAME_MS
    minimum_voiced_frames: int = 12
    maximum_utterance_frames: int = 750
    cooldown_ms: int = 300

    def __post_init__(self) -> None:
        if self.mode not in range(4):
            raise ValueError("WebRTC VAD mode must be 0, 1, 2, or 3")
        if not 0 < self.onset_voiced_frames <= self.onset_window_frames:
            raise ValueError("invalid onset threshold")
        for name in ("prefix_frames", "endpoint_silence_frames", "minimum_voiced_frames",
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
    rejection_reason: str | None = None


class EndpointDetector:
    """Turn exact PCM frames into one bounded, exactly-once utterance commit."""

    def __init__(self, config: VadConfig | None = None,
                 classifier: Callable[[bytes], bool] | None = None) -> None:
        self.config = config or VadConfig()
        self.classifier = classifier or WebRtcVadClassifier(self.config.mode)
        self.state = TurnState.IDLE
        self._prefix: deque[tuple[bytes, bool]] = deque(maxlen=self.config.prefix_frames)
        self._onset: deque[bool] = deque(maxlen=self.config.onset_window_frames)
        self._utterance: list[tuple[bytes, bool]] = []
        self.voiced_frames = 0
        self.consecutive_silence_frames = 0
        self._committed = False

    @property
    def buffered_frames(self) -> int:
        return len(self._utterance)

    def reset(self, state: TurnState = TurnState.IDLE) -> None:
        self.state = state
        self._prefix.clear()
        self._onset.clear()
        self._utterance.clear()
        self.voiced_frames = 0
        self.consecutive_silence_frames = 0
        self._committed = False

    def process(self, frame: bytes) -> EndpointResult:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"audio frame must be exactly {FRAME_BYTES} bytes")
        if self.state not in (TurnState.IDLE, TurnState.USER_SPEAKING):
            return EndpointResult()

        voiced = bool(self.classifier(frame))
        if self.state == TurnState.IDLE:
            self._prefix.append((frame, voiced))
            self._onset.append(voiced)
            if (len(self._onset) == self.config.onset_window_frames
                    and sum(self._onset) >= self.config.onset_voiced_frames):
                self.state = TurnState.USER_SPEAKING
                self._utterance = list(self._prefix)
                self.voiced_frames = sum(flag for _, flag in self._utterance)
                self.consecutive_silence_frames = self._trailing_silence()
                self._prefix.clear()
                self._onset.clear()
                log.info("speech.started grace_ms=%s", self.config.endpoint_silence_frames * FRAME_MS)
                return EndpointResult(speech_started=True, voiced_frames=self.voiced_frames,
                                      total_frames=len(self._utterance))
            return EndpointResult()

        self._utterance.append((frame, voiced))
        if voiced:
            if self.consecutive_silence_frames: log.info("speech.resumed")
            self.voiced_frames += 1
            self.consecutive_silence_frames = 0
        else:
            self.consecutive_silence_frames += 1
            if self.consecutive_silence_frames == 1: log.info("silence.started grace_ms=%s", self.config.endpoint_silence_frames * FRAME_MS)

        forced = len(self._utterance) >= self.config.maximum_utterance_frames
        endpoint = self.consecutive_silence_frames >= self.config.endpoint_silence_frames
        if forced or endpoint:
            log.info("speech.ended input_audio_ms=%s grace_ms=%s", (len(self._utterance) - self.consecutive_silence_frames) * FRAME_MS, self.config.endpoint_silence_frames * FRAME_MS)
            return self._commit(forced=forced)
        return EndpointResult()

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
        result = EndpointResult(
            utterance=b"".join(frame for frame, _ in kept)
            if self.voiced_frames >= self.config.minimum_voiced_frames else None,
            rejected=self.voiced_frames < self.config.minimum_voiced_frames,
            forced=forced,
            voiced_frames=self.voiced_frames,
            total_frames=len(kept),
            rejection_reason="minimum_voiced_frames"
            if self.voiced_frames < self.config.minimum_voiced_frames else None,
        )
        if result.rejected:
            self.reset()
        else:
            # This transition and detachment make the accepted commit exactly once.
            self.state = TurnState.PROCESSING
            self._utterance.clear()
            self._prefix.clear()
            self._onset.clear()
        return result
