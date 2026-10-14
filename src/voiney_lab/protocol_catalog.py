"""Explicit, source-preserving multi-Protocol catalog service.

The service is deliberately constructed only by an authorized API/session
boundary.  Importing or listing it does not contact an analysis Provider.
All writes use the separate immutable Protocol store, never ProcedureStore.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import secrets
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolFixture
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_PIECE_CONCURRENCY,
    MAX_PIECED_ANALYSIS_PAGES,
    MAX_SINGLE_PASS_INPUT_BYTES,
    AnalysisPiece,
    OpenAICompatibleProtocolAnalysisModel,
    ProtocolAnalysisDraft,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisIncompleteError,
    ProtocolAnalysisInputTooLargeError,
    ProtocolAnalysisModel,
    ProtocolAnalysisPieceConflictError,
    ProtocolAnalysisPieceFailedError,
    ProtocolAnalysisTooManyPagesError,
    REVIEW_ITEM_KINDS,
    REVIEW_REASON_PIECE_FAILED,
    REVIEW_REASONS_KO,
    ReviewedChange,
    ReviewFlag,
    ReviewVersionInfo,
    analysis_pieces,
    analyze_protocol_extraction,
    analyze_protocol_piece,
    check_analysis_page_count,
    check_pieced_page_count,
    join_piece_analyses,
    prepare_protocol_analysis_request,
    replace_at_paths,
    review_item_id,
    verify_step_timers,
)
from voiney_lab.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    ProtocolPdfExtraction,
    ProtocolPdfPage,
    SourceConversion,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import (
    AnalysisRevisionRecord,
    ProtocolRevisionRecord,
    ProtocolStore,
    serialize_analysis,
)
# The store's own canonical encoding of domain records: a review-pending
# analysis (lane RV, decision a) is kept in a ledger event, because its
# refused quotes keep it out of the analysis table the domain validation
# guards.
from voiney_lab.experiment_protocol_store import (  # noqa: E402
    _decode_domain as _decode_stored_record,
    _encode_domain as _encode_stored_record,
)
from voiney_lab.protocol_ocr_providers import TEXT_LAYER as OCR_TEXT_LAYER_PROVIDER
from voiney_lab.protocol_ocr import (
    OcrResult,
    ProtocolOcrProvider,
    ocr_result_payload,
    validate_ocr_result,
)


_SAFE_FILENAME = re.compile(r"^[^/\\\x00]{1,255}\.pdf$", re.IGNORECASE)
_STABLE_PROTOCOL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REVISION_ID = re.compile(r"^pdf-(\d+)(?:-analysis-(\d+)|-review-(\d+))?$")


_ANALYSIS_REQUESTED_EVENT = "protocol_analysis_requested"
_ANALYSIS_STARTED_EVENT = "protocol_analysis_started"
_ANALYSIS_READY_EVENT = "protocol_analysis_ready"
_ANALYSIS_FAILED_EVENT = "protocol_analysis_failed"
_ANALYSIS_RETRY_EVENT = "protocol_analysis_retry_started"
#: Lane AN, decision 1 (2026-10-07), changing lane PA's "no automatic
#: retry": an analysis whose response broke the structure the domain
#: validation demands -- and only that failure, once per analysis request
#: (since lane AN's EV4 step, 2026-10-13, a person's "분석 다시 시도" too) --
#: is sent once more, the same request, by itself. A failed source-evidence
#: check, a time-out and a missing provider are not: sending them again costs
#: a call and settles nothing a person need not see.
#: Measured 2026-10-06 (lane PX): ANKOM ended so once and passed on other runs.
AUTOMATIC_RETRY_FAILURE_CODES = frozenset({"protocol_analysis_invalid_response"})
AUTOMATIC_RETRY_LIMIT = 1
AUTOMATIC_RETRY_AUTHORITY = "automatic_invalid_response_retry"
#: Lane AQ, decision 4 (2026-10-10): the reasoning level the one automatic
#: retry asks when the first answer was cut at the provider's output limit --
#: one level below the first request, the lowest staying where it is. DS-2
#: (2026-10-09) measured 51 of 195 Gemini 3.8 Flash "high" analysis calls
#: cut at 65,536 output tokens, a median 61k of them thinking, and every
#: same-setting retry cut again; the live server lost 12m47s on PMC8250384
#: that way on 2026-10-10 02:01. An answer that broke the structure for any
#: other reason is still sent again unchanged (lane AN).
TRUNCATED_RETRY_REASONING: dict[str, str] = {
    "xhigh": "high", "high": "medium", "medium": "low", "low": "low",
}
#: The OpenAI-style finish reason of an answer cut at the output limit. xAI
#: and OpenAI chat completions say so themselves; the Gemini adapter reports
#: MAX_TOKENS as this (model_providers). Any other or missing reason keeps
#: the retry as it was.
TRUNCATED_FINISH_REASON = "length"
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
#: An analysis that ended with items a person must check (lane RV, decision
#: a, human decision 2026-10-12): kept in this event, never as an analysis
#: revision, so nothing that runs can read it.
_REVIEW_PENDING_EVENT = "protocol_analysis_review_pending"
#: A person's review registered as a version that runs (lane RV, decision c):
#: the version's protocol is one more analysis revision, named
#: ``review-v<N>-...``, and this event beside it holds who registered it,
#: what it was reviewed from and every change with its original value.
_REVIEW_VERSION_EVENT = "protocol_review_version_registered"
REVIEW_VERSION_ANALYSIS_PREFIX = "review-v"
#: The authority an upload-time OCR acceptance is recorded under (lane PX,
#: decision 3). A person's review carries ``human_review``.
AUTOMATIC_OCR_AUTHORITY = "automatic_upload_ocr"
_OCR_REQUESTED_EVENT = "protocol_ocr_requested"
_OCR_COMPLETED_EVENT = "protocol_ocr_completed"
_OCR_FAILED_EVENT = "protocol_ocr_failed"
_OCR_REVIEWED_EVENT = "protocol_ocr_reviewed"


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


class ProtocolOcrRequiredError(ProtocolCatalogError):
    code = "ocr_required"


class ProtocolReanalysisRefusedError(ProtocolCatalogError):
    """The curated development fixture is served as curated, never analysed again."""

    code = "reanalysis_not_allowed"


class ProtocolOcrReviewError(ProtocolCatalogError):
    """An OCR result that does not hold against the source (evidence, not a person's review)."""

    code = "protocol_ocr_review_invalid"


class ProtocolChunkedAnalysisRequiredError(ProtocolCatalogError):
    code = "chunked_analysis_required"


class _RepairedAnswerWithItems(ProtocolAnalysisEvidenceError):
    """A response read after its brackets were repaired, with items the source
    does not hold (lane RV, decision a, keeping lane EV3's decision 1).

    "고쳐도 안 되면 지금처럼 자동 재시도한다": such an answer is recorded as the
    evidence failure it used to be and sent again once; should the retry not
    come back, the answer is kept for a person's review.
    """

    def __init__(self, draft: ProtocolAnalysisDraft) -> None:
        super().__init__("A repaired response holds items the source does not.")
        self.draft = draft
        self.response_repair = draft.response_repair


@dataclass(frozen=True)
class ProtocolCatalogEntry:
    protocol_id: str
    title: str
    source_filename: str
    source_sha256: str
    revision_id: str
    readiness_status: str
    analysis_status: str
    step_count: int
    created_at: str
    available_for_execution: bool
    lifecycle_state: str = "uploaded"
    #: The readiness reasons that keep this revision out of execution under
    #: the MVP rule (decision of 2026-10-08): nothing runnable came out of the
    #: analysis, or a page could not be read. Empty when it may run.
    execution_blocker_codes: tuple[str, ...] = ()
    #: The curated development fixture, as against an uploaded PDF's own
    #: analysis. Said on screen and in the report; it grants nothing.
    development_only: bool = False
    #: What marks the stored analysis as older than the analyser (lane RN,
    #: decision 3): ``repeat_count_kind_missing`` when a fixed repeat was
    #: stored before lane EV2 asked how the source states its count. The
    #: Korean line is advice ("다시 분석 권장"); the analysis still runs.
    analysis_outdated_codes: tuple[str, ...] = ()
    analysis_outdated_ko: str | None = None
    #: The latest analysis that ended with items for a person (lane RV,
    #: decision a), when it is newer than any analysis that passed: how many
    #: items, why, and which pages of a long document failed. None otherwise.
    review_pending: dict[str, object] | None = None
    #: The registered review versions, v1 first (lane RV, decisions c and
    #: d): number, revision id, reviewer, date and how many changes. The
    #: latest is the one that runs.
    review_versions: tuple[dict[str, object], ...] = ()

    def public_dict(self) -> dict[str, object]:
        return {
            "protocol_id": self.protocol_id,
            "title": self.title,
            "source_filename": self.source_filename,
            "source_sha256": self.source_sha256,
            "revision_id": self.revision_id,
            "readiness_status": self.readiness_status,
            "analysis_status": self.analysis_status,
            "step_count": self.step_count,
            "created_at": self.created_at,
            "available_for_execution": self.available_for_execution,
            "execution_blocker_codes": list(self.execution_blocker_codes),
            "development_only": self.development_only,
            "lifecycle_state": self.lifecycle_state,
            "analysis_outdated_codes": list(self.analysis_outdated_codes),
            "analysis_outdated_ko": self.analysis_outdated_ko,
            "review_pending": self.review_pending,
            "review_versions": list(self.review_versions),
            "current_review_version": (
                self.review_versions[-1] if self.review_versions else None),
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
    #: When the run on screen was requested (the ledger's own time, UTC), so
    #: the screen can say how long the analysis has taken. None before any
    #: request.
    requested_at: str | None = None
    #: The run's automatic retry (lane AN, decision 1): attempt, limit, state
    #: (``in_progress``, ``passed``, ``failed``) and the failure it retried.
    #: None when the run on screen had none.
    automatic_retry: dict[str, object] | None = None
    #: Whether the run on screen was asked for over a passed analysis (lane
    #: RN, decision 2), and whether the revision may run right now -- on the
    #: analysis it has, whatever this run does.
    reanalysis: bool = False
    available_for_execution: bool = False

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
            "requested_at": self.requested_at,
            "automatic_retry": self.automatic_retry,
            "reanalysis": self.reanalysis,
            "available_for_execution": self.available_for_execution,
        }


def _failures_not_superseded(events: Sequence[Any]) -> tuple[Any, ...]:
    """The events without the analysis failures a later attempt replaced.

    The failure an automatic retry was sent for is not the run's result
    (lane AN, decision 1): while the retry runs the run is analysing, and
    when it passes nothing failed. Nor is a failure an analysis that passed
    afterwards replaced -- a person's "분석 다시 시도" that passed used to
    read as failed, because the run status still carried the earlier code.
    A failure stays in the ledger either way.
    """

    last_superseding = max(
        (index for index, event in enumerate(events)
         if event.event_type in {
             _ANALYSIS_RETRY_EVENT, _ANALYSIS_READY_EVENT, _SINGLE_REVIEW_REQUIRED_EVENT,
             _REVIEW_PENDING_EVENT,
         }),
        default=-1,
    )
    return tuple(
        event for index, event in enumerate(events)
        if not (
            index < last_superseding
            and event.event_type in {_ANALYSIS_FAILED_EVENT, _CHUNK_FAILED_EVENT}
        )
    )


#: The run state a piece run (lane AN, decision 2) ended in, by its failure
#: code; any other failure is "analysis_failed".
_PIECE_RUN_FAILED_STATES = {
    "protocol_analysis_piece_failed": "chunk_analysis_failed",
    "protocol_analysis_piece_conflict": "merge_conflict",
}


def _piece_run_end(
    run_events: Sequence[Any], revision_events: Sequence[Any],
) -> tuple[str, str | None] | None:
    """How a piece run ended, from the lifecycle events after its plan.

    (state, failure code) once the analysis passed or failed after the
    plan; None while the run is still going. A piece run records its pieces
    as chunk events and its end as the analysis' own ready or failed event.
    """

    if not run_events:
        return None
    planned_at = run_events[0].sequence_id
    after = [
        event for event in revision_events
        if event.event_type in {
            _ANALYSIS_FAILED_EVENT, _ANALYSIS_READY_EVENT, _SINGLE_REVIEW_REQUIRED_EVENT,
            _REVIEW_PENDING_EVENT,
        }
        and event.sequence_id > planned_at
    ]
    if not after:
        return None
    last = after[-1]
    if last.event_type == _REVIEW_PENDING_EVENT:
        # Lane RV, decision a: the passed pieces kept, the rest for a person.
        return "review_pending", None
    if last.event_type == _ANALYSIS_FAILED_EVENT:
        payload = last.payload if isinstance(last.payload, dict) else {}
        code = payload.get("failure_code")
        code = code if isinstance(code, str) and code else "analysis_failed"
        return _PIECE_RUN_FAILED_STATES.get(code, "analysis_failed"), code
    return "review_required", None


def _review_pending_summary(event: Any) -> dict[str, object]:
    """What the screen says of a review-pending analysis (lane RV, decision a).

    ``needs_review_count`` items, by reason, with one Korean line: "검토 대기 ·
    확인 필요 12건(근거를 못 찾음 9 · OCR 흐림 3)", and "41–59쪽 분석 실패" for
    each failed piece of a long document.
    """

    payload = event.payload if isinstance(event.payload, dict) else {}
    flags = [
        ReviewFlag.from_public(item) for item in payload.get("flags") or ()
        if isinstance(item, dict)
    ]
    items = list(dict.fromkeys(flag.item_id for flag in flags))
    by_reason: dict[str, int] = {}
    for item in items:
        reason = next(flag.reason for flag in flags if flag.item_id == item)
        by_reason[reason] = by_reason.get(reason, 0) + 1
    failed_pages = [
        {"first_page": flag.first_page, "last_page": flag.last_page,
         "failure_code": flag.reason_code}
        for flag in flags if flag.first_page is not None
    ]
    words = " · ".join(
        f"{REVIEW_REASONS_KO.get(reason, reason)} {count}"
        for reason, count in by_reason.items()
        if reason != REVIEW_REASON_PIECE_FAILED
    )
    line = f"검토 대기 · 확인 필요 {len(items)}건"
    if words:
        line += f"({words})"
    for pages in failed_pages:
        line += f" · {pages['first_page']}–{pages['last_page']}쪽 분석 실패"
    return {
        "analysis_id": payload.get("analysis_id"),
        "recorded_at": event.recorded_at,
        "needs_review_count": len(items),
        "by_reason": by_reason,
        "failed_pages": failed_pages,
        "message_ko": line,
    }


def _korean_date(timestamp: str) -> str:
    """The date of a ledger time in Korea ("2026-10-14")."""

    try:
        moment = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return str(timestamp)[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()


def _review_version_summary(event: Any) -> dict[str, object]:
    """One registered review version as the list shows it (lane RV, c, d)."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    changes = [item for item in payload.get("changes") or () if isinstance(item, dict)]
    return {
        "version": payload.get("version"),
        "version_ko": f"검토본 v{payload.get('version')}",
        "revision_id": payload.get("revision_id"),
        "reviewer_name": payload.get("reviewer_name"),
        "registered_at": event.recorded_at,
        "registered_on": _korean_date(event.recorded_at),
        "base": payload.get("base"),
        "base_revision_id": payload.get("base_revision_id"),
        "change_count": sum(
            1 for item in changes if item.get("action") in {"fix", "enter", "clear"}),
        "confirmed_count": sum(1 for item in changes if item.get("action") == "confirm"),
    }


def _automatic_retry_status(events: Sequence[Any]) -> dict[str, object] | None:
    """The automatic retry of the run on screen, or None.

    A retry belongs to the run a person's request opened; a later request
    (a person pressing "분석 다시 시도") is a new run without one.
    """

    retry_index = max(
        (index for index, event in enumerate(events)
         if event.event_type == _ANALYSIS_RETRY_EVENT),
        default=-1,
    )
    if retry_index < 0 or any(
        event.event_type == _ANALYSIS_REQUESTED_EVENT
        for event in events[retry_index + 1:]
    ):
        return None
    retry = events[retry_index]
    payload = retry.payload if isinstance(retry.payload, dict) else {}
    state = "in_progress"
    for event in events[retry_index + 1:]:
        if event.event_type == _ANALYSIS_FAILED_EVENT:
            state = "failed"
        elif event.event_type in {_ANALYSIS_READY_EVENT, _SINGLE_REVIEW_REQUIRED_EVENT}:
            state = "passed"
        elif event.event_type == _REVIEW_PENDING_EVENT:
            state = "review_pending"
    status: dict[str, object] = {
        "attempt": payload.get("attempt", 1),
        "limit": payload.get("limit", AUTOMATIC_RETRY_LIMIT),
        "state": state,
        "reason_code": payload.get("reason_code"),
    }
    # A retry sent for an answer cut at the output limit says so, and what
    # reasoning level it asked (lane AQ, decision 4); any other reads as
    # lane AN wrote it.
    for key in ("truncated", "reasoning_effort", "previous_reasoning_effort"):
        if key in payload:
            status[key] = payload[key]
    return status


def _truncated_retry_ko(retry: dict[str, object]) -> str:
    """How the pipeline line says a retry was sent for a cut answer."""

    level = retry.get("reasoning_effort")
    previous = retry.get("previous_reasoning_effort")
    if level and previous and level != previous:
        return (f"첫 응답이 출력 한도에서 잘려 추론 수준을 한 단계 낮춰({previous}→{level}) "
                "같은 요청을")
    return "첫 응답이 출력 한도에서 잘렸고 추론 수준을 더 낮출 수 없어 같은 요청을 같은 설정으로"


def _response_repair_of(payload: object) -> dict[str, object] | None:
    """The structural repair an analysis attempt's event records (lane EV3,
    decision 1), or None for a response read as it came."""

    repair = payload.get("response_repaired") if isinstance(payload, dict) else None
    if not isinstance(repair, dict) or not isinstance(repair.get("bracket_count"), int):
        return None
    return repair


def _response_repair_ko(repair: dict[str, object]) -> str:
    """What the pipeline line says about a response read in a repaired shape."""

    return f"응답 형식을 고쳐 읽음(괄호 {repair['bracket_count']}개)"


class _FinishReasonRecorder:
    """An OpenAI-style client that remembers the last reply's finish reason.

    Wrapped around the analysis model's client for one analysis so the
    catalog can tell an answer cut at the output limit (finish reason
    "length") from one that broke the structure another way (lane AQ,
    decision 4). The request and the reply pass through unchanged; nothing
    here judges the reply.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.finish_reason: str | None = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request: Any) -> Any:
        self.finish_reason = None
        response = self._inner.chat.completions.create(**request)
        choices = getattr(response, "choices", None) or ()
        if choices:
            reason = getattr(choices[0], "finish_reason", None)
            self.finish_reason = reason if isinstance(reason, str) else None
        return response


@dataclass(frozen=True)
class _ObservedAnalysisModel:
    """The analysis model with its provider client observed.

    Only the server's adapter (``OpenAICompatibleProtocolAnalysisModel``)
    has a client to observe; any other ``ProtocolAnalysisModel`` is passed
    through untouched and its retry is the same object, as before.
    """

    model: ProtocolAnalysisModel
    recorder: _FinishReasonRecorder | None

    @classmethod
    def wrap(cls, model: ProtocolAnalysisModel) -> "_ObservedAnalysisModel":
        if not isinstance(model, OpenAICompatibleProtocolAnalysisModel):
            return cls(model, None)
        recorder = _FinishReasonRecorder(model.client)
        return cls(replace(model, client=recorder), recorder)

    @property
    def truncated(self) -> bool:
        """Whether the last answer was cut at the provider's output limit."""

        return (
            self.recorder is not None
            and self.recorder.finish_reason == TRUNCATED_FINISH_REASON
        )

    def retry(self) -> tuple[ProtocolAnalysisModel, dict[str, object]]:
        """The model the automatic retry calls, and what its event records.

        Unchanged unless the last answer was cut; then the same model one
        reasoning level lower (``TRUNCATED_RETRY_REASONING``), or the same
        model when its level is not known or already the lowest.
        """

        if not self.truncated:
            return self.model, {}
        current = getattr(self.model, "reasoning_effort", None)
        lowered = TRUNCATED_RETRY_REASONING.get(current) if isinstance(current, str) else None
        if lowered is None:
            return self.model, {"truncated": True}
        model = self.model if lowered == current else replace(self.model, reasoning_effort=lowered)
        return model, {
            "truncated": True,
            "reasoning_effort": lowered,
            "previous_reasoning_effort": current,
        }


