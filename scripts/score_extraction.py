#!/usr/bin/env python3
"""Score a cached extraction against a hand-built reference. Never calls out.

The pipeline has never produced an accuracy number. Every attempt has ended
before the merge, because a merge needs every chunk at once and a chunk costs a
provider call. This scores whatever the cache already holds, and refuses -- with
the ordinals named and the arithmetic stated -- when it does not hold enough.

It sends nothing. There is no provider client in this file and no --execute
flag: if a chunk is missing, the answer is a message, never a call.

The reference is a hand-built fixture. It is a **regression baseline**, not a
measure of absolute accuracy, and the difference is not academic: in-gel's
reference carries two RepeatUntil constructs where the document states three.
Page 8 segment 0 says "If the band is still transparent then repeat steps 17-18
until fully dehydrated", the server's own range test confirms it, and the
reference has nothing there. An extraction that finds all three would be
reported as having invented one.

So this tool never reports a single accuracy figure. Differences are reported
per side -- ``in_reference_only`` and ``in_candidate_only`` -- rather than
netted, ``audit_reference`` notes travel beside the score instead of being
folded into it, and every payload says which of the two it is.

    scripts/score_extraction.py SOURCE.pdf \\
        --reference   data/development_protocols/<name>.json \\
        --provenance  data/development_protocols/<name>.provenance.json

Nothing here is specific to one document: the source, the reference and the
chunk plan all come from the arguments and from the planner.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

from voice_workflow_agent.chunk_analysis_cache import (
    ChunkAnalysisCache,
    key_for_chunk,
)
from voice_workflow_agent.curated_protocol import load_curated_protocol_fixture
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ValidatedChunkResult,
    assemble_validated_protocol_claims,
    extraction_for_chunk,
    merge_validated_chunk_results,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    generate_page_evidence_segments,
    prepare_chunk_claim_request_context,
)
from voice_workflow_agent.protocol_extraction_accuracy import (
    audit_reference,
    score_extraction,
)


def _plain(value):
    """JSON-able form of a dataclass tree, without inventing a serializer."""

    if is_dataclass(value) and not isinstance(value, type):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _step_text_of(protocol, label: str) -> str:
    """One step's instruction plus its sub-actions, as the scorer reads it."""

    for section in protocol.sections:
        for step in section.steps:
            if step.source_label != label:
                continue
            parts = [step.instruction_source_text]
            parts.extend(
                action.instruction_source_text for action in step.sub_actions
            )
            return " ".join(part for part in parts if part)
    return ""


def _containment(reference_text: str, candidate_text: str) -> str:
    """Whether the two texts differ by inclusion or by disagreement.

    Text similarity conflates them, and STEP 39 measured the cost: of in-gel's
    five lowest-scoring steps, four had a candidate that contained every word
    of the reference and was marked down for the words it added. One of those
    additions was a section heading the segmenter had fused in; the others were
    notes and catalogue lines the reference chose to leave out. Only step 24
    was missing anything.

    So the two are separated. "candidate_contains_reference" is a step where
    nothing the reference says is absent -- the difference is what to include,
    which is a judgement, not an error. "differs" is a step where each side
    says something the other does not, and that is where an error lives.
    """

    reference_words = " ".join(reference_text.split())
    candidate_words = " ".join(candidate_text.split())
    if reference_words == candidate_words:
        return "identical"
    if reference_words and reference_words in candidate_words:
        return "candidate_contains_reference"
    if candidate_words and candidate_words in reference_words:
        return "reference_contains_candidate"
    left = set(reference_words.split())
    right = set(candidate_words.split())
    if left and left <= right:
        return "candidate_contains_reference"
    if right and right <= left:
        return "reference_contains_candidate"
    return "differs"


