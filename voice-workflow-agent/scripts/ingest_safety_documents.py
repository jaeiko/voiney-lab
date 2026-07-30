#!/usr/bin/env python3
"""Ingest a human-reviewed normalized safety-document JSON manifest."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from voice_workflow_agent.document_store import ingest_manifest_file  # noqa: E402
from voice_workflow_agent.safety_documents import ManifestValidationError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()
    try:
        summary = ingest_manifest_file(args.manifest, args.db)
    except (ManifestValidationError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"ingestion failed: {exc}", file=sys.stderr)
        return 1
    print(f"ingested documents={summary['documents']} sections={summary['sections']} aliases={summary['aliases']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
