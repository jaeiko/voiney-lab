"""Deterministic Experiment Protocol domain model and readiness assessment.

This module represents workflow semantics supplied by a later structured
extraction stage. It does not infer structure from PDF text. Protocol checksum
values retain the byte-identity-only meaning defined by Slice 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from voice_workflow_agent.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    ProtocolPdfExtraction,
    TextVerification,
)


GUIDANCE_READY_LABEL = "안내 준비 완료"
ANALYSIS_REQUIRED_LABEL = "Protocol 분석 필요"
_STABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolValidationCode(str, Enum):
    INVALID_IDENTIFIER = "invalid_identifier"
    INVALID_TEXT = "invalid_text"
    INVALID_FILE_IDENTITY = "invalid_file_identity"
    INVALID_SOURCE_PAGE = "invalid_source_page"
    SOURCE_EXCERPT_MISMATCH = "source_excerpt_mismatch"
    DUPLICATE_SECTION_ID = "duplicate_section_id"
    DUPLICATE_STEP_ID = "duplicate_step_id"
    DUPLICATE_ACTION_ID = "duplicate_action_id"
    DUPLICATE_CONSTRUCT_ID = "duplicate_construct_id"
    DANGLING_REFERENCE = "dangling_reference"
    DANGLING_DEPENDENCY = "dangling_dependency"
    DEPENDENCY_CYCLE = "dependency_cycle"
    INVALID_TIME_VALUE = "invalid_time_value"
    MISSING_SOURCE_LABEL = "missing_source_label"


class ProtocolValidationError(ValueError):
    """Sanitized, stable validation failure for a structured Protocol."""

    def __init__(
        self,
        code: ProtocolValidationCode,
        message: str,
        *,
        location: str | None = None,
    ) -> None:
        self.code = code
        self.location = location
        public_message = message if location is None else f"{location}: {message}"
        super().__init__(public_message)


class ReadinessStatus(str, Enum):
    GUIDANCE_READY = "guidance_ready"
    ANALYSIS_REQUIRED = "analysis_required"


class FeatureCode(str, Enum):
    CONDITIONAL_BRANCH = "conditional_branch"
    FIXED_RANGE_REPETITION = "fixed_range_repetition"
    REPEAT_UNTIL = "repeat_until"
    PARALLEL_BACKGROUND_WORK = "parallel_background_work"
    RECURRING_REMINDER = "recurring_reminder"
    RECURRING_ACTION = "recurring_action"
    REUSABLE_SUBPROCEDURE = "reusable_subprocedure"
    UNRESOLVED_AMBIGUITY = "unresolved_ambiguity"
    INFORMATIONAL_DIFFERENCE = "informational_difference"
    EXECUTION_VALUE_CONFLICT = "execution_value_conflict"
    SAFETY_CRITICAL_CONFLICT = "safety_critical_conflict"
    MISSING_EXECUTION_CRITICAL_VALUE = "missing_execution_critical_value"


class ReadinessReasonCode(str, Enum):
    INVALID_PROTOCOL = "invalid_protocol"
    SOURCE_TEXT_CROSS_CHECK_FAILED = "source_text_cross_check_failed"
    SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE = "source_text_cross_check_unavailable"
    NO_EXECUTABLE_STEPS = "no_executable_steps"
    UNSUPPORTED_CONDITIONAL_BRANCH = "unsupported_conditional_branch"
    UNSUPPORTED_FIXED_RANGE_REPETITION = "unsupported_fixed_range_repetition"
    UNSUPPORTED_REPEAT_UNTIL = "unsupported_repeat_until"
    UNSUPPORTED_PARALLEL_BACKGROUND_WORK = (
        "unsupported_parallel_background_work"
    )
    UNSUPPORTED_RECURRING_REMINDER = "unsupported_recurring_reminder"
    UNSUPPORTED_RECURRING_ACTION = "unsupported_recurring_action"
    UNSUPPORTED_REUSABLE_SUBPROCEDURE = "unsupported_reusable_subprocedure"
    UNRESOLVED_AMBIGUITY = "unresolved_ambiguity"
    UNRESOLVED_EXECUTION_VALUE_CONFLICT = (
        "unresolved_execution_value_conflict"
    )
    SAFETY_CRITICAL_CONFLICT = "safety_critical_conflict"
    NO_DECLARED_SAFETY_WARNINGS = "no_declared_safety_warnings"
    UNCONFIRMED_FIXED_REPETITION = "unconfirmed_fixed_repetition"
    MISSING_EXECUTION_CRITICAL_VALUE = "missing_execution_critical_value"


class BranchKind(str, Enum):
    ALTERNATIVE = "alternative"
    CONDITIONAL = "conditional"


class ConflictLevel(str, Enum):
    INFORMATIONAL = "informational"
    EXECUTION_VALUE = "execution_value"
    SAFETY_CRITICAL = "safety_critical"


@dataclass(frozen=True)
class SourceEvidence:
    """Where in the source a statement came from.

    ``evidence_segment_ids`` are canonical segment handles: server-computed
    identities for spans of text the server already owns.  A handle is a
    pointer into the document, not anything the provider wrote, so keeping it
    is not keeping provider content -- it is what makes the evidence
    re-openable later.  Without them a claim could be read back but the exact
    span it cited could not, which is how a hazard claim's basis became
    unrecoverable after the fact.

    Empty on statements assembled before handles were retained, and on
    hand-built records that never had one.
    """

    source_page_number: int
    source_excerpt: str
    location_detail: str | None = None
    evidence_segment_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProtocolMetadata:
    """Descriptive metadata plus a reference to Slice 1 byte identity."""

    pdf: ProtocolPdfExtraction
    title: str
    original_language: str
    authors: tuple[str, ...] = ()
    created_date: str | None = None
    modified_date: str | None = None
    publication_date: str | None = None
    version: str | None = None
    doi: str | None = None
    source_uri: str | None = None
    license: str | None = None
    source_status: str | None = None
    evidence: SourceEvidence | None = None

    @property
    def original_filename(self) -> str:
        return self.pdf.original_filename

    @property
    def file_checksum(self) -> str:
        """Return exact-byte identity, not trust, approval, or authority."""

        return self.pdf.sha256

    @property
    def media_type(self) -> str:
        return self.pdf.media_type

    @property
    def page_count(self) -> int:
        return self.pdf.page_count


@dataclass(frozen=True)
class ScientificValue:
    """Exact scientific source text with clearly secondary parsed fields."""

    source_text: str
    parsed_value: str | None = None
    normalized_unit: str | None = None


@dataclass(frozen=True)
class SourceStatement:
    statement_id: str
    source_text: str
    evidence: SourceEvidence


@dataclass(frozen=True)
class EstimatedDuration:
    source_text: str
    parsed_seconds: int | None = None


@dataclass(frozen=True)
class ProcessTimerSpecification:
    timer_id: str
    duration: ScientificValue | None
    evidence: SourceEvidence
    required_for_execution: bool = True


@dataclass(frozen=True)
class OneTimeReminder:
    reminder_id: str
    offset: ScientificValue | None
    message_source_text: str
    evidence: SourceEvidence
    required_for_execution: bool = True


@dataclass(frozen=True)
class RecurringReminder:
    reminder_id: str
    interval: ScientificValue | None
    message_source_text: str
    evidence: SourceEvidence
    required_for_execution: bool = True


@dataclass(frozen=True)
class ActualElapsedTime:
    source_text: str
    elapsed_seconds: int


@dataclass(frozen=True)
class BeforeStartPrerequisite:
    prerequisite_id: str
    source_text: str
    evidence: SourceEvidence
    conditions: tuple[SourceStatement, ...] = ()
    estimated_duration: EstimatedDuration | None = None


@dataclass(frozen=True)
class Material:
    material_id: str
    name_source_text: str
    evidence: SourceEvidence
    quantities: tuple[ScientificValue, ...] = ()
    conditions: tuple[SourceStatement, ...] = ()


@dataclass(frozen=True)
class Equipment:
    equipment_id: str
    name_source_text: str
    evidence: SourceEvidence
    settings: tuple[ScientificValue, ...] = ()


@dataclass(frozen=True)
class RequiredObservation:
    observation_id: str
    source_text: str
    evidence: SourceEvidence


@dataclass(frozen=True)
class MissingExecutionValue:
    value_id: str
    description: str
    evidence: SourceEvidence


@dataclass(frozen=True)
class DependencyTarget:
    step_id: str
    action_id: str | None = None


@dataclass(frozen=True)
class ProtocolSubAction:
    action_id: str
    instruction_source_text: str
    evidence: SourceEvidence
    quantities: tuple[ScientificValue, ...] = ()
    conditions: tuple[SourceStatement, ...] = ()
    dependencies: tuple[DependencyTarget, ...] = ()
    estimated_duration: EstimatedDuration | None = None
    process_timer: ProcessTimerSpecification | None = None
    reminders: tuple[OneTimeReminder, ...] = ()
    recurring_reminders: tuple[RecurringReminder, ...] = ()
    required_observations: tuple[RequiredObservation, ...] = ()
    expected_results: tuple[SourceStatement, ...] = ()
    notes: tuple[SourceStatement, ...] = ()
    tips: tuple[SourceStatement, ...] = ()
    warnings: tuple[SourceStatement, ...] = ()
    missing_execution_values: tuple[MissingExecutionValue, ...] = ()
    actual_elapsed_time: ActualElapsedTime | None = None


@dataclass(frozen=True)
class ProtocolSourceStep:
    step_id: str
    source_label: str
    instruction_source_text: str
    evidence: SourceEvidence
    sub_actions: tuple[ProtocolSubAction, ...] = ()
    dependencies: tuple[DependencyTarget, ...] = ()
    expected_results: tuple[SourceStatement, ...] = ()
    notes: tuple[SourceStatement, ...] = ()
    tips: tuple[SourceStatement, ...] = ()
    warnings: tuple[SourceStatement, ...] = ()


@dataclass(frozen=True)
class ProtocolSection:
    section_id: str
    title_source_text: str
    evidence: SourceEvidence
    steps: tuple[ProtocolSourceStep, ...] = ()


@dataclass(frozen=True)
class ConditionalBranch:
    branch_id: str
    kind: BranchKind
    condition_source_text: str
    branch_step_ids: tuple[str, ...]
    evidence: SourceEvidence
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class FixedRangeRepetition:
    repetition_id: str
    start_step_id: str
    end_step_id: str
    range_source_text: str
    evidence: SourceEvidence
    repeat_count: int | None = None
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class RepeatUntil:
    repetition_id: str
    condition_source_text: str
    repeated_step_ids: tuple[str, ...]
    evidence: SourceEvidence
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class ParallelWork:
    parallel_id: str
    concurrent_step_ids: tuple[str, ...]
    source_text: str
    evidence: SourceEvidence
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class RecurringAction:
    recurring_action_id: str
    target: DependencyTarget
    interval: ScientificValue
    source_text: str
    evidence: SourceEvidence
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class ReusableSubprocedure:
    subprocedure_id: str
    member_step_ids: tuple[str, ...]
    source_text: str
    evidence: SourceEvidence
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class SourceAmbiguity:
    ambiguity_id: str
    source_text: str
    evidence: SourceEvidence
    resolved: bool = False
    resolution_source_text: str | None = None
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class ProtocolConflict:
    conflict_id: str
    level: ConflictLevel
    source_text: str
    evidence: SourceEvidence
    resolved: bool = False
    resolution_source_text: str | None = None
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


WorkflowConstruct: TypeAlias = (
    ConditionalBranch
    | FixedRangeRepetition
    | RepeatUntil
    | ParallelWork
    | RecurringAction
    | ReusableSubprocedure
    | SourceAmbiguity
    | ProtocolConflict
)


@dataclass(frozen=True)
class ExperimentProtocol:
    protocol_id: str
    metadata: ProtocolMetadata
    before_start: tuple[BeforeStartPrerequisite, ...] = ()
    materials: tuple[Material, ...] = ()
    equipment: tuple[Equipment, ...] = ()
    sections: tuple[ProtocolSection, ...] = ()
    constructs: tuple[WorkflowConstruct, ...] = ()
    description: SourceStatement | None = None


@dataclass(frozen=True)
class DetectedFeature:
    code: FeatureCode
    construct_id: str
    evidence: SourceEvidence
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class CapabilityPolicy:
    profile_id: str
    supported_features: frozenset[FeatureCode]


P1_CAPABILITY_POLICY = CapabilityPolicy(
    profile_id="p1-conservative",
    supported_features=frozenset(
        {
            FeatureCode.FIXED_RANGE_REPETITION,
            FeatureCode.INFORMATIONAL_DIFFERENCE,
        }
    ),
)


@dataclass(frozen=True)
class ReadinessReason:
    code: ReadinessReasonCode
    message: str
    evidence: SourceEvidence | None = None
    feature_code: FeatureCode | None = None
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class ReadinessAssessment:
    status: ReadinessStatus
    label: str
    reasons: tuple[ReadinessReason, ...]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(reason.code.value for reason in self.reasons)


def _error(
    code: ProtocolValidationCode,
    message: str,
    location: str | None = None,
) -> ProtocolValidationError:
    return ProtocolValidationError(code, message, location=location)


def _identifier(value: object, location: str) -> str:
    if not isinstance(value, str) or not _STABLE_IDENTIFIER.fullmatch(value):
        raise _error(
            ProtocolValidationCode.INVALID_IDENTIFIER,
            "must be a non-empty stable identifier",
            location,
        )
    return value


def _text(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(
            ProtocolValidationCode.INVALID_TEXT,
            "must preserve non-empty source text",
            location,
        )
    return value


def _optional_text(value: object, location: str) -> None:
    if value is not None:
        _text(value, location)


def _validate_pdf(pdf: ProtocolPdfExtraction) -> None:
    location = "metadata.pdf"
    if (
        not isinstance(pdf, ProtocolPdfExtraction)
    ):
        raise _error(
            ProtocolValidationCode.INVALID_FILE_IDENTITY,
            "Slice 1 PDF identity is malformed",
            location,
        )
    if (
        not isinstance(pdf.original_filename, str)
        or not pdf.original_filename.strip()
        or not isinstance(pdf.byte_size, int)
        or isinstance(pdf.byte_size, bool)
        or pdf.byte_size < 0
        or not _LOWERCASE_SHA256.fullmatch(pdf.sha256)
        or pdf.media_type != PDF_MEDIA_TYPE
        or not isinstance(pdf.page_count, int)
        or isinstance(pdf.page_count, bool)
        or pdf.page_count <= 0
        or len(pdf.pages) != pdf.page_count
    ):
        raise _error(
            ProtocolValidationCode.INVALID_FILE_IDENTITY,
            "Slice 1 PDF identity is malformed",
            location,
        )
    expected_pages = list(range(1, pdf.page_count + 1))
    actual_pages = [page.source_page_number for page in pdf.pages]
    if actual_pages != expected_pages:
        raise _error(
            ProtocolValidationCode.INVALID_FILE_IDENTITY,
            "Slice 1 page mapping is not one-based and contiguous",
            location,
        )


def _validate_evidence(
    evidence: SourceEvidence,
    pdf: ProtocolPdfExtraction,
    location: str,
) -> None:
    if (
        not isinstance(evidence.source_page_number, int)
        or isinstance(evidence.source_page_number, bool)
        or evidence.source_page_number <= 0
        or evidence.source_page_number > pdf.page_count
    ):
        raise _error(
            ProtocolValidationCode.INVALID_SOURCE_PAGE,
            "source page is outside the document page range",
            location,
        )
    excerpt = _text(evidence.source_excerpt, f"{location}.source_excerpt")
    _optional_text(evidence.location_detail, f"{location}.location_detail")
    page_text = pdf.pages[evidence.source_page_number - 1].text
    if excerpt not in page_text:
        raise _error(
            ProtocolValidationCode.SOURCE_EXCERPT_MISMATCH,
            "source excerpt is not present on the referenced page",
            location,
        )


def _validate_scientific_value(value: ScientificValue, location: str) -> None:
    _text(value.source_text, f"{location}.source_text")
    _optional_text(value.parsed_value, f"{location}.parsed_value")
    _optional_text(value.normalized_unit, f"{location}.normalized_unit")


def _validate_estimated_duration(
    duration: EstimatedDuration,
    location: str,
) -> None:
    _text(duration.source_text, f"{location}.source_text")
    if (
        duration.parsed_seconds is not None
        and (
            not isinstance(duration.parsed_seconds, int)
            or isinstance(duration.parsed_seconds, bool)
            or duration.parsed_seconds <= 0
        )
    ):
        raise _error(
            ProtocolValidationCode.INVALID_TIME_VALUE,
            "parsed estimated duration must be positive",
            location,
        )


def _validate_actual_elapsed(value: ActualElapsedTime, location: str) -> None:
    _text(value.source_text, f"{location}.source_text")
    if (
        not isinstance(value.elapsed_seconds, int)
        or isinstance(value.elapsed_seconds, bool)
        or value.elapsed_seconds < 0
    ):
        raise _error(
            ProtocolValidationCode.INVALID_TIME_VALUE,
            "actual elapsed seconds must be non-negative",
            location,
        )


def _validate_statement(
    statement: SourceStatement,
    pdf: ProtocolPdfExtraction,
    location: str,
) -> None:
    _identifier(statement.statement_id, f"{location}.statement_id")
    _text(statement.source_text, f"{location}.source_text")
    _validate_evidence(statement.evidence, pdf, f"{location}.evidence")


def _validate_statement_group(
    statements: tuple[SourceStatement, ...],
    pdf: ProtocolPdfExtraction,
    location: str,
) -> None:
    seen: set[str] = set()
    for index, statement in enumerate(statements):
        item_location = f"{location}[{index}]"
        _validate_statement(statement, pdf, item_location)
        if statement.statement_id in seen:
            raise _error(
                ProtocolValidationCode.INVALID_IDENTIFIER,
                "statement identifier is duplicated in its scope",
                item_location,
            )
        seen.add(statement.statement_id)


def _validate_observations(
    observations: tuple[RequiredObservation, ...],
    pdf: ProtocolPdfExtraction,
    location: str,
) -> None:
    seen: set[str] = set()
    for index, observation in enumerate(observations):
        item_location = f"{location}[{index}]"
        _identifier(observation.observation_id, f"{item_location}.observation_id")
        if observation.observation_id in seen:
            raise _error(
                ProtocolValidationCode.INVALID_IDENTIFIER,
                "observation identifier is duplicated in its scope",
                item_location,
            )
        seen.add(observation.observation_id)
        _text(observation.source_text, f"{item_location}.source_text")
        _validate_evidence(
            observation.evidence,
            pdf,
            f"{item_location}.evidence",
        )


def _validate_reminder(
    reminder: OneTimeReminder | RecurringReminder,
    pdf: ProtocolPdfExtraction,
    location: str,
) -> None:
    _identifier(reminder.reminder_id, f"{location}.reminder_id")
    _text(reminder.message_source_text, f"{location}.message_source_text")
    _validate_evidence(reminder.evidence, pdf, f"{location}.evidence")
    value = reminder.offset if isinstance(reminder, OneTimeReminder) else reminder.interval
    if value is not None:
        _validate_scientific_value(value, f"{location}.time_value")


def _node_key(target: DependencyTarget) -> tuple[str, str, str]:
    if target.action_id is None:
        return ("step", target.step_id, "")
    return ("action", target.step_id, target.action_id)


def _validate_target(
    target: DependencyTarget,
    step_locations: dict[str, str],
    action_locations: dict[tuple[str, str], str],
    location: str,
    *,
    dependency: bool,
) -> tuple[str, str, str]:
    _identifier(target.step_id, f"{location}.step_id")
    if target.action_id is None:
        exists = target.step_id in step_locations
    else:
        _identifier(target.action_id, f"{location}.action_id")
        exists = (target.step_id, target.action_id) in action_locations
    if not exists:
        code = (
            ProtocolValidationCode.DANGLING_DEPENDENCY
            if dependency
            else ProtocolValidationCode.DANGLING_REFERENCE
        )
        raise _error(code, "target does not exist", location)
    return _node_key(target)


def _construct_identity(construct: WorkflowConstruct) -> str:
    if isinstance(construct, ConditionalBranch):
        return construct.branch_id
    if isinstance(construct, (FixedRangeRepetition, RepeatUntil)):
        return construct.repetition_id
    if isinstance(construct, ParallelWork):
        return construct.parallel_id
    if isinstance(construct, RecurringAction):
        return construct.recurring_action_id
    if isinstance(construct, ReusableSubprocedure):
        return construct.subprocedure_id
    if isinstance(construct, SourceAmbiguity):
        return construct.ambiguity_id
    return construct.conflict_id


def _construct_location(
    construct: WorkflowConstruct,
) -> tuple[str | None, str | None, str | None]:
    return construct.section_id, construct.step_id, construct.action_id


def _validate_related_location(
    construct: WorkflowConstruct,
    section_locations: dict[str, str],
    step_sections: dict[str, str],
    action_locations: dict[tuple[str, str], str],
    location: str,
) -> None:
    section_id, step_id, action_id = _construct_location(construct)
    if section_id is not None:
        _identifier(section_id, f"{location}.section_id")
        if section_id not in section_locations:
            raise _error(
                ProtocolValidationCode.DANGLING_REFERENCE,
                "related section does not exist",
                location,
            )
    if step_id is not None:
        _identifier(step_id, f"{location}.step_id")
        if step_id not in step_sections:
            raise _error(
                ProtocolValidationCode.DANGLING_REFERENCE,
                "related step does not exist",
                location,
            )
        if section_id is not None and step_sections[step_id] != section_id:
            raise _error(
                ProtocolValidationCode.DANGLING_REFERENCE,
                "related step is not in the related section",
                location,
            )
    if action_id is not None:
        _identifier(action_id, f"{location}.action_id")
        if step_id is None or (step_id, action_id) not in action_locations:
            raise _error(
                ProtocolValidationCode.DANGLING_REFERENCE,
                "related sub-action does not exist",
                location,
            )


def _validate_construct(
    construct: WorkflowConstruct,
    pdf: ProtocolPdfExtraction,
    section_locations: dict[str, str],
    step_locations: dict[str, str],
    step_sections: dict[str, str],
    action_locations: dict[tuple[str, str], str],
    location: str,
) -> None:
    construct_id = _construct_identity(construct)
    _identifier(construct_id, f"{location}.construct_id")
    _validate_evidence(construct.evidence, pdf, f"{location}.evidence")
    _validate_related_location(
        construct,
        section_locations,
        step_sections,
        action_locations,
        location,
    )

    if isinstance(construct, ConditionalBranch):
        if not isinstance(construct.kind, BranchKind):
            raise _error(
                ProtocolValidationCode.INVALID_TEXT,
                "branch kind is unsupported",
                location,
            )
        _text(
            construct.condition_source_text,
            f"{location}.condition_source_text",
        )
        if not construct.branch_step_ids:
            raise _error(
                ProtocolValidationCode.DANGLING_REFERENCE,
                "conditional branch must retain at least one branch target",
                location,
            )
        for target in construct.branch_step_ids:
            _validate_target(
                DependencyTarget(target),
                step_locations,
                action_locations,
                f"{location}.branch_step_ids",
                dependency=False,
            )
    elif isinstance(construct, FixedRangeRepetition):
        _text(construct.range_source_text, f"{location}.range_source_text")
        for target in (construct.start_step_id, construct.end_step_id):
            _validate_target(
                DependencyTarget(target),
                step_locations,
                action_locations,
                location,
                dependency=False,
            )
        if (
            construct.repeat_count is not None
            and (
                not isinstance(construct.repeat_count, int)
                or isinstance(construct.repeat_count, bool)
                or construct.repeat_count <= 0
            )
        ):
            raise _error(
                ProtocolValidationCode.INVALID_TIME_VALUE,
                "repeat count must be positive when parsed",
                location,
            )
    elif isinstance(construct, RepeatUntil):
        _text(
            construct.condition_source_text,
            f"{location}.condition_source_text",
        )
        if not construct.repeated_step_ids:
            raise _error(
                ProtocolValidationCode.DANGLING_REFERENCE,
                "repeat-until must retain repeated step targets",
                location,
            )
        for target in construct.repeated_step_ids:
            _validate_target(
                DependencyTarget(target),
                step_locations,
                action_locations,
                location,
                dependency=False,
            )
    elif isinstance(construct, ParallelWork):
        _text(construct.source_text, f"{location}.source_text")
        if len(construct.concurrent_step_ids) < 2:
            raise _error(
                ProtocolValidationCode.DANGLING_REFERENCE,
                "parallel work must retain at least two concurrent targets",
                location,
            )
        for target in construct.concurrent_step_ids:
            _validate_target(
                DependencyTarget(target),
                step_locations,
                action_locations,
                location,
                dependency=False,
            )
    elif isinstance(construct, RecurringAction):
        _text(construct.source_text, f"{location}.source_text")
        _validate_scientific_value(
            construct.interval,
            f"{location}.interval",
        )
        _validate_target(
            construct.target,
            step_locations,
            action_locations,
            f"{location}.target",
            dependency=False,
        )
    elif isinstance(construct, ReusableSubprocedure):
        _text(construct.source_text, f"{location}.source_text")
        if not construct.member_step_ids:
            raise _error(
                ProtocolValidationCode.DANGLING_REFERENCE,
                "reusable subprocedure must retain member steps",
                location,
            )
        for target in construct.member_step_ids:
            _validate_target(
                DependencyTarget(target),
                step_locations,
                action_locations,
                location,
                dependency=False,
            )
    elif isinstance(construct, SourceAmbiguity):
        _text(construct.source_text, f"{location}.source_text")
        _optional_text(
            construct.resolution_source_text,
            f"{location}.resolution_source_text",
        )
        if construct.resolved and construct.resolution_source_text is None:
            raise _error(
                ProtocolValidationCode.INVALID_TEXT,
                "resolved ambiguity must retain its resolution text",
                location,
            )
    else:
        if not isinstance(construct.level, ConflictLevel):
            raise _error(
                ProtocolValidationCode.INVALID_TEXT,
                "conflict level is unsupported",
                location,
            )
        _text(construct.source_text, f"{location}.source_text")
        _optional_text(
            construct.resolution_source_text,
            f"{location}.resolution_source_text",
        )
        if construct.resolved and construct.resolution_source_text is None:
            raise _error(
                ProtocolValidationCode.INVALID_TEXT,
                "resolved conflict must retain its resolution text",
                location,
            )


def _check_dependency_cycles(
    edges: dict[tuple[str, str, str], set[tuple[str, str, str]]],
) -> None:
    state: dict[tuple[str, str, str], int] = {}

    def visit(node: tuple[str, str, str]) -> None:
        node_state = state.get(node, 0)
        if node_state == 1:
            raise _error(
                ProtocolValidationCode.DEPENDENCY_CYCLE,
                "workflow dependencies contain a cycle",
                "dependencies",
            )
        if node_state == 2:
            return
        state[node] = 1
        for target in sorted(edges[node]):
            visit(target)
        state[node] = 2

    for node in sorted(edges):
        visit(node)


def validate_protocol(protocol: ExperimentProtocol) -> ExperimentProtocol:
    """Validate all evidence, identifiers, references, and dependency graphs."""

    _identifier(protocol.protocol_id, "protocol_id")
    _validate_pdf(protocol.metadata.pdf)
    _text(protocol.metadata.title, "metadata.title")
    _text(protocol.metadata.original_language, "metadata.original_language")
    for index, author in enumerate(protocol.metadata.authors):
        _text(author, f"metadata.authors[{index}]")
    for field_name in (
        "created_date",
        "modified_date",
        "publication_date",
        "version",
        "doi",
        "source_uri",
        "license",
        "source_status",
    ):
        _optional_text(
            getattr(protocol.metadata, field_name),
            f"metadata.{field_name}",
        )
    if protocol.metadata.evidence is not None:
        _validate_evidence(
            protocol.metadata.evidence,
            protocol.metadata.pdf,
            "metadata.evidence",
        )
    if protocol.description is not None:
        _validate_statement(
            protocol.description,
            protocol.metadata.pdf,
            "description",
        )

    for index, prerequisite in enumerate(protocol.before_start):
        location = f"before_start[{index}]"
        _identifier(prerequisite.prerequisite_id, f"{location}.prerequisite_id")
        _text(prerequisite.source_text, f"{location}.source_text")
        _validate_evidence(prerequisite.evidence, protocol.metadata.pdf, f"{location}.evidence")
        _validate_statement_group(
            prerequisite.conditions,
            protocol.metadata.pdf,
            f"{location}.conditions",
        )
        if prerequisite.estimated_duration is not None:
            _validate_estimated_duration(
                prerequisite.estimated_duration,
                f"{location}.estimated_duration",
            )

    for index, material in enumerate(protocol.materials):
        location = f"materials[{index}]"
        _identifier(material.material_id, f"{location}.material_id")
        _text(material.name_source_text, f"{location}.name_source_text")
        _validate_evidence(material.evidence, protocol.metadata.pdf, f"{location}.evidence")
        for value_index, value in enumerate(material.quantities):
            _validate_scientific_value(value, f"{location}.quantities[{value_index}]")
        _validate_statement_group(
            material.conditions,
            protocol.metadata.pdf,
            f"{location}.conditions",
        )

    for index, item in enumerate(protocol.equipment):
        location = f"equipment[{index}]"
        _identifier(item.equipment_id, f"{location}.equipment_id")
        _text(item.name_source_text, f"{location}.name_source_text")
        _validate_evidence(item.evidence, protocol.metadata.pdf, f"{location}.evidence")
        for value_index, value in enumerate(item.settings):
            _validate_scientific_value(value, f"{location}.settings[{value_index}]")

    section_locations: dict[str, str] = {}
    step_locations: dict[str, str] = {}
    step_sections: dict[str, str] = {}
    action_locations: dict[tuple[str, str], str] = {}
    edges: dict[tuple[str, str, str], set[tuple[str, str, str]]] = {}
    step_dependencies: list[
        tuple[tuple[str, str, str], DependencyTarget, str]
    ] = []

    for section_index, section in enumerate(protocol.sections):
        section_location = f"sections[{section_index}]"
        _identifier(section.section_id, f"{section_location}.section_id")
        if section.section_id in section_locations:
            raise _error(
                ProtocolValidationCode.DUPLICATE_SECTION_ID,
                "section identifier is duplicated",
                section_location,
            )
        section_locations[section.section_id] = section_location
        _text(section.title_source_text, f"{section_location}.title_source_text")
        _validate_evidence(
            section.evidence,
            protocol.metadata.pdf,
            f"{section_location}.evidence",
        )

        for step_index, step in enumerate(section.steps):
            step_location = f"{section_location}.steps[{step_index}]"
            _identifier(step.step_id, f"{step_location}.step_id")
            if step.step_id in step_locations:
                raise _error(
                    ProtocolValidationCode.DUPLICATE_STEP_ID,
                    "source-step identifier is duplicated",
                    step_location,
                )
            step_locations[step.step_id] = step_location
            step_sections[step.step_id] = section.section_id
            if not isinstance(step.source_label, str) or not step.source_label.strip():
                raise _error(
                    ProtocolValidationCode.MISSING_SOURCE_LABEL,
                    "source step must preserve its original number or label",
                    step_location,
                )
            _text(
                step.instruction_source_text,
                f"{step_location}.instruction_source_text",
            )
            _validate_evidence(
                step.evidence,
                protocol.metadata.pdf,
                f"{step_location}.evidence",
            )
            for group_name in ("expected_results", "notes", "tips", "warnings"):
                _validate_statement_group(
                    getattr(step, group_name),
                    protocol.metadata.pdf,
                    f"{step_location}.{group_name}",
                )
            step_node = ("step", step.step_id, "")
            edges[step_node] = set()
            for dependency_index, dependency in enumerate(step.dependencies):
                step_dependencies.append(
                    (
                        step_node,
                        dependency,
                        f"{step_location}.dependencies[{dependency_index}]",
                    )
                )

            action_ids: set[str] = set()
            for action_index, action in enumerate(step.sub_actions):
                action_location = f"{step_location}.sub_actions[{action_index}]"
                _identifier(action.action_id, f"{action_location}.action_id")
                if action.action_id in action_ids:
                    raise _error(
                        ProtocolValidationCode.DUPLICATE_ACTION_ID,
                        "sub-action identifier is duplicated within its source step",
                        action_location,
                    )
                action_ids.add(action.action_id)
                action_locations[(step.step_id, action.action_id)] = action_location
                _text(
                    action.instruction_source_text,
                    f"{action_location}.instruction_source_text",
                )
                _validate_evidence(
                    action.evidence,
                    protocol.metadata.pdf,
                    f"{action_location}.evidence",
                )
                for value_index, value in enumerate(action.quantities):
                    _validate_scientific_value(
                        value,
                        f"{action_location}.quantities[{value_index}]",
                    )
                _validate_statement_group(
                    action.conditions,
                    protocol.metadata.pdf,
                    f"{action_location}.conditions",
                )
                if action.estimated_duration is not None:
                    _validate_estimated_duration(
                        action.estimated_duration,
                        f"{action_location}.estimated_duration",
                    )
                if action.process_timer is not None:
                    timer = action.process_timer
                    _identifier(timer.timer_id, f"{action_location}.process_timer.timer_id")
                    _validate_evidence(
                        timer.evidence,
                        protocol.metadata.pdf,
                        f"{action_location}.process_timer.evidence",
                    )
                    if timer.duration is not None:
                        _validate_scientific_value(
                            timer.duration,
                            f"{action_location}.process_timer.duration",
                        )
                reminder_ids: set[str] = set()
                for group_name in ("reminders", "recurring_reminders"):
                    for reminder_index, reminder in enumerate(
                        getattr(action, group_name)
                    ):
                        reminder_location = (
                            f"{action_location}.{group_name}[{reminder_index}]"
                        )
                        _validate_reminder(
                            reminder,
                            protocol.metadata.pdf,
                            reminder_location,
                        )
                        if reminder.reminder_id in reminder_ids:
                            raise _error(
                                ProtocolValidationCode.INVALID_IDENTIFIER,
                                "reminder identifier is duplicated in its action",
                                reminder_location,
                            )
                        reminder_ids.add(reminder.reminder_id)
                _validate_observations(
                    action.required_observations,
                    protocol.metadata.pdf,
                    f"{action_location}.required_observations",
                )
                for group_name in ("expected_results", "notes", "tips", "warnings"):
                    _validate_statement_group(
                        getattr(action, group_name),
                        protocol.metadata.pdf,
                        f"{action_location}.{group_name}",
                    )
                missing_ids: set[str] = set()
                for missing_index, missing in enumerate(
                    action.missing_execution_values
                ):
                    missing_location = (
                        f"{action_location}.missing_execution_values[{missing_index}]"
                    )
                    _identifier(missing.value_id, f"{missing_location}.value_id")
                    if missing.value_id in missing_ids:
                        raise _error(
                            ProtocolValidationCode.INVALID_IDENTIFIER,
                            "missing-value identifier is duplicated in its action",
                            missing_location,
                        )
                    missing_ids.add(missing.value_id)
                    _text(missing.description, f"{missing_location}.description")
                    _validate_evidence(
                        missing.evidence,
                        protocol.metadata.pdf,
                        f"{missing_location}.evidence",
                    )
                if action.actual_elapsed_time is not None:
                    _validate_actual_elapsed(
                        action.actual_elapsed_time,
                        f"{action_location}.actual_elapsed_time",
                    )
                action_node = ("action", step.step_id, action.action_id)
                edges[action_node] = set()
                for dependency_index, dependency in enumerate(action.dependencies):
                    step_dependencies.append(
                        (
                            action_node,
                            dependency,
                            f"{action_location}.dependencies[{dependency_index}]",
                        )
                    )

    for source_node, target, location in step_dependencies:
        edges[source_node].add(
            _validate_target(
                target,
                step_locations,
                action_locations,
                location,
                dependency=True,
            )
        )
    _check_dependency_cycles(edges)

    construct_ids: set[str] = set()
    for construct_index, construct in enumerate(protocol.constructs):
        location = f"constructs[{construct_index}]"
        construct_id = _construct_identity(construct)
        if construct_id in construct_ids:
            raise _error(
                ProtocolValidationCode.DUPLICATE_CONSTRUCT_ID,
                "workflow construct identifier is duplicated",
                location,
            )
        construct_ids.add(construct_id)
        _validate_construct(
            construct,
            protocol.metadata.pdf,
            section_locations,
            step_locations,
            step_sections,
            action_locations,
            location,
        )
    return protocol


_FEATURE_ORDER = {
    code: index
    for index, code in enumerate(
        (
            FeatureCode.CONDITIONAL_BRANCH,
            FeatureCode.FIXED_RANGE_REPETITION,
            FeatureCode.REPEAT_UNTIL,
            FeatureCode.PARALLEL_BACKGROUND_WORK,
            FeatureCode.RECURRING_REMINDER,
            FeatureCode.RECURRING_ACTION,
            FeatureCode.REUSABLE_SUBPROCEDURE,
            FeatureCode.UNRESOLVED_AMBIGUITY,
            FeatureCode.INFORMATIONAL_DIFFERENCE,
            FeatureCode.EXECUTION_VALUE_CONFLICT,
            FeatureCode.SAFETY_CRITICAL_CONFLICT,
            FeatureCode.MISSING_EXECUTION_CRITICAL_VALUE,
        )
    )
}


def _feature(
    code: FeatureCode,
    construct_id: str,
    evidence: SourceEvidence,
    *,
    section_id: str | None = None,
    step_id: str | None = None,
    action_id: str | None = None,
) -> DetectedFeature:
    return DetectedFeature(
        code=code,
        construct_id=construct_id,
        evidence=evidence,
        section_id=section_id,
        step_id=step_id,
        action_id=action_id,
    )


def _detect_features(protocol: ExperimentProtocol) -> tuple[DetectedFeature, ...]:
    detected: list[DetectedFeature] = []
    for section in protocol.sections:
        for step in section.steps:
            for action in step.sub_actions:
                if (
                    action.process_timer is not None
                    and action.process_timer.required_for_execution
                    and action.process_timer.duration is None
                ):
                    detected.append(
                        _feature(
                            FeatureCode.MISSING_EXECUTION_CRITICAL_VALUE,
                            action.process_timer.timer_id,
                            action.process_timer.evidence,
                            section_id=section.section_id,
                            step_id=step.step_id,
                            action_id=action.action_id,
                        )
                    )
                for reminder in action.reminders:
                    if reminder.required_for_execution and reminder.offset is None:
                        detected.append(
                            _feature(
                                FeatureCode.MISSING_EXECUTION_CRITICAL_VALUE,
                                reminder.reminder_id,
                                reminder.evidence,
                                section_id=section.section_id,
                                step_id=step.step_id,
                                action_id=action.action_id,
                            )
                        )
                for reminder in action.recurring_reminders:
                    detected.append(
                        _feature(
                            FeatureCode.RECURRING_REMINDER,
                            reminder.reminder_id,
                            reminder.evidence,
                            section_id=section.section_id,
                            step_id=step.step_id,
                            action_id=action.action_id,
                        )
                    )
                    if reminder.required_for_execution and reminder.interval is None:
                        detected.append(
                            _feature(
                                FeatureCode.MISSING_EXECUTION_CRITICAL_VALUE,
                                reminder.reminder_id,
                                reminder.evidence,
                                section_id=section.section_id,
                                step_id=step.step_id,
                                action_id=action.action_id,
                            )
                        )
                for missing in action.missing_execution_values:
                    detected.append(
                        _feature(
                            FeatureCode.MISSING_EXECUTION_CRITICAL_VALUE,
                            missing.value_id,
                            missing.evidence,
                            section_id=section.section_id,
                            step_id=step.step_id,
                            action_id=action.action_id,
                        )
                    )

    for construct in protocol.constructs:
        construct_id = _construct_identity(construct)
        section_id, step_id, action_id = _construct_location(construct)
        if isinstance(construct, ConditionalBranch):
            code = FeatureCode.CONDITIONAL_BRANCH
        elif isinstance(construct, FixedRangeRepetition):
            code = FeatureCode.FIXED_RANGE_REPETITION
        elif isinstance(construct, RepeatUntil):
            code = FeatureCode.REPEAT_UNTIL
        elif isinstance(construct, ParallelWork):
            code = FeatureCode.PARALLEL_BACKGROUND_WORK
        elif isinstance(construct, RecurringAction):
            code = FeatureCode.RECURRING_ACTION
        elif isinstance(construct, ReusableSubprocedure):
            code = FeatureCode.REUSABLE_SUBPROCEDURE
        elif isinstance(construct, SourceAmbiguity):
            if construct.resolved:
                continue
            code = FeatureCode.UNRESOLVED_AMBIGUITY
        else:
            if construct.level is ConflictLevel.INFORMATIONAL:
                code = FeatureCode.INFORMATIONAL_DIFFERENCE
            elif construct.level is ConflictLevel.EXECUTION_VALUE:
                if construct.resolved:
                    continue
                code = FeatureCode.EXECUTION_VALUE_CONFLICT
            else:
                code = FeatureCode.SAFETY_CRITICAL_CONFLICT
        detected.append(
            _feature(
                code,
                construct_id,
                construct.evidence,
                section_id=section_id,
                step_id=step_id,
                action_id=action_id,
            )
        )

    return tuple(
        sorted(
            detected,
            key=lambda item: (
                _FEATURE_ORDER[item.code],
                item.section_id or "",
                item.step_id or "",
                item.action_id or "",
                item.evidence.source_page_number,
                item.construct_id,
            ),
        )
    )


def detect_features(protocol: ExperimentProtocol) -> tuple[DetectedFeature, ...]:
    """Return stable structured-feature detections without reading raw PDF text."""

    validate_protocol(protocol)
    return _detect_features(protocol)


_UNCONDITIONAL_BLOCKERS = {
    FeatureCode.UNRESOLVED_AMBIGUITY: (
        ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
        "A source ambiguity remains unresolved.",
    ),
    FeatureCode.EXECUTION_VALUE_CONFLICT: (
        ReadinessReasonCode.UNRESOLVED_EXECUTION_VALUE_CONFLICT,
        "An execution-value conflict requires researcher resolution.",
    ),
    FeatureCode.SAFETY_CRITICAL_CONFLICT: (
        ReadinessReasonCode.SAFETY_CRITICAL_CONFLICT,
        "A safety-critical conflict blocks execution readiness.",
    ),
    FeatureCode.MISSING_EXECUTION_CRITICAL_VALUE: (
        ReadinessReasonCode.MISSING_EXECUTION_CRITICAL_VALUE,
        "An execution-critical value is missing from the represented source.",
    ),
}
_UNSUPPORTED_REASONS = {
    FeatureCode.CONDITIONAL_BRANCH: (
        ReadinessReasonCode.UNSUPPORTED_CONDITIONAL_BRANCH,
        "Conditional or alternative branching is not executable by this capability profile.",
    ),
    FeatureCode.FIXED_RANGE_REPETITION: (
        ReadinessReasonCode.UNSUPPORTED_FIXED_RANGE_REPETITION,
        "Fixed-range repetition is not executable by this capability profile.",
    ),
    FeatureCode.REPEAT_UNTIL: (
        ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL,
        "Repeat-until execution is not supported by this capability profile.",
    ),
    FeatureCode.PARALLEL_BACKGROUND_WORK: (
        ReadinessReasonCode.UNSUPPORTED_PARALLEL_BACKGROUND_WORK,
        "Parallel or background execution is not supported by this capability profile.",
    ),
    FeatureCode.RECURRING_REMINDER: (
        ReadinessReasonCode.UNSUPPORTED_RECURRING_REMINDER,
        "Recurring reminders are not supported by this capability profile.",
    ),
    FeatureCode.RECURRING_ACTION: (
        ReadinessReasonCode.UNSUPPORTED_RECURRING_ACTION,
        "Recurring actions are not supported by this capability profile.",
    ),
    FeatureCode.REUSABLE_SUBPROCEDURE: (
        ReadinessReasonCode.UNSUPPORTED_REUSABLE_SUBPROCEDURE,
        "Reusable subprocedure execution is not supported by this capability profile.",
    ),
}
_REASON_ORDER = {
    code: index
    for index, code in enumerate(
        (
            ReadinessReasonCode.INVALID_PROTOCOL,
            ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_FAILED,
            ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE,
            ReadinessReasonCode.NO_EXECUTABLE_STEPS,
            ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
            ReadinessReasonCode.MISSING_EXECUTION_CRITICAL_VALUE,
            ReadinessReasonCode.UNRESOLVED_EXECUTION_VALUE_CONFLICT,
            ReadinessReasonCode.SAFETY_CRITICAL_CONFLICT,
            ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS,
            ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION,
            ReadinessReasonCode.UNSUPPORTED_CONDITIONAL_BRANCH,
            ReadinessReasonCode.UNSUPPORTED_FIXED_RANGE_REPETITION,
            ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL,
            ReadinessReasonCode.UNSUPPORTED_PARALLEL_BACKGROUND_WORK,
            ReadinessReasonCode.UNSUPPORTED_RECURRING_REMINDER,
            ReadinessReasonCode.UNSUPPORTED_RECURRING_ACTION,
            ReadinessReasonCode.UNSUPPORTED_REUSABLE_SUBPROCEDURE,
        )
    )
}


def declared_safety_warning_count(protocol: ExperimentProtocol) -> int:
    """Count safety warnings this Protocol would surface during execution.

    Only step- and action-attached warnings count.  A hazard that reaches the
    domain without attaching to a step is never read out at the moment it
    matters, so it does not discharge the execution-time safety obligation.

    This counts *our own extracted output*.  It deliberately never inspects the
    source document for hazard wording: the question is what this Protocol
    declares, not whether some phrase appears in the PDF.

    It is reported for review and no longer clears any gate.  A count is a
    record that the provider called something a hazard, which is not evidence
    that the document declares one, so it cannot stand in for a reviewer -- see
    the NO_DECLARED_SAFETY_WARNINGS reason in ``assess_readiness``.
    """

    total = 0
    for section in protocol.sections:
        for step in section.steps:
            total += len(step.warnings)
            for action in step.sub_actions:
                total += len(action.warnings)
    return total


def assess_readiness(
    protocol: ExperimentProtocol,
    *,
    capability_policy: CapabilityPolicy = P1_CAPABILITY_POLICY,
) -> ReadinessAssessment:
    """Fail closed with two public outcomes and stable, sanitized reasons."""

    try:
        validate_protocol(protocol)
    except ProtocolValidationError:
        return ReadinessAssessment(
            status=ReadinessStatus.ANALYSIS_REQUIRED,
            label=ANALYSIS_REQUIRED_LABEL,
            reasons=(
                ReadinessReason(
                    code=ReadinessReasonCode.INVALID_PROTOCOL,
                    message="The structured Protocol is invalid and requires correction.",
                ),
            ),
        )

    reasons: list[ReadinessReason] = []
    # Evidence integrity comes first: if the extracted source text was not
    # confirmed by an independent engine, nothing derived from it is
    # trustworthy, however well formed it looks.
    verification = getattr(
        protocol.metadata.pdf, "text_verification", None
    )
    if verification is TextVerification.MISMATCH:
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_FAILED,
                message=(
                    "Extracted source text disagreed with an independent "
                    "extraction engine and cannot support execution."
                ),
            )
        )
    elif verification is TextVerification.COMPARATOR_UNAVAILABLE:
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE,
                message=(
                    "Extracted source text was not cross-checked because no "
                    "comparison engine was available. A reviewer must confirm "
                    "this source before execution."
                ),
            )
        )

    if not any(section.steps for section in protocol.sections):
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.NO_EXECUTABLE_STEPS,
                message="The structured Protocol contains no executable source steps.",
            )
        )

    if any(section.steps for section in protocol.sections):
        # The count used to clear this gate on its own, and that was the
        # gate's own defect: a warning in this Protocol is a warning the
        # provider produced, so a non-zero count records that a model called
        # something a hazard -- never that the document declares one.  One such
        # claim took readiness from analysis_required straight to
        # guidance_ready, so the model's output waived the human review this
        # gate exists to compel.  Measured on a real response, the only
        # warning-shaped text available was a note about analysis software
        # crashing: no chemical, thermal or physical hazard anywhere on the
        # pages concerned.
        #
        # The gate is now raised whenever there are steps to execute, and only
        # an audited human acknowledgement clears it.  The count is still
        # reported for review, as information rather than as authority, and no
        # hazard wording is inspected anywhere: what counts as a hazard remains
        # the provider's judgement, and whether this Protocol may execute on it
        # remains a person's.
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS,
                message=(
                    "A reviewer must confirm this Protocol's safety warnings "
                    "before execution. Extracted warnings are model judgement "
                    "and do not discharge the review by themselves."
                ),
            )
        )

    if any(
        isinstance(construct, FixedRangeRepetition)
        for construct in protocol.constructs
    ):
        # A bounded repetition is only as safe as the bound, and the bound is
        # the provider's reading of the source. Saying a conditional
        # repetition is a fixed one is the dangerous direction: the agent
        # would stop early and announce completion on a step whose own
        # condition is unmet, which is the false completion notice this
        # system must never produce. The mistake in the other direction only
        # makes it ask a person.
        #
        # So a fixed repetition does not execute on the model's word. A
        # reviewer confirms that it really is a fixed count and what that
        # count is, and until then this blocks. Nothing quietly downgrades an
        # unconfirmed fixed repetition to a conditional one either: that would
        # be another guess.
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION,
                message=(
                    "A reviewer must confirm each fixed repetition's count "
                    "before execution. A declared count is model judgement "
                    "and does not discharge the review by itself."
                ),
            )
        )

    for feature in _detect_features(protocol):
        if feature.code is FeatureCode.INFORMATIONAL_DIFFERENCE:
            continue
        if feature.code in _UNCONDITIONAL_BLOCKERS:
            reason_code, message = _UNCONDITIONAL_BLOCKERS[feature.code]
        elif feature.code not in capability_policy.supported_features:
            reason_code, message = _UNSUPPORTED_REASONS[feature.code]
        else:
            continue
        reasons.append(
            ReadinessReason(
                code=reason_code,
                message=message,
                evidence=feature.evidence,
                feature_code=feature.code,
                section_id=feature.section_id,
                step_id=feature.step_id,
                action_id=feature.action_id,
            )
        )

    reasons.sort(
        key=lambda reason: (
            _REASON_ORDER[reason.code],
            reason.section_id or "",
            reason.step_id or "",
            reason.action_id or "",
            reason.evidence.source_page_number if reason.evidence else 0,
            reason.feature_code.value if reason.feature_code else "",
        )
    )
    if reasons:
        return ReadinessAssessment(
            status=ReadinessStatus.ANALYSIS_REQUIRED,
            label=ANALYSIS_REQUIRED_LABEL,
            reasons=tuple(reasons),
        )
    return ReadinessAssessment(
        status=ReadinessStatus.GUIDANCE_READY,
        label=GUIDANCE_READY_LABEL,
        reasons=(),
    )
