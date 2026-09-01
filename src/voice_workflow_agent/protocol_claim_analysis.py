"""Evidence-first claim DTOs for bounded Protocol source analysis.

The model-facing schema in this module is intentionally much smaller than the
``ExperimentProtocol`` schema.  A model may propose source claims and structural
markers, but deterministic code owns source identity, exact-quote validation,
cross-chunk consistency, and construction of the existing domain model.
"""

from __future__ import annotations

import hashlib
import json
import re
from base64 import urlsafe_b64encode
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisDraft,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisModel,
    ProtocolAnalysisModelError,
    ProtocolAnalysisResponseError,
    ProtocolEvidenceDiagnostic,
    validate_protocol_analysis_evidence,
)
from voice_workflow_agent.experiment_protocol_pdf import ProtocolPdfExtraction
from voice_workflow_agent.experiment_protocol_store import ANALYSIS_SCHEMA_VERSION


CLAIM_SCHEMA_VERSION = 4
EVIDENCE_SEGMENT_VERSION = 2
MAX_CHUNK_CLAIM_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_CLAIMS_PER_CHUNK = 4096
_MAX_MARKERS_PER_CHUNK = 1024
_MAX_TEXT_CHARS = 32 * 1024
_MAX_EVIDENCE_SEGMENTS_PER_SPAN = 256
_MAX_PROVIDER_SEGMENT_CHARS = 4096
MAX_PAGE_COVERAGE_RECORDS = 32
MAX_EVIDENCE_ITEM_REFS_PER_PAGE = 256
_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_NUMBERED_SOURCE_LINE = re.compile(
    r"(?m)^[ \t]*(?P<label>[1-9][0-9]{0,3})(?:[.)])?[ \t]+(?P<next>\S+)"
)
_INLINE_NUMBERED_SOURCE = re.compile(
    r"(?<!\S)(?P<label>[1-9][0-9]{0,3})\.[ \t]+(?P<next>\S+)"
)
_VALUE_UNITS = {
    "c",
    "g",
    "h",
    "l",
    "mg",
    "min",
    "ml",
    "mm",
    "mm3",
    "rpm",
    "s",
    "ul",
    "µl",
    "μl",
}


class ClaimCategory(str, Enum):
    MATERIAL = "material"
    EQUIPMENT = "equipment"
    ACTION = "action"
    QUANTITY = "quantity"
    CONCENTRATION = "concentration"
    TEMPERATURE = "temperature"
    DURATION = "duration"
    AGITATION_SPEED = "agitation_speed"
    PREREQUISITE = "prerequisite"
    WARNING_HAZARD = "warning_hazard"
    OBSERVATION_CHECKPOINT = "observation_checkpoint"
    REPEAT_CONDITION = "repeat_condition"
    EXPLICIT_MISSING_AMBIGUOUS_VALUE = "explicit_missing_ambiguous_value"


class StructureMarkerKind(str, Enum):
    PROTOCOL_TITLE = "protocol_title"
    SECTION = "section"


class PageCoverageStatus(str, Enum):
    COMPLETE = "complete"
    NO_RELEVANT_CLAIMS = "no_relevant_claims"
    ANALYSIS_INCOMPLETE = "analysis_incomplete"


