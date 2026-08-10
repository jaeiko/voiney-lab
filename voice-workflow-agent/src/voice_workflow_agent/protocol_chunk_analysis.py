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
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from typing import Any, Iterable

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_analysis import (
    MAX_SINGLE_PASS_INPUT_BYTES,
    ProtocolAnalysisDraft,
    ProtocolAnalysisError,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisModel,
    analyze_protocol_extraction,
    prepare_protocol_analysis_request,
    validate_protocol_analysis_evidence,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfPage,
)


PLANNER_VERSION = "bounded-page-v1"
_HARD_MAX_PAGES = 512
_HARD_MAX_EXTRACTED_TEXT_BYTES = 8 * 1024 * 1024
_HARD_MAX_CHUNKS = 64
_HARD_MAX_CHUNK_TEXT_BYTES = 192 * 1024
_HARD_MAX_CHUNK_RESULT_BYTES = 2 * 1024 * 1024
_HARD_MAX_CONCURRENCY = 2
_HARD_MAX_TIMEOUT_SECONDS = 120.0
_HARD_MAX_RETRIES = 1


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
    max_concurrency: int = 2
    timeout_seconds: float = 120.0
    max_retries: int = 1
    overlap_pages: int = 1

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
            or self.max_chunks < 2
            or self.max_chunk_text_bytes < 1
            or self.max_chunk_result_bytes < 1
            or self.max_concurrency < 1
            or self.overlap_pages > 1
            or self.max_pages > _HARD_MAX_PAGES
            or self.max_extracted_text_bytes > _HARD_MAX_EXTRACTED_TEXT_BYTES
            or self.max_chunks > _HARD_MAX_CHUNKS
            or self.max_chunk_text_bytes > _HARD_MAX_CHUNK_TEXT_BYTES
            or self.max_chunk_result_bytes > _HARD_MAX_CHUNK_RESULT_BYTES
            or self.max_concurrency > _HARD_MAX_CONCURRENCY
            or self.max_retries > _HARD_MAX_RETRIES
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
    draft: ProtocolAnalysisDraft
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
    if extraction.non_empty_page_count == 0:
        raise ProtocolChunkAdmissionError(
            "Protocol has no extractable text; OCR is required."
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

    core_groups: list[tuple[int, ...]] = []
    current: list[int] = []
    current_bytes = 0
    for page_number, byte_count in enumerate(page_sizes, start=1):
        if current and current_bytes + byte_count > limits.max_chunk_text_bytes:
            core_groups.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(page_number)
        current_bytes += byte_count
    if current:
        core_groups.append(tuple(current))
    if len(core_groups) < 2:
        raise ProtocolChunkAdmissionError(
            "Protocol fits the single-pass path and does not need chunking."
        )
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
    prepare_protocol_analysis_request(
        scoped,
        max_input_bytes=MAX_SINGLE_PASS_INPUT_BYTES,
    )
    return scoped


def _iter_evidence(value: Any) -> Iterable[domain.SourceEvidence]:
    if isinstance(value, domain.SourceEvidence):
        yield value
        return
    if isinstance(value, (ProtocolPdfExtraction, Enum)) or value is None:
        return
    if isinstance(value, tuple):
        for item in value:
            yield from _iter_evidence(item)
        return
    if is_dataclass(value):
        for field in fields(value):
            yield from _iter_evidence(getattr(value, field.name))


def analyze_protocol_chunk(
    extraction: ProtocolPdfExtraction,
    chunk: ProtocolAnalysisChunk,
    model: ProtocolAnalysisModel,
) -> ProtocolAnalysisDraft:
    """Analyze and re-check one exact chunk through the production boundary."""

    scoped = extraction_for_chunk(extraction, chunk)
    draft = analyze_protocol_extraction(scoped, model)
    protocol = draft.protocol
    if protocol.protocol_id != chunk.protocol_id:
        protocol = replace(protocol, protocol_id=chunk.protocol_id)
        protocol, evidence_count = validate_protocol_analysis_evidence(
            protocol,
            scoped,
        )
        draft = replace(
            draft,
            protocol=protocol,
            readiness=domain.assess_readiness(
                protocol,
                capability_policy=draft.capability_policy,
            ),
            verified_evidence_count=evidence_count,
        )
    allowed = set(chunk.source_page_refs)
    if any(
        evidence.source_page_number not in allowed
        for evidence in _iter_evidence(draft.protocol)
    ):
        raise ProtocolAnalysisEvidenceError(
            "Chunk analysis cites a source page outside its chunk."
        )
    return draft


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
        or result.draft.extraction != extraction_for_chunk(extraction, expected)
        or result.draft.protocol.metadata.file_checksum != plan.document_id
    ):
        raise ProtocolChunkResultError(
            "Chunk result provenance does not match the active analysis run."
        )
    try:
        revalidated, evidence_count = validate_protocol_analysis_evidence(
            result.draft.protocol,
            result.draft.extraction,
        )
    except ProtocolAnalysisError as exc:
        raise ProtocolChunkResultError(
            "Chunk result failed deterministic revalidation."
        ) from exc
    if (
        revalidated != result.draft.protocol
        or evidence_count != result.draft.verified_evidence_count
        or domain.assess_readiness(
            revalidated,
            capability_policy=result.draft.capability_policy,
        )
        != result.draft.readiness
    ):
        raise ProtocolChunkResultError(
            "Chunk result no longer matches its validated analysis."
        )
    allowed = set(expected.source_page_refs)
    if any(
        evidence.source_page_number not in allowed
        for evidence in _iter_evidence(result.draft.protocol)
    ):
        raise ProtocolChunkResultError(
            "Chunk result contains out-of-scope evidence."
        )


