#!/usr/bin/env python3
"""Create a fresh SQLite catalog for the fictional Moss retrieval demo."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from safebridge_voice.document_store import ingest_manifest_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    catalog = output / "moss_demo_catalog.sqlite"
    if catalog.exists():
        parser.error("output directory already contains moss_demo_catalog.sqlite")
    manifest = PROJECT_ROOT / "data" / "moss_demo" / "approved_documents.ko.json"
    summary = ingest_manifest_file(manifest, catalog)
    print(
        f"created fictional Moss demo catalog: {catalog} "
        f"documents={summary['documents']} sections={summary['sections']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
