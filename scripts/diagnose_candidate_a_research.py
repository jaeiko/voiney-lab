#!/usr/bin/env python3
"""Sanitized Candidate A external-research configuration/live diagnostic."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from voice_workflow_agent.external_references import (
    ExternalReferenceSettings,
    XaiAuthoritativeWebSearch,
)


def configured_api_url() -> str:
    return os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/") + "/"


def configured_api_key() -> str:
    value = os.environ.get("XAI_API_KEY", "").strip()
    if not value:
        raise RuntimeError("XAI_API_KEY is not configured")
    return value


async def main() -> int:
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true",
        help="perform exactly one bounded xAI Responses web-search request",
    )
    parser.add_argument(
        "--query-profile",
        choices=("ambic", "hplc-water", "solution-a-role", "step-safety"),
        default="ambic",
        help="select one predefined, non-secret Candidate A research diagnostic",
    )
    args = parser.parse_args()
    queries = {
        "ambic": "ammonium bicarbonate AMBIC definition in-gel digestion proteomics",
        "hplc-water": "HPLC-grade water definition analytical chemistry impurities",
        "solution-a-role": (
            "acetonitrile and ammonium bicarbonate purpose during in-gel "
            "protein-band washing and destaining"
        ),
        "step-safety": (
            "acetonitrile ammonium bicarbonate gel-band solution handling "
            "laboratory safety PPE ventilation official guidance"
        ),
    }
    settings = ExternalReferenceSettings.from_environment()
    output: dict[str, object] = {
        "live": args.live,
        "query_profile": args.query_profile,
        "configuration": settings.public_capability(),
        "credential_present": bool(os.environ.get("XAI_API_KEY")),
        "provider_request_count": 0,
    }
    if not args.live:
        output["status"] = "offline_configuration_valid"
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    if not settings.enabled:
        output["status"] = "disabled"
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 2
    started = time.monotonic()
    client = AsyncOpenAI(
        base_url=configured_api_url(), api_key=configured_api_key(),
        max_retries=0,
    )
    output["provider_request_count"] = 1
    result = await XaiAuthoritativeWebSearch(client, settings).search(
        queries[args.query_profile],
        language="ko",
    )
    output.update({
        "status": result.get("status"),
        "model": settings.model,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "phase": result.get("phase"),
        "exception_class": result.get("exception_class"),
        "http_status": result.get("http_status"),
        "attempt_count": result.get("attempt_count", 1),
        "tool_usage_count": result.get("tool_usage_count", 0),
        "streaming": result.get("streaming", False),
        "provider_event_count": result.get("event_count", 0),
        "provider_tool_event_count": result.get("tool_event_count", 0),
        "first_event_ms": result.get("first_event_ms"),
        "first_text_ms": result.get("first_text_ms"),
        "tool_started_ms": result.get("tool_started_ms"),
        "tool_ended_ms": result.get("tool_ended_ms"),
        "citation_domains": result.get("admitted_domains", []),
        "citation_count": len(result.get("matches", [])),
        "admitted": result.get("status") == "success",
        "provider_request_id": result.get("provider_request_id"),
    })
    pending = [
        task for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and not task.done()
    ]
    output["pending_task_count"] = len(pending)
    output["pending_task_types"] = sorted({
        getattr(task.get_coro(), "__qualname__", type(task.get_coro()).__name__)
        for task in pending
    })
    print(json.dumps(output, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result.get("status") == "success" else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
