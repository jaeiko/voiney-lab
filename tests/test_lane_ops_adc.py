"""ADC shapes through server.transcribe; google-auth is real, HTTP is fake."""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from tests.test_lane_stt3_google_v2 import CREDENTIALS, TOKEN, v2_env
from tests.test_voice_providers import FakeResponse, PCM
from tests.test_lane_hear2_timer_commands import FIELDS
from voiney_lab import server, voice_providers as vp
from voiney_lab.configuration import ConfigurationError

STS = 'https://sts.googleapis.com/v1/token'
IAM = ('https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'
       'speech@test-project.iam.gserviceaccount.com:generateAccessToken')
IMDS = 'http://169.254.169.254/latest/'


def credential_info(shape):
    if shape == 'authorized_user':
        return {'type': shape, 'client_id': 'fake.apps.googleusercontent.com',
                'client_secret': 'fake-client-secret', 'refresh_token': 'fake-refresh-token',
                'quota_project_id': 'test-quota-project'}
    if shape == 'external_account':
        return {'type': shape,
                'audience': '//iam.googleapis.com/projects/123456789/locations/global/'
                            'workloadIdentityPools/voiney-aws/providers/ec2',
                'subject_token_type': 'urn:ietf:params:aws:token-type:aws4_request',
                'token_url': STS, 'service_account_impersonation_url': IAM,
                'credential_source': {
                    'environment_id': 'aws1',
                    'region_url': IMDS + 'meta-data/placement/availability-zone',
                    'url': IMDS + 'meta-data/iam/security-credentials',
                    'imdsv2_session_token_url': IMDS + 'api/token',
                    'regional_cred_verification_url':
                        'https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15'}}
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {'type': 'service_account', 'client_email': 'speech@test-project.iam.gserviceaccount.com',
            'token_uri': vp._GOOGLE_TOKEN_URL,
            'private_key': key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()}


def install_info(v2_env, info):
    Path(v2_env[CREDENTIALS]).write_text(json.dumps(info))
    vp._google_v2_credentials.cache_clear()


def fake_transport(calls, fail_url=None, failure=None):
    def send(method):
        def request(url, **kwargs):
            calls.append((method, url, kwargs))
            if url == fail_url:
                if failure:
                    raise failure
                return FakeResponse({'error': {'status': 'PERMISSION_DENIED',
                    'message': 'fake-refresh-token'}}, status=403)
            if url == IMDS + 'api/token':
                return FakeResponse({}, content=b'fake-imds-token')
            if url.endswith('/placement/availability-zone'):
                return FakeResponse({}, content=b'ap-northeast-2a')
            if url.endswith('/iam/security-credentials'):
                return FakeResponse({}, content=b'VoineySpeech')
            if url.endswith('/iam/security-credentials/VoineySpeech'):
                return FakeResponse({'AccessKeyId': 'fake-aws-id',
                    'SecretAccessKey': 'fake-aws-secret', 'Token': 'fake-aws-token'})
            if url == IAM:
                return FakeResponse({'accessToken': TOKEN, 'expireTime': '2099-01-01T00:00:00Z'})
            if url in (STS, vp._GOOGLE_TOKEN_URL):
                return FakeResponse({'access_token': TOKEN, 'expires_in': 3600,
                    'token_type': 'Bearer', 'issued_token_type': 'urn:ietf:params:oauth:token-type:access_token'})
            assert '-speech.googleapis.com/v2/' in url, url
            return FakeResponse({'results': [{'alternatives': [{'transcript': '확인했어요.'}]}]})
        return request
    return send


@pytest.fixture(autouse=True)
def no_aws_environment(monkeypatch):
    for name in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
                 'AWS_REGION', 'AWS_DEFAULT_REGION'):
        monkeypatch.delenv(name, raising=False)
    yield
    vp._google_v2_credentials.cache_clear()


@pytest.mark.parametrize('shape', ['service_account', 'external_account', 'authorized_user'])
def test_adc_authenticates_three_protocol_domains_and_reuses_token(v2_env, shape):
    install_info(v2_env, credential_info(shape))
    calls = []
    first_request_calls = 0
    send = fake_transport(calls)
    with patch.object(vp.requests, 'post', side_effect=send('POST')), \
         patch.object(vp.requests, 'get', side_effect=send('GET')), \
         patch.object(vp.requests, 'put', side_effect=send('PUT')):
        for term in ('토양 분석', '단백질 분석', '배터리 소재 분석'):
            assert server.transcribe(PCM, keyterms=(term,)).text == '확인했어요.'
            if not first_request_calls:
                first_request_calls = len(calls)
    speech = [call for call in calls if '-speech.googleapis.com/v2/' in call[1]]
    assert len(speech) == 3
    for _, _, options in speech:
        assert options['headers']['Authorization'] == 'Bearer ' + TOKEN
    for _, _, options in calls:
        assert options['allow_redirects'] is False
        assert 0 < options['timeout'] <= vp.DEFAULT_TIMEOUT_SECONDS
    assert all(calls[i][2]['timeout'] >= calls[i + 1][2]['timeout']
               for i in range(first_request_calls - 1))
    if shape == 'external_account':
        assert [method for method, url, _ in calls if url == IMDS + 'api/token'] == ['PUT', 'PUT']
        assert len([c for c in calls if c[1] == STS]) == 1
        assert len([c for c in calls if c[1] == IAM]) == 1
        assert not any('cloudresourcemanager' in c[1] for c in calls)
    else:
        assert len([c for c in calls if c[1] == vp._GOOGLE_TOKEN_URL]) == 1
    if shape == 'authorized_user':
        assert all(options['headers']['x-goog-user-project'] == 'test-quota-project'
                   for _, _, options in speech)


