"""A record keeps the researcher's negation (lane TS, decision 4).

Human decision of 2026-10-08: the value ``record_log`` stores must, when the
utterance holds a negation (안, 않, 없, 못, no, not, without, ...), hold the
whole clause that negation stands in; otherwise the whole utterance is stored.
Before it, any substring of the utterance was taken (``tools.py``
``_observation_matches_transcript``), so "침전물 안 보임 남겨 줘" could be
recorded as "침전물" -- the opposite of what was seen, in a record that can
only be added to.

The front rules' note (lane N, "말 그대로") already keeps the negation; the
last class pins that.
"""

from __future__ import annotations

import unittest

from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    note_request,
)
from voiney_lab.llm_router import (
    ProposalBasis,
    RouterTurnFacts,
    ToolProposal,
    validate_tool_proposals,
)
from voiney_lab.tools import record_value_keeping_negation

#: (utterance, the value a model might pick that drops the negation)
DROPPED = (
    ("침전물 안 보임 남겨 줘", "침전물"),
    ("침전물 안 보임 남겨 줘", "보임"),
    ("밴드가 보이지 않는다고 적어 줘", "밴드가 보이"),
    ("기포 없음 기록해 줘", "기포"),
    ("펠릿이 잘 안 풀림 남겨 줘", "펠릿이 잘"),
    ("색 변화 못 봤어 적어 줘", "색 변화"),
    ("pH 7.2, 침전물 안 보임 남겨 줘", "pH 7.2"),
    ("no precipitate, note this", "precipitate"),
    ("the pellet did not dissolve, note this", "the pellet"),
    ("record that the band was not visible", "the band was"),
)
#: (utterance, a value that keeps every negated clause whole)
KEPT = (
    ("침전물 안 보임 남겨 줘", "침전물 안 보임"),
    ("밴드가 보이지 않는다고 적어 줘", "밴드가 보이지 않는다"),
    ("밴드가 보이지 않는다고 적어 줘", "밴드가 보이지 않는다고"),
    ("기포 없음 기록해 줘", "기포 없음"),
    ("no precipitate, note this", "no precipitate"),
    ("pH 7.2, 침전물 안 보임 남겨 줘", "pH 7.2, 침전물 안 보임"),
)
#: No negation in the words: the value stays what it was.
PLAIN = (
    ("튜브 라벨 A-170 남겨 줘", "튜브 라벨 A-170"),
    ("젤이 투명해졌어 적어 줘", "젤이 투명해졌어"),
    ("튜브 안에 침전물 있음 남겨 줘", "침전물 있음"),
)


def _record(value: str, evidence: str) -> ToolProposal:
    return ToolProposal(tool="record_log", log_type="observation", value=value, evidence=evidence)


def _facts(utterance: str) -> RouterTurnFacts:
    return RouterTurnFacts(
        utterance=utterance, language="ko", turn_id=5, generation=1,
        workflow_revision=3, step_id="step-4", current_step_label="4",
        workflow_active=True, workflow_status="active", paused=False,
        experiment_started=True, experiment_running=True, open_question=None,
        observation_step=False, step_timer_seconds=0, timer_running=False,
        control_question=False, transcript_unreliable=False,
    )


_BASIS = ProposalBasis(turn_id=5, generation=1, workflow_revision=3, step_id="step-4")


class RecordValueTests(unittest.TestCase):
    def test_a_value_that_drops_a_negation_becomes_the_whole_utterance(self) -> None:
        for said, value in DROPPED:
            with self.subTest(said=said, value=value):
                self.assertEqual(record_value_keeping_negation(value, said), said)

    def test_a_value_that_keeps_the_negated_clause_is_kept(self) -> None:
        for said, value in KEPT:
            with self.subTest(said=said, value=value):
                self.assertEqual(record_value_keeping_negation(value, said), value)

    def test_words_with_no_negation_are_left_alone(self) -> None:
        for said, value in PLAIN:
            with self.subTest(said=said, value=value):
                self.assertEqual(record_value_keeping_negation(value, said), value)


class RouterRulingTests(unittest.TestCase):
    def test_the_accepted_proposal_carries_the_whole_utterance(self) -> None:
        for said, value in DROPPED:
            with self.subTest(said=said, value=value):
                verdict = validate_tool_proposals([_record(value, said)], _facts(said), _BASIS)
                self.assertEqual((verdict.effect, verdict.reason_code), ("execute", "accepted"))
                self.assertEqual(verdict.proposal.value, said)

    def test_a_proposal_that_keeps_the_clause_is_unchanged(self) -> None:
        for said, value in KEPT:
            with self.subTest(said=said, value=value):
                verdict = validate_tool_proposals([_record(value, said)], _facts(said), _BASIS)
                self.assertEqual(verdict.proposal.value, value)


class StoredNoteTests(unittest.TestCase):
    """What the session records from an accepted proposal (miniprep, no PDF)."""

    def _session(self) -> CuratedProtocolSession:
        session = CuratedProtocolSession(miniprep_fixture())
        session.activate_configured()
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        session.current_index = 3
        return session

    def test_the_record_holds_the_negation(self) -> None:
        session = self._session()
        said = "침전물 안 보임 남겨 줘"
        self.assertIsNone(session.front_plan(
            said, turn_id=2, language="ko", configuration_id=1, generation=1))
        applied = session.apply_tool_proposal(
            [_record("침전물", said)], transcript=said,
            basis=session.proposal_basis(turn_id=2, generation=1),
            turn_id=2, language="ko", configuration_id=1, generation=1,
        )
        self.assertIs(applied.plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertEqual(applied.plan.observation_outcome, said)
        self.assertIn("안 보임", applied.plan.note_record["content"])


class FrontRuleNoteTests(unittest.TestCase):
    """Lane N's note rule stores the words as said, negation included."""

    def test_the_note_rule_keeps_the_negation(self) -> None:
        for said, content in (
            ("침전물 안 보임 기록해 줘", "침전물 안 보임"),
            ("기록해 줘 침전물 안 보임", "침전물 안 보임"),
            ("밴드가 보이지 않는다고 메모해 줘", "밴드가 보이지 않는다"),
            ("기포 없음 메모해 줘", "기포 없음"),
            ("색 변화 못 봤어 기록해 줘", "색 변화 못 봤어"),
        ):
            with self.subTest(said=said):
                self.assertEqual(note_request(said)[0], content)

    def test_the_stored_note_keeps_the_negation(self) -> None:
        session = CuratedProtocolSession(miniprep_fixture())
        session.activate_configured()
        session.plan("시작", turn_id=1, language="ko", configuration_id=1, generation=1)
        plan = session.plan(
            "침전물 안 보임 기록해 줘", turn_id=2, language="ko", configuration_id=1, generation=1)
        self.assertIs(plan.action, CuratedProtocolAction.RECORD_OBSERVATION)
        self.assertEqual(plan.note_record["content"], "침전물 안 보임")


if __name__ == "__main__":
    unittest.main()
