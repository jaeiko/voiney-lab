"""What a model's ``record_log`` proposal may write down (decision 1, 2026-10-03).

The lane R part 2-a adversary showed that a proposal recording the whole
utterance was taken for almost anything the researcher said ("자세히
알려줘", "다 했어", ...), 79 rows as observations and 79 as anomalies, into
an experiment record that can only be added to. The people running the
pilot narrowed it:

* An observation is recorded only when its evidence carries a word of
  recording: 메모, 기록, 관찰, 적어, note, record, ... (A reply while the
  observation question is open is the front rules' turn, F5, and never
  reaches a proposal.)
* An anomaly is recorded when the words read as a problem by the rules' own
  tables (the endpoint question's problem words and the anomaly reader's).
  Otherwise the server asks once, "이상 사항으로 기록할까요?": yes records
  the words the question was about, no records nothing, anything else lets
  the question go. A pause holds it and the voice resume asks it again.

Everything is offline; only apply_tool_proposal opens the question, so the
rules alone never meet it.
"""

from __future__ import annotations

import unittest

from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab.curated_protocol import CuratedProtocolAction, CuratedProtocolSession
from voiney_lab.llm_router import (
    ProposalBasis,
    RouterTurnFacts,
    ToolProposal,
    reports_a_problem,
    validate_tool_proposals,
)

ASK = "이상 사항으로 기록할까요?"
RECORD_WORDS = (
    ("메모해 줘 튜브 라벨 A-170", "튜브 라벨 A-170"),
    # Lane R3 (decision 1 narrowed): a request to record, not the noun.
    ("관찰 기록해 줘 젤이 투명해졌어", "젤이 투명해졌어"),
    ("적어줘 버퍼 1 넣은 시간 3시 10분", "버퍼 1 넣은 시간 3시 10분"),
    ("note the pellet looks loose", "the pellet looks loose"),
)
NOT_RECORD_WORDS = (
    "자세히 알려줘", "다 했어", "튜브 라벨 A-170", "젤이 투명해졌어",
    "관찰 기록 젤이 투명해졌어",
)
PROBLEMS = ("튜브가 터졌어", "원심분리기에서 이상한 소리가 나", "시료를 흘렸어", "예상과 달라")
NOT_PROBLEMS = ("튜브 뚜껑이 좀 헐거워", "젤 조각이 바닥에 붙었어", "다 했어", "자세히 알려줘")


def _record(log_type: str, value: str, evidence: str | None = None) -> ToolProposal:
    return ToolProposal(tool="record_log", log_type=log_type, value=value, evidence=evidence or value)


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


