"""Authoritative, read-only request arbitration for every conversation path.

The arbiter classifies the user's latest normalized request.  It never performs
workflow mutations and it never treats a classification as authorization.  The
curated protocol runtime and the generic tool runtime consume the same typed
decision so specialized informational requests cannot be shadowed by a generic
current-step answer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class RequestIntent(str, Enum):
    """Stable top-level intents used by production routing and replay tests."""

    WORKFLOW_CONTROL = "workflow_control"
    LEARNING = "learning"
    PROTOCOL_AUDIT = "protocol_audit"
    HISTORY_RESUME = "history_resume"
    UNCERTAINTY = "uncertainty"
    COMBINED_LEARNING_NEXT = "combined_learning_next"
    VISUAL = "visual"
    CURRENT_STEP = "current_step"
    GENERAL_QA = "general_qa"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RequestArbitration:
    """One sanitized interpretation contract with no mutation authority."""

    normalized_text: str
    intent: RequestIntent
    confidence: float | None
    reason_code: str
    dimensions: tuple[str, ...] = ()
    history_action: str | None = None
    mutation_candidate: bool = False

    @property
    def state_mutation(self) -> bool:
        """Classifications are evidence only; this is always false by design."""

        return False


def normalize_request_text(value: str) -> str:
    """Normalize harmless Unicode and spacing variation without changing meaning."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("’", "'").replace("`", "'")
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return normalized.rstrip(".!?。？！ ")


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) is not None for pattern in patterns)


