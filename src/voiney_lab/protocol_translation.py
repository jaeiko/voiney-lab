"""Korean for every sentence a Protocol revision shows, made once per revision.

When a revision becomes executable (development activation or approval) each
sentence the step card, the safety box and the answers draw from it -- step
guidance, sub-actions, warnings, notes, expected results, before-start
prerequisites and the purpose -- is translated into Korean once, in batches,
by the same model and endpoint the per-turn reader translation uses. Each
sentence is held to ``reader_translation_issue`` (numbers with their units,
the protocol's own terms, negation); a sentence that fails is stored as
failed and is never shown, so the screen keeps its source as the body.

Nothing here touches workflow state. A stored translation is presentation
data the session picks in this order: reviewed translation (the fixture's
sidecar, or a stored ``reviewed`` row), stored machine translation, source.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Sequence

from voiney_lab.curated_protocol import (
    PURPOSE_FACT_KEY,
    CuratedProtocolFixture,
    ProtocolKnowledgeView,
    ProtocolVocabulary,
    _term_pattern,
    reader_translation_issue,
)
from voiney_lab.workspace_store import FactTranslationRecord


log = logging.getLogger(__name__)

TARGET_LANGUAGE = "ko"
PROMPT_VERSION = "revision_translation_v1"
#: The fact kinds the screen and the answers draw, in the order they appear.
TRANSLATED_KINDS = (
    "step", "sub_action", "warning", "note", "expected_result", "prerequisite",
)
#: How much one model call carries.
BATCH_MAX_ITEMS = 16
BATCH_MAX_CHARACTERS = 3500

REVISION_TRANSLATION_PROMPT = (
    "You translate sentences of one laboratory protocol for a researcher who "
    "is new to the lab and reads Korean. Translate each item on its own into "
    "plain, concrete Korean that says exactly what the sentence says, so it "
    "can be followed at the bench. Keep every number, unit, time, "
    "temperature, concentration and ratio exactly as written, and keep "
    "reagent, material and equipment names in their original spelling. Keep "
    "every negation and do not add one. Do not add any step, reason, "
    "warning, quantity, condition or advice the text does not state, and do "
    "not leave out any instruction. Return one entry for every id, and only "
    "the JSON object."
)


@dataclass(frozen=True)
class TranslationUnit:
    """One sentence to translate, named the way the fixture names it."""

    fact_key: str
    kind: str
    source_text: str
    required_terms: tuple[str, ...] = ()

    @property
    def source_sha256(self) -> str:
        return source_sha256(self.source_text)


@dataclass(frozen=True)
class BatchResult:
    """What one model call returned: id -> Korean, and what it cost."""

    translations: dict[str, str]
    model_version: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


BatchTranslator = Callable[[Sequence[TranslationUnit]], Awaitable[BatchResult]]


@dataclass
class GenerationReport:
    revision_id: str
    units: int = 0
    skipped_reviewed: int = 0
    skipped_korean: int = 0
    skipped_stored: int = 0
    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    records: list[FactTranslationRecord] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.records if item.check_result == "passed")

    @property
    def refused(self) -> list[FactTranslationRecord]:
        return [item for item in self.records if item.check_result != "passed"]


def source_sha256(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")


def is_korean(text: str) -> bool:
    """Whether a source sentence is already written in Korean."""

    hangul = len(_HANGUL.findall(text))
    return hangul > 0 and hangul >= len(_LATIN.findall(text))


def translation_units(fixture: CuratedProtocolFixture) -> tuple[TranslationUnit, ...]:
    """Every sentence the screen, the safety box and the answers draw."""

    vocabulary = ProtocolVocabulary.from_fixture(fixture)

    def terms(text: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            term.text for term in vocabulary.terms
            if _term_pattern(term.text).search(text)
        ))

    units: list[TranslationUnit] = []
    seen: set[str] = set()
    try:
        purpose = ProtocolKnowledgeView.from_fixture(fixture).purpose
    except Exception:  # noqa: BLE001 - a fixture without a purpose has none
        purpose = None
    if purpose is not None and purpose.text.strip():
        units.append(TranslationUnit(
            PURPOSE_FACT_KEY, "purpose", purpose.text, terms(purpose.text)))
        seen.add(PURPOSE_FACT_KEY)
    for index, step in enumerate(fixture.steps):
        for fact in fixture.facts_for_step(index):
            if fact.kind not in TRANSLATED_KINDS or not fact.text.strip():
                continue
            key = f"{step.step_id}/{fact.fact_id}"
            if key in seen:
                continue
            seen.add(key)
            units.append(TranslationUnit(key, fact.kind, fact.text, terms(fact.text)))
    return tuple(units)


def _reviewed_keys(fixture: CuratedProtocolFixture) -> set[str]:
    return set(getattr(fixture, "localizations", None) or ())


def _batches(units: Sequence[TranslationUnit]) -> list[list[TranslationUnit]]:
    """Consecutive units, a step's sentences together, within the call bounds."""

    batches: list[list[TranslationUnit]] = []
    current: list[TranslationUnit] = []
    size = 0
    for unit in units:
        length = len(unit.source_text)
        if current and (
            len(current) >= BATCH_MAX_ITEMS or size + length > BATCH_MAX_CHARACTERS
        ):
            batches.append(current)
            current, size = [], 0
        current.append(unit)
        size += length
    if current:
        batches.append(current)
    return batches


def check_translation(unit: TranslationUnit, korean: str | None) -> str:
    if not isinstance(korean, str) or not korean.strip():
        return "missing"
    issue = reader_translation_issue(
        unit.source_text, korean, required_terms=unit.required_terms)
    return issue or "passed"


