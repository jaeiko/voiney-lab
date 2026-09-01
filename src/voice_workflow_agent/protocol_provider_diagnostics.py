"""Privacy-safe, diagnostic-only timing for Protocol provider streams.

This module is not used by production claim execution. It deliberately keeps
provider output in local memory only long enough to prove complete transport,
JSON parsing, and caller-supplied canonical validation. Public results contain
only bounded configuration, timing, size, usage, and outcome metadata.
"""

from __future__ import annotations

import json
import math
import re
import signal
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import FrameType
from typing import Any, Literal

from openai import APITimeoutError

from voice_workflow_agent.experiment_protocol_analysis import (
    build_protocol_analysis_chat_request,
)
from voice_workflow_agent.protocol_claim_analysis import (
    MAX_CHUNK_CLAIM_RESPONSE_BYTES,
)
from voice_workflow_agent.protocol_claim_stream_telemetry import (
    IncrementalProtocolClaimTelemetry,
    ProtocolClaimStructuralTelemetry,
)


TimeoutPhase = Literal["before_first_output", "after_first_output"]
ServiceTier = Literal["default", "priority"]


class ProtocolProviderDiagnosticError(RuntimeError):
    """The diagnostic harness itself was configured incorrectly."""


class _DiagnosticWallClockTimeout(TimeoutError):
    """Private total-wall-clock deadline signal."""


_SAFE_DIAGNOSTIC_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
_SAFE_FIELD_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9_.\[\]-]{0,199}$")


def _safe_diagnostic_token(value: object, fallback: str) -> str:
    if isinstance(value, str) and _SAFE_DIAGNOSTIC_TOKEN.fullmatch(value):
        return value
    return fallback


def _safe_diagnostic_count(value: object) -> int | None:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 10_000_000
    ):
        return value
    return None


def _safe_canonical_validation_diagnostic(
    exc: BaseException,
) -> dict[str, object]:
    """Project one exception to a strict content-free metadata allowlist."""

    diagnostic = getattr(exc, "diagnostic", None)
    values: dict[str, object] = {
        "validation_stage": _safe_diagnostic_token(
            getattr(diagnostic, "validation_stage", None),
            "canonical_validation",
        ),
        "reason_code": _safe_diagnostic_token(
            getattr(diagnostic, "reason_code", None),
            _safe_diagnostic_token(
                getattr(exc, "code", None),
                "canonical_validation_failed",
            ),
        ),
        "mismatch_class": _safe_diagnostic_token(
            getattr(diagnostic, "mismatch_class", None),
            "validation_exception",
        ),
    }
    text_fields = {
        "item_type": getattr(diagnostic, "evidence_type", None),
        "category": getattr(diagnostic, "category", None),
    }
    for key, value in text_fields.items():
        if isinstance(value, str) and _SAFE_DIAGNOSTIC_TOKEN.fullmatch(value):
            values[key] = value
    field_path = getattr(diagnostic, "field_path", None)
    if isinstance(field_path, str) and _SAFE_FIELD_PATH.fullmatch(field_path):
        values["field_path"] = field_path
    numeric_fields = {
        "item_index": getattr(diagnostic, "evidence_index", None),
        "source_page": getattr(diagnostic, "page_number", None),
        "provider_handle_count": getattr(
            diagnostic, "provider_handle_count", None
        ),
        "expected_source_page": getattr(
            diagnostic, "expected_page_number", None
        ),
        "expected_count": getattr(diagnostic, "expected_count", None),
        "actual_count": getattr(diagnostic, "actual_count", None),
        "expected_length": getattr(diagnostic, "expected_length", None),
        "actual_length": getattr(diagnostic, "actual_length", None),
        "missing_numbered_action_count": getattr(
            diagnostic, "missing_numbered_action_count", None
        ),
        "page_coverage_count": getattr(
            diagnostic, "page_coverage_count", None
        ),
    }
    for key, value in numeric_fields.items():
        safe_value = _safe_diagnostic_count(value)
        if safe_value is not None:
            values[key] = safe_value
    return values


