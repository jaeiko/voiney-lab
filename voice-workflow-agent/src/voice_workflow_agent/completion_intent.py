"""Server-authorized Korean and English completion intent classifier.

Provides deterministic, bounded recognition of natural current-step and explicit
numbered-step completion statements while strictly guarding against questions,
negations, criteria inquiries, and future/hypothetical statements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

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
    """Classify if the user is asking about the rationale, purpose, or common mistakes of a step/procedure."""
    normalized = _normalize_conversational_utterance(text).casefold()
    learning_patterns = (
        r"(?:왜\s*(?:이|이번|해당|이런)?\s*(?:단계|것|거|작업|과정)?|이\s*단계(?:는|를|가|도)?\s*왜|단계(?:가|는)?\s*왜|왜\s*(?:해야\s*(?:돼|되|하)|필요|하는)|필요한\s*이유|목적이\s*(?:뭐|무엇)|목적\s*(?:알려|설명)|이걸\s*왜|왜\s*해야\s*돼|왜\s*해야\s*되)",
        r"(?:흔한\s*실수|자주\s*하는\s*실수|주의해야\s*할\s*(?:점|실수)|주의할\s*점|주의사항|실수하기\s*쉬운|조심해야\s*할)",
        r"(?:원리가\s*(?:뭐|무엇)|이유가\s*(?:뭐|무엇)|이유를?\s*설명|원리를?\s*설명|배경\s*설명)",
        r"\b(?:why\s+(?:do\s+we\s+)?(?:do|need)\s+this\s+step|why\s+is\s+this\s+step|purpose\s+of\s+(?:this\s+)?(?:step|procedure)|common\s+mistakes|what\s+mistakes|precautions)\b",
    )
    return any(re.search(p, normalized) is not None for p in learning_patterns)


def is_version_question(text: str) -> bool:
    """Classify if the user is asking about active protocol version, document origin, or cryptographic hash."""
    normalized = _normalize_conversational_utterance(text).casefold()
    version_patterns = (
        r"(?:프로토콜(?:의)?\s*버전|sop(?:의)?\s*버전|절차(?:의)?\s*버전|문서(?:의)?\s*버전|버전(?:이|을)?\s*(?:뭐|무엇|몇|알려|확인)|몇\s*버전|버전\s*(?:정보|확인|알려))",
        r"(?:프로토콜(?:의)?\s*해시|문서(?:의)?\s*해시|sha256|해시값|해시(?:를|의)?\s*(?:정보|알려|확인))",
        r"\b(?:protocol\s+version|sop\s+version|which\s+version|document\s+version|protocol\s+hash|sha256|document\s+hash)\b",
    )
    return any(re.search(p, normalized) is not None for p in version_patterns)


def is_history_or_continuation_intent(text: str) -> tuple[str, str | None] | None:
    """Classify if the user is asking to view previous experiments or resume/continue an experiment."""
    normalized = _normalize_conversational_utterance(text).casefold()
    if any(re.search(p, normalized) is not None for p in (
        r"(?:어제|이전|지난|전에|기존)\s*(?:하던|진행하던)?\s*(?:것|실험|세션|워크플로)?\s*(?:을|를)?\s*(?:이어서|이어줘|계속|불러와|재개)",
        r"\b(?:continue\s+(?:the\s+)?(?:previous\s+)?experiment|resume\s+experiment|continue\s+yesterday)\b",
    )):
        return "continue", None
    if any(re.search(p, normalized) is not None for p in (
        r"(?:이전|최근|과거|지난)?\s*(?:실험|세션|워크플로|기록|이력)+(?:\s*(?:목록|이력|기록|내역))?\s*(?:보여|조회|알려|리스트|확인|불러)",
        r"\b(?:experiment\s+history|recent\s+experiments|previous\s+sessions|list\s+experiments)\b",
    )):
        return "history", None
    return None


def is_combined_learning_and_next_question(text: str) -> bool:
    """Classify compound queries asking why the current step is done and what the next step is."""
    normalized = _normalize_conversational_utterance(text).casefold()
    has_why = any(re.search(p, normalized) is not None for p in (
        r"(?:왜\s*(?:하는지|해야\s*(?:하는지|돼|되|하는)|필요한지)|이유|목적)",
        r"\b(?:why\s+(?:we\s+do|this\s+step)|purpose)\b",
    ))
    has_next = any(re.search(p, normalized) is not None for p in (
        r"(?:다음\s*단계(?:도)?\s*(?:알려|설명|뭐|무엇)|다음(?:으로)?\s*(?:알려|설명))",
        r"\b(?:next\s+step|what(?:'s|\s+is)\s+next)\b",
    ))
    return has_why and has_next


def is_speculative_or_uncertainty_question(text: str) -> bool:
    """Classify speculative outcome or ungrounded experiment prediction questions."""
    normalized = _normalize_conversational_utterance(text).casefold()
    patterns = (
        r"(?:실험\s*(?:결과(?:가)?)?\s*(?:성공|잘\s*될|실패)|결과가\s*(?:성공|잘\s*나올|좋을)|성공할까|성공할\s*수\s*있을까|잘\s*될까|성공할지|성공\s*여부|망할까)",
        r"\b(?:will\s+(?:this\s+)?experiment\s+succeed|will\s+it\s+work|is\s+it\s+successful)\b",
    )
    return any(re.search(p, normalized) is not None for p in patterns)
