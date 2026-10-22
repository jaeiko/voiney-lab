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
    "시약 엎질렀지",
)
EXPLANATORY_QUESTIONS = (
    "이 단계 설명해 줘 다음 단계 넘어갔나",
    "왜 이렇게 하는지 알려줘 그리고 다음 단계 넘어갔나",
    "다 했나 설명해 줘",
    "다 했나 다음 단계 설명해 줘",
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


@pytest.mark.parametrize("words", EXPLANATORY_QUESTIONS)
def test_an_explanatory_question_preserves_the_open_endpoint_checkpoint(words):
    from voiney_lab.runtime_routing import route_curated_runtime_turn
    session = session_at(4)
    session.plan("5단계 완료했어", turn_id=3, language="ko", configuration_id=1, generation=1)
    before = workflow_checkpoint(session)
    plan = route_curated_runtime_turn(
        session, words, turn_id=4, language="ko", configuration_id=1, generation=1,
    ).plan
    assert not plan.state_changed
    assert not plan.reported_completion
    assert workflow_checkpoint(session) == before


@pytest.mark.parametrize("words", [
    "넘어가도 돼?", "타이머 시작해?", "투명해졌어?", "넘어가도 돼? 시료를 흘렸어",
])
def test_punctuated_questions_do_not_renew_the_endpoint_checkpoint(words):
    from voiney_lab.runtime_routing import route_curated_runtime_turn
    session = session_at(4)
    session.plan("5단계 완료했어", turn_id=3, language="ko", configuration_id=1, generation=1)
    assert session._open_questions(turn_id=4, configuration_id=1, generation=1).first_open is not None
    before = workflow_checkpoint(session)
    plan = route_curated_runtime_turn(
        session, words, turn_id=4, language="ko", configuration_id=1, generation=1,
    ).plan
    assert not plan.state_changed
    assert not plan.reported_completion
    assert not plan.reported_observation
    assert not plan.reported_anomaly
    assert workflow_checkpoint(session) == before


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

    def test_explanatory_questions_keep_the_endpoint_and_execution_records(self):
        async def scenario(socket, listener, say):
            for turn_id, words in enumerate((
                "프로토콜 시작해줘", "넘어가", "1단계 완료했어", "2단계 완료했어",
                "네", "3단계 완료했어", "4단계 완료했어", "5단계 완료했어",
            ), 1):
                await say(turn_id, words)
            assert listener.curated_protocol_session.pending_observation_confirmation is not None
            for turn_id, words in enumerate(EXPLANATORY_QUESTIONS, 9):
                before = workflow_checkpoint(listener.curated_protocol_session)
                experiment = self.experiment(listener.session_id)
                events = self.report()["events"]
                await say(turn_id, words)
                assert workflow_checkpoint(listener.curated_protocol_session) == before, words
                assert self.experiment(listener.session_id) == experiment, words
                # Reading the source may append its existing presentation
                # event, but cannot invent execution or observation records.
                added = self.report()["events"][len(events):]
                assert all(event["event_type"] in {"step_presented", "source_presented"}
                           for event in added), added
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


def test_a_request_to_record_uncertainty_does_not_authorize_a_workflow_tool():
    assert evidence_fence_rejection(
        utterance="타이머 시작했나 메모해 줘", evidence="타이머 시작했나", bounded_control=True,
    ) == "interrogative_not_authorized"


@pytest.mark.parametrize("pending", [False, True])
def test_a_refused_question_proposal_preserves_held_checkpoints(pending):
    from voiney_lab.llm_router import ToolProposal
    session = session_at(4)
    if pending:
        session.plan("5단계 완료했어", turn_id=3, language="ko", configuration_id=1, generation=1)
    before = workflow_checkpoint(session)
    outcome = session.apply_tool_proposal(
        [ToolProposal(tool="record_log", log_type="anomaly", value="시약 엎질렀나", evidence="시약 엎질렀나")],
        transcript="시약 엎질렀나", basis=session.proposal_basis(turn_id=4, generation=1),
        turn_id=4, language="ko", configuration_id=1, generation=1,
    )
    assert outcome.verdict.effect == "refuse"
    assert not outcome.plan.reported_anomaly
    assert workflow_checkpoint(session) == before
