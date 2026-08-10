"""Explicit, source-preserving multi-Protocol catalog service.

The service is deliberately constructed only by an authorized API/session
boundary.  Importing or listing it does not contact an analysis Provider.
All writes use the separate immutable Protocol store, never ProcedureStore.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import CuratedProtocolFixture
from voice_workflow_agent.experiment_protocol_analysis import (
    MAX_SINGLE_PASS_INPUT_BYTES,
    ProtocolAnalysisDraft,
    ProtocolAnalysisInputTooLargeError,
    ProtocolAnalysisModel,
    analyze_protocol_extraction,
    prepare_protocol_analysis_request,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    ProtocolPdfExtraction,
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
    merge_validated_chunk_results,
    plan_protocol_chunks,
)


_SAFE_FILENAME = re.compile(r"^[^/\\\x00]{1,255}\.pdf$", re.IGNORECASE)
_STABLE_PROTOCOL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REVISION_ID = re.compile(r"^pdf-(\d+)(?:-analysis-(\d+))?$")
_APPROVAL_EVENT = "protocol_revision_approved"
_ANALYSIS_FAILED_EVENT = "protocol_analysis_failed"
_CHUNK_PLAN_EVENT = "protocol_chunk_plan_created"
_CHUNK_STARTED_EVENT = "protocol_chunk_analysis_started"
_CHUNK_COMPLETED_EVENT = "protocol_chunk_analysis_completed"
_CHUNK_FAILED_EVENT = "protocol_chunk_analysis_failed"
_MERGE_STARTED_EVENT = "protocol_chunk_merge_started"
_MERGE_CONFLICT_EVENT = "protocol_chunk_merge_conflict"
_REVIEW_REQUIRED_EVENT = "protocol_chunk_review_required"
_RUN_CANCELLED_EVENT = "protocol_chunk_run_cancelled"
_DEVELOPMENT_FIXTURE_EVENT = "development_fixture_materialized"
_CHUNK_RUN_LOCKS = tuple(threading.Lock() for _ in range(64))


class ProtocolCatalogError(RuntimeError):
    code = "protocol_catalog_error"


class ProtocolRegistrationError(ProtocolCatalogError):
    code = "protocol_registration_invalid"


class ProtocolCatalogNotFoundError(ProtocolCatalogError):
    code = "protocol_catalog_not_found"


class ProtocolCatalogUnavailableError(ProtocolCatalogError):
    code = "protocol_catalog_unavailable"


class ProtocolApprovalError(ProtocolCatalogError):
    code = "protocol_approval_denied"


class ProtocolOcrRequiredError(ProtocolCatalogError):
    code = "ocr_required"


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
    merge_status: str | None = None
    restart_behavior: str = "explicit_analysis_request_only"

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
            "merge_status": self.merge_status,
            "restart_behavior": self.restart_behavior,
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


def _analysis_state(extraction: ProtocolPdfExtraction) -> str:
    extracted_chars = sum(len(page.text.strip()) for page in extraction.pages)
    if extraction.non_empty_page_count == 0 or extracted_chars < 32:
        return "ocr_required"
    try:
        prepare_protocol_analysis_request(
            extraction, max_input_bytes=MAX_SINGLE_PASS_INPUT_BYTES
        )
    except ProtocolAnalysisInputTooLargeError:
        return "chunked_analysis_required"
    return "structured_analysis_ready"


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
) -> tuple[ProtocolAnalysisDraft | None, int, str | None]:
    attempts = 0
    while attempts <= max_retries:
        attempts += 1
        try:
            return analyze_protocol_chunk(extraction, chunk, model), attempts, None
        except Exception as exc:
            if attempts > max_retries:
                return None, attempts, _safe_failure_code(exc)
    return None, attempts, "chunk_analysis_failed"


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

    def _is_approved(
        self,
        revision: ProtocolRevisionRecord,
        analysis: AnalysisRevisionRecord | None,
    ) -> bool:
        if analysis is None:
            return False
        return any(
            event.event_type == _APPROVAL_EVENT
            and event.protocol_revision_number == revision.revision_number
            and event.analysis_revision_number
            == analysis.analysis_revision_number
            and isinstance(event.payload, dict)
            and event.payload.get("decision") == "approved"
            for event in self.store.list_events(revision.experiment_id)
        )

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
            return ProtocolAnalysisRunStatus(
                protocol_id=protocol_id,
                candidate_revision_id=candidate_revision_id,
                analysis_run_id=None,
                state=entry.analysis_status,
                total_chunks=0,
                completed_chunks=0,
                failed_chunks=0,
                pending_chunks=0,
                chunks=(),
            )
        plan_event = next(
            event for event in events if event.event_type == _CHUNK_PLAN_EVENT
        )
        plan_payload = plan_event.payload
        raw_chunks = plan_payload.get("chunks", [])
        chunks: list[dict[str, object]] = []
        statuses: dict[str, tuple[str, str | None]] = {}
        state = "chunk_planned"
        failure_code = None
        merge_status = None
        for event in events:
            payload = event.payload
            if event.event_type == _CHUNK_STARTED_EVENT:
                state = "chunk_analysis_in_progress"
                statuses[str(payload.get("chunk_id"))] = ("in_progress", None)
            elif event.event_type == _CHUNK_COMPLETED_EVENT:
                statuses[str(payload.get("chunk_id"))] = ("completed", None)
            elif event.event_type == _CHUNK_FAILED_EVENT:
                code = payload.get("failure_code")
                failure_code = code if isinstance(code, str) else "chunk_analysis_failed"
                statuses[str(payload.get("chunk_id"))] = (
                    "failed",
                    failure_code,
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
        for raw in raw_chunks if isinstance(raw_chunks, list) else []:
            if not isinstance(raw, dict) or not isinstance(raw.get("chunk_id"), str):
                continue
            chunk_id = raw["chunk_id"]
            status, code = statuses.get(chunk_id, ("pending", None))
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "ordinal": raw.get("ordinal"),
                    "source_page_start": raw.get("source_page_start"),
                    "source_page_end": raw.get("source_page_end"),
                    "source_page_refs": raw.get("source_page_refs", []),
                    "status": status,
                    "failure_code": code,
                }
            )
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
            merge_status=merge_status,
            restart_behavior=(
                "terminal_review_required"
                if state == "review_required"
                else "explicit_new_run_required_after_interruption"
                if state in {"chunk_planned", "chunk_analysis_in_progress", "merge_in_progress"}
                else "explicit_analysis_request_only"
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
        if analysis is None:
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
        approved = self._is_approved(revision, analysis)
        if approved:
            analysis_status = "approved"
        readiness = (
            analysis.readiness_status if analysis else "analysis_required"
        )
        available = bool(
            approved
            and analysis is not None
            and readiness == domain.ReadinessStatus.GUIDANCE_READY.value
        )
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
            approval_status="approved" if approved else "unapproved",
            analysis_status=analysis_status,
            step_count=(
                sum(len(section.steps) for section in analysis.protocol.sections)
                if analysis
                else 0
            ),
            created_at=revision.created_at,
            available_for_execution=available,
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
            if sum(
                len(page.text.encode("utf-8")) for page in extraction.pages
            ) <= limits.max_chunk_text_bytes:
                raise ProtocolChunkedAnalysisRequiredError(
                    "Protocol does not require the bounded chunk path."
                ) from exc
            raise ProtocolChunkAnalysisFailedError(
                "Protocol exceeds a bounded chunk-analysis admission limit."
            ) from exc
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
                done, pending = wait(
                    futures,
                    timeout=limits.timeout_seconds,
                )
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
                    draft, attempts, failure_code = future.result()
                    if draft is None:
                        self._append_chunk_event(
                            revision,
                            plan,
                            _CHUNK_FAILED_EVENT,
                            {
                                "chunk_id": chunk.chunk_id,
                                "ordinal": chunk.ordinal,
                                "status": "failed",
                                "attempts": attempts,
                                "failure_code": failure_code
                                or "chunk_analysis_failed",
                            },
                            chunk.chunk_id,
                            "failed",
                        )
                        failed = True
                        continue
                    result = ValidatedChunkResult(chunk, draft, attempts)
                    payload_json, payload_sha256 = serialize_analysis(
                        draft.protocol,
                        draft.readiness,
                        draft.capability_policy_id,
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
                            "analysis_payload_sha256": payload_sha256,
                            # This is a strictly decoded, evidence-validated
                            # internal recovery record. Status APIs omit it.
                            "analysis_payload_json": payload_json,
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
            merged = merge_validated_chunk_results(extraction, plan, results)
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
        status = _analysis_state(extraction)
        if status == "ocr_required":
            raise ProtocolOcrRequiredError(
                "Protocol requires OCR before structured analysis."
            )
        if status == "chunked_analysis_required":
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
            self.store.append_event(
                f"analysis-failed-{failure_digest}",
                protocol_id,
                revision.revision_number,
                _ANALYSIS_FAILED_EVENT,
                {"status": "failed", "failure_code": failure_code},
            )
            raise
        if draft.protocol.protocol_id != protocol_id:
            assigned_protocol = replace(draft.protocol, protocol_id=protocol_id)
            domain.validate_protocol(assigned_protocol)
            draft = replace(draft, protocol=assigned_protocol)
        self.store.append_analysis_revision(
            protocol_id,
            revision.revision_number,
            analysis_id,
            draft.protocol,
            draft.readiness,
            draft.capability_policy_id,
        )
        return self.get_entry(protocol_id)

    def approve(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        policy: ApprovalPolicy,
        presented_secret: str | None,
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
            raise ProtocolApprovalError(
                "Protocol analysis is not ready for execution approval."
            )
        self.store.append_event(
            (
                f"approved-{protocol_id[-16:]}-{protocol_revision_number}-"
                f"{analysis_revision_number}"
            ),
            protocol_id,
            protocol_revision_number,
            _APPROVAL_EVENT,
            {"decision": "approved", "authority": "service_policy"},
            analysis_revision_number=analysis_revision_number,
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
            development_only=False,
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
