from __future__ import annotations

import io
import json
import logging
import unittest
from types import SimpleNamespace

from voice_workflow_agent.protocol_provider_diagnostics import (
    run_protocol_provider_stream_diagnostic,
)


class ManualClock:
    def __init__(self) -> None:
        self.value = -1.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def chunk(
    content: str | None = None,
    *,
    finish_reason: str | None = None,
    service_tier: str | None = None,
    usage: object | None = None,
) -> object:
    choices = []
    if content is not None or finish_reason is not None:
        choices.append(
            SimpleNamespace(
                delta=SimpleNamespace(content=content),
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


if __name__ == "__main__":
    unittest.main()