class ProtocolClaimConsistencyError(ValueError):
    """A complete set of valid chunks cannot form one coherent protocol."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__("Protocol claims failed whole-document consistency validation.")


@dataclass(frozen=True)
class ClaimSourceEvidence:
    """Server-resolved immutable source identity for one proposed claim."""

    source_revision: str
    source_sha256: str
    source_page_number: int
    page_text_sha256: str
    evidence_segment_ids: tuple[str, ...]
    source_excerpt: str

    def public_dict(self) -> dict[str, object]:
        return {
            "source_revision": self.source_revision,
            "source_sha256": self.source_sha256,
            "source_page_number": self.source_page_number,
            "page_text_sha256": self.page_text_sha256,
            "evidence_segment_ids": list(self.evidence_segment_ids),
            "source_excerpt": self.source_excerpt,
        }


@dataclass(frozen=True)
class ProtocolEvidenceSegment:
    """One deterministic, page-bounded unit of immutable extracted text."""

    segment_id: str
    source_revision: str
    source_sha256: str
    source_page_number: int
    page_text_sha256: str
    segment_index: int
    text: str


@dataclass(frozen=True)
class ProviderEvidenceHandle:
    """Request-scoped provider handle bound to one canonical segment."""

    handle: str
    segment: ProtocolEvidenceSegment

    def provider_pair(self) -> tuple[str, str]:
        return (self.handle, self.segment.text)


@dataclass(frozen=True)
class ProviderEvidencePage:
    """Minimal provider page plus its immutable server-owned handle map."""

    source_page_number: int
    role: str
    evidence: tuple[ProviderEvidenceHandle, ...]

    def provider_dict(self) -> dict[str, object]:
        return {
            "source_page_number": self.source_page_number,
            "role": self.role,
            "segments": [item.provider_pair() for item in self.evidence],
        }


@dataclass(frozen=True)
class ProviderClaimRequest:
    """Compact provider request with a full immutable server-side identity map."""

    request_handle: str
    capability_policy_id: str
    source_revision: str
    source_sha256: str
    chunk_id: str
    ordinal: int
    core_page_refs: tuple[int, ...]
    context_page_refs: tuple[int, ...]
    pages: tuple[ProviderEvidencePage, ...]

    def provider_dict(self) -> dict[str, object]:
        return {
            "claim_schema_version": CLAIM_SCHEMA_VERSION,
            "capability_policy_id": self.capability_policy_id,
            "request_handle": self.request_handle,
            "pages": [page.provider_dict() for page in self.pages],
        }

    def input_json(self) -> str:
        return _canonical_json(self.provider_dict())


@dataclass(frozen=True)
class ClaimResponseSchemaMetrics:
    """Content-free size and cardinality evidence for one request schema."""

    schema_before_bytes: int
    schema_after_bytes: int
    schema_growth_bytes: int
    schema_growth_percent: float
    core_page_count: int
    core_handle_count: int
    context_handle_count: int
    handle_enum_entry_count: int
    largest_page_handle_enum: int
    handles_per_core_page: tuple[tuple[int, int], ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_before_bytes": self.schema_before_bytes,
            "schema_after_bytes": self.schema_after_bytes,
            "schema_growth_bytes": self.schema_growth_bytes,
            "schema_growth_percent": self.schema_growth_percent,
            "core_page_count": self.core_page_count,
            "core_handle_count": self.core_handle_count,
            "context_handle_count": self.context_handle_count,
            "handle_enum_entry_count": self.handle_enum_entry_count,
            "largest_page_handle_enum": self.largest_page_handle_enum,
            "handles_per_core_page": [
                {"source_page_number": page_number, "handle_count": handle_count}
                for page_number, handle_count in self.handles_per_core_page
            ],
        }


@dataclass(frozen=True)
class ProtocolStructureMarker:
    marker_id: str
    kind: StructureMarkerKind
    source_order: int
    source_text: str
    section_id: str | None
    evidence: ClaimSourceEvidence

    def public_dict(self) -> dict[str, object]:
        return {
            "marker_id": self.marker_id,
            "kind": self.kind.value,
            "source_order": self.source_order,
            "source_text": self.source_text,
            "section_id": self.section_id,
            "evidence": self.evidence.public_dict(),
        }


@dataclass(frozen=True)
class ProtocolClaim:
    claim_id: str
    category: ClaimCategory
    source_order: int
    source_text: str
    section_id: str | None
    step_id: str | None
    source_label: str | None
    target_claim_id: str | None
    required_for_execution: bool
    evidence: ClaimSourceEvidence

    def public_dict(self) -> dict[str, object]:
        return {
            "claim_id": self.claim_id,
            "category": self.category.value,
            "source_order": self.source_order,
            "source_text": self.source_text,
            "section_id": self.section_id,
            "step_id": self.step_id,
            "source_label": self.source_label,
            "target_claim_id": self.target_claim_id,
            "required_for_execution": self.required_for_execution,
            "evidence": self.evidence.public_dict(),
        }


@dataclass(frozen=True)
class ProtocolPageClaimCoverage:
    source_revision: str
    source_sha256: str
    source_page_number: int
    page_text_sha256: str
    status: PageCoverageStatus
    evidence_item_ids: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "source_revision": self.source_revision,
            "source_sha256": self.source_sha256,
            "source_page_number": self.source_page_number,
            "page_text_sha256": self.page_text_sha256,
            "status": self.status.value,
            "evidence_item_ids": list(self.evidence_item_ids),
        }


@dataclass(frozen=True)
class ProtocolChunkClaimAnalysis:
    claim_schema_version: int
    capability_policy_id: str
    source_revision: str
    source_sha256: str
    chunk_id: str
    page_coverage: tuple[ProtocolPageClaimCoverage, ...]
    structure: tuple[ProtocolStructureMarker, ...]
    claims: tuple[ProtocolClaim, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "claim_schema_version": self.claim_schema_version,
            "capability_policy_id": self.capability_policy_id,
            "source_revision": self.source_revision,
            "source_sha256": self.source_sha256,
            "chunk_id": self.chunk_id,
            "page_coverage": [item.public_dict() for item in self.page_coverage],
            "structure": [item.public_dict() for item in self.structure],
            "claims": [item.public_dict() for item in self.claims],
        }


@dataclass(frozen=True)
class MergedProtocolClaims:
    protocol_id: str
    source_revision: str
    source_sha256: str
    capability_policy_id: str
    required_chunk_ids: tuple[str, ...]
    page_coverage: tuple[ProtocolPageClaimCoverage, ...]
    structure: tuple[ProtocolStructureMarker, ...]
    claims: tuple[ProtocolClaim, ...]


_NULLABLE_STRING_SCHEMA: dict[str, object] = {
    "anyOf": [{"type": "string"}, {"type": "null"}]
}
_EVIDENCE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source_page_number": {"type": "integer", "minimum": 1},
        "evidence_segment_ids": {
            "type": "array",
            "minItems": 1,
            "maxItems": _MAX_EVIDENCE_SEGMENTS_PER_SPAN,
            "items": {"type": "string"},
        },
    },
    "required": [
        "source_page_number",
        "evidence_segment_ids",
    ],
}
CLAIM_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim_schema_version": {"type": "integer", "const": CLAIM_SCHEMA_VERSION},
        "capability_policy_id": {"type": "string"},
        "request_handle": {"type": "string"},
        "page_coverage": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_PAGE_COVERAGE_RECORDS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_page_number": {"type": "integer", "minimum": 1},
                    "status": {
                        "type": "string",
                        "enum": [item.value for item in PageCoverageStatus],
                    },
                    "evidence_item_ids": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": MAX_EVIDENCE_ITEM_REFS_PER_PAGE,
                        "uniqueItems": True,
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "source_page_number",
                    "status",
                    "evidence_item_ids",
                ],
            },
        },
        "structure": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "marker_id": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [item.value for item in StructureMarkerKind],
                    },
                    "source_order": {"type": "integer", "minimum": 0},
                    "section_id": _NULLABLE_STRING_SCHEMA,
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": [
                    "marker_id",
                    "kind",
                    "source_order",
                    "section_id",
                    "evidence",
                ],
            },
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_id": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [item.value for item in ClaimCategory],
                    },
                    "source_order": {"type": "integer", "minimum": 0},
                    "section_id": _NULLABLE_STRING_SCHEMA,
                    "step_id": _NULLABLE_STRING_SCHEMA,
                    "source_label": _NULLABLE_STRING_SCHEMA,
                    "target_claim_id": _NULLABLE_STRING_SCHEMA,
                    "required_for_execution": {"type": "boolean"},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": [
                    "claim_id",
                    "category",
                    "source_order",
                    "section_id",
                    "step_id",
                    "source_label",
                    "target_claim_id",
                    "required_for_execution",
                    "evidence",
                ],
            },
        },
    },
    "required": [
        "claim_schema_version",
        "capability_policy_id",
        "request_handle",
        "page_coverage",
        "structure",
        "claims",
    ],
}


def _claim_response_schema_for_core_pages(
    core_page_refs: tuple[int, ...],
) -> dict[str, Any]:
    """Build the pre-handle baseline with request-exact coverage bounds."""

    if (
        not core_page_refs
        or len(core_page_refs) > MAX_PAGE_COVERAGE_RECORDS
        or len(set(core_page_refs)) != len(core_page_refs)
        or any(
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number < 1
            for page_number in core_page_refs
        )
    ):
        raise ValueError("Core page references are invalid for the claim schema.")
    schema = deepcopy(CLAIM_RESPONSE_SCHEMA)
    coverage = schema["properties"]["page_coverage"]
    coverage["minItems"] = len(core_page_refs)
    coverage["maxItems"] = len(core_page_refs)
    coverage["items"]["properties"]["source_page_number"] = {
        "type": "integer",
        "enum": list(core_page_refs),
    }
    for section_name in ("structure", "claims"):
        evidence = schema["properties"][section_name]["items"]["properties"][
            "evidence"
        ]
        evidence["properties"]["source_page_number"] = {
            "type": "integer",
            "enum": list(core_page_refs),
        }
    return schema


def _request_core_page_handles(
    request: ProviderClaimRequest,
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    """Validate and return the active request's exact core-page handle map."""

    if not isinstance(request, ProviderClaimRequest):
        raise ValueError("A provider claim request is required for the claim schema.")
    _claim_response_schema_for_core_pages(request.core_page_refs)
    if (
        len(set(request.context_page_refs)) != len(request.context_page_refs)
        or set(request.core_page_refs) & set(request.context_page_refs)
        or any(
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number < 1
            for page_number in request.context_page_refs
        )
    ):
        raise ValueError("Context page references are invalid for the claim schema.")
    expected_roles = {
        **{page_number: "context" for page_number in request.context_page_refs},
        **{page_number: "core" for page_number in request.core_page_refs},
    }
    if (
        len(request.pages) != len(expected_roles)
        or {page.source_page_number for page in request.pages}
        != set(expected_roles)
    ):
        raise ValueError("Provider evidence pages do not match the active request.")

    seen_handles: set[str] = set()
    handles_by_page: dict[int, tuple[str, ...]] = {}
    for page in request.pages:
        if page.role != expected_roles.get(page.source_page_number):
            raise ValueError("Provider evidence page roles do not match the request.")
        handles: list[str] = []
        for item in page.evidence:
            if (
                not isinstance(item, ProviderEvidenceHandle)
                or not _STABLE_ID.fullmatch(item.handle)
                or item.segment.source_page_number != page.source_page_number
                or item.segment.source_revision != request.source_revision
                or item.segment.source_sha256 != request.source_sha256
                or item.handle in seen_handles
            ):
                raise ValueError("Provider evidence handles are invalid for the request.")
            seen_handles.add(item.handle)
            handles.append(item.handle)
        handles_by_page[page.source_page_number] = tuple(handles)
    return tuple(
        (page_number, handles_by_page[page_number])
        for page_number in request.core_page_refs
    )


def claim_response_schema(request: ProviderClaimRequest) -> dict[str, Any]:
    """Bind evidence page/handle pairs and coverage to one active request."""

    handles_by_core_page = _request_core_page_handles(request)
    schema = _claim_response_schema_for_core_pages(request.core_page_refs)
    page_local_branches: list[dict[str, Any]] = []
    for page_number, handles in handles_by_core_page:
        if not handles:
            continue
        evidence = deepcopy(_EVIDENCE_SCHEMA)
        evidence["properties"]["source_page_number"] = {
            "type": "integer",
            "const": page_number,
        }
        evidence["properties"]["evidence_segment_ids"]["items"] = {
            "type": "string",
            "enum": list(handles),
        }
        page_local_branches.append(evidence)

    if not page_local_branches:
        # A source unit with no text has no selectable evidence. Keep the schema
        # provider-compatible (empty unions/enums are rejected) and make both
        # evidence-bearing collections deterministically empty instead.
        for section_name in ("structure", "claims"):
            schema["properties"][section_name]["maxItems"] = 0
        return schema

    definition_name = "page_local_core_evidence"
    schema["$defs"] = {
        definition_name: {
            "oneOf": page_local_branches,
        }
    }
    evidence_reference = {"$ref": f"#/$defs/{definition_name}"}
    for section_name in ("structure", "claims"):
        schema["properties"][section_name]["items"]["properties"][
            "evidence"
        ] = deepcopy(evidence_reference)
    return schema


def claim_response_schema_metrics(
    request: ProviderClaimRequest,
) -> ClaimResponseSchemaMetrics:
    """Measure schema growth without exposing source text or handle values."""

    handles_by_core_page = _request_core_page_handles(request)
    before = _claim_response_schema_for_core_pages(request.core_page_refs)
    after = claim_response_schema(request)
    before_bytes = len(_canonical_json(before).encode("utf-8"))
    after_bytes = len(_canonical_json(after).encode("utf-8"))
    growth_bytes = after_bytes - before_bytes
    core_handle_count = sum(len(handles) for _, handles in handles_by_core_page)
    context_handle_count = sum(
        len(page.evidence) for page in request.pages if page.role == "context"
    )
    return ClaimResponseSchemaMetrics(
        schema_before_bytes=before_bytes,
        schema_after_bytes=after_bytes,
        schema_growth_bytes=growth_bytes,
        schema_growth_percent=round((growth_bytes / before_bytes) * 100.0, 6),
        core_page_count=len(handles_by_core_page),
        core_handle_count=core_handle_count,
        context_handle_count=context_handle_count,
        handle_enum_entry_count=core_handle_count,
        largest_page_handle_enum=max(
            (len(handles) for _, handles in handles_by_core_page),
            default=0,
        ),
        handles_per_core_page=tuple(
            (page_number, len(handles))
            for page_number, handles in handles_by_core_page
        ),
    )


