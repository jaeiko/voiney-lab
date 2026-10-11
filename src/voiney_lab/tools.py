"""What the server keeps beside the voice tools: the trusted turn context and
the record-value checks ``record_log`` applies.

The model-facing tools of the old generic brain -- the approved safety manual
and lab-reference searches and the safety report's creation and status --
were deleted on 2026-10-10 (lane CL); they live in the git history only. The
LLM router's tools are ``change_state`` and ``record_log`` (``llm_router``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    """Trusted retrieval inputs owned by the server, never by Tool JSON."""

    catalog_path: Path | None
    facility_id: str | None
    language: str
    usage_scope: str
    # The hand-off worker that read it was deleted (lane CL, 2026-10-10); the
    # field stays so the context keeps its shape.
    report_language: str = "ko"


#: A negation in what the researcher said (lane TS, human decision 4 of
#: 2026-10-08): 안 (as a word of its own), 않, 없, 못, 말고·말아, and the
#: English no, not, never, none, nothing, without, cannot, -n't.
_RECORD_NEGATION = re.compile(
    r"(?<![가-힣])안(?=\s|$)|않|없|못|말(?:고|아|라)"
    r"|\b(?:no|not|never|none|nothing|without|cannot|neither|nor)\b|\b\w+n['’]t\b",
    re.IGNORECASE,
)
#: Where one clause ends and the next begins: punctuation, or a joining word.
_RECORD_CLAUSE_BREAK = re.compile(
    r"\s*[,.;!?·\n]\s*|\s+(?:그리고|그런데|근데|하지만|그래서|and|but|so)\s+", re.IGNORECASE)
#: The request to record, which is not part of what was seen: "남겨 줘",
#: "기록해 줘", "적어 줘", "메모해 줘" (with the quotation ending before it),
#: "note this", "record that ...".
_RECORD_REQUEST_KO = re.compile(
    r"(?:(?:이?라고|다고|고)\s*)?(?:기록|메모|적어|남겨|저장)\S*(?:\s+(?:좀|줘|줘요|주세요|줄래|둬|놔|해|해줘|해주세요))*\s*$"
)
_RECORD_REQUEST_EN = re.compile(
    r"^(?:please\s+)?(?:note|record|log|jot|write)(?:\s+(?:this|that|it|down))*\s*:?\s*(?:that\s+)?"
    r"|\s*(?:please\s+)?(?:note|record|log|jot|write)(?:\s+(?:this|that|it|down))+\s*$",
    re.IGNORECASE,
)
_QUOTED_ENDING = re.compile(r"(?:이?라고|다고|고)$")


def record_value_keeping_negation(value: str, transcript: str) -> str:
    """What ``record_log`` stores: ``value``, or the whole transcript.

    Lane TS, human decision 4 of 2026-10-08: when the words hold a negation,
    the value must hold the whole of every clause a negation stands in (the
    request to record taken off: "침전물 안 보임 남겨 줘" -> "침전물 안 보임");
    a value that does not ("침전물") is replaced by everything that was
    said, so a record never says the opposite of what was seen.
    """

    said = " ".join(str(transcript).split())
    wanted = " ".join(str(value).split()).casefold()
    if not _RECORD_NEGATION.search(said):
        return value
    for clause in _RECORD_CLAUSE_BREAK.split(said):
        if not clause or not _RECORD_NEGATION.search(clause):
            continue
        core = _RECORD_REQUEST_EN.sub("", _RECORD_REQUEST_KO.sub("", clause)).strip()
        if not core:
            continue
        shorter = _QUOTED_ENDING.sub("", core).strip()
        if core.casefold() not in wanted and (not shorter or shorter.casefold() not in wanted):
            return said
    return value


def _observation_matches_transcript(value: Any, transcript: str) -> bool:
    """Require a recorded observation to be supported by the final transcript."""
    normalized_transcript = " ".join(transcript.split())
    if not normalized_transcript:
        return False
    if isinstance(value, bool):
        evidence = {
            True: ("true", "yes", "예", "맞아", "있음", "có"),
            False: ("false", "no", "아니", "없음", "không"),
        }[value]
        folded = normalized_transcript.casefold()
        return any(item in folded for item in evidence)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        token = re.escape(str(value))
        return re.search(rf"(?<![0-9.]){token}(?![0-9.])", normalized_transcript) is not None
    if not isinstance(value, str):
        return False
    cleaned = " ".join(value.split())
    if not cleaned:
        return False
    if re.search(r"[A-Za-z0-9]", cleaned):
        # ASCII identifiers require full token boundaries. This rejects a model
        # argument such as A-17 when the transcript actually contains A-170.
        token = re.escape(cleaned)
        return re.search(
            rf"(?<![A-Za-z0-9]){token}(?![A-Za-z0-9])",
            normalized_transcript,
            flags=re.IGNORECASE,
        ) is not None
    # Korean and Vietnamese particles can attach to a value in normal speech.
    return cleaned.casefold() in normalized_transcript.casefold()
