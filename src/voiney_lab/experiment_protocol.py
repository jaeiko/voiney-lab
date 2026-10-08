"""Deterministic Experiment Protocol domain model and readiness assessment.

This module represents workflow semantics supplied by a later structured
extraction stage. It does not infer structure from PDF text. Protocol checksum
values retain the byte-identity-only meaning defined by Slice 1.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from voiney_lab.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    ProtocolPdfExtraction,
    # Retired; re-exported only because experiment_protocol_store decodes
    # analyses stored before 2026-10-02 that carry it. Nothing produces it.
    TextVerification,  # noqa: F401
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
    OPERATOR_DETERMINED_REPETITION = "operator_determined_repetition"
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
    # Retired with the engine cross-check on 2026-10-02 and never produced
    # now. Kept because stored assessments and protocol_catalog's gate table
    # name them.
    SOURCE_TEXT_CROSS_CHECK_FAILED = "source_text_cross_check_failed"
    SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE = "source_text_cross_check_unavailable"
    NO_EXECUTABLE_STEPS = "no_executable_steps"
    UNSUPPORTED_CONDITIONAL_BRANCH = "unsupported_conditional_branch"
    UNSUPPORTED_FIXED_RANGE_REPETITION = "unsupported_fixed_range_repetition"
    UNSUPPORTED_OPERATOR_DETERMINED_REPETITION = (
        "unsupported_operator_determined_repetition"
    )
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
    #: A page states a value the analysis neither claimed nor declined.
    #:
    #: Declining such a segment has always been refused outright -- see
    #: ``declined_segment_states_a_value`` -- on the ground that a value
    #: belonging to a step is read out to an operator and losing it is
    #: dangerous. Leaving the same segment out raised nothing at all, and the
    #: comment that justified the silence claimed the merge would refuse it,
    #: which stopped being true when STEP 28 removed that veto. So for six
    #: steps a model could lose a value by saying nothing about it, and only
    #: by saying something about it could it be caught.
    #:
    #: This is the other half. An omission is still not treated as a false
    #: statement -- the chunk survives, and so do the claims that were right --
    #: but the value that went missing keeps the Protocol out of execution
    #: until a person has looked at it.
    SOURCE_PAGE_NOT_FULLY_READ = "source_page_not_fully_read"
    #: A source page the PDF text layer could not supply -- no text, or a
    #: glyph with no Unicode mapping -- and whose OCR text has not been
    #: accepted.
    #:
    #: The page is marked, not the document, so the readable pages are still
    #: analysed. What was on the marked page is unknown, and it can be a value
    #: or a whole step: on ANKOM page 3 two unmappable glyphs stand directly
    #: before a duration. A signature does not make that page readable, so this is
    #: deliberately not an acknowledgeable gate. It clears the way the page
    #: gets read: OCR, a reviewer accepting the OCR text, and an analysis of
    #: that text -- whose pages no longer carry the mark.
    SOURCE_PAGE_REQUIRES_OCR = "source_page_requires_ocr"
    #: The analysis declined a segment that states a value inside a numbered
    #: step -- it read the segment and recorded that it holds no claim.
    #:
    #: This used to discard the whole chunk. That was one defensible reading of
    #: fail closed and it turned out to be the expensive one: two of in-gel's
    #: five chunks were thrown away over two segments, and both segments were a
    #: heading and a table of durations that genuinely are not instructions.
    #: The model's judgement was right and the refusal destroyed the four
    #: correct claims that travelled with it.
    #:
    #: Safety here means "not executed before a person has looked", not
    #: "evidence destroyed". So the declination stands as a record, the rest of
    #: the chunk survives, and the Protocol does not run until a reviewer has
    #: decided whether that value was an instruction.
    DECLINED_VALUE_NOT_RESOLVED = "declined_value_not_resolved"
    #: So many values declined that the page is unlikely to have been read.
    #:
    #: A few headings and tables state values that instruct nobody. A quarter
    #: of a chunk doing so is not that; it is a chunk that was skimmed. This is
    #: the difference between the ordinary case above and a failure, and it is
    #: a separate reason so a reviewer can tell them apart.
    EXCESSIVE_DECLINED_VALUES = "excessive_declined_values"
    #: The source says to repeat a numbered range and the analysis has no
    #: repetition for it.
    #:
    #: in-gel states three such sentences and the analysis carried one. The
    #: other two were simply absent, and nothing knew: the assembled Protocol
    #: looked complete, so an agent would have walked the steps once and said
    #: they were done -- which is the false completion notice this system is
    #: built to never produce.
    #:
    #: Nothing is inferred to raise this. The server reads the range the
    #: document printed and asks whether any repetition claim cites that
    #: passage with that range. It does not build the repeat, does not guess
    #: what it is for, and does not attach it to a step. It says a person must
    #: look.
    SOURCE_STATES_AN_UNCAPTURED_REPETITION = (
        "source_states_an_uncaptured_repetition"
    )
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
    #: A statement the page cuts at its end and the next page finishes (lane
    #: PA, human decision 3, 2026-10-06): the next page and its own text that
    #: completes the statement. Server-computed after the sentence was found
    #: in the two pages joined; never taken from a provider. None for a
    #: statement on one page.
    continued_on_page_number: int | None = None
    continued_excerpt: str | None = None


@dataclass(frozen=True)
class OperatorDeterminedRepetition:
    """A repetition whose count the document hands to the experimenter.

    "Repeat steps 19-20 for the required number of replicates" is not
    ambiguous: the source is perfectly clear that the operator decides, so
    calling it an ambiguity would misdescribe it, and a reviewer confirming a
    fixed count would be inventing one. The shape is fully determined -- which
    steps, repeated as a unit -- and only the number is open.

    So the protocol can become ready with this in it, because nothing about it
    is unknown to the protocol. What must not happen is a session starting the
    repetition without a number: the count is supplied at session start, by a
    named person, and recorded. Neither the server nor a model may guess it --
    that is the road to a false completion notice.
    """

    repetition_id: str
    start_step_id: str
    end_step_id: str
    range_source_text: str
    evidence: SourceEvidence
    section_id: str | None = None
    step_id: str | None = None
    action_id: str | None = None


@dataclass(frozen=True)
class NonStepLabelDisposition:
    """One numbered label a **reviewer** says is not an execution step.

    Measured on three of the four local sources, a numbered line is not always
    an instruction: a materials note, a bare section heading, a table of
    contents. Demanding an action claim for those refused correct responses.

    A provider can no longer say this. On the first real use of that contract
    a model disposed of six numbered lines that were plainly instructions, each
    carrying a temperature, a time or a piece of equipment; approved, the
    protocol would have been missing six steps silently. The asymmetry is why:
    treating a description as a step costs an operator hearing a description
    read out, while disposing of a real step removes it. So every numbered line
    is now a step as far as extraction is concerned, and this record exists
    only for a reviewer's own annotation, kept in the append-only ledger.
    """

    source_page_number: int
    source_label: str
    evidence: SourceEvidence


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
    #: Evidence for one field, where that field is printed on another page
    #: than ``evidence`` -- a protocols.io title on page 1 and its dates on
    #: page 2 cannot share one quote. A field without its own evidence is
    #: checked against ``evidence`` as before (human decision 2026-10-05).
    title_evidence: SourceEvidence | None = None
    authors_evidence: SourceEvidence | None = None
    created_date_evidence: SourceEvidence | None = None
    modified_date_evidence: SourceEvidence | None = None
    publication_date_evidence: SourceEvidence | None = None
    version_evidence: SourceEvidence | None = None
    doi_evidence: SourceEvidence | None = None
    source_uri_evidence: SourceEvidence | None = None
    license_evidence: SourceEvidence | None = None
    source_status_evidence: SourceEvidence | None = None

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
    #: Where the value itself is printed, when that is not on its owner's
    #: page -- a step on page 1 whose amount is given in a note on page 4.
    #: Without it the value is checked on its owner's evidence page.
    evidence: SourceEvidence | None = None


@dataclass(frozen=True)
class SourceStatement:
    statement_id: str
    source_text: str
    evidence: SourceEvidence


@dataclass(frozen=True)
class EstimatedDuration:
    source_text: str
    parsed_seconds: int | None = None
    #: Same as ``ScientificValue.evidence``: the page the time is printed on.
    evidence: SourceEvidence | None = None


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
    | OperatorDeterminedRepetition
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
            # The shape is fully determined and only the number is open, so
            # nothing about it is unknown to the protocol. The session refuses
            # to start it until a named person supplies the count.
            FeatureCode.OPERATOR_DETERMINED_REPETITION,
            # A repeat-until step has a stated endpoint and no stated count,
            # and it is supported the only way that shape can be: the person
            # at the bench reports the endpoint the document states, in their
            # own words, and that report is written down with the step, the
            # time and the actor before the step is left. Nothing about the
            # loop runs on model output -- the session neither counts rounds
            # nor decides that one was enough.
            #
            # The gate this reason used to raise did not leave with it. It
            # stands on the document instead: at a step the analysis anchors a
            # repetition to, CuratedProtocolSession refuses the transition
            # until an endpoint observation is on record for that step
            # (``endpoint_observation_outstanding``), the server refuses the
            # turn unless that observation reached the experiment record, and
            # the preview and completion-criteria answers say the step is held
            # for that reason rather than for this one. What changed is the
            # reason, not the obligation: the endpoint was always the
            # experimenter's to report, and calling it "unsupported" asked a
            # reviewer to resolve something no reviewer can resolve.
            #
            # ``profile_id`` deliberately does not change. It is written into
            # cached claim payloads and validated against them, so renaming it
            # would discard every cached chunk and spend provider calls to
            # re-earn them.
            FeatureCode.REPEAT_UNTIL,
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


#: The readiness reasons that keep a Protocol out of execution under the MVP
#: rule (human decision of 2026-10-08, lane DI): the analysis produced nothing
#: runnable, a page could not be read, or the source contradicts itself on a
#: safety-critical value. Every other reason is a notice: the experimenter
#: reads it before pressing start, and the session says the source's own
#: words at the step it belongs to. ``assess_readiness`` itself is unchanged
#: -- it still records every reason -- so stored assessments keep their
#: meaning and this split is applied where the catalog reads them.
EXECUTION_BLOCKING_REASON_CODES: frozenset[ReadinessReasonCode] = frozenset(
    {
        ReadinessReasonCode.INVALID_PROTOCOL,
        ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_FAILED,
        ReadinessReasonCode.NO_EXECUTABLE_STEPS,
        ReadinessReasonCode.SOURCE_PAGE_REQUIRES_OCR,
        ReadinessReasonCode.SAFETY_CRITICAL_CONFLICT,
    }
)

#: Reasons naming a construct this version has no guidance for yet. The
#: session says so once when the run reaches the step, and reads the source.
#: Conditional branches and the three repetition kinds are not here: lane CB
#: guides them (the condition is asked, the count is asked or read from the
#: source, the rounds are counted).
NO_GUIDANCE_YET_REASON_CODES: frozenset[ReadinessReasonCode] = frozenset(
    {
        ReadinessReasonCode.UNSUPPORTED_PARALLEL_BACKGROUND_WORK,
        ReadinessReasonCode.UNSUPPORTED_RECURRING_REMINDER,
        ReadinessReasonCode.UNSUPPORTED_RECURRING_ACTION,
        ReadinessReasonCode.UNSUPPORTED_REUSABLE_SUBPROCEDURE,
    }
)


def execution_blocking_reasons(
    assessment: ReadinessAssessment,
) -> tuple[ReadinessReason, ...]:
    """The reasons that keep this assessment's Protocol out of execution."""

    return tuple(
        reason for reason in assessment.reasons
        if reason.code in EXECUTION_BLOCKING_REASON_CODES
    )


