#!/usr/bin/env python3
"""Run one content-free streaming latency probe for ANKOM claim chunk 3.

The source, model, response schema, compact-handle request, reasoning effort,
retry policy, and deadline are fixed to the established diagnostic baseline.
Production protocol analysis does not import or call this script.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from voice_workflow_agent.experiment_protocol_analysis import (
    build_protocol_analysis_chat_request,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    extraction_for_chunk,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_RESPONSE_SCHEMA,
    parse_chunk_claim_response,
    prepare_chunk_claim_request_context,
)
from voice_workflow_agent.protocol_provider_diagnostics import (
    ProtocolProviderStreamDiagnostic,
    run_protocol_provider_stream_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]
ANKOM_SOURCE = (
    ROOT
    / "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    / "5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)
MODEL = "grok-4.3"
REASONING_EFFORT = "none"
TIMEOUT_SECONDS = 119.0
EXPECTED_CORE_PAGES = tuple(range(25, 33))
EXPECTED_CONTEXT_PAGES = (24,)
EXPECTED_NUMBERED_ACTIONS = 19


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _classification(result: ProtocolProviderStreamDiagnostic) -> str:
    if result.timeout_phase == "before_first_output":
        return "provider_scheduling_or_ttft_bound"
    if result.timeout_phase == "after_first_output":
        return "structured_output_generation_bound"
    if result.failure_code == "provider_transport_error":
        return "client_or_transport_error"
    if result.validation_succeeded:
        return "provider_completed_and_canonical_validation_succeeded"
    if result.stream_completed:
        return "provider_completed_but_output_was_not_canonically_valid"
    return "provider_boundary_failure"


def _prepare_case() -> tuple[dict[str, object], dict[str, Any]]:
    if not ANKOM_SOURCE.is_file():
        raise RuntimeError("The fixed ANKOM diagnostic source is unavailable.")
    extraction = extract_protocol_pdf(ANKOM_SOURCE)
    limits = ChunkAnalysisLimits(max_retries=0)
    protocol_id = f"protocol-{extraction.sha256[:32]}"
    plan = plan_protocol_chunks(
        extraction,
        protocol_id,
        "pdf-1",
        limits=limits,
    )
    chunk = plan.chunks[3]
    if (
        chunk.ordinal != 3
        or chunk.core_page_refs != EXPECTED_CORE_PAGES
        or chunk.overlap_page_refs != EXPECTED_CONTEXT_PAGES
    ):
        raise RuntimeError("The fixed representative chunk identity changed.")
    scoped = extraction_for_chunk(extraction, chunk)
    request = prepare_chunk_claim_request_context(
        scoped,
        source_revision=chunk.candidate_revision_id,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        core_page_refs=chunk.core_page_refs,
        context_page_refs=chunk.overlap_page_refs,
    )
    input_json = request.input_json()
    non_streaming_request = build_protocol_analysis_chat_request(
        model=MODEL,
        reasoning_effort=REASONING_EFFORT,
        system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
        input_json=input_json,
        response_schema=CLAIM_RESPONSE_SCHEMA,
    )
    streaming_request = dict(non_streaming_request)
    streaming_request["stream"] = True
    streaming_request["stream_options"] = {"include_usage": True}
    case_metadata: dict[str, object] = {
        "source_sha256": extraction.sha256,
        "source_page_count": extraction.page_count,
        "chunk_ordinal": chunk.ordinal,
        "core_page_refs": list(chunk.core_page_refs),
        "context_page_refs": list(chunk.overlap_page_refs),
        "expected_numbered_actions": EXPECTED_NUMBERED_ACTIONS,
        "claim_input_bytes": len(input_json.encode("utf-8")),
        "logical_non_streaming_payload_bytes": len(
            _canonical_json(non_streaming_request).encode("utf-8")
        ),
        "logical_streaming_payload_bytes": len(
            _canonical_json(streaming_request).encode("utf-8")
        ),
        "segment_count": sum(len(page.evidence) for page in request.pages),
        "provider_handle_bytes": sum(
            len(item.handle.encode("utf-8"))
            for page in request.pages
            for item in page.evidence
        ),
    }
    validation_metadata: dict[str, object] = {}

    def validate_complete(raw_response: str) -> None:
        analysis = parse_chunk_claim_response(
            raw_response,
            scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            core_page_refs=chunk.core_page_refs,
            request=request,
        )
        validation_metadata.update(
            {
                "coverage_record_count": len(analysis.page_coverage),
                "structure_marker_count": len(analysis.structure),
                "claim_count": len(analysis.claims),
            }
        )

    runtime: dict[str, Any] = {
        "input_json": input_json,
        "validate_complete": validate_complete,
        "validation_metadata": validation_metadata,
    }
    return case_metadata, runtime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--service-tier",
        choices=("default", "priority"),
        default="default",
    )
    parser.add_argument(
        "--acknowledge-priority-cost",
        action="store_true",
        help="Required for the premium priority diagnostic.",
    )
    arguments = parser.parse_args()
    if (
        arguments.service_tier == "priority"
        and not arguments.acknowledge_priority_cost
    ):
        raise SystemExit(
            "Priority processing has a premium token cost; pass the explicit "
            "cost acknowledgement only after the default-tier result justifies it."
        )

    case_metadata, runtime = _prepare_case()
    base_output: dict[str, object] = {
        "diagnostic": "protocol_claim_stream_latency_v1",
        "provider_api_style": "OpenAI-compatible Chat Completions",
        "endpoint": "POST /v1/chat/completions",
        "sdk": "openai-python synchronous OpenAI client",
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "requested_service_tier": arguments.service_tier,
        "service_tier_parameter": (
            "omitted"
            if arguments.service_tier == "default"
            else "priority"
        ),
        "priority_cost_acknowledged": bool(
            arguments.service_tier == "priority"
            and arguments.acknowledge_priority_cost
        ),
        "max_retries": 0,
        "timeout_seconds": TIMEOUT_SECONDS,
        "production_claim_streaming_changed": False,
        "production_model_configuration_changed": False,
        "provider_content_persisted_or_logged": False,
        "case": case_metadata,
    }
    if arguments.dry_run:
        base_output["provider_call_performed"] = False
        print(json.dumps(base_output, indent=2, sort_keys=True))
        return 0

    load_dotenv(ROOT / ".env", override=False)
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("XAI_API_KEY is not configured.")
    base_url = os.environ.get(
        "XAI_BASE_URL",
        "https://api.x.ai/v1",
    ).rstrip("/")
    base_output["base_url"] = (
        "https://api.x.ai/v1"
        if base_url == "https://api.x.ai/v1"
        else "deployment-configured OpenAI-compatible base URL"
    )

    with OpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=0,
        timeout=TIMEOUT_SECONDS,
    ) as client:
        result = run_protocol_provider_stream_diagnostic(
            client,
            model=MODEL,
            reasoning_effort=REASONING_EFFORT,
            system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
            input_json=runtime["input_json"],
            response_schema=CLAIM_RESPONSE_SCHEMA,
            validate_complete=runtime["validate_complete"],
            timeout_seconds=TIMEOUT_SECONDS,
            service_tier=arguments.service_tier,
        )

    base_output.update(
        {
            "provider_call_performed": True,
            "result": result.public_dict(),
            "canonical_validation_metadata": runtime[
                "validation_metadata"
            ],
            "dominant_latency_classification": _classification(result),
        }
    )
    print(json.dumps(base_output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
