"""Explicit, source-preserving multi-Protocol catalog service.

The service is deliberately constructed only by an authorized API/session
boundary.  Importing or listing it does not contact an analysis Provider.
All writes use the separate immutable Protocol store, never ProcedureStore.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Protocol

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import CuratedProtocolFixture
from voice_workflow_agent.experiment_protocol_analysis import (
    MAX_SINGLE_PASS_INPUT_BYTES,
    ProtocolAnalysisDraft,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisInputTooLargeError,
    ProtocolAnalysisModel,
    analyze_protocol_extraction,
    prepare_protocol_analysis_request,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    ProtocolPdfExtraction,
    ProtocolPdfPage,
    extract_protocol_pdf,
)
from voice_workflow_agent.experiment_protocol_store import (
    AnalysisRevisionRecord,
    ProtocolRevisionRecord,
    ProtocolStore,
    serialize_analysis,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ProtocolChunkAdmissionError,
    ProtocolChunkMergeError,
    ProtocolChunkPlan,
    ValidatedChunkResult,
    analyze_protocol_chunk,
    assemble_validated_protocol_claims,
    merge_validated_chunk_results,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    ProtocolChunkClaimAnalysis,
    reopen_evidence_span,
    serialize_chunk_claim_analysis,
)
from voice_workflow_agent.protocol_ocr import (
    OcrResult,
    ProtocolOcrProvider,
    ocr_result_payload,
    validate_ocr_result,
)


_SAFE_FILENAME = re.compile(r"^[^/\\\x00]{1,255}\.pdf$", re.IGNORECASE)
_STABLE_PROTOCOL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REVISION_ID = re.compile(r"^pdf-(\d+)(?:-analysis-(\d+))?$")
_ACTOR_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}")
_ACTOR_ROLES = frozenset({"reviewer", "lab_admin", "organization_admin"})


def _checked_actor(
    actor_principal_id: str,
    actor_role: str | None,
    error: type[Exception],
) -> None:
    """Reject an unidentified or unauthorized human actor."""

    if not _ACTOR_PRINCIPAL.fullmatch(actor_principal_id):
        raise error("Protocol approval actor is invalid.")
    if actor_role not in _ACTOR_ROLES:
        raise error("Protocol approval role is invalid.")


_APPROVAL_EVENT = "protocol_revision_approved"
_GATE_ACKNOWLEDGEMENT_EVENT = "protocol_readiness_gate_acknowledged"
# Gates a person may clear.  A gate is listed here only when "we could not
# determine this" is the honest state and a human genuinely can resolve it.
# source_text_cross_check_failed is deliberately absent: a proven
# disagreement between extraction engines is not a judgement call.
_ACKNOWLEDGEABLE_GATES = frozenset(
    {
        domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value,
        domain.ReadinessReasonCode.SOURCE_TEXT_CROSS_CHECK_UNAVAILABLE.value,
    }
)
_DISPOSITION_CONFIRMATION_EVENT = "protocol_label_disposition_confirmed"
_DISPOSITION_REVOCATION_EVENT = (
    "protocol_label_disposition_confirmation_revoked"
)
_REPETITION_CONFIRMATION_EVENT = "protocol_fixed_repetition_confirmed"
_REPETITION_REVOCATION_EVENT = "protocol_fixed_repetition_confirmation_revoked"
_AMBIGUITY_RESOLUTION_EVENT = "protocol_ambiguity_resolved"
_AMBIGUITY_REVOCATION_EVENT = "protocol_ambiguity_resolution_revoked"
# A reviewer's two possible findings about one ambiguity. Only the first can
# clear anything: deciding that two statements really are different is a
# finding, not a resolution, and it leaves the Protocol blocked.
AMBIGUITY_SINGLE_AUTHORITATIVE = "single_statement_is_authoritative"
AMBIGUITY_STATEMENTS_DISTINCT = "statements_are_distinct"
_AMBIGUITY_DECISIONS = frozenset(
    {AMBIGUITY_SINGLE_AUTHORITATIVE, AMBIGUITY_STATEMENTS_DISTINCT}
)
_ANALYSIS_REQUESTED_EVENT = "protocol_analysis_requested"
_ANALYSIS_STARTED_EVENT = "protocol_analysis_started"
_ANALYSIS_READY_EVENT = "protocol_analysis_ready"
_ANALYSIS_FAILED_EVENT = "protocol_analysis_failed"
_CHUNK_PLAN_EVENT = "protocol_chunk_plan_created"
_CHUNK_STARTED_EVENT = "protocol_chunk_analysis_started"
_CHUNK_COMPLETED_EVENT = "protocol_chunk_analysis_completed"
_CHUNK_FAILED_EVENT = "protocol_chunk_analysis_failed"
_MERGE_STARTED_EVENT = "protocol_chunk_merge_started"
_MERGE_CONFLICT_EVENT = "protocol_chunk_merge_conflict"
_REVIEW_REQUIRED_EVENT = "protocol_chunk_review_required"
_SINGLE_REVIEW_REQUIRED_EVENT = "protocol_review_required"
_RUN_CANCELLED_EVENT = "protocol_chunk_run_cancelled"
_DEVELOPMENT_FIXTURE_EVENT = "development_fixture_materialized"
_DEVELOPMENT_ACTIVATION_EVENT = "protocol_development_activated"
_OCR_REQUESTED_EVENT = "protocol_ocr_requested"
_OCR_COMPLETED_EVENT = "protocol_ocr_completed"
_OCR_FAILED_EVENT = "protocol_ocr_failed"
_OCR_REVIEWED_EVENT = "protocol_ocr_reviewed"
_CHUNK_RUN_LOCKS = tuple(threading.Lock() for _ in range(64))
CLAIM_CHUNK_ANALYSIS_ENABLED_ENV = (
    "VOICE_WORKFLOW_AGENT_PROTOCOL_CLAIM_CHUNKS_ENABLED"
)
_TRUE_FEATURE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_FEATURE_VALUES = frozenset({"0", "false", "no", "off", ""})


class ProtocolCatalogError(RuntimeError):
    code = "protocol_catalog_error"


class ProtocolRegistrationError(ProtocolCatalogError):
    code = "protocol_registration_invalid"


class ProtocolCatalogNotFoundError(ProtocolCatalogError):
    code = "protocol_catalog_not_found"


class ProtocolCatalogUnavailableError(ProtocolCatalogError):
    code = "protocol_catalog_unavailable"


class ProtocolAnalysisUnavailableError(ProtocolCatalogError):
    code = "protocol_analysis_not_configured"


class ProtocolApprovalError(ProtocolCatalogError):
    code = "protocol_approval_denied"


class ProtocolOcrRequiredError(ProtocolCatalogError):
    code = "ocr_required"


class ProtocolOcrReviewError(ProtocolCatalogError):
    code = "protocol_ocr_review_invalid"


class ProtocolChunkedAnalysisRequiredError(ProtocolCatalogError):
    code = "chunked_analysis_required"


class ProtocolChunkAnalysisFailedError(ProtocolCatalogError):
    code = "chunk_analysis_failed"


class ProtocolChunkMergeConflictError(ProtocolCatalogError):
    code = "merge_conflict"


class ApprovalPolicy(Protocol):
    def permits(self, presented_secret: str | None) -> bool: ...


@dataclass(frozen=True)
class SharedSecretApprovalPolicy:
    configured_secret: str | None

    def permits(self, presented_secret: str | None) -> bool:
        if not self.configured_secret or not presented_secret:
            return False
        return hmac.compare_digest(
            hashlib.sha256(presented_secret.encode("utf-8")).digest(),
            hashlib.sha256(self.configured_secret.encode("utf-8")).digest(),
        )


@dataclass(frozen=True)
class ProtocolCatalogEntry:
    protocol_id: str
    title: str
    source_filename: str
    source_sha256: str
    revision_id: str
    readiness_status: str
    approval_status: str
    analysis_status: str
    step_count: int
    created_at: str
    available_for_execution: bool
    lifecycle_state: str = "uploaded"

    def public_dict(self) -> dict[str, object]:
        return {
            "protocol_id": self.protocol_id,
            "title": self.title,
            "source_filename": self.source_filename,
            "source_sha256": self.source_sha256,
            "revision_id": self.revision_id,
            "readiness_status": self.readiness_status,
            "approval_status": self.approval_status,
            "analysis_status": self.analysis_status,
            "step_count": self.step_count,
            "created_at": self.created_at,
            "available_for_execution": self.available_for_execution,
            "development_only": self.approval_status == "development_only",
            "lifecycle_state": self.lifecycle_state,
        }


@dataclass(frozen=True)
class ProtocolRegistration:
    entry: ProtocolCatalogEntry
    deduplicated: bool


@dataclass(frozen=True)
class ProtocolDevelopmentBootstrap:
    """Result of explicitly materializing one verified development fixture."""

    entry: ProtocolCatalogEntry
    deduplicated: bool


@dataclass(frozen=True)
class ProtocolAssetResolution:
    path: Path
    sha256: str
    source_page: int
    mime_type: str = "image/svg+xml"


@dataclass(frozen=True)
class ProtocolAnalysisRunStatus:
    protocol_id: str
    candidate_revision_id: str
    analysis_run_id: str | None
    state: str
    total_chunks: int
    completed_chunks: int
    failed_chunks: int
    pending_chunks: int
    chunks: tuple[dict[str, object], ...]
    failure_code: str | None = None
    failure_detail: dict[str, object] | None = None
    merge_status: str | None = None
    restart_behavior: str = "explicit_analysis_request_only"
    lifecycle_state: str = "uploaded"

    def public_dict(self) -> dict[str, object]:
        return {
            "protocol_id": self.protocol_id,
            "candidate_revision_id": self.candidate_revision_id,
            "analysis_run_id": self.analysis_run_id,
            "state": self.state,
            "total_chunks": self.total_chunks,
            "completed_chunks": self.completed_chunks,
            "failed_chunks": self.failed_chunks,
            "pending_chunks": self.pending_chunks,
            "chunks": list(self.chunks),
            "failure_code": self.failure_code,
            "failure_detail": self.failure_detail,
            "merge_status": self.merge_status,
            "restart_behavior": self.restart_behavior,
            "lifecycle_state": self.lifecycle_state,
        }


def _protocol_id(checksum: str) -> str:
    return f"protocol-{checksum[:32]}"


def _revision_id(
    protocol_revision_number: int,
    analysis_revision_number: int | None = None,
) -> str:
    value = f"pdf-{protocol_revision_number}"
    if analysis_revision_number is not None:
        value += f"-analysis-{analysis_revision_number}"
    return value


def _parse_revision_id(value: str) -> tuple[int, int | None]:
    match = _REVISION_ID.fullmatch(value)
    if match is None:
        raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
    return int(match.group(1)), (
        int(match.group(2)) if match.group(2) is not None else None
    )


def _display_filename(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_FILENAME.fullmatch(value):
        raise ProtocolRegistrationError(
            "Protocol filename must be one plain PDF filename."
        )
    return value


def _review_value(value: object) -> object:
    """Convert selected Protocol records into a stable, JSON-safe review view."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _review_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [_review_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_review_value(item) for item in value)
    if isinstance(value, dict):
        return {
            str(key): _review_value(item) for key, item in value.items()
        }
    return value


def _analysis_state(extraction: ProtocolPdfExtraction) -> str:
    extracted_chars = sum(len(page.text.strip()) for page in extraction.pages)
    if extraction.non_empty_page_count == 0 or extracted_chars < 32:
        return "ocr_required"
    if (
        _claim_chunk_analysis_enabled()
        and extraction.page_count
        > ChunkAnalysisLimits().max_core_pages_per_chunk
    ):
        return "chunked_analysis_required"
    try:
        prepare_protocol_analysis_request(
            extraction, max_input_bytes=MAX_SINGLE_PASS_INPUT_BYTES
        )
    except ProtocolAnalysisInputTooLargeError:
        return "chunked_analysis_required"
    return "structured_analysis_ready"


