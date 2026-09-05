"""Bounded, evidence-preserving analysis for large text-native Protocol PDFs.

This module is provider-neutral.  Planning, chunk identity, result isolation,
and merging are deterministic; a caller must explicitly invoke the supplied
analysis model.  Chunk evidence is checked by the production analysis
validator before a result can enter the merge.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, fields, replace
from typing import Iterable

from voice_workflow_agent.experiment_protocol_analysis import (
    MAX_SINGLE_PASS_INPUT_BYTES,
    ProtocolAnalysisDraft,
    ProtocolAnalysisError,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisModel,
)
from voice_workflow_agent.protocol_claim_analysis import (
    _numbered_step_labels,
    MAX_CHUNK_CLAIM_RESPONSE_BYTES,
    MergedProtocolClaims,
    ProtocolChunkClaimAnalysis,
    ProtocolClaimConsistencyError,
    analyze_chunk_claims,
    assemble_experiment_protocol,
    prepare_chunk_claim_request,
    validate_chunk_claim_analysis,
    validate_whole_protocol_claims,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    TextVerification,
    ProtocolPdfPage,
)


PLANNER_VERSION = "evidence-claim-handle-v5"
_HARD_MAX_PAGES = 512
_HARD_MAX_EXTRACTED_TEXT_BYTES = 8 * 1024 * 1024
_HARD_MAX_CHUNKS = 64
_HARD_MAX_CHUNK_TEXT_BYTES = 192 * 1024
_HARD_MAX_CHUNK_RESULT_BYTES = MAX_CHUNK_CLAIM_RESPONSE_BYTES
_HARD_MAX_CONCURRENCY = 2
_HARD_MAX_TIMEOUT_SECONDS = 120.0
_HARD_MAX_RETRIES = 1
_HARD_MAX_CORE_PAGES_PER_CHUNK = 32
_HARD_MAX_CORE_LABELS_PER_CHUNK = 256
_HARD_MAX_CORE_SOURCE_BYTES_PER_CHUNK = 192 * 1024


class ProtocolChunkError(ValueError):
    code = "protocol_chunk_error"


class ProtocolChunkAdmissionError(ProtocolChunkError):
    code = "protocol_chunk_admission_failed"


class ProtocolChunkResultError(ProtocolChunkError):
    code = "protocol_chunk_result_invalid"


class ProtocolChunkMergeError(ProtocolChunkError):
    code = "merge_conflict"

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__("Validated chunk results could not be merged safely.")


@dataclass(frozen=True)
class ChunkAnalysisLimits:
    """All large-analysis resource limits are explicit and finite."""

    max_pages: int = 512
    max_extracted_text_bytes: int = 8 * 1024 * 1024
    max_chunks: int = 64
    max_chunk_text_bytes: int = 192 * 1024
    max_chunk_result_bytes: int = 2 * 1024 * 1024
    max_concurrency: int = 1
    timeout_seconds: float = 120.0
    max_retries: int = 1
    overlap_pages: int = 1
    max_core_pages_per_chunk: int = 8
    max_core_source_bytes_per_chunk: int = 4 * 1024
    #: How many numbered source labels one chunk may owe an action claim for.
    #:
    #: Source bytes were the proxy for claim cardinality, and measurement says
    #: they are a poor one. On in-gel, chunk 0 held 3398 bytes and 2 labels and
    #: passed; chunk 1 held 4007 bytes -- barely more -- and 22 labels, and was
    #: rejected on three separate attempts. Across the three admissible local
    #: sources the worst chunk owed 22, 25 and 13 labels while every chunk sat
    #: inside the same byte bound, so the bound that was supposed to cap the
    #: work was capping something else.
    #:
    #: A label count is what the completeness invariant actually charges a
    #: chunk for: an action claim per numbered label, plus everything attached
    #: to it. It is computed by the server from its own page text, so it reads
    #: no meaning and favours no document.
    #:
    #: This is a target, not a ceiling. A page is atomic because provenance is
    #: page-bound, so a single page carrying more labels than this is admitted
    #: whole -- in-gel page 7 owes 9 on its own.
    max_core_labels_per_chunk: int = 12

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_pages,
            self.max_extracted_text_bytes,
            self.max_chunks,
            self.max_chunk_text_bytes,
            self.max_chunk_result_bytes,
            self.max_concurrency,
            self.max_retries,
            self.overlap_pages,
            self.max_core_pages_per_chunk,
            self.max_core_source_bytes_per_chunk,
            self.max_core_labels_per_chunk,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in integer_limits
        ):
            raise ProtocolChunkAdmissionError(
                "Chunk analysis limits are invalid."
            )
        if (
            self.max_pages < 1
            or self.max_extracted_text_bytes < 1
            or self.max_chunks < 1
            or self.max_chunk_text_bytes < 1
            or self.max_chunk_result_bytes < 1
            or self.max_concurrency < 1
            or self.overlap_pages > 1
            or self.max_core_pages_per_chunk < 1
            or self.max_core_source_bytes_per_chunk < 1
            or self.max_core_labels_per_chunk < 1
            or self.max_core_labels_per_chunk
            > _HARD_MAX_CORE_LABELS_PER_CHUNK
            or self.max_pages > _HARD_MAX_PAGES
            or self.max_extracted_text_bytes > _HARD_MAX_EXTRACTED_TEXT_BYTES
            or self.max_chunks > _HARD_MAX_CHUNKS
            or self.max_chunk_text_bytes > _HARD_MAX_CHUNK_TEXT_BYTES
            or self.max_chunk_result_bytes > _HARD_MAX_CHUNK_RESULT_BYTES
            or self.max_concurrency > _HARD_MAX_CONCURRENCY
            or self.max_retries > _HARD_MAX_RETRIES
            or self.max_core_pages_per_chunk
            > _HARD_MAX_CORE_PAGES_PER_CHUNK
            or self.max_core_source_bytes_per_chunk
            > _HARD_MAX_CORE_SOURCE_BYTES_PER_CHUNK
            or not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > _HARD_MAX_TIMEOUT_SECONDS
        ):
            raise ProtocolChunkAdmissionError(
                "Chunk analysis limits are invalid."
            )

    def public_dict(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
        }

    @property
    def configuration_sha256(self) -> str:
        return _sha256_json(self.public_dict())


@dataclass(frozen=True)
class ProtocolAnalysisChunk:
    analysis_run_id: str
    document_id: str
    protocol_id: str
    candidate_revision_id: str
    chunk_id: str
    ordinal: int
    source_page_start: int
    source_page_end: int
    source_page_refs: tuple[int, ...]
    core_page_refs: tuple[int, ...]
    overlap_page_refs: tuple[int, ...]
    extracted_text_sha256: str
    extracted_text_bytes: int
    conservative_token_estimate: int
    planner_version: str
    planner_configuration_sha256: str

    def public_dict(self) -> dict[str, object]:
        return {
            "analysis_run_id": self.analysis_run_id,
            "document_id": self.document_id,
            "protocol_id": self.protocol_id,
            "candidate_revision_id": self.candidate_revision_id,
            "chunk_id": self.chunk_id,
            "ordinal": self.ordinal,
            "source_page_start": self.source_page_start,
            "source_page_end": self.source_page_end,
            "source_page_refs": list(self.source_page_refs),
            "core_page_refs": list(self.core_page_refs),
            "overlap_page_refs": list(self.overlap_page_refs),
            "extracted_text_sha256": self.extracted_text_sha256,
            "extracted_text_bytes": self.extracted_text_bytes,
            "conservative_token_estimate": self.conservative_token_estimate,
            "planner_version": self.planner_version,
            "planner_configuration_sha256": (
                self.planner_configuration_sha256
            ),
        }


@dataclass(frozen=True)
class ProtocolChunkPlan:
    analysis_run_id: str
    document_id: str
    protocol_id: str
    candidate_revision_id: str
    planner_version: str
    planner_configuration_sha256: str
    extracted_text_bytes: int
    chunks: tuple[ProtocolAnalysisChunk, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "analysis_run_id": self.analysis_run_id,
            "document_id": self.document_id,
            "protocol_id": self.protocol_id,
            "candidate_revision_id": self.candidate_revision_id,
            "planner_version": self.planner_version,
            "planner_configuration_sha256": (
                self.planner_configuration_sha256
            ),
            "extracted_text_bytes": self.extracted_text_bytes,
            "chunks": [chunk.public_dict() for chunk in self.chunks],
        }


@dataclass(frozen=True)
class ValidatedChunkResult:
    chunk: ProtocolAnalysisChunk
    analysis: ProtocolChunkClaimAnalysis
    attempts: int = 1


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _page_payload(
    extraction: ProtocolPdfExtraction,
    page_refs: Iterable[int],
) -> list[dict[str, object]]:
    return [
        {
            "source_page_number": page_number,
            "text": extraction.pages[page_number - 1].text,
        }
        for page_number in page_refs
    ]


def _page_text_bytes(
    extraction: ProtocolPdfExtraction,
    page_refs: Iterable[int],
) -> int:
    return sum(
        len(extraction.pages[page_number - 1].text.encode("utf-8"))
        for page_number in page_refs
    )


def plan_protocol_chunks(
    extraction: ProtocolPdfExtraction,
    protocol_id: str,
    candidate_revision_id: str,
    *,
    limits: ChunkAnalysisLimits = ChunkAnalysisLimits(),
) -> ProtocolChunkPlan:
    """Create stable, page-aligned chunks without truncating source text."""

    if (
        not extraction.all_pages_inspected
        or extraction.page_count <= 0
        or len(extraction.pages) != extraction.page_count
    ):
        raise ProtocolChunkAdmissionError(
            "Protocol extraction is incomplete."
        )
    # A proven disagreement between two extraction engines means the page text
    # is not the document.  Nothing derived from it may become canonical
    # evidence, so admission fails closed here rather than downstream.  An
    # unavailable comparator is a different case: it is unknown rather than
    # wrong, so it is carried as a readiness reason a reviewer must clear.
    if extraction.text_verification is TextVerification.MISMATCH:
        raise ProtocolChunkAdmissionError(
            "Protocol source text failed independent extraction cross-check."
        )
    if extraction.non_empty_page_count == 0:
        raise ProtocolChunkAdmissionError(
            "Protocol has no extractable text; reviewed OCR is required."
        )
    if extraction.page_count > limits.max_pages:
        raise ProtocolChunkAdmissionError(
            "Protocol exceeds the chunk planner page limit."
        )
    total_bytes = _page_text_bytes(
        extraction,
        range(1, extraction.page_count + 1),
    )
    if total_bytes > limits.max_extracted_text_bytes:
        raise ProtocolChunkAdmissionError(
            "Protocol exceeds the extracted-text admission limit."
        )
    page_sizes = tuple(
        len(page.text.encode("utf-8")) for page in extraction.pages
    )
    if any(size > limits.max_chunk_text_bytes for size in page_sizes):
        raise ProtocolChunkAdmissionError(
            "One source page exceeds the per-chunk text limit."
        )

    legacy_core_groups: list[tuple[int, ...]] = []
    current: list[int] = []
    current_bytes = 0
    for page_number, byte_count in enumerate(page_sizes, start=1):
        if current and (
            current_bytes + byte_count > limits.max_chunk_text_bytes
            or len(current) >= limits.max_core_pages_per_chunk
        ):
            legacy_core_groups.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(page_number)
        current_bytes += byte_count
    if current:
        legacy_core_groups.append(tuple(current))

    # Preserve the established page/text windows, then subdivide only where
    # their provider output burden is likely to be high. Source bytes are a
    # deterministic, content-agnostic proxy for claim cardinality; a single
    # atomic page may exceed the target because provenance remains page-bound.
    core_groups: list[tuple[int, ...]] = []
    for legacy_group in legacy_core_groups:
        current = []
        current_bytes = 0
        current_labels = 0
        for page_number in legacy_group:
            byte_count = page_sizes[page_number - 1]
            label_count = len(
                _numbered_step_labels(extraction.pages[page_number - 1].text)
            )
            if current and (
                current_bytes + byte_count
                > limits.max_core_source_bytes_per_chunk
                or current_labels + label_count
                > limits.max_core_labels_per_chunk
            ):
                core_groups.append(tuple(current))
                current = []
                current_bytes = 0
                current_labels = 0
            current.append(page_number)
            current_bytes += byte_count
            current_labels += label_count
        if current:
            core_groups.append(tuple(current))
    if len(core_groups) > limits.max_chunks:
        raise ProtocolChunkAdmissionError(
            "Protocol exceeds the maximum chunk count."
        )

    config_sha256 = limits.configuration_sha256
    run_material = {
        "document_id": extraction.sha256,
        "protocol_id": protocol_id,
        "candidate_revision_id": candidate_revision_id,
        "planner_version": PLANNER_VERSION,
        "planner_configuration_sha256": config_sha256,
    }
    analysis_run_id = f"run-{_sha256_json(run_material)[:40]}"
    chunks: list[ProtocolAnalysisChunk] = []
    for ordinal, core_refs in enumerate(core_groups):
        overlap_refs: tuple[int, ...] = ()
        if ordinal and limits.overlap_pages:
            candidate = core_groups[ordinal - 1][-1]
            if (
                page_sizes[candidate - 1]
                + _page_text_bytes(extraction, core_refs)
                <= limits.max_chunk_text_bytes
            ):
                overlap_refs = (candidate,)
        page_refs = overlap_refs + core_refs
        extracted_bytes = _page_text_bytes(extraction, page_refs)
        text_sha256 = _sha256_json(_page_payload(extraction, page_refs))
        chunk_material = {
            **run_material,
            "analysis_run_id": analysis_run_id,
            "ordinal": ordinal,
            "source_page_refs": page_refs,
            "core_page_refs": core_refs,
            "overlap_page_refs": overlap_refs,
            "extracted_text_sha256": text_sha256,
        }
        chunks.append(
            ProtocolAnalysisChunk(
                analysis_run_id=analysis_run_id,
                document_id=extraction.sha256,
                protocol_id=protocol_id,
                candidate_revision_id=candidate_revision_id,
                chunk_id=f"chunk-{_sha256_json(chunk_material)[:40]}",
                ordinal=ordinal,
                source_page_start=page_refs[0],
                source_page_end=page_refs[-1],
                source_page_refs=page_refs,
                core_page_refs=core_refs,
                overlap_page_refs=overlap_refs,
                extracted_text_sha256=text_sha256,
                extracted_text_bytes=extracted_bytes,
                conservative_token_estimate=(extracted_bytes + 2) // 3,
                planner_version=PLANNER_VERSION,
                planner_configuration_sha256=config_sha256,
            )
        )
    plan = ProtocolChunkPlan(
        analysis_run_id=analysis_run_id,
        document_id=extraction.sha256,
        protocol_id=protocol_id,
        candidate_revision_id=candidate_revision_id,
        planner_version=PLANNER_VERSION,
        planner_configuration_sha256=config_sha256,
        extracted_text_bytes=total_bytes,
        chunks=tuple(chunks),
    )
    try:
        for chunk in plan.chunks:
            extraction_for_chunk(extraction, chunk)
    except ProtocolAnalysisError as exc:
        raise ProtocolChunkAdmissionError(
            "A planned chunk exceeds the bounded provider request envelope."
        ) from exc
    return plan


def extraction_for_chunk(
    extraction: ProtocolPdfExtraction,
    chunk: ProtocolAnalysisChunk,
) -> ProtocolPdfExtraction:
    """Keep the full one-based page map while blanking out-of-chunk text."""

    if extraction.sha256 != chunk.document_id:
        raise ProtocolChunkResultError(
            "Chunk does not belong to the selected source document."
        )
    allowed = set(chunk.source_page_refs)
    scoped = replace(
        extraction,
        pages=tuple(
            page
            if page.source_page_number in allowed
            else ProtocolPdfPage(
                source_page_number=page.source_page_number,
                text="",
                text_empty=True,
            )
            for page in extraction.pages
        ),
    )
    if _sha256_json(_page_payload(scoped, chunk.source_page_refs)) != (
        chunk.extracted_text_sha256
    ):
        raise ProtocolChunkResultError(
            "Chunk source text identity changed after planning."
        )
    request_json = prepare_chunk_claim_request(
        scoped,
        source_revision=chunk.candidate_revision_id,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        core_page_refs=chunk.core_page_refs,
        context_page_refs=chunk.overlap_page_refs,
    )
    if len(request_json.encode("utf-8")) > MAX_SINGLE_PASS_INPUT_BYTES:
        raise ProtocolChunkAdmissionError(
            "Chunk claim request exceeds the bounded provider envelope."
        )
    return scoped


def analyze_protocol_chunk(
    extraction: ProtocolPdfExtraction,
    chunk: ProtocolAnalysisChunk,
    model: ProtocolAnalysisModel,
) -> ProtocolChunkClaimAnalysis:
    """Extract and validate only evidence-first claims for one exact chunk."""

    scoped = extraction_for_chunk(extraction, chunk)
    try:
        return analyze_chunk_claims(
            scoped,
            model,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
            core_page_refs=chunk.core_page_refs,
            context_page_refs=chunk.overlap_page_refs,
        )
    except ProtocolAnalysisEvidenceError as exc:
        exc.enrich_diagnostic(
            chunk_id=chunk.chunk_id,
            source_revision=chunk.candidate_revision_id,
            source_hash=chunk.document_id,
        )
        raise


def validate_chunk_result(
    plan: ProtocolChunkPlan,
    result: ValidatedChunkResult,
    extraction: ProtocolPdfExtraction,
) -> None:
    expected = next(
        (chunk for chunk in plan.chunks if chunk.chunk_id == result.chunk.chunk_id),
        None,
    )
    if expected is None or expected != result.chunk:
        raise ProtocolChunkResultError(
            "Chunk result is stale or belongs to another analysis plan."
        )
    if (
        plan.document_id != extraction.sha256
        or result.chunk.analysis_run_id != plan.analysis_run_id
        or result.chunk.protocol_id != plan.protocol_id
        or result.chunk.candidate_revision_id != plan.candidate_revision_id
        or result.chunk.planner_version != plan.planner_version
        or result.chunk.planner_configuration_sha256
        != plan.planner_configuration_sha256
    ):
        raise ProtocolChunkResultError(
            "Chunk result provenance does not match the active analysis run."
        )
    try:
        extraction_for_chunk(extraction, expected)
        validate_chunk_claim_analysis(
            result.analysis,
            extraction,
            source_revision=expected.candidate_revision_id,
            chunk_id=expected.chunk_id,
            core_page_refs=expected.core_page_refs,
        )
    except ProtocolAnalysisError as exc:
        raise ProtocolChunkResultError(
            "Chunk result failed deterministic revalidation."
        ) from exc
    except Exception as exc:
        raise ProtocolChunkResultError(
            "Chunk result failed deterministic revalidation."
        ) from exc


def _merge_by_identifier(
    values: Iterable[object],
    *,
    identifier_name: str,
    conflict_code: str,
) -> tuple[object, ...]:
    merged: dict[str, object] = {}
    for value in values:
        identifier = getattr(value, identifier_name)
        prior = merged.get(identifier)
        if prior is not None and prior != value:
            raise ProtocolChunkMergeError(conflict_code)
        merged[identifier] = value
    return tuple(merged.values())


def merge_validated_chunk_results(
    extraction: ProtocolPdfExtraction,
    plan: ProtocolChunkPlan,
    results: Iterable[ValidatedChunkResult],
) -> MergedProtocolClaims:
    """Deterministically merge only a complete set of valid claim DTOs."""

    supplied = tuple(results)
    by_id: dict[str, ValidatedChunkResult] = {}
    for result in supplied:
        validate_chunk_result(plan, result, extraction)
        prior = by_id.get(result.chunk.chunk_id)
        if prior is not None and prior != result:
            raise ProtocolChunkMergeError("duplicate_chunk_conflict")
        by_id[result.chunk.chunk_id] = result
    expected_ids = {chunk.chunk_id for chunk in plan.chunks}
    if set(by_id) != expected_ids:
        raise ProtocolChunkMergeError("missing_chunk_result")
    ordered = tuple(by_id[chunk.chunk_id] for chunk in plan.chunks)
    if sum(
        len(_canonical_json(result.chunk.public_dict()).encode("utf-8"))
        for result in ordered
    ) > 4 * 1024 * 1024:
        raise ProtocolChunkMergeError("merge_metadata_limit_exceeded")

    coverage = tuple(
        item
        for result in ordered
        for item in result.analysis.page_coverage
    )
    structure = _merge_by_identifier(
        (
            item
            for result in ordered
            for item in result.analysis.structure
        ),
        identifier_name="marker_id",
        conflict_code="structure_marker_conflict",
    )
    claims = _merge_by_identifier(
        (
            item
            for result in ordered
            for item in result.analysis.claims
        ),
        identifier_name="claim_id",
        conflict_code="claim_identity_conflict",
    )
    merged = MergedProtocolClaims(
        protocol_id=plan.protocol_id,
        source_revision=plan.candidate_revision_id,
        source_sha256=plan.document_id,
        capability_policy_id=ordered[0].analysis.capability_policy_id,
        required_chunk_ids=tuple(chunk.chunk_id for chunk in plan.chunks),
        page_coverage=tuple(
            sorted(coverage, key=lambda item: item.source_page_number)
        ),
        structure=tuple(
            sorted(
                structure,
                key=lambda item: (
                    item.evidence.source_page_number,
                    item.source_order,
                    item.marker_id,
                ),
            )
        ),
        claims=tuple(
            sorted(
                claims,
                key=lambda item: (
                    item.evidence.source_page_number,
                    item.source_order,
                    item.claim_id,
                ),
            )
        ),
    )
    try:
        return validate_whole_protocol_claims(extraction, merged)
    except ProtocolClaimConsistencyError as exc:
        raise ProtocolChunkMergeError(exc.reason_code) from exc


def assemble_validated_protocol_claims(
    extraction: ProtocolPdfExtraction,
    merged: MergedProtocolClaims,
) -> ProtocolAnalysisDraft:
    """Run the final deterministic adapter into ``ExperimentProtocol``."""

    try:
        return assemble_experiment_protocol(extraction, merged)
    except ProtocolClaimConsistencyError as exc:
        raise ProtocolChunkMergeError(exc.reason_code) from exc
