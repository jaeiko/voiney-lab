"""Korean for every sentence a Protocol revision shows, made once per revision.

As soon as a revision's analysis passes (lane PX, human decision 1 of
2026-10-06) -- and again, without a second model call, when it becomes
executable (development activation or approval) or a session opens on it --
each sentence the step card, the safety box and the answers draw from it is
translated into Korean once, by the translation role's model: step guidance,
sub-actions, warnings, notes, expected results, before-start prerequisites
and the purpose. The rows are stored under ``translation_revision_key``
(protocol id and revision id together): a catalog revision id such as
``pdf-1-analysis-1`` repeats for every uploaded PDF, so the bare id would
hand one protocol's glossary and rows to another.

The translation reads the protocol, not single sentences. A glossary is made
first from the title, the purpose, the materials, the equipment and every
step, fixing one Korean form for the words the protocol repeats (names of
reagents, equipment and products stay in English); it is stored with the
revision. Each sentence call then carries the title, a short purpose, the
glossary, and the step the sentence belongs to with the steps either side.
The style is the reviewed in-gel Korean's (``STYLE_GUIDE``).

Sentences are translated purpose and safety first, then step by step from
step 1, with the steps open sessions are on (current and next) ahead of the
rest. Every batch is checked and handed on as soon as it returns, so an open
session's card turns Korean while the rest is still being made. Each
sentence is held to ``reader_translation_issue``; a sentence that fails is
stored as failed and never shown, so the screen keeps its source. The names
it must keep in English are the glossary's ``keep_english`` entries (matched
in singular or plural); an ordinary word the glossary gives in Korean
("Petri dish", "agar", "metal plate") is not demanded in English, and a
term the glossary does not know is demanded only when it is written like a
name (a capital letter, as in "LB", "Porapak", "Ringer's"). Measured on
2026-10-06 (lane PX), the stricter rule -- every material or equipment name
the analysis listed, verbatim -- refused 36 of 68 headspace sentences, all
for a Korean common noun.

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
from voiney_lab.workspace_store import FactTranslationRecord, TranslationGlossaryRecord


log = logging.getLogger(__name__)

TARGET_LANGUAGE = "ko"
PROMPT_VERSION = "revision_translation_v2"
#: The fact kinds the screen and the answers draw.
TRANSLATED_KINDS = (
    "step", "sub_action", "warning", "note", "expected_result", "prerequisite",
)
#: Kinds made first, with the purpose: what keeps the bench safe.
SAFETY_KINDS = frozenset({"warning", "note"})
#: How much one model call carries. Measured 2026-10-03 on grok-4.6: one
#: sentence took about 48 s and a 16-sentence call did not return in 60 s,
#: so a call carries at most 8 sentences and is allowed 180 s. Generation
#: runs off the request, so nobody waits on it.
BATCH_MAX_ITEMS = 8
BATCH_MAX_CHARACTERS = 2000
BATCH_TIMEOUT_SECONDS = 180.0
#: The glossary call reads the whole protocol, so it gets its own bound.
GLOSSARY_MAX_CHARACTERS = 60000
GLOSSARY_MAX_ENTRIES = 80
PURPOSE_SUMMARY_CHARACTERS = 600

#: The reviewed in-gel Korean (candidate_a_curated_analysis.localization.ko.json)
#: is the house style every machine translation follows.
STYLE_GUIDE = (
    "Write in formal polite Korean declarative style (합니다체: '넣습니다', "
    "'배양합니다', '준비합니다'), as instructions to the reader, never "
    "addressing the reader as '당신'. Begin a step instruction with its step "
    "label followed by '단계: ' (for example '3단계: '), and an expected "
    "result with '예상 결과: '. Keep names of reagents, solutions, kits, "
    "products, instruments and brands in their original English spelling "
    "(for example Solution A, HPLC water, acetonitrile, AMBIC, DTT, "
    "trypsin, Thermomixer, speedvac), following the glossary. Write ordinary "
    "words in Korean (water as 물 unless it is part of a reagent name, tube "
    "튜브, gloves 장갑, gel 젤, chamber 챔버, hours 시간, room temperature "
    "실온). Keep every number, unit, symbol, time, temperature, "
    "concentration and ratio exactly as written."
)

GLOSSARY_PROMPT = (
    "You prepare a Korean glossary for translating one laboratory protocol "
    "for a researcher who is new to the lab. Read the whole protocol and list "
    "the words and phrases it repeats or that need one fixed Korean form: "
    "reagents, solutions, materials, equipment, products and techniques, and "
    "the ordinary lab words it uses. For a name of a reagent, solution, kit, "
    "product, instrument or brand set keep_english to true and korean to the "
    "English name itself, or to 'Korean(English)' when a Korean reader needs "
    "the meaning on first use. For an ordinary word (water, chamber, gloves, "
    "hours, tube, incubation, contamination) set keep_english to false and "
    "give the plain Korean word. Return only the JSON object."
)

REVISION_TRANSLATION_PROMPT = (
    "You translate sentences of one laboratory protocol into Korean for a "
    "researcher who is new to the lab, so each can be followed at the bench. "
    "You are given the protocol title, a short purpose, a glossary, and the "
    "steps around the sentences; use them to understand what each sentence "
    "means in this protocol, then translate each item so it says exactly "
    "what the sentence says -- not word by word. "
    + STYLE_GUIDE
    + " Use the glossary's Korean for every glossary word. Keep every "
    "negation and do not add one. Do not add any step, reason, warning, "
    "quantity, condition or advice the text does not state, and do not leave "
    "out any instruction. Return one entry for every id, and only the JSON "
    "object."
)


@dataclass(frozen=True)
class TranslationUnit:
    """One sentence to translate, named the way the fixture names it."""

    fact_key: str
    kind: str
    source_text: str
    #: Every protocol vocabulary term the sentence uses. Which of them the
    #: Korean must keep in English is decided by ``required_terms_for``.
    required_terms: tuple[str, ...] = ()
    #: The step the sentence belongs to; None for the purpose.
    step_index: int | None = None
    step_label: str | None = None

    @property
    def source_sha256(self) -> str:
        return source_sha256(self.source_text)


@dataclass(frozen=True)
class GlossaryEntry:
    source: str
    korean: str
    keep_english: bool


@dataclass(frozen=True)
class ProtocolContext:
    """What a translation call reads besides its own sentences."""

    title: str
    purpose: str
    materials: tuple[str, ...]
    equipment: tuple[str, ...]
    #: (label, full step text) in order.
    steps: tuple[tuple[str, str], ...]

    def whole_text(self) -> str:
        parts = [self.title, self.purpose, *self.materials, *self.equipment]
        parts.extend(f"{label} {text}" for label, text in self.steps)
        return "\n".join(" ".join(part.split()) for part in parts if part)[
            :GLOSSARY_MAX_CHARACTERS]

    def purpose_summary(self) -> str:
        return " ".join(self.purpose.split())[:PURPOSE_SUMMARY_CHARACTERS]

    def neighbourhood(self, indexes: Iterable[int]) -> list[dict[str, str]]:
        """The steps these indexes name, with the step before and after each."""

        chosen = sorted({
            near for index in indexes for near in (index - 1, index, index + 1)
            if 0 <= near < len(self.steps)
        })
        return [
            {"step_label": self.steps[index][0],
             "text": " ".join(self.steps[index][1].split())}
            for index in chosen
        ]


@dataclass(frozen=True)
class BatchResult:
    """What one model call returned: id -> Korean, and what it cost."""

    translations: dict[str, str]
    model_version: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class GlossaryResult:
    entries: tuple[GlossaryEntry, ...]
    model_version: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


#: (sentences, request context) -> what the model returned.
BatchTranslator = Callable[[Sequence[TranslationUnit], dict[str, Any]], Awaitable[BatchResult]]
GlossaryMaker = Callable[[ProtocolContext], Awaitable[GlossaryResult]]


@dataclass
class GenerationReport:
    revision_id: str
    units: int = 0
    skipped_reviewed: int = 0
    skipped_korean: int = 0
    skipped_stored: int = 0
    calls: int = 0
    failed_calls: int = 0
    glossary_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    glossary: tuple[GlossaryEntry, ...] = ()
    glossary_record: TranslationGlossaryRecord | None = None
    records: list[FactTranslationRecord] = field(default_factory=list)
    #: The fact keys in the order they were sent.
    order: list[str] = field(default_factory=list)

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
_ENDS_IN_HANGUL = re.compile(r"[가-힣][\s.!?)\]…]*$")


def is_korean(text: str) -> bool:
    """Whether a source sentence is already written in Korean.

    Korean with more Hangul than Latin letters, or a sentence that ends in
    Hangul and keeps at least a quarter as many Hangul letters as Latin
    ones: a Korean protocol names its instruments in English ("안정화를
    위해서 Seahorse XFe/XF Analyzer 를 켜서 예열합니다"), and such a sentence
    is Korean already -- translating it added a step label the check then
    refused (measured 2026-10-06, glycolysis).
    """

    stripped = text.strip()
    hangul = len(_HANGUL.findall(stripped))
    latin = len(_LATIN.findall(stripped))
    if hangul == 0:
        return False
    if hangul >= latin:
        return True
    return hangul >= 4 and 4 * hangul >= latin and _ENDS_IN_HANGUL.search(stripped) is not None


def protocol_context(fixture: CuratedProtocolFixture) -> ProtocolContext:
    protocol = fixture.draft.protocol
    try:
        purpose = ProtocolKnowledgeView.from_fixture(fixture).purpose.text
    except Exception:  # noqa: BLE001 - a fixture without a purpose has none
        purpose = ""
    steps = []
    for index, step in enumerate(fixture.steps):
        texts = [
            fact.text for fact in fixture.facts_for_step(index)
            if fact.kind in TRANSLATED_KINDS and fact.kind != "prerequisite"
        ]
        steps.append((step.source_label, " ".join(texts)))
    return ProtocolContext(
        title=fixture.title,
        purpose=purpose,
        materials=tuple(item.name_source_text for item in protocol.materials),
        equipment=tuple(item.name_source_text for item in protocol.equipment),
        steps=tuple(steps),
    )


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
            units.append(TranslationUnit(
                key, fact.kind, fact.text, terms(fact.text),
                step_index=index, step_label=step.source_label,
            ))
    return tuple(units)


def generation_order(
    units: Sequence[TranslationUnit], priority_steps: Iterable[int] = (),
) -> list[TranslationUnit]:
    """Purpose and safety first, then the open sessions' steps, then step order."""

    first = {index for index in priority_steps if index is not None}

    def rank(item: tuple[int, TranslationUnit]) -> tuple[int, int, int]:
        order, unit = item
        if unit.kind == "purpose" or unit.kind in SAFETY_KINDS:
            return (0, unit.step_index if unit.step_index is not None else -1, order)
        if unit.step_index in first:
            return (1, unit.step_index, order)
        return (2, unit.step_index if unit.step_index is not None else -1, order)

    return [unit for _, unit in sorted(enumerate(units), key=rank)]


def _next_batch(ordered: Sequence[TranslationUnit]) -> list[TranslationUnit]:
    batch: list[TranslationUnit] = []
    size = 0
    for unit in ordered:
        length = len(unit.source_text)
        if batch and (
            len(batch) >= BATCH_MAX_ITEMS or size + length > BATCH_MAX_CHARACTERS
        ):
            break
        batch.append(unit)
        size += length
    return batch


def translation_revision_key(fixture: CuratedProtocolFixture) -> str:
    """The key translations of this revision are stored and looked up under.

    ``protocol_id/revision_id``: a catalog revision id alone ("pdf-1-analysis-1")
    is the same string for every protocol's first analysis.
    """

    return f"{fixture.protocol_id}/{fixture.revision_id}"


_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_SIBILANT_PLURAL = ("ches", "shes", "xes", "sses", "zes")


def _singular(word: str) -> str:
    lowered = word.casefold()
    if lowered.endswith(("'s", "\u2019s")):
        return lowered  # a possessive ("Ringer's"), not a plural
    if lowered.endswith(_SIBILANT_PLURAL):
        return lowered[:-2]
    if len(lowered) > 3 and lowered.endswith("s") and not lowered.endswith("ss"):
        return lowered[:-1]
    return lowered


def term_stem(term: str) -> str:
    """A term in lower case with each word in the singular: "Porapak tubes"
    and "Porapak tube" both become "porapak tube", and the stem is a prefix
    of either spelling, so a Korean sentence holding either one contains it."""

    return _WORD.sub(lambda match: _singular(match.group(0)), " ".join(term.split()))


def _looks_like_a_name(term: str) -> bool:
    return any(character.isupper() for character in term)


def _name_words(term: str) -> tuple[str, ...]:
    """The words of a term that carry a name: those with a capital letter
    ("Porapak" of "Porapak tubes", "Ringer's" of "Ringer's solution"), as
    stems. A Korean sentence keeps the name and may put the common noun in
    Korean ("Porapak 튜브"), so these are what the check looks for."""

    return tuple(dict.fromkeys(
        _singular(word) for word in _WORD.findall(term)
        if any(character.isupper() for character in word)
    ))


def _required_strings(term: str) -> tuple[str, ...]:
    return _name_words(term) or ((term_stem(term),) if term_stem(term) else ())


def required_terms_for(
    unit: TranslationUnit, glossary: Sequence[GlossaryEntry] = (),
) -> tuple[str, ...]:
    """What this sentence's Korean must keep in English, as stems.

    A vocabulary term the glossary knows follows the glossary. The entry
    that names the term exactly decides; failing that, an entry the term
    contains ("LB agar" in "LB agar plates") decides; an entry that merely
    contains the term ("agar" in "LB agar") says nothing about the term
    itself. A required term is looked for by its name words, so "Porapak
    tubes" is kept by "Porapak 튜브". A term the glossary does not know is
    required only when it is written like a name.
    """

    entries = [
        (term_stem(entry.source), entry) for entry in glossary if entry.source.strip()
    ]
    required: list[str] = []

    def add(strings: Sequence[str]) -> None:
        for string in strings:
            if string and string not in required:
                required.append(string)

    for term in unit.required_terms:
        stem = term_stem(term)
        if not stem:
            continue
        exact = [entry for source, entry in entries if source == stem]
        if exact:
            if any(entry.keep_english for entry in exact):
                add(_required_strings(
                    next(entry.source for entry in exact if entry.keep_english)))
            continue
        within = [entry for source, entry in entries if f" {source} " in f" {stem} "]
        if within:
            if any(entry.keep_english for entry in within):
                add(_required_strings(term))
            continue
        if _looks_like_a_name(term):
            add(_required_strings(term))
    return tuple(required)


def check_translation(
    unit: TranslationUnit, korean: str | None,
    glossary: Sequence[GlossaryEntry] = (),
) -> str:
    if not isinstance(korean, str) or not korean.strip():
        return "missing"
    issue = reader_translation_issue(
        unit.source_text, korean,
        required_terms=required_terms_for(unit, glossary),
        step_label=_label_only_in_the_korean(unit, korean),
    )
    return issue or "passed"


def _label_only_in_the_korean(unit: TranslationUnit, korean: str) -> str | None:
    """The step label to discount, when the Korean alone carries it.

    The style guide opens a step's Korean with "N단계: ". Where the source
    step's text begins with its own number ("1 Prepare 400 mL ...") the two
    balance and nothing is discounted; where it does not (protocols.io
    exports such as ANKOM: "Use a solvent resistant marker ...") the N would
    be counted as a quantity the source lacks -- measured 2026-10-06, every
    one of ANKOM's 67 steps was refused that way -- so the check is told
    the label and discounts it on the Korean side.
    """

    label = unit.step_label
    if unit.kind != "step" or not label:
        return None
    if not re.match(rf"\s*{re.escape(label)}\s*단계", korean):
        return None
    if re.match(rf"\s*{re.escape(label)}(?![0-9.])", unit.source_text):
        return None
    return label


def batch_context(
    context: ProtocolContext,
    glossary: Sequence[GlossaryEntry],
    batch: Sequence[TranslationUnit],
) -> dict[str, Any]:
    """The request context of one sentence call."""

    return {
        "title": context.title,
        "purpose_summary": context.purpose_summary(),
        "glossary": [
            {"source": entry.source, "korean": entry.korean,
             "keep_english": entry.keep_english}
            for entry in glossary
        ],
        "steps": context.neighbourhood(
            unit.step_index for unit in batch if unit.step_index is not None),
    }


def glossary_entries(record: TranslationGlossaryRecord | None) -> tuple[GlossaryEntry, ...]:
    if record is None:
        return ()
    try:
        payload = json.loads(record.entries_json)
    except ValueError:
        return ()
    return tuple(
        GlossaryEntry(str(item["source"]), str(item["korean"]), bool(item["keep_english"]))
        for item in payload if isinstance(item, dict)
        and {"source", "korean", "keep_english"} <= set(item)
    )


async def generate_revision_translations(
    fixture: CuratedProtocolFixture,
    translate: BatchTranslator,
    *,
    model: str,
    stored: Iterable[FactTranslationRecord] = (),
    make_glossary: GlossaryMaker | None = None,
    glossary: TranslationGlossaryRecord | None = None,
    priority_steps: Callable[[], Iterable[int]] = lambda: (),
    on_glossary: Callable[[TranslationGlossaryRecord], Any] | None = None,
    on_batch: Callable[[list[FactTranslationRecord]], Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GenerationReport:
    """Translate the sentences of one revision not yet translated or reviewed.

    Sentences with a reviewed translation, sentences already in Korean and
    sentences already stored for this revision and source text are skipped,
    so a second call for the same revision makes no model call. The glossary
    is made once (``make_glossary``) unless ``glossary`` holds it. Before
    each batch ``priority_steps`` says which steps open sessions are on;
    after it ``on_batch`` receives its rows. A batch whose call fails stores
    nothing, so a later run can try again.
    """

    now = clock or (lambda: datetime.now(timezone.utc))
    key = translation_revision_key(fixture)
    report = GenerationReport(revision_id=key)
    held = {
        (item.fact_key, item.source_sha256) for item in stored
        if item.revision_id == key
        and item.language == TARGET_LANGUAGE
    }
    reviewed = set(getattr(fixture, "localizations", None) or ())
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
    if not pending:
        return report
    context = protocol_context(fixture)
    entries = glossary_entries(glossary)
    if glossary is None and make_glossary is not None:
        report.glossary_calls += 1
        try:
            made = await make_glossary(context)
        except Exception as exc:  # noqa: BLE001 - sentences go on without one
            log.warning(
                "revision_translation glossary_failed revision=%s error=%s",
                fixture.revision_id, type(exc).__name__)
        else:
            entries = made.entries
            report.prompt_tokens += made.prompt_tokens
            report.completion_tokens += made.completion_tokens
            glossary = TranslationGlossaryRecord(
                revision_id=key,
                language=TARGET_LANGUAGE,
                entries_json=json.dumps(
                    [entry.__dict__ for entry in entries], ensure_ascii=False),
                model=model,
                model_version=made.model_version or model,
                created_at=now().isoformat(),
            )
            report.glossary_record = glossary
            if on_glossary is not None:
                await _maybe_await(on_glossary(glossary))
    report.glossary = entries
    while pending:
        ordered = generation_order(pending, priority_steps())
        batch = _next_batch(ordered)
        sent = {unit.fact_key for unit in batch}
        pending = [unit for unit in pending if unit.fact_key not in sent]
        report.order.extend(unit.fact_key for unit in batch)
        report.calls += 1
        try:
            result = await translate(batch, batch_context(context, entries, batch))
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
        rows = []
        for unit in batch:
            korean = result.translations.get(unit.fact_key)
            korean = " ".join(korean.split()) if isinstance(korean, str) else ""
            rows.append(FactTranslationRecord(
                revision_id=key,
                fact_key=unit.fact_key,
                language=TARGET_LANGUAGE,
                source_sha256=unit.source_sha256,
                translated_text=korean,
                status="machine",
                check_result=check_translation(unit, korean, entries),
                model=model,
                model_version=result.model_version or model,
                created_at=created,
            ))
        report.records.extend(rows)
        if on_batch is not None:
            await _maybe_await(on_batch(rows))
    # Counts only: the sentences and the readings stay out of logs.
    log.info(
        "revision_translation revision=%s units=%d calls=%d failed_calls=%d "
        "glossary_calls=%d passed=%d refused=%d prompt_tokens=%d "
        "completion_tokens=%d",
        fixture.revision_id, report.units, report.calls, report.failed_calls,
        report.glossary_calls, report.passed, len(report.refused),
        report.prompt_tokens, report.completion_tokens,
    )
    return report


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value) or isinstance(value, asyncio.Future):
        await value


def stored_localizations(
    fixture: CuratedProtocolFixture,
    records: Iterable[FactTranslationRecord],
    glossary: Sequence[GlossaryEntry] = (),
) -> tuple[dict[str, str], dict[str, str]]:
    """(reviewed, machine) Korean by fact key, from rows that still hold.

    A row is used only when it belongs to this revision (stored under
    ``translation_revision_key``), was made from the sentence the revision
    holds now, and passes the check again here -- with the revision's
    glossary, the same way it was checked when it was made.
    """

    units = {unit.fact_key: unit for unit in translation_units(fixture)}
    key = translation_revision_key(fixture)
    reviewed: dict[str, str] = {}
    machine: dict[str, str] = {}
    for record in records:
        unit = units.get(record.fact_key)
        if (
            unit is None
            or record.revision_id != key
            or record.language != TARGET_LANGUAGE
            or record.source_sha256 != unit.source_sha256
            or record.check_result != "passed"
            or check_translation(unit, record.translated_text, glossary) != "passed"
        ):
            continue
        target = reviewed if record.status == "reviewed" else machine
        target[record.fact_key] = record.translated_text
    return reviewed, machine


def with_stored_translations(
    fixture: CuratedProtocolFixture,
    records: Iterable[FactTranslationRecord],
    glossary: Sequence[GlossaryEntry] = (),
) -> CuratedProtocolFixture:
    """The fixture carrying the stored translations that may be shown.

    Adds to what the fixture already carries, so it also takes one batch
    at a time while a revision is being translated.
    """

    reviewed, machine = stored_localizations(fixture, records, glossary)
    if not reviewed and not machine:
        return fixture
    sidecar = dict(getattr(fixture, "localizations", None) or {})
    for key, text in reviewed.items():
        sidecar.setdefault(key, text)
    merged = dict(getattr(fixture, "machine_localizations", None) or {})
    merged.update(machine)
    return replace(
        fixture,
        localizations=sidecar or fixture.localizations,
        machine_localizations={
            key: text for key, text in merged.items() if key not in sidecar
        } or None,
    )


def _json_schema(name: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": {
                "type": "object", "additionalProperties": False,
                "properties": properties, "required": required,
            },
        },
    }


