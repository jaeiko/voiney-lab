"""Gaps lane RP found on the rules' path, closed by rule (lane R7).

Decisions of 2026-10-06 (the professor's advice of 9/22: what is mechanical is
a rule; no safety guidance the source and the approved safety material do not
give):

1. Spilling, knocking over or overflowing -- "흘렸어", "엎질렀어", "쏟았어",
   "넘쳤어", with or without what it was -- is recorded as an anomaly.
   "튜브를 흘렸어" used to be a question about tubes. A question about it
   ("흘려도 돼?") stays a question, and the emergency gate is unchanged.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol_vocabulary_support import SOURCE_PDF, build_fixture, in_gel_fixture
from voiney_lab import experiment_protocol as domain
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolSession,
)
from voiney_lab.emergency import recognize_emergency
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.runtime_routing import route_curated_runtime_turn

STEPS = (
    "1 Cut the stained band into pieces and place them in a 1.5 mL tube.",
    "2 Wash the pieces with 500 µL of Solution A.",
    "3 Remove and discard Solution A.",
    "4 Wash the pieces with 500 µL of Solution B.",
    "5 Repeat steps 2-4 until the band is clear.",
    "6 Dry the pieces in the speedvac.",
)


def _fixture(*, with_repeat: bool = True):
    base = build_fixture(protocol_id="lane-r7-wash", title="Fictional wash", steps=STEPS)
    if not with_repeat:
        return base
    protocol = base.draft.protocol
    anchor = protocol.sections[0].steps[4]
    protocol = dataclasses.replace(protocol, constructs=(
        domain.RepeatUntil(
            repetition_id="repeat-2-4",
            condition_source_text="until the band is clear",
            repeated_step_ids=("step-2", "step-3", "step-4"),
            evidence=domain.SourceEvidence(1, anchor.instruction_source_text),
            step_id="step-5",
        ),
    ))
    domain.validate_protocol(protocol)
    draft = dataclasses.replace(
        base.draft, protocol=protocol, readiness=domain.assess_readiness(protocol),
    )
    return dataclasses.replace(base, draft=draft)


class _Turns:
    """One session, its turns numbered, read the way the server reads them."""

    def open(self, step_index: int | None, *, fixture=None) -> CuratedProtocolSession:
        self.session = CuratedProtocolSession(fixture or _fixture())
        self.session.activate_configured()
        self.turn_id = 0
        if step_index is not None:
            self.say("프로토콜 시작해줘")
            self.session.current_index = step_index
        return self.session

    def say(self, text: str, *, front: bool = False):
        self.turn_id += 1
        if front:
            return self.session.front_plan(
                text, turn_id=self.turn_id, language="ko",
                configuration_id=1, generation=1,
            )
        return route_curated_runtime_turn(
            self.session, text, turn_id=self.turn_id, language="ko",
            configuration_id=1, generation=1,
        ).plan

    def label(self) -> str | None:
        session = self.session
        return session.fixture.steps[session.current_index].source_label if session.active else None

    def projection(self):
        session = self.session
        return (
            session.active, session.current_index, session.workflow_status,
            session.state()["revision"],
        )


# --- Decision 1 --------------------------------------------------------------


class SpillIsRecordedAsAnAnomalyTests(_Turns, unittest.TestCase):
    """Decision 1: spilling words are a problem at the bench, recorded."""

    SPILLS = (
        "튜브를 흘렸어", "흘렸어", "아 흘렸다", "용액을 조금 흘렸어", "피펫팅하다가 흘렸어",
        "엎질렀어", "시약을 엎질렀어", "엎질러졌어", "튜브를 엎었어", "튜브가 엎어졌어",
        "쏟았어", "튜브를 쏟았어", "쏟아졌어", "쏟아버렸어",
        "넘쳤어", "용액이 넘쳤어", "넘쳐버렸어", "용액이 흘러넘쳤어",
        "Solution A를 흘렸어", "조금 흘린 것 같아", "흘렸어요", "쏟았습니다",
        "넘치고 있어", "용액이 쏟아지고 있어",
        # Said as having happened, with a question after it.
        "흘렸는데 어떡해", "튜브를 쏟았는데 어떻게 해?", "용액을 쏟았는데 어떻게 하지",
        "흘렸는데 괜찮아?",
    )
    QUESTIONS = (
        "흘려도 돼?", "쏟으면 어떡해?", "엎지르면 어떻게 해?", "넘치면 어떡해?",
        "흘렸어?", "흘리지 않게 조심해야 돼?", "흘린 거 아니야", "안 흘렸어",
        "쏟지 않도록 해야 해?", "시료를 흘렸어?", "흘렸는지 모르겠어", "흘렸어 아니야",
        "튜브가 넘치고 있어?",
    )

    def test_spilling_with_or_without_what_spilled_is_an_anomaly(self) -> None:
        for said in self.SPILLS:
            with self.subTest(said=said):
                self.open(2)
                before = self.projection()
                plan = self.say(said)
                self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
                self.assertTrue(plan.reported_anomaly)
                self.assertEqual(plan.anomaly_text, said)
                self.assertFalse(plan.state_changed)
                self.assertEqual(self.projection(), before)

    def test_a_question_or_a_denial_about_spilling_is_not_recorded(self) -> None:
        for said in self.QUESTIONS:
            with self.subTest(said=said):
                self.open(2)
                plan = self.say(said)
                self.assertIsNot(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
                self.assertFalse(plan.reported_anomaly)
                self.assertFalse(plan.state_changed)

    def test_the_anomaly_reply_adds_no_safety_guidance(self) -> None:
        # Recorded, and nothing said about what to do: no guidance the source
        # or the approved safety material does not give.
        self.open(2)
        plan = self.say("튜브를 흘렸어")
        self.assertEqual(
            plan.speech_text,
            "현재 3단계의 이상 사항 기록 요청을 확인했습니다. "
            "실험 기록 저장이 성공한 뒤에만 기록 완료를 확인합니다.",
        )

    def test_spilling_under_an_open_endpoint_question_keeps_the_question(self) -> None:
        # As "튜브가 터졌어" already did: recorded, the question stays open and
        # no longer takes a bare yes.
        self.open(4)
        self.say("5단계 완료했어")
        self.assertIsNotNone(self.session.pending_observation_confirmation)
        plan = self.say("튜브를 흘렸어")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)
        held = self.session.pending_observation_confirmation
        self.assertIsNotNone(held)
        self.assertFalse(held.accepts_yes_no)
        self.assertEqual(self.label(), "5")

    def test_the_emergency_gate_is_unchanged(self) -> None:
        self.assertIsNotNone(recognize_emergency("불이 났어"))
        self.assertIsNotNone(recognize_emergency("용액 누출됐어"))
        for said in ("튜브를 흘렸어", "흘렸어", "용액이 넘쳤어"):
            with self.subTest(said=said):
                self.assertIsNone(recognize_emergency(said))

    def test_the_anomaly_is_written_to_the_experiment_record(self) -> None:
        self.open(2)
        before = self.session.current_index
        plan = self.say("튜브를 흘렸어")
        events = _report_events(self.session, plan, before, turn_id=self.turn_id)
        self.assertEqual(
            [(e["event_type"], e["step_label"], e["user_wording"]) for e in events],
            [("anomaly", "3", "튜브를 흘렸어")],
        )


def _report_events(session, plan, pre_index, *, turn_id, store=None, listener=None):
    """Append ``plan`` as server.py does and return the report's events."""

    if listener is None:
        tmp = tempfile.TemporaryDirectory()
        store = ExperimentReportStore(Path(tmp.name) / "reports.sqlite")
        listener = SimpleNamespace(
            session_id="lane-r7-session", experiment_report_store=store,
            experiment_report_id=None, _tmp=tmp,
        )
    with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
        server_module._record_experiment_report_plan(
            listener, session, plan, turn_id=turn_id, generation=1,
            pre_transition_index=pre_index,
        )
    return listener.experiment_report_store.get_report(listener.experiment_report_id)["events"]


# --- The real in-gel protocol (lane RP's scenario) ----------------------------


@unittest.skipUnless(
    SOURCE_PDF.is_file(),
    f"requires the externally licensed Candidate A source PDF at {SOURCE_PDF}",
)
class InGelScenarioTests(_Turns, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = in_gel_fixture()

    def test_tube_spilled_is_an_anomaly(self) -> None:
        self.open(12, fixture=self.fixture)
        plan = self.say("튜브를 흘렸어")
        self.assertIs(plan.action, CuratedProtocolAction.REPORT_ANOMALY)


if __name__ == "__main__":
    unittest.main()
