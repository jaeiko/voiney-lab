"""Tool proposals from the lane R router, as the server rules on them.

A model behind the front rules may propose one change per turn with
``change_state`` or ``record_log``. The proposal is evidence, never
authorization (AGENTS rules 2-3 as revised by decision D1): llm_router rules
on it against server-owned facts, and CuratedProtocolSession.
apply_tool_proposal carries an accepted one out through plan()'s own branches.
Nothing is wired to server.py yet and every test is offline.

Decisions of 2026-10-02 checked here: D2 ``next`` only ever opens a question;
D5 ``stop`` needs "종료" and asks once more; D6 a duration other than the
source's starts nothing and asks; D7 "덜 됐는데 그냥 넘어가자" is refused with
the step's completion criterion; D10 the timer question is held through a
pause like the others.
"""

from __future__ import annotations

import dataclasses
import json
import unittest

from tests.protocol_vocabulary_support import SOURCE_PDF, in_gel_fixture, miniprep_fixture
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.llm_router import (
    CHANGE_STATE_TOOL,
    RECORD_LOG_TOOL,
    REFUSAL_REASONS,
    ROUTER_TOOLS,
    ProposalBasis,
    RouterTurnFacts,
    ToolProposal,
    parse_tool_call,
    stated_duration_seconds,
    validate_tool_proposals,
)


def _change(action: str, evidence: str, target: str | None = None) -> ToolProposal:
    return ToolProposal(tool="change_state", action=action, target_step=target, evidence=evidence)


def _record(log_type: str, value: str, evidence: str) -> ToolProposal:
    return ToolProposal(tool="record_log", log_type=log_type, value=value, evidence=evidence)


def _facts(utterance: str, **changes: object) -> RouterTurnFacts:
    values: dict[str, object] = dict(
        utterance=utterance, language="ko", turn_id=5, generation=1,
        workflow_revision=3, step_id="step-4", current_step_label="4",
        workflow_active=True, workflow_status="active", paused=False,
        experiment_started=True, experiment_running=True, open_question=None,
        observation_step=False, step_timer_seconds=0, timer_running=False,
        control_question=False, transcript_unreliable=False,
    )
    values.update(changes)
    return RouterTurnFacts(**values)  # type: ignore[arg-type]


_BASIS = ProposalBasis(turn_id=5, generation=1, workflow_revision=3, step_id="step-4")


class SchemaTests(unittest.TestCase):
    def test_two_tools_with_closed_arguments(self) -> None:
        self.assertEqual(
            [tool["function"]["name"] for tool in ROUTER_TOOLS], ["change_state", "record_log"]
        )
        change = CHANGE_STATE_TOOL["function"]["parameters"]
        self.assertFalse(change["additionalProperties"])
        self.assertEqual(change["required"], ["action", "evidence"])
        self.assertEqual(
            change["properties"]["action"]["enum"],
            ["start", "next", "stop", "pause", "resume", "start_timer"],
        )
        record = RECORD_LOG_TOOL["function"]["parameters"]
        self.assertFalse(record["additionalProperties"])
        self.assertEqual(record["required"], ["type", "value", "evidence"])
        self.assertEqual(record["properties"]["type"]["enum"], ["observation", "anomaly"])
        # The duration of a timer is the source's: the tool has no such argument.
        self.assertNotIn("duration", json.dumps(change))

    def test_parsing_fails_closed(self) -> None:
        self.assertEqual(
            parse_tool_call("change_state", '{"action": "next", "evidence": "다 했어"}'),
            _change("next", "다 했어"),
        )
        self.assertEqual(
            parse_tool_call("record_log", {"type": "observation", "value": "A-170", "evidence": "메모 A-170"}),
            _record("observation", "A-170", "메모 A-170"),
        )
        for name, arguments, code in (
            ("advance_step", {"action": "next", "evidence": "x"}, "tool_unknown"),
            ("change_state", "{not json", "arguments_not_json"),
            ("change_state", "[1, 2]", "arguments_not_json"),
            ("change_state", {"action": "next"}, "arguments_invalid"),
            ("change_state", {"action": "skip", "evidence": "x"}, "arguments_invalid"),
            ("change_state", {"action": "next", "evidence": " "}, "arguments_invalid"),
            ("change_state", {"action": "next", "evidence": "x" * 201}, "arguments_invalid"),
            ("change_state", {"action": "next", "evidence": "x", "duration": 600}, "arguments_invalid"),
            ("change_state", {"action": "next", "evidence": "x", "target_step": 7}, "arguments_invalid"),
            ("record_log", {"type": "observation", "evidence": "x"}, "arguments_invalid"),
            ("record_log", {"type": "note", "value": "x", "evidence": "x"}, "arguments_invalid"),
            ("record_log", {"type": "anomaly", "value": "x" * 501, "evidence": "x"}, "arguments_invalid"),
        ):
            with self.subTest(name=name, arguments=arguments):
                self.assertEqual(parse_tool_call(name, arguments), code)

    def test_a_duration_said_aloud_is_read(self) -> None:
        for said, seconds in (
            ("10분 재줘", 600), ("15분 재줘", 900), ("1시간 30분", 5400), ("십오 분 타이머", 900),
            ("삼십분", 1800), ("한 시간", 3600), ("90 seconds", 90), ("타이머 시작해줘", None),
            ("이 단계 시간 재 줘", None), ("두 분이 하세요", None),
        ):
            with self.subTest(said=said):
                self.assertEqual(stated_duration_seconds(said), seconds)