CLAIM_ANALYSIS_SYSTEM_PROMPT = """\
Extract evidence-linked Protocol claims from only the supplied core pages.
Context pages are read-only continuity context: never emit a claim or marker
whose evidence page is context-only. Return the small claim schema supplied by
the caller, not an ExperimentProtocol and not a summary.

Every scientific or execution fact must be its own claim. Categories include
material, equipment, action, quantity, concentration, temperature, duration,
agitation_speed, prerequisite, warning_hazard, observation_checkpoint,
repeat_condition, and explicit_missing_ambiguous_value. Never combine distinct
values into evidence-free fields. For each distinct explicit numbered source
action on every core page, emit a distinct action claim that preserves that
action's source step label, step identity, section identity, and direct evidence.
Never omit or merge numbered source actions. Quantity, duration, prerequisite,
warning_hazard, explicit_missing_ambiguous_value, and all other non-action claims
may coexist with an action claim but never substitute for it. If every numbered
source action cannot be represented, mark that page analysis_incomplete.
Parameter claims must target the action, material, or equipment claim they
qualify. Use stable identifiers across page boundaries when a context page shows
the beginning of the same source step.

Each page is supplied as ordered segment pairs. For every claim and structural
marker, cite a one-based core source page and select one or more directly
adjacent evidence_segment_ids in source order. Never return source_excerpt text.
Never return source_text. The server resolves the selected handles to the exact
immutable excerpt and uses that exact excerpt as canonical source_text. Do not
normalize whitespace, repair OCR, paraphrase, translate, merge non-adjacent
passages, infer missing values, or add scientific knowledge.
Explicit source ambiguity or a missing execution value is a blocking claim,
never a guessed value.

Each page contains ordered [handle, exact_text] segment pairs. Segment handles
and request_handle are opaque, request-scoped server identities. Copy only the
selected adjacent handles and the exact request_handle; never calculate,
derive, normalize, shorten, alter, or invent an identity.

Return exactly one coverage record for every core page. Mark it complete when
all relevant claims and markers on that page were extracted, no_relevant_claims
only when the page contains none, and analysis_incomplete whenever the page
cannot be fully accounted for. evidence_item_ids must exactly list the claim and
marker identifiers emitted from that page. Return one JSON object only.
"""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _page_text_sha256(extraction: ProtocolPdfExtraction, page_number: int) -> str:
    return hashlib.sha256(
        extraction.pages[page_number - 1].text.encode("utf-8")
    ).hexdigest()


def _numbered_action_matches(page_text: str) -> tuple[re.Match[str], ...]:
    matches = sorted(
        (
            *_NUMBERED_SOURCE_LINE.finditer(page_text),
            *_INLINE_NUMBERED_SOURCE.finditer(page_text),
        ),
        key=lambda match: match.start("label"),
    )
    actions: list[re.Match[str]] = []
    seen_offsets: set[int] = set()
    for match in matches:
        following = match.group("next").strip(".,:;()[]{}").casefold()
        offset = match.start("label")
        if (
            following.isdecimal()
            or following in _VALUE_UNITS
            or offset in seen_offsets
        ):
            continue
        actions.append(match)
        seen_offsets.add(offset)
    return tuple(actions)


def _bounded_action_block_boundaries(page_text: str) -> tuple[int, ...]:
    """Return exact action-block boundaries with a deterministic size ceiling."""

    coarse = sorted(
        {
            0,
            len(page_text),
            *(match.start("label") for match in _numbered_action_matches(page_text)),
        }
    )
    bounded = [coarse[0]]
    for block_end in coarse[1:]:
        block_start = bounded[-1]
        while block_end - block_start > _MAX_PROVIDER_SEGMENT_CHARS:
            hard_end = block_start + _MAX_PROVIDER_SEGMENT_CHARS
            newline = page_text.rfind("\n", block_start + 1, hard_end + 1)
            split_at = newline + 1 if newline >= block_start + 1 else hard_end
            bounded.append(split_at)
            block_start = split_at
        if bounded[-1] != block_end:
            bounded.append(block_end)
    return tuple(bounded)


def generate_page_evidence_segments(
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    page_number: int,
) -> tuple[ProtocolEvidenceSegment, ...]:
    """Split one page into exact, bounded numbered-action blocks."""

    if (
        not isinstance(source_revision, str)
        or not source_revision.strip()
        or not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or page_number < 1
        or page_number > extraction.page_count
    ):
        raise ValueError("Evidence segment source identity is invalid.")
    page_text = extraction.pages[page_number - 1].text
    ordered_boundaries = _bounded_action_block_boundaries(page_text)
    segment_texts = tuple(
        page_text[start:end]
        for start, end in zip(ordered_boundaries, ordered_boundaries[1:])
        if end > start
    )
    if "".join(segment_texts) != page_text:
        raise ValueError("Evidence segments do not reconstruct the source page.")
    page_hash = _page_text_sha256(extraction, page_number)
    segments: list[ProtocolEvidenceSegment] = []
    for segment_index, text in enumerate(segment_texts):
        identity = {
            "evidence_segment_version": EVIDENCE_SEGMENT_VERSION,
            "source_revision": source_revision,
            "source_sha256": extraction.sha256,
            "source_page_number": page_number,
            "page_text_sha256": page_hash,
            "segment_index": segment_index,
            "segment_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        segment_id = "seg-" + hashlib.sha256(
            _canonical_json(identity).encode("utf-8")
        ).hexdigest()
        segments.append(
            ProtocolEvidenceSegment(
                segment_id=segment_id,
                source_revision=source_revision,
                source_sha256=extraction.sha256,
                source_page_number=page_number,
                page_text_sha256=page_hash,
                segment_index=segment_index,
                text=text,
            )
        )
    return tuple(segments)


def serialize_chunk_claim_analysis(
    analysis: ProtocolChunkClaimAnalysis,
) -> tuple[str, str]:
    payload = _canonical_json(analysis.public_dict())
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compact_handle(prefix: str, identity: object, *, digest_bytes: int) -> str:
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).digest()
    encoded = urlsafe_b64encode(digest[:digest_bytes]).decode("ascii").rstrip("=")
    return prefix + encoded


def prepare_chunk_claim_request_context(
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    chunk_id: str,
    ordinal: int,
    core_page_refs: tuple[int, ...],
    context_page_refs: tuple[int, ...],
    capability_policy_id: str = domain.P1_CAPABILITY_POLICY.profile_id,
) -> ProviderClaimRequest:
    page_numbers = context_page_refs + core_page_refs
    canonical_pages = tuple(
        (
            page_number,
            generate_page_evidence_segments(
                extraction,
                source_revision=source_revision,
                page_number=page_number,
            ),
        )
        for page_number in page_numbers
    )
    request_identity = {
        "claim_schema_version": CLAIM_SCHEMA_VERSION,
        "capability_policy_id": capability_policy_id,
        "source_revision": source_revision,
        "source_sha256": extraction.sha256,
        "chunk_id": chunk_id,
        "ordinal": ordinal,
        "core_page_refs": core_page_refs,
        "context_page_refs": context_page_refs,
        "pages": [
            {
                "source_page_number": page_number,
                "page_text_sha256": _page_text_sha256(extraction, page_number),
                "segment_ids": [segment.segment_id for segment in segments],
            }
            for page_number, segments in canonical_pages
        ],
    }
    request_handle = _compact_handle("r-", request_identity, digest_bytes=16)
    seen_handles: set[str] = set()
    pages: list[ProviderEvidencePage] = []
    core_pages = frozenset(core_page_refs)
    for page_number, segments in canonical_pages:
        evidence: list[ProviderEvidenceHandle] = []
        for segment in segments:
            handle = _compact_handle(
                "s-",
                {
                    "request_handle": request_handle,
                    "canonical_segment_id": segment.segment_id,
                },
                digest_bytes=12,
            )
            if handle in seen_handles:
                raise ValueError("Provider evidence handle collision.")
            seen_handles.add(handle)
            evidence.append(ProviderEvidenceHandle(handle=handle, segment=segment))
        pages.append(
            ProviderEvidencePage(
                source_page_number=page_number,
                role="core" if page_number in core_pages else "context",
                evidence=tuple(evidence),
            )
        )
    return ProviderClaimRequest(
        request_handle=request_handle,
        capability_policy_id=capability_policy_id,
        source_revision=source_revision,
        source_sha256=extraction.sha256,
        chunk_id=chunk_id,
        ordinal=ordinal,
        core_page_refs=core_page_refs,
        context_page_refs=context_page_refs,
        pages=tuple(pages),
    )


def prepare_chunk_claim_request(
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    chunk_id: str,
    ordinal: int,
    core_page_refs: tuple[int, ...],
    context_page_refs: tuple[int, ...],
    capability_policy_id: str = domain.P1_CAPABILITY_POLICY.profile_id,
) -> str:
    return prepare_chunk_claim_request_context(
        extraction,
        source_revision=source_revision,
        chunk_id=chunk_id,
        ordinal=ordinal,
        core_page_refs=core_page_refs,
        context_page_refs=context_page_refs,
        capability_policy_id=capability_policy_id,
    ).input_json()