#: What a person does after an analysis failure, by failure code (lane PA
#: decision 4). Every code not listed gets the default.
_ANALYSIS_RECOVERY_ACTIONS: dict[str, str] = {
    "provider_configuration_missing": (
        "분석 역할 설정이 없습니다. .env 의 VOINEY_LAB_ANALYSIS_PROVIDER·"
        "VOINEY_LAB_ANALYSIS_MODEL 과 그 공급자의 API 키를 확인하고 서버를 다시 "
        "시작한 뒤 '분석 다시 시도'를 누르세요."
    ),
    "protocol_analysis_timeout": (
        "분석 호출이 제한 시간(VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS, "
        "기본 600초) 안에 끝나지 않았습니다. '분석 다시 시도'를 누르거나, 긴 "
        "문서라면 제한 시간을 늘리세요."
    ),
    "protocol_analysis_invalid_evidence": (
        "분석 모델이 낸 근거가 원문 쪽의 글과 맞지 않아 결과를 쓰지 않았습니다. "
        "'분석 다시 시도'를 누르면 새로 분석합니다(분석 모델 호출 비용이 듭니다). "
        "원문은 바뀌지 않습니다."
    ),
    "protocol_analysis_invalid_response": (
        "분석 모델의 응답이 정해진 형식(JSON 스키마)에 맞지 않아 결과를 쓰지 않았습니다. "
        "'분석 다시 시도'를 누르면 새로 분석합니다(분석 모델 호출 비용이 듭니다). "
        "원문은 바뀌지 않습니다."
    ),
    "ocr_required": (
        "이 PDF 는 읽을 수 있는 글자가 없는 쪽이 있어 OCR 글이 먼저 필요합니다. "
        "업로드 때 OCR 이 자동으로 돌지 않았다면 OCR 공급자 설정"
        "(VOINEY_LAB_OCR_PROVIDERS 와 그 엔진의 키)을 확인하고 서버를 다시 시작한 뒤 "
        "PDF 를 다시 올리거나 'OCR 텍스트 추출'을 누르세요. OCR 이 끝나면 분석이 "
        "바로 시작됩니다."
    ),
    "protocol_pdf_too_large": (
        "PDF 가 등록 한도보다 큽니다. 더 작은 파일로 다시 올리세요."
    ),
    # Lane EV2, decision 4.
    "protocol_analysis_incomplete": (
        "원문에 번호가 붙은 단계가 분석 결과보다 훨씬 많아, 원문 일부만 읽은 분석으로 보고 "
        "쓰지 않았습니다. '분석 다시 시도'를 누르면 새로 분석합니다(분석 모델 호출 비용이 "
        "듭니다). 다시 해도 같으면 절차가 있는 쪽만 골라 PDF 로 만들어 올려 주세요."
    ),
    "protocol_analysis_too_many_pages": (
        f"분석하는 문서는 {MAX_PIECED_ANALYSIS_PAGES}쪽까지입니다. 실험에 쓸 절차가 있는 쪽만 "
        "골라 PDF 로 만들어 다시 올려 주세요(쪽 범위를 고르는 화면은 아직 없습니다). 분석 "
        "모델은 부르지 않았고 원문은 바뀌지 않습니다."
    ),
    # Lane AN, decision 2: a long document read in pieces.
    "protocol_analysis_piece_failed": (
        "실패한 조각은 자동으로 한 번 다시 보냈지만 다시 실패해, 나머지 조각의 결과도 쓰지 "
        "않았습니다(일부만 읽은 분석은 쓰지 않습니다). '분석 다시 시도'를 누르면 문서 전체를 "
        "새로 분석합니다(조각 수만큼 분석 모델 호출 비용이 듭니다). 원문은 바뀌지 않습니다."
    ),
    "protocol_analysis_piece_conflict": (
        "두 조각이 함께 읽은 쪽을 서로 다르게 읽어 결과를 쓰지 않았습니다. '분석 다시 시도'를 "
        "누르면 문서 전체를 새로 분석합니다(조각 수만큼 분석 모델 호출 비용이 듭니다). 원문은 "
        "바뀌지 않습니다."
    ),
}
#: What a person does with an analysis waiting for review (lane RV, a).
REVIEW_PENDING_ACTION_KO = (
    "검토 화면에서 확인 필요 항목을 확인·고침·비움 중 하나로 정하고, 검토자 이름을 적어 "
    "'검토 완료하고 등록'을 누르세요. 원문이 바뀌었다면 '다시 분석'을 누르세요."
)
_DEFAULT_ANALYSIS_RECOVERY_ACTION = (
    "실패 원인을 확인한 뒤 '분석 다시 시도'를 누르세요. 다시 시도해도 원문은 "
    "바뀌지 않습니다."
)
#: What a person does after a re-analysis over a passed analysis failed
#: (lane RN, decision 2): nothing is lost, the pass before it still runs.
_REANALYSIS_RECOVERY_ACTION = (
    "'다시 분석'을 눌러 다시 할 수 있습니다. 원문과 지금 분석은 바뀌지 않습니다."
)
_NOT_RETRYABLE_FAILURE_CODES = frozenset({
    "ocr_required", "protocol_pdf_too_large", "protocol_analysis_too_many_pages",
})

#: One Korean line per readiness reason: what blocks execution and what a
#: person does about it (lane PX, decision 4 of 2026-10-06). The English
#: ``ReadinessReason.message`` the domain records is unchanged; the review
#: carries this beside it as ``message_ko``. ``{pages}`` is filled from the
#: recorded message by ``readiness_reason_korean``.
READINESS_REASON_KO: dict[str, str] = {
    "invalid_protocol": "구조화된 프로토콜이 올바르지 않습니다. '분석 다시 시도'를 누르세요.",
    "source_text_cross_check_failed": (
        "옛 추출 대조 판정입니다(2026-10-02 뒤로는 만들어지지 않음). 분석을 다시 돌리면 "
        "사라집니다."),
    "source_text_cross_check_unavailable": (
        "옛 추출 대조 판정입니다(2026-10-02 뒤로는 만들어지지 않음). 분석을 다시 돌리면 "
        "사라집니다."),
    "no_executable_steps": (
        "실행할 단계를 원문에서 찾지 못했습니다. 원문을 확인하고 '분석 다시 시도'를 누르세요."),
    "unsupported_conditional_branch": (
        "조건·선택 분기(예: '새로 만든 튜브면 36~41단계를 두 번 더 반복')가 있습니다. "
        "그 단계에 이르면 조건에 해당하는지 묻고 답대로 안내합니다."),
    "unsupported_fixed_range_repetition": (
        "정해진 범위의 단계 반복이 있습니다. 범위의 끝에서 회차를 세어 안내합니다."),
    "unsupported_operator_determined_repetition": (
        "작업자가 횟수를 정하는 반복이 있습니다. 범위의 첫 단계에서 몇 번 하실지 묻습니다."),
    "unsupported_repeat_until": (
        "조건이 될 때까지 반복하는 지시가 있습니다. 그 단계에서 관찰 결과를 묻습니다."),
    "unsupported_parallel_background_work": (
        "동시에 또는 백그라운드로 진행하는 작업은 아직 안내 기능이 없습니다. 그 단계에 이르면 "
        "한 번 알리고 원문을 읽어 드립니다."),
    "unsupported_recurring_reminder": (
        "반복 알림은 아직 안내 기능이 없습니다. 그 단계에 이르면 한 번 알리고 원문을 읽어 "
        "드립니다."),
    "unsupported_recurring_action": (
        "일정 간격으로 되풀이하는 동작은 아직 안내 기능이 없습니다. 그 단계에 이르면 한 번 "
        "알리고 원문을 읽어 드립니다."),
    "unsupported_reusable_subprocedure": (
        "다른 곳에서 다시 쓰는 하위 절차는 아직 안내 기능이 없습니다. 그 단계에 이르면 한 번 "
        "알리고 원문을 읽어 드립니다."),
    "unresolved_ambiguity": (
        "원문에 서로 다른 두 서술이 있어 어느 쪽이 맞는지 정해지지 않았습니다. 그 단계에서는 "
        "원문을 그대로 읽어 드리니 실험자가 원문을 보고 판단하세요."),
    "unresolved_execution_value_conflict": (
        "실행에 쓰는 값이 원문 안에서 서로 다릅니다. 그 단계에서 원문을 그대로 읽어 드리니 "
        "실험자가 어느 값을 쓸지 정하세요."),
    "safety_critical_conflict": (
        "안전에 중요한 값이 원문 안에서 서로 달라 실행할 수 없습니다. 원문을 확인하고 고친 "
        "PDF 를 올리세요."),
    "no_declared_safety_warnings": (
        "원문의 안전 주의는 시작 전 화면에 모아 보이고, 각 단계에서 원문 그대로 읽어 드립니다. "
        "시작을 누르면 안전 주의를 확인한 것으로 기록합니다."),
    "source_page_requires_ocr": (
        "원문 {pages}쪽은 글자 층이 없어 OCR 글이 필요합니다. 업로드 때 OCR 이 돌고 나면 "
        "다음 분석부터 이 사유는 사라집니다."),
    "source_page_not_fully_read": (
        "분석이 원문 {pages}쪽에 적힌 값을 다 다루지 못했습니다. 그 쪽의 단계에 이르면 쪽 "
        "원문을 그대로 보여 드리니 직접 확인하세요."),
    "declined_value_not_resolved": (
        "분석이 지시가 아니라고 본 원문 값이 있습니다. 원문 단계를 그대로 읽어 드리니 "
        "실험자가 확인하세요."),
    "excessive_declined_values": (
        "지시가 아니라고 본 원문 값이 너무 많습니다. 쪽을 제대로 읽지 않았을 수 있으니 "
        "원문을 함께 보며 진행하세요."),
    "source_states_an_uncaptured_repetition": (
        "원문에 적힌 반복 지시를 분석이 놓쳤습니다. 그 단계의 원문을 그대로 읽어 드리니 "
        "실험자가 반복을 확인하세요."),
    "unconfirmed_fixed_repetition": (
        "정해진 반복 횟수는 분석이 원문에서 읽은 값입니다. 범위의 끝에서 그 횟수로 회차를 "
        "세어 안내합니다."),
    "missing_execution_critical_value": (
        "실행에 꼭 필요한 값이 원문에 없습니다. 그 단계에서 원문을 그대로 읽어 드리니 "
        "실험자가 값을 정하세요."),
}
#: What a person does about each execution blocker, for the pipeline line.
_BLOCKER_ACTIONS: dict[str, str] = {
    "no_executable_steps": "원문을 확인하고 '분석 다시 시도'를 누르세요.",
    "invalid_protocol": "'분석 다시 시도'를 누르세요.",
    "source_page_requires_ocr": (
        "OCR 공급자가 설정돼 있으면 'OCR 텍스트 추출'을 누르세요. 없으면 글자 층이 있는 "
        "PDF 를 올리세요."),
    "safety_critical_conflict": "원문을 확인하고 고친 PDF 를 올리세요.",
}
_READINESS_LABEL_KO = {
    "guidance_ready": "실행 안내 준비 완료",
    "analysis_required": "시작 전에 읽을 알림이 있음",
}
_ANALYSIS_REQUIRED_LABEL_KO = "구조 분석이 아직 끝나지 않았습니다."
_ANALYSIS_REQUIRED_MESSAGE_KO = "실행에 앞서 구조 분석이 통과해야 합니다. 업로드 직후 자동으로 시작됩니다."
_PAGE_NUMBERS = re.compile(r"(?<![\w.])\d+(?![\w.])")

#: Where a document stands on its way to a start, in Korean (decision 4).
PIPELINE_STAGE_KO: dict[str, str] = {
    "extraction": "원문 읽기",
    "ocr": "OCR",
    "analysis": "분석",
    "evidence": "근거 대조",
    # Lane RV, decision a: an analysis with items a person must check.
    "review": "검토",
    "readiness": "실행 준비",
    "translation": "번역",
    "ready": "실행 가능",
}
_ANALYSIS_FAILURE_KO: dict[str, str] = {
    "provider_configuration_missing": "분석 모델 설정이 없어 분석을 시작하지 못했습니다.",
    "protocol_analysis_timeout": "분석 호출이 제한 시간 안에 끝나지 않았습니다.",
    "protocol_analysis_invalid_evidence": (
        "분석 모델이 낸 근거가 원문 쪽의 글과 맞지 않아 결과를 쓰지 않았습니다."),
    "protocol_analysis_invalid_response": (
        "분석 모델의 응답이 정해진 형식에 맞지 않아 결과를 쓰지 않았습니다."),
    "protocol_analysis_not_configured": "분석 모델 설정이 없어 분석을 시작하지 못했습니다.",
    "ocr_required": "글자 층이 없는 쪽의 OCR 글이 아직 없어 분석할 수 없습니다.",
    "protocol_pdf_too_large": "PDF 가 등록 한도보다 큽니다.",
    "protocol_analysis_incomplete": "분석이 원문 단계를 다 담지 못했어요.",
    "protocol_analysis_too_many_pages": (
        f"원문이 {MAX_PIECED_ANALYSIS_PAGES}쪽을 넘어 분석하지 않았어요. 분석할 쪽 범위를 "
        "줄여야 합니다."),
    "protocol_analysis_piece_failed": "일부 조각 분석 실패",
    "protocol_analysis_piece_conflict": (
        "조각을 합치다 같은 쪽을 두 조각이 서로 다르게 읽은 곳이 있어 결과를 쓰지 않았습니다."),
    "analysis_cancelled": "분석이 취소되었습니다.",
    "chunk_analysis_failed": "큰 문서의 일부 분석이 실패했습니다.",
    "merge_conflict": "큰 문서의 분석 결과를 합치다 충돌이 났습니다.",
}


def _duration_ko(seconds: int) -> str:
    """"12시간", "1시간 30분", "15분", "30초": a timer value said in Korean."""

    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = [
        f"{value}{unit}"
        for value, unit in ((hours, "시간"), (minutes, "분"), (secs, "초"))
        if value
    ]
    return " ".join(parts) or "0초"


def _review_timers(table: domain.StepTimerTable) -> dict[str, list[dict[str, object]]]:
    """The start screen's timer list (lane PT, decision 4).

    ``verified``: each step's timer values with the source excerpt they were
    read from; a step with more than one value is a choice the experimenter
    makes when starting the timer. ``bound`` says whether the source states
    the value as a minimum, an approximate value or a maximum, and
    ``bound_ko`` is the word the screen shows before it -- "최소", "약",
    "최대" (lane VX, decision 4). ``refused``: each time the server did not
    make a timer from, with the reason in Korean.
    """

    choices = table.choices()
    return {
        "verified": [
            {
                "step_id": timer.step_id,
                "step_label": timer.source_label,
                "source_literal": timer.literal,
                "source_excerpt": timer.excerpt,
                "source_page_number": timer.page_number,
                "seconds": list(timer.seconds),
                "value_ko": " 또는 ".join(_duration_ko(s) for s in timer.seconds),
                "choice": timer.step_id in choices,
                "source": timer.source,
                "bound": timer.bound,
                "bound_ko": domain.TIMER_BOUND_KO.get(timer.bound, ""),
            }
            for timer in table.verified
        ],
        "refused": [
            {
                "step_id": item.step_id,
                "step_label": item.source_label,
                "source_literal": item.literal,
                "source_excerpt": item.excerpt,
                "source_page_number": item.page_number,
                "reason": item.reason,
                "reason_ko": item.reason_ko,
            }
            for item in table.refused
        ],
    }


#: Lane RN, decision 3: the one line the list and the start screen show over
#: an analysis stored before the analyser improved.
ANALYSIS_OUTDATED_KO = "분석기 개선 전 분석 · 다시 분석 권장"
ANALYSIS_OUTDATED_REPEAT_KIND = "repeat_count_kind_missing"


def _analysis_outdated_codes(protocol: domain.ExperimentProtocol) -> tuple[str, ...]:
    """What marks a stored analysis as older than the analyser, if anything.

    Lane RN, decision 3: a fixed repeat stored without ``repeat_count_kind``
    was read before lane EV2 asked how the source states its count, so its
    count may be one round short (headspace: "three … in total" stored as
    2). The analysis is still served and still runs; this is the advice to
    analyse it again. An analysis with no fixed repeat, or whose repeats
    carry a kind, cannot be told from a current one and gets nothing.
    """

    if any(
        isinstance(construct, domain.FixedRangeRepetition)
        and construct.repeat_count_kind is None
        for construct in protocol.constructs
    ):
        return (ANALYSIS_OUTDATED_REPEAT_KIND,)
    return ()


def _repeat_count_checks_ko(protocol: domain.ExperimentProtocol) -> list[str]:
    """One line per fixed repeat whose count the experimenter should check.

    Lane EV2, decision 3: a count is the total number of runs. A count
    stored without saying how the source states it (every analysis before
    the decision) is kept as it was and needs checking; a count the source
    states two ways is asked before the start. A count the source states as
    a total, or as runs after the first (made a total by the server), needs
    no line.
    """

    labels = {
        step.step_id: step.source_label
        for section in protocol_with_display_step_labels(protocol).sections
        for step in section.steps
    }
    lines: list[str] = []
    for construct in protocol.constructs:
        if not isinstance(construct, domain.FixedRangeRepetition) or (
            construct.repeat_count_kind
            in {domain.REPEAT_COUNT_TOTAL, domain.REPEAT_COUNT_ADDITIONAL}
        ):
            continue
        where = (
            f"{labels.get(construct.start_step_id, construct.start_step_id)}~"
            f"{labels.get(construct.end_step_id, construct.end_step_id)}단계"
        )
        source = " ".join(construct.range_source_text.split())
        if construct.repeat_count_kind == domain.REPEAT_COUNT_AMBIGUOUS:
            how = (
                "처음을 포함한 총 횟수인지 더 하는 횟수인지 원문이 두 가지로 읽혀 "
                "시작할 때 여쭤봅니다"
            )
        elif construct.repeat_count is not None:
            how = (
                f"분석이 {construct.repeat_count}회로 읽음"
                "(처음을 포함한 총 횟수인지 원문을 확인하세요)"
            )
        else:
            how = "분석이 횟수를 읽지 않음(원문을 확인하세요)"
        lines.append(f"반복 횟수 확인 필요 · {where} · 원문 “{source}” · {how}")
    return lines


def _safety_notice_sources(protocol: Any) -> list[dict[str, object]]:
    """Every safety statement the source declares, step by step, verbatim.

    The screen the experimenter reads before pressing start (decision of
    2026-10-08): the step's warnings and its sub-actions' warnings in source
    order, each with the page it stands on. The words are the document's own
    (``evidence.source_excerpt`` where the server reconstructed one, else the
    statement text); nothing is summarised.
    """

    items: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for section in protocol.sections:
        for step in section.steps:
            statements = list(step.warnings)
            for action in step.sub_actions:
                statements.extend(action.warnings)
            for index, statement in enumerate(statements, 1):
                source = " ".join(
                    str(statement.evidence.source_excerpt
                        or statement.source_text or "").split()
                )
                if not source or (step.step_id, source) in seen:
                    continue
                seen.add((step.step_id, source))
                items.append({
                    "step_id": step.step_id,
                    "step_label": step.source_label,
                    "statement_id": statement.statement_id,
                    "warning_index": index,
                    "source_page_number": statement.evidence.source_page_number,
                    "source_text": source,
                })
    return items


def readiness_reason_korean(code: str, message: str | None = None) -> str | None:
    """The Korean line for one readiness reason, pages filled in."""

    template = READINESS_REASON_KO.get(code)
    if template is None:
        return None
    if "{pages}" not in template:
        return template
    numbers = _PAGE_NUMBERS.findall(message or "")
    if code == "source_page_not_fully_read":
        pages = numbers[0] if numbers else "일부"
    else:
        pages = ", ".join(numbers) if numbers else "일부"
    return template.format(pages=pages)


def _with_korean_reasons(readiness: dict[str, object]) -> dict[str, object]:
    status = str(readiness.get("status") or "")
    readiness["label_ko"] = _READINESS_LABEL_KO.get(status, readiness.get("label"))
    reasons = readiness.get("reasons")
    if isinstance(reasons, list):
        for reason in reasons:
            if isinstance(reason, dict):
                reason["message_ko"] = readiness_reason_korean(
                    str(reason.get("code") or ""), str(reason.get("message") or ""))
    return readiness


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def protocol_with_display_step_labels(
    protocol: domain.ExperimentProtocol,
) -> domain.ExperimentProtocol:
    """The Protocol with its steps numbered in order when the source prints none.

    An empty source_label means the source prints no step numbers (human
    decision 2026-10-05, lane P3). The stored analysis keeps it empty -- that
    is what the source says -- and the screen and the run number the steps
    1, 2, 3 ... in their order instead. A labelled Protocol is returned as is.
    """

    steps = [step for section in protocol.sections for step in section.steps]
    if not steps or any(step.source_label.strip() for step in steps):
        return protocol
    ordinal = iter(range(1, len(steps) + 1))
    return replace(
        protocol,
        sections=tuple(
            replace(
                section,
                steps=tuple(
                    replace(step, source_label=str(next(ordinal)))
                    for step in section.steps
                ),
            )
            for section in protocol.sections
        ),
    )


def _review_sections(protocol: domain.ExperimentProtocol) -> object:
    shown = protocol_with_display_step_labels(protocol)
    sections = _review_value(shown.sections)
    if shown is not protocol:
        for section in sections:
            for step in section["steps"]:
                step["source_label_printed"] = False
    return sections


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


def _ocr_needed(extraction: ProtocolPdfExtraction) -> bool:
    """Whether any page of this source has to come from OCR.

    Wider than ``_analysis_state(...) == "ocr_required"``, which is the whole
    document having too little text to analyse at all. A readable document
    with one page the text layer cannot supply is analysed as it stands, and
    still needs this route for that page: until the page's OCR text has been
    accepted, readiness keeps the Protocol out of execution
    (``source_page_requires_ocr``). The OCR provider reads only the marked
    pages and keeps every other page's text layer.
    """

    return (
        _analysis_state(extraction) == "ocr_required"
        or bool(extraction.ocr_required_page_numbers)
    )


def _analysis_state(extraction: ProtocolPdfExtraction) -> str:
    extracted_chars = sum(len(page.text.strip()) for page in extraction.pages)
    if extraction.non_empty_page_count == 0 or extracted_chars < 32:
        return "ocr_required"
    if analysis_pieces(extraction):
        # Read in pieces (lane AN, decision 2): "대형 문서 분석 요청 대기"
        # until the analysis starts, as the screen says it.
        return "chunked_analysis_required"
    try:
        prepare_protocol_analysis_request(
            extraction, max_input_bytes=MAX_SINGLE_PASS_INPUT_BYTES
        )
    except ProtocolAnalysisInputTooLargeError:
        return "chunked_analysis_required"
    return "structured_analysis_ready"


#: An analysis refused under lane EV2's 60-page bound, of a document the
#: pieces read now (lane AN, decision 2): it may be asked again.
_OLD_PAGE_BOUND_KO = (
    "이 원문은 예전 한도(한 번에 60쪽)에 걸려 분석하지 않았어요. 이제 긴 문서는 조각으로 "
    "나눠 분석합니다."
)
_OLD_PAGE_BOUND_ACTION = (
    "'분석 다시 시도'를 누르면 조각으로 나눠 새로 분석합니다(조각 수만큼 분석 모델 호출 "
    "비용이 듭니다). 원문은 바뀌지 않습니다."
)


def _refused_under_the_old_page_bound(failure_code: str | None, page_count: int) -> bool:
    return (
        failure_code == "protocol_analysis_too_many_pages"
        and page_count <= MAX_PIECED_ANALYSIS_PAGES
    )


def _failed_pieces_ko(events: Sequence[Any]) -> str | None:
    """"41–59쪽, 97–115쪽": the pieces the latest failed analysis names (lane AN, 2)."""

    failed = next(
        (
            event.payload for event in reversed(events)
            if event.event_type == _ANALYSIS_FAILED_EVENT and isinstance(event.payload, dict)
        ),
        None,
    )
    pieces = (failed or {}).get("failed_pieces")
    if not isinstance(pieces, list):
        return None
    pages = [
        f"{piece['first_page']}–{piece['last_page']}쪽" for piece in pieces
        if isinstance(piece, dict) and isinstance(piece.get("first_page"), int)
        and isinstance(piece.get("last_page"), int)
    ]
    return ", ".join(pages) or None


