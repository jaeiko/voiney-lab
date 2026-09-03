"""Focused tests for Protocol domain validation and fail-closed readiness."""

from __future__ import annotations

import unittest
from dataclasses import fields, replace

from voice_workflow_agent.experiment_protocol import (
    ANALYSIS_REQUIRED_LABEL,
    GUIDANCE_READY_LABEL,
    ActualElapsedTime,
    BeforeStartPrerequisite,
    BranchKind,
    CapabilityPolicy,
    ConditionalBranch,
    ConflictLevel,
    DependencyTarget,
    Equipment,
    EstimatedDuration,
    ExperimentProtocol,
    FeatureCode,
    FixedRangeRepetition,
    Material,
    MissingExecutionValue,
    OneTimeReminder,
    P1_CAPABILITY_POLICY,
    ParallelWork,
    ProcessTimerSpecification,
    ProtocolConflict,
    ProtocolMetadata,
    ProtocolSection,
    ProtocolSourceStep,
    ProtocolSubAction,
    ProtocolValidationCode,
    ProtocolValidationError,
    ReadinessReasonCode,
    ReadinessStatus,
    RecurringAction,
    RecurringReminder,
    RepeatUntil,
    RequiredObservation,
    ReusableSubprocedure,
    ScientificValue,
    SourceAmbiguity,
    SourceEvidence,
    SourceStatement,
    assess_readiness,
    detect_features,
    validate_protocol,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    TextVerification,
    PDF_MEDIA_TYPE,
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)


PAGE_ONE_TEXT = "\n".join(
    (
        "Purpose and preparation",
        "Materials and equipment",
        "Section digestion",
        "1. Add 500 µL Solution A and mix at 37°C and 800 rpm.",
        "Add 500 µL Solution A.",
        "Mix at 37°C and 800 rpm.",
        "Incubate for 15 minutes.",
        "Observe the solution colour.",
        "Expected clear solution.",
        "Keep the tube closed.",
        "Work carefully.",
        "Wear gloves; Solution A is corrosive.",
        "Use the centrifuge at 800 rpm.",
        "Before starting, warm the instrument for 30 minutes.",
        "Room temperature shaking for 20 minutes is an alternative.",
        "Repeat steps 2–7.",
        "The note says repeat 17–18 and later says repeat 19–20.",
        "Run steps 20–21 while step 19 continues for three hours.",
        "Every 30 minutes, inspect the vessel.",
        "Repeat until the pH is neutral.",
        "Reuse the flush procedure.",
        "No SpeedVac duration is stated.",
        "A supporting value differs.",
        "An SDS value conflicts with the execution value.",
        "A safety-critical equipment limit conflicts.",
    )
)
PAGE_TWO_TEXT = "\n".join(
    (
        "Section completion",
        "2. Record the result.",
        "Record the result.",
        "3. Store the sample.",
        "Store the sample.",
        "Dependency evidence.",
        "Wear gloves; Solution A is corrosive.",
    )
)


def pdf_identity() -> ProtocolPdfExtraction:
    return ProtocolPdfExtraction(
        original_filename="fixture.pdf",
        byte_size=1234,
        sha256="a" * 64,
        media_type=PDF_MEDIA_TYPE,
        page_count=2,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title=None,
            author=None,
            subject=None,
            creator=None,
            producer=None,
            creation_date=None,
            modification_date=None,
        ),
        pages=(
            ProtocolPdfPage(1, PAGE_ONE_TEXT, False),
            ProtocolPdfPage(2, PAGE_TWO_TEXT, False),
        ),
        # This fixture stands in for a source whose text a second extraction
        # engine confirmed.  The field defaults to comparator_unavailable, so a
        # fixture that forgets to say this blocks readiness rather than
        # silently passing.
        text_verification=TextVerification.VERIFIED,
    )


def evidence(excerpt: str, page: int = 1) -> SourceEvidence:
    return SourceEvidence(page, excerpt)


