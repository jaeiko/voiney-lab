"""Derive the step-timer manifest from the source, for human review.

A build-time tool, not a runtime path. It locates each timer's duration inside
the step's own text, records the exact literal with the canonical segment that
holds it, and binds the result to the document and fixture digests -- the same
shape the visuals manifest uses. The loader then verifies every field against
the source and refuses anything it cannot confirm.

Run it, read the output, and commit the file. Nothing parses a duration at
runtime except to check that the literal the manifest cites really states the
number the manifest claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_claim_analysis import (
    generate_page_evidence_segments,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
SOURCE = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"
OUT = ROOT / "data/development_protocols/candidate_a_curated_analysis.timers.json"
STATUS = "development_only_not_final_acceptance"
_CLOCK = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")
_WORDED = re.compile(r"(\d{1,3})\s*(min|h)\b", re.I)


def duration_seconds(literal: str) -> int | None:
    """The seconds a source literal states, in the two forms the source uses."""

    clock = _CLOCK.fullmatch(literal.strip())
    if clock is not None:
        hours, minutes, seconds = (int(part) for part in clock.groups())
        return hours * 3600 + minutes * 60 + seconds
    worded = _WORDED.fullmatch(literal.strip())
    if worded is not None:
        count, unit = int(worded.group(1)), worded.group(2).lower()
        return count * 60 if unit == "min" else count * 3600
    return None


def main() -> int:
    from voice_workflow_agent.curated_protocol import (
        _CANDIDATE_A_STEP_TIMERS as table,
    )

    payload = json.loads(FIXTURE.read_text())
    steps = [
        step
        for section in payload["protocol"]["sections"]
        for step in section["steps"]
    ]
    extraction = extract_protocol_pdf(SOURCE)
    fixture_sha256 = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()

    def anchor(index: int) -> int:
        return steps[index]["evidence"]["source_page_number"]

    candidates = []
    unfounded = []
    for index in sorted(key for key, value in table.items() if value):
        seconds = table[index]
        step = steps[index]
        last = anchor(index + 1) if index + 1 < len(steps) else extraction.page_count
        hit = None
        for page in range(anchor(index), last + 1):
            text = extraction.pages[page - 1].text
            if page == anchor(index):
                start = text.find(f"\n{step['source_label']} ")
                start = 0 if start < 0 else start
                following = re.search(
                    r"\n[1-9][0-9]{0,2}[ .)]", text[start + 1 :]
                )
                stop = start + 1 + following.start() if following else len(text)
            else:
                following = re.search(r"\n[1-9][0-9]{0,2}[ .)]", text)
                start, stop = 0, following.start() if following else len(text)
            window = text[start:stop]
            for pattern in (_CLOCK, _WORDED):
                for match in pattern.finditer(window):
                    if duration_seconds(match.group(0)) == seconds:
                        hit = (page, start + match.start(), match.group(0))
                        break
                if hit:
                    break
            if hit:
                break
        if hit is None:
            unfounded.append(
                {"step_id": step["step_id"], "seconds": seconds}
            )
            continue
        page, offset, literal = hit
        segments = generate_page_evidence_segments(
            extraction, source_revision="pdf-1", page_number=page
        )
        cursor = 0
        holder = None
        for segment in segments:
            if cursor <= offset < cursor + len(segment.text):
                holder = segment
                break
            cursor += len(segment.text)
        if holder is None:
            unfounded.append(
                {"step_id": step["step_id"], "seconds": seconds,
                 "reason": "no segment holds the literal"}
            )
            continue
        candidates.append(
            {
                "linked_step_id": step["step_id"],
                "page_number": page,
                "step_anchor_page": anchor(index),
                "duration_seconds": seconds,
                "source_literal": literal,
                "evidence_segment_ids": [holder.segment_id],
                "confidence": "verified",
            }
        )

    manifest = {
        "version": 1,
        "document_sha256": extraction.sha256,
        "fixture_sha256": fixture_sha256,
        "status": STATUS,
        "candidates": candidates,
    }
    # Written in exactly the canonical form the loader recomputes, so a byte
    # of drift in the committed file is itself a load failure.
    from voice_workflow_agent.curated_protocol import _canonical_json_bytes

    OUT.write_bytes(_canonical_json_bytes(manifest))
    print(f"wrote {OUT.name}: {len(candidates)} verified, {len(unfounded)} unfounded")
    for item in unfounded:
        print("  UNFOUNDED:", item)
    for item in candidates:
        cross = (
            "" if item["page_number"] == item["step_anchor_page"]
            else f"  (continuation page, anchor p{item['step_anchor_page']})"
        )
        print(f"  {item['linked_step_id']:22} p{item['page_number']} "
              f"{item['duration_seconds']:>6}s  {item['source_literal']!r}{cross}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
