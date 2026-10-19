"""Two key values to confirm before the start (lane ON, decision ③, 2026-10-18).

Human decision: from the analysis (or review version) that is about to
run, the server picks the two values that matter most for the run -- time,
temperature, speed, amount, in that order -- and asks each as a multiple
choice beside its source excerpt: the analysed value and two other values
the server makes deterministically (the other OCR engine's reading where
there is one, a decimal shift ×10 / ÷10, another value of the same kind in
the protocol). The server decides what is right; the screen draws only the
choices the server sends. A right choice moves on. A wrong one says "원문
조각을 다시 봐 주세요" and offers [원문대로 이 값이 맞아요] (the value stands)
and [분석 값이 틀렸어요 → 검토 카드 열기] (a card whose button is the review
decision that fixes the item). Every answer is recorded in the protocol's
ledger (``protocol_start_check_answered``). A protocol without numeric values
skips the check, and ``VOINEY_LAB_START_CHECK_ENABLED=false`` turns it off.

The values are read from the items of the review draft (the same records
the review screen shows, with their source lines), so the question's
excerpt and the card's item are the review's own.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

START_CHECK_ENABLED_ENV = "VOINEY_LAB_START_CHECK_ENABLED"
START_CHECK_EVENT = "protocol_start_check_answered"
QUESTION_COUNT = 2
KINDS = ("time", "temperature", "speed", "amount")
KIND_KO = {"time": "시간", "temperature": "온도", "speed": "속도", "amount": "양"}
_ON = frozenset({"1", "true", "yes", "on"})
LOOK_AGAIN_KO = "원문 조각을 다시 봐 주세요."
ACCEPT_KO = "원문대로 이 값이 맞아요"
OPEN_CARD_KO = "분석 값이 틀렸어요 → 검토 카드 열기"

_NUMBER = r"(\d+(?:[.,]\d+)*)"
_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("time", re.compile(
        _NUMBER + r"\s?(h|hrs?|hours?|min|mins|minutes?|s|secs?|seconds?|days?|분|시간|초)(?![A-Za-z])", re.I)),
    ("temperature", re.compile(_NUMBER + r"\s?(°\s?[CF]|℃|[CF])(?![A-Za-z])")),
    ("speed", re.compile(_NUMBER + r"\s?(rpm|[x×]\s?g|RCF)(?![A-Za-z])", re.I)),
    ("amount", re.compile(
        _NUMBER + r"\s?([µμu]?L|mL|ml|µl|ul|nL|nl|[µμunmk]?g|[µμunm]?M|%)(?![A-Za-z])")),
)
_ITEM_KINDS = frozenset({"step", "sub_action", "value", "duration", "timer"})
_LEADING_LABEL = re.compile(r"^\s*\(?[0-9]+(?:\.[0-9]+)*[.)]\s+")
_UNIT_TOKENS = {"ul": "µl", "ug": "µg", "um": "µm", "xg": "×g", "x g": "×g", "hr": "h", "hrs": "h",
                "sec": "s", "secs": "s", "hour": "h", "hours": "h", "minute": "min", "minutes": "min",
                "mins": "min", "second": "s", "seconds": "s", "c": "°c", "℃": "°c", "° c": "°c"}


class StartCheckError(RuntimeError):
    code = "start_check_invalid"


def start_check_enabled(environment: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    return env.get(START_CHECK_ENABLED_ENV, "true").strip().casefold() in _ON or not env.get(
        START_CHECK_ENABLED_ENV, "").strip()


@dataclass(frozen=True)
class FoundValue:
    kind: str
    text: str
    number: str
    unit: str
    start: int

    @property
    def token(self) -> str:
        """The number and unit as the OCR comparison spells them ("10min", "37°c")."""

        unit = unicodedata.normalize("NFKC", self.unit).casefold().replace("μ", "µ").replace(" ", "")
        unit = _UNIT_TOKENS.get(unit, unit)
        if unit in {"x g", "xg"}:
            unit = "×g"
        return self.number + unit


def key_values_in(text: object, *, label: str | None = None) -> list[FoundValue]:
    """The values a sentence states, with their kind, in order of appearance.

    A step's own number opening its sentence ("3.", "(2)", or its label) is
    not a value. A number without a unit of the four kinds is not one either.
    """

    words = str(text or "")
    start = 0
    opening = _LEADING_LABEL.match(words)
    if opening is not None:
        start = opening.end()
    elif label and label.strip():
        own = re.match(rf"\s*\(?{re.escape(label.strip())}[.)]?\s+", words)
        if own is not None:
            start = own.end()
    found: list[FoundValue] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in _KIND_PATTERNS:
        for match in pattern.finditer(words, start):
            span = (match.start(), match.end())
            if any(not (span[1] <= first or span[0] >= last) for first, last in taken):
                continue
            if kind == "amount" and match[2].casefold() == "g" and re.search(r"[x×]\s?$", words[start:match.start()]):
                continue  # "500 x g" is a speed, not a mass
            taken.append(span)
            found.append(FoundValue(kind, match[0].strip(), match[1], match[2], match.start()))
    found.sort(key=lambda value: value.start)
    return found


def decimal_shifts(number: str) -> tuple[str, str]:
    """``number`` ×10 and ÷10, written the way the number was (commas kept)."""

    grouped = "," in number
    plain = number.replace(",", "")
    if "." in plain:
        whole, fraction = plain.split(".", 1)
    else:
        whole, fraction = plain, ""
    digits = whole + fraction
    point = len(whole)
    up = _place(digits, point + 1, grouped)
    down = _place(digits, point - 1, grouped)
    return up, down


def _place(digits: str, point: int, grouped: bool) -> str:
    if point <= 0:
        digits = "0" * (1 - point) + digits
        point = 1
    if point > len(digits):
        digits = digits + "0" * (point - len(digits))
    whole, fraction = digits[:point], digits[point:].rstrip("0")
    whole = whole.lstrip("0") or "0"
    if grouped:
        whole = f"{int(whole):,}"
    return whole + ("." + fraction if fraction else "")


def _other_reading(value: FoundValue, page: Mapping[str, Any] | None) -> str | None:
    """The other OCR engine's words for this value on its page, when they differ."""

    readings = (page or {}).get("value_readings")
    if not isinstance(readings, list):
        return None
    for reading in readings:
        if not isinstance(reading, dict) or reading.get("token") != value.token:
            continue
        other = reading.get("other_text")
        if isinstance(other, str) and other.strip() and reading.get("other_token") != value.token:
            return other.strip()
    return None


