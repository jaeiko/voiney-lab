"""A bounded repetition does not execute on the model's word.

The two ways of getting a repetition's kind wrong are not symmetric. Calling a
conditional repetition fixed makes the agent stop early and announce
completion while the source's own condition is unmet -- a false completion
notice, the worst outcome this system can produce. Calling a fixed repetition
conditional only makes it ask a person.

So the safe direction is the default: a fixed repetition is recorded with
``unconfirmed_fixed_repetition`` so the count stays marked as the model's
reading. Under the MVP rule (lane DI, 2026-10-08) that reason is a notice,
not a gate: lane CB guides the rounds from the source's count and tells the
experimenter the count's origin. Nothing quietly downgrades a fixed
repetition to a conditional one either -- that would be another guess.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf

_PAGE = (
    "Protocol Repetition\nSection preparation\n1. Wash the pellet.\n"
    "2. Repeat steps 1-1 twice more.\n"
)
_GATE = domain.ReadinessReasonCode.NO_DECLARED_SAFETY_WARNINGS.value
_REPEAT_GATE = domain.ReadinessReasonCode.UNCONFIRMED_FIXED_REPETITION.value


class ClassifyingBoundedUnblocksTheUnsupportedShapeTests(unittest.TestCase):
    """A provider that says "fixed" removes the unsupported blocker.

    This is what the vocabulary was added for. A statement classified as
    fixed_range_repetition assembles into the construct P1 supports, so
    `unsupported_repeat_until` is gone and only the confirmation gate remains.
    The offline fixture cannot make this choice -- it does not read prose and
    always says repeat_condition, the safe direction -- so a document whose
    repetitions are all bounded still blocks until a real model classifies
    them.
    """

    def test_a_bounded_classification_assembles_and_leaves_one_gate(self) -> None:
        import json

        from tests.test_protocol_claim_analysis import write_lined_pages
        from voiney_lab.protocol_claim_analysis import (
            CLAIM_SCHEMA_VERSION,
            parse_chunk_claim_response,
            prepare_chunk_claim_request_context,
        )
        from voiney_lab.protocol_chunk_analysis import (
            ChunkAnalysisLimits,
            ValidatedChunkResult,
            assemble_validated_protocol_claims,
            extraction_for_chunk,
            merge_validated_chunk_results,
            plan_protocol_chunks,
        )

        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "fixed.pdf"
            write_lined_pages(
                pdf,
                (
                    (
                        "Protocol Fixed",
                        "Section wash",
                        "1. Wash the pellet with 10 mL buffer.",
                        "2. Repeat steps 1-1 twice more.",
                    ),
                ),
            )
            extraction = extract_protocol_pdf(pdf)
            plan = plan_protocol_chunks(
                extraction,
                f"protocol-{extraction.sha256[:32]}",
                "pdf-1",
                limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
            )
            chunk = plan.chunks[0]
            scoped = extraction_for_chunk(extraction, chunk)
            request = prepare_chunk_claim_request_context(
                scoped,
                source_revision=chunk.candidate_revision_id,
                chunk_id=chunk.chunk_id,
                ordinal=chunk.ordinal,
                core_page_refs=chunk.core_page_refs,
                context_page_refs=chunk.overlap_page_refs,
            )
            handles = {
                item.segment.text: item.handle
                for page in request.pages
                for item in page.evidence
            }
            find = lambda needle: [
                handle
                for text, handle in handles.items()
                if needle in text
            ]
            def claim(claim_id, category, needle, order, **kwargs):
                return {
                    "claim_id": claim_id,
                    "category": category,
                    "source_order": order,
                    "section_id": "section-1",
                    "step_id": kwargs.get("step_id"),
                    "source_label": kwargs.get("source_label"),
                    "target_claim_id": kwargs.get("target"),
                    "required_for_execution": category == "action",
                    "repeated_step_labels": kwargs.get("labels"),
                    "repetition_count": kwargs.get("count"),
                    "evidence": {
                        "source_page_number": 1,
                        "evidence_segment_ids": find(needle),
                    },
                }

            claims = [
                claim("action-1", "action", "Wash the pellet", 1,
                      step_id="step-1", source_label="1"),
                claim("action-2", "action", "Repeat steps 1-1", 2,
                      step_id="step-2", source_label="2"),
                claim("fixed-1", "fixed_range_repetition", "Repeat steps 1-1",
                      3, step_id="step-2", target="action-2",
                      labels=["1", "1"], count=2),
            ]
            title = find("Protocol Fixed")
            structure = [
                {
                    "marker_id": "protocol-title",
                    "kind": "protocol_title",
                    "source_order": 0,
                    "section_id": None,
                    "evidence": {
                        "source_page_number": 1,
                        "evidence_segment_ids": title,
                    },
                },
                {
                    "marker_id": "marker-1",
                    "kind": "section",
                    "source_order": 1,
                    "section_id": "section-1",
                    "evidence": {
                        "source_page_number": 1,
                        "evidence_segment_ids": title,
                    },
                },
            ]
            cited = {
                handle
                for record in claims
                for handle in record["evidence"]["evidence_segment_ids"]
            } | set(title)
            payload = {
                "claim_schema_version": CLAIM_SCHEMA_VERSION,
                "capability_policy_id": "p1-conservative",
                "request_handle": request.request_handle,
                "page_coverage": [
                    {
                        "source_page_number": 1,
                        "analysis_incomplete": False,
                        "declined_evidence_segment_ids": [
                            handle
                            for handle in handles.values()
                            if handle not in cited
                        ],
                    }
                ],
                "structure": structure,
                "claims": claims,
            }
            analysis = parse_chunk_claim_response(
                json.dumps(payload),
                extraction=scoped,
                source_revision=chunk.candidate_revision_id,
                chunk_id=chunk.chunk_id,
                core_page_refs=chunk.core_page_refs,
                request=request,
            )
            merged = merge_validated_chunk_results(
                extraction, plan, (ValidatedChunkResult(chunk, analysis),)
            )
            protocol = assemble_validated_protocol_claims(
                extraction, merged
            ).protocol

        self.assertEqual(len(protocol.constructs), 1)
        construct = protocol.constructs[0]
        self.assertIsInstance(construct, domain.FixedRangeRepetition)
        self.assertEqual(construct.repeat_count, 2)
        self.assertEqual(construct.start_step_id, "step-1")
        self.assertEqual(construct.end_step_id, "step-1")
        readiness = domain.assess_readiness(protocol)
        self.assertEqual(
            sorted(set(readiness.reason_codes)), [_GATE, _REPEAT_GATE]
        )
        self.assertNotIn(
            domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL.value,
            readiness.reason_codes,
        )


if __name__ == "__main__":
    unittest.main()
