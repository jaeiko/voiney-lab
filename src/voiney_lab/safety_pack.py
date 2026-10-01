"""Protocol-bound safety SOP, SDS, and equipment document context.

Binds approved facility safety documents strictly to the active experiment protocol
session without turning external web search into safety authority. Safety documents
are an optional supplementary layer and fail closed without blocking protocol guidance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voiney_lab.document_store import CATALOG_SCHEMA_VERSION, connect
from voiney_lab.experiment_protocol import (
    Equipment,
    ExperimentProtocol,
    Material,
    ProtocolSection,
    ProtocolSourceStep,
    ProtocolSubAction,
)

log = logging.getLogger("voiney_lab.safety_pack")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class SafetyPackResolutionError(RuntimeError):
    """Raised when safety pack resolution fails critically."""


@dataclass(frozen=True)
class SafetyDocumentRef:
    """Immutable reference to one approved safety document or section."""

    document_id: str
    document_type: str  # "facility_sop", "supplier_sds", "equipment_manual"
    title: str
    version: str
    language: str
    facility_id: str | None
    topic: str | None
    section_code: str | None
    page_number: int | None
    source_uri: str | None
    summary_text: str | None
    is_demo: bool = False
    #: (language, excerpt) of this section in a human-reviewed translation the
    #: catalog links to this document; the only translation the card may show.
    reviewed_translations: tuple[tuple[str, str], ...] = ()
    #: What the document is about as the catalog names it, casefolded: an
    #: SDS's product name, CAS numbers and approved non-generic aliases; an
    #: equipment manual's equipment name, model and approved non-generic
    #: aliases. A step gets the document only when its own text names one.
    name_terms: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_type": self.document_type,
            "title": self.title,
            "version": self.version,
            "language": self.language,
            "facility_id": self.facility_id,
            "topic": self.topic,
            "section_code": self.section_code,
            "page_number": self.page_number,
            "source_uri": self.source_uri,
            "summary_text": self.summary_text,
            "is_demo": self.is_demo,
        }


@dataclass(frozen=True)
class StepSafetyGuidance:
    """Safety context specifically mapped to one executed protocol step."""

    step_id: str
    step_label: str
    warnings: tuple[str, ...]  # PDF step's own warnings preserved
    applicable_documents: tuple[SafetyDocumentRef, ...]
    ppe_requirements: tuple[str, ...]
    handling_precautions: tuple[str, ...]
    citation_label: str
    display_bullets: tuple[str, ...] = ()
    localized_display_bullets: tuple[str, ...] = ()
    localization_language: str = "ko"
    localization_status: str = "localized"

    def public_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_label": self.step_label,
            "warnings": list(self.warnings),
            "applicable_documents": [d.public_dict() for d in self.applicable_documents],
            "ppe_requirements": list(self.ppe_requirements),
            "handling_precautions": list(self.handling_precautions),
            "citation_label": self.citation_label,
            "display_bullets": list(self.display_bullets),
            "localized_display_bullets": list(self.localized_display_bullets),
            "localization_language": self.localization_language,
            "localization_status": self.localization_status,
        }


@dataclass(frozen=True)
class ProtocolSafetySubjects:
    """Extracted safety-relevant subjects from an ExperimentProtocol."""

    protocol_id: str
    materials: tuple[str, ...]
    equipment: tuple[str, ...]
    prerequisites: tuple[str, ...]
    protocol_warnings: tuple[str, ...]
    grounded_terms: tuple[str, ...]


@dataclass(frozen=True)
class SafetyPack:
    """Immutable session-bound safety document pack resolved for an experiment protocol."""

    protocol_id: str
    protocol_revision: str
    facility_id: str | None
    resolved_at: str
    sop_documents: tuple[SafetyDocumentRef, ...]
    sds_documents: tuple[SafetyDocumentRef, ...]
    equipment_documents: tuple[SafetyDocumentRef, ...]
    applicable_topics: tuple[str, ...]
    coverage_status: str  # "available", "partial", "unavailable", "disabled", "demo_only"
    missing_coverage: tuple[str, ...]
    source_identities: tuple[str, ...]

    @property
    def total_document_count(self) -> int:
        seen = set()
        for doc in self.sop_documents + self.sds_documents + self.equipment_documents:
            seen.add(doc.document_id)
        return len(seen)

    def guidance_for_step(
        self,
        step: ProtocolSourceStep | Any,
        step_index: int = 0,
    ) -> StepSafetyGuidance:
        """Derive safety guidance and citations for a specific step without modifying protocol prose."""
        return resolve_step_safety_context(self, step, step_index)

    def public_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "protocol_revision": self.protocol_revision,
            "facility_id": self.facility_id,
            "resolved_at": self.resolved_at,
            "sop_documents": [d.public_dict() for d in self.sop_documents],
            "sds_documents": [d.public_dict() for d in self.sds_documents],
            "equipment_documents": [d.public_dict() for d in self.equipment_documents],
            "applicable_topics": list(self.applicable_topics),
            "coverage_status": self.coverage_status,
            "missing_coverage": list(self.missing_coverage),
            "source_identities": list(self.source_identities),
            "total_document_count": self.total_document_count,
        }


def _clean_str(val: Any) -> str:
    return str(val or "").strip()


_SENTENCE_BREAK = re.compile(r"(?<=[.!?。])\s+")
_EXCERPT_CHARS = 240
#: Marks that the source text goes on past the excerpt.
EXCERPT_OMISSION = " …"
#: Translation states whose text is neither the source nor reviewed by a person.
UNREVIEWED_TRANSLATION_STATUSES = ("machine_unreviewed", "unavailable")
#: Label the safety card puts before a document's own text, naming its kind.
_DOCUMENT_LABELS = {
    "facility_sop": "안전 SOP",
    "supplier_sds": "물질 SDS",
    "equipment_manual": "장비 매뉴얼",
}
REVIEWED_TRANSLATION_LABEL = "검토된 한국어 번역"


def _collapse_whitespace(text: Any) -> str:
    return " ".join(str(text or "").split())


def _match_text(text: Any) -> str:
    return _collapse_whitespace(text).casefold()


_HANGUL = "\uac00-\ud7a3"


def mentions_name(term: str, text: str) -> bool:
    """Whether ``text`` names ``term`` as a whole name, not inside a longer word.

    Both arguments are already :func:`_match_text`. ``dtt`` is not found in
    ``wdtt`` and ``75-05-8`` not in ``175-05-8``; a Korean name may still take
    a particle (``아세토니트릴을``). A one-character term never matches.
    """
    if len(term) < 2:
        return False
    tail = "" if "\uac00" <= term[-1] <= "\ud7a3" else r"(?![0-9a-z])"
    return re.search(rf"(?<![0-9a-z{_HANGUL}]){re.escape(term)}{tail}", text) is not None


def _step_text(step: Any) -> str:
    """A step's own words that may name a substance or hazard: instruction, sub-actions, warnings."""
    instruction = getattr(step, "instruction_source_text", "") or getattr(step, "instruction", "")
    sub_instructions = [
        getattr(sub, "instruction_source_text", "")
        for sub in getattr(step, "sub_actions", ()) or ()
        if getattr(sub, "instruction_source_text", None)
    ]
    warnings = []
    for w in getattr(step, "warnings", ()) or ():
        text = getattr(w, "source_text", None) or getattr(w, "text", None) or (w if isinstance(w, str) else "")
        if text:
            warnings.append(text)
    return _match_text(f"{instruction} {' '.join(sub_instructions)} {' '.join(warnings)}")


