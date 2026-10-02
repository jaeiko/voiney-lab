"""An observation prompt is not given up on a reply it could not read.

At in-gel's repeat steps the session asks for the endpoint observation. A reply
that was neither an endpoint phrase nor a yes/no used to clear the prompt on
the spot and go to the general classifier, where the endpoint's own words --
탈색, 밴드 -- made it a term question and the server attached a web search.
From the next turn the prompt was gone, so the semantic model was consulted
too. These are the four replies from the pilot log (report A-D), in the order
they were spoken.

Now the prompt is asked again, at most twice, and then let go with only the
fact that nothing was recorded. An explicit question gets its read-only answer
and keeps the prompt; control commands go through as before.

Since lane M the step-7 reader knows "탈색이 됐어" and "탈색된 상태야", the
words A and D used, so those two are now read as the endpoint reached -- what
the person meant. B and C are still not read, and are still held.

Turns go through ``route_curated_runtime_turn``, the boundary the Cascade
runtime calls. In-gel's repeat steps need the externally licensed PDF, so the
tests skip where it is absent.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voiney_lab.runtime_routing import (
    probe_curated_semantic_fallback,
    route_curated_runtime_turn,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

#: The pilot log's replies at step 7. A arrived with no prompt outstanding and
#: opened it; B, C and D answered it.
REPORT_A = "어, 젤이 완전히 이제 젤 밴드가 투명해졌어. 탈, 아, 그니까 탈색이 됐어. 이제 이번 단계도 완료했어."
REPORT_B = "어, 결과는 탈색, 탈색돼 있어."
REPORT_C = "아니, 아, 7단계로 완료했다고. 탈색이 완료됐어."
REPORT_D = "응, 그 관찰 결과는 젤 밴드가 탈색된 상태야. 탈색이 되어 있어."

#: Routes that answer a question -- and for RELATED_QUESTION, the one the
#: server attaches a reference or web search to.
QUESTION_ROUTES = {
    CuratedProtocolAction.QUESTION,
    CuratedProtocolAction.RELATED_QUESTION,
    CuratedProtocolAction.VISUAL_REQUEST,
}


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class ObservationPromptHoldTests(unittest.TestCase):
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

    @staticmethod
    def _turn(session: CuratedProtocolSession, transcript: str, turn_id: int):
        return route_curated_runtime_turn(
            session, transcript, turn_id=turn_id, language="ko"
        ).plan

    def _prompted_at(self, label: str) -> CuratedProtocolSession:
        session = self._session_at(label)
        opened = self._turn(session, "현재 단계를 완료했어요", 1)
        self.assertEqual(opened.intent_kind, "observation_confirmation_required")
        self.assertIsNotNone(session.pending_observation_confirmation)
        return session

    def test_report_a_to_d_are_read_or_held_and_never_reach_a_term_question(self):
        # A, said with no prompt outstanding, reports the endpoint itself.
        session = self._session_at("7")
        opening = session.current_index
        a = self._turn(session, REPORT_A, 1)
        self.assertEqual(a.intent_kind, "direct_positive_observation")
        self.assertEqual(a.observation_predicate, "positive")
        self.assertTrue(a.state_changed)
        self.assertNotIn(a.action, QUESTION_ROUTES)
        self.assertEqual(session.current_index, opening + 1)

        # B, C and D answer an open prompt.
        session = self._session_at("7")
        opened = self._turn(session, "현재 단계를 완료했어요", 1)
        self.assertEqual(opened.intent_kind, "observation_confirmation_required")
        self.assertIsNotNone(session.pending_observation_confirmation)

        for turn_id, reply in ((2, REPORT_B), (3, REPORT_C)):
            with self.subTest(reply=reply):
                # The model is not consulted while the prompt owns the turn.
                self.assertEqual(
                    probe_curated_semantic_fallback(
                        session, reply, language="ko"
                    ).reason_code,
                    "pending_gate_owns_turn",
                )
                plan = self._turn(session, reply, turn_id)
                self.assertEqual(plan.intent_kind, "observation_confirmation_reasked")
                self.assertNotIn(plan.action, QUESTION_ROUTES)
                self.assertFalse(plan.state_changed)
                self.assertIsNotNone(session.pending_observation_confirmation)
                self.assertEqual(session.current_index, opening)
                self.assertEqual(session.endpoint_observations(), {})

        # D says the endpoint in words the reader knows, after two re-asks.
        self.assertEqual(
            probe_curated_semantic_fallback(
                session, REPORT_D, language="ko"
            ).reason_code,
            "pending_gate_owns_turn",
        )
        d = self._turn(session, REPORT_D, 4)
        self.assertEqual(d.intent_kind, "pending_observation_confirmed")
        self.assertEqual(d.observation_predicate, "positive")
        self.assertNotIn(d.action, QUESTION_ROUTES)
        self.assertTrue(d.state_changed)
        self.assertEqual(session.current_index, opening + 1)

    def test_the_prompt_is_asked_again_twice_then_let_go_without_a_record(self):
        for label in ("7", "9", "20"):
            with self.subTest(step=label):
                session = self._prompted_at(label)
                first = self._turn(session, "음 글쎄", 2)
                second = self._turn(session, "결과는 그렇게 됐어", 3)
                released = self._turn(session, "잘 모르겠네", 4)

                self.assertEqual(
                    [first.intent_kind, second.intent_kind],
                    ["observation_confirmation_reasked"] * 2,
                )
                self.assertEqual(
                    released.intent_kind, "observation_confirmation_released"
                )
                self.assertEqual(
                    released.display_text, f"{label}단계 관찰 결과는 기록하지 않았습니다."
                )
                self.assertFalse(released.reported_observation)
                self.assertIsNone(session.pending_observation_confirmation)
                self.assertEqual(session.current_index, self.index[label])
                self.assertEqual(session.endpoint_observations(), {})

    def test_an_explicit_question_is_answered_and_the_prompt_kept(self):
        for question in ("탈색이 뭐야?", "완전히 탈색되려면 얼마나 걸려?"):
            with self.subTest(question=question):
                session = self._prompted_at("7")
                answered = self._turn(session, question, 2)
                self.assertIn(answered.action, QUESTION_ROUTES)
                self.assertFalse(answered.state_changed)
                pending = session.pending_observation_confirmation
                self.assertIsNotNone(pending)
                # Answering a question is not asking again.
                self.assertEqual(pending.reprompt_count, 0)

                released = self._turn(session, "네", 3)
                self.assertEqual(released.intent_kind, "pending_observation_confirmed")
                self.assertEqual(session.current_index, self.index["7"] + 1)

    def test_control_commands_go_through_as_before(self):
        for command in ("일시정지", "그만", "타이머 시작"):
            with self.subTest(command=command):
                unprompted = self._turn(self._session_at("7"), command, 1)
                session = self._prompted_at("7")
                prompted = self._turn(session, command, 2)
                self.assertEqual(prompted.action, unprompted.action)
                self.assertEqual(prompted.intent_kind, unprompted.intent_kind)
                self.assertEqual(session.current_index, self.index["7"])


if __name__ == "__main__":
    unittest.main()
