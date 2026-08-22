"""Server-authorized Korean and English completion intent classifier.

Provides deterministic, bounded recognition of natural current-step and explicit
numbered-step completion statements while strictly guarding against questions,
negations, criteria inquiries, and future/hypothetical statements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from voice_workflow_agent.intent_arbitration import (
    RequestIntent,
    arbitrate_request,
)

# Punctuation to trim
_PUNCT_RE = re.compile(r"[\s.!?。？！~]+$")

_KOREAN_NUMERALS: dict[str, int] = {
    "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5,
    "육": 6, "칠": 7, "팔": 8, "구": 9, "십": 10,
    "십일": 11, "십이": 12, "십삼": 13, "십사": 14, "십오": 15,
    "십육": 16, "십칠": 17, "십팔": 18, "십구": 19, "이십": 20,
    "이십일": 21, "이십이": 22, "이십삼": 23, "이십사": 24, "이십오": 25,
}

# Guard patterns that MUST NOT be classified as completion
_NEGATIVE_OR_QUESTION_PATTERNS = (
    # Explicit question indicators
    re.compile(r"(?:조건|기준|의미|뜻|방법)(?:이|은|는|을|를|\s*)?(?:뭐|무엇|어떻게|알려|설명|인가|인지)"),
    re.compile(r"(?:완료|끝)(?:한\s*거야|한\s*건가|인가|인가요|인\s*상태|해야\s*해|해야\s*하나요|할까|해도\s*될까|했나요|했습니까)\b"),
    re.compile(r"(?:완료|끝)(?:라는|란|이라는)\s*(?:게|것|말|뜻|의미)"),
    re.compile(r"(?:완료|끝)(?:하면|했을\s*때|했다고\s*치면|한다고\s*가정하면|한\s*뒤에|한다면)"),
    re.compile(r"(?:완료|끝|마칠)\s*(?:할게|할\s*거야|하겠|예정|하려고|하려\s*함)"),
    re.compile(r"다음\s*단계.*(?:완료|끝)"),
    re.compile(r"(?:몇|어떤)\s*단계.*완료"),
    # Negations
    re.compile(r"(?:아직|안|못)\s*(?:완료|끝|다\s*했)"),
    re.compile(r"(?:완료|끝)(?:하지|내지|나지)\s*(?:않|못)"),
    re.compile(r"(?:완료|끝)\s*(?:안|못)\s*했"),
)

# Numbered step reference extraction regex
_STEP_NUM_PREFIX = (
    r"(?:(?P<num>[1-9]|1[0-9]|2[0-5])\s*단계|"
    r"step\s*(?P<en_num>[1-9]|1[0-9]|2[0-5])|"
    r"(?P<kor_num>일|이|삼|사|오|육|칠|팔|구|십(?:[일이삼사오육칠팔구])?|이십(?:[일이삼사오육칠팔구])?)\s*단계)"
)

# Positive completion command patterns (Current step)
_POSITIVE_COMPLETION_PATTERNS = (
    # Standard prefix + optional adverbs + completion verb:
    # "현재/지금/이번/이 단계/작업 [도/를/은/는/이/가/로] [미리/이미/벌써/방금/아까/다/완전히/모두] 완료했어/끝냈어/마쳤어/다 했어"
    re.compile(
        r"^(?:(?:현재|지금|이번|이)\s*(?:단계|작업)?\s*(?:도|는|은|를|을|이|가|로)?\s*)"
        r"(?:미리|이미|벌써|방금|아까|다|완전히|모두)?\s*"
        r"(?:완료(?:했어|했어요|했습니다|했으니|했으니까|함)?|"
        r"끝(?:냈어|냈어요|냈습니다|났어|났어요|났습니다)|"
        r"다\s*(?:했어|했어요|했습니다)|"
        r"마쳤(?:어|어요|습니다))$"
    ),
    # Exact noun shorthand: "현재 단계 완료", "이번 단계 완료", "이 단계 완료"
    re.compile(r"^(?:현재|지금|이번|이)\s*(?:단계|작업)\s*완료$"),
    # Natural completion with "여기까지", "방금", "벌써", "이미", "미리":
    re.compile(r"^(?:여기까지|방금\s*(?:작업|단계)?|벌써|이미|미리)\s*(?:다\s*했어|다\s*했어요|마쳤어|마쳤어요|끝났어|끝났어요|끝냈어|끝냈어요|완료했어|완료했어요|완료했습니다)$"),
    # Compound completion + proceed:
    # "현재/이번 단계 [도] [미리/이미] 완료했으니 다음으로 넘어가자/넘어가줘" or "다 했으니까 다음으로 넘어가줘"
    re.compile(
        r"^(?:(?:현재|지금|이번|이)\s*(?:단계|작업)?\s*(?:도|는|은|를|을|이|가|로)?\s*)?"
        r"(?:미리|이미|벌써|방금|아까|다|완전히|모두)?\s*"
        r"(?:완료(?:했어|했어요|했습니다|했으니|했으니까|했으므로)|"
        r"끝(?:냈어|냈어요|냈습니다|났어|났어요|났습니다|냈으니|냈으니까|났으니|났으니까)|"
        r"다\s*(?:했어|했어요|했습니다|했으니|했으니까)|"
        r"마쳤(?:어|어요|습니다|으니|으니까))\s*"
        r".*(?:다음(?:\s*단계)?|다음으로|넘어가|넘어가자|넘어가줘|넘어가주세요|가자).*"
        r"(?:안내|알려|넘어|진행|가자|줘|요)?$"
    ),
)

# Numbered step completion patterns
_NUMBERED_COMPLETION_PATTERNS = (
    # "[이번/현재]? N단계 [도/는/은/를/을/이/가/로/까지] [미리/이미/벌써/방금/아까/다/완전히/모두]? 완료했어/끝냈어/다 했어/마쳤어/완료"
    re.compile(
        rf"^(?:(?:현재|지금|이번|이)\s*)?{_STEP_NUM_PREFIX}\s*(?:도|는|은|를|을|이|가|로|까지)?\s*"
        r"(?:미리|이미|벌써|방금|아까|다|완전히|모두)?\s*"
        r"(?:완료(?:했어|했어요|했습니다|했으니|했으니까|함)?|"
        r"끝(?:냈어|냈어요|냈습니다|났어|났어요|났습니다)|"
        r"다\s*(?:했어|했어요|했습니다)|"
        r"했어|했어요|했습니다|"
        r"마쳤(?:어|어요|습니다))$",
        re.IGNORECASE,
    ),
    # Exact noun shorthand: "[이번]? N단계 완료"
    re.compile(
        rf"^(?:(?:현재|지금|이번|이)\s*)?{_STEP_NUM_PREFIX}\s*완료$",
        re.IGNORECASE,
    ),
    # English: "Step N is done", "I completed step N", "Step N completed", "yep step N done"
    re.compile(
        r"^(?:(?:i\s+(?:have\s+)?(?:completed|finished|done)|yep|yes|ok|okay)\s+)?step\s*(?P<en_num>[1-9]|1[0-9]|2[0-5])(?:\s+(?:is\s+)?(?:done|complete|completed|finished))?$",
        re.IGNORECASE,
    ),
    # Compound numbered completion + proceed:
    # "N단계 [도] [미리/이미] 완료했으니 다음으로 넘어가자/넘어가줘"
    re.compile(
        rf"^(?:(?:현재|지금|이번|이)\s*)?{_STEP_NUM_PREFIX}\s*(?:도|는|은|를|을|이|가|로|까지)?\s*"
        r"(?:미리|이미|벌써|방금|아까|다|완전히|모두)?\s*"
        r"(?:완료(?:했어|했어요|했습니다|했으니|했으니까|했으므로)|"
        r"끝(?:냈어|냈어요|냈습니다|났어|났어요|났습니다|냈으니|냈으니까|났으니|났으니까)|"
        r"다\s*(?:했어|했어요|했습니다|했으니|했으니까)|"
        r"마쳤(?:어|어요|습니다|으니|으니까))\s*"
        r".*(?:다음(?:\s*단계)?|다음으로|넘어가|넘어가자|넘어가줘|넘어가주세요|가자).*"
        r"(?:안내|알려|넘어|진행|가자|줘|요)?$",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class CompletionIntentDecision:
    is_completion: bool
    target_kind: Literal["current", "explicit_step", "none"] = "none"
    target_step_number: int | None = None
    target_step_label: str | None = None
    temporal_status: str = "past_completed"  # "past_completed" | "future_plan" | "hypothetical" | "unresolved"
    mutation_candidate: bool = True
    confidence: float = 1.0
    normalized_transcript: str = ""


def _normalize_conversational_utterance(text: str) -> str:
    # Strip leading fillers e.g. "Okay,", "어 음", "네", "응", "좋아", "자", "맞아요", "네 맞아요", "yep", "yes", "alright"
    cleaned = re.sub(r"^(?:okay|ok|yep|yes|alright|sure|네\s*맞아요|맞아요|네|예|응|어\s*음|어|음|좋아|아|그래|자|그럼)[\s,]+", "", text, flags=re.IGNORECASE)
    # Deduplicate repeated words e.g. "현재 현재" -> "현재", "지금 지금" -> "지금"
    cleaned = re.sub(r"\b(\w+)\s+\1\b", r"\1", cleaned)
    return cleaned.strip()


def resolve_korean_completion_decision(
    transcript: str,
    language: str = "ko",
) -> CompletionIntentDecision:
    """Evaluate completion intent and extract explicit step target if present."""
    if not isinstance(transcript, str):
        return CompletionIntentDecision(is_completion=False, target_kind="none")

    raw = transcript.strip()
    if not raw:
        return CompletionIntentDecision(is_completion=False, target_kind="none")

    # If ending with a question mark in the original utterance, reject immediately
    if raw.endswith("?") or raw.endswith("？"):
        return CompletionIntentDecision(
            is_completion=False,
            target_kind="none",
            temporal_status="hypothetical",
            mutation_candidate=False,
        )

    trimmed = _PUNCT_RE.sub("", raw)
    if not trimmed:
        return CompletionIntentDecision(is_completion=False, target_kind="none")

    # Normalize conversational stutter/fillers
    normalized = _normalize_conversational_utterance(trimmed)
    if not normalized:
        return CompletionIntentDecision(is_completion=False, target_kind="none")

    # Check negative/question guards on both original trimmed and normalized
    for guard in _NEGATIVE_OR_QUESTION_PATTERNS:
        if guard.search(trimmed) or guard.search(normalized):
            return CompletionIntentDecision(
                is_completion=False,
                target_kind="none",
                temporal_status="unresolved",
                mutation_candidate=False,
            )

    # Check numbered step patterns first
    for pattern in _NUMBERED_COMPLETION_PATTERNS:
        for candidate in (trimmed, normalized):
            match = pattern.match(candidate)
            if match:
                groups = match.groupdict()
                step_num = None
                if groups.get("num"):
                    step_num = int(groups["num"])
                elif groups.get("en_num"):
                    step_num = int(groups["en_num"])
                elif groups.get("kor_num") and groups["kor_num"] in _KOREAN_NUMERALS:
                    kor = groups["kor_num"]
                    # "이 단계" with space is the demonstrative "this step" (current step)
                    if kor == "이" and re.search(r"(?:^|\s)이\s+단계", candidate):
                        step_num = None
                    else:
                        step_num = _KOREAN_NUMERALS[kor]

                if step_num is not None:
                    return CompletionIntentDecision(
                        is_completion=True,
                        target_kind="explicit_step",
                        target_step_number=step_num,
                        target_step_label=str(step_num),
                        temporal_status="past_completed",
                        mutation_candidate=True,
                        confidence=1.0,
                        normalized_transcript=normalized,
                    )
                return CompletionIntentDecision(
                    is_completion=True,
                    target_kind="current",
                    target_step_number=None,
                    target_step_label=None,
                    temporal_status="past_completed",
                    mutation_candidate=True,
                    confidence=1.0,
                    normalized_transcript=normalized,
                )

    # Check standard current-step patterns
    for pattern in _POSITIVE_COMPLETION_PATTERNS:
        if pattern.match(trimmed) or pattern.match(normalized):
            return CompletionIntentDecision(
                is_completion=True,
                target_kind="current",
                target_step_number=None,
                target_step_label=None,
                temporal_status="past_completed",
                mutation_candidate=True,
                confidence=1.0,
                normalized_transcript=normalized,
            )

    return CompletionIntentDecision(is_completion=False, target_kind="none")


def classify_korean_completion_command(transcript: str, language: str = "ko") -> bool:
    """Determine whether an utterance is an authorized current-step or explicit-step completion command."""
    decision = resolve_korean_completion_decision(transcript, language=language)
    return decision.is_completion


def is_learning_question(text: str) -> bool:
    """Compatibility wrapper over the shared production request arbiter."""

    return arbitrate_request(text).intent is RequestIntent.LEARNING


def is_version_question(text: str) -> bool:
    """Compatibility wrapper over the shared production request arbiter."""

    return arbitrate_request(text).intent is RequestIntent.PROTOCOL_AUDIT


def is_history_or_continuation_intent(text: str) -> tuple[str, str | None] | None:
    """Compatibility wrapper over the shared production request arbiter."""

    decision = arbitrate_request(text)
    if decision.intent is not RequestIntent.HISTORY_RESUME:
        return None
    return (
        "continue" if decision.history_action == "resume" else "history",
        None,
    )


def is_combined_learning_and_next_question(text: str) -> bool:
    """Compatibility wrapper over the shared production request arbiter."""

    return arbitrate_request(text).intent is RequestIntent.COMBINED_LEARNING_NEXT


def is_speculative_or_uncertainty_question(text: str) -> bool:
    """Compatibility wrapper over the shared production request arbiter."""

    return arbitrate_request(text).intent is RequestIntent.UNCERTAINTY
