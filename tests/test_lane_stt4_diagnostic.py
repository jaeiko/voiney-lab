"""The human-only STT4 diagnostic is bounded and never prints transcript text."""
from __future__ import annotations

import json
import os
import wave
from unittest.mock import patch

import pytest

from voiney_lab.language import Transcription
from voiney_lab.voice_providers import SpeechProviderError


def _wav(path) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)


def test_diagnostic_runs_three_single_attempt_scenarios_and_prints_no_text(
    tmp_path, capsys,
):
    from scripts import check_google_stt_v2_adaptation as diagnostic

    audio = tmp_path / "same-recording.wav"
    credentials = tmp_path / "credentials.json"
    _wav(audio)
    credentials.write_text(json.dumps({"private_key": "must-not-print"}))
    credentials.chmod(0o600)
    environment = {
        "VOINEY_LAB_STT_PROVIDER": "google_cloud_v2",
        "VOINEY_LAB_STT_MODEL": "chirp_3",
        "VOINEY_LAB_GOOGLE_STT_V2_PROJECT": "test-project",
        "VOINEY_LAB_GOOGLE_STT_V2_CREDENTIALS_FILE": str(credentials),
        "VOINEY_LAB_GOOGLE_STT_V2_LOCATION": "us",
    }
    calls = []

    def transcribe(pcm, settings, *, language, keyterms, retry_without_adaptation):
        calls.append((
            settings.google_v2.location,
            getattr(settings.google_v2, "adaptation_enabled", None),
            keyterms,
            retry_without_adaptation,
            pcm,
        ))
        if len(calls) == 1:
            raise SpeechProviderError(
                "private provider reply", status=404, code="not_found")
        text = "private transcript" if keyterms else "another private transcript"
        return Transcription(text, "ko", response_status=200)

    with patch.dict(os.environ, environment, clear=True), patch.object(
        diagnostic, "transcribe_google_cloud_v2", side_effect=transcribe,
    ):
        assert diagnostic.main([
            "--execute", "--wav", str(audio), "--max-calls", "10",
        ]) == 0

    assert [
        (location, enabled, bool(terms), retry)
        for location, enabled, terms, retry, _ in calls
    ] == [
        ("us", True, True, False),
        ("eu", True, True, False),
        ("us", False, False, False),
    ]
    assert all(pcm == b"\0\0" * 16000 for _, _, _, _, pcm in calls)
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [
        {key: value for key, value in row.items() if key != "response_ms"}
        for row in rows
    ] == [
        {"scenario": "inline_adaptation", "location": "us", "status": 404, "characters": 0},
        {"scenario": "other_supported_location", "location": "eu", "status": 200, "characters": 18},
        {"scenario": "without_adaptation", "location": "us", "status": 200, "characters": 26},
    ]
    assert all(
        isinstance(row.get("response_ms"), int) and row["response_ms"] >= 0
        for row in rows
    )
    visible = json.dumps(rows, ensure_ascii=False)
    assert "private transcript" not in visible
    assert "must-not-print" not in visible
    assert str(credentials) not in visible


@pytest.mark.parametrize("arguments", [
    ["--wav", "recording.wav"],
    ["--execute", "--wav", "recording.wav", "--max-calls", "11"],
    ["--execute", "--wav", "recording.wav", "--max-calls", "2"],
])
def test_diagnostic_requires_explicit_execution_and_three_to_ten_call_budget(arguments):
    from scripts import check_google_stt_v2_adaptation as diagnostic

    with pytest.raises(SystemExit):
        diagnostic.main(arguments)


def test_diagnostic_names_a_damaged_or_unsupported_wav_reason(tmp_path, capsys):
    from scripts import check_google_stt_v2_adaptation as diagnostic

    broken = tmp_path / "old-diagnostic.wav"
    broken.write_bytes(b"RIFF\x10\0\0\0WAVEfmt ")

    with pytest.raises(SystemExit):
        diagnostic.main([
            "--execute", "--wav", str(broken), "--max-calls", "3",
        ])

    message = capsys.readouterr().err
    assert (
        "WAV header or audio data is damaged or truncated" in message
        or "WAV encoding is unsupported" in message
    )
