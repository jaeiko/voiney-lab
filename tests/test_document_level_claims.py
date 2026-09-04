"""Claims for content that sits outside every numbered step."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_protocol_claim_analysis import write_lined_pages
from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_claim_analysis import (
    CLAIM_SCHEMA_VERSION,
    ClaimCategory,
    ClaimSourceEvidence,
    MergedProtocolClaims,
    PageCoverageStatus,
    ProtocolClaim,
    ProtocolClaimConsistencyError,
    ProtocolPageClaimCoverage,
    ProtocolStructureMarker,
    StructureMarkerKind,
    assemble_experiment_protocol,
    generate_page_evidence_segments,
    validate_whole_protocol_claims,
)

REVISION = "pdf-1"

PAGE = (
    "Before start samples are dried for 72 h at 65 degrees C.",
    "50 Prepare the acid solution.",
    "Safety information",
    "Danger, highly corrosive.",
    "Use a cylinder to measure 242 ml of water.",
)


class DocumentLevelClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        path = Path(self._temp.name) / "page.pdf"
        write_lined_pages(path, (PAGE,))
        self.extraction = extract_protocol_pdf(path)
        self.segments = generate_page_evidence_segments(
            self.extraction, source_revision=REVISION, page_number=1
        )
        self.page_text = self.extraction.pages[0].text

    def _evidence(self, index: int) -> ClaimSourceEvidence:
        segment = self.segments[index]
        return ClaimSourceEvidence(
            source_revision=REVISION,
            source_sha256=self.extraction.sha256,
            source_page_number=1,
            page_text_sha256=hashlib.sha256(
                self.page_text.encode("utf-8")
            ).hexdigest(),
            evidence_segment_ids=(segment.segment_id,),
            source_excerpt=segment.text,
        )

    def _index_of(self, needle: str) -> int:
        return next(
            index
            for index, segment in enumerate(self.segments)
            if needle in segment.text
        )

    def _baseline(self):
        """A valid single-step protocol the claim under test is added to."""

        step_index = self._index_of("50 Prepare the acid")
        title = ProtocolStructureMarker(
            marker_id="protocol-title",
            kind=StructureMarkerKind.PROTOCOL_TITLE,
            source_order=0,
            source_text=self.segments[step_index].text,
            section_id=None,
            evidence=self._evidence(step_index),
        )
        section = ProtocolStructureMarker(
            marker_id="marker-steps",
            kind=StructureMarkerKind.SECTION,
            source_order=1,
            source_text=self.segments[step_index].text,
            section_id="section-steps",
            evidence=self._evidence(step_index),
        )
        action = ProtocolClaim(
            claim_id="action-50",
            category=ClaimCategory.ACTION,
            source_order=10,
            source_text=self.segments[step_index].text,
            section_id="section-steps",
            step_id="step-50",
            source_label="50",
            target_claim_id=None,
            required_for_execution=True,
            evidence=self._evidence(step_index),
        )
        coverage = ProtocolPageClaimCoverage(
            source_revision=REVISION,
            source_sha256=self.extraction.sha256,
            source_page_number=1,
            page_text_sha256=hashlib.sha256(
                self.page_text.encode("utf-8")
            ).hexdigest(),
            status=PageCoverageStatus.COMPLETE,
            evidence_item_ids=("action-50", "marker-steps", "protocol-title"),
        )
        return MergedProtocolClaims(
            protocol_id="protocol-1",
            source_revision=REVISION,
            source_sha256=self.extraction.sha256,
            capability_policy_id=domain.P1_CAPABILITY_POLICY.profile_id,
            required_chunk_ids=("chunk-1",),
            page_coverage=(coverage,),
            structure=(title, section),
            claims=(action,),
        )

    def _claim(
        self,
        category: ClaimCategory,
        index: int,
        *,
        target: str | None,
        scoped: bool,
        claim_id: str = "claim-under-test",
    ) -> ProtocolClaim:
        return ProtocolClaim(
            claim_id=claim_id,
            category=category,
            source_order=500,
            source_text=self.segments[index].text,
            section_id="section-steps" if scoped else None,
            step_id="step-50" if scoped else None,
            source_label=None,
            target_claim_id=target,
            required_for_execution=category
            is ClaimCategory.WARNING_HAZARD,
            evidence=self._evidence(index),
        )

    def _validate(self, claim: ProtocolClaim):
        merged = self._baseline()
        merged = replace(
            merged,
            claims=(*merged.claims, claim),
            page_coverage=(
                replace(
                    merged.page_coverage[0],
                    evidence_item_ids=tuple(
                        sorted(
                            (*merged.page_coverage[0].evidence_item_ids,
                             claim.claim_id)
                        )
                    ),
                ),
            ),
        )
        return validate_whole_protocol_claims(self.extraction, merged), merged

    def test_the_page_puts_the_before_start_line_outside_every_step(self) -> None:
        outside = self._index_of("Before start")
        inside = self._index_of("242 ml")
        self.assertLess(outside, self._index_of("50 Prepare the acid"))
        self.assertGreater(inside, self._index_of("50 Prepare the acid"))

    def test_a_value_outside_every_step_needs_no_target(self) -> None:
        """The 72 h / 65 degrees C case: a document-level condition."""

        claim = self._claim(
            ClaimCategory.TEMPERATURE,
            self._index_of("Before start"),
            target=None,
            scoped=False,
        )
        merged, _ = self._validate(claim)
        self.assertIn(claim, merged.claims)

    def test_a_value_inside_a_step_still_needs_a_target(self) -> None:
        """A provider cannot claim document-level status to dodge a target."""

        claim = self._claim(
            ClaimCategory.QUANTITY,
            self._index_of("242 ml"),
            target=None,
            scoped=False,
        )
        with self.assertRaises(ProtocolClaimConsistencyError) as caught:
            self._validate(claim)
        self.assertEqual(caught.exception.reason_code, "claim_target_invalid")

    def test_a_document_level_value_carries_no_step_scoping(self) -> None:
        claim = self._claim(
            ClaimCategory.TEMPERATURE,
            self._index_of("Before start"),
            target=None,
            scoped=True,
        )
        with self.assertRaises(ProtocolClaimConsistencyError) as caught:
            self._validate(claim)
        self.assertEqual(
            caught.exception.reason_code, "document_level_claim_scope_invalid"
        )

    def test_a_value_inside_a_step_may_target_that_step(self) -> None:
        claim = self._claim(
            ClaimCategory.QUANTITY,
            self._index_of("242 ml"),
            target="action-50",
            scoped=True,
        )
        merged, _ = self._validate(claim)
        self.assertIn(claim, merged.claims)

    def test_a_hazard_may_cite_only_itself_attached_to_the_step(self) -> None:
        """Previously impossible: the substring rule rejected it."""

        claim = self._claim(
            ClaimCategory.WARNING_HAZARD,
            self._index_of("Danger, highly corrosive"),
            target="action-50",
            scoped=True,
        )
        merged, _ = self._validate(claim)
        self.assertIn(claim, merged.claims)

    def test_a_hazard_inside_a_step_may_not_stand_at_document_level(self) -> None:
        """Superseded expectation, replaced by the positional rule.

        An earlier revision allowed this. It let a warning that governs a
        numbered step be filed as a document-level note, where execution never
        reads it out at the step. Position now decides, so the same warning
        must attach to the step whose territory it sits in.
        """

        claim = self._claim(
            ClaimCategory.WARNING_HAZARD,
            self._index_of("Danger, highly corrosive"),
            target=None,
            scoped=False,
        )
        with self.assertRaises(ProtocolClaimConsistencyError) as caught:
            self._validate(claim)
        self.assertEqual(
            caught.exception.reason_code,
            "warning_must_attach_to_enclosing_step",
        )

    def test_a_hazard_outside_every_step_does_stand_at_document_level(self) -> None:
        claim = self._claim(
            ClaimCategory.WARNING_HAZARD,
            self._index_of("Before start"),
            target=None,
            scoped=False,
        )
        merged, _ = self._validate(claim)
        self.assertIn(claim, merged.claims)

    def test_a_document_level_value_reaches_the_assembled_protocol(self) -> None:
        """A claim that surfaces nowhere is invisible to reviewer and execution."""

        claim = self._claim(
            ClaimCategory.TEMPERATURE,
            self._index_of("Before start"),
            target=None,
            scoped=False,
        )
        _, merged = self._validate(claim)
        draft = assemble_experiment_protocol(self.extraction, merged)
        identifiers = {
            item.prerequisite_id for item in draft.protocol.before_start
        }
        self.assertIn(f"condition-{claim.claim_id}", identifiers)

    def test_the_response_shape_changed_for_the_repeat_range(self) -> None:
        """8 since a repetition claim declares its range, kind and count.

        7 added repeated_step_labels; 8 adds the fixed_range_repetition
        category and the required repetition_count. A response written against
        an earlier version omits a required field and is refused, so declaring
        one would misstate the shape.
        """

        self.assertEqual(CLAIM_SCHEMA_VERSION, 10)


if __name__ == "__main__":
    unittest.main()
