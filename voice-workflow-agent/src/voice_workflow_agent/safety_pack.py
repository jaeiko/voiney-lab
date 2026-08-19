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

from voice_workflow_agent.document_store import CATALOG_SCHEMA_VERSION, connect
from voice_workflow_agent.experiment_protocol import (
    Equipment,
    ExperimentProtocol,
    Material,
    ProtocolSection,
    ProtocolSourceStep,
    ProtocolSubAction,
)

log = logging.getLogger("voice_workflow_agent.safety_pack")

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

    def public_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_label": self.step_label,
            "warnings": list(self.warnings),
            "applicable_documents": [d.public_dict() for d in self.applicable_documents],
            "ppe_requirements": list(self.ppe_requirements),
            "handling_precautions": list(self.handling_precautions),
            "citation_label": self.citation_label,
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

    # Build grounded step text for conservative matching
    instruction = getattr(step, "instruction_source_text", "") or getattr(step, "instruction", "")
    sub_instructions = [
        getattr(sub, "instruction_source_text", "")
        for sub in getattr(step, "sub_actions", ()) or ()
        if getattr(sub, "instruction_source_text", None)
    ]
    step_text = f"{instruction} {' '.join(sub_instructions)} {' '.join(step_pdf_warnings)}".casefold()

    matching_docs: list[SafetyDocumentRef] = []

    if safety_pack.coverage_status not in ("unavailable", "disabled") and safety_pack.total_document_count > 0:
        # Match SDS by material name / topic appearing in step text
        for sds in safety_pack.sds_documents:
            title_lower = sds.title.casefold()
            topic_lower = (sds.topic or "").casefold()
            summary_lower = (sds.summary_text or "").casefold()
            # Match grounded chemical terms
            if any(term in step_text for term in (title_lower, topic_lower)) or any(
                chem in step_text for chem in ("ambic", "dtt", "iaa", "trypsin", "formic", "acetonitrile", "solvent", "buffer")
            ):
                matching_docs.append(sds)

        # Match Equipment manuals
        for eq in safety_pack.equipment_documents:
            title_lower = eq.title.casefold()
            topic_lower = (eq.topic or "").casefold()
            if any(term in step_text for term in (title_lower, topic_lower)) or any(
                term in step_text for term in ("centrifuge", "speedvac", "vortex", "incubator", "원심분리기", "인큐베이터", "기계", "설비")
            ):
                matching_docs.append(eq)

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

    return StepSafetyGuidance(
        step_id=step_id,
        step_label=step_label,
        warnings=tuple(step_pdf_warnings),
        applicable_documents=tuple(unique_docs),
        ppe_requirements=tuple(ppe_reqs),
        handling_precautions=tuple(handling),
        citation_label=" · ".join(citations),
    )


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
        if usage_scope == "operational":
            # In operational mode, never use demo records as authority
            return unavailable_safety_pack(
                protocol_id=protocol_id,
                protocol_revision=protocol_revision,
                facility_id=facility_id,
                status="unavailable",
            )

        # In demo/test scope, fall back to demo safety manual fixture in data/
        demo_json_path = Path(__file__).resolve().parents[2] / "data" / "approved_safety_manual.demo.json"
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

    try:
        conn = connect(catalog_path)
        try:
            scope_filter = "d.usage_scope != 'test_only'" if usage_scope != "test_only" else "1=1"
            rows = conn.execute(
                f"""
                SELECT d.id, d.document_id, d.document_type, d.title, d.version, d.language,
                       d.facility_id, d.manufacturer, d.product_name, d.product_code,
                       d.cas_numbers, d.usage_scope, d.source_uri, d.source_checksum,
                       s.section_code, s.section_title, s.page_start, s.content, s.topic, s.keywords
                FROM documents AS d
                LEFT JOIN sections AS s ON s.document_row_id = d.id
                WHERE d.approval_status = 'approved' AND d.active = 1 AND {scope_filter}
                ORDER BY d.document_type, d.document_id, s.page_start
                """
            ).fetchall()

            for row in rows:
                doc_type = _clean_str(row["document_type"])
                doc_id = _clean_str(row["document_id"])
                doc_facility = row["facility_id"]
                doc_scope = _clean_str(row["usage_scope"])
                doc_title = _clean_str(row["title"])
                is_demo = doc_scope in ("demo", "test_only") or "fictional" in doc_title.casefold()

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
                        summary_text=_clean_str(row["content"])[:240] if row["content"] else None,
                        is_demo=is_demo,
                    )
                    sop_docs.append(ref)
                    if row["topic"]:
                        topics_found.add(_clean_str(row["topic"]))
                    identities.add(f"{doc_id}:{row['version']}")

                elif doc_type == "supplier_sds":
                    prod_name = _clean_str(row["product_name"]).casefold()
                    prod_code = _clean_str(row["product_code"]).casefold()
                    cas_list = []
                    if row["cas_numbers"]:
                        try:
                            cas_list = [c.casefold() for c in json.loads(row["cas_numbers"])]
                        except Exception:
                            pass

                    matches_material = (
                        (prod_name and any(m in prod_name or prod_name in m for m in all_materials_lower))
                        or (prod_code and any(prod_code == m for m in all_materials_lower))
                        or any(c in all_materials_lower for c in cas_list)
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
                            summary_text=_clean_str(row["content"])[:240] if row["content"] else None,
                            is_demo=is_demo,
                        )
                        sds_docs.append(ref)
                        identities.add(f"{doc_id}:{row['version']}")

                elif doc_type == "equipment_manual":
                    prod_name = _clean_str(row["product_name"]).casefold()
                    prod_code = _clean_str(row["product_code"]).casefold()
                    matches_eq = (
                        (prod_name and any(e in prod_name or prod_name in e for e in all_equipment_lower))
                        or (prod_code and any(prod_code == e for e in all_equipment_lower))
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
                            summary_text=_clean_str(row["content"])[:240] if row["content"] else None,
                            is_demo=is_demo,
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