def _piece_failure_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        return code
    return "protocol_analysis_error"


def _safe_evidence_failure(
    exc: BaseException,
    *,
    source_revision: str,
    source_hash: str,
) -> dict[str, object] | None:
    if not isinstance(exc, ProtocolAnalysisEvidenceError):
        return None
    del source_revision, source_hash
    return exc.diagnostic.privacy_safe_dict()


class ProtocolCatalog:
    """Catalog facade over immutable source and analysis records.

    The execution rule (decision of 2026-10-08, lane DI): a revision may run
    when its latest analysis passed -- which includes the source-evidence
    check every analysis goes through -- and none of its readiness reasons
    is an execution blocker (``experiment_protocol.execution_blocking_reasons``:
    nothing runnable, or a page that could not be read). The one human
    confirmation is the experimenter pressing start, recorded by the server
    on the session. There is no approval, no reviewer finding and no
    development activation here any more; the rows those paths wrote stay in
    the ledger and are no longer read.
    """

    def __init__(
        self,
        store: ProtocolStore,
        *,
        on_analysis_ready: (
            Callable[["ProtocolCatalog", str], None] | None
        ) = None,
    ) -> None:
        self.store = store
        #: Told the protocol id as soon as an analysis has passed -- the
        #: moment its sentences start being translated (lane PX, human
        #: decision 1 of 2026-10-06), so the Korean is ready by the time the
        #: experimenter presses start. It adds no authority and its failure
        #: changes nothing the catalog recorded.
        self.on_analysis_ready = on_analysis_ready

    def _analysis_ready(self, entry: ProtocolCatalogEntry) -> None:
        if self.on_analysis_ready is None or entry.analysis_status != "review_required":
            return
        try:
            self.on_analysis_ready(self, entry.protocol_id)
        except Exception as exc:  # noqa: BLE001 - see on_analysis_ready
            logging.getLogger(__name__).warning(
                "protocol.analysis_ready.hook_failed protocol_id=%s error=%s",
                entry.protocol_id, type(exc).__name__,
            )

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
        """The latest analysis the analyser made; a registered review version
        (lane RV, decision c) is a person's, read by ``_review_versions``."""

        analyses = [
            analysis for analysis in self.store.list_analysis_revisions(
                revision.experiment_id, revision.revision_number
            )
            if not analysis.analysis_id.startswith(REVIEW_VERSION_ANALYSIS_PREFIX)
        ]
        return analyses[-1] if analyses else None

    def _review_versions(
        self, revision: ProtocolRevisionRecord,
    ) -> list[tuple[Any, AnalysisRevisionRecord]]:
        """Every registered review version of the revision, v1 first (lane RV, c)."""

        versions = []
        for event in self.store.list_events(revision.experiment_id):
            if (
                event.event_type != _REVIEW_VERSION_EVENT
                or event.protocol_revision_number != revision.revision_number
                or event.analysis_revision_number is None
            ):
                continue
            versions.append((event, self.store.get_analysis_revision(
                revision.experiment_id, revision.revision_number,
                event.analysis_revision_number,
            )))
        return versions

    def _reanalysis_requested(self, revision: ProtocolRevisionRecord) -> bool:
        """Whether the run on screen was asked for over a passed analysis."""

        request = next(
            (
                event for event in reversed(self.store.list_events(revision.experiment_id))
                if event.protocol_revision_number == revision.revision_number
                and event.event_type == _ANALYSIS_REQUESTED_EVENT
            ),
            None,
        )
        return (
            request is not None and isinstance(request.payload, dict)
            and request.payload.get("reanalysis") is True
        )

    @staticmethod
    def _reanalysis_status(
        events: Sequence[Any], analysis: AnalysisRevisionRecord | None,
    ) -> dict[str, object] | None:
        """The run asked for over the latest passed analysis, or None.

        Lane RN, decision 2: the lifecycle events after the pass (or after
        the development fixture's record) are that run's. Its failure is its
        own, never the executable analysis's, so the review says it apart
        from ``analysis_failure``. None while no such run was asked for.
        """

        if analysis is None:
            return None
        last_pass = max(
            (
                index for index, event in enumerate(events)
                if event.event_type in {
                    _ANALYSIS_READY_EVENT, _SINGLE_REVIEW_REQUIRED_EVENT,
                    _DEVELOPMENT_FIXTURE_EVENT,
                }
            ),
            default=-1,
        )
        after = tuple(
            event for event in events[last_pass + 1:]
            if event.event_type in {
                _ANALYSIS_REQUESTED_EVENT, _ANALYSIS_STARTED_EVENT,
                _ANALYSIS_FAILED_EVENT, _ANALYSIS_RETRY_EVENT,
                _REVIEW_PENDING_EVENT,
            }
        )
        if not after:
            return None
        state = {
            _ANALYSIS_REQUESTED_EVENT: "analysis_pending",
            _ANALYSIS_STARTED_EVENT: "analyzing",
            _ANALYSIS_FAILED_EVENT: "analysis_failed",
            _ANALYSIS_RETRY_EVENT: "analyzing",
            # Lane RV, decision a: the run ended with items for a person;
            # the analysis that passed before still runs.
            _REVIEW_PENDING_EVENT: "review_pending",
        }[after[-1].event_type]
        failure: dict[str, object] | None = None
        if state == "analysis_failed":
            payload = after[-1].payload if isinstance(after[-1].payload, dict) else {}
            code = payload.get("failure_code")
            code = code if isinstance(code, str) and code else "analysis_failed"
            detail = payload.get("evidence_failure")
            failure = {
                "code": code,
                "detail": detail if isinstance(detail, dict) else None,
                "retryable": code not in _NOT_RETRYABLE_FAILURE_CODES,
                "action": _REANALYSIS_RECOVERY_ACTION,
            }
            repair = _response_repair_of(payload)
            if repair is not None:
                # Lane EV3, decision 1.
                failure["response_repaired"] = repair
        status: dict[str, object] = {
            "state": state,
            "requested_at": next(
                (event.recorded_at for event in reversed(after)
                 if event.event_type == _ANALYSIS_REQUESTED_EVENT),
                None,
            ),
            "failure": failure,
            "automatic_retry": _automatic_retry_status(after),
        }
        if state == "review_pending":
            status["review_pending"] = _review_pending_summary(after[-1])
        return status

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
        if not _ocr_needed(extraction):
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
        result_provider = projection.get("provider")
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
            # A page the provider kept from the text layer is not OCR text;
            # a provider that names one engine for every page names it on
            # the result only.
            ocr_derived = (
                (page.get("provider") or result_provider) != OCR_TEXT_LAYER_PROVIDER
            )
            original = extraction.pages[expected_number - 1]
            # The text layer's own page keeps its geometry: the footer band
            # and the text blocks the page-boundary rule (lane PA, decision
            # 3) reads. Measured 2026-10-06 (lane PX): with them dropped for
            # every page after an OCR acceptance, ANKOM's step 21 -- "21
            # Flush procedure:" ending page 19, its sentence opening page 20
            # -- was refused, while the same response passed on the raw
            # extraction. OCR text has no geometry, so an OCR page keeps none.
            keeps_text_layer = not ocr_derived and page["text"] == original.text
            reconstructed.append(
                ProtocolPdfPage(
                    source_page_number=expected_number,
                    text=page["text"],
                    text_empty=not page["text"].strip(),
                    warning=(
                        original.warning if keeps_text_layer
                        else "Text was produced by OCR and accepted for structured review."
                    ),
                    bottom_band_offset=(
                        original.bottom_band_offset if keeps_text_layer else None
                    ),
                    blocks=original.blocks if keeps_text_layer else (),
                    ocr_derived=ocr_derived,
                )
            )
        return replace(
            extraction,
            pages=tuple(reconstructed),
            warnings=tuple(
                dict.fromkeys(
                    (
                        *extraction.warnings,
                        "OCR-derived text is read as source text; it is not an analysed protocol.",
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
        accepted_automatically: bool = True,
    ) -> dict[str, object]:
        """Read the pages without a usable text layer with the OCR provider.

        With ``accepted_automatically`` (lane PX, human decision 3 of
        2026-10-06: OCR runs at upload for the pages whose text layer is
        missing or unreadable) the validated result is accepted for analysis
        at once, under the authority ``automatic_upload_ocr`` written into
        the ledger, and the one confirmation a person gives before execution
        (the experimenter pressing start) covers it; there is no separate
        OCR approval step. Each OCR page keeps its provider and its
        numeric-review mark, so the screen still shows which pages came from
        OCR and where the two engines read different numbers.
        """

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
        if not _ocr_needed(extraction):
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
            if accepted_automatically:
                ocr_pages = [
                    page.source_page_number for page in validated.pages
                    if page.provider != OCR_TEXT_LAYER_PROVIDER
                    and (page.provider is not None
                         or validated.provider != OCR_TEXT_LAYER_PROVIDER)
                ]
                self.store.append_event(
                    f"ocr-reviewed-{ocr_id}-accepted",
                    protocol_id,
                    revision.revision_number,
                    _OCR_REVIEWED_EVENT,
                    {
                        "ocr_id": ocr_id,
                        "decision": "accepted",
                        "authority": AUTOMATIC_OCR_AUTHORITY,
                        "comment": (
                            "Upload-time OCR of the pages without a usable text "
                            "layer; covered by the one confirmation a person "
                            "gives before execution."
                        ),
                        "executable": False,
                        "ocr_page_numbers": ocr_pages,
                        "numeric_review_page_numbers": [
                            page.source_page_number for page in validated.pages
                            if page.numeric_review_required
                        ],
                    },
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
        if any(
            event.event_type == _ANALYSIS_REQUESTED_EVENT
            and event.sequence_id > planned[-1].sequence_id
            for event in events
        ):
            # A later request opened a run of its own (lane AN, decision 2):
            # the planned run is no longer the one on screen.
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
        entry = self._entry_for_revision(revision)
        reanalysis = self._reanalysis_requested(revision)
        if not events:
            lifecycle_events = tuple(
                event for event in self.store.list_events(revision.experiment_id)
                if event.protocol_revision_number == revision.revision_number
                and event.event_type in {
                    _ANALYSIS_REQUESTED_EVENT,
                    _ANALYSIS_STARTED_EVENT,
                    _ANALYSIS_FAILED_EVENT,
                    _ANALYSIS_RETRY_EVENT,
                    _ANALYSIS_READY_EVENT,
                    _SINGLE_REVIEW_REQUIRED_EVENT,
                    _REVIEW_PENDING_EVENT,
                }
            )
            current_events = _failures_not_superseded(lifecycle_events)
            latest_failure = next(
                (
                    event.payload.get("failure_code")
                    for event in reversed(current_events)
                    if event.event_type == _ANALYSIS_FAILED_EVENT
                    and isinstance(event.payload, dict)
                    and isinstance(event.payload.get("failure_code"), str)
                ),
                None,
            )
            latest_failure_detail = next(
                (
                    event.payload.get("evidence_failure")
                    for event in reversed(current_events)
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
            requested_at = next(
                (
                    event.recorded_at
                    for event in reversed(lifecycle_events)
                    if event.event_type == _ANALYSIS_REQUESTED_EVENT
                ),
                None,
            )
            state = entry.analysis_status
            if lifecycle_events:
                state = {
                    _ANALYSIS_REQUESTED_EVENT: "analysis_pending",
                    _ANALYSIS_STARTED_EVENT: "analyzing",
                    _ANALYSIS_FAILED_EVENT: "analysis_failed",
                    _ANALYSIS_RETRY_EVENT: "analyzing",
                    _ANALYSIS_READY_EVENT: "analysis_ready",
                    _SINGLE_REVIEW_REQUIRED_EVENT: "review_required",
                    _REVIEW_PENDING_EVENT: "review_pending",
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
                requested_at=requested_at,
                automatic_retry=_automatic_retry_status(lifecycle_events),
                reanalysis=reanalysis,
                available_for_execution=entry.available_for_execution,
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
        end = _piece_run_end(events, tuple(
            event for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
        ))
        if end is not None:
            # A piece run (lane AN, decision 2) ends in the analysis' own
            # ready or failed event, not in a chunk event.
            state, end_code = end
            failure_code = end_code or failure_code
            merge_status = "complete" if state == "review_required" else merge_status
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
                if state in {"review_required", "review_pending"}
                else "explicit_new_run_required_after_interruption"
                if state in {"chunk_planned", "chunk_analysis_in_progress", "merge_in_progress"}
                else "explicit_analysis_request_only"
            ),
            lifecycle_state=(
                state
                if state in {"review_required", "review_pending"}
                else "blocked"
                if state in {
                    "merge_conflict",
                    "chunk_analysis_failed",
                    "chunk_analysis_cancelled",
                    "analysis_failed",
                }
                else "analyzing"
            ),
            # The plan is the run's first record.
            requested_at=plan_event.recorded_at,
            reanalysis=reanalysis,
            available_for_execution=entry.available_for_execution,
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
                    for event in reversed(_failures_not_superseded(tuple(
                        event for event in self.store.list_events(revision.experiment_id)
                        if event.protocol_revision_number == revision.revision_number
                    )))
                    if event.event_type == _ANALYSIS_FAILED_EVENT
                ),
                None,
            )
            if failed is not None and not chunk_events:
                analysis_status = "analysis_failed"
            elif chunk_events:
                end = _piece_run_end(chunk_events, tuple(
                    event for event in self.store.list_events(revision.experiment_id)
                    if event.protocol_revision_number == revision.revision_number
                ))
                if end is not None and end[0] != "review_required":
                    analysis_status = end[0]
            lifecycle_events = tuple(
                event
                for event in self.store.list_events(revision.experiment_id)
                if event.protocol_revision_number == revision.revision_number
                and event.event_type in {
                    _ANALYSIS_REQUESTED_EVENT,
                    _ANALYSIS_STARTED_EVENT,
                    _ANALYSIS_FAILED_EVENT,
                    _ANALYSIS_RETRY_EVENT,
                    _REVIEW_PENDING_EVENT,
                }
            )
            if lifecycle_events:
                lifecycle_state = {
                    _ANALYSIS_REQUESTED_EVENT: "analysis_pending",
                    _ANALYSIS_STARTED_EVENT: "analyzing",
                    _ANALYSIS_FAILED_EVENT: "blocked",
                    _ANALYSIS_RETRY_EVENT: "analyzing",
                    # Lane RV, decision a: waiting for a person's review;
                    # it never runs as it is.
                    _REVIEW_PENDING_EVENT: "review_pending",
                }[lifecycle_events[-1].event_type]
                if lifecycle_events[-1].event_type == _REVIEW_PENDING_EVENT:
                    analysis_status = "review_pending"
            elif analysis_status == "ocr_required":
                lifecycle_state = "blocked"
        development_only = any(
            event.event_type == _DEVELOPMENT_FIXTURE_EVENT
            and event.protocol_revision_number == revision.revision_number
            for event in self.store.list_events(revision.experiment_id)
        )
        readiness = (
            analysis.readiness_status if analysis else "analysis_required"
        )
        # The execution rule (decision of 2026-10-08): a passed analysis whose
        # readiness carries no execution blocker may run. A missing or failed
        # analysis stays out; so does one that found no step to run or a
        # page it could not read. Every other readiness reason is a notice
        # the experimenter sees before pressing start.
        blocker_codes: tuple[str, ...] = ()
        outdated_codes: tuple[str, ...] = ()
        if analysis is not None:
            blocker_codes = tuple(
                reason.code.value
                for reason in domain.execution_blocking_reasons(analysis.readiness)
            )
            lifecycle_state = "blocked" if blocker_codes else "ready"
            outdated_codes = _analysis_outdated_codes(analysis.protocol)
        available = analysis is not None and not blocker_codes
        # An analysis whose title was emptied (lane EV2, decision 2) is named
        # as one without an analysis is: by the file's own title or name.
        title = (
            analysis.protocol.metadata.title
            if analysis is not None and analysis.protocol.metadata.title
            else extraction.metadata.title or Path(revision.original_filename).stem
        )
        pending = self._review_pending_event(revision)
        revision_id = _revision_id(
            revision.revision_number,
            analysis.analysis_revision_number if analysis else None,
        )
        shown = analysis
        versions = self._review_versions(revision)
        if versions:
            # Lane RV, decision c: the latest registered review version runs,
            # whatever the analyser made after it; it was registered only
            # without an execution blocker.
            event, shown = versions[-1]
            revision_id = str(event.payload["revision_id"])
            readiness = shown.readiness_status
            blocker_codes = ()
            available = True
            lifecycle_state = "ready"
            outdated_codes = ()
            if analysis_status == "review_pending" and pending is None:
                analysis_status = "review_registered"
            if shown.protocol.metadata.title:
                title = shown.protocol.metadata.title
        return ProtocolCatalogEntry(
            protocol_id=revision.experiment_id,
            title=title,
            source_filename=revision.original_filename,
            source_sha256=revision.pdf_checksum,
            revision_id=revision_id,
            readiness_status=readiness,
            analysis_status=analysis_status,
            step_count=(
                sum(len(section.steps) for section in shown.protocol.sections)
                if shown
                else 0
            ),
            created_at=revision.created_at,
            available_for_execution=available,
            lifecycle_state=lifecycle_state,
            execution_blocker_codes=blocker_codes,
            development_only=development_only,
            analysis_outdated_codes=outdated_codes,
            analysis_outdated_ko=ANALYSIS_OUTDATED_KO if outdated_codes else None,
            review_pending=(
                _review_pending_summary(pending) if pending is not None else None),
            review_versions=tuple(
                _review_version_summary(event) for event, _ in versions),
        )

    def _review_pending_event(self, revision: ProtocolRevisionRecord) -> Any | None:
        """The latest review-pending analysis newer than any passed one and
        not yet registered as a review version, or None.

        Lane RV, decision a. A later failed or running request leaves it as
        it is: it is still the latest analysis a person can review.
        """

        events = [
            event for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
            and event.event_type in {_REVIEW_PENDING_EVENT, _ANALYSIS_READY_EVENT}
        ]
        if not events or events[-1].event_type != _REVIEW_PENDING_EVENT:
            return None
        pending = events[-1]
        key = {"kind": "review_pending", "event_id": pending.event_id}
        if any(
            event.event_type == _REVIEW_VERSION_EVENT
            and isinstance(event.payload, dict) and event.payload.get("base") == key
            for event in self.store.list_events(revision.experiment_id)
        ):
            return None
        return pending

    def review_pending_analysis(
        self, protocol_id: str,
    ) -> tuple[domain.ExperimentProtocol, domain.ReadinessAssessment, tuple[ReviewFlag, ...], Any]:
        """The latest review-pending analysis, read back (lane RV, decision a).

        The protocol is given its source pages again from the stored source
        and its accepted OCR; when those are not the pages the analysis read
        (an OCR read again since), it is refused: the items cannot be shown
        against pages they were not read from.
        """

        revision = self._latest_protocol_revision(protocol_id)
        event = self._review_pending_event(revision)
        if event is None:
            raise ProtocolCatalogNotFoundError("No analysis is waiting for review.")
        payload = event.payload if isinstance(event.payload, dict) else {}
        protocol = _decode_stored_record(payload.get("protocol"))
        readiness = _decode_stored_record(payload.get("readiness"))
        if not isinstance(protocol, domain.ExperimentProtocol) or not isinstance(
            readiness, domain.ReadinessAssessment
        ):
            raise ProtocolCatalogUnavailableError("The review-pending analysis is unreadable.")
        extraction = self._extraction_for_analysis(
            revision, self._source_extraction(revision))
        if (
            extraction.sha256 != protocol.metadata.pdf.sha256
            or [_text_sha256(page.text) for page in extraction.pages]
            != payload.get("source_pages")
        ):
            raise ProtocolCatalogUnavailableError(
                "The pages this analysis read are no longer the source's pages."
            )
        protocol = replace(protocol, metadata=replace(protocol.metadata, pdf=extraction))
        flags = tuple(
            ReviewFlag.from_public(item) for item in payload.get("flags") or ()
            if isinstance(item, dict)
        )
        return protocol, readiness, flags, event

    def _source_extraction(self, revision: ProtocolRevisionRecord) -> ProtocolPdfExtraction:
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        return extract_protocol_pdf(self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        ))

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
        }

    @staticmethod
    def _development_fixture_event_id(
        fixture: CuratedProtocolFixture,
        analysis_payload_sha256: str,
        payload: dict[str, object],
    ) -> str:
        """Name the materialization event by everything the event records.

        Keyed on the fixture alone, the event collided with its own earlier
        record as soon as the analysis changed, so the analysis digest joined
        the id. The id still could not see the event's own payload. Lane DI
        (2026-10-08) dropped ``final_approval`` from it, and every store that
        had materialized this fixture before then held a row under the same id
        with the old payload: the store refused, correctly, and from
        2026-10-08 ``scripts/run_dev.sh`` did not start.

        The digest of the payload as the store writes it is part of the id
        now (lane BT, 2026-10-09). A changed payload is a new event appended
        beside the old one, and the old row stays exactly as it was written.
        """

        event_sha256 = hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return (
            f"development-fixture-{fixture.fixture_sha256[:48]}"
            f"-{analysis_payload_sha256[:16]}-{event_sha256[:16]}"
        )

    @staticmethod
    def _development_analysis_identity(
        fixture: CuratedProtocolFixture,
    ) -> tuple[str, str]:
        """Name this fixture's analysis by everything the analysis contains.

        The id used to be the fixture's own SHA-256 and nothing else, which
        held for as long as the fixture's bytes decided the analysis. They do
        not. Readiness is assessed when the fixture is loaded, against the
        capability policy, so declaring a capability changes the stored
        analysis while leaving the fixture byte-identical -- and the id could
        not see it. The store then found an id it already held whose payload
        had changed and refused, correctly, at server start, which is the
        worst moment to learn it: the pilot catalog holds
        ``curated-69517f0f...`` with payload ``47df9633...`` and the declared
        policy produces ``824e9b54...``.

        Same lesson as the chunk cache key before STEP 31. A key that cannot
        see the question reports a hit it cannot honour -- or, here, a
        collision that is really a new analysis. The payload digest is part of
        the identity now, so a changed analysis is a different analysis and is
        appended as the next revision. Nothing is overwritten and nothing is
        deleted: the earlier revisions stay exactly where they are, and so do
        the findings recorded against them, which belong to those revisions
        and not to this one.
        """

        _, payload_sha256 = serialize_analysis(
            fixture.draft.protocol,
            fixture.draft.readiness,
            fixture.draft.capability_policy_id,
        )
        return (
            f"curated-{fixture.fixture_sha256}-{payload_sha256[:16]}",
            payload_sha256,
        )

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
        analysis_id, payload_sha256 = self._development_analysis_identity(
            fixture
        )
        analysis = self.store.create_experiment_with_analysis(
            fixture.protocol_id,
            fixture.source_pdf_path,
            analysis_id,
            fixture.draft.protocol,
            fixture.draft.readiness,
            fixture.draft.capability_policy_id,
        )
        revision = self.store.get_protocol_revision(fixture.protocol_id, 1)
        if revision is None:
            raise ProtocolCatalogUnavailableError(
                "Development fixture revision is unavailable."
            )
        payload = self._development_fixture_payload(fixture)
        self.store.append_event(
            self._development_fixture_event_id(fixture, payload_sha256, payload),
            fixture.protocol_id,
            revision.revision_number,
            _DEVELOPMENT_FIXTURE_EVENT,
            payload,
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
        """Verify the exact development-only provenance marker in the store.

        The question is identity, not uniqueness.  Asking whether the protocol
        carried exactly one analysis revision made an ordinary event -- editing
        the fixture, whose hash then names a second analysis at the next
        bootstrap -- indistinguishable from a corrupted store, and from
        2026-09-04 the whole catalog listing failed with a 503 for that reason
        alone.  A second analysis revision is what an append-only ledger is
        supposed to accumulate.

        What has to hold is that the analysis this exact fixture materializes
        exists, still carries the fixture's own content, and is the one the
        catalog would serve.  An analysis that does not match is not tolerated
        by being ignored: if the latest analysis is not this fixture's, the
        answer is False, because then the reader and the fixture disagree about
        what this protocol is.
        """

        if not fixture.development_only:
            return False
        revisions = self.store.list_protocol_revisions(fixture.protocol_id)
        if len(revisions) != 1:
            return False
        revision = revisions[0]
        if revision.pdf_checksum != fixture.source_pdf_sha256:
            return False
        analysis = self._latest_analysis(revision)
        if analysis is None:
            return False
        if analysis.analysis_id != self._development_analysis_identity(
            fixture
        )[0]:
            return False
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
        current_events = _failures_not_superseded(revision_events)
        latest_failure = next(
            (
                event.payload.get("failure_code")
                for event in reversed(current_events)
                if event.event_type in {_ANALYSIS_FAILED_EVENT, _CHUNK_FAILED_EVENT}
                and isinstance(event.payload, dict)
                and isinstance(event.payload.get("failure_code"), str)
            ),
            None,
        )
        latest_failure_detail = next(
            (
                event.payload.get("evidence_failure")
                for event in reversed(current_events)
                if event.event_type
                in {_ANALYSIS_FAILED_EVENT, _CHUNK_FAILED_EVENT}
                and isinstance(event.payload, dict)
                and isinstance(event.payload.get("evidence_failure"), dict)
            ),
            None,
        )
        reanalysis = self._reanalysis_status(revision_events, analysis)
        base: dict[str, object] = {
            **entry.public_dict(),
            "analysis_available": analysis is not None,
            "lifecycle_state": entry.lifecycle_state,
            # Lane RN decision 2: a failure after a pass belongs to the run
            # asked for over it (``reanalysis``), not to the analysis that
            # passed, which still runs.
            "analysis_failure": (
                {
                    "code": latest_failure,
                    "detail": latest_failure_detail,
                    "retryable": (
                        latest_failure not in _NOT_RETRYABLE_FAILURE_CODES
                        or _refused_under_the_old_page_bound(
                            latest_failure, extraction.page_count)
                    ),
                    # Lane PA decision 4 (2026-10-06): the recovery guidance
                    # is the server's, in Korean.
                    "action": (
                        _OLD_PAGE_BOUND_ACTION
                        if _refused_under_the_old_page_bound(
                            latest_failure, extraction.page_count)
                        else _ANALYSIS_RECOVERY_ACTIONS.get(
                            latest_failure, _DEFAULT_ANALYSIS_RECOVERY_ACTION
                        )
                    ),
                }
                if latest_failure is not None and reanalysis is None
                else None
            ),
            "reanalysis": reanalysis,
            # Lane RV, decision a: the latest analysis waiting for a person.
            "review_pending": entry.review_pending,
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
                "label_ko": _ANALYSIS_REQUIRED_LABEL_KO,
                "reasons": [
                    {
                        "code": entry.analysis_status,
                        "message": "Explicit structured analysis is required before review or execution.",
                        "message_ko": _ANALYSIS_REQUIRED_MESSAGE_KO,
                    }
                ],
            },
            "capability_policy_id": domain.P1_CAPABILITY_POLICY.profile_id,
            # The execution rule's two lists (decision of 2026-10-08): what
            # still keeps this revision from running, and what the
            # experimenter is told before starting. Empty before an analysis.
            "execution_blockers": [],
            "execution_notices": [],
            "safety_notice_sources": [],
            "timers": {"verified": [], "refused": []},
            # Lane EV2, decision 2: the fields emptied because the source did
            # not confirm them, and the one line the start screen shows.
            "cleared_fields": [],
            "cleared_fields_ko": None,
            # Lane EV2, decision 3: the fixed repeat counts to check before
            # the start, one line each.
            "repeat_count_checks_ko": [],
        }
        ocr_projection = base["ocr"]
        base["pipeline"] = self._pipeline(
            entry, analysis, revision,
            ocr=ocr_projection if isinstance(ocr_projection, dict) else {},
            failure_code=latest_failure,
            automatic_retry=_automatic_retry_status(revision_events),
            reanalysis=reanalysis,
            events=revision_events,
            page_count=extraction.page_count,
        )
        versions = self._review_versions(revision)
        version_payload: dict[str, object] | None = None
        if versions:
            # Lane RV, decision c: the start screen shows the version that
            # runs -- the latest registered review version.
            version_event, analysis = versions[-1]
            version_payload = version_event.payload
            base["analysis_available"] = True
            base["review_version"] = _review_version_summary(version_event)
        if analysis is None:
            return base

        protocol = analysis.protocol
        declared_warning_count = domain.declared_safety_warning_count(protocol)
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
                "sections": _review_sections(protocol),
                "constructs": [
                    {
                        "construct_type": type(construct).__name__,
                        **_review_value(construct),
                    }
                    for construct in protocol.constructs
                ],
                "readiness": _with_korean_reasons(_review_value(analysis.readiness)),
                "capability_policy_id": analysis.capability_policy_id,
                "analysis_payload_sha256": analysis.payload_sha256,
                "page_coverage": [
                    dict(item) for item in analysis.page_coverage
                ],
                "declined_segment_count": sum(
                    len(item.get("declined_segment_ids") or ())
                    for item in analysis.page_coverage
                ),
                "declared_safety_warning_count": declared_warning_count,
                "execution_blockers": self._execution_reasons(
                    analysis, blocking=True
                ),
                "execution_notices": self._execution_reasons(
                    analysis, blocking=False
                ),
                # The source's own safety statements, step by step, for the
                # screen the experimenter reads before pressing start. The
                # server adds the Korean beside each (it holds the
                # translation store); the words here are the document's.
                "safety_notice_sources": _safety_notice_sources(protocol),
                "cleared_fields": [
                    {
                        "field": field,
                        "label_ko": domain.cleared_field_label_ko(field, protocol),
                    }
                    for field in protocol.cleared_fields
                ],
                "cleared_fields_ko": domain.cleared_fields_line_ko(
                    protocol.cleared_fields, protocol
                ),
                "repeat_count_checks_ko": _repeat_count_checks_ko(protocol),
                # Step timers for the start screen (lane PT, decision 4):
                # each verified value with its source excerpt, and each time
                # the server could not verify, with the reason.
                "timers": _review_timers(
                    self._step_timer_table(
                        revision, extraction,
                        protocol_with_display_step_labels(protocol),
                        version=version_payload,
                    )
                ),
            }
        )
        return base

    @staticmethod
    def _execution_reasons(
        analysis: Any, *, blocking: bool
    ) -> list[dict[str, object]]:
        """The readiness reasons of one kind, each with its Korean line.

        ``blocking`` selects the reasons that keep the revision out of
        execution; otherwise the notices the experimenter reads before
        starting. ``kind`` says what sort of notice it is:
        ``no_guidance_yet`` for a construct this version cannot guide (said
        once more at the step, with the source read out) and
        ``source_note`` for a finding about the source itself.
        """

        reasons = (
            domain.execution_blocking_reasons(analysis.readiness)
            if blocking else domain.execution_notice_reasons(analysis.readiness)
        )
        return [
            {
                "code": reason.code.value,
                "message": reason.message,
                "message_ko": readiness_reason_korean(
                    reason.code.value, reason.message
                ),
                "kind": (
                    "blocking" if blocking
                    else "no_guidance_yet"
                    if reason.code in domain.NO_GUIDANCE_YET_REASON_CODES
                    else "source_note"
                ),
                "source_page_number": (
                    reason.evidence.source_page_number
                    if reason.evidence else None
                ),
                "source_excerpt": (
                    reason.evidence.source_excerpt
                    if reason.evidence else None
                ),
                "step_id": reason.step_id,
            }
            for reason in reasons
        ]

    def pipeline_status(self, protocol_id: str) -> dict[str, object]:
        """Where this document stands on the way to a start (lane PX, 4)."""

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
            event for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
        )
        failure = next(
            (
                event.payload.get("failure_code")
                for event in reversed(_failures_not_superseded(revision_events))
                if event.event_type in {_ANALYSIS_FAILED_EVENT, _CHUNK_FAILED_EVENT}
                and isinstance(event.payload, dict)
                and isinstance(event.payload.get("failure_code"), str)
            ),
            None,
        )
        return self._pipeline(
            entry, analysis, revision,
            ocr=self._ocr_projection(revision, extraction, include_text=False),
            failure_code=failure,
            automatic_retry=_automatic_retry_status(revision_events),
            reanalysis=self._reanalysis_status(revision_events, analysis),
            events=revision_events,
            page_count=extraction.page_count,
        )

    def _piece_progress_ko(self, revision: ProtocolRevisionRecord) -> str | None:
        """The progress line of a piece run still going, or None (lane AN, 2).

        "긴 문서를 조각 11개로 나눠 분석 중입니다 · 조각 5·6·7·8/11 분석 중 · 끝난 조각
        4/11", a retry beside the piece it is for, and the joining once
        every piece has passed.
        """

        events = self._latest_chunk_events(revision)
        if not events or _piece_run_end(events, tuple(
            event for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
        )) is not None:
            return None
        plan = events[0].payload if isinstance(events[0].payload, dict) else {}
        chunks = plan.get("chunks") if isinstance(plan.get("chunks"), list) else []
        count = len(chunks)
        if not count:
            return None
        state: dict[str, str] = {}
        retrying: set[str] = set()
        joining = False
        for event in events[1:]:
            payload = event.payload if isinstance(event.payload, dict) else {}
            chunk_id = str(payload.get("chunk_id"))
            if event.event_type == _CHUNK_STARTED_EVENT:
                state[chunk_id] = "in_progress"
                if payload.get("attempt") == 2:
                    retrying.add(chunk_id)
            elif event.event_type == _CHUNK_COMPLETED_EVENT:
                state[chunk_id] = "completed"
                retrying.discard(chunk_id)
            elif event.event_type == _CHUNK_FAILED_EVENT:
                state[chunk_id] = "failed"
                retrying.discard(chunk_id)
            elif event.event_type == _MERGE_STARTED_EVENT:
                joining = True
        ordinal = {
            str(chunk.get("chunk_id")): chunk.get("ordinal")
            for chunk in chunks if isinstance(chunk, dict)
        }
        done = sum(1 for value in state.values() if value == "completed")
        if joining:
            return (
                f"긴 문서를 조각 {count}개로 나눠 분석했습니다 · 조각 {count}개를 합쳐 원문 "
                "전체와 다시 대조하고 있습니다."
            )
        running = sorted(
            ordinal[chunk_id] for chunk_id, value in state.items()
            if value == "in_progress" and isinstance(ordinal.get(chunk_id), int)
        )
        line = f"긴 문서를 조각 {count}개로 나눠 분석 중입니다"
        if running:
            line += f" · 조각 {'·'.join(str(index) for index in running)}/{count} 분석 중"
        line += f" · 끝난 조각 {done}/{count}"
        again = sorted(
            ordinal[chunk_id] for chunk_id in retrying
            if isinstance(ordinal.get(chunk_id), int)
        )
        if again:
            line += (
                f" · 조각 {'·'.join(str(index) for index in again)} 다시 시도 중(1/1)"
            )
        return line + "."

    def _pipeline(
        self,
        entry: ProtocolCatalogEntry,
        analysis: AnalysisRevisionRecord | None,
        revision: ProtocolRevisionRecord,
        *,
        ocr: dict[str, object],
        failure_code: str | None,
        automatic_retry: dict[str, object] | None = None,
        reanalysis: dict[str, object] | None = None,
        events: Sequence[Any] = (),
        page_count: int | None = None,
    ) -> dict[str, object]:
        """One Korean line: the stage a document is at, whether it is stuck
        there, why, and what a person does (lane PX, decision 4).

        Stages, in order: 원문 읽기 → OCR → 분석 → 근거 대조 → 실행 준비 →
        사람 확인 → 실행 가능. Translation is added by the server, which
        holds the translation store. A document that reached this method
        was read, so 원문 읽기 never blocks here. An automatic retry (lane
        AN, decision 1) is said while it runs and after it failed. A run
        asked for over a passed analysis (lane RN, decision 2) is said beside
        the pass, which still runs whatever that run does. A response read
        after its brackets were repaired (lane EV3, decision 1) is said on the
        line of the attempt it belongs to.
        """

        passed_repair = next(
            (
                _response_repair_of(event.payload) for event in events
                if analysis is not None and event.event_type == _ANALYSIS_READY_EVENT
                and event.analysis_revision_number == analysis.analysis_revision_number
            ),
            None,
        )
        failed_repair = next(
            (
                _response_repair_of(event.payload)
                for event in reversed(_failures_not_superseded(events))
                if event.event_type in {_ANALYSIS_FAILED_EVENT, _CHUNK_FAILED_EVENT}
                and isinstance(event.payload, dict)
                and isinstance(event.payload.get("failure_code"), str)
            ),
            None,
        )
        passed_clause = (
            f" {_response_repair_ko(passed_repair)}: 고친 응답도 형식·원문 근거·경계 검사를 "
            "모두 통과했습니다." if passed_repair else "")
        passed_extra: dict[str, object] = (
            {"response_repair": passed_repair} if passed_repair else {})
        retry = automatic_retry or {}
        again = reanalysis or {}
        again_failure = again.get("failure") if isinstance(again.get("failure"), dict) else None
        # Lane EV3, decision 5: the re-analysis's own automatic retry.
        again_retry = again.get("automatic_retry") if isinstance(
            again.get("automatic_retry"), dict) else {}
        again_retry_count = (f"{again_retry.get('attempt', 1)}/"
                             f"{again_retry.get('limit', AUTOMATIC_RETRY_LIMIT)}")
        if again.get("state") == "review_pending":
            # Lane RV, decision a: the run over a passed analysis ended with
            # items for a person; the passed analysis still runs.
            summary = again.get("review_pending") if isinstance(
                again.get("review_pending"), dict) else {}
            again_clause = (
                f" 다시 분석은 {summary.get('message_ko') or '검토 대기'}입니다 · "
                "지금 분석으로 계속 실행할 수 있습니다.")
        elif again.get("state") in {"analysis_pending", "analyzing"}:
            again_clause = (
                (f" 다시 분석 중입니다 · 다시 시도 중({again_retry_count}) · 첫 응답이 정해진 "
                 "형식에 맞지 않아 같은 요청을 자동으로 한 번 다시 보냈습니다 · "
                 if again_retry.get("state") == "in_progress"
                 else " 다시 분석 중입니다 · 원문 근거를 확인하고 있습니다 · ")
                + "지금 분석으로 계속 실행할 수 있습니다.")
        elif again_failure is not None:
            again_repair = _response_repair_of(again_failure)
            again_clause = (
                " 다시 분석이 실패했습니다: "
                f"{_ANALYSIS_FAILURE_KO.get(str(again_failure.get('code')), '분석이 실패했습니다.')} "
                + (f"같은 요청을 자동으로 한 번 다시 보냈지만 자동 재시도({again_retry_count})도 "
                   "실패했습니다. " if again_retry.get("state") == "failed" else "")
                + (f"{_response_repair_ko(again_repair)}. " if again_repair else "")
                + "지금까지의 통과 분석으로 계속 실행할 수 있습니다.")
        else:
            again_clause = ""
        again_extra: dict[str, object] = (
            {"reanalysis": again, **({"failure_code": again_failure["code"]} if again_failure else {})}
            if again else {}
        )
        retry_count = f"{retry.get('attempt', 1)}/{retry.get('limit', AUTOMATIC_RETRY_LIMIT)}"
        truncated_how = _truncated_retry_ko(retry) if retry.get("truncated") else None

        def result(stage: str, *, blocked: bool, message: str, action: str | None = None,
                   **extra: object) -> dict[str, object]:
            return {
                "stage": stage, "stage_ko": PIPELINE_STAGE_KO[stage], "blocked": blocked,
                "message": message, "action": action, **extra,
            }

        ocr_state = str(ocr.get("state") or "not_required")
        ocr_pages = [
            page["source_page_number"] for page in (ocr.get("pages") or ())
            if isinstance(page, dict) and page.get("provider") != OCR_TEXT_LAYER_PROVIDER
            and isinstance(page.get("source_page_number"), int)
        ] if isinstance(ocr.get("pages"), list) else []
        if entry.available_for_execution:
            return result(
                "ready", blocked=False,
                message="분석을 통과했습니다. 실행할 수 있습니다." + passed_clause + again_clause,
                action=(_REANALYSIS_RECOVERY_ACTION if again_failure is not None
                        else "시작 전 화면의 안전 주의를 읽고 '이 프로토콜로 시작'을 누르세요."),
                ocr_page_numbers=ocr_pages, **again_extra, **passed_extra)
        if analysis is None:
            if entry.analysis_status == "review_pending" and entry.review_pending:
                # Lane RV, decision a (human decision 2026-10-12): kept for a
                # person instead of thrown away; it does not run as it is.
                return result(
                    "review", blocked=True,
                    message=(
                        f"{entry.review_pending['message_ko']}. 분석은 끝났지만 원문에서 "
                        "확인하지 못한 항목이 있어 이대로는 실행하지 않습니다."
                        + passed_clause),
                    action=REVIEW_PENDING_ACTION_KO,
                    review_pending=entry.review_pending,
                    ocr_page_numbers=ocr_pages)
            if ocr_state in {"queued", "in_progress"}:
                return result(
                    "ocr", blocked=False,
                    message="글자 층이 없는 쪽을 OCR 로 읽는 중입니다. 끝나면 분석이 바로 시작됩니다.")
            if ocr_state in {"failed", "rejected"}:
                code = str(ocr.get("failure_code") or "protocol_ocr_failed")
                return result(
                    "ocr", blocked=True,
                    message=("OCR 결과를 거절했습니다." if ocr_state == "rejected"
                             else "글자 층이 없는 쪽의 OCR 이 실패했습니다."),
                    action="'OCR 텍스트 추출'을 눌러 다시 시도하세요. 원문은 바뀌지 않습니다.",
                    failure_code=code)
            if ocr_state == "ocr_required" and entry.analysis_status in {"ocr_required"}:
                return result(
                    "ocr", blocked=True,
                    message="글자 층이 없는 쪽이 있어 OCR 글이 먼저 필요합니다.",
                    action=("OCR 공급자가 설정돼 있으면 업로드 때 자동으로 돕니다. 돌지 않았다면 "
                            "서버의 OCR 설정(VOINEY_LAB_OCR_PROVIDERS 와 엔진 키)을 확인하고 "
                            "'OCR 텍스트 추출'을 누르세요."))
            if failure_code is not None and entry.lifecycle_state not in {
                "analysis_pending", "analyzing",
            }:
                stage = ("evidence" if failure_code == "protocol_analysis_invalid_evidence"
                         else "analysis")
                message = _ANALYSIS_FAILURE_KO.get(failure_code, "분석이 실패했습니다.")
                if page_count is not None and _refused_under_the_old_page_bound(
                    failure_code, page_count
                ):
                    # Lane AN, decision 2: refused under lane EV2's 60-page
                    # bound, which the pieces replaced.
                    return result(
                        "analysis", blocked=True, message=_OLD_PAGE_BOUND_KO,
                        action=_OLD_PAGE_BOUND_ACTION, failure_code=failure_code)
                failed_pieces = _failed_pieces_ko(events)
                if failure_code == "protocol_analysis_piece_failed" and failed_pieces:
                    # Lane AN, decision 2: "일부 조각 분석 실패 · 41–59쪽".
                    message += f" · {failed_pieces}"
                if retry.get("state") == "failed" and truncated_how:
                    message += (f" {truncated_how} 자동으로 한 번 다시 보냈지만 "
                                f"자동 재시도({retry_count})도 실패했습니다.")
                elif retry.get("state") == "failed":
                    message += f" 같은 요청을 자동으로 한 번 다시 보냈지만 자동 재시도({retry_count})도 실패했습니다."
                if failed_repair:
                    message += f" {_response_repair_ko(failed_repair)}."
                return result(
                    stage, blocked=True,
                    message=message,
                    action=_ANALYSIS_RECOVERY_ACTIONS.get(
                        failure_code, _DEFAULT_ANALYSIS_RECOVERY_ACTION),
                    failure_code=failure_code,
                    **({"response_repair": failed_repair} if failed_repair else {}))
            pieces_line = self._piece_progress_ko(revision)
            if pieces_line is not None and entry.lifecycle_state in {
                "analysis_pending", "analyzing",
            }:
                # Lane AN, decision 2: "조각 k/n 분석 중".
                return result("analysis", blocked=False, message=pieces_line)
            if entry.lifecycle_state in {"analysis_pending", "analyzing"} or (
                entry.analysis_status in {
                    "chunk_planned", "chunk_analysis_in_progress", "merge_in_progress",
                }
            ):
                if retry.get("state") == "in_progress" and truncated_how:
                    return result(
                        "analysis", blocked=False,
                        message=(f"분석 중입니다 · 다시 시도 중({retry_count}). {truncated_how} "
                                 "자동으로 한 번 다시 보냈습니다."),
                        automatic_retry=retry)
                if retry.get("state") == "in_progress":
                    return result(
                        "analysis", blocked=False,
                        message=(f"분석 중입니다 · 다시 시도 중({retry_count}). 첫 응답이 정해진 형식에 "
                                 "맞지 않아 같은 요청을 자동으로 한 번 다시 보냈습니다."),
                        automatic_retry=retry)
                return result(
                    "analysis", blocked=False,
                    message="분석 중입니다. 원문 근거를 확인하고 있습니다.")
            if entry.analysis_status in {"chunk_analysis_failed", "merge_conflict",
                                         "chunk_analysis_cancelled"}:
                return result(
                    "analysis", blocked=True,
                    message=_ANALYSIS_FAILURE_KO.get(entry.analysis_status, "분석이 실패했습니다."),
                    action=_DEFAULT_ANALYSIS_RECOVERY_ACTION)
            return result(
                "analysis", blocked=False,
                message="분석 대기 중입니다. 업로드 직후 자동으로 시작되며, 시작되지 않았다면 "
                        "'분석 다시 시도'를 누르세요.",
                ocr_page_numbers=ocr_pages)
        blockers = list(domain.execution_blocking_reasons(analysis.readiness))
        if not blockers:
            # Cannot happen while available_for_execution reads the same
            # list, but a pipeline line must still say something true.
            return result(
                "ready", blocked=False,
                message="분석을 통과했습니다. 실행할 수 있습니다." + passed_clause,
                action="시작 전 화면의 안전 주의를 읽고 '이 프로토콜로 시작'을 누르세요.",
                ocr_page_numbers=ocr_pages, **passed_extra)
        first = readiness_reason_korean(
            blockers[0].code.value, blockers[0].message) or "실행을 막는 사유가 있습니다."
        return result(
            "readiness", blocked=True,
            message=f"{first}" + (f" (외 {len(blockers) - 1}건)" if len(blockers) > 1 else "")
            + passed_clause
            + again_clause.replace("지금 분석으로 계속 실행할 수 있습니다.", "지금 분석은 그대로입니다.")
            .replace("지금까지의 통과 분석으로 계속 실행할 수 있습니다.", "지금 분석은 그대로입니다."),
            action=_BLOCKER_ACTIONS.get(
                blockers[0].code.value,
                "원문을 확인하고 고친 PDF 를 올리거나 '다시 분석'을 누르세요."),
            remaining_reason_codes=[reason.code.value for reason in blockers],
            ocr_page_numbers=ocr_pages, **again_extra, **passed_extra)

    def register(
        self,
        source_pdf: str | Path,
        *,
        source_filename: str,
        media_type: str,
        converted_from: SourceConversion | None = None,
    ) -> ProtocolRegistration:
        """Register one source PDF, or find the one already registered.

        ``converted_from`` names the Word document or photo the PDF was made
        from (lane AN, FM); it is kept in the registration event, where
        ``find_converted_upload`` finds it.
        """

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
                **(
                    {"converted_from": converted_from.payload()}
                    if converted_from is not None else {}
                ),
            },
        )
        return ProtocolRegistration(
            self._entry_for_revision(creation.protocol_revision),
            deduplicated=False,
        )

    def attach_reagent_table(
        self, protocol_id: str, source: str | Path, *, filename: str,
    ) -> tuple[dict[str, object], bool]:
        """Attach an .xlsx workbook to a protocol as its reagent table (FM b).

        Append-only: one event per workbook, named by its checksum; the same
        workbook attached again returns the one already there. Returns the
        table and whether it was already attached.
        """

        if not isinstance(filename, str) or not _SAFE_TABLE_FILENAME.fullmatch(filename):
            raise ReagentTableError("Reagent table filename must be one plain .xlsx filename.")
        revision = self._latest_protocol_revision(protocol_id)
        digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        for table in self.reagent_tables(protocol_id):
            if table.get("sha256") == digest:
                return table, True
        workbook = read_reagent_workbook(source)
        table_id = f"reagent-table-{digest[:24]}"
        payload = {
            "table_id": table_id,
            "filename": filename,
            "media_type": REAGENT_TABLE_MEDIA_TYPE,
            "sha256": digest,
            "byte_size": Path(source).stat().st_size,
            "authority": "person_upload",
            **workbook,
        }
        self.store.append_event(
            f"{table_id}-{revision.pdf_checksum[:16]}",
            protocol_id,
            revision.revision_number,
            _REAGENT_TABLE_EVENT,
            payload,
        )
        return payload, False

    def reagent_tables(self, protocol_id: str) -> list[dict[str, object]]:
        """The reagent tables attached to a protocol, oldest first."""

        revision = self._latest_protocol_revision(protocol_id)
        return [
            event.payload for event in self.store.list_events(revision.experiment_id)
            if event.event_type == _REAGENT_TABLE_EVENT and isinstance(event.payload, dict)
        ]

    def find_converted_upload(self, sha256: str) -> ProtocolRegistration | None:
        """The protocol a Word document or photo with this checksum made, if any.

        Lane AN, FM: LibreOffice writes the time into each PDF it prints, so
        the same .docx uploaded again gives other PDF bytes; it is found by
        its own checksum before it is converted again.
        """

        revision = self._revision_converted_from(sha256)
        if revision is None:
            return None
        return ProtocolRegistration(self._entry_for_revision(revision), deduplicated=True)

    def _revision_converted_from(self, sha256: str) -> ProtocolRevisionRecord | None:
        """The revision registered from the upload with this checksum, if any."""

        for experiment in self.store.list_experiments():
            for event in self.store.list_events(experiment.experiment_id):
                if event.event_type != "protocol_registered" or not isinstance(
                    event.payload, dict
                ):
                    continue
                origin = event.payload.get("converted_from")
                if isinstance(origin, dict) and origin.get("sha256") == sha256:
                    return self.store.get_protocol_revision(
                        experiment.experiment_id, event.protocol_revision_number
                    )
        return None

    def analyze(
        self,
        protocol_id: str,
        model: ProtocolAnalysisModel,
        *,
        analysis_id: str,
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
        pieces = analysis_pieces(extraction)
        try:
            # Lane EV2, decision 4, changed by lane AN, decision 2: a document
            # longer than one analysis reads is read in pieces; one longer
            # than the pieces read is refused here, before any model call.
            if pieces:
                check_pieced_page_count(extraction)
            else:
                check_analysis_page_count(extraction)
        except ProtocolAnalysisTooManyPagesError as exc:
            self._record_analysis_failure(revision, analysis_id, exc)
            raise
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
        if pieces:
            try:
                draft = self._analyze_in_pieces(
                    revision, extraction, model, pieces, analysis_id=analysis_id)
            except Exception as exc:
                self._record_analysis_failure(revision, analysis_id, exc)
                raise
            return self._store_analysis_result(
                revision, draft, analysis_id, pieces=pieces)
        if status == "chunked_analysis_required":
            raise ProtocolChunkedAnalysisRequiredError(
                "Protocol text is larger than one analysis reads."
            )
        observed = _ObservedAnalysisModel.wrap(model)
        # A repaired answer with items for a person, kept should its retry
        # not come back better (lane RV, decision a).
        kept: ProtocolAnalysisDraft | None = None
        try:
            draft = analyze_protocol_extraction(
                extraction, observed.model, review_unverified=True)
            if draft.review_flags and draft.response_repair is not None:
                raise _RepairedAnswerWithItems(draft)
        except Exception as exc:
            if isinstance(exc, _RepairedAnswerWithItems):
                kept = exc.draft
            failure_code = self._record_analysis_failure(
                revision, analysis_id, exc, truncated=observed.truncated)
            repair = getattr(exc, "response_repair", None)
            if not self._automatic_retry_allowed(
                revision, failure_code, repaired=repair is not None
            ):
                if kept is not None:
                    return self._store_review_pending(revision, kept, analysis_id)
                raise
            # The same request again: the same extraction to the same model,
            # nothing about the failure added (lane AN, decision 1) -- except
            # that an answer cut at the output limit is asked for one
            # reasoning level lower (lane AQ, decision 4).
            retry_model, retry_payload = observed.retry()
            retry_of, analysis_id = analysis_id, f"{analysis_id}-retry-1"
            self.store.append_event(
                f"analysis-retry-{analysis_id}",
                protocol_id,
                revision.revision_number,
                _ANALYSIS_RETRY_EVENT,
                {
                    "status": "analyzing",
                    "analysis_id": analysis_id,
                    "retry_of": retry_of,
                    "attempt": 1,
                    "limit": AUTOMATIC_RETRY_LIMIT,
                    "reason_code": failure_code,
                    "authority": AUTOMATIC_RETRY_AUTHORITY,
                    **retry_payload,
                    **(
                        {"response_repaired": repair.public_dict()}
                        if repair is not None else {}
                    ),
                },
            )
            logging.getLogger(__name__).info(
                "protocol.analysis.automatic_retry protocol_id=%s attempt=1 limit=%d "
                "reason_code=%s truncated=%s reasoning_effort=%s",
                protocol_id, AUTOMATIC_RETRY_LIMIT, failure_code,
                bool(retry_payload.get("truncated")), retry_payload.get("reasoning_effort"),
            )
            try:
                draft = analyze_protocol_extraction(
                    extraction, retry_model, review_unverified=True)
            except Exception as retry_exc:
                self._record_analysis_failure(
                    revision, analysis_id, retry_exc, truncated=observed.truncated)
                if kept is not None:
                    # Lane RV, decision a: the first answer's items wait for a
                    # person rather than the analysis being thrown away.
                    return self._store_review_pending(revision, kept, retry_of)
                raise
        return self._store_analysis_result(revision, draft, analysis_id)

    def _store_analysis_result(
        self,
        revision: ProtocolRevisionRecord,
        draft: ProtocolAnalysisDraft,
        analysis_id: str,
        *,
        pieces: tuple[AnalysisPiece, ...] = (),
    ) -> ProtocolCatalogEntry:
        """A passed analysis as the next analysis revision; one with items a
        person must check as a review-pending ledger event (lane RV, a)."""

        if draft.review_flags:
            return self._store_review_pending(revision, draft, analysis_id, pieces=pieces)
        return self._store_passed_analysis(revision, draft, analysis_id, pieces=pieces)

    def _store_review_pending(
        self,
        revision: ProtocolRevisionRecord,
        draft: ProtocolAnalysisDraft,
        analysis_id: str,
        *,
        pieces: tuple[AnalysisPiece, ...] = (),
    ) -> ProtocolCatalogEntry:
        """Keep an analysis with items for a person, and say so (lane RV, a).

        Human decision 2026-10-12/14: the analysis is kept as the model read
        it -- the source and this record are never changed afterwards -- with
        each item the server could not find in the source flagged and why. It
        is not an analysis revision, so nothing that runs a protocol can read
        it; a person's review and registration make the version that runs.
        The source's pages are not copied into the record: their checksums
        are, and the pages are read again from the stored source.
        """

        protocol_id = revision.experiment_id
        protocol = draft.protocol
        if protocol.protocol_id != protocol_id:
            protocol = replace(protocol, protocol_id=protocol_id)
        pdf = protocol.metadata.pdf
        stored = replace(protocol, metadata=replace(
            protocol.metadata, pdf=replace(pdf, pages=())))
        flags = draft.review_flags
        payload: dict[str, object] = {
            "status": "review_pending",
            "analysis_id": analysis_id,
            "flags": [flag.public_dict() for flag in flags],
            "needs_review_count": len({flag.item_id for flag in flags}),
            "verified_evidence_count": draft.verified_evidence_count,
            "capability_policy_id": draft.capability_policy_id,
            "source_pages": [_text_sha256(page.text) for page in pdf.pages],
            "protocol": _encode_stored_record(stored),
            "readiness": _encode_stored_record(draft.readiness),
        }
        if protocol.cleared_fields:
            payload["cleared_fields"] = list(protocol.cleared_fields)
        if pieces:
            payload["pieces"] = [piece.public_dict() for piece in pieces]
        if draft.response_repair is not None:
            payload["response_repaired"] = draft.response_repair.public_dict()
        self.store.append_event(
            f"analysis-review-pending-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _REVIEW_PENDING_EVENT,
            payload,
        )
        logging.getLogger(__name__).info(
            "protocol.analysis.review_pending protocol_id=%s items=%d flags=%d",
            protocol_id, payload["needs_review_count"], len(flags),
        )
        return self.get_entry(protocol_id)

    def _analyze_in_pieces(
        self,
        revision: ProtocolRevisionRecord,
        extraction: ProtocolPdfExtraction,
        model: ProtocolAnalysisModel,
        pieces: tuple[AnalysisPiece, ...],
        *,
        analysis_id: str,
    ) -> ProtocolAnalysisDraft:
        """Analyse a long document in pieces, ``ANALYSIS_PIECE_CONCURRENCY`` at once.

        Lane AN, decision 2. The plan and each piece's start, pass and
        failure are ledger events of this request's run (``analysis_id``),
        which the run status and the pipeline line read ("조각 k/n 분석 중").
        A piece that fails is sent once more by itself -- the same request,
        or one reasoning level lower when its answer was cut at the output
        limit (lane AQ). Lane RV, decision a (human decision 2026-10-12,
        changing lane AN's "nothing passes on part of it"): a piece that fails
        again no longer fails the document. The pieces that passed are joined
        and the joined analysis is checked against the whole document; each
        failed piece's pages become an item a person checks, so the analysis
        is one waiting for review and does not run as it is. Only when every
        piece failed does the document fail.
        """

        protocol_id = revision.experiment_id
        run = {"analysis_run_id": analysis_id}

        def append(event_id: str, event_type: str, payload: dict[str, object]) -> None:
            self.store.append_event(
                event_id, protocol_id, revision.revision_number, event_type,
                {**run, **payload},
            )

        append(f"pieces-planned-{analysis_id}", _CHUNK_PLAN_EVENT, {
            "status": "planned",
            "chunks": [
                {"chunk_id": f"piece-{piece.index}", "ordinal": piece.index,
                 "source_page_start": piece.first_page,
                 "source_page_end": piece.last_page}
                for piece in pieces
            ],
        })
        # The workers only call the model and check its answer. The ledger
        # connection belongs to this thread, so their starts come back here
        # through ``outbox`` and are written in order.
        outbox: queue.SimpleQueue[tuple[str, str, dict[str, object]]] = queue.SimpleQueue()

        def one(piece: AnalysisPiece) -> ProtocolAnalysisDraft:
            chunk = {"chunk_id": f"piece-{piece.index}", "ordinal": piece.index}
            observed = _ObservedAnalysisModel.wrap(model)
            outbox.put((f"piece-started-{analysis_id}-{piece.index}", _CHUNK_STARTED_EVENT,
                        {**chunk, "status": "in_progress", "attempt": 1}))
            try:
                return analyze_protocol_piece(
                    extraction, piece, observed.model, review_unverified=True)
            except Exception as exc:  # noqa: BLE001 - sent once more by itself
                retry_model, retry_payload = observed.retry()
                outbox.put((f"piece-retry-{analysis_id}-{piece.index}", _CHUNK_STARTED_EVENT, {
                    **chunk, "status": "in_progress", "attempt": 2,
                    "retry_of_failure_code": _piece_failure_code(exc), **retry_payload,
                }))
            return analyze_protocol_piece(
                extraction, piece, retry_model, review_unverified=True)

        def drain() -> None:
            while True:
                try:
                    event_id, event_type, payload = outbox.get_nowait()
                except queue.Empty:
                    return
                append(event_id, event_type, payload)

        drafts: dict[int, ProtocolAnalysisDraft] = {}
        failures: dict[int, BaseException] = {}
        with ThreadPoolExecutor(
            max_workers=ANALYSIS_PIECE_CONCURRENCY,
            thread_name_prefix="protocol-piece-analysis",
        ) as pool:
            futures = {pool.submit(one, piece): piece for piece in pieces}
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                drain()
                for future in sorted(done, key=lambda item: futures[item].index):
                    piece = futures[future]
                    chunk = {"chunk_id": f"piece-{piece.index}", "ordinal": piece.index}
                    try:
                        draft = future.result()
                    except Exception as exc:  # noqa: BLE001 - the piece's result
                        failures[piece.index] = exc
                        failure: dict[str, object] = {
                            **chunk, "status": "failed", "attempt": 2,
                            "failure_code": _piece_failure_code(exc),
                        }
                        if isinstance(exc, ProtocolAnalysisEvidenceError):
                            failure["evidence_failure"] = exc.diagnostic.privacy_safe_dict()
                        append(f"piece-failed-{analysis_id}-{piece.index}",
                               _CHUNK_FAILED_EVENT, failure)
                        continue
                    drafts[piece.index] = draft
                    append(f"piece-completed-{analysis_id}-{piece.index}",
                           _CHUNK_COMPLETED_EVENT, {
                               **chunk, "status": "completed",
                               "steps": sum(
                                   len(section.steps) for section in draft.protocol.sections),
                           })
        failed = tuple(piece for piece in pieces if piece.index in failures)
        if len(failed) == len(pieces):
            raise ProtocolAnalysisPieceFailedError(
                "A piece of the document failed analysis after its retry.",
                pieces=failed,
                failure_codes=tuple(
                    _piece_failure_code(failures[piece.index]) for piece in failed),
            )
        # Lane RV, decision a: the pieces that passed are kept and joined;
        # each failed piece's pages wait for a person ("41–59쪽 분석 실패").
        passed = tuple(piece for piece in pieces if piece.index in drafts)
        append(f"pieces-joining-{analysis_id}", _MERGE_STARTED_EVENT, {"status": "in_progress"})
        try:
            return join_piece_analyses(
                extraction, passed, [drafts[piece.index] for piece in passed],
                review_unverified=True,
                failed_pieces=tuple(
                    (piece, _piece_failure_code(failures[piece.index])) for piece in failed),
            )
        except ProtocolAnalysisPieceConflictError as exc:
            append(f"pieces-conflict-{analysis_id}", _MERGE_CONFLICT_EVENT, {
                "status": "conflict", "failure_code": exc.code, "reason": exc.reason,
                "source_page_start": exc.first_page, "source_page_end": exc.last_page,
            })
            raise

    def _store_passed_analysis(
        self,
        revision: ProtocolRevisionRecord,
        draft: ProtocolAnalysisDraft,
        analysis_id: str,
        *,
        pieces: tuple[AnalysisPiece, ...] = (),
    ) -> ProtocolCatalogEntry:
        """Store a passed analysis as the revision's next analysis, and say so."""

        protocol_id = revision.experiment_id
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
        ready_payload: dict[str, object] = {
            "status": "analysis_ready",
            "analysis_payload_sha256": analysis.payload_sha256,
        }
        if draft.protocol.cleared_fields:
            # Lane EV2, decision 2: the fields emptied because the source
            # did not confirm them, kept in the ledger beside the analysis.
            ready_payload["cleared_fields"] = list(draft.protocol.cleared_fields)
        if pieces:
            # Lane AN, decision 2: read in these pieces and joined.
            ready_payload["pieces"] = [piece.public_dict() for piece in pieces]
        if draft.response_repair is not None:
            # Lane EV3, decision 1: the response was read after its brackets
            # were repaired in one of the two fixed shapes; every check above
            # ran on the repaired response like on any other.
            ready_payload["response_repaired"] = draft.response_repair.public_dict()
            logging.getLogger(__name__).info(
                "protocol.analysis.response_repaired protocol_id=%s shapes=%s brackets=%d",
                protocol_id, ",".join(draft.response_repair.shapes),
                draft.response_repair.bracket_count,
            )
        self.store.append_event(
            f"analysis-ready-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _ANALYSIS_READY_EVENT,
            ready_payload,
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
        entry = self.get_entry(protocol_id)
        self._analysis_ready(entry)
        return entry

    def _record_analysis_failure(
        self,
        revision: ProtocolRevisionRecord,
        analysis_id: str,
        exc: BaseException,
        *,
        truncated: bool = False,
    ) -> str:
        """Persist one attempt's bounded failure code; return the code.

        ``truncated`` marks an answer the provider cut at its output limit
        (lane AQ, decision 4), so the ledger tells that failure from one
        the model wrote whole.
        """

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
        if truncated:
            failure_payload["truncated"] = True
        repair = getattr(exc, "response_repair", None)
        if repair is not None:
            # Lane EV3, decision 1: this attempt was read after a repair of
            # its brackets and then failed a check.
            failure_payload["response_repaired"] = repair.public_dict()
        if isinstance(exc, ProtocolAnalysisPieceFailedError):
            # Lane AN, decision 2: the pieces that failed after their retry.
            failure_payload["failed_pieces"] = [
                {**piece.public_dict(), "failure_code": code}
                for piece, code in zip(exc.pieces, exc.failure_codes)
            ]
        if isinstance(exc, ProtocolAnalysisPieceConflictError):
            failure_payload["piece_conflict"] = {
                "reason": exc.reason, "source_page_start": exc.first_page,
                "source_page_end": exc.last_page,
            }
        if isinstance(exc, ProtocolAnalysisIncompleteError):
            # Lane EV2, decision 4: the two counts the refusal compared.
            failure_payload["step_counts"] = {
                "source_numbered_steps": exc.source_numbered_steps,
                "analysis_steps": exc.analysis_steps,
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
            revision.experiment_id,
            revision.revision_number,
            _ANALYSIS_FAILED_EVENT,
            failure_payload,
        )
        return failure_code

    def _automatic_retry_allowed(
        self,
        revision: ProtocolRevisionRecord,
        failure_code: str,
        *,
        repaired: bool = False,
    ) -> bool:
        """Whether this failure is sent again by itself (lane AN, decision 1).

        Mechanical: the failure is a response that broke the structure and no
        automatic retry was sent after the revision's latest analysis request
        (after none, when a caller analyses without recording one).

        Lane EV3, decision 5 (human decision 2026-10-12): a person's "다시
        분석" over a passed analysis (lane RN) is a run of its own and gets
        the same one retry.

        Lane AN (2026-10-13), EV4: so does a person's "분석 다시 시도" after a
        failed analysis -- each request is one run with one automatic retry.
        Before, only the revision's first request was retried, and the
        person's request whose first answer did not close as JSON had to be
        pressed again by hand.

        Lane EV3, decision 1: an answer read only after its brackets were
        repaired (``repaired``) and then refused by any check is sent again
        like the broken answer it was ("고쳐도 안 되면 지금처럼 자동
        재시도한다"); refused whole, an evidence failure still is not.
        """

        if failure_code not in AUTOMATIC_RETRY_FAILURE_CODES and not repaired:
            return False
        events = [
            event for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
        ]
        latest = max(
            (index for index, event in enumerate(events)
             if event.event_type == _ANALYSIS_REQUESTED_EVENT),
            default=-1,
        )
        return not any(
            event.event_type == _ANALYSIS_RETRY_EVENT for event in events[latest + 1:]
        )

    def request_analysis(
        self, protocol_id: str, analysis_id: str, *, reanalysis: bool = False,
    ) -> ProtocolCatalogEntry:
        """Persist an explicit analysis request without contacting a provider.

        A revision that holds a passed analysis is not asked again by a plain
        request (lane PA, decision 2: an upload never calls a passed analysis
        again). ``reanalysis`` is the person's "다시 분석" (lane RN, decision
        2): one more request event, naming the analysis it is asked over; a
        pass then becomes one more analysis revision beside the old one, and
        a failure leaves the old one as it is. The curated development
        fixture is never analysed again.
        """

        revision = self._latest_protocol_revision(protocol_id)
        previous = self._latest_analysis(revision)
        if previous is not None and not reanalysis:
            return self.get_entry(protocol_id)
        if not reanalysis and (
            self._review_pending_event(revision) is not None
            or self._review_versions(revision)
        ):
            # Lane RV, decisions a and d: an analysis waiting for a person, or
            # a registered review version, is a result; a plain request (an
            # upload of the same file) does not ask again. "다시 분석" does.
            return self.get_entry(protocol_id)
        if previous is not None and any(
            event.event_type == _DEVELOPMENT_FIXTURE_EVENT
            and event.protocol_revision_number == revision.revision_number
            for event in self.store.list_events(revision.experiment_id)
        ):
            raise ProtocolReanalysisRefusedError(
                "The curated development fixture is not analysed again."
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
        request_payload: dict[str, object] = {
            "status": "analysis_pending", "analysis_id": analysis_id,
        }
        if previous is not None:
            request_payload["reanalysis"] = True
            request_payload["previous_analysis_revision_number"] = (
                previous.analysis_revision_number)
            request_payload["previous_analysis_payload_sha256"] = previous.payload_sha256
        self.store.append_event(
            f"analysis-requested-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _ANALYSIS_REQUESTED_EVENT,
            request_payload,
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

    def load_executable_fixture(
        self, protocol_id: str
    ) -> CuratedProtocolFixture:
        revision = self._latest_protocol_revision(protocol_id)
        entry = self._entry_for_revision(revision)
        versions = self._review_versions(revision)
        if versions:
            # Lane RV, decision c: the latest registered review version runs.
            event, analysis = versions[-1]
            return self._fixture_for_analysis(
                revision, analysis, entry, status="analysis_passed",
                version=_version_payload(event))
        analysis = self._latest_analysis(revision)
        if analysis is None or not entry.available_for_execution:
            raise ProtocolCatalogUnavailableError(
                "Protocol analysis has not passed, or a page could not be read."
            )
        return self._fixture_for_analysis(
            revision, analysis, entry, status="analysis_passed"
        )

    def load_analysis_fixture(self, protocol_id: str) -> CuratedProtocolFixture:
        """The fixture a session *would* run for the latest passed analysis.

        For translation only (lane PX, decision 1): the same steps, facts,
        ``revision_id`` and ``fixture_sha256`` the executable fixture will
        carry, so the Korean made from it is the Korean the session shows --
        but marked ``analysis_draft``, and never handed to a session: no
        authority is read or granted here. Refuses while no analysis has
        passed.
        """

        revision = self._latest_protocol_revision(protocol_id)
        versions = self._review_versions(revision)
        if versions:
            # Lane RV, decision c: the version a session will run.
            event, analysis = versions[-1]
            return self._fixture_for_analysis(
                revision, analysis, self._entry_for_revision(revision),
                status="analysis_passed", version=_version_payload(event))
        analysis = self._latest_analysis(revision)
        if analysis is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol analysis is required before translation."
            )
        entry = self._entry_for_revision(revision)
        return self._fixture_for_analysis(
            revision, analysis, entry,
            status="analysis_passed" if entry.available_for_execution
            else "analysis_draft",
        )

    def _fixture_for_analysis(
        self,
        revision: ProtocolRevisionRecord,
        analysis: AnalysisRevisionRecord,
        entry: ProtocolCatalogEntry,
        *,
        status: str,
        version: dict[str, object] | None = None,
    ) -> CuratedProtocolFixture:
        protocol_id = revision.experiment_id
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
            protocol=protocol_with_display_step_labels(analysis.protocol),
            readiness=analysis.readiness,
            capability_policy=domain.P1_CAPABILITY_POLICY,
            analysis_schema_version=analysis.analysis_schema_version,
            verified_evidence_count=0,
            review_version=_review_version_info(version) if version else None,
        )
        labels = tuple(
            step.source_label
            for section in draft.protocol.sections
            for step in section.steps
        )
        timers = self._step_timer_table(
            revision, extraction, draft.protocol, version=version)
        return CuratedProtocolFixture(
            draft=draft,
            status=status,
            ordered_step_labels=labels,
            fixture_sha256=analysis.payload_sha256,
            revision_id=(
                str(version["revision_id"]) if version else entry.revision_id),
            development_only=entry.development_only,
            source_pdf_path=source,
            source_pdf_sha256=revision.pdf_checksum,
            source_filename=revision.original_filename,
            # The timers this analysis's own source supports (lane PT,
            # decision 1): the same step_id -> seconds shape the in-gel
            # sidecar loads into, plus the steps whose timer is a choice.
            timer_manifest=timers.manifest(),
            timer_choices=timers.choices() or None,
            timer_table=timers,
        )

    def _step_timer_table(
        self,
        revision: ProtocolRevisionRecord,
        extraction: ProtocolPdfExtraction,
        protocol: domain.ExperimentProtocol,
        *,
        version: dict[str, object] | None = None,
    ) -> domain.StepTimerTable:
        """Every time the analysis attached to a step, verified or refused.

        Checked against the text the analysis itself was checked against --
        the accepted OCR for a page that had none -- so a scanned page's time
        is found where its claims were found. A registered review version's
        timers (lane RV, decision c) replace a step's own: a time a reviewer
        entered or fixed runs as the reviewer's, and a step whose timer the
        reviewer left out has none.
        """

        try:
            source = self._extraction_for_analysis(revision, extraction)
        except ProtocolOcrReviewError:
            source = extraction
        table = verify_step_timers(protocol, source)
        timers = (version or {}).get("timers")
        if isinstance(timers, dict) and timers:
            table = _with_reviewer_timers(table, protocol, timers)
        return table

    # --- The review tab (lane RV, decision b) ---------------------------------

    def _review_target(self, revision: ProtocolRevisionRecord) -> _ReviewTarget | None:
        """The analysis a person reviews now: the latest of the analysis that
        passed, the one waiting for review, and (decision c) the latest
        registered review version. None before any, and for the curated
        development fixture, which is never reviewed."""

        events = tuple(
            event for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
        )
        if any(event.event_type == _DEVELOPMENT_FIXTURE_EVENT for event in events):
            return None
        latest = None
        for event in events:
            if event.event_type in {
                _ANALYSIS_READY_EVENT, _REVIEW_PENDING_EVENT, _REVIEW_VERSION_EVENT,
            }:
                latest = event
        if latest is None:
            return None
        if latest.event_type == _REVIEW_VERSION_EVENT:
            # Reviewed again: "다시 고치면 v2 가 되고 v1 기록은 남는다".
            payload = latest.payload if isinstance(latest.payload, dict) else {}
            analysis = self.store.get_analysis_revision(
                revision.experiment_id, revision.revision_number,
                latest.analysis_revision_number,
            )
            return _ReviewTarget(
                kind="review_version",
                key={"kind": "review_version", "version": payload.get("version")},
                protocol=analysis.protocol, readiness=analysis.readiness, flags=(),
                recorded_at=latest.recorded_at,
                revision_id=str(payload.get("revision_id")),
                analysis_id=analysis.analysis_id,
                origins=dict(payload.get("origins") or {}),
                timers=dict(payload.get("timers") or {}),
            )
        if latest.event_type == _REVIEW_PENDING_EVENT:
            protocol, readiness, flags, event = self.review_pending_analysis(
                revision.experiment_id)
            return _ReviewTarget(
                kind="review_pending",
                key={"kind": "review_pending", "event_id": event.event_id},
                protocol=protocol, readiness=readiness, flags=flags,
                recorded_at=event.recorded_at,
                analysis_id=str((event.payload or {}).get("analysis_id") or ""),
            )
        analysis = self.store.get_analysis_revision(
            revision.experiment_id, revision.revision_number,
            latest.analysis_revision_number,
        )
        return _ReviewTarget(
            kind="analysis",
            key={"kind": "analysis",
                 "analysis_revision_number": analysis.analysis_revision_number},
            protocol=analysis.protocol, readiness=analysis.readiness, flags=(),
            recorded_at=latest.recorded_at,
            revision_id=_revision_id(
                revision.revision_number, analysis.analysis_revision_number),
            analysis_id=analysis.analysis_id,
        )

    def _review_decisions(
        self, revision: ProtocolRevisionRecord, target: _ReviewTarget,
    ) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
        """The draft decisions on the target, latest per item, and the memos."""

        decisions: dict[str, dict[str, object]] = {}
        memos: dict[str, dict[str, object]] = {}
        for event in self.store.list_events(revision.experiment_id):
            if (
                event.event_type != _REVIEW_DECISION_EVENT
                or event.protocol_revision_number != revision.revision_number
                or not isinstance(event.payload, dict)
                or event.payload.get("target") != target.key
            ):
                continue
            decision = {**event.payload, "recorded_at": event.recorded_at,
                        "event_id": event.event_id}
            item_id = str(decision.get("item_id"))
            if decision.get("action") == "memo":
                memos[item_id] = decision
            elif decision.get("action") == "reset":
                decisions.pop(item_id, None)
            else:
                decisions[item_id] = decision
        return decisions, memos

    def _review_timer_table(
        self, revision: ProtocolRevisionRecord, target: _ReviewTarget,
    ) -> domain.StepTimerTable:
        extraction = target.protocol.metadata.pdf
        try:
            table = verify_step_timers(
                _safe_page_view(target.protocol, extraction), extraction)
        except (IndexError, ValueError):
            return domain.StepTimerTable()
        if target.timers:
            table = _with_reviewer_timers(table, target.protocol, target.timers)
        return table

    def _review_items(
        self,
        revision: ProtocolRevisionRecord,
        target: _ReviewTarget,
        decisions: dict[str, dict[str, object]],
        memos: dict[str, dict[str, object]],
        *,
        timer_steps: Sequence[str] = (),
    ) -> list[dict[str, object]]:
        """Every item of the target as the review screen shows it.

        A step's timer is an item where the step has a time (verified or
        refused) or a decision; ``timer_steps`` adds more steps' timers, so a
        reviewer may enter one where the source prints none.
        """

        protocol = target.protocol
        extraction = protocol.metadata.pdf
        flags_by_item: dict[str, list[ReviewFlag]] = {}
        for flag in target.flags:
            flags_by_item.setdefault(flag.item_id, []).append(flag)
        origins = target.origins or {}
        items: list[dict[str, object]] = []

        def view(item_id: str, kind: str, text: object, *, step=None, section=None,
                 evidence: domain.SourceEvidence | None = None,
                 evidence_item: str | None = None, extra: dict[str, object] | None = None,
                 ) -> dict[str, object]:
            own = flags_by_item.get(item_id, [])
            quote_refused = evidence_item is not None and any(
                flag.field == "evidence" for flag in flags_by_item.get(evidence_item, []))
            needs_review = bool(own)
            covered_by = (
                evidence_item if quote_refused and not needs_review and evidence_item != item_id
                else None)
            origin = origins.get(item_id) or (
                REVIEW_ORIGIN_AI if needs_review or covered_by else REVIEW_ORIGIN_SOURCE)
            source: dict[str, object] | None = None
            if evidence is not None:
                lines = None if quote_refused else _quote_lines(evidence, extraction)
                source = {
                    "page": evidence.source_page_number,
                    "excerpt": evidence.source_excerpt,
                    "continued_page": evidence.continued_on_page_number,
                    "continued_excerpt": evidence.continued_excerpt,
                    "verified": not quote_refused,
                    "lines": (
                        {"page": evidence.source_page_number, "first_line": lines[0],
                         "last_line": lines[1]} if lines else None),
                    "suggested_lines": (
                        _suggested_lines(
                            evidence.source_excerpt or str(text or ""), extraction,
                            (evidence.source_page_number,
                             *(page for flag in own for page in flag.matching_source_pages)))
                        if quote_refused and evidence_item == item_id else None),
                }
            allowed = ["memo", "reset"]
            if kind in _FIXABLE_KINDS:
                allowed[:0] = ["confirm", "fix"]
            elif kind in {"warning", "section", "metadata", "construct", "reminder",
                          "timer_spec", "pages"}:
                allowed[:0] = ["confirm"]
            elif kind == "repeat_count":
                allowed[:0] = (["confirm", "fix", "clear"] if text is not None
                               else ["enter"])
            elif kind == "missing_value":
                allowed[:0] = ["confirm", "enter"] if needs_review else ["enter"]
            elif kind == "timer":
                allowed[:0] = ["fix", "clear"] if text is not None else ["enter"]
            if needs_review and kind in _CLEARABLE_KINDS:
                allowed.insert(allowed.index("memo"), "clear")
            decision = decisions.get(item_id)
            current: object = text
            origin_after = origin
            if decision is not None:
                action = decision.get("action")
                value = decision.get("value") if isinstance(decision.get("value"), dict) else {}
                if action in {"fix", "enter"}:
                    current = value.get("text")
                    origin_after = REVIEW_ORIGIN_REVIEWER
                elif action == "clear":
                    current = None
            return {
                "item_id": item_id,
                "kind": kind,
                "kind_ko": REVIEW_ITEM_KIND_KO.get(kind, kind),
                "step_id": step.step_id if step is not None else None,
                "step_label": step.source_label if step is not None else None,
                "section_id": section.section_id if section is not None else None,
                "text": text,
                "value_kind_ko": (
                    value_kind_ko(str(text)) if kind in {"value", "duration", "timer"}
                    and text is not None else None),
                "source": source,
                "origin": origin,
                "origin_ko": REVIEW_ORIGIN_KO[origin],
                "needs_review": needs_review,
                "covered_by": covered_by,
                "reasons": [flag.public_dict() for flag in own],
                "allowed_actions": allowed,
                "decision": (
                    {key: decision.get(key) for key in (
                        "action", "value", "source_lines", "reviewer_name", "recorded_at")}
                    if decision is not None else None),
                "memo": memos[item_id].get("memo") if item_id in memos else None,
                "current": current,
                "origin_after": origin_after,
                "origin_after_ko": REVIEW_ORIGIN_KO[origin_after],
                "differs_from_original": bool(
                    decision is not None and decision.get("action") in {"fix", "enter", "clear"}),
                **(extra or {}),
            }

        listed: set[str] = set()
        if "metadata" in flags_by_item:
            items.append(view(
                "metadata", "metadata", protocol.metadata.title,
                evidence=protocol.metadata.evidence, evidence_item="metadata"))
            listed.add("metadata")
        for record in _review_records(protocol):
            value = record.record
            if record.kind == "repeat_count":
                text: object = value.repeat_count
            elif record.kind == "timer_spec":
                text = value.duration.source_text if value.duration is not None else None
            else:
                text = getattr(value, _REVIEW_TEXT_FIELD[type(value)])
            items.append(view(
                record.item_id, record.kind, text, step=record.step, section=record.section,
                evidence=record.evidence, evidence_item=record.evidence_item_id))
            listed.add(record.item_id)
        steps = {step.step_id: step for section in protocol.sections for step in section.steps}
        table = self._review_timer_table(revision, target)
        timer_steps = list(dict.fromkeys(
            [timer.step_id for timer in table.verified]
            + [time.step_id for time in table.refused if time.step_id]
            + [str(item_id)[len("timer:"):] for item_id in decisions
               if str(item_id).startswith("timer:")]
            + list(timer_steps)
        ))
        for step_id in timer_steps:
            step = steps.get(step_id)
            if step is None:
                continue
            verified = table.for_step(step_id)
            seconds = sorted({value for timer in verified for value in timer.seconds})
            items.append(view(
                f"timer:{step_id}", "timer",
                " · ".join(dict.fromkeys(timer.literal for timer in verified)) or None,
                step=step, evidence=step.evidence, evidence_item=None,
                extra={
                    "seconds": seconds,
                    "candidates": [
                        {"literal": time.literal, "excerpt": time.excerpt,
                         "reason": time.reason, "reason_ko": time.reason_ko,
                         "page": time.page_number}
                        for time in table.refused if time.step_id == step_id
                    ],
                }))
            listed.add(f"timer:{step_id}")
        for item_id, flags in flags_by_item.items():
            if item_id in listed:
                continue
            flag = flags[0]
            items.append(view(
                item_id, "pages" if flag.field == "pages" else "construct",
                flag.reason_ko,
                extra={"first_page": flag.first_page, "last_page": flag.last_page}))
        return items

    def review_draft(self, protocol_id: str) -> dict[str, object]:
        """What the review screen shows (lane RV, decision b): the target, its
        items with their source lines, origins and draft decisions, and
        whether it can be registered now."""

        revision = self._latest_protocol_revision(protocol_id)
        entry = self._entry_for_revision(revision)
        base: dict[str, object] = {
            "protocol_id": protocol_id,
            "title": entry.title,
            "source_filename": entry.source_filename,
            "analysis_status": entry.analysis_status,
            "lifecycle_state": entry.lifecycle_state,
            "available_for_execution": entry.available_for_execution,
            "review_pending": entry.review_pending,
            "target": None,
            "items": [],
            "needs_review": [],
            "counts": {"items": 0, "needs_review": 0, "decided": 0, "undecided": 0,
                       "changed": 0},
            "pages": [],
            "registrable": False,
            "registration_blockers": [],
        }
        target = self._review_target(revision)
        if target is None:
            base["message_ko"] = (
                "개발용 고정 프로토콜은 검토하지 않습니다."
                if entry.development_only else "검토할 분석이 아직 없습니다.")
            return base
        decisions, memos = self._review_decisions(revision, target)
        items = self._review_items(revision, target, decisions, memos)
        pending = [
            item["item_id"] for item in items
            if item["needs_review"] and item["item_id"] not in decisions
        ]
        base.update({
            "target": {
                **target.key, "recorded_at": target.recorded_at,
                "revision_id": target.revision_id, "analysis_id": target.analysis_id,
            },
            "items": items,
            "needs_review": [item["item_id"] for item in items if item["needs_review"]],
            "counts": {
                "items": len(items),
                "needs_review": sum(1 for item in items if item["needs_review"]),
                "decided": len(decisions),
                "undecided": len(pending),
                "changed": sum(1 for item in items if item["differs_from_original"]),
            },
            "pages": self._review_pages(revision, target),
            "registration_blockers": [
                {"item_id": item_id, "message_ko": "확인 필요 항목을 정하지 않았습니다."}
                for item_id in pending
            ],
            "registrable": not pending,
        })
        return base

    def _review_pages(
        self, revision: ProtocolRevisionRecord, target: _ReviewTarget,
    ) -> list[dict[str, object]]:
        extraction = target.protocol.metadata.pdf
        ocr_pages = self._ocr_pages(revision)
        pages = []
        for page in extraction.pages:
            ocr = ocr_pages.get(page.source_page_number) or {}
            lines = self._line_views(page, ocr)
            pages.append({
                "page": page.source_page_number,
                "ocr": page.ocr_derived,
                "ocr_unclear": _ocr_page_unclear(page, ocr),
                "confidence": ocr.get("confidence") if page.ocr_derived else None,
                "line_count": len(lines),
                "value_line_count": sum(1 for line in lines if line["has_value"]),
                "unclear_line_count": sum(1 for line in lines if line["unclear"]),
                "value_box_count": len(ocr.get("value_boxes") or ()) if page.ocr_derived else 0,
            })
        return pages

    def _ocr_pages(self, revision: ProtocolRevisionRecord) -> dict[int, dict[str, object]]:
        projection = self._ocr_projection(
            revision, self._source_extraction(revision), include_text=True)
        if projection.get("accepted_for_analysis") is not True:
            return {}
        return {
            page["source_page_number"]: page for page in projection.get("pages") or ()
            if isinstance(page, dict) and isinstance(page.get("source_page_number"), int)
        }

    @staticmethod
    def _line_views(page: ProtocolPdfPage, ocr: dict[str, object]) -> list[dict[str, object]]:
        unclear_page = _ocr_page_unclear(page, ocr)
        views = []
        for number, (_, _, text) in enumerate(_page_lines(page.text), start=1):
            has_value = bool(re.search(r"[0-9]", text))
            views.append({
                "number": number, "text": text, "has_value": has_value,
                "unclear": unclear_page and has_value,
            })
        return views

    def source_pdf(self, protocol_id: str) -> tuple[Path, str]:
        """The stored source PDF of the latest protocol revision and its checksum."""

        revision = self._latest_protocol_revision(protocol_id)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError("Protocol source object is unavailable.")
        return (
            self.store.file_store.object_path(
                revision.pdf_checksum, expected_size=pdf_object.byte_size),
            revision.pdf_checksum,
        )

    def review_source_page(self, protocol_id: str, page_number: int) -> dict[str, object]:
        """One page of the source the target was read from, line by line, with
        line numbers, and the OCR's value boxes (lane RV, decision b; UI2's
        "원문 쪽 전체 줄과 줄 번호", "OCR 흐린 줄 수")."""

        revision = self._latest_protocol_revision(protocol_id)
        target = self._review_target(revision)
        if target is not None:
            extraction = target.protocol.metadata.pdf
        else:
            extraction = self._extraction_for_analysis(
                revision, self._source_extraction(revision))
        if not isinstance(page_number, int) or not 1 <= page_number <= extraction.page_count:
            raise ProtocolCatalogNotFoundError("Protocol source page is unknown.")
        page = extraction.pages[page_number - 1]
        ocr = self._ocr_pages(revision).get(page_number) or {}
        lines = self._line_views(page, ocr)
        boxes = ocr.get("value_boxes") if page.ocr_derived else None
        return {
            "protocol_id": protocol_id,
            "page": page_number,
            "page_count": extraction.page_count,
            "ocr": page.ocr_derived,
            "ocr_unclear": _ocr_page_unclear(page, ocr),
            "confidence": ocr.get("confidence") if page.ocr_derived else None,
            "numeric_review_required": bool(ocr.get("numeric_review_required"))
            if page.ocr_derived else False,
            "lines": lines,
            "unclear_line_count": sum(1 for line in lines if line["unclear"]),
            "render_dpi": ocr.get("render_dpi") if page.ocr_derived else None,
            "value_boxes": [
                {"index": index, "text": box.get("text"), "box": box.get("box")}
                for index, box in enumerate(boxes or ())
                if isinstance(box, dict)
            ],
        }

    def record_review_decision(
        self, protocol_id: str, request: dict[str, object],
    ) -> dict[str, object]:
        """Record one decision on one item of the target (lane RV, decision b).

        One more ledger event: the item, the action, the value, the source
        lines a person pointed at, the original value and origin, and the
        reviewer's name when given. Nothing is changed but the draft; the
        registration (decision c) makes the version that runs.
        """

        if not isinstance(request, dict):
            raise ProtocolReviewError("A review decision must be an object.")
        allowed_keys = {"item_id", "action", "text", "seconds", "count", "source_lines",
                        "memo", "reviewer_name", "target"}
        if set(request) - allowed_keys:
            raise ProtocolReviewError("A review decision holds an unknown field.")
        revision = self._latest_protocol_revision(protocol_id)
        target = self._review_target(revision)
        if target is None:
            raise ProtocolReviewNotReadyError("There is no analysis to review.")
        if request.get("target") is not None and not _same_target(request["target"], target.key):
            raise ProtocolReviewTargetChangedError(
                "The analysis under review changed; read the review again.")
        decisions, memos = self._review_decisions(revision, target)
        item_id = request.get("item_id")
        items = {item["item_id"]: item for item in self._review_items(
            revision, target, decisions, memos,
            timer_steps=(
                (item_id[len("timer:"):],)
                if isinstance(item_id, str) and item_id.startswith("timer:") else ()),
        )}
        if not isinstance(item_id, str) or item_id not in items:
            raise ProtocolReviewError("The review has no such item.")
        item = items[item_id]
        action = request.get("action")
        if action not in REVIEW_ACTIONS or action not in item["allowed_actions"]:
            raise ProtocolReviewError("This action is not allowed on this item.")
        value: dict[str, object] = {}
        if action in {"fix", "enter"}:
            if item["kind"] == "timer":
                seconds = request.get("seconds")
                if (not isinstance(seconds, int) or isinstance(seconds, bool)
                        or not 1 <= seconds <= _MAX_TIMER_SECONDS):
                    raise ProtocolReviewError("A timer needs whole seconds.")
                value = {"seconds": seconds, "text": _duration_words(seconds)}
            elif item["kind"] == "repeat_count":
                count = request.get("count")
                if (not isinstance(count, int) or isinstance(count, bool)
                        or not 1 <= count <= _MAX_REPEAT_COUNT):
                    raise ProtocolReviewError("A repeat count needs a whole number.")
                value = {"count": count, "text": f"{count}회"}
            else:
                limit = (_MAX_REVIEW_VALUE if item["kind"] in {"value", "duration", "missing_value"}
                         else _MAX_REVIEW_TEXT)
                value = {"text": _clean_review_text(request.get("text"), limit=limit,
                                                    label="Reviewed text")}
                if action == "fix" and value["text"] == item["text"]:
                    raise ProtocolReviewError("A fix must change the text; confirm it instead.")
        source_lines = None
        source = item.get("source") if isinstance(item.get("source"), dict) else None
        if action in {"confirm", "fix"} and item["needs_review"] and source is not None \
                and source.get("verified") is False:
            # The quote was not found: a person points at where the item is.
            evidence = source_lines_excerpt(target.protocol.metadata.pdf,
                                            request.get("source_lines"))
            source_lines = {**request["source_lines"], "excerpt": evidence.source_excerpt}
        elif request.get("source_lines") is not None:
            raise ProtocolReviewError("Source lines are asked only for an item whose quote was not found.")
        memo = None
        if action == "memo":
            memo = _clean_review_text(request.get("memo"), limit=_MAX_REVIEW_MEMO, label="Memo")
        elif request.get("memo") is not None:
            raise ProtocolReviewError("A memo is its own action.")
        name = request.get("reviewer_name")
        payload: dict[str, object] = {
            "target": target.key,
            "item_id": item_id,
            "kind": item["kind"],
            "action": action,
            "value": value or None,
            "source_lines": source_lines,
            "memo": memo,
            "reviewer_name": reviewer_name(name) if name is not None else None,
            "original": {
                "text": item["text"],
                "origin": item["origin"],
                **({"seconds": item.get("seconds")} if item["kind"] == "timer" else {}),
            },
        }
        self.store.append_event(
            f"review-decision-{secrets.token_hex(16)}",
            protocol_id,
            revision.revision_number,
            _REVIEW_DECISION_EVENT,
            payload,
        )
        draft = self.review_draft(protocol_id)
        return {
            "item": next(entry for entry in draft["items"] if entry["item_id"] == item_id),
            "counts": draft["counts"],
            "registrable": draft["registrable"],
            "target": draft["target"],
        }

    def register_review(
        self, protocol_id: str, request: dict[str, object],
    ) -> dict[str, object]:
        """"검토 완료하고 등록": the review becomes version N (lane RV, decision c).

        Human decision 2026-10-12: the reviewer's name is required; every
        item that waited for a person must be decided; the version is one
        more analysis revision -- the analysis it was reviewed from, the
        versions before it and the source stay as they are -- and the
        registration event beside it holds who registered it, what it was
        reviewed from and every change with its original value. A version
        whose protocol would carry an execution blocker is not registered.
        The latest version is the one that runs (decision d).
        """

        if not isinstance(request, dict) or set(request) - {"reviewer_name", "target"}:
            raise ProtocolReviewError("A registration names the reviewer and the target.")
        name = reviewer_name(request.get("reviewer_name"))
        revision = self._latest_protocol_revision(protocol_id)
        target = self._review_target(revision)
        if target is None:
            raise ProtocolReviewNotReadyError("There is no analysis to review.")
        if request.get("target") is not None and not _same_target(request["target"], target.key):
            raise ProtocolReviewTargetChangedError(
                "The analysis under review changed; read the review again.")
        decisions, memos = self._review_decisions(revision, target)
        items = self._review_items(revision, target, decisions, memos)
        pending = [
            item["item_id"] for item in items
            if item["needs_review"] and item["item_id"] not in decisions
        ]
        if pending:
            raise ProtocolReviewItemsPendingError(
                "Every item that waits for a person must be decided first.",
                item_ids=pending)
        protocol, changes, timers, origins = _apply_review(target, items, decisions, name)
        try:
            domain.validate_protocol(protocol)
        except domain.ProtocolValidationError as exc:
            raise ProtocolReviewNotReadyError(
                "The reviewed protocol does not hold against its source.") from exc
        readiness = domain.assess_readiness(protocol, capability_policy=domain.P1_CAPABILITY_POLICY)
        blockers = domain.execution_blocking_reasons(readiness)
        if blockers:
            raise ProtocolReviewBlockedError(
                "The reviewed protocol would not run.",
                codes=[reason.code.value for reason in blockers])
        version = len(self._review_versions(revision)) + 1
        analysis_id = f"{REVIEW_VERSION_ANALYSIS_PREFIX}{version}-{secrets.token_hex(12)}"
        analysis = self.store.append_analysis_revision(
            protocol_id, revision.revision_number, analysis_id, protocol, readiness,
            domain.P1_CAPABILITY_POLICY.profile_id,
        )
        revision_id = f"pdf-{revision.revision_number}-review-{version}"
        payload: dict[str, object] = {
            "status": "review_registered",
            "version": version,
            "revision_id": revision_id,
            "reviewer_name": name,
            "base": target.key,
            "base_revision_id": target.revision_id,
            "base_analysis_id": target.analysis_id,
            "analysis_id": analysis_id,
            "analysis_payload_sha256": analysis.payload_sha256,
            "changes": [change.public_dict() for change in changes],
            "timers": timers,
            "origins": origins,
            "memos": {
                item_id: memo.get("memo") for item_id, memo in memos.items()
                if isinstance(memo.get("memo"), str)
            },
            "decision_event_ids": [str(decision["event_id"]) for decision in decisions.values()],
        }
        event = self.store.append_event(
            f"review-version-{analysis_id}",
            protocol_id,
            revision.revision_number,
            _REVIEW_VERSION_EVENT,
            payload,
            analysis_revision_number=analysis.analysis_revision_number,
        )
        logging.getLogger(__name__).info(
            "protocol.review.registered protocol_id=%s version=%d changes=%d",
            protocol_id, version, len(changes),
        )
        if self.on_analysis_ready is not None:
            # The version's Korean, as for a passed analysis (lane PX, 1).
            try:
                self.on_analysis_ready(self, protocol_id)
            except Exception as exc:  # noqa: BLE001 - see on_analysis_ready
                logging.getLogger(__name__).warning(
                    "protocol.review.translation_start_failed error=%s", type(exc).__name__)
        return {
            "protocol": self.get_entry(protocol_id).public_dict(),
            "review_version": {
                **_review_version_summary(event),
                "changes": payload["changes"],
            },
        }

    def review_version(self, protocol_id: str, version: int) -> dict[str, object]:
        """One registered version with its changes against what it was
        reviewed from (lane RV, decision c; the review tab's v1→v2)."""

        revision = self._latest_protocol_revision(protocol_id)
        for event, _ in self._review_versions(revision):
            if event.payload.get("version") == version:
                return {
                    **_review_version_summary(event),
                    "changes": list(event.payload.get("changes") or ()),
                    "memos": dict(event.payload.get("memos") or {}),
                    "timers": dict(event.payload.get("timers") or {}),
                }
        raise ProtocolCatalogNotFoundError("The protocol has no such review version.")

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


# ---------------------------------------------------------------------------
# The review tab (lane RV, decisions b-d; human decisions 2026-10-12 and the
# review scope recommended 2026-10-14)
# ---------------------------------------------------------------------------
#
# A person reviews one analysis -- one waiting for review, one that passed, or
# the latest registered review version -- item by item: confirm it, fix it (a
# value, a unit, a typo), leave it empty because the source does not hold it,
# or enter a value the source does not print. Steps are not added or removed
# and a safety statement's words are not changed (a memo is); a value a
# person wrote is shown as "검토자 입력", one the analysis or the OCR's second
# reading produced as "AI 제안", and an AI suggestion becomes something that
# runs only when a person confirms it. Every decision is one more ledger
# event; the source and the analysis are never changed.

REVIEW_ORIGIN_SOURCE = "source"
REVIEW_ORIGIN_AI = "ai_suggestion"
REVIEW_ORIGIN_REVIEWER = "reviewer_input"
REVIEW_ORIGIN_KO: dict[str, str] = {
    REVIEW_ORIGIN_SOURCE: "원문",
    REVIEW_ORIGIN_AI: "AI 제안",
    REVIEW_ORIGIN_REVIEWER: "검토자 입력",
}
REVIEW_ITEM_KIND_KO: dict[str, str] = {
    "metadata": "문서 정보",
    "section": "절 제목",
    "step": "단계",
    "sub_action": "세부 동작",
    "warning": "안전 주의",
    "note": "참고",
    "tip": "팁",
    "expected_result": "완료 기준",
    "observation": "완료 기준(관찰)",
    "condition": "조건",
    "material": "재료",
    "equipment": "장비",
    "prerequisite": "시작 전 준비",
    "value": "값",
    "duration": "시간",
    "timer_spec": "타이머 정보",
    "reminder": "알림",
    "missing_value": "원문에 없는 값",
    "construct": "구조",
    "repeat_count": "반복 횟수",
    "timer": "단계 타이머",
    "pages": "분석 실패 쪽",
}
#: The review's actions: confirm, fix, leave empty (원문에 없음), enter a value
#: the source does not print, a memo beside an item, and taking a draft
#: decision back.
REVIEW_ACTIONS: tuple[str, ...] = ("confirm", "fix", "clear", "enter", "memo", "reset")
REVIEW_ACTION_KO: dict[str, str] = {
    "confirm": "확인", "fix": "고침", "clear": "원문에 없음으로 비워 둠",
    "enter": "입력", "memo": "메모", "reset": "되돌리기",
}
_STATEMENT_KINDS = {
    "warnings": "warning", "notes": "note", "tips": "tip",
    "expected_results": "expected_result", "conditions": "condition",
}
#: The text field a person reads (and may fix) on each record.
_REVIEW_TEXT_FIELD: dict[type, str] = {
    domain.ProtocolSection: "title_source_text",
    domain.ProtocolSourceStep: "instruction_source_text",
    domain.ProtocolSubAction: "instruction_source_text",
    domain.SourceStatement: "source_text",
    domain.RequiredObservation: "source_text",
    domain.Material: "name_source_text",
    domain.Equipment: "name_source_text",
    domain.BeforeStartPrerequisite: "source_text",
    domain.ScientificValue: "source_text",
    domain.EstimatedDuration: "source_text",
    domain.MissingExecutionValue: "description",
    domain.ProcessTimerSpecification: "timer_id",
    domain.OneTimeReminder: "message_source_text",
    domain.RecurringReminder: "message_source_text",
    domain.ConditionalBranch: "condition_source_text",
    domain.FixedRangeRepetition: "range_source_text",
    domain.OperatorDeterminedRepetition: "range_source_text",
    domain.RepeatUntil: "condition_source_text",
    domain.ParallelWork: "source_text",
    domain.RecurringAction: "source_text",
    domain.ReusableSubprocedure: "source_text",
    domain.SourceAmbiguity: "source_text",
    domain.ProtocolConflict: "source_text",
}
#: The kinds whose words a person may fix: values, units and typos in the
#: step sentences and the statements beside them (review scope, 2026-10-14).
#: A safety statement's words are not changed, nor a structure's.
_FIXABLE_KINDS = frozenset({
    "step", "sub_action", "note", "tip", "expected_result", "observation",
    "condition", "material", "equipment", "prerequisite", "value", "duration",
})
#: The kinds a person may leave empty because the source does not hold them --
#: only while flagged: a quote the server found is the source's own.
_CLEARABLE_KINDS = frozenset({
    "warning", "note", "tip", "expected_result", "observation", "condition",
    "material", "equipment", "prerequisite", "value", "duration", "reminder",
})
_MAX_REVIEW_TEXT = 2000
_MAX_REVIEW_VALUE = 200
_MAX_REVIEW_MEMO = 1000
_MAX_REVIEWER_NAME = 80
_MAX_TIMER_SECONDS = 7 * 24 * 3600
_MAX_REPEAT_COUNT = 999

#: What kind of quantity a value is, read from its unit (UI2's "수치 종류 이름").
_VALUE_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("온도", re.compile(r"°\s*[CF]\b|℃|(?<![A-Za-z])[0-9.]+\s*C\b|도\b", re.I)),
    ("원심·회전", re.compile(r"\brpm\b|[x×]\s*g\b|\bRCF\b", re.I)),
    ("시간", re.compile(
        r"\b(?:h|hr|hrs|hours?|min|mins|minutes?|s|sec|secs|seconds?|days?|overnight)\b"
        r"|분|시간|초|일\b|밤새|[0-9]{1,2}:[0-9]{2}", re.I)),
    ("부피", re.compile(r"(?:[µμu]|m|n)?L\b|(?:[µμu]|m)l\b|ml\b", re.I)),
    ("농도", re.compile(r"(?<![A-Za-z])(?:[µμunm]?M)\b|%|\bw/v\b|\bv/v\b|/\s*m?L\b|\bU/", re.I)),
    ("질량", re.compile(r"(?<![A-Za-z])(?:[µμunmk]?g)\b|\bkDa\b", re.I)),
    ("횟수", re.compile(r"\b(?:times?|cycles?|rounds?|washes)\b|회\b|번\b|[0-9]\s*[x×]\b", re.I)),
    ("길이", re.compile(r"(?<![A-Za-z])(?:[µμn]?m|mm|cm)\b", re.I)),
    ("전압·전류", re.compile(r"(?<![A-Za-z])(?:[mk]?V|[mµμ]?A)\b")),
)


def value_kind_ko(text: str | None) -> str | None:
    """"온도", "시간", "부피", ... for a value's words, or None when there is no number."""

    if not isinstance(text, str) or not re.search(r"[0-9]", text):
        return None
    for name, pattern in _VALUE_KINDS:
        if pattern.search(text):
            return name
    return "수치"


def _clean_review_text(value: object, *, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise ProtocolReviewError(f"{label} must be text.")
    cleaned = " ".join(value.split()) if "\n" not in value else "\n".join(
        " ".join(line.split()) for line in value.splitlines()).strip()
    if not cleaned or len(cleaned) > limit or any(
        ord(character) < 32 and character != "\n" for character in cleaned
    ):
        raise ProtocolReviewError(f"{label} is empty, too long or holds control characters.")
    return cleaned


def reviewer_name(value: object) -> str:
    """The reviewer's name as written, required (human decision 2026-10-12)."""

    if not isinstance(value, str) or not value.strip():
        raise ProtocolReviewNameRequiredError("A reviewer name is required.")
    return _clean_review_text(value, limit=_MAX_REVIEWER_NAME, label="Reviewer name")


def _duration_words(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours}시간")
    if minutes:
        parts.append(f"{minutes}분")
    if secs or not parts:
        parts.append(f"{secs}초")
    return " ".join(parts)


@dataclass(frozen=True)
class _ReviewRecord:
    """One reviewable record found walking a protocol."""

    item_id: str
    kind: str
    record: Any
    path: str
    step: domain.ProtocolSourceStep | None
    section: domain.ProtocolSection | None
    evidence: domain.SourceEvidence | None
    evidence_item_id: str | None


def _review_records(protocol: domain.ExperimentProtocol) -> list[_ReviewRecord]:
    """Every record a person reviews, in document order."""

    found: list[_ReviewRecord] = []
    item_ids: dict[int, str] = {}

    def visit(value: Any, path: str, *, field_name: str, step, section, evidence, evidence_item):
        if isinstance(value, tuple):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]", field_name=field_name, step=step,
                      section=section, evidence=evidence, evidence_item=evidence_item)
            return
        if not is_dataclass(value) or isinstance(
            value, (domain.SourceEvidence, ProtocolPdfExtraction, domain.DependencyTarget,
                    domain.ActualElapsedTime)
        ):
            return
        kind_name = REVIEW_ITEM_KINDS.get(type(value))
        if type(value) in _REVIEW_TEXT_FIELD:
            item_id = review_item_id(protocol, f"{path}.x")
            if isinstance(value, domain.ProtocolSourceStep):
                step = value
                kind = "step"
            elif isinstance(value, domain.ProtocolSection):
                section = value
                kind = "section"
            elif isinstance(value, domain.ProtocolSubAction):
                kind = "sub_action"
            elif isinstance(value, domain.SourceStatement):
                kind = _STATEMENT_KINDS.get(field_name, "note")
            elif isinstance(value, domain.ScientificValue):
                kind = "value"
            elif isinstance(value, domain.EstimatedDuration):
                kind = "duration"
            else:
                kind = kind_name[0] if kind_name else "construct"
            own = getattr(value, "evidence", None)
            if isinstance(own, domain.SourceEvidence):
                evidence, evidence_item = own, item_id
            found.append(_ReviewRecord(
                item_id, kind, value, path, step, section, evidence, evidence_item))
            if isinstance(value, domain.FixedRangeRepetition):
                found.append(_ReviewRecord(
                    f"{item_id}/repeat_count", "repeat_count", value, path, step, section,
                    evidence, evidence_item))
        for field in fields(value):
            visit(getattr(value, field.name), f"{path}.{field.name}", field_name=field.name,
                  step=step, section=section, evidence=evidence, evidence_item=evidence_item)

    for name in ("before_start", "materials", "equipment", "sections", "constructs"):
        visit(getattr(protocol, name), f"protocol.{name}", field_name=name, step=None,
              section=None, evidence=None, evidence_item=None)
    del item_ids
    return found


