#!/usr/bin/env python3
"""Bounded human-run comparison of Google STT V2 inline adaptation."""
from __future__ import annotations

import argparse
import json
import os
import time
import wave
from dataclasses import replace
from pathlib import Path

import requests

from voiney_lab.audio import SAMPLE_RATE
from voiney_lab.configuration import ConfigurationError
from voiney_lab.voice_providers import (
    GOOGLE_CLOUD_V2,
    STT_MODEL_ENV,
    STT_PROVIDER_ENV,
    SpeechProviderError,
    SttProviderSettings,
    transcribe_google_cloud_v2,
)

TERMS = ("AMBIC", "acetonitrile", "Solution A", "gel plug", "트립신")
SCENARIO_COUNT = 3
MAX_RECOGNITION_CALLS = 10


def _pcm16(path: Path, parser: argparse.ArgumentParser) -> bytes:
    try:
        with wave.open(str(path), "rb") as recording:
            valid = (
                recording.getnchannels() == 1
                and recording.getsampwidth() == 2
                and recording.getframerate() == SAMPLE_RATE
                and recording.getcomptype() == "NONE"
                and 0 < recording.getnframes() < SAMPLE_RATE * 60
            )
            if not valid:
                parser.error("WAV must be mono 16-bit 16 kHz PCM and shorter than 60 seconds")
            return recording.readframes(recording.getnframes())
    except wave.Error as exc:
        reason = str(exc).casefold()
        if any(word in reason for word in ("format", "compression", "unsupported")):
            parser.error(
                "WAV encoding is unsupported; convert it to mono 16-bit "
                "16 kHz PCM (WAVE_FORMAT_PCM)"
            )
        parser.error("WAV header or audio data is damaged or truncated")
    except (OSError, EOFError):
        parser.error("WAV header or audio data is damaged or truncated")


def _settings(parser: argparse.ArgumentParser) -> SttProviderSettings:
    try:
        settings = SttProviderSettings.from_environment(os.environ)
    except ConfigurationError as exc:
        parser.error(str(exc))
    if settings.provider != GOOGLE_CLOUD_V2:
        parser.error(f"{STT_PROVIDER_ENV} must be google_cloud_v2")
    if settings.model != "chirp_3":
        parser.error(f"{STT_MODEL_ENV} must be chirp_3")
    return settings


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="make the three recognition calls")
    parser.add_argument("--wav", required=True, type=Path, help="the same local WAV for every scenario")
    parser.add_argument("--max-calls", type=int, default=MAX_RECOGNITION_CALLS)
    args = parser.parse_args(arguments)
    if not args.execute:
        parser.error("--execute is required")
    if not SCENARIO_COUNT <= args.max_calls <= MAX_RECOGNITION_CALLS:
        parser.error("--max-calls must be between 3 and 10")

    pcm = _pcm16(args.wav, parser)
    settings = _settings(parser)
    assert settings.google_v2 is not None
    configured_location = settings.google_v2.location
    other_location = "eu" if configured_location != "eu" else "us"
    scenarios = (
        ("inline_adaptation", configured_location, TERMS),
        ("other_supported_location", other_location, TERMS),
        ("without_adaptation", configured_location, ()),
    )
    for name, location, keyterms in scenarios:
        scenario_settings = replace(
            settings,
            google_v2=replace(
                settings.google_v2,
                location=location,
                adaptation_enabled=bool(keyterms),
            ),
        )
        started = time.monotonic()
        try:
            result = transcribe_google_cloud_v2(
                pcm,
                scenario_settings,
                language="ko",
                keyterms=keyterms,
                retry_without_adaptation=False,
            )
            status = result.response_status
            characters = len(result.text)
        except SpeechProviderError as exc:
            status = exc.status
            characters = 0
        except requests.RequestException:
            status = None
            characters = 0
        except ConfigurationError as exc:
            parser.error(str(exc))
        response_ms = max(0, round((time.monotonic() - started) * 1000))
        print(json.dumps({
            "scenario": name,
            "location": location,
            "status": status,
            "characters": characters,
            "response_ms": response_ms,
        }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
