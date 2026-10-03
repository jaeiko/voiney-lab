"""The router's checks, narrowed and widened after the lane R part 2-b evaluation (lane R3).

Decisions of 2026-10-03 that change only how the server rules on a model's
proposal or answer (the router is off by default; the rules' path does not
meet any of this):

1. A record is about the experiment only. A model's observation is taken only
   when its evidence asks for one in so many words -- "기록해 (줘)", "적어",
   "메모해", "남겨", "note this", "record this". The noun "기록" ("실험 기록
   보여줘", "기록 열어줘") is not a request. An anomaly in the problem words
   is not recorded when the turn asks for it to be handed to someone ("~에게
   전달해줘 / 보내줘 / 알려줘"): hand-off is not done by voice (D8), and the
   reply is "보고서는 화면에서 보내 주세요."
3. The server-value check also catches a protocol number said as something
   else ("프로토콜 번호는 25단계").
5. An answer may not imitate a question only the server asks ("…완료하셨나요?",
   "종료할까요?", "기록할까요?"): the rules answer instead.
6. An outside-PDF explanation may be about a word of the active protocol's
   text -- its steps, materials and warnings -- not only its term list. The
   rest of D4 is unchanged.
7. A Korean reading in the router's context that is a machine translation
   says so (``localization_source``).

Everything is offline (a fake model client).
"""

from __future__ import annotations

import asyncio
import dataclasses
import unittest

