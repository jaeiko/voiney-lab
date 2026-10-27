"""Google V2 production STT boundary, entirely fake-backed."""
from __future__ import annotations

import base64
import json
import os
import traceback
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient

from tests.test_voice_providers import FakeResponse, PCM
from voiney_lab import server, voice_providers as vp
from voiney_lab.configuration import ConfigurationError

PROJECT = 'VOINEY_LAB_GOOGLE_STT_V2_PROJECT'
CREDENTIALS = 'VOINEY_LAB_GOOGLE_STT_V2_CREDENTIALS_FILE'
LOCATION = 'VOINEY_LAB_GOOGLE_STT_V2_LOCATION'
DENOISE = 'VOINEY_LAB_GOOGLE_STT_V2_DENOISE'
TOKEN = 'fake-private-bearer-token'
SECRET = 'fake-private-key-content'


@pytest.fixture
def v2_env(tmp_path, monkeypatch):
    path = tmp_path / 'service-account.json'
    path.write_text(json.dumps({'private_key': SECRET}))
    path.chmod(0o600)
    values = {vp.STT_PROVIDER_ENV: 'google_cloud_v2', PROJECT: 'test-project', CREDENTIALS: str(path)}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(vp.STT_MODEL_ENV, raising=False)
    monkeypatch.delenv(vp.STT_LANGUAGE_ENV, raising=False)
    monkeypatch.delenv(LOCATION, raising=False)
    monkeypatch.delenv(DENOISE, raising=False)
    monkeypatch.setenv('VOINEY_LAB_USAGE_SCOPE', 'source_protocol_only')
    return values


def test_server_transcribe_sends_inline_v2_config_and_reads_offsets(v2_env):
    payload = {'results': [{'languageCode': 'ko-KR', 'alternatives': [{
        'transcript': '1.5 mL 넣었어요.', 'words': [
            {'word': '1.5', 'startOffset': '0.1s', 'endOffset': '0.3s'}]}]}]}
    with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(
        vp.requests, 'post', return_value=FakeResponse(payload)
    ) as post:
        result = server.transcribe(PCM, keyterms=('AMBIC', 'AMBIC', '토양 추출'))
    args, kwargs = post.call_args
    assert args == ('https://us-speech.googleapis.com/v2/projects/test-project/locations/us/recognizers/_:recognize',)
    assert kwargs['headers']['Authorization'] == f'Bearer {TOKEN}'
    assert 'X-goog-api-key' not in kwargs['headers']
    assert kwargs['allow_redirects'] is False
    body = kwargs['json']
    assert body['content'] == base64.b64encode(PCM).decode('ascii')
    assert body['config']['explicitDecodingConfig'] == {
        'encoding': 'LINEAR16', 'sampleRateHertz': 16000, 'audioChannelCount': 1}
    assert body['config']['model'] == 'chirp_3'
    assert body['config']['languageCodes'] == ['ko-KR']
    assert body['config']['features'] == {'enableAutomaticPunctuation': True, 'enableWordTimeOffsets': True}
    assert body['config']['denoiserConfig'] == {'denoiseAudio': True}
    assert body['config']['adaptation'] == {'phraseSets': [{'inlinePhraseSet': {
        'phrases': [{'value': 'AMBIC'}, {'value': '토양 추출'}]}}]}
    assert result.text == '1.5 mL 넣었어요.'
    assert result.detected_language == 'ko'
    assert result.words == ({'word': '1.5', 'start': 0.1, 'end': 0.3},)
    assert result.duration_seconds == 1.0 and result.response_status == 200
    assert post.call_count == 1
    assert server.stt_endpoint_label() == 'google_cloud_v2:/v2/recognizers/_:recognize'


@pytest.mark.parametrize('language,locale', [('en', 'en-US'), ('vi', 'vi-VN'), (None, 'ko-KR')])
def test_language_denoise_and_adaptation_limits(v2_env, monkeypatch, language, locale):
    monkeypatch.setenv(DENOISE, 'false')
    monkeypatch.setenv(LOCATION, 'eu')
    terms = ('', 'x' * 101, '정확한 μL', '정확한 μL') + tuple(f'term-{n}' for n in range(1200))
    with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(
        vp.requests, 'post', return_value=FakeResponse({})
    ) as post:
        result = server.transcribe(PCM, language=language, keyterms=terms)
    config = post.call_args.kwargs['json']['config']
    assert config['languageCodes'] == [locale]
    assert config['denoiserConfig'] == {'denoiseAudio': False}
    phrases = config['adaptation']['phraseSets'][0]['inlinePhraseSet']['phrases']
    assert phrases[0] == {'value': '정확한 μL'}
    assert len(phrases) == 1000
    assert sum(len(p['value']) for p in phrases) <= 100000
    assert result.text == '' and result.words == ()


