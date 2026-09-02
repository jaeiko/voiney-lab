"""One authorized call: does a model invent an action for a figure caption?

The numbered-action obligation fires on any line the extractor reads as a
numbered step label. On a near-unnumbered document those labels are mostly
figure captions, so the obligation demands an action claim where no action
exists. This measures which way a real model resolves that: it invents an
action for the caption, or it declines to and the chunk is refused.

The document is refused at admission, because its extraction carries unmapped
code points on seven pages. That refusal is correct and is not weakened here.
This harness selects a chunk whose pages carry none, asserts that before
sending, and clears the verification flag on a local copy only so the chunk
plan can be built for the measurement. Nothing it does admits the document.

Sends exactly one request. Never retries. Does not persist the response.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from voice_workflow_agent.experiment_protocol_pdf import (
    TextVerification,
    extract_protocol_pdf,
    unmapped_code_points,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    extraction_for_chunk,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_ANALYSIS_SYSTEM_PROMPT,
    CLAIM_SCHEMA_VERSION,
    ClaimCategory,
    _numbered_action_matches,
    _numbered_step_labels,
    claim_response_schema,
    generate_page_evidence_segments,
    parse_chunk_claim_response,
    prepare_chunk_claim_request_context,
    segment_is_substantive,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "intracellularmetaboliteextraction.pdf"
MODEL = "grok-4.3"
REASONING_EFFORT = "none"
TIMEOUT_SECONDS = 119.0
_CAPTION_PREFIX = "Figure "


def _label_lines(text: str) -> list[tuple[str, str]]:
    rows = []
    for match in _numbered_action_matches(text):
        start = text.rfind("\n", 0, match.start()) + 1
        end = text.find("\n", match.start())
        line = text[start : end if end != -1 else len(text)]
        rows.append((match.group("label"), " ".join(line.split())))
    return rows


def _select(extraction):
    """The chunk with the most caption labels and no unmapped code points."""

    probe = dataclasses.replace(
        extraction,
        text_verification=TextVerification.VERIFIED,
        divergent_page_numbers=(),
    )
    plan = plan_protocol_chunks(
        probe,
        f"protocol-{extraction.sha256[:32]}",
        "pdf-1",
        limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
    )
    dirty = {
        page.source_page_number
        for page in extraction.pages
        if unmapped_code_points(page.text)
    }
    best = None
    for chunk in plan.chunks:
        pages = set(chunk.core_page_refs) | set(chunk.overlap_page_refs)
        if pages & dirty:
            continue
        captions = sum(
            1
            for page in chunk.core_page_refs
            for _, line in _label_lines(extraction.pages[page - 1].text)
            if line.startswith(_CAPTION_PREFIX)
        )
        if captions and (best is None or captions > best[1]):
            best = (chunk, captions)
    if best is None:
        raise SystemExit("no chunk carries caption labels on unmapped-free pages")
    return probe, best[0], best[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    arguments = parser.parse_args()

    extraction = extract_protocol_pdf(SOURCE)
    probe, chunk, captions = _select(extraction)
    scoped = extraction_for_chunk(probe, chunk)
    request = prepare_chunk_claim_request_context(
        scoped,
        source_revision=chunk.candidate_revision_id,
        chunk_id=chunk.chunk_id,
        ordinal=chunk.ordinal,
        core_page_refs=chunk.core_page_refs,
        context_page_refs=chunk.overlap_page_refs,
    )
    obligation = {
        page: list(_numbered_step_labels(extraction.pages[page - 1].text))
        for page in chunk.core_page_refs
    }
    report = {
        "source": SOURCE.name,
        "source_sha256": extraction.sha256,
        "document_text_verification": extraction.text_verification.value,
        "claim_schema_version": CLAIM_SCHEMA_VERSION,
        "chunk_ordinal": chunk.ordinal,
        "core_pages": list(chunk.core_page_refs),
        "caption_labels": captions,
        "labels_demanding_an_action_claim": obligation,
        "label_lines": {
            page: _label_lines(extraction.pages[page - 1].text)
            for page in chunk.core_page_refs
        },
        "substantive_segments": {
            page: sum(
                1
                for segment in generate_page_evidence_segments(
                    scoped,
                    source_revision=chunk.candidate_revision_id,
                    page_number=page,
                )
                if segment_is_substantive(segment.text)
            )
            for page in chunk.core_page_refs
        },
        "sent": False,
    }
    assert not any(
        unmapped_code_points(page.text) for page in scoped.pages
    ), "refusing to measure on pages carrying unmapped code points"

    if not arguments.execute:
        report["note"] = "dry run; nothing sent"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    load_dotenv()
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        report["note"] = "XAI_API_KEY absent; nothing sent"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    report["sent"] = True
    started = time.monotonic()
    with OpenAI(
        base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
        api_key=api_key,
        timeout=TIMEOUT_SECONDS,
    ) as client:
        try:
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
                        "schema": claim_response_schema(request),
                        "strict": True,
                    },
                },
            )
        except Exception as error:  # noqa: BLE001 - classify, never retry
            report["latency_seconds"] = round(time.monotonic() - started, 3)
            report["transport_error"] = type(error).__name__
            report["canonical_validation"] = "not_attempted"
            print(json.dumps(report, indent=2, sort_keys=True))
            return 1

    report["latency_seconds"] = round(time.monotonic() - started, 3)
    raw = completion.choices[0].message.content or ""
    report["response_bytes"] = len(raw.encode("utf-8"))
    usage = getattr(completion, "usage", None)
    if usage is not None:
        report["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
        report["completion_tokens"] = getattr(usage, "completion_tokens", None)

    # Structure only: category counts, labels, attachment and accounting.
    # The response text itself is not logged and not written to disk.
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        claims = payload.get("claims") or []
        actions = [c for c in claims if c.get("category") == "action"]
        report["observation"] = {
            "claims_total": len(claims),
            "categories": dict(Counter(c.get("category") for c in claims)),
            "action_claims": len(actions),
            "action_labels_by_page": {
                page: sorted(
                    str(c.get("source_label"))
                    for c in actions
                    if c.get("evidence", {}).get("source_page_number") == page
                )
                for page in chunk.core_page_refs
            },
            "document_level_claims": sum(
                1 for c in claims if c.get("target_claim_id") is None
            ),
            "step_attached_claims": sum(
                1 for c in claims if c.get("target_claim_id") is not None
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
        }

    try:
        parse_chunk_claim_response(
            raw,
            extraction=scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            core_page_refs=chunk.core_page_refs,
            request=request,
        )
    except Exception as error:  # noqa: BLE001 - classify, never retry
        diagnostic = getattr(error, "diagnostic", None)
        report["canonical_validation"] = "rejected"
        report["failure"] = {
            "error": type(error).__name__,
            "reason_code": getattr(diagnostic, "reason_code", None),
            "validation_stage": getattr(diagnostic, "validation_stage", None),
            "page_number": getattr(diagnostic, "page_number", None),
        }
    else:
        report["canonical_validation"] = "passed"

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
