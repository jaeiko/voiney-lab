"""Single source of user-facing Korean product copy for internal status codes.

Internal codes (``analysis_required``, ``unresolved_ambiguity``,
``development_activated``, ...) stay internal. Every place that shows a status to
a researcher, reviewer, or lab admin resolves it here, so the same concept is not
translated differently on two pages and no raw lifecycle code reaches a normal
user.

Two rules apply to everything in this module:

* it never rewrites protocol source text - source quotations are rendered
  verbatim by the caller and are clearly separated from these UI explanations;
* it never upgrades a status. A human-confirmation checkpoint is described as
  routine bench work, and a true ambiguity is described as review work; neither
  description changes what the server will allow.
"""

from __future__ import annotations

from typing import Final


UNKNOWN_STATUS_LABEL: Final = "상태 확인 필요"

READINESS_STATUS_LABELS: Final = {
    "guidance_ready": "실행 준비 확인됨",
    "analysis_required": "원문 검토 필요",
}

#: Readiness reason codes as a reviewer should read them. ``blocking`` marks the
#: ones that genuinely stop execution; the rest are informational.
READINESS_REASON_LABELS: Final = {
    "invalid_protocol": (
        "구조 분석 결과 오류",
        "구조 분석 결과가 유효하지 않아 다시 분석해야 합니다.",
    ),
    "no_executable_steps": (
        "실행 가능한 단계 없음",
        "원문에서 실행할 단계를 찾지 못했습니다.",
    ),
    "unresolved_ambiguity": (
        "원문 해석 확인 필요",
        "원문 문장의 의도가 한 가지로 읽히지 않습니다. 검토자가 의미를 확정해야 합니다.",
    ),
    "missing_execution_critical_value": (
        "필수 값 누락",
        "실행에 필요한 값이 원문에 없습니다. 임의로 채우지 않습니다.",
    ),
    "unresolved_execution_value_conflict": (
        "값 충돌 확인 필요",
        "원문 안에서 서로 다른 값이 지시돼 있습니다. 검토자가 확정해야 합니다.",
    ),
    "safety_critical_conflict": (
        "안전 관련 충돌",
        "안전에 영향을 주는 상충 내용이 있어 실행할 수 없습니다.",
    ),
    "unsupported_conditional_branch": (
        "조건 분기 미지원",
        "원문의 조건 분기는 아직 자동 안내로 실행할 수 없습니다.",
    ),
    "unsupported_fixed_range_repetition": (
        "고정 반복 미지원",
        "원문의 고정 횟수 반복은 아직 자동 안내로 실행할 수 없습니다.",
    ),
    "unsupported_repeat_until": (
        "반복 구간 확인 필요",
        "반복할 단계 범위를 원문 구조에서 확정할 수 없습니다. 검토자가 범위를 지정해야 합니다.",
    ),
    "unsupported_human_confirmed_repeat_until": (
        "연구자 확인 반복 미지원",
        "현재 실행 프로필에서는 연구자 확인 반복 단계를 안내할 수 없습니다.",
    ),
    "unsupported_parallel_background_work": (
        "동시 진행 미지원",
        "원문의 동시·백그라운드 작업은 아직 자동 안내로 실행할 수 없습니다.",
    ),
    "unsupported_recurring_reminder": (
        "반복 알림 미지원",
        "원문의 반복 알림은 아직 자동 안내로 실행할 수 없습니다.",
    ),
    "unsupported_recurring_action": (
        "반복 작업 미지원",
        "원문의 반복 작업은 아직 자동 안내로 실행할 수 없습니다.",
    ),
    "unsupported_reusable_subprocedure": (
        "공용 하위 절차 미지원",
        "원문의 공용 하위 절차는 아직 자동 안내로 실행할 수 없습니다.",
    ),
}

#: Reason codes a reviewer can act on directly in the product.
REVIEWER_RESOLVABLE_REASONS: Final = frozenset(
    {"unresolved_ambiguity", "unsupported_repeat_until"}
)

EXECUTION_READINESS_LABELS: Final = {
    "analysis_pending": (
        "구조 분석 대기",
        "원문 분석이 끝나면 검토할 수 있습니다.",
    ),
    "needs_clarification": (
        "원문 해석 확인 필요",
        "검토자가 원문의 의미를 확정해야 실행 승인 단계로 넘어갑니다.",
    ),
    "ready_for_execution_approval": (
        "실행 승인 가능",
        "실행을 막는 항목이 없습니다. 실행 승인을 기록하면 연구자가 바로 선택할 수 있습니다.",
    ),
    "approved_for_execution": (
        "실행 승인됨",
        "연구자가 이 버전으로 실험을 시작할 수 있습니다.",
    ),
    "approval_revoked": (
        "실행 승인 해제됨",
        "새 실험에서는 사용할 수 없습니다. 기록은 그대로 보존됩니다.",
    ),
    "development_only": (
        "연구·데모용 초안",
        "실행 승인 기록이 없는 개발용 초안입니다.",
    ),
}