def source_action(
    action_id: str = "add-solution",
    instruction: str = "Add 500 µL Solution A.",
    **overrides,
) -> ProtocolSubAction:
    values = {
        "action_id": action_id,
        "instruction_source_text": instruction,
        "evidence": evidence(instruction),
        "quantities": (ScientificValue("500 µL Solution A"),),
        "conditions": (
            SourceStatement(
                "mix-condition",
                "Mix at 37°C and 800 rpm.",
                evidence("Mix at 37°C and 800 rpm."),
            ),
        ),
        "process_timer": ProcessTimerSpecification(
            "incubation-timer",
            ScientificValue("15 minutes", "900", "seconds"),
            evidence("Incubate for 15 minutes."),
        ),
        "required_observations": (
            RequiredObservation(
                "colour",
                "Observe the solution colour.",
                evidence("Observe the solution colour."),
            ),
        ),
        "expected_results": (
            SourceStatement(
                "clear-result",
                "Expected clear solution.",
                evidence("Expected clear solution."),
            ),
        ),
        "notes": (
            SourceStatement(
                "closed-note",
                "Keep the tube closed.",
                evidence("Keep the tube closed."),
            ),
        ),
        "tips": (
            SourceStatement(
                "careful-tip",
                "Work carefully.",
                evidence("Work carefully."),
            ),
        ),
    }
    values.update(overrides)
    return ProtocolSubAction(**values)


SAFETY_WARNING_TEXT = "Wear gloves; Solution A is corrosive."


def safety_warning(page: int = 1) -> SourceStatement:
    """A source-backed hazard so a fixture can be execution-ready."""

    return SourceStatement(
        f"safety-warning-p{page}",
        SAFETY_WARNING_TEXT,
        evidence(SAFETY_WARNING_TEXT, page),
    )


def source_step(
    step_id: str = "step-1",
    source_label: str = "1",
    instruction: str = "1. Add 500 µL Solution A and mix at 37°C and 800 rpm.",
    **overrides,
) -> ProtocolSourceStep:
    values = {
        "step_id": step_id,
        "source_label": source_label,
        "instruction_source_text": instruction,
        "evidence": evidence(instruction),
        "sub_actions": (source_action(),),
        "warnings": (safety_warning(),),
    }
    values.update(overrides)
    return ProtocolSourceStep(**values)


def metadata(**overrides) -> ProtocolMetadata:
    values = {
        "pdf": pdf_identity(),
        "title": "Compact Protocol fixture",
        "original_language": "en",
    }
    values.update(overrides)
    return ProtocolMetadata(**values)


def minimal_protocol(**overrides) -> ExperimentProtocol:
    values = {
        "protocol_id": "fixture-protocol",
        "metadata": metadata(),
        "sections": (
            ProtocolSection(
                "digestion",
                "Section digestion",
                evidence("Section digestion"),
                (source_step(),),
            ),
        ),
    }
    values.update(overrides)
    return ExperimentProtocol(**values)


# Every Protocol with executable steps now carries the safety-confirmation
# gate until a reviewer clears it, so a domain-level assessment can never read
# GUIDANCE_READY on its own. Where these tests used to assert "ready" they now
# assert "nothing except the safety confirmation is blocking", which is the
# same statement about the feature under test.
_SAFETY_GATE = (ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,)