def source_excerpt(text: Any, limit: int = _EXCERPT_CHARS) -> str:
    """The leading whole sentences of ``text``, within ``limit`` where a sentence allows.

    Never cuts inside a sentence, so a cut cannot drop the "not" or "unless"
    that ends one; a first sentence longer than ``limit`` is kept whole. When
    sentences are left out, :data:`EXCERPT_OMISSION` says so.
    """
    collapsed = _collapse_whitespace(text)
    if len(collapsed) <= limit:
        return collapsed
    sentences = _SENTENCE_BREAK.split(collapsed)
    kept = sentences[0]
    for sentence in sentences[1:]:
        if len(kept) + 1 + len(sentence) > limit:
            break
        kept = f"{kept} {sentence}"
    return kept if kept == collapsed else f"{kept}{EXCERPT_OMISSION}"


#: Catalog usage scopes whose documents are demo material, never facility guidance.
DEMO_USAGE_SCOPES = ("demo", "test_only")


def is_demo_document(usage_scope: Any, title: Any) -> bool:
    """Whether one catalog document is demo material rather than approved guidance."""
    return _clean_str(usage_scope) in DEMO_USAGE_SCOPES or "fictional" in _clean_str(title).casefold()


def count_catalog_documents(catalog_path: str | Path, usage_scope: str) -> tuple[int, int]:
    """Return (demo documents, approved active documents in ``usage_scope``).

    For a launcher that refuses a catalog before it starts the server. Every
    document row counts toward the first number whatever its approval state,
    by the rule :func:`is_demo_document` applies. The second is what the
    server's approved-catalog check needs to be at least one. The catalog is
    opened read-only; a missing or unreadable one raises ``sqlite3.Error``.
    """
    uri = f"{Path(catalog_path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute("SELECT usage_scope, title, approval_status, active FROM documents").fetchall()
    finally:
        conn.close()
    demo = sum(1 for scope, title, _, _ in rows if is_demo_document(scope, title))
    in_scope = sum(
        1 for scope, _, status, active in rows if scope == usage_scope and status == "approved" and active == 1
    )
    return demo, in_scope


