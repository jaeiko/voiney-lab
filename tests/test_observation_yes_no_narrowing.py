"""The endpoint prompt is narrowed to a yes/no, and only a yes says yes.

When a description of what the person sees cannot be read, the second question
is a yes/no. It speaks one short sentence and points at the screen, where the
source endpoint is quoted as written and the verified sidecar is shown when one
translates exactly that sentence; no Korean criterion is composed for it.

A yes to that question is 네, 예, 응, 맞아, 맞아요, 됐어 or 됐어요. "완료했어요",
"했어" and "다 했어" say the work was done -- a different claim -- so they still
answer the completion prompt but no longer the observation prompt. The record
keeps the quoted endpoint beside the answer, since a bare "네" says nothing on
its own.

The reply checks need no fixture. The session and record checks use in-gel's
repeat steps and skip where that externally licensed PDF is absent; the record
check writes to a temporary report store through the server's own writer.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.test_curated_answer_gating import _fixture
from voiney_lab.curated_protocol import (
    CuratedProtocolSession,
    _binary_frame_reply,
    _observation_binary_reply,
    load_curated_protocol_fixture,
)
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.runtime_routing import route_curated_runtime_turn
from voiney_lab.server import ListenerSession, _record_experiment_report_plan
from voiney_lab.tools import ToolContext
from voiney_lab.vad import TurnState

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE = (
    ROOT / "data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
)
SOURCE_PDF = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"

OBSERVATION_YES = ("네", "예", "응", "맞아", "맞아요", "됐어", "됐어요", "네, 맞아요")
#: Say the work was done, not that the endpoint was seen.
WORK_DONE = ("완료했어요", "했어", "다 했어")
QUESTIONING_YES = ("네?", "응?", "예?")
REASK_SPEECH = "화면에 보이는 원문 기준대로 되었나요? 네 또는 아니요로 답해 주세요."


def _turn(session: CuratedProtocolSession, transcript: str, turn_id: int):
    return route_curated_runtime_turn(
        session, transcript, turn_id=turn_id, language="ko"
    ).plan


class ReplyTests(unittest.TestCase):
    def test_only_the_listed_words_say_yes_to_the_endpoint(self):
        for reply in OBSERVATION_YES:
            with self.subTest(reply=reply):
                self.assertEqual(_observation_binary_reply(reply), "affirmative")
        for reply in WORK_DONE + QUESTIONING_YES + ("그래", "물론"):
            with self.subTest(reply=reply):
                self.assertIsNone(_observation_binary_reply(reply))
        for reply in ("아니요", "아니", "아직이요"):
            with self.subTest(reply=reply):
                self.assertEqual(_observation_binary_reply(reply), "negative")

    def test_the_completion_prompt_still_takes_the_work_done_replies(self):
        for reply in WORK_DONE:
            with self.subTest(reply=reply):
                self.assertEqual(_binary_frame_reply(reply), "affirmative")
                session = CuratedProtocolSession(
                    _fixture("fictional-work-done-reply-protocol")
                )
                session.active = True
                session.current_index = 0
                _turn(session, "다음", 1)
                self.assertIsNotNone(session.pending_completion_confirmation)
                plan = _turn(session, reply, 2)
                self.assertEqual(plan.intent_kind, "pending_completion_confirmed")
                self.assertEqual(session.current_index, 1)


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class ObservationPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_curated_protocol_fixture(FIXTURE, PROVENANCE, SOURCE_PDF)
        cls.index = {step.source_label: i for i, step in enumerate(cls.fixture.steps)}

    def _prompted_at(self, label: str) -> CuratedProtocolSession:
        session = CuratedProtocolSession(self.fixture)
        session.active = True
        session._workflow_status = "active"
        session.current_index = self.index[label]
        opened = _turn(session, "현재 단계를 완료했어요", 1)
        self.assertEqual(opened.intent_kind, "observation_confirmation_required")
        return session

    def _stated(self, session: CuratedProtocolSession) -> str:
        interval = session.repetition_anchored_at(
            self.fixture.steps[session.current_index].step_id
        )
        return " ".join(str(interval["source_text"]).split())

    def test_the_second_question_is_a_short_yes_no_over_the_quoted_source(self):
        pages = {"7": 5, "9": 6, "20": 8}
        for label in ("7", "9", "20"):
            with self.subTest(step=label):
                session = self._prompted_at(label)
                stated = self._stated(session)
                asked = _turn(session, "결과는 그렇게 됐어", 2)

                self.assertEqual(asked.intent_kind, "observation_confirmation_reasked")
                self.assertEqual(asked.speech_text, REASK_SPEECH)
                # Step 7's endpoint is one fact's whole text, and that fact has
                # a verified sidecar; 9's and 20's sit inside longer passages,
                # whose sidecars are not a translation of the sentence alone.
                translated = (
                    "\n검증된 한국어 번역: "
                    "7단계: 젤 밴드가 완전히 탈색될 때까지 2–7단계를 반복합니다."
                    if label == "7" else ""
                )
                self.assertEqual(
                    asked.display_text,
                    f"원문 기준 (PDF p.{pages[label]}): “{stated}”{translated}"
                    f"\n\n{REASK_SPEECH}",
                )
                self.assertFalse(asked.state_changed)

    def test_only_a_narrowed_yes_releases_the_step(self):
        for label in ("7", "9", "20"):
            for reply in OBSERVATION_YES:
                with self.subTest(step=label, reply=reply):
                    session = self._prompted_at(label)
                    plan = _turn(session, reply, 2)
                    self.assertEqual(plan.intent_kind, "pending_observation_confirmed")
                    self.assertEqual(plan.observation_predicate, "positive")
                    self.assertEqual(session.current_index, self.index[label] + 1)
            for reply in WORK_DONE + QUESTIONING_YES:
                with self.subTest(step=label, reply=reply):
                    session = self._prompted_at(label)
                    plan = _turn(session, reply, 2)
                    self.assertEqual(plan.intent_kind, "observation_confirmation_reasked")
                    self.assertFalse(plan.state_changed)
                    self.assertEqual(session.current_index, self.index[label])
                    self.assertIsNotNone(session.pending_observation_confirmation)
                    self.assertEqual(session.endpoint_observations(), {})

    def test_the_answer_is_kept_beside_the_quoted_endpoint(self):
        for reply, predicate in (("네", "positive"), ("아니요", "negative")):
            with self.subTest(reply=reply):
                session = self._prompted_at("7")
                stated = self._stated(session)
                plan = _turn(session, reply, 2)
                self.assertEqual(plan.observation_predicate, predicate)
                self.assertEqual(
                    plan.observation_outcome, f"원문 종점 “{stated}” — 답: {reply}"
                )
        # The gate's own record keeps the words as spoken; it is unchanged.
        session = self._prompted_at("7")
        _turn(session, "네", 2)
        record = session.endpoint_observations()["candidate-a-step-07"]
        self.assertEqual(record["utterance"], "네")

    def test_the_experiment_record_holds_the_endpoint_and_the_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = CuratedProtocolSession(self.fixture)
            workflow.active = True
            workflow.current_index = self.index["7"]
            session = ListenerSession(
                tool_context=ToolContext(
                    Path("/unused/offline-catalog"), None, "ko", "test_only"
                ),
                curated_protocol_session=workflow,
            )
            session.accept_configuration(
                41, "cascade", "ko", self.fixture.protocol_id
            )
            session.detector.state = TurnState.PROCESSING
            session.experiment_report_store = ExperimentReportStore(
                Path(directory) / "reports.sqlite"
            )
            workflow.plan(
                "현재 단계를 완료했어요", turn_id=1, language="ko",
                configuration_id=41, generation=session.generation,
            )
            plan = workflow.plan(
                "네", turn_id=2, language="ko",
                configuration_id=41, generation=session.generation,
            )
            report = _record_experiment_report_plan(
                session, workflow, plan, turn_id=2,
                generation=session.generation,
                pre_transition_index=self.index["7"],
            )
        completed = [
            event for event in report["events"]
            if event["event_type"] == "step_completed"
        ]
        self.assertEqual(len(completed), 1)
        self.assertEqual(
            completed[0]["user_wording"],
            "원문 종점 “7 Repeat steps 2-7 until the gel band is fully destained”"
            " — 답: 네",
        )


if __name__ == "__main__":
    unittest.main()
