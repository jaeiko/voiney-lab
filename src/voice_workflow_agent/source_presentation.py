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
5. Anything that fails any check falls back to the exact source text with an
   honest notice. Failing closed to English is always allowed; inventing
   smoother Korean never is.

Nothing here decides whether a workflow advances. It formats an answer that the
deterministic layer has already decided to give.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from voice_workflow_agent.configuration import bounded_integer


class SourcePresentationStatus(str, Enum):
    """Where the primary text a researcher hears actually came from."""

    #: A reviewer-approved Korean sidecar shipped with the protocol revision.
    VERIFIED_SIDECAR = "verified_sidecar"
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
    SourcePresentationStatus.AUTOMATIC_TRANSLATION.value: "답변 · 자동 번역",
    SourcePresentationStatus.SOURCE_ONLY.value: "답변 · 원문 그대로",
    SourcePresentationStatus.SOURCE_LANGUAGE.value: "답변",
}

PRESENTATION_NOTICES: Mapping[str, str | None] = {
    SourcePresentationStatus.VERIFIED_SIDECAR.value: None,
    SourcePresentationStatus.AUTOMATIC_TRANSLATION.value: (
        "검토를 거치지 않은 자동 번역입니다. 수치와 조건은 아래 원문을 기준으로 확인해 주세요."
    ),
    SourcePresentationStatus.SOURCE_ONLY.value: (
        "확인된 한국어 번역이 없어 승인된 원문을 그대로 표시했습니다."
    ),
    SourcePresentationStatus.SOURCE_LANGUAGE.value: None,
}

SOURCE_DISCLOSURE_LABEL = "원문 보기"


@dataclass(frozen=True)
class TranslationSettings:
    """Runtime presentation-translation policy.

    Disabled by default. A pilot that has not agreed how a machine translation
    of an approved protocol will be reviewed should be showing English, not
    generating Korean.
    """

    enabled: bool = False
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
                0, 0, 1) == 1,
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
    rf"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(?:({_UNIT})(?![A-Za-z]))?"
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
    "PCR",  # kept out only because it is checked as a keyterm elsewhere
})


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


def _measurement_pattern(measurement: str) -> re.Pattern[str]:
    number, _, unit = measurement.partition(" ")
    if not unit:
        return re.compile(rf"(?<![\w.]){re.escape(number)}(?![\w.])")
    # The number must be followed by the same unit. Spacing and letter case may
    # differ (5mL / 5 ML); the unit itself may not (5 mL is not 5 L).
    return re.compile(
        rf"(?<![\w.]){re.escape(number)}\s*{re.escape(unit)}(?![A-Za-z])",
        re.IGNORECASE)


def _measurement_occurrences(text: str, measurement: str) -> int:
    return len(_measurement_pattern(measurement).findall(text))


@dataclass(frozen=True)
class PreservationResult:
    """Why a candidate translation was accepted or rejected."""

    preserved: bool
    missing_measurements: tuple[str, ...] = ()
    missing_identifiers: tuple[str, ...] = ()

    @property
    def reason(self) -> str | None:
        if self.preserved:
            return None
        if self.missing_measurements:
            return "measurement_dropped"
        return "identifier_dropped"


def check_source_preservation(
    source: str, candidate: str,
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
    missing_measurements = tuple(
        measurement
        for measurement in dict.fromkeys(source_measurements(source))
        if _measurement_occurrences(normalized, measurement)
        < _measurement_occurrences(normalized_source, measurement)
    )
    missing_identifiers = tuple(
        identifier for identifier in source_identifiers(source)
        if identifier.lower() not in normalized.lower()
    )
    return PreservationResult(
        preserved=not missing_measurements and not missing_identifiers,
        missing_measurements=missing_measurements,
        missing_identifiers=missing_identifiers,
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
            SourcePresentationStatus.AUTOMATIC_TRANSLATION,
        )

    @property
    def reviewer_approved_translation(self) -> bool:
        """Only a reviewed sidecar may ever be described as verified."""

        return self.status is SourcePresentationStatus.VERIFIED_SIDECAR

    def speech_text(self, suffix: str = "") -> str:
        """What is spoken. The source block is a screen affordance, not audio."""

        parts = [self.primary_text.strip()]
        if suffix.strip():
            parts.append(suffix.strip())
        return " ".join(part for part in parts if part)

    def display_text(self, suffix: str = "") -> str:
        """Korean first, then the exact approved original under 원문 보기."""

        if self.status is SourcePresentationStatus.SOURCE_LANGUAGE:
            body = self.primary_text.strip()
            if suffix.strip():
                body = f"{body} {suffix.strip()}"
            return f"{body}\n\nSource\n{self.citation}" if self.citation else body

        lines = [self.label, self.primary_text.strip()]
        if suffix.strip():
            lines.append(suffix.strip())
        if self.notice:
            lines.append(self.notice)
        source = self.source_text.strip()
        if source and source != self.primary_text.strip():
            lines.append(f"\n{SOURCE_DISCLOSURE_LABEL} · English\n{source}")
        if self.citation:
            lines.append(f"\n출처\n{self.citation}")
        return "\n".join(lines)


def present_source(
    *,
    language: str,
    source_text: str,
    verified_translation: str | None = None,
    translator: Callable[[str], str] | None = None,
    settings: TranslationSettings | None = None,
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

    if verified_translation and verified_translation.strip():
        return SourcePresentation(
            language=language, primary_text=verified_translation.strip(),
            source_text=source,
            status=SourcePresentationStatus.VERIFIED_SIDECAR, citation=citation)

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

    candidate = str(translator(source) or "").strip()  # type: ignore[misc]
    verdict = _reject_candidate(source, candidate, policy)
    if verdict is not None:
        return SourcePresentation(
            language=language, primary_text=source, source_text=source,
            status=SourcePresentationStatus.SOURCE_ONLY,
            rejection_reason=verdict, citation=citation)
    return SourcePresentation(
        language=language, primary_text=candidate, source_text=source,
        status=SourcePresentationStatus.AUTOMATIC_TRANSLATION, citation=citation)


def _translation_rejection(
    source: str, translator: Callable[[str], str] | None,
    policy: TranslationSettings,
) -> str | None:
    if not source:
        return "empty_source"
    if not policy.enabled or translator is None:
        return "translation_disabled"
    if len(source) > policy.maximum_source_characters:
        return "source_too_long"
    return None


def _reject_candidate(
    source: str, candidate: str, policy: TranslationSettings,
) -> str | None:
    if not candidate:
        return "empty_translation"
    if not looks_korean(candidate):
        return "translation_not_korean"
    if len(candidate) > policy.maximum_source_characters * 3:
        return "translation_too_long"
    preservation = check_source_preservation(source, candidate)
    if not preservation.preserved:
        return preservation.reason
    return None


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
