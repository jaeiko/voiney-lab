"""Korean commands and stored glossary names reach the shared STT boundary."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from tests.test_lane_fx2_glossary_hearing import GLOSSARY, soil_fixture
from voiney_lab import server
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.language import Transcription
from voiney_lab.protocol_translation import with_stored_translations


COMMANDS = (
    "다음", "타이머", "일시정지", "다시 시작", "종료",
    "특이사항", "메모", "기록해 줘", "화면 크게",
)


def test_shared_stt_context_prioritizes_korean_commands_and_stored_glossary():
    curated = CuratedProtocolSession(
        with_stored_translations(soil_fixture(), (), GLOSSARY)
    )
    listener = server.ListenerSession(curated_protocol_session=curated)

    ordinary = server.cascade_transcription_context(
        listener, audio_origin="ordinary")
    barge_in = server.cascade_transcription_context(
        listener, audio_origin="barge_in")

    assert ordinary.keyterms == barge_in.keyterms
    assert ordinary.keyterms[:len(COMMANDS)] == COMMANDS
    for term in ("진탕기", "히팅 블록", "시약수", "냉장고"):
        assert term in ordinary.keyterms
    assert "shaker table" in ordinary.keyterms

    with patch.object(
        server, "transcribe", return_value=Transcription("타이머", "ko"),
    ) as provider:
        server.transcribe_cascade_audio(b"\0\0", ordinary)
    assert provider.call_args.kwargs["keyterms"] == ordinary.keyterms


def test_korean_priority_keeps_the_cross_provider_limit_and_dump_fence():
    glossary = tuple(
        (f"source-term-{index}", f"한국용어{index}")
        for index in range(150)
    )
    curated = SimpleNamespace(
        active=False,
        fixture=SimpleNamespace(steps=(), translation_glossary=glossary),
        pending_observation_confirmation=None,
        pending_completion_confirmation=None,
        stt_keyterms=lambda **_kwargs: tuple(
            f"source-term-{index}" for index in range(100)
        ),
    )
    listener = server.ListenerSession(curated_protocol_session=curated)

    context = server.cascade_transcription_context(
        listener, audio_origin="ordinary")

    assert len(context.keyterms) == 100
    assert context.keyterms[:len(COMMANDS)] == COMMANDS
    assert "한국용어90" in context.keyterms
    assert "한국용어91" not in context.keyterms
    assert all(1 <= len(term) <= 50 for term in context.keyterms)
    assert not set(COMMANDS) & set(server.keyterm_dump_terms(context.keyterms))