class ValidationTests(unittest.TestCase):
    """Every refusal code, from server-owned facts alone."""

    def rule(self, proposals, facts, basis=_BASIS):
        return validate_tool_proposals(proposals, facts, basis)

    def test_each_refusal_code(self) -> None:
        cases = (
            ("no_proposal", [], _facts("다 했어")),
            ("tool_unknown", ["tool_unknown"], _facts("다 했어")),
            ("arguments_not_json", ["arguments_not_json"], _facts("다 했어")),
            ("arguments_invalid", ["arguments_invalid"], _facts("다 했어")),
            ("more_than_one_proposal",
             [_change("pause", "잠깐"), _record("observation", "잠깐", "잠깐")], _facts("잠깐")),
            ("stale_proposal", [_change("pause", "잠깐")], _facts("잠깐", turn_id=6)),
            ("stale_proposal", [_change("pause", "잠깐")], _facts("잠깐", workflow_revision=4)),
            ("stale_proposal", [_change("pause", "잠깐")], _facts("잠깐", step_id="step-5")),
            ("stale_proposal", [_change("pause", "잠깐")], _facts("잠깐", generation=2)),
            ("transcript_unreliable", [_change("pause", "잠깐")], _facts("잠깐", transcript_unreliable=True)),
            ("pending_gate_owns_turn", [_change("next", "다 했어")], _facts("다 했어", open_question="completion")),
            ("workflow_not_active", [_change("pause", "잠깐 멈춰")],
             _facts("잠깐 멈춰", workflow_active=False, workflow_status="preview",
                    experiment_started=False, experiment_running=False)),
            ("workflow_not_active", [_change("stop", "실험 종료할게요")],
             _facts("실험 종료할게요", workflow_active=False, workflow_status="stopped",
                    experiment_running=False)),
            ("already_started", [_change("start", "시작하자")], _facts("시작하자")),
            ("session_ended", [_change("start", "다시 시작하자")],
             _facts("다시 시작하자", workflow_active=False, workflow_status="stopped",
                    experiment_running=False)),
            ("workflow_paused", [_change("start", "시작하자")],
             _facts("시작하자", workflow_active=False, workflow_status="paused",
                    experiment_started=False, experiment_running=False, paused=True)),
            ("workflow_paused", [_change("next", "다 했어")], _facts("다 했어", paused=True)),
            ("workflow_paused", [_record("observation", "A-170", "메모 A-170")],
             _facts("메모 A-170", paused=True)),
            ("workflow_paused", [_change("start_timer", "타이머 시작해줘")],
             _facts("타이머 시작해줘", paused=True, step_timer_seconds=900)),
            ("evidence_not_verbatim", [_change("next", "현재 단계 완료했어")], _facts("다 했어")),
            ("interrogative_not_authorized", [_change("next", "다 했어")], _facts("다 했어?")),
            ("interrogative_not_authorized", [_change("pause", "멈춰도 돼")],
             _facts("멈춰도 돼", control_question=True)),
            ("hypothetical_not_authorized", [_change("next", "다 했다고 하면")], _facts("다 했다고 하면 넘어가자")),
            ("target_not_current_step", [_change("next", "12단계로 넘어가자", target="12")],
             _facts("12단계로 넘어가자")),
            ("no_completion_word", [_change("next", "버퍼 1 넣었어")], _facts("버퍼 1 넣었어")),
            ("end_word_missing", [_change("stop", "그만 끝내자")], _facts("그만 끝내자")),
            # "1단계부터 해볼까" is a start word since decision 8 (lane R3).
            ("no_start_word", [_change("start", "이제 해볼까")],
             _facts("자 이제 해볼까", workflow_active=False, workflow_status="ready",
                    experiment_started=False, experiment_running=False)),
            ("no_start_word", [_change("start", "타이머 시작해줘")],
             _facts("타이머 시작해줘", workflow_active=False, workflow_status="ready",
                    experiment_started=False, experiment_running=False)),
            ("no_pause_word", [_change("pause", "버퍼 넣었어")], _facts("버퍼 넣었어")),
            ("no_resume_word", [_change("resume", "제개")], _facts("제개", paused=True)),
            ("no_timer_word", [_change("start_timer", "시작해줘")], _facts("시작해줘", step_timer_seconds=900)),
            ("completion_not_reached", [_change("next", "다음 단계 가자")],
             _facts("탈색 아직 덜 됐는데 그냥 다음 단계 가자")),
            ("already_paused", [_change("pause", "잠깐")], _facts("잠깐", paused=True)),
            ("not_paused", [_change("resume", "계속하자")], _facts("계속하자")),
            ("no_step_timer", [_change("start_timer", "타이머 시작해줘")], _facts("타이머 시작해줘")),
            ("value_not_in_utterance", [_record("observation", "A-17", "메모 튜브 라벨 A-170")],
             _facts("메모 튜브 라벨 A-170")),
            # Decision 1 (2026-10-03): an observation needs a word of recording.
            ("no_record_word", [_record("observation", "튜브 라벨 A-170", "튜브 라벨 A-170")],
             _facts("튜브 라벨 A-170")),
        )
        seen = set()
        for code, proposals, facts in cases:
            with self.subTest(code=code, utterance=facts.utterance):
                verdict = self.rule(proposals, facts)
                self.assertEqual(verdict.effect, "refuse")
                self.assertEqual(verdict.reason_code, code)
                self.assertFalse(verdict.accepted)
                seen.add(code)
        self.assertEqual(seen, set(REFUSAL_REASONS))

    def test_next_only_ever_asks(self) -> None:
        verdict = self.rule([_change("next", "다 했어")], _facts("다 했어"))
        self.assertEqual((verdict.effect, verdict.question), ("ask", "completion"))
        verdict = self.rule(
            [_change("next", "완료했어")], _facts("완료했어", observation_step=True)
        )
        self.assertEqual((verdict.effect, verdict.question), ("ask", "observation"))
        verdict = self.rule([_change("next", "다음 거")], _facts("다음 거"))
        self.assertEqual((verdict.effect, verdict.question), ("ask", "completion"))

    def test_stop_asks_once_and_works_during_a_pause(self) -> None:
        for facts in (_facts("오늘 실험은 이걸로 종료할게요"),
                      _facts("오늘 실험은 이걸로 종료할게요", paused=True)):
            verdict = self.rule([_change("stop", "종료할게요")], facts)
            self.assertEqual((verdict.effect, verdict.question), ("ask", "stop"))

    def test_bounded_control_executes(self) -> None:
        self.assertEqual(self.rule([_change("pause", "잠깐 기다려 줘")], _facts("잠깐 기다려 줘")).effect, "execute")
        self.assertEqual(self.rule([_change("pause", "멈춰 줄래")], _facts("멈춰 줄래?")).effect, "execute")
        self.assertEqual(self.rule([_change("resume", "이어서 할게")], _facts("이어서 할게", paused=True)).effect, "execute")
        not_started = dict(workflow_active=False, workflow_status="ready",
                           experiment_started=False, experiment_running=False, current_step_label="1",
                           step_id="step-4")
        self.assertEqual(
            self.rule([_change("start", "프로토콜 시작해줘")], _facts("프로토콜 시작해줘", **not_started)).effect,
            "execute",
        )
        self.assertEqual(
            self.rule([_record("anomaly", "이상한 소리가 나", "이상한 소리가 나")],
                      _facts("원심분리기에서 이상한 소리가 나")).effect,
            "execute",
        )

    def test_a_timer_runs_the_source_duration_and_asks_about_any_other(self) -> None:
        timer = dict(step_timer_seconds=900)
        verdict = self.rule([_change("start_timer", "타이머 맞춰 줘")], _facts("타이머 맞춰 줘", **timer))
        self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "accepted"))
        verdict = self.rule([_change("start_timer", "15분 재줘")], _facts("15분 재줘", **timer))
        self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "accepted"))
        verdict = self.rule([_change("start_timer", "10분 재줘")], _facts("10분 재줘", **timer))
        self.assertEqual((verdict.effect, verdict.question, verdict.stated_duration_seconds),
                         ("ask", "timer", 600))
        verdict = self.rule([_change("start_timer", "타이머 시작해줘")],
                            _facts("타이머 시작해줘", timer_running=True, **timer))
        self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "timer_already_running"))