def _declared_step_values(protocol):
    """Per step, the values the extraction actually *claims*, by kind.

    The existing scorer reads values out of a step's text, and STEP 38 showed
    what that costs. In-gel's step 24 was reported as claiming a 16-hour
    duration and contradicting the reference's 30 minutes. It claims no
    duration at all: the "16h" is the next section's time estimate, fused into
    step 24's segment by the segmenter, and the text-reading scorer found the
    digits and attributed them.

    So the two are reported side by side. Text-derived values say what an
    operator would be read out; claimed values say what the extraction
    asserts. When they disagree, the disagreement is the finding.
    """

    declared = {}
    for section in protocol.sections:
        for step in section.steps:
            kinds = {"durations": set(), "temperatures": set(), "volumes": set()}
            declared[step.source_label] = kinds
    return declared


def _claimed_values_by_step(merged, label_of_step):
    """Values the merge claims, grouped by the step each one qualifies."""

    from voice_workflow_agent.protocol_claim_analysis import ClaimCategory

    kind_of = {
        ClaimCategory.DURATION: "durations",
        ClaimCategory.TEMPERATURE: "temperatures",
        ClaimCategory.QUANTITY: "volumes",
    }
    by_step: dict[str, dict[str, list[str]]] = {}
    for claim in merged.claims:
        kind = kind_of.get(claim.category)
        if kind is None or claim.step_id is None:
            continue
        label = label_of_step.get(claim.step_id)
        if label is None:
            continue
        by_step.setdefault(label, {}).setdefault(kind, []).append(claim.claim_id)
    return by_step


def _repeats(protocol, label_of):
    """Every repetition construct, as (kind, first label, last label, page)."""

    found = []
    for construct in protocol.constructs:
        ids = getattr(construct, "repeated_step_ids", None)
        if ids is None:
            start = getattr(construct, "start_step_id", None)
            end = getattr(construct, "end_step_id", None)
            ids = tuple(item for item in (start, end) if item)
        if not ids:
            continue
        found.append(
            {
                "kind": type(construct).__name__,
                "labels": [label_of.get(item, item) for item in ids],
                "source_page_number": getattr(
                    getattr(construct, "evidence", None),
                    "source_page_number",
                    None,
                ),
                "repeat_count": getattr(construct, "repeat_count", None),
            }
        )
    return sorted(found, key=lambda item: (item["source_page_number"] or 0))


def _repeat_comparison(reference, candidate, reference_labels, candidate_labels):
    """Which repetitions each side found, matched on the range they name.

    Matched on the labels rather than on identity: the two sides assign their
    own ids, and what matters to an operator is which steps get repeated.
    """

    left = _repeats(reference, reference_labels)
    right = _repeats(candidate, candidate_labels)
    key = lambda item: (tuple(item["labels"]), item["source_page_number"])
    right_by_key = {key(item): item for item in right}
    matched, missed = [], []
    for item in left:
        other = right_by_key.pop(key(item), None)
        (matched if other else missed).append(
            {"reference": item, "candidate": other}
        )
    return {
        "reference_count": len(left),
        "candidate_count": len(right),
        "matched": matched,
        "in_reference_only": missed,
        "in_candidate_only": list(right_by_key.values()),
    }