@pytest.mark.parametrize('payload', [{}, {'results': []}, {'results': [{'alternatives': []}]}])
def test_empty_results_are_empty_transcriptions(v2_env, payload):
    with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(
        vp.requests, 'post', return_value=FakeResponse(payload)
    ):
        assert server.transcribe(PCM).text == ''


@pytest.mark.parametrize('status,code', [(401, 'UNAUTHENTICATED'), (403, 'PERMISSION_DENIED'),
    (429, 'RESOURCE_EXHAUSTED'), (400, 'INVALID_ARGUMENT'), (503, 'UNAVAILABLE')])
def test_provider_errors_never_contain_response_or_secrets(v2_env, caplog, capsys, status, code):
    payload = {'error': {'status': code, 'message': SECRET + TOKEN}}
    with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(
        vp.requests, 'post', return_value=FakeResponse(payload, status=status)
    ) as post, pytest.raises(vp.SpeechProviderError) as caught:
        server.transcribe(PCM)
    assert caught.value.status == status and caught.value.code == code.lower()
    visible = ''.join(traceback.format_exception(caught.value)) + caplog.text + str(capsys.readouterr())
    assert SECRET not in visible and TOKEN not in visible
    assert post.call_count == 1


@pytest.mark.parametrize('failure,code', [(requests.Timeout(TOKEN), 'timeout'),
    (requests.ConnectionError(SECRET), 'unreachable')])
def test_transport_failures_are_sanitized_without_fallback(v2_env, failure, code):
    with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(
        vp.requests, 'post', side_effect=failure
    ) as post, pytest.raises(vp.SpeechProviderError) as caught:
        server.transcribe(PCM)
    assert caught.value.code == code
    assert TOKEN not in ''.join(traceback.format_exception(caught.value))
    assert SECRET not in ''.join(traceback.format_exception(caught.value))
    assert post.call_count == 1


@pytest.mark.parametrize('field,value', [(PROJECT, ''), (PROJECT, '../bad'), (CREDENTIALS, ''),
    (CREDENTIALS, '/missing/private.json'), (LOCATION, 'https://bad'), (DENOISE, 'perhaps'),
    (vp.STT_LANGUAGE_ENV, 'bad-language'), (vp.STT_TIMEOUT_ENV, 'bad-timeout')])
