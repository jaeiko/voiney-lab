from __future__ import annotations

import io
import json
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

from voice_workflow_agent.curated_protocol import (
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_protocol_analysis import (
    ProtocolAnalysisEvidenceError,
    ProtocolEvidenceDiagnostic,
)
from voice_workflow_agent.protocol_claim_stream_telemetry import (
    IncrementalProtocolClaimTelemetry,
    measure_protocol_claim_json_telemetry,
)
from voice_workflow_agent.protocol_provider_diagnostics import (
    run_protocol_provider_stream_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]
CURATED_FIXTURE = (
    ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
)
CURATED_PROVENANCE = CURATED_FIXTURE.with_suffix(".provenance.json")
CURATED_SOURCE = ROOT / "data/runtime/candidate-a-source/in-gel-digestion.pdf"


class ManualClock:
    def __init__(self) -> None:
        self.value = -1.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def chunk(
    content: str | None = None,
    *,
    reasoning_content: str | None = None,
    finish_reason: str | None = None,
    service_tier: str | None = None,
    usage: object | None = None,
) -> object:
    choices = []
    if content is not None or finish_reason is not None:
        choices.append(
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning_content,
                ),
                finish_reason=finish_reason,
            )
        )
    return SimpleNamespace(
        choices=choices,
        service_tier=service_tier,
        usage=usage,
    )


