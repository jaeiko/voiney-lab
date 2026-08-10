#!/usr/bin/env python3
"""Validate an approved-document manifest or audit/query a catalog read-only."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from voice_workflow_agent.retrieval import retrieve_approved_lab_documents  # noqa: E402
from voice_workflow_agent.safety_documents import (  # noqa: E402
    ManifestValidationError,
    validate_manifest,
)


def _validate_manifest(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = validate_manifest(payload)
    except (OSError, json.JSONDecodeError, ManifestValidationError) as exc:
        print(f"manifest invalid: {exc}", file=sys.stderr)
        return 1
    print(f"manifest valid: documents={len(manifest.documents)}")
    for item in manifest.documents:
        print(
            "document "
            f"id={item['document_id']} title={item['title']!r} "
            f"version={item['version']} checksum={item['source_checksum']} "
            f"status={item['approval_status']} active={str(item['active']).lower()} "
            f"scope={item['usage_scope']} language={item['language']}"
        )
    return 0


def _audit_catalog(
    path: Path,
    *,
    scope: str,
    facility_id: str | None,
    query: str | None,
) -> int:
    if not path.is_file():
        print("catalog unavailable: expected an existing regular file", file=sys.stderr)
        return 1
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT document_id,title,version,source_checksum,approval_status,
                       active,language,usage_scope,facility_id
                FROM documents
                WHERE usage_scope=?
                ORDER BY document_id,version,language
                """,
                (scope,),
            ).fetchall()
            section_count = connection.execute(
                """
                SELECT COUNT(*) FROM sections AS s
                JOIN documents AS d ON d.id=s.document_row_id
                WHERE d.usage_scope=? AND d.approval_status='approved'
                  AND d.active=1
                """,
                (scope,),
            ).fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        print(f"catalog invalid: {exc}", file=sys.stderr)
        return 1
    print(f"catalog healthy: scope={scope} documents={len(rows)} active_sections={section_count}")
    for row in rows:
        print(
            "document "
            f"id={row['document_id']} title={row['title']!r} "
            f"version={row['version']} checksum={row['source_checksum']} "
            f"status={row['approval_status']} active={bool(row['active'])} "
            f"language={row['language']}"
        )
    if query:
        result = retrieve_approved_lab_documents(
            query,
            path,
            filters={
                "approval_status": "approved",
                "lab_scope": scope,
                **({"facility_id": facility_id} if facility_id else {}),
            },
        )
        print(
            f"query status={result['status']} answerable={result['answerable']} "
            f"matches={len(result['matches'])}"
        )
        for match in result["matches"]:
            print(
                "match "
                f"document_id={match['document_id']} version={match['version']} "
                f"section={match['section_code']} page={match['page_number']} "
                f"chunk_id={match['chunk_id']} score={match['score']:.3f}"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--scope")
    parser.add_argument("--facility-id")
    parser.add_argument("--query")
    args = parser.parse_args()
    if (args.manifest is None) == (args.db is None):
        parser.error("select exactly one of --manifest or --db")
    if args.manifest is not None:
        if args.scope or args.facility_id or args.query:
            parser.error("catalog options require --db")
        return _validate_manifest(args.manifest)
    if not args.scope:
        parser.error("--scope is required with --db")
    return _audit_catalog(
        args.db,
        scope=args.scope,
        facility_id=args.facility_id,
        query=args.query,
    )


if __name__ == "__main__":
    raise SystemExit(main())
