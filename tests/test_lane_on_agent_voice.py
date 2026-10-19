"""Lane ON, decision ② (2026-10-18): the agent's voice, chosen by the person.

Human decision (interpretation of 10/16, confirmed 10/18): "목소리 선택" is
choosing the voice the agent speaks with -- two to four Korean voices of
the configured TTS provider, each with [들어 보기], the choice saved to the
account so the server speaks with it. The voice list is fixed per provider
here; the preview is one real TTS call per voice (fake-backed in tests);
the choice is an experimenter setting (``agent_voice``) and the server's
own ``synthesize`` reads it from the speaking session. Nothing here
advances a protocol.
"""

from __future__ import annotations

import asyncio
import io
import json
import wave
from unittest.mock import patch

import httpx
import pytest

from voiney_lab import server as server_module
from voiney_lab import voice_providers as vp
from voiney_lab.server import app, synthesize, voice_socket
from voiney_lab.workspace_store import EXPERIMENTER_SETTING_VALUES


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _get(path):
    async def run():
        async with _client() as client:
            return await client.get(path)
    return asyncio.run(run())


def _post(path, payload):
    async def run():
        async with _client() as client:
            return await client.post(path, json=payload)
    return asyncio.run(run())


def _put(path, payload):
    async def run():
        async with _client() as client:
            return await client.put(path, json=payload)
    return asyncio.run(run())


def _dev(monkeypatch, tmp_path, provider="google_cloud"):
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_ENABLED", "false")
    monkeypatch.setenv("VOINEY_LAB_TTS_PROVIDER", provider)
    monkeypatch.delenv("VOINEY_LAB_TTS_VOICE", raising=False)
    monkeypatch.delenv("VOINEY_LAB_AUTH_MODE", raising=False)
    server_module._EXPERIMENTER_SETTINGS_MEMORY.clear()
    server_module._VOICE_PREVIEW_CACHE.clear()


# --- The list --------------------------------------------------------------------


def test_each_provider_offers_korean_voices_with_the_default_first():
    for provider in (vp.GOOGLE_CLOUD, vp.ELEVENLABS):
        voices = vp.korean_voices(provider)
        assert 2 <= len(voices) <= 4, provider
        assert voices[0].voice_id == vp.DEFAULT_TTS_VOICES[provider]
        assert len({voice.voice_id for voice in voices}) == len(voices)
        for voice in voices:
            assert voice.label_ko and voice.provider == provider
    assert vp.korean_voices(vp.XAI)[0].voice_id == vp.DEFAULT_TTS_VOICES[vp.XAI]
    assert vp.korean_voices("nobody") == ()


def test_the_google_voices_are_chirp_3_hd_korean_voices():
    for voice in vp.korean_voices(vp.GOOGLE_CLOUD):
        assert voice.voice_id.startswith("ko-KR-Chirp3-HD-")
        locale, name = vp.google_voice_for(voice.voice_id, "ko")
        assert (locale, name) == ("ko-KR", voice.voice_id)


