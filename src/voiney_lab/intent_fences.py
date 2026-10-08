"""Utterance fences shared by the server's model-proposal validation and the session rules.

What a model proposes is evidence, never authorization: a state change has to be
visible in what the researcher actually said, a question authorizes nothing but a
polite request for bounded control, a hypothetical authorizes nothing at all, and
a proposal may never redirect a change onto another step. The LLM router's tool
validation (``llm_router.py``) applies these fences to every tool proposal, and
the session rules read ``has_completion_evidence`` for a spoken observation.

These functions came verbatim from the removed xAI-only semantic-intent helper
(lane DI, 2026-10-08); the fences and the words they look for are unchanged.
"""

from __future__ import annotations

import re
import unicodedata


_COMPLETION_EVIDENCE = re.compile(
    r"완료|끝|다\s*했|마쳤|마무리|넘어가|넘어갈|진행하자|done|finish|complete|"
    r"move\s+on|next\s+step",
    re.IGNORECASE,
)
_INTERROGATIVE_EVIDENCE = re.compile(
    r"[?？]|뭐|무엇|무슨|뭔지|어떻게|어떤|어디|언제|누가|왜|"
    r"\bwhat\b|\bwhy\b|\bhow\b|\bwhen\b|\bwhere\b|\bwhich\b|\bwho\b",
    re.IGNORECASE,
)
_HYPOTHETICAL_EVIDENCE = re.compile(
    r"하면|한다면|치면|가정|라고\s*하|라는\s*게|무슨\s*뜻|의미(?:가|는)|"
    r"\bif\b|assuming|suppose|what\s+happens",
    re.IGNORECASE,
)
_POLITE_ACTION_REQUEST = re.compile(
    r"(?:해\s*)?(?:줘|줄래|주세요|주시겠|부탁)|"
    r"\b(?:please|could\s+you|would\s+you)\b",
    re.IGNORECASE,
)


def normalize_semantic_utterance(value: str) -> str:
    """Normalize width/spacing only; punctuation and case-bearing text survive.

    The policy guards below need the interrogative punctuation the curated
    utterance key strips, so this is deliberately a *lighter* normalization.
    """

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", normalized).strip()


#: Server-recognized ways of naming "the step the session is actually on".
_CURRENT_STEP_TARGETS = frozenset({
    "current", "current step", "authoritative current step", "this", "this step",
    "현재", "현재 단계", "이 단계", "지금",
})
_CURRENT_STEP_TIMER_TARGETS = frozenset({
    "timer", "step timer", "current timer", "current step timer",
    "authoritative current step timer", "this timer", "this step timer",
    "타이머", "현재 타이머", "현재 단계 타이머", "이 단계 타이머", "지금 타이머",
})


def evidence_fence_rejection(
    *,
    utterance: str,
    evidence: str,
    bounded_control: bool,
) -> str | None:
    """The first evidence fence a proposed change fails, or ``None``.

    Shared by every model proposal that would change state -- this module's
    semantic proposal and the lane R router's tool proposals: the action has
    to be visible in what the researcher said (``evidence`` verbatim in
    ``utterance``, which is ``normalize_semantic_utterance`` text), a question
    authorizes nothing except a polite request for bounded control ("멈춰
    줄래?"), and a hypothetical authorizes nothing at all.
    """

    if not evidence or evidence.casefold() not in utterance.casefold():
        return "evidence_not_verbatim"
    if (
        _INTERROGATIVE_EVIDENCE.search(utterance)
        and not (bounded_control and _POLITE_ACTION_REQUEST.search(utterance))
    ):
        return "interrogative_not_authorized"
    if _HYPOTHETICAL_EVIDENCE.search(utterance):
        return "hypothetical_not_authorized"
    return None


def has_completion_evidence(utterance: str) -> bool:
    """True when the words name finishing or moving on ("완료", "다 했", "넘어가")."""

    return _COMPLETION_EVIDENCE.search(utterance) is not None


def _normalized_semantic_target(target: str) -> str:
    """Normalize target separators without guessing a target's meaning."""

    normalized = unicodedata.normalize("NFKC", target).strip().casefold()
    return re.sub(r"[\s_-]+", " ", normalized)


def targets_current_step(
    target: str | None,
    *,
    current_step_label: str | None,
    timer_available: bool = False,
    timer_target: bool = False,
) -> bool:
    """True only when ``target`` names the current step, or its timer.

    A model proposal may never redirect a change onto another step: no target,
    "현재 단계", "this step" and the current step's own label are the current
    step; with ``timer_target`` and a timer to speak of, so is its timer.
    """

    if not target:
        return True
    normalized = _normalized_semantic_target(target)
    if normalized in _CURRENT_STEP_TARGETS:
        return True
    label = (current_step_label or "").strip().casefold()
    if label and normalized in {label, f"{label}단계", f"step {label}"}:
        return True
    if not timer_target or not timer_available:
        return False
    timer_targets = set(_CURRENT_STEP_TIMER_TARGETS)
    if label:
        timer_targets.update({
            f"{label} timer",
            f"step {label} timer",
            f"{label}단계 타이머",
        })
    return normalized in timer_targets