def _evidence_addresses_resolve(extraction, source_revision, protocol):
    """Every cited segment id must name a segment the server can recompute.

    A claim whose evidence address does not resolve is a claim nobody can check
    against the document, which is worse than a wrong value: a wrong value can
    be seen to be wrong.
    """

    by_page: dict[int, set[str]] = {}
    checked = unresolved = 0
    offenders: list[dict[str, object]] = []

    def visit(evidence, where: str) -> None:
        nonlocal checked, unresolved
        if evidence is None:
            return
        page = getattr(evidence, "source_page_number", None)
        if page is None:
            return
        if page not in by_page:
            try:
                by_page[page] = {
                    segment.segment_id
                    for segment in generate_page_evidence_segments(
                        extraction,
                        source_revision=source_revision,
                        page_number=page,
                    )
                }
            except Exception:  # noqa: BLE001 - an unreadable page resolves nothing
                by_page[page] = set()
        for segment_id in getattr(evidence, "evidence_segment_ids", ()) or ():
            checked += 1
            if segment_id not in by_page[page]:
                unresolved += 1
                if len(offenders) < 20:
                    offenders.append(
                        {"where": where, "page": page, "segment_id": segment_id}
                    )

    for section in protocol.sections:
        for step in section.steps:
            visit(step.evidence, f"step:{step.source_label}")
            for group in ("warnings", "notes", "tips", "expected_results"):
                for statement in getattr(step, group, ()) or ():
                    visit(
                        statement.evidence, f"{group}:{step.source_label}"
                    )
            for action in step.sub_actions:
                visit(action.evidence, f"sub_action:{step.source_label}")
    for construct in protocol.constructs:
        visit(getattr(construct, "evidence", None), "construct")
    return {
        "addresses_checked": checked,
        "addresses_unresolved": unresolved,
        "all_resolve": unresolved == 0,
        "offenders": offenders,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--source-revision", default="pdf-1")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/development_cache/accuracy"),
    )
    arguments = parser.parse_args()

    extraction = extract_protocol_pdf(arguments.source)
    plan = plan_protocol_chunks(
        extraction,
        f"protocol-{extraction.sha256[:32]}",
        arguments.source_revision,
        limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
    )
    cache = ChunkAnalysisCache(arguments.cache_dir)

    validated: list[ValidatedChunkResult] = []
    missing: list[int] = []
    for chunk in plan.chunks:
        scoped = extraction_for_chunk(extraction, chunk)
        request = prepare_chunk_claim_request_context(
            scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
            core_page_refs=chunk.core_page_refs,
            context_page_refs=chunk.overlap_page_refs,
        )
        hit = cache.load(
            key_for_chunk(extraction, chunk, request),
            extraction=scoped,
            request=request,
        )
        if hit is None:
            missing.append(chunk.ordinal)
        else:
            validated.append(ValidatedChunkResult(chunk, hit.analysis))

    if missing:
        print(
            json.dumps(
                {
                    "scored": False,
                    "reason": "cache_incomplete",
                    "chunks_total": len(plan.chunks),
                    "chunks_cached": len(validated),
                    "missing_ordinals": missing,
                    "provider_calls_needed": len(missing),
                    "standing": "regression_baseline_not_absolute_accuracy",
                    "note": (
                        "A merge needs every chunk. Nothing was sent; collect "
                        "the missing ordinals and run this again."
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    merged = merge_validated_chunk_results(extraction, plan, tuple(validated))
    draft = assemble_validated_protocol_claims(extraction, merged)
    candidate = draft.protocol

    fixture = load_curated_protocol_fixture(
        arguments.reference, arguments.provenance, arguments.source
    )
    reference = fixture.draft.protocol

    label_of = lambda protocol: {
        step.step_id: step.source_label
        for section in protocol.sections
        for step in section.steps
    }
    report = score_extraction(
        reference,
        candidate,
        reference_notes=audit_reference(reference, extraction),
    )
    similarities = sorted(item.text_similarity for item in report.compared)
    outcomes: dict[str, int] = {}
    for item in report.compared:
        outcomes[item.value_outcome] = outcomes.get(item.value_outcome, 0) + 1

    incomplete = [
        item.source_page_number
        for item in merged.page_coverage
        if item.status.value == "analysis_incomplete"
    ]
    label_of_step = {
        step.step_id: step.source_label
        for section in candidate.sections
        for step in section.steps
    }
    claimed = _claimed_values_by_step(merged, label_of_step)
    payload = {
        "scored": True,
        # Said in the output, not only in the docstring: a number read out of
        # this file is a comparison against one person's reading of the
        # document, and that reading is known to be incomplete in at least one
        # place. Treat a change as a signal; do not treat the level as a score.
        "standing": "regression_baseline_not_absolute_accuracy",
        "baseline_caveat": (
            "The reference is hand-built and is not ground truth. A difference "
            "may be the extraction's fault or the reference's. See "
            "reference_notes, and in_reference_only/in_candidate_only under "
            "repetitions, which are reported per side and never netted."
        ),
        "source": arguments.source.name,
        "source_sha256": extraction.sha256,
        "reference": arguments.reference.name,
        "steps": {
            "reference": report.reference_steps,
            "candidate": report.candidate_steps,
            "order_matches": report.order_matches,
            "missing_labels": list(report.missing_labels),
            "extra_labels": list(report.extra_labels),
        },
        # Inclusion and disagreement, kept apart. A step whose candidate
        # contains the whole reference has no missing content, whatever its
        # similarity score says; a step under "differs" is where each side has
        # something the other lacks, and that is where to look.
        "text_containment": {
            outcome: sorted(
                item.source_label
                for item in report.compared
                if _containment(
                    _step_text_of(reference, item.source_label),
                    _step_text_of(candidate, item.source_label),
                )
                == outcome
            )
            for outcome in (
                "identical",
                "candidate_contains_reference",
                "reference_contains_candidate",
                "differs",
            )
        },
        "text_similarity": {
            "minimum": similarities[0] if similarities else None,
            "median": (
                similarities[len(similarities) // 2] if similarities else None
            ),
            "maximum": similarities[-1] if similarities else None,
            "lowest_three": [
                {
                    "source_label": item.source_label,
                    "text_similarity": item.text_similarity,
                }
                for item in sorted(
                    report.compared, key=lambda item: item.text_similarity
                )[:3]
            ],
        },
        "values": {
            # Read out of each step's text. Kept because it is what an operator
            # would hear, but it is not what the extraction claims -- see
            # values_claimed below, and _declared_step_values for why.
            "derived_from_step_text": True,
            "outcomes": outcomes,
            "per_step": [
                {
                    "source_label": item.source_label,
                    "outcome": item.value_outcome,
                    "reference": _plain(item.reference_values),
                    "candidate": _plain(item.candidate_values),
                }
                for item in report.compared
                if item.value_outcome != "matching"
            ],
        },
        # What the extraction actually asserts, per step, as claim ids by kind.
        # A step that appears here with no duration claims nothing about time,
        # whatever digits its text happens to contain.
        "values_claimed": {
            label: {kind: len(ids) for kind, ids in sorted(kinds.items())}
            for label, kinds in sorted(claimed.items())
        },
        "steps_claiming_no_value": sorted(
            label
            for label in (
                step.source_label
                for section in candidate.sections
                for step in section.steps
            )
            if label not in claimed
        ),
        "repetitions": _repeat_comparison(
            reference, candidate, label_of(reference), label_of(candidate)
        ),
        "evidence_addresses": _evidence_addresses_resolve(
            extraction, arguments.source_revision, candidate
        ),
        "reading": {
            "pages_total": extraction.page_count,
            "pages_analysis_incomplete": len(incomplete),
            "incomplete_page_numbers": incomplete,
            "unaccounted_segment_total": sum(
                len(item.unaccounted_segment_ids)
                for item in merged.page_coverage
            ),
        },
        # The reference is hand-built. Where it disagrees with the document,
        # that is reported beside the score and never scored against.
        "reference_notes": list(report.reference_notes),
    }

    arguments.out_dir.mkdir(parents=True, exist_ok=True)
    stem = arguments.source.stem
    score_path = arguments.out_dir / f"{stem}.accuracy.json"
    protocol_path = arguments.out_dir / f"{stem}.merged-protocol.json"
    score_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    protocol_path.write_text(
        json.dumps(_plain(candidate), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"(score written to {score_path})", file=sys.stderr)
    print(f"(merged protocol written to {protocol_path})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
