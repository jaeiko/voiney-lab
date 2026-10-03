"""Server values, step lookups and "1단계부터 해 볼까" are the front rules' (lane R3).

Decisions of 2026-10-03, after the lane R part 2-b evaluation read the model's
answers:

3. A question about a value only the server holds -- the protocol's number
   (its revision), hash, version, title, its total step count and the
   current step -- is answered by the rules' protocol query, as a front
   rule. The model said "프로토콜 번호는 25단계" for the number and "해시는
   PDF에 없어요" for the hash.
4. A lookup that points at a step -- "21단계는 뭐 해?", "마지막 단계 뭐야",
   "다음 단계는 뭐야?" -- reads the source text as it is, so the rules'
   preview, next-step and full-detail answers are front rules too. The
   model answered "다음 단계는 뭐야?" with the current step.
8. Before the experiment has started, "1단계부터 해 볼까 / 하자 / 시작하자"
   starts it (R004 "자 이제 1단계부터 해볼까" was missed by every run).

Everything here is the rules' own path, offline.
"""

from __future__ import annotations

import unittest

from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab.curated_protocol import (
    FRONT_RULES,
    CuratedProtocolAction,
    CuratedProtocolSession,
)
from voiney_lab.llm_router import (
    ProposalBasis,
    RouterTurnFacts,
    ToolProposal,
    validate_tool_proposals,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

#: (said, the protocol query scope the rules answer with)
SERVER_VALUE_QUESTIONS = (
    ("프로토콜 번호와 해시를 알려줘", "version"),
    ("프로토콜 번호 뭐야", "version"),
    ("이 프로토콜 제목이 뭐야", "version"),
    ("프로토콜 이름 알려줘", "version"),
    ("프로토콜 해시 알려줘", "version"),
    ("실행 버전 알려줘", "version"),
    ("what is the protocol revision", "version"),
    ("총 몇 단계야", "total_steps"),
    ("전체 단계 수 알려줘", "total_steps"),
    ("지금 몇 단계야", "current_position"),
)
#: Protocol questions that are not about a server value stay with the model.
NOT_SERVER_VALUES = ("이 프로토콜 목적이 뭐야", "전체 재료 목록 알려줘")
#: (said, the action the rules answer with, the step it reads)
STEP_LOOKUPS = (
    ("21단계는 뭐 해?", CuratedProtocolAction.FULL_DETAIL, "21"),
    ("3단계 뭐 하는 거야", CuratedProtocolAction.FULL_DETAIL, "3"),
    ("마지막 단계 뭐야", CuratedProtocolAction.FULL_DETAIL, "LAST"),
    ("첫 단계는 뭐야?", CuratedProtocolAction.FULL_DETAIL, "1"),
    ("12단계 미리 알려줘", CuratedProtocolAction.PREVIEW_STEP, "12"),
    ("다음 단계는 뭐야?", CuratedProtocolAction.NEXT_INFORMATION, "NEXT"),
    ("다음엔 뭐 해?", CuratedProtocolAction.NEXT_INFORMATION, "NEXT"),
    ("what's next?", CuratedProtocolAction.NEXT_INFORMATION, "NEXT"),
    ("이 단계 전체 내용 읽어줘", CuratedProtocolAction.FULL_DETAIL, "CURRENT"),
    ("상세 내용을 읽어줘", CuratedProtocolAction.FULL_DETAIL, "CURRENT"),
)
#: Near sentences that are not a step lookup keep their reading.
NOT_LOOKUPS = (
    ("마지막 단계 완료했어", CuratedProtocolAction.FULL_DETAIL),
    ("12단계로 가자", CuratedProtocolAction.FULL_DETAIL),
    ("다음", CuratedProtocolAction.NEXT_INFORMATION),
)
FIRST_STEP_STARTS = (
    "자 이제 1단계부터 해볼까",
    "1단계부터 해 볼까",
    "1단계부터 하자",
    "이제 1단계부터 시작하자",
    "그럼 첫 단계부터 해보자",
)


def _session(step_index: int | None = 3) -> CuratedProtocolSession:
    from tests.protocol_vocabulary_support import in_gel_fixture, SOURCE_PDF

    fixture = in_gel_fixture() if SOURCE_PDF.is_file() else miniprep_fixture()
    session = CuratedProtocolSession(fixture)
    session.activate_configured()
    if step_index is not None:
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = min(step_index, len(fixture.steps) - 1)
    return session


def _front(session, said, turn_id=2):
    return session.front_plan(said, turn_id=turn_id, language="ko", configuration_id=1, generation=1)


def _rules(session, said, turn_id=2):
    return route_curated_runtime_turn(
        session, said, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
    ).plan


class ServerValueFrontTests(unittest.TestCase):
    def test_server_value_questions_are_a_front_rule(self) -> None:
        self.assertIn("server_value_query", FRONT_RULES)
        for said, scope in SERVER_VALUE_QUESTIONS:
            with self.subTest(said=said):
                session = _session()
                plan = _front(session, said)
                self.assertIsNotNone(plan, "handed on to the model")
                self.assertIs(plan.action, CuratedProtocolAction.PROTOCOL_QUERY)
                self.assertEqual(session.last_front_rule, "server_value_query")
                self.assertFalse(plan.state_changed)

    def test_the_number_question_says_the_server_values(self) -> None:
        session = _session()
        plan = _rules(session, "프로토콜 번호와 해시를 알려줘")
        self.assertIs(plan.action, CuratedProtocolAction.PROTOCOL_QUERY)
        self.assertIn(session.fixture.revision_id, plan.speech_text)
        self.assertIn(session.fixture.title, plan.speech_text)
        self.assertIn(session.fixture.fixture_sha256, plan.display_text)

    def test_other_protocol_questions_stay_with_the_model(self) -> None:
        for said in NOT_SERVER_VALUES:
            with self.subTest(said=said):
                self.assertIsNone(_front(_session(), said))


class StepLookupFrontTests(unittest.TestCase):
    def test_a_lookup_that_points_at_a_step_is_a_front_rule(self) -> None:
        self.assertIn("step_lookup", FRONT_RULES)
        for said, action, target in STEP_LOOKUPS:
            with self.subTest(said=said):
                session = _session(4)
                steps = session.fixture.steps
                if target == "21" and len(steps) < 21:
                    continue
                before = (session.current_index, session.workflow_status)
                plan = _front(session, said)
                self.assertIsNotNone(plan, "handed on to the model")
                self.assertIs(plan.action, action)
                self.assertEqual(session.last_front_rule, "step_lookup")
                self.assertFalse(plan.state_changed)
                self.assertEqual((session.current_index, session.workflow_status), before)
                expected = {
                    "LAST": steps[-1], "NEXT": steps[5], "CURRENT": steps[4],
                }.get(target) or steps[int(target) - 1]
                self.assertIn(
                    " ".join(expected.instruction_source_text.split())[:20],
                    " ".join(" ".join(plan.source_texts).split())
                    + " ".join(plan.display_text.split()),
                )

    def test_near_sentences_keep_their_reading(self) -> None:
        for said, not_this in NOT_LOOKUPS:
            with self.subTest(said=said):
                session = _session(4)
                plan = _rules(session, said)
                self.assertIsNot(plan.action, not_this)
                self.assertNotEqual(session.last_front_rule, "step_lookup")


class FirstStepStartTests(unittest.TestCase):
    def test_before_the_start_it_starts_the_experiment(self) -> None:
        for said in FIRST_STEP_STARTS:
            with self.subTest(said=said):
                session = _session(None)
                plan = _front(session, said, turn_id=1)
                self.assertIsNotNone(plan, "handed on to the model")
                self.assertIs(plan.action, CuratedProtocolAction.START)
                self.assertEqual(session.last_front_rule, "start_command")
                self.assertTrue(plan.state_changed)
                self.assertEqual((session.active, session.current_index), (True, 0))

    def test_once_running_it_starts_nothing_again(self) -> None:
        for said in FIRST_STEP_STARTS:
            with self.subTest(said=said):
                session = _session(6)
                plan = _rules(session, said)
                self.assertFalse(plan.state_changed)
                self.assertEqual((session.active, session.current_index), (True, 6))
                self.assertNotEqual(session.last_front_rule, "start_command")

    def test_a_model_start_with_these_words_is_accepted(self) -> None:
        said = "자 이제 1단계부터 해볼까"
        facts = RouterTurnFacts(
            utterance=said, language="ko", turn_id=1, generation=1, workflow_revision=0,
            step_id=None, current_step_label=None, workflow_active=False,
            workflow_status="preview", paused=False, experiment_started=False,
            experiment_running=False, open_question=None, observation_step=False,
            step_timer_seconds=0, timer_running=False, control_question=False,
            transcript_unreliable=False,
        )
        verdict = validate_tool_proposals(
            [ToolProposal(tool="change_state", action="start", evidence="1단계부터 해볼까")],
            facts, ProposalBasis(turn_id=1, generation=1, workflow_revision=0, step_id=None),
        )
        self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "accepted"))


if __name__ == "__main__":
    unittest.main()
