"""One boundary where approved source text becomes a Korean answer.

The approved protocol revision is authoritative and it is often written in
English. Reading English sentences aloud to a Korean-speaking researcher who
asked a Korean question is bad product behaviour, but paraphrasing an approved
protocol is *dangerous* product behaviour. This module is the single place where
those two facts are reconciled, so translation never gets sprinkled through code
that mutates workflow state.

The rules it enforces:

1. The approved source is authoritative and is never edited, re-ordered or
   summarised. It is carried verbatim and stays available under 원문 보기.
2. A reviewer-approved Korean sidecar always wins. Nothing is generated when an
   approved translation already exists.
3. A runtime translation is labelled as a runtime translation — "자동 번역",
   never "검증된 한국어 번역". A machine translation that claims review it never
   had is worse than showing English.
4. Every number, unit, concentration, duration, and identifier in the source
   must survive into the translation, checked mechanically. A translation that
   drops or alters one is rejected outright rather than shown with a warning.
5. Anything that fails any check keeps the exact source text on screen with an
   honest notice. Korean TTS directs the researcher to that source instead of
   reading a long English instruction aloud. Inventing smoother Korean never is
   allowed.

Nothing here decides whether a workflow advances. It formats an answer that the
deterministic layer has already decided to give.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from voice_workflow_agent.configuration import bounded_integer


log = logging.getLogger("voice_workflow_agent.source_presentation")


PRESENTATION_POLICY_VERSION = "ko-source-presentation-v2"


class SourcePresentationStatus(str, Enum):
    """Where the primary text a researcher hears actually came from."""

    #: A reviewer-approved Korean sidecar shipped with the protocol revision.
    VERIFIED_SIDECAR = "verified_sidecar"
    #: A source-bound development localization without reviewer approval.
    DEVELOPMENT_SIDECAR = "development_sidecar"
    #: Generated at runtime and mechanically checked. Not reviewer-approved.
    AUTOMATIC_TRANSLATION = "automatic_translation"
    #: No trustworthy Korean available, so the exact source is the answer.
    SOURCE_ONLY = "source_only"
    #: The answer language already matches the source language.
    SOURCE_LANGUAGE = "source_language"


#: Korean provenance labels. Only ``VERIFIED_SIDECAR`` is allowed to use the
#: word 검증된, because only it has actually been reviewed.
PRESENTATION_LABELS: Mapping[str, str] = {
    SourcePresentationStatus.VERIFIED_SIDECAR.value: "답변 · 검증된 한국어 번역",
    SourcePresentationStatus.DEVELOPMENT_SIDECAR.value: "답변 · 개발용 한국어 번역",
    SourcePresentationStatus.AUTOMATIC_TRANSLATION.value: "답변 · 자동 번역",
    SourcePresentationStatus.SOURCE_ONLY.value: "답변 · 원문 그대로",
    SourcePresentationStatus.SOURCE_LANGUAGE.value: "답변",
}

PRESENTATION_NOTICES: Mapping[str, str | None] = {
    SourcePresentationStatus.VERIFIED_SIDECAR.value: None,
    SourcePresentationStatus.DEVELOPMENT_SIDECAR.value: (
        "검토 승인 전 개발용 번역입니다. 수치와 조건은 아래 원문을 기준으로 확인해 주세요."
    ),
    SourcePresentationStatus.AUTOMATIC_TRANSLATION.value: (
        "검토를 거치지 않은 자동 번역입니다. 수치와 조건은 아래 원문을 기준으로 확인해 주세요."
    ),
    SourcePresentationStatus.SOURCE_ONLY.value: (
        "안전한 자동 한국어 번역을 만들지 못해 승인된 원문을 표시했습니다."
    ),
    SourcePresentationStatus.SOURCE_LANGUAGE.value: None,
}

SOURCE_DISCLOSURE_LABEL = "원문 보기"


@dataclass(frozen=True)
class TranslationSettings:
    """Runtime presentation-translation policy.

    Enabled by default for the normal Korean pilot/development path. A deployment
    can still disable it explicitly. Every generated result remains unapproved
    presentation text and must pass the mechanical preservation gate.
    """

    enabled: bool = True
    model: str = "grok-4.6"
    #: Bounds one presentation translation. Long source text is a sign the
    #: caller is trying to translate a document rather than one step.
    maximum_source_characters: int = 1200
    timeout_seconds: int = 12

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None,
    ) -> "TranslationSettings":
        env = os.environ if environment is None else environment
        model = env.get(
            "VOICE_WORKFLOW_AGENT_PRESENTATION_TRANSLATION_MODEL", "grok-4.6",
        ).strip() or "grok-4.6"
        return cls(
            enabled=bounded_integer(
                env, "VOICE_WORKFLOW_AGENT_PRESENTATION_TRANSLATION_ENABLED",
                1, 0, 1) == 1,
            model=model,
            maximum_source_characters=bounded_integer(
                env, "VOICE_WORKFLOW_AGENT_PRESENTATION_TRANSLATION_MAX_CHARS",
                1200, 100, 8000),
            timeout_seconds=bounded_integer(
                env, "VOICE_WORKFLOW_AGENT_PRESENTATION_TRANSLATION_TIMEOUT",
                12, 1, 60),
        )


#: A number, optionally with a following unit. Units are enumerated rather than
#: matched loosely, because a loose match would silently accept a translation
#: that turned "5 mL" into "5 L".
_UNIT = (
    r"%|℃|°C|°|µL|μL|uL|mL|ml|L|l|µg|μg|ug|mg|kg|ng|pg|g|"
    r"mM|µM|μM|uM|nM|pM|M|N|Da|kDa|bp|kb|"
    r"rpm|×g|xg|g|"
    r"ms|msec|sec|s|min|mins|minute|minutes|h|hr|hrs|hour|hours|"
    r"일|시간|분|초|배|회|번|개"
)
_MEASUREMENT = re.compile(
    rf"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(?:({_UNIT})(?![A-Za-z]))?",
    re.IGNORECASE,
)

#: Reagent codes, equipment names and step identifiers that must survive
#: verbatim. Deliberately narrow: an all-caps or mixed alphanumeric token is a
#: name, whereas an ordinary English word is something a translation is
#: *supposed* to replace.
_IDENTIFIER = re.compile(
    r"\b(?:[A-Z]{2,}[A-Za-z0-9]*|[A-Za-z]+\d+[A-Za-z0-9]*|"
    r"[A-Za-z]+-[A-Za-z0-9]+)\b"
)

#: Identifier-shaped tokens that are ordinary English words in disguise. These
#: legitimately disappear in a Korean sentence.
_IDENTIFIER_STOPWORDS = frozenset({
    "OK", "NOTE", "STEP", "AND", "THE", "FOR", "NOT", "ALL", "USE", "ADD",
    "RETAIN", "REMOVE", "MIX", "IN", "DARK",
    "PCR",  # kept out only because it is checked as a keyterm elsewhere
})


_RATIO_PATTERNS = (
    re.compile(
        r"(?<![\w.])(\d+(?:[.,]\d+)?(?:\s*:\s*\d+(?:[.,]\d+)?)+)"
        r"(?!\s*:\s*\d)(?![A-Za-z0-9.])"
    ),
    re.compile(
        r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(?:parts?|부분)(?![A-Za-z])"
        r".{0,160}?"
        r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(?:parts?|부분)(?![A-Za-z])",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*대\s*"
        r"(\d+(?:[.,]\d+)?)(?![A-Za-z0-9.])"
    ),
)


def _normalize(text: str) -> str:
    """Fold Unicode width and whitespace so a check compares meaning, not bytes."""

    folded = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", " ", folded).strip()


def source_measurements(text: str) -> tuple[str, ...]:
    """Every number-plus-unit pair the source states, in order of appearance."""

    found: list[str] = []
    for number, unit in _MEASUREMENT.findall(_normalize(text)):
        found.append(f"{number} {unit}".strip() if unit else number)
    return tuple(found)


def source_identifiers(text: str) -> tuple[str, ...]:
    """Reagent, equipment and code tokens that a translation must keep."""

    normalized = _normalize(text)
    return tuple(dict.fromkeys(
        token for token in _IDENTIFIER.findall(normalized)
        if token.upper() not in _IDENTIFIER_STOPWORDS
    ))


def source_ratios(text: str) -> tuple[tuple[str, ...], ...]:
    """Return explicitly ratio-marked numeric tuples in source order."""

    normalized = _normalize(text)
    found: list[tuple[str, ...]] = []
    for pattern_index, pattern in enumerate(_RATIO_PATTERNS):
        for match in pattern.finditer(normalized):
            if pattern_index == 0:
                values = tuple(
                    part.strip() for part in re.split(r"\s*:\s*", match.group(1))
                )
            else:
                values = tuple(part.strip() for part in match.groups())
            if len(values) >= 2 and values not in found:
                found.append(values)
    return tuple(found)


def _canonical_measurement(measurement: str) -> str:
    return measurement.replace("μ", "µ").replace(" ", "").casefold()


@dataclass(frozen=True)
class PreservationResult:
    """Why a candidate translation was accepted or rejected."""

    preserved: bool
    missing_measurements: tuple[str, ...] = ()
    missing_ratios: tuple[tuple[str, ...], ...] = ()
    missing_identifiers: tuple[str, ...] = ()
    missing_stable_tokens: tuple[str, ...] = ()

    @property
    def reason(self) -> str | None:
        if self.preserved:
            return None
        if self.missing_measurements:
            return "measurement_dropped"
        if self.missing_ratios:
            return "ratio_dropped"
        if self.missing_stable_tokens:
            return "stable_token_dropped"
        return "identifier_dropped"


def check_source_preservation(
    source: str,
    candidate: str,
    *,
    stable_tokens: Sequence[str] = (),
) -> PreservationResult:
    """Verify that a candidate Korean text kept everything that must not change.

    This is a *rejection* test, not a scoring heuristic. Anything it flags is
    discarded, so a false rejection costs an English answer while a false accept
    would cost a wrong concentration at a bench.
    """

    normalized = _normalize(candidate)
    normalized_source = _normalize(source)
    # Counted, not merely present. A source that states 100 mM twice and a
    # translation that states it once has changed one of them, and asking only
    # "does 100 mM appear?" would wave that through.
    source_measurement_counts = Counter(
        _canonical_measurement(item) for item in source_measurements(source)
    )
    candidate_measurement_counts = Counter(
        _canonical_measurement(item) for item in source_measurements(candidate)
    )
    representative_measurements = {
        _canonical_measurement(item): item for item in source_measurements(source)
    }
    missing_measurements = tuple(
        representative_measurements[measurement]
        for measurement, required_count in source_measurement_counts.items()
        if candidate_measurement_counts[measurement] < required_count
    )
    missing_identifiers = tuple(
        identifier for identifier in source_identifiers(source)
        if identifier.lower() not in normalized.lower()
    )
    candidate_ratios = source_ratios(normalized)
    missing_ratios = tuple(
        ratio for ratio in source_ratios(normalized_source)
        if ratio not in candidate_ratios
    )
    normalized_folded = normalized.casefold()
    source_folded = normalized_source.casefold()
    bounded_stable_tokens = tuple(dict.fromkeys(
        _normalize(token) for token in stable_tokens
        if isinstance(token, str) and _normalize(token)
    ))
    missing_stable_tokens = tuple(
        token for token in bounded_stable_tokens
        if source_folded.count(token.casefold())
        > normalized_folded.count(token.casefold())
    )
    return PreservationResult(
        preserved=(
            not missing_measurements
            and not missing_ratios
            and not missing_identifiers
            and not missing_stable_tokens
        ),
        missing_measurements=missing_measurements,
        missing_ratios=missing_ratios,
        missing_identifiers=missing_identifiers,
        missing_stable_tokens=missing_stable_tokens,
    )


_HANGUL = re.compile(r"[가-힣]")


def looks_korean(text: str) -> bool:
    """Cheap guard against a translator that echoed the English back."""

    return bool(_HANGUL.search(str(text or "")))


@dataclass(frozen=True)
class SourcePresentation:
    """One answer, with its provenance and its exact source kept side by side."""

    language: str
    primary_text: str
    source_text: str
    status: SourcePresentationStatus
    rejection_reason: str | None = None
    citation: str | None = None

    @property
    def label(self) -> str:
        return PRESENTATION_LABELS[self.status.value]

    @property
    def notice(self) -> str | None:
        return PRESENTATION_NOTICES[self.status.value]

    @property
    def translated(self) -> bool:
        return self.status in (
            SourcePresentationStatus.VERIFIED_SIDECAR,
            SourcePresentationStatus.DEVELOPMENT_SIDECAR,
            SourcePresentationStatus.AUTOMATIC_TRANSLATION,
        )

    @property
    def reviewer_approved_translation(self) -> bool:
        """Only a reviewed sidecar may ever be described as verified."""

        return self.status is SourcePresentationStatus.VERIFIED_SIDECAR

    def speech_text(self, suffix: str = "") -> str:
        """What is spoken. The source block is a screen affordance, not audio."""

        primary = self.primary_text.strip()
        if (
            self.language == "ko"
            and self.status is SourcePresentationStatus.SOURCE_ONLY
        ):
            primary = (
                "안전한 자동 한국어 번역을 만들지 못했습니다. "
                "화면의 원문 보기에서 승인된 원문을 확인해 주세요."
            )
        parts = [primary]
        if suffix.strip():
            parts.append(suffix.strip())
        return " ".join(part for part in parts if part)

    @property
    def display_primary_text(self) -> str:
        """Primary screen copy without promoting a source-only fallback."""

        if self.status is SourcePresentationStatus.SOURCE_ONLY:
            return self.notice or "승인된 원문을 확인해 주세요."
        return self.primary_text.strip()

    def display_text(self, suffix: str = "") -> str:
        """Korean first, then the exact approved original under 원문 보기."""

        if self.status is SourcePresentationStatus.SOURCE_LANGUAGE:
            body = self.primary_text.strip()
            if suffix.strip():
                body = f"{body} {suffix.strip()}"
            return f"{body}\n\nSource\n{self.citation}" if self.citation else body

        lines = [self.label, self.display_primary_text]
        if suffix.strip():
            lines.append(suffix.strip())
        if self.notice and self.notice != self.display_primary_text:
            lines.append(self.notice)
        source = self.source_text.strip()
        if source:
            lines.append(f"\n{SOURCE_DISCLOSURE_LABEL} · English\n{source}")
        if self.citation:
            lines.append(f"\n출처\n{self.citation}")
        return "\n".join(lines)


def present_source(
    *,
    language: str,
    source_text: str,
    verified_translation: str | None = None,
    development_translation: str | None = None,
    translator: Callable[[str], str] | None = None,
    settings: TranslationSettings | None = None,
    stable_tokens: Sequence[str] = (),
    cache_key: "TranslationCacheKey | None" = None,
    cache: "PresentationTranslationCache | None" = None,
    citation: str | None = None,
    safety_critical: bool = False,
) -> SourcePresentation:
    """Produce the answer for one piece of approved source text.

    ``safety_critical`` forces the fail-closed path: when the deterministic layer
    has flagged an ambiguity that a human must settle, the exact source is the
    only acceptable answer, and no translator is consulted at all.
    """

    source = str(source_text or "").strip()
    policy = settings or TranslationSettings()

    if language != "ko":
        return SourcePresentation(
            language=language, primary_text=source, source_text=source,
            status=SourcePresentationStatus.SOURCE_LANGUAGE, citation=citation)

    for candidate_translation, status in (
        (verified_translation, SourcePresentationStatus.VERIFIED_SIDECAR),
        (development_translation, SourcePresentationStatus.DEVELOPMENT_SIDECAR),
    ):
        if candidate_translation and candidate_translation.strip():
            candidate = candidate_translation.strip()
            rejection = _reject_candidate(
                source, candidate, policy, stable_tokens=stable_tokens,
            )
            if rejection is None:
                return SourcePresentation(
                    language=language, primary_text=candidate,
                    source_text=source, status=status, citation=citation)

    if safety_critical:
        return SourcePresentation(
            language=language, primary_text=source, source_text=source,
            status=SourcePresentationStatus.SOURCE_ONLY,
            rejection_reason="safety_critical_fail_closed", citation=citation)

    rejection = _translation_rejection(source, translator, policy)
    if rejection is not None:
        return SourcePresentation(
            language=language, primary_text=source, source_text=source,
            status=SourcePresentationStatus.SOURCE_ONLY,
            rejection_reason=rejection, citation=citation)

    translation_cache = (
        cache if cache is not None else PRESENTATION_TRANSLATION_CACHE
    )
    cached = translation_cache.get(cache_key) if cache_key is not None else None
    if cached is not None:
        verdict = _reject_candidate(
            source, cached, policy, stable_tokens=stable_tokens,
        )
        if verdict is None:
            return SourcePresentation(
                language=language, primary_text=cached, source_text=source,
                status=SourcePresentationStatus.AUTOMATIC_TRANSLATION,
                citation=citation,
            )
        translation_cache.discard(cache_key)

    try:
        candidate = str(translator(source) or "").strip()  # type: ignore[misc]
    except Exception:
        log.warning("presentation translation failed; approved source retained")
        return SourcePresentation(
            language=language, primary_text=source, source_text=source,
            status=SourcePresentationStatus.SOURCE_ONLY,
            rejection_reason="translation_failed", citation=citation)
    verdict = _reject_candidate(
        source, candidate, policy, stable_tokens=stable_tokens,
    )
    if verdict is not None:
        return SourcePresentation(
            language=language, primary_text=source, source_text=source,
            status=SourcePresentationStatus.SOURCE_ONLY,
            rejection_reason=verdict, citation=citation)
    if cache_key is not None:
        translation_cache.put(cache_key, candidate)
    return SourcePresentation(
        language=language, primary_text=candidate, source_text=source,
        status=SourcePresentationStatus.AUTOMATIC_TRANSLATION, citation=citation)


def _translation_rejection(
    source: str, translator: Callable[[str], str] | None,
    policy: TranslationSettings,
) -> str | None:
    if not source:
        return "empty_source"
    if not policy.enabled:
        return "translation_disabled"
    if translator is None:
        return "translator_unavailable"
    if len(source) > policy.maximum_source_characters:
        return "source_too_long"
    return None


def _reject_candidate(
    source: str,
    candidate: str,
    policy: TranslationSettings,
    *,
    stable_tokens: Sequence[str] = (),
) -> str | None:
    if not candidate:
        return "empty_translation"
    if not looks_korean(candidate):
        return "translation_not_korean"
    if len(candidate) > policy.maximum_source_characters * 3:
        return "translation_too_long"
    preservation = check_source_preservation(
        source, candidate, stable_tokens=stable_tokens,
    )
    if not preservation.preserved:
        return preservation.reason
    return None


@dataclass(frozen=True)
class TranslationCacheKey:
    """Immutable identity for one approved-source presentation translation."""

    protocol_revision_id: str
    source_document_sha256: str
    step_id: str
    source_text_sha256: str
    target_language: str
    policy_version: str
    model: str

    @classmethod
    def for_source(
        cls,
        *,
        protocol_revision_id: str,
        source_document_sha256: str,
        step_id: str,
        source_text: str,
        target_language: str,
        model: str,
    ) -> "TranslationCacheKey":
        return cls(
            protocol_revision_id=protocol_revision_id,
            source_document_sha256=source_document_sha256,
            step_id=step_id,
            source_text_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            target_language=target_language,
            policy_version=PRESENTATION_POLICY_VERSION,
            model=model,
        )


class PresentationTranslationCache:
    """Small process-local LRU cache for successful immutable translations."""

    def __init__(self, maximum_entries: int = 256) -> None:
        if not 1 <= maximum_entries <= 4096:
            raise ValueError("translation cache size is outside safe bounds")
        self.maximum_entries = maximum_entries
        self._values: OrderedDict[TranslationCacheKey, str] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: TranslationCacheKey | None) -> str | None:
        if key is None:
            return None
        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._values.move_to_end(key)
            return value

    def put(self, key: TranslationCacheKey, value: str) -> None:
        with self._lock:
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > self.maximum_entries:
                self._values.popitem(last=False)

    def discard(self, key: TranslationCacheKey) -> None:
        with self._lock:
            self._values.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)


PRESENTATION_TRANSLATION_CACHE = PresentationTranslationCache()


#: The whole instruction given to a presentation translator. Deliberately
#: narrow: translate, preserve, add nothing. It is a formatting instruction, not
#: a reasoning task, and it never sees or affects workflow state.
PRESENTATION_TRANSLATION_INSTRUCTION = (
    "You translate one approved laboratory protocol instruction from English "
    "into natural Korean for a researcher standing at a bench.\n"
    "Rules:\n"
    "1. Preserve every number, unit, concentration, duration, temperature, "
    "material name, reagent code and identifier exactly as written.\n"
    "2. Preserve the order and the boundaries of the actions described.\n"
    "3. Do not add explanation, interpretation, safety advice, completion "
    "criteria, or any content that is not in the source.\n"
    "4. Do not omit any clause.\n"
    "5. Reply with the Korean translation only, with no preamble, no quotes "
    "and no commentary."
)


def build_presentation_translator(
    client: object, settings: TranslationSettings,
) -> Callable[[str], str] | None:
    """Adapt the existing model client into the narrow translator contract.

    The returned callable is the *only* way an LLM participates in presentation,
    and it is handed one string and expected to return one string. It cannot see
    session state, cannot call a tool, and cannot influence routing.
    """

    if not settings.enabled or client is None:
        return None

    def translate(source: str) -> str:
        response = client.chat.completions.create(  # type: ignore[attr-defined]
            model=settings.model,
            messages=[
                {"role": "system",
                 "content": PRESENTATION_TRANSLATION_INSTRUCTION},
                {"role": "user", "content": source},
            ],
            temperature=0,
            timeout=settings.timeout_seconds,
        )
        choices: Sequence = getattr(response, "choices", ()) or ()
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        return str(getattr(message, "content", "") or "")

    return translate