def _expect_record(value: object, expected: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProtocolAnalysisResponseError(
            f"Chunk claim response has a malformed {location} record."
        )
    return value


def _required_text(value: object, location: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_TEXT_CHARS
    ):
        raise ProtocolAnalysisResponseError(
            f"Chunk claim response has invalid {location} text."
        )
    return value


def _optional_identifier(value: object, location: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, location)


def _identifier(value: object, location: str) -> str:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ProtocolAnalysisResponseError(
            f"Chunk claim response has an invalid {location} identifier."
        )
    return value


def resolve_claim_source_evidence(
    raw: object,
    *,
    request: ProviderClaimRequest,
    item_index: int | None = None,
    item_type: str | None = None,
    category: str | None = None,
) -> ClaimSourceEvidence:
    value = _expect_record(
        raw,
        {
            "source_page_number",
            "evidence_segment_ids",
        },
        "evidence",
    )
    page_number = value["source_page_number"]
    candidate_handles = value["evidence_segment_ids"]
    candidate_handle_count = (
        len(candidate_handles) if isinstance(candidate_handles, list) else None
    )
    valid_page = (
        isinstance(page_number, int)
        and not isinstance(page_number, bool)
        and page_number in request.core_page_refs
    )
    if not valid_page:
        mismatch_class = (
            "context_evidence_for_core_item"
            if isinstance(page_number, int)
            and page_number in request.context_page_refs
            else "source_identity_mismatch"
        )
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim evidence cites a page outside the active core chunk.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class=mismatch_class,
                evidence_index=item_index,
                evidence_type=item_type,
                category=category,
                page_number=(page_number if isinstance(page_number, int) else None),
                provider_handle_count=candidate_handle_count,
                chunk_id=request.chunk_id,
                source_revision=request.source_revision,
                source_hash=request.source_sha256,
            ),
        )
    raw_segment_ids = value["evidence_segment_ids"]
    if (
        not isinstance(raw_segment_ids, list)
        or not 1 <= len(raw_segment_ids) <= _MAX_EVIDENCE_SEGMENTS_PER_SPAN
        or any(
            not isinstance(segment_id, str)
            or not _STABLE_ID.fullmatch(segment_id)
            for segment_id in raw_segment_ids
        )
    ):
        raise ProtocolAnalysisResponseError(
            "Chunk claim response has an invalid evidence segment list."
        )
    provider_handles = tuple(raw_segment_ids)
    matching_pages = tuple(
        item for item in request.pages if item.source_page_number == page_number
    )
    if len(matching_pages) != 1:
        raise ProtocolAnalysisEvidenceError(
            "Active provider evidence mapping has invalid page identity.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class="source_identity_mismatch",
                evidence_index=item_index,
                evidence_type=item_type,
                category=category,
                page_number=page_number,
                provider_handle_count=len(provider_handles),
                chunk_id=request.chunk_id,
                source_revision=request.source_revision,
                source_hash=request.source_sha256,
            ),
        )
    page = matching_pages[0]
    mapped_evidence = tuple(
        item for request_page in request.pages for item in request_page.evidence
    )
    handle_map = {item.handle: item.segment for item in mapped_evidence}
    if len(handle_map) != len(mapped_evidence):
        raise ProtocolAnalysisEvidenceError(
            "Active provider evidence mapping contains a handle collision.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="evidence_segment_unknown",
                mismatch_class="source_identity_mismatch",
                evidence_index=item_index,
                evidence_type=item_type,
                category=category,
                page_number=page_number,
                provider_handle_count=len(provider_handles),
                chunk_id=request.chunk_id,
                source_revision=request.source_revision,
                source_hash=request.source_sha256,
            ),
        )
    if any(handle not in handle_map for handle in provider_handles):
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim evidence contains an unknown or stale request handle.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="evidence_segment_unknown",
                mismatch_class="source_identity_mismatch",
                evidence_index=item_index,
                evidence_type=item_type,
                category=category,
                page_number=page_number,
                provider_handle_count=len(provider_handles),
                chunk_id=request.chunk_id,
                source_revision=request.source_revision,
                source_hash=request.source_sha256,
            ),
        )
    selected = tuple(handle_map[handle] for handle in provider_handles)
    if any(segment.source_page_number != page_number for segment in selected):
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim evidence handle belongs to a different source page.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class="provider_handle_page_mismatch",
                evidence_index=item_index,
                evidence_type=item_type,
                category=category,
                page_number=page_number,
                provider_handle_count=len(provider_handles),
                expected_page_number=page_number,
                chunk_id=request.chunk_id,
                source_revision=request.source_revision,
                source_hash=request.source_sha256,
            ),
        )
    page_positions = {
        item.segment.segment_id: item.segment.segment_index for item in page.evidence
    }
    positions = tuple(page_positions[segment.segment_id] for segment in selected)
    expected_positions = tuple(range(positions[0], positions[-1] + 1))
    if positions != expected_positions:
        if len(set(provider_handles)) != len(provider_handles):
            mismatch_class = "duplicate_provider_handle_selection"
        elif positions != tuple(sorted(positions)):
            mismatch_class = "reversed_provider_handle_selection"
        else:
            mismatch_class = "non_contiguous_source_evidence"
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim evidence segments are reversed or non-contiguous.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="evidence_segment_range_invalid",
                mismatch_class=mismatch_class,
                evidence_index=item_index,
                evidence_type=item_type,
                category=category,
                page_number=page_number,
                provider_handle_count=len(provider_handles),
                expected_count=len(expected_positions),
                actual_count=len(positions),
                chunk_id=request.chunk_id,
                source_revision=request.source_revision,
                source_hash=request.source_sha256,
            ),
        )
    excerpt = "".join(segment.text for segment in selected)
    if not excerpt.strip() or len(excerpt) > _MAX_TEXT_CHARS:
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim evidence resolves to an invalid source span.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="evidence_segment_range_invalid",
                mismatch_class="invalid_source_evidence",
                evidence_index=item_index,
                evidence_type=item_type,
                category=category,
                page_number=page_number,
                provider_handle_count=len(provider_handles),
                actual_length=len(excerpt),
                chunk_id=request.chunk_id,
                source_revision=request.source_revision,
                source_hash=request.source_sha256,
            ),
        )
    return ClaimSourceEvidence(
        source_revision=request.source_revision,
        source_sha256=request.source_sha256,
        source_page_number=page_number,
        page_text_sha256=selected[0].page_text_sha256,
        evidence_segment_ids=tuple(segment.segment_id for segment in selected),
        source_excerpt=excerpt,
    )


def _decode_evidence(
    raw: object,
    *,
    request: ProviderClaimRequest,
    item_index: int | None = None,
    item_type: str | None = None,
    category: str | None = None,
) -> ClaimSourceEvidence:
    return resolve_claim_source_evidence(
        raw,
        request=request,
        item_index=item_index,
        item_type=item_type,
        category=category,
    )


def _resolve_canonical_claim_source_evidence(
    evidence: ClaimSourceEvidence,
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    core_pages: frozenset[int],
    chunk_id: str,
) -> ClaimSourceEvidence:
    page_number = evidence.source_page_number
    if (
        evidence.source_revision != source_revision
        or evidence.source_sha256 != extraction.sha256
        or not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or page_number not in core_pages
        or evidence.page_text_sha256 != _page_text_sha256(extraction, page_number)
    ):
        raise ProtocolAnalysisEvidenceError(
            "Canonical chunk claim evidence has stale source identity.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="decoded_chunk_claim_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class="source_identity_mismatch",
                page_number=page_number if isinstance(page_number, int) else None,
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
            ),
        )
    segments = generate_page_evidence_segments(
        extraction,
        source_revision=source_revision,
        page_number=page_number,
    )
    segment_map = {segment.segment_id: segment for segment in segments}
    if (
        not evidence.evidence_segment_ids
        or len(evidence.evidence_segment_ids) > _MAX_EVIDENCE_SEGMENTS_PER_SPAN
        or any(
            not isinstance(segment_id, str)
            or not _STABLE_ID.fullmatch(segment_id)
            or segment_id not in segment_map
            for segment_id in evidence.evidence_segment_ids
        )
    ):
        raise ProtocolAnalysisEvidenceError(
            "Canonical chunk claim evidence contains a stale segment identity.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="decoded_chunk_claim_validation",
                reason_code="evidence_segment_unknown",
                mismatch_class="source_identity_mismatch",
                page_number=page_number,
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
            ),
        )
    selected = tuple(segment_map[item] for item in evidence.evidence_segment_ids)
    positions = tuple(segment.segment_index for segment in selected)
    if positions != tuple(range(positions[0], positions[-1] + 1)):
        raise ProtocolAnalysisEvidenceError(
            "Canonical chunk claim evidence is reversed or non-contiguous.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="decoded_chunk_claim_validation",
                reason_code="evidence_segment_range_invalid",
                mismatch_class="non_contiguous_source_evidence",
                page_number=page_number,
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
            ),
        )
    excerpt = "".join(segment.text for segment in selected)
    if not excerpt.strip() or len(excerpt) > _MAX_TEXT_CHARS:
        raise ProtocolAnalysisEvidenceError(
            "Canonical chunk claim evidence resolves to an invalid source span.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="decoded_chunk_claim_validation",
                reason_code="evidence_segment_range_invalid",
                mismatch_class="invalid_source_evidence",
                page_number=page_number,
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
            ),
        )
    return ClaimSourceEvidence(
        source_revision=source_revision,
        source_sha256=extraction.sha256,
        source_page_number=page_number,
        page_text_sha256=selected[0].page_text_sha256,
        evidence_segment_ids=tuple(segment.segment_id for segment in selected),
        source_excerpt=excerpt,
    )