class RecordLogRulingTests(unittest.TestCase):
    def test_an_observation_needs_a_word_of_recording(self) -> None:
        for said, value in RECORD_WORDS:
            with self.subTest(said=said):
                verdict = validate_tool_proposals([_record("observation", value, said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "accepted"))
        for said in NOT_RECORD_WORDS:
            with self.subTest(said=said):
                verdict = validate_tool_proposals([_record("observation", said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.reason_code), ("refuse", "no_record_word"))

    def test_the_word_must_be_in_the_evidence_itself(self) -> None:
        said = "튜브 라벨 A-170 메모해 줘"
        verdict = validate_tool_proposals(
            [_record("observation", "튜브 라벨 A-170", "튜브 라벨 A-170")], _facts(said), _BASIS,
        )
        self.assertEqual(verdict.reason_code, "no_record_word")

    def test_a_problem_is_recorded_and_anything_else_is_asked_about(self) -> None:
        for said in PROBLEMS:
            with self.subTest(said=said):
                self.assertTrue(reports_a_problem(said))
                verdict = validate_tool_proposals([_record("anomaly", said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "accepted"))
        for said in NOT_PROBLEMS:
            with self.subTest(said=said):
                self.assertFalse(reports_a_problem(said))
                verdict = validate_tool_proposals([_record("anomaly", said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.question), ("ask", "anomaly"))
                self.assertTrue(verdict.accepted)

    def test_the_old_fences_still_come_first(self) -> None:
        said = "튜브가 터졌어"
        self.assertEqual(
            validate_tool_proposals([_record("anomaly", said)], _facts(said, paused=True), _BASIS).reason_code,
            "workflow_paused",
        )
        self.assertEqual(
            validate_tool_proposals([_record("anomaly", "튜브가 깨졌어", said)], _facts(said), _BASIS).reason_code,
            "value_not_in_utterance",
        )
        self.assertEqual(
            validate_tool_proposals(
                [_record("observation", "A-170", "메모 A-170")],
                _facts("메모 A-170", open_question="completion"), _BASIS,
            ).reason_code,
            "pending_gate_owns_turn",
        )


def _session() -> CuratedProtocolSession:
    session = CuratedProtocolSession(miniprep_fixture())
    session.activate_configured()
    session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
    session.current_index = 3
    return session


def _propose(session: CuratedProtocolSession, turn_id: int, said: str, proposal: ToolProposal):
    applied = session.apply_tool_proposal(
        [proposal], transcript=said,
        basis=session.proposal_basis(turn_id=turn_id, generation=1),
        turn_id=turn_id, language="ko", configuration_id=1, generation=1,
    )
    return applied.verdict, applied.plan


def _say(session: CuratedProtocolSession, turn_id: int, said: str):
    return session.plan(said, turn_id=turn_id, language="ko", configuration_id=1, generation=1)


class AnomalyQuestionTests(unittest.TestCase):
    SAID = "튜브 뚜껑이 좀 헐거워"

    def _asked(self) -> CuratedProtocolSession:
        session = _session()
        verdict, plan = _propose(session, 2, self.SAID, _record("anomaly", self.SAID))
        self.assertEqual(verdict.question, "anomaly")
        self.assertEqual(plan.speech_text, ASK)
        self.assertFalse(plan.reported_anomaly)
        self.assertIsNot(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertFalse(plan.state_changed)
        self.assertTrue(session.awaiting_server_confirmation)
        return session

    def test_yes_records_the_words_asked_about(self) -> None:
        session = self._asked()
        recorded = _say(session, 3, "네")
        self.assertIs(recorded.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertTrue(recorded.reported_anomaly)
        self.assertEqual(recorded.anomaly_text, self.SAID)
        self.assertEqual(session.last_front_rule, "yes_no_open_question")
        self.assertFalse(session.awaiting_server_confirmation and session._pending_anomaly_confirmation)
        self.assertEqual(session.current_index, 3)

    def test_yes_is_a_front_rule_turn(self) -> None:
        session = self._asked()
        front = session.front_plan("응", turn_id=3, language="ko", configuration_id=1, generation=1)
        self.assertIsNotNone(front)
        self.assertTrue(front.reported_anomaly)
        self.assertEqual(front.anomaly_text, self.SAID)

    def test_no_records_nothing(self) -> None:
        session = self._asked()
        declined = _say(session, 3, "아니")
        self.assertEqual(declined.speech_text, "알겠습니다. 이상 사항으로 기록하지 않았습니다.")
        self.assertFalse(declined.reported_anomaly)
        self.assertIsNot(declined.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertIsNone(session._pending_anomaly_confirmation)

    def test_anything_else_lets_the_question_go(self) -> None:
        session = self._asked()
        _say(session, 3, "버퍼 1은 뭐야?")
        self.assertIsNone(session._pending_anomaly_confirmation)
        later = _say(session, 4, "네")
        self.assertFalse(later.reported_anomaly)

    def test_an_unclear_yes_is_not_taken(self) -> None:
        session = self._asked()
        plan = session.plan(
            "네", turn_id=3, language="ko", configuration_id=1, generation=1,
            transcript_quality="low_confidence",
        )
        self.assertFalse(plan.reported_anomaly)

    def test_a_pause_holds_the_question_and_the_resume_asks_it_again(self) -> None:
        session = self._asked()
        self.assertIs(_say(session, 3, "잠깐").action, CuratedProtocolAction.PAUSE)
        resumed = _say(session, 4, "재개")
        self.assertTrue(resumed.speech_text.endswith(ASK))
        recorded = _say(session, 5, "네")
        self.assertTrue(recorded.reported_anomaly)
        self.assertEqual(recorded.anomaly_text, self.SAID)

    def test_the_question_rolls_back_with_the_turn(self) -> None:
        session = _session()
        checkpoint = session._checkpoint()
        _propose(session, 2, self.SAID, _record("anomaly", self.SAID))
        self.assertIsNotNone(session._pending_anomaly_confirmation)
        session._restore(checkpoint)
        self.assertIsNone(session._pending_anomaly_confirmation)
        asked = self._asked()
        kept = asked._checkpoint()
        asked._pending_anomaly_confirmation = None
        asked._restore(kept)
        self.assertEqual(asked._pending_anomaly_confirmation["utterance"], self.SAID)

    def test_a_problem_is_recorded_at_once(self) -> None:
        session = _session()
        verdict, plan = _propose(session, 2, "튜브가 터졌어", _record("anomaly", "튜브가 터졌어"))
        self.assertEqual(verdict.effect, "execute")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        self.assertTrue(plan.reported_anomaly)
        self.assertIsNone(session._pending_anomaly_confirmation)

    def test_an_observation_without_the_word_records_nothing(self) -> None:
        session = _session()
        verdict, plan = _propose(session, 2, "자세히 알려줘", _record("observation", "자세히 알려줘"))
        self.assertEqual(verdict.reason_code, "no_record_word")
        self.assertFalse(plan.reported_observation)
        self.assertEqual(session.endpoint_observations(), {})


if __name__ == "__main__":
    unittest.main()
