"""Privacy-safe incremental telemetry for streamed Protocol claim JSON.

The counter is diagnostic-only.  It recognizes the fixed claim response shape
without constructing a response object, retaining provider string values, or
calling the production DTO decoder.  String values are reduced immediately to
lengths and process-local keyed digests; digests never leave this module.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from voice_workflow_agent.protocol_claim_analysis import (
    ClaimCategory,
    PageCoverageStatus,
    StructureMarkerKind,
)


_MAJOR_SECTIONS = ("page_coverage", "structure", "claims")
_RECOGNIZED_KEYS = frozenset(
    {
        "claim_schema_version",
        "capability_policy_id",
        "request_handle",
        "page_coverage",
        "source_page_number",
        "status",
        "evidence_item_ids",
        "structure",
        "marker_id",
        "kind",
        "source_order",
        "source_text",
        "section_id",
        "evidence",
        "evidence_segment_ids",
        "claims",
        "claim_id",
        "category",
        "step_id",
        "source_label",
        "target_claim_id",
        "required_for_execution",
    }
)
_COUNTED_FIELDS = (
    "category",
    "source_text",
    "source_label",
    "evidence",
    "evidence_segment_ids",
    "section_id",
    "step_id",
    "target_claim_id",
    "required_for_execution",
    "request_handle",
)
_STRING_FIELD_CLASSES = (
    "source_text",
    "source_label",
    "section_id",
    "step_id",
    "target_id",
    "evidence_handle",
    "claim_id",
    "marker_id",
    "request_handle",
)
_RELATIONSHIP_FIELDS = frozenset(
    {"section_id", "step_id", "target_claim_id"}
)
_CAPTURED_VALUE_FIELDS = frozenset({"category", "kind", "status"})
_OTHER_KEY = "<unrecognized>"


@dataclass(frozen=True)
class StringLengthStatistics:
    """Aggregate decoded character lengths without any string values."""

    count: int
    total_characters: int
    minimum_length: int | None
    maximum_length: int | None
    average_length: float | None

    def public_dict(self) -> dict[str, int | float | None]:
        return {
            "count": self.count,
            "total_characters": self.total_characters,
            "minimum_length": self.minimum_length,
            "maximum_length": self.maximum_length,
            "average_length": self.average_length,
        }


@dataclass(frozen=True)
class RepetitionStatistics:
    """Cardinality-only projection of process-local keyed digest counts."""

    total_count: int
    unique_count: int
    repeated_count: int
    maximum_repeat_frequency: int

    def public_dict(self) -> dict[str, int]:
        return {
            "total_count": self.total_count,
            "unique_count": self.unique_count,
            "repeated_count": self.repeated_count,
            "maximum_repeat_frequency": self.maximum_repeat_frequency,
        }


@dataclass(frozen=True)
class MajorSectionProgress:
    arrays_started: int
    arrays_completed: int

    def public_dict(self) -> dict[str, int | bool]:
        return {
            "arrays_started": self.arrays_started,
            "arrays_completed": self.arrays_completed,
            "reached_end": self.arrays_completed > 0,
        }


@dataclass(frozen=True)
class ProtocolClaimStructuralTelemetry:
    """Immutable, provider-content-free structural telemetry snapshot."""

    total_content_bytes: int
    root_objects_started: int
    root_objects_completed: int
    complete_json_structure: bool
    valid_json_prefix: bool
    restart_or_repeated_structure_detected: bool
    claims_begun: int
    claims_completed: int
    structural_markers_begun: int
    structural_markers_completed: int
    page_coverage_records_begun: int
    page_coverage_records_completed: int
    evidence_objects_begun: int
    evidence_objects_completed: int
    evidence_reference_count: int
    coverage_evidence_item_reference_count: int
    field_counts: dict[str, int]
    category_counts: dict[str, int]
    marker_kind_counts: dict[str, int]
    coverage_status_counts: dict[str, int]
    string_lengths: dict[str, StringLengthStatistics]
    repetition: dict[str, RepetitionStatistics]
    major_sections: dict[str, MajorSectionProgress]

    def public_dict(self) -> dict[str, object]:
        return {
            "total_content_bytes": self.total_content_bytes,
            "root_objects_started": self.root_objects_started,
            "root_objects_completed": self.root_objects_completed,
            "complete_json_structure": self.complete_json_structure,
            "valid_json_prefix": self.valid_json_prefix,
            "restart_or_repeated_structure_detected": (
                self.restart_or_repeated_structure_detected
            ),
            "records": {
                "claims_begun": self.claims_begun,
                "claims_completed": self.claims_completed,
                "structural_markers_begun": self.structural_markers_begun,
                "structural_markers_completed": (
                    self.structural_markers_completed
                ),
                "page_coverage_records_begun": (
                    self.page_coverage_records_begun
                ),
                "page_coverage_records_completed": (
                    self.page_coverage_records_completed
                ),
                "evidence_objects_begun": self.evidence_objects_begun,
                "evidence_objects_completed": self.evidence_objects_completed,
                "evidence_reference_count": self.evidence_reference_count,
                "coverage_evidence_item_reference_count": (
                    self.coverage_evidence_item_reference_count
                ),
            },
            "field_counts": dict(self.field_counts),
            "category_counts": dict(self.category_counts),
            "marker_kind_counts": dict(self.marker_kind_counts),
            "coverage_status_counts": dict(self.coverage_status_counts),
            "string_lengths": {
                key: value.public_dict()
                for key, value in self.string_lengths.items()
            },
            "repetition": {
                key: value.public_dict()
                for key, value in self.repetition.items()
            },
            "major_sections": {
                key: value.public_dict()
                for key, value in self.major_sections.items()
            },
        }


@dataclass
class _LengthAccumulator:
    count: int = 0
    total: int = 0
    minimum: int | None = None
    maximum: int | None = None

    def add(self, length: int) -> None:
        self.count += 1
        self.total += length
        self.minimum = length if self.minimum is None else min(self.minimum, length)
        self.maximum = length if self.maximum is None else max(self.maximum, length)

    def snapshot(self) -> StringLengthStatistics:
        return StringLengthStatistics(
            count=self.count,
            total_characters=self.total,
            minimum_length=self.minimum,
            maximum_length=self.maximum,
            average_length=(
                None if self.count == 0 else round(self.total / self.count, 6)
            ),
        )


@dataclass(frozen=True)
class _StringToken:
    length: int
    digest: bytes
    captured: str | None


@dataclass(frozen=True)
class _PrimitiveToken:
    length: int
    digest: bytes


@dataclass
class _JsonFrame:
    kind: Literal["object", "array"]
    path: tuple[str, ...]
    state: str
    current_key: str | None = None


def _repetition_snapshot(counter: Counter[bytes]) -> RepetitionStatistics:
    total = sum(counter.values())
    return RepetitionStatistics(
        total_count=total,
        unique_count=len(counter),
        repeated_count=sum(value - 1 for value in counter.values()),
        maximum_repeat_frequency=max(counter.values(), default=0),
    )


class IncrementalProtocolClaimTelemetry:
    """Consume arbitrary stream splits and retain only aggregate metadata."""

    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)
        self._frames: list[_JsonFrame] = []
        self._root_value_seen = False
        self._root_complete = False
        self._valid_prefix = True
        self._trailing_token_seen = False
        self._restart_marker_seen = False
        self._total_bytes = 0

        self._lex_state = "normal"
        self._string_hasher: hmac.HMAC | None = None
        self._string_length = 0
        self._string_capture_limit = 0
        self._string_capture: list[str] = []
        self._string_capture_overflow = False
        self._pending_high_surrogate: int | None = None
        self._unicode_digits = ""
        self._primitive_hasher: hmac.HMAC | None = None
        self._primitive_length = 0

        self._root_objects_started = 0
        self._root_objects_completed = 0
        self._claims_begun = 0
        self._claims_completed = 0
        self._markers_begun = 0
        self._markers_completed = 0
        self._coverage_begun = 0
        self._coverage_completed = 0
        self._evidence_begun = 0
        self._evidence_completed = 0
        self._evidence_references = 0
        self._coverage_references = 0
        self._field_counts = Counter({key: 0 for key in _COUNTED_FIELDS})
        self._categories = Counter({item.value: 0 for item in ClaimCategory})
        self._marker_kinds = Counter(
            {item.value: 0 for item in StructureMarkerKind}
        )
        self._coverage_statuses = Counter(
            {item.value: 0 for item in PageCoverageStatus}
        )
        self._lengths = {
            key: _LengthAccumulator() for key in _STRING_FIELD_CLASSES
        }
        self._repetitions = {
            key: Counter()
            for key in (
                "complete_claim_objects",
                "source_text_values",
                "relationship_values",
                "evidence_handle_sets",
                "claim_ids",
            )
        }
        self._section_starts = Counter({key: 0 for key in _MAJOR_SECTIONS})
        self._section_completions = Counter(
            {key: 0 for key in _MAJOR_SECTIONS}
        )
        self._envelope_marker_count = 0
        self._active_claim_hasher: hmac.HMAC | None = None
        self._active_evidence_set_hasher: hmac.HMAC | None = None

    def _new_hasher(self, domain: bytes) -> hmac.HMAC:
        value = hmac.new(self._secret, digestmod=hashlib.sha256)
        value.update(domain)
        return value

    def feed(self, content: str) -> None:
        """Consume a content delta without retaining it after this call."""

        if not isinstance(content, str):
            raise TypeError("Protocol claim telemetry accepts string deltas only.")
        self._total_bytes += len(content.encode("utf-8"))
        index = 0
        while index < len(content):
            char = content[index]
            consumed = self._consume_character(char)
            if consumed:
                index += 1

    def snapshot(self) -> ProtocolClaimStructuralTelemetry:
        repeated_structure = (
            self._root_objects_started > 1
            or self._restart_marker_seen
            or self._envelope_marker_count > 1
            or any(self._section_starts[key] > 1 for key in _MAJOR_SECTIONS)
        )
        complete_structure = (
            self._root_complete
            and not self._frames
            and self._lex_state == "normal"
            and self._valid_prefix
            and not self._trailing_token_seen
        )
        return ProtocolClaimStructuralTelemetry(
            total_content_bytes=self._total_bytes,
            root_objects_started=self._root_objects_started,
            root_objects_completed=self._root_objects_completed,
            complete_json_structure=complete_structure,
            valid_json_prefix=self._valid_prefix,
            restart_or_repeated_structure_detected=repeated_structure,
            claims_begun=self._claims_begun,
            claims_completed=self._claims_completed,
            structural_markers_begun=self._markers_begun,
            structural_markers_completed=self._markers_completed,
            page_coverage_records_begun=self._coverage_begun,
            page_coverage_records_completed=self._coverage_completed,
            evidence_objects_begun=self._evidence_begun,
            evidence_objects_completed=self._evidence_completed,
            evidence_reference_count=self._evidence_references,
            coverage_evidence_item_reference_count=self._coverage_references,
            field_counts=dict(self._field_counts),
            category_counts=dict(self._categories),
            marker_kind_counts=dict(self._marker_kinds),
            coverage_status_counts=dict(self._coverage_statuses),
            string_lengths={
                key: value.snapshot() for key, value in self._lengths.items()
            },
            repetition={
                key: _repetition_snapshot(value)
                for key, value in self._repetitions.items()
            },
            major_sections={
                key: MajorSectionProgress(
                    arrays_started=self._section_starts[key],
                    arrays_completed=self._section_completions[key],
                )
                for key in _MAJOR_SECTIONS
            },
        )

    def _consume_character(self, char: str) -> bool:
        if self._lex_state == "string":
            if char == '"':
                self._finish_string()
            elif char == "\\":
                self._lex_state = "string_escape"
            elif ord(char) < 0x20:
                self._valid_prefix = False
            else:
                self._accept_string_codepoint(ord(char))
            return True
        if self._lex_state == "string_escape":
            escaped = {
                '"': 0x22,
                "\\": 0x5C,
                "/": 0x2F,
                "b": 0x08,
                "f": 0x0C,
                "n": 0x0A,
                "r": 0x0D,
                "t": 0x09,
            }
            if char == "u":
                self._unicode_digits = ""
                self._lex_state = "string_unicode"
            elif char in escaped:
                self._accept_string_codepoint(escaped[char])
                self._lex_state = "string"
            else:
                self._valid_prefix = False
                self._lex_state = "string"
            return True
        if self._lex_state == "string_unicode":
            if char not in "0123456789abcdefABCDEF":
                self._valid_prefix = False
                self._lex_state = "string"
                return True
            self._unicode_digits += char
            if len(self._unicode_digits) == 4:
                self._accept_string_codepoint(int(self._unicode_digits, 16))
                self._unicode_digits = ""
                self._lex_state = "string"
            return True
        if self._lex_state == "primitive":
            if char in " \t\r\n,]}:":
                self._finish_primitive()
                return False
            assert self._primitive_hasher is not None
            self._primitive_hasher.update(ord(char).to_bytes(4, "big"))
            self._primitive_length += 1
            return True

        if char in " \t\r\n":
            return True
        if char == '"':
            self._start_string()
            return True
        if char == "{":
            self._start_container("object")
            return True
        if char == "[":
            self._start_container("array")
            return True
        if char == "}":
            self._end_container("object")
            return True
        if char == "]":
            self._end_container("array")
            return True
        if char == ":":
            self._accept_colon()
            return True
        if char == ",":
            self._accept_comma()
            return True
        self._start_primitive(char)
        return True

    def _start_string(self) -> None:
        capture_limit = 0
        if self._frames:
            frame = self._frames[-1]
            if frame.kind == "object" and frame.state == "key_or_end":
                capture_limit = 96
            elif (
                frame.kind == "object"
                and frame.state == "value"
                and frame.current_key in _CAPTURED_VALUE_FIELDS
            ):
                capture_limit = 64
        self._string_hasher = self._new_hasher(b"string-value-v1")
        self._string_length = 0
        self._string_capture_limit = capture_limit
        self._string_capture.clear()
        self._string_capture_overflow = False
        self._pending_high_surrogate = None
        self._lex_state = "string"

    def _accept_string_codepoint(self, codepoint: int) -> None:
        if self._pending_high_surrogate is not None:
            if 0xDC00 <= codepoint <= 0xDFFF:
                high = self._pending_high_surrogate
                codepoint = 0x10000 + ((high - 0xD800) << 10) + (
                    codepoint - 0xDC00
                )
                self._pending_high_surrogate = None
                self._record_decoded_codepoint(codepoint)
                return
            self._record_decoded_codepoint(self._pending_high_surrogate)
            self._pending_high_surrogate = None
        if 0xD800 <= codepoint <= 0xDBFF:
            self._pending_high_surrogate = codepoint
            return
        self._record_decoded_codepoint(codepoint)

    def _record_decoded_codepoint(self, codepoint: int) -> None:
        assert self._string_hasher is not None
        self._string_hasher.update(codepoint.to_bytes(4, "big"))
        self._string_length += 1
        if self._string_capture_limit <= 0 or self._string_capture_overflow:
            return
        if self._string_length > self._string_capture_limit:
            self._string_capture.clear()
            self._string_capture_overflow = True
            return
        self._string_capture.append(chr(codepoint))

    def _finish_string(self) -> None:
        if self._pending_high_surrogate is not None:
            self._record_decoded_codepoint(self._pending_high_surrogate)
            self._pending_high_surrogate = None
        assert self._string_hasher is not None
        captured = (
            "".join(self._string_capture)
            if self._string_capture_limit > 0
            and not self._string_capture_overflow
            else None
        )
        token = _StringToken(
            length=self._string_length,
            digest=self._string_hasher.digest(),
            captured=captured,
        )
        self._string_hasher = None
        self._string_capture.clear()
        self._string_capture_limit = 0
        self._string_capture_overflow = False
        self._unicode_digits = ""
        self._lex_state = "normal"
        self._accept_string_token(token)

    def _start_primitive(self, char: str) -> None:
        self._primitive_hasher = self._new_hasher(b"primitive-value-v1")
        self._primitive_hasher.update(ord(char).to_bytes(4, "big"))
        self._primitive_length = 1
        self._lex_state = "primitive"

    def _finish_primitive(self) -> None:
        assert self._primitive_hasher is not None
        token = _PrimitiveToken(
            length=self._primitive_length,
            digest=self._primitive_hasher.digest(),
        )
        self._primitive_hasher = None
        self._primitive_length = 0
        self._lex_state = "normal"
        self._accept_value_token(token)

    def _value_path(self) -> tuple[str, ...] | None:
        if not self._frames:
            if self._root_value_seen:
                self._trailing_token_seen = True
                self._valid_prefix = False
                return None
            self._root_value_seen = True
            return ()
        frame = self._frames[-1]
        if frame.kind == "object":
            if frame.state != "value":
                self._valid_prefix = False
                return None
            path = frame.path + (frame.current_key or _OTHER_KEY,)
            frame.state = "comma_or_end"
            frame.current_key = None
            return path
        if frame.state != "value_or_end":
            self._valid_prefix = False
            return None
        frame.state = "comma_or_end"
        return frame.path + ("[]",)

    def _start_container(self, kind: Literal["object", "array"]) -> None:
        if not self._frames and self._root_value_seen and kind == "object":
            self._restart_marker_seen = True
        path = self._value_path()
        if path is None:
            return
        if not self._frames and kind == "object":
            self._root_objects_started += 1
        if path == ("claims", "[]") and kind == "object":
            self._claims_begun += 1
            self._active_claim_hasher = self._new_hasher(b"claim-object-v1")
        self._record_claim_token(b"{" if kind == "object" else b"[")
        if path == ("structure", "[]") and kind == "object":
            self._markers_begun += 1
        elif path == ("page_coverage", "[]") and kind == "object":
            self._coverage_begun += 1
        if path and path[-1] == "evidence" and kind == "object":
            self._evidence_begun += 1
        if path and path[-1] in _MAJOR_SECTIONS and kind == "array":
            self._section_starts[path[-1]] += 1
        if path and path[-1] == "evidence_segment_ids" and kind == "array":
            self._active_evidence_set_hasher = self._new_hasher(
                b"evidence-handle-set-v1"
            )
        self._frames.append(
            _JsonFrame(
                kind=kind,
                path=path,
                state="key_or_end" if kind == "object" else "value_or_end",
            )
        )

    def _end_container(self, kind: Literal["object", "array"]) -> None:
        if not self._frames or self._frames[-1].kind != kind:
            self._valid_prefix = False
            return
        frame = self._frames[-1]
        valid_end_states = (
            {"key_or_end", "comma_or_end"}
            if kind == "object"
            else {"value_or_end", "comma_or_end"}
        )
        if frame.state not in valid_end_states:
            self._valid_prefix = False
        self._record_claim_token(b"}" if kind == "object" else b"]")
        self._frames.pop()
        path = frame.path
        if path == ("claims", "[]") and kind == "object":
            self._claims_completed += 1
            if self._active_claim_hasher is not None:
                self._repetitions["complete_claim_objects"][
                    self._active_claim_hasher.digest()
                ] += 1
            self._active_claim_hasher = None
        elif path == ("structure", "[]") and kind == "object":
            self._markers_completed += 1
        elif path == ("page_coverage", "[]") and kind == "object":
            self._coverage_completed += 1
        if path and path[-1] == "evidence" and kind == "object":
            self._evidence_completed += 1
        if path and path[-1] in _MAJOR_SECTIONS and kind == "array":
            self._section_completions[path[-1]] += 1
        if path and path[-1] == "evidence_segment_ids" and kind == "array":
            if self._active_evidence_set_hasher is not None:
                self._repetitions["evidence_handle_sets"][
                    self._active_evidence_set_hasher.digest()
                ] += 1
            self._active_evidence_set_hasher = None
        if not self._frames:
            self._root_complete = True
            if kind == "object":
                self._root_objects_completed += 1

    def _accept_colon(self) -> None:
        if (
            not self._frames
            or self._frames[-1].kind != "object"
            or self._frames[-1].state != "colon"
        ):
            self._valid_prefix = False
            return
        self._record_claim_token(b":")
        self._frames[-1].state = "value"

    def _accept_comma(self) -> None:
        if not self._frames or self._frames[-1].state != "comma_or_end":
            self._valid_prefix = False
            return
        self._record_claim_token(b",")
        frame = self._frames[-1]
        frame.state = "key_or_end" if frame.kind == "object" else "value_or_end"

    def _accept_string_token(self, token: _StringToken) -> None:
        if (
            self._frames
            and self._frames[-1].kind == "object"
            and self._frames[-1].state == "key_or_end"
        ):
            self._record_claim_token(b"K", token.digest, token.length)
            key = (
                token.captured
                if token.captured in _RECOGNIZED_KEYS
                else _OTHER_KEY
            )
            frame = self._frames[-1]
            frame.current_key = key
            frame.state = "colon"
            if key in _COUNTED_FIELDS:
                self._field_counts[key] += 1
            if key == "claim_schema_version":
                self._envelope_marker_count += 1
            return
        path = self._value_path()
        if path is None:
            return
        self._record_claim_token(b"S", token.digest, token.length)
        field_name = path[-1] if path else None
        self._record_string_value(field_name, token)

    def _accept_value_token(self, token: _PrimitiveToken) -> None:
        path = self._value_path()
        if path is None:
            return
        self._record_claim_token(b"P", token.digest, token.length)

    def _record_string_value(
        self,
        field_name: str | None,
        token: _StringToken,
    ) -> None:
        length_class = {
            "source_text": "source_text",
            "source_label": "source_label",
            "section_id": "section_id",
            "step_id": "step_id",
            "target_claim_id": "target_id",
            "claim_id": "claim_id",
            "marker_id": "marker_id",
            "request_handle": "request_handle",
        }.get(field_name)
        if length_class is not None:
            self._lengths[length_class].add(token.length)
        if field_name == "source_text":
            self._repetitions["source_text_values"][token.digest] += 1
        if field_name in _RELATIONSHIP_FIELDS:
            self._repetitions["relationship_values"][token.digest] += 1
        if field_name == "claim_id":
            self._repetitions["claim_ids"][token.digest] += 1
        if field_name == "category" and token.captured in self._categories:
            assert token.captured is not None
            self._categories[token.captured] += 1
        if field_name == "kind" and token.captured in self._marker_kinds:
            assert token.captured is not None
            self._marker_kinds[token.captured] += 1
        if field_name == "status" and token.captured in self._coverage_statuses:
            assert token.captured is not None
            self._coverage_statuses[token.captured] += 1
        if len(self._frames) >= 1:
            parent = self._frames[-1]
            if parent.kind == "array" and parent.path[-1:] == (
                "evidence_segment_ids",
            ):
                self._lengths["evidence_handle"].add(token.length)
                self._evidence_references += 1
                if self._active_evidence_set_hasher is not None:
                    self._active_evidence_set_hasher.update(token.digest)
                    self._active_evidence_set_hasher.update(
                        token.length.to_bytes(8, "big")
                    )
            elif parent.kind == "array" and parent.path[-1:] == (
                "evidence_item_ids",
            ):
                self._coverage_references += 1

    def _record_claim_token(
        self,
        tag: bytes,
        digest: bytes = b"",
        length: int = 0,
    ) -> None:
        if self._active_claim_hasher is None:
            return
        self._active_claim_hasher.update(tag)
        self._active_claim_hasher.update(length.to_bytes(8, "big"))
        self._active_claim_hasher.update(digest)


def measure_protocol_claim_json_telemetry(
    raw_response: str,
) -> ProtocolClaimStructuralTelemetry:
    """Measure one complete or partial in-memory JSON string safely."""

    counter = IncrementalProtocolClaimTelemetry()
    counter.feed(raw_response)
    return counter.snapshot()