def _source_order(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProtocolAnalysisResponseError(
            "Chunk claim response has an invalid source order."
        )
    return value


def _numbered_step_labels(page_text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            match.group("label") for match in _numbered_action_matches(page_text)
        )
    )


def parse_chunk_claim_response(
    raw_response: str,
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    chunk_id: str,
    core_page_refs: tuple[int, ...],
    request: ProviderClaimRequest,
    capability_policy_id: str = domain.P1_CAPABILITY_POLICY.profile_id,
) -> ProtocolChunkClaimAnalysis:
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise ProtocolAnalysisResponseError("Chunk claim response is empty.")
    if (
        len(raw_response) > MAX_CHUNK_CLAIM_RESPONSE_BYTES
        or len(raw_response.encode("utf-8")) > MAX_CHUNK_CLAIM_RESPONSE_BYTES
    ):
        raise ProtocolAnalysisResponseError(
            "Chunk claim response exceeds the bounded response limit."
        )
    try:
        raw = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise ProtocolAnalysisResponseError(
            "Chunk claim response is not exactly one JSON object."
        ) from exc
    value = _expect_record(
        raw,
        {
            "claim_schema_version",
            "capability_policy_id",
            "request_handle",
            "page_coverage",
            "structure",
            "claims",
        },
        "root",
    )
    if (
        request.source_revision != source_revision
        or request.source_sha256 != extraction.sha256
        or request.chunk_id != chunk_id
        or request.core_page_refs != core_page_refs
        or request.capability_policy_id != capability_policy_id
    ):
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim response does not match the active source and chunk.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_envelope_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class="source_identity_mismatch",
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
            ),
        )
    if value["request_handle"] != request.request_handle:
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim response does not match the active request.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_envelope_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class="request_handle_mismatch",
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
            ),
        )
    if (
        value["claim_schema_version"] != CLAIM_SCHEMA_VERSION
        or value["capability_policy_id"] != capability_policy_id
    ):
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim response uses the wrong analysis contract.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_envelope_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class="claim_contract_identity_mismatch",
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
            ),
        )
    raw_structure = value["structure"]
    raw_claims = value["claims"]
    raw_coverage = value["page_coverage"]
    if (
        not isinstance(raw_structure, list)
        or len(raw_structure) > _MAX_MARKERS_PER_CHUNK
        or not isinstance(raw_claims, list)
        or len(raw_claims) > _MAX_CLAIMS_PER_CHUNK
        or not isinstance(raw_coverage, list)
        or len(raw_coverage) > MAX_PAGE_COVERAGE_RECORDS
    ):
        raise ProtocolAnalysisResponseError(
            "Chunk claim response exceeds a bounded record limit."
        )
    core_pages = frozenset(core_page_refs)
    markers: list[ProtocolStructureMarker] = []
    for item_index, item in enumerate(raw_structure):
        record = _expect_record(
            item,
            {
                "marker_id",
                "kind",
                "source_order",
                "section_id",
                "evidence",
            },
            "structure marker",
        )
        try:
            kind = StructureMarkerKind(record["kind"])
        except (TypeError, ValueError) as exc:
            raise ProtocolAnalysisResponseError(
                "Chunk claim response has an unsupported structure marker.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_claim_record_validation",
                    reason_code="unsupported_structure_marker",
                    mismatch_class="semantic_contract_violation",
                    evidence_index=item_index,
                    evidence_type="structure_marker",
                ),
            ) from exc
        evidence = _decode_evidence(
            record["evidence"],
            request=request,
            item_index=item_index,
            item_type="structure_marker",
            category=kind.value,
        )
        section_id = _optional_identifier(record["section_id"], "section")
        if (kind is StructureMarkerKind.SECTION) != (section_id is not None):
            raise ProtocolAnalysisResponseError(
                "Chunk structure marker has inconsistent section identity."
            )
        markers.append(
            ProtocolStructureMarker(
                marker_id=_identifier(record["marker_id"], "marker"),
                kind=kind,
                source_order=_source_order(record["source_order"]),
                source_text=evidence.source_excerpt,
                section_id=section_id,
                evidence=evidence,
            )
        )
    claims: list[ProtocolClaim] = []
    for item_index, item in enumerate(raw_claims):
        record = _expect_record(
            item,
            {
                "claim_id",
                "category",
                "source_order",
                "section_id",
                "step_id",
                "source_label",
                "target_claim_id",
                "required_for_execution",
                "evidence",
            },
            "claim",
        )
        try:
            category = ClaimCategory(record["category"])
        except (TypeError, ValueError) as exc:
            raise ProtocolAnalysisResponseError(
                "Chunk claim response has an unsupported claim category.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_claim_record_validation",
                    reason_code="unsupported_claim_category",
                    mismatch_class="semantic_contract_violation",
                    evidence_index=item_index,
                    evidence_type="claim",
                ),
            ) from exc
        evidence = _decode_evidence(
            record["evidence"],
            request=request,
            item_index=item_index,
            item_type="claim",
            category=category.value,
        )
        required = record["required_for_execution"]
        if not isinstance(required, bool):
            raise ProtocolAnalysisResponseError(
                "Chunk claim response has an invalid execution requirement."
            )
        claims.append(
            ProtocolClaim(
                claim_id=_identifier(record["claim_id"], "claim"),
                category=category,
                source_order=_source_order(record["source_order"]),
                source_text=evidence.source_excerpt,
                section_id=_optional_identifier(record["section_id"], "section"),
                step_id=_optional_identifier(record["step_id"], "step"),
                source_label=(
                    _required_text(record["source_label"], "source label")
                    if record["source_label"] is not None
                    else None
                ),
                target_claim_id=_optional_identifier(
                    record["target_claim_id"], "target claim"
                ),
                required_for_execution=required,
                evidence=evidence,
            )
        )
    identifiers = [item.marker_id for item in markers] + [
        item.claim_id for item in claims
    ]
    if len(set(identifiers)) != len(identifiers):
        raise ProtocolAnalysisResponseError(
            "Chunk claim response contains duplicate evidence item identifiers.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_relationship_validation",
                reason_code="duplicate_evidence_item_identifier",
                mismatch_class="duplicate_or_conflicting_record",
                evidence_type="evidence_item",
                expected_count=len(set(identifiers)),
                actual_count=len(identifiers),
            ),
        )
    coverage: list[ProtocolPageClaimCoverage] = []
    for item_index, item in enumerate(raw_coverage):
        record = _expect_record(
            item,
            {
                "source_page_number",
                "status",
                "evidence_item_ids",
            },
            "page coverage",
        )
        try:
            status = PageCoverageStatus(record["status"])
        except (TypeError, ValueError) as exc:
            raise ProtocolAnalysisResponseError(
                "Chunk claim response has an unsupported coverage status.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_page_coverage_validation",
                    reason_code="unsupported_coverage_status",
                    mismatch_class="semantic_contract_violation",
                    evidence_index=item_index,
                    evidence_type="page_coverage",
                    page_coverage_count=len(raw_coverage),
                ),
            ) from exc
        page_number = record["source_page_number"]
        item_ids = record["evidence_item_ids"]
        if (
            isinstance(item_ids, list)
            and len(item_ids) > MAX_EVIDENCE_ITEM_REFS_PER_PAGE
        ):
            raise ProtocolAnalysisEvidenceError(
                "Chunk page coverage exceeds the bounded evidence reference limit.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_page_coverage_validation",
                    reason_code="coverage_mismatch",
                    mismatch_class="coverage_reference_cardinality_exceeded",
                    evidence_index=item_index,
                    evidence_type="page_coverage",
                    page_number=(
                        page_number if isinstance(page_number, int) else None
                    ),
                    expected_count=MAX_EVIDENCE_ITEM_REFS_PER_PAGE,
                    actual_count=len(item_ids),
                    page_coverage_count=len(raw_coverage),
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            )
        if (
            isinstance(item_ids, list)
            and all(isinstance(item_id, str) for item_id in item_ids)
            and len(set(item_ids)) != len(item_ids)
        ):
            raise ProtocolAnalysisEvidenceError(
                "Chunk page coverage contains duplicate evidence references.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_page_coverage_validation",
                    reason_code="coverage_mismatch",
                    mismatch_class="duplicate_coverage_reference",
                    evidence_index=item_index,
                    evidence_type="page_coverage",
                    page_number=(
                        page_number if isinstance(page_number, int) else None
                    ),
                    expected_count=len(set(item_ids)),
                    actual_count=len(item_ids),
                    page_coverage_count=len(raw_coverage),
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            )
        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number not in core_pages
            or not isinstance(item_ids, list)
            or any(not isinstance(item_id, str) for item_id in item_ids)
        ):
            raise ProtocolAnalysisEvidenceError(
                "Chunk page coverage does not match the immutable source.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_page_coverage_validation",
                    reason_code="chunk_identity_mismatch",
                    mismatch_class="source_identity_mismatch",
                    evidence_index=item_index,
                    evidence_type="page_coverage",
                    page_number=(page_number if isinstance(page_number, int) else None),
                    expected_count=len(core_pages),
                    actual_count=len(raw_coverage),
                    page_coverage_count=len(raw_coverage),
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            )
        expected_page_hash = _page_text_sha256(extraction, page_number)
        coverage.append(
            ProtocolPageClaimCoverage(
                source_revision=source_revision,
                source_sha256=extraction.sha256,
                source_page_number=page_number,
                page_text_sha256=expected_page_hash,
                status=status,
                evidence_item_ids=tuple(item_ids),
            )
        )
    analysis = ProtocolChunkClaimAnalysis(
        claim_schema_version=CLAIM_SCHEMA_VERSION,
        capability_policy_id=capability_policy_id,
        source_revision=source_revision,
        source_sha256=extraction.sha256,
        chunk_id=chunk_id,
        page_coverage=tuple(coverage),
        structure=tuple(markers),
        claims=tuple(claims),
    )
    validate_chunk_claim_analysis(
        analysis,
        extraction,
        source_revision=source_revision,
        chunk_id=chunk_id,
        core_page_refs=core_page_refs,
    )
    return analysis


