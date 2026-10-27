"""Production TTS boundary: the provider hears Korean, source/display stay verbatim."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_lane_safe_questions import workflow_checkpoint

from voiney_lab import server
from voiney_lab.voice_providers import TtsProviderSettings


EXAMPLES = tuple((row["written"], row["spoken"]) for row in json.loads(
    (Path(__file__).resolve().parents[1] / "data/fixtures/evaluation/korean_speech_readings.json").read_text()
)["examples"])


@pytest.mark.parametrize(('written', 'spoken'), EXAMPLES)
def test_provider_boundary(written: str, spoken: str) -> None:
    settings = TtsProviderSettings.from_environment({'VOINEY_LAB_TTS_PROVIDER': 'xai'})
    with patch.object(server.TtsProviderSettings, 'from_environment', return_value=settings), \
         patch.object(server, '_provider_speech', return_value=b'pcm') as provider:
        assert server.synthesize(written, 'ko') == b'pcm'
    assert provider.call_args.args[0] == spoken


def test_english_and_scientific_names_are_not_rewritten() -> None:
    text = 'Add 5 µL Tn5 to H3K4me3 in Tris-HCl and CUT&Tag.'
    settings = TtsProviderSettings.from_environment({'VOINEY_LAB_TTS_PROVIDER': 'xai'})
    with patch.object(server.TtsProviderSettings, 'from_environment', return_value=settings), \
         patch.object(server, '_provider_speech', return_value=b'pcm') as provider:
        server.synthesize(text, 'en')
        assert provider.call_args.args[0] == text
        server.synthesize(text, 'ko')
        spoken = provider.call_args.args[0]
        for name in ('Tn5', 'H3K4me3', 'Tris-HCl', 'CUT&Tag'):
            assert name in spoken


@pytest.mark.parametrize("command", ["이 단계 읽어줘", "원문 그대로 읽어 줘"])
def test_voice_turn_keeps_display_source_and_checkpoints(command: str) -> None:
    import asyncio
    from tests import test_reader_speech as reader
    from tests.test_lane_ko2_screen_korean import soil_fixture
    from voiney_lab.language import Transcription
    from voiney_lab.vad import TurnState
    fixture = soil_fixture()
    source = fixture.steps[1].instruction_source_text
    korean = '2단계: 시약수 20 mL를 넣고 현탁액을 5분 동안 저어요.'
    fixture = replace(fixture, localizations={f'{fixture.steps[1].step_id}/current_step': korean})
    session = reader._listener(fixture, 1, model_roles=False)
    session.active_turn_id = 1
    session.next_turn_id = 2
    session.turn_generations[1] = session.generation
    session.detector.state = TurnState.PROCESSING
    settings = TtsProviderSettings.from_environment({'VOINEY_LAB_TTS_PROVIDER': 'xai'})
    before = workflow_checkpoint(session.curated_protocol_session)
    socket = reader._Socket()
    with patch.object(server, 'transcribe', return_value=Transcription(command, 'ko')), \
         patch.object(server.TtsProviderSettings, 'from_environment', return_value=settings), \
         patch.object(server, '_provider_speech', return_value=b'\0\0') as provider:
        asyncio.run(server.run_turn(socket, session, b'\0\0', 1, session.generation))
    heard = ' '.join(call.args[0] for call in provider.call_args_list)
    if command == '원문 그대로 읽어 줘':
        assert '20 mL' in heard
        assert '이십 밀리리터' not in heard
    else:
        assert '이십 밀리리터' in heard
        assert '오 분' in heard
    reply = next(event for event in socket.text if event['type'] == 'reply.complete')
    assert '20 mL' in reply['text']
    assert source in reply['text']
    assert '20 mL' in reply['speech_text']
    assert workflow_checkpoint(session.curated_protocol_session) == before


@pytest.mark.parametrize(('written', 'spoken'), [
    ('12 - 16 h', '열두 시간에서 열여섯 시간'),
    ('-20–-16°C', '섭씨 영하 이십에서 영하 십육 도'),
    ('1:50-1:100', '일 대 오십에서 일 대 백'),
    ('Tn5 H3K4me3 Tris-HCl', 'Tn5 H3K4me3 Tris-HCl'),
])
def test_compound_ranges_and_identifiers(written: str, spoken: str) -> None:
    assert server.normalize_korean_speech(written) == spoken


@pytest.mark.parametrize('written', ['0.5 mL를 넣어요. 5분 기다려요.', '-20°C에서 보관해요. 5분 기다려요.'])
def test_sentence_splitting_keeps_a_leading_decimal_or_negative(written: str) -> None:
    settings = TtsProviderSettings.from_environment({'VOINEY_LAB_TTS_PROVIDER': 'xai'})
    with patch.object(server.TtsProviderSettings, 'from_environment', return_value=settings), \
         patch.object(server, '_provider_speech', return_value=b'pcm') as provider:
        for sentence in server.speech_sentences(written):
            server.synthesize(sentence, 'ko')
    heard = ' '.join(call.args[0] for call in provider.call_args_list)
    assert heard == server.normalize_korean_speech(written)


def test_two_field_ratio_does_not_acquire_time_units() -> None:
    assert server.normalize_korean_speech('10:50') == '십 대 오십'


def test_explicit_english_keeps_leading_decimal() -> None:
    text = '0.5 mL of solution is needed.'
    settings = TtsProviderSettings.from_environment({'VOINEY_LAB_TTS_PROVIDER': 'xai'})
    with patch.object(server.TtsProviderSettings, 'from_environment', return_value=settings), \
         patch.object(server, '_provider_speech', return_value=b'pcm') as provider:
        server.synthesize(text, 'en')
    assert provider.call_args.args[0] == text


@pytest.mark.parametrize(('written', 'spoken'), [
    ('값은 0.50이에요.', '값은 영 점 오 영이에요.'),
    ('Sample-12-16', 'Sample-12-16'),
    ('2026-10-27', '2026-10-27'),
    ('7.2.1단계', '7.2.1단계'),
])
def test_bare_decimal_and_non_quantity_identifiers(written: str, spoken: str) -> None:
    assert server.normalize_korean_speech(written) == spoken


@pytest.mark.parametrize(('written', 'spoken'), [
    ('1 h', '한 시간'),
    ('16개', '열여섯 개'),
    ('20개', '스무 개'),
    ('12–16개', '열두 개에서 열여섯 개'),
])
def test_hours_and_item_counts_use_korean_counters(written: str, spoken: str) -> None:
    assert server.normalize_korean_speech(written) == spoken


def test_uncertainty_between_explicit_units_is_read_as_words() -> None:
    assert server.normalize_korean_speech('20%±2%') == '이십 퍼센트 플러스 마이너스 이 퍼센트'


@pytest.mark.parametrize(('written', 'spoken'), [
    ('-00:15:00', '마이너스 십오 분'),
    ('−01:00:00', '마이너스 한 시간'),
    ('Sample-01:02:03', 'Sample-01:02:03'),
    ('1:2:3', '1:2:3'),
])
def test_clock_signs_and_ambiguous_identifiers(written: str, spoken: str) -> None:
    assert server.normalize_korean_speech(written) == spoken


@pytest.mark.parametrize(('written', 'language', 'expected'), [
    ('시료를 준비해요.\n-20°C에서 보관해요.', 'ko', '시료를 준비해요. 섭씨 영하 이십 도에서 보관해요.'),
    ('시료를 준비해요.\n0.5 mL를 넣어요.', 'ko', '시료를 준비해요. 영 점 오 밀리리터를 넣어요.'),
    ('Prepare the sample.\n-20°C storage.', 'en', 'Prepare the sample. -20°C storage.'),
    ('Prepare the sample.\n0.5 mL is needed.', 'en', 'Prepare the sample. 0.5 mL is needed.'),
])
def test_multiline_quantity_signs_reach_the_voice(written: str, language: str, expected: str) -> None:
    settings = TtsProviderSettings.from_environment({'VOINEY_LAB_TTS_PROVIDER': 'xai'})
    with patch.object(server.TtsProviderSettings, 'from_environment', return_value=settings), \
         patch.object(server, '_provider_speech', return_value=b'pcm') as provider:
        for sentence in server.speech_sentences(written):
            server.synthesize(sentence, language)
    assert ' '.join(call.args[0] for call in provider.call_args_list) == expected
