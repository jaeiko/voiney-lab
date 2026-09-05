"""Run one authorized provider call on one named chunk, and classify it.

Reports structure only: category counts, declared ranges and counts, whether a
citation satisfies the evidence shape rule, and the refusal reason code if the
response is refused. The response text itself is never logged or written to
disk -- evidence handles are identities the server already owns, not provider
content, so those are reported.

Never retries. One invocation sends at most the number of chunks named on the
command line, and refuses to run without --execute.
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

from voice_workflow_agent.chunk_analysis_cache import (
    ChunkAnalysisCache,
    key_for_chunk,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ValidatedChunkResult,
    extraction_for_chunk,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_SCHEMA_VERSION,
    claim_response_schema,
    excerpt_states_range,
    parse_chunk_claim_response,
    prepare_chunk_claim_request_context,
)

MODEL = "grok-4.3"
REASONING_EFFORT = "none"
TIMEOUT_SECONDS = 119.0


def _plan(source: Path):
    extraction = extract_protocol_pdf(source)
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


def _describe_segments(extraction, page_number, source_revision, segment_ids):
    """Say what shape each named segment has, without quoting the document.

    STEP 25 had to re-derive by hand which segment a refusal was about, and
    the answer mattered: in-gel page 6's offender was a Note carrying the only
    volume step 11 needs, not the running footer everyone assumed. The shape
    facts here -- index, length, whether it sits inside a numbered step, which
    labels are on the page -- are all computed by the server from its own
    bytes, so they identify the unit without reproducing its text.
    """

    if not segment_ids or not page_number:
        return []
    from voice_workflow_agent.protocol_claim_analysis import (
        _numbered_step_labels,
        _segments_inside_numbered_steps,
        generate_page_evidence_segments,
        segment_carries_unit_bearing_value,
    )

    segments = generate_page_evidence_segments(
        extraction, source_revision=source_revision, page_number=page_number
    )
    inside = _segments_inside_numbered_steps(segments)
    wanted = set(segment_ids)
    return [
        {
            "segment_id": segment.segment_id,
            "segment_index": segment.segment_index,
            "character_length": len(segment.text),
            "inside_a_numbered_step": segment.segment_id in inside,
            "states_a_unit_bearing_value": segment_carries_unit_bearing_value(
                segment.text
            ),
            "page_labels": list(
                _numbered_step_labels(
                    extraction.pages[page_number - 1].text
                )
            ),
        }
        for segment in segments
        if segment.segment_id in wanted
    ]


def _cache_hit(cache, extraction, chunk, scoped, request):
    """A previously validated chunk, put back through the current rules."""

    if cache is None:
        return None
    return cache.load(
        key_for_chunk(extraction, chunk),
        extraction=scoped,
        request=request,
    )


def _observe(payload: dict, request) -> dict:
    """Structure only: no provider prose is copied out of the response."""

    claims = payload.get("claims") or []
    handles = {
        item.handle: item.segment
        for page in request.pages
        for item in page.evidence
    }
    repetitions = []
    for claim in claims:
        if claim.get("category") not in {
            "repeat_condition",
            "fixed_range_repetition",
            "operator_determined_repetition",
        }:
            continue
        labels = claim.get("repeated_step_labels")
        cited = (claim.get("evidence") or {}).get("evidence_segment_ids") or []
        excerpt = "".join(
            handles[handle].text for handle in cited if handle in handles
        )
        satisfies = (
            excerpt_states_range(excerpt, labels[0], labels[1])
            if isinstance(labels, list) and len(labels) == 2
            else None
        )
        repetitions.append(
            {
                "claim_id": claim.get("claim_id"),
                "category": claim.get("category"),
                "declared_range": labels,
                "declared_count": claim.get("repetition_count"),
                "cited_handles": len(cited),
                "cited_handles_resolve": all(h in handles for h in cited),
                "evidence_states_declared_range": satisfies,
            }
        )
    return {
        "claims_total": len(claims),
        "categories": dict(Counter(c.get("category") for c in claims)),
        "action_labels": sorted(
            str(c.get("source_label"))
            for c in claims
            if c.get("category") == "action"
        ),
        "document_level_claims": sum(
            1 for c in claims if c.get("target_claim_id") is None
        ),
        "declined_segments": sum(
            len(i.get("declined_evidence_segment_ids") or [])
            for i in payload.get("page_coverage") or []
        ),
        "pages_self_reported_incomplete": [
            i.get("source_page_number")
            for i in payload.get("page_coverage") or []
            if i.get("analysis_incomplete")
        ],
        "repetition_claims": repetitions,
        # How each numbered label was accounted for. Without this a refusal
        # that happens before the label check leaves the disposition
        # unobservable, which is what made the first Stage 1 run silent about
        # the label that caused the previous step's failure.
        "non_step_labels": [
            {
                "source_page_number": item.get("source_page_number"),
                "labels": sorted(
                    str(entry.get("source_label"))
                    for entry in (item.get("non_step_labels") or [])
                ),
            }
            for item in payload.get("page_coverage") or []
            if item.get("non_step_labels")
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--chunk", type=int, action="append", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--budget", type=int, required=True)
    # Merge needs every chunk's validated analysis at once. Keeping them for
    # the length of one invocation is the product's own path -- the analysis is
    # what append_analysis_revision stores -- and nothing is written to disk
    # here. Not doing this is why the first in-gel run could not be merged
    # without paying for the calls again.
    parser.add_argument("--walk", action="store_true")
    # A chunk that already passed validation does not have to be paid for
    # again. Merge needs every chunk at once, so without this a document with
    # more chunks than remaining budget could not be closed at any budget --
    # arithmetic, not model quality. A cached chunk is revalidated under the
    # current rules before it is used; see chunk_analysis_cache.
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--no-cache", action="store_true")
    arguments = parser.parse_args()

    if len(arguments.chunk) > arguments.budget:
        raise SystemExit(
            f"refusing: {len(arguments.chunk)} chunks exceeds budget "
            f"{arguments.budget}"
        )

    extraction, plan = _plan(arguments.source)
    cache = None if arguments.no_cache else ChunkAnalysisCache(arguments.cache_dir)
    report: dict[str, object] = {
        "source": arguments.source.name,
        "source_sha256": extraction.sha256,
        "text_verification": extraction.text_verification.value,
        "claim_schema_version": CLAIM_SCHEMA_VERSION,
        "model": MODEL,
        "chunk_count": len(plan.chunks),
        "requested_chunks": arguments.chunk,
        "calls_sent": 0,
        "cache_enabled": cache is not None,
        "cache_root": str(cache.root) if cache is not None else None,
        "cache_hits": [],
        "results": [],
    }
    validated: list[ValidatedChunkResult] = []
    from_cache: dict[str, str] = {}
    selected = [c for c in plan.chunks if c.ordinal in set(arguments.chunk)]
    if len(selected) != len(set(arguments.chunk)):
        raise SystemExit("a requested chunk ordinal does not exist")

    if not arguments.execute:
        for chunk in selected:
            scoped, request = _request(extraction, chunk)
            schema = claim_response_schema(request)
            body = request.input_json()
            required = set(
                schema["properties"]["claims"]["items"]["required"]
            )
            unstated = [
                field
                for field in required
                if field not in CLAIM_ANALYSIS_SYSTEM_PROMPT
            ]
            report["results"].append(
                {
                    "chunk": chunk.ordinal,
                    "core_pages": list(chunk.core_page_refs),
                    "request_bytes": len(body.encode("utf-8")),
                    "schema_builds": bool(schema),
                    "required_claim_fields_unstated_in_prompt": unstated,
                }
            )
        report["note"] = "dry run; nothing sent"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    load_dotenv()
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        report["note"] = "XAI_API_KEY absent; nothing sent"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    with OpenAI(
        base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
        api_key=api_key,
        timeout=TIMEOUT_SECONDS,
    ) as client:
        for chunk in selected:
            scoped, request = _request(extraction, chunk)
            hit = _cache_hit(cache, extraction, chunk, scoped, request)
            if hit is not None:
                validated.append(ValidatedChunkResult(chunk, hit.analysis))
                from_cache[chunk.chunk_id] = hit.key_digest
                report["cache_hits"].append(
                    {"chunk": chunk.ordinal, "key_digest": hit.key_digest}
                )
                report["results"].append(
                    {
                        "chunk": chunk.ordinal,
                        "core_pages": list(chunk.core_page_refs),
                        "source": "cache",
                        "canonical_validation": "revalidated",
                    }
                )
                continue
            entry: dict[str, object] = {
                "chunk": chunk.ordinal,
                "core_pages": list(chunk.core_page_refs),
                "source": "provider",
            }
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
                report["calls_sent"] = int(report["calls_sent"]) + 1
                entry["latency_seconds"] = round(time.monotonic() - started, 3)
                entry["transport_error"] = type(error).__name__
                entry["canonical_validation"] = "not_attempted"
                report["results"].append(entry)
                break
            report["calls_sent"] = int(report["calls_sent"]) + 1
            entry["latency_seconds"] = round(time.monotonic() - started, 3)
            raw = completion.choices[0].message.content or ""
            entry["response_bytes"] = len(raw.encode("utf-8"))
            usage = getattr(completion, "usage", None)
            if usage is not None:
                entry["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
                entry["completion_tokens"] = getattr(
                    usage, "completion_tokens", None
                )
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                entry["observation"] = _observe(payload, request)
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
                offending = tuple(
                    getattr(diagnostic, "offending_segment_ids", ()) or ()
                )
                entry["failure"] = {
                    "error": type(error).__name__,
                    "reason_code": getattr(diagnostic, "reason_code", None),
                    "validation_stage": getattr(
                        diagnostic, "validation_stage", None
                    ),
                    "page_number": getattr(diagnostic, "page_number", None),
                    "mismatch_class": getattr(
                        diagnostic, "mismatch_class", None
                    ),
                    # Identities, not content: which segment was mishandled.
                    "offending_segment_ids": list(offending),
                    "offending_segments": _describe_segments(
                        extraction, getattr(diagnostic, "page_number", None),
                        chunk.candidate_revision_id, offending,
                    ),
                }
            else:
                entry["canonical_validation"] = "passed"
                validated.append(ValidatedChunkResult(chunk, analysis))
                if cache is not None:
                    try:
                        path = cache.store(
                            key_for_chunk(extraction, chunk), raw
                        )
                    except Exception as error:  # noqa: BLE001 - never fatal
                        entry["cache_write"] = type(error).__name__
                    else:
                        entry["cache_write"] = "stored"
                        entry["cache_key_digest"] = path.stem
            report["results"].append(entry)

    if cache is not None:
        have = {result.chunk.chunk_id for result in validated}
        for chunk in plan.chunks:
            if chunk.chunk_id in have:
                continue
            scoped, request = _request(extraction, chunk)
            hit = _cache_hit(cache, extraction, chunk, scoped, request)
            if hit is None:
                continue
            validated.append(ValidatedChunkResult(chunk, hit.analysis))
            from_cache[chunk.chunk_id] = hit.key_digest
            report["cache_hits"].append(
                {"chunk": chunk.ordinal, "key_digest": hit.key_digest}
            )
    # "Validated" is not "done", and reading it that way cost a STEP. A chunk
    # whose page coverage leaves segments unaccounted passes on its own --
    # omission is a silence, not a false statement -- and then blocks the
    # whole-document merge. Say so here rather than reporting five passes and
    # an unexplained merge refusal.
    coverage = []
    for result in validated:
        for item in result.analysis.page_coverage:
            coverage.append(
                {
                    "chunk": result.chunk.ordinal,
                    "source_page_number": item.source_page_number,
                    "status": item.status.value,
                    "cited": len(item.evidence_item_ids),
                    "declined": len(item.declined_segment_ids),
                    "unaccounted": len(item.unaccounted_segment_ids),
                }
            )
    report["page_coverage"] = sorted(
        coverage, key=lambda item: item["source_page_number"]
    )
    report["unaccounted_segment_total"] = sum(
        item["unaccounted"] for item in coverage
    )
    report["pages_blocking_merge"] = sorted(
        item["source_page_number"]
        for item in coverage
        if item["status"] == "analysis_incomplete"
    )
    report["validated_chunks"] = sorted(r.chunk.ordinal for r in validated)
    report["validated_from_cache"] = sorted(
        r.chunk.ordinal for r in validated if r.chunk.chunk_id in from_cache
    )

    if arguments.walk and len(validated) == len(plan.chunks):
        # Merge is order-sensitive; a cache fill appends out of plan order.
        order = {chunk.chunk_id: chunk.ordinal for chunk in plan.chunks}
        validated.sort(key=lambda result: order[result.chunk.chunk_id])
        report["walk"] = _walk(arguments.source, extraction, plan, validated)
    elif arguments.walk:
        report["walk"] = {
            "attempted": False,
            "reason": (
                f"{len(validated)} of {len(plan.chunks)} chunks validated; "
                "merge requires every chunk"
            ),
        }

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _walk(source, extraction, plan, validated):
    """Merge, assemble, and push through the gated route in a temp store."""

    import tempfile
    from dataclasses import replace

    from voice_workflow_agent import experiment_protocol as domain
    from voice_workflow_agent.curated_protocol import CuratedProtocolSession
    from voice_workflow_agent.experiment_protocol_store import (
        ProtocolPersistenceSettings,
        initialize_protocol_store,
    )
    from voice_workflow_agent.protocol_catalog import (
        AMBIGUITY_SINGLE_AUTHORITATIVE,
        ProtocolCatalog,
    )
    from voice_workflow_agent.protocol_chunk_analysis import (
        assemble_validated_protocol_claims,
        merge_validated_chunk_results,
    )

    out: dict[str, object] = {"attempted": True}
    try:
        merged = merge_validated_chunk_results(
            extraction, plan, tuple(validated)
        )
    except Exception as error:  # noqa: BLE001
        out["merge"] = {"ok": False, **_reason(error)}
        return out
    out["merge"] = {
        "ok": True,
        "claims": len(merged.claims),
        "markers": len(merged.structure),
    }
    try:
        draft = assemble_validated_protocol_claims(extraction, merged)
    except Exception as error:  # noqa: BLE001
        out["assembly"] = {"ok": False, **_reason(error)}
        return out
    protocol = draft.protocol
    steps = [s for section in protocol.sections for s in section.steps]
    values = 0
    for step in steps:
        for action in step.sub_actions:
            values += len(action.quantities) + len(action.conditions)
            values += 1 if action.estimated_duration else 0
            values += 1 if action.process_timer else 0
    out["assembly"] = {
        "ok": True,
        "sections": len(protocol.sections),
        "steps": len(steps),
        "values": values,
        "warnings_step": sum(len(s.warnings) for s in steps),
        "warnings_action": sum(
            len(a.warnings) for s in steps for a in s.sub_actions
        ),
        "materials": len(protocol.materials),
        "equipment": len(protocol.equipment),
        "before_start": len(protocol.before_start),
        "constructs": dict(
            Counter(type(c).__name__ for c in protocol.constructs)
        ),
    }
    readiness = domain.assess_readiness(protocol)
    out["readiness"] = {
        "status": readiness.status.value,
        "reason_codes": sorted(set(readiness.reason_codes)),
    }

    with tempfile.TemporaryDirectory() as temporary:
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, Path(temporary) / "catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            registration = catalog.register(
                source,
                source_filename=source.name,
                media_type="application/pdf",
            )
            protocol_id = registration.entry.protocol_id
            stored = domain.validate_protocol(
                replace(protocol, protocol_id=protocol_id)
            )
            store.append_analysis_revision(
                protocol_id,
                1,
                "analysis-provider-run",
                stored,
                domain.assess_readiness(stored),
                draft.capability_policy.profile_id,
            )
            revision_id = "pdf-1-analysis-1"
            findings: dict[str, object] = {}
            catalog.acknowledge_readiness_gate(
                protocol_id,
                revision_id,
                reason_code=(
                    domain.ReadinessReasonCode
                    .NO_DECLARED_SAFETY_WARNINGS.value
                ),
                actor_principal_id="reviewer@example.org",
                actor_role="reviewer",
                comment="Provider run; warnings reviewed against the source.",
            )
            findings["safety_gate_acknowledged"] = True
            for construct in stored.constructs:
                if isinstance(construct, domain.SourceAmbiguity):
                    catalog.resolve_ambiguity(
                        protocol_id,
                        revision_id,
                        ambiguity_id=construct.ambiguity_id,
                        decision=AMBIGUITY_SINGLE_AUTHORITATIVE,
                        evidence_segment_ids=(
                            construct.evidence.evidence_segment_ids
                        ),
                        actor_principal_id="reviewer@example.org",
                        actor_role="reviewer",
                        comment="Statements state the same value.",
                    )
                elif isinstance(construct, domain.FixedRangeRepetition):
                    catalog.confirm_fixed_repetition(
                        protocol_id,
                        revision_id,
                        repetition_id=construct.repetition_id,
                        repeat_count=construct.repeat_count,
                        evidence_segment_ids=(
                            construct.evidence.evidence_segment_ids
                        ),
                        actor_principal_id="reviewer@example.org",
                        actor_role="reviewer",
                        comment="Source states this count.",
                    )
            findings["ambiguities_resolved"] = len(
                catalog.ambiguity_findings(protocol_id, revision_id)
            )
            findings["repetitions_confirmed"] = len(
                catalog.repetition_findings(protocol_id, revision_id)
            )
            out["reviewer_findings"] = findings
            try:
                activated = catalog.activate_development(protocol_id)
            except Exception as error:  # noqa: BLE001
                out["development_activation"] = {"ok": False, **_reason(error)}
                return out
            out["development_activation"] = {
                "ok": True,
                "available_for_execution": activated.available_for_execution,
                "approval_status": activated.approval_status,
            }
            try:
                fixture = catalog.load_executable_fixture(protocol_id)
            except Exception as error:  # noqa: BLE001
                out["load_executable_fixture"] = {
                    "ok": False,
                    **_reason(error),
                }
                return out
            out["load_executable_fixture"] = {
                "ok": True,
                "ordered_step_labels": len(fixture.ordered_step_labels),
                "development_only": fixture.development_only,
            }
            session = CuratedProtocolSession(fixture)
            session.active = True
            session.current_index = 0
            frame = session.current_step_semantic_frame()
            out["first_step_guidance"] = {
                "ok": True,
                "step_id": frame.step_id,
                "step_label": frame.step_label,
                "parameters": len(frame.parameters),
                "actions": len(frame.actions),
                "ratios": len(frame.ratios),
            }
        finally:
            store.close()
    return out


def _reason(error: BaseException) -> dict[str, object]:
    diagnostic = getattr(error, "diagnostic", None)
    return {
        "error": type(error).__name__,
        "message": str(error)[:200],
        "reason_code": getattr(diagnostic, "reason_code", None)
        or getattr(error, "reason_code", None),
    }


if __name__ == "__main__":
    raise SystemExit(main())
