"""VOICE2: short read-only questions, terse progress, and useful re-asks."""

from __future__ import annotations

from dataclasses import replace

from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn


def _started(*, localized_second: bool = False) -> CuratedProtocolSession:
    fixture = miniprep_fixture()
    if localized_second:
        second = fixture.steps[1]
        fixture = replace(
            fixture,
            localizations={
                f"{second.step_id}/current_step":
                    "2단계: 라이소자임을 넣고 튜브를 뒤집어 섞어요.",
            },
        )
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    session.active = True
    return session


def _route(session: CuratedProtocolSession, text: str, turn_id: int):
    return route_curated_runtime_turn(
        session,
        text,
        turn_id=turn_id,
        language="ko",
        configuration_id=11,
        generation=3,
    ).plan


def _workflow(session: CuratedProtocolSession) -> tuple[object, ...]:
    return (
        session.active,
        session.current_index,
        session.workflow_status,
        session.state()["revision"],
        tuple(sorted(session._completed_step_ids)),
    )


def test_short_timer_word_combinations_are_read_only_at_the_runtime_boundary() -> None:
    phrases = (
        "타임 얼마?",
        "타임 얼마나?",
        "타이머 얼마?",
        "어, 그럼 타이머 얼마나 남았지?",
        "어... 타임, 얼마 남았어?",
        "시간 몇 남았어?",
        "몇 분 남았지?",
    )
    for turn_id, phrase in enumerate(phrases, 20):
        session = _started()
        before = _workflow(session)
        plan = _route(session, phrase, turn_id)
        assert plan.action is CuratedProtocolAction.TIMER_STATUS, phrase
        assert plan.intent_kind == "step_timer_status", phrase
        assert plan.state_changed is False
        assert _workflow(session) == before


def test_short_step_state_questions_are_read_only_at_the_runtime_boundary() -> None:
    cases = (
        ("지금 몇 단계?", CuratedProtocolAction.CURRENT),
        ("어, 지금 단계 몇이야?", CuratedProtocolAction.CURRENT),
        ("다음 뭐야?", CuratedProtocolAction.NEXT_INFORMATION),
        ("그럼 다음에 뭐 해?", CuratedProtocolAction.NEXT_INFORMATION),
    )
    for turn_id, (phrase, action) in enumerate(cases, 40):
        session = _started()
        before = _workflow(session)
        plan = _route(session, phrase, turn_id)
        assert plan.action is action, phrase
        assert plan.state_changed is False
        assert _workflow(session) == before


def test_out_of_scope_reply_gives_three_short_examples_without_mutation() -> None:
    session = _started()
    before = _workflow(session)
    plan = _route(session, "점심 뭐 먹지?", 60)
    assert plan.action is CuratedProtocolAction.OFF_TOPIC
    assert plan.speech_text == "지금은 '현재 단계', '타이머 얼마', '다음 뭐야'처럼 말해 주세요."
    assert plan.state_changed is False
    assert _workflow(session) == before


def test_next_says_one_short_progress_line_and_bare_cancel_keeps_the_revert_gate() -> None:
    session = _started(localized_second=True)
    moved = _route(session, "다음", 70)
    assert moved.action is CuratedProtocolAction.NEXT
    assert moved.speech_text == (
        "1단계 완료. 2단계: 라이소자임을 넣고 튜브를 뒤집어 섞어요. "
        "잘못이면 '취소'."
    )
    assert session.current_index == 1

    asked = _route(session, "취소", 71)
    assert asked.action is CuratedProtocolAction.CLARIFY_COMPLETION
    assert asked.state_changed is False
    assert asked.speech_text == "1단계 완료를 취소하고 1단계로 돌아갈까요?"
    assert session.current_index == 1

    reverted = _route(session, "네", 72)
    assert reverted.action is CuratedProtocolAction.NEXT
    assert reverted.state_changed is True
    assert session.current_index == 0


def test_timer_and_step_near_misses_keep_their_existing_meaning() -> None:
    cases = (
        ("점심 타임 얼마 남았어?", CuratedProtocolAction.OFF_TOPIC, False),
        ("이 단계 시간이 얼마나 걸려?", CuratedProtocolAction.TIMER_STATUS, False),
        # This is still the existing explicit move, not a read-only preview.
        ("다음 단계로 가자", CuratedProtocolAction.NEXT, True),
    )
    for turn_id, (phrase, action, changed) in enumerate(cases, 80):
        session = _started()
        plan = _route(session, phrase, turn_id)
        assert plan.action is action, phrase
        assert plan.state_changed is changed