async def generate_revision_translations(
    fixture: CuratedProtocolFixture,
    translate: BatchTranslator,
    *,
    model: str,
    stored: Iterable[FactTranslationRecord] = (),
    clock: Callable[[], datetime] | None = None,
) -> GenerationReport:
    """Translate the sentences of one revision not yet translated or reviewed.

    Sentences with a reviewed translation, sentences already in Korean and
    sentences already stored for this revision and source text are skipped,
    so a second call for the same revision makes no model call. A batch
    whose call fails stores nothing, so a later activation can try again.
    """

    now = clock or (lambda: datetime.now(timezone.utc))
    report = GenerationReport(revision_id=fixture.revision_id)
    held = {
        (item.fact_key, item.source_sha256) for item in stored
        if item.revision_id == fixture.revision_id
        and item.language == TARGET_LANGUAGE
    }
    reviewed = _reviewed_keys(fixture)
    pending: list[TranslationUnit] = []
    for unit in translation_units(fixture):
        report.units += 1
        if unit.fact_key in reviewed:
            report.skipped_reviewed += 1
        elif is_korean(unit.source_text):
            report.skipped_korean += 1
        elif (unit.fact_key, unit.source_sha256) in held:
            report.skipped_stored += 1
        else:
            pending.append(unit)
    for batch in _batches(pending):
        report.calls += 1
        try:
            result = await translate(batch)
        except Exception as exc:  # noqa: BLE001 - a failed call stores nothing
            report.failed_calls += 1
            log.warning(
                "revision_translation batch_failed revision=%s items=%d error=%s",
                fixture.revision_id, len(batch), type(exc).__name__,
            )
            continue
        report.prompt_tokens += result.prompt_tokens
        report.completion_tokens += result.completion_tokens
        created = now().isoformat()
        for unit in batch:
            korean = result.translations.get(unit.fact_key)
            korean = " ".join(korean.split()) if isinstance(korean, str) else ""
            report.records.append(FactTranslationRecord(
                revision_id=fixture.revision_id,
                fact_key=unit.fact_key,
                language=TARGET_LANGUAGE,
                source_sha256=unit.source_sha256,
                translated_text=korean,
                status="machine",
                check_result=check_translation(unit, korean),
                model=model,
                model_version=result.model_version or model,
                created_at=created,
            ))
    # Counts only: the sentences and the readings stay out of logs.
    log.info(
        "revision_translation revision=%s units=%d calls=%d failed_calls=%d "
        "passed=%d refused=%d prompt_tokens=%d completion_tokens=%d",
        fixture.revision_id, report.units, report.calls, report.failed_calls,
        report.passed, len(report.refused), report.prompt_tokens,
        report.completion_tokens,
    )
    return report


def stored_localizations(
    fixture: CuratedProtocolFixture,
    records: Iterable[FactTranslationRecord],
) -> tuple[dict[str, str], dict[str, str]]:
    """(reviewed, machine) Korean by fact key, from rows that still hold.

    A row is used only when it belongs to this revision, was made from the
    sentence the revision holds now, and passes the check again here.
    """

    units = {unit.fact_key: unit for unit in translation_units(fixture)}
    reviewed: dict[str, str] = {}
    machine: dict[str, str] = {}
    for record in records:
        unit = units.get(record.fact_key)
        if (
            unit is None
            or record.revision_id != fixture.revision_id
            or record.language != TARGET_LANGUAGE
            or record.source_sha256 != unit.source_sha256
            or record.check_result != "passed"
            or check_translation(unit, record.translated_text) != "passed"
        ):
            continue
        target = reviewed if record.status == "reviewed" else machine
        target[record.fact_key] = record.translated_text
    return reviewed, machine


def with_stored_translations(
    fixture: CuratedProtocolFixture,
    records: Iterable[FactTranslationRecord],
) -> CuratedProtocolFixture:
    """The fixture carrying the stored translations that may be shown."""

    reviewed, machine = stored_localizations(fixture, records)
    if not reviewed and not machine:
        return fixture
    sidecar = dict(getattr(fixture, "localizations", None) or {})
    for key, text in reviewed.items():
        sidecar.setdefault(key, text)
    return replace(
        fixture,
        localizations=sidecar or fixture.localizations,
        machine_localizations={
            key: text for key, text in machine.items() if key not in sidecar
        } or None,
    )


def openai_batch_translator(
    client_factory: Callable[[], Any], model: str, *, timeout: float = 60.0,
) -> BatchTranslator:
    """One structured chat call per batch, on the reader translation's model."""

    async def translate(batch: Sequence[TranslationUnit]) -> BatchResult:
        client = client_factory()
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": REVISION_TRANSLATION_PROMPT},
                    {"role": "user", "content": json.dumps(
                        {"items": [
                            {"id": unit.fact_key, "source_text": unit.source_text}
                            for unit in batch
                        ]},
                        ensure_ascii=False,
                    )},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "protocol_revision_translation_v1",
                        "strict": True,
                        "schema": {
                            "type": "object", "additionalProperties": False,
                            "properties": {"items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "id": {"type": "string"},
                                        "korean": {"type": "string"},
                                    },
                                    "required": ["id", "korean"],
                                },
                            }},
                            "required": ["items"],
                        },
                    },
                },
                temperature=0,
            ),
            timeout=timeout,
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        items = payload.get("items") if isinstance(payload, dict) else None
        translations: dict[str, str] = {}
        for item in items if isinstance(items, list) else ():
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("korean"), str)
            ):
                translations.setdefault(item["id"], item["korean"])
        usage = getattr(response, "usage", None)
        return BatchResult(
            translations=translations,
            model_version=str(getattr(response, "model", "") or model),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )

    return translate