_WHY_PATTERNS = (
    r"(?:이\s*(?:단계|작업)|여기서|이걸|이거|이\s*과정)?\s*왜\s*(?:꼭\s*)?(?:해야|하는|필요|먼저)",
    r"왜\s*(?:이|현재|이번)\s*(?:단계|작업)(?:를|은|는)?\s*(?:해야|하는|필요)",
    r"(?:왜\s*(?:해야\s*(?:돼|해|하는지)|하는지|필요한지)|이\s*단계의\s*(?:이유|목적)|(?:이유|목적)(?:가|은|이)?\s*(?:뭐|무엇))",
    r"\b(?:why\s+(?:do\s+we\s+(?:do|need)\s+this(?:\s+step)?|should\s+i\s+do\s+this(?:\s+step)?|is\s+this(?:\s+step)?\s+(?:needed|necessary|important)|this\s+step\s+(?:matters|is\s+needed))|purpose\s+of\s+(?:this|the\s+current)\s+step)\b",
)
_WARNING_PATTERNS = (
    r"(?:(?:여기서|이\s*(?:단계|작업)에서)\s*(?:조심|주의)(?:할|해야|사항|점|할\s*건|할\s*것)|조심(?:할|해야)\s*(?:점|건|것))",
    r"(?:주의\s*사항|주의점|주의(?:해야\s*할|할)\s*(?:점|사항)|흔한\s*실수|실수하기\s*쉬운|피해야\s*할)(?:을|를|은|는)?\s*(?:알려|뭐|무엇|설명)",
    r"주의(?:해야\s*할|할)\s*(?:흔한\s*)?실수(?:가|는|를)?\s*(?:뭐|무엇|알려|설명)",
    r"\b(?:warning|caution|precaution|common\s+mistake|what\s+should\s+i\s+(?:avoid|watch))s?\b",
)
_NEXT_PATTERNS = (
    r"(?:다음\s*(?:단계|작업)|그\s*다음)(?:도|은|에는|으로)?\s*(?:알려|설명|뭐|무엇|보여|하는)",
    r"(?:다음에는|다음엔)\s*(?:뭐|무엇|어떤\s*작업)",
    r"\b(?:next\s+step|what(?:'s|\s+is)\s+next|what\s+do\s+we\s+do\s+next)\b",
)
_AUDIT_PATTERNS = (
    r"(?:현재\s*)?(?:프로토콜|sop|절차|문서)(?:의|가|는|을|를)?\s*(?:버전|판|개정|리비전|해시|체크섬)",
    r"(?:지금\s*쓰는\s*)?(?:sop|프로토콜)(?:가|는)?\s*몇\s*(?:판|버전)",
    r"(?:문서\s*)?(?:버전|해시|sha\s*-?\s*256|체크섬)(?:값|정보)?\s*(?:알려|확인|보여|뭐|무엇|몇)",
    r"\b(?:protocol|sop|document)\s+(?:version|revision|hash|checksum)\b",
    r"\bsha\s*-?\s*256\b",
)
_RESUME_HISTORY_PATTERNS = (
    r"(?:어제|이전|지난|지난번|지난\s*번|전에|기존)\s*(?:하던|진행하던|멈춘)?\s*(?:것|거|실험|세션|워크플로)?\s*(?:을|를)?\s*(?:이어|이어줘|이어서|계속|불러|재개)",
    r"(?:전에|이전에|지난번에)?\s*멈춘\s*(?:실험|세션|워크플로)(?:이|가)?\s*(?:있어|있나|있나요)",
    r"\b(?:continue|resume)\s+(?:yesterday(?:'s)?|(?:the\s+|a\s+)?previous|last)\s+(?:experiment|session|workflow)\b",
)
_HISTORY_LIST_PATTERNS = (
    r"(?:이전|최근|과거|지난)?\s*(?:실험|세션|워크플로)\s*(?:기록|이력|내역|목록)(?:을|를)?\s*(?:보여|조회|알려|확인|불러)",
    r"\b(?:experiment|session|workflow)\s+history\b",
    r"\b(?:list|show)\s+(?:recent|previous|past)\s+(?:experiments|sessions|workflows)\b",
)
_UNCERTAINTY_PATTERNS = (
    r"(?:이\s*)?(?:실험(?:\s*결과)?|결과)(?:이|가|은|는)?\s*(?:성공|실패|잘\s*(?:될|나올)|좋을|괜찮을)(?:할|될|나올)?(?:까|지|까요|건가)",
    r"(?:이\s*정도면|지금\s*상태면)\s*(?:성공|잘\s*된|괜찮은)(?:\s*거야|건가|걸까|거냐)",
    r"(?:성공\s*여부|결과\s*예측|성공할\s*수\s*있을까|망할까)",
    r"\b(?:will\s+(?:this|the)\s+experiment\s+succeed|will\s+it\s+work|is\s+(?:this|it)\s+successful)\b",
)
_VISUAL_PATTERNS = (
    r"(?:사진|이미지|그림|구조식|시각\s*자료)(?:을|를|이|가)?\s*(?:찾아|검색|보여|열어|그려|만들)",
    r"(?:어떻게\s*생겼|생김새).*(?:찾아|보여|알려)",
    r"\b(?:find|show|search\s+for|display|generate)\b.*\b(?:photo|image|picture|diagram|structure)\b",
)
_CURRENT_STEP_PATTERNS = (
    r"(?:현재|지금|이번)\s*(?:몇\s*)?단계(?:가|는|를|의)?\s*(?:뭐|무엇|알려|설명|확인)",
    r"지금\s*(?:뭐|무엇)을?\s*해야",
    r"\b(?:current\s+step|what\s+should\s+i\s+do\s+now)\b",
)
_PROTOCOL_INFORMATION_PATTERNS = (
    r"(?:이\s*)?(?:실험|프로토콜|sop|절차)(?:의|가|는|을|를)?\s*(?:목적|목표|개요|전체\s*흐름)",
    r"\b(?:purpose|goal|objective|overview)\s+of\s+(?:this|the)\s+(?:experiment|protocol|sop|procedure)\b",
)
_AGENT_INFORMATION_PATTERNS = (
    r"(?:이\s*)?(?:에이전트|보이스\s*에이전트|ai|시스템|너|당신)(?:의|가|는)?\s*(?:목적|역할|기능|능력)",
    r"\b(?:purpose|role|function|capabilit(?:y|ies))\s+of\s+(?:this|the)\s+(?:agent|assistant|system)\b",
)
_WORKFLOW_CONTROL_PATTERNS = (
    r"^(?:프로토콜|실험|워크플로)?\s*(?:을|를)?\s*(?:시작|중지|중단|종료|일시\s*정지|재개)(?:해\s*줘|하자|할게|합니다|해)?$",
    r"(?:현재\s*|이번\s*|\d+\s*)단계(?:를|는)?\s*(?:완료|끝|마쳤|마침|다\s*했)",
    r"^(?:완료|끝났어|다\s*했어|다음\s*단계로\s*(?:가|넘어가|진행))",
    r"\b(?:start|stop|pause|resume|complete)\s+(?:the\s+)?(?:protocol|workflow|current\s+step)\b",
)


