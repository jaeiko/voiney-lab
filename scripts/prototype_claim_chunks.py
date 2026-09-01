#!/usr/bin/env python3
"""Offline claim-chunk prototype over local text-native laboratory PDFs.

This is deliberately a deterministic provider fake.  It measures extraction,
planning, exact-evidence validation, merge, and domain assembly without making
or pretending to make a live provider call.  Live provider latency remains an
explicit separate gate.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    extract_protocol_pdf,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    ValidatedChunkResult,
    analyze_protocol_chunk,
    assemble_validated_protocol_claims,
    merge_validated_chunk_results,
    plan_protocol_chunks,
)
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_RESPONSE_SCHEMA,
    CLAIM_SCHEMA_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANKOM = (
    ROOT
    / "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    / "5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)
DEFAULT_MULTI_PAGE = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"
_STEP = re.compile(
    r"(?ms)^(?P<label>[1-9][0-9]{0,2})(?:[.)])?[ \t]+(?P<body>.*?)"
    r"(?=^[1-9][0-9]{0,2}(?:[.)])?[ \t]+|^protocols\.io|\Z)"
)
_PARAMETERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "concentration",
        re.compile(
            r"(?<![A-Za-z0-9])(?:[0-9]+(?:\.[0-9]+)?\s*%|"
            r"[0-9]+(?:\.[0-9]+)?\s*(?:mM|millimolar|mg/mL|ng/uL))"
        ),
    ),
    (
        "temperature",
        re.compile(r"(?<![A-Za-z0-9])[0-9]+(?:\s*[±]\s*[0-9]+)?\s*°?C\b"),
    ),
    (
        "duration",
        re.compile(
            r"(?<![A-Za-z0-9])(?:[0-9]+(?:\.[0-9]+)?\s*(?:h|min|s)\b|"
            r"[0-9]{2}[0-9]{2}[0-9]{2})"
        ),
    ),
    (
        "agitation_speed",
        re.compile(r"(?<![A-Za-z0-9])[0-9]+(?:\.[0-9]+)?\s*rpm\b"),
    ),
    (
        "quantity",
        re.compile(
            r"(?<![A-Za-z0-9])[0-9]+(?:\.[0-9]+)?\s*"
            r"(?:mL|ml|µL|uL|g|mg|L)\b"
        ),
    ),
)


def _exact_metadata_title(extraction: ProtocolPdfExtraction) -> str:
    target = extraction.metadata.title
    if target:
        pattern = r"\s+".join(re.escape(token) for token in target.split())
        for page in extraction.pages:
            match = re.search(pattern, page.text)
            if match is not None:
                return match.group(0)
    for page in extraction.pages:
        for line in page.text.splitlines():
            if line.strip():
                return line
    raise ValueError("Protocol source has no title evidence.")


def _title_page(extraction: ProtocolPdfExtraction, title: str) -> int:
    return next(
        page.source_page_number
        for page in extraction.pages
        if title in page.text
    )


class ExactNumberedStepClaimModel:
    """Public-source test double; not a semantic completeness claim."""

    def __init__(self, extraction: ProtocolPdfExtraction) -> None:
        self.extraction = extraction
        self.title = _exact_metadata_title(extraction)
        self.title_page = _title_page(extraction, self.title)

    @staticmethod
    def _evidence(
        page: dict[str, object],
        excerpt: str,
    ) -> dict[str, object]:
        segments = page["segments"]
        if not isinstance(segments, list):
            raise ValueError("Provider page has invalid evidence segments.")
        page_text = "".join(str(segment[1]) for segment in segments)
        start = page_text.index(excerpt)
        end = start + len(excerpt)
        selected: list[str] = []
        offset = 0
        for segment in segments:
            segment_text = str(segment[1])
            segment_end = offset + len(segment_text)
            if segment_end > start and offset < end:
                selected.append(str(segment[0]))
            offset = segment_end
        return {
            "source_page_number": page["source_page_number"],
            "evidence_segment_ids": selected,
        }

    def analyze(
        self,
        *,
        system_prompt: str,
        input_json: str,
        response_schema: dict[str, Any],
    ) -> str:
        del system_prompt
        if response_schema != CLAIM_RESPONSE_SCHEMA:
            raise ValueError("Prototype received the full Protocol schema.")
        request = json.loads(input_json)
        structure: list[dict[str, object]] = []
        claims: list[dict[str, object]] = []
        coverage: list[dict[str, object]] = []
        core_pages = [
            page for page in request["pages"] if page["role"] == "core"
        ]
        for page in core_pages:
            page_number = page["source_page_number"]
            page_text = "".join(str(segment[1]) for segment in page["segments"])
            item_ids: list[str] = []
            if page_number == self.title_page:
                title_evidence = self._evidence(page, self.title)
                structure.extend(
                    (
                        {
                            "marker_id": "protocol-title",
                            "kind": "protocol_title",
                            "source_order": 0,
                            "source_text": self.title,
                            "section_id": None,
                            "evidence": title_evidence,
                        },
                        {
                            "marker_id": "marker-protocol-steps",
                            "kind": "section",
                            "source_order": 1,
                            "source_text": self.title,
                            "section_id": "section-protocol-steps",
                            "evidence": title_evidence,
                        },
                    )
                )
                item_ids.extend(("protocol-title", "marker-protocol-steps"))
            skip_intro = self.extraction.page_count > 2 and page_number <= 2
            if not skip_intro:
                for action_index, match in enumerate(_STEP.finditer(page_text)):
                    excerpt = match.group(0).strip()
                    label = match.group("label")
                    body = match.group("body").lstrip()
                    if (
                        len(excerpt) < 12
                        or "DOI:" in excerpt
                        or re.match(
                            r"^(?:µL|uL|mL|ml|L|g|mg|rpm|°C|C)\b",
                            body,
                        )
                    ):
                        continue
                    action_id = f"action-p{page_number}-{action_index}"
                    step_id = f"step-{label}"
                    action_evidence = self._evidence(page, excerpt)
                    claims.append(
                        {
                            "claim_id": action_id,
                            "category": "action",
                            "source_order": 10 + action_index * 20,
                            "source_text": excerpt,
                            "section_id": "section-protocol-steps",
                            "step_id": step_id,
                            "source_label": label,
                            "target_claim_id": None,
                            "required_for_execution": True,
                            "evidence": action_evidence,
                        }
                    )
                    item_ids.append(action_id)
                    parameter_index = 0
                    seen: set[tuple[str, str]] = set()
                    for category, pattern in _PARAMETERS:
                        for parameter in pattern.finditer(excerpt):
                            source_text = parameter.group(0)
                            identity = (category, source_text)
                            if identity in seen:
                                continue
                            seen.add(identity)
                            claim_id = (
                                f"{category}-p{page_number}-{action_index}-{parameter_index}"
                            )
                            parameter_index += 1
                            claims.append(
                                {
                                    "claim_id": claim_id,
                                    "category": category,
                                    "source_order": 11 + action_index * 20 + parameter_index,
                                    "source_text": source_text,
                                    "section_id": "section-protocol-steps",
                                    "step_id": step_id,
                                    "source_label": None,
                                    "target_claim_id": action_id,
                                    "required_for_execution": False,
                                    "evidence": action_evidence,
                                }
                            )
                            item_ids.append(claim_id)
                    repeat = re.search(
                        r"(?is)\brepeat\b[^.\n]*(?:\.|$)",
                        excerpt,
                    )
                    if repeat is not None:
                        claim_id = f"repeat-p{page_number}-{action_index}"
                        claims.append(
                            {
                                "claim_id": claim_id,
                                "category": "repeat_condition",
                                "source_order": 29 + action_index * 20,
                                "source_text": repeat.group(0).strip(),
                                "section_id": "section-protocol-steps",
                                "step_id": step_id,
                                "source_label": None,
                                "target_claim_id": action_id,
                                "required_for_execution": True,
                                "evidence": action_evidence,
                            }
                        )
                        item_ids.append(claim_id)
            coverage.append(
                {
                    "source_page_number": page_number,
                    "status": "complete" if item_ids else "no_relevant_claims",
                    "evidence_item_ids": item_ids,
                }
            )
        return json.dumps(
            {
                "claim_schema_version": CLAIM_SCHEMA_VERSION,
                "capability_policy_id": "p1-conservative",
                "request_handle": request["request_handle"],
                "page_coverage": coverage,
                "structure": structure,
                "claims": claims,
            },
            separators=(",", ":"),
        )


def _write_short_protocol(path: Path) -> None:
    lines = (
        "Protocol Evidence",
        "Preparation",
        "1. Add 10 mL buffer and incubate at 37 C for 15 min.",
    )
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    operations = ["BT /F1 9 Tf 36 740 Td"]
    for index, line in enumerate(lines):
        if index:
            operations.append("0 -14 Td")
        operations.append(f"({line}) Tj")
    operations.append("ET")
    stream.set_data(" ".join(operations).encode("ascii"))
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Protocol Evidence"})
    with path.open("wb") as target:
        writer.write(target)


def run_source(path: Path, concurrency: int) -> dict[str, object]:
    extraction_started = time.monotonic()
    extraction = extract_protocol_pdf(path)
    extraction_seconds = time.monotonic() - extraction_started
    limits = ChunkAnalysisLimits(max_concurrency=concurrency, max_retries=0)
    protocol_id = f"protocol-{extraction.sha256[:32]}"
    plan = plan_protocol_chunks(
        extraction,
        protocol_id,
        "pdf-1",
        limits=limits,
    )
    model = ExactNumberedStepClaimModel(extraction)
    analysis_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        analyses = tuple(
            executor.map(
                lambda chunk: ValidatedChunkResult(
                    chunk,
                    analyze_protocol_chunk(extraction, chunk, model),
                ),
                plan.chunks,
            )
        )
    merged = merge_validated_chunk_results(extraction, plan, analyses)
    draft = assemble_validated_protocol_claims(extraction, merged)
    analysis_seconds = time.monotonic() - analysis_started
    action_count = sum(
        claim.category.value == "action" for claim in merged.claims
    )
    return {
        "filename": path.name,
        "source_sha256": extraction.sha256,
        "source_bytes": extraction.byte_size,
        "source_pages": extraction.page_count,
        "extracted_text_bytes": sum(
            len(page.text.encode("utf-8")) for page in extraction.pages
        ),
        "chunk_count": len(plan.chunks),
        "configured_concurrency": concurrency,
        "claim_count": len(merged.claims),
        "action_count": action_count,
        "all_required_chunks_valid": len(analyses) == len(plan.chunks),
        "exact_evidence_validated": True,
        "readiness_status": draft.readiness.status.value,
        "readiness_reason_codes": list(draft.readiness.reason_codes),
        "extraction_seconds": round(extraction_seconds, 6),
        "claim_pipeline_seconds": round(analysis_seconds, 6),
        "total_local_seconds": round(extraction_seconds + analysis_seconds, 6),
        "provider_mode": "deterministic_offline_fixture",
        "operational_provider_latency_validated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ankom", type=Path, default=DEFAULT_ANKOM)
    parser.add_argument("--multi-page", type=Path, default=DEFAULT_MULTI_PAGE)
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=1)
    arguments = parser.parse_args()
    missing = [
        str(path)
        for path in (arguments.ankom, arguments.multi_page)
        if not path.is_file()
    ]
    if missing:
        raise SystemExit("Required local prototype source is missing: " + ", ".join(missing))
    with tempfile.TemporaryDirectory() as temporary:
        short = Path(temporary) / "short-text-native-protocol.pdf"
        _write_short_protocol(short)
        results = [
            run_source(arguments.ankom, arguments.concurrency),
            run_source(short, arguments.concurrency),
            run_source(arguments.multi_page, arguments.concurrency),
        ]
    print(
        json.dumps(
            {
                "prototype": "evidence_first_claim_chunks",
                "concurrency_production_default": 1,
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
