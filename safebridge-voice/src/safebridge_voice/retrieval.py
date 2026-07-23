"""Deterministic, fail-closed retrieval from the approved document catalog."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .document_store import CATALOG_SCHEMA_VERSION, connect


TOPICS = {
    "first_aid": (("supplier_sds", "4"),),
    "fire": (("supplier_sds", "5"),),
    "spill": (("facility_sop", None), ("supplier_sds", "6")),
    "handling_storage": (("supplier_sds", "7"), ("facility_sop", None)),
    "exposure_ppe": (("supplier_sds", "8"), ("facility_sop", None)),
    "disposal": (("facility_sop", None), ("supplier_sds", "13")),
    "equipment_operation": (("facility_sop", None), ("equipment_manual", None)),
}
RUNTIME_SCOPES = {"operational", "demo", "reference_only"}
PRODUCT_DOCUMENT_TYPES = {"supplier_sds", "equipment_manual"}


def _result(status: str) -> dict[str, Any]:
    return {"status": status, "answerable": False, "matches": []}


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _mentions(query: str, value: str) -> bool:
    """Match a complete normalized identifier or phrase, never a substring."""
    value = _normalized(value)
    return bool(value) and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", query) is not None


def _section_number(code: str) -> str | None:
    match = re.search(r"(?:^|\D)(\d{1,2})(?:\D|$)", code)
    return str(int(match.group(1))) if match else None


def _cas_set(row: sqlite3.Row) -> set[str]:
    return {_normalized(value) for value in json.loads(row["cas_numbers"])}


def _same_product(left: sqlite3.Row, right: sqlite3.Row) -> bool:
    """Relate records only through strong, complete product identifiers."""
    if left["product_code"] and right["product_code"]:
        return _normalized(left["product_code"]) == _normalized(right["product_code"])
    left_cas, right_cas = _cas_set(left), _cas_set(right)
    if left_cas and right_cas and left_cas.intersection(right_cas):
        return True
    return bool(
        left["manufacturer"] and left["product_name"] and
        right["manufacturer"] and right["product_name"] and
        _normalized(left["manufacturer"]) == _normalized(right["manufacturer"]) and
        _normalized(left["product_name"]) == _normalized(right["product_name"])
    )


def _is_stale(row: sqlite3.Row, now: datetime) -> bool:
    if row["approval_status"] == "superseded" or not row["active"]:
        return True
    due = row["review_due_at"]
    if not due:
        return False
    due_at = datetime.fromisoformat(due.replace("Z", "+00:00"))
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at < now


def _canonical_identity(row: sqlite3.Row) -> tuple[str, str]:
    return row["canonical_source_id"], row["canonical_version"]


def _facility_eligible(row: sqlite3.Row, facility_id: str | None) -> bool:
    if row["document_type"] != "facility_sop":
        return True
    if facility_id is None:
        return row["facility_id"] is None
    return row["facility_id"] in (None, facility_id)


def _eligible_sections(
    connection: sqlite3.Connection,
    document: sqlite3.Row,
    topic: str,
) -> list[sqlite3.Row]:
    expected = next((code for kind, code in TOPICS[topic]
                     if kind == document["document_type"]), None)
    sections = connection.execute(
        "SELECT * FROM sections WHERE document_row_id=?", (document["id"],)
    ).fetchall()
    if document["document_type"] == "supplier_sds":
        return [section for section in sections
                if expected is not None and _section_number(section["section_code"]) == expected]
    return [section for section in sections if section["topic"] == topic]


def _status_and_usable(rows: list[sqlite3.Row], now: datetime) -> tuple[str | None, list[sqlite3.Row]]:
    if not rows:
        return "not_found", []
    usable = [row for row in rows if row["approval_status"] == "approved" and not _is_stale(row, now)]
    if usable:
        return None, usable
    if any(row["approval_status"] in {"superseded", "approved"} and _is_stale(row, now) for row in rows):
        return "stale_document", []
    if all(row["approval_status"] in {"draft", "rejected"} for row in rows):
        return "unapproved_document", []
    return "stale_document", []


def search_safety_documents(
    query: str,
    language: str,
    db_path: str | Path,
    *,
    usage_scope: str | None = None,
    facility_id: str | None = None,
    topic: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return verbatim source sections for one explicit runtime usage scope."""
    if (not isinstance(query, str) or not query.strip() or
            not isinstance(language, str) or not language.strip() or
            not isinstance(db_path, (str, Path)) or
            not isinstance(usage_scope, str) or usage_scope not in RUNTIME_SCOPES):
        return _result("invalid_arguments")
    if facility_id is not None and (not isinstance(facility_id, str) or not facility_id.strip()):
        return _result("invalid_arguments")
    if not isinstance(topic, str) or topic not in TOPICS:
        return _result("invalid_arguments")
    if now is not None and not isinstance(now, datetime):
        return _result("invalid_arguments")
    selected_topic = topic
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    try:
        path = Path(db_path)
        if not path.is_file():
            return _result("error")
        connection = connect(db_path)
        metadata = connection.execute(
            "SELECT schema_version FROM catalog_metadata"
        ).fetchall()
        if len(metadata) != 1 or metadata[0]["schema_version"] != CATALOG_SCHEMA_VERSION:
            connection.close()
            return _result("error")
        docs = connection.execute("SELECT * FROM documents ORDER BY document_id, version, language").fetchall()
        aliases = connection.execute(
            "SELECT * FROM aliases WHERE approved=1 ORDER BY generic, alias, document_row_id"
        ).fetchall()
    except (sqlite3.Error, OSError):
        return _result("error")
    try:
        scoped = [row for row in docs if row["usage_scope"] == usage_scope and
                  row["usage_scope"] != "test_only" and row["source_authority"] != "test_fixture"]
        # Facility SOPs never establish product identity. They enter only through the
        # shared facility-filtered path below, after a product-specific anchor resolves.
        product_pool = [row for row in scoped if row["document_type"] in PRODUCT_DOCUMENT_TYPES]
        q = _normalized(query)
        requested_language = _normalized(language)
        stages: list[list[sqlite3.Row]] = [
            [row for row in product_pool if row["product_code"] and _mentions(q, row["product_code"])],
            [row for row in product_pool if row["manufacturer"] and row["product_name"] and
             _mentions(q, row["manufacturer"]) and _mentions(q, row["product_name"])],
            [row for row in product_pool if any(_mentions(q, cas) for cas in _cas_set(row))],
            [row for row in product_pool if row["product_name"] and
             _normalized(row["language"]) == requested_language and
             _mentions(q, row["product_name"])],
        ]
        by_id = {row["id"]: row for row in product_pool}
        for generic in (0, 1):
            ids = {alias["document_row_id"] for alias in aliases
                   if alias["generic"] == generic and
                   _normalized(alias["language"]) == requested_language and
                   _mentions(q, alias["alias"])}
            stages.append([by_id[row_id] for row_id in sorted(ids) if row_id in by_id])
        product_docs = next((stage for stage in stages if stage), [])
        if not product_docs:
            return _result("not_found")
        anchors: list[sqlite3.Row] = []
        for candidate in product_docs:
            if not any(_same_product(candidate, anchor) for anchor in anchors):
                anchors.append(candidate)
        if len(anchors) != 1:
            return _result("ambiguous_product")
        anchor = anchors[0]

        routed_types = {kind for kind, _ in TOPICS[selected_topic]}
        product_related = [row for row in product_pool if row["document_type"] in routed_types and
                           _same_product(anchor, row)]
        sop_related: list[sqlite3.Row] = []
        if "facility_sop" in routed_types:
            for row in scoped:
                if row["document_type"] != "facility_sop":
                    continue
                if not _facility_eligible(row, facility_id):
                    continue
                product_specific = any((row["product_code"], row["manufacturer"], row["product_name"], _cas_set(row)))
                if not product_specific or _same_product(anchor, row):
                    sop_related.append(row)
        routed = product_related + sop_related
        sections_by_document = {
            row["id"]: _eligible_sections(connection, row, selected_topic) for row in routed
        }
        matching = [row for row in routed if sections_by_document[row["id"]]]
        status, usable = _status_and_usable(matching, current_time)
        if status:
            return _result(status)

        families: dict[tuple[str, str, str | None], set[tuple[str, str]]] = {}
        for row in usable:
            key = (row["document_family_id"], row["usage_scope"], row["facility_id"])
            families.setdefault(key, set()).add(_canonical_identity(row))
        if any(len(versions) > 1 for versions in families.values()):
            return _result("conflicting_documents")

        language_rows = [row for row in usable if _normalized(row["language"]) == requested_language]
        if not language_rows:
            return _result("translation_unverified" if requested_language == "vi" else "not_found")
        verified = [row for row in language_rows if row["translation_status"] in {"original", "human_reviewed"}]
        if not verified:
            return _result("translation_unverified")
        # A translation may only represent the same active canonical version as its source family.
        for row in verified:
            if row["translation_status"] == "original":
                continue
            family_versions = {_canonical_identity(item) for item in usable
                               if item["document_family_id"] == row["document_family_id"]}
            if family_versions != {_canonical_identity(row)}:
                return _result("stale_document")

        route_order = {pair: index for index, pair in enumerate(TOPICS[selected_topic])}
        rows = []
        for doc in verified:
            expected = next(code for kind, code in TOPICS[selected_topic] if kind == doc["document_type"])
            for section in sections_by_document[doc["id"]]:
                rows.append((route_order[(doc["document_type"], expected)], doc, section))
        if not rows:
            return _result("not_found")
        rows.sort(key=lambda item: (item[0], item[1]["document_family_id"],
                                    item[1]["document_id"], item[1]["version"],
                                    item[2]["page_start"], item[2]["section_code"]))
        matches = []
        for _, doc, section in rows[:3]:
            matches.append({
                "document_id": doc["document_id"], "document_family_id": doc["document_family_id"],
                "canonical_source_id": doc["canonical_source_id"], "canonical_version": doc["canonical_version"],
                "document_type": doc["document_type"], "title": doc["title"], "issuer": doc["issuer"],
                "manufacturer": doc["manufacturer"], "product_name": doc["product_name"],
                "product_code": doc["product_code"], "cas_numbers": json.loads(doc["cas_numbers"]),
                "version": doc["version"], "language": doc["language"],
                "source_authority": doc["source_authority"], "approval_status": doc["approval_status"],
                "usage_scope": doc["usage_scope"], "section_code": section["section_code"],
                "section_title": section["section_title"], "page_start": section["page_start"],
                "page_end": section["page_end"], "content": section["content"],
                "translation_status": doc["translation_status"],
                "translation_of_document_id": doc["translation_of_document_id"],
                "source_uri": doc["source_uri"], "source_path": doc["source_path"],
                "source_checksum": doc["source_checksum"],
            })
        return {"status": "success", "answerable": True, "matches": matches}
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        return _result("error")
    finally:
        connection.close()