def _page_lines(text: str) -> list[tuple[int, int, str]]:
    """(start, end, text) of each line of a page, numbered from 1 by position."""

    lines: list[tuple[int, int, str]] = []
    start = 0
    for line in text.split("\n"):
        lines.append((start, start + len(line), line))
        start += len(line) + 1
    return lines


def _lines_of_span(text: str, start: int, end: int) -> tuple[int, int]:
    lines = _page_lines(text)
    first = next(index for index, (s, e, _) in enumerate(lines, 1) if e >= start)
    last = next(index for index, (s, e, _) in enumerate(lines, 1) if e >= max(start, end - 1))
    return first, last


def _quote_lines(evidence: domain.SourceEvidence, extraction: ProtocolPdfExtraction) -> tuple[int, int] | None:
    page_number = evidence.source_page_number
    if not isinstance(page_number, int) or not 1 <= page_number <= extraction.page_count:
        return None
    text = extraction.pages[page_number - 1].text
    found = text.find(evidence.source_excerpt)
    if found < 0:
        return None
    return _lines_of_span(text, found, found + len(evidence.source_excerpt))


def _suggested_lines(
    words: str, extraction: ProtocolPdfExtraction, pages: Sequence[int],
) -> dict[str, object] | None:
    """The source lines most like ``words`` on these pages: an AI suggestion
    of where an item comes from, for a person to accept or change."""

    target = " ".join(words.split()).casefold()
    if not target:
        return None
    best: tuple[float, int, int, int] | None = None
    for page_number in dict.fromkeys(pages):
        if not isinstance(page_number, int) or not 1 <= page_number <= extraction.page_count:
            continue
        lines = _page_lines(extraction.pages[page_number - 1].text)
        for first in range(len(lines)):
            joined = ""
            for last in range(first, min(first + 4, len(lines))):
                joined = f"{joined} {lines[last][2]}".strip()
                if not joined:
                    continue
                ratio = SequenceMatcher(None, target, " ".join(joined.split()).casefold(),
                                        autojunk=False).ratio()
                if best is None or ratio > best[0]:
                    best = (ratio, page_number, first + 1, last + 1)
    if best is None or best[0] < 0.6:
        return None
    return {"page": best[1], "first_line": best[2], "last_line": best[3],
            "similarity": round(best[0], 2)}