def test_the_voices_route_lists_the_configured_providers_voices_and_the_choice(monkeypatch, tmp_path):
    _dev(monkeypatch, tmp_path)
    response = _get("/api/voice/voices")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "google_cloud"
    assert [voice["voice_id"] for voice in body["voices"]] == [
        voice.voice_id for voice in vp.korean_voices(vp.GOOGLE_CLOUD)]
    assert body["selected"] == "default"
    assert body["default_voice_id"] == vp.DEFAULT_TTS_VOICES[vp.GOOGLE_CLOUD]
    assert body["preview_text_ko"] == vp.PREVIEW_SENTENCE_KO
    saved = _put("/api/experimenter/settings", {"agent_voice": "ko-KR-Chirp3-HD-Kore"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["agent_voice"] == "ko-KR-Chirp3-HD-Kore"
    assert _get("/api/voice/voices").json()["selected"] == "ko-KR-Chirp3-HD-Kore"
    assert _get("/api/experimenter/settings").json()["settings"]["agent_voice"] == "ko-KR-Chirp3-HD-Kore"


def test_a_voice_of_another_provider_or_an_invented_one_is_refused(monkeypatch, tmp_path):
    _dev(monkeypatch, tmp_path)
    for value in ("JBFqnCBsd6RMkjVDRZzb", "leo", "ko-KR-Chirp3-HD-Nobody", ""):
        response = _put("/api/experimenter/settings", {"agent_voice": value})
        assert response.status_code == 400, (value, response.text)
    assert _put("/api/experimenter/settings", {"agent_voice": "default"}).status_code == 200


def test_without_a_tts_provider_the_list_is_empty_and_says_so(monkeypatch, tmp_path):
    _dev(monkeypatch, tmp_path, provider="")
    response = _get("/api/voice/voices")
    assert response.status_code == 200
    assert response.json()["voices"] == []
    assert response.json()["provider"] == ""


# --- The preview -------------------------------------------------------------------


def test_the_preview_is_one_tts_call_with_that_voice_answered_as_a_wav(monkeypatch, tmp_path):
    _dev(monkeypatch, tmp_path)
    calls = []

    def fake_synthesize(text, language, settings):
        calls.append((text, language, settings.voice, settings.provider))
        return b"\x01\x00" * 1600

    monkeypatch.setattr(vp, "synthesize", fake_synthesize)
    response = _post("/api/voice/preview", {"voice_id": "ko-KR-Chirp3-HD-Kore"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("audio/wav")
    with wave.open(io.BytesIO(response.content), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
        assert audio.getnframes() == 1600
    assert calls == [(vp.PREVIEW_SENTENCE_KO, "ko", "ko-KR-Chirp3-HD-Kore", "google_cloud")]
    # Heard again: no second call.
    again = _post("/api/voice/preview", {"voice_id": "ko-KR-Chirp3-HD-Kore"})
    assert again.status_code == 200 and len(calls) == 1
    refused = _post("/api/voice/preview", {"voice_id": "ko-KR-Chirp3-HD-Nobody"})
    assert refused.status_code == 422 and refused.json() == {"detail": "voice_unknown"}
    assert len(calls) == 1


def test_a_failed_preview_is_said_not_cached(monkeypatch, tmp_path):
    _dev(monkeypatch, tmp_path)

    def failing(text, language, settings):
        raise vp.SpeechProviderError("google_cloud TTS failed (500): boom")

    monkeypatch.setattr(vp, "synthesize", failing)
    response = _post("/api/voice/preview", {"voice_id": "ko-KR-Chirp3-HD-Kore"})
    assert response.status_code == 502
    assert response.json() == {"detail": "voice_preview_failed"}
    assert not server_module._VOICE_PREVIEW_CACHE


# --- The server speaks with it ------------------------------------------------------


def test_synthesize_speaks_with_the_sessions_voice_when_one_is_chosen(monkeypatch, tmp_path):
    _dev(monkeypatch, tmp_path)
    seen = []

    def fake_synthesize(text, language, settings):
        seen.append(settings.voice)
        return b"\x00\x00" * 320

    monkeypatch.setattr(vp, "synthesize", fake_synthesize)
    session = server_module.ListenerSession()
    assert session.tts_voice is None
    token = server_module._SPEAKING_SESSION.set(session)
    try:
        synthesize("안녕하세요", "ko")
        session.tts_voice = "ko-KR-Chirp3-HD-Kore"
        synthesize("안녕하세요", "ko")
    finally:
        server_module._SPEAKING_SESSION.reset(token)
    assert seen == [vp.DEFAULT_TTS_VOICES[vp.GOOGLE_CLOUD], "ko-KR-Chirp3-HD-Kore"]


class _Socket:
    def __init__(self):
        self.headers = {}
        self.cookies = {}
        self.sent = []

    async def accept(self):
        pass

    async def send_text(self, value):
        self.sent.append(json.loads(value))

    async def send_bytes(self, value):
        pass

    async def close(self, code=1000, reason=""):
        pass

    async def receive(self):
        await asyncio.sleep(0.01)
        return {"type": "websocket.disconnect", "code": 1000}


def test_the_connection_takes_the_saved_voice_and_says_so_in_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_WORKSPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "demo")
    monkeypatch.setenv("VOINEY_LAB_TTS_PROVIDER", "google_cloud")
    monkeypatch.delenv("VOINEY_LAB_TTS_VOICE", raising=False)
    monkeypatch.delenv("VOINEY_LAB_AUTH_MODE", raising=False)
    for name in ("VOINEY_LAB_OIDC_ISSUER", "VOINEY_LAB_OIDC_AUDIENCE", "VOINEY_LAB_OIDC_JWKS_URL"):
        monkeypatch.delenv(name, raising=False)
    first = _Socket()
    asyncio.run(voice_socket(first))
    ready = next(item for item in first.sent if item["type"] == "ready")
    assert ready["voice_profile"]["voice_id"] == vp.DEFAULT_TTS_VOICES[vp.GOOGLE_CLOUD]
    assert ready["voice_profile"]["agent_voice"] == "default"
    assert _put("/api/experimenter/settings", {"agent_voice": "ko-KR-Chirp3-HD-Aoede"}).status_code == 200
    second = _Socket()
    asyncio.run(voice_socket(second))
    ready = next(item for item in second.sent if item["type"] == "ready")
    assert ready["voice_profile"]["voice_id"] == "ko-KR-Chirp3-HD-Aoede"
    assert ready["voice_profile"]["agent_voice"] == "ko-KR-Chirp3-HD-Aoede"


def test_the_setting_names_are_in_the_table_of_values():
    assert "agent_voice" in EXPERIMENTER_SETTING_VALUES
    assert "voice_calibration" in EXPERIMENTER_SETTING_VALUES
    assert EXPERIMENTER_SETTING_VALUES["voice_calibration"] == ("none", "measured", "skipped")


@pytest.mark.parametrize("state", ["measured", "skipped"])
def test_a_calibration_outcome_is_kept_as_the_experimenters_setting(monkeypatch, tmp_path, state):
    _dev(monkeypatch, tmp_path)
    saved = _put("/api/experimenter/settings", {"voice_calibration": state})
    assert saved.status_code == 200
    assert _get("/api/experimenter/settings").json()["settings"]["voice_calibration"] == state
    assert _get("/api/experimenter/settings").json()["settings"]["agent_voice"] == "default"
