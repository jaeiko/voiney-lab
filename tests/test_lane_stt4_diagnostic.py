"""The human-only STT4 diagnostic is bounded and never prints transcript text."""
from __future__ import annotations

import json
import os
import wave
from unittest.mock import patch

import pytest

from voiney_lab.language import Transcription


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
        calls.append((settings.google_v2.location, keyterms, retry_without_adaptation, pcm))
        text = "private transcript" if keyterms else "another private transcript"
        return Transcription(text, "ko", response_status=200)

    with patch.dict(os.environ, environment, clear=True), patch.object(
        diagnostic, "transcribe_google_cloud_v2", side_effect=transcribe,
    ):
        assert diagnostic.main([
            "--execute", "--wav", str(audio), "--max-calls", "10",
        ]) == 0

    assert [(location, bool(terms), retry) for location, terms, retry, _ in calls] == [
        ("us", True, False), ("eu", True, False), ("us", False, False),
    ]
    assert all(pcm == b"\0\0" * 16000 for _, _, _, pcm in calls)
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows == [
        {"scenario": "inline_adaptation", "location": "us", "status": 200, "characters": 18},
        {"scenario": "other_supported_location", "location": "eu", "status": 200, "characters": 18},
        {"scenario": "without_adaptation", "location": "us", "status": 200, "characters": 26},
    ]
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