APPROVAL_STATUS_LABELS: Final = {
    "approved": "실행 승인됨",
    "unapproved": "실행 승인 전",
    "development_only": "연구·데모용 초안",
    "revoked": "실행 승인 해제됨",
    "development_only_not_final_acceptance": "연구·데모용 초안",
}

GOVERNANCE_DECISION_LABELS: Final = {
    "review_required": "변경 내용 확인 필요",
    "approved": "변경 내용 확인 완료",
    "rejected": "수정 요청됨",
    "revoked": "향후 사용 중지됨",
}

ANALYSIS_STATUS_LABELS: Final = {
    "structured_analysis_ready": "구조 분석 준비됨",
    "analysis_pending": "구조 분석 대기",
    "analysis_in_progress": "구조 분석 진행 중",
    "analysis_failed": "구조 분석 실패",
    "review_required": "검토 필요",
    "validated": "검토 완료",
    "approved": "실행 승인됨",
    "active_development": "연구·데모용 활성",
    "validated_curated_fixture": "검증된 개발용 원문",
    "ocr_required": "이미지 원문 · 텍스트 추출 필요",
    "ocr_in_progress": "텍스트 추출 중",
    "ocr_review_required": "추출 텍스트 대조 필요",
    "ocr_rejected": "추출 텍스트 거절됨",
    "ocr_failed": "텍스트 추출 실패",
    "chunk_planned": "분할 분석 준비됨",
    "chunk_analysis_in_progress": "분할 분석 진행 중",
    "chunk_analysis_failed": "분할 분석 실패",
    "chunk_analysis_cancelled": "분할 분석 취소됨",
    "merge_in_progress": "분석 병합 중",
    "merge_conflict": "분석 병합 충돌",
}

LIFECYCLE_STATE_LABELS: Final = {
    "uploaded": "원문 등록됨",
    "analyzing": "분석 중",
    "analysis_pending": "분석 대기",
    "review_required": "검토 필요",
    "blocked": "확인 필요",
    "executable_draft": "연구·데모용 실행 가능",
    "approved": "실행 승인됨",
}

#: Bench-side block reasons. These are shown as guidance, never as raw codes.
BLOCK_REASON_LABELS: Final = {
    "final_step_boundary": "마지막 단계까지 완료",
    "unresolved_ambiguity": "원문 해석 확인 필요",
    "unsupported_repeat_until": "반복 구간 확인 필요",
    "human_checkpoint_review_requested": "반복 검토 요청됨",
}

HUMAN_CHECKPOINT_LABEL: Final = "연구자 확인 단계"
HUMAN_CHECKPOINT_DETAIL: Final = "연구자가 실험 중 직접 확인합니다."
HUMAN_CHECKPOINT_REVIEWER_DETAIL: Final = (
    "원문이 정한 관찰 조건입니다. 오류가 아니며, 실험 중 연구자가 직접 확인합니다."
)


def _pair(mapping: dict[str, tuple[str, str]], code: str) -> tuple[str, str]:
    return mapping.get(code, (UNKNOWN_STATUS_LABEL, ""))


def readiness_status_label(code: str) -> str:
    return READINESS_STATUS_LABELS.get(code, UNKNOWN_STATUS_LABEL)


def readiness_reason_label(code: str) -> str:
    return _pair(READINESS_REASON_LABELS, code)[0]


def readiness_reason_detail(code: str) -> str:
    return _pair(READINESS_REASON_LABELS, code)[1]


def execution_readiness_label(state: str) -> str:
    return _pair(EXECUTION_READINESS_LABELS, state)[0]


def execution_readiness_detail(state: str) -> str:
    return _pair(EXECUTION_READINESS_LABELS, state)[1]


def approval_status_label(code: str) -> str:
    return APPROVAL_STATUS_LABELS.get(code, UNKNOWN_STATUS_LABEL)


def analysis_status_label(code: str) -> str:
    return ANALYSIS_STATUS_LABELS.get(code, UNKNOWN_STATUS_LABEL)


def lifecycle_state_label(code: str) -> str:
    return LIFECYCLE_STATE_LABELS.get(code, UNKNOWN_STATUS_LABEL)


def block_reason_label(code: str | None) -> str | None:
    if code is None:
        return None
    return BLOCK_REASON_LABELS.get(code, UNKNOWN_STATUS_LABEL)


def governance_decision_label(code: str) -> str:
    return GOVERNANCE_DECISION_LABELS.get(code, UNKNOWN_STATUS_LABEL)


def reason_is_reviewer_resolvable(code: str) -> bool:
    return code in REVIEWER_RESOLVABLE_REASONS
