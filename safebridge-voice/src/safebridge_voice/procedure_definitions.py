"""Fail-closed immutable ProcedureDefinition loading."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProcedureDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class SourceReference:
    section_reference: str
    page_start: int
    page_end: int


@dataclass(frozen=True)
class ProcedureStep:
    step_id: str
    order: int
    title: str
    instruction: str
    completion_mode: str
    source: SourceReference
    timer: dict[str, Any] | None = None
    observation_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProcedureDefinition:
    schema_version: int
    procedure_id: str
    title: str
    version: str
    facility_id: str
    language: str
    approval_status: str
    usage_scope: str
    active: bool
    document_id: str
    document_version: str
    document_language: str
    document_source: SourceReference
    steps: tuple[ProcedureStep, ...]


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProcedureDefinitionError(f"{field} must be a non-empty string")
    return value.strip()


def _positive(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProcedureDefinitionError(f"{field} must be a positive integer")
    return value


def _source(raw: Any, field: str) -> SourceReference:
    if not isinstance(raw, dict) or set(raw) != {
        "section_reference", "page_start", "page_end"
    }:
        raise ProcedureDefinitionError(f"{field} is malformed")
    value = SourceReference(
        _text(raw["section_reference"], f"{field}.section_reference"),
        _positive(raw["page_start"], f"{field}.page_start"),
        _positive(raw["page_end"], f"{field}.page_end"),
    )
    if value.page_end < value.page_start:
        raise ProcedureDefinitionError(f"{field} page range is invalid")
    return value


def _timer(raw: Any, field: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"duration_seconds"}:
        raise ProcedureDefinitionError(f"{field} is malformed")
    return {"duration_seconds": _positive(raw["duration_seconds"], f"{field}.duration_seconds")}


def _observation(raw: Any, field: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"type", "required"}:
        raise ProcedureDefinitionError(f"{field} is malformed")
    if raw["type"] not in ("text", "number", "boolean") or not isinstance(raw["required"], bool):
        raise ProcedureDefinitionError(f"{field} is malformed")
    return {"type": raw["type"], "required": raw["required"]}


def load_procedure_definitions(
    definition_path: str | Path,
    approved_catalog_path: str | Path,
    *,
    facility_id: str | None,
    language: str,
    usage_scope: str,
    now: datetime | None = None,
) -> dict[str, ProcedureDefinition]:
    """Load only definitions compatible with trusted policy and approved documents."""
    try:
        payload = json.loads(Path(definition_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcedureDefinitionError("procedure catalog is unavailable") from exc
    if not isinstance(payload, dict) or set(payload) != {"procedures"} or not isinstance(payload["procedures"], list):
        raise ProcedureDefinitionError("procedure catalog is malformed")
    result: dict[str, ProcedureDefinition] = {}
    now = now or datetime.now(timezone.utc)
    for index, raw in enumerate(payload["procedures"]):
        p = f"procedures[{index}]"
        if not isinstance(raw, dict):
            raise ProcedureDefinitionError(f"{p} is malformed")
        required = {"schema_version","procedure_id","title","version","facility_id","language",
                    "approval_status","usage_scope","active","approved_document","steps"}
        if set(raw) != required:
            raise ProcedureDefinitionError(f"{p} has unexpected or missing fields")
        if raw["schema_version"] != 1:
            raise ProcedureDefinitionError(f"{p}.schema_version is unsupported")
        procedure_id = _text(raw["procedure_id"], f"{p}.procedure_id")
        if procedure_id in result:
            raise ProcedureDefinitionError("duplicate procedure_id")
        if raw["approval_status"] != "approved" or raw["active"] is not True:
            raise ProcedureDefinitionError("procedure is not active and approved")
        if raw["facility_id"] != facility_id:
            raise ProcedureDefinitionError("procedure facility mismatch")
        if raw["language"] != language:
            raise ProcedureDefinitionError("procedure language mismatch")
        if raw["usage_scope"] != usage_scope:
            raise ProcedureDefinitionError("procedure usage scope mismatch")
        if usage_scope == "operational" and raw["usage_scope"] in ("demo","test_only","reference_only"):
            raise ProcedureDefinitionError("non-operational procedure cannot be used operationally")
        doc = raw["approved_document"]
        if not isinstance(doc, dict) or set(doc) != {
            "document_id","version","language","section_reference","page_start","page_end"
        }:
            raise ProcedureDefinitionError("approved document reference is malformed")
        doc_source = _source({k: doc[k] for k in ("section_reference","page_start","page_end")},
                             f"{p}.approved_document")
        try:
            with sqlite3.connect(
                f"file:{Path(approved_catalog_path)}?mode=ro", uri=True
            ) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    """SELECT d.*,s.section_code,s.page_start AS section_page_start,
                       s.page_end AS section_page_end,s.content AS section_content
                       FROM documents d JOIN sections s ON s.document_row_id=d.id
                       WHERE d.document_id=? AND d.version=? AND d.language=?
                       AND s.section_code=?""",
                    (doc["document_id"],doc["version"],doc["language"],
                     doc_source.section_reference)
                ).fetchone()
        except sqlite3.Error as exc:
            raise ProcedureDefinitionError("approved catalog is unavailable") from exc
        if row is None or row["approval_status"] != "approved" or not row["active"]:
            raise ProcedureDefinitionError("approved source document is unavailable")
        if row["facility_id"] != facility_id or row["language"] != language or row["usage_scope"] != usage_scope:
            raise ProcedureDefinitionError("procedure metadata does not match approved source")
        if not (row["section_page_start"] <= doc_source.page_start <= doc_source.page_end <= row["section_page_end"]):
            raise ProcedureDefinitionError("approved document page range is invalid")
        try:
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError
            for date_field in ("effective_at","review_due_at"):
                value=row[date_field]
                if value:
                    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
                    if parsed.tzinfo is None or parsed.utcoffset() is None:
                        raise ValueError
                    if ((date_field=="effective_at" and parsed>now) or
                            (date_field=="review_due_at" and parsed<now)):
                        raise ProcedureDefinitionError(
                            "approved source document is not currently valid")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProcedureDefinitionError(
                "approved source document validity is malformed") from exc
        if not isinstance(raw["steps"], list) or not raw["steps"]:
            raise ProcedureDefinitionError("procedure steps must not be empty")
        steps=[]; ids=set()
        for s_index, step in enumerate(raw["steps"]):
            sp=f"{p}.steps[{s_index}]"
            allowed={"step_id","order","title","approved_spoken_instruction","completion_mode",
                     "source_reference","timer","observation_schema"}
            required_step=allowed-{"timer","observation_schema"}
            if not isinstance(step,dict) or not required_step.issubset(step) or not set(step).issubset(allowed):
                raise ProcedureDefinitionError(f"{sp} is malformed")
            step_id=_text(step["step_id"],f"{sp}.step_id")
            if step_id in ids: raise ProcedureDefinitionError("duplicate step_id")
            ids.add(step_id)
            order=_positive(step["order"],f"{sp}.order")
            source=_source(step["source_reference"],f"{sp}.source_reference")
            if (source.section_reference != doc_source.section_reference or
                source.page_start < doc_source.page_start or source.page_end > doc_source.page_end):
                raise ProcedureDefinitionError("step source lies outside approved document")
            if step["completion_mode"] != "explicit_confirmation":
                raise ProcedureDefinitionError("unsupported completion mode")
            instruction=_text(
                step["approved_spoken_instruction"],
                f"{sp}.approved_spoken_instruction")
            if instruction not in row["section_content"]:
                raise ProcedureDefinitionError(
                    "approved spoken instruction is absent from approved source")
            steps.append(ProcedureStep(
                step_id,order,_text(step["title"],f"{sp}.title"),
                instruction,
                step["completion_mode"],source,_timer(step.get("timer"),f"{sp}.timer"),
                _observation(step.get("observation_schema"),f"{sp}.observation_schema")))
        if [step.order for step in steps] != list(range(1,len(steps)+1)):
            raise ProcedureDefinitionError("step order must be contiguous and one-based")
        result[procedure_id]=ProcedureDefinition(
            1,procedure_id,_text(raw["title"],f"{p}.title"),_text(raw["version"],f"{p}.version"),
            raw["facility_id"],raw["language"],raw["approval_status"],raw["usage_scope"],True,
            _text(doc["document_id"],"document_id"),_text(doc["version"],"document version"),
            _text(doc["language"],"document language"),doc_source,tuple(steps))
    return result
