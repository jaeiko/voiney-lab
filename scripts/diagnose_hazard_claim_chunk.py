#!/usr/bin/env python3
"""One authorized provider call on the ANKOM chunk that contains page 30.

Page 30 carries an unnumbered safety block -- a hazard heading, an exothermic
warning, required protective equipment, a containment instruction -- that the
deterministic offline model cannot claim, because recognising it is a semantic
judgement. Earlier work proved such a claim is *admissible*; this measures
whether a real provider actually makes one.

The chunk is located by asking which chunk holds page 30 as a core page, not by
hardcoding a chunk index. Production protocol analysis does not import or call
this script. It refuses to send anything unless --execute is passed, and it
sends exactly one request with no retry.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    extraction_for_chunk,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    ClaimCategory,
    claim_response_schema,
    claim_response_schema_metrics,
    generate_page_evidence_segments,
    parse_chunk_claim_response,
    prepare_chunk_claim_request_context,
)

ROOT = Path(__file__).resolve().parents[1]
ANKOM_SOURCE = (
    ROOT
    / "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    / "5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)
HAZARD_PAGE = 30
MODEL = "grok-4.3"
REASONING_EFFORT = "none"
TIMEOUT_SECONDS = 119.0


def build_request():
    extraction = extract_protocol_pdf(ANKOM_SOURCE)
    plan = plan_protocol_chunks(
        extraction,
        f"protocol-{extraction.sha256[:32]}",
        "pdf-1",
        limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
    )
    chunk = next(
        item for item in plan.chunks if HAZARD_PAGE in item.core_page_refs
    )
    scoped = extraction_for_chunk(extraction, chunk)
    request = prepare_chunk_claim_request_context(
        scoped,
        source_revision=chunk.candidate_revision_id,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        core_page_refs=chunk.core_page_refs,
        context_page_refs=chunk.overlap_page_refs,
    )
    return extraction, scoped, chunk, request


def composition(chunk, request) -> dict[str, object]:
    metrics = claim_response_schema_metrics(request)
    return {
        "chunk_id": chunk.chunk_id,
        "core_page_refs": list(chunk.core_page_refs),
        "context_page_refs": list(chunk.overlap_page_refs),
        "extracted_text_bytes": chunk.extracted_text_bytes,
        "conservative_token_estimate": chunk.conservative_token_estimate,
        "handles_per_page": {
            page.source_page_number: len(page.evidence)
            for page in request.pages
        },
        "input_json_bytes": len(request.input_json().encode("utf-8")),
        "schema_metrics": metrics.public_dict(),
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "timeout_seconds": TIMEOUT_SECONDS,
    }


def hazard_report(scoped, chunk, analysis) -> dict[str, object]:
    """Which hazard-block segments a claim now cites, and how."""

    segments = generate_page_evidence_segments(
        scoped,
        source_revision=chunk.candidate_revision_id,
        page_number=HAZARD_PAGE,
    )
    index_of = {segment.segment_id: segment.segment_index for segment in segments}
    hazards = [
        claim
        for claim in analysis.claims
        if claim.category is ClaimCategory.WARNING_HAZARD
    ]
    cited: dict[int, list[dict[str, object]]] = {}
    for claim in hazards:
        for segment_id in claim.evidence.evidence_segment_ids:
            if segment_id not in index_of:
                continue
            cited.setdefault(index_of[segment_id], []).append(
                {
                    "claim_id": claim.claim_id,
                    "form": (
                        "document-level"
                        if claim.target_claim_id is None
                        else "step-attached"
                    ),
                }
            )
    coverage = {
        item.source_page_number: item for item in analysis.page_coverage
    }
    hazard_coverage = coverage.get(HAZARD_PAGE)
    declined = (
        {index_of[s] for s in hazard_coverage.declined_segment_ids if s in index_of}
        if hazard_coverage is not None
        else set()
    )
    return {
        "warning_hazard_claim_count": len(hazards),
        "hazard_block_segments": {
            index: {
                "claimed_by": cited.get(index, []),
                "declined": index in declined,
            }
            for index in (4, 5, 6, 7)
        },
        "page_30_status": (
            hazard_coverage.status.value if hazard_coverage is not None else None
        ),
        "claims_total": len(analysis.claims),
        "claims_by_category": {
            category.value: sum(
                1 for claim in analysis.claims if claim.category is category
            )
            for category in ClaimCategory
            if any(claim.category is category for claim in analysis.claims)
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send the single authorized request. Without it, nothing is sent.",
    )
    parser.add_argument("--save-response", type=Path, default=None)
    arguments = parser.parse_args()

    extraction, scoped, chunk, request = build_request()
    output: dict[str, object] = {
        "source_sha256": extraction.sha256,
        "text_verification": extraction.text_verification.value,
        "request": composition(chunk, request),
        "sent": False,
    }
    if not arguments.execute:
        output["note"] = "dry run; no request was sent"
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    load_dotenv()
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        output["note"] = "XAI_API_KEY is absent; nothing was sent"
        print(json.dumps(output, indent=2, sort_keys=True))
        return 1

    schema = claim_response_schema(request)
    started = time.monotonic()
    with OpenAI(
        base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
        api_key=api_key,
        timeout=TIMEOUT_SECONDS,
    ) as client:
        completion = client.chat.completions.create(
            model=MODEL,
            reasoning_effort=REASONING_EFFORT,
            timeout=TIMEOUT_SECONDS,
            messages=[
                {"role": "system", "content": CLAIM_ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": request.input_json()},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "protocol_chunk_claims",
                    "schema": schema,
                    "strict": True,
                },
            },
        )
    elapsed = time.monotonic() - started
    raw = completion.choices[0].message.content or ""
    output["sent"] = True
    output["latency_seconds"] = round(elapsed, 3)
    output["response_bytes"] = len(raw.encode("utf-8"))
    if arguments.save_response is not None:
        arguments.save_response.write_text(raw, encoding="utf-8")

    try:
        analysis = parse_chunk_claim_response(
            raw,
            extraction=scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            core_page_refs=chunk.core_page_refs,
            request=request,
        )
    except Exception as error:  # noqa: BLE001 - classify, never retry
        diagnostic = getattr(error, "diagnostic", None)
        output["canonical_validation"] = "rejected"
        output["failure"] = {
            "error": type(error).__name__,
            "reason_code": getattr(diagnostic, "reason_code", None),
            "mismatch_class": getattr(diagnostic, "mismatch_class", None),
            "validation_stage": getattr(diagnostic, "validation_stage", None),
            "page_number": getattr(diagnostic, "page_number", None),
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 1

    output["canonical_validation"] = "passed"
    output["hazard"] = hazard_report(scoped, chunk, analysis)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