class ExperimentProtocolTests(unittest.TestCase):
    def assert_analysis_required(
        self,
        protocol: ExperimentProtocol,
        reason: ReadinessReasonCode,
    ):
        assessment = assess_readiness(protocol)
        self.assertEqual(assessment.status, ReadinessStatus.ANALYSIS_REQUIRED)
        self.assertEqual(assessment.label, ANALYSIS_REQUIRED_LABEL)
        self.assertIn(reason.value, assessment.reason_codes)
        return assessment

    def test_minimal_sequential_protocol_is_ready_and_deterministic(self):
        protocol = minimal_protocol()

        first = assess_readiness(protocol)
        second = assess_readiness(protocol)

        self.assertEqual(first, second)
        self.assertEqual(first.reason_codes, _SAFETY_GATE)
        self.assertEqual(first.status, ReadinessStatus.ANALYSIS_REQUIRED)
        self.assertEqual(
            {GUIDANCE_READY_LABEL, ANALYSIS_REQUIRED_LABEL},
            {"안내 준비 완료", "Protocol 분석 필요"},
        )

    def test_pdf_identity_is_reused_with_identity_only_semantics(self):
        value = minimal_protocol().metadata

        self.assertEqual(value.original_filename, "fixture.pdf")
        self.assertEqual(value.media_type, "application/pdf")
        self.assertEqual(value.page_count, 2)
        self.assertEqual(value.file_checksum, "a" * 64)
        self.assertTrue(
            {field.name for field in fields(ProtocolMetadata)}.isdisjoint(
                {
                    "trusted",
                    "approved",
                    "official",
                    "current",
                    "approval_status",
                    "trust_status",
                }
            )
        )

    def test_missing_optional_metadata_is_valid_and_not_inferred(self):
        value = minimal_protocol(metadata=metadata())

        validated = validate_protocol(value)

        self.assertEqual(validated.metadata.authors, ())
        self.assertIsNone(validated.metadata.version)
        self.assertIsNone(validated.metadata.doi)
        self.assertIsNone(validated.metadata.license)
        self.assertIsNone(validated.metadata.source_status)

    def test_protocol_categories_and_exact_scientific_strings_are_preserved(self):
        protocol = minimal_protocol(
            metadata=metadata(
                authors=("Fixture Author",),
                publication_date="2026-07-30",
                version="draft-1",
                doi="10.0000/fixture",
                source_uri="https://example.test/protocol",
                license="CC BY",
                source_status="In development",
            ),
            before_start=(
                BeforeStartPrerequisite(
                    "warm-instrument",
                    "Before starting, warm the instrument for 30 minutes.",
                    evidence(
                        "Before starting, warm the instrument for 30 minutes."
                    ),
                    estimated_duration=EstimatedDuration("30 minutes", 1800),
                ),
            ),
            materials=(
                Material(
                    "solution-a",
                    "Solution A",
                    evidence("Add 500 µL Solution A."),
                    quantities=(ScientificValue("500 µL Solution A"),),
                ),
            ),
            equipment=(
                Equipment(
                    "centrifuge",
                    "centrifuge",
                    evidence("Use the centrifuge at 800 rpm."),
                    settings=(ScientificValue("800 rpm"),),
                ),
            ),
        )

        validated = validate_protocol(protocol)

        action = validated.sections[0].steps[0].sub_actions[0]
        self.assertEqual(action.quantities[0].source_text, "500 µL Solution A")
        self.assertEqual(
            action.conditions[0].source_text,
            "Mix at 37°C and 800 rpm.",
        )
        self.assertEqual(validated.metadata.source_status, "In development")

    def test_invalid_page_or_excerpt_evidence_is_rejected(self):
        base = minimal_protocol()
        action = base.sections[0].steps[0].sub_actions[0]
        step = base.sections[0].steps[0]

        for bad_evidence in (
            SourceEvidence(0, action.evidence.source_excerpt),
            SourceEvidence(3, action.evidence.source_excerpt),
            SourceEvidence(1, "Text absent from the source page."),
        ):
            bad_action = replace(action, evidence=bad_evidence)
            bad_step = replace(step, sub_actions=(bad_action,))
            bad_section = replace(base.sections[0], steps=(bad_step,))
            with self.assertRaises(ProtocolValidationError):
                validate_protocol(replace(base, sections=(bad_section,)))

    def test_duplicate_section_step_and_action_identifiers_are_rejected(self):
        base = minimal_protocol()
        section = base.sections[0]
        with self.assertRaises(ProtocolValidationError) as section_error:
            validate_protocol(replace(base, sections=(section, section)))
        self.assertEqual(
            section_error.exception.code,
            ProtocolValidationCode.DUPLICATE_SECTION_ID,
        )

        second_section = ProtocolSection(
            "completion",
            "Section completion",
            evidence("Section completion", 2),
            (section.steps[0],),
        )
        with self.assertRaises(ProtocolValidationError) as step_error:
            validate_protocol(replace(base, sections=(section, second_section)))
        self.assertEqual(
            step_error.exception.code,
            ProtocolValidationCode.DUPLICATE_STEP_ID,
        )

        step = section.steps[0]
        duplicate_actions = replace(
            step,
            sub_actions=(step.sub_actions[0], step.sub_actions[0]),
        )
        with self.assertRaises(ProtocolValidationError) as action_error:
            validate_protocol(
                replace(base, sections=(replace(section, steps=(duplicate_actions,)),))
            )
        self.assertEqual(
            action_error.exception.code,
            ProtocolValidationCode.DUPLICATE_ACTION_ID,
        )

    def test_dangling_dependencies_and_references_are_rejected(self):
        base = minimal_protocol()
        step = replace(
            base.sections[0].steps[0],
            dependencies=(DependencyTarget("missing-step"),),
        )
        with self.assertRaises(ProtocolValidationError) as dependency_error:
            validate_protocol(
                replace(
                    base,
                    sections=(replace(base.sections[0], steps=(step,)),),
                )
            )
        self.assertEqual(
            dependency_error.exception.code,
            ProtocolValidationCode.DANGLING_DEPENDENCY,
        )

        construct = RepeatUntil(
            "repeat-missing",
            "Repeat until the pH is neutral.",
            ("missing-step",),
            evidence("Repeat until the pH is neutral."),
        )
        with self.assertRaises(ProtocolValidationError) as reference_error:
            validate_protocol(replace(base, constructs=(construct,)))
        self.assertEqual(
            reference_error.exception.code,
            ProtocolValidationCode.DANGLING_REFERENCE,
        )

    def test_dependency_cycles_are_detected_deterministically(self):
        first = source_step(
            dependencies=(DependencyTarget("step-2"),),
        )
        second = ProtocolSourceStep(
            "step-2",
            "2",
            "2. Record the result.",
            evidence("2. Record the result.", 2),
            dependencies=(DependencyTarget("step-1"),),
        )
        protocol = minimal_protocol(
            sections=(
                ProtocolSection(
                    "digestion",
                    "Section digestion",
                    evidence("Section digestion"),
                    (first, second),
                ),
            )
        )

        with self.assertRaises(ProtocolValidationError) as context:
            validate_protocol(protocol)

        self.assertEqual(
            context.exception.code,
            ProtocolValidationCode.DEPENDENCY_CYCLE,
        )

    def test_source_step_label_and_stable_identifiers_are_required(self):
        base = minimal_protocol()
        for bad_step in (
            replace(base.sections[0].steps[0], source_label=""),
            replace(base.sections[0].steps[0], step_id="not stable"),
        ):
            with self.assertRaises(ProtocolValidationError):
                validate_protocol(
                    replace(
                        base,
                        sections=(
                            replace(base.sections[0], steps=(bad_step,)),
                        ),
                    )
                )

    def test_time_semantics_are_distinct_domain_types(self):
        estimated = EstimatedDuration("15 minutes", 900)
        timer = ProcessTimerSpecification(
            "timer",
            ScientificValue("15 minutes"),
            evidence("Incubate for 15 minutes."),
        )
        reminder = OneTimeReminder(
            "reminder",
            ScientificValue("10 minutes"),
            "Incubate for 15 minutes.",
            evidence("Incubate for 15 minutes."),
        )
        recurring = RecurringReminder(
            "recurring",
            ScientificValue("30 minutes"),
            "Every 30 minutes, inspect the vessel.",
            evidence("Every 30 minutes, inspect the vessel."),
        )
        elapsed = ActualElapsedTime("14 minutes elapsed", 840)

        self.assertNotEqual(type(estimated), type(timer))
        self.assertNotEqual(type(timer), type(reminder))
        self.assertNotEqual(type(reminder), type(recurring))
        self.assertNotEqual(type(recurring), type(elapsed))

    def test_compound_step_keeps_timer_on_only_its_extraction_sub_action(self):
        add = source_action()
        mix = source_action(
            "mix",
            "Mix at 37°C and 800 rpm.",
            quantities=(),
            conditions=(),
            process_timer=None,
            required_observations=(),
            expected_results=(),
            notes=(),
            tips=(),
        )
        extract = source_action(
            "extract",
            "Incubate for 15 minutes.",
            quantities=(),
            conditions=(),
            process_timer=ProcessTimerSpecification(
                "extraction-only",
                ScientificValue("15 minutes"),
                evidence("Incubate for 15 minutes."),
            ),
            required_observations=(),
            expected_results=(),
            notes=(),
            tips=(),
        )
        step = replace(
            minimal_protocol().sections[0].steps[0],
            sub_actions=(add, mix, extract),
        )
        protocol = minimal_protocol(
            sections=(
                replace(minimal_protocol().sections[0], steps=(step,)),
            )
        )

        validate_protocol(protocol)

        self.assertIsNone(step.sub_actions[0].estimated_duration)
        self.assertIsNone(step.sub_actions[1].process_timer)
        self.assertIsNotNone(step.sub_actions[2].process_timer)
        self.assertFalse(hasattr(step, "process_timer"))

    def test_candidate_a_repeat_ambiguities_block_without_correction(self):
        cases = (
            SourceAmbiguity(
                "step-7-self-reference",
                "Repeat steps 2–7.",
                evidence("Repeat steps 2–7."),
                step_id="step-1",
            ),
            SourceAmbiguity(
                "step-20-range-conflict",
                "The note says repeat 17–18 and later says repeat 19–20.",
                evidence(
                    "The note says repeat 17–18 and later says repeat 19–20."
                ),
                step_id="step-1",
            ),
        )
        for ambiguity in cases:
            with self.subTest(ambiguity=ambiguity.ambiguity_id):
                assessment = self.assert_analysis_required(
                    replace(minimal_protocol(), constructs=(ambiguity,)),
                    ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
                )
                self.assertEqual(
                    assessment.reasons[0].feature_code,
                    FeatureCode.UNRESOLVED_AMBIGUITY,
                )
                self.assertEqual(assessment.reasons[0].step_id, "step-1")

    def test_missing_speedvac_duration_remains_missing_and_blocks(self):
        base = minimal_protocol()
        action = replace(
            base.sections[0].steps[0].sub_actions[0],
            instruction_source_text="No SpeedVac duration is stated.",
            evidence=evidence("No SpeedVac duration is stated."),
            quantities=(),
            conditions=(),
            process_timer=ProcessTimerSpecification(
                "speedvac-duration",
                None,
                evidence("No SpeedVac duration is stated."),
                required_for_execution=True,
            ),
        )
        step = replace(base.sections[0].steps[0], sub_actions=(action,))
        protocol = replace(
            base,
            sections=(replace(base.sections[0], steps=(step,)),),
        )

        assessment = self.assert_analysis_required(
            protocol,
            ReadinessReasonCode.MISSING_EXECUTION_CRITICAL_VALUE,
        )

        self.assertIsNone(action.process_timer.duration)
        self.assertEqual(
            assessment.reasons[0].evidence.source_excerpt,
            "No SpeedVac duration is stated.",
        )

    def test_explicit_missing_values_block_without_inference(self):
        base = minimal_protocol()
        action = replace(
            base.sections[0].steps[0].sub_actions[0],
            missing_execution_values=(
                MissingExecutionValue(
                    "unknown-volume",
                    "Required transfer volume is absent.",
                    evidence("Add 500 µL Solution A."),
                ),
            ),
        )
        protocol = replace(
            base,
            sections=(
                replace(
                    base.sections[0],
                    steps=(replace(base.sections[0].steps[0], sub_actions=(action,)),),
                ),
            ),
        )
        self.assert_analysis_required(
            protocol,
            ReadinessReasonCode.MISSING_EXECUTION_CRITICAL_VALUE,
        )

    def test_repeat_until_remains_explicit_and_policy_can_evolve(self):
        construct = RepeatUntil(
            "neutral-ph",
            "Repeat until the pH is neutral.",
            ("step-1",),
            evidence("Repeat until the pH is neutral."),
            step_id="step-1",
        )
        protocol = replace(minimal_protocol(), constructs=(construct,))

        features = detect_features(protocol)
        self.assertEqual(features[0].code, FeatureCode.REPEAT_UNTIL)
        self.assert_analysis_required(
            protocol,
            ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL,
        )

        future_policy = CapabilityPolicy(
            "repeat-until-capable",
            P1_CAPABILITY_POLICY.supported_features
            | {FeatureCode.REPEAT_UNTIL},
        )
        self.assertEqual(
            assess_readiness(
                protocol,
                capability_policy=future_policy,
            ).reason_codes,
            _SAFETY_GATE,
        )

    def test_fixed_range_is_represented_but_ambiguity_still_blocks(self):
        repetition = FixedRangeRepetition(
            "repeat-1-to-1",
            "step-1",
            "step-1",
            "Repeat step 1 twice.",
            evidence("Repeat steps 2–7."),
            repeat_count=2,
            step_id="step-1",
        )
        protocol = replace(minimal_protocol(), constructs=(repetition,))
        # The construct is represented and the policy supports it, but a
        # bounded repetition does not execute on a declared count: a reviewer
        # confirms the bound, so this blocks alongside the safety gate.
        self.assertEqual(
            assess_readiness(protocol).reason_codes,
            (
                ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
                ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION.value,
            ),
        )

        ambiguity = SourceAmbiguity(
            "ambiguous-range",
            "Repeat steps 2–7.",
            evidence("Repeat steps 2–7."),
            step_id="step-1",
        )
        self.assert_analysis_required(
            replace(protocol, constructs=(repetition, ambiguity)),
            ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
        )

    def test_parallel_and_recurring_work_are_detected_and_blocked(self):
        base = minimal_protocol()
        second = ProtocolSourceStep(
            "step-2",
            "2",
            "2. Record the result.",
            evidence("2. Record the result.", 2),
        )
        section = replace(
            base.sections[0],
            steps=(base.sections[0].steps[0], second),
        )
        parallel = ParallelWork(
            "three-hour-background",
            ("step-1", "step-2"),
            "Run steps 20–21 while step 19 continues for three hours.",
            evidence(
                "Run steps 20–21 while step 19 continues for three hours."
            ),
            section_id="digestion",
        )
        recurring_action = RecurringAction(
            "inspect-every-30",
            DependencyTarget("step-1", "add-solution"),
            ScientificValue("30 minutes"),
            "Every 30 minutes, inspect the vessel.",
            evidence("Every 30 minutes, inspect the vessel."),
            step_id="step-1",
            action_id="add-solution",
        )
        protocol = replace(
            base,
            sections=(section,),
            constructs=(parallel, recurring_action),
        )

        feature_codes = tuple(feature.code for feature in detect_features(protocol))
        self.assertIn(FeatureCode.PARALLEL_BACKGROUND_WORK, feature_codes)
        self.assertIn(FeatureCode.RECURRING_ACTION, feature_codes)
        assessment = assess_readiness(protocol)
        self.assertIn(
            ReadinessReasonCode.UNSUPPORTED_PARALLEL_BACKGROUND_WORK.value,
            assessment.reason_codes,
        )
        self.assertIn(
            ReadinessReasonCode.UNSUPPORTED_RECURRING_ACTION.value,
            assessment.reason_codes,
        )

    def test_recurring_reminder_is_not_flattened_to_one_time_timer(self):
        base = minimal_protocol()
        action = replace(
            base.sections[0].steps[0].sub_actions[0],
            recurring_reminders=(
                RecurringReminder(
                    "every-30",
                    ScientificValue("30 minutes"),
                    "Every 30 minutes, inspect the vessel.",
                    evidence("Every 30 minutes, inspect the vessel."),
                ),
            ),
        )
        protocol = replace(
            base,
            sections=(
                replace(
                    base.sections[0],
                    steps=(replace(base.sections[0].steps[0], sub_actions=(action,)),),
                ),
            ),
        )

        features = detect_features(protocol)

        self.assertEqual(features[0].code, FeatureCode.RECURRING_REMINDER)
        self.assert_analysis_required(
            protocol,
            ReadinessReasonCode.UNSUPPORTED_RECURRING_REMINDER,
        )

    def test_conditional_and_reusable_subprocedure_are_not_flattened(self):
        conditional = ConditionalBranch(
            "room-temperature-alternative",
            BranchKind.ALTERNATIVE,
            "Room temperature shaking for 20 minutes is an alternative.",
            ("step-1",),
            evidence(
                "Room temperature shaking for 20 minutes is an alternative."
            ),
            step_id="step-1",
        )
        reusable = ReusableSubprocedure(
            "flush",
            ("step-1",),
            "Reuse the flush procedure.",
            evidence("Reuse the flush procedure."),
            step_id="step-1",
        )
        protocol = replace(
            minimal_protocol(),
            constructs=(conditional, reusable),
        )

        codes = {feature.code for feature in detect_features(protocol)}
        self.assertEqual(
            codes,
            {
                FeatureCode.CONDITIONAL_BRANCH,
                FeatureCode.REUSABLE_SUBPROCEDURE,
            },
        )
        assessment = assess_readiness(protocol)
        self.assertIn(
            ReadinessReasonCode.UNSUPPORTED_CONDITIONAL_BRANCH.value,
            assessment.reason_codes,
        )
        self.assertIn(
            ReadinessReasonCode.UNSUPPORTED_REUSABLE_SUBPROCEDURE.value,
            assessment.reason_codes,
        )

    def test_conflict_levels_have_fail_closed_readiness_semantics(self):
        informational = ProtocolConflict(
            "info",
            ConflictLevel.INFORMATIONAL,
            "A supporting value differs.",
            evidence("A supporting value differs."),
            step_id="step-1",
        )
        self.assertEqual(
            assess_readiness(
                replace(minimal_protocol(), constructs=(informational,))
            ).reason_codes,
            _SAFETY_GATE,
        )

        execution = ProtocolConflict(
            "execution",
            ConflictLevel.EXECUTION_VALUE,
            "An SDS value conflicts with the execution value.",
            evidence("An SDS value conflicts with the execution value."),
            step_id="step-1",
        )
        self.assert_analysis_required(
            replace(minimal_protocol(), constructs=(execution,)),
            ReadinessReasonCode.UNRESOLVED_EXECUTION_VALUE_CONFLICT,
        )

        safety = ProtocolConflict(
            "safety",
            ConflictLevel.SAFETY_CRITICAL,
            "A safety-critical equipment limit conflicts.",
            evidence("A safety-critical equipment limit conflicts."),
            resolved=True,
            resolution_source_text="Researcher recorded a resolution.",
            step_id="step-1",
        )
        self.assert_analysis_required(
            replace(minimal_protocol(), constructs=(safety,)),
            ReadinessReasonCode.SAFETY_CRITICAL_CONFLICT,
        )

    def test_feature_and_reason_order_is_stable_with_related_evidence(self):
        parallel = ParallelWork(
            "parallel",
            ("step-1", "step-2"),
            "Run steps 20–21 while step 19 continues for three hours.",
            evidence(
                "Run steps 20–21 while step 19 continues for three hours."
            ),
            section_id="digestion",
        )
        ambiguity = SourceAmbiguity(
            "ambiguous",
            "Repeat steps 2–7.",
            evidence("Repeat steps 2–7."),
            section_id="digestion",
            step_id="step-1",
        )
        second = ProtocolSourceStep(
            "step-2",
            "2",
            "2. Record the result.",
            evidence("2. Record the result.", 2),
        )
        base = minimal_protocol()
        protocol = replace(
            base,
            sections=(
                replace(
                    base.sections[0],
                    steps=(base.sections[0].steps[0], second),
                ),
            ),
            constructs=(ambiguity, parallel),
        )

        first = assess_readiness(protocol)
        second_assessment = assess_readiness(protocol)

        self.assertEqual(first, second_assessment)
        self.assertEqual(
            first.reason_codes,
            (
                ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value,
                ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
                ReadinessReasonCode.UNSUPPORTED_PARALLEL_BACKGROUND_WORK.value,
            ),
        )
        self.assertEqual(first.reasons[0].step_id, "step-1")
        self.assertEqual(first.reasons[0].evidence.source_page_number, 1)
        self.assertNotIn("Traceback", first.reasons[0].message)

    def test_invalid_protocol_assessment_is_sanitized_and_fail_closed(self):
        invalid = replace(minimal_protocol(), protocol_id="not stable")

        with self.assertRaises(ProtocolValidationError) as context:
            validate_protocol(invalid)
        assessment = assess_readiness(invalid)

        self.assertNotIn("Traceback", str(context.exception))
        self.assertNotIn("/home/", str(context.exception))
        self.assertEqual(
            assessment.reason_codes,
            (ReadinessReasonCode.INVALID_PROTOCOL.value,),
        )
        self.assertNotIn("Traceback", assessment.reasons[0].message)

    def test_no_steps_is_valid_structure_but_not_ready(self):
        protocol = replace(minimal_protocol(), sections=())

        validate_protocol(protocol)
        self.assert_analysis_required(
            protocol,
            ReadinessReasonCode.NO_EXECUTABLE_STEPS,
        )


if __name__ == "__main__":
    unittest.main()