def collect_protocol_safety_subjects(protocol: ExperimentProtocol | Any) -> ProtocolSafetySubjects:
    """Extract materials, equipment, prerequisites, and warnings from the actual ExperimentProtocol domain model."""
    protocol_id = (
        getattr(protocol, "protocol_id", None)
        or getattr(getattr(protocol, "metadata", None), "protocol_id", None)
        or "unknown_protocol"
    )

    materials: list[str] = []
    for m in getattr(protocol, "materials", ()) or ():
        name = getattr(m, "name_source_text", "") or getattr(m, "name", "")
        if name and name.strip():
            materials.append(name.strip())

    equipment: list[str] = []
    for e in getattr(protocol, "equipment", ()) or ():
        name = getattr(e, "name_source_text", "") or getattr(e, "name", "")
        if name and name.strip():
            equipment.append(name.strip())

    prerequisites: list[str] = []
    for p in getattr(protocol, "before_start", ()) or ():
        text = getattr(p, "source_text", "")
        if text and text.strip():
            prerequisites.append(text.strip())

    protocol_warnings: list[str] = []
    grounded_terms: set[str] = set()

    for m_name in materials:
        grounded_terms.add(m_name.casefold())
    for e_name in equipment:
        grounded_terms.add(e_name.casefold())

    for section in getattr(protocol, "sections", ()) or ():
        for step in getattr(section, "steps", ()) or ():
            # Step warnings
            for w in getattr(step, "warnings", ()) or ():
                w_text = getattr(w, "source_text", "") or (w if isinstance(w, str) else "")
                if w_text and w_text.strip():
                    protocol_warnings.append(w_text.strip())

            # Sub-action warnings
            for sub in getattr(step, "sub_actions", ()) or ():
                for w in getattr(sub, "warnings", ()) or ():
                    w_text = getattr(w, "source_text", "") or (w if isinstance(w, str) else "")
                    if w_text and w_text.strip():
                        protocol_warnings.append(w_text.strip())

    return ProtocolSafetySubjects(
        protocol_id=protocol_id,
        materials=tuple(materials),
        equipment=tuple(equipment),
        prerequisites=tuple(prerequisites),
        protocol_warnings=tuple(protocol_warnings),
        grounded_terms=tuple(sorted(grounded_terms)),
    )


