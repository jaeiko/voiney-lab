"""SQLite catalog creation and atomic normalized-manifest ingestion."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .safety_documents import ValidatedManifest, validate_manifest


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, document_family_id TEXT NOT NULL,
 canonical_source_id TEXT NOT NULL, canonical_version TEXT NOT NULL,
 document_type TEXT NOT NULL, title TEXT NOT NULL, issuer TEXT NOT NULL,
 manufacturer TEXT, product_name TEXT, product_code TEXT, cas_numbers TEXT NOT NULL,
 version TEXT NOT NULL, language TEXT NOT NULL, facility_id TEXT,
 source_authority TEXT NOT NULL, approval_status TEXT NOT NULL, usage_scope TEXT NOT NULL,
 effective_at TEXT, review_due_at TEXT, source_path TEXT, source_uri TEXT,
 source_checksum TEXT NOT NULL, translation_status TEXT NOT NULL,
 translation_of_document_id TEXT, active INTEGER NOT NULL,
 UNIQUE(document_id, version, language)
);
CREATE TABLE IF NOT EXISTS sections (
 id INTEGER PRIMARY KEY, document_row_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 section_code TEXT NOT NULL, section_title TEXT NOT NULL, page_start INTEGER NOT NULL,
 page_end INTEGER NOT NULL, content TEXT NOT NULL, topic TEXT, keywords TEXT NOT NULL,
 UNIQUE(document_row_id, section_code)
);
CREATE TABLE IF NOT EXISTS aliases (
 id INTEGER PRIMARY KEY, document_row_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 alias TEXT NOT NULL COLLATE NOCASE, language TEXT NOT NULL, approved INTEGER NOT NULL,
 generic INTEGER NOT NULL, UNIQUE(document_row_id, alias, language)
);
CREATE INDEX IF NOT EXISTS idx_documents_product_code ON documents(product_code);
CREATE INDEX IF NOT EXISTS idx_documents_family ON documents(document_family_id);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA)


def ingest_manifest(payload: dict[str, Any] | ValidatedManifest, db_path: str | Path) -> dict[str, int]:
    """Validate first, then insert all documents in one SQLite transaction."""
    path = Path(db_path)
    existing_documents: list[sqlite3.Row] = []
    if path.exists():
        with connect(path) as existing_connection:
            existing_documents = existing_connection.execute(
                "SELECT document_id, document_family_id, canonical_source_id, "
                "canonical_version, translation_status FROM documents"
            ).fetchall()
    manifest = (payload if isinstance(payload, ValidatedManifest)
                else validate_manifest(payload, existing_documents))
    initialize_database(db_path)
    section_count = alias_count = 0
    with connect(db_path) as connection:
        try:
            connection.execute("BEGIN")
            for doc in manifest.documents:
                cursor = connection.execute(
                    """INSERT INTO documents (
                    document_id, document_family_id, canonical_source_id, canonical_version,
                    document_type, title, issuer, manufacturer,
                    product_name, product_code, cas_numbers, version, language, facility_id,
                    source_authority, approval_status, usage_scope, effective_at, review_due_at,
                    source_path, source_uri, source_checksum, translation_status,
                    translation_of_document_id, active
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(doc[k] for k in (
                        "document_id", "document_family_id", "canonical_source_id", "canonical_version",
                        "document_type", "title", "issuer", "manufacturer",
                        "product_name", "product_code"
                    )) + (json.dumps(doc["cas_numbers"], ensure_ascii=False),) + tuple(doc[k] for k in (
                        "version", "language", "facility_id", "source_authority", "approval_status", "usage_scope",
                        "effective_at", "review_due_at", "source_path", "source_uri", "source_checksum",
                        "translation_status", "translation_of_document_id", "active"
                    )),
                )
                row_id = cursor.lastrowid
                for section in doc["sections"]:
                    connection.execute(
                        "INSERT INTO sections (document_row_id, section_code, section_title, page_start, page_end, content, topic, keywords) VALUES (?,?,?,?,?,?,?,?)",
                        (row_id, section["section_code"], section["section_title"], section["page_start"], section["page_end"], section["content"], section["topic"], json.dumps(section["keywords"], ensure_ascii=False)),
                    )
                    section_count += 1
                for alias in doc["aliases"]:
                    connection.execute(
                        "INSERT INTO aliases (document_row_id, alias, language, approved, generic) VALUES (?,?,?,?,?)",
                        (row_id, alias["alias"], alias["language"], alias["approved"], alias["generic"]),
                    )
                    alias_count += 1
        except Exception:
            connection.rollback()
            raise
    return {"documents": len(manifest.documents), "sections": section_count, "aliases": alias_count}


def ingest_manifest_file(manifest_path: str | Path, db_path: str | Path) -> dict[str, int]:
    with Path(manifest_path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return ingest_manifest(payload, db_path)
