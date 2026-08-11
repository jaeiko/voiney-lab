"""Validated environment configuration for both voice activity pipelines."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from collections.abc import Mapping

from voice_workflow_agent.audio import FRAME_MS


class ConfigurationError(ValueError):
    """A named environment setting is malformed or outside its safe range."""


CASCADE_FILLER_DELAY_ENV = "CASCADE_FILLER_DELAY_MS"
DEFAULT_CASCADE_FILLER_DELAY_MS = 700


def cascade_filler_delay_ms(
    environment: Mapping[str, str] | None = None,
) -> int:
    """Return one bounded, side-effect-free Cascade filler threshold."""

    env = os.environ if environment is None else environment
    return _integer(
        env,
        CASCADE_FILLER_DELAY_ENV,
        DEFAULT_CASCADE_FILLER_DELAY_MS,
        100,
        5000,
    )


def _integer(
    environment: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw=environment.get(name,str(default)).strip()
    try:
        value=int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum<=value<=maximum:
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}")
    return value


def _floating(
    environment: Mapping[str, str],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw=environment.get(name,str(default)).strip()
    try:
        value=float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value) or not minimum<=value<=maximum:
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}")
    return value


def milliseconds_to_frames(milliseconds: int) -> int:
    """Round up to 20 ms frames so a configured duration is never shortened."""
    if milliseconds<=0:
        raise ConfigurationError("frame duration must be positive")
    return math.ceil(milliseconds/FRAME_MS)


@dataclass(frozen=True)
class CascadeVadSettings:
    mode: int=3
    onset_voiced_frames: int=4
    onset_window_frames: int=6
    prefix_ms: int=300
    barge_in_prefix_ms: int=800
    endpoint_silence_ms: int=1000
    minimum_speech_ms: int=240
    maximum_utterance_ms: int=15000
    cooldown_ms: int=300
    playback_onset_voiced_frames: int=12
    playback_onset_window_frames: int=15
    listening_onset_voiced_frames: int=8
    listening_onset_window_frames: int=12
    listening_resume_voiced_frames: int=6
    listening_resume_window_frames: int=10

    @classmethod
    def from_environment(
        cls,environment: Mapping[str,str]|None=None
    )->"CascadeVadSettings":
        env=os.environ if environment is None else environment
        settings=cls(
            mode=_integer(env,"CASCADE_VAD_MODE",3,0,3),
            onset_voiced_frames=_integer(
                env,"CASCADE_VAD_ONSET_VOICED_FRAMES",4,1,100),
            onset_window_frames=_integer(
                env,"CASCADE_VAD_ONSET_WINDOW_FRAMES",6,1,100),
            prefix_ms=_integer(env,"CASCADE_VAD_PREFIX_MS",300,20,5000),
            barge_in_prefix_ms=_integer(
                env,"CASCADE_BARGE_IN_PREFIX_MS",800,300,5000),
            endpoint_silence_ms=_integer(
                env,"CASCADE_VAD_ENDPOINT_SILENCE_MS",1000,20,10000),
            minimum_speech_ms=_integer(
                env,"CASCADE_VAD_MIN_SPEECH_MS",240,20,10000),
            maximum_utterance_ms=_integer(
                env,"CASCADE_VAD_MAX_UTTERANCE_MS",15000,20,300000),
            cooldown_ms=_integer(
                env,"CASCADE_VAD_COOLDOWN_MS",300,0,10000),
            playback_onset_voiced_frames=_integer(
                env,"CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES",12,1,100),
            playback_onset_window_frames=_integer(
                env,"CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES",15,1,100),
            listening_onset_voiced_frames=_integer(
                env,"CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES",8,1,100),
            listening_onset_window_frames=_integer(
                env,"CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES",12,1,100),
            listening_resume_voiced_frames=_integer(
                env,"CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES",6,1,100),
            listening_resume_window_frames=_integer(
                env,"CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES",10,1,100),
        )
        if settings.onset_voiced_frames>settings.onset_window_frames:
            raise ConfigurationError(
                "CASCADE_VAD_ONSET_VOICED_FRAMES cannot exceed "
                "CASCADE_VAD_ONSET_WINDOW_FRAMES")
        if settings.minimum_speech_ms>settings.maximum_utterance_ms:
            raise ConfigurationError(
                "CASCADE_VAD_MIN_SPEECH_MS cannot exceed "
                "CASCADE_VAD_MAX_UTTERANCE_MS")
        if (settings.playback_onset_voiced_frames>
                settings.playback_onset_window_frames):
            raise ConfigurationError(
                "CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES cannot exceed "
                "CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES")
        if (settings.listening_onset_voiced_frames>
                settings.listening_onset_window_frames):
            raise ConfigurationError(
                "CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES cannot exceed "
                "CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES")
        if (settings.listening_resume_voiced_frames>
                settings.listening_resume_window_frames):
            raise ConfigurationError(
                "CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES cannot exceed "
                "CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES")
        return settings


@dataclass(frozen=True)
class NativeVadSettings:
    threshold: float=0.6
    prefix_padding_ms: int=333
    silence_duration_ms: int=1600

    @classmethod
    def from_environment(
        cls,environment: Mapping[str,str]|None=None
    )->"NativeVadSettings":
        env=os.environ if environment is None else environment
        return cls(
            threshold=_floating(
                env,"XAI_REALTIME_VAD_THRESHOLD",0.6,0.1,0.9),
            prefix_padding_ms=_integer(
                env,"NATIVE_VAD_PREFIX_PADDING_MS",333,0,5000),
            silence_duration_ms=_integer(
                env,"XAI_REALTIME_SILENCE_DURATION_MS",1600,500,3000),
        )


@dataclass(frozen=True)
class VoiceVadSettings:
    cascade: CascadeVadSettings
    native: NativeVadSettings

    @classmethod
    def from_environment(
        cls,environment: Mapping[str,str]|None=None
    )->"VoiceVadSettings":
        env=os.environ if environment is None else environment
        return cls(
            cascade=CascadeVadSettings.from_environment(env),
            native=NativeVadSettings.from_environment(env),
        )
