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


CLAIM_SCHEMA_VERSION = 1
MAX_CHUNK_CLAIM_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_CLAIMS_PER_CHUNK = 4096
_MAX_MARKERS_PER_CHUNK = 1024
_MAX_TEXT_CHARS = 32 * 1024
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
    """Independent immutable source identity for one proposed claim."""

    source_revision: str
    source_sha256: str
    source_page_number: int
    source_excerpt: str

    def public_dict(self) -> dict[str, object]:
        return {
            "source_revision": self.source_revision,
            "source_sha256": self.source_sha256,
            "source_page_number": self.source_page_number,
            "source_excerpt": self.source_excerpt,
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
        "source_revision": {"type": "string"},
        "source_sha256": {"type": "string"},
        "source_page_number": {"type": "integer", "minimum": 1},
        "source_excerpt": {"type": "string"},
    },
    "required": [
        "source_revision",
        "source_sha256",
        "source_page_number",
        "source_excerpt",
    ],
}
CLAIM_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim_schema_version": {"type": "integer", "const": CLAIM_SCHEMA_VERSION},
        "capability_policy_id": {"type": "string"},
        "source_revision": {"type": "string"},
        "source_sha256": {"type": "string"},
        "chunk_id": {"type": "string"},
        "page_coverage": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_revision": {"type": "string"},
                    "source_sha256": {"type": "string"},
                    "source_page_number": {"type": "integer", "minimum": 1},
                    "page_text_sha256": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [item.value for item in PageCoverageStatus],
                    },
                    "evidence_item_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "source_revision",
                    "source_sha256",
                    "source_page_number",
                    "page_text_sha256",
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
                    "source_text": {"type": "string"},
                    "section_id": _NULLABLE_STRING_SCHEMA,
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": [
                    "marker_id",
                    "kind",
                    "source_order",
                    "source_text",
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
                    "source_text": {"type": "string"},
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
                    "source_text",
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
        "source_revision",
        "source_sha256",
        "chunk_id",
        "page_coverage",
        "structure",
        "claims",
    ],
}