@dataclass(frozen=True)
class ProtocolProviderStreamDiagnostic:
    """Content-free result for exactly one streamed provider request."""

    model: str
    reasoning_effort: str | None
    requested_service_tier: ServiceTier
    granted_service_tier: str | None
    timeout_seconds: float
    t0_seconds: float
    t_headers_seconds: float | None
    t_first_seconds: float | None
    t_last_seconds: float | None
    t_parse_seconds: float | None
    t_validate_seconds: float | None
    connection_header_seconds: float | None
    ttft_seconds: float | None
    output_generation_seconds: float | None
    elapsed_after_first_seconds: float | None
    provider_seconds: float
    total_wall_seconds: float
    stream_chunk_count: int
    output_delta_count: int
    output_bytes: int
    reasoning_delta_count: int
    reasoning_bytes: int
    structural_telemetry: ProtocolClaimStructuralTelemetry
    stream_completed: bool
    finish_reason: str | None
    complete_json_returned: bool
    parse_succeeded: bool
    validation_succeeded: bool
    canonical_validation_diagnostic: dict[str, object] | None
    timeout_phase: TimeoutPhase | None
    failure_code: str | None
    usage_available: bool
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None

    def public_dict(self) -> dict[str, object]:
        """Return JSON-ready diagnostic metadata with no provider content."""

        def rounded(value: float | None) -> float | None:
            return None if value is None else round(value, 6)

        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "requested_service_tier": self.requested_service_tier,
            "granted_service_tier": self.granted_service_tier,
            "timeout_seconds": self.timeout_seconds,
            "timing_seconds_from_t0": {
                "t0": rounded(self.t0_seconds),
                "t_headers": rounded(self.t_headers_seconds),
                "t_first": rounded(self.t_first_seconds),
                "t_last": rounded(self.t_last_seconds),
                "t_parse": rounded(self.t_parse_seconds),
                "t_validate": rounded(self.t_validate_seconds),
            },
            "connection_header_seconds": rounded(
                self.connection_header_seconds
            ),
            "ttft_seconds": rounded(self.ttft_seconds),
            "output_generation_seconds": rounded(
                self.output_generation_seconds
            ),
            "elapsed_after_first_seconds": rounded(
                self.elapsed_after_first_seconds
            ),
            "provider_seconds": rounded(self.provider_seconds),
            "total_wall_seconds": rounded(self.total_wall_seconds),
            "stream_chunk_count": self.stream_chunk_count,
            "output_delta_count": self.output_delta_count,
            "output_bytes": self.output_bytes,
            "reasoning_delta_count": self.reasoning_delta_count,
            "reasoning_bytes": self.reasoning_bytes,
            "structural_telemetry": self.structural_telemetry.public_dict(),
            "stream_completed": self.stream_completed,
            "finish_reason": self.finish_reason,
            "complete_json_returned": self.complete_json_returned,
            "parse_succeeded": self.parse_succeeded,
            "validation_succeeded": self.validation_succeeded,
            "canonical_validation_diagnostic": (
                dict(self.canonical_validation_diagnostic)
                if self.canonical_validation_diagnostic is not None
                else None
            ),
            "timeout_phase": self.timeout_phase,
            "failure_code": self.failure_code,
            "usage": {
                "available": self.usage_available,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
        }


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _is_timeout(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (TimeoutError, APITimeoutError, _DiagnosticWallClockTimeout),
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


@contextmanager
def _total_wall_clock_deadline(
    timeout_seconds: float,
    *,
    enabled: bool,
) -> Iterator[None]:
    if not enabled:
        yield
        return
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "SIGALRM")
    ):
        raise ProtocolProviderDiagnosticError(
            "The diagnostic total deadline requires a POSIX main thread."
        )

    def deadline_reached(
        signum: int,
        frame: FrameType | None,
    ) -> None:
        del signum, frame
        raise _DiagnosticWallClockTimeout(
            "Protocol provider diagnostic reached its total deadline."
        )

    started = time.monotonic()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    signal.signal(signal.SIGALRM, deadline_reached)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0:
            elapsed = time.monotonic() - started
            signal.setitimer(
                signal.ITIMER_REAL,
                max(1e-6, previous_delay - elapsed),
                previous_interval,
            )