def _session(fixture, step_index: int | None = 3) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    if step_index is not None:
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = step_index
    return session


def _propose(session, turn_id: int, said: str, *proposals, quality=None):
    """front_plan() first, as the router will; a proposal only if it hands on."""

    front = session.front_plan(
        said, turn_id=turn_id, language="ko", transcript_quality=quality,
        configuration_id=1, generation=1,
    )
    if front is not None:
        return None, front
    applied = session.apply_tool_proposal(
        list(proposals), transcript=said,
        basis=session.proposal_basis(turn_id=turn_id, generation=1),
        turn_id=turn_id, language="ko", transcript_quality=quality,
        configuration_id=1, generation=1,
    )
    return applied.verdict, applied.plan


def _say(session, turn_id: int, said: str):
    return session.plan(said, turn_id=turn_id, language="ko", configuration_id=1, generation=1)


class ApplyToolProposalTests(unittest.TestCase):
    """Miniprep (no licensed PDF); step 3 is given a 15-minute source timer."""

    @classmethod
    def setUpClass(cls) -> None:
        plain = miniprep_fixture()
        cls.fixture = dataclasses.replace(plain, timer_manifest={plain.steps[2].step_id: 900})

    def test_next_opens_the_completion_question_and_only_yes_moves_on(self) -> None:
        session = _session(self.fixture)
        verdict, plan = _propose(session, 2, "자 이제 다음 거 하자", _change("next", "다음 거"))
        self.assertEqual((verdict.effect, verdict.question), ("ask", "completion"))
        self.assertIs(plan.action, CuratedProtocolAction.CLARIFY_COMPLETION)
        self.assertEqual(plan.speech_text, "4단계 완료하셨나요?")
        self.assertFalse(plan.state_changed)
        self.assertEqual(session.current_index, 3)
        self.assertIsNotNone(session.pending_completion_confirmation)
        moved = _say(session, 3, "네")
        self.assertTrue(moved.state_changed)
        self.assertEqual(session.current_index, 4)

    def test_not_done_yet_is_refused_with_the_completion_criterion_d7(self) -> None:
        session = _session(self.fixture)
        verdict, plan = _propose(
            session, 2, "아직 덜 됐는데 그냥 다음 단계 가자", _change("next", "다음 단계 가자")
        )
        self.assertEqual(verdict.reason_code, "completion_not_reached")
        self.assertIs(plan.action, CuratedProtocolAction.COMPLETION_CRITERIA)
        self.assertTrue(plan.speech_text.startswith("아직 끝나지 않았다고 하셔서 4단계에 머뭅니다."))
        self.assertIn("4 Elute the DNA", " ".join(plan.source_texts) or plan.display_text)
        self.assertIsNone(session.pending_completion_confirmation)
        self.assertEqual(session.current_index, 3)
        # Nothing was opened, so a yes now moves nothing.
        self.assertFalse(_say(session, 3, "네").state_changed)
        self.assertEqual(session.current_index, 3)

    def test_stop_needs_the_end_word_and_asks_once_d5(self) -> None:
        session = _session(self.fixture)
        verdict, plan = _propose(session, 2, "그만 끝내야겠다", _change("stop", "끝내야겠다"))
        self.assertEqual(verdict.reason_code, "end_word_missing")
        self.assertIn("실험을 끝내려면 '실험 종료'라고 말씀해 주세요.", plan.speech_text)
        self.assertTrue(session.active)
        verdict, plan = _propose(
            session, 3, "오늘 실험은 이걸로 종료할게요 수고했어", _change("stop", "종료할게요")
        )
        self.assertEqual((verdict.effect, verdict.question), ("ask", "stop"))
        self.assertEqual(plan.speech_text, "실험을 종료할까요?")
        self.assertFalse(plan.state_changed)
        self.assertTrue(session.active)
        ended = _say(session, 4, "네")
        self.assertIs(ended.action, CuratedProtocolAction.STOP)
        self.assertFalse(session.active)

    def test_pause_and_resume(self) -> None:
        session = _session(self.fixture)
        verdict, plan = _propose(session, 2, "잠깐 기다려 줄래", _change("pause", "잠깐 기다려 줄래"))
        self.assertEqual(verdict.effect, "execute")
        self.assertIs(plan.action, CuratedProtocolAction.PAUSE)
        self.assertEqual(session.workflow_status, "paused")
        verdict, _ = _propose(session, 3, "잠깐 기다려 줄래", _change("pause", "잠깐 기다려 줄래"))
        self.assertEqual(verdict.reason_code, "already_paused")
        verdict, _ = _propose(session, 4, "다 했어", _change("next", "다 했어"))
        self.assertEqual(verdict.reason_code, "workflow_paused")
        verdict, plan = _propose(session, 5, "이어서 할게", _change("resume", "이어서 할게"))
        self.assertEqual(verdict.effect, "execute")
        self.assertIs(plan.action, CuratedProtocolAction.RESUME)
        self.assertEqual(session.workflow_status, "active")
        verdict, _ = _propose(session, 6, "이어서 할게", _change("resume", "이어서 할게"))
        self.assertEqual(verdict.reason_code, "not_paused")

    def test_start_only_starts_a_protocol_that_never_started(self) -> None:
        session = _session(self.fixture, step_index=None)
        verdict, plan = _propose(session, 1, "실험 시작할게요 준비됐어", _change("start", "실험 시작할게요"))
        self.assertEqual(verdict.effect, "execute")
        self.assertTrue(plan.state_changed)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 0)
        session.current_index = 3
        verdict, _ = _propose(session, 2, "처음부터 다시 시작하자", _change("start", "다시 시작하자"))
        self.assertEqual(verdict.reason_code, "already_started")
        self.assertEqual(session.current_index, 3)
        _say(session, 3, "실험 종료")
        _say(session, 4, "네")
        self.assertFalse(session.active)
        # Decision 2 (2026-10-03): a start after the end is a front rule's
        # turn, and a proposal that still reaches the server is refused.
        verdict, _ = _propose(session, 5, "실험 다시 시작하자", _change("start", "실험 다시 시작하자"))
        self.assertIsNone(verdict)
        applied = session.apply_tool_proposal(
            [_change("start", "실험 다시 시작하자")], transcript="실험 다시 시작하자",
            basis=session.proposal_basis(turn_id=6, generation=1),
            turn_id=6, language="ko", configuration_id=1, generation=1,
        )
        verdict = applied.verdict
        self.assertEqual(verdict.reason_code, "session_ended")
        self.assertFalse(session.active)

    def test_the_timer_runs_the_source_duration_d6(self) -> None:
        session = _session(self.fixture, step_index=2)
        verdict, plan = _propose(session, 2, "타이머 좀 맞춰 주이소", _change("start_timer", "타이머 좀 맞춰 주이소"))
        self.assertEqual(verdict.effect, "execute")
        self.assertIs(plan.action, CuratedProtocolAction.START_TIMER)
        self.assertEqual(session.timer_status()["duration_seconds"], 900)

    def test_another_duration_asks_and_yes_starts_the_source_one_d6(self) -> None:
        session = _session(self.fixture, step_index=2)
        verdict, plan = _propose(session, 2, "10분 재줘", _change("start_timer", "10분 재줘"))
        self.assertEqual(verdict.reason_code, "duration_differs_from_source")
        self.assertEqual(plan.speech_text, "원문은 15분입니다. 15분으로 시작할까요?")
        self.assertEqual(session.timer_status()["state"], "not_started")
        started = _say(session, 3, "네")
        self.assertIs(started.action, CuratedProtocolAction.START_TIMER)
        self.assertEqual(session.last_front_rule, "yes_no_open_question")
        self.assertEqual(session.timer_status()["state"], "running")
        self.assertEqual(session.timer_status()["duration_seconds"], 900)

    def test_no_to_the_duration_question_starts_nothing(self) -> None:
        session = _session(self.fixture, step_index=2)
        _propose(session, 2, "10분 재줘", _change("start_timer", "10분 재줘"))
        declined = _say(session, 3, "아니")
        self.assertEqual(declined.speech_text, "알겠습니다. 타이머를 시작하지 않았습니다.")
        self.assertEqual(session.timer_status()["state"], "not_started")

    def test_the_duration_question_lapses_and_rolls_back(self) -> None:
        session = _session(self.fixture, step_index=2)
        checkpoint = session._checkpoint()
        _propose(session, 2, "10분 재줘", _change("start_timer", "10분 재줘"))
        self.assertTrue(session.awaiting_server_confirmation)
        session._restore(checkpoint)
        self.assertFalse(session.awaiting_server_confirmation)
        _propose(session, 2, "10분 재줘", _change("start_timer", "10분 재줘"))
        _say(session, 3, "버퍼 1은 뭐야?")
        self.assertFalse(session.awaiting_server_confirmation)
        self.assertFalse(_say(session, 4, "네").state_changed)
        self.assertEqual(session.timer_status()["state"], "not_started")

    def test_a_pause_holds_the_duration_question_d10(self) -> None:
        session = _session(self.fixture, step_index=2)
        _propose(session, 2, "10분 재줘", _change("start_timer", "10분 재줘"))
        self.assertIs(_say(session, 3, "잠깐").action, CuratedProtocolAction.PAUSE)
        resumed = _say(session, 4, "재개")
        self.assertTrue(resumed.speech_text.endswith("원문은 15분입니다. 15분으로 시작할까요?"))
        _say(session, 5, "네")
        self.assertEqual(session.timer_status()["state"], "running")

    def test_record_log_takes_only_the_researchers_own_words(self) -> None:
        session = _session(self.fixture)
        verdict, plan = _propose(
            session, 2, "메모 튜브 라벨 A-170", _record("observation", "튜브 라벨 A-170", "메모 튜브 라벨 A-170")
        )
        self.assertEqual(verdict.effect, "execute")
        self.assertIs(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertEqual(plan.observation_outcome, "튜브 라벨 A-170")
        self.assertFalse(plan.state_changed)
        verdict, plan = _propose(
            session, 3, "메모 튜브 라벨 A-170", _record("observation", "A-17", "메모 튜브 라벨 A-170")
        )
        self.assertEqual(verdict.reason_code, "value_not_in_utterance")
        self.assertFalse(plan.reported_observation)
        verdict, plan = _propose(
            session, 4, "원심분리기에서 이상한 소리가 나",
            _record("anomaly", "원심분리기에서 이상한 소리가 나", "이상한 소리가 나"),
        )
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertTrue(plan.reported_anomaly)

    def test_an_open_question_is_asked_again_not_lost(self) -> None:
        session = _session(self.fixture)
        _say(session, 2, "다음 단계")
        verdict, plan = _propose(session, 3, "버퍼 1은 뭐야 다음 단계", _change("next", "다음 단계"))
        self.assertEqual(verdict.reason_code, "pending_gate_owns_turn")
        self.assertEqual(plan.speech_text, "먼저 질문에 답해 주세요. 4단계 완료하셨나요?")
        self.assertEqual(session.current_index, 3)
        self.assertTrue(_say(session, 4, "네").state_changed)
        self.assertEqual(session.current_index, 4)

    def test_late_or_doubled_proposals_change_nothing(self) -> None:
        session = _session(self.fixture)
        basis = session.proposal_basis(turn_id=2, generation=1)
        _say(session, 2, "잠깐")
        late = session.apply_tool_proposal(
            [_change("resume", "재개")], transcript="재개", basis=basis, turn_id=2,
            language="ko", configuration_id=1, generation=1,
        )
        self.assertEqual(late.verdict.reason_code, "stale_proposal")
        self.assertEqual(session.workflow_status, "paused")
        old_basis = session.proposal_basis(turn_id=3, generation=1)
        _say(session, 3, "재개")
        stale = session.apply_tool_proposal(
            [_change("pause", "잠깐 멈춰")], transcript="잠깐 멈춰", basis=old_basis, turn_id=4,
            language="ko", configuration_id=1, generation=1,
        )
        self.assertEqual(stale.verdict.reason_code, "stale_proposal")
        self.assertEqual(session.workflow_status, "active")
        doubled = session.apply_tool_proposal(
            [_change("pause", "잠깐 멈춰"), _change("pause", "잠깐 멈춰")], transcript="잠깐 멈춰",
            basis=session.proposal_basis(turn_id=5, generation=1), turn_id=5,
            language="ko", configuration_id=1, generation=1,
        )
        self.assertEqual(doubled.verdict.reason_code, "more_than_one_proposal")
        self.assertEqual(session.workflow_status, "active")

    def test_every_reply_is_the_servers(self) -> None:
        session = _session(self.fixture)
        _, plan = _propose(session, 2, "다 했다고 치면 넘어가도 되겠다", _change("next", "다 했다고"))
        self.assertEqual(plan.intent_kind, "tool_proposal_refused")
        self.assertEqual(plan.limitations, ("refused:hypothetical_not_authorized",))
        self.assertEqual(plan.speech_text, "말씀만으로는 바꿀 내용을 확인하지 못해 상태를 바꾸지 않았습니다.")
        self.assertFalse(plan.state_changed)


#: What a worst-case model would do with every turn the front hands on.
_ADVERSARIAL_ACTIONS = ("next", "stop", "start")
_CORPUS = (
    "다 했어", "완료했어", "끝났어", "다음 단계", "넘어가", "다음 거", "next step 가자", "된 것 같아",
    "거의 다 했어", "아마 끝난 듯", "실험 끝내야겠다", "오늘은 이걸로 종료할게요", "종료하면 어떻게 돼",
    "실험 종료해도 돼?", "처음부터 다시 하자", "프로토콜 시작해줘", "다시 시작하자", "12단계로 가자",
    "버퍼 1은 뭐야?", "몇 도에서 해?", "자세히 알려줘", "이 단계 왜 해?", "어디까지 했지?",
    "탈색 아직 덜 됐는데 그냥 다음 단계 가자", "메모 튜브 라벨 A-170", "타이머 시작해줘", "10분 재줘",
    "이어서 할게", "재개", "잠깐", "네", "아니", "오늘 점심 뭐 먹지",
)


class AdversarialModelTests(unittest.TestCase):
    """The worst a model can propose -- next, stop or start, the whole turn as
    evidence -- on every turn the front hands on, at several states. Nothing
    irreversible may happen: no step moved, no session ended, no restart."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = miniprep_fixture()

    def _states(self):
        yield "step 4", _session(self.fixture), 2
        paused = _session(self.fixture)
        _say(paused, 2, "잠깐")
        yield "paused", paused, 3
        asked = _session(self.fixture)
        _say(asked, 2, "다음 단계")
        yield "completion question open", asked, 3
        ending = _session(self.fixture)
        _say(ending, 2, "실험 종료")
        yield "end question open", ending, 3

    def test_no_proposal_moves_ends_or_restarts(self) -> None:
        for name, session, turn_id in self._states():
            checkpoint = session._checkpoint()
            for said in _CORPUS:
                for action in _ADVERSARIAL_ACTIONS:
                    with self.subTest(state=name, said=said, action=action):
                        session._restore(checkpoint)
                        before = (session.active, session.current_index, session._experiment_ended_at)
                        verdict, plan = _propose(session, turn_id, said, _change(action, said))
                        if verdict is None:
                            continue  # a front rule took the turn; the model never saw it
                        after = (session.active, session.current_index, session._experiment_ended_at)
                        self.assertEqual(after, before)
                        self.assertFalse(plan.state_changed and plan.action in {
                            CuratedProtocolAction.NEXT, CuratedProtocolAction.STOP,
                            CuratedProtocolAction.START,
                        })


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelEndpointProposalTests(unittest.TestCase):
    """At in-gel step 7 a proposal can never move on without the observation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()
        cls.step_7 = next(i for i, s in enumerate(cls.fixture.steps) if s.source_label == "7")

    def test_next_opens_the_endpoint_question(self) -> None:
        session = _session(self.fixture, step_index=self.step_7)
        verdict, plan = _propose(session, 2, "다 했어", _change("next", "다 했어"))
        self.assertEqual((verdict.effect, verdict.question), ("ask", "observation"))
        self.assertIsNotNone(session.pending_observation_confirmation)
        self.assertEqual(session.current_index, self.step_7)

    def test_not_done_is_refused_with_the_stated_endpoint_d7(self) -> None:
        session = _session(self.fixture, step_index=self.step_7)
        verdict, plan = _propose(
            session, 2, "탈색 아직 덜 됐는데 그냥 다음 단계 가자", _change("next", "다음 단계 가자")
        )
        self.assertEqual(verdict.reason_code, "completion_not_reached")
        self.assertIn("관찰 결과를 말씀하기 전에는 이 단계를 완료 처리하지 마세요", plan.speech_text)
        self.assertIsNone(session.pending_observation_confirmation)
        self.assertEqual(session.current_index, self.step_7)

    def test_a_recorded_note_never_releases_the_endpoint(self) -> None:
        session = _session(self.fixture, step_index=self.step_7)
        verdict, plan = _propose(
            session, 2, "탈색이 됐는지 모르겠어 일단 메모해 줘",
            # Decision 1: the evidence carries the word of recording.
            _record("observation", "탈색이 됐는지 모르겠어", "탈색이 됐는지 모르겠어 일단 메모해 줘"),
        )
        self.assertEqual(plan.observation_predicate, "note")
        self.assertEqual(session.endpoint_observations(), {})
        _say(session, 3, "다음 단계")
        _say(session, 4, "네")
        self.assertEqual(session.current_index, self.step_7)


if __name__ == "__main__":
    unittest.main()
