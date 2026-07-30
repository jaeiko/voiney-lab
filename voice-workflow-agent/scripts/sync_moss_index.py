#!/usr/bin/env python3
"""Sync approved Voice Workflow Agent catalog sections to an explicitly chosen Moss index."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from voice_workflow_agent.moss_retrieval import (  # noqa: E402
    MOSS_CAPABLE_SCOPES,
    catalog_sections_for_moss,
)


def _job_status_name(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value).rsplit(".", 1)[-1].casefold()


async def _wait_for_job(client: Any, job_id: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = await client.get_job_status(job_id)
        state = _job_status_name(status.status)
        if state == "completed":
            return
        if state == "failed":
            detail = getattr(status, "error", None) or "unknown error"
            raise RuntimeError(f"Moss index job failed: {detail}")
        await asyncio.sleep(1)
    raise TimeoutError(f"Moss index job did not finish within {timeout_seconds:g}s")


async def _sync(args: argparse.Namespace, documents: list[Any]) -> None:
    try:
        from moss import DocumentInfo, MossClient, MutationOptions
    except ImportError as exc:
        raise RuntimeError(
            "Moss SDK is not installed; run: python -m pip install -e '.[moss]'"
        ) from exc

    project_id = os.environ.get("MOSS_PROJECT_ID", "").strip()
    project_key = os.environ.get("MOSS_PROJECT_KEY", "").strip()
    if not project_id or not project_key:
        raise RuntimeError("MOSS_PROJECT_ID and MOSS_PROJECT_KEY are required")

    sdk_documents = [
        DocumentInfo(id=item.id, text=item.text, metadata=item.metadata)
        for item in documents
    ]
    client = MossClient(project_id, project_key)
    indexes = await client.list_indexes()
    existing = next(
        (item for item in indexes if getattr(item, "name", None) == args.index_name),
        None,
    )

    if existing is None:
        result = await client.create_index(
            args.index_name, sdk_documents, args.model_id
        )
        await _wait_for_job(client, result.job_id, args.wait_timeout)
        action = "created"
    else:
        current = await client.get_docs(args.index_name)
        unmanaged = [
            item.id
            for item in current
            if (
                not isinstance(getattr(item, "metadata", None), dict)
                or item.metadata.get("voice_workflow_agent_key") != item.id
                or item.metadata.get("usage_scope") != args.usage_scope
            )
        ]
        if unmanaged:
            raise RuntimeError(
                "existing index is not exclusively managed by Voice Workflow Agent for "
                f"usage scope '{args.usage_scope}'; choose a different index name"
            )
        existing_ids = {
            item.id for item in current if isinstance(getattr(item, "id", None), str)
        }
        desired_ids = {item.id for item in documents}
        upsert = await client.add_docs(
            args.index_name,
            sdk_documents,
            MutationOptions(upsert=True),
        )
        await _wait_for_job(client, upsert.job_id, args.wait_timeout)
        stale_ids = sorted(existing_ids - desired_ids)
        if stale_ids:
            deletion = await client.delete_docs(args.index_name, stale_ids)
            await _wait_for_job(client, deletion.job_id, args.wait_timeout)
        action = f"updated; removed={len(stale_ids)}"

    await client.load_index(args.index_name)
    print(
        f"Moss index {action}: name={args.index_name} "
        f"documents={len(documents)} model={args.model_id}"
    )


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument(
        "--usage-scope",
        required=True,
        choices=sorted(MOSS_CAPABLE_SCOPES),
    )
    parser.add_argument(
        "--index-name",
        default=os.environ.get("MOSS_INDEX_NAME", "").strip(),
    )
    parser.add_argument(
        "--model-id",
        default=os.environ.get("MOSS_MODEL_ID", "moss-minilm").strip(),
        choices=("moss-minilm", "moss-mediumlm"),
    )
    parser.add_argument("--wait-timeout", type=float, default=300.0)
    parser.add_argument(
        "--allow-sensitive-scope",
        action="store_true",
        help=(
            "Explicitly permit uploading operational-scope section text to Moss "
            "Cloud. Not required for demo or reference_only data."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and count eligible sections without importing Moss or uploading.",
    )
    args = parser.parse_args()

    if not args.index_name:
        parser.error("--index-name or MOSS_INDEX_NAME is required")
    if args.wait_timeout <= 0 or args.wait_timeout > 3600:
        parser.error("--wait-timeout must be between 0 and 3600 seconds")
    if args.usage_scope == "operational" and not args.allow_sensitive_scope:
        parser.error(
            "operational content requires --allow-sensitive-scope because index "
            "creation uploads section text to Moss Cloud"
        )

    try:
        documents = catalog_sections_for_moss(
            args.db.resolve(), args.usage_scope
        )
    except (OSError, ValueError) as exc:
        print(f"Moss export failed: {exc}", file=sys.stderr)
        return 1
    if not documents:
        print("Moss export failed: no eligible approved sections", file=sys.stderr)
        return 1
    if args.dry_run:
        print(
            f"Moss dry run: index={args.index_name} "
            f"scope={args.usage_scope} documents={len(documents)}"
        )
        return 0

    try:
        asyncio.run(_sync(args, documents))
    except Exception as exc:
        print(f"Moss sync failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
