#!/usr/bin/env python3
"""Run one content-free structural probe for ANKOM core pages 25--29.

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
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    extract_protocol_pdf,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    extraction_for_chunk,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    MAX_EVIDENCE_ITEM_REFS_PER_PAGE,
    MAX_PAGE_COVERAGE_RECORDS,
    ClaimCategory,
    ProviderClaimRequest,
    _numbered_action_matches,
    claim_response_schema,
    claim_response_schema_metrics,
    parse_chunk_claim_response,
    prepare_chunk_claim_request_context,
)
from voice_workflow_agent.protocol_provider_diagnostics import (
    ProtocolProviderStreamDiagnostic,
    run_protocol_provider_stream_diagnostic,
)
from voice_workflow_agent.protocol_claim_stream_telemetry import (
    measure_protocol_claim_json_telemetry,
)

try:
    from scripts.prototype_claim_chunks import ExactNumberedStepClaimModel
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from prototype_claim_chunks import ExactNumberedStepClaimModel


ROOT = Path(__file__).resolve().parents[1]
ANKOM_SOURCE = (
    ROOT
    / "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    / "5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)
MODEL = "grok-4.3"
REASONING_EFFORT = "none"
TIMEOUT_SECONDS = 119.0
EXPECTED_CORE_PAGES = tuple(range(25, 30))
EXPECTED_CONTEXT_PAGES = (24,)
EXPECTED_CLAIMS = 20
EXPECTED_RESPONSE_BYTES = 6_551


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


def _page_local_handles_are_valid(
    raw_response: str,
    request: ProviderClaimRequest,
) -> bool:
    """Check only provider page/handle membership without retaining content."""

    payload = json.loads(raw_response)
    if not isinstance(payload, dict):
        return False
    allowed = {
        page.source_page_number: frozenset(
            item.handle for item in page.evidence
        )
        for page in request.pages
        if page.role == "core"
    }
    records = (*payload.get("structure", ()), *payload.get("claims", ()))
    for record in records:
        if not isinstance(record, dict) or not isinstance(
            record.get("evidence"),
            dict,
        ):
            return False
        evidence = record["evidence"]
        page_number = evidence.get("source_page_number")
        handles = evidence.get("evidence_segment_ids")
        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number not in allowed
            or not isinstance(handles, list)
            or not handles
            or any(
                not isinstance(handle, str)
                or handle not in allowed[page_number]
                for handle in handles
            )
        ):
            return False
    return True


def _required_action_handle_map(
    extraction: ProtocolPdfExtraction,
    request: ProviderClaimRequest,
) -> dict[tuple[int, str], frozenset[str]]:
    """Map bounded page/label identities to their issued request handles."""

    result: dict[tuple[int, str], frozenset[str]] = {}
    for request_page in request.pages:
        if request_page.role != "core":
            continue
        page_number = request_page.source_page_number
        page_text = extraction.pages[page_number - 1].text
        matches = _numbered_action_matches(page_text)
        segment_ranges: list[tuple[int, int, str]] = []
        offset = 0
        for evidence in request_page.evidence:
            segment_end = offset + len(evidence.segment.text)
            segment_ranges.append((offset, segment_end, evidence.handle))
            offset = segment_end
        for index, match in enumerate(matches):
            start = match.start("label")
            end = (
                matches[index + 1].start("label")
                if index + 1 < len(matches)
                else len(page_text)
            )
            handles = frozenset(
                handle
                for segment_start, segment_end, handle in segment_ranges
                if segment_end > start and segment_start < end
            )
            if not handles:
                raise RuntimeError(
                    "A deterministic action has no issued provider evidence handle."
                )
            result[(page_number, match.group("label"))] = handles
    return result


def _privacy_safe_action_audit(
    raw_response: str,
    extraction: ProtocolPdfExtraction,
    request: ProviderClaimRequest,
) -> dict[str, object]:
    """Reduce one complete response to bounded action-coverage metadata."""

    payload = json.loads(raw_response)
    claims = payload.get("claims", ()) if isinstance(payload, dict) else ()
    if not isinstance(claims, list):
        claims = []
    required_handles = _required_action_handle_map(extraction, request)
    expected_order = tuple(required_handles)
    expected = frozenset(expected_order)
    action_by_label: set[tuple[int, str]] = set()
    action_by_evidence: set[tuple[int, str]] = set()
    categories_by_evidence: dict[tuple[int, str], set[str]] = {
        identity: set() for identity in expected_order
    }
    provider_action_count = 0
    unmapped_action_count = 0
    mapping_mismatch_count = 0
    multi_action_evidence_count = 0
    allowed_categories = {item.value for item in ClaimCategory}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        category = claim.get("category")
        if category not in allowed_categories:
            continue
        evidence = claim.get("evidence")
        page_number = (
            evidence.get("source_page_number")
            if isinstance(evidence, dict)
            else None
        )
        raw_handles = (
            evidence.get("evidence_segment_ids")
            if isinstance(evidence, dict)
            else None
        )
        selected_handles = (
            frozenset(item for item in raw_handles if isinstance(item, str))
            if isinstance(raw_handles, list)
            else frozenset()
        )
        evidence_identities = {
            identity
            for identity, handles in required_handles.items()
            if identity[0] == page_number and selected_handles & handles
        }
        for identity in evidence_identities:
            categories_by_evidence[identity].add(category)
        if category != ClaimCategory.ACTION.value:
            continue
        provider_action_count += 1
        if len(evidence_identities) > 1:
            multi_action_evidence_count += 1
        action_by_evidence.update(evidence_identities)
        source_label = claim.get("source_label")
        label_identity = (page_number, source_label)
        if label_identity in expected:
            action_by_label.add(label_identity)
            if label_identity not in evidence_identities:
                mapping_mismatch_count += 1
        else:
            unmapped_action_count += 1

    represented = action_by_label & action_by_evidence
    missing = tuple(
        identity for identity in expected_order if identity not in represented
    )
    return {
        "required_action_count": len(expected_order),
        "provider_action_count": provider_action_count,
        "represented_required_action_count": len(represented),
        "missing_action_count": len(missing),
        "missing_action_identities": [
            f"p{page_number}-n{label}" for page_number, label in missing
        ],
        "missing_action_other_categories": {
            f"p{page_number}-n{label}": sorted(
                categories_by_evidence[(page_number, label)] - {"action"}
            )
            for page_number, label in missing
            if categories_by_evidence[(page_number, label)] - {"action"}
        },
        "action_identity_mapping_mismatch_count": mapping_mismatch_count,
        "multi_action_evidence_claim_count": multi_action_evidence_count,
        "unmapped_action_claim_count": unmapped_action_count,
    }


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
    matching_chunks = tuple(
        chunk
        for chunk in plan.chunks
        if chunk.core_page_refs == EXPECTED_CORE_PAGES
        and chunk.overlap_page_refs == EXPECTED_CONTEXT_PAGES
    )
    if len(matching_chunks) != 1:
        raise RuntimeError("The fixed representative analysis unit changed.")
    chunk = matching_chunks[0]
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
    required_action_handles = _required_action_handle_map(scoped, request)
    required_action_count = len(required_action_handles)
    response_schema = claim_response_schema(request)
    schema_metrics = claim_response_schema_metrics(request)
    non_streaming_request = build_protocol_analysis_chat_request(
        model=MODEL,
        reasoning_effort=REASONING_EFFORT,
        system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
        input_json=input_json,
        response_schema=response_schema,
    )
    streaming_request = dict(non_streaming_request)
    streaming_request["stream"] = True
    streaming_request["stream_options"] = {"include_usage": True}
    case_metadata: dict[str, object] = {
        "source_page_count": extraction.page_count,
        "chunk_ordinal": chunk.ordinal,
        "core_page_refs": list(chunk.core_page_refs),
        "context_page_refs": list(chunk.overlap_page_refs),
        "expected_numbered_actions": required_action_count,
        "expected_numbered_action_identities": [
            f"p{page_number}-n{label}"
            for page_number, label in required_action_handles
        ],
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
        "page_local_handle_schema": schema_metrics.public_dict(),
    }
    validation_metadata: dict[str, object] = {}

    def validate_complete(raw_response: str) -> None:
        validation_metadata["page_local_handle_validity"] = (
            _page_local_handles_are_valid(raw_response, request)
        )
        validation_metadata["action_completeness_audit"] = (
            _privacy_safe_action_audit(raw_response, scoped, request)
        )
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
                "coverage_reference_count": sum(
                    len(item.evidence_item_ids)
                    for item in analysis.page_coverage
                ),
                "structure_marker_count": len(analysis.structure),
                "claim_count": len(analysis.claims),
                "action_count": sum(
                    claim.category.value == "action"
                    for claim in analysis.claims
                ),
                "evidence_handle_reference_count": sum(
                    len(item.evidence.evidence_segment_ids)
                    for item in (*analysis.structure, *analysis.claims)
                ),
                "exact_source_text_reconstruction": all(
                    item.source_text == item.evidence.source_excerpt
                    for item in (*analysis.structure, *analysis.claims)
                ),
                "core_coverage_exact": (
                    len(analysis.page_coverage) == len(chunk.core_page_refs)
                    and {
                        item.source_page_number
                        for item in analysis.page_coverage
                    }
                    == set(chunk.core_page_refs)
                ),
                "numbered_action_completeness": sum(
                    claim.category.value == "action"
                    for claim in analysis.claims
                )
                == required_action_count,
                "canonical_validation_succeeded": True,
            }
        )

    deterministic_response = ExactNumberedStepClaimModel(extraction).analyze(
        system_prompt=CLAIM_ANALYSIS_SYSTEM_PROMPT,
        input_json=input_json,
        response_schema=response_schema,
    )
    deterministic_analysis = parse_chunk_claim_response(
        deterministic_response,
        scoped,
        source_revision=chunk.candidate_revision_id,
        chunk_id=chunk.chunk_id,
        core_page_refs=chunk.core_page_refs,
        request=request,
    )
    deterministic_bytes = len(deterministic_response.encode("utf-8"))
    deterministic_claims = len(deterministic_analysis.claims)
    deterministic_actions = sum(
        claim.category.value == "action"
        for claim in deterministic_analysis.claims
    )
    deterministic_coverage_reference_counts = tuple(
        len(item.evidence_item_ids)
        for item in deterministic_analysis.page_coverage
    )
    if (
        deterministic_bytes != EXPECTED_RESPONSE_BYTES
        or deterministic_claims != EXPECTED_CLAIMS
        or deterministic_actions != required_action_count
        or len(deterministic_analysis.page_coverage)
        > MAX_PAGE_COVERAGE_RECORDS
        or max(deterministic_coverage_reference_counts, default=0)
        > MAX_EVIDENCE_ITEM_REFS_PER_PAGE
    ):
        raise RuntimeError("The deterministic claim-output baseline changed.")
    deterministic_telemetry = measure_protocol_claim_json_telemetry(
        deterministic_response
    )
    del deterministic_response
    case_metadata["deterministic_baseline"] = {
        "total_bytes": deterministic_bytes,
        "claim_count": deterministic_claims,
        "numbered_action_count": deterministic_actions,
        "coverage_record_count": len(deterministic_analysis.page_coverage),
        "maximum_coverage_reference_count": max(
            deterministic_coverage_reference_counts,
            default=0,
        ),
        "structure_marker_count": len(deterministic_analysis.structure),
        "canonical_validation_succeeded": True,
        "structural_telemetry": deterministic_telemetry.public_dict(),
    }
    case_metadata["schema_cardinality_bounds"] = {
        "required_page_coverage_records": len(chunk.core_page_refs),
        "permitted_core_coverage_pages": list(chunk.core_page_refs),
        "maximum_page_coverage_records": MAX_PAGE_COVERAGE_RECORDS,
        "maximum_evidence_item_references_per_page": (
            MAX_EVIDENCE_ITEM_REFS_PER_PAGE
        ),
        "coverage_references_must_be_unique": True,
    }

    runtime: dict[str, Any] = {
        "input_json": input_json,
        "response_schema": response_schema,
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
            response_schema=runtime["response_schema"],
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
