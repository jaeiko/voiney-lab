"""Deterministic, fail-closed retrieval from the approved document catalog."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .document_store import CATALOG_SCHEMA_VERSION


_GENERAL_STOP_TERMS = frozenset({
    "the", "and", "for", "with", "this", "that", "what", "when", "step",
    "current", "please", "tell", "about", "같은", "있어", "할때", "할", "때",
    "현재", "단계", "주의사항", "주의", "알려줘", "어떻게", "관련", "자료",
})


def _rank_terms(value: str) -> frozenset[str]:
    normalized = _normalized(value).replace("μ", "µ")
    return frozenset(
        token
        for token in re.findall(r"[가-힣]{2,}|[a-z0-9µ°][a-z0-9µ°./_-]+", normalized)
        if token not in _GENERAL_STOP_TERMS
    )


def _chunk_id(row: sqlite3.Row) -> str:
    identity = "\x1f".join(str(row[name] or "") for name in (
        "document_id", "version", "language", "section_code",
        "page_start", "page_end", "source_checksum",
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def retrieve_approved_lab_documents(
    query: str,
    db_path: str | Path,
    *,
    filters: dict[str, Any] | None = None,
    top_k: int = 5,
    now: datetime | None = None,
    minimum_score: float = 1.0,
) -> dict[str, Any]:
    """Rank approved lab sections read-only without broadening approval policy."""

    filters = {} if filters is None else filters
    allowed_filters = {
        "approval_status", "protocol_id", "document_id", "language",
        "authority_tier", "lab_scope", "facility_id",
        "exclude_non_operational",
    }
    if (
        not isinstance(query, str) or not query.strip()
        or not isinstance(db_path, (str, Path))
        or not isinstance(filters, dict)
        or not set(filters).issubset(allowed_filters)
        or filters.get("approval_status", "approved") != "approved"
        or not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20
        or not isinstance(minimum_score, (int, float)) or isinstance(minimum_score, bool)
        or minimum_score < 0
    ):
        return {"status": "invalid_arguments", "answerable": False, "matches": []}
    path = Path(db_path)
    if not path.is_file():
        return {"status": "error", "answerable": False, "matches": []}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    query_terms = _rank_terms(query)
    if not query_terms:
        return {"status": "not_found", "answerable": False, "matches": []}
    try:
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            metadata = connection.execute(
                "SELECT schema_version FROM catalog_metadata"
            ).fetchall()
            if len(metadata) != 1 or metadata[0][0] != CATALOG_SCHEMA_VERSION:
                return {"status": "error", "answerable": False, "matches": []}
            rows = connection.execute(
                """
                SELECT d.*, s.section_code, s.section_title, s.page_start,
                       s.page_end, s.content, s.topic, s.keywords
                FROM documents AS d
                JOIN sections AS s ON s.document_row_id = d.id
                WHERE d.approval_status = 'approved' AND d.active = 1
                  AND d.usage_scope != 'test_only'
                  AND d.source_authority != 'test_fixture'
                  AND d.translation_status IN ('original', 'human_reviewed')
                ORDER BY d.document_family_id, d.document_id, d.version,
                         d.language, s.page_start, s.section_code
                """
            ).fetchall()
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        return {"status": "error", "answerable": False, "matches": []}

    eligible: list[sqlite3.Row] = []
    rejections: list[dict[str, str]] = []
    for row in rows:
        if _is_stale(row, current):
            continue
        if filters.get("document_id") and row["document_id"] != filters["document_id"]:
            continue
        if filters.get("language") and row["language"] != filters["language"]:
            continue
        if filters.get("authority_tier") and row["source_authority"] != filters["authority_tier"]:
            continue
        if filters.get("lab_scope") and row["usage_scope"] != filters["lab_scope"]:
            continue
        facility = filters.get("facility_id")
        if facility and row["facility_id"] not in (None, facility):
            continue
        if filters.get("exclude_non_operational") is True:
            if (
                row["usage_scope"] == "demo"
                or str(row["source_uri"] or "").casefold().startswith(
                    "demo:"
                )
            ):
                rejections.append({
                    "document_id": str(row["document_id"]),
                    "section": str(row["section_code"]),
                    "reason": "non_operational_or_demo",
                })
                continue
        # The v2 catalog stores global approved references. A protocol filter
        # narrows the query context but never converts a global document into
        # active-protocol authority.
        eligible.append(row)

    family_versions: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for row in eligible:
        family_versions.setdefault(
            (row["document_family_id"], row["language"]), set()
        ).add((row["canonical_source_id"], row["canonical_version"]))
    if any(len(versions) > 1 for versions in family_versions.values()):
        return {
            "status": "conflicting_documents",
            "answerable": False,
            "matches": [],
        }

    ranked: list[tuple[float, sqlite3.Row]] = []
    for row in eligible:
        aliases = " ".join(str(value or "") for value in (
            row["title"], row["manufacturer"], row["product_name"],
            row["product_code"], row["section_title"], row["content"],
            row["keywords"], row["cas_numbers"],
        ))
        terms = _rank_terms(aliases)
        overlap = query_terms.intersection(terms)
        if not overlap:
            continue
        phrase_bonus = 1.0 if _normalized(row["content"]) in _normalized(query) else 0.0
        score = float(len(overlap)) + phrase_bonus
        if score >= minimum_score:
            ranked.append((score, row))
    ranked.sort(key=lambda item: (
        -item[0], item[1]["document_id"], item[1]["version"],
        item[1]["language"], item[1]["page_start"], item[1]["section_code"],
    ))
    matches = []
    seen_hashes: set[str] = set()
    for score, row in ranked:
        content_hash = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        matches.append({
            "chunk_id": _chunk_id(row),
            "document_id": row["document_id"],
            "document_sha256": row["source_checksum"],
            "document_title": row["title"],
            "document_version": row["version"],
            "document_type": row["document_type"],
            "authority_tier": row["source_authority"],
            "approval_status": row["approval_status"],
            "lab_scope": row["usage_scope"],
            "language": row["language"],
            "section": row["section_code"],
            "section_title": row["section_title"],
            "page_number": row["page_start"],
            "page_end": row["page_end"],
            "original_text": row["content"],
            "source_uri": row["source_uri"],
            "score": score,
            # The catalog fields that name the approved SQLite row a match
            # came from (document, version, section), kept beside the ranking.
            "version": row["version"],
            "source_checksum": row["source_checksum"],
            "section_code": row["section_code"],
        })
        if len(matches) >= top_k:
            break
    return {
        "status": (
            "success" if matches else
            "no_admissible_evidence" if rejections else
            "not_found"
        ),
        "answerable": bool(matches),
        "matches": matches,
        "retrieval": {"backend": "sqlite", "query_terms": len(query_terms)},
        "rejections": rejections[:20],
    }


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


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
