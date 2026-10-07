"""Shared helpers for the lane CB tests: a headspace-shaped fictional protocol.

Built from text alone: the sentences of the headspace source's pages 5 and
10-12 for steps 11-21 and 36-43, filler for the rest, so the step labels are
the document's own (12-16, 19-21, 36-42) and no licensed PDF is needed. The
tests run in both pytest baselines. Its constructs are the three the human
decisions of 2026-10-07 name:

* step 16 "Repeat steps 12-15 twice more": a FixedRangeRepetition of 12-15
  stated at 16, count 3 (the analysis's reading, which the catalog's reviewer
  confirms before an approved run; a development run takes it as read);
* step 21 "Repeat steps 19-20 for the required number of bacterial
  isolates/replicates": an OperatorDeterminedRepetition of 19-20 stated at 21;
* step 42 "If using newly made Porapak tubes, repeat steps 36-41 twice more":
  a ConditionalBranch whose step is 42, and a FixedRangeRepetition of 36-41
  stated at 42, count 3.

``branch_later=True`` adds a second condition at step 43 whose branch is
steps 44-45, for the case where the branch's steps follow the condition.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.protocol_vocabulary_support import build_fixture
from voiney_lab import experiment_protocol as domain
from voiney_lab import server as server_module
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.runtime_routing import route_curated_runtime_turn

PROTOCOL_ID = "lane-cb-headspace"
TITLE = "Using dynamic headspace collections for bacterial volatile sampling (fictional subset)"

REAL_STEPS: dict[int, str] = {
    11: (
        "After ~16 hours, remove the falcon tubes used in step 9 from the shaking "
        "incubator and assess the bacterial growth by visually comparing the bacterial "
        "culture to the LB control tube."
    ),
    12: "Centrifuge the bacterial culture into a pellet at 3000 rpm for 10 minutes.",
    13: "Discard the supernatant after centrifugation.",
    14: "Transfer 10 mL of Ringer's solution into the falcon tube containing the bacterial pellet.",
    15: "Mix the solution using a plastic pipette (10 mL) attached to a pipette controller.",
    16: "Repeat steps 12-15 twice more, to wash bacterial cells.",
    17: (
        "Measure the Optical Density (OD) of the bacterial culture in the Ringer's "
        "solution using a spectrophotometer, using Ringer's solution alone as a reference blank."
    ),
    18: "Dilute the bacterial culture with Ringer's solution, to reach the required optical density (OD=0.1).",
    19: (
        "Apply 70 µL (Garbeva, 2014) of the diluted bacterial culture, at the desired "
        "Optical Density, to a fresh agar plate."
    ),
    20: "Spread the diluted bacterial culture across the entire plate using a sterile inoculating loop.",
    21: (
        "Repeat steps 19-20 for the required number of bacterial isolates/replicates, "
        "leaving one agar plate without any inoculum as an uninoculated control."
    ),
    36: "In a fume hood, clamp the Porapak tubes so they are suspended.",
    37: (
        "Using a glass Pasteur pipette, take up 2 mL of redistilled diethyl ether and "
        "pass through the Porapak tubes."
    ),
    38: "Attach the Porapak tubes to a supply of nitrogen, using brass fittings, and turn the nitrogen supply on.",
    39: (
        "Check for a flow of nitrogen by placing the end of the Porapak tube into a "
        "vial of diethyl ether and assess for bubbles."
    ),
    40: "Place the Porapak tubes into a modified heating block at 132˚C for a minimum of 2 hours.",
    41: (
        "After 2 hours, remove the Porapak tubes from the heating block using heat "
        "resistant gloves and allow the Porapak tubes to cool. Keep nitrogen flow on "
        "whilst the tubes are cooling."
    ),
    42: (
        "If using newly made Porapak tubes, repeat steps 36-41 twice more (three "
        "conditioning rounds in total) to remove any contaminants on the Porapak."
    ),
    43: "Place a filter paper disc (12.5 cm) on top of one of the metal plates.",
}
STEP_COUNT = 46
CONDITION_42 = "If using newly made Porapak tubes"
RANGE_16 = "Repeat steps 12-15 twice more, to wash bacterial cells."
RANGE_21 = (
    "Repeat steps 19-20 for the required number of bacterial isolates/replicates, "
    "leaving one agar plate without any inoculum as an uninoculated control."
)
RANGE_42 = "repeat steps 36-41 twice more (three conditioning rounds in total)"
CONDITION_43 = "If a second batch of plates is prepared"


def step_texts(*, branch_later: bool = False) -> tuple[str, ...]:
    texts = []
    for number in range(1, STEP_COUNT + 1):
        text = REAL_STEPS.get(number, f"Filler step {number} of the headspace-shaped protocol.")
        if branch_later and number == 43:
            text = f"{CONDITION_43}, do steps 44-45 for that batch as well."
        texts.append(f"{number} {text}")
    return tuple(texts)


def headspace_fixture(*, branch_later: bool = False):
    base = build_fixture(
        protocol_id=PROTOCOL_ID, title=TITLE, steps=step_texts(branch_later=branch_later),
        materials=("Porapak tubes", "Ringer's solution"), equipment=("Fume hood",),
    )
    protocol = base.draft.protocol
    steps = {step.step_id: step for step in protocol.sections[0].steps}

    def evidence(step_id: str) -> domain.SourceEvidence:
        return domain.SourceEvidence(1, steps[step_id].instruction_source_text)

    constructs: list[domain.WorkflowConstruct] = [
        domain.FixedRangeRepetition(
            repetition_id="repeat-12-15", start_step_id="step-12", end_step_id="step-15",
            range_source_text=RANGE_16, evidence=evidence("step-16"), repeat_count=3,
            step_id="step-16",
        ),
        domain.OperatorDeterminedRepetition(
            repetition_id="repeat-19-20", start_step_id="step-19", end_step_id="step-20",
            range_source_text=RANGE_21, evidence=evidence("step-21"), step_id="step-21",
        ),
        domain.ConditionalBranch(
            branch_id="branch-42", kind=domain.BranchKind.CONDITIONAL,
            condition_source_text=CONDITION_42, branch_step_ids=("step-42",),
            evidence=evidence("step-42"), step_id="step-42",
        ),
        domain.FixedRangeRepetition(
            repetition_id="repeat-36-41", start_step_id="step-36", end_step_id="step-41",
            range_source_text=RANGE_42, evidence=evidence("step-42"), repeat_count=3,
            step_id="step-42",
        ),
    ]
    if branch_later:
        constructs.append(domain.ConditionalBranch(
            branch_id="branch-43", kind=domain.BranchKind.CONDITIONAL,
            condition_source_text=CONDITION_43, branch_step_ids=("step-44", "step-45"),
            evidence=evidence("step-43"), step_id="step-43",
        ))
    protocol = dataclasses.replace(protocol, constructs=tuple(constructs))
    domain.validate_protocol(protocol)
    draft = dataclasses.replace(
        base.draft, protocol=protocol, readiness=domain.assess_readiness(protocol),
    )
    return dataclasses.replace(base, draft=draft)


def index_of(label: str) -> int:
    """The step index of a source label of the headspace-shaped protocol."""

    return int(label) - 1


class Turns:
    """One session, its turns numbered, read the way the server reads them."""

    def open(self, step_index: int | None, *, fixture=None) -> CuratedProtocolSession:
        self.session = CuratedProtocolSession(fixture or headspace_fixture())
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

    def complete(self, *labels: str) -> None:
        for label in labels:
            self.say(f"{label}단계 완료했어")


def report_events(session, plan, pre_index, *, turn_id, listener):
    """Append ``plan`` as server.py does and return the report's events."""

    with patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}):
        server_module._record_experiment_report_plan(
            listener, session, plan, turn_id=turn_id, generation=1,
            pre_transition_index=pre_index,
        )
    return listener.experiment_report_store.get_report(listener.experiment_report_id)["events"]


class Recorded(Turns):
    """Turns whose state changes and records are written to an experiment report."""

    def start_recording(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.listener = SimpleNamespace(
            session_id="lane-cb-session",
            experiment_report_store=ExperimentReportStore(Path(tmp.name) / "r.sqlite"),
            experiment_report_id=None,
        )

    def record(self, said: str):
        before = self.session.current_index
        plan = self.say(said)
        if plan.state_changed or getattr(plan, "step_record", None):
            report_events(self.session, plan, before, turn_id=self.turn_id,
                          listener=self.listener)
        return plan

    def events(self):
        report = self.listener.experiment_report_store.get_report(
            self.listener.experiment_report_id)
        return [
            (e["event_type"], e["step_label"], (e.get("payload") or {}).get("step_record"))
            for e in report["events"]
        ]

    def report(self):
        return self.listener.experiment_report_store.get_report(
            self.listener.experiment_report_id)
