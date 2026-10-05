"""Validated environment configuration for both voice activity pipelines."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import shlex
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from voiney_lab.audio import FRAME_MS


class ConfigurationError(ValueError):
    """A named environment setting is malformed or outside its safe range."""


# --- What only xAI provides (lane XO, decision 2 of 2026-10-05) ----------------
#: The features no other provider stands in for yet, by the setting that turns
#: each on, with the name a refusal gives it.
XAI_ONLY_FEATURES: Mapping[str, str] = {
    "VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED": "외부 근거 웹 검색",
    "VOINEY_LAB_WEB_VISUAL_SEARCH_ENABLED": "웹 이미지 검색",
    "VOINEY_LAB_GENERATED_VISUALS_ENABLED": "그림 생성",
    "VOINEY_LAB_SEMANTIC_INTENT_ENABLED": "의미 의도 보조",
}
_ON = frozenset({"1", "true", "yes", "on"})


class XaiKeyRequiredError(ConfigurationError):
    """An xAI-only feature is on and XAI_API_KEY is not set."""


def enabled_xai_only_features(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """The settings of the xAI-only features that are switched on."""

    env = os.environ if environment is None else environment
    return tuple(
        name for name in XAI_ONLY_FEATURES
        if env.get(name, "").strip().casefold() in _ON
    )


def refuse_xai_only_features_without_key(
    environment: Mapping[str, str] | None = None,
) -> None:
    """Refuse to start when an xAI-only feature is on without XAI_API_KEY.

    Fail closed, naming each feature: such a feature would otherwise start
    and then fail on every turn that reaches it. With all four off the server
    starts without an xAI key.
    """

    env = os.environ if environment is None else environment
    enabled = enabled_xai_only_features(env)
    if not enabled or env.get("XAI_API_KEY", "").strip():
        return
    listed = ", ".join(f"{XAI_ONLY_FEATURES[name]}({name})" for name in enabled)
    raise XaiKeyRequiredError(
        f"XAI_API_KEY 가 없는데 xAI 에만 있는 기능이 켜져 있습니다: {listed}. "
        "XAI_API_KEY 를 넣거나, 이 기능을 false 로 끄세요."
    )


# --- Launcher defaults (lane XO, decision 1 of 2026-10-05) --------------------


def launcher_defaults(
    defaults: Sequence[tuple[str, str]],
    *,
    environment: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> tuple[tuple[str, str], ...]:
    """What a launcher exports for its defaults: a person's value wins.

    A name already set in the shell (even to an empty value) is left alone
    and not returned. A name written in the repository ``.env`` is returned
    with the file's value -- the value the server would load anyway, since it
    reads the ``.env`` without overriding the environment -- so a launcher's
    banner shows what the server will run with. Only the names in
    ``defaults`` are ever read from the file. Any other name gets the
    launcher's default.
    """

    env = os.environ if environment is None else environment
    written: Mapping[str, str | None] = {}
    if dotenv_path is not None and dotenv_path.is_file():
        from dotenv import dotenv_values

        written = dotenv_values(dotenv_path)
    chosen: list[tuple[str, str]] = []
    for name, value in defaults:
        if name in env:
            continue
        if name in written:
            value = written[name] or ""
        chosen.append((name, value))
    return tuple(chosen)


def _launcher_defaults_main(arguments: Sequence[str]) -> int:
    """``--launcher-defaults DOTENV NAME=VALUE...``: print shell ``export`` lines."""

    if len(arguments) < 1:
        print("usage: python -m voiney_lab.configuration --launcher-defaults "
              "DOTENV NAME=VALUE...", file=sys.stderr)
        return 2
    pairs = []
    for item in arguments[1:]:
        name, separator, value = item.partition("=")
        if not separator or not name:
            print(f"not NAME=VALUE: {item}", file=sys.stderr)
            return 2
        pairs.append((name, value))
    for name, value in launcher_defaults(pairs, dotenv_path=Path(arguments[0])):
        print(f"export {name}={shlex.quote(value)}")
    return 0


CASCADE_FILLER_DELAY_ENV = "VOINEY_LAB_CASCADE_FILLER_DELAY_MS"
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
            mode=_integer(env,"VOINEY_LAB_CASCADE_VAD_MODE",3,0,3),
            onset_voiced_frames=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_ONSET_VOICED_FRAMES",4,1,100),
            onset_window_frames=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_ONSET_WINDOW_FRAMES",6,1,100),
            prefix_ms=_integer(env,"VOINEY_LAB_CASCADE_VAD_PREFIX_MS",300,20,5000),
            barge_in_prefix_ms=_integer(
                env,"VOINEY_LAB_CASCADE_BARGE_IN_PREFIX_MS",800,300,5000),
            endpoint_silence_ms=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_ENDPOINT_SILENCE_MS",1000,20,10000),
            minimum_speech_ms=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_MIN_SPEECH_MS",240,20,10000),
            maximum_utterance_ms=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_MAX_UTTERANCE_MS",15000,20,300000),
            cooldown_ms=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_COOLDOWN_MS",300,0,10000),
            playback_onset_voiced_frames=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES",12,1,100),
            playback_onset_window_frames=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES",15,1,100),
            listening_onset_voiced_frames=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES",8,1,100),
            listening_onset_window_frames=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES",12,1,100),
            listening_resume_voiced_frames=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES",6,1,100),
            listening_resume_window_frames=_integer(
                env,"VOINEY_LAB_CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES",10,1,100),
        )
        if settings.onset_voiced_frames>settings.onset_window_frames:
            raise ConfigurationError(
                "VOINEY_LAB_CASCADE_VAD_ONSET_VOICED_FRAMES cannot exceed "
                "VOINEY_LAB_CASCADE_VAD_ONSET_WINDOW_FRAMES")
        if settings.minimum_speech_ms>settings.maximum_utterance_ms:
            raise ConfigurationError(
                "VOINEY_LAB_CASCADE_VAD_MIN_SPEECH_MS cannot exceed "
                "VOINEY_LAB_CASCADE_VAD_MAX_UTTERANCE_MS")
        if (settings.playback_onset_voiced_frames>
                settings.playback_onset_window_frames):
            raise ConfigurationError(
                "VOINEY_LAB_CASCADE_VAD_PLAYBACK_ONSET_VOICED_FRAMES cannot exceed "
                "VOINEY_LAB_CASCADE_VAD_PLAYBACK_ONSET_WINDOW_FRAMES")
        if (settings.listening_onset_voiced_frames>
                settings.listening_onset_window_frames):
            raise ConfigurationError(
                "VOINEY_LAB_CASCADE_VAD_LISTENING_ONSET_VOICED_FRAMES cannot exceed "
                "VOINEY_LAB_CASCADE_VAD_LISTENING_ONSET_WINDOW_FRAMES")
        if (settings.listening_resume_voiced_frames>
                settings.listening_resume_window_frames):
            raise ConfigurationError(
                "VOINEY_LAB_CASCADE_VAD_LISTENING_RESUME_VOICED_FRAMES cannot exceed "
                "VOINEY_LAB_CASCADE_VAD_LISTENING_RESUME_WINDOW_FRAMES")
        return settings


@dataclass(frozen=True)
class CascadeSttSettings:
    """Documented xAI REST STT fields that stay off the voice-path critical loop.

    Only the ``xai`` STT provider sends them. Lane SV moved the setting names
    from xAI-only to provider-neutral ones (``VOINEY_LAB_STT_VAD_THRESHOLD``,
    ``VOINEY_LAB_STT_FILLER_WORDS``).
    """

    vad_threshold: float = 0.5
    filler_words: bool = False

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "CascadeSttSettings":
        env = os.environ if environment is None else environment
        return cls(
            vad_threshold=_floating(
                env, "VOINEY_LAB_STT_VAD_THRESHOLD", 0.5, 0.0, 1.0
            ),
            filler_words=_integer(
                env, "VOINEY_LAB_STT_FILLER_WORDS", 0, 0, 1
            ) == 1,
        )


@dataclass(frozen=True)
class VoiceVadSettings:
    cascade: CascadeVadSettings
    stt: CascadeSttSettings

    @classmethod
    def from_environment(
        cls,environment: Mapping[str,str]|None=None
    )->"VoiceVadSettings":
        env=os.environ if environment is None else environment
        return cls(
            cascade=CascadeVadSettings.from_environment(env),
            stt=CascadeSttSettings.from_environment(env),
        )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--launcher-defaults"]:
        raise SystemExit(_launcher_defaults_main(sys.argv[2:]))
    if sys.argv[1:2] == ["--refuse-xai-only-without-key"]:
        # The launchers ask before serving, reading the .env as the server
        # will (the environment wins); the server asks again at import.
        merged: dict[str, str] = {}
        if len(sys.argv) > 2 and Path(sys.argv[2]).is_file():
            from dotenv import dotenv_values

            merged.update({
                key: value for key, value in dotenv_values(sys.argv[2]).items()
                if value is not None
            })
        merged.update(os.environ)
        try:
            refuse_xai_only_features_without_key(merged)
        except XaiKeyRequiredError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)
    print("usage: python -m voiney_lab.configuration "
          "--launcher-defaults DOTENV NAME=VALUE... | --refuse-xai-only-without-key [DOTENV]",
          file=sys.stderr)
    raise SystemExit(2)
