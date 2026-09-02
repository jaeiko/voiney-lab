#!/usr/bin/env python3
"""Run the deterministic semantic-quality audit over local protocol PDFs.

This runner makes no provider call.  It drives the same offline deterministic
fixture model used by ``prototype_claim_chunks.py``, so what it measures is the
audit itself plus the semantic quality of that fixture -- it is *not* evidence
about any live provider's semantic quality.

Output is content-free by default because findings quote laboratory source
documents; pass ``--include-source-excerpts`` only for public fixtures.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ProtocolChunkMergeError,
    ValidatedChunkResult,
    analyze_protocol_chunk,
    assemble_validated_protocol_claims,
    merge_validated_chunk_results,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    MergedProtocolClaims,
    degraded_segmentation_pages,
)
from voice_workflow_agent.protocol_claim_semantic_audit import (
    audit_assembly_preservation,
    audit_chunk_semantics,
    audit_merged_semantics,
)

try:
    from scripts.prototype_claim_chunks import (
        DEFAULT_ANKOM,
        DEFAULT_MULTI_PAGE,
        ExactNumberedStepClaimModel,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from prototype_claim_chunks import (
        DEFAULT_ANKOM,
        DEFAULT_MULTI_PAGE,
        ExactNumberedStepClaimModel,
    )


def _unvalidated_merged_view(extraction, plan, results):
    """Concatenate validated chunk claims without whole-document validation.

    Used only for reporting when the merge was refused; it asserts nothing
    about the document being coherent.
    """

    claims: dict[str, object] = {}
    markers: dict[str, object] = {}
    coverage = []
    for result in results:
        for claim in result.analysis.claims:
            claims.setdefault(claim.claim_id, claim)
        for marker in result.analysis.structure:
            markers.setdefault(marker.marker_id, marker)
        coverage.extend(result.analysis.page_coverage)
    return MergedProtocolClaims(
        protocol_id=plan.protocol_id,
        source_revision=plan.candidate_revision_id,
        source_sha256=extraction.sha256,
        capability_policy_id=results[0].analysis.capability_policy_id,
        required_chunk_ids=tuple(chunk.chunk_id for chunk in plan.chunks),
        page_coverage=tuple(coverage),
        structure=tuple(markers.values()),
        claims=tuple(claims.values()),
    )


def audit_source(path: Path, *, include_source_excerpts: bool) -> dict[str, object]:
    extraction = extract_protocol_pdf(path)
    limits = ChunkAnalysisLimits(max_concurrency=1, max_retries=0)
    plan = plan_protocol_chunks(
        extraction,
        f"protocol-{extraction.sha256[:32]}",
        "pdf-1",
        limits=limits,
    )
    model = ExactNumberedStepClaimModel(extraction)
    results = tuple(
        ValidatedChunkResult(chunk, analyze_protocol_chunk(extraction, chunk, model))
        for chunk in plan.chunks
    )
    # A document whose pages cannot all be accounted for is refused at merge.
    # That refusal is the correct outcome, and the audit still has to be able
    # to report on such a document, so the whole-document view falls back to
    # the validated per-chunk claims.
    merge_status = "accepted"
    draft = None
    try:
        merged = merge_validated_chunk_results(extraction, plan, results)
        draft = assemble_validated_protocol_claims(extraction, merged)
    except ProtocolChunkMergeError as error:
        merge_status = f"rejected: {error}"
        merged = _unvalidated_merged_view(extraction, plan, results)
    whole = audit_merged_semantics(extraction, merged)
    lost = (
        audit_assembly_preservation(merged, draft.protocol)
        if draft is not None
        else ()
    )
    return {
        "filename": path.name,
        "source_pages": extraction.page_count,
        "chunk_count": len(plan.chunks),
        "claim_count": len(merged.claims),
        "provider_mode": "deterministic_offline_fixture",
        "canonical_admission": "passed",
        "whole_document_merge": merge_status,
        # Pages with line structure that still produced one segment.  Reported,
        # not gated: on such a page a single claim accounts for everything, so
        # findings attributed elsewhere may simply be unreachable here.
        "degraded_segmentation_pages": list(
            degraded_segmentation_pages(extraction, source_revision="pdf-1")
        ),
        "per_chunk": [
            {
                "chunk_id": result.chunk.chunk_id,
                "core_pages": list(result.chunk.core_page_refs),
                **audit_chunk_semantics(extraction, result.analysis).public_dict(
                    include_source_excerpts=include_source_excerpts
                ),
            }
            for result in results
        ],
        "whole_document": whole.public_dict(
            include_source_excerpts=include_source_excerpts
        ),
        "assembly_preservation": {
            "claims_lost_in_assembly": len(lost),
            "findings": [
                finding.public_dict(
                    include_source_excerpts=include_source_excerpts
                )
                for finding in lost
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ankom", type=Path, default=DEFAULT_ANKOM)
    parser.add_argument("--multi-page", type=Path, default=DEFAULT_MULTI_PAGE)
    parser.add_argument(
        "--include-source-excerpts",
        action="store_true",
        help="Include quoted source text; use only for public fixtures.",
    )
    arguments = parser.parse_args()
    sources = [
        path
        for path in (arguments.ankom, arguments.multi_page)
        if path.exists()
    ]
    print(
        json.dumps(
            {
                "audit": "protocol_claim_semantic_quality",
                "live_provider_call": False,
                "sources": [
                    audit_source(
                        path,
                        include_source_excerpts=arguments.include_source_excerpts,
                    )
                    for path in sources
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
