"""The six-step loss from the first real use, fixed as input.

STEP 20, headspace chunk 2, pages 6-8: a model returned page coverage
disposing of labels 22, 30, 31, 32, 33, 34 as "not execution steps". Every one
is an imperative carrying a temperature, a time or a piece of equipment:

    p6 22  Incubate the Petri dishes in total darkness (48 h at 28 C).
    p7 30  Attach the charcoal filter tube to a supply of purified nitrogen
    p7 31  Check for a flow of nitrogen by attaching a Teflon tube
    p7 32  Place the charcoal filter into a modified heating oven at 170 C
    p8 33  After 2 hours, remove the charcoal filter from the oven
    p8 34  Once the charcoal filters have cooled, turn off the nitrogen supply

Had that response been accepted and approved, the protocol would have been
missing six steps with nothing in the extraction saying so. These tests fix
that input and assert the accident is no longer expressible.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisResponseError,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_SCHEMA_VERSION,
    _numbered_step_labels,
    parse_chunk_claim_response,
    prepare_chunk_claim_request_context,
)
from voice_workflow_agent.protocol_chunk_analysis import (
    ChunkAnalysisLimits,
    extraction_for_chunk,
    plan_protocol_chunks,
)

HEADSPACE = Path("usingdynamicheadspacecollections.pdf")
HEADSPACE_SHA256 = (
    "2bf102779364ec2dad517efc5acff7c5b6a5b569465e708f68186d7415c46fa2"
)
# The labels the model disposed of, with the page each is printed on.
LOST_LABELS = {6: ("22",), 7: ("30", "31", "32"), 8: ("33", "34")}


def _headspace():
    if not HEADSPACE.is_file():
        raise unittest.SkipTest(
            f"{HEADSPACE.name} is not present in the working tree; it is a "
            f"5.1 MB source deliberately not committed. Expected sha256 "
            f"{HEADSPACE_SHA256}."
        )
    extraction = extract_protocol_pdf(HEADSPACE)
    if extraction.sha256 != HEADSPACE_SHA256:
        raise unittest.SkipTest(
            f"{HEADSPACE.name} is a different file: {extraction.sha256}."
        )
    return extraction


class TheLostLabelsAreRealStepsTests(unittest.TestCase):
    """First, that the input is what the report said it was."""

    def test_every_lost_label_is_printed_on_its_page(self) -> None:
        extraction = _headspace()
        for page, labels in LOST_LABELS.items():
            printed = _numbered_step_labels(extraction.pages[page - 1].text)
            for label in labels:
                with self.subTest(page=page, label=label):
                    self.assertIn(label, printed)

    def test_they_are_six_distinct_labels(self) -> None:
        self.assertEqual(
            sum(len(labels) for labels in LOST_LABELS.values()), 6
        )


class TheAccidentIsNoLongerExpressibleTests(unittest.TestCase):
    def _chunk_for_page(self, extraction, page):
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
        )
        return next(
            chunk for chunk in plan.chunks if page in chunk.core_page_refs
        )

    def _response(self, extraction, chunk, *, dispose):
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
            page.source_page_number: [
                item.handle for item in page.evidence
            ]
            for page in request.pages
        }
        coverage = []
        for page in chunk.core_page_refs:
            record = {
                "source_page_number": page,
                "analysis_incomplete": False,
                "declined_evidence_segment_ids": handles.get(page, []),
            }
            if dispose and page in LOST_LABELS:
                record["non_step_labels"] = [
                    {
                        "source_label": label,
                        "evidence_segment_ids": handles[page][:1],
                    }
                    for label in LOST_LABELS[page]
                ]
            coverage.append(record)
        payload = {
            "claim_schema_version": CLAIM_SCHEMA_VERSION,
            "capability_policy_id": "p1-conservative",
            "request_handle": request.request_handle,
            "page_coverage": coverage,
            "structure": [],
            "claims": [],
        }
        return scoped, request, json.dumps(payload)

    def _parse(self, extraction, chunk, raw, request, scoped):
        return parse_chunk_claim_response(
            raw,
            extraction=scoped,
            source_revision=chunk.candidate_revision_id,
            chunk_id=chunk.chunk_id,
            core_page_refs=chunk.core_page_refs,
            request=request,
        )

    def test_a_response_disposing_of_them_is_refused_as_a_violation(
        self,
    ) -> None:
        """Not ignored: a provider must not believe it was honoured."""

        extraction = _headspace()
        chunk = self._chunk_for_page(extraction, 6)
        scoped, request, raw = self._response(
            extraction, chunk, dispose=True
        )
        with self.assertRaises(ProtocolAnalysisResponseError) as caught:
            self._parse(extraction, chunk, raw, request, scoped)
        self.assertEqual(
            caught.exception.diagnostic.reason_code,
            "label_disposition_not_accepted",
        )
        self.assertEqual(
            caught.exception.diagnostic.mismatch_class,
            "semantic_contract_violation",
        )

    def test_the_same_response_without_dispositions_fails_differently(
        self,
    ) -> None:
        """The obligation still catches the missing steps themselves.

        With the disposition removed the response claims nothing at all, so it
        is refused for omitting the numbered actions -- which is the guarantee
        that must survive removing the disposition route.
        """

        extraction = _headspace()
        chunk = self._chunk_for_page(extraction, 6)
        scoped, request, raw = self._response(
            extraction, chunk, dispose=False
        )
        with self.assertRaises(Exception) as caught:
            self._parse(extraction, chunk, raw, request, scoped)
        reason = getattr(
            getattr(caught.exception, "diagnostic", None), "reason_code", None
        )
        self.assertNotEqual(reason, "label_disposition_not_accepted")
        self.assertIsNotNone(reason)


class TheNumberedObligationStillBitesTests(unittest.TestCase):
    """Removing the disposition route must not weaken the obligation."""

    def test_a_label_absent_from_the_claims_still_fails(self) -> None:
        extraction = _headspace()
        plan = plan_protocol_chunks(
            extraction,
            f"protocol-{extraction.sha256[:32]}",
            "pdf-1",
            limits=ChunkAnalysisLimits(max_concurrency=1, max_retries=0),
        )
        chunk = next(
            chunk for chunk in plan.chunks if 6 in chunk.core_page_refs
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
        handles = {
            page.source_page_number: [item.handle for item in page.evidence]
            for page in request.pages
        }
        payload = {
            "claim_schema_version": CLAIM_SCHEMA_VERSION,
            "capability_policy_id": "p1-conservative",
            "request_handle": request.request_handle,
            "page_coverage": [
                {
                    "source_page_number": page,
                    "analysis_incomplete": False,
                    "declined_evidence_segment_ids": handles.get(page, []),
                }
                for page in chunk.core_page_refs
            ],
            "structure": [],
            "claims": [],
        }
        with self.assertRaises(Exception) as caught:
            parse_chunk_claim_response(
                json.dumps(payload),
                extraction=scoped,
                source_revision=chunk.candidate_revision_id,
                chunk_id=chunk.chunk_id,
                core_page_refs=chunk.core_page_refs,
                request=request,
            )
        self.assertIsNotNone(caught.exception)


if __name__ == "__main__":
    unittest.main()