def validate_chunk_claim_analysis(
    analysis: ProtocolChunkClaimAnalysis,
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    chunk_id: str,
    core_page_refs: tuple[int, ...],
) -> None:
    """Revalidate a decoded DTO without trusting its construction path."""

    if (
        analysis.claim_schema_version != CLAIM_SCHEMA_VERSION
        or analysis.capability_policy_id != domain.P1_CAPABILITY_POLICY.profile_id
        or analysis.source_revision != source_revision
        or analysis.source_sha256 != extraction.sha256
        or analysis.chunk_id != chunk_id
    ):
        raise ProtocolAnalysisEvidenceError(
            "Decoded chunk claims do not match the active analysis run.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="decoded_chunk_claim_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class="source_identity_mismatch",
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
            ),
        )
    core = frozenset(core_page_refs)
    item_pages: dict[str, int] = {}
    indexed_items = (
        *(
            ("structure_marker", index, item)
            for index, item in enumerate(analysis.structure)
        ),
        *(
            ("claim", index, item)
            for index, item in enumerate(analysis.claims)
        ),
    )
    for item_type, item_index, item in indexed_items:
        item_category = (
            item.kind.value
            if isinstance(item, ProtocolStructureMarker)
            else (
                item.category.value
                if isinstance(item, ProtocolClaim)
                else None
            )
        )
        if isinstance(item, ProtocolStructureMarker):
            if (
                not _STABLE_ID.fullmatch(item.marker_id)
                or not isinstance(item.kind, StructureMarkerKind)
                or not isinstance(item.source_order, int)
                or isinstance(item.source_order, bool)
                or item.source_order < 0
                or not isinstance(item.source_text, str)
                or not item.source_text.strip()
                or len(item.source_text) > _MAX_TEXT_CHARS
                or (
                    item.kind is StructureMarkerKind.SECTION
                    and (
                        item.section_id is None
                        or not _STABLE_ID.fullmatch(item.section_id)
                    )
                )
                or (
                    item.kind is StructureMarkerKind.PROTOCOL_TITLE
                    and item.section_id is not None
                )
            ):
                raise ProtocolAnalysisResponseError(
                    "Decoded chunk structure marker is malformed."
                )
        elif isinstance(item, ProtocolClaim):
            optional_ids = (
                item.section_id,
                item.step_id,
                item.target_claim_id,
            )
            if (
                not _STABLE_ID.fullmatch(item.claim_id)
                or not isinstance(item.category, ClaimCategory)
                or not isinstance(item.source_order, int)
                or isinstance(item.source_order, bool)
                or item.source_order < 0
                or not isinstance(item.source_text, str)
                or not item.source_text.strip()
                or len(item.source_text) > _MAX_TEXT_CHARS
                or any(
                    value is not None and not _STABLE_ID.fullmatch(value)
                    for value in optional_ids
                )
                or (
                    item.source_label is not None
                    and (
                        not isinstance(item.source_label, str)
                        or not item.source_label.strip()
                        or len(item.source_label) > _MAX_TEXT_CHARS
                    )
                )
                or not isinstance(item.required_for_execution, bool)
            ):
                raise ProtocolAnalysisResponseError(
                    "Decoded Protocol claim is malformed."
                )
        else:
            raise ProtocolAnalysisResponseError(
                "Decoded chunk claims contain an unsupported record."
            )
        identifier = (
            item.marker_id if isinstance(item, ProtocolStructureMarker) else item.claim_id
        )
        if identifier in item_pages:
            raise ProtocolAnalysisResponseError(
                "Decoded chunk claims contain duplicate identifiers.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_claim_relationship_validation",
                    reason_code="duplicate_evidence_item_identifier",
                    mismatch_class="duplicate_or_conflicting_record",
                    evidence_index=item_index,
                    evidence_type=item_type,
                    category=item_category,
                ),
            )
        evidence = item.evidence
        if not isinstance(evidence, ClaimSourceEvidence):
            raise ProtocolAnalysisResponseError(
                "Decoded chunk claim evidence is malformed."
            )
        try:
            resolved_evidence = _resolve_canonical_claim_source_evidence(
                evidence,
                extraction,
                source_revision=source_revision,
                core_pages=core,
                chunk_id=chunk_id,
            )
        except ProtocolAnalysisEvidenceError as exc:
            exc.enrich_diagnostic(
                evidence_index=item_index,
                evidence_type=item_type,
                category=item_category,
            )
            raise
        except ProtocolAnalysisResponseError as exc:
            raise ProtocolAnalysisEvidenceError(
                "Decoded chunk claim evidence failed span revalidation.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="decoded_chunk_claim_validation",
                    reason_code="evidence_segment_range_invalid",
                    mismatch_class="claim_evidence_mismatch",
                    evidence_index=item_index,
                    evidence_type=item_type,
                    category=item_category,
                    page_number=evidence.source_page_number,
                    provider_handle_count=len(evidence.evidence_segment_ids),
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            ) from exc
        if (
            evidence != resolved_evidence
            or evidence.source_revision != source_revision
            or evidence.source_sha256 != extraction.sha256
            or evidence.source_page_number not in core
            or evidence.source_excerpt
            not in extraction.pages[evidence.source_page_number - 1].text
            or item.source_text != evidence.source_excerpt
        ):
            raise ProtocolAnalysisEvidenceError(
                "Decoded chunk claim evidence failed exact revalidation.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="decoded_chunk_claim_validation",
                    reason_code="quote_not_found",
                    mismatch_class="claim_evidence_mismatch",
                    evidence_index=item_index,
                    evidence_type=item_type,
                    category=item_category,
                    page_number=evidence.source_page_number,
                    provider_handle_count=len(evidence.evidence_segment_ids),
                    expected_length=len(evidence.source_excerpt),
                    actual_length=len(item.source_text),
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            )
        item_pages[identifier] = evidence.source_page_number
    if any(
        not isinstance(item, ProtocolPageClaimCoverage)
        for item in analysis.page_coverage
    ):
        raise ProtocolAnalysisResponseError(
            "Decoded chunk page coverage is malformed."
        )
    coverage_by_page = {item.source_page_number: item for item in analysis.page_coverage}
    if len(coverage_by_page) != len(analysis.page_coverage) or set(coverage_by_page) != core:
        raise ProtocolAnalysisResponseError(
            "Chunk claims do not account for every core source page exactly once.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_page_coverage_validation",
                reason_code="coverage_mismatch",
                mismatch_class="incomplete_or_duplicate_page_coverage",
                evidence_type="page_coverage",
                expected_count=len(core),
                actual_count=len(analysis.page_coverage),
                page_coverage_count=len(analysis.page_coverage),
            ),
        )
    for page_number in core_page_refs:
        coverage = coverage_by_page[page_number]
        numbered_labels = _numbered_step_labels(
            extraction.pages[page_number - 1].text
        )
        action_labels = {
            claim.source_label
            for claim in analysis.claims
            if claim.category is ClaimCategory.ACTION
            and claim.evidence.source_page_number == page_number
        }
        if not set(numbered_labels).issubset(action_labels):
            missing_numbered_actions = set(numbered_labels) - action_labels
            raise ProtocolAnalysisEvidenceError(
                "Chunk claims omit a numbered source action.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_page_coverage_validation",
                    reason_code="numbered_action_missing",
                    mismatch_class="claim_coverage_mismatch",
                    evidence_type="page_coverage",
                    page_number=page_number,
                    expected_count=len(numbered_labels),
                    actual_count=len(action_labels),
                    missing_numbered_action_count=len(missing_numbered_actions),
                    page_coverage_count=len(analysis.page_coverage),
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            )
        expected_ids = tuple(
            sorted(
                identifier
                for identifier, item_page in item_pages.items()
                if item_page == page_number
            )
        )
        if (
            not isinstance(coverage, ProtocolPageClaimCoverage)
            or not isinstance(coverage.status, PageCoverageStatus)
            or coverage.source_revision != source_revision
            or coverage.source_sha256 != extraction.sha256
            or coverage.page_text_sha256
            != _page_text_sha256(extraction, page_number)
            or tuple(sorted(coverage.evidence_item_ids)) != expected_ids
            or len(set(coverage.evidence_item_ids))
            != len(coverage.evidence_item_ids)
            or (
                coverage.status is PageCoverageStatus.COMPLETE
                and not expected_ids
            )
            or (
                coverage.status is PageCoverageStatus.NO_RELEVANT_CLAIMS
                and bool(expected_ids)
            )
        ):
            raise ProtocolAnalysisEvidenceError(
                "Chunk page coverage does not match its extracted evidence items.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_page_coverage_validation",
                    reason_code="coverage_mismatch",
                    mismatch_class="claim_coverage_mismatch",
                    evidence_type="page_coverage",
                    page_number=page_number,
                    expected_count=len(expected_ids),
                    actual_count=len(coverage.evidence_item_ids),
                    page_coverage_count=len(analysis.page_coverage),
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            )


