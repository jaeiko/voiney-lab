"""Every substantive segment is claimed or explicitly declined, exactly once."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_claim_analysis import write_lined_pages
from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    ProtocolAnalysisResponseError,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_claim_analysis import (
    PageCoverageStatus,
    CLAIM_SCHEMA_VERSION,
    parse_chunk_claim_response,
    prepare_chunk_claim_request_context,
    segment_carries_unit_bearing_value,
    segment_is_substantive,
    step_block_ranges,
)

REVISION = "pdf-1"
CHUNK = "chunk-1"

# One numbered step, then a hazard block that belongs to it but is not it.
PAGE = (
    "50 Prepare a 72% H2SO4 solution.",
    "Safety information",
    "Danger, highly corrosive.",
    "Wear gloves, labcoat, safety glasses.",
    "Use a cylinder to measure 242 ml of water.",
)


class SegmentAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        path = Path(self._temp.name) / "page.pdf"
        write_lined_pages(path, (PAGE,))
        self.extraction = extract_protocol_pdf(path)
        self.request = prepare_chunk_claim_request_context(
            self.extraction,
            source_revision=REVISION,
            chunk_id=CHUNK,
            ordinal=0,
            core_page_refs=(1,),
            context_page_refs=(),
        )
        self.handles = {
            item.segment.segment_index: item.handle
            for page in self.request.pages
            for item in page.evidence
        }
        self.texts = {
            item.segment.segment_index: item.segment.text
            for page in self.request.pages
            for item in page.evidence
        }

    def _response(self, *, claimed, declined, status="complete", values=()):
        claims = [
            {
                "claim_id": "action-1",
                "category": "action",
                "source_order": 10,
                "section_id": "section-1",
                "step_id": "step-50",
                "source_label": "50",
                "target_claim_id": None,
                "required_for_execution": True,
                "repeated_step_labels": None,
                "repetition_count": None,
                "evidence": {
                    "source_page_number": 1,
                    "evidence_segment_ids": [self.handles[i] for i in claimed],
                },
            }
        ]
        claims.extend(
            {
                "claim_id": f"quantity-{index}",
                "category": "quantity",
                "source_order": 20 + index,
                "section_id": "section-1",
                "step_id": "step-50",
                "source_label": None,
                "target_claim_id": "action-1",
                "required_for_execution": False,
                "repeated_step_labels": None,
                "repetition_count": None,
                "evidence": {
                    "source_page_number": 1,
                    "evidence_segment_ids": [self.handles[segment]],
                },
            }
            for index, segment in enumerate(values)
        )
        return json.dumps(
            {
                "claim_schema_version": CLAIM_SCHEMA_VERSION,
                "capability_policy_id": self.request.capability_policy_id,
                "request_handle": self.request.request_handle,
                "page_coverage": [
                    {
                        "source_page_number": 1,
                        "analysis_incomplete": status == "analysis_incomplete",
                        "declined_evidence_segment_ids": [
                            self.handles[i] for i in declined
                        ],
                    }
                ],
                "structure": [],
                "claims": claims,
            }
        )

    def _parse(self, payload):
        return parse_chunk_claim_response(
            payload,
            extraction=self.extraction,
            source_revision=REVISION,
            chunk_id=CHUNK,
            core_page_refs=(1,),
            request=self.request,
        )

    def _substantive(self):
        return {i for i, text in self.texts.items() if segment_is_substantive(text)}

    def test_the_page_splits_the_hazard_out_of_the_step(self) -> None:
        joined = [" ".join(text.split()) for text in self.texts.values()]
        self.assertIn("50 Prepare a 72% H2SO4 solution.", joined)
        self.assertTrue(
            any("Danger, highly corrosive." in text for text in joined)
        )

    def test_an_unaccounted_segment_forces_the_page_incomplete(self) -> None:
        """Superseded expectation: this used to discard the whole chunk.

        An omission is a silence, not a false statement. It is now recorded
        against the exact segments and forces the page to analysis_incomplete,
        which blocks the whole-document merge just as firmly, while keeping the
        claims that were correct available for review. The silent third option
        is still gone: the page cannot be reported complete.
        """

        substantive = sorted(self._substantive())
        analysis = self._parse(
            self._response(claimed=[substantive[0]], declined=[])
        )
        coverage = analysis.page_coverage[0]
        self.assertEqual(coverage.status, PageCoverageStatus.ANALYSIS_INCOMPLETE)
        self.assertEqual(
            len(coverage.unaccounted_segment_ids), len(substantive) - 1
        )
        self.assertEqual(coverage.declined_segment_ids, ())

    def test_a_fully_accounted_page_records_no_omission(self) -> None:
        substantive = sorted(self._substantive())
        action = substantive[0]
        values = [
            i
            for i in substantive
            if i != action and segment_carries_unit_bearing_value(self.texts[i])
        ]
        rest = [i for i in substantive if i != action and i not in values]
        analysis = self._parse(
            self._response(claimed=[action], declined=rest, values=values)
        )
        coverage = analysis.page_coverage[0]
        self.assertEqual(coverage.unaccounted_segment_ids, ())
        self.assertEqual(coverage.status, PageCoverageStatus.COMPLETE)

    def test_declining_the_hazard_is_accepted_and_recorded(self) -> None:
        """Accounting is not claiming; declining is a decision on the record."""

        substantive = sorted(self._substantive())
        action = substantive[0]
        value_segments = [
            i
            for i in substantive
            if i != action and segment_carries_unit_bearing_value(self.texts[i])
        ]
        declined = [
            i for i in substantive if i != action and i not in value_segments
        ]
        analysis = self._parse(
            self._response(
                claimed=[action], declined=declined, values=value_segments
            )
        )
        coverage = analysis.page_coverage[0]
        self.assertEqual(len(coverage.declined_segment_ids), len(declined))
        hazard = next(
            i for i in declined if "Danger" in self.texts[i]
        )
        self.assertIn(
            self.request.pages[0].evidence[hazard].segment.segment_id,
            coverage.declined_segment_ids,
        )

    def test_a_claim_wins_over_a_redundant_declination(self) -> None:
        """Superseded: this used to be refused as a contradiction.

        A citation is positive evidence and declining the same segment adds
        nothing, so the claim stands and the redundant declination is dropped.
        Nothing is lost: the claim is validated in full, and a segment that is
        only declined is still held to every rule.
        """

        substantive = sorted(self._substantive())
        action = substantive[0]
        values = [
            i
            for i in substantive
            if i != action and segment_carries_unit_bearing_value(self.texts[i])
        ]
        rest = [i for i in substantive if i != action and i not in values]
        analysis = self._parse(
            self._response(
                claimed=[action],
                declined=[action, *rest],
                values=values,
            )
        )
        coverage = analysis.page_coverage[0]
        action_id = self.request.pages[0].evidence[action].segment.segment_id
        self.assertNotIn(action_id, coverage.declined_segment_ids)
        self.assertEqual(coverage.unaccounted_segment_ids, ())
        self.assertEqual(coverage.status, PageCoverageStatus.COMPLETE)

    def test_a_segment_stating_a_value_cannot_be_declined(self) -> None:
        """The unit cross-check: units are notation, not vocabulary."""

        substantive = sorted(self._substantive())
        value = next(
            i for i in substantive if segment_carries_unit_bearing_value(self.texts[i])
        )
        others = [i for i in substantive if i != value]
        with self.assertRaises(ProtocolAnalysisEvidenceError) as caught:
            self._parse(
                self._response(
                    claimed=[substantive[0]],
                    declined=[value, *others[1:]],
                )
            )
        self.assertEqual(
            caught.exception.diagnostic.reason_code,
            "declined_segment_states_a_value",
        )

    def test_a_declined_handle_must_belong_to_the_page(self) -> None:
        substantive = sorted(self._substantive())
        payload = json.loads(
            self._response(claimed=[substantive[0]], declined=substantive[1:])
        )
        payload["page_coverage"][0]["declined_evidence_segment_ids"] = ["s-bogus"]
        with self.assertRaises(ProtocolAnalysisEvidenceError) as caught:
            self._parse(json.dumps(payload))
        self.assertEqual(
            caught.exception.diagnostic.reason_code, "unknown_evidence_handle"
        )

    def test_the_same_segment_cannot_be_declined_twice(self) -> None:
        substantive = sorted(self._substantive())
        action = substantive[0]
        rest = [i for i in substantive if i != action]
        payload = json.loads(
            self._response(claimed=[action], declined=rest)
        )
        payload["page_coverage"][0]["declined_evidence_segment_ids"].append(
            self.handles[rest[0]]
        )
        with self.assertRaises(ProtocolAnalysisResponseError) as caught:
            self._parse(json.dumps(payload))
        self.assertEqual(
            caught.exception.diagnostic.reason_code,
            "duplicate_declined_segment",
        )

    def test_an_incomplete_page_is_exempt_from_accounting(self) -> None:
        """The one honest answer that cannot also promise accounting."""

        substantive = sorted(self._substantive())
        analysis = self._parse(
            self._response(
                claimed=[substantive[0]],
                declined=[],
                status="analysis_incomplete",
            )
        )
        self.assertEqual(
            analysis.page_coverage[0].status.value, "analysis_incomplete"
        )

    def test_the_declination_list_is_required_by_the_schema(self) -> None:
        substantive = sorted(self._substantive())
        payload = json.loads(
            self._response(claimed=[substantive[0]], declined=substantive[1:])
        )
        del payload["page_coverage"][0]["declined_evidence_segment_ids"]
        with self.assertRaises(ProtocolAnalysisResponseError):
            self._parse(json.dumps(payload))

    def test_schema_version_records_the_new_response_shape(self) -> None:
        self.assertEqual(CLAIM_SCHEMA_VERSION, 8)


class StepBlockRangeTests(unittest.TestCase):
    def test_a_step_owns_everything_up_to_the_next_step(self) -> None:
        text = "50 Prepare acid.\nDanger.\n51 Place bags.\nNote.\n"
        ranges = step_block_ranges(text)
        self.assertEqual(len(ranges), 2)
        first, second = ranges
        self.assertIn("Danger.", text[first[0] : first[1]])
        self.assertNotIn("Danger.", text[second[0] : second[1]])
        self.assertIn("Note.", text[second[0] : second[1]])

    def test_a_page_with_no_numbered_step_has_no_block(self) -> None:
        self.assertEqual(step_block_ranges("Safety information\nDanger.\n"), ())


class SubstantiveAndUnitHelperTests(unittest.TestCase):
    def test_substantive_needs_one_alphanumeric_character(self) -> None:
        self.assertFalse(segment_is_substantive(". \n"))
        self.assertFalse(segment_is_substantive("   "))
        self.assertTrue(segment_is_substantive("a"))

    def test_unit_matching_ignores_case_as_the_step_guard_does(self) -> None:
        for text in ("758 mL of acid", "2 L beaker", "200 µL", "1 h", "20 g"):
            with self.subTest(text=text):
                self.assertTrue(segment_carries_unit_bearing_value(text))

    def test_prose_without_a_unit_bearing_number_is_declinable(self) -> None:
        for text in (
            "Danger, highly corrosive.",
            "ALWAYS ADD ACID TO WATER",
            "Wear gloves, labcoat, safety glasses.",
            "Catalog #A6141",
        ):
            with self.subTest(text=text):
                self.assertFalse(segment_carries_unit_bearing_value(text))


if __name__ == "__main__":
    unittest.main()
