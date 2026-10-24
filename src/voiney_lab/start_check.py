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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from voiney_lab.configuration import boolean_setting

START_CHECK_ENABLED_ENV = "VOINEY_LAB_START_CHECK_ENABLED"
START_CHECK_EVENT = "protocol_start_check_answered"
QUESTION_COUNT = 2
KINDS = ("time", "temperature", "speed", "amount")
KIND_KO = {"time": "시간", "temperature": "온도", "speed": "속도", "amount": "양"}
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


@dataclass(frozen=True)
class StartTarget:
    """Immutable identity of the analysis/review whose values and safety were read."""

    kind: str | None = None
    version: int | None = None
    event_id: str | None = None
    analysis_id: str | None = None
    revision_id: str | None = None
    analysis_revision_number: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> StartTarget:
        fields = {key: value.get(key) for key in cls.__dataclass_fields__}
        for key, item in fields.items():
            expected = int if key in {"version", "analysis_revision_number"} else str
            if item is not None and (type(item) is not expected or (expected is int and item < 1)):
                raise StartCheckError("The start confirmation target is invalid.")
        return cls(**fields)


def start_check_enabled(environment: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    return boolean_setting(env,START_CHECK_ENABLED_ENV,default=True)


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
            "question_ko": f"{where}{KIND_KO[value.kind]}{'은' if value.kind in {'time', 'amount'} else '는'} 원문에 얼마로 적혀 있나요?",
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
        if target is None and draft.get("available_for_execution"):
            # Materialized development and older stored analyses may have no
            # editable review draft. Read their immutable executable analysis.
            revision = self.catalog._latest_protocol_revision(protocol_id)  # noqa: SLF001
            source = next((event for event in reversed(self.catalog.store.list_events(protocol_id))
                           if event.event_type == "development_fixture_materialized"), None)
            fixture = self.catalog.load_executable_fixture(protocol_id)
            analysis = self.catalog._latest_analysis(revision)  # noqa: SLF001
            if analysis is not None:
                target = {"kind": "development_fixture" if source is not None else "analysis",
                          "revision_id": source.payload["revision_id"] if source is not None else fixture.revision_id,
                          "analysis_id": analysis.analysis_id,
                          "analysis_revision_number": analysis.analysis_revision_number}
                items = [{"item_id": f"step:{step.step_id}", "kind": "step", "step_id": step.step_id,
                          "step_label": step.source_label, "text": step.instruction_source_text,
                          "source": {"page": step.evidence.source_page_number,
                                     "excerpt": step.evidence.source_excerpt}}
                         for step in fixture.steps]
                draft = {**draft, "target": target, "items": items}
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

    @staticmethod
    def _target_identity(target: Mapping[str, Any]) -> dict[str, Any]:
        return asdict(StartTarget.from_mapping(target))

    def readiness(
        self, protocol_id: str, *, actor_principal_id: str | None,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Read account/revision-bound receipts; client claims grant nothing."""

        if not self.enabled:
            return {"ready": True, "reason": None, "target": None}
        draft, questions, _ = self._built(protocol_id)
        target = draft.get("target") or {}
        revision = self.catalog._latest_protocol_revision(protocol_id)  # noqa: SLF001
        events = [event for event in self.catalog.store.list_events(protocol_id)
                  if event.protocol_revision_number == revision.revision_number]
        identity = self._target_identity(target)
        safety = None
        executable = bool(draft.get("available_for_execution"))
        for event in events:
            payload = event.payload
            if (event.event_type == "protocol_start_safety_acknowledged"
                    and actor_principal_id is not None
                    and payload.get("actor_principal_id") == actor_principal_id
                    and self._target_identity(payload.get("target") or {}) == identity):
                safety = payload
        # Recovery stays on its original analysed/reviewed revision. Its own
        # receipt supplies the exact questions checked then, never a new draft.
        if revision_id is not None and revision_id != identity.get("revision_id"):
            safety = next((event.payload for event in reversed(events)
                           if event.event_type == "protocol_start_safety_acknowledged"
                           and actor_principal_id is not None
                           and event.payload.get("actor_principal_id") == actor_principal_id
                           and (event.payload.get("target") or {}).get("revision_id") == revision_id), None)
            target = safety.get("target", {}) if safety else {}
            identity = self._target_identity(target)
            questions = safety.get("questions", []) if safety else []
            executable = safety is not None
        latest: dict[str, Any] = {}
        for event in events:
            payload = event.payload
            held = payload.get("target") or {}
            if (event.event_type == START_CHECK_EVENT
                    and actor_principal_id is not None
                    and payload.get("actor_principal_id") == actor_principal_id
                    and payload.get("analysis_id") == identity.get("analysis_id")
                    and all(held.get(key) == identity.get(key) for key in ("kind", "version", "event_id"))):
                latest[str(payload.get("question_id"))] = payload
        missing = [question["question_id"] for question in questions
                   if not ((answer := latest.get(question["question_id"]))
                           and answer.get("value") == question["value"]
                           and answer.get("item_id") == question["item_id"]
                           and (answer.get("correct") is True or answer.get("action") == "accept"))]
        reason = ("start_check_unavailable" if not target or not executable
                  else "start_check_identity_required" if actor_principal_id is None
                  else "start_check_required" if missing
                  else "safety_acknowledgement_required" if safety is None else None)
        return {"ready": reason is None, "reason": reason, "target": target,
                "missing_question_ids": missing, "safety_acknowledged": safety is not None}

    def acknowledge_safety(
        self, protocol_id: str, *, target: object, actor_principal_id: str | None,
    ) -> dict[str, Any]:
        """Append an explicit safety receipt only for the currently shown target."""

        status = self.readiness(protocol_id, actor_principal_id=actor_principal_id)
        if not self.enabled or status.get("reason") not in {None, "safety_acknowledgement_required"}:
            raise StartCheckError("Complete the current key-value check first.")
        if (not isinstance(target, Mapping)
                or self._target_identity(target) != self._target_identity(status["target"])):
            raise StartCheckError("The safety confirmation target is stale.")
        if status["safety_acknowledged"]:
            return status
        _, questions, _ = self._built(protocol_id)
        revision = self.catalog._latest_protocol_revision(protocol_id)  # noqa: SLF001
        self.catalog.store.append_event(
            "start-safety-" + secrets.token_hex(12), protocol_id, revision.revision_number,
            "protocol_start_safety_acknowledged",
            {"actor_principal_id": actor_principal_id,
             "target": self._target_identity(status["target"]),
             "questions": [{key: question[key] for key in ("question_id", "item_id", "value")}
                           for question in questions]},
        )
        return self.readiness(protocol_id, actor_principal_id=actor_principal_id)

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


# --- Today's values (lane EASY, decision 2, human decision 2026-10-24) -------
#
# A lab that runs from a paper's Methods changes conditions run to run and
# will not rewrite its protocol for that. After the key values, the start
# wizard offers "오늘 원문과 다르게 할 값" (it may be skipped): a value the
# analysis read -- a time, a temperature, a speed, a volume or a
# concentration in a step's own sentence -- and the new value a person types.
# Only the same kind of unit is taken (a time for a time), a value in a
# safety sentence of the step is never offered, and nothing here writes a
# completion criterion. The choice is one event in the protocol's ledger,
# bound to the person and the exact analysis or review version it was made
# on; the start applies it once (a second event names the experiment), and
# the run says and shows the value as "20분(오늘 바꾼 값 · 원문 15분)", runs
# the step's timer for it and writes "원문 → 실제" in the notebook. No model
# chooses or proposes such a value.

RUN_CHANGES_EVENT = "protocol_run_changes_chosen"
RUN_CHANGES_APPLIED_EVENT = "protocol_run_changes_applied"
RUN_CHANGE_KINDS = ("time", "temperature", "speed", "volume", "concentration")
RUN_CHANGE_KIND_KO = {"time": "시간", "temperature": "온도", "speed": "속도", "volume": "부피",
                      "concentration": "농도"}
TODAY_MARK_KO = "오늘 바꾼 값"
RUN_CHANGE_LIMIT = 12
#: A choice older than this is not today's: a start never applies it (a sheet
#: closed yesterday without a start leaves nothing for today's run).
RUN_CHANGE_MAX_AGE_SECONDS = 12 * 3600
RUN_CHANGE_SOURCES = ("wizard", "last_time")


@dataclass(frozen=True)
class RunUnit:
    """A unit a changed value may be written in: its kind, the family it is
    compared within (rpm is never ×g) and its scale to the family's base."""

    canonical: str
    kind: str
    family: str
    scale: float


_RUN_UNITS: dict[str, RunUnit] = {unit.canonical: unit for unit in (
    RunUnit("s", "time", "time", 1.0), RunUnit("min", "time", "time", 60.0),
    RunUnit("h", "time", "time", 3600.0), RunUnit("day", "time", "time", 86400.0),
    RunUnit("°C", "temperature", "celsius", 1.0), RunUnit("°F", "temperature", "fahrenheit", 1.0),
    RunUnit("rpm", "speed", "rpm", 1.0), RunUnit("×g", "speed", "rcf", 1.0),
    RunUnit("nL", "volume", "volume", 0.001), RunUnit("µL", "volume", "volume", 1.0),
    RunUnit("mL", "volume", "volume", 1000.0), RunUnit("L", "volume", "volume", 1_000_000.0),
    RunUnit("nM", "concentration", "molar", 0.001), RunUnit("µM", "concentration", "molar", 1.0),
    RunUnit("mM", "concentration", "molar", 1000.0), RunUnit("M", "concentration", "molar", 1_000_000.0),
    RunUnit("%", "concentration", "percent", 1.0),
    RunUnit("ng/µL", "concentration", "mass_per_volume", 1.0),
    RunUnit("µg/mL", "concentration", "mass_per_volume", 1.0),
    RunUnit("mg/mL", "concentration", "mass_per_volume", 1000.0),
    RunUnit("µg/µL", "concentration", "mass_per_volume", 1000.0),
    RunUnit("g/L", "concentration", "mass_per_volume", 1000.0),
    RunUnit("mg/L", "concentration", "mass_per_volume", 1.0),
)}
#: The units a family is written in, for the refusal that names them.
_FAMILY_UNITS_KO = {
    "time": "초·분·시간", "celsius": "°C", "fahrenheit": "°F", "rpm": "rpm", "rcf": "×g",
    "volume": "µL·mL·L", "molar": "nM·µM·mM·M", "percent": "%",
    "mass_per_volume": "mg/mL·µg/mL·ng/µL",
}
#: Spellings of a unit, folded (casefold, µ for μ and u, no spaces) -> its
#: canonical unit. Molar units are read case-sensitively (nm is a length).
_UNIT_SPELLINGS: dict[str, str] = {
    **{word: "s" for word in ("s", "sec", "secs", "second", "seconds", "초")},
    **{word: "min" for word in ("min", "mins", "minute", "minutes", "분")},
    **{word: "h" for word in ("h", "hr", "hrs", "hour", "hours", "시간")},
    **{word: "day" for word in ("day", "days", "일")},
    **{word: "°C" for word in ("°c", "℃", "c", "도", "°")},
    "°f": "°F", "rpm": "rpm", "알피엠": "rpm",
    **{word: "×g" for word in ("×g", "xg", "rcf")},
    "nl": "nL", "µl": "µL", "마이크로리터": "µL", "ml": "mL", "밀리리터": "mL", "l": "L", "리터": "L",
    "%": "%", "퍼센트": "%",
    "나노몰": "nM", "마이크로몰": "µM", "밀리몰": "mM", "몰": "M",
    "ng/µl": "ng/µL", "µg/ml": "µg/mL", "mg/ml": "mg/mL", "µg/µl": "µg/µL", "g/l": "g/L",
    "mg/l": "mg/L",
}
_MOLAR = {"nM": "nM", "µM": "µM", "uM": "µM", "μM": "µM", "mM": "mM", "M": "M"}
_RUN_NUMBER = r"-?\d+(?:\.\d+)?"
_RUN_SOURCE_VALUE = re.compile(
    r"(?<![\w.])(?P<number>\d+(?:\.\d+)?)\s?(?P<unit>"
    r"(?:[nµμu]?g|mg)\s?/\s?(?:[µμu]?L|ml|mL|l)"
    r"|°\s?[CF]|℃|[CF](?![A-Za-z])"
    r"|rpm|RPM|[x×]\s?g(?![A-Za-z])|RCF"
    r"|[nµμu]?[Ll](?![A-Za-z])|m[Ll](?![A-Za-z])"
    r"|[nµμum]?M(?![A-Za-z])"
    r"|%"
    r"|(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)(?![A-Za-z])"
    r"|(?:s|min|h)(?![A-Za-z])"
    r"|초|분|시간"
    r")"
)
#: A sentence that sets a limit or warns: its values are a safety statement's,
#: never offered for change.
_SAFETY_SENTENCE = re.compile(
    r"\b(?:do\s+not|don't|never|must\s+not|should\s+not|exceed|caution|warning|hazard|danger"
    r"|toxic|flammable|corrosive)\b|주의|경고|금지|넘지|초과|위험",
    re.IGNORECASE,
)


def run_unit(unit: str) -> RunUnit | None:
    """The canonical unit of a spelling ("mins", "분", "x g", "uL"), or None."""

    text = unicodedata.normalize("NFKC", str(unit or "")).strip()
    if text.replace(" ", "") in _MOLAR:
        return _RUN_UNITS[_MOLAR[text.replace(" ", "")]]
    folded = text.casefold().replace("μ", "µ").replace(" ", "")
    if folded.startswith("u") and folded[1:2] in {"l", "g"} and len(folded) <= 5:
        folded = "µ" + folded[1:]
    folded = folded.replace("/ul", "/µl")
    canonical = _UNIT_SPELLINGS.get(folded)
    return _RUN_UNITS.get(canonical) if canonical else None


def value_words(number: str, unit: str) -> str:
    """A value as the run says and shows it: "20분", "1.5시간", "40°C", "1.5 mL"."""

    if unit in {"s", "min", "h", "day"}:
        return f"{number}{ {'s': '초', 'min': '분', 'h': '시간', 'day': '일'}[unit] }"
    if unit in {"°C", "°F", "%"}:
        return f"{number}{unit}"
    return f"{number} {unit}"


def _base(number: str, unit: str) -> float | None:
    info = _RUN_UNITS.get(unit)
    try:
        return float(number) * info.scale if info is not None else None
    except ValueError:
        return None


def _same_value(number: str, unit: str, other_number: str, other_unit: str) -> bool:
    mine, theirs = _RUN_UNITS.get(unit), _RUN_UNITS.get(other_unit)
    if mine is None or theirs is None or mine.family != theirs.family:
        return False
    left, right = _base(number, unit), _base(other_number, other_unit)
    return left is not None and right is not None and abs(left - right) <= 1e-9 * max(1.0, abs(right))


class RunChangeError(StartCheckError):
    """A changed value the server does not take, with the sentence that says why."""

    def __init__(self, code: str, message_ko: str) -> None:
        super().__init__(message_ko)
        self.code = code
        self.message_ko = message_ko


@dataclass(frozen=True)
class ChangeableValue:
    """One value of a step's own sentence that a run may use another value for."""

    item_id: str
    step_id: str
    step_label: str
    kind: str
    number: str
    unit: str
    written: str

    @property
    def shown(self) -> str:
        return value_words(self.number, self.unit)

    @property
    def seconds(self) -> int | None:
        base = _base(self.number, self.unit) if self.kind == "time" else None
        return int(round(base)) if base is not None else None

    def public_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id, "step_id": self.step_id, "step_label": self.step_label,
            "kind": self.kind, "kind_ko": RUN_CHANGE_KIND_KO[self.kind], "number": self.number,
            "unit": self.unit, "written": self.written, "shown_ko": self.shown,
            "units_ko": _FAMILY_UNITS_KO[_RUN_UNITS[self.unit].family],
        }


def _sentences(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for match in re.finditer(r"[.!?。;](?=\s|$)|\n", text):
        spans.append((start, match.end()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def step_changeable_values(
    step_id: str, step_label: str, text: str, warnings: Sequence[str] = (),
) -> list[ChangeableValue]:
    """The values of one step's sentence a run may change, in order.

    A step's own number opening its sentence is not a value; a value inside
    a sentence that warns or sets a limit, or that one of the step's safety
    statements prints too, is never offered (decision 2: "안전 주의 글과 완료
    기준은 바꿀 수 없다").
    """

    words = str(text or "")
    begin = 0
    opening = _LEADING_LABEL.match(words)
    if opening is not None:
        begin = opening.end()
    guarded = [span for span in _sentences(words) if _SAFETY_SENTENCE.search(words[span[0]:span[1]])]
    warned = " ".join(" ".join(str(item or "").split()) for item in warnings)
    found: list[ChangeableValue] = []
    for match in _RUN_SOURCE_VALUE.finditer(words, begin):
        unit = run_unit(match["unit"])
        if unit is None or unit.kind not in RUN_CHANGE_KINDS:
            continue
        if any(first <= match.start() < last for first, last in guarded):
            continue
        written = " ".join(match[0].split())
        if warned and re.search(rf"(?<![\d.]){re.escape(match['number'])}\s?{re.escape(match['unit'].strip())}", warned):
            continue
        if any(item.unit == unit.canonical and item.number == match["number"] for item in found):
            continue
        found.append(ChangeableValue(
            item_id=f"{step_id}#{len(found) + 1}", step_id=step_id, step_label=step_label,
            kind=unit.kind, number=match["number"], unit=unit.canonical, written=written))
    return found


def changeable_values(steps: Sequence[Any]) -> list[ChangeableValue]:
    """Every changeable value of a run's steps (``ProtocolSourceStep``s), in step order."""

    found: list[ChangeableValue] = []
    for step in steps:
        warnings = [getattr(item, "source_text", "") for item in getattr(step, "warnings", ()) or ()]
        found += step_changeable_values(
            step.step_id, step.source_label, step.instruction_source_text, warnings)
    return found


def parse_run_value(text: object) -> tuple[str, str] | None:
    """A typed value as (number, canonical unit): "20 min", "20분", "40 °C", "1.5mL"."""

    words = unicodedata.normalize("NFKC", str(text or "")).strip()
    match = re.fullmatch(rf"({_RUN_NUMBER})\s*([^\d\s].*)", words)
    if match is None:
        return None
    unit = run_unit(match[2])
    return (match[1], unit.canonical) if unit is not None else None


@dataclass(frozen=True)
class RunChange:
    """A value a person chose for this run in place of the source's."""

    item_id: str
    step_id: str
    step_label: str
    kind: str
    source_number: str
    source_unit: str
    source_written: str
    number: str
    unit: str

    @property
    def shown(self) -> str:
        return value_words(self.number, self.unit)

    @property
    def source_shown(self) -> str:
        return value_words(self.source_number, self.source_unit)

    @property
    def words(self) -> str:
        """"20분(오늘 바꾼 값 · 원문 15분)"."""

        return f"{self.shown}({TODAY_MARK_KO} · 원문 {self.source_shown})"

    @property
    def seconds(self) -> int | None:
        base = _base(self.number, self.unit) if self.kind == "time" else None
        return int(round(base)) if base is not None else None

    @property
    def source_seconds(self) -> int | None:
        base = _base(self.source_number, self.source_unit) if self.kind == "time" else None
        return int(round(base)) if base is not None else None

    def public_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id, "step_id": self.step_id, "step_label": self.step_label,
            "kind": self.kind, "kind_ko": RUN_CHANGE_KIND_KO.get(self.kind, ""),
            "source_number": self.source_number, "source_unit": self.source_unit,
            "source_written": self.source_written, "source_shown_ko": self.source_shown,
            "number": self.number, "unit": self.unit, "shown_ko": self.shown, "words_ko": self.words,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RunChange:
        fields = {key: value.get(key) for key in (
            "item_id", "step_id", "step_label", "kind", "source_number", "source_unit",
            "source_written", "number", "unit")}
        if any(not isinstance(item, str) or not item for item in fields.values()):
            raise RunChangeError("run_change_invalid", "바꾼 값 기록을 읽을 수 없어요.")
        if fields["kind"] not in RUN_CHANGE_KINDS or fields["unit"] not in _RUN_UNITS:
            raise RunChangeError("run_change_invalid", "바꾼 값 기록을 읽을 수 없어요.")
        return cls(**fields)


def check_run_change(value: ChangeableValue, number: str, unit: str) -> RunChange:
    """The change of ``value`` to ``number unit``, or the reason it is refused."""

    info = _RUN_UNITS.get(unit)
    if info is None:
        raise RunChangeError(
            "run_change_unit_unknown",
            f"단위를 알 수 없어요. 원문 값({value.shown})과 같은 종류인 "
            f"{_FAMILY_UNITS_KO[_RUN_UNITS[value.unit].family]} 단위로 넣어 주세요.")
    source = _RUN_UNITS[value.unit]
    if info.kind != source.kind or info.family != source.family:
        raise RunChangeError(
            "run_change_unit_kind",
            f"단위 종류가 달라요. 원문 값({value.shown})과 같은 종류인 "
            f"{_FAMILY_UNITS_KO[source.family]} 단위로 넣어 주세요.")
    try:
        amount = float(number)
    except ValueError as exc:
        raise RunChangeError("run_change_number_invalid", "숫자를 알아듣지 못했어요. 다시 넣어 주세요.") from exc
    if amount != amount or amount in {float("inf"), float("-inf")} or (
            info.kind != "temperature" and amount <= 0) or len(number) > 12:
        raise RunChangeError("run_change_number_invalid", "0보다 큰 값으로 넣어 주세요.")
    if _same_value(number, unit, value.number, value.unit):
        raise RunChangeError("run_change_same_as_source", f"원문과 같은 값이에요({value.shown}).")
    return RunChange(
        item_id=value.item_id, step_id=value.step_id, step_label=value.step_label, kind=value.kind,
        source_number=value.number, source_unit=value.unit, source_written=value.written,
        number=number, unit=unit)


def _target_key(target: Mapping[str, Any] | None) -> dict[str, Any]:
    target = target or {}
    return {key: target.get(key) for key in ("kind", "version", "event_id", "analysis_id", "revision_id",
                                             "analysis_revision_number")}


class RunChanges:
    """Today's values of a protocol about to run: what may change, a person's
    choice, and the choice the start applies (lane EASY, decision 2)."""

    def __init__(self, catalog: Any) -> None:
        self.catalog = catalog

    def _current(self, protocol_id: str) -> tuple[dict[str, Any], Any, list[ChangeableValue]]:
        """(the target the run would follow, its fixture, its changeable values)."""

        draft, _, _ = StartCheck(self.catalog, enabled=True)._built(protocol_id)  # noqa: SLF001
        target = draft.get("target") if isinstance(draft.get("target"), Mapping) else None
        if target is None or not draft.get("available_for_execution"):
            raise RunChangeError("run_change_unavailable", "실행할 수 있는 분석이 없어요.")
        fixture = self.catalog.load_executable_fixture(protocol_id)
        return dict(target), fixture, changeable_values(fixture.steps)

    def options(self, protocol_id: str) -> dict[str, Any]:
        target, fixture, values = self._current(protocol_id)
        return {"protocol_id": protocol_id, "target": target, "revision_id": fixture.revision_id,
                "values": [value.public_dict() for value in values], "limit": RUN_CHANGE_LIMIT}

    def choose(
        self, protocol_id: str, changes: object, *, target: object, actor_principal_id: str | None,
        source: object = "wizard",
    ) -> dict[str, Any]:
        """Check each (item, new value) and append the choice; an empty list is
        the step skipped, which is a choice too (nothing changes today)."""

        if actor_principal_id is None:
            raise RunChangeError("run_change_identity_required", "로그인한 뒤에 고를 수 있어요.")
        current, fixture, values = self._current(protocol_id)
        if not isinstance(target, Mapping) or _target_key(target) != _target_key(current):
            raise RunChangeError("run_change_target_stale", "분석이 바뀌었어요. 시작 화면을 다시 열어 주세요.")
        if source not in RUN_CHANGE_SOURCES:
            raise RunChangeError("run_change_invalid", "바꾼 값을 읽을 수 없어요.")
        if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes)) or len(changes) > RUN_CHANGE_LIMIT:
            raise RunChangeError("run_change_invalid", f"바꿀 값은 {RUN_CHANGE_LIMIT}개까지 고를 수 있어요.")
        by_id = {value.item_id: value for value in values}
        chosen: list[RunChange] = []
        for item in changes:
            if not isinstance(item, Mapping) or str(item.get("item_id")) not in by_id:
                raise RunChangeError("run_change_item_unknown", "고른 값이 이 분석의 값 목록에 없어요.")
            value = by_id[str(item.get("item_id"))]
            if any(change.item_id == value.item_id for change in chosen):
                raise RunChangeError("run_change_invalid", "같은 값을 두 번 골랐어요.")
            parsed = parse_run_value(item.get("value"))
            if parsed is None:
                raise RunChangeError(
                    "run_change_number_invalid",
                    f"{value.step_label}단계 {value.shown}: 새 값을 숫자와 단위로 넣어 주세요(예: "
                    f"{value_words('20' if value.kind != 'temperature' else '40', value.unit)}).")
            try:
                chosen.append(check_run_change(value, *parsed))
            except RunChangeError as exc:
                raise RunChangeError(exc.code, f"{value.step_label}단계 {value.shown}: {exc.message_ko}") from exc
        change_set_id = "run-changes-" + secrets.token_hex(12)
        revision = self.catalog._latest_protocol_revision(protocol_id)  # noqa: SLF001
        payload = {
            "change_set_id": change_set_id, "actor_principal_id": actor_principal_id,
            "target": _target_key(current), "revision_id": fixture.revision_id, "source": source,
            "changes": [change.public_dict() for change in chosen],
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.catalog.store.append_event(change_set_id, protocol_id, revision.revision_number,
                                        RUN_CHANGES_EVENT, payload)
        return {"change_set_id": change_set_id, "changes": payload["changes"], "source": source}

    def for_start(
        self, protocol_id: str, *, actor_principal_id: str | None, revision_id: str | None,
        now: datetime | None = None,
    ) -> tuple[str | None, tuple[RunChange, ...]]:
        """The person's latest choice on the run's revision that no start used
        yet, made within ``RUN_CHANGE_MAX_AGE_SECONDS``."""

        if actor_principal_id is None or revision_id is None:
            return None, ()
        events = self.catalog.store.list_events(protocol_id)
        used = {str((event.payload or {}).get("change_set_id")) for event in events
                if event.event_type == RUN_CHANGES_APPLIED_EVENT}
        latest = next((event.payload for event in reversed(events)
                       if event.event_type == RUN_CHANGES_EVENT
                       and (event.payload or {}).get("actor_principal_id") == actor_principal_id
                       and (event.payload or {}).get("revision_id") == revision_id), None)
        if latest is None or str(latest.get("change_set_id")) in used:
            return None, ()
        try:
            age = ((now or datetime.now(timezone.utc))
                   - datetime.fromisoformat(str(latest.get("recorded_at")))).total_seconds()
        except (TypeError, ValueError):
            return None, ()
        if not 0 <= age <= RUN_CHANGE_MAX_AGE_SECONDS:
            return None, ()
        try:
            changes = tuple(RunChange.from_mapping(item) for item in latest.get("changes") or ())
        except RunChangeError:
            return None, ()
        return str(latest["change_set_id"]), changes

    def mark_applied(
        self, protocol_id: str, change_set_id: str, *, experiment_session_id: str | None,
        actor_principal_id: str | None,
    ) -> None:
        revision = self.catalog._latest_protocol_revision(protocol_id)  # noqa: SLF001
        self.catalog.store.append_event(
            "run-changes-applied-" + secrets.token_hex(12), protocol_id, revision.revision_number,
            RUN_CHANGES_APPLIED_EVENT,
            {"change_set_id": change_set_id, "experiment_session_id": experiment_session_id,
             "actor_principal_id": actor_principal_id,
             "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    def applied_to(self, protocol_id: str, experiment_session_id: str) -> tuple[RunChange, ...]:
        """The choice a start applied to that experiment (for its recovery)."""

        events = self.catalog.store.list_events(protocol_id)
        applied = next((event.payload for event in reversed(events)
                        if event.event_type == RUN_CHANGES_APPLIED_EVENT
                        and (event.payload or {}).get("experiment_session_id") == experiment_session_id), None)
        if applied is None:
            return ()
        chosen = next((event.payload for event in events if event.event_type == RUN_CHANGES_EVENT
                       and (event.payload or {}).get("change_set_id") == applied.get("change_set_id")), None)
        try:
            return tuple(RunChange.from_mapping(item) for item in (chosen or {}).get("changes") or ())
        except RunChangeError:
            return ()