def _usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def openai_glossary_maker(
    client_factory: Callable[[], Any], model: str, *,
    timeout: float = BATCH_TIMEOUT_SECONDS,
) -> GlossaryMaker:
    """One structured call reading the whole protocol, on the reader's model."""

    async def make(context: ProtocolContext) -> GlossaryResult:
        client = client_factory()
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": GLOSSARY_PROMPT},
                    {"role": "user", "content": json.dumps(
                        {"title": context.title, "protocol": context.whole_text()},
                        ensure_ascii=False,
                    )},
                ],
                response_format=_json_schema(
                    "protocol_translation_glossary_v1",
                    {"entries": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "source": {"type": "string"},
                            "korean": {"type": "string"},
                            "keep_english": {"type": "boolean"},
                        },
                        "required": ["source", "korean", "keep_english"],
                    }}},
                    ["entries"],
                ),
                temperature=0,
            ),
            timeout=timeout,
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        items = payload.get("entries") if isinstance(payload, dict) else None
        entries = []
        seen: set[str] = set()
        for item in items if isinstance(items, list) else ():
            if not (
                isinstance(item, dict)
                and isinstance(item.get("source"), str)
                and isinstance(item.get("korean"), str)
                and isinstance(item.get("keep_english"), bool)
            ):
                continue
            key = " ".join(item["source"].split()).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append(GlossaryEntry(
                " ".join(item["source"].split()),
                " ".join(item["korean"].split()),
                item["keep_english"],
            ))
        prompt_tokens, completion_tokens = _usage(response)
        return GlossaryResult(
            entries=tuple(entries[:GLOSSARY_MAX_ENTRIES]),
            model_version=str(getattr(response, "model", "") or model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    return make


def openai_batch_translator(
    client_factory: Callable[[], Any], model: str, *,
    timeout: float = BATCH_TIMEOUT_SECONDS,
) -> BatchTranslator:
    """One structured chat call per batch, on the reader translation's model."""

    async def translate(
        batch: Sequence[TranslationUnit], context: dict[str, Any],
    ) -> BatchResult:
        client = client_factory()
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": REVISION_TRANSLATION_PROMPT},
                    {"role": "user", "content": json.dumps(
                        {
                            **context,
                            "items": [
                                {
                                    "id": unit.fact_key,
                                    "kind": unit.kind,
                                    "step_label": unit.step_label,
                                    "source_text": unit.source_text,
                                }
                                for unit in batch
                            ],
                        },
                        ensure_ascii=False,
                    )},
                ],
                response_format=_json_schema(
                    "protocol_revision_translation_v2",
                    {"items": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "korean": {"type": "string"},
                        },
                        "required": ["id", "korean"],
                    }}},
                    ["items"],
                ),
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
        prompt_tokens, completion_tokens = _usage(response)
        return BatchResult(
            translations=translations,
            model_version=str(getattr(response, "model", "") or model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    return translate
