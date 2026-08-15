"""Development-only, non-persistent curated Protocol cascade state.

The loader accepts only a byte-identified fixture and provenance sidecar, then
routes the fixture through the production PDF extraction, strict domain
decoder, recursive evidence verifier, and readiness calculation.  It never
creates a store or treats the fixture as final protocol approval.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import zlib
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pypdf import PdfReader

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ProtocolAnalysisDraft,
    parse_protocol_analysis_response,
)
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf


DEVELOPMENT_FIXTURE_STATUS = "development_only_not_final_acceptance"
DEVELOPMENT_FIXTURE_MODE = "offline_curated_development_fixture"
_CANONICAL_SCHEMA_SHA256 = (
    "3d7970faf5f55cd7ad11abbccffa01cd4f8989bb5932a436740e77bac7f23923"
)
_PROVENANCE_FIELDS = {
    "candidate_filename",
    "candidate_sha256",
    "candidate_byte_size",
    "page_count",
    "extraction_method",
    "canonical_schema_sha256",
    "fixture_sha256",
    "fixture_creation_mode",
    "validation_methods_completed",
    "ordered_step_labels",
    "status",
    "creation_timestamp",
}


class CuratedProtocolFixtureError(ValueError):
    """A sanitized fail-closed development-fixture loading error."""


class CuratedProtocolAction(str, Enum):
    START = "start"
    CURRENT = "current"
    REPEAT = "repeat"
    FULL_DETAIL = "full_detail"
    NEXT_INFORMATION = "next_information"
    COMPLETION_CRITERIA = "completion_criteria"
    OPERATIONAL_DEVIATION = "operational_deviation"
    NEXT = "next"
    QUESTION = "question"
    RELATED_QUESTION = "related_question"
    VISUAL_REQUEST = "visual_request"
    AUDIO_RECOVERY = "audio_recovery"
    TRANSCRIPT_UNRELIABLE = "transcript_unreliable"
    CANCEL_READONLY = "cancel_readonly"
    REPORT_ANOMALY = "report_anomaly"
    SHOW_REPORT = "show_report"
    PROTOCOL_QUERY = "protocol_query"
    CLARIFY_COMPLETION = "clarify_completion"
    DECLINE_COMPLETION = "decline_completion"
    CLARIFY_REFERENCE = "clarify_reference"
    CLARIFY_PARAMETER = "clarify_parameter"
    OFF_TOPIC = "off_topic"
    UNSUPPORTED = "unsupported"
    STOP = "stop"
    INACTIVE = "inactive"


class CuratedProtocolSpeechMode(str, Enum):
    CONTROL = "control"
    FULL_DETAIL = "full_detail"
    VERIFIED_FACT = "verified_fact"
    REFERENCE = "reference"
    BLOCKED = "blocked"
    STOP = "stop"


class CoreferenceStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class ProtocolVisualKind(str, Enum):
    SOURCE_CROP = "source_crop"
    TEXT_EXCERPT = "text_excerpt"


@dataclass(frozen=True)
class CuratedProtocolFact:
    fact_id: str
    kind: str
    text: str
    source_page: int


@dataclass(frozen=True)
class ProtocolVisualAsset:
    """One verified visual extracted from the immutable source document."""

    asset_id: str
    protocol_id: str
    revision_id: str
    kind: str
    source_document_id: str
    source_page: int
    mime_type: str
    sha256: str
    alt_text: str
    label: str
    caption_primary: str
    caption_source: str
    source_page_url: str
    normalized_bounding_box: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        try:
            ProtocolVisualKind(self.kind)
        except ValueError as exc:
            raise CuratedProtocolFixtureError(
                "Protocol visual kind is unsupported."
            ) from exc
        if self.normalized_bounding_box is not None and (
            len(self.normalized_bounding_box) != 4
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
                or value > 1
                for value in self.normalized_bounding_box
            )
            or (
                self.normalized_bounding_box[0]
                + self.normalized_bounding_box[2]
                > 1
            )
            or (
                self.normalized_bounding_box[1]
                + self.normalized_bounding_box[3]
                > 1
            )
        ):
            raise CuratedProtocolFixtureError(
                "Protocol visual bounding box is invalid."
            )

    def public_dict(self) -> dict[str, object]:
        encoded_protocol = quote(self.protocol_id, safe="")
        encoded_revision = quote(self.revision_id, safe="")
        encoded_asset = quote(self.asset_id, safe="")
        return {
            "asset_id": self.asset_id,
            "protocol_id": self.protocol_id,
            "revision_id": self.revision_id,
            "kind": self.kind,
            "source_document_id": self.source_document_id,
            "source_page": self.source_page,
            "mime_type": self.mime_type,
            "sha256": self.sha256,
            "alt_text": self.alt_text,
            "label": self.label,
            "caption_primary": self.caption_primary,
            "caption_source": self.caption_source,
            "source_page_url": self.source_page_url,
            "normalized_bounding_box": (
                list(self.normalized_bounding_box)
                if self.normalized_bounding_box is not None
                else None
            ),
            "url": (
                f"/api/protocols/{encoded_protocol}/revisions/"
                f"{encoded_revision}/assets/{encoded_asset}"
            ),
        }


@dataclass(frozen=True)
class CuratedProtocolFixture:
    draft: ProtocolAnalysisDraft
    status: str
    ordered_step_labels: tuple[str, ...]
    fixture_sha256: str
    revision_id: str = ""
    development_only: bool = True
    source_pdf_path: Path | None = None
    source_pdf_sha256: str | None = None
    source_filename: str | None = None
    localizations: dict[str, str] | None = None
    visual_manifest: dict[str, dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if not self.revision_id:
            object.__setattr__(
                self, "revision_id", f"fixture-{self.fixture_sha256[:20]}"
            )
        if self.source_pdf_sha256 is None:
            object.__setattr__(
                self,
                "source_pdf_sha256",
                self.draft.protocol.metadata.file_checksum,
            )

    @property
    def protocol_id(self) -> str:
        return self.draft.protocol.protocol_id

    @property
    def title(self) -> str:
        return self.draft.protocol.metadata.title

    @property
    def steps(self) -> tuple[domain.ProtocolSourceStep, ...]:
        return tuple(
            step
            for section in self.draft.protocol.sections
            for step in section.steps
        )

    def facts_for_step(self, index: int) -> tuple[CuratedProtocolFact, ...]:
        step = self.steps[index]
        facts = [
            CuratedProtocolFact(
                fact_id="current_step",
                kind="step",
                text=step.instruction_source_text,
                source_page=step.evidence.source_page_number,
            )
        ]
        for item_index, action in enumerate(step.sub_actions, 1):
            facts.append(CuratedProtocolFact(
                fact_id=f"sub_action_{item_index}",
                kind="sub_action",
                text=action.instruction_source_text,
                source_page=action.evidence.source_page_number,
            ))
        for kind, statements in (
            ("warning", step.warnings),
            ("note", step.notes),
            ("expected_result", step.expected_results),
        ):
            for item_index, item in enumerate(statements, 1):
                facts.append(CuratedProtocolFact(
                    fact_id=f"{kind}_{item_index}",
                    kind=kind,
                    text=item.source_text,
                    source_page=item.evidence.source_page_number,
                ))

        claim_text = "\n".join(fact.text for fact in facts).casefold()
        for item_index, material in enumerate(self.draft.protocol.materials, 1):
            if _resource_is_referenced(material.name_source_text, claim_text):
                facts.append(CuratedProtocolFact(
                    fact_id=f"material_{item_index}",
                    kind="material",
                    text=material.name_source_text,
                    source_page=material.evidence.source_page_number,
                ))
        for item_index, equipment in enumerate(self.draft.protocol.equipment, 1):
            if _resource_is_referenced(equipment.name_source_text, claim_text):
                facts.append(CuratedProtocolFact(
                    fact_id=f"equipment_{item_index}",
                    kind="equipment",
                    text=equipment.name_source_text,
                    source_page=equipment.evidence.source_page_number,
                ))
        if index == 0:
            for item_index, item in enumerate(
                self.draft.protocol.before_start,
                1,
            ):
                facts.append(CuratedProtocolFact(
                    fact_id=f"prerequisite_{item_index}",
                    kind="prerequisite",
                    text=item.source_text,
                    source_page=item.evidence.source_page_number,
                ))
        return tuple(facts)

    def localized_fact(self, step_id: str, fact_id: str) -> str | None:
        if self.localizations is None:
            return None
        return self.localizations.get(f"{step_id}/{fact_id}")

    def visual_for_step(self, index: int) -> ProtocolVisualAsset | None:
        """Return only an explicitly selected, verified source crop."""

        if self.source_pdf_path is None:
            return None
        step = self.steps[index]
        page = step.evidence.source_page_number
        if not isinstance(page, int) or page <= 0:
            return None
        checksum = self.source_pdf_sha256
        if not isinstance(checksum, str) or len(checksum) != 64:
            return None
        page_url = (
            f"/api/protocols/{quote(self.protocol_id, safe='')}/revisions/"
            f"{quote(self.revision_id, safe='')}/source-pages/{page}"
        )
        candidate = (self.visual_manifest or {}).get(step.step_id)
        if candidate is not None and candidate.get("selected") is True:
            content, mime_type = _verified_source_crop(
                self.source_pdf_path,
                page,
                candidate["object_name"],
                candidate["source_region_hash"],
            )
            return ProtocolVisualAsset(
                asset_id=f"source-crop-{page}-{candidate['object_name'][2:]}",
                protocol_id=self.protocol_id,
                revision_id=self.revision_id,
                kind=ProtocolVisualKind.SOURCE_CROP.value,
                source_document_id=checksum,
                source_page=page,
                mime_type=mime_type,
                sha256=hashlib.sha256(content).hexdigest(),
                alt_text=candidate["caption_primary"],
                label=f"원본 시각 자료 · PDF p.{page}",
                caption_primary=candidate["caption_primary"],
                caption_source=candidate["nearby_caption"],
                source_page_url=page_url,
                normalized_bounding_box=tuple(candidate["normalized_bounding_box"]),
            )
        return None

    def visual_content(self, index: int) -> tuple[ProtocolVisualAsset, bytes]:
        asset = self.visual_for_step(index)
        if asset is None:
            raise CuratedProtocolFixtureError("Protocol visual is unavailable.")
        candidate = (self.visual_manifest or {})[self.steps[index].step_id]
        content, _ = _verified_source_crop(
            self.source_pdf_path,
            asset.source_page,
            candidate["object_name"],
            candidate["source_region_hash"],
        )
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise CuratedProtocolFixtureError("Protocol visual identity changed.")
        return asset, content


@dataclass(frozen=True)
class CuratedProtocolTurnPlan:
    action: CuratedProtocolAction
    display_text: str | None
    speech_text: str | None
    speech_mode: CuratedProtocolSpeechMode
    facts: tuple[CuratedProtocolFact, ...]
    step_label: str | None
    final_step: bool
    state_changed: bool
    fact_id: str | None = None
    critical_warning_text: str | None = None
    primary_text: str | None = None
    source_texts: tuple[str, ...] = ()
    source_pages: tuple[int, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    translation_status: str = "not_applicable"
    intent_kind: str = "unknown"
    reported_completion: bool = False
    requested_transition: str | None = None
    requested_followup: str | None = None
    target_step: str | None = None
    intent_confidence: float | None = None
    visual_requested: bool = False
    visual_kind: str | None = None
    requested_entity: str | None = None
    requested_entities: tuple[str, ...] = ()
    question_kind: str | None = None
    reported_anomaly: bool = False
    anomaly_category: str | None = None
    anomaly_text: str | None = None
    answer_origin: str = "current_protocol"
    citations: tuple[dict[str, object], ...] = ()
    retrieval_backend: str | None = None
    retrieval_scores: tuple[float, ...] = ()
    limitations: tuple[str, ...] = ()
    normalized_transcript: str | None = None
    transcript_correction_note: str | None = None
    transcript_corrections: tuple[tuple[str, str], ...] = ()
    question_dimensions: tuple[str, ...] = ()
    source_plan_scopes: tuple[str, ...] = ()
    unresolved_dimensions: tuple[str, ...] = ()
    unresolved_claim_ids: tuple[str, ...] = ()
    coreference_status: str | None = None
    coreference_reason: str | None = None
    reported_observation: bool = False
    observation_predicate: str | None = None
    observation_outcome: str | None = None
    claim_requests: tuple["ClaimRequest", ...] = ()
    plausibility_status: str | None = None
    plausibility_reason: str | None = None

    @property
    def response_text(self) -> str | None:
        """Compatibility alias for the authoritative user-facing display."""

        return self.display_text

    @property
    def display_summary(self) -> str | None:
        return self.display_text

    @property
    def spoken_summary(self) -> str | None:
        return self.speech_text


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _png_rgb(width: int, height: int, pixels: bytes) -> bytes:
    if len(pixels) != width * height * 3:
        raise CuratedProtocolFixtureError("Source image pixel data is invalid.")

    def chunk(kind: bytes, content: bytes) -> bytes:
        return (
            struct.pack(">I", len(content))
            + kind
            + content
            + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
        )

    rows = b"".join(
        b"\0" + pixels[offset : offset + width * 3]
        for offset in range(0, len(pixels), width * 3)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk("IHDR".encode(), struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def _verified_source_crop(
    source_pdf: Path,
    page_number: int,
    object_name: str,
    source_region_hash: str,
) -> tuple[bytes, str]:
    try:
        reader = PdfReader(source_pdf)
        image = reader.pages[page_number - 1]["/Resources"]["/XObject"][
            object_name
        ].get_object()
        raw = image._data
    except Exception as exc:
        raise CuratedProtocolFixtureError(
            "Verified source image is unavailable."
        ) from exc
    if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != source_region_hash:
        raise CuratedProtocolFixtureError("Verified source image identity changed.")
    filter_name = str(image.get("/Filter"))
    if filter_name == "/DCTDecode":
        return raw, "image/jpeg"
    color_space = image.get("/ColorSpace")
    rgb = str(color_space) == "/DeviceRGB" or (
        isinstance(color_space, list)
        and len(color_space) == 2
        and str(color_space[0]) == "/ICCBased"
        and int(color_space[1].get_object().get("/N", 0)) == 3
    )
    if filter_name == "/FlateDecode" and rgb:
        width, height = int(image["/Width"]), int(image["/Height"])
        return _png_rgb(width, height, image.get_data()), "image/png"
    raise CuratedProtocolFixtureError("Verified source image format is unsupported.")


_PRESENTATION_TOKEN = re.compile(
    r"(?:\d{2}[:]\d{2}[:]\d{2}|"
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:mg/mL|ng/uL|mm3|mm³|µL|uL|mL|ml|mM|°C|rpm|min|v/v|C|h|%)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9]))",
    re.IGNORECASE,
)


def _presentation_tokens(value: str) -> frozenset[str]:
    return frozenset(
        token.casefold().replace(" ", "").replace("ul", "µl").replace("μ", "µ")
        .replace("mm3", "mm³").replace("", ":")
        for token in _PRESENTATION_TOKEN.findall(value)
    )


def _load_localizations(
    path: Path,
    *,
    fixture_sha256: str,
    source_sha256: str,
    step_facts: dict[str, dict[str, CuratedProtocolFact]],
) -> dict[str, str]:
    if not path.exists():
        return {}
    payload, raw = _load_json_object(path)
    if _canonical_json_bytes(payload) != raw or set(payload) != {
        "version", "document_sha256", "fixture_sha256", "locale", "status", "translations"
    }:
        raise CuratedProtocolFixtureError("Localization sidecar has an invalid shape.")
    if (
        payload["version"] != 1
        or payload["document_sha256"] != source_sha256
        or payload["fixture_sha256"] != fixture_sha256
        or payload["locale"] != "ko"
        or payload["status"] != DEVELOPMENT_FIXTURE_STATUS
        or not isinstance(payload["translations"], dict)
    ):
        raise CuratedProtocolFixtureError("Localization sidecar identity is invalid.")
    translations: dict[str, str] = {}
    valid_keys = {
        f"{step_id}/{fact_id}"
        for step_id, facts in step_facts.items()
        for fact_id in facts
    }
    if set(payload["translations"]) - valid_keys:
        raise CuratedProtocolFixtureError("Localization sidecar references an unknown fact.")
    for key, value in payload["translations"].items():
        if not isinstance(value, str) or not value.strip():
            raise CuratedProtocolFixtureError("Localization sidecar text is invalid.")
        step_id, fact_id = key.split("/", 1)
        source = step_facts[step_id][fact_id].text
        if _presentation_tokens(source) != _presentation_tokens(value):
            raise CuratedProtocolFixtureError(
                "Localization sidecar changed or omitted a numeric value or unit."
            )
        translations[key] = value
    return translations


def _load_visual_manifest(
    path: Path,
    *,
    fixture_sha256: str,
    source_sha256: str,
    steps: tuple[domain.ProtocolSourceStep, ...],
) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload, raw = _load_json_object(path)
    required = {"version", "document_sha256", "fixture_sha256", "status", "candidates"}
    if _canonical_json_bytes(payload) != raw or set(payload) != required:
        raise CuratedProtocolFixtureError("Visual manifest has an invalid shape.")
    if (
        payload["version"] != 1
        or payload["document_sha256"] != source_sha256
        or payload["fixture_sha256"] != fixture_sha256
        or payload["status"] != DEVELOPMENT_FIXTURE_STATUS
        or not isinstance(payload["candidates"], list)
    ):
        raise CuratedProtocolFixtureError("Visual manifest identity is invalid.")
    by_step = {step.step_id: step for step in steps}
    selected: dict[str, dict[str, Any]] = {}
    roles = {"instructional_process", "expected_result", "reference_equipment", "video_thumbnail", "decorative", "ambiguous"}
    expected_fields = {
        "page_number", "object_name", "linked_step_id", "nearby_heading", "nearby_caption",
        "caption_primary", "visual_role", "confidence", "source_region_hash",
        "normalized_bounding_box", "selected",
    }
    for item in payload["candidates"]:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise CuratedProtocolFixtureError("Visual candidate has an invalid shape.")
        step = by_step.get(item["linked_step_id"])
        box = item["normalized_bounding_box"]
        valid = (
            step is not None
            and item["page_number"] == step.evidence.source_page_number
            and isinstance(item["object_name"], str)
            and re.fullmatch(r"/X[1-9][0-9]*", item["object_name"]) is not None
            and item["visual_role"] in roles
            and item["confidence"] in {"verified", "ambiguous", "excluded"}
            and isinstance(item["source_region_hash"], str)
            and re.fullmatch(r"[0-9a-f]{64}", item["source_region_hash"]) is not None
            and isinstance(box, list) and len(box) == 4
            and all(type(value) in (int, float) and 0 <= value <= 1 for value in box)
            and box[0] + box[2] <= 1 and box[1] + box[3] <= 1
            and isinstance(item["selected"], bool)
            and all(isinstance(item[name], str) for name in (
                "nearby_heading", "nearby_caption", "caption_primary"
            ))
        )
        if not valid:
            raise CuratedProtocolFixtureError("Visual candidate is invalid.")
        if item["selected"]:
            if item["visual_role"] not in {"instructional_process", "expected_result"} or item["confidence"] != "verified":
                raise CuratedProtocolFixtureError("Unsafe visual candidate was selected.")
            if step.step_id in selected:
                raise CuratedProtocolFixtureError("Multiple visuals target one step.")
            selected[step.step_id] = dict(item)
    return selected


def _load_json_object(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CuratedProtocolFixtureError(
            "Development protocol fixture is unavailable or malformed."
        ) from exc
    if not isinstance(value, dict):
        raise CuratedProtocolFixtureError(
            "Development protocol fixture must be one JSON object."
        )
    return value, raw


def load_curated_protocol_fixture(
    fixture_path: str | Path,
    provenance_path: str | Path,
    source_pdf_path: str | Path,
) -> CuratedProtocolFixture:
    """Load one integrity-bound fixture without persistence or approval."""

    fixture_file = Path(fixture_path)
    provenance_file = Path(provenance_path)
    source_pdf_file = Path(source_pdf_path)
    payload, fixture_bytes = _load_json_object(fixture_file)
    provenance, _ = _load_json_object(provenance_file)
    if set(provenance) != _PROVENANCE_FIELDS:
        raise CuratedProtocolFixtureError(
            "Development protocol provenance has an invalid shape."
        )
    if (
        provenance["status"] != DEVELOPMENT_FIXTURE_STATUS
        or provenance["fixture_creation_mode"] != DEVELOPMENT_FIXTURE_MODE
        or provenance["extraction_method"]
        != "voice_workflow_agent.experiment_protocol_pdf.extract_protocol_pdf"
    ):
        raise CuratedProtocolFixtureError(
            "Development protocol provenance is not explicitly development-only."
        )
    completed = provenance["validation_methods_completed"]
    required_validations = {
        "canonical_json_shape",
        "strict_domain_decoder",
        "recursive_same_page_verbatim_evidence",
        "ordered_source_labels_1_through_25",
        "candidate_a_locked_development_checks",
    }
    if not isinstance(completed, list) or not required_validations.issubset(completed):
        raise CuratedProtocolFixtureError(
            "Development protocol provenance lacks required validation records."
        )
    if _canonical_json_bytes(payload) != fixture_bytes:
        raise CuratedProtocolFixtureError(
            "Development protocol fixture is not canonical JSON."
        )
    fixture_sha256 = _sha256(fixture_bytes)
    if provenance["fixture_sha256"] != fixture_sha256:
        raise CuratedProtocolFixtureError(
            "Development protocol fixture integrity verification failed."
        )
    schema_sha256 = _sha256(_canonical_json_bytes(ANALYSIS_RESPONSE_SCHEMA)[:-1])
    if (
        schema_sha256 != _CANONICAL_SCHEMA_SHA256
        or provenance["canonical_schema_sha256"] != schema_sha256
    ):
        raise CuratedProtocolFixtureError(
            "Development protocol fixture schema identity is unsupported."
        )

    extraction = extract_protocol_pdf(source_pdf_file)
    expected_pdf = (
        provenance["candidate_filename"],
        provenance["candidate_sha256"],
        provenance["candidate_byte_size"],
        provenance["page_count"],
    )
    actual_pdf = (
        extraction.original_filename,
        extraction.sha256,
        extraction.byte_size,
        extraction.page_count,
    )
    if actual_pdf != expected_pdf or extraction.encrypted:
        raise CuratedProtocolFixtureError(
            "Development protocol source identity verification failed."
        )
    try:
        draft = parse_protocol_analysis_response(
            fixture_bytes.decode("utf-8"),
            extraction,
        )
    except Exception as exc:
        raise CuratedProtocolFixtureError(
            "Development protocol fixture failed strict validation."
        ) from exc
    labels = tuple(
        step.source_label
        for section in draft.protocol.sections
        for step in section.steps
    )
    expected_labels = provenance["ordered_step_labels"]
    if (
        not isinstance(expected_labels, list)
        or not expected_labels
        or any(not isinstance(label, str) for label in expected_labels)
        or labels != tuple(expected_labels)
        or len(set(labels)) != len(labels)
    ):
        raise CuratedProtocolFixtureError(
            "Development protocol fixture step inventory is invalid."
        )
    steps = tuple(
        step
        for section in draft.protocol.sections
        for step in section.steps
    )
    sub_actions = tuple(action for step in steps for action in step.sub_actions)
    warnings = tuple(item for step in steps for item in step.warnings) + tuple(
        item for action in sub_actions for item in action.warnings)
    notes = tuple(item for step in steps for item in step.notes) + tuple(
        item for action in sub_actions for item in action.notes)
    expected_results = tuple(
        item for step in steps for item in step.expected_results
    ) + tuple(item for action in sub_actions for item in action.expected_results)
    if not all((
        draft.protocol.sections,
        draft.protocol.materials,
        draft.protocol.equipment,
        draft.protocol.before_start,
        draft.verified_evidence_count,
        warnings,
        notes,
        expected_results,
    )):
        raise CuratedProtocolFixtureError(
            "Development protocol fixture is incomplete."
        )
    if domain.ReadinessReasonCode.NO_EXECUTABLE_STEPS.value in (
        draft.readiness.reason_codes
    ):
        raise CuratedProtocolFixtureError(
            "Development protocol fixture has no executable steps."
        )
    base_fixture = CuratedProtocolFixture(
        draft=draft,
        status=DEVELOPMENT_FIXTURE_STATUS,
        ordered_step_labels=labels,
        fixture_sha256=fixture_sha256,
        source_pdf_path=source_pdf_file.resolve(),
        source_pdf_sha256=extraction.sha256,
        source_filename=extraction.original_filename,
    )
    step_facts = {
        step.step_id: {
            fact.fact_id: fact for fact in base_fixture.facts_for_step(index)
        }
        for index, step in enumerate(base_fixture.steps)
    }
    localization_path = fixture_file.with_name(
        f"{fixture_file.stem}.localization.ko.json"
    )
    visual_path = fixture_file.with_name(f"{fixture_file.stem}.visuals.json")
    return CuratedProtocolFixture(
        draft=draft,
        status=DEVELOPMENT_FIXTURE_STATUS,
        ordered_step_labels=labels,
        fixture_sha256=fixture_sha256,
        revision_id=base_fixture.revision_id,
        source_pdf_path=source_pdf_file.resolve(),
        source_pdf_sha256=extraction.sha256,
        source_filename=extraction.original_filename,
        localizations=_load_localizations(
            localization_path,
            fixture_sha256=fixture_sha256,
            source_sha256=extraction.sha256,
            step_facts=step_facts,
        ),
        visual_manifest=_load_visual_manifest(
            visual_path,
            fixture_sha256=fixture_sha256,
            source_sha256=extraction.sha256,
            steps=base_fixture.steps,
        ),
    )


def _resource_is_referenced(resource_text: str, claim_text: str) -> bool:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", resource_text.casefold())
    ignored = {"catalog", "grade", "scientific", "international", "brand"}
    return any(token not in ignored and token in claim_text for token in tokens[:8])


def _normalized_transcript(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def _utterance_key(value: str) -> str:
    key = _normalized_transcript(value).rstrip(" .?!。！？")
    key = re.sub(r"해\s*주세요$", "해줘", key)
    return re.sub(r"해\s*줘$", "해줘", key)


def _semantic_utterance_key(value: str) -> str:
    """Normalize harmless speech variation without fuzzy intent inference."""

    key = _utterance_key(value)
    # Decimal points are operational tokens, not punctuation. Preserve them so
    # 1.5 mL cannot become the unrelated value 5 mL during routing.
    key = re.sub(r"(?<!\d)\.(?!\d)|[,!?。！？;:]", " ", key)
    key = re.sub(r"^(?:어+|음+|저기)\s+", "", key)
    key = re.sub(r"\b(현재|이제)\s+\1\b", r"\1", key)
    return " ".join(key.split())


@dataclass(frozen=True)
class CuratedControlIntent:
    """Server-reviewed control projection; never an LLM-authored transition."""

    intent_kind: str
    action: CuratedProtocolAction
    reported_completion: bool = False
    requested_transition: str | None = None
    requested_followup: str | None = None
    target_step: str | None = None
    requested_entity: str | None = None
    requested_entities: tuple[str, ...] = ()
    resolved_entity: str | None = None
    question_kind: str | None = None
    detail_level: str = "concise"
    visual_requested: bool = False
    audio_recovery_requested: bool = False
    transcript_quality: str = "accepted"
    confidence: float | None = None
    confidence_source: str = "deterministic"
    requires_confirmation: bool = False
    allows_state_mutation: bool = False
    language: str = "ko"
    reported_anomaly: bool = False
    anomaly_category: str | None = None
    visual_kind: str | None = None
    normalized_transcript: str | None = None
    transcript_correction_note: str | None = None
    transcript_corrections: tuple[tuple[str, str], ...] = ()
    question_dimensions: tuple[str, ...] = ()
    protocol_scope: str | None = None
    coreference_status: str | None = None
    coreference_reason: str | None = None
    reported_observation: bool = False
    observation_predicate: str | None = None
    observation_outcome: str | None = None
    claim_requests: tuple["ClaimRequest", ...] = ()
    plausibility_status: str | None = None
    plausibility_reason: str | None = None


class DiscourseFocusKind(str, Enum):
    """Bounded semantic focus; it carries no workflow authority."""

    NONE = "none"
    ENTITY = "entity"
    PROTOCOL_PURPOSE = "protocol_purpose"
    PROTOCOL_BENEFIT = "protocol_benefit"
    CURRENT_STEP_ACTION = "current_step_action"
    CURRENT_STEP_PARAMETER = "current_step_parameter"
    OBSERVATION_PREDICATE = "observation_predicate"
    COMPARISON = "comparison"
    SAFETY = "safety"


class ClaimTargetType(str, Enum):
    ENTITY = "entity"
    PROTOCOL_PROPOSITION = "protocol_proposition"
    ACTION = "action"
    PARAMETER = "parameter"
    RATIO = "ratio"
    OBSERVATION = "observation"
    COMPARISON = "comparison"
    SAFETY = "safety"


class ClaimAdmissionStatus(str, Enum):
    LOCAL_SUPPORTED = "local_supported"
    RESEARCH_REQUIRED = "research_required"
    UNSUPPORTED_OPERATIONAL = "unsupported_operational"
    CLARIFICATION_REQUIRED = "clarification_required"


@dataclass(frozen=True)
class ClaimRequest:
    """One independently admitted or unresolved question claim."""

    claim_id: str
    target_type: ClaimTargetType
    target_id: str
    dimension: str
    required_authority: str
    evidence_ids: tuple[str, ...] = ()
    admission_status: ClaimAdmissionStatus = ClaimAdmissionStatus.RESEARCH_REQUIRED
    local_answer: str | None = None
    unresolved_reason: str | None = None
    operational: bool = False


@dataclass(frozen=True)
class StepParameterBinding:
    parameter_id: str
    value: str
    unit: str
    role: str
    action_id: str | None
    evidence_id: str
    source_page: int
    source_approved_alternative: bool = False


@dataclass(frozen=True)
class StepActionBinding:
    action_id: str
    action_type: str
    target_id: str | None
    evidence_id: str
    source_page: int
    source_text: str


@dataclass(frozen=True)
class StepRatioBinding:
    ratio_id: str
    mixture_id: str
    components: tuple[tuple[str, int], ...]
    evidence_id: str
    source_page: int


@dataclass(frozen=True)
class StepSemanticFrame:
    """Read-only index derived from canonical evidence, never a second authority."""

    step_id: str
    step_label: str
    parameters: tuple[StepParameterBinding, ...]
    actions: tuple[StepActionBinding, ...]
    ratios: tuple[StepRatioBinding, ...]
    observation_predicate_id: str | None = None


@dataclass(frozen=True)
class TranscriptPlausibility:
    status: str
    reason_code: str
    observed_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourcePlan:
    """Claim-scope contract for one read-only protocol answer."""

    scopes: tuple[str, ...]
    unresolved_dimensions: tuple[str, ...] = ()
    unresolved_claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerEnvelope:
    """Direct answer first; raw evidence remains supporting material."""

    direct_answer: str
    speech_summary: str
    entity_sections: tuple[tuple[str, str], ...]
    protocol_relevance: str
    evidence_ids: tuple[str, ...]
    source_plan: SourcePlan
    admitted_claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProtocolKnowledgeView:
    """Read-only PDF-wide projection; it never becomes execution authority."""

    title:str
    status:str
    purpose:CuratedProtocolFact
    before_start:tuple[CuratedProtocolFact,...]
    materials:tuple[CuratedProtocolFact,...]
    equipment:tuple[CuratedProtocolFact,...]
    safety:tuple[CuratedProtocolFact,...]
    sections:tuple[CuratedProtocolFact,...]

    @classmethod
    def from_fixture(cls,fixture:CuratedProtocolFixture)->"ProtocolKnowledgeView":
        pages=fixture.draft.protocol.metadata.pdf.pages
        page2=next((page.text for page in pages if page.source_page_number==2),"")

        def span(start:str,end:str)->str:
            start_at=page2.find(start)
            end_at=page2.find(end,start_at+len(start)) if start_at>=0 else -1
            if start_at<0 or end_at<0:
                raise CuratedProtocolFixtureError(
                    "Protocol-wide source span is unavailable.")
            return page2[start_at+len(start):end_at].strip()

        purpose=span("Abstract\n","\nProtocol materials")
        safety=span("Safety warnings\n","\nBefore start")
        return cls(
            title=fixture.title,status=fixture.status,
            purpose=CuratedProtocolFact(
                "protocol_purpose","purpose",purpose,2),
            before_start=tuple(CuratedProtocolFact(
                f"before_start_{index}","prerequisite",item.source_text,
                item.evidence.source_page_number,
            ) for index,item in enumerate(fixture.draft.protocol.before_start,1)),
            materials=tuple(CuratedProtocolFact(
                f"protocol_material_{index}","material",item.name_source_text,
                item.evidence.source_page_number,
            ) for index,item in enumerate(fixture.draft.protocol.materials,1)),
            equipment=tuple(CuratedProtocolFact(
                f"protocol_equipment_{index}","equipment",item.name_source_text,
                item.evidence.source_page_number,
            ) for index,item in enumerate(fixture.draft.protocol.equipment,1)),
            safety=(CuratedProtocolFact(
                "protocol_safety_warning","warning",safety,2),),
            sections=tuple(CuratedProtocolFact(
                f"protocol_section_{index}","protocol_section",
                f"{section.title_source_text}: steps "
                f"{section.steps[0].source_label}-{section.steps[-1].source_label}",
                section.evidence.source_page_number,
            ) for index,section in enumerate(fixture.draft.protocol.sections,1)),
        )


@dataclass(frozen=True)
class PendingCompletionConfirmation:
    """One server-owned, step/version-bound request to confirm completion."""

    configuration_id: int | None
    step_id: str
    step_index: int
    workflow_revision: int
    requested_turn_id: int
    requested_generation: int | None


@dataclass(frozen=True)
class PendingObservationConfirmation:
    """One step/version-bound request for an explicit visible endpoint."""

    configuration_id: int | None
    step_id: str
    step_index: int
    step_label: str
    workflow_revision: int
    requested_turn_id: int
    requested_generation: int | None
    predicate_id: str
    affirmative_outcome: str = "positive"
    negative_outcome: str = "negative"


@dataclass(frozen=True)
class ProtocolDiscourseContext:
    """Bounded, revision-owned semantic focus with no workflow authority."""

    focused_entities: tuple[str, ...] = ()
    comparison_entities: tuple[str, ...] = ()
    requested_dimension: str | None = None
    semantic_topic: str | None = None
    step_id: str | None = None
    response_language: str | None = None
    turn_id: int | None = None
    generation: int | None = None
    workflow_revision: int | None = None
    focus_kind: DiscourseFocusKind = DiscourseFocusKind.NONE
    proposition_ids: tuple[str, ...] = ()
    source_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoreferenceResolution:
    status: CoreferenceStatus
    entities: tuple[str, ...]
    reason_code: str


_COREFERENCE_REFERENCE = re.compile(
    r"(?:이게|이건|이것|그게|그건|그것|그거|그\s*물|그\s*물질|"
    r"그\s*용액|그중\s*첫\s*번째|that(?:\s+(?:material|water|solution|one))?|"
    r"the\s+first\s+one|how\s+is\s+that\s+different|tell\s+me\s+more\s+about\s+it)"
)


def resolve_bounded_coreference(
    transcript:str,*,explicit_entities:tuple[str,...],
    context:ProtocolDiscourseContext|None,
)->CoreferenceResolution:
    """Resolve one recent semantic focus without granting workflow authority."""

    if explicit_entities:
        return CoreferenceResolution(
            CoreferenceStatus.RESOLVED,explicit_entities,"explicit_entity_wins")
    key=_semantic_utterance_key(transcript)
    if _COREFERENCE_REFERENCE.search(key) is None:
        return CoreferenceResolution(
            CoreferenceStatus.UNRESOLVED,(),"no_coreference_expression")
    if context is None:
        return CoreferenceResolution(
            CoreferenceStatus.UNRESOLVED,(),"no_owned_recent_context")
    if re.search(r"(?:그중\s*첫\s*번째|the\s+first\s+one)",key):
        if context.comparison_entities:
            return CoreferenceResolution(
                CoreferenceStatus.RESOLVED,
                context.comparison_entities[:1],"ordered_comparison_first")
        return CoreferenceResolution(
            CoreferenceStatus.UNRESOLVED,(),"comparison_context_missing")
    candidates=tuple(dict.fromkeys(
        context.comparison_entities or context.focused_entities
    ))
    if len(candidates)==1:
        return CoreferenceResolution(
            CoreferenceStatus.RESOLVED,candidates,"single_recent_focus")
    if len(candidates)>1:
        return CoreferenceResolution(
            CoreferenceStatus.AMBIGUOUS,candidates,"multiple_recent_entities")
    return CoreferenceResolution(
        CoreferenceStatus.UNRESOLVED,(),"recent_focus_empty")


_COMPLETION_AND_NEXT_PATTERNS = (
    re.compile(
        r"(?:현재\s+){0,2}(?:이\s*)?(?:단계|작업)?\s*"
        r"(?:완료(?:했어|했어요|했습니다)?|끝냈어|끝냈어요|끝났어|끝났어요|다\s*했어|다\s*했어요)"
        r".*(?:다음(?:\s*단계)?|다음으로).*(?:안내|알려|넘어|진행|가자)",
    ),
    re.compile(
        r"여기까지\s*(?:했어|했고|했습니다).*(?:이제\s*)?다음(?:으로|\s*단계)",
    ),
    re.compile(
        r"(?:i\s+)?(?:finished|completed|am\s+done\s+with)\s+(?:this|the\s+current)?\s*step"
        r".*(?:next|what\s+comes\s+next)",
    ),
    re.compile(r"this\s+step\s+is\s+complete.*(?:next|what\s+comes\s+next)"),
)
_COMPLETION_ONLY_PATTERNS = (
    re.compile(
        r"^(?:(?:현재|지금)\s+)?(?:이\s*)?(?:단계|작업)(?:는|를|가)?\s*"
        r"(?:완료(?:했어|했어요|했습니다)?|끝냈어|끝냈어요|끝났습니다|"
        r"끝났어요|마쳤어|마쳤어요|마쳤습니다)$"
    ),
    re.compile(r"^(?:여기까지\s*)?(?:다\s*했어|다\s*했어요|끝났습니다)$"),
    re.compile(r"^(?:방금\s*)?(?:작업\s*)?(?:마쳤어|마쳤어요|마쳤습니다)$"),
    re.compile(r"^(?:i\s+)?completed\s+(?:the\s+)?current\s+step$"),
    re.compile(r"^(?:this|the\s+current)\s+step\s+is\s+(?:finished|complete)$"),
)
_COMPLETION_CLAIM = re.compile(
    r"(?:"
    r"(?:(?:현재|지금|이)\s*)?(?:단계|작업)(?:는|를|가)?\s*"
    r"(?:완료(?:했어|했어요|했습니다|해서|했으니|했으니까)?|"
    r"끝(?:났어|났어요|났습니다|냈어|냈어요)?|"
    r"다\s*했어|다\s*했어요|마쳤어|마쳤어요|마쳤습니다)"
    r"|(?:i\s+)?(?:completed|finished)\s+(?:this|the\s+current)\s+step"
    r"|(?:this|the\s+current)\s+step\s+is\s+(?:complete|finished)"
    r")"
)
_NEXT_STEP_REQUEST = re.compile(
    r"(?:다음(?:\s*단계)?|next(?:\s+step|\s+one)|what\s+comes\s+next)"
    r".*(?:안내|알려|넘어|진행|가자|show|tell|move|go|proceed)?"
)
_AFFIRMATIVE_COMPLETION_CONFIRMATION = re.compile(
    r"^(?:(?:네|예|응|그래|맞아|맞아요)(?:\s+(?:(?:현재|이)?\s*"
    r"(?:단계|작업)(?:를|는)?\s*)?(?:완료(?:했어|했어요|했습니다)?|"
    r"끝냈어|끝냈어요|다\s*했어|다\s*했어요|했어|했어요))?|"
    r"yes(?:\s*,?\s*(?:i\s+)?(?:finished|completed)(?:\s+it|\s+the\s+step)?)?|"
    r"done|"
    r"(?:다음\s*단계로\s*)?(?:이동할게|이동해\s*줘|넘어갈게|넘어가자|진행해|옮겨)|"
    r"yes[, ]*move\s+on|let(?:'|’)s\s+continue|proceed|"
    r"go\s+to\s+(?:the\s+)?next\s+step)$"
)
_NEGATIVE_COMPLETION_CONFIRMATION = re.compile(
    r"^(?:아니|아니요|아직|아직\s*아니야|아직\s*안\s*(?:끝났어|했어|했어요)|"
    r"아니요?\s+아직\s+안\s*(?:끝났어|했어|했어요)|"
    r"아니[,.]?\s*아직\s*안\s*끝났어|no|not\s+yet)$"
)


def _observation_predicate(step_label: str, transcript: str) -> str | None:
    """Classify only explicit source-defined endpoint observations.

    The phrases below do not invent a vision result: they preserve a user's
    report and bind it to the source endpoint for the current step. Negative
    patterns run first so phrases such as ``not transparent`` never become a
    positive result by substring overlap.
    """

    key = _semantic_utterance_key(transcript)
    if step_label == "7":
        if re.search(
            r"(?:아직.*(?:색|염색).*(?:남아|있어)|"
            r"투명하지\s*않|완전히\s*탈색되지\s*않|"
            r"still.*(?:stain|color)|not\s+(?:fully\s+)?(?:destained|transparent))",
            key,
        ):
            return "negative"
        if re.search(
            r"(?:완전히\s*탈색(?:됐|되었|됐어|됐습니다)?|"
            r"젤(?:이|은|이\s*)?\s*투명(?:해|합니다|해졌어|해요)|"
            r"fully\s+destained|gel\s+is\s+transparent)",
            key,
        ):
            return "positive"
    if step_label in {"9", "20"}:
        if re.search(
            r"(?:아직.*(?:투명|젖어|수분)|흰색이\s*아니|"
            r"not\s+(?:white|whitish|dehydrated|dry)|still\s+(?:clear|wet))",
            key,
        ):
            return "negative"
        if re.search(
            r"(?:흰색(?:으로\s*변했|이\s*됐|이야|입니다)|"
            r"탈수(?:됐|되었|됐어|됐습니다)|완전히\s*말랐|"
            r"(?:turned|is)\s+(?:white|whitish)|fully\s+(?:dehydrated|dry))",
            key,
        ):
            return "positive"
    return None
_NON_MUTATING_COMPLETION = (
    ("completion_criteria_question", re.compile(
        r"(?:완료|끝)(?:\s*조건|하려면|이라는\s*건)|"
        r"(?:condition|criteria).*(?:complete|finish)"
    )),
    ("negated_completion", re.compile(
        r"(?:아직|안)\s*(?:완료|끝)|(?:완료|끝)(?:하지|내지)\s*않|"
        r"(?:not|haven't|have\s+not)\s+(?:complete|finished|done)"
    )),
    ("quoted_completion", re.compile(
        r"[“\"]?.*(?:완료|끝).*(?:라고\s*말|라고\s*하면|say)|"
        r"다음\s*단계.*완료.*기록"
    )),
    ("hypothetical_completion", re.compile(
        r"(?:완료|끝).*(?:가정|하면|했다고\s*치면)|"
        r"(?:if|assuming).*(?:complete|finished)"
    )),
)
_REPEAT_PATTERNS = (
    re.compile(r"(?:다시\s*(?:한\s*번)?\s*(?:말|설명|안내).*(?:해줘|해\s*줄래|해\s*주세요)?)"),
    re.compile(r"(?:방금|아까)\s*(?:말|설명|안내).*(?:반복|다시)"),
    re.compile(r"(?:설명|안내).*(?:반복해줘|다시\s*말해줘)"),
    re.compile(r"(?:say|explain|tell).*(?:again|repeat)|repeat.*(?:that|guidance)"),
)
_STEP_ELABORATION_PATTERNS = (
    re.compile(
        r"(?:(?P<label>[0-9]{1,2})\s*단계|현재\s*단계|이\s*단계)"
        r".*(?:조금\s*더|더\s*)?(?:자세|상세|설명)"
    ),
    re.compile(
        r"^단계를?\s*(?:(?:좀|조금)\s*더\s*|더\s*)?(?:자세|상세).*설명"
    ),
    re.compile(
        r"(?:explain|describe).*(?:step\s*(?P<label>[0-9]{1,2})|current\s*step)"
        r".*(?:detail|more)?"
    ),
    re.compile(
        r"(?:지금|현재|이)?\s*단계에서\s*(?:뭘|무엇을)\s*해야\s*하(?:는지|나요).*"
        r"(?:자세|구체)"
    ),
    re.compile(r"(?:답변|내용).*(?:너무\s*짧|조금\s*더|더\s*)(?:자세|구체)"),
)
_AMBIGUOUS_COMPLETION_PATTERNS = (
    re.compile(r"(?:완료|끝난|다\s*한).*(?:것\s*같|맞나|할까|해도\s*될까|인가)"),
    re.compile(r"(?:maybe|i\s+think|not\s+sure).*(?:done|complete|next)"),
)
_VISUAL_REQUEST_PATTERNS = (
    re.compile(r"(?:이|현재)?\s*단계.*(?:그림|삽화|일러스트).*(?:설명|보여|그려)"),
    re.compile(r"(?:그림|삽화|일러스트).*(?:이|현재)?\s*단계"),
    re.compile(r"(?:illustrate|show\s+an?\s+illustration|draw).*(?:this|current)\s+step"),
    re.compile(r"(?:이미지|사진|그림|삽화|시각\s*자료).*(?:보여|찾아|만들|설명)"),
    re.compile(r"(?:보여|찾아|만들|설명).*(?:이미지|사진|그림|삽화|시각\s*자료)"),
    re.compile(r"(?:show|find|make|generate).*(?:image|photo|illustration|visual)"),
)
_WEB_VISUAL_REQUEST_PATTERNS = (
    re.compile(r"(?:원본|실제|인터넷|웹).*(?:사진|이미지).*(?:보여|찾아)"),
    re.compile(r"(?:find|show).*(?:real|web|source).*(?:photo|image)"),
)
_TERM_QUESTION_PATTERNS = (
    (re.compile(r"(?<![a-z0-9])ambic(?![a-z0-9])|ammonium\s+bicarbonate|암빅"), "ambic"),
    (re.compile(r"hplc\s*(?:grade\s*)?water|hplc\s*워터|hplc\s*물"), "hplc_water"),
    (re.compile(r"solution\s*a|용액\s*a|용액\s*에이|a\s*용액"), "solution_a"),
    (re.compile(r"solution\s*b|용액\s*b|용액\s*비|b\s*용액"), "solution_b"),
    (re.compile(r"acetonitrile|아세토니트릴"), "acetonitrile"),
    (re.compile(r"gel\s*plug|젤\s*플러그"), "gel_plug"),
    (re.compile(r"stained\s+protein\s+band|염색된\s*단백질\s*밴드"), "stained_protein_band"),
    (re.compile(r"(?<![a-z0-9])rpm(?![a-z0-9])|분당\s*회전"), "rpm"),
    (re.compile(r"incubat(?:e|ion)|배양"), "incubation"),
    (re.compile(r"contamination|오염"), "contamination"),
)
_TERM_QUESTION_DIMENSIONS = frozenset({
    "뭐", "무엇", "물질", "성분", "구성", "차이", "왜", "역할", "준비",
    "만들", "일반 물", "증류수", "위험", "주의", "안전", "알려", "대해서",
    "what", "which", "define", "difference", "why", "role", "contain",
    "prepare", "hazard", "safe",
})
_REPORT_REQUEST_PATTERNS = (
    re.compile(r"(?:현재\s*)?(?:실험\s*)?(?:기록|보고서).*(?:보여|열어|내보내|export)"),
    re.compile(r"(?:show|export|open).*(?:experiment\s*)?(?:report|record)"),
)
_ANOMALY_PATTERNS = (
    (re.compile(r"(?:용액|시약).*(?:잘못|틀리게).*(?:넣|준비)"), "reagent_preparation_issue"),
    (re.compile(r"(?:시료|샘플).*(?:흘렸|쏟았)"), "sample_deviation"),
    (re.compile(
        r"(?:색|색깔|투명|침전|결과).*(?:남아|이상|다르|예상|변했|변형됐|"
        r"달라|something\s+seems\s+wrong|looks\s+different|changed\s+unexpectedly)"
    ), "protocol_block"),
    (re.compile(
        r"(?:오염(?:된\s*것\s*같|됐|된\s*것이\s*보|을\s*발견)|"
        r"(?:sample|시료).*(?:contaminated|오염됐))"
    ), "contamination_concern"),
    (re.compile(r"(?:장비|기기).*(?:멈췄|고장|이상)"), "equipment_issue"),
    (re.compile(r"(?:타이머|시간|온도).*(?:끝|지났|벗어|이상)"), "timing_temperature_deviation"),
    (re.compile(r"(?:노출|spill|쏟|누출|피부|눈에)"), "spill_exposure_safety_event"),
)
_AUDIO_RECOVERY_PATTERNS = (
    re.compile(r"^(?:소리가\s*안\s*(?:나|나요|나요)|안\s*들려|음성이\s*재생되지\s*않았어)$"),
    re.compile(r"^(?:방금\s*)?(?:답변|음성).*(?:다시\s*)?(?:들려|재생해)"),
    re.compile(r"^(?:there(?:'|’)s\s+no\s+sound|i\s+can(?:'|’)t\s+hear(?:\s+the\s+answer)?|replay\s+that)$"),
)
_EXPECTED_RESULT_PATTERNS = (
    re.compile(r"(?:완전히\s*탈색|fully\s+destained).*(?:의미|무슨\s*뜻|설명|mean)"),
    re.compile(r"(?:투명|transparent).*(?:젤|gel).*(?:의미|설명|mean)"),
)
_UNRELIABLE_TRANSCRIPT_PATTERNS = (
    re.compile(r"^[\u3040-\u30ff]{2,12}$"),
    re.compile(r"^(?:yes,?\s+you\s+go|how\s+many\s+months\??\s*it\s+was\s+a\s+year)$"),
)
_NATURAL_STOP_PATTERNS = (
    re.compile(r"^(?:프로토콜(?:을)?\s*)?(?:종료|중단|중지)(?:해\s*줘|할게|할게요|하겠습니다)?$"),
    re.compile(r"^(?:여기서\s*)?(?:끝낼게|끝낼게요|그만할래|그만할게|그만할게요)$"),
    re.compile(r"^(?:please\s+)?stop(?:\s+the\s+protocol)?$"),
    re.compile(r"^(?:i(?:'|’)ll\s+)?stop\s+here$"),
)
_PROTOCOL_SCOPE_PATTERNS = (
    ("total_steps", re.compile(r"(?:이\s*실험|프로토콜|지금)?(?:은|는)?\s*총\s*몇\s*단계|총\s*단계\s*수|how\s+many\s+steps")),
    ("current_position", re.compile(r"(?:현재|지금)\s*(?:몇\s*번째|몇)\s*단계|where\s+am\s+i")),
    ("remaining_steps", re.compile(r"몇\s*단계\s*남|남은\s*단계|steps?\s+(?:are\s+)?remaining")),
    ("purpose", re.compile(
        r"(?:실험|프로토콜).*(?:목표|목적|뭐\s*하는|무슨\s*실험|어떤\s*실험|"
        r"무엇을\s*(?:위한|하는)|어떤\s*일을\s*하는)|"
        r"대체\s*이게\s*뭐\s*하는\s*실험|"
        r"what\s+(?:kind\s+of\s+)?(?:experiment|protocol).*(?:is\s+this|does\s+this\s+do|for|accomplish)|"
        r"(?:could\s+you\s+)?(?:tell|explain)(?:\s+me)?.*what\s+kind\s+of\s+"
        r"(?:experiment|protocol)\s+this\s+is|"
        r"what.*(?:protocol|experiment).*(?:meant|intended).*(?:do|accomplish)|"
        r"what\s+is\s+the\s+(?:goal|purpose|objective)|what.*experiment.*for"
    )),
    ("overview", re.compile(r"(?:전체|실험)\s*(?:흐름|과정|프로토콜).*(?:요약|설명)|overview|summari[sz]e\s+the\s+(?:whole\s+)?protocol")),
    ("materials", re.compile(r"(?:전체|프로토콜)\s*(?:재료|시약).*(?:목록|뭐|알려)|(?:what|list)\s+(?:all\s+)?(?:protocol\s+)?(?:materials|reagents)")),
    ("equipment", re.compile(r"(?:전체|프로토콜)\s*(?:장비|기기).*(?:목록|뭐|알려)|(?:what|list)\s+(?:all\s+)?(?:protocol\s+)?(?:equipment|instruments)")),
    ("preparation", re.compile(r"(?:(?:프로토콜|실험)\s*)?시작\s*전에?.*(?:준비|필요)|(?:준비물).*(?:전체|목록)|what.*prepare.*before")),
    ("safety", re.compile(r"전체\s*(?:안전\s*수칙|주의\s*사항|경고)|protocol.*(?:safety|warnings)")),
)
_SPECIFIC_STEP_PATTERN = re.compile(
    r"^(?:(?P<ko>[1-9]|1[0-9]|2[0-5])\s*단계|step\s*(?P<en>[1-9]|1[0-9]|2[0-5]))"
    r"(?:는|은|를)?\s*(?:뭐야|무엇|알려|설명|show|explain|what).*$"
)
_SOURCE_REQUEST_PATTERNS = (
    re.compile(r"^(?:방금\s*)?(?:답변의\s*)?(?:출처|근거)(?:를)?\s*(?:보여줘|알려줘|열어줘)$"),
    re.compile(r"^(?:show|open)\s+(?:the\s+)?(?:sources|citations)$"),
)
_EXTERNAL_MORE_PATTERNS = (
    re.compile(r"^(?:웹|외부\s*자료)(?:에서)?\s*(?:더\s*)?(?:찾아|검색)(?:봐|해줘)$"),
    re.compile(r"^(?:외부\s*검색|웹\s*검색)(?:은|을|를)?\s*(?:어떻게|해줘|확인해줘)?$"),
    re.compile(r"^웹에서\s*(?:확인|검색)(?:해줘)?$"),
    re.compile(r"^(?:search|look)\s+(?:the\s+)?web\s+(?:for\s+)?more$"),
)
_CANCEL_READONLY_PATTERNS = (
    re.compile(r"^(?:방금\s*)?(?:검색|자료\s*확인)(?:을|를)?\s*취소해$"),
    re.compile(r"^cancel\s+(?:that\s+)?(?:search|lookup)$"),
)
_PROTOCOL_RELATED_TERMS = frozenset({
    "단계", "프로토콜", "절차", "실험", "용액", "시약", "재료", "장비",
    "주의", "주의사항", "안전", "안전하게", "위험", "경고", "온도", "시간", "겔", "밴드", "세척",
    "탈색", "탈수", "ambic", "ammonium bicarbonate", "hplc water",
    "hplc", "acetonitrile", "solution", "reagent", "성분", "구성", "역할",
    "물질", "일반 물", "증류수", "왜", "bicarbonate", "중탄산", "케라틴",
    "protocol", "procedure", "step", "gel", "destain", "dehydrat",
    "precaution", "warning", "equipment", "material", "temperature",
    "rpm", "회전", "교반", "배양", "incubat", "contamination", "단위",
    "설정", "setting", "lc-ms", "lc ms", "grade", "등급", "색", "색깔",
})
_SAFETY_RELATED_TERMS = frozenset({
    "안전", "안전하게", "주의", "주의사항", "위험", "경고",
    "오염", "피해야", "safety", "safe", "precaution", "hazard",
    "warning", "contamination", "avoid",
})

_NAVIGATION_PATTERNS = (
    re.compile(
        r"^다음\s*단계(?:에\s*대해서)?\s*(?:로)?\s*"
        r"(?:진행|넘어|안내)(?:해\s*줘|해줘|하자|하죠|해\s*주세요)?$"
    ),
    re.compile(
        r"^(?:please\s+)?(?:guide\s+me|proceed|move|go)\s+"
        r"(?:to\s+)?(?:the\s+)?next\s+step$"
    ),
)
_NEXT_INFORMATION_PATTERNS = (
    re.compile(r"^(?:다음\s*단계(?:는|가)?\s*(?:뭐야|무엇|어떤\s*단계|알려\s*줘))$"),
    re.compile(r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?next\s+step|tell\s+me\s+what\s+the\s+next\s+step\s+is)$"),
)
_CURRENT_INFORMATION_PATTERNS = (
    re.compile(r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?current\s+step)$"),
    re.compile(r"^(?:(?:이\s*)?실험에서\s*)?(?:지금|현재)\s*단계(?:는|가)?\s*(?:뭐야|무엇|어디야)$"),
)
_OPERATIONAL_DEVIATION_PATTERNS = (
    re.compile(
        r"(?P<approved>\d+(?:\.\d+)?)\s*(?P<unit>°?c|도|rpm|mM|µL|uL|mL|min|분|시간|%)"
        r"\s*(?:대신|말고)\s*(?P<requested>\d+(?:\.\d+)?)\s*(?:°?c|도|rpm|mM|µL|uL|mL|min|분|시간|%)?"
    ),
    re.compile(r"(?:대신|바꿔|변경|substitut|replace|instead).*(?:해도\s*돼|써도\s*돼|사용해도\s*돼|가능|괜찮|can\s+i|may\s+i)"),
)
_ANOMALY_NON_ASSERTION = re.compile(
    r"(?:무슨\s*의미|왜\s*생|어떻게\s*해야|어떻게\s*해|면\s*(?:어떻게|무슨)|"
    r"아닌|아니야|않았|라고\s*(?:말|하면)|what\s+(?:does|if)|why\s+does|"
    r"if\s+the|not\s+(?:remain|changed)|say\s+that)"
)


def _scientific_entity_inventory(value: tuple[str, ...]) -> tuple[str, ...]:
    defaults = (
        "ambic", "hplc water", "solution a", "solution b", "acetonitrile",
        "gel plug", "stained protein band", "rpm", "incubation", "contamination",
    )
    return tuple(dict.fromkeys(value or defaults))


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, first in enumerate(left, 1):
        current = [row]
        for column, second in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (first != second),
            ))
        previous = current
    return previous[-1]


def normalize_scientific_request(
    transcript: str,
    *,
    entity_inventory: tuple[str, ...] = (),
) -> tuple[
    str, tuple[str, ...], str | None, tuple[tuple[str, str], ...]
]:
    """Resolve ordered known entities while preserving auditable corrections."""

    raw = _semantic_utterance_key(transcript)
    key = re.sub(
        r"(?<![a-z0-9])(?:[a-z]\s+){2,}[a-z](?![a-z0-9])",
        lambda match: match.group(0).replace(" ", ""),
        raw,
    )
    corrections: list[tuple[str, str]] = []

    def repair(pattern: str, replacement: str, label: str) -> None:
        nonlocal key
        match = re.search(pattern, key)
        if match is None:
            return
        observed = match.group(0)
        key = re.sub(pattern, replacement, key)
        if observed.casefold() != label.casefold():
            corrections.append((observed, label))

    repair(r"(?<![a-z0-9])anbi[-\s]*c?(?![a-z0-9])", "ambic", "AMBIC")
    repair(r"(?<![a-z0-9])am\s+bic(?![a-z0-9])", "ambic", "AMBIC")
    repair(r"(?<![a-z0-9])jel\s+tug(?![a-z0-9])", "gel plug", "gel plug")
    repair(r"제트\s*플러그", "젤 플러그", "gel plug")
    key = re.sub(r"에이\s*엠\s*빅", "ambic", key)
    key = re.sub(r"솔루션\s*([ab])", r"solution \1", key)
    hplc_spaced = re.search(
        r"(?<![a-z0-9])h\s+plc\s*(?:water|워터)", key
    )
    if hplc_spaced is not None:
        corrections.append((hplc_spaced.group(0), "HPLC water"))
        key = re.sub(
            r"(?<![a-z0-9])h\s+plc\s*(?:water|워터)",
            "hplc water", key,
        )
    inventory = _scientific_entity_inventory(entity_inventory)
    inventory_tokens = {
        token for entity in inventory for token in entity.split()
    }
    tokens = set(re.findall(r"[a-z]{3,}", key))
    candidates: list[tuple[str, str]] = []
    for entity in inventory:
        canonical_token = entity.split()[0]
        for token in tokens:
            if token == canonical_token or token in inventory_tokens:
                continue
            if len(token) >= 3 and _edit_distance(token, canonical_token) == 1:
                candidates.append((token, canonical_token))
    if "hplc water" in inventory and re.search(r"(?<![a-z])plc\s*(?:water|워터)", key):
        candidates.append(("plc", "hplc"))
    unique = tuple(dict.fromkeys(candidates))
    correction_note = None
    if len(unique) == 1:
        observed, replacement = unique[0]
        key = re.sub(rf"(?<![a-z0-9]){re.escape(observed)}(?![a-z0-9])", replacement, key)
        corrections.append((observed, replacement.upper()))
    matches: list[tuple[int, str]] = []
    for pattern, name in _TERM_QUESTION_PATTERNS:
        for match in pattern.finditer(key):
            matches.append((match.start(), name))
    entities = tuple(dict.fromkeys(
        name for _, name in sorted(matches, key=lambda item: item[0])
    ))
    labels = {
        "ambic": "AMBIC", "hplc_water": "HPLC water",
        "solution_a": "Solution A", "solution_b": "Solution B",
        "acetonitrile": "acetonitrile", "gel_plug": "gel plug",
        "stained_protein_band": "stained protein band",
        "rpm": "rpm", "incubation": "incubation",
        "contamination": "contamination",
    }
    if key != raw and not corrections and entities:
        corrections.append((raw, labels[entities[0]]))
    corrections = list(dict.fromkeys(corrections))
    if corrections:
        correction_note = " / ".join(
            f'인식된 음성: “{observed}” · 문맥상 해석: “{label}”'
            for observed, label in corrections
        )
    return key, entities, correction_note, tuple(corrections)


def normalize_scientific_query(
    transcript: str,
    *,
    entity_inventory: tuple[str, ...] = (),
) -> tuple[str, str | None, str | None]:
    """Backward-compatible single-entity view of normalized scientific input."""

    key, entities, correction_note, _ = normalize_scientific_request(
        transcript, entity_inventory=entity_inventory,
    )
    return key, (entities[0] if entities else None), correction_note


def question_dimensions(value: str) -> tuple[str, ...]:
    dimensions: list[str] = []
    patterns = (
        ("definition", ("뭐", "무엇", "물질", "정의", "알려", "대해서", "what", "define")),
        ("composition", ("성분", "구성", "들어가", "contain", "composition")),
        ("role", ("왜", "역할", "쓰", "목적", "why", "role", "purpose")),
        ("mechanism", ("작동", "원리", "메커니즘", "mechanism", "how does")),
        ("difference", ("차이", "다르", "다른", "difference", "versus")),
        ("preparation", ("준비", "만들", "prepare", "make")),
        ("safety", tuple(_SAFETY_RELATED_TERMS)),
        ("expected_result", ("결과", "투명", "탈색", "result", "destain")),
        ("visual", ("사진", "이미지", "그림", "photo", "image", "visual")),
    )
    for name, terms in patterns:
        if any(term in value for term in terms):
            dimensions.append(name)
    return tuple(dimensions or ("related_knowledge",))


_DERIVED_SOURCE_GLYPHS = str.maketrans({
    "\ue081": "(", "\ue082": ")", "\ue088": "-", "\ue092": ":",
})
_PARAMETER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mm3|mm³|µL|uL|mL|ml|mM|°C|C|도|rpm|min|minutes?|분|시간|%)"
    r"(?![A-Za-z0-9])",
    re.I,
)
_BARE_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])")


def _derived_source_text(value: str) -> str:
    """Normalize only an in-memory derived view; raw evidence stays untouched."""

    return value.translate(_DERIVED_SOURCE_GLYPHS).replace("mm3", "mm³")


def _parameter_role(unit: str, text: str) -> str:
    normalized = unit.casefold().replace("ul", "µl").replace("μ", "µ")
    if normalized in {"°c", "c"}:
        return "incubation_temperature"
    if normalized in {"min", "minute", "minutes", "분", "시간"}:
        return "incubation_duration"
    if normalized == "rpm":
        return "agitation_speed"
    if normalized == "ml" and "tube" in text.casefold():
        return "vessel_capacity"
    if normalized == "µl":
        return "solution_volume"
    if normalized == "mm":
        return "concentration"
    if normalized == "mm³":
        return "gel_piece_size"
    return "operational_parameter"


def _action_type(text: str) -> str | None:
    lowered = text.casefold()
    for action_type, terms in (
        ("remove_discard", ("remove", "discard", "제거", "버리")),
        ("incubate", ("incubat", "배양")),
        ("wash", ("wash", "세척")),
        ("prepare", ("prepare", "준비")),
        ("place", ("place", "넣")),
        ("cut", ("cut", "excise", "slice", "자르", "절취")),
        ("repeat", ("repeat", "반복")),
    ):
        if any(term in lowered for term in terms):
            return action_type
    return None


def _target_from_text(text: str) -> str | None:
    lowered = text.casefold()
    for target, terms in (
        ("solution_a", ("solution a",)),
        ("solution_b", ("solution b",)),
        ("gel_plug", ("gel plug", "gel band", "stained protein band")),
        ("acetonitrile", ("acetonitrile",)),
        ("sample_material", ("material", "sample")),
    ):
        if any(term in lowered for term in terms):
            return target
    return None


def build_step_semantic_frame(
    fixture: CuratedProtocolFixture,
    step_index: int,
) -> StepSemanticFrame:
    """Index canonical step facts for read-only semantic binding."""

    step = fixture.steps[step_index]
    parameters: list[StepParameterBinding] = []
    actions: list[StepActionBinding] = []
    ratios: list[StepRatioBinding] = []
    for fact in fixture.facts_for_step(step_index):
        derived = _derived_source_text(fact.text)
        semantic_fact = fact.kind in {"step", "note"}
        action_type = _action_type(derived) if semantic_fact else None
        action_id = f"{fact.fact_id}:{action_type}" if action_type else None
        if action_type:
            actions.append(StepActionBinding(
                action_id=action_id or fact.fact_id,
                action_type=action_type,
                target_id=_target_from_text(derived),
                evidence_id=fact.fact_id,
                source_page=fact.source_page,
                source_text=fact.text,
            ))
        for ordinal, match in enumerate(
            _PARAMETER_PATTERN.finditer(derived) if semantic_fact else (), 1
        ):
            unit = match.group("unit").replace("uL", "µL")
            parameters.append(StepParameterBinding(
                parameter_id=f"{fact.fact_id}:parameter:{ordinal}",
                value=match.group("value"),
                unit=unit,
                role=_parameter_role(unit, derived),
                action_id=action_id,
                evidence_id=fact.fact_id,
                source_page=fact.source_page,
                source_approved_alternative=fact.kind == "note",
            ))
        ratio = re.search(
            r"(?P<first>\d+)\s+parts?\s+of\s+25\s*mM\s+ammonium\s+bicarbonate"
            r".*?mixed\s+with\s+(?P<second>\d+)\s+part\s+acetonitrile",
            derived,
            re.I | re.S,
        )
        if ratio:
            ratios.append(StepRatioBinding(
                ratio_id=f"{fact.fact_id}:solution_a_ratio",
                mixture_id="solution_a",
                components=(("ambic_solution", int(ratio.group("first"))),
                            ("acetonitrile", int(ratio.group("second")))),
                evidence_id=fact.fact_id,
                source_page=fact.source_page,
            ))
    predicate = (
        f"candidate_a_step_{step.source_label}_endpoint"
        if step.source_label in {"7", "9", "20"} else None
    )
    deduplicated: dict[tuple[str, str, str], StepParameterBinding] = {}
    for item in parameters:
        normalized_unit = item.unit.casefold().replace("μ", "µ")
        normalized_unit = {
            "c": "°c", "°c": "°c", "도": "°c",
            "ul": "µl", "µl": "µl",
        }.get(normalized_unit, normalized_unit)
        deduplicated.setdefault(
            (item.value, normalized_unit, item.role), item
        )
    deduplicated_parameters = tuple(deduplicated.values())
    return StepSemanticFrame(
        step.step_id, step.source_label, deduplicated_parameters, tuple(actions),
        tuple(ratios), predicate,
    )


def assess_transcript_plausibility(
    transcript: str,
    frame: StepSemanticFrame,
) -> TranscriptPlausibility:
    """Classify operational tokens without rewriting any user value."""

    def canonical_token(value: str, unit: str) -> str:
        normalized_unit = unit.casefold().replace("μ", "µ")
        normalized_unit = {
            "c": "°c", "°c": "°c", "도": "°c",
            "분": "min", "minute": "min", "minutes": "min",
            "ul": "µl", "µl": "µl",
        }.get(normalized_unit, normalized_unit)
        return f"{value}{normalized_unit}".replace(" ", "")

    key = _semantic_utterance_key(transcript)
    source_tokens = {
        canonical_token(item.value, item.unit)
        for item in frame.parameters
    }
    parameter_matches = tuple(_PARAMETER_PATTERN.finditer(key))
    observed = tuple(
        canonical_token(match.group("value"), match.group("unit"))
        for match in parameter_matches
    )
    exact = tuple(token for token in observed if token in source_tokens)
    alternatives = {
        canonical_token(item.value, item.unit)
        for item in frame.parameters if item.source_approved_alternative
    }
    if observed and all(token in source_tokens for token in observed):
        status = "source_approved_alternative" if any(
            token in alternatives for token in observed
        ) else "exact_compatible"
        return TranscriptPlausibility(status, status, observed)
    comparison = bool(re.search(
        r"(?:대신|말고|차이|비교|instead|rather\s+than|different)", key
    ))
    if observed and exact and comparison:
        return TranscriptPlausibility(
            "plausible_explanatory_comparison",
            "active_value_and_proposed_value_kept_distinct",
            observed,
        )
    bare = tuple(
        match.group(0)
        for match in _BARE_NUMBER_PATTERN.finditer(key)
        if not any(
            parameter.start() <= match.start() < parameter.end()
            for parameter in parameter_matches
        )
        and not re.match(r"\s*(?:단계|step\b)", key[match.end():])
    )
    operational_frame = bool(re.search(
        r"(?:섞|비율|넣|사용|설정|온도|시간|회전|단계|해도\s*돼|"
        r"mix|ratio|use|set|temperature|duration|step)", key
    ))
    if operational_frame and (
        any(token not in source_tokens for token in observed)
        or (bare and not observed)
    ):
        tokens = observed or bare
        return TranscriptPlausibility(
            "incompatible_suspicious", "value_not_bound_to_current_step", tokens
        )
    return TranscriptPlausibility("not_applicable", "no_operational_token", observed)


def _binary_frame_reply(value: str) -> str | None:
    """Interpret a short answer only after a server-owned binary question."""

    key = _semantic_utterance_key(value)
    if re.fullmatch(
        r"(?:(?:네|예|응|그래|맞아|맞아요|물론)(?:\s+|$))*"
        r"(?:(?:전부|모두)\s*)?(?:다\s*(?:했어|했어요|끝냈어|끝냈어요)|"
        r"마쳤어|마쳤어요|끝냈어|끝냈어요|"
        r"완료(?:했어|했어요|했습니다)?|했어|했어요|됐어|됐어요)?"
        r"|yes(?:\s+i\s+(?:did|finished))?|done|correct|that(?:'|’)s\s+right",
        key,
    ) and key:
        return "affirmative"
    if re.fullmatch(
        r"(?:아니|아니요|아직|아직\s*아니야|아직\s*안\s*(?:했어|됐어|끝났어)|"
        r"no|not\s+yet|no,?\s+not\s+yet)", key,
    ):
        return "negative"
    return None


def classify_curated_control_intent(
    transcript: str,
    *,
    language: str,
    entity_inventory: tuple[str, ...] = (),
    recent_related_query: str | None = None,
    recent_related_entities: tuple[str, ...] = (),
    discourse_context: ProtocolDiscourseContext | None = None,
    completion_context: bool = False,
) -> CuratedControlIntent:
    """Classify reviewed workflow shapes before any knowledge or model route."""

    key, normalized_entities, correction_note, corrections = (
        normalize_scientific_request(
        transcript, entity_inventory=entity_inventory
        )
    )
    coreference=resolve_bounded_coreference(
        key,explicit_entities=normalized_entities,context=discourse_context)
    if (
        not normalized_entities
        and coreference.status is CoreferenceStatus.RESOLVED
    ):
        normalized_entities=coreference.entities
    normalized_entity = normalized_entities[0] if normalized_entities else None
    dimensions = question_dimensions(key)
    if len(normalized_entities) > 1 and "relationship" not in dimensions:
        dimensions = (*dimensions, "relationship")
    if any(pattern.fullmatch(key) for pattern in _NATURAL_STOP_PATTERNS):
        return CuratedControlIntent(
            intent_kind="workflow_command", action=CuratedProtocolAction.STOP,
            language=language, allows_state_mutation=True,
            normalized_transcript=key,
        )
    purpose_followup = bool(
        discourse_context is not None
        and discourse_context.focus_kind in {
            DiscourseFocusKind.PROTOCOL_PURPOSE,
            DiscourseFocusKind.PROTOCOL_BENEFIT,
        }
        and re.search(
            r"(?:그(?:게|것|거|게는)|그\s*(?:목적|장점)|왜\s*(?:유용|중요)|"
            r"장점|어떤\s*도움|why\s+(?:is\s+)?that\s+(?:useful|important)|"
            r"how\s+does\s+that\s+help|why\s+does\s+that\s+matter)", key,
        )
    )
    if purpose_followup:
        return CuratedControlIntent(
            intent_kind="protocol_purpose_followup",
            action=CuratedProtocolAction.RELATED_QUESTION,
            requested_followup="explain_protocol_benefit",
            target_step="authoritative_current_step",
            question_kind="protocol_benefit",
            language=language,
            normalized_transcript=key,
            question_dimensions=("rationale",),
            coreference_status=CoreferenceStatus.RESOLVED.value,
            coreference_reason="recent_admitted_protocol_purpose",
        )
    if (
        not normalized_entities
        and _COREFERENCE_REFERENCE.search(key)
        and not any(pattern.search(key) for pattern in _REPEAT_PATTERNS)
        and not re.search(
            r"(?:용액|그거|방금\s*말한\s*것).*(?:어떻게|준비|만들|구성|비율)",
            key,
        )
        and coreference.status in {
            CoreferenceStatus.AMBIGUOUS,CoreferenceStatus.UNRESOLVED,
        }
    ):
        return CuratedControlIntent(
            intent_kind=(
                "ambiguous_protocol_entity"
                if coreference.status is CoreferenceStatus.AMBIGUOUS
                else "unresolved_protocol_entity"
            ),
            action=CuratedProtocolAction.CLARIFY_REFERENCE,
            requested_entities=coreference.entities,
            question_kind="contextual_reference",
            language=language,normalized_transcript=key,
            question_dimensions=dimensions,
            coreference_status=coreference.status.value,
            coreference_reason=coreference.reason_code,
        )
    if any(pattern.fullmatch(key) for pattern in _NEXT_INFORMATION_PATTERNS):
        return CuratedControlIntent(
            intent_kind="next_step_information",
            action=CuratedProtocolAction.NEXT_INFORMATION,
            requested_transition=None,
            requested_followup="preview_next_step",
            target_step="authoritative_next_step",
            question_kind="navigation_information",
            language=language,
            normalized_transcript=key,
        )
    if any(pattern.fullmatch(key) for pattern in _CURRENT_INFORMATION_PATTERNS):
        return CuratedControlIntent(
            intent_kind="current_step_information",
            action=CuratedProtocolAction.CURRENT,
            requested_followup="describe_current_step",
            target_step="authoritative_current_step",
            question_kind="navigation_information",
            language=language,
            normalized_transcript=key,
        )
    exact = _WORKFLOW_COMMANDS.get(key)
    if exact is not None:
        if exact is CuratedProtocolAction.NEXT:
            return CuratedControlIntent(
                intent_kind="next_step_confirmation_required",
                action=CuratedProtocolAction.CLARIFY_COMPLETION,
                requested_transition="next",
                requested_followup="confirm_current_step_completion",
                target_step="authoritative_current_step",
                requires_confirmation=True,
                language=language,
                normalized_transcript=key,
            )
        return CuratedControlIntent(
            intent_kind="workflow_command",
            action=exact,
            requested_transition="next" if exact is CuratedProtocolAction.NEXT else None,
            requested_followup=(
                "describe_new_current_step"
                if exact is CuratedProtocolAction.NEXT
                else None
            ),
            language=language,
            allows_state_mutation=exact in {
                CuratedProtocolAction.START, CuratedProtocolAction.NEXT,
                CuratedProtocolAction.STOP,
            },
            normalized_transcript=key,
        )
    for scope, pattern in _PROTOCOL_SCOPE_PATTERNS:
        if pattern.search(key):
            return CuratedControlIntent(
                intent_kind=f"protocol_{scope}",
                action=CuratedProtocolAction.PROTOCOL_QUERY,
                question_kind="protocol_metadata",
                language=language,
                protocol_scope=scope,
                normalized_transcript=key,
            )
    if match := _SPECIFIC_STEP_PATTERN.fullmatch(key):
        label = match.group("ko") or match.group("en")
        return CuratedControlIntent(
            intent_kind="specific_step_lookup",
            action=CuratedProtocolAction.FULL_DETAIL,
            requested_followup="explain_step", target_step=label,
            detail_level="detailed", language=language,
            normalized_transcript=key,
        )
    non_mutating_completion = next(
        (kind for kind, pattern in _NON_MUTATING_COMPLETION if pattern.search(key)),
        None,
    )
    completion_claimed = bool(_COMPLETION_CLAIM.search(key))
    next_requested = bool(_NEXT_STEP_REQUEST.search(key))
    if any(pattern.search(key) for pattern in _AMBIGUOUS_COMPLETION_PATTERNS):
        return CuratedControlIntent(
            intent_kind="ambiguous_completion",
            action=CuratedProtocolAction.CLARIFY_COMPLETION,
            confidence=None,
            confidence_source="deterministic_ambiguity",
            requires_confirmation=True,
            target_step="authoritative_current_step",
            language=language,
        )
    if non_mutating_completion == "completion_criteria_question":
        return CuratedControlIntent(
            intent_kind=non_mutating_completion,
            action=CuratedProtocolAction.COMPLETION_CRITERIA,
            requested_followup="explain_completion_criteria",
            target_step="authoritative_current_step",
            detail_level="detailed",
            language=language,
            normalized_transcript=key,
        )
    if non_mutating_completion is not None:
        return CuratedControlIntent(
            intent_kind=non_mutating_completion,
            action=CuratedProtocolAction.OFF_TOPIC,
            language=language,
            normalized_transcript=key,
        )
    if completion_claimed:
        return CuratedControlIntent(
            intent_kind=(
                "completion_and_next" if next_requested else "report_completion"
            ),
            action=CuratedProtocolAction.NEXT,
            reported_completion=True,
            requested_transition="next",
            requested_followup="describe_new_current_step",
            target_step="authoritative_current_step",
            language=language,
            allows_state_mutation=True,
            normalized_transcript=key,
        )
    if completion_context and re.fullmatch(
        r"장기(?:를|가)?\s*완료(?:했어|했어요|했습니다)", key
    ):
        return CuratedControlIntent(
            intent_kind="ambiguous_completion",
            action=CuratedProtocolAction.CLARIFY_COMPLETION,
            target_step="authoritative_current_step",
            confidence_source="bounded_contextual_repair",
            requires_confirmation=True,
            language=language,
            normalized_transcript=key,
            transcript_correction_note=(
                '인식된 음성: “장기를 완료했어” · '
                '문맥상 “현재 단계를 완료했어”인지 확인이 필요합니다.'
            ),
            transcript_corrections=(("장기", "현재 단계"),),
        )
    if any(pattern.search(key) for pattern in _NAVIGATION_PATTERNS):
        return CuratedControlIntent(
            intent_kind="next_step_confirmation_required",
            action=CuratedProtocolAction.CLARIFY_COMPLETION,
            requested_transition="next",
            requested_followup="confirm_current_step_completion",
            target_step="authoritative_current_step",
            requires_confirmation=True,
            language=language,
            allows_state_mutation=False,
            normalized_transcript=key,
        )
    if any(pattern.search(key) for pattern in _AUDIO_RECOVERY_PATTERNS):
        return CuratedControlIntent(
            intent_kind="audio_playback_help",
            action=CuratedProtocolAction.AUDIO_RECOVERY,
            audio_recovery_requested=True,
            requested_followup="replay_last_answer",
            language=language,
        )
    if any(pattern.search(key) for pattern in _REPEAT_PATTERNS):
        return CuratedControlIntent(
            intent_kind="workflow_command",
            action=CuratedProtocolAction.REPEAT,
            requested_followup="repeat_spoken_guidance",
            language=language,
        )
    if any(pattern.search(key) for pattern in _COMPLETION_AND_NEXT_PATTERNS):
        return CuratedControlIntent(
            intent_kind="completion_and_next",
            action=CuratedProtocolAction.NEXT,
            reported_completion=True,
            requested_transition="next",
            requested_followup="describe_new_current_step",
            target_step="authoritative_current_step",
            language=language,
            allows_state_mutation=True,
        )
    if any(pattern.search(key) for pattern in _COMPLETION_ONLY_PATTERNS):
        return CuratedControlIntent(
            intent_kind="report_completion",
            action=CuratedProtocolAction.NEXT,
            reported_completion=True,
            requested_transition="next",
            requested_followup="describe_new_current_step",
            target_step="authoritative_current_step",
            language=language,
            allows_state_mutation=True,
        )
    if key in _FULL_DETAIL_COMMANDS:
        return CuratedControlIntent(
            intent_kind="full_detail",
            action=CuratedProtocolAction.FULL_DETAIL,
            language=language,
        )
    for pattern in _STEP_ELABORATION_PATTERNS:
        if match := pattern.search(key):
            label = match.groupdict().get("label")
            return CuratedControlIntent(
                intent_kind="step_elaboration",
                action=CuratedProtocolAction.FULL_DETAIL,
                requested_followup="explain_step",
                target_step=label or "authoritative_current_step",
                detail_level="detailed",
                language=language,
            )
    if any(pattern.search(key) for pattern in _EXPECTED_RESULT_PATTERNS):
        return CuratedControlIntent(
            intent_kind="expected_result_explanation",
            action=CuratedProtocolAction.FULL_DETAIL,
            requested_followup="explain_expected_result",
            target_step="7",
            question_kind="expected_result",
            detail_level="detailed",
            language=language,
        )
    if (
        any(pattern.search(key) for pattern in _VISUAL_REQUEST_PATTERNS)
        or any(pattern.search(key) for pattern in _WEB_VISUAL_REQUEST_PATTERNS)
    ):
        visual_kind = (
            "web_photo"
            if (
                any(pattern.search(key) for pattern in _WEB_VISUAL_REQUEST_PATTERNS)
                or any(term in key for term in ("이미지", "사진", "photo", "image"))
            )
            else "instructional_illustration"
        )
        return CuratedControlIntent(
            intent_kind="visual_request",
            action=CuratedProtocolAction.VISUAL_REQUEST,
            target_step="authoritative_current_step",
            visual_requested=True,
            visual_kind=visual_kind,
            requested_entity=normalized_entity,
            requested_entities=normalized_entities,
            resolved_entity=normalized_entity,
            transcript_correction_note=correction_note,
            transcript_corrections=corrections,
            question_dimensions=dimensions,
            language=language,
            coreference_status=coreference.status.value,
            coreference_reason=coreference.reason_code,
        )
    if any(pattern.search(key) for pattern in _REPORT_REQUEST_PATTERNS):
        return CuratedControlIntent(
            intent_kind="show_experiment_report",
            action=CuratedProtocolAction.SHOW_REPORT,
            requested_followup="show_experiment_report",
            language=language,
        )
    if any(pattern.search(key) for pattern in _OPERATIONAL_DEVIATION_PATTERNS):
        return CuratedControlIntent(
            intent_kind="operational_deviation",
            action=CuratedProtocolAction.OPERATIONAL_DEVIATION,
            requested_followup="explain_without_authorizing",
            target_step="authoritative_current_step",
            question_kind="operational_deviation",
            language=language,
            normalized_transcript=key,
            requested_entity=normalized_entity,
            requested_entities=normalized_entities,
            resolved_entity=normalized_entity,
            question_dimensions=("operational_deviation", "rationale"),
            protocol_scope="UNSUPPORTED_OPERATIONAL",
        )
    for pattern, category in _ANOMALY_PATTERNS:
        if pattern.search(key) and not _ANOMALY_NON_ASSERTION.search(key):
            return CuratedControlIntent(
                intent_kind="record_anomaly",
                action=CuratedProtocolAction.REPORT_ANOMALY,
                question_kind="anomaly",
                language=language,
                reported_anomaly=True,
                anomaly_category=category,
            )
    if any(pattern.search(key) for pattern in _SOURCE_REQUEST_PATTERNS):
        return CuratedControlIntent(
            intent_kind="show_sources",
            action=CuratedProtocolAction.FULL_DETAIL,
            requested_followup="show_existing_sources",
            target_step="authoritative_current_step",
            detail_level="detailed",
            language=language,
        )
    if any(pattern.search(key) for pattern in _EXTERNAL_MORE_PATTERNS):
        return CuratedControlIntent(
            intent_kind="external_reference_followup",
            action=CuratedProtocolAction.RELATED_QUESTION,
            requested_followup="search_external_reference",
            target_step="authoritative_current_step",
            question_kind="related_knowledge",
            language=language,
            normalized_transcript=key,
            question_dimensions=dimensions,
            coreference_status=coreference.status.value,
            coreference_reason=coreference.reason_code,
        )
    if any(pattern.search(key) for pattern in _CANCEL_READONLY_PATTERNS):
        return CuratedControlIntent(
            intent_kind="cancel_readonly_operation",
            action=CuratedProtocolAction.CANCEL_READONLY,
            requested_followup="cancel_readonly_operation",
            language=language,
        )
    if any(pattern.search(key) for pattern in _UNRELIABLE_TRANSCRIPT_PATTERNS):
        return CuratedControlIntent(
            intent_kind="transcript_unreliable",
            action=CuratedProtocolAction.TRANSCRIPT_UNRELIABLE,
            transcript_quality="unreliable_language_mismatch",
            confidence=None,
            confidence_source="no_provider_confidence_conservative_rule",
            language=language,
        )
    if re.fullmatch(r"투\s*루(?:를)?\s*시작해\s*줘", key):
        # Reviewed live STT corruption of a mutation-sensitive start request.
        # It is a clarification candidate, never an automatic workflow action.
        return CuratedControlIntent(
            intent_kind="ambiguous_protocol_start",
            action=CuratedProtocolAction.TRANSCRIPT_UNRELIABLE,
            transcript_quality="mutation_sensitive_ambiguous",
            confidence=None,
            confidence_source="bounded_contextual_repair",
            requires_confirmation=True,
            language=language,
            normalized_transcript=key,
            transcript_correction_note=(
                '인식된 음성: “투 루를 시작해 줘” · '
                '“프로토콜을 시작해 줘”인지 확인이 필요합니다.'
            ),
            transcript_corrections=(("투 루", "프로토콜"),),
        )
    requested_entity = normalized_entity
    if requested_entity is not None and any(
        dimension in key for dimension in _TERM_QUESTION_DIMENSIONS
    ):
        return CuratedControlIntent(
            intent_kind="protocol_entity_question",
            action=CuratedProtocolAction.RELATED_QUESTION,
            target_step="authoritative_current_step",
            requested_entity=requested_entity,
            requested_entities=normalized_entities,
            resolved_entity=requested_entity,
            question_kind=(
                "safety"
                if any(term in key for term in _SAFETY_RELATED_TERMS)
                else "scientific_definition"
            ),
            language=language,
            normalized_transcript=key,
            transcript_correction_note=correction_note,
            transcript_corrections=corrections,
            question_dimensions=dimensions,
            coreference_status=coreference.status.value,
            coreference_reason=coreference.reason_code,
        )
    if (
        _PARAMETER_PATTERN.search(key)
        and re.search(
            r"(?:뭐|무엇|왜|역할|의미|값|설명|what|why|mean|explain|for)", key
        )
    ):
        return CuratedControlIntent(
            intent_kind="current_step_parameter_question",
            action=CuratedProtocolAction.RELATED_QUESTION,
            target_step="authoritative_current_step",
            question_kind="parameter",
            language=language,
            normalized_transcript=key,
            question_dimensions=("role",),
            requested_entity=normalized_entity,
            requested_entities=normalized_entities,
            resolved_entity=normalized_entity,
        )
    if any(term in key for term in _PROTOCOL_RELATED_TERMS):
        question_kind = (
            "safety"
            if any(term in key for term in _SAFETY_RELATED_TERMS)
            else "related_knowledge"
        )
        return CuratedControlIntent(
            intent_kind=(
                "related_safety_question"
                if question_kind == "safety"
                else "related_question"
            ),
            action=CuratedProtocolAction.RELATED_QUESTION,
            target_step="authoritative_current_step",
            question_kind=question_kind,
            language=language,
            normalized_transcript=key,
            transcript_correction_note=correction_note,
            question_dimensions=dimensions,
            requested_entity=normalized_entity,
            requested_entities=normalized_entities,
            resolved_entity=normalized_entity,
            coreference_status=coreference.status.value,
            coreference_reason=coreference.reason_code,
        )
    if recent_related_query and key in {"되는 거 아니야", "그건 왜 써", "위험하지 않아"}:
        return CuratedControlIntent(
            intent_kind="related_followup",
            action=CuratedProtocolAction.RELATED_QUESTION,
            requested_followup="continue_related_question",
            target_step="authoritative_current_step",
            question_kind="related_knowledge",
            language=language,
            normalized_transcript=key,
            question_dimensions=dimensions,
        )
    return CuratedControlIntent(
        intent_kind="off_topic",
        action=CuratedProtocolAction.OFF_TOPIC,
        language=language,
    )


_WORKFLOW_COMMANDS = {
    "프로토콜 시작": CuratedProtocolAction.START,
    "프로토콜을 시작해줘": CuratedProtocolAction.START,
    "프로토콜 시작해줘": CuratedProtocolAction.START,
    "실험을 진행해줘": CuratedProtocolAction.START,
    "프로토콜을 진행해줘": CuratedProtocolAction.START,
    "프로토콜 진행해줘": CuratedProtocolAction.START,
    "절차를 진행해줘": CuratedProtocolAction.START,
    "프로토콜 재개": CuratedProtocolAction.START,
    "프로토콜 계속": CuratedProtocolAction.START,
    "재개": CuratedProtocolAction.START,
    "계속": CuratedProtocolAction.START,
    "start": CuratedProtocolAction.START,
    "resume": CuratedProtocolAction.START,
    "현재 단계": CuratedProtocolAction.CURRENT,
    "현재 단계를 알려줘": CuratedProtocolAction.CURRENT,
    "현재 단계 알려줘": CuratedProtocolAction.CURRENT,
    "현재 단계가 뭐야": CuratedProtocolAction.CURRENT,
    "현재 단계 다시 알려줘": CuratedProtocolAction.CURRENT,
    "현재 단계를 다시 알려줘": CuratedProtocolAction.CURRENT,
    "지금 무슨 단계야": CuratedProtocolAction.CURRENT,
    "current step": CuratedProtocolAction.CURRENT,
    "다시 말해 줘": CuratedProtocolAction.REPEAT,
    "다시 말해줘": CuratedProtocolAction.REPEAT,
    "반복": CuratedProtocolAction.REPEAT,
    "repeat": CuratedProtocolAction.REPEAT,
    "다음": CuratedProtocolAction.NEXT,
    "다음 단계로 넘어가 줘": CuratedProtocolAction.NEXT,
    "다음 단계로 넘어가죠": CuratedProtocolAction.NEXT,
    "단계로 넘어가죠": CuratedProtocolAction.NEXT,
    "다음 단계를 진행해줘": CuratedProtocolAction.NEXT,
    "다음 단계로 진행해줘": CuratedProtocolAction.NEXT,
    "다음 단계 진행해줘": CuratedProtocolAction.NEXT,
    "next": CuratedProtocolAction.NEXT,
    "종료": CuratedProtocolAction.STOP,
    "중지": CuratedProtocolAction.STOP,
    "그만": CuratedProtocolAction.STOP,
    "프로토콜 종료": CuratedProtocolAction.STOP,
    "프로토콜을 종료해줘": CuratedProtocolAction.STOP,
    "프로토콜 종료해줘": CuratedProtocolAction.STOP,
    "중지해줘": CuratedProtocolAction.STOP,
    "프로토콜을 중지해줘": CuratedProtocolAction.STOP,
    "절차를 중지해줘": CuratedProtocolAction.STOP,
    "stop": CuratedProtocolAction.STOP,
    "end session": CuratedProtocolAction.STOP,
}


_FULL_DETAIL_COMMANDS = frozenset({
    "전체 내용을 읽어줘",
    "현재 단계 전체를 읽어줘",
    "상세 내용을 읽어줘",
    "현재 단계 상세 내용을 읽어줘",
})


# Each reviewed question selects one existing current-step fact.  A rule that
# matches zero or multiple facts fails closed instead of asking a model to
# choose, summarize, or broaden the fixture's allowlist.
_VERIFIED_FACT_QUESTION_RULES = {
    "현재 온도는": ("fact_id", "current_step", ("°c",)),
    "이 작업의 온도는": ("fact_id", "current_step", ("°c",)),
    "주의 사항은": ("kind", "warning", ()),
    "준비 사항은": ("kind", "prerequisite", ()),
    "필요한 재료는": ("kind", "material", ()),
    "사용할 장비는": ("kind", "equipment", ()),
    "예상 결과는": ("kind", "expected_result", ()),
    "용액 a는 어떻게 준비해": (
        "fact_id", "current_step", ("solution a", "prepare"),
    ),
    "용액 에이는 어떻게 준비해": (
        "fact_id", "current_step", ("solution a", "prepare"),
    ),
}

_SOLUTION_A_QUESTION_KEYS = frozenset({
    "용액 a는 어떻게 준비해",
    "용액 에이는 어떻게 준비해",
})


def _localized_solution_a_presentation(
    source_text: str,
    source_page: int,
) -> tuple[str, str]:
    """Keep the verified source visible while failing closed on localization."""

    display = (
        f"원문\n{source_text}\n\n한국어 참고 번역\n"
        "검증된 한국어 참고 번역을 사용할 수 없습니다. "
        f"원문 {source_page}페이지를 확인해 주세요."
    )
    speech = (
        "요청하신 용액 A 준비 방법을 화면에 표시했습니다. "
        "검증된 한국어 참고 번역을 사용할 수 없어 "
        f"원문 {source_page}페이지를 확인해 주세요."
    )
    return display, speech


def _display_contract(
    language: str,
    primary_text: str,
    source_texts: tuple[str, ...],
    source_pages: tuple[int, ...],
    evidence_ids: tuple[str, ...],
    *,
    translated: bool,
) -> str:
    if language == "en":
        citations = ", ".join(
            f"{evidence_id} · p.{page}"
            for evidence_id, page in zip(evidence_ids, source_pages)
        )
        source_suffix = f"\n\nSource · {citations}" if citations else ""
        return primary_text + source_suffix
    source = "\n\n".join(source_texts)
    citations = ", ".join(
        f"{evidence_id} · 원문 p.{page}"
        for evidence_id, page in zip(evidence_ids, source_pages)
    )
    label = "답변 · 한국어 참고 번역" if translated else "답변 · 한국어"
    if not translated:
        primary_text = (
            f"{primary_text}\n검증된 한국어 번역을 사용할 수 없어 원문을 함께 확인해 주세요."
        )
    citation_block = f"\n\n출처\n{citations}" if citations else ""
    return f"{label}\n{primary_text}\n\n원문 · English\n{source}{citation_block}"


def _step_presentation(
    fixture: CuratedProtocolFixture,
    index: int,
    language: str,
    control_text: str,
) -> tuple[str, str, tuple[str, ...], tuple[int, ...], tuple[str, ...], str]:
    step = fixture.steps[index]
    source_texts = (step.instruction_source_text,)
    pages = (step.evidence.source_page_number,)
    evidence_ids = ("current_step",)
    localized = fixture.localized_fact(step.step_id, "current_step")
    if language == "ko" and localized is not None:
        primary = localized
        status = "verified_sidecar"
        translated = True
    elif language == "ko":
        primary = control_text
        status = "unavailable"
        translated = False
    else:
        primary = step.instruction_source_text
        status = "source_language"
        translated = True
    return (
        _display_contract(
            language,
            primary,
            source_texts,
            pages,
            evidence_ids,
            translated=translated,
        ),
        primary,
        source_texts,
        pages,
        evidence_ids,
        status,
    )


_FACT_POINT_LABELS = {
    "step": "확인된 동작",
    "note": "원문 참고",
    "warning": "원문 주의",
    "expected_result": "확인 기준",
    "prerequisite": "시작 전 확인",
    "material": "확인된 재료",
    "equipment": "확인된 장비",
}


def _detailed_step_presentation(
    fixture: CuratedProtocolFixture,
    index: int,
    language: str,
    *,
    expected_result_only: bool = False,
) -> tuple[str, str, tuple[CuratedProtocolFact, ...], tuple[str, ...], tuple[int, ...], tuple[str, ...], str]:
    """Compose a richer display only from facts admitted by the fixture."""

    step = fixture.steps[index]
    admitted = tuple(
        fact for fact in fixture.facts_for_step(index)
        if not expected_result_only or fact.kind in {"step", "expected_result"}
    )
    localized_items: list[tuple[CuratedProtocolFact, str]] = []
    for fact in admitted:
        localized = fixture.localized_fact(step.step_id, fact.fact_id)
        if language == "ko" and localized:
            localized_items.append((fact, localized))
        elif language != "ko":
            localized_items.append((fact, fact.text))
    if not localized_items:
        source = step.instruction_source_text
        return (
            _display_contract(
                language, source, (source,),
                (step.evidence.source_page_number,), ("current_step",),
                translated=language != "ko",
            ),
            source,
            admitted,
            (source,),
            (step.evidence.source_page_number,),
            ("current_step",),
            "source_language" if language != "ko" else "unavailable",
        )

    facts = tuple(item[0] for item in localized_items)
    localized_texts = tuple(item[1] for item in localized_items)
    if language == "ko":
        points = "\n".join(
            f"- {('원문이 허용한 대안' if step.source_label == '3' and fact.kind == 'note' else _FACT_POINT_LABELS.get(fact.kind, '확인된 내용'))}: {text}"
            for fact, text in localized_items
        )
        if step.source_label == "4":
            points = (
                "- 무엇을 제거하나요: Solution A를 제거합니다.\n"
                "- 어디에서 제거하나요: 젤 밴드가 들어 있는 튜브입니다.\n"
                "- 무엇이 남아 있나요: 다음 작업 대상인 젤 밴드는 튜브에 남습니다.\n"
                "- 원문이 지정하지 않은 내용: 제거 도구와 폐기물 분류 방법은 이 단계에 명시되어 있지 않습니다."
            )
        elif expected_result_only and step.source_label == "7":
            points += (
                "\n- 실행 경계: 두 번의 세척 사이클은 원문의 일반적 설명이며, "
                "고정 반복 횟수나 자동 완료 승인이 아닙니다. 7단계의 관찰 기반 반복 제어는 계속 차단됩니다."
            )
        primary = f"{step.source_label}단계 상세 설명\n{points}"
        speech = (
            localized_texts[-1]
            if expected_result_only and len(localized_texts) > 1
            else localized_texts[0]
        )
        status = "verified_sidecar"
    else:
        primary = "\n".join(
            f"- {('Source-approved alternative' if step.source_label == '3' and fact.kind == 'note' else _FACT_POINT_LABELS.get(fact.kind, 'Verified detail'))}: {text}"
            for fact, text in localized_items
        )
        speech = localized_texts[0]
        status = "source_language"
    source_texts = tuple(fact.text for fact in facts)
    pages = tuple(fact.source_page for fact in facts)
    evidence_ids = tuple(fact.fact_id for fact in facts)
    return (
        _display_contract(
            language, primary, source_texts, pages, evidence_ids,
            translated=True,
        ),
        speech,
        facts,
        source_texts,
        pages,
        evidence_ids,
        status,
    )


def _protocol_query_presentation(
    fixture: CuratedProtocolFixture,
    *,
    current_index: int,
    scope: str,
    language: str,
) -> tuple[str, str, tuple[CuratedProtocolFact, ...]]:
    """Answer whole-protocol questions from the ordered protected structure."""

    knowledge=ProtocolKnowledgeView.from_fixture(fixture)
    total = len(fixture.steps)
    current = current_index + 1
    remaining = total - current
    facts: list[CuratedProtocolFact] = []
    if scope in {"total_steps", "current_position", "remaining_steps"}:
        step = fixture.steps[current_index]
        facts.append(CuratedProtocolFact(
            fact_id="protocol_step_inventory",
            kind="protocol_metadata",
            text=(
                f"Ordered protocol step inventory: {total} steps; "
                f"current source label: {step.source_label}."
            ),
            source_page=step.evidence.source_page_number,
        ))
        if language == "ko":
            if scope == "total_steps":
                speech = f"이 프로토콜은 총 {total}단계입니다. 현재 {current}단계입니다."
            elif scope == "current_position":
                speech = f"현재 총 {total}단계 중 {current}단계입니다."
            else:
                speech = f"현재 단계 다음으로 {remaining}단계가 남아 있습니다."
            display = (
                f"프로토콜 진행 현황\n- 전체: {total}단계\n"
                f"- 현재: {current}/{total}\n- 현재 단계 이후 남은 단계: {remaining}\n\n"
                f"출처 · 보호된 순서형 단계 목록 · 현재 원문 p.{step.evidence.source_page_number}"
            )
        else:
            speech = (
                f"This protocol has {total} steps. You are at step {current}, "
                f"with {remaining} steps after the current step."
            )
            display = speech + (
                f"\n\nSource · protected ordered step inventory · "
                f"current source p.{step.evidence.source_page_number}"
            )
        return display, speech, tuple(facts)

    if scope == "purpose":
        facts.append(knowledge.purpose)
        if language=="ko":
            speech=(
                "이 실험의 목적은 젤 안의 단백질을 소화해 질량분석용 시료를 준비하는 것입니다. "
                "소화가 끝나면 시료를 Evotip에 로딩해 질량분석을 진행할 수 있다고 원문이 설명합니다."
            )
            display=(
                f"실험 목적\n{speech}\n\n원문 · English · PDF p.2\n"
                f"{knowledge.purpose.text}"
            )
        else:
            speech=(
                "This in-gel protocol prepares in-gel digests. After digestion, "
                "the sample is ready to load onto Evotips for mass-spectrometry analysis."
            )
            display=f"Experiment purpose\n{speech}\n\nOriginal · PDF p.2\n{knowledge.purpose.text}"
        return display,speech,tuple(facts)

    if scope == "overview":
        facts.extend((knowledge.purpose,*knowledge.sections))
        if language == "ko":
            speech = (
                "젤 안의 단백질 소화물을 준비해 질량분석 시료로 만드는 프로토콜입니다. "
                f"전체 {total}단계는 밴드 절단, 탈색, 환원·알킬화, 트립신 소화, 펩타이드 추출 순서입니다."
            )
            display = f"전체 흐름\n{speech}\n\n구간\n" + "\n".join(
                f"- {fact.text} · 원문 p.{fact.source_page}"
                for fact in knowledge.sections
            )
        else:
            speech = f"The {total}-step protocol is organized into five ordered sections."
            display = "Protocol overview\n" + "\n".join(
                f"- {fact.text} · source p.{fact.source_page}" for fact in facts
            )
        return display, speech, tuple(facts)

    if scope in {"preparation","materials","equipment"}:
        selected=(
            knowledge.materials if scope=="materials" else
            knowledge.equipment if scope=="equipment" else
            (*knowledge.before_start,*knowledge.materials,*knowledge.equipment)
        )
        facts.extend(selected)
        label = "시작 전 준비" if language == "ko" else "Before-start preparation"
        if scope=="materials":
            label="검증된 재료" if language=="ko" else "Verified materials"
        elif scope=="equipment":
            label="검증된 장비" if language=="ko" else "Verified equipment"
        display = label + "\n" + "\n".join(
            f"- {fact.text} · {'원문' if language == 'ko' else 'source'} p.{fact.source_page}"
            for fact in facts
        )
        speech = (
            "시작 전에는 깨끗한 작업면과 도구를 준비하고, 화면의 검증된 재료와 장비 목록을 확인해 주세요."
            if language == "ko" else
            "Before starting, prepare a clean surface and tools and review the verified materials and equipment shown on screen."
        )
        return display, speech, tuple(facts)

    if scope == "safety":
        facts.extend(knowledge.safety)
        if language == "ko":
            speech = (
                "활성 프로토콜 전체에서 명시적으로 확인되는 주의사항은 오염 방지를 위해 "
                "깨끗한 작업면과 도구, 새롭거나 깨끗한 메스, 장갑을 사용하는 것입니다."
            )
            missing = "활성 프로토콜은 그 밖의 전체 PPE·화학물질 취급·폐기 규칙을 명시하지 않습니다."
            display = "전체 안전수칙\n" + "\n".join(
                f"- {fact.text} · 원문 p.{fact.source_page}" for fact in facts
            ) + f"\n\n제한\n{missing}"
        else:
            speech = "The protocol explicitly warns about contamination control and using clean tools and gloves."
            display = "Protocol-wide safety\n" + "\n".join(
                f"- {fact.text} · source p.{fact.source_page}" for fact in facts
            ) + "\n\nLimitation\nThe active protocol does not specify a complete PPE, chemical-handling, or disposal policy."
        return display, speech, tuple(facts)
    raise CuratedProtocolFixtureError("Protocol query scope is unsupported.")


def _select_verified_fact(
    transcript: str,
    facts: tuple[CuratedProtocolFact, ...],
) -> CuratedProtocolFact | None:
    rule = _VERIFIED_FACT_QUESTION_RULES.get(_utterance_key(transcript))
    if rule is None:
        return None
    field, expected, required_terms = rule
    candidates = tuple(
        fact
        for fact in facts
        if getattr(fact, field) == expected
        and all(term in fact.text.casefold() for term in required_terms)
    )
    return candidates[0] if len(candidates) == 1 else None


def _question_is_supported(
    transcript: str,
    facts: tuple[CuratedProtocolFact, ...],
) -> bool:
    return _select_verified_fact(transcript, facts) is not None


def _unsupported_fact_reply(
    language: str, *, development_only: bool = True,
    question_kind: str | None = None,
) -> str:
    if question_kind == "safety":
        return {
            "en": (
                "The active protocol does not state an additional safety rule "
                "for this step. No further authoritative safety guidance was "
                "available for this answer, so the workflow has not been changed."
            ),
            "vi": (
                "Quy trình hiện tại không nêu quy tắc an toàn bổ sung cho bước "
                "này. Chưa có hướng dẫn an toàn có thẩm quyền khác cho câu trả lời "
                "này, vì vậy quy trình không thay đổi."
            ),
            "ko": (
                "활성 프로토콜에는 이 단계의 추가 안전 수칙이 명시되어 있지 않습니다. "
                "이번 답변에서 확인할 수 있는 권위 있는 추가 안전 근거도 없어 "
                "워크플로 상태는 변경하지 않았습니다."
            ),
        }.get(language, "추가 안전 근거를 확인하지 못해 워크플로 상태를 유지했습니다.")
    return {
        "en": (
            "I could not find enough confirmed information in the active "
            "procedure or its available references. Please ask with a little "
            "more detail."
        ),
        "vi": (
            "Tôi chưa tìm thấy đủ thông tin đã xác nhận trong quy "
            "trình hiện tại hoặc tài liệu tham khảo sẵn có. Vui lòng "
            "nói rõ hơn nội dung cần biết."
        ),
        "ko": (
            "현재 단계에서 확인되는 활성 프로토콜 내용은 화면에 그대로 유지했습니다. "
            "질문하신 추가 내용은 현재 승인된 근거에서 확인되지 않았습니다. "
            "필요한 재료나 조건을 한 가지 지정해 주시면 그 항목을 확인하겠습니다."
        ),
    }.get(
        language,
        "현재 절차와 참고자료에서 답변할 근거를 찾지 못했습니다.",
    )


def _control_speech(
    action: CuratedProtocolAction,
    language: str,
    label: str,
    *,
    resumed: bool = False,
    development_only: bool = True,
) -> str:
    english_subject = "Protocol"
    if language == "en":
        if action is CuratedProtocolAction.START:
            verb = "redisplayed" if resumed else "displayed"
            return f"{english_subject} step {label} guidance is {verb} on screen."
        if action is CuratedProtocolAction.CURRENT:
            return f"The current step is {label}. Its guidance is displayed on screen."
        if action is CuratedProtocolAction.REPEAT:
            return f"Current step {label} guidance is displayed again on screen."
        return f"Moved to step {label}. Its guidance is displayed on screen."
    if language == "vi":
        subject = "quy trình"
        if action is CuratedProtocolAction.START:
            verb = "hiển thị lại" if resumed else "hiển thị"
            return f"Hướng dẫn bước {label} của {subject} đã được {verb} trên màn hình."
        if action is CuratedProtocolAction.CURRENT:
            return f"Hiện tại là bước {label}. Hướng dẫn được hiển thị trên màn hình."
        if action is CuratedProtocolAction.REPEAT:
            return f"Hướng dẫn bước {label} hiện tại đã được hiển thị lại trên màn hình."
        return f"Đã chuyển sang bước {label}. Hướng dẫn được hiển thị trên màn hình."
    korean_subject = ""
    if action is CuratedProtocolAction.START:
        if resumed:
            return f"{korean_subject}{label}단계 안내를 화면에 다시 표시했습니다."
        return f"{korean_subject}{label}단계 안내를 화면에 표시했습니다."
    if action is CuratedProtocolAction.CURRENT:
        return f"현재 {label}단계입니다. 안내를 화면에 표시했습니다."
    if action is CuratedProtocolAction.REPEAT:
        return f"현재 {label}단계 안내를 다시 표시했습니다."
    return f"{label}단계로 이동했습니다. 안내를 화면에 표시했습니다."


def _step_reply(
    language: str,
    label: str,
    text: str,
    *,
    prefix: str,
    development_only: bool = True,
) -> str:
    if language == "en":
        noun = "Protocol step"
        return f"{prefix} {noun} {label}: {text}"
    if language == "vi":
        noun = "dữ liệu phát triển" if development_only else "quy trình"
        return f"{prefix} Bước {label} của {noun}: {text}"
    noun = ""
    return f"{prefix} {noun}{label}단계: {text}"


class CuratedProtocolSession:
    """Server-owned in-memory state for one validated structured fixture."""

    def __init__(self, fixture: CuratedProtocolFixture) -> None:
        self.fixture = fixture
        self.active = False
        self.current_index = 0
        self._revision = 0
        self._block_reason: str | None = None
        self._replay: dict[int, CuratedProtocolTurnPlan] = {}
        self._recent_verified_entities: list[str] = []
        self._pending_clarification: str | None = None
        self._last_related_query: str | None = None
        self._last_related_entities: tuple[str, ...] = ()
        self._discourse_context = ProtocolDiscourseContext()
        self._pending_completion_confirmation: PendingCompletionConfirmation | None = None
        self._pending_observation_confirmation: PendingObservationConfirmation | None = None

    @property
    def pending_completion_confirmation(self) -> PendingCompletionConfirmation | None:
        return self._pending_completion_confirmation

    @property
    def pending_observation_confirmation(self) -> PendingObservationConfirmation | None:
        return self._pending_observation_confirmation

    def _entity_inventory(self) -> tuple[str, ...]:
        indexes = {
            index for index in (
                self.current_index - 1, self.current_index,
                self.current_index + 1,
            ) if 0 <= index < len(self.fixture.steps)
        }
        text = " ".join(
            fact.text.casefold()
            for index in indexes
            for fact in self.fixture.facts_for_step(index)
        )
        aliases = {
            "ambic": ("ambic", "ammonium bicarbonate"),
            "hplc water": ("hplc water",),
            "solution a": ("solution a",),
            "solution b": ("solution b",),
            "acetonitrile": ("acetonitrile",),
            "gel plug": ("gel plug",),
            "stained protein band": ("stained protein band",),
            "rpm": ("rpm",),
            "incubation": ("incubat",),
            "contamination": ("contaminat", "오염"),
        }
        present = [
            entity for entity, terms in aliases.items()
            if any(term in text for term in terms)
        ]
        return tuple(dict.fromkeys((*present, *self._recent_verified_entities)))

    def stt_keyterms(self) -> tuple[str, ...]:
        """Return protocol-wide technical and workflow terms within xAI's cap.

        The STT request receives a bounded vocabulary, not the protocol body and
        not numeric operating values.  Including later-step vocabulary avoids a
        dependence on the current step when a user asks an adjacent/global
        question or the microphone endpoint lags behind a committed transition.
        """

        scientific = (
            "AMBIC", "ammonium bicarbonate", "HPLC water", "acetonitrile",
            "Solution A", "Solution B", "DTT", "iodoacetamide", "trypsin",
            "formic acid", "LC-MS", "SDS-PAGE", "gel plug",
            "stained protein band", "Thermomixer", "rpm", "incubation",
            "keratin", "contamination", "Evotip",
        )
        workflow = (
            "프로토콜 시작", "현재 단계", "다음 단계", "다음 단계 미리보기",
            "완료", "현재 단계 완료", "완료했어요", "다 했어요", "완료 조건",
            "네", "아니요", "다시 알려줘", "프로토콜 중단",
            "이상 사항 기록", "관찰 결과", "완전히 탈색", "투명해요",
            "투명한가요", "흰색으로 변했어요", "아직 색이 남아 있어요",
        )
        return tuple(dict.fromkeys((*scientific, *workflow)))[:100]

    def current_step_semantic_frame(self) -> StepSemanticFrame:
        return build_step_semantic_frame(self.fixture, self.current_index)

    def _semantic_claim_requests(
        self,
        transcript: str,
        intent: CuratedControlIntent,
        *,
        language: str,
    ) -> tuple[ClaimRequest, ...]:
        """Decompose read-only questions into independently admitted claims."""

        if not self.active or intent.action not in {
            CuratedProtocolAction.RELATED_QUESTION,
            CuratedProtocolAction.PROTOCOL_QUERY,
            CuratedProtocolAction.OPERATIONAL_DEVIATION,
            CuratedProtocolAction.VISUAL_REQUEST,
        }:
            return ()
        key = intent.normalized_transcript or _semantic_utterance_key(transcript)
        frame = self.current_step_semantic_frame()
        facts = self.related_facts(transcript)
        knowledge = ProtocolKnowledgeView.from_fixture(self.fixture)
        claims: list[ClaimRequest] = []

        def add(
            target_type: ClaimTargetType,
            target_id: str,
            dimension: str,
            *,
            evidence_ids: tuple[str, ...] = (),
            local_answer: str | None = None,
            unresolved_reason: str | None = None,
            operational: bool = False,
            authority: str = "ACTIVE_PROTOCOL",
            status: ClaimAdmissionStatus | None = None,
        ) -> None:
            claim_id = f"claim-{len(claims)+1}-{target_type.value}-{target_id}-{dimension}"
            claims.append(ClaimRequest(
                claim_id=claim_id,
                target_type=target_type,
                target_id=target_id,
                dimension=dimension,
                required_authority=authority,
                evidence_ids=evidence_ids,
                admission_status=status or (
                    ClaimAdmissionStatus.LOCAL_SUPPORTED
                    if local_answer and evidence_ids
                    else ClaimAdmissionStatus.RESEARCH_REQUIRED
                ),
                local_answer=local_answer,
                unresolved_reason=unresolved_reason,
                operational=operational,
            ))

        if intent.protocol_scope == "purpose":
            answer = (
                "이 프로토콜은 젤 안의 단백질을 소화해 Evotip과 질량분석에 사용할 시료를 준비하는 실험입니다."
                if language == "ko" else
                "This protocol prepares in-gel digests for loading onto Evotips and mass-spectrometry analysis."
            )
            add(
                ClaimTargetType.PROTOCOL_PROPOSITION, "protocol_purpose", "purpose",
                evidence_ids=(knowledge.purpose.fact_id,), local_answer=answer,
            )
        elif intent.question_kind == "protocol_benefit":
            answer = (
                "이 과정이 유용한 이유는 젤 안의 단백질을 소화한 뒤 Evotip과 질량분석으로 이어질 수 있는 시료 상태를 만들기 때문입니다."
                if language == "ko" else
                "It is useful because it turns protein in the gel into an in-gel digest that the source says is ready for Evotips and mass-spectrometry analysis."
            )
            add(
                ClaimTargetType.PROTOCOL_PROPOSITION, "protocol_purpose",
                "rationale", evidence_ids=(knowledge.purpose.fact_id,),
                local_answer=answer,
            )

        action_words = bool(re.search(
            r"(?:discard|remove|버리|제거|씻|wash|incubat|배양|넣|place|cut|자르)", key
        ))
        ratio_words = bool(re.search(
            r"(?:비율|parts?|ratio|몇\s*대\s*몇|\d+\s*대\s*\d+|대\s*일|:\s*1)",
            key,
        ))
        entity_answers = {
            "ambic": (
                "AMBIC는 ammonium bicarbonate를 가리키는 약칭이며, 이 프로토콜에서는 Solution A와 B의 구성 성분입니다."
                if language == "ko" else
                "AMBIC is the protocol's abbreviation for ammonium bicarbonate and is a component of Solutions A and B."
            ),
            "hplc_water": (
                "HPLC water는 이 프로토콜에서 25 mM AMBIC 용액을 만드는 물로 사용됩니다."
                if language == "ko" else
                "In this protocol, HPLC water is used to prepare the 25 mM AMBIC solution."
            ),
            "solution_a": (
                "Solution A는 HPLC water로 만든 25 mM AMBIC 2 parts와 acetonitrile 1 part의 혼합물입니다."
                if language == "ko" else
                "Solution A is 2 parts 25 mM AMBIC in HPLC water mixed with 1 part acetonitrile."
            ),
            "solution_b": (
                "Solution B는 HPLC water로 만든 25 mM AMBIC 용액입니다."
                if language == "ko" else
                "Solution B is 25 mM AMBIC made in HPLC water."
            ),
            "acetonitrile": (
                "Acetonitrile은 이 프로토콜에서 Solution A의 1 part 성분입니다."
                if language == "ko" else
                "In this protocol, acetonitrile is the 1-part component of Solution A."
            ),
            "rpm": (
                "rpm은 분당 회전수를 나타내는 기기 설정값이며, 현재 단계에서는 부드러운 교반 속도에 연결됩니다."
                if language == "ko" else
                "rpm means revolutions per minute; in the current step it is the gentle-agitation speed setting."
            ),
        }
        alias_map = {
            "ambic": ("ambic", "ammonium bicarbonate"),
            "hplc_water": ("hplc water",),
            "solution_a": ("solution a",), "solution_b": ("solution b",),
            "acetonitrile": ("acetonitrile",), "rpm": ("rpm",),
        }
        for entity in intent.requested_entities:
            if action_words and entity in {action.target_id for action in frame.actions}:
                continue
            evidence = tuple(
                fact.fact_id for fact in facts
                if any(alias in _derived_source_text(fact.text).casefold()
                       for alias in alias_map.get(entity, (entity,)))
            )[:2]
            answer = entity_answers.get(entity)
            if answer and evidence:
                dimension = (
                    "composition" if entity in {"solution_a", "solution_b"}
                    else "definition"
                )
                add(
                    ClaimTargetType.ENTITY, entity, dimension,
                    evidence_ids=evidence, local_answer=answer,
                )
        if "difference" in intent.question_dimensions and intent.requested_entities:
            target = "-vs-".join(intent.requested_entities)
            limitation = (
                "활성 프로토콜은 일반 물과의 품질 차이를 정의하지 않으므로, 그 비교에는 별도 권위 자료가 필요합니다."
                if language == "ko" else
                "The active protocol does not define that general quality difference, so the comparison requires a separate authoritative source."
            )
            add(
                ClaimTargetType.COMPARISON, target, "difference",
                evidence_ids=tuple(dict.fromkeys(
                    evidence_id for claim in claims
                    for evidence_id in claim.evidence_ids
                )),
                local_answer=limitation,
                unresolved_reason="comparison_absent_from_active_protocol",
                authority="AUTHORITATIVE_EXTERNAL_REFERENCE",
                status=ClaimAdmissionStatus.RESEARCH_REQUIRED,
            )
        elif (
            "relationship" in intent.question_dimensions
            and {"hplc_water", "ambic"} <= set(intent.requested_entities)
        ):
            evidence_ids = tuple(dict.fromkeys(
                evidence_id for claim in claims
                for evidence_id in claim.evidence_ids
            ))
            answer = (
                "관계: 이 프로토콜에서는 HPLC water가 25 mM AMBIC 용액을 만드는 물이고, AMBIC가 그 용액의 성분입니다."
                if language == "ko" else
                "Relationship: in this protocol, HPLC water is the water used to prepare the 25 mM AMBIC solution, and AMBIC is its solute component."
            )
            if evidence_ids:
                add(
                    ClaimTargetType.COMPARISON, "hplc_water-ambic",
                    "relationship", evidence_ids=evidence_ids,
                    local_answer=answer,
                )

        if re.search(r"(?:1\.5\s*mL\s*튜브|1\.5\s*mL\s*tube|그\s*크기\s*튜브)", key, re.I):
            current_evidence = ("current_step",)
            if re.search(r"(?:뭘|무엇|what).*(?:넣|들어|place|contain)|(?:튜브).*(?:뭘|무엇)", key):
                answer = (
                    "1.5 mL 튜브에는 약 200 µL의 25 mM AMBIC이 있고, 절취한 젤 재료를 그 안에 넣습니다."
                    if language == "ko" else
                    "The 1.5 mL tube contains approximately 200 µL of 25 mM AMBIC, and the excised gel material is placed into it."
                )
                add(
                    ClaimTargetType.ACTION, "place_gel_in_tube", "value",
                    evidence_ids=current_evidence, local_answer=answer,
                    operational=True,
                )
            if re.search(r"(?:왜|이유|why).*(?:1\.5|그\s*크기)|(?:1\.5|그\s*크기).*(?:왜|이유)", key):
                limitation = (
                    "활성 프로토콜은 1.5 mL 튜브를 지정하지만 그 크기를 선택한 과학적 이유는 설명하지 않습니다."
                    if language == "ko" else
                    "The active protocol specifies a 1.5 mL tube but does not explain the scientific reason for that size."
                )
                add(
                    ClaimTargetType.PARAMETER, "vessel_capacity", "rationale",
                    evidence_ids=current_evidence, local_answer=limitation,
                    unresolved_reason="rationale_absent_from_active_protocol",
                    authority="AUTHORITATIVE_EXTERNAL_REFERENCE",
                    status=ClaimAdmissionStatus.RESEARCH_REQUIRED,
                )

        for binding in frame.parameters:
            unit_aliases = {
                "°C": r"(?:°\s*C|C|도)",
                "C": r"(?:°\s*C|C|도)",
                "min": r"(?:min(?:ute)?s?|분)",
                "rpm": r"(?:rpm|분당\s*회전)",
                "µL": r"(?:µL|uL|microliters?|마이크로리터)",
                "mL": r"(?:mL|milliliters?|밀리리터)",
                "mM": r"(?:mM|millimolar|밀리몰)",
            }
            unit_pattern = unit_aliases.get(
                binding.unit, re.escape(binding.unit)
            )
            token_pattern = (
                rf"(?<![0-9]){re.escape(binding.value)}\s*"
                rf"{unit_pattern}(?![A-Za-z0-9])"
            )
            if re.search(token_pattern, key, re.I) is None:
                continue
            role_labels = {
                "incubation_temperature": ("배양 온도", "incubation temperature"),
                "incubation_duration": ("배양 시간", "incubation duration"),
                "agitation_speed": ("부드러운 교반 속도", "gentle-agitation speed"),
                "solution_volume": ("사용 용액 부피", "solution volume"),
                "concentration": ("용액 농도", "solution concentration"),
                "vessel_capacity": ("튜브 용량", "tube capacity"),
                "gel_piece_size": ("젤 조각 크기", "gel-piece size"),
            }
            label = role_labels.get(binding.role, ("단계 조건", "step condition"))[
                0 if language == "ko" else 1
            ]
            rendered = f"{binding.value} {binding.unit}"
            answer = (
                f"{rendered}는 현재 {frame.step_label}단계의 {label}입니다."
                if language == "ko" else
                f"{rendered} is the current step's {label}."
            )
            add(
                ClaimTargetType.PARAMETER, binding.parameter_id, "role",
                evidence_ids=(binding.evidence_id,), local_answer=answer,
                operational=True,
                authority=(
                    "SOURCE_APPROVED_ALTERNATIVE"
                    if binding.source_approved_alternative else "ACTIVE_PROTOCOL"
                ),
            )

        if not any(
            claim.target_type is ClaimTargetType.PARAMETER
            and claim.dimension == "role" for claim in claims
        ):
            requested_role = next((role for role, pattern in (
                ("incubation_temperature", r"(?:온도|temperature)"),
                ("incubation_duration", r"(?:시간|기간|duration|how\s+long)"),
                ("agitation_speed", r"(?:교반|회전|rpm|agitation|speed)"),
                ("solution_volume", r"(?:부피|용량|volume)"),
                ("concentration", r"(?:농도|concentration)"),
            ) if re.search(pattern, key)), None)
            if requested_role:
                candidates = tuple(
                    item for item in frame.parameters
                    if item.role == requested_role
                )
                if len(candidates) == 1:
                    item = candidates[0]
                    answer = (
                        f"현재 {frame.step_label}단계에서 {item.value} {item.unit}는 {requested_role}에 연결된 값입니다."
                        if language == "ko" else
                        f"In step {frame.step_label}, {item.value} {item.unit} is bound to {requested_role}."
                    )
                    add(
                        ClaimTargetType.PARAMETER,item.parameter_id,"role",
                        evidence_ids=(item.evidence_id,),local_answer=answer,
                        operational=True,
                    )
                elif len(candidates) > 1:
                    values = ", ".join(
                        f"{item.value} {item.unit}" for item in candidates
                    )
                    message = (
                        f"현재 단계에는 {values}처럼 같은 종류의 값이 둘 이상 있습니다. 어떤 값을 말씀하시는지 확인해 주세요."
                        if language == "ko" else
                        f"The current step has more than one matching value ({values}). Please specify which one you mean."
                    )
                    add(
                        ClaimTargetType.PARAMETER,requested_role,"value",
                        local_answer=message,
                        unresolved_reason="multiple_current_step_parameters",
                        status=ClaimAdmissionStatus.CLARIFICATION_REQUIRED,
                        operational=True,
                    )

        matching_actions = tuple(
            action for action in frame.actions
            if action_words and (
                action.target_id is None
                or action.target_id in intent.requested_entities
                or action.action_type in key.replace(" ", "_")
                or action.action_type == "remove_discard"
                and re.search(r"(?:discard|remove|버리|제거)", key)
            )
        )
        if len(matching_actions) == 1:
            action = matching_actions[0]
            action_answer = (
                "활성 프로토콜은 젤 밴드가 든 튜브에서 Solution A를 제거해 버리라고 지시합니다."
                if language == "ko" and action.action_type == "remove_discard" else
                "The active protocol instructs you to remove and discard Solution A from the tube containing the gel band."
                if action.action_type == "remove_discard" else
                (f"현재 단계의 확인된 동작은 {_derived_source_text(action.source_text)}입니다.")
            )
            add(
                ClaimTargetType.ACTION, action.action_id, "value",
                evidence_ids=(action.evidence_id,), local_answer=action_answer,
                operational=True,
            )
            if re.search(r"(?:왜|이유|rationale|why)", key):
                limitation = (
                    "활성 프로토콜은 이 동작을 지시하지만 그 기전적 이유는 설명하지 않습니다."
                    if language == "ko" else
                    "The active protocol instructs this action but does not state its mechanistic rationale."
                )
                add(
                    ClaimTargetType.ACTION, action.action_id, "rationale",
                    evidence_ids=(action.evidence_id,), local_answer=limitation,
                    unresolved_reason="rationale_absent_from_active_protocol",
                    authority="AUTHORITATIVE_EXTERNAL_REFERENCE",
                    status=ClaimAdmissionStatus.RESEARCH_REQUIRED,
                )

        if ratio_words and len(frame.ratios) == 1:
            ratio = frame.ratios[0]
            first, second = ratio.components
            answer = (
                f"Solution A의 비율은 25 mM AMBIC 용액 {first[1]} parts 대 acetonitrile {second[1]} part입니다."
                if language == "ko" else
                f"Solution A uses {first[1]} parts 25 mM AMBIC solution to {second[1]} part acetonitrile."
            )
            add(
                ClaimTargetType.RATIO, ratio.ratio_id, "value",
                evidence_ids=(ratio.evidence_id,), local_answer=answer,
                operational=True,
            )
        return tuple(claims)

    def _localized_fact(self, step_id: str, fact_id: str) -> str | None:
        """Read optional presentation data without weakening fixture validation."""

        lookup = getattr(self.fixture, "localized_fact", None)
        if not callable(lookup):
            return None
        value = lookup(step_id, fact_id)
        return value if isinstance(value, str) and value.strip() else None

    def _step_index_for_label(self, label: str | None) -> int | None:
        if label in (None, "authoritative_current_step"):
            return self.current_index
        return next(
            (
                index
                for index, step in enumerate(self.fixture.steps)
                if step.source_label == label
            ),
            None,
        )

    def _contextual_solution_fact(
        self,
        transcript: str,
    ) -> tuple[int, CuratedProtocolFact, str] | None:
        """Resolve one dominant recent Solution A/B reference from adjacent facts."""

        if self.fixture.steps[self.current_index].source_label not in {"3", "5"}:
            return None
        key = _semantic_utterance_key(transcript)
        if not any(
            term in key
            for term in (
                "어떻게", "준비", "만들", "조성", "구성", "비율", "뭐가",
                "무엇이", "들어가", "prepare", "make", "contain",
            )
        ):
            return None
        explicit_a = bool(re.search(
            r"(?:solution\s*a|a\s*용액|용액\s*a|에이\s*용액|용액\s*에이)", key
        ))
        explicit_b = bool(re.search(
            r"(?:solution\s*b|b\s*용액|용액\s*b|비\s*용액|용액\s*비)", key
        ))
        vague = bool(re.search(r"(?:(?:그|해당)\s*용액|그거|방금\s*말한\s*것)", key))
        if not (explicit_a or explicit_b or vague or re.search(
            r"(?<![a-z0-9])ambic(?![a-z0-9])", key
        )):
            return None
        current_label = self.fixture.steps[self.current_index].source_label
        mentions_ambic = bool(re.search(
            r"(?<![a-z0-9])ambic(?![a-z0-9])", key
        ))
        entity = (
            "solution_a" if explicit_a else
            "solution_b" if explicit_b else
            "solution_a" if vague and current_label == "3" else
            "solution_b" if vague and current_label == "5" else
            "solution_a" if mentions_ambic and current_label == "3" else
            "solution_b" if mentions_ambic and current_label == "5" else
            None
        )
        if entity is None:
            return None
        source_index = next(
            (index for index, step in enumerate(self.fixture.steps)
             if step.source_label == "2"), -1
        )
        candidates = tuple(
            fact
            for fact in self.fixture.facts_for_step(source_index)
            if fact.fact_id == "current_step"
            and "solution a" in fact.text.casefold()
            and "solution b" in fact.text.casefold()
            and "ambic" in fact.text.casefold()
            and "acetonitrile" in fact.text.casefold()
        )
        return (source_index, candidates[0], entity) if len(candidates) == 1 else None

    def _needs_solution_clarification(self, transcript: str) -> bool:
        key = _semantic_utterance_key(transcript)
        return bool(
            self.fixture.steps[self.current_index].source_label == "2"
            and re.search(r"(?:(?:그|해당)\s*용액|그거|방금\s*말한\s*것)", key)
            and any(term in key for term in ("어떻게", "준비", "만들", "구성", "비율"))
        )

    def activate_configured(self) -> None:
        """Make one successfully configured structured protocol usable."""

        opening = (self.active, self.current_index, self._block_reason)
        self.active = True
        self.current_index = 0
        self._block_reason = None
        self._replay.clear()
        self._recent_verified_entities.clear()
        self._pending_clarification = None
        self._last_related_query = None
        self._last_related_entities = ()
        self._discourse_context = ProtocolDiscourseContext()
        self._pending_completion_confirmation = None
        self._pending_observation_confirmation = None
        if opening != (self.active, self.current_index, self._block_reason):
            self._revision += 1

    def reset(self) -> None:
        opening = (self.active, self.current_index, self._block_reason)
        self.active = False
        self.current_index = 0
        self._block_reason = None
        self._replay.clear()
        self._recent_verified_entities.clear()
        self._pending_clarification = None
        self._last_related_query = None
        self._last_related_entities = ()
        self._discourse_context = ProtocolDiscourseContext()
        self._pending_completion_confirmation = None
        self._pending_observation_confirmation = None
        if opening != (self.active, self.current_index, self._block_reason):
            self._revision += 1

    def _checkpoint(
        self,
    ) -> tuple[
        bool,
        int,
        int,
        str | None,
        dict[int, CuratedProtocolTurnPlan],
        tuple[str, ...],
        str | None,
        str | None,
        tuple[str, ...],
        PendingCompletionConfirmation | None,
        PendingObservationConfirmation | None,
        ProtocolDiscourseContext,
    ]:
        return (
            self.active,
            self.current_index,
            self._revision,
            self._block_reason,
            dict(self._replay),
            tuple(self._recent_verified_entities),
            self._pending_clarification,
            self._last_related_query,
            self._last_related_entities,
            self._pending_completion_confirmation,
            self._pending_observation_confirmation,
            self._discourse_context,
        )

    def _restore(
        self,
        checkpoint: tuple[
            bool,
            int,
            int,
            str | None,
            dict[int, CuratedProtocolTurnPlan],
            tuple[str, ...],
            str | None,
            str | None,
            tuple[str, ...],
            PendingCompletionConfirmation | None,
            PendingObservationConfirmation | None,
            ProtocolDiscourseContext,
        ],
    ) -> None:
        (
            self.active,
            self.current_index,
            self._revision,
            self._block_reason,
            replay,
            recent_entities,
            self._pending_clarification,
            self._last_related_query,
            self._last_related_entities,
            self._pending_completion_confirmation,
            self._pending_observation_confirmation,
            self._discourse_context,
        ) = checkpoint
        self._replay = dict(replay)
        self._recent_verified_entities = list(recent_entities)

    def reference_query_for(
        self, transcript: str, plan: CuratedProtocolTurnPlan
    ) -> str | None:
        """Resolve an explicit external follow-up to one bounded prior query."""

        if plan.requested_followup == "search_external_reference":
            return self._last_related_query
        if plan.requested_followup == "continue_related_question":
            return (
                f"{self._last_related_query}\nFollow-up: {transcript}"
                if self._last_related_query else None
            )
        if plan.normalized_transcript:
            return plan.normalized_transcript
        return transcript

    def related_facts(self, transcript: str) -> tuple[CuratedProtocolFact, ...]:
        """Return uniquely identified current/adjacent facts relevant to research."""

        key, entities, _, _ = normalize_scientific_request(
            transcript, entity_inventory=self._entity_inventory()
        )
        indexes = {self.current_index}
        if entities:
            indexes.update({
                index for index, step in enumerate(self.fixture.steps)
                if step.source_label in {"1", "2", "3", "4", "5", "6", "7"}
            })
        else:
            indexes.update(
                index for index in (self.current_index - 1, self.current_index + 1)
                if 0 <= index < len(self.fixture.steps)
            )
        selected: list[CuratedProtocolFact] = []
        seen: set[tuple[str, str]] = set()
        alias_map = {
            "ambic": ("ambic", "ammonium bicarbonate"),
            "hplc_water": ("hplc water",),
            "solution_a": ("solution a",),
            "solution_b": ("solution b",),
            "acetonitrile": ("acetonitrile",),
            "gel_plug": ("gel plug", "plug of a stained protein band"),
            "stained_protein_band": ("stained protein band", "gel band"),
            "rpm": ("rpm",),
            "incubation": ("incubat",),
            "contamination": ("contaminat", "오염"),
        }
        aliases = tuple(
            alias for entity in entities for alias in alias_map.get(entity, ())
        )
        for index in sorted(indexes):
            step = self.fixture.steps[index]
            for fact in self.fixture.facts_for_step(index):
                lowered = fact.text.casefold()
                if (
                    aliases and not any(alias in lowered for alias in aliases)
                ):
                    continue
                if entities and fact.kind in {"material", "equipment"}:
                    # Product catalog rows are identity evidence, not a direct
                    # conversational definition or workflow explanation.
                    continue
                identity = (step.step_id, fact.fact_id)
                if identity in seen:
                    continue
                seen.add(identity)
                selected.append(CuratedProtocolFact(
                    fact_id=(
                        fact.fact_id if index == self.current_index else
                        f"related_{step.source_label}_{fact.fact_id}"
                    ),
                    kind=fact.kind,
                    text=fact.text,
                    source_page=fact.source_page,
                ))
        return tuple(selected[:24])

    def protocol_answer_envelope(
        self,
        plan: CuratedProtocolTurnPlan,
        *,
        language: str,
    ) -> AnswerEnvelope:
        """Synthesize a direct local answer without expanding source authority."""

        facts = plan.facts
        evidence_ids = tuple(fact.fact_id for fact in facts[:8])
        entities = plan.requested_entities or (
            (plan.requested_entity,) if plan.requested_entity else ()
        )
        if plan.claim_requests:
            admitted = tuple(
                claim for claim in plan.claim_requests
                if claim.admission_status is ClaimAdmissionStatus.LOCAL_SUPPORTED
                and claim.local_answer
                and claim.evidence_ids
            )
            unresolved_claims = tuple(
                claim for claim in plan.claim_requests
                if claim.admission_status is ClaimAdmissionStatus.RESEARCH_REQUIRED
            )
            visible = tuple(
                claim for claim in plan.claim_requests if claim.local_answer
            )
            if visible:
                direct = "\n".join(
                    f"• {claim.local_answer}" for claim in visible
                )
                spoken_parts = [claim.local_answer for claim in admitted[:3]]
                if unresolved_claims and len(spoken_parts) < 3:
                    limitation = next(
                        (claim.local_answer for claim in unresolved_claims
                         if claim.local_answer), None
                    )
                    if limitation:
                        spoken_parts.append(limitation)
                speech = " ".join(spoken_parts)
                claim_evidence = tuple(dict.fromkeys(
                    evidence_id for claim in visible
                    for evidence_id in claim.evidence_ids
                ))
                scopes = ["ACTIVE_PROTOCOL"]
                if any(
                    claim.required_authority == "SOURCE_APPROVED_ALTERNATIVE"
                    for claim in admitted
                ):
                    scopes.append("SOURCE_APPROVED_ALTERNATIVE")
                if unresolved_claims:
                    scopes.extend((
                        "APPROVED_REFERENCE",
                        "AUTHORITATIVE_EXTERNAL_REFERENCE",
                    ))
                return AnswerEnvelope(
                    direct_answer=direct,
                    speech_summary=speech,
                    entity_sections=tuple(
                        (claim.target_id, claim.local_answer or "")
                        for claim in visible
                    ),
                    protocol_relevance="",
                    evidence_ids=claim_evidence,
                    source_plan=SourcePlan(
                        tuple(dict.fromkeys(scopes)),
                        tuple(dict.fromkeys(
                            claim.dimension for claim in unresolved_claims
                        )),
                        tuple(claim.claim_id for claim in unresolved_claims),
                    ),
                    admitted_claim_ids=tuple(
                        claim.claim_id for claim in admitted
                    ),
                )
        sections: list[tuple[str, str]] = []
        explanations_ko = {
            "ambic": (
                "AMBIC",
                "AMBIC는 이 프로토콜에서 ammonium bicarbonate를 가리키는 약칭입니다.",
            ),
            "hplc_water": (
                "HPLC water",
                "HPLC water는 이 프로토콜에서 25mM ammonium bicarbonate(AMBIC) 용액을 만드는 데 쓰이는 물입니다.",
            ),
            "solution_a": (
                "Solution A",
                "Solution A는 HPLC water로 만든 25mM AMBIC 2 parts와 acetonitrile 1 part를 섞은 세척 용액입니다.",
            ),
            "solution_b": (
                "Solution B",
                "Solution B는 HPLC water로 만든 25mM AMBIC 용액입니다.",
            ),
            "acetonitrile": (
                "Acetonitrile",
                "Acetonitrile은 이 프로토콜에서 Solution A에 1 part로 포함되는 성분입니다.",
            ),
            "gel_plug": (
                "Gel plug",
                "젤 플러그는 이 프로토콜에서 염색된 단백질 밴드에서 잘라 세척·배양하는 작은 젤 조각을 가리킵니다.",
            ),
            "stained_protein_band": (
                "Stained protein band",
                "염색된 단백질 밴드는 이 프로토콜에서 젤에서 잘라 작은 플러그나 조각으로 만드는 대상입니다.",
            ),
            "rpm": (
                "rpm",
                "800 rpm은 이 프로토콜의 배양 단계에서 부드러운 교반에 사용하는 기기 설정값입니다.",
            ),
            "incubation": (
                "Incubation",
                "배양은 이 프로토콜에서 시료를 지정된 시간과 온도 조건에 두는 단계로 사용됩니다.",
            ),
            "contamination": (
                "Contamination",
                "오염은 원하지 않는 물질이 시료에 섞이는 상태이며, 이 프로토콜은 특히 먼지와 케라틴 오염을 피하도록 경고합니다.",
            ),
        }
        explanations_en = {
            "ambic": ("AMBIC", "AMBIC is the abbreviation used by this protocol for ammonium bicarbonate."),
            "hplc_water": ("HPLC water", "In this protocol, HPLC water is the water used to prepare 25mM ammonium bicarbonate (AMBIC)."),
            "solution_a": ("Solution A", "Solution A is the wash solution made from 2 parts 25mM AMBIC in HPLC water and 1 part acetonitrile."),
            "solution_b": ("Solution B", "Solution B is 25mM AMBIC made in HPLC water."),
            "acetonitrile": ("Acetonitrile", "In this protocol, acetonitrile is the 1-part component of Solution A."),
            "gel_plug": ("Gel plug", "In this protocol, a gel plug is the small gel piece cut from a stained protein band for washing and incubation."),
            "stained_protein_band": ("Stained protein band", "In this protocol, the stained protein band is the gel region cut into a plug or smaller pieces."),
            "rpm": ("rpm", "800 rpm is the instrument setting used for gentle agitation in the protocol's incubation steps."),
            "incubation": ("Incubation", "In this protocol, incubation means holding the sample under the stated time and temperature conditions."),
            "contamination": ("Contamination", "Contamination is unwanted material in the sample; this protocol specifically warns about dust and keratin contamination."),
        }
        explanations = explanations_ko if language == "ko" else explanations_en
        for entity in entities:
            if entity in explanations:
                sections.append(explanations[entity])
        relation = ""
        if {"hplc_water", "ambic"}.issubset(entities):
            relation = (
                "이 프로토콜에서는 HPLC water에 AMBIC를 녹여 "
                "Solution A와 B의 기본 용액을 만듭니다."
                if language == "ko" else
                "In this protocol, AMBIC is dissolved in HPLC water to make the base solution used in Solutions A and B."
            )
        if plan.question_kind == "safety":
            step = self.fixture.steps[self.current_index]
            warnings = tuple(fact for fact in facts if fact.kind == "warning")
            if warnings:
                direct = (
                    f"현재 {step.source_label}단계에는 활성 프로토콜에 명시된 "
                    "주의사항이 있습니다. 해당 원문을 근거로 표시합니다. "
                    "추가 지침은 승인되거나 권위 있는 근거가 확인된 경우에만 분리해 안내합니다."
                )
            else:
                direct = (
                    f"활성 프로토콜의 {step.source_label}단계에는 추가 안전수칙이 "
                    "직접 명시되어 있지 않습니다. 추가 지침은 현재 단계의 재료와 "
                    "동작에 맞는 승인 자료나 권위 자료가 확인된 경우에만 분리해 안내합니다."
                )
            speech = direct
        elif sections:
            direct = "\n".join(f"• {label}: {text}" for label, text in sections)
            if relation:
                direct += f"\n\n{'관계' if language == 'ko' else 'Relationship'}\n{relation}"
            if "difference" in plan.question_dimensions and "hplc_water" in entities:
                limitation = (
                    "활성 프로토콜은 HPLC water와 일반 물의 품질 차이를 정의하지 않으므로 그 차이는 별도 권위 자료가 필요합니다."
                    if language == "ko" else
                    "The active protocol does not define the quality difference between HPLC water and ordinary water, so that comparison needs a separate authoritative source."
                )
                direct += f"\n\n{limitation}"
            speech = " ".join(text for _, text in sections[:2])
            if relation and len(sections) <= 2:
                speech += " " + relation
            if "role" in plan.question_dimensions:
                role_notes = {
                    "ambic": (
                        "이 프로토콜에서는 HPLC water에 녹여 Solution A와 B의 구성 성분으로 사용합니다."
                        if language == "ko" else
                        "In this protocol, it is dissolved in HPLC water and used in Solutions A and B."
                    ),
                    "hplc_water": (
                        "이 프로토콜에서는 AMBIC 용액을 만드는 물로 사용합니다."
                        if language == "ko" else
                        "In this protocol, it is the water used to prepare the AMBIC solution."
                    ),
                }
                if entities and entities[0] in role_notes:
                    speech += " " + role_notes[entities[0]]
                    direct += f"\n\n{role_notes[entities[0]]}"
            if "difference" in plan.question_dimensions and "hplc_water" in entities:
                speech += " " + limitation
        else:
            step = self.fixture.steps[self.current_index]
            localized = self._localized_fact(step.step_id, "current_step")
            direct = (
                f"현재 {step.source_label}단계에서 프로토콜이 명시한 내용은 "
                f"다음과 같습니다: {localized or step.instruction_source_text}"
            )
            speech = (
                f"활성 프로토콜이 확인하는 내용을 먼저 정리했습니다. "
                "추가 설명은 검증 가능한 읽기 전용 근거가 있을 때만 분리해 안내합니다."
            )
        locally_supported: set[str] = set()
        if sections:
            locally_supported.add("definition")
        if entities and all(entity in explanations for entity in entities):
            locally_supported.add("role")
        if any(fact.kind == "warning" for fact in facts):
            locally_supported.add("safety")
        if {"hplc_water", "ambic"}.issubset(entities):
            locally_supported.add("relationship")
        if any(entity in {"solution_a", "solution_b"} for entity in entities):
            locally_supported.add("composition")
        research_dimensions = {
            "definition", "role", "difference", "safety", "rationale",
            "mechanism", "related_knowledge", "preparation",
        }
        unresolved = tuple(dict.fromkeys(
            dimension for dimension in plan.question_dimensions
            if dimension in research_dimensions and dimension not in locally_supported
        ))
        scopes = ["ACTIVE_PROTOCOL"]
        if unresolved:
            scopes.extend(("APPROVED_REFERENCE", "AUTHORITATIVE_EXTERNAL_REFERENCE"))
        return AnswerEnvelope(
            direct_answer=direct,
            speech_summary=speech,
            entity_sections=tuple(sections),
            protocol_relevance=relation,
            evidence_ids=evidence_ids,
            source_plan=SourcePlan(tuple(scopes), unresolved),
        )

    def state(self, *, spoken_summary: str | None = None) -> dict[str, object]:
        steps = self.fixture.steps
        current_step = steps[self.current_index] if self.active else None
        current_visual = (
            self.fixture.visual_for_step(self.current_index)
            if self.active
            else None
        )
        current_primary = (
            self._localized_fact(current_step.step_id, "current_step")
            if current_step is not None
            else None
        )
        warning_presentations = []
        if current_step is not None:
            for index, item in enumerate(current_step.warnings, 1):
                warning_presentations.append({
                    "primary_text": self._localized_fact(
                        current_step.step_id, f"warning_{index}"
                    ),
                    "source_text": item.source_text,
                    "source_page": item.evidence.source_page_number,
                    "evidence_id": f"warning_{index}",
                })
        return {
            "attached": True,
            "protocol_id": self.fixture.protocol_id,
            "revision_id": self.fixture.revision_id,
            "display_name": self.fixture.title,
            "development_only": self.fixture.development_only,
            "readiness_status": self.fixture.draft.readiness.status.value,
            "active": self.active,
            "current_step_label": (
                steps[self.current_index].source_label if self.active else None
            ),
            "current_step_id": (
                steps[self.current_index].step_id if self.active else None
            ),
            "total_steps": len(steps),
            "at_final_step": self.active and self.current_index == len(steps) - 1,
            "block_reason": self._block_reason,
            "revision": self._revision,
            "display_summary": (
                current_step.instruction_source_text
                if current_step is not None
                else None
            ),
            "primary_summary": current_primary,
            "source_language": "en",
            "spoken_summary": spoken_summary,
            "source_filename": (
                getattr(self.fixture, "source_filename", None)
                or (
                    self.fixture.source_pdf_path.name
                    if getattr(self.fixture, "source_pdf_path", None) is not None
                    else None
                )
            ),
            "source_sha256": getattr(self.fixture, "source_pdf_sha256", None),
            "source_page_refs": (
                [current_step.evidence.source_page_number]
                if current_step is not None
                else []
            ),
            "visual_assets": (
                [current_visual.public_dict()]
                if current_visual is not None
                else []
            ),
            "visual_status": (
                "available"
                if current_visual is not None
                else "unavailable"
            ),
            "warning_texts": (
                [item.source_text for item in current_step.warnings]
                if current_step is not None
                else []
            ),
            "warning_presentations": warning_presentations,
            # Warning severity is not represented in the canonical domain.
            # Keep ordinary warnings visible without inventing a critical cue.
            "critical_warning_texts": [],
        }

    def _current_step_readiness_blocker(
        self,
        observation_predicate: str | None = None,
    ) -> domain.ReadinessReasonCode | None:
        step_id = self.fixture.steps[self.current_index].step_id
        blocking_codes = {
            domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
            domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL,
        }
        blocker = next(
            (
                reason.code
                for reason in self.fixture.draft.readiness.reasons
                if reason.step_id == step_id and reason.code in blocking_codes
            ),
            None,
        )
        step_label = self.fixture.steps[self.current_index].source_label
        if observation_predicate == "positive" and (
            step_label in {"7", "9"}
            and blocker is domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL
            or step_label == "20"
            and blocker is domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY
        ):
            # This is a user-reported, source-defined observation. It does not
            # claim machine vision or model approval; the normal transactional
            # completion/report path still owns the single mutation.
            return None
        return blocker

    def plan(
        self,
        transcript: str,
        *,
        turn_id: int,
        language: str,
        transcript_quality: str | None = None,
        configuration_id: int | None = None,
        generation: int | None = None,
    ) -> CuratedProtocolTurnPlan:
        if turn_id in self._replay:
            return self._replay[turn_id]
        command_key = _utterance_key(transcript)
        pending = self._pending_completion_confirmation
        observation_pending = self._pending_observation_confirmation
        pending_valid = bool(
            pending is not None
            and self.active
            and self.current_index == pending.step_index
            and self.fixture.steps[self.current_index].step_id == pending.step_id
            and self._revision == pending.workflow_revision
            and turn_id == pending.requested_turn_id + 1
            and (
                pending.configuration_id is None
                or configuration_id == pending.configuration_id
            )
            and (
                pending.requested_generation is None
                or generation is None
                or generation >= pending.requested_generation
            )
        )
        normalized_confirmation = _semantic_utterance_key(transcript)
        observation_pending_valid = bool(
            observation_pending is not None
            and self.active
            and self.current_index == observation_pending.step_index
            and self.fixture.steps[self.current_index].step_id
            == observation_pending.step_id
            and self._revision == observation_pending.workflow_revision
            and turn_id == observation_pending.requested_turn_id + 1
            and (
                observation_pending.configuration_id is None
                or configuration_id == observation_pending.configuration_id
            )
            and (
                observation_pending.requested_generation is None
                or generation is None
                or generation >= observation_pending.requested_generation
            )
        )
        if pending is not None and not pending_valid:
            self._pending_completion_confirmation = None
        if observation_pending is not None and not observation_pending_valid:
            self._pending_observation_confirmation = None
        binary_reply = _binary_frame_reply(transcript)
        pending_language_mismatch = bool(
            (pending_valid or observation_pending_valid)
            and language == "ko"
            and re.fullmatch(r"[a-z]{1,4}", normalized_confirmation)
            and normalized_confirmation not in {"yes", "no", "done"}
        )
        if pending_valid and (
            _AFFIRMATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            or binary_reply == "affirmative"
        ):
            self._pending_completion_confirmation = None
            intent = CuratedControlIntent(
                intent_kind="pending_completion_confirmed",
                action=CuratedProtocolAction.NEXT,
                reported_completion=True,
                requested_transition="next",
                requested_followup="describe_new_current_step",
                target_step="authoritative_current_step",
                confidence_source="server_pending_confirmation",
                allows_state_mutation=True,
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        elif pending_valid and (
            _NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            or binary_reply == "negative"
        ):
            self._pending_completion_confirmation = None
            intent = CuratedControlIntent(
                intent_kind="pending_completion_declined",
                action=CuratedProtocolAction.DECLINE_COMPLETION,
                requested_transition="next",
                target_step="authoritative_current_step",
                confidence_source="server_pending_confirmation",
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        elif observation_pending_valid and (
            observed := _observation_predicate(
                observation_pending.step_label, transcript
            )
        ) is not None:
            self._pending_observation_confirmation = None
            intent = CuratedControlIntent(
                intent_kind="pending_observation_confirmed",
                action=CuratedProtocolAction.NEXT,
                reported_completion=observed == "positive",
                reported_observation=True,
                observation_predicate=observed,
                observation_outcome=normalized_confirmation,
                requested_transition=("next" if observed == "positive" else None),
                requested_followup="describe_new_current_step",
                target_step="authoritative_current_step",
                confidence_source="server_pending_observation",
                allows_state_mutation=observed == "positive",
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        elif observation_pending_valid and binary_reply in {
            "affirmative", "negative"
        }:
            self._pending_observation_confirmation = None
            observed = (
                observation_pending.affirmative_outcome
                if binary_reply == "affirmative"
                else observation_pending.negative_outcome
            )
            intent = CuratedControlIntent(
                intent_kind="pending_observation_confirmed",
                action=CuratedProtocolAction.NEXT,
                reported_completion=observed == "positive",
                reported_observation=True,
                observation_predicate=observed,
                observation_outcome=normalized_confirmation,
                requested_transition=("next" if observed == "positive" else None),
                requested_followup="describe_new_current_step",
                target_step="authoritative_current_step",
                confidence_source="server_pending_observation_binary_reply",
                allows_state_mutation=observed == "positive",
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        elif pending_language_mismatch:
            if pending_valid and pending is not None:
                self._pending_completion_confirmation = replace(
                    pending, requested_turn_id=turn_id,
                    requested_generation=generation,
                )
            if observation_pending_valid and observation_pending is not None:
                self._pending_observation_confirmation = replace(
                    observation_pending, requested_turn_id=turn_id,
                    requested_generation=generation,
                )
            intent = CuratedControlIntent(
                intent_kind="pending_reply_transcript_unreliable",
                action=CuratedProtocolAction.TRANSCRIPT_UNRELIABLE,
                transcript_quality="pending_frame_language_mismatch",
                confidence=None,
                confidence_source="source_frame_plausibility",
                requires_confirmation=True,
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        else:
            if pending_valid:
                # A non-answer invalidates the one-turn gate before normal routing.
                self._pending_completion_confirmation = None
            if observation_pending_valid:
                self._pending_observation_confirmation = None
            intent = classify_curated_control_intent(
                transcript,
                language=language,
                entity_inventory=self._entity_inventory(),
                recent_related_query=self._last_related_query,
                recent_related_entities=self._last_related_entities,
                discourse_context=(
                    self._discourse_context
                    if self._discourse_context.step_id
                    == self.fixture.steps[self.current_index].step_id
                    and self._discourse_context.workflow_revision == self._revision
                    else None
                ),
                completion_context=self.active,
            )
        if self.active:
            plausibility = assess_transcript_plausibility(
                transcript, self.current_step_semantic_frame()
            )
            claims = self._semantic_claim_requests(
                transcript, intent, language=language
            )
            intent = replace(
                intent,
                claim_requests=claims,
                plausibility_status=plausibility.status,
                plausibility_reason=plausibility.reason_code,
            )
            if any(
                claim.admission_status is ClaimAdmissionStatus.CLARIFICATION_REQUIRED
                for claim in claims
            ):
                intent = replace(
                    intent,
                    intent_kind="operational_parameter_ambiguous",
                    action=CuratedProtocolAction.CLARIFY_PARAMETER,
                    allows_state_mutation=False,
                    requires_confirmation=True,
                )
            if (
                plausibility.status == "incompatible_suspicious"
                and intent.action in {
                    CuratedProtocolAction.RELATED_QUESTION,
                    CuratedProtocolAction.OPERATIONAL_DEVIATION,
                    CuratedProtocolAction.OFF_TOPIC,
                }
            ):
                intent = replace(
                    intent,
                    intent_kind="operational_value_clarification_required",
                    action=CuratedProtocolAction.CLARIFY_PARAMETER,
                    allows_state_mutation=False,
                    requires_confirmation=True,
                )
        if (
            self.active
            and intent.action is CuratedProtocolAction.NEXT
            and self.fixture.steps[self.current_index].source_label in {"7", "9", "20"}
            and not intent.reported_observation
        ):
            observed = _observation_predicate(
                self.fixture.steps[self.current_index].source_label, transcript
            )
            if observed is not None:
                intent = replace(
                    intent,
                    reported_completion=observed == "positive",
                    reported_observation=True,
                    observation_predicate=observed,
                    observation_outcome=normalized_confirmation,
                    allows_state_mutation=observed == "positive",
                    requested_transition=("next" if observed == "positive" else None),
                )
            elif intent.reported_completion:
                intent = replace(
                    intent,
                    intent_kind="observation_confirmation_required",
                    action=CuratedProtocolAction.CLARIFY_COMPLETION,
                    reported_completion=False,
                    requires_confirmation=True,
                    allows_state_mutation=False,
                )
        if (
            transcript_quality is not None
            and intent.action not in {
                CuratedProtocolAction.STOP,
                CuratedProtocolAction.AUDIO_RECOVERY,
            }
        ):
            intent = CuratedControlIntent(
                intent_kind="transcript_unreliable",
                action=CuratedProtocolAction.TRANSCRIPT_UNRELIABLE,
                transcript_quality=transcript_quality,
                confidence=None,
                confidence_source="provider_metadata",
                language=language,
            )
        command = intent.action
        steps = self.fixture.steps
        opening_projection = (self.active, self.current_index, self._block_reason)
        changed = False

        if command is CuratedProtocolAction.STOP:
            changed = self.active
            self.active = False
            self._block_reason = None
            action = CuratedProtocolAction.STOP
            response = {
                "en": "The protocol session has ended without a completion claim.",
                "vi": "Phiên quy trình đã kết thúc mà không xác nhận hoàn thành.",
                "ko": "완료로 처리하지 않고 프로토콜 세션을 종료했습니다.",
            }.get(language, "프로토콜 세션을 종료했습니다.")
            plan = CuratedProtocolTurnPlan(
                action=action,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.STOP,
                facts=(),
                step_label=None,
                final_step=False,
                state_changed=changed,
                intent_kind=intent.intent_kind,
            )
        elif command is CuratedProtocolAction.START:
            resumed = self.active and bool(self._replay)
            if not self.active:
                self.active = True
                self.current_index = 0
                self._block_reason = None
                changed = True
            step = steps[self.current_index]
            control_text = _control_speech(
                CuratedProtocolAction.START,
                language,
                step.source_label,
                resumed=resumed,
                development_only=self.fixture.development_only,
            )
            response, primary, sources, pages, evidence_ids, translation_status = (
                _step_presentation(
                    self.fixture,
                    self.current_index,
                    language,
                    control_text,
                )
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.START,
                display_text=response,
                speech_text=control_text,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=changed,
                primary_text=primary,
                source_texts=sources,
                source_pages=pages,
                evidence_ids=evidence_ids,
                translation_status=translation_status,
                intent_kind=intent.intent_kind,
            )
        elif command is CuratedProtocolAction.AUDIO_RECOVERY:
            response = {
                "en": "I will replay the last available answer once. The protocol state will not change.",
                "vi": "Tôi sẽ phát lại câu trả lời gần nhất một lần. Trạng thái quy trình không thay đổi.",
                "ko": "마지막으로 재생 가능한 답변을 한 번 다시 들려드릴게요. 프로토콜 상태는 변경하지 않습니다.",
            }.get(language, "마지막 답변을 한 번 다시 재생합니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.AUDIO_RECOVERY,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                requested_followup=intent.requested_followup,
            )
        elif command is CuratedProtocolAction.CLARIFY_PARAMETER:
            step = steps[self.current_index]
            response = {
                "en": (
                    "That value is not uniquely bound to the current step. "
                    "Please repeat the value with its unit or say whether you are comparing it with the active protocol value. No state changed."
                ),
                "vi": (
                    "Giá trị đó chưa gắn rõ với bước hiện tại. Vui lòng nói lại kèm đơn vị. Trạng thái không thay đổi."
                ),
                "ko": (
                    "말씀하신 값은 현재 단계의 조건이나 비율에 명확히 연결되지 않습니다. "
                    "단위와 함께 다시 말하거나, 활성 프로토콜 값과 비교하려는 것인지 알려 주세요. 상태는 변경하지 않았습니다."
                ),
            }.get(language, "값과 단위를 다시 확인해 주세요. 상태는 변경하지 않았습니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.CLARIFY_PARAMETER,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                normalized_transcript=intent.normalized_transcript,
                plausibility_status=intent.plausibility_status,
                plausibility_reason=intent.plausibility_reason,
            )
        elif command is CuratedProtocolAction.TRANSCRIPT_UNRELIABLE:
            response = ({
                "en": "Did you mean start the protocol? Please say it once more clearly. No procedure state changed.",
                "vi": "Bạn có muốn bắt đầu quy trình không? Vui lòng nói lại rõ ràng. Trạng thái chưa thay đổi.",
                "ko": "‘프로토콜을 시작해 줘’라고 말씀하셨나요? 한 번만 명확히 다시 말씀해 주세요. 프로토콜 상태는 변경하지 않았습니다.",
            } if intent.intent_kind == "ambiguous_protocol_start" else {
                "en": "I could not reliably recognize that utterance. Please repeat it clearly. No procedure state changed.",
                "vi": "Tôi chưa nhận dạng câu nói đó một cách đáng tin cậy. Vui lòng nói lại rõ ràng. Trạng thái quy trình không thay đổi.",
                "ko": "방금 음성을 정확히 인식하지 못했습니다. 짧게 다시 말씀해 주세요. 프로토콜 상태는 변경하지 않았습니다.",
            }).get(language, "방금 음성을 정확히 인식하지 못했습니다. 다시 말씀해 주세요.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.TRANSCRIPT_UNRELIABLE,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                normalized_transcript=intent.normalized_transcript,
                transcript_correction_note=intent.transcript_correction_note,
                transcript_corrections=intent.transcript_corrections,
            )
        elif command is CuratedProtocolAction.CANCEL_READONLY:
            response = {
                "en": "The read-only reference lookup was cancelled. The protocol state did not change.",
                "vi": "Việc tra cứu tài liệu chỉ đọc đã được hủy. Trạng thái quy trình không thay đổi.",
                "ko": "진행 중인 읽기 전용 자료 확인을 취소했습니다. 프로토콜 상태는 변경하지 않았습니다.",
            }.get(language, "자료 확인을 취소했고 프로토콜 상태는 유지했습니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.CANCEL_READONLY,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                requested_followup=intent.requested_followup,
            )
        elif not self.active:
            response = {
                "en": "The protocol session is stopped. Say start protocol to resume it.",
                "vi": "Phiên quy trình đã dừng. Hãy yêu cầu bắt đầu quy trình để tiếp tục.",
                "ko": "프로토콜 세션이 중지되었습니다. 다시 사용하려면 프로토콜을 시작해 주세요.",
            }.get(language, "프로토콜 세션이 중지되었습니다. 프로토콜을 시작해 주세요.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.INACTIVE,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(),
                step_label=None,
                final_step=False,
                state_changed=False,
                intent_kind=intent.intent_kind,
            )
        elif command is CuratedProtocolAction.REPORT_ANOMALY:
            step = steps[self.current_index]
            response = {
                "en": (
                    f"I recognized an issue to record against step {step.source_label}. "
                    "I will confirm only after the experiment record accepts it."
                ),
                "vi": (
                    f"Tôi đã nhận yêu cầu ghi vấn đề ở bước {step.source_label}. "
                    "Tôi chỉ xác nhận sau khi bản ghi thí nghiệm lưu thành công."
                ),
                "ko": (
                    f"현재 {step.source_label}단계의 이상 사항 기록 요청을 확인했습니다. "
                    "실험 기록 저장이 성공한 뒤에만 기록 완료를 확인합니다."
                ),
            }.get(language, "이상 사항 기록 요청을 확인했습니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.REPORT_ANOMALY,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                reported_anomaly=True,
                anomaly_category=intent.anomaly_category,
                anomaly_text=transcript.strip()[:800],
            )
        elif command is CuratedProtocolAction.SHOW_REPORT:
            step = steps[self.current_index]
            response = {
                "en": "The current experiment record is shown below the active workspace.",
                "vi": "Bản ghi thí nghiệm hiện tại được hiển thị bên dưới không gian làm việc.",
                "ko": "현재 실험 기록을 작업 영역 아래에 표시했습니다.",
            }.get(language, "현재 실험 기록을 표시했습니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.SHOW_REPORT,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                requested_followup=intent.requested_followup,
            )
        elif command is CuratedProtocolAction.PROTOCOL_QUERY:
            if intent.protocol_scope is None:
                raise CuratedProtocolFixtureError(
                    "Protocol query scope is unavailable."
                )
            display, speech, protocol_facts = _protocol_query_presentation(
                self.fixture, current_index=self.current_index,
                scope=intent.protocol_scope, language=language,
            )
            step = steps[self.current_index]
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.PROTOCOL_QUERY,
                display_text=display, speech_text=speech,
                speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                facts=protocol_facts, step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False, primary_text=speech,
                source_texts=tuple(fact.text for fact in protocol_facts),
                source_pages=tuple(fact.source_page for fact in protocol_facts),
                evidence_ids=tuple(fact.fact_id for fact in protocol_facts),
                translation_status=(
                    "deterministic_protocol_structure"
                    if language == "ko" else "source_language"
                ),
                intent_kind=intent.intent_kind,
                question_kind=intent.question_kind,
                answer_origin="current_protocol",
            )
        elif command is CuratedProtocolAction.NEXT_INFORMATION:
            current = steps[self.current_index]
            if self.current_index >= len(steps) - 1:
                response = (
                    "This is the final protocol step; there is no later step to preview. "
                    "The workflow state is unchanged."
                    if language == "en" else
                    "현재 단계가 프로토콜의 마지막 단계라 미리 볼 다음 단계가 없습니다. 상태는 변경하지 않았습니다."
                )
                facts: tuple[CuratedProtocolFact, ...] = ()
                next_label = current.source_label
            else:
                next_index = self.current_index + 1
                next_step = steps[next_index]
                localized = self._localized_fact(next_step.step_id, "current_step")
                instruction = localized if language == "ko" and localized else next_step.instruction_source_text
                blocker = self._current_step_readiness_blocker()
                blocker_text = (
                    " The current step has an unresolved execution gate, so this preview does not authorize entry."
                    if blocker is not None and language == "en" else
                    " 현재 단계의 실행 제어가 미해결이므로 이 미리보기는 진입 승인이 아닙니다."
                    if blocker is not None else ""
                )
                response = (
                    f"Preview only — Step {next_step.source_label}: {instruction} "
                    f"The workflow remains at step {current.source_label}.{blocker_text}"
                    if language == "en" else
                    f"다음 단계 미리보기 · {next_step.source_label}단계: {instruction} "
                    f"워크플로는 현재 {current.source_label}단계에 그대로 있습니다.{blocker_text}"
                )
                facts = self.fixture.facts_for_step(next_index)
                next_label = next_step.source_label
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.NEXT_INFORMATION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                facts=facts,
                step_label=current.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                source_texts=tuple(fact.text for fact in facts[:4]),
                source_pages=tuple(fact.source_page for fact in facts[:4]),
                evidence_ids=tuple(fact.fact_id for fact in facts[:4]),
                translation_status=("verified_sidecar" if language == "ko" else "source_language"),
                intent_kind=intent.intent_kind,
                requested_followup=intent.requested_followup,
                target_step=next_label,
                question_kind=intent.question_kind,
            )
        elif command is CuratedProtocolAction.COMPLETION_CRITERIA:
            step = steps[self.current_index]
            step_facts = self.fixture.facts_for_step(self.current_index)
            expected = tuple(fact for fact in step_facts if fact.kind == "expected_result")
            blocker = self._current_step_readiness_blocker()
            if blocker is not None:
                reason = (
                    "the observed end point required by the repeat-until instruction is not connected to a supported server completion signal"
                    if blocker is domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL else
                    "the source contains an unresolved execution ambiguity"
                )
                response = (
                    f"Step {step.source_label} cannot be marked complete yet: {reason}. "
                    "No completion rule is being inferred, and the workflow state is unchanged."
                    if language == "en" else
                    f"{step.source_label}단계는 아직 완료 처리할 수 없습니다. "
                    + (
                        "반복 종료에 필요한 관찰 결과가 지원되는 서버 완료 신호에 연결되어 있지 않습니다. "
                        if blocker is domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL else
                        "원문의 실행 의미가 미해결 상태입니다. "
                    )
                    + "확인되지 않은 완료 조건은 만들지 않았고 워크플로 상태도 변경하지 않았습니다."
                )
            elif expected:
                response = (
                    f"Step {step.source_label} is complete only when its verified expected result is satisfied. "
                    "The source evidence is shown below; this explanation does not record completion."
                    if language == "en" else
                    f"{step.source_label}단계의 완료 기준은 원문에 확인된 예상 결과가 충족되는 것입니다. "
                    "아래에 근거를 표시하며, 이 설명만으로 완료가 기록되지는 않습니다."
                )
            else:
                response = (
                    f"The active protocol gives the Step {step.source_label} action but no separate verified completion criterion. "
                    "A clear completion report can use the server's normal validation path; this question itself changes nothing."
                    if language == "en" else
                    f"활성 프로토콜은 {step.source_label}단계의 동작을 제시하지만 별도의 검증된 완료 기준은 명시하지 않습니다. "
                    "명확한 완료 보고는 서버의 기존 검증 경로로 처리되며, 지금 질문은 상태를 바꾸지 않습니다."
                )
            evidence = expected or tuple(fact for fact in step_facts if fact.fact_id == "current_step")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.COMPLETION_CRITERIA,
                display_text=response,
                speech_text=response,
                speech_mode=(CuratedProtocolSpeechMode.BLOCKED if blocker is not None else CuratedProtocolSpeechMode.VERIFIED_FACT),
                facts=evidence,
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                source_texts=tuple(fact.text for fact in evidence),
                source_pages=tuple(fact.source_page for fact in evidence),
                evidence_ids=tuple(fact.fact_id for fact in evidence),
                translation_status="deterministic_protocol_structure",
                intent_kind=intent.intent_kind,
                requested_followup=intent.requested_followup,
                target_step=step.source_label,
                question_kind="completion_criteria",
                limitations=((f"blocker:{blocker.value}",) if blocker is not None else ()),
            )
        elif command is CuratedProtocolAction.OPERATIONAL_DEVIATION:
            step = steps[self.current_index]
            step_facts = self.fixture.facts_for_step(self.current_index)
            key = intent.normalized_transcript or command_key
            requested_numbers = tuple(re.findall(r"\d+(?:\.\d+)?", key))
            supported = tuple(
                fact for fact in step_facts
                if any(number in fact.text for number in requested_numbers)
            )
            if not supported and intent.requested_entities:
                entity_labels = {
                    "ambic": ("ambic", "ammonium bicarbonate"),
                    "hplc_water": ("hplc water",),
                    "solution_a": ("solution a",),
                    "solution_b": ("solution b",),
                    "acetonitrile": ("acetonitrile",),
                }
                requested_labels = tuple(
                    label
                    for entity in intent.requested_entities
                    for label in entity_labels.get(entity, ())
                )
                supported = tuple(
                    fact for fact in step_facts
                    if any(label in fact.text.lower() for label in requested_labels)
                )
            approved_text = supported[0].text if supported else None
            response = (
                (
                    f"The active protocol requirement is: {approved_text} I cannot authorize the requested change. "
                    "General background may be explained separately, but it does not change the protocol."
                ) if language == "en" and approved_text else (
                    "The current step does not contain the stated approved value, and I cannot authorize the requested change. "
                    "Please identify the intended step; the protocol state is unchanged."
                ) if language == "en" else (
                    f"활성 프로토콜의 승인된 요구사항은 다음과 같습니다: {approved_text} "
                    "요청한 변경은 승인할 수 없습니다. 일반적인 배경 설명은 별도로 제공할 수 있지만 프로토콜은 변경되지 않습니다."
                ) if approved_text else (
                    "현재 단계에는 말씀하신 승인 값이 없으며 요청한 변경은 승인할 수 없습니다. "
                    "대상 단계를 알려 주세요. 프로토콜 상태는 변경되지 않았습니다."
                )
            )
            evidence = supported or tuple(fact for fact in step_facts if fact.fact_id == "current_step")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.OPERATIONAL_DEVIATION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=evidence,
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                source_texts=tuple(fact.text for fact in evidence),
                source_pages=tuple(fact.source_page for fact in evidence),
                evidence_ids=tuple(fact.fact_id for fact in evidence),
                translation_status="deterministic_protocol_structure",
                intent_kind=intent.intent_kind,
                target_step=step.source_label,
                question_kind=intent.question_kind,
                answer_origin="unsupported_operational",
                source_plan_scopes=("ACTIVE_PROTOCOL", "UNSUPPORTED_OPERATIONAL"),
                unresolved_dimensions=("rationale",),
            )
        elif command is CuratedProtocolAction.NEXT:
            blocker = self._current_step_readiness_blocker(
                intent.observation_predicate
            )
            if (
                intent.reported_observation
                and intent.observation_predicate == "negative"
            ):
                step = steps[self.current_index]
                endpoint = (
                    "fully destained and transparent"
                    if step.source_label == "7" else
                    "white/whitish and dehydrated"
                )
                if step.source_label == "7":
                    followup_en = "Continue the source-authorized Steps 2–7 repeat cycle, then report the visible endpoint again."
                    followup_ko = "원문이 지시한 2–7단계 반복 주기를 계속한 뒤 관찰 결과를 다시 말씀해 주세요."
                elif step.source_label == "9":
                    followup_en = "Continue the source-authorized Steps 8–9 repeat cycle, then report the visible endpoint again."
                    followup_ko = "원문이 지시한 8–9단계 반복 주기를 계속한 뒤 관찰 결과를 다시 말씀해 주세요."
                else:
                    followup_en = "The source mentions repeating Steps 17–18, but that sequence remains unresolved in Candidate A; no loop transition was executed."
                    followup_ko = "원문에는 17–18단계 반복이 적혀 있지만 Candidate A에서는 그 순서가 미해결이므로 반복 이동을 실행하지 않습니다."
                response = (
                    f"Your report means the source-defined endpoint ({endpoint}) is not yet satisfied for Step {step.source_label}. "
                    f"The step remains current and no transition was made. {followup_en}"
                    if language == "en" else
                    f"말씀한 결과는 {step.source_label}단계의 원문 관찰 기준이 아직 충족되지 않았다는 뜻입니다. "
                    f"현재 단계를 유지하며 다음 단계로 이동하지 않습니다. {followup_ko}"
                )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.NEXT,
                    display_text=response,
                    speech_text=response,
                    speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=False,
                    intent_kind=intent.intent_kind,
                    reported_observation=True,
                    observation_predicate="negative",
                    observation_outcome=intent.observation_outcome,
                    requested_transition=None,
                    target_step=intent.target_step,
                )
            elif blocker is not None:
                self._block_reason = blocker.value
                step = steps[self.current_index]
                if blocker is domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL:
                    response = {
                        "en": (
                            f"Step {step.source_label} is a repeat-until step, but its observed endpoint is not connected to a supported server completion signal. "
                            "You cannot satisfy that gate through the current Candidate A development session. The step has not been marked complete, and no transition was made."
                        ),
                        "vi": (
                            f"Bước {step.source_label} yêu cầu lặp lại đến khi đạt điểm kết thúc quan sát, nhưng tín hiệu hoàn thành đó chưa được máy chủ hỗ trợ. Bước vẫn chưa hoàn thành."
                        ),
                        "ko": (
                            f"{step.source_label}단계는 관찰 결과가 충족될 때까지 반복해야 하지만, 그 관찰 종점이 지원되는 서버 완료 신호에 연결되어 있지 않습니다. "
                            "현재 Candidate A 개발 세션에서는 사용자가 이 게이트를 충족할 수 없습니다. 완료 처리되지 않았습니다. 단계 이동도 하지 않았습니다."
                        ),
                    }.get(language, "관찰 기반 반복 종료 신호가 지원되지 않아 진행할 수 없습니다.")
                else:
                    response = {
                        "en": (
                            f"Step {step.source_label} contains an unresolved source ambiguity, so Candidate A development mode cannot validate completion. "
                            "The step has not been marked complete, and no transition was made."
                        ),
                        "vi": f"Bước {step.source_label} còn mơ hồ trong nguồn nên chưa thể xác nhận hoàn thành.",
                        "ko": (
                            f"{step.source_label}단계는 원문의 실행 의미가 미해결 상태여서 Candidate A 개발 모드에서 완료를 검증할 수 없습니다. "
                            "완료 처리되지 않았습니다. 단계 이동도 하지 않았습니다."
                        ),
                    }.get(language, "원문의 실행 의미가 미해결 상태여서 진행할 수 없습니다.")
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.NEXT,
                    display_text=response,
                    speech_text=response,
                    speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=False,
                    intent_kind=intent.intent_kind,
                    reported_completion=intent.reported_completion,
                    requested_transition=intent.requested_transition,
                    requested_followup=intent.requested_followup,
                    target_step=intent.target_step,
                    reported_observation=intent.reported_observation,
                    observation_predicate=intent.observation_predicate,
                    observation_outcome=intent.observation_outcome,
                )
            elif self.current_index < len(steps) - 1:
                self.current_index += 1
                self._block_reason = None
                changed = True
                prefix = "Advanced once."
                step = steps[self.current_index]
                control_text = _control_speech(
                    CuratedProtocolAction.NEXT,
                    language,
                    step.source_label,
                    development_only=self.fixture.development_only,
                )
                response, primary, sources, pages, evidence_ids, translation_status = (
                    _step_presentation(
                        self.fixture,
                        self.current_index,
                        language,
                        control_text,
                    )
                )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.NEXT,
                    display_text=response,
                    speech_text=control_text,
                    speech_mode=CuratedProtocolSpeechMode.CONTROL,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=changed,
                    primary_text=primary,
                    source_texts=sources,
                    source_pages=pages,
                    evidence_ids=evidence_ids,
                    translation_status=translation_status,
                    intent_kind=intent.intent_kind,
                    reported_completion=intent.reported_completion,
                    requested_transition=intent.requested_transition,
                    requested_followup=intent.requested_followup,
                    target_step=intent.target_step,
                    reported_observation=intent.reported_observation,
                    observation_predicate=intent.observation_predicate,
                    observation_outcome=intent.observation_outcome,
                )
            else:
                self._block_reason = "final_step_boundary"
                step = steps[self.current_index]
                response = _step_reply(
                    language,
                    step.source_label,
                    step.instruction_source_text,
                    prefix=(
                        "The final-step boundary has been reached."
                        if language == "en"
                        else "마지막 단계이며 더 진행하지 않습니다."
                    ),
                    development_only=self.fixture.development_only,
                )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.NEXT,
                    display_text=response,
                    speech_text=(
                        "The final-step boundary has been reached; no further "
                        "step was created."
                        if language == "en"
                        else (
                            "Đã đến giới hạn bước cuối; không tạo thêm bước nào."
                            if language == "vi"
                            else "마지막 단계 경계이며 더 진행하지 않습니다."
                        )
                    ),
                    speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=step.source_label,
                    final_step=True,
                    state_changed=False,
                    intent_kind=intent.intent_kind,
                    reported_completion=intent.reported_completion,
                    requested_transition=intent.requested_transition,
                    requested_followup=intent.requested_followup,
                    target_step=intent.target_step,
                    reported_observation=intent.reported_observation,
                    observation_predicate=intent.observation_predicate,
                    observation_outcome=intent.observation_outcome,
                )
        elif command in (
            CuratedProtocolAction.CURRENT,
            CuratedProtocolAction.REPEAT,
        ):
            step = steps[self.current_index]
            action = command
            control_text = _control_speech(
                action,
                language,
                step.source_label,
                development_only=self.fixture.development_only,
            )
            response, primary, sources, pages, evidence_ids, translation_status = (
                _step_presentation(
                    self.fixture,
                    self.current_index,
                    language,
                    control_text,
                )
            )
            plan = CuratedProtocolTurnPlan(
                action=action,
                display_text=response,
                speech_text=control_text,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=primary,
                source_texts=sources,
                source_pages=pages,
                evidence_ids=evidence_ids,
                translation_status=translation_status,
                intent_kind=intent.intent_kind,
            )
        elif command is CuratedProtocolAction.FULL_DETAIL:
            target_index = self._step_index_for_label(intent.target_step)
            if target_index is None:
                response = {
                    "en": "That step is not present in the selected protocol. The current step did not change.",
                    "vi": "Bước đó không có trong quy trình đã chọn. Bước hiện tại không thay đổi.",
                    "ko": "선택한 절차에 해당 단계가 없습니다. 현재 단계는 변경하지 않았습니다.",
                }.get(language, "해당 단계를 확인할 수 없습니다.")
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.FULL_DETAIL,
                    display_text=response,
                    speech_text=response,
                    speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                    facts=(),
                    step_label=steps[self.current_index].source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=False,
                    intent_kind=intent.intent_kind,
                    target_step=intent.target_step,
                )
                self._replay[turn_id] = plan
                return plan
            step = steps[target_index]
            if intent.intent_kind == "full_detail":
                localized = self._localized_fact(step.step_id, "current_step")
                response = _display_contract(
                    language,
                    (
                        localized
                        if language == "ko" and localized is not None
                        else step.instruction_source_text
                    ),
                    (step.instruction_source_text,),
                    (step.evidence.source_page_number,),
                    ("current_step",),
                    translated=language != "ko" or localized is not None,
                )
                speech = step.instruction_source_text
                admitted_facts = self.fixture.facts_for_step(target_index)
                sources = (step.instruction_source_text,)
                pages = (step.evidence.source_page_number,)
                evidence_ids = ("current_step",)
                translation_status = (
                    "verified_sidecar"
                    if language == "ko" and localized is not None
                    else "source_language"
                )
            else:
                (
                    response, speech, admitted_facts, sources, pages,
                    evidence_ids, translation_status,
                ) = _detailed_step_presentation(
                    self.fixture,
                    target_index,
                    language,
                    expected_result_only=(
                        intent.intent_kind == "expected_result_explanation"
                    ),
                )
            primary = response.split("\n\n원문 · English", 1)[0]
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.FULL_DETAIL,
                display_text=response,
                speech_text=speech,
                speech_mode=CuratedProtocolSpeechMode.FULL_DETAIL,
                facts=admitted_facts,
                step_label=step.source_label,
                final_step=target_index == len(steps) - 1,
                state_changed=False,
                primary_text=primary,
                source_texts=sources,
                source_pages=pages,
                evidence_ids=evidence_ids,
                translation_status=translation_status,
                intent_kind=intent.intent_kind,
                target_step=(
                    step.source_label
                    if intent.intent_kind in {
                        "step_elaboration", "expected_result_explanation"
                    }
                    else intent.target_step
                ),
                source_plan_scopes=(
                    ("ACTIVE_PROTOCOL", "SOURCE_APPROVED_ALTERNATIVE")
                    if step.source_label == "3"
                    and any(fact.kind == "note" for fact in admitted_facts)
                    else ("ACTIVE_PROTOCOL",)
                ),
            )
        elif command is CuratedProtocolAction.VISUAL_REQUEST:
            step = steps[self.current_index]
            source_visual = self.fixture.visual_for_step(self.current_index)
            if source_visual is not None:
                control_text = {
                    "en": f"The verified original visual for step {step.source_label} is shown.",
                    "vi": f"Hình ảnh gốc đã xác minh cho bước {step.source_label} được hiển thị.",
                    "ko": f"현재 {step.source_label}단계의 검증된 원본 시각 자료를 표시합니다.",
                }.get(language, f"현재 {step.source_label}단계의 원본 시각 자료를 표시합니다.")
            else:
                control_text = ({
                    "en": (
                        f"Step {step.source_label} has no verified original visual. "
                        "An authoritative real-image source will be checked only when web image search is enabled."
                    ),
                    "vi": (
                        f"Bước {step.source_label} không có hình ảnh gốc đã xác minh. "
                        "Nguồn ảnh thực có thẩm quyền chỉ được kiểm tra khi tìm kiếm ảnh web được bật."
                    ),
                    "ko": (
                        f"현재 {step.source_label}단계에는 검증된 원본 시각 자료가 없습니다. "
                        "웹 이미지 검색이 활성화된 경우에만 권위 있는 실제 이미지 출처를 확인합니다."
                    ),
                } if intent.visual_kind == "web_photo" else {
                    "en": (
                        f"Step {step.source_label} has no verified original visual. "
                        "A separate illustration will be prepared only when image generation is enabled."
                    ),
                    "vi": (
                        f"Bước {step.source_label} không có hình ảnh gốc đã xác minh. "
                        "Hình minh họa riêng chỉ được chuẩn bị khi tính năng tạo ảnh được bật."
                    ),
                    "ko": (
                        f"현재 {step.source_label}단계에는 검증된 원본 시각 자료가 없습니다. "
                        "이미지 생성 기능이 활성화된 경우에만 별도 삽화를 준비합니다."
                    ),
                }).get(language, f"현재 {step.source_label}단계에는 검증된 원본 시각 자료가 없습니다.")
            response, primary, sources, pages, evidence_ids, translation_status = (
                _step_presentation(
                    self.fixture,
                    self.current_index,
                    language,
                    control_text,
                )
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.VISUAL_REQUEST,
                display_text=response,
                speech_text=control_text,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(
                    self.related_facts(transcript)
                    if intent.requested_entities else
                    self.fixture.facts_for_step(self.current_index)
                ),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=primary,
                source_texts=sources,
                source_pages=pages,
                evidence_ids=evidence_ids,
                translation_status=translation_status,
                intent_kind=intent.intent_kind,
                target_step=intent.target_step,
                visual_requested=True,
                visual_kind=intent.visual_kind,
                requested_entity=intent.requested_entity,
                requested_entities=intent.requested_entities,
                normalized_transcript=intent.normalized_transcript,
                transcript_correction_note=intent.transcript_correction_note,
                transcript_corrections=intent.transcript_corrections,
                question_dimensions=intent.question_dimensions,
                coreference_status=intent.coreference_status,
                coreference_reason=intent.coreference_reason,
            )
            if intent.requested_entities:
                envelope=self.protocol_answer_envelope(
                    replace(plan, claim_requests=intent.claim_requests),
                    language=language,
                )
                visual_status=(
                    "검증된 원본 시각 자료를 함께 표시합니다."
                    if source_visual is not None else
                    "요청하신 시각 자료를 별도로 확인합니다."
                )
                plan=replace(
                    plan,
                    display_text=(
                        f"직접 답변\n{envelope.direct_answer}\n\n"
                        f"시각 자료\n{visual_status}"
                    ),
                    speech_text=envelope.speech_summary,
                    speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                    primary_text=envelope.direct_answer,
                    source_texts=tuple(fact.text for fact in plan.facts[:8]),
                    source_pages=tuple(fact.source_page for fact in plan.facts[:8]),
                    evidence_ids=tuple(fact.fact_id for fact in plan.facts[:8]),
                    source_plan_scopes=envelope.source_plan.scopes,
                    unresolved_dimensions=(
                        envelope.source_plan.unresolved_dimensions),
                    unresolved_claim_ids=(
                        envelope.source_plan.unresolved_claim_ids),
                )
        elif command is CuratedProtocolAction.CLARIFY_COMPLETION:
            step = steps[self.current_index]
            if intent.intent_kind == "observation_confirmation_required":
                if step.source_label == "7":
                    response = (
                        "Is the gel fully destained and transparent? I will not record completion until you report that observation."
                        if language == "en" else
                        "젤이 완전히 탈색되어 투명한가요? 그 관찰 결과를 말씀해 주셔야 완료를 기록할 수 있어요."
                    )
                else:
                    response = (
                        "Has the gel turned white or whitish and reached the dehydrated endpoint? I will not record completion until you report that observation."
                        if language == "en" else
                        "젤이 흰색 또는 흰빛으로 변하고 탈수 종점에 도달했나요? 그 관찰 결과를 말씀해 주셔야 완료를 기록할 수 있어요."
                    )
            else:
                response = ({
                "en": "Have you completed the current step? No state has changed.",
                "vi": "Bạn đã hoàn thành bước hiện tại chưa? Trạng thái chưa thay đổi.",
                "ko": "현재 단계를 완료하셨나요? 아직 상태는 변경하지 않았습니다.",
            } if intent.intent_kind == "next_step_confirmation_required" else {
                "en": (
                    f"Please confirm whether step {step.source_label} is complete "
                    "and you want to move to the next step. No state was changed."
                ),
                "vi": (
                    f"Vui lòng xác nhận bước {step.source_label} đã hoàn thành và "
                    "bạn muốn chuyển sang bước tiếp theo. Trạng thái chưa thay đổi."
                ),
                "ko": (
                    f"현재 {step.source_label}단계를 완료했고 다음 단계로 이동할지 "
                    "명확히 말씀해 주세요. 상태는 변경하지 않았습니다."
                ),
                }).get(language, "현재 단계를 완료하고 다음으로 이동할지 확인해 주세요.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.CLARIFY_COMPLETION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                target_step=intent.target_step,
                intent_confidence=intent.confidence,
                normalized_transcript=intent.normalized_transcript,
                transcript_correction_note=intent.transcript_correction_note,
                transcript_corrections=intent.transcript_corrections,
                reported_observation=intent.reported_observation,
                observation_predicate=intent.observation_predicate,
                observation_outcome=intent.observation_outcome,
            )
        elif command is CuratedProtocolAction.DECLINE_COMPLETION:
            step = steps[self.current_index]
            response = {
                "en": (
                    f"Understood. Step {step.source_label} remains current. "
                    "No completion or report event was recorded."
                ),
                "vi": (
                    f"Đã hiểu. Bước {step.source_label} vẫn là bước hiện tại. "
                    "Không ghi nhận hoàn thành."
                ),
                "ko": (
                    f"알겠습니다. 현재 {step.source_label}단계를 그대로 유지합니다. "
                    "완료 처리나 완료 기록은 만들지 않았습니다."
                ),
            }.get(language, "현재 단계를 그대로 유지합니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.DECLINE_COMPLETION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                requested_transition=intent.requested_transition,
                target_step=intent.target_step,
            )
        elif command is CuratedProtocolAction.CLARIFY_REFERENCE:
            step=steps[self.current_index]
            candidates=intent.requested_entities
            labels={
                "solution_a":"Solution A","solution_b":"Solution B",
                "hplc_water":"HPLC water","ambic":"AMBIC",
                "acetonitrile":"acetonitrile","gel_plug":"gel plug",
            }
            if intent.coreference_status==CoreferenceStatus.AMBIGUOUS.value:
                choices="와 ".join(labels.get(item,item) for item in candidates[:2])
                response=(
                    f"{choices} 중 어느 것을 말씀하시나요? 프로토콜 상태는 변경하지 않았습니다."
                    if language=="ko" else
                    f"Which do you mean: {' or '.join(labels.get(item,item) for item in candidates[:2])}? The protocol state did not change."
                )
            else:
                response=(
                    "어떤 물질이나 용액을 말씀하시는지 이름을 알려 주세요. 프로토콜 상태는 변경하지 않았습니다."
                    if language=="ko" else
                    "Please name the material or solution you mean. The protocol state did not change."
                )
            plan=CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.CLARIFY_REFERENCE,
                display_text=response,speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(),step_label=step.source_label,
                final_step=self.current_index==len(steps)-1,
                state_changed=False,intent_kind=intent.intent_kind,
                requested_entities=candidates,
                question_kind=intent.question_kind,
                normalized_transcript=intent.normalized_transcript,
                question_dimensions=intent.question_dimensions,
                coreference_status=intent.coreference_status,
                coreference_reason=intent.coreference_reason,
            )
        elif self._needs_solution_clarification(transcript):
            step = steps[self.current_index]
            self._pending_clarification = "solution_a_or_b"
            response = {
                "en": "Do you mean Solution A or Solution B? No protocol state changed.",
                "vi": "Bạn muốn hỏi Solution A hay Solution B? Trạng thái quy trình không thay đổi.",
                "ko": "Solution A와 Solution B 중 어느 용액을 말씀하시나요? 프로토콜 상태는 변경하지 않았습니다.",
            }.get(language, "Solution A와 Solution B 중 어느 용액인지 말씀해 주세요.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.CLARIFY_REFERENCE,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(),
                step_label=step.source_label,
                final_step=False,
                state_changed=False,
                intent_kind="ambiguous_protocol_entity",
                target_step=step.source_label,
            )
        elif contextual := self._contextual_solution_fact(transcript):
            source_index, selected_fact, resolved_entity = contextual
            self._pending_clarification = None
            source_step = steps[source_index]
            current_step = steps[self.current_index]
            localized = self._localized_fact(
                source_step.step_id, selected_fact.fact_id
            )
            translated = language != "ko" or localized is not None
            primary = (
                localized
                if language == "ko" and localized is not None
                else selected_fact.text
                if language != "ko"
                else "검증된 한국어 번역을 사용할 수 없습니다."
            )
            evidence_id = f"{source_step.step_id}/{selected_fact.fact_id}"
            display_text = _display_contract(
                language,
                primary,
                (selected_fact.text,),
                (selected_fact.source_page,),
                (evidence_id,),
                translated=translated,
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.QUESTION,
                display_text=display_text,
                speech_text=primary,
                speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                facts=(selected_fact,),
                step_label=current_step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                fact_id=evidence_id,
                primary_text=primary,
                source_texts=(selected_fact.text,),
                source_pages=(selected_fact.source_page,),
                evidence_ids=(evidence_id,),
                translation_status=(
                    "verified_sidecar" if language == "ko" and localized else
                    "unavailable" if language == "ko" else "source_language"
                ),
                intent_kind="contextual_protocol_entity",
                target_step=current_step.source_label,
                limitations=(f"resolved_entity:{resolved_entity}",),
            )
        elif (
            selected_fact := _select_verified_fact(
                transcript,self.fixture.facts_for_step(self.current_index)
            )
        ) is not None:
            step = steps[self.current_index]
            localized = self._localized_fact(step.step_id, selected_fact.fact_id)
            translated = language != "ko" or localized is not None
            primary = (
                localized
                if language == "ko" and localized is not None
                else selected_fact.text
                if language != "ko"
                else "검증된 한국어 번역을 사용할 수 없습니다."
            )
            display_text = _display_contract(
                language,primary,(selected_fact.text,),
                (selected_fact.source_page,),(selected_fact.fact_id,),
                translated=translated,
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.QUESTION,
                display_text=display_text,speech_text=primary,
                speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                facts=(selected_fact,),step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,fact_id=selected_fact.fact_id,
                primary_text=primary,source_texts=(selected_fact.text,),
                source_pages=(selected_fact.source_page,),
                evidence_ids=(selected_fact.fact_id,),
                translation_status=(
                    "verified_sidecar" if language == "ko" and localized else
                    "unavailable" if language == "ko" else "source_language"
                ),
                intent_kind="current_protocol_fact",
            )
        elif command is CuratedProtocolAction.OFF_TOPIC:
            step = steps[self.current_index]
            response = {
                "en": (
                    "I can help with the active laboratory procedure and related "
                    f"laboratory references. The procedure remains at step {step.source_label}."
                ),
                "vi": (
                    "Tôi có thể hỗ trợ quy trình phòng thí nghiệm đang hoạt động "
                    f"và tài liệu liên quan. Quy trình vẫn ở bước {step.source_label}."
                ),
                "ko": (
                    "현재 진행 중인 실험 절차와 관련 실험실 "
                    f"자료에 대한 질문을 도와드릴 수 있어요. 현재 {step.source_label}단계를 유지합니다."
                ),
            }.get(language, f"현재 프로토콜은 {step.source_label}단계를 유지합니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.OFF_TOPIC,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                answer_origin="unsupported",
            )
        else:
            step = steps[self.current_index]
            missing_followup_query = (
                command is CuratedProtocolAction.RELATED_QUESTION
                and intent.requested_followup in {
                    "search_external_reference", "continue_related_question"
                }
                and self._last_related_query is None
            )
            response = (
                {
                    "en": "Please ask the related laboratory question first, then request a web search. The protocol state did not change.",
                    "vi": "Hãy hỏi câu hỏi phòng thí nghiệm liên quan trước, rồi yêu cầu tìm trên web. Trạng thái quy trình không thay đổi.",
                    "ko": "먼저 관련 실험 질문을 말씀한 뒤 웹 추가 검색을 요청해 주세요. 프로토콜 상태는 변경하지 않았습니다.",
                }.get(language, "먼저 관련 실험 질문을 말씀해 주세요.")
                if missing_followup_query
                else _unsupported_fact_reply(
                    language,
                    development_only=self.fixture.development_only,
                    question_kind=intent.question_kind,
                )
            )
            plan = CuratedProtocolTurnPlan(
                action=(
                    CuratedProtocolAction.CLARIFY_REFERENCE
                    if missing_followup_query
                    else CuratedProtocolAction.RELATED_QUESTION
                    if command is CuratedProtocolAction.RELATED_QUESTION
                    else CuratedProtocolAction.UNSUPPORTED
                ),
                display_text=response,speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(
                    self.related_facts(transcript)
                    if command is CuratedProtocolAction.RELATED_QUESTION
                    else ()
                ),step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,intent_kind=intent.intent_kind,
                target_step=intent.target_step,answer_origin="unsupported",
                requested_followup=intent.requested_followup,
                requested_entity=intent.requested_entity,
                requested_entities=intent.requested_entities,
                question_kind=intent.question_kind,
                normalized_transcript=intent.normalized_transcript,
                transcript_correction_note=intent.transcript_correction_note,
                transcript_corrections=intent.transcript_corrections,
                question_dimensions=intent.question_dimensions,
                coreference_status=intent.coreference_status,
                coreference_reason=intent.coreference_reason,
            )
            if (
                command is CuratedProtocolAction.RELATED_QUESTION
                and plan.facts
                and not missing_followup_query
            ):
                envelope=self.protocol_answer_envelope(
                    replace(plan, claim_requests=intent.claim_requests),
                    language=language,
                )
                plan=replace(
                    plan,
                    display_text=(
                        f"직접 답변\n{envelope.direct_answer}\n\n"
                        "근거 경계\n활성 프로토콜의 확인된 내용입니다."
                        if language=="ko" else envelope.direct_answer
                    ),
                    speech_text=envelope.speech_summary,
                    speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                    primary_text=envelope.direct_answer,
                    source_texts=tuple(fact.text for fact in plan.facts[:8]),
                    source_pages=tuple(fact.source_page for fact in plan.facts[:8]),
                    evidence_ids=tuple(fact.fact_id for fact in plan.facts[:8]),
                    translation_status="deterministic_protocol_structure",
                    answer_origin="current_protocol",
                    source_plan_scopes=envelope.source_plan.scopes,
                    unresolved_dimensions=(
                        envelope.source_plan.unresolved_dimensions),
                    unresolved_claim_ids=(
                        envelope.source_plan.unresolved_claim_ids),
                )
        plan = replace(
            plan,
            claim_requests=intent.claim_requests,
            plausibility_status=intent.plausibility_status,
            plausibility_reason=intent.plausibility_reason,
        )
        if opening_projection != (
            self.active,
            self.current_index,
            self._block_reason,
        ):
            self._revision += 1
        if (
            plan.action is CuratedProtocolAction.CLARIFY_COMPLETION
            and plan.intent_kind == "next_step_confirmation_required"
        ):
            step = self.fixture.steps[self.current_index]
            self._pending_completion_confirmation = PendingCompletionConfirmation(
                configuration_id=configuration_id,
                step_id=step.step_id,
                step_index=self.current_index,
                workflow_revision=self._revision,
                requested_turn_id=turn_id,
                requested_generation=generation,
            )
        elif (
            plan.action is CuratedProtocolAction.CLARIFY_COMPLETION
            and plan.intent_kind == "observation_confirmation_required"
        ):
            step = self.fixture.steps[self.current_index]
            self._pending_observation_confirmation = PendingObservationConfirmation(
                configuration_id=configuration_id,
                step_id=step.step_id,
                step_index=self.current_index,
                step_label=step.source_label,
                workflow_revision=self._revision,
                requested_turn_id=turn_id,
                requested_generation=generation,
                predicate_id=(
                    self.current_step_semantic_frame().observation_predicate_id
                    or f"candidate_a_step_{step.source_label}_endpoint"
                ),
            )
        elif plan.action in {
            CuratedProtocolAction.START,
            CuratedProtocolAction.NEXT,
            CuratedProtocolAction.STOP,
        }:
            self._pending_completion_confirmation = None
            if plan.state_changed or plan.action is CuratedProtocolAction.STOP:
                self._pending_observation_confirmation = None
        self._replay[turn_id] = plan
        for fact in plan.facts:
            lowered = fact.text.casefold()
            for entity in (
                "solution a", "solution b", "ambic", "acetonitrile",
                "gel plug", "stained protein band",
            ):
                if entity in lowered and entity not in self._recent_verified_entities:
                    self._recent_verified_entities.append(entity)
        self._recent_verified_entities = self._recent_verified_entities[-8:]
        if (
            command is CuratedProtocolAction.RELATED_QUESTION
            and intent.requested_followup not in {
                "search_external_reference", "continue_related_question"
            }
        ):
            self._last_related_query = transcript
            self._last_related_entities = plan.requested_entities
            self._discourse_context = ProtocolDiscourseContext(
                focused_entities=plan.requested_entities,
                comparison_entities=(
                    plan.requested_entities
                    if len(plan.requested_entities) > 1 else ()
                ),
                requested_dimension=(
                    plan.question_dimensions[-1]
                    if plan.question_dimensions else None
                ),
                semantic_topic=plan.question_kind,
                step_id=self.fixture.steps[self.current_index].step_id,
                response_language=language,
                turn_id=turn_id,
                generation=generation,
                workflow_revision=self._revision,
                focus_kind=(
                    DiscourseFocusKind.PROTOCOL_BENEFIT
                    if plan.question_kind == "protocol_benefit"
                    else DiscourseFocusKind.CURRENT_STEP_PARAMETER
                    if any(claim.target_type is ClaimTargetType.PARAMETER
                           for claim in plan.claim_requests)
                    else DiscourseFocusKind.CURRENT_STEP_ACTION
                    if any(claim.target_type is ClaimTargetType.ACTION
                           for claim in plan.claim_requests)
                    else DiscourseFocusKind.COMPARISON
                    if len(plan.requested_entities) > 1
                    else DiscourseFocusKind.ENTITY
                ),
                proposition_ids=tuple(
                    claim.claim_id for claim in plan.claim_requests
                    if claim.admission_status is ClaimAdmissionStatus.LOCAL_SUPPORTED
                ),
                source_evidence_ids=tuple(dict.fromkeys(
                    evidence_id for claim in plan.claim_requests
                    for evidence_id in claim.evidence_ids
                )),
            )
        elif (
            command is CuratedProtocolAction.PROTOCOL_QUERY
            and intent.protocol_scope == "purpose"
        ):
            self._discourse_context = ProtocolDiscourseContext(
                requested_dimension="purpose",
                semantic_topic="protocol_purpose",
                step_id=self.fixture.steps[self.current_index].step_id,
                response_language=language,
                turn_id=turn_id,
                generation=generation,
                workflow_revision=self._revision,
                focus_kind=DiscourseFocusKind.PROTOCOL_PURPOSE,
                proposition_ids=tuple(
                    claim.claim_id for claim in plan.claim_requests
                ) or ("protocol_purpose",),
                source_evidence_ids=("protocol_purpose",),
            )
        elif command is CuratedProtocolAction.VISUAL_REQUEST and plan.requested_entities:
            self._discourse_context = ProtocolDiscourseContext(
                focused_entities=plan.requested_entities,
                comparison_entities=(
                    plan.requested_entities
                    if len(plan.requested_entities) > 1 else ()
                ),
                requested_dimension="visual",
                semantic_topic="visual",
                step_id=self.fixture.steps[self.current_index].step_id,
                response_language=language,
                turn_id=turn_id,
                generation=generation,
                workflow_revision=self._revision,
                focus_kind=DiscourseFocusKind.ENTITY,
                proposition_ids=tuple(
                    claim.claim_id for claim in plan.claim_requests
                ),
                source_evidence_ids=tuple(dict.fromkeys(
                    evidence_id for claim in plan.claim_requests
                    for evidence_id in claim.evidence_ids
                )),
            )
        elif plan.state_changed or command in {
            CuratedProtocolAction.START,
            CuratedProtocolAction.STOP,
            CuratedProtocolAction.OFF_TOPIC,
        }:
            self._last_related_query = None
            self._last_related_entities = ()
            self._discourse_context = ProtocolDiscourseContext()
        if len(self._replay) > 64:
            self._replay.pop(next(iter(self._replay)))
        return plan

    def apply_grounded_answer(
        self,
        *,
        turn_id: int,
        language: str,
        primary_text: str,
        evidence_ids: tuple[str, ...],
        inference_labels: tuple[str, ...],
        unsupported_parts: tuple[str, ...],
    ) -> CuratedProtocolTurnPlan:
        """Replace one fail-closed read-only plan with a validated QA answer."""

        opening = self._replay.get(turn_id)
        if opening is None or opening.action not in {
            CuratedProtocolAction.UNSUPPORTED,
            CuratedProtocolAction.RELATED_QUESTION,
        }:
            raise CuratedProtocolFixtureError("Grounded answer does not own this turn.")
        step = self.fixture.steps[self.current_index]
        fact_map = {fact.fact_id: fact for fact in opening.facts}
        if not evidence_ids or any(fact_id not in fact_map for fact_id in evidence_ids):
            raise CuratedProtocolFixtureError("Grounded answer evidence is invalid.")
        facts = tuple(fact_map[fact_id] for fact_id in evidence_ids)
        sources = tuple(fact.text for fact in facts)
        pages = tuple(fact.source_page for fact in facts)
        if unsupported_parts:
            suffix = (
                "\n\n지원되지 않은 부분\n" + "\n".join(unsupported_parts)
                if language == "ko"
                else "\n\nUnsupported portion\n" + "\n".join(unsupported_parts)
            )
        else:
            suffix = ""
        display = _display_contract(
            language,
            primary_text + suffix,
            sources,
            pages,
            evidence_ids,
            translated=language != "ko" or bool(primary_text.strip()),
        )
        plan = CuratedProtocolTurnPlan(
            action=CuratedProtocolAction.QUESTION,
            display_text=display,
            speech_text=primary_text,
            speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
            facts=facts,
            step_label=step.source_label,
            final_step=self.current_index == len(self.fixture.steps) - 1,
            state_changed=False,
            fact_id=evidence_ids[0],
            primary_text=primary_text,
            source_texts=sources,
            source_pages=pages,
            evidence_ids=evidence_ids,
            translation_status="grounded_model",
            intent_kind=opening.intent_kind,
            requested_entity=opening.requested_entity,
            requested_entities=opening.requested_entities,
            question_kind=opening.question_kind,
            normalized_transcript=opening.normalized_transcript,
            transcript_correction_note=opening.transcript_correction_note,
            transcript_corrections=opening.transcript_corrections,
            question_dimensions=opening.question_dimensions,
        )
        self._replay[turn_id] = plan
        return plan

    def apply_reference_answer(
        self,
        *,
        turn_id: int,
        language: str,
        primary_text: str,
        origin: str,
        citations: tuple[dict[str, object], ...],
        retrieval_backend: str,
        retrieval_scores: tuple[float, ...] = (),
        limitations: tuple[str, ...] = (),
    ) -> CuratedProtocolTurnPlan:
        """Attach a validated read-only reference answer to one related turn."""

        opening = self._replay.get(turn_id)
        if (
            opening is None
            or opening.action not in {
                CuratedProtocolAction.RELATED_QUESTION,
                CuratedProtocolAction.VISUAL_REQUEST,
            }
            or origin not in {
                "approved_lab_corpus", "external_authoritative_reference"
            }
            or not isinstance(primary_text, str) or not primary_text.strip()
            or not citations
        ):
            raise CuratedProtocolFixtureError("Reference answer does not own this turn.")
        step = self.fixture.steps[self.current_index]
        if origin == "approved_lab_corpus":
            required = {
                "chunk_id", "document_id", "document_sha256", "document_title",
                "document_version", "page_number", "section", "source_language",
                "approval_status", "original_excerpt",
            }
            if any(
                not isinstance(item, dict) or not required.issubset(item)
                or item["approval_status"] != "approved"
                or not isinstance(item["page_number"], int)
                or item["page_number"] <= 0
                for item in citations
            ):
                raise CuratedProtocolFixtureError("Approved reference citation is invalid.")
            source_texts = tuple(str(item["original_excerpt"]) for item in citations)
            source_pages = tuple(int(item["page_number"]) for item in citations)
            evidence_ids = tuple(str(item["chunk_id"]) for item in citations)
            sources = "\n".join(
                f"{item['document_title']} · v{item['document_version']} · "
                f"{item['section']} · p.{item['page_number']} · {item['chunk_id']}"
                for item in citations
            )
            label = (
                "Additional approved reference · not part of the active protocol"
                if language == "en"
                else "추가 승인 참고자료 · 활성 프로토콜의 일부가 아님"
            )
            original_label = "Original" if language == "en" else "원문 · English"
            joined_sources = "\n\n".join(source_texts)
            display = (
                f"Direct answer\n{primary_text}\n\n{label}\n\n{original_label}\n"
                f"{joined_sources}\n\nSources\n{sources}"
            )
            speech_suffix = (
                " This is additional approved reference guidance."
                if language == "en" else " 이 내용은 추가 승인 참고자료 안내입니다."
            )
        else:
            required = {
                "title", "canonical_url", "domain", "retrieved_at",
                "source_kind", "relevant_excerpt",
            }
            if any(
                not isinstance(item, dict) or not required.issubset(item)
                or item["source_kind"] != "external_authoritative_reference"
                for item in citations
            ):
                raise CuratedProtocolFixtureError("External reference citation is invalid.")
            source_texts = tuple(
                str(item["relevant_excerpt"]) for item in citations
                if str(item["relevant_excerpt"]).strip()
            )
            source_pages = ()
            evidence_ids = tuple(str(item["canonical_url"]) for item in citations)
            sources = "\n".join(
                f"{item['title']} · {item['canonical_url']} · {item['retrieved_at']}"
                for item in citations
            )
            label = (
                "External reference · not part of the active protocol"
                if language == "en"
                else "외부 참고자료 · 활성 프로토콜의 일부가 아님"
            )
            display = f"Direct answer\n{primary_text}\n\n{label}\n\nSources\n{sources}"
            speech_suffix = (
                " This is external reference guidance, not the active protocol."
                if language == "en" else
                " 이 내용은 활성 프로토콜이 아닌 외부 참고자료 안내입니다."
            )
        plan = CuratedProtocolTurnPlan(
            action=CuratedProtocolAction.QUESTION,
            display_text=display,
            speech_text=primary_text + speech_suffix,
            speech_mode=CuratedProtocolSpeechMode.REFERENCE,
            facts=(),
            step_label=step.source_label,
            final_step=self.current_index == len(self.fixture.steps) - 1,
            state_changed=False,
            fact_id=evidence_ids[0],
            primary_text=primary_text,
            source_texts=source_texts,
            source_pages=source_pages,
            evidence_ids=evidence_ids,
            translation_status="grounded_reference",
            intent_kind=opening.intent_kind,
            answer_origin=origin,
            citations=citations,
            retrieval_backend=retrieval_backend,
            retrieval_scores=retrieval_scores,
            limitations=limitations,
            requested_entity=opening.requested_entity,
            requested_entities=opening.requested_entities,
            question_kind=opening.question_kind,
            normalized_transcript=opening.normalized_transcript,
            transcript_correction_note=opening.transcript_correction_note,
            transcript_corrections=opening.transcript_corrections,
            question_dimensions=opening.question_dimensions,
        )
        self._replay[turn_id] = plan
        return plan

    def apply_supplemental_answer(
        self,
        *,
        turn_id: int,
        language: str,
        primary_text: str,
        retrieval_backend: str,
    ) -> CuratedProtocolTurnPlan:
        """Attach model-only background without promoting it to evidence."""

        opening = self._replay.get(turn_id)
        if (
            opening is None
            or opening.action not in {
                CuratedProtocolAction.RELATED_QUESTION,
                CuratedProtocolAction.VISUAL_REQUEST,
            }
            or not isinstance(primary_text, str)
            or not primary_text.strip()
        ):
            raise CuratedProtocolFixtureError(
                "Supplemental answer does not own this turn."
            )
        step = self.fixture.steps[self.current_index]
        label = (
            "General model explanation · no admitted authoritative source"
            if language == "en" else
            "일반 모델 설명 · 확인된 권위 근거 없음"
        )
        qualification = (
            "This is general model knowledge without an admitted authoritative source."
            if language == "en" else
            "확인된 권위 근거가 없는 일반 모델 설명입니다."
        )
        plan = CuratedProtocolTurnPlan(
            action=CuratedProtocolAction.QUESTION,
            display_text=f"{label}\n{primary_text.strip()}",
            speech_text=f"{qualification} {primary_text.strip()}",
            speech_mode=CuratedProtocolSpeechMode.REFERENCE,
            facts=(),
            step_label=step.source_label,
            final_step=self.current_index == len(self.fixture.steps) - 1,
            state_changed=False,
            primary_text=primary_text.strip(),
            translation_status="supplemental_model_knowledge",
            intent_kind=opening.intent_kind,
            answer_origin="supplemental_model_knowledge",
            retrieval_backend=retrieval_backend,
            limitations=(
                "No admitted authoritative citation supports this general explanation.",
                "This explanation cannot modify or authorize the active protocol.",
            ),
            requested_entity=opening.requested_entity,
            requested_entities=opening.requested_entities,
            question_kind=opening.question_kind,
            normalized_transcript=opening.normalized_transcript,
            transcript_correction_note=opening.transcript_correction_note,
            transcript_corrections=opening.transcript_corrections,
            question_dimensions=opening.question_dimensions,
            source_plan_scopes=("SUPPLEMENTAL_MODEL_KNOWLEDGE",),
        )
        self._replay[turn_id] = plan
        return plan
