"""Explicit fictional fixture matrix for commercial PDF-readiness behavior."""

from __future__ import annotations

from dataclasses import replace

from tests.test_experiment_protocol import evidence, minimal_protocol, source_action
from voice_workflow_agent.experiment_protocol import (
    BranchKind,
    ConditionalBranch,
    FeatureCode,
    MissingExecutionValue,
    ProtocolSourceStep,
    ReadinessReasonCode,
    ReadinessStatus,
    ScientificValue,
    SourceAmbiguity,
    assess_readiness,
    detect_features,
    validate_protocol,
)


def test_fictional_multistep_fixture_preserves_quantities_and_timers() -> None:
    protocol = minimal_protocol()
    first = protocol.sections[0].steps[0]
    second_instruction = "2. Record the result."
    second_action = source_action(
        action_id="record-result",
        instruction="Record the result.",
        quantities=(ScientificValue("2 × 100 µL aliquots"),),
        conditions=(),
        process_timer=None,
        required_observations=(),
        expected_results=(),
        notes=(),
        tips=(),
    )
    second = ProtocolSourceStep(
        "step-2", "2", second_instruction, evidence(second_instruction, 2),
        sub_actions=(replace(
            second_action, evidence=evidence("Record the result.", 2)),),
    )
    protocol = replace(
        protocol,
        sections=(replace(protocol.sections[0], steps=(first, second)),),
    )
    validated = validate_protocol(protocol)
    assert assess_readiness(validated).reason_codes == (
        ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
    ), assess_readiness(validated).reason_codes
    assert validated.sections[0].steps[0].sub_actions[0].process_timer is not None
    assert validated.sections[0].steps[1].sub_actions[0].quantities[0].source_text == (
        "2 × 100 µL aliquots"
    )


def test_fictional_conditional_fixture_remains_explicit_and_fail_closed() -> None:
    branch = ConditionalBranch(
        "room-temperature-branch", BranchKind.CONDITIONAL,
        "Room temperature shaking for 20 minutes is an alternative.",
        ("step-1",),
        evidence("Room temperature shaking for 20 minutes is an alternative."),
        step_id="step-1",
    )
    protocol = replace(minimal_protocol(), constructs=(branch,))
    assert {item.code for item in detect_features(protocol)} == {
        FeatureCode.CONDITIONAL_BRANCH
    }
    readiness = assess_readiness(protocol)
    assert readiness.status is ReadinessStatus.ANALYSIS_REQUIRED
    assert ReadinessReasonCode.UNSUPPORTED_CONDITIONAL_BRANCH.value in (
        readiness.reason_codes
    )


def test_fictional_ambiguous_missing_fixture_never_invents_execution_values() -> None:
    protocol = minimal_protocol()
    action = replace(
        protocol.sections[0].steps[0].sub_actions[0],
        missing_execution_values=(MissingExecutionValue(
            "missing-transfer-volume", "Required transfer volume is absent.",
            evidence("Add 500 µL Solution A."),
        ),),
    )
    ambiguity = SourceAmbiguity(
        "ambiguous-incubation", "The note says repeat 17–18 and later says repeat 19–20.",
        evidence("The note says repeat 17–18 and later says repeat 19–20."),
        step_id="step-1",
    )
    protocol = replace(
        protocol,
        sections=(replace(
            protocol.sections[0],
            steps=(replace(
                protocol.sections[0].steps[0], sub_actions=(action,)
            ),),
        ),),
        constructs=(ambiguity,),
    )
    readiness = assess_readiness(protocol)
    assert readiness.status is ReadinessStatus.ANALYSIS_REQUIRED
    assert set(readiness.reason_codes) >= {
        ReadinessReasonCode.MISSING_EXECUTION_CRITICAL_VALUE.value,
        ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
    }
    assert action.missing_execution_values[0].description == (
        "Required transfer volume is absent."
    )