def analyze_chunk_claims(
    extraction: ProtocolPdfExtraction,
    model: ProtocolAnalysisModel,
    *,
    source_revision: str,
    chunk_id: str,
    ordinal: int,
    core_page_refs: tuple[int, ...],
    context_page_refs: tuple[int, ...],
) -> ProtocolChunkClaimAnalysis:
    request = prepare_chunk_claim_request_context(
        extraction,
        source_revision=source_revision,
        chunk_id=chunk_id,
        ordinal=ordinal,
        core_page_refs=core_page_refs,
        context_page_refs=context_page_refs,
    )
    input_json = request.input_json()
    try:
        raw_response = model.analyze(
            system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
            input_json=input_json,
            response_schema=claim_response_schema(request),
        )
    except (ProtocolAnalysisEvidenceError, ProtocolAnalysisResponseError):
        raise
    except Exception as exc:
        raise ProtocolAnalysisModelError(
            "Protocol chunk claim model request failed."
        ) from exc
    return parse_chunk_claim_response(
        raw_response,
        extraction,
        source_revision=source_revision,
        chunk_id=chunk_id,
        core_page_refs=core_page_refs,
        request=request,
    )


def _source_sort_key(
    extraction: ProtocolPdfExtraction,
    item: ProtocolStructureMarker | ProtocolClaim,
) -> tuple[int, int, int, str]:
    evidence = item.evidence
    source_offset = extraction.pages[
        evidence.source_page_number - 1
    ].text.find(evidence.source_excerpt)
    kind_order = 0 if isinstance(item, ProtocolStructureMarker) else 1
    return (
        evidence.source_page_number,
        source_offset,
        item.source_order,
        f"{kind_order}:{item.marker_id if kind_order == 0 else item.claim_id}",
    )


def validate_whole_protocol_claims(
    extraction: ProtocolPdfExtraction,
    merged: MergedProtocolClaims,
) -> MergedProtocolClaims:
    """Validate completeness and references across all required source chunks."""

    if (
        merged.source_sha256 != extraction.sha256
        or merged.capability_policy_id != domain.P1_CAPABILITY_POLICY.profile_id
        or not merged.required_chunk_ids
        or len(set(merged.required_chunk_ids)) != len(merged.required_chunk_ids)
    ):
        raise ProtocolClaimConsistencyError("whole_source_identity_mismatch")
    expected_pages = set(range(1, extraction.page_count + 1))
    coverage_pages = {item.source_page_number for item in merged.page_coverage}
    if (
        len(merged.page_coverage) != extraction.page_count
        or coverage_pages != expected_pages
        or any(
            item.status is PageCoverageStatus.ANALYSIS_INCOMPLETE
            for item in merged.page_coverage
        )
    ):
        raise ProtocolClaimConsistencyError("incomplete_source_coverage")
    title_markers = tuple(
        item
        for item in merged.structure
        if item.kind is StructureMarkerKind.PROTOCOL_TITLE
    )
    if len(title_markers) != 1:
        raise ProtocolClaimConsistencyError("protocol_title_missing_or_conflicting")
    sections: dict[str, ProtocolStructureMarker] = {}
    for marker in merged.structure:
        if marker.kind is not StructureMarkerKind.SECTION:
            continue
        assert marker.section_id is not None
        prior = sections.get(marker.section_id)
        if prior is not None and prior != marker:
            raise ProtocolClaimConsistencyError("section_conflict")
        sections[marker.section_id] = marker
    claims_by_id: dict[str, ProtocolClaim] = {}
    for claim in merged.claims:
        prior = claims_by_id.get(claim.claim_id)
        if prior is not None and prior != claim:
            raise ProtocolClaimConsistencyError("claim_identity_conflict")
        claims_by_id[claim.claim_id] = claim
    actions = {
        claim.claim_id: claim
        for claim in merged.claims
        if claim.category is ClaimCategory.ACTION
    }
    step_identity: dict[str, tuple[str, str]] = {}
    source_labels: dict[tuple[str, str], str] = {}
    for action in actions.values():
        if (
            action.section_id is None
            or action.section_id not in sections
            or action.step_id is None
            or action.source_label is None
            or action.target_claim_id is not None
            or not action.required_for_execution
        ):
            raise ProtocolClaimConsistencyError("action_structure_invalid")
        identity = (action.section_id, action.source_label)
        prior_step = step_identity.get(action.step_id)
        if prior_step is not None and prior_step != identity:
            raise ProtocolClaimConsistencyError("step_identity_conflict")
        prior_id = source_labels.get(identity)
        if prior_id is not None and prior_id != action.step_id:
            raise ProtocolClaimConsistencyError("source_label_conflict")
        step_identity[action.step_id] = identity
        source_labels[identity] = action.step_id
    top_level = {
        ClaimCategory.MATERIAL,
        ClaimCategory.EQUIPMENT,
        ClaimCategory.PREREQUISITE,
    }
    parameter_targets = {
        ClaimCategory.QUANTITY: {ClaimCategory.ACTION, ClaimCategory.MATERIAL},
        ClaimCategory.CONCENTRATION: {
            ClaimCategory.ACTION,
            ClaimCategory.MATERIAL,
        },
        ClaimCategory.TEMPERATURE: {ClaimCategory.ACTION, ClaimCategory.EQUIPMENT},
        ClaimCategory.DURATION: {ClaimCategory.ACTION},
        ClaimCategory.AGITATION_SPEED: {
            ClaimCategory.ACTION,
            ClaimCategory.EQUIPMENT,
        },
        ClaimCategory.OBSERVATION_CHECKPOINT: {ClaimCategory.ACTION},
        ClaimCategory.REPEAT_CONDITION: {ClaimCategory.ACTION},
    }
    for claim in merged.claims:
        if claim.category in top_level:
            if any(
                value is not None
                for value in (
                    claim.section_id,
                    claim.step_id,
                    claim.source_label,
                    claim.target_claim_id,
                )
            ):
                raise ProtocolClaimConsistencyError("top_level_claim_scope_invalid")
            continue
        if claim.category is ClaimCategory.ACTION:
            continue
        target = (
            claims_by_id.get(claim.target_claim_id)
            if claim.target_claim_id is not None
            else None
        )
        allowed = parameter_targets.get(claim.category)
        if allowed is not None:
            if target is None or target.category not in allowed:
                raise ProtocolClaimConsistencyError("claim_target_invalid")
        elif claim.category is ClaimCategory.WARNING_HAZARD:
            if target is not None and target.category is not ClaimCategory.ACTION:
                raise ProtocolClaimConsistencyError("claim_target_invalid")
        elif claim.category is ClaimCategory.EXPLICIT_MISSING_AMBIGUOUS_VALUE:
            if not claim.required_for_execution or (
                target is not None and target.category is not ClaimCategory.ACTION
            ):
                raise ProtocolClaimConsistencyError("missing_value_scope_invalid")
        if target is not None and target.category is ClaimCategory.ACTION:
            if (
                claim.section_id != target.section_id
                or claim.step_id != target.step_id
                or claim.source_label is not None
                or claim.source_text not in target.evidence.source_excerpt
            ):
                raise ProtocolClaimConsistencyError("action_claim_scope_conflict")
        elif target is not None:
            if any(
                value is not None
                for value in (claim.section_id, claim.step_id, claim.source_label)
            ) or claim.source_text not in target.evidence.source_excerpt:
                raise ProtocolClaimConsistencyError("resource_claim_scope_conflict")
        elif claim.category not in {
            ClaimCategory.WARNING_HAZARD,
            ClaimCategory.EXPLICIT_MISSING_AMBIGUOUS_VALUE,
        }:
            raise ProtocolClaimConsistencyError("orphan_execution_claim")
    return merged


def _domain_evidence(evidence: ClaimSourceEvidence) -> domain.SourceEvidence:
    return domain.SourceEvidence(
        source_page_number=evidence.source_page_number,
        source_excerpt=evidence.source_excerpt,
        location_detail=(
            f"source_revision={evidence.source_revision};"
            f"source_sha256={evidence.source_sha256}"
        ),
    )


