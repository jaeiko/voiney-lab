"""Two neighbouring holes the observation work turned up.

1. At a repeat step, "N단계 완료했어" naming another step went to the endpoint
   prompt before the step-number check, so a "네" to that prompt released the
   current step on a report about a different one. The step number is now
   asked about first, as at every other step, with or without the endpoint
   prompt outstanding.
2. Previewing a step the protocol does not have showed step 1 instead. It is
   now refused, as the full-detail lookup refuses it.

The preview checks use the fictional two-step fixture and run everywhere; the
repeat-step checks need in-gel and skip where that licensed PDF is absent.
Turns go through ``route_curated_runtime_turn``.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_curated_answer_gating import _fixture
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voiney_lab.runtime_routing import route_curated_runtime_turn

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

MISSING_STEP = "선택한 절차에 해당 단계가 없습니다. 현재 단계는 변경하지 않았습니다."


def _turn(session: CuratedProtocolSession, transcript: str, turn_id: int):
    return route_curated_runtime_turn(
        session, transcript, turn_id=turn_id, language="ko"
    ).plan


class MissingStepPreviewTests(unittest.TestCase):
    def _session(self, *, active: bool) -> CuratedProtocolSession:
        session = CuratedProtocolSession(_fixture("fictional-missing-preview-protocol"))
        session.active = active
        session.current_index = 0
        return session

    def test_a_missing_step_is_refused_rather_than_shown_as_step_one(self):
        for active in (True, False):
            for request in ("3단계 미리 알려줘", "30단계 미리 알려줘"):
                with self.subTest(active=active, request=request):
                    session = self._session(active=active)
                    before = session.state()
                    plan = _turn(session, request, 1)
                    self.assertEqual(plan.action, CuratedProtocolAction.PREVIEW_STEP)
                    self.assertEqual(plan.display_text, MISSING_STEP)
                    self.assertNotIn("미리보기", plan.display_text)
                    self.assertEqual(plan.facts, ())
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(session.state(), before)

    def test_a_step_that_exists_is_still_previewed(self):
        session = self._session(active=True)
        plan = _turn(session, "2단계 미리 알려줘", 1)
        self.assertEqual(plan.action, CuratedProtocolAction.PREVIEW_STEP)
        self.assertEqual(plan.target_step, "2")
        self.assertTrue(plan.display_text.startswith("2단계 미리보기"))


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class StepNumberBeforeEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)
        cls.index = {step.source_label: i for i, step in enumerate(cls.fixture.steps)}

    def _session_at(self, label: str, *, prompted: bool) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session._workflow_status = "active"
        session.current_index = self.index[label]
        if prompted:
            opened = _turn(session, "현재 단계를 완료했어요", 1)
            self.assertEqual(opened.intent_kind, "observation_confirmation_required")
        return session

    def test_a_completion_naming_another_step_is_asked_about_first(self):
        cases = {"7": ("8", "20", "30"), "9": ("10", "20", "30"), "20": ("21", "30")}
        for label, others in cases.items():
            for other in others:
                for prompted in (False, True):
                    with self.subTest(step=label, said=other, prompted=prompted):
                        session = self._session_at(label, prompted=prompted)
                        asked = _turn(session, f"{other}단계 완료했어", 2)
                        self.assertEqual(
                            asked.intent_kind, "explicit_step_mismatch_clarification"
                        )
                        self.assertIn(f"현재 진행 중인 단계는 {label}단계입니다", asked.display_text)
                        self.assertFalse(asked.state_changed)

                        # Confirming the current step opens its endpoint prompt;
                        # it does not release the step.
                        confirmed = _turn(session, "네", 3)
                        self.assertEqual(
                            confirmed.intent_kind, "observation_confirmation_required"
                        )
                        self.assertFalse(confirmed.state_changed)
                        self.assertEqual(session.current_index, self.index[label])
                        self.assertEqual(session.endpoint_observations(), {})

    def test_naming_the_current_step_still_meets_the_endpoint_prompt(self):
        for label in ("7", "9", "20"):
            with self.subTest(step=label):
                session = self._session_at(label, prompted=False)
                plan = _turn(session, f"{label}단계 완료했어", 1)
                self.assertEqual(plan.intent_kind, "observation_confirmation_required")
                self.assertEqual(session.current_index, self.index[label])

    def test_previewing_a_step_past_the_end_is_refused(self):
        session = self._session_at("7", prompted=False)
        plan = _turn(session, "30단계 미리 알려줘", 1)
        self.assertEqual(plan.display_text, MISSING_STEP)
        self.assertEqual(session.current_index, self.index["7"])


if __name__ == "__main__":
    unittest.main()