def _merge_records(
    groups: Iterable[Iterable[Any]],
    identity,
    conflict_code: str,
) -> tuple[Any, ...]:
    ordered: list[Any] = []
    known: dict[str, Any] = {}
    for group in groups:
        for item in group:
            key = identity(item)
            prior = known.get(key)
            if prior is None:
                known[key] = item
                ordered.append(item)
            elif prior != item:
                raise ProtocolChunkMergeError(conflict_code)
    return tuple(ordered)


def _construct_id(value: domain.WorkflowConstruct) -> str:
    for name in (
        "branch_id",
        "repetition_id",
        "parallel_id",
        "recurring_action_id",
        "subprocedure_id",
        "ambiguity_id",
        "conflict_id",
    ):
        identifier = getattr(value, name, None)
        if isinstance(identifier, str):
            return identifier
    raise ProtocolChunkMergeError("construct_identity_missing")


def merge_validated_chunk_results(
    extraction: ProtocolPdfExtraction,
    plan: ProtocolChunkPlan,
    results: Iterable[ValidatedChunkResult],
) -> ProtocolAnalysisDraft:
    """Merge only a complete, isolated set of validated structured results."""

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

    first = ordered[0].draft
    metadata = replace(first.protocol.metadata, pdf=extraction)
    metadata_values = tuple(
        getattr(metadata, field.name)
        for field in fields(metadata)
        if field.name not in {"pdf", "evidence"}
    )
    for result in ordered[1:]:
        candidate = replace(result.draft.protocol.metadata, pdf=extraction)
        candidate_values = tuple(
            getattr(candidate, field.name)
            for field in fields(candidate)
            if field.name not in {"pdf", "evidence"}
        )
        if candidate_values != metadata_values:
            raise ProtocolChunkMergeError("metadata_conflict")

    before_start = _merge_records(
        (item.draft.protocol.before_start for item in ordered),
        lambda item: item.prerequisite_id,
        "prerequisite_conflict",
    )
    materials = _merge_records(
        (item.draft.protocol.materials for item in ordered),
        lambda item: item.material_id,
        "material_conflict",
    )
    equipment = _merge_records(
        (item.draft.protocol.equipment for item in ordered),
        lambda item: item.equipment_id,
        "equipment_conflict",
    )

    section_order: list[str] = []
    section_headers: dict[str, tuple[str, domain.SourceEvidence]] = {}
    section_steps: dict[str, list[domain.ProtocolSourceStep]] = {}
    step_by_id: dict[str, domain.ProtocolSourceStep] = {}
    label_to_step: dict[str, domain.ProtocolSourceStep] = {}
    last_section_with_new_content: str | None = None
    for result in ordered:
        for section in result.draft.protocol.sections:
            header = (section.title_source_text, section.evidence)
            prior_header = section_headers.get(section.section_id)
            is_new_section = prior_header is None
            if prior_header is None:
                section_order.append(section.section_id)
                section_headers[section.section_id] = header
                section_steps[section.section_id] = []
            elif prior_header[0] != header[0]:
                raise ProtocolChunkMergeError("section_conflict")
            new_steps = tuple(
                step for step in section.steps if step.step_id not in step_by_id
            )
            if (
                new_steps
                and not is_new_section
                and last_section_with_new_content != section.section_id
            ):
                raise ProtocolChunkMergeError("section_order_conflict")
            for step in section.steps:
                prior_step = step_by_id.get(step.step_id)
                prior_label = label_to_step.get(step.source_label)
                if prior_step is not None:
                    if prior_step != step:
                        raise ProtocolChunkMergeError("step_conflict")
                    continue
                if prior_label is not None and prior_label != step:
                    raise ProtocolChunkMergeError("source_label_conflict")
                step_by_id[step.step_id] = step
                label_to_step[step.source_label] = step
                section_steps[section.section_id].append(step)
            if is_new_section or new_steps:
                last_section_with_new_content = section.section_id
    sections = tuple(
        domain.ProtocolSection(
            section_id,
            section_headers[section_id][0],
            section_headers[section_id][1],
            tuple(section_steps[section_id]),
        )
        for section_id in section_order
    )
    constructs = _merge_records(
        (item.draft.protocol.constructs for item in ordered),
        _construct_id,
        "construct_conflict",
    )
    descriptions = tuple(
        item.draft.protocol.description
        for item in ordered
        if item.draft.protocol.description is not None
    )
    description = descriptions[0] if descriptions else None
    if any(item != description for item in descriptions[1:]):
        raise ProtocolChunkMergeError("description_conflict")

    merged = domain.ExperimentProtocol(
        protocol_id=plan.protocol_id,
        metadata=metadata,
        before_start=before_start,
        materials=materials,
        equipment=equipment,
        sections=sections,
        constructs=constructs,
        description=description,
    )
    verified, evidence_count = validate_protocol_analysis_evidence(
        merged,
        extraction,
    )
    readiness = domain.assess_readiness(
        verified,
        capability_policy=first.capability_policy,
    )
    return ProtocolAnalysisDraft(
        extraction=extraction,
        protocol=verified,
        readiness=readiness,
        capability_policy=first.capability_policy,
        analysis_schema_version=first.analysis_schema_version,
        verified_evidence_count=evidence_count,
    )