def _statement(claim: ProtocolClaim, prefix: str) -> domain.SourceStatement:
    return domain.SourceStatement(
        statement_id=f"{prefix}-{claim.claim_id}",
        source_text=claim.source_text,
        evidence=_domain_evidence(claim.evidence),
    )


def _children_by_target(
    claims: Iterable[ProtocolClaim],
) -> dict[str, tuple[ProtocolClaim, ...]]:
    grouped: dict[str, list[ProtocolClaim]] = {}
    for claim in claims:
        if claim.target_claim_id is not None:
            grouped.setdefault(claim.target_claim_id, []).append(claim)
    return {key: tuple(value) for key, value in grouped.items()}


def assemble_experiment_protocol(
    extraction: ProtocolPdfExtraction,
    merged: MergedProtocolClaims,
) -> ProtocolAnalysisDraft:
    """Deterministically map a complete, consistent claim set to the domain."""

    validate_whole_protocol_claims(extraction, merged)
    title = next(
        item
        for item in merged.structure
        if item.kind is StructureMarkerKind.PROTOCOL_TITLE
    )
    section_markers = tuple(
        item
        for item in merged.structure
        if item.kind is StructureMarkerKind.SECTION
    )
    children = _children_by_target(merged.claims)
    materials: list[domain.Material] = []
    equipment: list[domain.Equipment] = []
    prerequisites: list[domain.BeforeStartPrerequisite] = []
    constructs: list[domain.WorkflowConstruct] = []
    global_missing: list[ProtocolClaim] = []
    global_warnings: list[ProtocolClaim] = []
    for claim in merged.claims:
        if claim.category is ClaimCategory.MATERIAL:
            related = children.get(claim.claim_id, ())
            values = tuple(
                domain.ScientificValue(item.source_text)
                for item in related
                if item.category
                in {ClaimCategory.QUANTITY, ClaimCategory.CONCENTRATION}
            )
            materials.append(
                domain.Material(
                    material_id=claim.claim_id,
                    name_source_text=claim.source_text,
                    evidence=_domain_evidence(claim.evidence),
                    quantities=values,
                    conditions=tuple(
                        _statement(item, "material-claim") for item in related
                    ),
                )
            )
        elif claim.category is ClaimCategory.EQUIPMENT:
            related = children.get(claim.claim_id, ())
            equipment.append(
                domain.Equipment(
                    equipment_id=claim.claim_id,
                    name_source_text=claim.source_text,
                    evidence=_domain_evidence(claim.evidence),
                    settings=tuple(
                        domain.ScientificValue(item.source_text) for item in related
                    ),
                )
            )
        elif claim.category is ClaimCategory.PREREQUISITE:
            prerequisites.append(
                domain.BeforeStartPrerequisite(
                    prerequisite_id=claim.claim_id,
                    source_text=claim.source_text,
                    evidence=_domain_evidence(claim.evidence),
                )
            )
        elif (
            claim.category is ClaimCategory.WARNING_HAZARD
            and claim.target_claim_id is None
        ):
            global_warnings.append(claim)
        elif (
            claim.category is ClaimCategory.EXPLICIT_MISSING_AMBIGUOUS_VALUE
            and claim.target_claim_id is None
        ):
            global_missing.append(claim)
    prerequisites.extend(
        domain.BeforeStartPrerequisite(
            prerequisite_id=f"hazard-{claim.claim_id}",
            source_text=claim.source_text,
            evidence=_domain_evidence(claim.evidence),
        )
        for claim in global_warnings
    )
    constructs.extend(
        domain.SourceAmbiguity(
            ambiguity_id=claim.claim_id,
            source_text=claim.source_text,
            evidence=_domain_evidence(claim.evidence),
        )
        for claim in global_missing
    )
    actions_by_step: dict[str, list[ProtocolClaim]] = {}
    for claim in merged.claims:
        if claim.category is ClaimCategory.ACTION:
            assert claim.step_id is not None
            actions_by_step.setdefault(claim.step_id, []).append(claim)
    sections: list[domain.ProtocolSection] = []
    for marker in section_markers:
        assert marker.section_id is not None
        step_groups = [
            (step_id, actions)
            for step_id, actions in actions_by_step.items()
            if actions[0].section_id == marker.section_id
        ]
        step_groups.sort(key=lambda item: _source_sort_key(extraction, item[1][0]))
        steps: list[domain.ProtocolSourceStep] = []
        for step_id, action_claims in step_groups:
            action_claims.sort(key=lambda item: _source_sort_key(extraction, item))
            sub_actions: list[domain.ProtocolSubAction] = []
            for action_claim in action_claims:
                related = children.get(action_claim.claim_id, ())
                parameters = tuple(
                    item
                    for item in related
                    if item.category
                    in {
                        ClaimCategory.QUANTITY,
                        ClaimCategory.CONCENTRATION,
                        ClaimCategory.TEMPERATURE,
                        ClaimCategory.AGITATION_SPEED,
                    }
                )
                durations = tuple(
                    item
                    for item in related
                    if item.category is ClaimCategory.DURATION
                )
                duration = durations[0] if durations else None
                constructs.extend(
                    domain.SourceAmbiguity(
                        ambiguity_id=f"ambiguous-{item.claim_id}",
                        source_text=item.source_text,
                        evidence=_domain_evidence(item.evidence),
                        section_id=marker.section_id,
                        step_id=step_id,
                        action_id=action_claim.claim_id,
                    )
                    for item in durations[1:]
                )
                observations = tuple(
                    item
                    for item in related
                    if item.category is ClaimCategory.OBSERVATION_CHECKPOINT
                )
                warnings = tuple(
                    item
                    for item in related
                    if item.category is ClaimCategory.WARNING_HAZARD
                )
                missing = tuple(
                    item
                    for item in related
                    if item.category
                    is ClaimCategory.EXPLICIT_MISSING_AMBIGUOUS_VALUE
                )
                repeat_claims = tuple(
                    item
                    for item in related
                    if item.category is ClaimCategory.REPEAT_CONDITION
                )
                constructs.extend(
                    domain.RepeatUntil(
                        repetition_id=item.claim_id,
                        condition_source_text=item.source_text,
                        repeated_step_ids=(step_id,),
                        evidence=_domain_evidence(item.evidence),
                        section_id=marker.section_id,
                        step_id=step_id,
                        action_id=action_claim.claim_id,
                    )
                    for item in repeat_claims
                )
                sub_actions.append(
                    domain.ProtocolSubAction(
                        action_id=action_claim.claim_id,
                        instruction_source_text=action_claim.source_text,
                        evidence=_domain_evidence(action_claim.evidence),
                        quantities=tuple(
                            domain.ScientificValue(item.source_text)
                            for item in parameters
                        ),
                        conditions=tuple(
                            _statement(item, "parameter")
                            for item in (*parameters, *durations)
                        ),
                        estimated_duration=(
                            domain.EstimatedDuration(duration.source_text)
                            if duration is not None
                            else None
                        ),
                        process_timer=(
                            domain.ProcessTimerSpecification(
                                timer_id=f"timer-{duration.claim_id}",
                                duration=domain.ScientificValue(duration.source_text),
                                evidence=_domain_evidence(duration.evidence),
                                required_for_execution=True,
                            )
                            if duration is not None
                            else None
                        ),
                        required_observations=tuple(
                            domain.RequiredObservation(
                                observation_id=item.claim_id,
                                source_text=item.source_text,
                                evidence=_domain_evidence(item.evidence),
                            )
                            for item in observations
                        ),
                        warnings=tuple(
                            _statement(item, "warning") for item in warnings
                        ),
                        missing_execution_values=tuple(
                            domain.MissingExecutionValue(
                                value_id=item.claim_id,
                                description=item.source_text,
                                evidence=_domain_evidence(item.evidence),
                            )
                            for item in missing
                        ),
                    )
                )
            first = action_claims[0]
            assert first.source_label is not None
            steps.append(
                domain.ProtocolSourceStep(
                    step_id=step_id,
                    source_label=first.source_label,
                    instruction_source_text=first.source_text,
                    evidence=_domain_evidence(first.evidence),
                    sub_actions=tuple(sub_actions),
                )
            )
        sections.append(
            domain.ProtocolSection(
                section_id=marker.section_id,
                title_source_text=marker.source_text,
                evidence=_domain_evidence(marker.evidence),
                steps=tuple(steps),
            )
        )
    protocol = domain.ExperimentProtocol(
        protocol_id=merged.protocol_id,
        metadata=domain.ProtocolMetadata(
            pdf=extraction,
            title=title.source_text,
            original_language="und",
            evidence=_domain_evidence(title.evidence),
        ),
        before_start=tuple(prerequisites),
        materials=tuple(materials),
        equipment=tuple(equipment),
        sections=tuple(sections),
        constructs=tuple(constructs),
    )
    verified, evidence_count = validate_protocol_analysis_evidence(
        protocol,
        extraction,
    )
    readiness = domain.assess_readiness(verified)
    return ProtocolAnalysisDraft(
        extraction=extraction,
        protocol=verified,
        readiness=readiness,
        capability_policy=domain.P1_CAPABILITY_POLICY,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        verified_evidence_count=evidence_count,
    )
