#!/usr/bin/env python3
"""Sanitized Candidate A external-research configuration/live diagnostic."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true",
        help="perform exactly one bounded xAI Responses web-search request",
    )
    parser.add_argument(
        "--query-profile",
        choices=("ambic", "hplc-water"),
        default="ambic",
        help="select one predefined, non-secret Candidate A research diagnostic",
    )
    args = parser.parse_args()
    queries = {
        "ambic": "ammonium bicarbonate AMBIC definition in-gel digestion proteomics",
        "hplc-water": "HPLC-grade water definition analytical chemistry impurities",
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
        "citation_domains": result.get("admitted_domains", []),
        "citation_count": len(result.get("matches", [])),
        "admitted": result.get("status") == "success",
        "provider_request_id": result.get("provider_request_id"),
    })
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "success" else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