from tests.protocol_vocabulary_support import miniprep_fixture
from tests.router_fakes import FakeRouterClient, answer_reply, tool_reply
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.llm_router import (
    REFUSAL_REASONS,
    LlmRouterSettings,
    ProposalBasis,
    RouterAnswer,
    RouterTurnFacts,
    ToolProposal,
    answer_check_failures,
    route_turn_with_llm_router,
    validate_tool_proposals,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

ON = LlmRouterSettings(enabled=True, model="fake-router-model", timeout_seconds=1.0)
HANDOFF_REPLY = "보고서는 화면에서 보내 주세요."

#: (said, the value the model would record)
RECORD_REQUESTS = (
    ("기록해 줘 젤이 살짝 부풀었어", "젤이 살짝 부풀었어"),
    ("젤이 살짝 부풀었어 기록해", "젤이 살짝 부풀었어"),
    ("기록 좀 해줘 튜브 라벨 A-170", "튜브 라벨 A-170"),
    ("관찰 메모해줘 젤이 살짝 부풀었어", "젤이 살짝 부풀었어"),
    ("적어줘 버퍼 1 넣은 시간 3시 10분", "버퍼 1 넣은 시간 3시 10분"),
    ("남겨 줘 원심 끝난 시각", "원심 끝난 시각"),
    ("note this the pellet looks loose", "the pellet looks loose"),
    ("record this: pellet is loose", "pellet is loose"),
)
#: Not a request to write something down: the noun, or a look-alike.
NOT_RECORD_REQUESTS = (
    "실험 기록 보여줘",
    "지금까지 기록 열어줘",
    "관찰 기록 젤이 투명해졌어",
    "메모 튜브 라벨 A-170",
    "적어도 10분은 기다렸어",
    "남겨진 용액이 탁해",
    "the record shows a loose pellet",
)
#: Problem words, but asking for a hand-off.
HANDOFFS = (
    "안전관리자에게 이상사항 전달해줘",
    "교수님께 튜브가 터졌다고 알려줘",
    "튜브가 터졌어 관리자한테 보내줘",
    "send the anomaly to the safety officer",
)
SERVER_QUESTIONS = (
    "4단계 완료하지 않으셨나요? 확인해주세요.",
    "4단계를 완료하셨나요?",
    "실험을 종료할까요?",
    "종료하시겠어요?",
    "이상 사항으로 기록할까요?",
    "관찰이나 이상을 기록하시겠어요?",
    "다음 단계로 넘어갈까요?",
    "타이머를 시작할까요?",
    "Did you finish step 4?",
    "Shall I end the experiment?",
    "안전관리자에게 이상사항을 전달해 드릴까요?",
    "교수님께 보고서를 전송할까요?",
)
NOT_SERVER_QUESTIONS = (
    "완료 기준은 PDF에서 확인할 수 없어요.",
    "어떤 튜브를 말씀하시는 건가요?",
    "50 µL water로 용출합니다.",
)


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


def _record(log_type: str, value: str, evidence: str) -> ToolProposal:
    return ToolProposal(tool="record_log", log_type=log_type, value=value, evidence=evidence)


def _session(fixture=None) -> CuratedProtocolSession:
    session = CuratedProtocolSession(fixture or miniprep_fixture())
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = 3
    return session


def _route(session, said, client, *, turn_id=2):
    async def rules():
        return route_curated_runtime_turn(
            session, said, turn_id=turn_id, language="ko", configuration_id=1, generation=1,
        )

    return asyncio.run(route_turn_with_llm_router(
        session, said, turn_id=turn_id, language="ko", settings=ON,
        client_factory=lambda: client, rule_route=rules,
        configuration_id=1, generation=1,
    ))


def _answer(spoken, *, kind="none", ids=(), term=None) -> RouterAnswer:
    return RouterAnswer(spoken, "", kind, tuple(ids), term)


class RecordRequestTests(unittest.TestCase):
    """Decision 1: an observation only on a request to write it down."""

    def test_a_request_to_write_it_down_is_taken(self) -> None:
        for said, value in RECORD_REQUESTS:
            with self.subTest(said=said):
                verdict = validate_tool_proposals(
                    [_record("observation", value, said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "accepted"))

    def test_the_noun_and_look_alikes_are_not_requests(self) -> None:
        for said in NOT_RECORD_REQUESTS:
            with self.subTest(said=said):
                verdict = validate_tool_proposals(
                    [_record("observation", said, said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.reason_code), ("refuse", "no_record_word"))

    def test_showing_the_record_records_nothing(self) -> None:
        session = _session()
        said = "실험 기록 보여줘"
        outcome = _route(session, said, FakeRouterClient(tool_reply(
            ("record_log", {"type": "observation", "value": said, "evidence": said}),
        )))
        self.assertEqual(outcome.fallback_reason, "refused:no_record_word")
        self.assertFalse(outcome.plan.reported_observation)
        self.assertEqual(session.endpoint_observations(), {})


class HandoffTests(unittest.TestCase):
    """Decision 1: a hand-off is not recorded, and is sent from the screen."""

    def test_a_handoff_is_not_recorded(self) -> None:
        self.assertIn("handoff_not_by_voice", REFUSAL_REASONS)
        for said in HANDOFFS:
            with self.subTest(said=said):
                verdict = validate_tool_proposals(
                    [_record("anomaly", said, said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.reason_code),
                                 ("refuse", "handoff_not_by_voice"))

    def test_a_problem_without_a_handoff_is_still_recorded(self) -> None:
        for said in ("튜브가 터졌어", "시료를 흘렸어", "원심분리기에서 이상한 소리가 나"):
            with self.subTest(said=said):
                verdict = validate_tool_proposals(
                    [_record("anomaly", said, said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "accepted"))

    def test_the_reply_says_to_send_it_from_the_screen(self) -> None:
        session = _session()
        said = "안전관리자에게 이상사항 전달해줘"
        outcome = _route(session, said, FakeRouterClient(tool_reply(
            ("record_log", {"type": "anomaly", "value": said, "evidence": said}),
        )))
        self.assertEqual(outcome.verdict.reason_code, "handoff_not_by_voice")
        self.assertEqual(outcome.plan.speech_text, HANDOFF_REPLY)
        self.assertEqual(outcome.plan.display_text, HANDOFF_REPLY)
        self.assertFalse(outcome.plan.state_changed)
        self.assertFalse(outcome.plan.reported_anomaly)
        # No e-mail question is left open, and nothing waits for a yes.
        self.assertIsNone(session._pending_handoff_confirmation)
        self.assertFalse(session.awaiting_server_confirmation)
        self.assertNotIn("@", outcome.plan.speech_text)


    def test_a_dropped_answer_to_a_handoff_is_not_an_email_question(self) -> None:
        session = _session()
        said = "안전관리자에게 이상사항 전달해줘"
        outcome = _route(session, said, FakeRouterClient(answer_reply(
            "안전관리자에게 이상사항을 전달해 드릴까요?", source_kind="none",
        )))
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertIs(outcome.rule_route.plan.action, CuratedProtocolAction.REPORT_HANDOFF)
        self.assertEqual(outcome.plan.speech_text, HANDOFF_REPLY)
        self.assertIsNone(session._pending_handoff_confirmation)

    def test_an_answer_promising_a_handoff_is_dropped(self) -> None:
        context = _session().router_context(turn_id=2, language="ko")
        for spoken in ("교수님께 보고서를 보내드릴게요.", "안전관리자에게 전달하겠습니다."):
            with self.subTest(spoken):
                self.assertIn("claims_state_change", answer_check_failures(
                    _answer(spoken), context, utterance="교수님께 보고서 보내줘"))
        self.assertEqual(answer_check_failures(
            _answer("보고서는 화면에서 보내 주세요."), context, utterance="교수님께 보고서 보내줘"), ())

    def test_the_rules_alone_keep_their_handoff_reply(self) -> None:
        # Router off: the rules' path is not changed by lane R3.
        session = _session()
        plan = route_curated_runtime_turn(
            session, "안전관리자에게 이상사항 전달해줘", turn_id=2, language="ko",
            configuration_id=1, generation=1,
        ).plan
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_HANDOFF)
        self.assertNotEqual(plan.speech_text, HANDOFF_REPLY)


class ServerValueCheckTests(unittest.TestCase):
    """Decision 3: the protocol number said as anything but the server's."""

    def setUp(self) -> None:
        self.session = _session()
        self.context = self.session.router_context(turn_id=2, language="ko")

    def test_a_number_that_is_not_the_servers_is_caught(self) -> None:
        for spoken in ("프로토콜 번호는 25단계, 현재 4단계입니다.", "프로토콜 번호는 3번이에요.",
                       "The protocol number is 25."):
            with self.subTest(spoken):
                self.assertIn("server_value", answer_check_failures(
                    _answer(spoken, kind="server_state"), self.context, utterance="프로토콜 번호 알려줘"))

    def test_the_servers_own_number_passes(self) -> None:
        revision = self.session.fixture.revision_id
        self.assertNotIn("server_value", answer_check_failures(
            _answer(f"프로토콜 번호는 {revision}입니다.", kind="server_state"),
            self.context, utterance="프로토콜 번호 알려줘"))


class ServerQuestionImitationTests(unittest.TestCase):
    """Decision 5: an answer may not ask what only the server asks."""

    def setUp(self) -> None:
        self.context = _session().router_context(turn_id=2, language="ko")

    def test_a_server_question_in_an_answer_is_caught(self) -> None:
        for spoken in SERVER_QUESTIONS:
            with self.subTest(spoken):
                self.assertIn("server_question", answer_check_failures(
                    _answer(spoken), self.context, utterance="잠깐 아니야"))

    def test_other_answers_and_questions_pass(self) -> None:
        for spoken in NOT_SERVER_QUESTIONS:
            with self.subTest(spoken):
                kind, ids = ("pdf", ("S4.current_step",)) if "µL" in spoken else ("none", ())
                self.assertNotIn("server_question", answer_check_failures(
                    _answer(spoken, kind=kind, ids=ids), self.context, utterance="질문"))

    def test_the_rules_answer_instead(self) -> None:
        session = _session()
        outcome = _route(session, "잠깐 아니야", FakeRouterClient(answer_reply(
            "4단계 완료하지 않으셨나요? 확인해주세요.", source_kind="none",
        )))
        self.assertEqual(outcome.handled_by, "fallback_rules")
        self.assertIn("server_question", outcome.fallback_reason)
        self.assertNotIn("완료하지 않으셨나요", outcome.plan.speech_text)
        self.assertFalse(session.awaiting_server_confirmation)


class OutsidePdfTermTests(unittest.TestCase):
    """Decision 6: a word of the active protocol's text, not only its terms."""

    def setUp(self) -> None:
        self.context = _session().router_context(turn_id=2, language="ko")

    def _failures(self, term: str, said: str) -> tuple[str, ...]:
        return answer_check_failures(
            _answer("세척에 쓰는 정제용 칼럼이에요.", kind="outside_pdf", term=term),
            self.context, utterance=said,
        )

    def test_a_word_of_the_steps_may_be_explained(self) -> None:
        self.assertNotIn("column", [term.casefold() for term in self.context.terms])
        self.assertEqual(self._failures("column", "column이 뭐야?"), ())
        self.assertEqual(self._failures("pellet", "pellet이 뭐야?"), ())

    def test_a_word_of_another_protocol_still_may_not(self) -> None:
        failures = self._failures("trypsin", "trypsin이 뭐야?")
        self.assertTrue(any("term_not_in_active_protocol" in item for item in failures), failures)
        # Part of a word is not the word.
        failures = self._failures("col", "col이 뭐야?")
        self.assertTrue(any("term_not_in_active_protocol" in item for item in failures), failures)

    def test_the_rest_of_d4_is_unchanged(self) -> None:
        failures = self._failures("column", "column은 얼마나 써?")
        self.assertTrue(any("question_asks_quantity" in item for item in failures), failures)


class MachineReadingMarkTests(unittest.TestCase):
    """Decision 7: a machine-translated Korean reading says so."""

    def test_a_machine_reading_carries_its_source(self) -> None:
        fixture = dataclasses.replace(
            miniprep_fixture(),
            localizations={"step-3/current_step": "칼럼을 500 µL ethanol로 세척합니다."},
            machine_localizations={"step-4/current_step": "DNA를 50 µL 물로 용출합니다."},
        )
        context = _session(fixture).router_context(turn_id=2, language="ko")
        facts = {
            fact["id"]: fact
            for step in context.protocol["steps_near_current"] for fact in step["facts"]
        }
        self.assertEqual(facts["S4.current_step"]["ko"], "DNA를 50 µL 물로 용출합니다.")
        self.assertEqual(facts["S4.current_step"]["localization_source"], "machine")
        self.assertEqual(facts["S3.current_step"]["ko"], "칼럼을 500 µL ethanol로 세척합니다.")
        self.assertNotIn("localization_source", facts["S3.current_step"])
        self.assertNotIn("localization_source", facts["S5.current_step"])


if __name__ == "__main__":
    unittest.main()