@pytest.mark.parametrize('url', [IMDS + 'api/token', IMDS + 'meta-data/placement/availability-zone',
    IMDS + 'meta-data/iam/security-credentials', IMDS + 'meta-data/iam/security-credentials/VoineySpeech', STS, IAM])
@pytest.mark.parametrize('timeout', [False, True])
def test_wif_failure_is_bounded_sanitized_and_never_recognizes(v2_env, url, timeout):
    install_info(v2_env, credential_info('external_account'))
    calls = []
    send = fake_transport(calls, url, requests.Timeout('fake-refresh-token') if timeout else None)
    with patch.object(vp.requests, 'post', side_effect=send('POST')), \
         patch.object(vp.requests, 'get', side_effect=send('GET')), \
         patch.object(vp.requests, 'put', side_effect=send('PUT')), \
         pytest.raises(vp.SpeechProviderError) as caught:
        server.transcribe(PCM)
    assert caught.value.code == ('timeout' if timeout else 'permission_denied')
    assert calls[-1][1] == url
    assert 'fake-refresh-token' not in ''.join(traceback.format_exception(caught.value))
    assert not any('-speech.googleapis.com/' in c[1] for c in calls)


@pytest.mark.parametrize('field', ['token_url', 'service_account_impersonation_url',
    'region_url', 'url', 'imdsv2_session_token_url', 'regional_cred_verification_url', 'environment_id'])
def test_wif_untrusted_destinations_are_refused_before_any_request(v2_env, field):
    info = credential_info('external_account')
    target = info if field in ('token_url', 'service_account_impersonation_url') else info['credential_source']
    target[field] = 'https://untrusted.invalid/fake-refresh-token'
    install_info(v2_env, info)
    with patch.object(vp.requests, 'post') as post, patch.object(vp.requests, 'get') as get, \
         patch.object(vp.requests, 'put') as put, pytest.raises(ConfigurationError) as caught:
        server.transcribe(PCM)
    assert not (post.called or get.called or put.called)
    assert 'fake-refresh-token' not in str(caught.value)


@pytest.mark.parametrize('field,fixture_factory,index,seconds', FIELDS[:3])
def test_wif_failure_keeps_voice_workflow_checkpoint_across_domains(
    v2_env, field, fixture_factory, index, seconds,
):
    from tests.test_lane_hear_drops import DropHarness, utterance
    from voiney_lab.curated_protocol import CuratedProtocolSession
    install_info(v2_env, credential_info('external_account'))
    harness = DropHarness()
    listener, clock = harness.listener()
    curated = CuratedProtocolSession(fixture_factory())
    curated.activate_configured()
    curated.plan('프로토콜 시작해줘', turn_id=1, language='ko')
    listener.curated_protocol_session = curated
    checkpoint = curated._checkpoint()
    disconnected = []
    original_stop = listener.stop

    def stop():
        disconnected.append(curated._checkpoint())
        original_stop()

    calls = []
    send = fake_transport(calls, IAM)
    actual_transcribe = server.transcribe
    with patch.object(vp.requests, 'post', side_effect=send('POST')), \
         patch.object(vp.requests, 'get', side_effect=send('GET')), \
         patch.object(vp.requests, 'put', side_effect=send('PUT')), \
         patch.object(listener, 'stop', side_effect=stop):
        sent = harness.run_socket(listener, clock, [
            {'bytes': utterance(0.1)}, {'sleep': 0.3},
        ], heard=lambda pcm: actual_transcribe(pcm).text)
    assert disconnected == [checkpoint]
    assert calls[-1][1] == IAM
    assert not harness.of(sent, 'turn.route_decision')
    assert [row['code'] for row in harness.of(sent, 'speech.dropped')] == ['permission_denied']
    assert 'fake-refresh-token' not in json.dumps(sent) + harness.logs


@pytest.mark.parametrize('failure', [None, 'http', 'provider'])
def test_opt_in_auth_check_outputs_only_result_codes(v2_env, monkeypatch, capsys, failure):
    from scripts import check_google_stt_auth as check
    monkeypatch.setattr('sys.argv', ['check_google_stt_auth.py'])
    problem = {'http': requests.Timeout('fake-refresh-token'),
               'provider': vp.SpeechProviderError('fake-refresh-token', code='timeout')}.get(failure)
    with patch.object(check, '_google_v2_token', return_value=TOKEN, side_effect=problem) as token:
        result = check.main()
    output = capsys.readouterr().out
    assert result == (0 if failure is None else 1)
    assert TOKEN not in output and 'fake-refresh-token' not in output
    assert str(Path(v2_env[CREDENTIALS])) not in output
    assert token.call_count == 1