def _claim_chunk_analysis_enabled() -> bool:
    raw = os.environ.get(CLAIM_CHUNK_ANALYSIS_ENABLED_ENV, "false")
    normalized = raw.strip().casefold()
    if normalized in _TRUE_FEATURE_VALUES:
        return True
    if normalized in _FALSE_FEATURE_VALUES:
        return False
    raise ProtocolCatalogUnavailableError(
        f"{CLAIM_CHUNK_ANALYSIS_ENABLED_ENV} must be a boolean."
    )


def _event_id(*values: object) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(value) for value in values).encode("utf-8")
    ).hexdigest()
    return f"chunk-event-{digest[:48]}"


def _safe_failure_code(exc: BaseException) -> str:
    value = getattr(exc, "code", "chunk_analysis_failed")
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,63}", value
    ):
        return "chunk_analysis_failed"
    return value


def _safe_evidence_failure(
    exc: BaseException,
    *,
    source_revision: str,
    source_hash: str,
    chunk_id: str | None = None,
) -> dict[str, object] | None:
    if not isinstance(exc, ProtocolAnalysisEvidenceError):
        return None
    del source_revision, source_hash, chunk_id
    return exc.diagnostic.privacy_safe_dict()


def _chunk_run_lock(analysis_run_id: str) -> threading.Lock:
    stripe = int(
        hashlib.sha256(analysis_run_id.encode("utf-8")).hexdigest()[:8],
        16,
    ) % len(_CHUNK_RUN_LOCKS)
    return _CHUNK_RUN_LOCKS[stripe]


def _worker_analyze_chunk(
    extraction: ProtocolPdfExtraction,
    chunk,
    model: ProtocolAnalysisModel,
    max_retries: int,
) -> tuple[
    ProtocolChunkClaimAnalysis | None,
    int,
    str | None,
    dict[str, object] | None,
]:
    attempts = 0
    while attempts <= max_retries:
        attempts += 1
        try:
            return (
                analyze_protocol_chunk(extraction, chunk, model),
                attempts,
                None,
                None,
            )
        except Exception as exc:
            if attempts > max_retries:
                return (
                    None,
                    attempts,
                    _safe_failure_code(exc),
                    _safe_evidence_failure(
                        exc,
                        source_revision=chunk.candidate_revision_id,
                        source_hash=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                    ),
                )
    return None, attempts, "chunk_analysis_failed", None


