"""Server-authorized Korean completion intent classifier.

Provides deterministic, bounded recognition of natural Korean current-step
completion statements while strictly guarding against questions, negations,
criteria inquiries, and future/hypothetical statements.
"""

from __future__ import annotations

import re

# Punctuation to trim
_PUNCT_RE = re.compile(r"[\s.!?。？！~]+$")

# Guard patterns that MUST NOT be classified as completion
_NEGATIVE_OR_QUESTION_PATTERNS = (
    # Explicit question indicators
    re.compile(r"(?:조건|기준|의미|뜻|방법)(?:이|은|는|을|를|\s*)?(?:뭐|무엇|어떻게|알려|설명|인가|인지)"),
    re.compile(r"(?:완료|끝)(?:한\s*거야|한\s*건가|인가|인가요|인\s*상태|해야\s*해|해야\s*하나요|할까|해도\s*될까|했나요|했습니까)\b"),
    re.compile(r"(?:완료|끝)(?:라는|란|이라는)\s*(?:게|것|말|뜻|의미)"),
    re.compile(r"(?:완료|끝)(?:하면|했을\s*때|했다고\s*치면|한다고\s*가정하면|한\s*뒤에)"),
    re.compile(r"다음\s*단계.*(?:완료|끝)"),
    re.compile(r"(?:몇|어떤)\s*단계.*완료"),
    # Negations
    re.compile(r"(?:아직|안|못)\s*(?:완료|끝|다\s*했)"),
    re.compile(r"(?:완료|끝)(?:하지|내지|나지)\s*(?:않|못)"),
    re.compile(r"(?:완료|끝)\s*(?:안|못)\s*했"),
)

# Positive completion command patterns
_POSITIVE_COMPLETION_PATTERNS = (
    # Standard prefix + completion verb:
    # "현재/지금/이번/이 단계/작업 [도/를/은/는/이/가/로] 완료했어/끝냈어/마쳤어/다 했어"
    re.compile(
        r"^(?:(?:현재|지금|이번|이)\s*(?:단계|작업)?\s*(?:도|는|은|를|을|이|가|로)?\s*)"
        r"(?:완료(?:했어|했어요|했습니다|했으니|했으니까|함)?|"
        r"끝(?:냈어|냈어요|냈습니다|났어|났어요|났습니다)|"
        r"다\s*(?:했어|했어요|했습니다)|"
        r"마쳤(?:어|어요|습니다))$"
    ),
    # Exact noun shorthand: "현재 단계 완료", "이번 단계 완료", "이 단계 완료"
    re.compile(r"^(?:현재|지금|이번|이)\s*(?:단계|작업)\s*완료$"),
    # Natural completion with "여기까지" or "방금":
    re.compile(r"^(?:여기까지|방금\s*(?:작업|단계)?)\s*(?:다\s*했어|다\s*했어요|마쳤어|마쳤어요|끝났어|끝났어요|끝냈어|끝냈어요|완료했어|완료했어요|완료했습니다)$"),
    # Compound completion + proceed:
    # "현재/이번 단계 [도] 완료했으니 다음으로 넘어가자/넘어가줘" or "다 했으니까 다음으로 넘어가줘"
    re.compile(
        r"^(?:(?:현재|지금|이번|이)\s*(?:단계|작업)?\s*(?:도|는|은|를|을|이|가|로)?\s*)?"
        r"(?:완료(?:했어|했어요|했습니다|했으니|했으니까|했으므로)|"
        r"끝(?:냈어|냈어요|냈습니다|났어|났어요|났습니다|냈으니|냈으니까|났으니|났으니까)|"
        r"다\s*(?:했어|했어요|했습니다|했으니|했으니까)|"
        r"마쳤(?:어|어요|습니다|으니|으니까))\s*"
        r".*(?:다음(?:\s*단계)?|다음으로|넘어가|넘어가자|넘어가줘|넘어가주세요|가자).*"
        r"(?:안내|알려|넘어|진행|가자|줘|요)?$"
    ),
)


def _normalize_conversational_utterance(text: str) -> str:
    # Strip leading fillers e.g. "Okay,", "어 음", "네", "좋아"
    cleaned = re.sub(r"^(?:okay|ok|네|예|어\s*음|어|음|좋아|아|그래|자|그럼)[\s,]+", "", text, flags=re.IGNORECASE)
    # Deduplicate repeated words e.g. "현재 현재" -> "현재", "지금 지금" -> "지금"
    cleaned = re.sub(r"\b(\w+)\s+\1\b", r"\1", cleaned)
    return cleaned.strip()


def classify_korean_completion_command(transcript: str, language: str = "ko") -> bool:
    """Determine whether an utterance is an authorized Korean current-step completion command."""
    if language != "ko" or not isinstance(transcript, str):
        return False

    raw = transcript.strip()
    if not raw:
        return False

    # If ending with a question mark in the original utterance, reject immediately
    if raw.endswith("?") or raw.endswith("？"):
        return False

    trimmed = _PUNCT_RE.sub("", raw)
    if not trimmed:
        return False

    # Normalize conversational stutter/fillers
    normalized = _normalize_conversational_utterance(trimmed)
    if not normalized:
        return False

    # Check negative/question guards on both original trimmed and normalized
    for guard in _NEGATIVE_OR_QUESTION_PATTERNS:
        if guard.search(trimmed) or guard.search(normalized):
            return False

    # Check positive patterns
    for pattern in _POSITIVE_COMPLETION_PATTERNS:
        if pattern.match(trimmed) or pattern.match(normalized):
            return True

    return False