class FakeStream:
    def __init__(
        self,
        values: list[object],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.values = values
        self.failure = failure
        self.closed = False

    def __iter__(self):
        yield from self.values
        if self.failure is not None:
            raise self.failure

    def close(self) -> None:
        self.closed = True


class FakeCompletions:
    def __init__(
        self,
        stream: FakeStream | None = None,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.stream = stream
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.stream


def client_for(completions: FakeCompletions) -> object:
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )


class ProtocolProviderDiagnosticTests(unittest.TestCase):
    def run_diagnostic(
        self,
        completions: FakeCompletions,
        validator,
        *,
        service_tier: str = "default",
    ):
        return run_protocol_provider_stream_diagnostic(
            client_for(completions),
            model="grok-4.3",
            reasoning_effort="none",
            system_prompt="system contract",
            input_json='{"request":"bounded"}',
            response_schema={"type": "object"},
            validate_complete=validator,
            timeout_seconds=119.0,
            service_tier=service_tier,
            monotonic=ManualClock(),
            enforce_wall_clock_deadline=False,
        )

    def test_complete_stream_captures_boundaries_usage_and_validation(self):
        usage = SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        )
        stream = FakeStream(
            [
                chunk(""),
                chunk("{"),
                chunk("}"),
                chunk(finish_reason="stop"),
                chunk(service_tier="default", usage=usage),
            ]
        )
        completions = FakeCompletions(stream)
        validated: list[str] = []

        result = self.run_diagnostic(completions, validated.append)

        self.assertTrue(stream.closed)
        self.assertTrue(result.stream_completed)
        self.assertTrue(result.complete_json_returned)
        self.assertTrue(result.parse_succeeded)
        self.assertTrue(result.validation_succeeded)
        self.assertIsNone(result.canonical_validation_diagnostic)
        self.assertEqual(validated, ["{}"])
        self.assertEqual(result.t_headers_seconds, 1.0)
        self.assertEqual(result.t_first_seconds, 2.0)
        self.assertEqual(result.t_last_seconds, 3.0)
        self.assertEqual(result.output_generation_seconds, 1.0)
        self.assertEqual(result.provider_seconds, 4.0)
        self.assertEqual(result.t_parse_seconds, 5.0)
        self.assertEqual(result.t_validate_seconds, 6.0)
        self.assertEqual(result.stream_chunk_count, 5)
        self.assertEqual(result.output_delta_count, 2)
        self.assertEqual(result.output_bytes, 2)
        self.assertEqual(result.reasoning_delta_count, 0)
        self.assertEqual(result.reasoning_bytes, 0)
        self.assertEqual(
            result.structural_telemetry.total_content_bytes,
            result.output_bytes,
        )
        self.assertTrue(result.usage_available)
        self.assertEqual(result.prompt_tokens, 11)
        self.assertEqual(result.completion_tokens, 7)
        self.assertEqual(result.total_tokens, 18)
        self.assertEqual(result.granted_service_tier, "default")

        request = completions.calls[0]
        self.assertIs(request["stream"], True)
        self.assertEqual(
            request["stream_options"],
            {"include_usage": True},
        )
        self.assertEqual(request["reasoning_effort"], "none")
        self.assertNotIn("service_tier", request)
        self.assertEqual(
            request["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "protocol_analysis_response_v1",
                    "schema": {"type": "object"},
                    "strict": True,
                },
            },
        )

    def test_timeout_before_first_output_is_distinct(self):
        stream = FakeStream([], failure=TimeoutError())
        validator_calls: list[str] = []

        result = self.run_diagnostic(
            FakeCompletions(stream),
            validator_calls.append,
        )

        self.assertEqual(result.timeout_phase, "before_first_output")
        self.assertIsNone(result.t_first_seconds)
        self.assertFalse(result.stream_completed)
        self.assertFalse(result.parse_succeeded)
        self.assertFalse(result.validation_succeeded)
        self.assertEqual(validator_calls, [])

    def test_timeout_after_output_never_accepts_partial_json(self):
        private_delta = '{"private_source":"partial"'
        stream = FakeStream(
            [chunk(private_delta)],
            failure=TimeoutError(),
        )
        validator_calls: list[str] = []

        result = self.run_diagnostic(
            FakeCompletions(stream),
            validator_calls.append,
        )

        self.assertEqual(result.timeout_phase, "after_first_output")
        self.assertIsNotNone(result.t_first_seconds)
        self.assertIsNotNone(result.t_last_seconds)
        self.assertFalse(result.stream_completed)
        self.assertFalse(result.complete_json_returned)
        self.assertFalse(result.parse_succeeded)
        self.assertFalse(result.validation_succeeded)
        self.assertEqual(validator_calls, [])
        self.assertNotIn(private_delta, json.dumps(result.public_dict()))
        self.assertEqual(
            result.structural_telemetry.root_objects_started,
            1,
        )

    def test_invalid_completed_json_never_reaches_validation(self):
        stream = FakeStream(
            [chunk("{"), chunk(finish_reason="stop")]
        )
        validator_calls: list[str] = []

        result = self.run_diagnostic(
            FakeCompletions(stream),
            validator_calls.append,
        )

        self.assertTrue(result.stream_completed)
        self.assertFalse(result.complete_json_returned)
        self.assertFalse(result.parse_succeeded)
        self.assertFalse(result.validation_succeeded)
        self.assertEqual(result.failure_code, "provider_invalid_json")
        self.assertEqual(validator_calls, [])

    def test_provider_content_is_not_logged_or_returned(self):
        private_delta = '{"private_source":"never log me"}'
        stream = FakeStream(
            [chunk(private_delta), chunk(finish_reason="stop")]
        )
        capture = io.StringIO()
        handler = logging.StreamHandler(capture)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            result = self.run_diagnostic(
                FakeCompletions(stream),
                lambda value: None,
            )
        finally:
            root.removeHandler(handler)

        self.assertNotIn(private_delta, capture.getvalue())
        self.assertNotIn(private_delta, json.dumps(result.public_dict()))

    def test_canonical_failure_exposes_only_allowlisted_validation_metadata(self):
        private_source = "PRIVATE PROVIDER SOURCE VALUE"
        private_handle = "s-private-provider-handle"
        private_claim_id = "private-claim-id"
        private_hash = "a" * 64
        raw = json.dumps(
            {
                "source_text": private_source,
                "evidence_segment_ids": [private_handle],
                "claim_id": private_claim_id,
            },
            separators=(",", ":"),
        )
        stream = FakeStream([chunk(raw), chunk(finish_reason="stop")])

        def reject_complete(value: str) -> None:
            self.assertEqual(value, raw)
            raise ProtocolAnalysisEvidenceError(
                "Sanitized validator rejection.",
                diagnostic=ProtocolEvidenceDiagnostic(
                    validation_stage="chunk_claim_evidence_validation",
                    reason_code="evidence_segment_unknown",
                    mismatch_class="source_identity_mismatch",
                    evidence_index=2,
                    evidence_type="claim",
                    category="action",
                    page_number=25,
                    provider_handle_count=1,
                    expected_count=2,
                    actual_count=1,
                    chunk_id=private_claim_id,
                    source_revision="private-revision",
                    source_hash=private_hash,
                    quote_sha256=private_hash,
                ),
            )

        capture = io.StringIO()
        handler = logging.StreamHandler(capture)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            result = self.run_diagnostic(
                FakeCompletions(stream),
                reject_complete,
            )
        finally:
            root.removeHandler(handler)

        public = result.public_dict()
        diagnostic = public["canonical_validation_diagnostic"]
        self.assertEqual(
            diagnostic,
            {
                "validation_stage": "chunk_claim_evidence_validation",
                "reason_code": "evidence_segment_unknown",
                "mismatch_class": "source_identity_mismatch",
                "item_type": "claim",
                "category": "action",
                "item_index": 2,
                "source_page": 25,
                "provider_handle_count": 1,
                "expected_count": 2,
                "actual_count": 1,
            },
        )
        self.assertEqual(result.failure_code, "protocol_analysis_invalid_evidence")
        self.assertFalse(result.validation_succeeded)
        rendered = json.dumps(public, sort_keys=True)
        for private_value in (
            private_source,
            private_handle,
            private_claim_id,
            private_hash,
            "private-revision",
        ):
            self.assertNotIn(private_value, rendered)
            self.assertNotIn(private_value, capture.getvalue())
        self.assertFalse(
            {
                "source_hash",
                "quote_sha256",
                "chunk_id",
                "source_revision",
            }
            & set(diagnostic)
        )

    def test_priority_is_explicit_and_granted_tier_is_observed(self):
        stream = FakeStream(
            [
                chunk("{}"),
                chunk(finish_reason="stop", service_tier="priority"),
            ]
        )
        completions = FakeCompletions(stream)

        result = self.run_diagnostic(
            completions,
            lambda value: None,
            service_tier="priority",
        )

        self.assertEqual(completions.calls[0]["service_tier"], "priority")
        self.assertEqual(result.granted_service_tier, "priority")

    def test_reasoning_and_provider_metadata_are_not_structured_output_bytes(self):
        private_reasoning = "private reasoning must not be exposed"
        stream = FakeStream(
            [
                chunk(
                    "{}",
                    reasoning_content=private_reasoning,
                    service_tier="default",
                ),
                chunk(finish_reason="stop"),
            ]
        )

        result = self.run_diagnostic(
            FakeCompletions(stream),
            lambda value: None,
        )
        rendered = json.dumps(result.public_dict())

        self.assertEqual(result.output_bytes, 2)
        self.assertEqual(result.reasoning_delta_count, 1)
        self.assertEqual(
            result.reasoning_bytes,
            len(private_reasoning.encode("utf-8")),
        )
        self.assertEqual(result.structural_telemetry.total_content_bytes, 2)
        self.assertNotIn(private_reasoning, rendered)

    def test_incomplete_json_yields_safe_counts_without_production_parsing(self):
        first_source = "PRIVATE SOURCE VALUE ONE"
        second_source = "PRIVATE SOURCE VALUE TWO"
        partial = json.dumps(
            {
                "claim_schema_version": 3,
                "request_handle": "private-request-handle",
                "page_coverage": [
                    {
                        "source_page_number": 25,
                        "status": "complete",
                        "evidence_item_ids": ["private-claim-one"],
                    }
                ],
                "structure": [],
                "claims": [
                    {
                        "claim_id": "private-claim-one",
                        "category": "action",
                        "source_text": first_source,
                        "section_id": "private-section",
                        "step_id": "private-step",
                        "source_label": "private-label",
                        "target_claim_id": None,
                        "required_for_execution": True,
                        "repeated_step_labels": None,
                        "evidence": {
                            "source_page_number": 25,
                            "evidence_segment_ids": ["private-evidence-handle"],
                        },
                    },
                    {
                        "claim_id": "private-claim-two",
                        "category": "duration",
                        "source_text": second_source,
                    },
                ],
            },
            separators=(",", ":"),
        )
        partial = partial[:-2]
        stream = FakeStream([chunk(partial)], failure=TimeoutError())
        validator_calls: list[str] = []

        result = self.run_diagnostic(
            FakeCompletions(stream),
            validator_calls.append,
        )
        telemetry = result.structural_telemetry
        rendered = json.dumps(result.public_dict())

        self.assertEqual(validator_calls, [])
        self.assertFalse(result.complete_json_returned)
        self.assertEqual(telemetry.claims_begun, 2)
        self.assertEqual(telemetry.claims_completed, 2)
        self.assertEqual(telemetry.category_counts["action"], 1)
        self.assertEqual(telemetry.category_counts["duration"], 1)
        self.assertEqual(telemetry.page_coverage_records_completed, 1)
        self.assertTrue(
            telemetry.major_sections["page_coverage"].arrays_completed
        )
        self.assertFalse(telemetry.major_sections["claims"].arrays_completed)
        self.assertNotIn(first_source, rendered)
        self.assertNotIn(second_source, rendered)
        self.assertNotIn("private-request-handle", rendered)
        self.assertNotIn("private-evidence-handle", rendered)

    def test_values_are_discarded_and_repetition_hashes_are_never_emitted(self):
        private_source = "PRIVATE REPEATED SOURCE"
        claim = {
            "claim_id": "private-repeated-claim",
            "category": "action",
            "source_text": private_source,
            "section_id": "private-section",
            "step_id": "private-step",
            "source_label": "private-label",
            "target_claim_id": "private-target",
            "required_for_execution": True,
            "repeated_step_labels": None,
            "evidence": {
                "source_page_number": 25,
                "evidence_segment_ids": ["private-evidence-handle"],
            },
        }
        raw = json.dumps(
            {
                "claim_schema_version": 3,
                "capability_policy_id": "private-policy",
                "request_handle": "private-request-handle",
                "page_coverage": [],
                "structure": [],
                "claims": [claim, claim],
            },
            separators=(",", ":"),
        )
        counter = IncrementalProtocolClaimTelemetry()
        for character in raw:
            counter.feed(character)
        telemetry = counter.snapshot()
        rendered_public = json.dumps(telemetry.public_dict(), sort_keys=True)
        rendered_internal = repr(vars(counter))

        self.assertEqual(
            telemetry.string_lengths["source_text"].total_characters,
            2 * len(private_source),
        )
        self.assertEqual(
            telemetry.repetition["complete_claim_objects"].repeated_count,
            1,
        )
        self.assertEqual(
            telemetry.repetition["source_text_values"].repeated_count,
            1,
        )
        self.assertEqual(
            telemetry.repetition["claim_ids"].repeated_count,
            1,
        )
        self.assertEqual(
            telemetry.repetition["evidence_handle_sets"].repeated_count,
            1,
        )
        for private_value in (
            private_source,
            "private-repeated-claim",
            "private-request-handle",
            "private-evidence-handle",
            "private-section",
        ):
            self.assertNotIn(private_value, rendered_public)
            self.assertNotIn(private_value, rendered_internal)
        self.assertNotIn("digest", rendered_public)
        self.assertNotIn("hash", rendered_public)

    def test_incremental_counter_handles_escaped_strings_and_restart_marker(self):
        source_value = "two characters: μ\n"
        raw = json.dumps(
            {
                "page_coverage": [],
                "structure": [],
                "claims": [{"source_text": source_value}],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        telemetry = measure_protocol_claim_json_telemetry(raw + "{")

        self.assertEqual(
            telemetry.string_lengths["source_text"].total_characters,
            len(source_value),
        )
        self.assertTrue(telemetry.restart_or_repeated_structure_detected)
        self.assertFalse(telemetry.complete_json_structure)

    def test_diagnostic_is_read_only_for_server_owned_protocol_state(self):
        fixture = load_curated_protocol_fixture(
            CURATED_FIXTURE,
            CURATED_PROVENANCE,
            CURATED_SOURCE,
        )
        session = CuratedProtocolSession(fixture)
        before = session.state()
        stream = FakeStream([chunk("{}"), chunk(finish_reason="stop")])

        result = self.run_diagnostic(
            FakeCompletions(stream),
            lambda value: None,
        )

        self.assertTrue(result.validation_succeeded)
        self.assertEqual(session.state(), before)


if __name__ == "__main__":
    unittest.main()
