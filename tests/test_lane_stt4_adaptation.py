"""Chirp 3 inline adaptation rejection recovery, entirely fake-backed."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.test_lane_hear2_timer_commands import soil_fixture
from tests.test_lane_hear_drops import CONFIG, DropHarness, Socket, utterance
from tests.test_voice_providers import FakeResponse, PCM
from voiney_lab import server, voice_providers as vp
from voiney_lab.curated_protocol import CuratedProtocolSession

PROJECT = "VOINEY_LAB_GOOGLE_STT_V2_PROJECT"
CREDENTIALS = "VOINEY_LAB_GOOGLE_STT_V2_CREDENTIALS_FILE"
ADAPTATION = "VOINEY_LAB_GOOGLE_STT_V2_ADAPTATION_ENABLED"
TOKEN = "fake-private-bearer-token"
TERMS = ("AMBIC", "acetonitrile", "Solution A", "gel plug", "트립신")
SUCCESS = {
    "results": [{
        "languageCode": "ko-KR",
        "alternatives": [{"transcript": "현재 단계 알려 줘", "words": []}],
    }],
}


@pytest.fixture
def v2_env(tmp_path, monkeypatch):
    credentials = tmp_path / "service-account.json"
    credentials.write_text(json.dumps({"private_key": "fake-private-key"}))
    credentials.chmod(0o600)
    monkeypatch.setenv(vp.STT_PROVIDER_ENV, vp.GOOGLE_CLOUD_V2)
    monkeypatch.setenv(PROJECT, "test-project")
    monkeypatch.setenv(CREDENTIALS, str(credentials))
    monkeypatch.setenv(ADAPTATION, "true")
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "source_protocol_only")
    return credentials


@pytest.mark.parametrize(
    "status,provider_code",
    [(400, "INVALID_ARGUMENT"), (404, "NOT_FOUND")],
)
def test_adaptation_400_or_404_retries_once_without_adaptation(
    v2_env, status, provider_code,
):
    refused = FakeResponse(
        {"error": {"status": provider_code, "message": "provider detail must stay private"}},
        status=status,
    )
    with patch.object(vp, "_google_v2_token", return_value=TOKEN), patch.object(
        vp.requests, "post", side_effect=[refused, FakeResponse(SUCCESS)]
    ) as post, patch.object(vp, "transcribe_google_cloud") as v1, patch.object(
        vp, "transcribe_elevenlabs"
    ) as elevenlabs:
        result = server.transcribe(PCM, keyterms=TERMS)

    assert result.text == "현재 단계 알려 줘"
    assert result.adaptation_fallback.status == status
    assert result.adaptation_fallback.code == provider_code.casefold()
    assert post.call_count == 2
    first = post.call_args_list[0].kwargs["json"]["config"]
    second = post.call_args_list[1].kwargs["json"]["config"]
    assert first["adaptation"]["phraseSets"][0]["inlinePhraseSet"]["phrases"] == [
        {"value": term} for term in TERMS
    ]
    assert "adaptation" not in second
    assert first["model"] == second["model"] == "chirp_3"
    assert post.call_args_list[0].args == post.call_args_list[1].args
    v1.assert_not_called()
    elevenlabs.assert_not_called()


@pytest.mark.parametrize(
    "status,provider_code",
    [(400, "INVALID_ARGUMENT"), (404, "NOT_FOUND")],
)
def test_voice_session_remembers_rejection_and_exposes_fallback_reason(
    v2_env, status, provider_code,
):
    harness = DropHarness()
    session, clock = harness.listener()
    curated = CuratedProtocolSession(soil_fixture())
    curated.activate_configured()
    curated.plan("프로토콜 시작해줘", turn_id=1, language="ko")
    session.curated_protocol_session = curated
    socket = Socket([
        {"bytes": utterance(0.1)}, {"sleep": 0.8},
        {"advance": 5.0}, {"bytes": utterance(0.1)}, {"sleep": 0.8},
    ], clock)
    refused = FakeResponse(
        {"error": {"status": provider_code, "message": "provider detail must stay private"}},
        status=status,
    )
    metrics: list[dict] = []
    with patch.object(server, "ListenerSession", return_value=session), patch.object(
        server.VoiceVadSettings, "from_environment", return_value=SimpleNamespace(cascade=object())
    ), patch.object(server.VadConfig, "from_settings", return_value=CONFIG), patch.object(
        vp, "_google_v2_token", return_value=TOKEN
    ), patch.object(
        vp.requests, "post",
        side_effect=[refused, FakeResponse(SUCCESS), FakeResponse(SUCCESS)],
    ) as post, patch.object(server, "synthesize", return_value=b"\0\0" * 320), patch.object(
        server, "_record_workspace_metric", side_effect=lambda **item: metrics.append(item)
    ), patch.object(vp, "transcribe_google_cloud") as v1, patch.object(
        vp, "transcribe_elevenlabs"
    ) as elevenlabs:
        asyncio.run(server.voice_socket(socket))

    assert post.call_count == 3
    configs = [call.kwargs["json"]["config"] for call in post.call_args_list]
    assert "adaptation" in configs[0]
    assert all("adaptation" not in config for config in configs[1:])
    assert not [event for event in socket.sent if event["type"] == "speech.dropped"]
    done = [event for event in socket.sent if event["type"] == "turn.done"]
    assert len(done) == 2
    assert [event["reason_code"] for event in done] == [
        "stt_adaptation_disabled", "stt_adaptation_disabled",
    ]
    recovery = [item for item in metrics if item.get("metric_name") == "stt_adaptation_fallback"]
    assert recovery == [{
        "category": "voice",
        "metric_name": "stt_adaptation_fallback",
        "dimensions": {
            "status": "recovered",
            "reason_code": "stt_adaptation_disabled",
            "event_kind": provider_code.casefold(),
        },
    }]
    v1.assert_not_called()
    elevenlabs.assert_not_called()
