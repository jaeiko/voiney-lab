"""Side-effect-free configuration for optional Protocol persistence."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


PROTOCOL_ENABLED_ENV = "VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED"
PROTOCOL_DATA_DIR_ENV = "VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR"
REPEAT_REVIEW_THRESHOLD_ENV = (
    "VOICE_WORKFLOW_AGENT_REPEAT_CONFIRMATION_REVIEW_THRESHOLD"
)
DEFAULT_REPEAT_REVIEW_THRESHOLD = 5
_MAX_REPEAT_REVIEW_THRESHOLD = 100
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


class ProtocolConfigurationError(ValueError):
    """A Protocol persistence setting is malformed."""


class ProtocolFeatureDisabledError(RuntimeError):
    """Protocol persistence was requested while its feature flag is disabled."""


def _enabled(environment: Mapping[str, str]) -> bool:
    raw = environment.get(PROTOCOL_ENABLED_ENV, "false").strip().casefold()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ProtocolConfigurationError(
        f"{PROTOCOL_ENABLED_ENV} must be true or false"
    )


@dataclass(frozen=True)
class ProtocolPersistenceSettings:
    enabled: bool
    data_dir: Path | None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "ProtocolPersistenceSettings":
        env = os.environ if environment is None else environment
        enabled = _enabled(env)
        raw_data_dir = env.get(PROTOCOL_DATA_DIR_ENV)
        if raw_data_dir is None or not raw_data_dir.strip():
            if enabled:
                raise ProtocolConfigurationError(
                    f"{PROTOCOL_DATA_DIR_ENV} is required when Protocol persistence is enabled"
                )
            return cls(enabled=False, data_dir=None)
        data_dir = Path(raw_data_dir.strip())
        if enabled and not data_dir.is_absolute():
            raise ProtocolConfigurationError(
                f"{PROTOCOL_DATA_DIR_ENV} must be an absolute path"
            )
        return cls(enabled=enabled, data_dir=data_dir)


def repeat_confirmation_review_threshold(
    environment: Mapping[str, str] | None = None,
) -> int:
    """Return how many human-confirmed repetitions run before a check-in.

    This is an operational guardrail, not a scientific limit. The source
    protocol does not define a maximum iteration count and this value must never
    be presented as one: reaching it only makes the server stop and ask the
    researcher whether to continue, pause, or request review.
    """

    env = os.environ if environment is None else environment
    raw = env.get(REPEAT_REVIEW_THRESHOLD_ENV, "").strip()
    if not raw:
        return DEFAULT_REPEAT_REVIEW_THRESHOLD
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProtocolConfigurationError(
            f"{REPEAT_REVIEW_THRESHOLD_ENV} must be a positive integer"
        ) from exc
    if not 1 <= value <= _MAX_REPEAT_REVIEW_THRESHOLD:
        raise ProtocolConfigurationError(
            f"{REPEAT_REVIEW_THRESHOLD_ENV} must be between 1 and "
            f"{_MAX_REPEAT_REVIEW_THRESHOLD}"
        )
    return value
