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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

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
    NEXT = "next"
    QUESTION = "question"
    RELATED_QUESTION = "related_question"
    VISUAL_REQUEST = "visual_request"
    CLARIFY_COMPLETION = "clarify_completion"
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


class ProtocolVisualKind(str, Enum):
    SOURCE_CROP = "source_crop"
    GENERATED_SCHEMATIC = "generated_schematic"
    TEXT_EXCERPT = "text_excerpt"


@dataclass(frozen=True)
class CuratedProtocolFact:
    fact_id: str
    kind: str
    text: str
    source_page: int


@dataclass(frozen=True)
class ProtocolVisualAsset:
    """One verified source crop or locally rendered fact-only schematic."""

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
        """Return a verified crop or a fact-only local schematic."""

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
        content = _diagram_svg(step.source_label, step.instruction_source_text)
        primary = self.localized_fact(step.step_id, "current_step")
        if primary is None:
            primary = f"{step.source_label}단계의 검증된 동작 흐름"
        return ProtocolVisualAsset(
            asset_id=f"diagram-step-{step.source_label}",
            protocol_id=self.protocol_id,
            revision_id=self.revision_id,
            kind=ProtocolVisualKind.GENERATED_SCHEMATIC.value,
            source_document_id=checksum,
            source_page=page,
            mime_type="image/svg+xml",
            sha256=hashlib.sha256(content).hexdigest(),
            alt_text=f"Step {step.source_label} verified-action schematic",
            label="설명용 도식 · 원본 이미지 아님",
            caption_primary=primary,
            caption_source=step.instruction_source_text,
            source_page_url=page_url,
        )

    def visual_content(self, index: int) -> tuple[ProtocolVisualAsset, bytes]:
        asset = self.visual_for_step(index)
        if asset is None:
            raise CuratedProtocolFixtureError("Protocol visual is unavailable.")
        if asset.kind == ProtocolVisualKind.SOURCE_CROP.value:
            candidate = (self.visual_manifest or {})[self.steps[index].step_id]
            content, _ = _verified_source_crop(
                self.source_pdf_path,
                asset.source_page,
                candidate["object_name"],
                candidate["source_region_hash"],
            )
        else:
            content = _diagram_svg(
                self.steps[index].source_label,
                self.steps[index].instruction_source_text,
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
    intent_confidence: float = 1.0
    visual_requested: bool = False
    answer_origin: str = "current_protocol"
    citations: tuple[dict[str, object], ...] = ()
    retrieval_backend: str | None = None
    retrieval_scores: tuple[float, ...] = ()
    limitations: tuple[str, ...] = ()

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


def _diagram_svg(step_label: str, source_text: str) -> bytes:
    """Render an allowlisted action-box diagram from one exact verified fact."""

    safe_label = xml_escape(step_label)
    safe_text = xml_escape(" ".join(source_text.split()))
    lines = [safe_text[index : index + 78] for index in range(0, len(safe_text), 78)]
    height = max(220, 150 + 24 * len(lines))
    body = "".join(
        f'<text x="60" y="{125 + index * 24}">{line}</text>'
        for index, line in enumerate(lines)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" '
        f'viewBox="0 0 900 {height}" role="img" aria-label="Step {safe_label} schematic">'
        '<rect width="100%" height="100%" fill="#f7fbf8"/>'
        '<rect x="36" y="36" width="828" height="150" rx="18" fill="#ffffff" '
        'stroke="#3e7057" stroke-width="3"/>'
        f'<text x="60" y="78" font-family="sans-serif" font-size="22" '
        f'font-weight="700">Step {safe_label} · verified action</text>'
        f'<g font-family="sans-serif" font-size="17" fill="#17211b">{body}</g>'
        '<path d="M450 190v24" stroke="#3e7057" stroke-width="4"/>'
        '<path d="M440 207l10 12 10-12" fill="none" stroke="#3e7057" '
        'stroke-width="4"/></svg>'
    ).encode("utf-8")


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
    key = re.sub(r"[,.!?。！？;:]", " ", key)
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
    confidence: float = 1.0
    language: str = "ko"


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
        r"(?:explain|describe).*(?:step\s*(?P<label>[0-9]{1,2})|current\s*step)"
        r".*(?:detail|more)?"
    ),
)
_AMBIGUOUS_COMPLETION_PATTERNS = (
    re.compile(r"(?:완료|끝난|다\s*한).*(?:것\s*같|맞나|할까|해도\s*될까|인가)"),
    re.compile(r"(?:maybe|i\s+think|not\s+sure).*(?:done|complete|next)"),
)
_VISUAL_REQUEST_PATTERNS = (
    re.compile(r"(?:이|현재)?\s*단계.*(?:그림|삽화|일러스트).*(?:설명|보여|그려)"),
    re.compile(r"(?:그림|삽화|일러스트).*(?:이|현재)?\s*단계"),
    re.compile(r"(?:illustrate|show\s+an?\s+illustration|draw).*(?:this|current)\s+step"),
)
_PROTOCOL_RELATED_TERMS = frozenset({
    "단계", "프로토콜", "절차", "실험", "용액", "시약", "재료", "장비",
    "주의", "주의사항", "경고", "온도", "시간", "겔", "밴드", "세척",
    "탈색", "탈수", "ambic", "acetonitrile", "solution", "reagent",
    "protocol", "procedure", "step", "gel", "destain", "dehydrat",
    "precaution", "warning", "equipment", "material", "temperature",
})


