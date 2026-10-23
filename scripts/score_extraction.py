#!/usr/bin/env python3
"""Compare two saved analysis responses against the same source, offline.

Both JSON files use the current analysis response envelope. They are read
through the production parser and evidence checks; no model is called and
no catalog or execution state is opened. The reference is a hand-built
baseline, not ground truth. Missing steps and source records are reported
separately from evidence findings, without an execution verdict.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from voiney_lab.experiment_protocol_analysis import (
    ProtocolAnalysisError,
    parse_protocol_analysis_response,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfError,
    extract_protocol_pdf,
)
from voiney_lab.protocol_extraction_accuracy import (
    audit_reference,
    score_extraction,
    source_record_differences,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Source PDF shared by both analyses")
    parser.add_argument("--reference", type=Path, required=True,
                        help="Hand-built baseline in the current analysis JSON format")
    parser.add_argument("--candidate", type=Path, required=True,
                        help="Saved candidate analysis JSON; never calls a provider")
    parser.add_argument("--output", type=Path,
                        help="Also create this JSON report; an existing file is never overwritten")
    args = parser.parse_args(argv)

    try:
        extraction = extract_protocol_pdf(args.source)
        reference = parse_protocol_analysis_response(
            args.reference.read_text(encoding="utf-8"), extraction, review_unverified=True)
        candidate = parse_protocol_analysis_response(
            args.candidate.read_text(encoding="utf-8"), extraction, review_unverified=True)
        report = score_extraction(
            reference.protocol, candidate.protocol,
            reference_notes=audit_reference(reference.protocol, extraction))
        payload = {
            "scored": True,
            "standing": "regression_baseline_not_absolute_accuracy",
            "source_sha256": extraction.sha256,
            "source_pages": extraction.page_count,
            "source_checks": {
                "reference_items_for_review": len(reference.review_flags),
                "candidate_items_for_review": len(candidate.review_flags),
            },
            **report.public_dict(),
            "steps": [
                {
                    "source_key": item.source_key,
                    "source_label": item.source_label,
                    "text_similarity": round(item.text_similarity, 4),
                    "value_outcome": item.value_outcome,
                    "reference_values": item.reference_values,
                    "candidate_values": item.candidate_values,
                }
                for item in report.compared
            ],
            "source_records": source_record_differences(reference.protocol, candidate.protocol),
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
    except (OSError, UnicodeError, ValueError, ProtocolAnalysisError, ProtocolPdfError) as exc:
        # No document text, provider exception body, path or credential is
        # copied into the failure report.
        print(json.dumps({"scored": False, "reason": type(exc).__name__}))
        return 2
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