def source_lines_excerpt(
    extraction: ProtocolPdfExtraction, source_lines: object,
) -> domain.SourceEvidence:
    """The quote a person pointed at: page and lines, as the page prints them."""

    if not isinstance(source_lines, dict):
        raise ProtocolReviewError("Source lines must name a page and its lines.")
    page = source_lines.get("page")
    first = source_lines.get("first_line")
    last = source_lines.get("last_line", first)
    if not all(isinstance(value, int) and not isinstance(value, bool)
               for value in (page, first, last)):
        raise ProtocolReviewError("Source lines must be numbers.")
    if not 1 <= page <= extraction.page_count:
        raise ProtocolReviewError("The source has no such page.")
    text = extraction.pages[page - 1].text
    lines = _page_lines(text)
    if not 1 <= first <= last <= len(lines) or last - first > 20:
        raise ProtocolReviewError("The page has no such lines.")
    start, end = lines[first - 1][0], lines[last - 1][1]
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        raise ProtocolReviewError("The lines pointed at hold no text.")
    return domain.SourceEvidence(source_page_number=page, source_excerpt=text[start:end])


class ProtocolReviewError(ProtocolCatalogError):
    """A review decision the server does not take (lane RV, decision b)."""

    code = "review_decision_invalid"


class ProtocolReviewNameRequiredError(ProtocolReviewError):
    code = "reviewer_name_required"