def classify_curated_control_intent(
    transcript: str,
    *,
    language: str,
) -> CuratedControlIntent:
    """Classify reviewed workflow shapes before any knowledge or model route."""

    key = _semantic_utterance_key(transcript)
    exact = _WORKFLOW_COMMANDS.get(key)
    if exact is not None:
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
                language=language,
            )
    if any(pattern.search(key) for pattern in _AMBIGUOUS_COMPLETION_PATTERNS):
        return CuratedControlIntent(
            intent_kind="ambiguous_completion",
            action=CuratedProtocolAction.CLARIFY_COMPLETION,
            confidence=0.5,
            target_step="authoritative_current_step",
            language=language,
        )
    if any(pattern.search(key) for pattern in _VISUAL_REQUEST_PATTERNS):
        return CuratedControlIntent(
            intent_kind="visual_request",
            action=CuratedProtocolAction.VISUAL_REQUEST,
            target_step="authoritative_current_step",
            language=language,
        )
    if any(term in key for term in _PROTOCOL_RELATED_TERMS):
        return CuratedControlIntent(
            intent_kind="related_question",
            action=CuratedProtocolAction.RELATED_QUESTION,
            target_step="authoritative_current_step",
            language=language,
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
    language: str, *, development_only: bool = True
) -> str:
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
            "현재 단계와 사용 가능한 참고자료에서 답변할 근거를 "
            "충분히 찾지 못했습니다. 필요한 내용을 조금 더 구체적으로 "
            "말씀해 주세요."
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

    def _contextual_solution_a_fact(
        self,
        transcript: str,
    ) -> tuple[int, CuratedProtocolFact] | None:
        """Resolve Step 3 Solution A references from verified adjacent facts only."""

        if self.fixture.steps[self.current_index].source_label != "3":
            return None
        key = _semantic_utterance_key(transcript)
        if not any(
            term in key
            for term in ("어떻게", "준비", "만들", "조성", "구성", "비율", "prepare", "make")
        ):
            return None
        if not any(
            re.search(pattern, key)
            for pattern in (
                r"(?:solution\s*a|a\s*용액|용액\s*a|에이\s*용액|용액\s*에이)",
                r"(?:그|해당)\s*용액",
                r"(?<![a-z0-9])ambic(?![a-z0-9])",
            )
        ):
            return None
        source_index = self.current_index - 1
        if source_index < 0:
            return None
        candidates = tuple(
            fact
            for fact in self.fixture.facts_for_step(source_index)
            if fact.fact_id == "current_step"
            and "solution a" in fact.text.casefold()
            and "ambic" in fact.text.casefold()
            and "acetonitrile" in fact.text.casefold()
        )
        return (source_index, candidates[0]) if len(candidates) == 1 else None

    def activate_configured(self) -> None:
        """Make one successfully configured structured protocol usable."""

        opening = (self.active, self.current_index, self._block_reason)
        self.active = True
        self.current_index = 0
        self._block_reason = None
        self._replay.clear()
        if opening != (self.active, self.current_index, self._block_reason):
            self._revision += 1

    def reset(self) -> None:
        opening = (self.active, self.current_index, self._block_reason)
        self.active = False
        self.current_index = 0
        self._block_reason = None
        self._replay.clear()
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
    ]:
        return (
            self.active,
            self.current_index,
            self._revision,
            self._block_reason,
            dict(self._replay),
        )

    def _restore(
        self,
        checkpoint: tuple[
            bool,
            int,
            int,
            str | None,
            dict[int, CuratedProtocolTurnPlan],
        ],
    ) -> None:
        (
            self.active,
            self.current_index,
            self._revision,
            self._block_reason,
            replay,
        ) = checkpoint
        self._replay = dict(replay)

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
    ) -> domain.ReadinessReasonCode | None:
        step_id = self.fixture.steps[self.current_index].step_id
        blocking_codes = {
            domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY,
            domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL,
        }
        return next(
            (
                reason.code
                for reason in self.fixture.draft.readiness.reasons
                if reason.step_id == step_id and reason.code in blocking_codes
            ),
            None,
        )

    def plan(
        self,
        transcript: str,
        *,
        turn_id: int,
        language: str,
    ) -> CuratedProtocolTurnPlan:
        if turn_id in self._replay:
            return self._replay[turn_id]
        command_key = _utterance_key(transcript)
        intent = classify_curated_control_intent(
            transcript,
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
            )
        elif command is CuratedProtocolAction.NEXT:
            blocker = self._current_step_readiness_blocker()
            if blocker is not None:
                self._block_reason = blocker.value
                step = steps[self.current_index]
                response = {
                    "en": "The current step cannot advance because its execution control is unresolved or unsupported. It has not been marked complete.",
                    "vi": "Bước hiện tại không thể tiếp tục vì điều khiển thực hiện chưa được giải quyết hoặc chưa được hỗ trợ. Bước chưa được đánh dấu hoàn thành.",
                    "ko": "현재 단계의 실행 제어가 해결되지 않았거나 지원되지 않아 진행할 수 없습니다. 이 단계는 완료 처리되지 않았습니다.",
                }.get(language, "현재 단계는 실행 제어가 해결될 때까지 진행할 수 없습니다.")
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
            localized = self._localized_fact(step.step_id, "current_step")
            primary = localized if language == "ko" and localized else step.instruction_source_text
            response = _display_contract(
                language,
                primary,
                (step.instruction_source_text,),
                (step.evidence.source_page_number,),
                ("current_step",),
                translated=language != "ko" or localized is not None,
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.FULL_DETAIL,
                display_text=response,
                speech_text=(
                    primary
                    if intent.intent_kind == "step_elaboration"
                    else step.instruction_source_text
                ),
                speech_mode=CuratedProtocolSpeechMode.FULL_DETAIL,
                facts=self.fixture.facts_for_step(target_index),
                step_label=step.source_label,
                final_step=target_index == len(steps) - 1,
                state_changed=False,
                primary_text=primary,
                source_texts=(step.instruction_source_text,),
                source_pages=(step.evidence.source_page_number,),
                evidence_ids=("current_step",),
                translation_status=(
                    "verified_sidecar" if language == "ko" and localized else
                    "unavailable" if language == "ko" else "source_language"
                ),
                intent_kind=intent.intent_kind,
                target_step=(
                    step.source_label
                    if intent.intent_kind == "step_elaboration"
                    else intent.target_step
                ),
            )
        elif command is CuratedProtocolAction.VISUAL_REQUEST:
            step = steps[self.current_index]
            control_text = {
                "en": f"Step {step.source_label} is shown with its available instructional visual.",
                "vi": f"Bước {step.source_label} được hiển thị cùng hình minh họa hướng dẫn hiện có.",
                "ko": f"현재 {step.source_label}단계를 설명하는 시각 자료를 준비합니다.",
            }.get(language, f"현재 {step.source_label}단계의 시각 자료를 준비합니다.")
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
                target_step=intent.target_step,
                visual_requested=True,
            )
        elif command is CuratedProtocolAction.CLARIFY_COMPLETION:
            step = steps[self.current_index]
            response = {
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
            }.get(language, "현재 단계를 완료하고 다음으로 이동할지 확인해 주세요.")
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
            )
        elif contextual := self._contextual_solution_a_fact(transcript):
            source_index, selected_fact = contextual
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
                    "자료에 대한 질문을 도와드릴 수 있어요."
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
            response = _unsupported_fact_reply(
                language,development_only=self.fixture.development_only)
            plan = CuratedProtocolTurnPlan(
                action=(
                    CuratedProtocolAction.RELATED_QUESTION
                    if command is CuratedProtocolAction.RELATED_QUESTION
                    else CuratedProtocolAction.UNSUPPORTED
                ),
                display_text=response,speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(),step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,intent_kind=intent.intent_kind,
                target_step=intent.target_step,answer_origin="unsupported",
            )
        if opening_projection != (
            self.active,
            self.current_index,
            self._block_reason,
        ):
            self._revision += 1
        self._replay[turn_id] = plan
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
        fact_map = {
            fact.fact_id: fact for fact in self.fixture.facts_for_step(self.current_index)
        }
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
            or opening.action is not CuratedProtocolAction.RELATED_QUESTION
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
                f"{label}\n{primary_text}\n\n{original_label}\n"
                f"{joined_sources}\n\nSources\n{sources}"
            )
            speech_prefix = (
                "This is additional approved reference guidance. "
                if language == "en" else "추가 승인 참고자료 안내입니다. "
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
            display = f"{label}\n{primary_text}\n\nSources\n{sources}"
            speech_prefix = (
                "This is external reference guidance, not the active protocol. "
                if language == "en" else
                "활성 프로토콜이 아닌 외부 참고자료 안내입니다. "
            )
        plan = CuratedProtocolTurnPlan(
            action=CuratedProtocolAction.QUESTION,
            display_text=display,
            speech_text=speech_prefix + primary_text,
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
        )
        self._replay[turn_id] = plan
        return plan