def resolve_step_safety_context(
    safety_pack: SafetyPack,
    step: ProtocolSourceStep | Any,
    step_index: int = 0,
) -> StepSafetyGuidance:
    """Resolve step-specific safety guidance conservatively using explicit grounded terms."""
    step_id = getattr(step, "step_id", f"step-{step_index + 1}")
    step_label = getattr(step, "source_label", str(step_index + 1))

    # Extract step warnings (from protocol PDF)
    raw_warnings = getattr(step, "warnings", ()) or ()
    step_pdf_warnings: list[str] = []
    for w in raw_warnings:
        if hasattr(w, "source_text") and w.source_text:
            step_pdf_warnings.append(w.source_text)
        elif isinstance(w, str) and w.strip():
            step_pdf_warnings.append(w.strip())
        elif hasattr(w, "text") and w.text:
            step_pdf_warnings.append(w.text)

    # Extract source page number from evidence
    source_page = 1
    if hasattr(step, "evidence") and hasattr(step.evidence, "source_page_number"):
        source_page = step.evidence.source_page_number
    elif hasattr(step, "source_page"):
        source_page = step.source_page
    elif hasattr(step, "source_pages") and step.source_pages:
        source_page = step.source_pages[0]

    step_text = _step_text(step)

    matching_docs: list[SafetyDocumentRef] = []

    if safety_pack.coverage_status not in ("unavailable", "disabled") and safety_pack.total_document_count > 0:
        # An SDS or an equipment manual belongs to a step that names its own
        # substance or machine, and to no other: a word such as "buffer",
        # "solvent", "centrifuge" or "기계" names neither.
        for doc in safety_pack.sds_documents + safety_pack.equipment_documents:
            if any(mentions_name(term, step_text) for term in doc.name_terms):
                matching_docs.append(doc)

        # Match SOP by step hazard warnings / topics
        for sop in safety_pack.sop_documents:
            topic = (sop.topic or "").casefold()
            summary = (sop.summary_text or "").casefold()
            if "ppe" in topic or "보호구" in summary or "glove" in summary:
                if any(w in step_text for w in ("wear", "ppe", "glove", "mask", "보호구", "장갑", "마스크", "hood", "fume", "cut", "scalpel")):
                    matching_docs.append(sop)
            elif "spill" in topic or "leak" in topic or "누출" in summary:
                if any(w in step_text for w in ("spill", "hazard", "toxic", "leak", "유독", "누출", "주의", "solvent")):
                    matching_docs.append(sop)
            elif "general" in topic or "general" in sop.document_id.casefold():
                # Read off the name: no catalog field says a document applies
                # to every step, so a "general" SOP takes a card line on each.
                matching_docs.append(sop)

    # Deduplicate matching docs
    unique_docs: list[SafetyDocumentRef] = []
    seen_ids = set()
    for doc in matching_docs:
        if doc.document_id not in seen_ids:
            seen_ids.add(doc.document_id)
            unique_docs.append(doc)

    ppe_reqs: list[str] = []
    handling: list[str] = []
    citations = [f"실험 PDF p.{source_page}"]

    for doc in unique_docs:
        if doc.document_type == "facility_sop":
            citations.append(f"안전 SOP p.{doc.page_number or 1}")
        elif doc.document_type == "supplier_sds":
            citations.append(f"물질 SDS p.{doc.page_number or 1}")
        elif doc.document_type == "equipment_manual":
            citations.append(f"장비 매뉴얼 p.{doc.page_number or 1}")

        if doc.summary_text:
            if "ppe" in (doc.topic or "").casefold() or "보호구" in (doc.summary_text or "") or "glove" in (doc.summary_text or "").casefold():
                ppe_reqs.append(doc.summary_text)
            else:
                handling.append(doc.summary_text)

    # Every card line is a source's own words: the step's PDF warning, or a
    # document's excerpt under a label naming the document. The Korean card
    # adds a translation only where the catalog holds a human-reviewed one;
    # nothing is paraphrased, so the card never states what no source says.
    card_lines: dict[str, str] = {}  # source-language line -> Korean card line
    for w in step_pdf_warnings:
        text = _collapse_whitespace(w)
        if text:
            line = f"• 주의: {text}"
            card_lines.setdefault(line, line)
    for doc in unique_docs:
        if not doc.summary_text:
            continue
        label = _DOCUMENT_LABELS.get(doc.document_type, "안전 자료")
        line = f"• {label} · {doc.title}: {doc.summary_text}"
        translation = (
            None
            if doc.language.casefold().startswith("ko")
            else next(
                (text for lang, text in doc.reviewed_translations if lang.casefold().startswith("ko")),
                None,
            )
        )
        card_lines.setdefault(
            line, f"{line}\n  {REVIEWED_TRANSLATION_LABEL}: {translation}" if translation else line
        )
    bullets = list(card_lines)
    localized_bullets = tuple(list(card_lines.values())[:3])

    return StepSafetyGuidance(
        step_id=step_id,
        step_label=step_label,
        warnings=tuple(step_pdf_warnings),
        applicable_documents=tuple(unique_docs),
        ppe_requirements=tuple(ppe_reqs),
        handling_precautions=tuple(handling),
        citation_label=" · ".join(citations),
        display_bullets=tuple(bullets[:3]),
        localized_display_bullets=localized_bullets,
        localization_language="ko",
        # Says which list the card shows, not that every line is Korean: a
        # line with no reviewed translation stays in its source language.
        localization_status="localized" if localized_bullets else "fallback",
    )