CLAIM_ANALYSIS_SYSTEM_PROMPT = """\
Extract evidence-linked Protocol claims from only the supplied core pages.
Context pages are read-only continuity context: never emit a claim or marker
whose evidence page is context-only. Return the small claim schema supplied by
the caller, not an ExperimentProtocol and not a summary.

Every scientific or execution fact must be its own claim. Categories include
material, equipment, action, quantity, concentration, temperature, duration,
agitation_speed, prerequisite, warning_hazard, observation_checkpoint,
repeat_condition, and explicit_missing_ambiguous_value. Never combine distinct
values into evidence-free fields. An action must preserve its source step label,
step identity, and section identity. Parameter claims must target the action,
material, or equipment claim they qualify. Use stable identifiers across page
boundaries when a context page shows the beginning of the same source step.

For every claim and structural marker, copy source_revision and source_sha256
exactly from the request, cite a one-based core source page, and copy one exact
contiguous source_excerpt from that page. source_text must itself be an exact
contiguous substring of source_excerpt. Do not normalize whitespace, repair OCR,
paraphrase, translate, merge passages, infer missing values, or add scientific
knowledge. Explicit source ambiguity or a missing execution value is a blocking
claim, never a guessed value.

Each supplied page_text_sha256 is an opaque, server-owned page identity. Copy the
exact supplied value for the cited core page into its coverage record. Never
calculate, derive, normalize, shorten, alter, or invent a page_text_sha256.

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


def serialize_chunk_claim_analysis(
    analysis: ProtocolChunkClaimAnalysis,
) -> tuple[str, str]:
    payload = _canonical_json(analysis.public_dict())
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    payload = {
        "claim_schema_version": CLAIM_SCHEMA_VERSION,
        "capability_policy_id": capability_policy_id,
        "source": {
            "source_revision": source_revision,
            "source_sha256": extraction.sha256,
            "page_count": extraction.page_count,
        },
        "chunk": {
            "chunk_id": chunk_id,
            "ordinal": ordinal,
            "core_page_refs": list(core_page_refs),
            "context_page_refs": list(context_page_refs),
        },
        "pages": [
            {
                "source_page_number": page_number,
                "role": (
                    "core" if page_number in set(core_page_refs) else "context"
                ),
                "page_text_sha256": _page_text_sha256(extraction, page_number),
                "text": extraction.pages[page_number - 1].text,
            }
            for page_number in context_page_refs + core_page_refs
        ],
    }
    return _canonical_json(payload)


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


def _source_pages_containing(
    extraction: ProtocolPdfExtraction,
    excerpt: str,
) -> tuple[int, ...]:
    return tuple(
        page.source_page_number
        for page in extraction.pages
        if excerpt in page.text
    )


def _decode_evidence(
    raw: object,
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    core_pages: frozenset[int],
    chunk_id: str,
) -> ClaimSourceEvidence:
    value = _expect_record(
        raw,
        {
            "source_revision",
            "source_sha256",
            "source_page_number",
            "source_excerpt",
        },
        "evidence",
    )
    excerpt = _required_text(value["source_excerpt"], "source excerpt")
    page_number = value["source_page_number"]
    valid_page = (
        isinstance(page_number, int)
        and not isinstance(page_number, bool)
        and page_number in core_pages
        and page_number <= extraction.page_count
    )
    identity_matches = (
        value["source_revision"] == source_revision
        and value["source_sha256"] == extraction.sha256
    )
    if not valid_page or not identity_matches:
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim evidence has a mismatched immutable source identity.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="chunk_identity_mismatch",
                mismatch_class="source_identity_mismatch",
                page_number=(page_number if isinstance(page_number, int) else None),
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
                received_source_hash=(
                    value["source_sha256"]
                    if isinstance(value["source_sha256"], str)
                    else None
                ),
            ),
        )
    page_text = extraction.pages[page_number - 1].text
    if excerpt not in page_text:
        raise ProtocolAnalysisEvidenceError(
            "Chunk claim evidence is not an exact contiguous source excerpt.",
            diagnostic=ProtocolEvidenceDiagnostic(
                validation_stage="chunk_claim_evidence_validation",
                reason_code="quote_not_found",
                mismatch_class="fabricated_or_non_verbatim_quote",
                page_number=page_number,
                matching_source_pages=_source_pages_containing(extraction, excerpt),
                chunk_id=chunk_id,
                source_revision=source_revision,
                source_hash=extraction.sha256,
                quote_sha256=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                quote_length=len(excerpt),
            ),
        )
    return ClaimSourceEvidence(
        source_revision=source_revision,
        source_sha256=extraction.sha256,
        source_page_number=page_number,
        source_excerpt=excerpt,
    )


def _source_order(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProtocolAnalysisResponseError(
            "Chunk claim response has an invalid source order."
        )
    return value


def _numbered_step_labels(page_text: str) -> tuple[str, ...]:
    labels: list[str] = []
    matches = (
        *_NUMBERED_SOURCE_LINE.finditer(page_text),
        *_INLINE_NUMBERED_SOURCE.finditer(page_text),
    )
    for match in matches:
        following = match.group("next").strip(".,:;()[]{}").casefold()
        if following.isdecimal() or following in _VALUE_UNITS:
            continue
        labels.append(match.group("label"))
    return tuple(dict.fromkeys(labels))


def parse_chunk_claim_response(
    raw_response: str,
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    chunk_id: str,
    core_page_refs: tuple[int, ...],
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
            "source_revision",
            "source_sha256",
            "chunk_id",
            "page_coverage",
            "structure",
            "claims",
        },
        "root",
    )
    if (
        value["claim_schema_version"] != CLAIM_SCHEMA_VERSION
        or value["capability_policy_id"] != capability_policy_id
        or value["source_revision"] != source_revision
        or value["source_sha256"] != extraction.sha256
        or value["chunk_id"] != chunk_id
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
                received_source_hash=(
                    value["source_sha256"]
                    if isinstance(value["source_sha256"], str)
                    else None
                ),
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
    ):
        raise ProtocolAnalysisResponseError(
            "Chunk claim response exceeds a bounded record limit."
        )
    core_pages = frozenset(core_page_refs)
    markers: list[ProtocolStructureMarker] = []
    for item in raw_structure:
        record = _expect_record(
            item,
            {
                "marker_id",
                "kind",
                "source_order",
                "source_text",
                "section_id",
                "evidence",
            },
            "structure marker",
        )
        try:
            kind = StructureMarkerKind(record["kind"])
        except (TypeError, ValueError) as exc:
            raise ProtocolAnalysisResponseError(
                "Chunk claim response has an unsupported structure marker."
            ) from exc
        evidence = _decode_evidence(
            record["evidence"],
            extraction,
            source_revision=source_revision,
            core_pages=core_pages,
            chunk_id=chunk_id,
        )
        source_text = _required_text(record["source_text"], "structure")
        if source_text not in evidence.source_excerpt:
            raise ProtocolAnalysisEvidenceError(
                "A structure marker is unsupported by its exact excerpt.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_claim_text_validation",
                    reason_code="claim_not_found",
                    mismatch_class="claim_evidence_mismatch",
                    page_number=evidence.source_page_number,
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
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
                source_text=source_text,
                section_id=section_id,
                evidence=evidence,
            )
        )
    claims: list[ProtocolClaim] = []
    for item in raw_claims:
        record = _expect_record(
            item,
            {
                "claim_id",
                "category",
                "source_order",
                "source_text",
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
                "Chunk claim response has an unsupported claim category."
            ) from exc
        evidence = _decode_evidence(
            record["evidence"],
            extraction,
            source_revision=source_revision,
            core_pages=core_pages,
            chunk_id=chunk_id,
        )
        source_text = _required_text(record["source_text"], "claim")
        if source_text not in evidence.source_excerpt:
            raise ProtocolAnalysisEvidenceError(
                "A Protocol claim is unsupported by its exact source excerpt.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_claim_text_validation",
                    reason_code="claim_not_found",
                    mismatch_class="claim_evidence_mismatch",
                    page_number=evidence.source_page_number,
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
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
                source_text=source_text,
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
            "Chunk claim response contains duplicate evidence item identifiers."
        )
    coverage: list[ProtocolPageClaimCoverage] = []
    for item in raw_coverage:
        record = _expect_record(
            item,
            {
                "source_revision",
                "source_sha256",
                "source_page_number",
                "page_text_sha256",
                "status",
                "evidence_item_ids",
            },
            "page coverage",
        )
        try:
            status = PageCoverageStatus(record["status"])
        except (TypeError, ValueError) as exc:
            raise ProtocolAnalysisResponseError(
                "Chunk claim response has an unsupported coverage status."
            ) from exc
        page_number = record["source_page_number"]
        item_ids = record["evidence_item_ids"]
        if (
            record["source_revision"] != source_revision
            or record["source_sha256"] != extraction.sha256
            or not isinstance(page_number, int)
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
                    page_number=(page_number if isinstance(page_number, int) else None),
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            )
        expected_page_hash = _page_text_sha256(extraction, page_number)
        if record["page_text_sha256"] != expected_page_hash:
            raise ProtocolAnalysisEvidenceError(
                "Chunk page coverage text identity changed.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_page_coverage_validation",
                    reason_code="invalid_source_hash",
                    mismatch_class="source_identity_mismatch",
                    page_number=page_number,
                    chunk_id=chunk_id,
                    source_revision=source_revision,
                    source_hash=extraction.sha256,
                ),
            )
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
    for item in (*analysis.structure, *analysis.claims):
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
                "Decoded chunk claims contain duplicate identifiers."
            )
        evidence = item.evidence
        if not isinstance(evidence, ClaimSourceEvidence):
            raise ProtocolAnalysisResponseError(
                "Decoded chunk claim evidence is malformed."
            )
        if (
            evidence.source_revision != source_revision
            or evidence.source_sha256 != extraction.sha256
            or evidence.source_page_number not in core
            or evidence.source_excerpt
            not in extraction.pages[evidence.source_page_number - 1].text
            or item.source_text not in evidence.source_excerpt
        ):
            raise ProtocolAnalysisEvidenceError(
                "Decoded chunk claim evidence failed exact revalidation.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="decoded_chunk_claim_validation",
                    reason_code="quote_not_found",
                    mismatch_class="claim_evidence_mismatch",
                    page_number=evidence.source_page_number,
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
            "Chunk claims do not account for every core source page exactly once."
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
            raise ProtocolAnalysisEvidenceError(
                "Chunk claims omit a numbered source action.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_page_coverage_validation",
                    reason_code="numbered_action_missing",
                    mismatch_class="claim_coverage_mismatch",
                    page_number=page_number,
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
                    page_number=page_number,
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
    input_json = prepare_chunk_claim_request(
        extraction,
        source_revision=source_revision,
        chunk_id=chunk_id,
        ordinal=ordinal,
        core_page_refs=core_page_refs,
        context_page_refs=context_page_refs,
    )
    try:
        raw_response = model.analyze(
            system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
            input_json=input_json,
            response_schema=CLAIM_RESPONSE_SCHEMA,
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