def arbitrate_request(text: str) -> RequestArbitration:
    """Classify one latest user request with deterministic, fail-closed priority."""

    normalized = normalize_request_text(text)
    if not normalized:
        return RequestArbitration(normalized, RequestIntent.UNKNOWN, None, "empty")

    history_action: str | None = None
    if _matches(normalized, _RESUME_HISTORY_PATTERNS):
        history_action = "resume"
    elif _matches(normalized, _HISTORY_LIST_PATTERNS):
        history_action = "list"
    if history_action is not None:
        return RequestArbitration(
            normalized,
            RequestIntent.HISTORY_RESUME,
            1.0,
            f"history_{history_action}",
            (history_action,),
            history_action=history_action,
        )

    if _matches(normalized, _AUDIT_PATTERNS):
        return RequestArbitration(
            normalized,
            RequestIntent.PROTOCOL_AUDIT,
            1.0,
            "protocol_version_or_hash",
            ("title", "protocol_version", "document_version", "checksum"),
        )

    if _matches(normalized, _UNCERTAINTY_PATTERNS):
        return RequestArbitration(
            normalized,
            RequestIntent.UNCERTAINTY,
            1.0,
            "outcome_prediction_unsupported",
            ("bounded_uncertainty",),
        )

    protocol_information = _matches(normalized, _PROTOCOL_INFORMATION_PATTERNS)
    agent_information = _matches(normalized, _AGENT_INFORMATION_PATTERNS)
    why = _matches(normalized, _WHY_PATTERNS) and not (
        protocol_information or agent_information
    )
    warning = _matches(normalized, _WARNING_PATTERNS)
    next_step = _matches(normalized, _NEXT_PATTERNS)
    if why and next_step:
        return RequestArbitration(
            normalized,
            RequestIntent.COMBINED_LEARNING_NEXT,
            1.0,
            "learning_and_next_preview",
            ("rationale", "next_step_preview", "completion_confirmation"),
        )
    if why or warning:
        return RequestArbitration(
            normalized,
            RequestIntent.LEARNING,
            1.0,
            "current_step_warning" if warning and not why else "current_step_rationale",
            (("warning",) if warning and not why else ("rationale", "warning")),
        )

    if _matches(normalized, _VISUAL_PATTERNS):
        return RequestArbitration(
            normalized,
            RequestIntent.VISUAL,
            1.0,
            "explicit_visual_request",
            ("image",),
        )

    if _matches(normalized, _CURRENT_STEP_PATTERNS) or next_step:
        return RequestArbitration(
            normalized,
            RequestIntent.CURRENT_STEP,
            1.0,
            "next_step_preview" if next_step else "current_step_information",
            (("next_step_preview",) if next_step else ("current_step",)),
        )

    if _matches(normalized, _WORKFLOW_CONTROL_PATTERNS):
        return RequestArbitration(
            normalized,
            RequestIntent.WORKFLOW_CONTROL,
            1.0,
            "workflow_control_candidate",
            mutation_candidate=True,
        )

    question_like = protocol_information or agent_information or bool(
        re.search(r"(?:\?|뭐|무엇|어떻게|알려|설명|why|what|how|when|where|who)", normalized)
    )
    if question_like:
        return RequestArbitration(
            normalized,
            RequestIntent.GENERAL_QA,
            0.75,
            "general_question",
        )
    return RequestArbitration(normalized, RequestIntent.UNKNOWN, None, "no_high_confidence_match")