def execution_notice_reasons(
    assessment: ReadinessAssessment,
) -> tuple[ReadinessReason, ...]:
    """The reasons the experimenter is told before starting; none blocks."""

    return tuple(
        reason for reason in assessment.reasons
        if reason.code not in EXECUTION_BLOCKING_REASON_CODES
    )


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
    if (
        evidence.continued_on_page_number is None
        and evidence.continued_excerpt is None
    ):
        return
    if (
        not isinstance(evidence.continued_on_page_number, int)
        or isinstance(evidence.continued_on_page_number, bool)
        or evidence.continued_on_page_number != evidence.source_page_number + 1
        or evidence.continued_on_page_number > pdf.page_count
    ):
        raise _error(
            ProtocolValidationCode.INVALID_SOURCE_PAGE,
            "a continued statement must continue on the next page",
            location,
        )
    continued = _text(evidence.continued_excerpt, f"{location}.continued_excerpt")
    if continued not in pdf.pages[evidence.continued_on_page_number - 1].text:
        raise _error(
            ProtocolValidationCode.SOURCE_EXCERPT_MISMATCH,
            "continued excerpt is not present on the next page",
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
    if isinstance(
        construct,
        (FixedRangeRepetition, OperatorDeterminedRepetition, RepeatUntil),
    ):
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
    elif isinstance(construct, OperatorDeterminedRepetition):
        _text(construct.range_source_text, f"{location}.range_source_text")
        for target in (construct.start_step_id, construct.end_step_id):
            _validate_target(
                DependencyTarget(target),
                step_locations,
                action_locations,
                location,
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


#: Each metadata claim field and the field that may hold its own evidence.
METADATA_FIELD_EVIDENCE: dict[str, str] = {
    name: f"{name}_evidence"
    for name in (
        "title",
        "authors",
        "created_date",
        "modified_date",
        "publication_date",
        "version",
        "doi",
        "source_uri",
        "license",
        "source_status",
    )
}


def _validate_value_evidence(
    value: object,
    pdf: ProtocolPdfExtraction,
    location: str,
) -> None:
    """Check the optional own evidence of every value and duration."""

    if isinstance(value, (ScientificValue, EstimatedDuration)):
        if value.evidence is not None:
            _validate_evidence(value.evidence, pdf, f"{location}.evidence")
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _validate_value_evidence(item, pdf, f"{location}[{index}]")
        return
    if (
        dataclasses.is_dataclass(value)
        and not isinstance(value, (type, ProtocolPdfExtraction, SourceEvidence))
    ):
        for field in dataclasses.fields(value):
            _validate_value_evidence(
                getattr(value, field.name), pdf, f"{location}.{field.name}"
            )


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
    for evidence_field in METADATA_FIELD_EVIDENCE.values():
        field_evidence = getattr(protocol.metadata, evidence_field)
        if field_evidence is not None:
            _validate_evidence(
                field_evidence,
                protocol.metadata.pdf,
                f"metadata.{evidence_field}",
            )
    _validate_value_evidence(protocol, protocol.metadata.pdf, "protocol")
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
    step_labelled: bool | None = None
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
            if not isinstance(step.source_label, str):
                raise _error(
                    ProtocolValidationCode.MISSING_SOURCE_LABEL,
                    "source step must preserve its original number or label",
                    step_location,
                )
            # Empty means the source prints no step numbers (human decision
            # 2026-10-05, lane P3); the screen numbers such steps in order.
            # That is a property of the whole source, so the steps are either
            # all labelled or all unlabelled.
            labelled = bool(step.source_label.strip())
            if step_labelled is None:
                step_labelled = labelled
            elif labelled != step_labelled:
                raise _error(
                    ProtocolValidationCode.MISSING_SOURCE_LABEL,
                    "source steps must all preserve their original number or "
                    "label, or all be unlabelled",
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
            FeatureCode.OPERATOR_DETERMINED_REPETITION,
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
        elif isinstance(construct, OperatorDeterminedRepetition):
            code = FeatureCode.OPERATOR_DETERMINED_REPETITION
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
    FeatureCode.OPERATOR_DETERMINED_REPETITION: (
        ReadinessReasonCode.UNSUPPORTED_OPERATOR_DETERMINED_REPETITION,
        "Operator-determined repetition is not executable by this capability "
        "profile.",
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
            ReadinessReasonCode.SOURCE_PAGE_REQUIRES_OCR,
            ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ,
            ReadinessReasonCode.DECLINED_VALUE_NOT_RESOLVED,
            ReadinessReasonCode.EXCESSIVE_DECLINED_VALUES,
            ReadinessReasonCode.SOURCE_STATES_AN_UNCAPTURED_REPETITION,
            ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION,
            ReadinessReasonCode.UNSUPPORTED_CONDITIONAL_BRANCH,
            ReadinessReasonCode.UNSUPPORTED_FIXED_RANGE_REPETITION,
            ReadinessReasonCode.UNSUPPORTED_OPERATOR_DETERMINED_REPETITION,
            ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL,
            ReadinessReasonCode.UNSUPPORTED_PARALLEL_BACKGROUND_WORK,
            ReadinessReasonCode.UNSUPPORTED_RECURRING_REMINDER,
            ReadinessReasonCode.UNSUPPORTED_RECURRING_ACTION,
            ReadinessReasonCode.UNSUPPORTED_REUSABLE_SUBPROCEDURE,
        )
    )
}


class SourceLineage(str, Enum):
    """Whether this Protocol is a document as registered, or a change to one.

    The safety gate asks a reviewer to confirm a Protocol's hazards before it
    executes. For a revision that is exactly the right question -- somebody
    altered a procedure people run, and a person should look at what they
    altered before anyone runs it.

    For a document as registered it is a question with no answer. There is no
    earlier version to compare against, and the only thing a reviewer could
    confirm is that our extraction found the hazards the PDF states -- which
    is a claim about our own reading, made by someone reading the same PDF we
    did. The gate collected an acknowledgement that asserted more than anybody
    could know: "safety review complete". What replaces it is not a weaker
    gate but a different obligation, discharged by the system rather than by
    the reviewer -- the source's own warning text is read out at the step it
    belongs to, every time, and the reading is recorded on the session. See
    CuratedProtocolSession.safety_warning_disclosure.

    UNKNOWN is not a third case. It is the absence of an answer, and it is
    treated as REVISION, because a caller that has not said which of the two
    this is has not established that the gate can be skipped.

    No production caller passes this yet, which is deliberate and is why the
    default is the strict one. Wiring it needs a decision, because the catalog
    and the workspace lineage live in separate databases -- ``protocol_revisions``
    in protocol_workspace.sqlite has no parent column, and
    ``protocol_lineage_revisions`` in commercial_workspace.sqlite is where
    ``parent_revision_id`` lives -- and the workspace can be switched off
    entirely, in which case there is no lineage to consult at all.

    One answer is permanently excluded. The catalog's own ``revision_number``
    must never be used to decide this. That number counts how many times a PDF
    has been registered under one experiment, not where the document sits in a
    protocol's lineage: uploading an edited document for the first time creates
    revision 1 of a new experiment, which this rule would call an original and
    wave past the safety gate -- the one direction the mistake must never go.
    It is cheap, it is one expression, and it is wrong; it is written down here
    so it is not rediscovered as a good idea.
    """

    ORIGINAL_REGISTRATION = "original_registration"
    REVISION = "revision"
    UNKNOWN = "unknown"


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
    source_lineage: SourceLineage = SourceLineage.UNKNOWN,
    pages_stating_unaccounted_values: tuple[int, ...] = (),
    pages_declining_stated_values: tuple[int, ...] = (),
    pages_declining_excessive_values: tuple[int, ...] = (),
    uncaptured_repeat_instructions: tuple[tuple[str, str], ...] = (),
) -> ReadinessAssessment:
    """Fail closed with two public outcomes and stable, sanitized reasons.

    ``source_lineage`` decides one gate and nothing else. It defaults to
    UNKNOWN, which is treated as a revision, so a caller that says nothing
    gets the stricter of the two outcomes.

    ``pages_stating_unaccounted_values`` names the pages carrying a value the
    analysis neither claimed nor declined. It is derived from the merge's own
    page coverage, never from anything a provider asserts, and an empty tuple
    is the ordinary case rather than a claim that every page was read.

    ``pages_declining_stated_values`` names the pages where a value inside a
    numbered step was declined -- read, and recorded as holding no claim. The
    two are kept apart because they are different faults: one is a silence, the
    other is a judgement a person may disagree with.

    ``pages_declining_excessive_values`` names pages that declined so many
    values the page is unlikely to have been read at all.

    ``uncaptured_repeat_instructions`` names ranges the source says to repeat
    and the analysis has no repetition for. Read from the document's own text,
    never inferred.

    Pages that need OCR are read from the Protocol's own source extraction,
    ``protocol.metadata.pdf``, which is the extraction the analysis read. Text
    accepted from OCR replaces those pages before analysis, so a Protocol
    analysed from it carries no marked page and no such reason.
    """

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
    if not any(section.steps for section in protocol.sections):
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.NO_EXECUTABLE_STEPS,
                message="The structured Protocol contains no executable source steps.",
            )
        )

    if (
        any(section.steps for section in protocol.sections)
        and source_lineage is not SourceLineage.ORIGINAL_REGISTRATION
    ):
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
        #
        # The gate is raised for a revision and for a lineage nobody has
        # stated. It is not raised for a document as registered, where the
        # question has no answerable form -- see SourceLineage. That is not
        # the obligation being dropped: an original owes the source's own
        # warning text at the step it belongs to, on every run, which is a
        # duty the system discharges rather than one a reviewer signs off.
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

    ocr_pages = protocol.metadata.pdf.ocr_required_page_numbers
    if ocr_pages:
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.SOURCE_PAGE_REQUIRES_OCR,
                message=(
                    "Source page(s) "
                    + ", ".join(str(page) for page in ocr_pages)
                    + " could not be read from the PDF text layer and need "
                    "OCR. Execution waits until a reviewer accepts their OCR "
                    "text and the Protocol is analysed from it."
                ),
            )
        )

    pages = tuple(
        sorted(
            {
                page
                for page in pages_stating_unaccounted_values
                if isinstance(page, int) and not isinstance(page, bool)
            }
        )
    )
    declined_pages = tuple(
        sorted(
            {
                page
                for page in pages_declining_stated_values
                if isinstance(page, int) and not isinstance(page, bool)
            }
        )
    )
    crowded = tuple(
        sorted(
            {
                page
                for page in pages_declining_excessive_values
                if isinstance(page, int) and not isinstance(page, bool)
            }
        )
    )
    if pages:
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.SOURCE_PAGE_NOT_FULLY_READ,
                message=(
                    "The analysis left a stated value unaccounted on "
                    f"{len(pages)} source page(s). A reviewer must read those "
                    "pages before execution; the value may be an instruction "
                    "nobody would otherwise be given."
                ),
            )
        )

    if declined_pages:
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.DECLINED_VALUE_NOT_RESOLVED,
                message=(
                    "The analysis recorded that a stated value holds no claim "
                    f"on {len(declined_pages)} source page(s). A reviewer must "
                    "decide whether it was an instruction; the analysis is not "
                    "trusted to settle that alone."
                ),
            )
        )
    uncaptured = tuple(
        sorted(
            {
                tuple(item)
                for item in uncaptured_repeat_instructions
                if isinstance(item, (tuple, list)) and len(item) == 2
            }
        )
    )
    if uncaptured:
        reasons.append(
            ReadinessReason(
                code=(
                    ReadinessReasonCode.SOURCE_STATES_AN_UNCAPTURED_REPETITION
                ),
                message=(
                    f"The source states {len(uncaptured)} repeat instruction(s) "
                    "the analysis did not capture. A reviewer must read those "
                    "passages; an agent that walks past a stated repeat would "
                    "reach the last step and report the work finished."
                ),
            )
        )
    if crowded:
        reasons.append(
            ReadinessReason(
                code=ReadinessReasonCode.EXCESSIVE_DECLINED_VALUES,
                message=(
                    f"{len(crowded)} source page(s) declined more stated "
                    "values than a page of headings and tables can account "
                    "for. Those pages were probably not read."
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


# --- Source durations (lane PT, human decisions 1-2 of 2026-10-08) ---------
#
# A timer for an uploaded protocol is a duration the source prints: the
# number and its unit, read here from the analysis's excerpt and nowhere
# else. Nothing is inferred -- "overnight", "until clear", "at least 2 h" and
# "after 16 h" make no timer, and the excerpt is read back instead. The
# server checks every value it keeps against the step's own source
# (experiment_protocol_analysis.verify_step_timers), the same level of trust
# the in-gel sidecar manifest gets from its loader.

_DURATION_UNIT_SECONDS: dict[str, int] = {
    "hours": 3600, "hour": 3600, "hrs": 3600, "hr": 3600, "h": 3600, "시간": 3600,
    "minutes": 60, "minute": 60, "mins": 60, "min": 60, "분": 60,
    "seconds": 1, "second": 1, "secs": 1, "sec": 1, "s": 1, "초": 1,
}
_UNIT = (
    r"(?:hours?|hrs?|h|minutes?|mins?|min|seconds?|secs?|sec|s|시간|분(?!\s*의)|초)"
    r"(?![A-Za-z])"
)
_NUMBER = r"\d+(?:\.\d+)?"
_PAIR = rf"{_NUMBER}(?:\s*|-){_UNIT}"
_COMPOUND = rf"{_PAIR}(?:\s*{_PAIR})*"
_CLOCK = r"(?<![\d:])\d{1,2}:\d{2}:\d{2}(?![\d:])"
_ALTERNATIVE = r"\s*(?:[-–—~～]|to|or|또는|혹은)\s*"
_SOURCE_DURATION = re.compile(
    rf"(?<![\d.])(?:"
    rf"(?P<between>between\s+(?P<b1>{_NUMBER})\s+and\s+(?P<b2>{_NUMBER})\s*(?P<bu>{_UNIT}))"
    rf"|(?P<pair>(?P<p1>{_COMPOUND}){_ALTERNATIVE}(?P<p2>{_COMPOUND}))"
    rf"|(?P<bare>(?P<n1>{_NUMBER}){_ALTERNATIVE}(?P<n2>{_NUMBER})\s*(?P<nu>{_UNIT}))"
    rf"|(?P<single>{_COMPOUND})"
    rf"|(?P<clock>{_CLOCK})"
    rf")",
    re.I,
)
_PAIR_PARTS = re.compile(rf"({_NUMBER})(?:\s*|-)({_UNIT})", re.I)
#: A bound, not a length: "at least 30 min", "up to 2 h", "30분 이상".
_OPEN_BOUND_BEFORE = re.compile(
    r"(?:at\s+least|no\s+(?:less|more)\s+than|not\s+(?:less|more)\s+than|"
    r"more\s+than|less\s+than|longer\s+than|shorter\s+than|up\s+to|within|"
    r"over|under|minimum(?:\s+of)?|maximum(?:\s+of)?|max\.?|[≥≤<>]|최소|최대)"
    r"\s*[~≈]?\s*$",
    re.I,
)
_OPEN_BOUND_AFTER = re.compile(
    r"^\s*(?:or\s+(?:more|longer|less|shorter)|and\s+(?:more|longer)|\+|"
    r"이상|이하|이내|미만|초과|넘게|까지)",
    re.I,
)
#: A repeat interval: "every 10 min", "10분마다".
_INTERVAL_BEFORE = re.compile(r"(?:every|each|per|매)\s*$", re.I)
_INTERVAL_AFTER = re.compile(r"^\s*(?:마다|간격|intervals?)", re.I)
#: Time since something else: "After 2 hours, remove ...", "30분 후".
_ELAPSED_BEFORE = re.compile(r"(?:after|post)\s*[~≈]?\s*$", re.I)
_ELAPSED_AFTER = re.compile(r"^\s*(?:후|뒤|지나|경과|later|after\b)", re.I)
#: A time the source states without a number.
_UNNUMBERED_TIME = re.compile(
    r"(?<![A-Za-z])(?:overnight|o/n|until|till|several\s+(?:hours|minutes|days)|"
    r"a\s+few\s+(?:hours|minutes|seconds)|briefly|brief)(?![A-Za-z])|"
    r"밤새|하룻밤|오버나이트|될\s*때까지|할\s*때까지",
    re.I,
)

SOURCE_DURATION_REFUSAL_KO: dict[str, str] = {
    "no_number": "숫자로 적힌 시간이 없어요(overnight, until … 같은 표현).",
    "with_unnumbered_alternative": "숫자 없는 시간 표현(overnight, until …)이 함께 적혀 있어 길이를 정할 수 없어요.",
    "open_bound": "정해진 길이가 아니라 최소·최대·이내 같은 한계로 적혀 있어요.",
    "interval": "반복 간격(매 …마다)으로 적혀 있어요.",
    "elapsed_reference": "다른 작업부터 흐른 시간(after …, … 후)을 가리켜요.",
    "not_whole_seconds": "초 단위로 떨어지지 않는 값이에요.",
    "unclear_range": "앞 값이 뒤 값보다 커서 범위로 읽을 수 없어요.",
    "no_duration": "시간 숫자와 단위를 찾지 못했어요.",
    "not_in_step_text": "발췌가 그 단계의 원문 근거 안에 없어요.",
    "page_outside_step": "발췌가 그 단계의 쪽 범위 밖에 있어요.",
    "not_on_page": "발췌가 원문 쪽에서 확인되지 않아요.",
    "analysis_value_mismatch": "분석이 적은 초가 원문 값과 달라요.",
    "step_time_ambiguity": "분석이 이 단계의 시간을 모호하다고 표시했어요.",
    "before_start": "단계가 아닌 시작 전 준비에 적힌 시간이에요.",
}


@dataclass(frozen=True)
class SourceDuration:
    """A duration the source prints, exactly as printed.

    ``seconds`` holds one value, or -- for a range ("12–16 h") or printed
    alternatives ("15 or 30 min") -- each value in the order printed. Only
    printed values are ever here: a range's middle is not one.
    """

    literal: str
    seconds: tuple[int, ...]

    @property
    def is_choice(self) -> bool:
        return len(self.seconds) > 1


@dataclass(frozen=True)
class RefusedSourceDuration:
    """A time expression that makes no timer, and why (a key of SOURCE_DURATION_REFUSAL_KO)."""

    literal: str
    reason: str


@dataclass(frozen=True)
class SourceDurationReading:
    durations: tuple[SourceDuration, ...]
    refused: tuple[RefusedSourceDuration, ...]


def _compound_seconds(text: str) -> float:
    total = 0.0
    for number, unit in _PAIR_PARTS.findall(text):
        total += float(number) * _DURATION_UNIT_SECONDS[unit.lower()]
    return total


def _clock_seconds(text: str) -> int:
    hours, minutes, seconds = (int(part) for part in text.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _match_seconds(match: re.Match[str]) -> tuple[float, ...]:
    if match.group("between"):
        unit = _DURATION_UNIT_SECONDS[match.group("bu").lower()]
        return (float(match.group("b1")) * unit, float(match.group("b2")) * unit)
    if match.group("pair"):
        return (
            _compound_seconds(match.group("p1")),
            _compound_seconds(match.group("p2")),
        )
    if match.group("bare"):
        unit = _DURATION_UNIT_SECONDS[match.group("nu").lower()]
        return (float(match.group("n1")) * unit, float(match.group("n2")) * unit)
    if match.group("clock"):
        return (float(_clock_seconds(match.group("clock"))),)
    return (_compound_seconds(match.group("single")),)


def read_source_durations(text: str) -> SourceDurationReading:
    """Every duration ``text`` prints, each kept or refused with its reason.

    Pure reading of printed characters; no model output is consulted. An
    expression qualified as a bound, an interval or a time since something
    else is refused, and so is every number in a text that also states a time
    without one ("1 h or overnight"): which one applies is not the server's
    to choose.
    """

    durations: list[SourceDuration] = []
    refused: list[RefusedSourceDuration] = []
    unnumbered = [m.group(0) for m in _UNNUMBERED_TIME.finditer(text)]
    for match in _SOURCE_DURATION.finditer(text):
        literal = match.group(0).strip()
        before = text[max(0, match.start() - 24):match.start()]
        after = text[match.end():match.end() + 16]
        reason: str | None = None
        if unnumbered:
            reason = "with_unnumbered_alternative"
        elif _OPEN_BOUND_BEFORE.search(before) or _OPEN_BOUND_AFTER.search(after):
            reason = "open_bound"
        elif _INTERVAL_BEFORE.search(before) or _INTERVAL_AFTER.search(after):
            reason = "interval"
        elif _ELAPSED_BEFORE.search(before) or _ELAPSED_AFTER.search(after):
            reason = "elapsed_reference"
        values = _match_seconds(match)
        if reason is None and any(
            value <= 0 or value != int(value) for value in values
        ):
            reason = "not_whole_seconds"
        if (
            reason is None
            and len(values) == 2
            and values[0] >= values[1]
            and not re.search(r"\bor\b|또는|혹은", literal, re.I)
        ):
            # "3000 - 5 min" is a speed beside a time, not a range.
            reason = "unclear_range"
        if reason is not None:
            refused.append(RefusedSourceDuration(literal, reason))
            continue
        seconds = tuple(dict.fromkeys(int(value) for value in values))
        durations.append(SourceDuration(literal, seconds))
    if unnumbered and not durations and not refused:
        refused.extend(
            RefusedSourceDuration(word, "no_number")
            for word in dict.fromkeys(unnumbered)
        )
    return SourceDurationReading(tuple(durations), tuple(refused))


@dataclass(frozen=True)
class VerifiedStepTimer:
    """One duration of a step that passed the server's source check."""

    step_id: str
    source_label: str
    action_id: str | None
    excerpt: str
    literal: str
    seconds: tuple[int, ...]
    page_number: int
    #: "analysis_duration": a duration the analysis attached to the step;
    #: "step_text": read by the server in the step's own instruction text.
    source: str = "analysis_duration"


@dataclass(frozen=True)
class RefusedStepTime:
    """A time expression that makes no timer, with the excerpt to read instead."""

    step_id: str | None
    source_label: str | None
    action_id: str | None
    excerpt: str
    literal: str
    reason: str
    page_number: int | None = None

    @property
    def reason_ko(self) -> str:
        return SOURCE_DURATION_REFUSAL_KO[self.reason]


@dataclass(frozen=True)
class TimerChoice:
    """One value a step's timer may be set to, as the source prints it."""

    seconds: int
    literal: str
    excerpt: str


@dataclass(frozen=True)
class StepTimerTable:
    """The verified timers and refused time expressions of one analysis.

    ``manifest`` is the shape the in-gel sidecar manifest loads into --
    step_id to seconds -- for a step whose verified durations come to one
    value. A step with a range, printed alternatives or two different
    durations has ``choices`` instead: the values in step order, each one
    printed, for the experimenter to pick from when the timer is started.
    """

    verified: tuple[VerifiedStepTimer, ...] = ()
    refused: tuple[RefusedStepTime, ...] = ()

    def _values(self) -> dict[str, list[TimerChoice]]:
        by_step: dict[str, list[TimerChoice]] = {}
        for timer in self.verified:
            options = by_step.setdefault(timer.step_id, [])
            for seconds in timer.seconds:
                if all(option.seconds != seconds for option in options):
                    options.append(TimerChoice(seconds, timer.literal, timer.excerpt))
        return by_step

    def manifest(self) -> dict[str, int]:
        return {
            step_id: options[0].seconds
            for step_id, options in self._values().items()
            if len(options) == 1
        }

    def choices(self) -> dict[str, tuple[TimerChoice, ...]]:
        return {
            step_id: tuple(options)
            for step_id, options in self._values().items()
            if len(options) > 1
        }

    def for_step(self, step_id: str) -> tuple[VerifiedStepTimer, ...]:
        return tuple(timer for timer in self.verified if timer.step_id == step_id)

    def refused_for_step(self, step_id: str) -> tuple[RefusedStepTime, ...]:
        return tuple(item for item in self.refused if item.step_id == step_id)