class ProtocolCatalog:
    """Catalog facade over immutable source, analysis, and approval records."""

    def __init__(self, store: ProtocolStore) -> None:
        self.store = store

    def _latest_protocol_revision(self, protocol_id: str) -> ProtocolRevisionRecord:
        if not _STABLE_PROTOCOL_ID.fullmatch(protocol_id):
            raise ProtocolCatalogNotFoundError("Protocol identifier is unknown.")
        revisions = self.store.list_protocol_revisions(protocol_id)
        if not revisions:
            raise ProtocolCatalogNotFoundError("Protocol identifier is unknown.")
        return revisions[-1]

    def _latest_analysis(
        self, revision: ProtocolRevisionRecord
    ) -> AnalysisRevisionRecord | None:
        analyses = self.store.list_analysis_revisions(
            revision.experiment_id, revision.revision_number
        )
        return analyses[-1] if analyses else None

    def _latest_ocr_events(
        self, revision: ProtocolRevisionRecord
    ) -> tuple[object, ...]:
        events = tuple(
            event
            for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
            and event.event_type
            in {
                _OCR_REQUESTED_EVENT,
                _OCR_COMPLETED_EVENT,
                _OCR_FAILED_EVENT,
                _OCR_REVIEWED_EVENT,
            }
        )
        requested = tuple(
            event for event in events if event.event_type == _OCR_REQUESTED_EVENT
        )
        if not requested or not isinstance(requested[-1].payload, dict):
            return ()
        ocr_id = requested[-1].payload.get("ocr_id")
        if not isinstance(ocr_id, str):
            return ()
        return tuple(
            event
            for event in events
            if isinstance(event.payload, dict)
            and event.payload.get("ocr_id") == ocr_id
        )

    def _ocr_projection(
        self,
        revision: ProtocolRevisionRecord,
        extraction: ProtocolPdfExtraction,
        *,
        include_text: bool,
    ) -> dict[str, object]:
        if _analysis_state(extraction) != "ocr_required":
            return {
                "state": "not_required",
                "review_required": False,
                "accepted_for_analysis": False,
                "executable": False,
            }
        events = self._latest_ocr_events(revision)
        if not events:
            return {
                "state": "ocr_required",
                "review_required": False,
                "accepted_for_analysis": False,
                "executable": False,
            }
        request = events[0].payload
        completed = next(
            (
                event.payload
                for event in reversed(events)
                if event.event_type == _OCR_COMPLETED_EVENT
                and isinstance(event.payload, dict)
            ),
            None,
        )
        failed = next(
            (
                event.payload
                for event in reversed(events)
                if event.event_type == _OCR_FAILED_EVENT
                and isinstance(event.payload, dict)
            ),
            None,
        )
        reviewed = next(
            (
                event.payload
                for event in reversed(events)
                if event.event_type == _OCR_REVIEWED_EVENT
                and isinstance(event.payload, dict)
            ),
            None,
        )
        if reviewed is not None:
            accepted = reviewed.get("decision") == "accepted"
            state = "accepted_for_analysis" if accepted else "rejected"
        elif completed is not None:
            accepted = False
            state = "review_required"
        elif failed is not None:
            accepted = False
            state = "failed"
        else:
            accepted = False
            state = "in_progress"
        projection: dict[str, object] = {
            "state": state,
            "ocr_id": request.get("ocr_id"),
            "source_sha256": revision.pdf_checksum,
            "review_required": state == "review_required",
            "accepted_for_analysis": accepted,
            "executable": False,
            "failure_code": (
                failed.get("failure_code") if failed is not None else None
            ),
            "review": reviewed,
        }
        if completed is not None:
            pages = completed.get("pages")
            projection.update(
                {
                    "provider": completed.get("provider"),
                    "provider_version": completed.get("provider_version"),
                    "languages": completed.get("languages", []),
                    "warnings": completed.get("warnings", []),
                    "page_count": completed.get("page_count"),
                    "pages": (
                        pages
                        if include_text
                        else [
                            {
                                key: page.get(key)
                                for key in (
                                    "source_page_number",
                                    "confidence",
                                    "text_sha256",
                                )
                            }
                            for page in pages
                            if isinstance(page, dict)
                        ]
                        if isinstance(pages, list)
                        else []
                    ),
                }
            )
        return projection

    def ocr_status(
        self, protocol_id: str, *, include_text: bool = True
    ) -> dict[str, object]:
        revision = self._latest_protocol_revision(protocol_id)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source)
        return {
            "protocol_id": protocol_id,
            "revision_id": _revision_id(revision.revision_number),
            **self._ocr_projection(
                revision, extraction, include_text=include_text
            ),
        }

    def _extraction_for_analysis(
        self,
        revision: ProtocolRevisionRecord,
        extraction: ProtocolPdfExtraction,
    ) -> ProtocolPdfExtraction:
        projection = self._ocr_projection(
            revision, extraction, include_text=True
        )
        if projection.get("accepted_for_analysis") is not True:
            return extraction
        pages = projection.get("pages")
        if not isinstance(pages, list) or len(pages) != extraction.page_count:
            raise ProtocolOcrReviewError("Accepted OCR page evidence is invalid.")
        reconstructed = []
        for expected_number, page in enumerate(pages, start=1):
            if (
                not isinstance(page, dict)
                or page.get("source_page_number") != expected_number
                or not isinstance(page.get("text"), str)
                or hashlib.sha256(page["text"].encode("utf-8")).hexdigest()
                != page.get("text_sha256")
            ):
                raise ProtocolOcrReviewError(
                    "Accepted OCR page evidence failed integrity validation."
                )
            reconstructed.append(
                ProtocolPdfPage(
                    source_page_number=expected_number,
                    text=page["text"],
                    text_empty=not page["text"].strip(),
                    warning="Text was produced by OCR and accepted for structured review.",
                )
            )
        return replace(
            extraction,
            pages=tuple(reconstructed),
            warnings=tuple(
                dict.fromkeys(
                    (
                        *extraction.warnings,
                        "OCR-derived text is review evidence, not an approved protocol.",
                    )
                )
            ),
        )

    def run_ocr(
        self,
        protocol_id: str,
        provider: ProtocolOcrProvider,
        *,
        ocr_id: str,
    ) -> dict[str, object]:
        if not _STABLE_PROTOCOL_ID.fullmatch(ocr_id):
            raise ProtocolOcrReviewError("OCR request identity is invalid.")
        revision = self._latest_protocol_revision(protocol_id)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source)
        if _analysis_state(extraction) != "ocr_required":
            raise ProtocolOcrReviewError("Protocol PDF does not require OCR.")
        current = self._ocr_projection(
            revision, extraction, include_text=False
        )
        if current["state"] in {
            "in_progress",
            "review_required",
            "accepted_for_analysis",
        }:
            return self.ocr_status(protocol_id)
        self.store.append_event(
            f"ocr-requested-{ocr_id}",
            protocol_id,
            revision.revision_number,
            _OCR_REQUESTED_EVENT,
            {
                "ocr_id": ocr_id,
                "status": "in_progress",
                "source_sha256": revision.pdf_checksum,
                "page_count": extraction.page_count,
            },
        )
        try:
            result = provider.recognize(
                source,
                source_sha256=revision.pdf_checksum,
                page_count=extraction.page_count,
            )
            validated = validate_ocr_result(
                result,
                expected_sha256=revision.pdf_checksum,
                expected_page_count=extraction.page_count,
            )
            payload = {"ocr_id": ocr_id, **ocr_result_payload(validated)}
            self.store.append_event(
                f"ocr-completed-{ocr_id}",
                protocol_id,
                revision.revision_number,
                _OCR_COMPLETED_EVENT,
                payload,
            )
        except Exception as exc:
            failure_code = getattr(exc, "code", "protocol_ocr_failed")
            if not isinstance(failure_code, str) or not re.fullmatch(
                r"[a-z][a-z0-9_]{0,63}", failure_code
            ):
                failure_code = "protocol_ocr_failed"
            self.store.append_event(
                f"ocr-failed-{ocr_id}",
                protocol_id,
                revision.revision_number,
                _OCR_FAILED_EVENT,
                {
                    "ocr_id": ocr_id,
                    "status": "failed",
                    "failure_code": failure_code,
                },
            )
            raise
        return self.ocr_status(protocol_id)

    def review_ocr(
        self,
        protocol_id: str,
        *,
        decision: str,
        policy: ApprovalPolicy,
        presented_secret: str | None,
        actor_principal_id: str | None = None,
        actor_role: str | None = None,
        comment: str = "OCR page text reviewed against the source PDF.",
    ) -> dict[str, object]:
        if not policy.permits(presented_secret):
            raise ProtocolApprovalError("OCR review authorization failed.")
        if decision not in {"accepted", "rejected"}:
            raise ProtocolOcrReviewError("OCR review decision is invalid.")
        revision = self._latest_protocol_revision(protocol_id)
        current = self.ocr_status(protocol_id)
        if current.get("state") != "review_required":
            raise ProtocolOcrReviewError("OCR output is not awaiting review.")
        ocr_id = current.get("ocr_id")
        if not isinstance(ocr_id, str):
            raise ProtocolOcrReviewError("OCR review identity is invalid.")
        payload: dict[str, object] = {
            "ocr_id": ocr_id,
            "decision": decision,
            "comment": comment[:4000],
            "authority": "human_review",
            "executable": False,
        }
        if actor_principal_id is not None:
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", actor_principal_id
            ):
                raise ProtocolOcrReviewError("OCR reviewer identity is invalid.")
            if actor_role not in {
                "reviewer",
                "lab_admin",
                "organization_admin",
            }:
                raise ProtocolOcrReviewError("OCR reviewer role is invalid.")
            payload.update(
                {
                    "actor_principal_id": actor_principal_id,
                    "actor_role": actor_role,
                }
            )
        self.store.append_event(
            f"ocr-reviewed-{ocr_id}-{decision}",
            protocol_id,
            revision.revision_number,
            _OCR_REVIEWED_EVENT,
            payload,
        )
        return self.ocr_status(protocol_id)

    def _is_approved(
        self,
        revision: ProtocolRevisionRecord,
        analysis: AnalysisRevisionRecord | None,
    ) -> bool:
        if analysis is None:
            return False
        return any(
            event.event_type in (_APPROVAL_EVENT, _DEVELOPMENT_FIXTURE_EVENT, _DEVELOPMENT_ACTIVATION_EVENT)
            and event.protocol_revision_number == revision.revision_number
            and (
                event.analysis_revision_number is None
                or event.analysis_revision_number == analysis.analysis_revision_number
            )
            and isinstance(event.payload, dict)
            and event.payload.get("decision") in ("approved", "development_activated", "development_only")
            for event in self.store.list_events(revision.experiment_id)
        )

    def approval_context(self, protocol_id: str) -> dict[str, object]:
        """Return the recorded approval actor and time without adding authority.

        This is a read-only researcher-facing projection of the same append-only
        event that already determines execution availability.  Missing actor
        metadata remains explicit instead of being inferred from an approval
        state or service policy.
        """

        entry = self.get_entry(protocol_id)
        final_approval = entry.approval_status == "approved"
        if entry.approval_status not in {"approved", "development_only"}:
            return {
                "status": "review_required",
                "final_approval": False,
                "actor_principal_id": None,
                "actor_role": None,
                "recorded_at": None,
                "authority": None,
            }
        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            entry.revision_id
        )
        matching = tuple(
            event
            for event in self.store.list_events(protocol_id)
            if event.event_type
            in {
                _APPROVAL_EVENT,
                _DEVELOPMENT_FIXTURE_EVENT,
                _DEVELOPMENT_ACTIVATION_EVENT,
            }
            and event.protocol_revision_number == protocol_revision_number
            and (
                event.analysis_revision_number is None
                or event.analysis_revision_number == analysis_revision_number
            )
            and isinstance(event.payload, dict)
            and event.payload.get("decision")
            in {"approved", "development_activated", "development_only"}
        )
        event = matching[-1] if matching else None
        payload = event.payload if event is not None else {}
        actor_principal_id = payload.get("actor_principal_id")
        actor_role = payload.get("actor_role")
        authority = payload.get("authority")
        return {
            "status": "approved" if final_approval else "development_only",
            "final_approval": final_approval,
            "actor_principal_id": (
                actor_principal_id
                if isinstance(actor_principal_id, str) and actor_principal_id
                else None
            ),
            "actor_role": (
                actor_role if isinstance(actor_role, str) and actor_role else None
            ),
            "recorded_at": event.recorded_at if event is not None else None,
            "authority": (
                authority if isinstance(authority, str) and authority else None
            ),
        }

    def _latest_chunk_events(
        self,
        revision: ProtocolRevisionRecord,
    ) -> tuple[object, ...]:
        events = tuple(
            event
            for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
        )
        planned = tuple(
            event for event in events if event.event_type == _CHUNK_PLAN_EVENT
        )
        if not planned:
            return ()
        payload = planned[-1].payload
        if not isinstance(payload, dict):
            return ()
        run_id = payload.get("analysis_run_id")
        if not isinstance(run_id, str):
            return ()
        return tuple(
            event
            for event in events
            if isinstance(event.payload, dict)
            and event.payload.get("analysis_run_id") == run_id
        )

    def analysis_run_status(
        self,
        protocol_id: str,
    ) -> ProtocolAnalysisRunStatus:
        revision = self._latest_protocol_revision(protocol_id)
        candidate_revision_id = _revision_id(revision.revision_number)
        events = self._latest_chunk_events(revision)
        if not events:
            entry = self._entry_for_revision(revision)
            lifecycle_events = tuple(
                event for event in self.store.list_events(revision.experiment_id)
                if event.protocol_revision_number == revision.revision_number
                and event.event_type in {
                    _ANALYSIS_REQUESTED_EVENT,
                    _ANALYSIS_STARTED_EVENT,
                    _ANALYSIS_FAILED_EVENT,
                    _ANALYSIS_READY_EVENT,
                    _SINGLE_REVIEW_REQUIRED_EVENT,
                }
            )
            latest_failure = next(
                (
                    event.payload.get("failure_code")
                    for event in reversed(lifecycle_events)
                    if event.event_type == _ANALYSIS_FAILED_EVENT
                    and isinstance(event.payload, dict)
                    and isinstance(event.payload.get("failure_code"), str)
                ),
                None,
            )
            latest_failure_detail = next(
                (
                    event.payload.get("evidence_failure")
                    for event in reversed(lifecycle_events)
                    if event.event_type == _ANALYSIS_FAILED_EVENT
                    and isinstance(event.payload, dict)
                    and isinstance(
                        event.payload.get("evidence_failure"), dict
                    )
                ),
                None,
            )
            analysis_run_id = next(
                (
                    event.payload.get("analysis_id")
                    for event in reversed(lifecycle_events)
                    if isinstance(event.payload, dict)
                    and isinstance(event.payload.get("analysis_id"), str)
                ),
                None,
            )
            state = entry.analysis_status
            if lifecycle_events:
                state = {
                    _ANALYSIS_REQUESTED_EVENT: "analysis_pending",
                    _ANALYSIS_STARTED_EVENT: "analyzing",
                    _ANALYSIS_FAILED_EVENT: "analysis_failed",
                    _ANALYSIS_READY_EVENT: "analysis_ready",
                    _SINGLE_REVIEW_REQUIRED_EVENT: "review_required",
                }[lifecycle_events[-1].event_type]
            return ProtocolAnalysisRunStatus(
                protocol_id=protocol_id,
                candidate_revision_id=candidate_revision_id,
                analysis_run_id=analysis_run_id,
                state=state,
                total_chunks=0,
                completed_chunks=0,
                failed_chunks=0,
                pending_chunks=0,
                chunks=(),
                failure_code=latest_failure,
                failure_detail=latest_failure_detail,
                lifecycle_state=entry.lifecycle_state,
            )
        plan_event = next(
            event for event in events if event.event_type == _CHUNK_PLAN_EVENT
        )
        plan_payload = plan_event.payload
        raw_chunks = plan_payload.get("chunks", [])
        chunks: list[dict[str, object]] = []
        statuses: dict[
            str,
            tuple[str, str | None, dict[str, object] | None],
        ] = {}
        state = "chunk_planned"
        failure_code = None
        failure_detail = None
        merge_status = None
        cancelled = False
        for event in events:
            payload = event.payload
            if event.event_type == _CHUNK_STARTED_EVENT:
                state = "chunk_analysis_in_progress"
                statuses[str(payload.get("chunk_id"))] = (
                    "in_progress",
                    None,
                    None,
                )
            elif event.event_type == _CHUNK_COMPLETED_EVENT:
                statuses[str(payload.get("chunk_id"))] = (
                    "completed",
                    None,
                    None,
                )
            elif event.event_type == _CHUNK_FAILED_EVENT:
                code = payload.get("failure_code")
                failure_code = code if isinstance(code, str) else "chunk_analysis_failed"
                raw_detail = payload.get("evidence_failure")
                detail = raw_detail if isinstance(raw_detail, dict) else None
                if detail is not None:
                    failure_detail = detail
                statuses[str(payload.get("chunk_id"))] = (
                    "failed",
                    failure_code,
                    detail,
                )
                state = "chunk_analysis_failed"
            elif event.event_type == _MERGE_STARTED_EVENT:
                state = "merge_in_progress"
                merge_status = "in_progress"
            elif event.event_type == _MERGE_CONFLICT_EVENT:
                state = "merge_conflict"
                merge_status = "conflict"
                code = payload.get("failure_code")
                failure_code = code if isinstance(code, str) else "merge_conflict"
            elif event.event_type == _REVIEW_REQUIRED_EVENT:
                state = "review_required"
                merge_status = "complete"
            elif event.event_type == _RUN_CANCELLED_EVENT:
                state = "chunk_analysis_cancelled"
                failure_code = "analysis_cancelled"
                cancelled = True
        # Cancellation is a terminal fence. A worker may already be between
        # scheduling members of one batch when another connection appends the
        # cancellation event, so a later benign "started" event must never make
        # the run appear resumable or in progress again.
        if cancelled:
            state = "chunk_analysis_cancelled"
            failure_code = "analysis_cancelled"
        for raw in raw_chunks if isinstance(raw_chunks, list) else []:
            if not isinstance(raw, dict) or not isinstance(raw.get("chunk_id"), str):
                continue
            chunk_id = raw["chunk_id"]
            status, code, detail = statuses.get(
                chunk_id,
                ("pending", None, None),
            )
            chunk_status: dict[str, object] = {
                "chunk_id": chunk_id,
                "ordinal": raw.get("ordinal"),
                "source_page_start": raw.get("source_page_start"),
                "source_page_end": raw.get("source_page_end"),
                "source_page_refs": raw.get("source_page_refs", []),
                "status": status,
                "failure_code": code,
            }
            if detail is not None:
                chunk_status["failure_detail"] = detail
            chunks.append(chunk_status)
        completed = sum(chunk["status"] == "completed" for chunk in chunks)
        failed = sum(chunk["status"] == "failed" for chunk in chunks)
        return ProtocolAnalysisRunStatus(
            protocol_id=protocol_id,
            candidate_revision_id=candidate_revision_id,
            analysis_run_id=str(plan_payload.get("analysis_run_id")),
            state=state,
            total_chunks=len(chunks),
            completed_chunks=completed,
            failed_chunks=failed,
            pending_chunks=len(chunks) - completed - failed,
            chunks=tuple(chunks),
            failure_code=failure_code,
            failure_detail=failure_detail,
            merge_status=merge_status,
            restart_behavior=(
                "terminal_review_required"
                if state == "review_required"
                else "explicit_new_run_required_after_interruption"
                if state in {"chunk_planned", "chunk_analysis_in_progress", "merge_in_progress"}
                else "explicit_analysis_request_only"
            ),
            lifecycle_state=(
                "review_required"
                if state == "review_required"
                else "blocked"
                if state in {
                    "merge_conflict",
                    "chunk_analysis_failed",
                    "chunk_analysis_cancelled",
                }
                else "analyzing"
            ),
        )

    def _entry_for_revision(
        self, revision: ProtocolRevisionRecord
    ) -> ProtocolCatalogEntry:
        analysis = self._latest_analysis(revision)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source_path = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source_path)
        analysis_status = "review_required" if analysis else _analysis_state(extraction)
        lifecycle_state = "review_required" if analysis else "uploaded"
        if analysis is None:
            if analysis_status == "ocr_required":
                ocr_state = self._ocr_projection(
                    revision, extraction, include_text=False
                )["state"]
                analysis_status, lifecycle_state = {
                    "ocr_required": ("ocr_required", "blocked"),
                    "in_progress": ("ocr_in_progress", "analyzing"),
                    "review_required": ("ocr_review_required", "review_required"),
                    "accepted_for_analysis": (
                        "structured_analysis_ready",
                        "uploaded",
                    ),
                    "rejected": ("ocr_rejected", "blocked"),
                    "failed": ("ocr_failed", "blocked"),
                }.get(str(ocr_state), ("ocr_required", "blocked"))
            chunk_events = self._latest_chunk_events(revision)
            if chunk_events:
                event_states = {
                    _CHUNK_PLAN_EVENT: "chunk_planned",
                    _CHUNK_STARTED_EVENT: "chunk_analysis_in_progress",
                    _CHUNK_COMPLETED_EVENT: "chunk_analysis_in_progress",
                    _CHUNK_FAILED_EVENT: "chunk_analysis_failed",
                    _MERGE_STARTED_EVENT: "merge_in_progress",
                    _MERGE_CONFLICT_EVENT: "merge_conflict",
                    _REVIEW_REQUIRED_EVENT: "review_required",
                    _RUN_CANCELLED_EVENT: "chunk_analysis_cancelled",
                }
                analysis_status = event_states.get(
                    chunk_events[-1].event_type,
                    analysis_status,
                )
            failed = next(
                (
                    event
                    for event in reversed(
                        self.store.list_events(revision.experiment_id)
                    )
                    if event.protocol_revision_number == revision.revision_number
                    and event.event_type == _ANALYSIS_FAILED_EVENT
                ),
                None,
            )
            if failed is not None and not chunk_events:
                analysis_status = "analysis_failed"
            lifecycle_events = tuple(
                event
                for event in self.store.list_events(revision.experiment_id)
                if event.protocol_revision_number == revision.revision_number
                and event.event_type in {
                    _ANALYSIS_REQUESTED_EVENT,
                    _ANALYSIS_STARTED_EVENT,
                    _ANALYSIS_FAILED_EVENT,
                }
            )
            if lifecycle_events:
                lifecycle_state = {
                    _ANALYSIS_REQUESTED_EVENT: "analysis_pending",
                    _ANALYSIS_STARTED_EVENT: "analyzing",
                    _ANALYSIS_FAILED_EVENT: "blocked",
                }[lifecycle_events[-1].event_type]
            elif analysis_status == "ocr_required":
                lifecycle_state = "blocked"
        approved = self._is_approved(revision, analysis)
        if approved:
            is_dev_only = any(
                event.event_type in (_DEVELOPMENT_FIXTURE_EVENT, _DEVELOPMENT_ACTIVATION_EVENT)
                and event.protocol_revision_number == revision.revision_number
                for event in self.store.list_events(revision.experiment_id)
            )
            analysis_status = "active_development" if is_dev_only else "approved"
            approval_status = "development_only" if is_dev_only else "approved"
            lifecycle_state = "executable_draft" if is_dev_only else "approved"
        else:
            approval_status = "unapproved"
        readiness = (
            analysis.readiness_status if analysis else "analysis_required"
        )
        execution_ready = analysis is not None and (
            readiness == domain.ReadinessStatus.GUIDANCE_READY.value
            or self._readiness_gates_cleared(
                revision.experiment_id, revision.revision_number, analysis
            )
        )
        if analysis is not None and not approved:
            lifecycle_state = "review_required" if execution_ready else "blocked"
        available = bool(approved and execution_ready)
        title = (
            analysis.protocol.metadata.title
            if analysis is not None
            else extraction.metadata.title or Path(revision.original_filename).stem
        )
        return ProtocolCatalogEntry(
            protocol_id=revision.experiment_id,
            title=title,
            source_filename=revision.original_filename,
            source_sha256=revision.pdf_checksum,
            revision_id=_revision_id(
                revision.revision_number,
                analysis.analysis_revision_number if analysis else None,
            ),
            readiness_status=readiness,
            approval_status=approval_status,
            analysis_status=analysis_status,
            step_count=(
                sum(len(section.steps) for section in analysis.protocol.sections)
                if analysis
                else 0
            ),
            created_at=revision.created_at,
            available_for_execution=available,
            lifecycle_state=lifecycle_state,
        )

    def list_entries(self) -> tuple[ProtocolCatalogEntry, ...]:
        entries = []
        for experiment in self.store.list_experiments():
            entries.append(
                self._entry_for_revision(
                    self._latest_protocol_revision(experiment.experiment_id)
                )
            )
        return tuple(entries)

    @staticmethod
    def _development_fixture_payload(
        fixture: CuratedProtocolFixture,
    ) -> dict[str, object]:
        return {
            "protocol_id": fixture.protocol_id,
            "revision_id": fixture.revision_id,
            "fixture_sha256": fixture.fixture_sha256,
            "source_sha256": fixture.source_pdf_sha256,
            "status": fixture.status,
            "development_only": True,
            "final_approval": False,
        }

    def bootstrap_development_fixture(
        self,
        fixture: CuratedProtocolFixture,
    ) -> ProtocolDevelopmentBootstrap:
        """Idempotently materialize an already validated development fixture.

        This boundary creates immutable source and analysis records only.  It
        never appends an approval event and never changes execution readiness.
        """

        if (
            not fixture.development_only
            or fixture.status != "development_only_not_final_acceptance"
            or fixture.source_pdf_path is None
            or fixture.source_pdf_sha256
            != fixture.draft.protocol.metadata.file_checksum
            or fixture.protocol_id != fixture.draft.protocol.protocol_id
        ):
            raise ProtocolRegistrationError(
                "Development fixture is not eligible for materialization."
            )
        existing = self.store.get_experiment(fixture.protocol_id)
        analysis = self.store.create_experiment_with_analysis(
            fixture.protocol_id,
            fixture.source_pdf_path,
            f"curated-{fixture.fixture_sha256}",
            fixture.draft.protocol,
            fixture.draft.readiness,
            fixture.draft.capability_policy_id,
        )
        revision = self.store.get_protocol_revision(fixture.protocol_id, 1)
        if revision is None:
            raise ProtocolCatalogUnavailableError(
                "Development fixture revision is unavailable."
            )
        self.store.append_event(
            f"development-fixture-{fixture.fixture_sha256[:48]}",
            fixture.protocol_id,
            revision.revision_number,
            _DEVELOPMENT_FIXTURE_EVENT,
            self._development_fixture_payload(fixture),
            analysis_revision_number=analysis.analysis_revision_number,
        )
        return ProtocolDevelopmentBootstrap(
            entry=self._entry_for_revision(revision),
            deduplicated=existing is not None,
        )

    def development_fixture_is_materialized(
        self,
        fixture: CuratedProtocolFixture,
    ) -> bool:
        """Verify the exact development-only provenance marker in the store."""

        if not fixture.development_only:
            return False
        revisions = self.store.list_protocol_revisions(fixture.protocol_id)
        if len(revisions) != 1:
            return False
        revision = revisions[0]
        if revision.pdf_checksum != fixture.source_pdf_sha256:
            return False
        analyses = self.store.list_analysis_revisions(
            fixture.protocol_id,
            revision.revision_number,
        )
        if len(analyses) != 1:
            return False
        analysis = analyses[0]
        if (
            analysis.protocol != fixture.draft.protocol
            or analysis.readiness != fixture.draft.readiness
            or analysis.capability_policy_id
            != fixture.draft.capability_policy_id
        ):
            return False
        expected_payload = self._development_fixture_payload(fixture)
        return any(
            event.event_type == _DEVELOPMENT_FIXTURE_EVENT
            and event.protocol_revision_number == revision.revision_number
            and event.analysis_revision_number
            == analysis.analysis_revision_number
            and event.payload == expected_payload
            for event in self.store.list_events(fixture.protocol_id)
        )

    def get_entry(self, protocol_id: str) -> ProtocolCatalogEntry:
        return self._entry_for_revision(
            self._latest_protocol_revision(protocol_id)
        )

    def review(self, protocol_id: str) -> dict[str, object]:
        """Return a source-linked, read-only view of the latest analysis draft."""

        revision = self._latest_protocol_revision(protocol_id)
        entry = self._entry_for_revision(revision)
        analysis = self._latest_analysis(revision)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source)
        revision_events = tuple(
            event
            for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
        )
        latest_failure = next(
            (
                event.payload.get("failure_code")
                for event in reversed(revision_events)
                if event.event_type in {_ANALYSIS_FAILED_EVENT, _CHUNK_FAILED_EVENT}
                and isinstance(event.payload, dict)
                and isinstance(event.payload.get("failure_code"), str)
            ),
            None,
        )
        latest_failure_detail = next(
            (
                event.payload.get("evidence_failure")
                for event in reversed(revision_events)
                if event.event_type
                in {_ANALYSIS_FAILED_EVENT, _CHUNK_FAILED_EVENT}
                and isinstance(event.payload, dict)
                and isinstance(event.payload.get("evidence_failure"), dict)
            ),
            None,
        )
        base: dict[str, object] = {
            **entry.public_dict(),
            "analysis_available": analysis is not None,
            "lifecycle_state": entry.lifecycle_state,
            "analysis_failure": (
                {
                    "code": latest_failure,
                    "detail": latest_failure_detail,
                    "retryable": latest_failure
                    not in {"ocr_required", "protocol_pdf_too_large"},
                    "action": (
                        "Configure XAI_API_KEY and PROTOCOL_ANALYSIS_MODEL, then retry."
                        if latest_failure == "provider_configuration_missing"
                        else "Review the failure code and explicitly retry analysis."
                    ),
                }
                if latest_failure is not None
                else None
            ),
            "source": {
                "filename": revision.original_filename,
                "sha256": revision.pdf_checksum,
                "media_type": extraction.media_type,
                "byte_size": extraction.byte_size,
                "page_count": extraction.page_count,
                "all_pages_inspected": extraction.all_pages_inspected,
                "extraction_warnings": list(extraction.warnings),
            },
            "ocr": self._ocr_projection(
                revision, extraction, include_text=True
            ),
            "metadata": {},
            "before_start": [],
            "materials": [],
            "equipment": [],
            "sections": [],
            "constructs": [],
            "readiness": {
                "status": domain.ReadinessStatus.ANALYSIS_REQUIRED.value,
                "label": "Structured analysis has not been completed.",
                "reasons": [
                    {
                        "code": entry.analysis_status,
                        "message": "Explicit structured analysis is required before review or execution.",
                    }
                ],
            },
            "capability_policy_id": domain.P1_CAPABILITY_POLICY.profile_id,
            "gates": {
                "parsing": "passed",
                "structural_readiness": "pending",
                "hazard_review": "pending",
                "human_approval": "pending",
                "operational_authorization": "blocked",
            },
            "reviewer_actions": ["retry_analysis"],
        }
        if analysis is None:
            return base

        protocol = analysis.protocol
        # A reviewer reads the declared hazards whenever the Protocol declares
        # any.  There is no hazard word list: deciding which wording is
        # dangerous is the reviewer's judgement, and a fixed list quietly
        # reported "passed" for every hazard it did not happen to contain --
        # including, worst of all, for a Protocol where extraction had produced
        # no warning at all.  The zero case is now a blocking readiness reason
        # (``no_declared_safety_warnings``), so it is never reported as passed
        # here either.
        declared_warning_count = domain.declared_safety_warning_count(protocol)
        # Hazard review follows the readiness gate, not the count.  It used to
        # be required only when the count was non-zero, which meant a single
        # provider-produced warning both cleared the gate and, here, was the
        # reason a reviewer was asked to look -- while a Protocol with no
        # warning at all asked for no hazard review.  The count is reported
        # beside this as information; the gate decides.
        hazard_review_required = (
            domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value
            in analysis.readiness.reason_codes
            or (protocol.metadata.source_status or "").casefold()
            in {"in development", "development", "draft"}
        )
        metadata = {
            field.name: _review_value(getattr(protocol.metadata, field.name))
            for field in fields(protocol.metadata)
            if field.name != "pdf"
        }
        base.update(
            {
                "metadata": metadata,
                "before_start": _review_value(protocol.before_start),
                "materials": _review_value(protocol.materials),
                "equipment": _review_value(protocol.equipment),
                "sections": _review_value(protocol.sections),
                "constructs": [
                    {
                        "construct_type": type(construct).__name__,
                        **_review_value(construct),
                    }
                    for construct in protocol.constructs
                ],
                "readiness": _review_value(analysis.readiness),
                "capability_policy_id": analysis.capability_policy_id,
                "analysis_payload_sha256": analysis.payload_sha256,
                "page_coverage": [
                    dict(item) for item in analysis.page_coverage
                ],
                "declined_segment_count": sum(
                    len(item.get("declined_segment_ids") or ())
                    for item in analysis.page_coverage
                ),
                "hazard_review_required": hazard_review_required,
                "declared_safety_warning_count": declared_warning_count,
                # Whether every remaining blocking reason is an acknowledged
                # gate. The catalog owns the acknowledgement ledger, so it
                # answers this; callers must not re-derive it from the
                # readiness status, which does not know about acknowledgements.
                "readiness_gates_cleared": self._readiness_gates_cleared(
                    revision.experiment_id,
                    revision.revision_number,
                    analysis,
                ),
                "gates": {
                    "parsing": "passed",
                    "structural_readiness": (
                        "passed"
                        if analysis.readiness_status
                        == domain.ReadinessStatus.GUIDANCE_READY.value
                        else "blocked"
                    ),
                    # "not_declared" used to be reported whenever the count
                    # was zero, which read as a cleared gate for the case that
                    # most needs a reviewer. The gate decides here too.
                    "hazard_review": (
                        "review_required"
                        if hazard_review_required
                        else "passed"
                    ),
                    "human_approval": (
                        "passed" if entry.approval_status == "approved" else "pending"
                    ),
                    "operational_authorization": (
                        "passed"
                        if entry.approval_status == "approved"
                        and entry.available_for_execution
                        else "simulation_only"
                        if entry.approval_status == "development_only"
                        and entry.available_for_execution
                        else "blocked"
                    ),
                },
                "reviewer_actions": (
                    ["review_hazards", "approve", "reject"]
                    if hazard_review_required
                    else ["approve", "reject"]
                ),
            }
        )
        return base

    def register(
        self,
        source_pdf: str | Path,
        *,
        source_filename: str,
        media_type: str,
    ) -> ProtocolRegistration:
        filename = _display_filename(source_filename)
        if media_type.casefold().split(";", 1)[0].strip() != PDF_MEDIA_TYPE:
            raise ProtocolRegistrationError(
                "Protocol registration requires application/pdf."
            )
        source = Path(source_pdf)
        extraction = extract_protocol_pdf(source)
        existing = self.store.find_protocol_revision_by_checksum(
            extraction.sha256
        )
        if existing is not None:
            return ProtocolRegistration(
                self._entry_for_revision(existing), deduplicated=True
            )
        protocol_id = _protocol_id(extraction.sha256)
        creation = self.store.create_experiment(
            protocol_id, source, original_filename=filename
        )
        status = _analysis_state(extraction)
        self.store.append_event(
            f"registered-{extraction.sha256[:40]}",
            protocol_id,
            creation.protocol_revision.revision_number,
            "protocol_registered",
            {
                "source_filename": filename,
                "source_sha256": extraction.sha256,
                "analysis_status": status,
            },
        )
        return ProtocolRegistration(
            self._entry_for_revision(creation.protocol_revision),
            deduplicated=False,
        )

    def _append_chunk_event(
        self,
        revision: ProtocolRevisionRecord,
        plan: ProtocolChunkPlan,
        event_type: str,
        payload: dict[str, object],
        *identity: object,
    ) -> None:
        self.store.append_event(
            _event_id(plan.analysis_run_id, event_type, *identity),
            revision.experiment_id,
            revision.revision_number,
            event_type,
            {
                "analysis_run_id": plan.analysis_run_id,
                "document_id": plan.document_id,
                "candidate_revision_id": plan.candidate_revision_id,
                "planner_version": plan.planner_version,
                **payload,
            },
        )

    def _chunk_run_is_current(
        self,
        revision: ProtocolRevisionRecord,
        plan: ProtocolChunkPlan,
    ) -> bool:
        events = self._latest_chunk_events(revision)
        return bool(
            events
            and isinstance(events[0].payload, dict)
            and events[0].payload.get("analysis_run_id") == plan.analysis_run_id
            and not any(
                event.event_type == _RUN_CANCELLED_EVENT for event in events
            )
        )

    def cancel_analysis_run(
        self,
        protocol_id: str,
        analysis_run_id: str,
    ) -> ProtocolAnalysisRunStatus:
        """Explicitly fence one non-terminal run; never approve or resume it."""

        revision = self._latest_protocol_revision(protocol_id)
        events = self._latest_chunk_events(revision)
        if not events or not isinstance(events[0].payload, dict):
            raise ProtocolCatalogNotFoundError("Protocol analysis run is unknown.")
        if events[0].payload.get("analysis_run_id") != analysis_run_id:
            raise ProtocolCatalogNotFoundError("Protocol analysis run is unknown.")
        status = self.analysis_run_status(protocol_id)
        if status.state in {
            "review_required",
            "merge_conflict",
            "chunk_analysis_failed",
            "chunk_analysis_cancelled",
        }:
            return status
        plan = ProtocolChunkPlan(
            analysis_run_id=analysis_run_id,
            document_id=str(events[0].payload.get("document_id")),
            protocol_id=protocol_id,
            candidate_revision_id=str(
                events[0].payload.get("candidate_revision_id")
            ),
            planner_version=str(events[0].payload.get("planner_version")),
            planner_configuration_sha256=str(
                events[0].payload.get("planner_configuration_sha256")
            ),
            extracted_text_bytes=int(
                events[0].payload.get("extracted_text_bytes", 0)
            ),
            chunks=(),
        )
        self._append_chunk_event(
            revision,
            plan,
            _RUN_CANCELLED_EVENT,
            {"status": "cancelled", "failure_code": "analysis_cancelled"},
            "cancelled",
        )
        return self.analysis_run_status(protocol_id)

    def _analyze_chunked(
        self,
        revision: ProtocolRevisionRecord,
        extraction: ProtocolPdfExtraction,
        model: ProtocolAnalysisModel,
        *,
        analysis_id: str,
        limits: ChunkAnalysisLimits,
    ) -> ProtocolCatalogEntry:
        try:
            plan = plan_protocol_chunks(
                extraction,
                revision.experiment_id,
                _revision_id(revision.revision_number),
                limits=limits,
            )
        except ProtocolChunkAdmissionError as exc:
            raise ProtocolChunkAnalysisFailedError(
                "Protocol exceeds a bounded chunk-analysis admission limit."
            ) from exc
        if len(plan.chunks) < 2:
            raise ProtocolChunkedAnalysisRequiredError(
                "Protocol does not produce multiple bounded source chunks."
            )
        with _chunk_run_lock(plan.analysis_run_id):
            existing = self._latest_chunk_events(revision)
            if existing:
                payload = existing[0].payload
                if (
                    isinstance(payload, dict)
                    and payload.get("analysis_run_id") == plan.analysis_run_id
                ):
                    # Status reads and repeated explicit requests never resume
                    # or duplicate an interrupted/terminal run implicitly.
                    return self.get_entry(revision.experiment_id)
            self._append_chunk_event(
                revision,
                plan,
                _CHUNK_PLAN_EVENT,
                plan.public_dict(),
                "plan",
            )

        results: list[ValidatedChunkResult] = []
        executor = ThreadPoolExecutor(
            max_workers=limits.max_concurrency,
            thread_name_prefix="protocol-chunk-analysis",
        )
        deadline = time.monotonic() + limits.timeout_seconds
        timed_out = False
        try:
            for offset in range(0, len(plan.chunks), limits.max_concurrency):
                batch = plan.chunks[offset : offset + limits.max_concurrency]
                futures = {}
                for chunk in batch:
                    self._append_chunk_event(
                        revision,
                        plan,
                        _CHUNK_STARTED_EVENT,
                        {
                            "chunk_id": chunk.chunk_id,
                            "ordinal": chunk.ordinal,
                            "status": "in_progress",
                        },
                        chunk.chunk_id,
                        "started",
                    )
                    futures[
                        executor.submit(
                            _worker_analyze_chunk,
                            extraction,
                            chunk,
                            model,
                            limits.max_retries,
                        )
                    ] = chunk
                remaining_seconds = max(0.0, deadline - time.monotonic())
                done, pending = wait(futures, timeout=remaining_seconds)
                for future in pending:
                    future.cancel()
                    chunk = futures[future]
                    self._append_chunk_event(
                        revision,
                        plan,
                        _CHUNK_FAILED_EVENT,
                        {
                            "chunk_id": chunk.chunk_id,
                            "ordinal": chunk.ordinal,
                            "status": "failed",
                            "failure_code": "chunk_timeout",
                        },
                        chunk.chunk_id,
                        "failed",
                    )
                    timed_out = True
                batch_results: list[ValidatedChunkResult] = []
                failed = timed_out
                for future in done:
                    chunk = futures[future]
                    (
                        analysis,
                        attempts,
                        failure_code,
                        evidence_failure,
                    ) = future.result()
                    if analysis is None:
                        failure_payload: dict[str, object] = {
                            "chunk_id": chunk.chunk_id,
                            "ordinal": chunk.ordinal,
                            "status": "failed",
                            "attempts": attempts,
                            "failure_code": failure_code
                            or "chunk_analysis_failed",
                        }
                        if evidence_failure is not None:
                            failure_payload["evidence_failure"] = (
                                evidence_failure
                            )
                        self._append_chunk_event(
                            revision,
                            plan,
                            _CHUNK_FAILED_EVENT,
                            failure_payload,
                            chunk.chunk_id,
                            "failed",
                        )
                        failed = True
                        continue
                    result = ValidatedChunkResult(chunk, analysis, attempts)
                    payload_json, payload_sha256 = serialize_chunk_claim_analysis(
                        analysis,
                    )
                    if len(payload_json.encode("utf-8")) > limits.max_chunk_result_bytes:
                        self._append_chunk_event(
                            revision,
                            plan,
                            _CHUNK_FAILED_EVENT,
                            {
                                "chunk_id": chunk.chunk_id,
                                "ordinal": chunk.ordinal,
                                "status": "failed",
                                "attempts": attempts,
                                "failure_code": "chunk_result_too_large",
                            },
                            chunk.chunk_id,
                            "failed",
                        )
                        failed = True
                        continue
                    batch_results.append(result)
                    self._append_chunk_event(
                        revision,
                        plan,
                        _CHUNK_COMPLETED_EVENT,
                        {
                            "chunk_id": chunk.chunk_id,
                            "ordinal": chunk.ordinal,
                            "status": "completed",
                            "attempts": attempts,
                            "claim_payload_sha256": payload_sha256,
                            # This is a strictly decoded, evidence-validated
                            # internal recovery record. Status APIs omit it.
                            "claim_payload_json": payload_json,
                        },
                        chunk.chunk_id,
                        "completed",
                    )
                results.extend(
                    sorted(batch_results, key=lambda item: item.chunk.ordinal)
                )
                if failed:
                    raise ProtocolChunkAnalysisFailedError(
                        "One or more bounded Protocol chunks failed analysis."
                    )
                if not self._chunk_run_is_current(revision, plan):
                    raise ProtocolChunkAnalysisFailedError(
                        "A stale Protocol analysis run cannot accept results."
                    )
        finally:
            executor.shutdown(wait=not timed_out, cancel_futures=True)

        if not self._chunk_run_is_current(revision, plan):
            raise ProtocolChunkAnalysisFailedError(
                "A stale Protocol analysis run cannot enter merge."
            )
        self._append_chunk_event(
            revision,
            plan,
            _MERGE_STARTED_EVENT,
            {"status": "in_progress", "chunk_count": len(results)},
            "merge",
            "started",
        )
        try:
            merged_claims = merge_validated_chunk_results(
                extraction,
                plan,
                results,
            )
            merged = assemble_validated_protocol_claims(
                extraction,
                merged_claims,
            )
        except ProtocolChunkMergeError as exc:
            self._append_chunk_event(
                revision,
                plan,
                _MERGE_CONFLICT_EVENT,
                {
                    "status": "conflict",
                    "failure_code": exc.code,
                    "reason_code": exc.reason_code,
                },
                "merge",
                "conflict",
            )
            raise ProtocolChunkMergeConflictError(
                "Protocol chunk merge requires explicit review."
            ) from exc
        if not self._chunk_run_is_current(revision, plan):
            raise ProtocolChunkAnalysisFailedError(
                "A stale Protocol analysis run cannot publish a candidate."
            )
        analysis = self.store.append_analysis_revision(
            revision.experiment_id,
            revision.revision_number,
            analysis_id,
            merged.protocol,
            merged.readiness,
            merged.capability_policy_id,
            # Carry the per-segment dispositions with the analysis. Without
            # this a provider's explicit "no claim here" existed only during
            # processing, which for a reviewer is the same as never existing.
            tuple(item.public_dict() for item in merged_claims.page_coverage),
        )
        self._append_chunk_event(
            revision,
            plan,
            _REVIEW_REQUIRED_EVENT,
            {
                "status": "review_required",
                "analysis_revision_number": analysis.analysis_revision_number,
                "analysis_payload_sha256": analysis.payload_sha256,
            },
            "review",
            analysis.analysis_revision_number,
        )
        return self.get_entry(revision.experiment_id)

    def analyze(
        self,
        protocol_id: str,
        model: ProtocolAnalysisModel,
        *,
        analysis_id: str,
        chunk_limits: ChunkAnalysisLimits = ChunkAnalysisLimits(),
    ) -> ProtocolCatalogEntry:
        """Run analysis only at this explicit caller-owned boundary."""

        revision = self._latest_protocol_revision(protocol_id)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source)
        extraction = self._extraction_for_analysis(revision, extraction)
        status = _analysis_state(extraction)
        if status == "ocr_required":
            raise ProtocolOcrRequiredError(
                "Protocol requires reviewed OCR before structured analysis."
            )
        self.store.append_event(
            f"analysis-started-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _ANALYSIS_STARTED_EVENT,
            {"status": "analyzing", "analysis_id": analysis_id},
        )
        if status == "chunked_analysis_required":
            if not _claim_chunk_analysis_enabled():
                raise ProtocolChunkedAnalysisRequiredError(
                    "Evidence-first claim chunk analysis is disabled."
                )
            return self._analyze_chunked(
                revision,
                extraction,
                model,
                analysis_id=analysis_id,
                limits=chunk_limits,
            )
        try:
            draft = analyze_protocol_extraction(extraction, model)
        except Exception as exc:
            failure_code = getattr(exc, "code", "analysis_failed")
            if not isinstance(failure_code, str) or not failure_code:
                failure_code = "analysis_failed"
            failure_digest = hashlib.sha256(
                analysis_id.encode("utf-8")
            ).hexdigest()[:24]
            failure_payload: dict[str, object] = {
                "status": "failed",
                "failure_code": failure_code,
            }
            evidence_failure = _safe_evidence_failure(
                exc,
                source_revision=_revision_id(revision.revision_number),
                source_hash=revision.pdf_checksum,
            )
            if evidence_failure is not None:
                failure_payload["evidence_failure"] = evidence_failure
            self.store.append_event(
                f"analysis-failed-{failure_digest}",
                protocol_id,
                revision.revision_number,
                _ANALYSIS_FAILED_EVENT,
                failure_payload,
            )
            raise
        if draft.protocol.protocol_id != protocol_id:
            assigned_protocol = replace(draft.protocol, protocol_id=protocol_id)
            domain.validate_protocol(assigned_protocol)
            draft = replace(draft, protocol=assigned_protocol)
        analysis = self.store.append_analysis_revision(
            protocol_id,
            revision.revision_number,
            analysis_id,
            draft.protocol,
            draft.readiness,
            draft.capability_policy_id,
        )
        self.store.append_event(
            f"analysis-ready-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _ANALYSIS_READY_EVENT,
            {
                "status": "analysis_ready",
                "analysis_payload_sha256": analysis.payload_sha256,
            },
            analysis_revision_number=analysis.analysis_revision_number,
        )
        self.store.append_event(
            f"review-required-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _SINGLE_REVIEW_REQUIRED_EVENT,
            {
                "status": "review_required",
                "analysis_payload_sha256": analysis.payload_sha256,
            },
            analysis_revision_number=analysis.analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def request_analysis(self, protocol_id: str, analysis_id: str) -> ProtocolCatalogEntry:
        """Persist an explicit analysis request without contacting a provider."""

        revision = self._latest_protocol_revision(protocol_id)
        if self._latest_analysis(revision) is not None:
            return self.get_entry(protocol_id)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source)
        if (
            _analysis_state(extraction) == "ocr_required"
            and self._ocr_projection(
                revision, extraction, include_text=False
            ).get("accepted_for_analysis")
            is not True
        ):
            raise ProtocolOcrRequiredError(
                "Protocol requires reviewed OCR before structured analysis."
            )
        self.store.append_event(
            f"analysis-requested-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _ANALYSIS_REQUESTED_EVENT,
            {"status": "analysis_pending", "analysis_id": analysis_id},
        )
        return self.get_entry(protocol_id)

    def fail_analysis_request(
        self,
        protocol_id: str,
        analysis_id: str,
        *,
        failure_code: str,
    ) -> ProtocolCatalogEntry:
        """Persist only a bounded failure code for an analysis that did not start."""

        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", failure_code):
            failure_code = "analysis_failed"
        revision = self._latest_protocol_revision(protocol_id)
        self.store.append_event(
            f"analysis-failed-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _ANALYSIS_FAILED_EVENT,
            {"status": "failed", "failure_code": failure_code},
        )
        return self.get_entry(protocol_id)

    def _acknowledged_gates(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis_revision_number: int,
    ) -> frozenset[str]:
        """Gate reason codes a human has cleared for this analysis revision."""

        return frozenset(
            str(event.payload.get("reason_code"))
            for event in self.store.list_events(protocol_id)
            if event.event_type == _GATE_ACKNOWLEDGEMENT_EVENT
            and event.protocol_revision_number == protocol_revision_number
            and event.analysis_revision_number == analysis_revision_number
            and isinstance(event.payload, dict)
        )

    def _readiness_gates_cleared(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis: Any,
    ) -> bool:
        """True when a person has cleared every blocking reason.

        There are two ways a reason clears, and both need a person. A gate in
        ``_ACKNOWLEDGEABLE_GATES`` clears when that exact gate is acknowledged.
        ``unresolved_ambiguity`` clears only when *every* ambiguity the
        analysis carries has a standing finding that one statement is
        authoritative -- one settled ambiguity does not clear the reason, and a
        finding that two statements are genuinely distinct does not clear it at
        all, because that is a finding rather than a resolution.
        ``unconfirmed_fixed_repetition`` clears only when every bounded
        repetition has a standing confirmation whose count matches the analysed
        one.

        Anything else still blocks, and with nothing recorded the answer is
        False. Nothing here inspects the ambiguous text.
        """

        blocking = set(analysis.readiness.reason_codes)
        if not blocking:
            return False
        acknowledged = self._acknowledged_gates(
            protocol_id,
            protocol_revision_number,
            analysis.analysis_revision_number,
        )
        for reason_code in blocking:
            if reason_code in _ACKNOWLEDGEABLE_GATES:
                if reason_code not in acknowledged:
                    return False
            elif (
                reason_code
                == domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY.value
            ):
                if not self._every_ambiguity_resolved(
                    protocol_id, protocol_revision_number, analysis
                ):
                    return False
            elif (
                reason_code
                == domain.ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION.value
            ):
                if not self._every_fixed_repetition_confirmed(
                    protocol_id, protocol_revision_number, analysis
                ):
                    return False
            elif (
                reason_code
                == domain.ReadinessReasonCode
                .UNCONFIRMED_LABEL_DISPOSITION.value
            ):
                if not self._every_label_disposition_confirmed(
                    protocol_id, protocol_revision_number, analysis
                ):
                    return False
            else:
                return False
        return True

    def _every_ambiguity_resolved(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis: Any,
    ) -> bool:
        """Every ambiguity in this analysis has a standing authoritative finding."""

        outstanding = {
            construct.ambiguity_id
            for construct in analysis.protocol.constructs
            if isinstance(construct, domain.SourceAmbiguity)
            and not construct.resolved
        }
        if not outstanding:
            return False
        findings = self._ambiguity_findings(
            protocol_id,
            protocol_revision_number,
            analysis.analysis_revision_number,
        )
        return all(
            findings.get(ambiguity_id) == AMBIGUITY_SINGLE_AUTHORITATIVE
            for ambiguity_id in outstanding
        )

    def acknowledge_readiness_gate(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        reason_code: str,
        actor_principal_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> ProtocolCatalogEntry:
        """Record a human clearing one readiness gate on one analysis revision.

        These gates are deliberately not self-clearing: some protocols
        genuinely carry no safety warning, and some environments genuinely
        cannot run a second extraction engine.  Only a person can tell either
        apart from a failure.  The decision is written to the append-only
        ledger with the actor, role, comment and the store's ``recorded_at``
        timestamp, so who cleared what, and when, stays auditable.
        """

        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            raise ProtocolApprovalError(
                "A validated analysis revision is required for acknowledgement."
            )
        if reason_code not in _ACKNOWLEDGEABLE_GATES:
            raise ProtocolApprovalError(
                "This readiness reason cannot be cleared by acknowledgement."
            )
        _checked_actor(actor_principal_id, actor_role, ProtocolApprovalError)
        revision = self.store.get_protocol_revision(
            protocol_id, protocol_revision_number
        )
        if revision is None:
            raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
        analysis = self.store.get_analysis_revision(
            protocol_id, protocol_revision_number, analysis_revision_number
        )
        if reason_code not in analysis.readiness.reason_codes:
            raise ProtocolApprovalError(
                "This analysis revision does not carry that readiness gate."
            )
        self.store.append_event(
            (
                f"gate-ack-{protocol_id[-16:]}-{protocol_revision_number}-"
                f"{analysis_revision_number}-{reason_code}"
            ),
            protocol_id,
            protocol_revision_number,
            _GATE_ACKNOWLEDGEMENT_EVENT,
            {
                "decision": "acknowledged",
                "reason_code": reason_code,
                "actor_principal_id": actor_principal_id,
                "actor_role": actor_role,
                "comment": (comment or "Reviewer cleared the readiness gate.")[
                    :4000
                ],
            },
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def resolve_ambiguity(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        ambiguity_id: str,
        decision: str,
        evidence_segment_ids: tuple[str, ...],
        actor_principal_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> ProtocolCatalogEntry:
        """Record one reviewer's finding about one source ambiguity.

        The pipeline stops on an ambiguity a person can settle in seconds --
        typically a source that states one interval twice, once in prose and
        once as a timer literal -- and until now there was no way to say so.
        Acknowledging a readiness gate is too coarse: it would clear every
        ambiguity in the document at once, including ones nobody had looked at.

        What this does *not* do matters as much. It never edits the source, it
        never deletes or rewrites a claim, and it never sets ``resolved`` on the
        stored analysis: the analysis stays exactly as validated, and the
        finding is appended beside it. Nothing infers whether two statements
        agree -- no string or numeric comparison decides it, because that would
        be repairing the document on a guess. Only a person decides, and the
        default with no decision recorded is still blocked.

        The reviewer must cite the segments they read. Those handles are
        resolved against the source before the finding is accepted, so a
        decision cannot rest on a span that does not exist.
        """

        if decision not in _AMBIGUITY_DECISIONS:
            raise ProtocolApprovalError("Ambiguity decision is unsupported.")
        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            raise ProtocolApprovalError(
                "A validated analysis revision is required to resolve an "
                "ambiguity."
            )
        _checked_actor(actor_principal_id, actor_role, ProtocolApprovalError)
        revision = self.store.get_protocol_revision(
            protocol_id, protocol_revision_number
        )
        if revision is None:
            raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
        analysis = self.store.get_analysis_revision(
            protocol_id, protocol_revision_number, analysis_revision_number
        )
        ambiguity = next(
            (
                construct
                for construct in analysis.protocol.constructs
                if isinstance(construct, domain.SourceAmbiguity)
                and construct.ambiguity_id == ambiguity_id
            ),
            None,
        )
        if ambiguity is None:
            raise ProtocolApprovalError(
                "This analysis revision has no such ambiguity."
            )
        if not evidence_segment_ids:
            raise ProtocolApprovalError(
                "An ambiguity decision must cite the segments it rests on."
            )
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        extraction = extract_protocol_pdf(
            self.store.file_store.object_path(
                revision.pdf_checksum, expected_size=pdf_object.byte_size
            )
        )
        try:
            reopen_evidence_span(
                extraction,
                replace(
                    ambiguity.evidence,
                    evidence_segment_ids=tuple(evidence_segment_ids),
                ),
                source_revision="pdf-1",
            )
        except Exception as exc:  # noqa: BLE001 - a citation that will not open
            raise ProtocolApprovalError(
                "The cited evidence segments do not resolve on that page."
            ) from exc
        ordinal = self._finding_ordinal(
            protocol_id,
            protocol_revision_number,
            analysis_revision_number,
            ambiguity_id,
            (_AMBIGUITY_RESOLUTION_EVENT, _AMBIGUITY_REVOCATION_EVENT),
            "ambiguity_id",
        )
        self.store.append_event(
            (
                f"ambiguity-{protocol_id[-16:]}-{protocol_revision_number}-"
                f"{analysis_revision_number}-{ambiguity_id[:40]}-{ordinal}"
            ),
            protocol_id,
            protocol_revision_number,
            _AMBIGUITY_RESOLUTION_EVENT,
            {
                "decision": decision,
                "ambiguity_id": ambiguity_id,
                "step_id": ambiguity.step_id,
                "action_id": ambiguity.action_id,
                "source_page_number": ambiguity.evidence.source_page_number,
                "evidence_segment_ids": list(evidence_segment_ids),
                "actor_principal_id": actor_principal_id,
                "actor_role": actor_role,
                "comment": (comment or "Reviewer recorded a finding.")[:4000],
            },
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def _finding_context(
        self,
        protocol_id: str,
        revision_id: str,
        actor_principal_id: str,
        actor_role: str,
    ) -> tuple[int, int, Any, Any]:
        """Shared preamble for every reviewer finding on one analysis."""

        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            raise ProtocolApprovalError(
                "A validated analysis revision is required to record a "
                "finding."
            )
        _checked_actor(actor_principal_id, actor_role, ProtocolApprovalError)
        revision = self.store.get_protocol_revision(
            protocol_id, protocol_revision_number
        )
        if revision is None:
            raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
        analysis = self.store.get_analysis_revision(
            protocol_id, protocol_revision_number, analysis_revision_number
        )
        return (
            protocol_revision_number,
            analysis_revision_number,
            revision,
            analysis,
        )

    def _check_cited_segments(
        self,
        revision: Any,
        evidence: domain.SourceEvidence,
        evidence_segment_ids: tuple[str, ...],
    ) -> None:
        """A finding must cite segments that actually open on that page."""

        if not evidence_segment_ids:
            raise ProtocolApprovalError(
                "A reviewer finding must cite the segments it rests on."
            )
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        extraction = extract_protocol_pdf(
            self.store.file_store.object_path(
                revision.pdf_checksum, expected_size=pdf_object.byte_size
            )
        )
        try:
            reopen_evidence_span(
                extraction,
                replace(
                    evidence,
                    evidence_segment_ids=tuple(evidence_segment_ids),
                ),
                source_revision="pdf-1",
            )
        except Exception as exc:  # noqa: BLE001 - a citation that will not open
            raise ProtocolApprovalError(
                "The cited evidence segments do not resolve on that page."
            ) from exc

    def _finding_ordinal(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis_revision_number: int,
        construct_id: str,
        event_types: tuple[str, ...],
        key: str,
    ) -> int:
        """How many findings this construct already has, plus one.

        The ledger is append-only and refuses to reuse an identifier for
        different content, so a reviewer who withdraws a finding and records a
        different one needs a fresh identifier rather than a collision.
        """

        return 1 + sum(
            1
            for event in self.store.list_events(protocol_id)
            if event.event_type in event_types
            and event.protocol_revision_number == protocol_revision_number
            and event.analysis_revision_number == analysis_revision_number
            and isinstance(event.payload, dict)
            and event.payload.get(key) == construct_id
        )

    def confirm_label_disposition(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        source_page_number: int,
        source_label: str,
        evidence_segment_ids: tuple[str, ...],
        actor_principal_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> ProtocolCatalogEntry:
        """Record a reviewer agreeing a numbered label is not a step.

        Measured on three of the four local sources, a numbered line is not
        always an instruction. But the asymmetry runs opposite to the
        repetition one: turning a description into a step only makes the agent
        read a description aloud, while disposing of a real step removes it
        from the protocol entirely. So the disposition is the exception a
        person confirms, and it blocks until they do.
        """

        (
            protocol_revision_number,
            analysis_revision_number,
            revision,
            analysis,
        ) = self._finding_context(
            protocol_id, revision_id, actor_principal_id, actor_role
        )
        disposition = next(
            (
                item
                for item in analysis.protocol.label_dispositions
                if item.source_label == source_label
                and item.source_page_number == source_page_number
            ),
            None,
        )
        if disposition is None:
            raise ProtocolApprovalError(
                "This analysis revision has no such label disposition."
            )
        self._check_cited_segments(
            revision, disposition.evidence, evidence_segment_ids
        )
        key = f"{source_page_number}:{source_label}"
        ordinal = self._finding_ordinal(
            protocol_id,
            protocol_revision_number,
            analysis_revision_number,
            key,
            (_DISPOSITION_CONFIRMATION_EVENT, _DISPOSITION_REVOCATION_EVENT),
            "label_key",
        )
        self.store.append_event(
            (
                f"disposition-{protocol_id[-16:]}-{protocol_revision_number}-"
                f"{analysis_revision_number}-{key}-{ordinal}"
            ),
            protocol_id,
            protocol_revision_number,
            _DISPOSITION_CONFIRMATION_EVENT,
            {
                "decision": "not_an_execution_step",
                "label_key": key,
                "source_page_number": source_page_number,
                "source_label": source_label,
                "evidence_segment_ids": list(evidence_segment_ids),
                "actor_principal_id": actor_principal_id,
                "actor_role": actor_role,
                "comment": (comment or "Reviewer confirmed a disposition.")[
                    :4000
                ],
            },
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def revoke_label_disposition_confirmation(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        source_page_number: int,
        source_label: str,
        actor_principal_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> ProtocolCatalogEntry:
        """Withdraw a disposition confirmation, which blocks again."""

        (
            protocol_revision_number,
            analysis_revision_number,
            _revision,
            _analysis,
        ) = self._finding_context(
            protocol_id, revision_id, actor_principal_id, actor_role
        )
        key = f"{source_page_number}:{source_label}"
        if key not in self._disposition_findings(
            protocol_id, protocol_revision_number, analysis_revision_number
        ):
            raise ProtocolApprovalError(
                "This analysis revision carries no confirmation to revoke."
            )
        ordinal = self._finding_ordinal(
            protocol_id,
            protocol_revision_number,
            analysis_revision_number,
            key,
            (_DISPOSITION_CONFIRMATION_EVENT, _DISPOSITION_REVOCATION_EVENT),
            "label_key",
        )
        self.store.append_event(
            (
                f"disposition-revoke-{protocol_id[-16:]}-"
                f"{protocol_revision_number}-{analysis_revision_number}-"
                f"{key}-{ordinal}"
            ),
            protocol_id,
            protocol_revision_number,
            _DISPOSITION_REVOCATION_EVENT,
            {
                "decision": "revoked",
                "label_key": key,
                "actor_principal_id": actor_principal_id,
                "actor_role": actor_role,
                "comment": (comment or "Reviewer withdrew a confirmation.")[
                    :4000
                ],
            },
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def _disposition_findings(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis_revision_number: int,
    ) -> frozenset[str]:
        """Standing disposition confirmations; the last event per label wins."""

        standing: set[str] = set()
        for event in self.store.list_events(protocol_id):
            if event.protocol_revision_number != protocol_revision_number:
                continue
            if event.analysis_revision_number != analysis_revision_number:
                continue
            if not isinstance(event.payload, dict):
                continue
            key = event.payload.get("label_key")
            if not isinstance(key, str):
                continue
            if event.event_type == _DISPOSITION_CONFIRMATION_EVENT:
                standing.add(key)
            elif event.event_type == _DISPOSITION_REVOCATION_EVENT:
                standing.discard(key)
        return frozenset(standing)

    def label_disposition_findings(
        self,
        protocol_id: str,
        revision_id: str,
    ) -> frozenset[str]:
        """Read the standing confirmations, for a reviewer or a projection."""

        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            return frozenset()
        return self._disposition_findings(
            protocol_id, protocol_revision_number, analysis_revision_number
        )

    def _every_label_disposition_confirmed(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis: Any,
    ) -> bool:
        """Every disposed label has a standing confirmation."""

        outstanding = {
            f"{item.source_page_number}:{item.source_label}"
            for item in analysis.protocol.label_dispositions
        }
        if not outstanding:
            return False
        return outstanding <= self._disposition_findings(
            protocol_id,
            protocol_revision_number,
            analysis.analysis_revision_number,
        )

    def confirm_fixed_repetition(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        repetition_id: str,
        repeat_count: int,
        evidence_segment_ids: tuple[str, ...],
        actor_principal_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> ProtocolCatalogEntry:
        """Record a reviewer confirming a bounded repetition and its count.

        The two ways of getting a repetition's kind wrong are not symmetric.
        Calling a conditional repetition fixed makes the agent stop early and
        announce completion while the source's own condition is unmet -- a
        false completion notice, the worst outcome this system can produce.
        Calling a fixed repetition conditional only makes it ask a person. So
        a declared count does not execute on the model's word: a reviewer
        confirms both that the repetition really is bounded and what the bound
        is, citing the source they read.

        The confirmed count must match the count the analysis carries. A
        reviewer who believes the number is different is not confirming this
        repetition, and re-analysis rather than an override is the route.
        """

        if not isinstance(repeat_count, int) or isinstance(repeat_count, bool):
            raise ProtocolApprovalError("A confirmed count must be a number.")
        if repeat_count < 1:
            raise ProtocolApprovalError("A confirmed count must be positive.")
        (
            protocol_revision_number,
            analysis_revision_number,
            revision,
            analysis,
        ) = self._finding_context(protocol_id, revision_id, actor_principal_id, actor_role)
        repetition = next(
            (
                construct
                for construct in analysis.protocol.constructs
                if isinstance(construct, domain.FixedRangeRepetition)
                and construct.repetition_id == repetition_id
            ),
            None,
        )
        if repetition is None:
            raise ProtocolApprovalError(
                "This analysis revision has no such fixed repetition."
            )
        if repetition.repeat_count != repeat_count:
            raise ProtocolApprovalError(
                "The confirmed count does not match the analysed count."
            )
        self._check_cited_segments(
            revision, repetition.evidence, evidence_segment_ids
        )
        ordinal = self._finding_ordinal(
            protocol_id,
            protocol_revision_number,
            analysis_revision_number,
            repetition_id,
            (_REPETITION_CONFIRMATION_EVENT, _REPETITION_REVOCATION_EVENT),
            "repetition_id",
        )
        self.store.append_event(
            (
                f"repetition-{protocol_id[-16:]}-{protocol_revision_number}-"
                f"{analysis_revision_number}-{repetition_id[:38]}-{ordinal}"
            ),
            protocol_id,
            protocol_revision_number,
            _REPETITION_CONFIRMATION_EVENT,
            {
                "decision": "fixed_count_confirmed",
                "repetition_id": repetition_id,
                "repeat_count": repeat_count,
                "start_step_id": repetition.start_step_id,
                "end_step_id": repetition.end_step_id,
                "source_page_number": repetition.evidence.source_page_number,
                "evidence_segment_ids": list(evidence_segment_ids),
                "actor_principal_id": actor_principal_id,
                "actor_role": actor_role,
                "comment": (comment or "Reviewer confirmed a fixed count.")[
                    :4000
                ],
            },
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def revoke_fixed_repetition_confirmation(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        repetition_id: str,
        actor_principal_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> ProtocolCatalogEntry:
        """Withdraw a confirmation, which blocks the Protocol again."""

        (
            protocol_revision_number,
            analysis_revision_number,
            _revision,
            _analysis,
        ) = self._finding_context(protocol_id, revision_id, actor_principal_id, actor_role)
        if repetition_id not in self._repetition_findings(
            protocol_id, protocol_revision_number, analysis_revision_number
        ):
            raise ProtocolApprovalError(
                "This analysis revision carries no confirmation to revoke."
            )
        ordinal = self._finding_ordinal(
            protocol_id,
            protocol_revision_number,
            analysis_revision_number,
            repetition_id,
            (_REPETITION_CONFIRMATION_EVENT, _REPETITION_REVOCATION_EVENT),
            "repetition_id",
        )
        self.store.append_event(
            (
                f"repetition-revoke-{protocol_id[-16:]}-"
                f"{protocol_revision_number}-{analysis_revision_number}-"
                f"{repetition_id[:32]}-{ordinal}"
            ),
            protocol_id,
            protocol_revision_number,
            _REPETITION_REVOCATION_EVENT,
            {
                "decision": "revoked",
                "repetition_id": repetition_id,
                "actor_principal_id": actor_principal_id,
                "actor_role": actor_role,
                "comment": (comment or "Reviewer withdrew a confirmation.")[
                    :4000
                ],
            },
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def _repetition_findings(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis_revision_number: int,
    ) -> dict[str, int]:
        """The standing confirmed count per repetition; the last event wins."""

        findings: dict[str, int] = {}
        for event in self.store.list_events(protocol_id):
            if event.protocol_revision_number != protocol_revision_number:
                continue
            if event.analysis_revision_number != analysis_revision_number:
                continue
            if not isinstance(event.payload, dict):
                continue
            repetition_id = event.payload.get("repetition_id")
            if not isinstance(repetition_id, str):
                continue
            if event.event_type == _REPETITION_CONFIRMATION_EVENT:
                count = event.payload.get("repeat_count")
                if isinstance(count, int) and not isinstance(count, bool):
                    findings[repetition_id] = count
            elif event.event_type == _REPETITION_REVOCATION_EVENT:
                findings.pop(repetition_id, None)
        return findings

    def repetition_findings(
        self,
        protocol_id: str,
        revision_id: str,
    ) -> dict[str, int]:
        """Read the standing confirmations, for a reviewer or a projection."""

        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            return {}
        return self._repetition_findings(
            protocol_id, protocol_revision_number, analysis_revision_number
        )

    def _every_fixed_repetition_confirmed(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis: Any,
    ) -> bool:
        """Every bounded repetition has a standing confirmation of its count."""

        outstanding = {
            construct.repetition_id: construct.repeat_count
            for construct in analysis.protocol.constructs
            if isinstance(construct, domain.FixedRangeRepetition)
        }
        if not outstanding:
            return False
        findings = self._repetition_findings(
            protocol_id,
            protocol_revision_number,
            analysis.analysis_revision_number,
        )
        return all(
            findings.get(repetition_id) == count
            for repetition_id, count in outstanding.items()
        )

    def revoke_ambiguity_resolution(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        ambiguity_id: str,
        actor_principal_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> ProtocolCatalogEntry:
        """Withdraw a finding, which blocks the Protocol again.

        A wrong decision has to be undoable, and undoing it must restore the
        block rather than leave the Protocol open on a withdrawn finding. The
        earlier decision is not erased -- the ledger is append-only -- so who
        decided what, and who later withdrew it, both stay readable.
        """

        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            raise ProtocolApprovalError(
                "A validated analysis revision is required to revoke a "
                "finding."
            )
        _checked_actor(actor_principal_id, actor_role, ProtocolApprovalError)
        if ambiguity_id not in self._ambiguity_findings(
            protocol_id, protocol_revision_number, analysis_revision_number
        ):
            raise ProtocolApprovalError(
                "This analysis revision carries no finding to revoke."
            )
        ordinal = self._finding_ordinal(
            protocol_id,
            protocol_revision_number,
            analysis_revision_number,
            ambiguity_id,
            (_AMBIGUITY_RESOLUTION_EVENT, _AMBIGUITY_REVOCATION_EVENT),
            "ambiguity_id",
        )
        self.store.append_event(
            (
                f"ambiguity-revoke-{protocol_id[-16:]}-"
                f"{protocol_revision_number}-{analysis_revision_number}-"
                f"{ambiguity_id[:34]}-{ordinal}"
            ),
            protocol_id,
            protocol_revision_number,
            _AMBIGUITY_REVOCATION_EVENT,
            {
                "decision": "revoked",
                "ambiguity_id": ambiguity_id,
                "actor_principal_id": actor_principal_id,
                "actor_role": actor_role,
                "comment": (comment or "Reviewer withdrew a finding.")[:4000],
            },
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def _ambiguity_findings(
        self,
        protocol_id: str,
        protocol_revision_number: int,
        analysis_revision_number: int,
    ) -> dict[str, str]:
        """The standing finding per ambiguity: the last event for each wins."""

        findings: dict[str, str] = {}
        for event in self.store.list_events(protocol_id):
            if event.protocol_revision_number != protocol_revision_number:
                continue
            if event.analysis_revision_number != analysis_revision_number:
                continue
            if not isinstance(event.payload, dict):
                continue
            ambiguity_id = event.payload.get("ambiguity_id")
            if not isinstance(ambiguity_id, str):
                continue
            if event.event_type == _AMBIGUITY_RESOLUTION_EVENT:
                findings[ambiguity_id] = str(event.payload.get("decision"))
            elif event.event_type == _AMBIGUITY_REVOCATION_EVENT:
                findings.pop(ambiguity_id, None)
        return findings

    def ambiguity_findings(
        self,
        protocol_id: str,
        revision_id: str,
    ) -> dict[str, str]:
        """Read the standing findings, for a reviewer or a projection."""

        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            return {}
        return self._ambiguity_findings(
            protocol_id, protocol_revision_number, analysis_revision_number
        )

    def approve(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        policy: ApprovalPolicy,
        presented_secret: str | None,
        actor_principal_id: str | None = None,
        actor_role: str | None = None,
        comment: str | None = None,
    ) -> ProtocolCatalogEntry:
        if not policy.permits(presented_secret):
            raise ProtocolApprovalError("Protocol approval authorization failed.")
        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            raise ProtocolApprovalError(
                "A validated analysis revision is required for approval."
            )
        revision = self.store.get_protocol_revision(
            protocol_id, protocol_revision_number
        )
        if revision is None:
            raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
        analysis = self.store.get_analysis_revision(
            protocol_id, protocol_revision_number, analysis_revision_number
        )
        if analysis.readiness.status is not domain.ReadinessStatus.GUIDANCE_READY:
            if not self._readiness_gates_cleared(
                protocol_id, protocol_revision_number, analysis
            ):
                raise ProtocolApprovalError(
                    "Protocol analysis is not ready for execution approval."
                )
        payload = {"decision": "approved", "authority": "service_policy"}
        if actor_principal_id is not None:
            _checked_actor(actor_principal_id, actor_role, ProtocolApprovalError)
            payload.update({
                "actor_principal_id":actor_principal_id,
                "actor_role":actor_role,
                "comment":(comment or "Tenant RBAC approval.")[:4000],
            })
        self.store.append_event(
            (
                f"approved-{protocol_id[-16:]}-{protocol_revision_number}-"
                f"{analysis_revision_number}"
            ),
            protocol_id,
            protocol_revision_number,
            _APPROVAL_EVENT,
            payload,
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def activate_development(
        self,
        protocol_id: str,
        *,
        revision_id: str | None = None,
    ) -> ProtocolCatalogEntry:
        revision = self._latest_protocol_revision(protocol_id)
        analysis = self._latest_analysis(revision)
        if analysis is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol analysis is required before development activation."
            )
        if analysis.readiness.status is not domain.ReadinessStatus.GUIDANCE_READY:
            if not self._readiness_gates_cleared(
                protocol_id, revision.revision_number, analysis
            ):
                raise ProtocolCatalogUnavailableError(
                    f"Protocol readiness ({analysis.readiness.status.value}) is not ready for development execution."
                )
        self.store.append_event(
            f"dev-active-{protocol_id[-16:]}-{revision.revision_number}-{analysis.analysis_revision_number}",
            protocol_id,
            revision.revision_number,
            _DEVELOPMENT_ACTIVATION_EVENT,
            {
                "decision": "development_activated",
                "authority": "development_policy",
                "readiness": analysis.readiness.status.value,
            },
            analysis_revision_number=analysis.analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def load_executable_fixture(
        self, protocol_id: str
    ) -> CuratedProtocolFixture:
        revision = self._latest_protocol_revision(protocol_id)
        analysis = self._latest_analysis(revision)
        entry = self._entry_for_revision(revision)
        if analysis is None or not entry.available_for_execution:
            raise ProtocolCatalogUnavailableError(
                "Protocol revision is not approved and ready for execution."
            )
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source)
        draft = ProtocolAnalysisDraft(
            extraction=extraction,
            protocol=analysis.protocol,
            readiness=analysis.readiness,
            capability_policy=domain.P1_CAPABILITY_POLICY,
            analysis_schema_version=analysis.analysis_schema_version,
            verified_evidence_count=0,
        )
        labels = tuple(
            step.source_label
            for section in analysis.protocol.sections
            for step in section.steps
        )
        return CuratedProtocolFixture(
            draft=draft,
            status="approved_revision",
            ordered_step_labels=labels,
            fixture_sha256=analysis.payload_sha256,
            revision_id=entry.revision_id,
            development_only=entry.approval_status == "development_only",
            source_pdf_path=source,
            source_pdf_sha256=revision.pdf_checksum,
            source_filename=revision.original_filename,
        )

    def resolve_asset(
        self,
        protocol_id: str,
        revision_id: str,
        asset_id: str,
    ) -> ProtocolAssetResolution:
        fixture = self.load_executable_fixture(protocol_id)
        if fixture.revision_id != revision_id:
            raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
        matching = tuple(
            asset
            for index in range(len(fixture.steps))
            if (asset := fixture.visual_for_step(index)) is not None
            and asset.asset_id == asset_id
        )
        if not matching:
            raise ProtocolCatalogNotFoundError("Protocol visual asset is unknown.")
        if fixture.source_pdf_path is None or fixture.source_pdf_sha256 is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol visual source is unavailable."
            )
        return ProtocolAssetResolution(
            path=fixture.source_pdf_path,
            sha256=fixture.source_pdf_sha256,
            source_page=matching[0].source_page,
        )
