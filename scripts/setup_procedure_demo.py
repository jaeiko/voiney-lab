#!/usr/bin/env python3
"""Create fresh, temporary SQLite databases for the fictional Procedure demo."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT_ROOT/"src"))

from voice_workflow_agent.document_store import ingest_manifest_file  # noqa: E402
from voice_workflow_agent.procedure_definitions import load_procedure_definitions  # noqa: E402
from voice_workflow_agent.procedure_store import ProcedureStore  # noqa: E402


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",required=True,type=Path)
    args=parser.parse_args()
    output=args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    catalog=output/"approved_catalog.sqlite"
    store_path=output/"procedure_sessions.sqlite"
    if catalog.exists() or store_path.exists():
        parser.error("output directory already contains demo SQLite files")
    fixture=PROJECT_ROOT/"data"/"procedure_demo"
    ingest_manifest_file(fixture/"approved_document.ko.json",catalog)
    definitions=load_procedure_definitions(
        fixture/"procedures.ko.json",catalog,facility_id="DEMO-FACILITY",
        language="ko",usage_scope="test_only")
    if "fictional-wet-lab-workflow-demo-ko" not in definitions:
        raise RuntimeError("fictional demo procedure validation failed")
    store=ProcedureStore(store_path)
    store.close()
    print(f"created fictional non-operational demo databases in {output}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
