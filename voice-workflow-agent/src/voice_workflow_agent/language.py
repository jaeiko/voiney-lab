"""Trusted STT language normalization and deterministic per-turn resolution."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SUPPORTED_LANGUAGES = frozenset({"ko", "en", "vi"})
PROVIDER_LANGUAGE_NAMES = {
    "korean": "ko",
    "한국어": "ko",
    "ko": "ko",
    "ko-kr": "ko",
    "english": "en",
    "영어": "en",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "vietnamese": "vi",
    "tiếng việt": "vi",
    "vi": "vi",
    "vi-vn": "vi",
}


def normalize_provider_language(value: Any) -> str | None:
    """Map only explicit, supported provider values; malformed values are unresolved."""
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().casefold().replace("_", "-").split())
    return PROVIDER_LANGUAGE_NAMES.get(normalized) if normalized else None


@dataclass(frozen=True)
class Transcription:
    text: str
    detected_language: str | None
    # xAI's documented batch STT response exposes duration and optional word
    # timing objects.  The legacy quality fields remain optional compatibility
    # seams for injected providers/tests; the xAI adapter does not fabricate or
    # populate them when the REST response does not document them.
    confidence: float | None = None
    no_speech_probability: float | None = None
    alternatives: tuple[str, ...] = ()
    duration_seconds: float | None = None
    words: tuple[dict[str, object], ...] = ()
    response_status: int | None = None


@dataclass(frozen=True)
class ServerVoicePolicy:
    """Server-owned voice policy for laboratory workflow sessions."""

    response_language: str = "ko"
    stt_language: str = "ko"
    tts_language: str = "ko"


@dataclass(frozen=True)
class KoreanTranscriptionAdmission:
    """Bounded transcript admission decision for server-owned Korean voice policy."""

    raw_text: str
    admitted_text: str
    detected_language: str | None
    expected_language: str = "ko"
    mismatch_status: str | None = None
    correction_class: str | None = None
    clarification_required: bool = False
    clarification_message: str | None = None


_SHORT_KOREAN_DISCOURSE_ALIASES = {
    "ani": "아니",
    "ani.": "아니.",
    "ani?": "아니?",
    "annie": "아니",
    "annie.": "아니.",
    "annie?": "아니?",
    "anny": "아니",
    "anny.": "아니.",
    "anny?": "아니?",
}


def classify_korean_admission(
    raw_text: str,
    detected_language: str | None,
    *,
    expected_language: str = "ko",
) -> KoreanTranscriptionAdmission:
    """Evaluate transcript against the server-owned Korean voice policy."""
    cleaned = raw_text.strip()
    if not cleaned:
        return KoreanTranscriptionAdmission(
            raw_text=raw_text,
            admitted_text="",
            detected_language=detected_language,
            expected_language=expected_language,
            mismatch_status="empty",
            clarification_required=True,
            clarification_message="음성을 인식하지 못했습니다. 다시 말씀해 주세요.",
        )

    lowered = cleaned.casefold()
    if lowered in _SHORT_KOREAN_DISCOURSE_ALIASES:
        admitted = _SHORT_KOREAN_DISCOURSE_ALIASES[lowered]
        return KoreanTranscriptionAdmission(
            raw_text=raw_text,
            admitted_text=admitted,
            detected_language=detected_language,
            expected_language=expected_language,
            correction_class="short_korean_discourse_alias",
            clarification_required=False,
        )

    # If text contains Hangul characters, admit directly
    if _HANGUL.search(cleaned):
        return KoreanTranscriptionAdmission(
            raw_text=raw_text,
            admitted_text=raw_text,
            detected_language=detected_language,
            expected_language=expected_language,
            clarification_required=False,
        )

    # Check if text is known scientific/technical terminology (e.g. "AMBIC", "HPLC water", "SDS-PAGE")
    tokens = [t.casefold() for t in re.findall(r"[0-9A-Za-z_-]+", cleaned)]
    scientific_known = {
        "ambic", "ammonium", "bicarbonate", "hplc", "water", "acetonitrile",
        "dtt", "iodoacetamide", "trypsin", "sds", "page", "sds-page", "gel",
        "thermomixer", "rpm", "evotip", "formic", "acid", "lc-ms", "lc", "ms",
        "solution", "a", "b", "plug", "band", "keratin", "contamination",
    }
    if tokens and all(t in scientific_known or t.isdigit() for t in tokens):
        return KoreanTranscriptionAdmission(
            raw_text=raw_text,
            admitted_text=raw_text,
            detected_language=detected_language,
            expected_language=expected_language,
            clarification_required=False,
        )

    # Check for strong English request / contradiction without Hangul
    ko_chars, en_words, _ = _signals(cleaned)
    if ko_chars == 0 and (en_words >= 1 or len(tokens) >= 3 or detected_language == "en"):
        return KoreanTranscriptionAdmission(
            raw_text=raw_text,
            admitted_text=raw_text,
            detected_language=detected_language,
            expected_language=expected_language,
            mismatch_status="contradiction",
            clarification_required=True,
            clarification_message="음성을 정확히 인식하지 못했어요. 다시 말씀해 주세요.",
        )

    return KoreanTranscriptionAdmission(
        raw_text=raw_text,
        admitted_text=raw_text,
        detected_language=detected_language,
        expected_language=expected_language,
        clarification_required=False,
    )


@dataclass(frozen=True)
class InputEventDecision:
    """Shared post-STT decision made before a user Turn is committed."""

    accepted: bool
    reason: str | None = None


_NON_LEXICAL_EVENT = re.compile(
    r"^\s*[\[(<{]?\s*(?:"
    r"cough(?:ing)?|throat[ -]?clear(?:ing)?|clears? throat|sniff(?:ing)?|"
    r"sneez(?:e|ing)|breath(?:ing)?|laugh(?:ter|ing)?|keyboard|typing|"
    r"tap(?:ping)?|impact|chair(?: movement)?|noise|silence|music|"
    r"unintelligible|inaudible|기침(?:\s*소리)?|헛기침|목\s*가다듬는\s*소리|"
    r"훌쩍(?:임)?|재채기|숨\s*소리|호흡|웃음(?:\s*소리)?|키보드|타자|"
    r"두드리는\s*소리|충격음|의자\s*소리|소음|무음|음악|알아들을\s*수\s*없음"
    r")\s*[\])>}]?\s*[.!?。！？]*\s*$",
    re.IGNORECASE,
)


_CANONICAL_COMMAND_TERMS = (
    # Korean workflow terms
    "프로토콜 시작", "현재 단계", "다음 단계", "다음 단계 미리보기",
    "완료", "현재 단계 완료", "완료했어요", "다 했어요", "완료 조건",
    "네", "아니요", "다시 알려줘", "프로토콜 중단", "이상 사항 기록",
    "관찰 결과", "완전히 탈색", "투명해요", "투명한가요", "흰색으로 변했어요",
    "아직 색이 남아 있어요",
    # English workflow terms / STT translations
    "protocol start", "proton start", "pro protocol start", "current step", "current stage",
    "next step", "next stage", "next step preview", "preview step", "step preview",
    "complete", "current step complete", "current stage complete", "i did it",
    "complete condition", "completion condition", "yes", "no", "tell me again",
    "protocol interruption", "protocol stop", "anomaly record", "abnormality record",
    "observation result", "completely decontaminated", "completely destained",
    "transparent", "white changed", "color is still there", "stain still remains",
)


def _normalized_keyterm_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token for token in re.findall(r"[0-9A-Za-z가-힣µμ°%./-]+", value.casefold())
        if token
    )


def _is_keyterm_echo(
    transcription: Transcription,
    *,
    keyterms: tuple[str, ...] | None = None,
    duration_seconds: float | None = None,
) -> bool:
    """Reject STT concatenations that are almost only injected keyterms / vocabulary dumps.

    Differentiates authentic single/double workflow commands (e.g. "현재 단계 완료했어")
    from STT model hallucinations that regurgitate the prompt / keyterm catalog
    as an enumerative concatenation in Korean, English, or mixed languages.
    """

    text = " ".join(str(transcription.text or "").split())
    if not text:
        return False
    tokens = _normalized_keyterm_tokens(text)
    if len(tokens) < 3:
        return False

    all_terms: list[str] = list(keyterms or ()) + list(_CANONICAL_COMMAND_TERMS)
    catalog: list[tuple[str, ...]] = []
    for term in all_terms:
        if not isinstance(term, str):
            continue
        parts = _normalized_keyterm_tokens(term)
        if parts:
            catalog.append(parts)
    if not catalog:
        return False

    # Sort catalog by length descending for greedy matching
    catalog.sort(key=len, reverse=True)

    covered = 0
    index = 0
    matched_distinct_terms: set[tuple[str, ...]] = set()

    while index < len(tokens):
        matched = 0
        matched_term = None
        for parts in catalog:
            width = len(parts)
            if width and index + width <= len(tokens) and tokens[index:index + width] == parts:
                matched = width
                matched_term = parts
                break
        if matched == 0:
            index += 1
            continue
        covered += matched
        if matched_term:
            matched_distinct_terms.add(matched_term)
        index += matched

    coverage_ratio = covered / len(tokens) if tokens else 0.0
    punct_count = len(re.findall(r"[,.·/?!]", text))

    # 1. Obvious large vocabulary dump: 4 or more distinct keyterm phrases with high coverage
    if len(matched_distinct_terms) >= 4 and coverage_ratio >= 0.65:
        return True

    # 2. Enumerative listing of 3+ keyterm commands with list punctuation
    if len(matched_distinct_terms) >= 3 and coverage_ratio >= 0.75 and punct_count >= 2:
        return True

    # 3. Dense keyterm repetition without natural syntax when length >= 4 tokens and coverage >= 0.85
    if len(tokens) >= 4 and coverage_ratio >= 0.85 and not has_language_bearing_content(text):
        return True

    # 4. Long sequence (>= 5 tokens) of catalog terms with >= 3 distinct terms and >= 80% coverage
    if len(tokens) >= 5 and len(matched_distinct_terms) >= 3 and coverage_ratio >= 0.80:
        return True

    return False


def classify_input_event(
    transcription: Transcription,
    keyterms: tuple[str, ...] | None = None,
    duration_seconds: float | None = None,
) -> InputEventDecision:
    """Reject only whole-event non-speech labels and explicit provider no-speech.

    The raw transcript remains available to diagnostics.  Substrings in real
    utterances (for example, "I coughed") are deliberately not rejected, and
    valid short workflow commands are never classified by length alone.
    Optional keyterm metadata rejects bias-echo concatenations without treating
    a lone injected term such as AMBIC as noise.
    """

    issue = transcription_quality_issue(transcription)
    if issue == "provider_no_speech_probability":
        return InputEventDecision(False, issue)
    if _NON_LEXICAL_EVENT.fullmatch(transcription.text):
        return InputEventDecision(False, "non_lexical_event")
    if _is_keyterm_echo(
        transcription, keyterms=keyterms, duration_seconds=duration_seconds,
    ):
        return InputEventDecision(False, "keyterm_echo")
    return InputEventDecision(True)


def transcription_quality_issue(transcription: Transcription) -> str | None:
    """Use provider quality metadata only when it is actually supplied."""

    if (
        transcription.no_speech_probability is not None
        and 0 <= transcription.no_speech_probability <= 1
        and transcription.no_speech_probability >= 0.8
    ):
        return "provider_no_speech_probability"
    if (
        transcription.confidence is not None
        and 0 <= transcription.confidence <= 1
        and transcription.confidence <= 0.25
    ):
        return "provider_low_confidence"
    return None


@dataclass(frozen=True)
class LanguageResolution:
    language: str | None
    reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.language in SUPPORTED_LANGUAGES


_HANGUL = re.compile(r"[가-힣]")
_LATIN_WORD = re.compile(r"[A-Za-zÀ-ỹ]+")
_IDENTIFIER = re.compile(
    r"^(?:[A-Z0-9][A-Z0-9._/-]*|(?:\d{2,7}-\d{2}-\d)|[A-Za-z]+\d+[A-Za-z0-9-]*)$",
    re.I,
)
_KO_ENDINGS = re.compile(r"(?:요|니다|니까|나요|세요|해|줘|인가|있어|없어|해야|습니까)(?:[.!?。？！]|$)")
_EN_REQUEST_WORDS = frozenset({
    "what", "where", "when", "which", "who", "how", "can", "could", "should",
    "do", "does", "did", "is", "are", "please", "tell", "show", "need", "use",
    "handle", "clean", "wear", "report", "help", "spill", "exposure",
})
_VI_REQUEST_WORDS = frozenset({
    "là", "gì", "ở", "đâu", "khi", "nào", "như", "thế", "nào", "xin", "hãy",
    "cần", "phải", "tôi", "bị", "tràn", "đổ", "phơi", "nhiễm",
})


def _signals(text: str) -> tuple[int, int, int]:
    ko_chars = len(_HANGUL.findall(text))
    words = [word.casefold() for word in _LATIN_WORD.findall(text)]
    en = sum(word in _EN_REQUEST_WORDS for word in words)
    vi = sum(word in _VI_REQUEST_WORDS or any(char in word for char in "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ") for word in words)
    return ko_chars, en, vi


def has_language_bearing_content(text: str) -> bool:
    cleaned = " ".join(text.split())
    if not cleaned:
        return False
    tokens = cleaned.split()
    # A lone lexical item can be a chemical/product name in any language.
    # Without a natural-language predicate or request it cannot establish a
    # safety-answer language, regardless of what STT labels it.
    if len(tokens) == 1:
        return False
    ko, en, vi = _signals(cleaned)
    return bool(_KO_ENDINGS.search(cleaned) or ko >= 3 or en or vi)


def resolve_turn_language(
    transcript: str,
    detected_language: str | None,
    *,
    mode: str,
    manual_language: str | None = None,
) -> LanguageResolution:
    """Resolve one turn without consulting model output or Tool arguments."""
    explicit = None
    lowered = transcript.casefold()
    if re.search(r"(?:한국어로|in\s+korean)", lowered):
        explicit = "ko"
    elif re.search(r"(?:영어로|in\s+english)", lowered):
        explicit = "en"
    elif re.search(r"(?:베트남어로|in\s+vietnamese)", lowered):
        explicit = "vi"
    if explicit is not None:
        return LanguageResolution(explicit)
    if mode == "manual":
        if manual_language not in SUPPORTED_LANGUAGES:
            return LanguageResolution(None, "invalid_manual_language")
        # Fixed Korean session policy: do not let unverified Latin/English STT
        # override manual Korean session authority.
        if manual_language == "ko":
            return LanguageResolution("ko")
        if has_language_bearing_content(transcript):
            ko, en, vi = _signals(transcript)
            korean = ko >= 8 or bool(_KO_ENDINGS.search(transcript))
            english = en >= 2
            vietnamese = vi >= 2
            if sum((korean, english, vietnamese)) == 1:
                return LanguageResolution(
                    "ko" if korean else "en" if english else "vi"
                )
        if manual_language in SUPPORTED_LANGUAGES:
            return LanguageResolution(manual_language)
        return LanguageResolution(None, "invalid_manual_language")
    if mode != "auto":
        return LanguageResolution(None, "invalid_mode")
    if detected_language not in SUPPORTED_LANGUAGES:
        return LanguageResolution(None, "language_unresolved")
    if not has_language_bearing_content(transcript):
        return LanguageResolution(None, "insufficient_language_content")

    ko, en, vi = _signals(transcript)
    # Two substantial natural-language signals are ambiguous. Product names,
    # labels, codes and isolated foreign tokens do not meet these thresholds.
    substantial = sum((ko >= 8 or bool(_KO_ENDINGS.search(transcript)), en >= 2, vi >= 2))
    if substantial > 1:
        return LanguageResolution(None, "ambiguous_mixed_language")
    if detected_language == "ko" and ko < 3 and en >= 2:
        return LanguageResolution(None, "provider_transcript_contradiction")
    if detected_language == "en" and (ko >= 8 or bool(_KO_ENDINGS.search(transcript))) and en == 0:
        return LanguageResolution(None, "provider_transcript_contradiction")
    if detected_language == "vi" and vi == 0 and (en >= 2 or ko >= 8):
        return LanguageResolution(None, "provider_transcript_contradiction")
    return LanguageResolution(detected_language)


CLARIFICATION_TEXT = {
    "ko": "음성을 정확히 인식하지 못했어요. 다시 말씀해 주세요.",
    "en": "Please repeat your question.",
    "vi": "Vui lòng nhắc lại câu hỏi.",
}


def clean_speech_text(text: str) -> str:
    """Strip markdown syntax, headers, bullets, bolding, and links from spoken audio text."""
    if not isinstance(text, str) or not text.strip():
        return ""
    # Remove markdown headers like ### Heading or ## Heading
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    # Remove list bullets like - bullet or • bullet or * bullet or 1. bullet
    cleaned = re.sub(r"^\s*[-*•]\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*\d+\.\s*", "", cleaned, flags=re.MULTILINE)
    # Remove bold / italic markers **word** or *word*
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    # Remove markdown links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    # Remove citation brackets like [1] or [CID 14013]
    cleaned = re.sub(r"\[[0-9A-Za-z\s_-]+\]", "", cleaned)
    # Normalize whitespace
    return " ".join(cleaned.split())