def test_configuration_errors_expose_only_names_in_readiness(v2_env, monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    with TestClient(server.app) as client:
        response = client.get('/readyz')
    assert response.status_code == 503
    assert response.json()['fields'] == [field]
    assert value == '' or value not in response.text
    assert TOKEN not in response.text and SECRET not in response.text


def test_credentials_in_any_git_tree_or_symlink_to_it_are_refused(v2_env, tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').write_text('gitdir: elsewhere')
    path = repo / 'private.json'
    path.write_text(SECRET)
    path.chmod(0o600)
    link = tmp_path / 'link.json'
    link.symlink_to(path)
    for candidate in (path, link):
        monkeypatch.setenv(CREDENTIALS, str(candidate))
        with pytest.raises(ConfigurationError, match=CREDENTIALS):
            vp.SttProviderSettings.from_environment()


def test_v2_is_stt_only_and_dormant_settings_do_not_affect_others(monkeypatch):
    with pytest.raises(ConfigurationError, match=vp.TTS_PROVIDER_ENV):
        vp.TtsProviderSettings.from_environment({vp.TTS_PROVIDER_ENV: 'google_cloud_v2'})
    assert vp.SttProviderSettings.from_environment({vp.STT_PROVIDER_ENV: 'xai', DENOISE: 'bad'}).provider == 'xai'


def test_oauth_exchange_uses_fixed_endpoint_caches_token_and_rotates_key(v2_env, tmp_path):
    """Real google-auth signing and parsing, both HTTP requests replaced."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()
    path = Path(v2_env[CREDENTIALS])
    path.write_text(json.dumps({'type': 'service_account', 'private_key': private,
        'client_email': 'test@test-project.iam.gserviceaccount.com', 'token_uri': vp._GOOGLE_TOKEN_URL}))
    vp._google_v2_credentials.cache_clear()
    calls = []

    def fake(url, **kwargs):
        calls.append((url, kwargs))
        if url == vp._GOOGLE_TOKEN_URL:
            return FakeResponse({'access_token': TOKEN, 'expires_in': 3600, 'token_type': 'Bearer'})
        return FakeResponse({'results': []})

    with patch.object(vp.requests, 'post', side_effect=fake):
        server.transcribe(PCM)
        server.transcribe(PCM)
        # A replaced file must not keep the old credential cache entry.
        path.write_text(path.read_text() + '\n')
        server.transcribe(PCM)
    assert len([url for url, _ in calls if url == vp._GOOGLE_TOKEN_URL]) == 2
    assert len(calls) == 5
    token_request = calls[0][1]
    assert token_request['allow_redirects'] is False
    assert token_request['timeout'] <= vp.DEFAULT_TIMEOUT_SECONDS
    assert b'grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer' in token_request['data']
    assert 'Authorization' not in token_request['headers']
    assert calls[1][1]['headers']['Authorization'] == f'Bearer {TOKEN}'
    assert calls[1][1]['timeout'] <= token_request['timeout']
    assert private not in repr(vp.SttProviderSettings.from_environment())
    assert TOKEN not in repr(vp.SttProviderSettings.from_environment())
    vp._google_v2_credentials.cache_clear()


@pytest.mark.parametrize('payload', [None, [], {'results': None}, {'results': [None]},
    {'results': [{'alternatives': 'bad'}]}, {'results': [{'alternatives': [{'transcript': 42}]}]}])
def test_malformed_responses_fail_safely(v2_env, payload):
    with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(
        vp.requests, 'post', return_value=FakeResponse(payload, content=b'{bad' if payload is None else None)
    ), pytest.raises(vp.SpeechProviderError) as caught:
        server.transcribe(PCM)
    assert caught.value.code == 'provider_error'
    assert TOKEN not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('mode', [0o644, 0o640, 0o666])
def test_json_file_permissions_are_private(v2_env, mode):
    Path(v2_env[CREDENTIALS]).chmod(mode)
    with pytest.raises(ConfigurationError, match=CREDENTIALS):
        vp.SttProviderSettings.from_environment()


@pytest.mark.parametrize('bad_content', [SECRET, json.dumps({'type': 'authorized_user', 'refresh_token': TOKEN}),
    json.dumps({'type': 'service_account', 'token_uri': 'https://untrusted.invalid/' + TOKEN})])
def test_malformed_or_other_credentials_do_not_leak_or_call(v2_env, bad_content, caplog, capsys):
    Path(v2_env[CREDENTIALS]).write_text(bad_content)
    with patch.object(vp.requests, 'post') as post, pytest.raises(ConfigurationError) as caught:
        server.transcribe(PCM)
    assert not post.called
    visible = ''.join(traceback.format_exception(caught.value)) + caplog.text + str(capsys.readouterr())
    assert SECRET not in visible and TOKEN not in visible
    vp._google_v2_credentials.cache_clear()


@pytest.mark.parametrize('kind', ['refused', 'malformed', 'timeout'])
def test_auth_failure_stops_before_recognition(v2_env, kind):
    from types import SimpleNamespace

    def refresh(request):
        response = request(vp._GOOGLE_TOKEN_URL, 'POST', body=b'fake-assertion')
        if kind == 'malformed':
            raise ValueError(TOKEN + SECRET)
        assert response.status == 200

    credentials = SimpleNamespace(valid=False, token=None, refresh=refresh)
    import threading
    response = FakeResponse({'error': {'status': 'PERMISSION_DENIED', 'message': TOKEN + SECRET}}, status=403)
    with patch.object(vp, '_google_v2_credentials', return_value=(credentials, threading.Lock())), patch.object(
        vp.requests, 'post', side_effect=requests.Timeout(TOKEN) if kind == 'timeout' else None,
        return_value=response if kind == 'refused' else FakeResponse({'access_token': TOKEN})
    ) as post, pytest.raises(vp.SpeechProviderError) as caught:
        server.transcribe(PCM)
    assert post.call_count == 1
    assert post.call_args.args == (vp._GOOGLE_TOKEN_URL,)
    assert caught.value.code == {'refused': 'permission_denied', 'malformed': 'unauthorized', 'timeout': 'timeout'}[kind]
    assert TOKEN not in ''.join(traceback.format_exception(caught.value))
    assert SECRET not in ''.join(traceback.format_exception(caught.value))


def test_readiness_does_not_authenticate_or_return_path(v2_env):
    with patch.object(vp.requests, 'post') as post, TestClient(server.app) as client:
        response = client.get('/readyz')
    assert response.status_code == 200
    assert not post.called
    assert v2_env[CREDENTIALS] not in response.text


def test_google_failure_uses_existing_voice_socket_outage_contract(v2_env):
    from tests.test_lane_hear_drops import DropHarness, utterance
    from tests.test_lane_hear2_timer_commands import soil_fixture
    from voiney_lab.curated_protocol import CuratedProtocolSession
    harness = DropHarness()
    session, clock = harness.listener()
    curated = CuratedProtocolSession(soil_fixture())
    curated.activate_configured()
    curated.plan('프로토콜 시작해줘', turn_id=1, language='ko')
    session.curated_protocol_session = curated
    checkpoint = curated._checkpoint()
    at_disconnect = []
    original_stop = session.stop

    def stop():
        # Disconnect normally resets the run; inspect the last live checkpoint.
        at_disconnect.append(curated._checkpoint())
        original_stop()

    actual_transcribe = server.transcribe
    metrics = []
    with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(vp.requests, 'post',
        return_value=FakeResponse({'error': {'status': 'RESOURCE_EXHAUSTED', 'message': TOKEN + SECRET}}, status=429)
    ) as post, patch.object(session, 'stop', side_effect=stop):
        sent = harness.run_socket(session, clock, [
            {'bytes': utterance(0.1)}, {'sleep': 0.3},
            {'advance': 5.0}, {'bytes': utterance(0.1)}, {'sleep': 0.3},
        ], heard=lambda pcm: actual_transcribe(pcm).text, metrics=metrics)
    dropped = harness.of(sent, 'speech.dropped')
    assert len(dropped) == 2 and all(m['code'] == 'resource_exhausted' for m in dropped)
    assert len([m for m in harness.of(sent, 'server.words') if m['words_kind'] == 'stt_failure']) == 1
    assert not harness.of(sent, 'turn.route_decision')
    assert all(m['route'] == 'server_words' and m['result_kind'] == 'stt_failure'
               for m in harness.of(sent, 'turn.done'))
    assert post.call_count == 2
    assert at_disconnect == [checkpoint]
    assert all(m['reason_ko'] == server.STT_FAILURE_REASON_WORDS['rate_limited'] for m in dropped)
    visible = json.dumps(sent, ensure_ascii=False) + harness.logs + json.dumps(metrics)
    assert TOKEN not in visible and SECRET not in visible and 'Traceback' not in visible


@pytest.mark.parametrize('terms', [('blocking buffer', 'western blot'),
    ('Porapak', 'headspace collection'), ('pH', 'soil extract')])
def test_protocol_terms_are_passed_verbatim_across_fields(v2_env, terms):
    with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(
        vp.requests, 'post', return_value=FakeResponse({})
    ) as post:
        server.transcribe(PCM, keyterms=terms)
    assert post.call_args.kwargs['json']['config']['adaptation']['phraseSets'][0]['inlinePhraseSet']['phrases'] == [
        {'value': term} for term in terms]


def test_redirect_and_untrusted_error_code_are_not_followed_or_exposed(v2_env):
    for status, payload, expected in [(302, {}, 'provider_refused'),
        (401, {'error': {'status': TOKEN, 'message': SECRET}}, 'unauthorized')]:
        with patch.object(vp, '_google_v2_token', return_value=TOKEN), patch.object(
            vp.requests, 'post', return_value=FakeResponse(payload, status=status)
        ) as post, pytest.raises(vp.SpeechProviderError) as caught:
            server.transcribe(PCM)
        assert caught.value.status == status and caught.value.code == expected
        assert post.call_count == 1 and post.call_args.kwargs['allow_redirects'] is False
        assert TOKEN not in ''.join(traceback.format_exception(caught.value))


def test_time_spent_authenticating_consumes_recognition_deadline(v2_env):
    def token(settings, deadline, *, request_headers=None):
        assert deadline == 11.0
        return TOKEN
    with patch.object(vp.time, 'monotonic', side_effect=[10.0, 12.0]), patch.object(
        vp, '_google_v2_token', side_effect=token
    ), patch.object(vp.requests, 'post') as post, pytest.raises(vp.SpeechProviderError) as caught:
        settings = vp.SttProviderSettings.from_environment({**os.environ, vp.STT_TIMEOUT_ENV: '1'})
        vp.transcribe(PCM, settings)
    assert caught.value.code == 'timeout' and not post.called