class ProtocolReviewNotReadyError(ProtocolCatalogError):
    """Nothing to review yet, or the items are not all decided (lane RV, c)."""

    code = "review_not_ready"


class ProtocolReviewItemsPendingError(ProtocolReviewNotReadyError):
    code = "review_items_pending"

    def __init__(self, message: str, *, item_ids: Sequence[str]) -> None:
        super().__init__(message)
        self.item_ids = tuple(item_ids)


_REVIEW_DECISION_EVENT = "protocol_review_decision_recorded"


@dataclass(frozen=True)
class _ReviewTarget:
    """The analysis a person reviews now (lane RV, decision b)."""

    kind: str
    key: dict[str, object]
    protocol: domain.ExperimentProtocol
    readiness: domain.ReadinessAssessment
    flags: tuple[ReviewFlag, ...]
    recorded_at: str
    revision_id: str | None = None
    analysis_id: str | None = None
    #: Item id -> origin carried from the review version reviewed again.
    origins: dict[str, str] | None = None
    #: Step id -> the reviewer's timer seconds (None: no timer) of that version.
    timers: dict[str, int | None] | None = None


def _safe_page_view(
    protocol: domain.ExperimentProtocol, extraction: ProtocolPdfExtraction,
) -> domain.ExperimentProtocol:
    """``protocol`` with each quote that cites no page of the source pointed
    at page 1, so a step's timers can be read; for reading only."""

    replacements = {
        path: replace(evidence, source_page_number=1, continued_on_page_number=None,
                      continued_excerpt=None)
        for path, evidence in _evidence_paths(protocol)
        if not isinstance(evidence.source_page_number, int)
        or not 1 <= evidence.source_page_number <= extraction.page_count
        or (evidence.continued_on_page_number is not None
            and not 1 <= evidence.continued_on_page_number <= extraction.page_count)
    }
    return replace_at_paths(protocol, replacements) if replacements else protocol