def _candidates(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every value the executed items state, in document order, with its item."""

    found: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("kind")) not in _ITEM_KINDS:
            continue
        if item.get("decision") is not None and str((item.get("decision") or {}).get("action")) == "clear":
            continue
        text = item.get("current") if item.get("current") is not None else item.get("text")
        if not isinstance(text, str):
            continue
        for value in key_values_in(text, label=item.get("step_label")):
            found.append({"value": value, "item": item, "text": text})
    return found


def build_questions(
    items: Sequence[Mapping[str, Any]], ocr_pages: Mapping[int, Mapping[str, Any]], *, seed: str,
) -> list[dict[str, Any]]:
    """The questions, each with its right value and its shuffled choices."""

    candidates = _candidates(items)
    if not candidates:
        return []
    priority = {kind: index for index, kind in enumerate(KINDS)}
    ordered = sorted(enumerate(candidates), key=lambda pair: (priority[pair[1]["value"].kind], pair[0]))
    chosen: list[dict[str, Any]] = []
    for _, candidate in ordered:
        if any(existing["value"].kind == candidate["value"].kind and existing["value"].text == candidate["value"].text
               for existing in chosen):
            continue
        if chosen and len(chosen) < QUESTION_COUNT and any(
                existing["value"].kind == candidate["value"].kind for existing in chosen):
            # Another kind first, when the protocol has one.
            other = next((c for _, c in ordered if all(c["value"].kind != e["value"].kind for e in chosen)
                          and c["value"].text != chosen[0]["value"].text), None)
            if other is not None:
                chosen.append(other)
                break
        chosen.append(candidate)
        if len(chosen) == QUESTION_COUNT:
            break
    questions: list[dict[str, Any]] = []
    for position, candidate in enumerate(chosen, 1):
        value: FoundValue = candidate["value"]
        item = candidate["item"]
        source = item.get("source") if isinstance(item.get("source"), Mapping) else {}
        page = source.get("page") if isinstance(source, Mapping) else None
        distractors: list[str] = []
        other = _other_reading(value, ocr_pages.get(page) if isinstance(page, int) else None)
        if other and other != value.text:
            distractors.append(other)
        same_kind = [c["value"].text for c in candidates
                     if c["value"].kind == value.kind and c["value"].text != value.text]
        same_step = [c["value"].text for c in candidates
                     if c["value"].kind == value.kind and c["value"].text != value.text
                     and c["item"].get("step_id") == item.get("step_id")]
        up, down = decimal_shifts(value.number)
        spacer = " " if " " in value.text else ""
        shifted = [f"{up}{spacer}{value.unit}", f"{down}{spacer}{value.unit}"]
        for pool in (same_step, shifted[:1], same_kind, shifted[1:]):
            for text in pool:
                if len(distractors) >= 2:
                    break
                if text != value.text and text not in distractors:
                    distractors.append(text)
        labels = [value.text, *distractors[:2]]
        digest = hashlib.sha256(f"{seed}|{item.get('item_id')}|{value.text}".encode("utf-8")).digest()
        rotation = digest[0] % len(labels)
        flip = digest[1] % 2 == 1
        order = labels[rotation:] + labels[:rotation]
        if flip and len(order) > 2:
            order = [order[0], order[2], order[1]]
        step_label = item.get("step_label")
        where = f"{step_label}단계의 " if step_label else ""
        questions.append({
            "question_id": f"q{position}:{item.get('item_id')}:{value.token}",
            "position": position,
            "total": len(chosen),
            "progress_ko": f"{position} / {len(chosen)}",
            "kind": value.kind,
            "kind_ko": KIND_KO[value.kind],
            "step_label": step_label,
            "item_id": item.get("item_id"),
            "question_ko": f"{where}{KIND_KO[value.kind]}은(는) 원문에 얼마로 적혀 있나요?",
            "source": {
                "page": page,
                "excerpt": source.get("excerpt") if isinstance(source, Mapping) else None,
                "lines": source.get("lines") if isinstance(source, Mapping) else None,
            },
            "value": value.text,
            "choices": [{"choice_id": f"c{index}", "label_ko": label} for index, label in enumerate(order, 1)],
            "item_text": candidate["text"],
        })
    return questions


def public_question(question: Mapping[str, Any]) -> dict[str, Any]:
    """The question as the screen gets it: without the right value."""

    return {key: question[key] for key in (
        "question_id", "position", "total", "progress_ko", "kind", "kind_ko", "step_label", "item_id",
        "question_ko", "source", "choices")}


def value_card(question: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    """The review card for the item whose value a person says is wrong.

    One card in the shape the review's cards have, so the screen draws it
    with the same function; its button is the review decision that fixes
    the item (the words, as the source prints them), or going back.
    """

    item_id = str(item.get("item_id"))
    source = item.get("source") if isinstance(item.get("source"), Mapping) else None
    shown = item.get("current") if item.get("current") is not None else item.get("text")
    return {
        "card_id": f"start-check:{item_id}",
        "item_id": item_id,
        "position": 1, "total": 1, "progress_ko": "1 / 1",
        "type": "start_check_value",
        "kind": item.get("kind"),
        "kind_ko": item.get("kind_ko"),
        "step_label": item.get("step_label"),
        "question_ko": f"분석이 읽은 {question['kind_ko']} {question['value']} 이(가) 원문과 다르면 원문대로 고쳐 주세요.",
        "reasons_ko": ["시작 전 확인에서 다르게 고름"],
        "shown": shown,
        "source": dict(source) if source else None,
        "choices": [
            {"choice_id": "fix", "label_ko": "원문대로 고칠게요", "request": {"item_id": item_id, "action": "fix"},
             "needs": "text", "route": None, "primary": True},
            {"choice_id": "keep", "label_ko": "그대로 둘게요", "request": None, "needs": None, "route": "back",
             "primary": False},
        ],
        "proposal": None, "change": None, "readings": None, "steps": None,
        "memo_allowed": False, "decided": False, "decision": None, "current": shown,
    }


class StartCheck:
    """The two questions of a protocol about to run, and their answers."""

    def __init__(self, catalog: Any, *, enabled: bool | None = None) -> None:
        self.catalog = catalog
        self.enabled = start_check_enabled() if enabled is None else enabled

    def _built(self, protocol_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        """(the draft's target, the questions with their answers, items by id)."""

        draft = self.catalog.review_draft(protocol_id)
        target = draft.get("target") if isinstance(draft.get("target"), dict) else None
        items = [item for item in draft.get("items") or () if isinstance(item, Mapping)]
        by_id = {str(item.get("item_id")): item for item in items}
        if target is None or not draft.get("available_for_execution"):
            return draft, [], by_id
        revision = self.catalog._latest_protocol_revision(protocol_id)  # noqa: SLF001 - the ledger's revision
        try:
            ocr_pages = self.catalog._ocr_pages(revision)  # noqa: SLF001 - the other engine's readings
        except Exception:  # noqa: BLE001 - no readings is no distractor, never a refusal
            ocr_pages = {}
        seed = f"{protocol_id}|{target.get('analysis_id')}|{target.get('kind')}|{target.get('version')}"
        return draft, build_questions(items, ocr_pages, seed=seed), by_id

    def questions(self, protocol_id: str) -> dict[str, Any]:
        """What the start screen asks: the questions without their answers."""

        if not self.enabled:
            return {"enabled": False, "protocol_id": protocol_id, "target": None, "questions": [],
                    "skipped_reason": "disabled"}
        draft, built, _ = self._built(protocol_id)
        target = draft.get("target")
        skipped = None
        if target is None or not draft.get("available_for_execution"):
            skipped = "not_executable"
        elif not built:
            skipped = "no_numeric_values"
        return {
            "enabled": True,
            "protocol_id": protocol_id,
            "target": target,
            "questions": [public_question(question) for question in built],
            "skipped_reason": skipped,
        }

    def answer(
        self, protocol_id: str, question_id: object, *, choice_id: object = None, action: object = None,
        actor_principal_id: str | None = None,
    ) -> dict[str, Any]:
        """Judge one choice (or take the value as it stands) and record it."""

        if not self.enabled:
            raise StartCheckError("The start check is off.")
        draft, built, by_id = self._built(protocol_id)
        question = next((item for item in built if item["question_id"] == question_id), None)
        if question is None:
            raise StartCheckError("The question is not one of this protocol's.")
        if action not in (None, "accept"):
            raise StartCheckError("The action is unknown.")
        chosen_label: str | None = None
        correct: bool | None = None
        if action == "accept":
            proceed = True
        else:
            choice = next((item for item in question["choices"] if item["choice_id"] == choice_id), None)
            if choice is None:
                raise StartCheckError("The choice is not one of the question's.")
            chosen_label = str(choice["label_ko"])
            correct = chosen_label == question["value"]
            proceed = correct
        position = int(question["position"])
        following = next((item for item in built if int(item["position"]) == position + 1), None)
        revision = self.catalog._latest_protocol_revision(protocol_id)  # noqa: SLF001
        target = draft.get("target") or {}
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            "question_id": question["question_id"],
            "kind": question["kind"],
            "item_id": question["item_id"],
            "step_label": question["step_label"],
            "value": question["value"],
            "chosen": chosen_label,
            "correct": correct,
            "action": "accept" if action == "accept" else "answer",
            "analysis_id": target.get("analysis_id"),
            "target": {key: target.get(key) for key in ("kind", "version", "event_id") if key in target},
            "actor_principal_id": actor_principal_id,
            "recorded_at": recorded_at,
        }
        self.catalog.store.append_event(
            "start-check-" + secrets.token_hex(12), protocol_id, revision.revision_number,
            START_CHECK_EVENT, payload,
        )
        result: dict[str, Any] = {
            "question_id": question["question_id"],
            "correct": correct,
            "proceed": proceed,
            "chosen_label": chosen_label,
            "next_question_id": following["question_id"] if following is not None else None,
            "message_ko": "" if proceed else LOOK_AGAIN_KO,
            "followups": [],
            "card": None,
        }
        if not proceed:
            item = by_id.get(str(question["item_id"]), {})
            result["followups"] = [
                {"choice_id": "accept", "label_ko": ACCEPT_KO,
                 "request": {"question_id": question["question_id"], "action": "accept"}},
                {"choice_id": "open_card", "label_ko": OPEN_CARD_KO, "request": None},
            ]
            result["card"] = value_card(question, item)
        return result
