"""Questions without STT punctuation cannot authorize workflow or records."""
from __future__ import annotations

import unittest

import pytest

from tests.lane_n_support import VoiceNotesHarness, notes_fixture
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.intent_fences import evidence_fence_rejection

ENDPOINT_QUESTIONS = (
    "투명해졌나", "투명해졌니", "투명해졌냐", "투명해졌을까", "투명해졌으려나",
    "투명해졌나 보다", "투명해졌나 봐", "투명해졌는지", "투명해졌던가",
    "투명해졌지", "투명해졌나 이제 다음", "투명해졌나 왜 그렇지", "투명해졌나봐", "투명해진 거야", "투명해졌을라나", "아마 투명해졌어",
)
CONTROL_QUESTIONS = (
    "타이머 시작했나", "타이머 시작했니", "타이머 시작했지", "타이머 시작했죠",
    "타이머 시작된 거야", "타이머 시작된 건가", "타이머 시작했던가",
    "타이머 시작했나 모르겠네", "시약 엎질렀나", "시약 엎질렀으려나",
    "시약 엎질렀나 보다", "다음 단계 넘어갔나", "이거 기록해야 되나",
    "타이머 시작합니까", "타이머 시작했습니까", "타이머 시작됐는가", "타이머 얼마야",
)


def workflow_checkpoint(session):
    checkpoint = session._checkpoint()
    return tuple(value for index, value in enumerate(checkpoint) if index not in {4, 5, 6, 7, 8, 11})


def session_at(index: int) -> CuratedProtocolSession:
    session = CuratedProtocolSession(notes_fixture())
    session.activate_configured()
    session.plan("프로토콜 시작해줘", turn_id=1, language="ko")
    session.plan("넘어가", turn_id=2, language="ko")
    session.current_index = index
    return session


@pytest.mark.parametrize("words", ENDPOINT_QUESTIONS)
@pytest.mark.parametrize("pending", [False, True])
def test_endpoint_question_does_not_report_or_advance(words, pending):
    session = session_at(4)
    if pending:
        session.plan("5단계 완료했어", turn_id=3, language="ko")
    before = workflow_checkpoint(session)
    plan = session.plan(words, turn_id=4, language="ko")
    assert not plan.state_changed
    assert not plan.reported_observation
    assert not plan.reported_completion
    assert workflow_checkpoint(session) == before


@pytest.mark.parametrize("words", CONTROL_QUESTIONS)
def test_router_evidence_cannot_authorize_question_or_speculation(words):
    assert evidence_fence_rejection(
        utterance=words, evidence=words, bounded_control=True,
    ) == "interrogative_not_authorized"


class ServedQuestionTests(VoiceNotesHarness, unittest.TestCase):
    def test_timer_spill_resume_and_forward_questions_keep_both_ledgers(self):
        async def scenario(socket, listener, say):
            await say(1, "프로토콜 시작해줘")
            await say(2, "넘어가")
            await say(3, "1단계 완료했어")
            for turn_id, words in enumerate(CONTROL_QUESTIONS, 4):
                before = workflow_checkpoint(listener.curated_protocol_session)
                state = self.experiment(listener.session_id)
                report = self.report()
                await say(turn_id, words)
                assert workflow_checkpoint(listener.curated_protocol_session) == before, words
                assert self.experiment(listener.session_id) == state, words
                assert self.report() == report, words
            await say(30, "잠깐")
            before = workflow_checkpoint(listener.curated_protocol_session)
            await say(31, "재개했나")
            assert workflow_checkpoint(listener.curated_protocol_session) == before
        self.voice(scenario)



@pytest.mark.parametrize("words", [
    "메모해 줘, 밴드가 투명해진 것 같아",
    "밴드가 투명해진 것 같아 일단 메모해 줘",
])
def test_an_explicit_record_command_keeps_uncertain_words_without_releasing_endpoint(words):
    session = session_at(4)
    plan = session.plan(words, turn_id=4, language="ko")
    assert session.current_index == 4
    assert not plan.reported_completion
    assert plan.reported_observation
    assert plan.observation_outcome == "밴드가 투명해진 것 같아" or "투명해진 것 같아" in plan.observation_outcome
    assert not session._endpoint_observations