def run_protocol_provider_stream_diagnostic(
    client: Any,
    *,
    model: str,
    reasoning_effort: str | None,
    system_prompt: str,
    input_json: str,
    response_schema: dict[str, Any],
    validate_complete: Callable[[str], None],
    timeout_seconds: float,
    service_tier: ServiceTier = "default",
    monotonic: Callable[[], float] = time.monotonic,
    enforce_wall_clock_deadline: bool = True,
) -> ProtocolProviderStreamDiagnostic:
    """Measure one strict structured-output stream without exposing content."""

    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > 120.0
        or service_tier not in {"default", "priority"}
    ):
        raise ProtocolProviderDiagnosticError(
            "Protocol provider diagnostic configuration is invalid."
        )

    request = build_protocol_analysis_chat_request(
        model=model,
        reasoning_effort=reasoning_effort,
        system_prompt=system_prompt,
        input_json=input_json,
        response_schema=response_schema,
    )
    request["stream"] = True
    request["stream_options"] = {"include_usage": True}
    if service_tier == "priority":
        request["service_tier"] = "priority"

    started = monotonic()
    headers_at: float | None = None
    first_at: float | None = None
    last_at: float | None = None
    provider_finished_at: float | None = None
    parse_at: float | None = None
    validate_at: float | None = None
    chunk_count = 0
    delta_count = 0
    output_bytes = 0
    reasoning_delta_count = 0
    reasoning_bytes = 0
    structural_counter = IncrementalProtocolClaimTelemetry()
    content_parts: list[str] = []
    finish_reason: str | None = None
    granted_tier: str | None = None
    usage_available = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    stream_completed = False
    complete_json_returned = False
    parse_succeeded = False
    validation_succeeded = False
    canonical_validation_diagnostic: dict[str, object] | None = None
    timeout_phase: TimeoutPhase | None = None
    failure_code: str | None = None
    stream: object | None = None

    try:
        with _total_wall_clock_deadline(
            float(timeout_seconds),
            enabled=enforce_wall_clock_deadline,
        ):
            stream = client.chat.completions.create(**request)
            headers_at = monotonic()
            for chunk in stream:
                chunk_count += 1
                tier = _field(chunk, "service_tier")
                if isinstance(tier, str) and tier:
                    granted_tier = tier
                usage = _field(chunk, "usage")
                if usage is not None:
                    usage_available = True
                    prompt_tokens = _optional_int(
                        _field(usage, "prompt_tokens")
                    )
                    completion_tokens = _optional_int(
                        _field(usage, "completion_tokens")
                    )
                    total_tokens = _optional_int(
                        _field(usage, "total_tokens")
                    )
                choices = _field(chunk, "choices", ())
                if not isinstance(choices, (list, tuple)):
                    continue
                for choice in choices:
                    reason = _field(choice, "finish_reason")
                    if isinstance(reason, str) and reason:
                        finish_reason = reason
                    delta = _field(choice, "delta")
                    reasoning_content = _field(delta, "reasoning_content")
                    if not isinstance(reasoning_content, str):
                        reasoning_content = _field(delta, "reasoning")
                    if isinstance(reasoning_content, str) and reasoning_content:
                        reasoning_delta_count += 1
                        reasoning_bytes += len(
                            reasoning_content.encode("utf-8")
                        )
                    content = _field(delta, "content")
                    if not isinstance(content, str) or not content:
                        continue
                    observed_at = monotonic()
                    if first_at is None:
                        first_at = observed_at
                    last_at = observed_at
                    delta_count += 1
                    encoded_bytes = len(content.encode("utf-8"))
                    output_bytes += encoded_bytes
                    structural_counter.feed(content)
                    if output_bytes > MAX_CHUNK_CLAIM_RESPONSE_BYTES:
                        failure_code = "provider_output_too_large"
                        raise ProtocolProviderDiagnosticError(
                            "Protocol provider diagnostic output exceeded its bound."
                        )
                    content_parts.append(content)
            provider_finished_at = monotonic()
            stream_completed = True
            if finish_reason != "stop" or not content_parts:
                failure_code = (
                    "provider_incomplete_finish"
                    if content_parts
                    else "provider_empty_output"
                )
            else:
                complete_text = "".join(content_parts)
                try:
                    json.loads(complete_text)
                    parse_succeeded = True
                    complete_json_returned = True
                except (json.JSONDecodeError, UnicodeError):
                    failure_code = "provider_invalid_json"
                parse_at = monotonic()
                if parse_succeeded:
                    try:
                        validate_complete(complete_text)
                        validation_succeeded = True
                    except Exception as exc:
                        canonical_validation_diagnostic = (
                            _safe_canonical_validation_diagnostic(exc)
                        )
                        code = getattr(exc, "code", None)
                        failure_code = (
                            code
                            if isinstance(code, str) and code.isidentifier()
                            else "canonical_validation_failed"
                        )
                    validate_at = monotonic()
                del complete_text
    except BaseException as exc:
        if _is_timeout(exc):
            timeout_phase = (
                "before_first_output"
                if first_at is None
                else "after_first_output"
            )
            failure_code = "provider_timeout"
        elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        elif failure_code is None:
            failure_code = "provider_transport_error"
    finally:
        close = _field(stream, "close") if stream is not None else None
        if callable(close):
            try:
                close()
            except Exception:
                pass
        content_parts.clear()

    finished = monotonic()
    provider_end = provider_finished_at or finished
    connection_seconds = (
        None if headers_at is None else headers_at - started
    )
    ttft_seconds = None if first_at is None else first_at - started
    output_generation_seconds = (
        None
        if first_at is None or last_at is None
        else last_at - first_at
    )
    elapsed_after_first_seconds = (
        None if first_at is None else provider_end - first_at
    )
    return ProtocolProviderStreamDiagnostic(
        model=model,
        reasoning_effort=reasoning_effort,
        requested_service_tier=service_tier,
        granted_service_tier=granted_tier,
        timeout_seconds=float(timeout_seconds),
        t0_seconds=0.0,
        t_headers_seconds=(
            None if headers_at is None else headers_at - started
        ),
        t_first_seconds=(
            None if first_at is None else first_at - started
        ),
        t_last_seconds=(
            None if last_at is None else last_at - started
        ),
        t_parse_seconds=(
            None if parse_at is None else parse_at - started
        ),
        t_validate_seconds=(
            None if validate_at is None else validate_at - started
        ),
        connection_header_seconds=connection_seconds,
        ttft_seconds=ttft_seconds,
        output_generation_seconds=output_generation_seconds,
        elapsed_after_first_seconds=elapsed_after_first_seconds,
        provider_seconds=provider_end - started,
        total_wall_seconds=finished - started,
        stream_chunk_count=chunk_count,
        output_delta_count=delta_count,
        output_bytes=output_bytes,
        reasoning_delta_count=reasoning_delta_count,
        reasoning_bytes=reasoning_bytes,
        structural_telemetry=structural_counter.snapshot(),
        stream_completed=stream_completed,
        finish_reason=finish_reason,
        complete_json_returned=complete_json_returned,
        parse_succeeded=parse_succeeded,
        validation_succeeded=validation_succeeded,
        canonical_validation_diagnostic=canonical_validation_diagnostic,
        timeout_phase=timeout_phase,
        failure_code=failure_code,
        usage_available=usage_available,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