def _cas_numbers(row: Any) -> list[str]:
    """The CAS numbers a catalog row lists; an unreadable list is logged and skipped."""
    if not row["cas_numbers"]:
        return []
    try:
        return [_clean_str(c) for c in json.loads(row["cas_numbers"]) if _clean_str(c)]
    except Exception as cas_exc:
        log.warning(
            "Ignoring unreadable CAS numbers on safety document %s: %s",
            _clean_str(row["document_id"]), type(cas_exc).__name__,
        )
        return []


def unavailable_safety_pack(
    protocol_id: str,
    protocol_revision: str = "1",
    facility_id: str | None = None,
    status: str = "unavailable",
    error_reason: str | None = None,
) -> SafetyPack:
    """Create a non-blocking unavailable/disabled SafetyPack."""
    return SafetyPack(
        protocol_id=protocol_id,
        protocol_revision=protocol_revision,
        facility_id=facility_id,
        resolved_at=datetime.now(timezone.utc).isoformat(),
        sop_documents=(),
        sds_documents=(),
        equipment_documents=(),
        applicable_topics=(),
        coverage_status=status,
        missing_coverage=("all_safety_documents",),
        source_identities=(),
    )


def resolve_safety_pack(
    protocol: ExperimentProtocol | Any,
    catalog_path: str | Path | None,
    facility_id: str | None = None,
    usage_scope: str = "demo",
    protocol_revision: str = "1",
) -> SafetyPack:
    """Conservatively resolve approved safety documents matching the structured protocol."""
    now_iso = datetime.now(timezone.utc).isoformat()
    subjects = collect_protocol_safety_subjects(protocol)
    protocol_id = subjects.protocol_id

    sop_docs: list[SafetyDocumentRef] = []
    sds_docs: list[SafetyDocumentRef] = []
    equipment_docs: list[SafetyDocumentRef] = []
    topics_found: set[str] = set()
    identities: set[str] = set()

    # If catalog path is None or file not found
    if catalog_path is None or not Path(catalog_path).is_file():
        if usage_scope not in DEMO_USAGE_SCOPES:
            # Only a demo or test runtime may show demo records; operational
            # and reference_only (the controlled pilot) show none instead.
            return unavailable_safety_pack(
                protocol_id=protocol_id,
                protocol_revision=protocol_revision,
                facility_id=facility_id,
                status="unavailable",
            )

        # In demo/test scope, fall back to demo safety manual fixture in data/fixtures/
        demo_json_path = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "approved_safety_manual.demo.json"
        if demo_json_path.is_file():
            try:
                demo_records = json.loads(demo_json_path.read_text(encoding="utf-8"))
                for rec in demo_records:
                    doc_id = rec.get("document_id", "")
                    translations = rec.get("translations", {})
                    ko = translations.get("ko", {})
                    ref = SafetyDocumentRef(
                        document_id=doc_id,
                        document_type=(
                            "facility_sop"
                            if "GENERAL" in doc_id or "PPE" in doc_id or "DISPOSAL" in doc_id or "STORAGE" in doc_id
                            else "equipment_manual"
                            if "MACHINE" in doc_id or "EQUIP" in doc_id
                            else "supplier_sds"
                        ),
                        title=ko.get("title", doc_id),
                        version="demo-1",
                        language="ko",
                        facility_id=facility_id,
                        topic=ko.get("section", "safety"),
                        section_code=ko.get("section", "01"),
                        page_number=1,
                        source_uri=None,
                        summary_text=ko.get("guidance", ""),
                        is_demo=True,
                    )
                    if ref.document_type == "facility_sop":
                        sop_docs.append(ref)
                    elif ref.document_type == "equipment_manual":
                        equipment_docs.append(ref)
                    else:
                        sds_docs.append(ref)
                    identities.add(f"{doc_id}:demo-1")
            except Exception as e:
                log.warning("Failed to load demo safety manual fallback: %s", e)

        return SafetyPack(
            protocol_id=protocol_id,
            protocol_revision=protocol_revision,
            facility_id=facility_id,
            resolved_at=now_iso,
            sop_documents=tuple(sop_docs),
            sds_documents=tuple(sds_docs),
            equipment_documents=tuple(equipment_docs),
            applicable_topics=tuple(sorted(topics_found)),
            coverage_status="demo_only" if (sop_docs or sds_docs or equipment_docs) else "unavailable",
            missing_coverage=() if (sop_docs or sds_docs or equipment_docs) else ("all_safety_documents",),
            source_identities=tuple(sorted(identities)),
        )

    all_materials_lower = {m.casefold() for m in subjects.materials}
    all_equipment_lower = {e.casefold() for e in subjects.equipment}
    step_texts = [
        _step_text(step)
        for section in getattr(protocol, "sections", ()) or ()
        for step in getattr(section, "steps", ()) or ()
    ]
    # Where the protocol may name a substance or a machine: its materials or
    # equipment list, and every step.
    material_texts = [_match_text(m) for m in subjects.materials] + step_texts
    equipment_texts = [_match_text(e) for e in subjects.equipment] + step_texts

    try:
        conn = connect(catalog_path)
        try:
            if usage_scope == "operational":
                # Demo and test documents never reach an operational pack.
                scope_filter = "d.usage_scope NOT IN ('demo', 'test_only')"
            elif usage_scope == "test_only":
                scope_filter = "1=1"
            else:
                scope_filter = "d.usage_scope != 'test_only'"
            rows = conn.execute(
                f"""
                SELECT d.id, d.document_id, d.document_type, d.title, d.version, d.language,
                       d.facility_id, d.manufacturer, d.product_name, d.product_code,
                       d.cas_numbers, d.usage_scope, d.source_uri, d.source_checksum,
                       d.translation_status, d.translation_of_document_id,
                       s.section_code, s.section_title, s.page_start, s.content, s.topic, s.keywords
                FROM documents AS d
                LEFT JOIN sections AS s ON s.document_row_id = d.id
                WHERE d.approval_status = 'approved' AND d.active = 1 AND {scope_filter}
                ORDER BY d.document_type, d.document_id, s.page_start
                """
            ).fetchall()

            # A human-reviewed translation is shown beside the section of its
            # original that has the same code, not as a document of its own.
            # Unreviewed translation text never reaches the card.
            originals = {
                _clean_str(row["document_id"]) for row in rows if not row["translation_of_document_id"]
            }
            reviewed: dict[tuple[str, str], list[tuple[str, str]]] = {}
            for row in rows:
                if (
                    _clean_str(row["translation_status"]) == "human_reviewed"
                    and _clean_str(row["translation_of_document_id"]) in originals
                    and row["content"]
                ):
                    reviewed.setdefault(
                        (_clean_str(row["translation_of_document_id"]), _clean_str(row["section_code"])), []
                    ).append((_clean_str(row["language"]), source_excerpt(row["content"])))

            try:
                alias_rows = conn.execute(
                    "SELECT document_row_id, alias FROM aliases WHERE approved = 1 AND generic = 0"
                ).fetchall()
            except sqlite3.Error:
                alias_rows = []
            aliases: dict[int, list[str]] = {}
            for alias_row in alias_rows:
                aliases.setdefault(alias_row["document_row_id"], []).append(_clean_str(alias_row["alias"]))
            # An SDS's substance names and an equipment manual's machine
            # names, its reviewed translations' included.
            name_terms: dict[str, set[str]] = {}
            cas_by_row: dict[int, list[str]] = {}
            for row in rows:
                status = _clean_str(row["translation_status"])
                doc_type = _clean_str(row["document_type"])
                if doc_type not in ("supplier_sds", "equipment_manual") or status in UNREVIEWED_TRANSLATION_STATUSES:
                    continue
                translation_of = _clean_str(row["translation_of_document_id"])
                owner = translation_of if status == "human_reviewed" and translation_of in originals else _clean_str(
                    row["document_id"]
                )
                if doc_type == "supplier_sds":
                    if row["id"] not in cas_by_row:
                        cas_by_row[row["id"]] = _cas_numbers(row)
                    names = [row["product_name"], *cas_by_row[row["id"]], *aliases.get(row["id"], ())]
                else:
                    # The machine's name, its model, and approved aliases.
                    names = [row["product_name"], row["product_code"], *aliases.get(row["id"], ())]
                name_terms.setdefault(owner, set()).update(
                    term for term in (_match_text(name) for name in names) if term
                )

            for row in rows:
                translation_status = _clean_str(row["translation_status"])
                if translation_status in UNREVIEWED_TRANSLATION_STATUSES or (
                    translation_status == "human_reviewed"
                    and _clean_str(row["translation_of_document_id"]) in originals
                ):
                    continue
                doc_type = _clean_str(row["document_type"])
                doc_id = _clean_str(row["document_id"])
                doc_facility = row["facility_id"]
                doc_scope = _clean_str(row["usage_scope"])
                doc_title = _clean_str(row["title"])
                is_demo = is_demo_document(doc_scope, doc_title)
                if is_demo and usage_scope == "operational":
                    # The scope filter above keeps demo scopes out; this also
                    # drops a document whose title marks it fictional, for
                    # facility SOPs as well as SDS and equipment manuals.
                    continue

                # Facility SOP filtering
                if doc_type == "facility_sop":
                    if facility_id and doc_facility and doc_facility != facility_id and doc_scope == "operational":
                        continue

                    ref = SafetyDocumentRef(
                        document_id=doc_id,
                        document_type=doc_type,
                        title=doc_title,
                        version=_clean_str(row["version"]),
                        language=_clean_str(row["language"]),
                        facility_id=doc_facility,
                        topic=_clean_str(row["topic"]) or None,
                        section_code=_clean_str(row["section_code"]) or None,
                        page_number=int(row["page_start"] or 1),
                        source_uri=_clean_str(row["source_uri"]) or None,
                        summary_text=source_excerpt(row["content"]) if row["content"] else None,
                        is_demo=is_demo,
                        reviewed_translations=tuple(reviewed.get((doc_id, _clean_str(row["section_code"])), ())),
                    )
                    sop_docs.append(ref)
                    if row["topic"]:
                        topics_found.add(_clean_str(row["topic"]))
                    identities.add(f"{doc_id}:{row['version']}")

                elif doc_type == "supplier_sds":
                    prod_name = _clean_str(row["product_name"]).casefold()
                    prod_code = _clean_str(row["product_code"]).casefold()
                    cas_list = [c.casefold() for c in cas_by_row.get(row["id"], ())]
                    terms = tuple(sorted(name_terms.get(doc_id, ())))

                    matches_material = (
                        (prod_name and any(m in prod_name or prod_name in m for m in all_materials_lower))
                        or (prod_code and any(prod_code == m for m in all_materials_lower))
                        or any(c in all_materials_lower for c in cas_list)
                        or any(mentions_name(t, text) for t in terms for text in material_texts)
                    )
                    if matches_material or (is_demo and usage_scope != "operational"):
                        ref = SafetyDocumentRef(
                            document_id=doc_id,
                            document_type=doc_type,
                            title=doc_title,
                            version=_clean_str(row["version"]),
                            language=_clean_str(row["language"]),
                            facility_id=None,
                            topic=_clean_str(row["topic"]) or "sds",
                            section_code=_clean_str(row["section_code"]) or None,
                            page_number=int(row["page_start"] or 1),
                            source_uri=_clean_str(row["source_uri"]) or None,
                            summary_text=source_excerpt(row["content"]) if row["content"] else None,
                            is_demo=is_demo,
                            reviewed_translations=tuple(reviewed.get((doc_id, _clean_str(row["section_code"])), ())),
                            name_terms=terms,
                        )
                        sds_docs.append(ref)
                        identities.add(f"{doc_id}:{row['version']}")

                elif doc_type == "equipment_manual":
                    prod_name = _clean_str(row["product_name"]).casefold()
                    prod_code = _clean_str(row["product_code"]).casefold()
                    terms = tuple(sorted(name_terms.get(doc_id, ())))
                    matches_eq = (
                        (prod_name and any(e in prod_name or prod_name in e for e in all_equipment_lower))
                        or (prod_code and any(prod_code == e for e in all_equipment_lower))
                        or any(mentions_name(t, text) for t in terms for text in equipment_texts)
                    )
                    if matches_eq or (is_demo and usage_scope != "operational"):
                        ref = SafetyDocumentRef(
                            document_id=doc_id,
                            document_type=doc_type,
                            title=doc_title,
                            version=_clean_str(row["version"]),
                            language=_clean_str(row["language"]),
                            facility_id=doc_facility,
                            topic=_clean_str(row["topic"]) or "equipment_operation",
                            section_code=_clean_str(row["section_code"]) or None,
                            page_number=int(row["page_start"] or 1),
                            source_uri=_clean_str(row["source_uri"]) or None,
                            summary_text=source_excerpt(row["content"]) if row["content"] else None,
                            is_demo=is_demo,
                            reviewed_translations=tuple(reviewed.get((doc_id, _clean_str(row["section_code"])), ())),
                            name_terms=terms,
                        )
                        equipment_docs.append(ref)
                        identities.add(f"{doc_id}:{row['version']}")

        finally:
            conn.close()
    except Exception as exc:
        log.warning("Failed to query approved safety documents: %s", exc)

    # Coverage assessment
    missing: list[str] = []
    if not sop_docs:
        missing.append("facility_sop")
    if all_materials_lower and not sds_docs:
        missing.append("supplier_sds")
    if all_equipment_lower and not equipment_docs:
        missing.append("equipment_manual")

    is_any_demo = any(d.is_demo for d in sop_docs + sds_docs + equipment_docs)
    coverage = "available" if not missing else "partial"
    if is_any_demo and usage_scope != "operational":
        coverage = "demo_only" if not missing else "partial"
    if not sop_docs and not sds_docs and not equipment_docs:
        coverage = "unavailable"

    return SafetyPack(
        protocol_id=protocol_id,
        protocol_revision=protocol_revision,
        facility_id=facility_id,
        resolved_at=now_iso,
        sop_documents=tuple(sop_docs),
        sds_documents=tuple(sds_docs),
        equipment_documents=tuple(equipment_docs),
        applicable_topics=tuple(sorted(topics_found)),
        coverage_status=coverage,
        missing_coverage=tuple(missing),
        source_identities=tuple(sorted(identities)),
    )
