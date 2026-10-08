"""Explicit, source-preserving multi-Protocol catalog service.

The service is deliberately constructed only by an authorized API/session
boundary.  Importing or listing it does not contact an analysis Provider.
All writes use the separate immutable Protocol store, never ProcedureStore.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from voiney_lab import experiment_protocol as domain
from voiney_lab.curated_protocol import CuratedProtocolFixture
from voiney_lab.experiment_protocol_analysis import (
    MAX_SINGLE_PASS_INPUT_BYTES,
    ProtocolAnalysisDraft,
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisInputTooLargeError,
    ProtocolAnalysisModel,
    analyze_protocol_extraction,
    prepare_protocol_analysis_request,
)
from voiney_lab.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    ProtocolPdfExtraction,
    ProtocolPdfPage,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import (
    AnalysisRevisionRecord,
    ProtocolRevisionRecord,
    ProtocolStore,
    serialize_analysis,
)
from voiney_lab.protocol_chunk_analysis import (
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
from voiney_lab.protocol_claim_analysis import (
    ProtocolChunkClaimAnalysis,
    serialize_chunk_claim_analysis,
    unaccounted_segments_by_page,
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
_REVISION_ID = re.compile(r"^pdf-(\d+)(?:-analysis-(\d+))?$")


_ANALYSIS_REQUESTED_EVENT = "protocol_analysis_requested"
_ANALYSIS_STARTED_EVENT = "protocol_analysis_started"
_ANALYSIS_READY_EVENT = "protocol_analysis_ready"
_ANALYSIS_FAILED_EVENT = "protocol_analysis_failed"
_ANALYSIS_RETRY_EVENT = "protocol_analysis_retry_started"
#: Lane AN, decision 1 (2026-10-07), changing lane PA's "no automatic
#: retry": an analysis whose response broke the structure the domain
#: validation demands -- and only that failure, only on the revision's first
#: analysis request -- is sent once more, the same request, by itself. A
#: failed source-evidence check, a time-out and a missing provider are not:
#: sending them again costs a call and settles nothing a person need not see.
#: Measured 2026-10-06 (lane PX): ANKOM ended so once and passed on other runs.
AUTOMATIC_RETRY_FAILURE_CODES = frozenset({"protocol_analysis_invalid_response"})
AUTOMATIC_RETRY_LIMIT = 1
AUTOMATIC_RETRY_AUTHORITY = "automatic_invalid_response_retry"
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
#: The authority an upload-time OCR acceptance is recorded under (lane PX,
#: decision 3). A person's review carries ``human_review``.
AUTOMATIC_OCR_AUTHORITY = "automatic_upload_ocr"
_OCR_REQUESTED_EVENT = "protocol_ocr_requested"
_OCR_COMPLETED_EVENT = "protocol_ocr_completed"
_OCR_FAILED_EVENT = "protocol_ocr_failed"
_OCR_REVIEWED_EVENT = "protocol_ocr_reviewed"
_CHUNK_RUN_LOCKS = tuple(threading.Lock() for _ in range(64))
CLAIM_CHUNK_ANALYSIS_ENABLED_ENV = (
    "VOINEY_LAB_PROTOCOL_CLAIM_CHUNKS_ENABLED"
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


class ProtocolOcrRequiredError(ProtocolCatalogError):
    code = "ocr_required"


class ProtocolOcrReviewError(ProtocolCatalogError):
    """An OCR result that does not hold against the source (evidence, not a person's review)."""

    code = "protocol_ocr_review_invalid"


class ProtocolChunkedAnalysisRequiredError(ProtocolCatalogError):
    code = "chunked_analysis_required"


class ProtocolChunkAnalysisFailedError(ProtocolCatalogError):
    code = "chunk_analysis_failed"


class ProtocolChunkMergeConflictError(ProtocolCatalogError):
    code = "merge_conflict"


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
         }),
        default=-1,
    )
    return tuple(
        event for index, event in enumerate(events)
        if not (index < last_superseding and event.event_type == _ANALYSIS_FAILED_EVENT)
    )


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
    return {
        "attempt": payload.get("attempt", 1),
        "limit": payload.get("limit", AUTOMATIC_RETRY_LIMIT),
        "state": state,
        "reason_code": payload.get("reason_code"),
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
}
_DEFAULT_ANALYSIS_RECOVERY_ACTION = (
    "실패 원인을 확인한 뒤 '분석 다시 시도'를 누르세요. 다시 시도해도 원문은 "
    "바뀌지 않습니다."
)

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
    "analysis_cancelled": "분석이 취소되었습니다.",
    "chunk_analysis_failed": "큰 문서의 일부 분석이 실패했습니다.",
    "merge_conflict": "큰 문서의 분석 결과를 합치다 충돌이 났습니다.",
}


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
                    _ANALYSIS_RETRY_EVENT,
                    _ANALYSIS_READY_EVENT,
                    _SINGLE_REVIEW_REQUIRED_EVENT,
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
            # The plan is the run's first record.
            requested_at=plan_event.recorded_at,
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
            lifecycle_events = tuple(
                event
                for event in self.store.list_events(revision.experiment_id)
                if event.protocol_revision_number == revision.revision_number
                and event.event_type in {
                    _ANALYSIS_REQUESTED_EVENT,
                    _ANALYSIS_STARTED_EVENT,
                    _ANALYSIS_FAILED_EVENT,
                    _ANALYSIS_RETRY_EVENT,
                }
            )
            if lifecycle_events:
                lifecycle_state = {
                    _ANALYSIS_REQUESTED_EVENT: "analysis_pending",
                    _ANALYSIS_STARTED_EVENT: "analyzing",
                    _ANALYSIS_FAILED_EVENT: "blocked",
                    _ANALYSIS_RETRY_EVENT: "analyzing",
                }[lifecycle_events[-1].event_type]
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
        if analysis is not None:
            blocker_codes = tuple(
                reason.code.value
                for reason in domain.execution_blocking_reasons(analysis.readiness)
            )
            lifecycle_state = "blocked" if blocker_codes else "ready"
        available = analysis is not None and not blocker_codes
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
            analysis_status=analysis_status,
            step_count=(
                sum(len(section.steps) for section in analysis.protocol.sections)
                if analysis
                else 0
            ),
            created_at=revision.created_at,
            available_for_execution=available,
            lifecycle_state=lifecycle_state,
            execution_blocker_codes=blocker_codes,
            development_only=development_only,
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
        }

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
        self.store.append_event(
            # The event names the analysis it materialized, for the reason
            # above: keyed on the fixture alone it collided with its own
            # earlier record as soon as the analysis changed.
            f"development-fixture-{fixture.fixture_sha256[:48]}"
            f"-{payload_sha256[:16]}",
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
                    # Lane PA decision 4 (2026-10-06): the recovery guidance
                    # is the server's, in Korean.
                    "action": _ANALYSIS_RECOVERY_ACTIONS.get(
                        latest_failure, _DEFAULT_ANALYSIS_RECOVERY_ACTION
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
        }
        ocr_projection = base["ocr"]
        base["pipeline"] = self._pipeline(
            entry, analysis, revision,
            ocr=ocr_projection if isinstance(ocr_projection, dict) else {},
            failure_code=latest_failure,
            automatic_retry=_automatic_retry_status(revision_events),
        )
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
        )

    def _pipeline(
        self,
        entry: ProtocolCatalogEntry,
        analysis: AnalysisRevisionRecord | None,
        revision: ProtocolRevisionRecord,
        *,
        ocr: dict[str, object],
        failure_code: str | None,
        automatic_retry: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """One Korean line: the stage a document is at, whether it is stuck
        there, why, and what a person does (lane PX, decision 4).

        Stages, in order: 원문 읽기 → OCR → 분석 → 근거 대조 → 실행 준비 →
        사람 확인 → 실행 가능. Translation is added by the server, which
        holds the translation store. A document that reached this method
        was read, so 원문 읽기 never blocks here. An automatic retry (lane
        AN, decision 1) is said while it runs and after it failed.
        """

        retry = automatic_retry or {}
        retry_count = f"{retry.get('attempt', 1)}/{retry.get('limit', AUTOMATIC_RETRY_LIMIT)}"

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
                message="분석을 통과했습니다. 실행할 수 있습니다.",
                action="시작 전 화면의 안전 주의를 읽고 '이 프로토콜로 시작'을 누르세요.",
                ocr_page_numbers=ocr_pages)
        if analysis is None:
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
                if retry.get("state") == "failed":
                    message += f" 같은 요청을 자동으로 한 번 다시 보냈지만 자동 재시도({retry_count})도 실패했습니다."
                return result(
                    stage, blocked=True,
                    message=message,
                    action=_ANALYSIS_RECOVERY_ACTIONS.get(
                        failure_code, _DEFAULT_ANALYSIS_RECOVERY_ACTION),
                    failure_code=failure_code)
            if entry.lifecycle_state in {"analysis_pending", "analyzing"} or (
                entry.analysis_status in {
                    "chunk_planned", "chunk_analysis_in_progress", "merge_in_progress",
                }
            ):
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
                message="분석을 통과했습니다. 실행할 수 있습니다.",
                action="시작 전 화면의 안전 주의를 읽고 '이 프로토콜로 시작'을 누르세요.",
                ocr_page_numbers=ocr_pages)
        first = readiness_reason_korean(
            blockers[0].code.value, blockers[0].message) or "실행을 막는 사유가 있습니다."
        return result(
            "readiness", blocked=True,
            message=f"{first}" + (f" (외 {len(blockers) - 1}건)" if len(blockers) > 1 else ""),
            action=_BLOCKER_ACTIONS.get(
                blockers[0].code.value,
                "원문을 확인하고 고친 PDF 를 올리거나 '분석 다시 시도'를 누르세요."),
            remaining_reason_codes=[reason.code.value for reason in blockers],
            ocr_page_numbers=ocr_pages)

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
        entry = self.get_entry(revision.experiment_id)
        self._analysis_ready(entry)
        return entry

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
            failure_code = self._record_analysis_failure(
                revision, analysis_id, exc)
            if not self._automatic_retry_allowed(revision, failure_code):
                raise
            # The same request again: the same extraction to the same model,
            # nothing about the failure added (lane AN, decision 1).
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
                },
            )
            logging.getLogger(__name__).info(
                "protocol.analysis.automatic_retry protocol_id=%s attempt=1 limit=%d "
                "reason_code=%s",
                protocol_id, AUTOMATIC_RETRY_LIMIT, failure_code,
            )
            try:
                draft = analyze_protocol_extraction(extraction, model)
            except Exception as retry_exc:
                self._record_analysis_failure(revision, analysis_id, retry_exc)
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
        entry = self.get_entry(protocol_id)
        self._analysis_ready(entry)
        return entry

    def _record_analysis_failure(
        self,
        revision: ProtocolRevisionRecord,
        analysis_id: str,
        exc: BaseException,
    ) -> str:
        """Persist one attempt's bounded failure code; return the code."""

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
    ) -> bool:
        """Whether this failure is sent again by itself (lane AN, decision 1).

        Mechanical: the failure is a response that broke the structure, the
        revision has had at most one analysis request (none when a caller
        analyses without recording one), and no automatic retry was sent for
        the revision before. A person's "분석 다시 시도" is a second request,
        so it is never retried automatically.
        """

        if failure_code not in AUTOMATIC_RETRY_FAILURE_CODES:
            return False
        events = [
            event for event in self.store.list_events(revision.experiment_id)
            if event.protocol_revision_number == revision.revision_number
        ]
        requests = sum(
            1 for event in events if event.event_type == _ANALYSIS_REQUESTED_EVENT)
        retried = any(event.event_type == _ANALYSIS_RETRY_EVENT for event in events)
        return requests <= 1 and not retried

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

    def load_executable_fixture(
        self, protocol_id: str
    ) -> CuratedProtocolFixture:
        revision = self._latest_protocol_revision(protocol_id)
        analysis = self._latest_analysis(revision)
        entry = self._entry_for_revision(revision)
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
        )
        labels = tuple(
            step.source_label
            for section in draft.protocol.sections
            for step in section.steps
        )
        # Which pages the analysis could not finish accounting for. STEP 28
        # built the execution-time disclosure for exactly this and then left it
        # unfed: until now the only assignment to unread_pages in the whole
        # tree was in a test, so on a real Protocol the disclosure could never
        # fire. The coverage records travel with the stored analysis, so the
        # answer was already here and simply not being read.
        unread = unaccounted_segments_by_page(
            extraction,
            analysis.page_coverage,
            source_revision=entry.revision_id,
        )
        return CuratedProtocolFixture(
            draft=draft,
            status=status,
            ordered_step_labels=labels,
            fixture_sha256=analysis.payload_sha256,
            revision_id=entry.revision_id,
            development_only=entry.development_only,
            source_pdf_path=source,
            source_pdf_sha256=revision.pdf_checksum,
            source_filename=revision.original_filename,
            unread_pages=unread or None,
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