def _evidence_paths(value: Any, path: str = "protocol"):
    if isinstance(value, domain.SourceEvidence):
        yield path, value
        return
    if isinstance(value, (ProtocolPdfExtraction, Enum, str)) or value is None:
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            yield from _evidence_paths(item, f"{path}[{index}]")
        return
    if is_dataclass(value):
        for field in fields(value):
            yield from _evidence_paths(getattr(value, field.name), f"{path}.{field.name}")


class ProtocolReviewTargetChangedError(ProtocolReviewError):
    code = "review_target_changed"


def _same_target(seen: object, key: dict[str, object]) -> bool:
    """Whether the target a screen read is the one under review now."""

    return isinstance(seen, dict) and all(seen.get(name) == value for name, value in key.items())


#: An OCR page whose reading a person should look at: the two engines read
#: different numbers or units, or the engine was not sure of the page.
OCR_UNCLEAR_CONFIDENCE = 0.8


def _ocr_page_unclear(page: ProtocolPdfPage, ocr: dict[str, object]) -> bool:
    if not page.ocr_derived:
        return False
    confidence = ocr.get("confidence")
    return bool(ocr.get("numeric_review_required")) or (
        isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
        and confidence < OCR_UNCLEAR_CONFIDENCE
    )


def _with_reviewer_timers(
    table: domain.StepTimerTable,
    protocol: domain.ExperimentProtocol,
    timers: dict[str, object],
) -> domain.StepTimerTable:
    """``table`` with a review version's timers in place of the steps' own.

    A step a reviewer gave a time runs that time, said as the reviewer's
    (``source="reviewer_input"``); a step whose timer the reviewer left out
    has none. The source's refused times at those steps are dropped: the
    reviewer has decided them.
    """

    steps = {step.step_id: step for section in protocol.sections for step in section.steps}
    decided = {step_id for step_id in timers if step_id in steps}
    verified = [timer for timer in table.verified if timer.step_id not in decided]
    for step_id in decided:
        seconds = timers[step_id]
        if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0:
            step = steps[step_id]
            words = _duration_words(seconds)
            verified.append(domain.VerifiedStepTimer(
                step_id, step.source_label, None, f"검토자 입력: {words}", words,
                (seconds,), step.evidence.source_page_number, source=REVIEW_ORIGIN_REVIEWER,
            ))
    order = {step_id: index for index, step_id in enumerate(steps)}
    verified.sort(key=lambda timer: order.get(timer.step_id, len(order)))
    return domain.StepTimerTable(
        tuple(verified),
        tuple(time for time in table.refused if time.step_id not in decided),
    )


