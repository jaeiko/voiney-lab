#!/usr/bin/env python3
"""One authorized pass over every chunk of the ANKOM protocol.

Three earlier calls all landed on the same chunk. This runs each of the eight
exactly once, with no retry, to produce the first whole-document measurement of
the current contract. It records per-chunk outcomes, attempts the merge and the
domain assembly, and reports which obligation each failure belongs to.

Production protocol analysis does not import or call this script. It sends
nothing unless --execute is passed, and a chunk that fails is recorded and
skipped, never retried.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ProtocolChunkMergeError,
    ValidatedChunkResult,
    assemble_validated_protocol_claims,
    extraction_for_chunk,
    merge_validated_chunk_results,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_SCHEMA_VERSION,
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
MODEL = "grok-4.3"
REASONING_EFFORT = "none"
TIMEOUT_SECONDS = 119.0
_EXCERPT = 72


def _short(text: str) -> str:
    collapsed = " ".join(text.split())
    return (
        collapsed
        if len(collapsed) <= _EXCERPT
        else collapsed[: _EXCERPT - 1] + "…"
    )


def _plan():
    extraction = extract_protocol_pdf(ANKOM_SOURCE)
    plan = plan_protocol_chunks(
        extraction,
        f"protocol-{extraction.sha256[:32]}",
        "pdf-1",
        limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
    )
    return extraction, plan


def _request(extraction, chunk):
    scoped = extraction_for_chunk(extraction, chunk)
    return scoped, prepare_chunk_claim_request_context(
        scoped,
        source_revision=chunk.candidate_revision_id,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        core_page_refs=chunk.core_page_refs,
        context_page_refs=chunk.overlap_page_refs,
    )


def _composition(chunk, request) -> dict[str, object]:
    return {
        "ordinal": chunk.ordinal,
        "core_page_refs": list(chunk.core_page_refs),
        "context_page_refs": list(chunk.overlap_page_refs),
        "extracted_text_bytes": chunk.extracted_text_bytes,
        "token_estimate": chunk.conservative_token_estimate,
        "handle_count": sum(len(page.evidence) for page in request.pages),
        "input_json_bytes": len(request.input_json().encode("utf-8")),
        "schema_bytes": claim_response_schema_metrics(
            request
        ).public_dict()["schema_after_bytes"],
    }


def _chunk_report(scoped, chunk, analysis) -> dict[str, object]:
    hazards = [
        claim
        for claim in analysis.claims
        if claim.category is ClaimCategory.WARNING_HAZARD
    ]
    unaccounted: list[dict[str, object]] = []
    cited = declined = 0
    for coverage in analysis.page_coverage:
        declined += len(coverage.declined_segment_ids)
        segments = {
            segment.segment_id: segment
            for segment in generate_page_evidence_segments(
                scoped,
                source_revision=chunk.candidate_revision_id,
                page_number=coverage.source_page_number,
            )
        }
        for segment_id in coverage.unaccounted_segment_ids:
            segment = segments.get(segment_id)
            unaccounted.append(
                {
                    "page": coverage.source_page_number,
                    "segment_index": segment.segment_index if segment else None,
                    "text": _short(segment.text) if segment else "",
                }
            )
    cited = len(
        {
            (item.evidence.source_page_number, segment_id)
            for item in (*analysis.structure, *analysis.claims)
            for segment_id in item.evidence.evidence_segment_ids
        }
    )
    return {
        "claims": len(analysis.claims),
        "structure_markers": len(analysis.structure),
        "by_category": dict(
            sorted(Counter(c.category.value for c in analysis.claims).items())
        ),
        "warning_hazard": len(hazards),
        "warning_hazard_step_attached": sum(
            1 for c in hazards if c.target_claim_id is not None
        ),
        "hazard_pages": sorted(
            {c.evidence.source_page_number for c in hazards}
        ),
        "accounting": {
            "cited_segments": cited,
            "declined_segments": declined,
            "unaccounted_segments": len(unaccounted),
        },
        "unaccounted": unaccounted,
        "page_status": {
            item.source_page_number: item.status.value
            for item in analysis.page_coverage
        },
        "obligations": {
            "numbered_action": any(
                c.category is ClaimCategory.ACTION for c in analysis.claims
            ),
            "value_extraction": any(
                c.category
                in {
                    ClaimCategory.QUANTITY,
                    ClaimCategory.CONCENTRATION,
                    ClaimCategory.TEMPERATURE,
                    ClaimCategory.DURATION,
                    ClaimCategory.AGITATION_SPEED,
                }
                for c in analysis.claims
            ),
            "hazard_judgement": bool(hazards),
            "declination_list": declined > 0,
            "exhaustive_accounting": not unaccounted,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--responses", type=Path, default=None)
    arguments = parser.parse_args()

    extraction, plan = _plan()
    report: dict[str, object] = {
        "source_sha256": extraction.sha256,
        "page_count": extraction.page_count,
        "text_verification": extraction.text_verification.value,
        "claim_schema_version": CLAIM_SCHEMA_VERSION,
        "model": MODEL,
        "chunk_count": len(plan.chunks),
        "chunks": [],
        "sent": False,
    }
    if not arguments.execute:
        report["chunks"] = [
            _composition(chunk, _request(extraction, chunk)[1])
            for chunk in plan.chunks
        ]
        report["note"] = "dry run; nothing sent"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    load_dotenv()
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        report["note"] = "XAI_API_KEY absent; nothing sent"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    if arguments.responses is not None:
        arguments.responses.mkdir(parents=True, exist_ok=True)

    report["sent"] = True
    validated: list[ValidatedChunkResult] = []
    totals = Counter()
    with OpenAI(
        base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
        api_key=api_key,
        timeout=TIMEOUT_SECONDS,
    ) as client:
        for chunk in plan.chunks:
            scoped, request = _request(extraction, chunk)
            entry = _composition(chunk, request)
            started = time.monotonic()
            try:
                completion = client.chat.completions.create(
                    model=MODEL,
                    reasoning_effort=REASONING_EFFORT,
                    timeout=TIMEOUT_SECONDS,
                    messages=[
                        {
                            "role": "system",
                            "content": CLAIM_ANALYSIS_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": request.input_json()},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "protocol_chunk_claims",
                            "schema": claim_response_schema(request),
                            "strict": True,
                        },
                    },
                )
            except Exception as error:  # noqa: BLE001 - never retry
                entry["latency_seconds"] = round(time.monotonic() - started, 3)
                entry["transport_error"] = type(error).__name__
                entry["canonical_validation"] = "not_attempted"
                report["chunks"].append(entry)
                continue
            entry["latency_seconds"] = round(time.monotonic() - started, 3)
            raw = completion.choices[0].message.content or ""
            entry["response_bytes"] = len(raw.encode("utf-8"))
            usage = getattr(completion, "usage", None)
            if usage is not None:
                entry["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
                entry["completion_tokens"] = getattr(
                    usage, "completion_tokens", None
                )
            if arguments.responses is not None:
                (
                    arguments.responses / f"chunk-{chunk.ordinal}.json"
                ).write_text(raw, encoding="utf-8")
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
                entry["canonical_validation"] = "rejected"
                entry["failure"] = {
                    "error": type(error).__name__,
                    "reason_code": getattr(diagnostic, "reason_code", None),
                    "validation_stage": getattr(
                        diagnostic, "validation_stage", None
                    ),
                    "page_number": getattr(diagnostic, "page_number", None),
                }
                report["chunks"].append(entry)
                continue
            entry["canonical_validation"] = "passed"
            entry.update(_chunk_report(scoped, chunk, analysis))
            validated.append(ValidatedChunkResult(chunk, analysis))
            for name, met in entry["obligations"].items():
                totals[name] += bool(met)
            report["chunks"].append(entry)

    report["validated_chunks"] = len(validated)
    report["obligation_success"] = {
        name: f"{count}/{len(plan.chunks)}" for name, count in sorted(totals.items())
    }
    report["totals"] = {
        "latency_seconds": round(
            sum(e.get("latency_seconds", 0) for e in report["chunks"]), 3
        ),
        "response_bytes": sum(e.get("response_bytes", 0) for e in report["chunks"]),
        "input_json_bytes": sum(e["input_json_bytes"] for e in report["chunks"]),
        "prompt_tokens": sum(e.get("prompt_tokens") or 0 for e in report["chunks"]),
        "completion_tokens": sum(
            e.get("completion_tokens") or 0 for e in report["chunks"]
        ),
        "claims": sum(e.get("claims", 0) for e in report["chunks"]),
        "warning_hazard": sum(e.get("warning_hazard", 0) for e in report["chunks"]),
        "warning_hazard_step_attached": sum(
            e.get("warning_hazard_step_attached", 0) for e in report["chunks"]
        ),
        "unaccounted_segments": sum(
            e.get("accounting", {}).get("unaccounted_segments", 0)
            for e in report["chunks"]
        ),
    }
    if len(validated) == len(plan.chunks):
        try:
            merged = merge_validated_chunk_results(
                extraction, plan, tuple(validated)
            )
            report["merge"] = "passed"
            try:
                draft = assemble_validated_protocol_claims(extraction, merged)
                report["assembly"] = {
                    "status": "passed",
                    "readiness": draft.readiness.status.value,
                    "reason_codes": sorted(set(draft.readiness.reason_codes)),
                    "sections": len(draft.protocol.sections),
                    "steps": sum(
                        len(s.steps) for s in draft.protocol.sections
                    ),
                    "before_start": len(draft.protocol.before_start),
                }
            except Exception as error:  # noqa: BLE001
                report["assembly"] = {
                    "status": "rejected",
                    "error": type(error).__name__,
                    "reason": str(error),
                }
        except ProtocolChunkMergeError as error:
            report["merge"] = f"refused: {error.reason_code}"
    else:
        report["merge"] = (
            f"not attempted: only {len(validated)} of {len(plan.chunks)} chunks validated"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
