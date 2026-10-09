"""A question, a hedge, a negation or the source line read back is not a report.

At in-gel's repeat steps (7, 9, 20) the endpoint phrase families released the
step on utterances that report nothing, or report the opposite:

- "완전히 탈색 안 됐어" -- the positive phrase has an optional ending, so a
  negation after it went unread;
- "젤이 투명해?" -- the question guard read the utterance key, and the key
  has already lost its "?";
- "색이 완전히 빠졌는지 모르겠어" -- no guard knew the hedge;
- "젤 밴드가 완전히 탈색될 때까지 2-7단계를 반복합니다" -- the source line,
  read back aloud.

"네?" -- asking to hear the question again -- confirmed both the completion
and the observation prompt for the same reason as the question above.

Every utterance below is from the investigation report's table of wrong
positives. The phrase checks need no fixture; the session checks use the
fictional fixture for the completion prompt and in-gel's repeat steps for the
observation prompt, which skip where that externally licensed PDF is absent.
Session turns go through ``route_curated_runtime_turn``, the boundary the
Cascade runtime calls.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_curated_answer_gating import _fixture
from voiney_lab.curated_protocol import (
    CuratedProtocolSession,
    _binary_frame_reply,
    _observation_predicate,
    load_curated_protocol_fixture,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

#: A negation after the endpoint phrase: a negative report, never a positive.
NEGATED = (
    ("7", "완전히 탈색 안 됐어"),
    ("7", "완전히 탈색은 안 됐어"),
    ("7", "완전히 탈색이 안 됐어"),
    ("7", "완전히 탈색되진 않았어"),
    ("7", "젤이 투명해 보이지 않아"),
    ("7", "젤이 투명해지지 않았어"),
)
#: Questions, hedges and the source read back: no report at all.
NOT_A_REPORT = (
    ("7", "젤이 투명해?"),
    ("7", "완전히 탈색되려면 얼마나 걸려?"),
    ("7", "완전히 탈색될 때까지 반복해야 해?"),
    ("7", "색이 완전히 빠졌는지 모르겠어"),
    ("7", "완전히 탈색됐는지 확인해볼게"),
    ("7", "젤 밴드가 완전히 탈색될 때까지 2-7단계를 반복합니다"),
    ("9", "흰색이야?"),
    ("9", "탈수됐어?"),
    ("9", "탈수됐는지 모르겠어"),
    ("20", "흰색이 됐는지 모르겠어"),
)
WRONG_POSITIVES = NEGATED + NOT_A_REPORT
#: Reports that released the step before this change and still must.
POSITIVES = (
    ("7", "완전히 탈색됐어"),
    ("7", "젤이 투명해졌어"),
    ("7", "젤이 완전히 탈색되어 투명해요"),
    ("7", "어, 젤이 이제 완전히 투명해졌어"),
    ("7", "색이 빠졌어"),
    ("7", "fully destained"),
    # Report E: "상태니까" is "because", and 안내 is not a negation.
    ("7", "어, 제 밴드가 완전히 탈색돼 있는 상태니까 7단계는 완료했어. 8단계로 안내해줘."),
    ("9", "흰색이야"),
    ("9", "탈수됐어"),
    ("9", "흰색으로 변했어"),
    ("9", "완전히 말랐어"),
    ("20", "흰색이야"),
    ("20", "탈수됐어"),
    ("20", "젤이 흰색으로 변했고 탈수됐어요"),
)
QUESTIONING_YES = ("네?", "응?", "예?")


def _turn(session: CuratedProtocolSession, transcript: str, turn_id: int):
    return route_curated_runtime_turn(
        session, transcript, turn_id=turn_id, language="ko"
    ).plan


class PhraseTests(unittest.TestCase):
    def test_a_negation_after_the_endpoint_phrase_is_a_negative_report(self):
        for label, utterance in NEGATED:
            with self.subTest(step=label, utterance=utterance):
                self.assertEqual(_observation_predicate(label, utterance), "negative")

    def test_a_question_a_hedge_or_the_source_read_back_is_no_report(self):
        for label, utterance in NOT_A_REPORT:
            with self.subTest(step=label, utterance=utterance):
                self.assertIsNone(_observation_predicate(label, utterance))

    def test_the_existing_reports_are_still_positive(self):
        for label, utterance in POSITIVES:
            with self.subTest(step=label, utterance=utterance):
                self.assertEqual(_observation_predicate(label, utterance), "positive")

    def test_a_questioning_yes_is_neither_yes_nor_no(self):
        for reply in QUESTIONING_YES + ("아니?", "아니야?"):
            with self.subTest(reply=reply):
                self.assertIsNone(_binary_frame_reply(reply))
        for reply in ("네", "응", "예"):
            with self.subTest(reply=reply):
                self.assertEqual(_binary_frame_reply(reply), "affirmative")


class CompletionPromptTests(unittest.TestCase):
    """The completion prompt reads the same reply; the fictional fixture suffices."""

    def test_a_questioning_yes_does_not_confirm_completion(self):
        # Lane VX, decision 1: "다음" no longer asks; "완료했어" does.
        for opener in ("완료했어", "2단계 완료했어"):
            for reply in QUESTIONING_YES:
                with self.subTest(opener=opener, reply=reply):
                    session = CuratedProtocolSession(
                        _fixture("fictional-questioning-yes-protocol")
                    )
                    session.active = True
                    session.current_index = 0
                    _turn(session, opener, 1)
                    # Guard the guard: the reply meets an outstanding question.
                    self.assertIsNotNone(session.pending_completion_confirmation)

                    plan = _turn(session, reply, 2)

                    self.assertNotEqual(plan.intent_kind, "pending_completion_confirmed")
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(session.current_index, 0)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class RepeatStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)
        cls.index = {step.source_label: i for i, step in enumerate(cls.fixture.steps)}

    def _session_at(self, label: str) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session._workflow_status = "active"
        session.current_index = self.index[label]
        return session

    def _prompted_at(self, label: str) -> CuratedProtocolSession:
        session = self._session_at(label)
        opened = _turn(session, "현재 단계를 완료했어요", 1)
        # Guard the guard: the next reply meets the observation prompt.
        self.assertEqual(opened.intent_kind, "observation_confirmation_required")
        self.assertIsNotNone(session.pending_observation_confirmation)
        return session

    def test_no_wrong_positive_advances_while_the_prompt_is_outstanding(self):
        for label, utterance in WRONG_POSITIVES:
            with self.subTest(step=label, utterance=utterance):
                session = self._prompted_at(label)
                plan = _turn(session, utterance, 2)
                self.assertFalse(plan.state_changed)
                self.assertNotEqual(plan.observation_predicate, "positive")
                self.assertEqual(session.current_index, self.index[label])
                self.assertEqual(session.endpoint_observations(), {})

    def test_no_wrong_positive_advances_unprompted(self):
        for label, utterance in WRONG_POSITIVES:
            with self.subTest(step=label, utterance=utterance):
                session = self._session_at(label)
                plan = _turn(session, utterance, 1)
                self.assertFalse(plan.state_changed)
                self.assertNotEqual(plan.observation_predicate, "positive")
                self.assertEqual(session.current_index, self.index[label])
                self.assertEqual(session.endpoint_observations(), {})

    def test_a_questioning_yes_answers_neither_prompt(self):
        for label in ("7", "9", "20"):
            for reply in QUESTIONING_YES:
                with self.subTest(step=label, prompt="observation", reply=reply):
                    session = self._prompted_at(label)
                    plan = _turn(session, reply, 2)
                    self.assertNotEqual(plan.intent_kind, "pending_observation_confirmed")
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(session.current_index, self.index[label])
                with self.subTest(step=label, prompt="completion", reply=reply):
                    session = self._session_at(label)
                    _turn(session, "다음", 1)
                    self.assertIsNotNone(session.pending_completion_confirmation)
                    plan = _turn(session, reply, 2)
                    self.assertNotEqual(plan.intent_kind, "pending_completion_confirmed")
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(session.current_index, self.index[label])

    def test_the_existing_reports_still_advance_exactly_one_step(self):
        for label, utterance in POSITIVES:
            with self.subTest(step=label, utterance=utterance):
                session = self._prompted_at(label)
                plan = _turn(session, utterance, 2)
                self.assertTrue(plan.state_changed)
                self.assertEqual(plan.observation_predicate, "positive")
                self.assertEqual(session.current_index, self.index[label] + 1)


if __name__ == "__main__":
    unittest.main()