def _version_payload(event: Any) -> dict[str, object]:
    """A registration event's payload with the time it was recorded."""

    return {
        **(event.payload if isinstance(event.payload, dict) else {}),
        "registered_at": event.recorded_at,
        "registered_on": _korean_date(event.recorded_at),
    }


def _review_version_info(payload: dict[str, object]) -> ReviewVersionInfo:
    return ReviewVersionInfo(
        version=int(payload["version"]),
        revision_id=str(payload["revision_id"]),
        reviewer_name=str(payload["reviewer_name"]),
        registered_at=str(payload.get("registered_at") or ""),
        registered_on=str(payload.get("registered_on") or ""),
        changes=tuple(
            ReviewedChange.from_public(item) for item in payload.get("changes") or ()
            if isinstance(item, dict)),
        base_revision_id=(
            str(payload["base_revision_id"]) if payload.get("base_revision_id") else None),
    )


def _path_depth(path: str) -> int:
    return path.count(".") + path.count("[")


def _removed_at(protocol: domain.ExperimentProtocol, path: str) -> domain.ExperimentProtocol:
    """``protocol`` without the record at ``path``: out of its tuple, or None."""

    match = re.fullmatch(r"(.+)\[([0-9]+)\]", path)
    if match is not None:
        parent, index = match[1], int(match[2])
        items = _value_at_path(protocol, parent)
        return replace_at_paths(protocol, {parent: items[:index] + items[index + 1:]})
    return replace_at_paths(protocol, {path: None})


def _value_at_path(protocol: domain.ExperimentProtocol, path: str) -> Any:
    current: Any = protocol
    for part in path.split(".")[1:]:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[([0-9]+)\])?", part)
        assert match is not None
        current = getattr(current, match[1])
        if match[2] is not None:
            current = current[int(match[2])]
    return current


def _shifted_item_ids(
    origins: dict[str, str], removed: Sequence[str],
) -> dict[str, str]:
    """Origins keyed by item id after values were taken out of their tuples.

    A value is named by its place ("action:a1/quantities/2"); once the value
    before it is left empty it is the second ("…/1").
    """

    gone: dict[str, set[int]] = {}
    for item_id in removed:
        head, _, index = item_id.rpartition("/")
        if index.isdigit():
            gone.setdefault(head, set()).add(int(index))
    shifted: dict[str, str] = {}
    for item_id, origin in origins.items():
        head, _, index = item_id.rpartition("/")
        if index.isdigit() and head in gone:
            number = int(index)
            if number in gone[head]:
                continue
            item_id = f"{head}/{number - sum(1 for value in gone[head] if value < number)}"
        elif item_id in removed:
            continue
        shifted[item_id] = origin
    return shifted


#: A step sentence's own number ("3.", "(2)") before its words.
_LEADING_LABEL = re.compile(r"^\s*\(?[0-9]+(?:\.[0-9]+)*[.)]\s+")
#: A number with the unit printed after it, as a step sentence states a value.
_STATED_VALUE = re.compile(
    r"[0-9]+(?:[.,][0-9]+)*(?:\s*(?:°\s*[CF]|℃|C\b|%|[µμunm]?[LlMg]\b|rpm\b|[x×]\s*g\b|"
    r"min\b|h\b|hr\b|hrs\b|s\b|sec\b|분|시간|초|회|번))?")


def _numbers_of(text: str | None) -> list[str]:
    """The values a sentence states, its own step number left out."""

    return [" ".join(value.split()) for value in _STATED_VALUE.findall(
        _LEADING_LABEL.sub("", text or "", count=1))]


#: The kinds the analysis may not word for the run (human decision
#: 2026-10-12): a safety statement and a completion criterion, confirmed,
#: run in the source's own words.
_SOURCE_WORDS_ONLY = frozenset({"warning", "expected_result", "observation"})


class ProtocolReviewBlockedError(ProtocolReviewNotReadyError):
    code = "review_version_blocked"

    def __init__(self, message: str, *, codes: Sequence[str]) -> None:
        super().__init__(message)
        self.codes = tuple(codes)


def _apply_review(
    target: "_ReviewTarget",
    items: list[dict[str, object]],
    decisions: dict[str, dict[str, object]],
    name: str,
) -> tuple[domain.ExperimentProtocol, list[ReviewedChange], dict[str, int | None], dict[str, str]]:
    """The protocol a review version holds, its changes, timers and origins.

    Applied to the target's protocol, deepest record first so a record's
    change and its value's change both hold: a fix writes the reviewer's
    words, a confirmed or fixed item whose quote was not found takes the
    lines a person pointed at as its quote, and an item left empty leaves
    the protocol. Timers, a repeat count and a value the source does not
    print are the reviewer's own values beside it.
    """

    protocol = target.protocol
    by_id = {item["item_id"]: item for item in items}
    records = {record.item_id: record for record in _review_records(protocol)}
    edits: list[tuple[str, Callable[[Any], Any]]] = []
    removals: list[tuple[str, str]] = []
    changes: list[ReviewedChange] = []
    timers: dict[str, int | None] = dict(target.timers or {})
    origins: dict[str, str] = dict(target.origins or {})
    confirmed_owners: set[str] = set()
    for item_id, decision in decisions.items():
        item = by_id.get(item_id)
        if item is None:
            continue
        action = str(decision.get("action"))
        kind = str(item["kind"])
        value = decision.get("value") if isinstance(decision.get("value"), dict) else {}
        original = item.get("text")
        reviewed: object = original
        spoken: str | None = None
        if kind == "timer":
            step_id = item_id[len("timer:"):]
            timers[step_id] = int(value["seconds"]) if action in {"fix", "enter"} else None
            reviewed = value.get("text") if action in {"fix", "enter"} else None
            spoken = value.get("text") if action in {"fix", "enter"} else None
        elif kind == "pages":
            pass
        elif kind == "missing_value" and action == "enter":
            reviewed = spoken = value.get("text")
        else:
            record = records.get(item_id)
            path = "protocol.metadata" if kind == "metadata" else record.path if record else None
            if path is None:
                continue
            if action == "clear":
                reviewed = None
                if kind == "repeat_count":
                    edits.append((path, lambda current: replace(current, repeat_count=None)))
                else:
                    removals.append((path, item_id))
            elif action in {"fix", "enter"}:
                if kind == "repeat_count":
                    count = int(value["count"])
                    edits.append((path, lambda current, count=count: replace(
                        current, repeat_count=count,
                        repeat_count_kind=domain.REPEAT_COUNT_TOTAL)))
                    reviewed = spoken = value.get("text")
                else:
                    text = str(value["text"])
                    reviewed = text

                    def fixed(current, text=text):
                        changed: dict[str, object] = {_REVIEW_TEXT_FIELD[type(current)]: text}
                        if isinstance(current, domain.ScientificValue):
                            changed.update(parsed_value=None, normalized_unit=None)
                        if isinstance(current, domain.EstimatedDuration):
                            changed["parsed_seconds"] = None
                        return replace(current, **changed)

                    edits.append((path, fixed))
                    if kind in {"step", "sub_action"}:
                        before = _numbers_of(str(original or ""))
                        spoken = " · ".join(
                            number for number in _numbers_of(text) if number not in before
                        ) or None
            lines = decision.get("source_lines")
            if isinstance(lines, dict) and action in {"confirm", "fix"}:
                quote = domain.SourceEvidence(
                    source_page_number=int(lines["page"]),
                    source_excerpt=str(lines["excerpt"]),
                )
                edits.append((path, lambda current, quote=quote: replace(current, evidence=quote)))
                confirmed_owners.add(item_id)
            if action == "confirm" and item["needs_review"] and kind in _SOURCE_WORDS_ONLY:
                # "안전 주의와 완료 기준은 AI 가 새로 써 넣지 않는다": confirmed,
                # a safety statement or a completion criterion the source
                # did not print as read runs in the source's own words -- the
                # lines a person pointed at, or the quote the server found.
                words = (
                    str(lines["excerpt"]) if isinstance(lines, dict)
                    else str((item.get("source") or {}).get("excerpt") or original))
                field = _REVIEW_TEXT_FIELD[type(record.record)] if record else None
                if field is not None:
                    edits.append((path, lambda current, field=field, words=words: replace(
                        current, **{field: words})))
                    reviewed = words
        if action == "confirm" and not item["needs_review"]:
            continue
        origin = (
            REVIEW_ORIGIN_REVIEWER if action in {"fix", "enter"}
            else REVIEW_ORIGIN_SOURCE if kind in _SOURCE_WORDS_ONLY and action == "confirm"
            else REVIEW_ORIGIN_AI if item["needs_review"] else str(item["origin"])
        )
        if action in {"fix", "enter"} or (action == "confirm" and item["needs_review"]):
            origins[item_id] = origin
        changes.append(ReviewedChange(
            item_id=item_id, kind=kind, kind_ko=str(item["kind_ko"]),
            step_id=item.get("step_id"), step_label=item.get("step_label"),
            action=action,
            original=None if original is None else str(original),
            reviewed=None if reviewed is None else str(reviewed),
            origin=origin, spoken=spoken,
            decided_by=str(decision.get("reviewer_name") or name),
            decided_at=str(decision.get("recorded_at") or ""),
        ))
    for item in items:
        if item.get("covered_by") in confirmed_owners:
            origins.setdefault(str(item["item_id"]), REVIEW_ORIGIN_AI)
    for path, edit in sorted(edits, key=lambda pair: _path_depth(pair[0]), reverse=True):
        protocol = replace_at_paths(protocol, {path: edit(_value_at_path(protocol, path))})
    for path, _ in sorted(
        removals, key=lambda pair: (_path_depth(pair[0]), pair[0]), reverse=True,
    ):
        protocol = _removed_at(protocol, path)
    origins = _shifted_item_ids(origins, [item_id for _, item_id in removals])
    changes.sort(key=lambda change: list(by_id).index(change.item_id))
    return protocol, changes, timers, origins


# ---------------------------------------------------------------------------
# Reagent tables (lane AN, FM b; human decision 2026-10-13)
# ---------------------------------------------------------------------------
#
# An Excel workbook is attached to a protocol as its "시약 표": every sheet
# and every non-empty cell kept where the workbook prints it (sheet name, cell
# reference, value, number format), so a question about an amount can be
# answered with the table position and the cell value. It is not a protocol
# source: it is never analysed, never advances a step, and the protocol's
# readiness does not depend on it. Connecting it to voice is a later lane.

REAGENT_TABLE_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_REAGENT_TABLE_EVENT = "protocol_reagent_table_attached"
#: The largest workbook read: a reagent list is small, and openpyxl holds the
#: whole workbook in memory to read its merged cells.
MAX_REAGENT_TABLE_BYTES = 5 * 1024 * 1024
#: The most non-empty cells kept; a larger workbook is refused, not cut.
MAX_REAGENT_TABLE_CELLS = 20_000
_SAFE_TABLE_FILENAME = re.compile(r"^[^/\\\x00]{1,255}\.xlsx$", re.IGNORECASE)


class ReagentTableError(ProtocolCatalogError):
    code = "invalid_reagent_table"


class ReagentTableTooLargeError(ReagentTableError):
    code = "reagent_table_too_large"


def _cell_value(value: object) -> object:
    """A cell value as JSON keeps it: numbers and text as they are."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _cell_display(value: object, number_format: str | None) -> str:
    """How the cell reads: the number format's percent and decimals applied.

    Only those two; any other format reads as the plain value, and the
    format itself is kept beside it.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "" if value is None else str(value)
    fmt = (number_format or "General").split(";", 1)[0]
    decimals_match = re.search(r"0\.(0+)", fmt)
    decimals = len(decimals_match[1]) if decimals_match else None
    if "%" in fmt:
        return f"{value * 100:.{decimals or 0}f}%"
    if decimals is not None:
        return f"{value:.{decimals}f}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def read_reagent_workbook(source: str | Path) -> dict[str, object]:
    """Every sheet and non-empty cell of an .xlsx workbook, where it prints them.

    A formula cell keeps the value the workbook saved for it; one saved
    without a value (written by a program that does not calculate) is kept
    with its formula and no value, and counted in ``warnings``.
    """

    try:
        import openpyxl
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as exc:  # pragma: no cover - openpyxl is a dependency
        raise ReagentTableError("openpyxl is not installed.") from exc
    import zipfile

    path = Path(source)
    if path.stat().st_size > MAX_REAGENT_TABLE_BYTES:
        raise ReagentTableTooLargeError("The workbook is larger than a reagent table reads.")
    try:
        values = openpyxl.load_workbook(path, data_only=True)
        formulas = openpyxl.load_workbook(path, data_only=False)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise ReagentTableError("The file is not an .xlsx workbook.") from exc
    sheets: list[dict[str, object]] = []
    count = 0
    unsaved = 0
    for sheet in values.worksheets:
        written = formulas[sheet.title]
        cells: list[dict[str, object]] = []
        for row in sheet.iter_rows():
            for cell in row:
                formula = written[cell.coordinate].value
                has_formula = isinstance(formula, str) and formula.startswith("=")
                if cell.value is None and not has_formula:
                    continue
                count += 1
                if count > MAX_REAGENT_TABLE_CELLS:
                    raise ReagentTableTooLargeError(
                        "The workbook has more cells than a reagent table keeps.")
                value = _cell_value(cell.value)
                item: dict[str, object] = {
                    "ref": cell.coordinate,
                    "row": cell.row,
                    "column": cell.column,
                    "value": value,
                    "display": _cell_display(value, cell.number_format),
                }
                if cell.number_format and cell.number_format != "General":
                    item["number_format"] = cell.number_format
                if has_formula:
                    item["formula"] = formula
                    if cell.value is None:
                        unsaved += 1
                cells.append(item)
        sheets.append({
            "name": sheet.title,
            "state": sheet.sheet_state,
            "max_row": sheet.max_row,
            "max_column": sheet.max_column,
            "merged": sorted(str(merged) for merged in sheet.merged_cells.ranges),
            "cells": cells,
        })
    warnings = []
    if unsaved:
        warnings.append(
            f"계산값이 저장되지 않은 수식 셀 {unsaved}개 — 엑셀에서 열어 저장한 뒤 다시 올리면 값이 보입니다."
        )
    return {"sheets": sheets, "cell_count": count, "warnings": warnings}


def _table_key(text: object) -> str:
    """Text as a lookup compares it: NFKC, case and spacing/punctuation aside."""

    import unicodedata

    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    return "".join(character for character in folded if character.isalnum())


def find_reagent_cells(
    tables: Sequence[dict[str, object]], query: str, *, limit: int = 20,
) -> list[dict[str, object]]:
    """The cells whose text holds ``query``, each with its row and header row.

    Matching sets case, spacing and punctuation aside ("tris hcl" finds
    "Tris-HCl"). The header row is the sheet's first row of two or more
    cells -- a title line above the table ("S1 Table. Key resources") is
    not it -- or its first row when no row has two. The answer carries
    positions and values only -- what a person or the voice lane reads from
    them is theirs.
    """

    wanted = _table_key(query)
    if not wanted:
        return []
    found: list[dict[str, object]] = []
    for table in tables:
        for sheet in table.get("sheets", []) if isinstance(table, dict) else []:
            cells = [cell for cell in sheet.get("cells", []) if isinstance(cell, dict)]
            if not cells:
                continue
            by_row: dict[int, list[dict[str, object]]] = {}
            for cell in cells:
                by_row.setdefault(cell["row"], []).append(cell)
            header_row = min(
                (row for row, items in by_row.items() if len(items) >= 2),
                default=min(by_row),
            )
            header = {cell["column"]: cell for cell in by_row.get(header_row, [])}
            for cell in cells:
                if wanted not in _table_key(cell.get("display") or cell.get("value") or ""):
                    continue
                found.append({
                    "table_id": table.get("table_id"),
                    "filename": table.get("filename"),
                    "sheet": sheet.get("name"),
                    "ref": cell["ref"],
                    "value": cell.get("value"),
                    "display": cell.get("display"),
                    "row": [
                        {
                            "ref": item["ref"],
                            "display": item.get("display"),
                            "header": (
                                header[item["column"]].get("display")
                                if item["column"] in header and cell["row"] > header_row
                                else None
                            ),
                        }
                        for item in sorted(by_row[cell["row"]], key=lambda item: item["column"])
                    ],
                })
                if len(found) >= limit:
                    return found
    return found


def reagent_table_summary(payload: dict[str, object]) -> dict[str, object]:
    return {
        key: payload.get(key)
        for key in ("table_id", "filename", "sha256", "byte_size", "cell_count", "warnings")
    } | {
        "sheets": [
            {"name": sheet.get("name"), "cells": len(sheet.get("cells", []))}
            for sheet in payload.get("sheets", []) if isinstance(sheet, dict)
        ],
    }
