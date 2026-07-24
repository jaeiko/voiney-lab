"""Conservative deterministic recognition of immediate emergency utterances."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

KOREAN_EMERGENCY_RESPONSE = (
    "즉시 작업을 멈추고 위험 구역에서 벗어나세요. 현장 안전관리자 또는 기존 비상 연락 절차를 통해 "
    "즉시 도움을 요청하세요. 이 응답만을 근거로 작업을 재개하지 마세요."
)
ENGLISH_EMERGENCY_RESPONSE = (
    "Stop work immediately and move away from the hazard. Immediately contact the "
    "on-site safety manager or use the established emergency contact procedure. "
    "Do not resume work based on this response."
)


@dataclass(frozen=True)
class EmergencyMatch:
    language: str
    response: str


_TERMINAL_PUNCTUATION = re.compile(r"[\s.!?。？！]+$")
_REPEATED_WHITESPACE = re.compile(r"\s+")

_KOREAN_EXCLUSIONS = re.compile(
    r"(?:알려|설명|절차|샤워|경우|만약|발생하면|났다면|"
    r"과거|예전|어제|지난|작년|사례|훈련|가정)"
)
_ENGLISH_EXCLUSIONS = re.compile(
    r"\b(?:procedure|explain|shower|if|would|could|hypothetical|"
    r"yesterday|previously|historical|history|last\s+(?:week|month|year)|drill)\b"
)

_KO_HELP_FOLLOWUP = (
    r"(?:\s*[.!?。？！]+\s*(?:어떻게\s+해야\s+(?:해요|돼요)|무엇을\s+해야\s+하나요|"
    r"뭘\s+해야\s+해요|도와\s*주세요))?"
)
_EN_HELP_FOLLOWUP = r"(?:\s*[,.!?]+\s*(?:what\s+should\s+i\s+do|how\s+do\s+we\s+get\s+help))?"

_KOREAN_IMMEDIATE = (
    re.compile(r"^도와\s*줘$"),
    re.compile(r"^도와\s*주세요$"),
    re.compile(r"^(?:지금\s*)?(?:불|화재)(?:이|가)?\s*(?:났|발생했|나고\s*있|발생하고\s*있)(?:어|어요|습니다|다)?" + _KO_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:지금\s*)?폭발(?:(?:이|가)?\s*(?:났|발생했|일어났|하고\s*있)|했)(?:어|어요|습니다|다)?" + _KO_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:지금\s*)?(?:가스|화학물질|액체|용액)?\s*누출(?:이|가)?\s*(?:됐|발생했|되고\s*있|발생하고\s*있)(?:어|어요|습니다|다)?" + _KO_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:사람이\s*)?(?:크게|심하게|심각하게)\s*다쳤(?:어|어요|습니다|다)?$"),
    re.compile(r"^(?:지금\s*)?(?:즉시|매우|너무)?\s*위험(?:한\s*상황)?(?:입니다|이에요|해요|합니다|하다|에\s*처해\s*있(?:어요|습니다)?)$"),
)
_ENGLISH_IMMEDIATE = (
    re.compile(r"^emergency$"),
    re.compile(r"^help$"),
    re.compile(r"^help(?:\s*[,!?])?\s+(?:there(?:['’]s| is)\s+(?:a\s+)?fire|we have\s+(?:a\s+)?fire)(?:\s+right\s+now)?$"),
    re.compile(r"^(?:there(?:['’]s| is)\s+(?:a\s+)?fire|(?:a\s+)?fire\s+(?:is\s+)?burning)(?:\s+right\s+now)?" + _EN_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:(?:an?\s+)?explosion\s+(?:just\s+happened|is\s+happening)|there\s+was\s+(?:an?\s+)?explosion\s+just\s+now)$"),
    re.compile(r"^(?:there(?:['’]s| is)\s+)?(?:an?\s+)?active\s+(?:gas\s+|chemical\s+)?leak(?:\s+(?:right\s+)?now)?" + _EN_HELP_FOLLOWUP + r"$"),
    re.compile(r"^(?:gas|chemical|liquid|solution|it)\s+is\s+leaking(?:\s+(?:right\s+)?now)?$"),
    re.compile(r"^(?:someone|a\s+person|i|we)\s+(?:is|am|are)\s+(?:seriously|badly|critically)\s+injured$"),
    re.compile(r"^(?:we|i|someone)\s+(?:are|am|is)\s+in\s+immediate\s+danger$"),
)


def normalize_emergency_text(text: str) -> str:
    """Normalize only representation, whitespace, English case, and terminal marks."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _REPEATED_WHITESPACE.sub(" ", normalized).strip().casefold()
    return _TERMINAL_PUNCTUATION.sub("", normalized)


def recognize_emergency(text: str) -> EmergencyMatch | None:
    """Return a fixed-response language only for an explicit current emergency."""
    normalized = normalize_emergency_text(text)
    if not normalized:
        return None
    if re.search(r"[가-힣]", normalized):
        if _KOREAN_EXCLUSIONS.search(normalized):
            return None
        if any(pattern.fullmatch(normalized) for pattern in _KOREAN_IMMEDIATE):
            return EmergencyMatch("ko", KOREAN_EMERGENCY_RESPONSE)
        return None
    if _ENGLISH_EXCLUSIONS.search(normalized):
        return None
    if any(pattern.fullmatch(normalized) for pattern in _ENGLISH_IMMEDIATE):
        return EmergencyMatch("en", ENGLISH_EMERGENCY_RESPONSE)
    return None
