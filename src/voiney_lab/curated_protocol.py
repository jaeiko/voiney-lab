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
import time
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Literal
from urllib.parse import quote

from pypdf import PdfReader

from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_analysis import (
    ANALYSIS_RESPONSE_SCHEMA,
    ProtocolAnalysisDraft,
    parse_protocol_analysis_response,
)
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.completion_intent import (
    CompletionIntentDecision,
    classify_korean_completion_command,
    resolve_korean_completion_decision,
)
from voiney_lab.intent_arbitration import (
    RequestArbitration,
    RequestIntent,
    arbitrate_request,
)
from voiney_lab.answer_checks import ServerValues
from voiney_lab.llm_router import (
    RECORD_LOG,
    ProposalBasis,
    ProposalVerdict,
    RouterAnswer,
    RouterContext,
    RouterTurnFacts,
    ToolProposal,
    validate_tool_proposals,
)
from voiney_lab.semantic_intent import (
    SemanticIntent,
    SemanticIntentContext,
    SemanticIntentDecision,
    SemanticIntentProposal,
    SemanticIntentSettings,
    evaluate_semantic_proposal,
    normalize_semantic_utterance,
    semantic_fallback_reason,
)


DEVELOPMENT_FIXTURE_STATUS = "development_only_not_final_acceptance"
DEVELOPMENT_FIXTURE_MODE = "offline_curated_development_fixture"
#: The one protocol whose curated prose answers were human-reviewed against the
#: source. Curated text keyed by a reagent name is only true of the document it
#: was reviewed against, so every block of it is gated on this identity.
CANDIDATE_A_PROTOCOL_ID = "candidate-a-curated-development-v1"
_CANONICAL_SCHEMA_SHA256 = (
    "33ca2886cdc6cbad272363ebfaafd3f69853304610c7e47dfce3d485d18ee528"
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
    RECORD_OBSERVATION = "record_observation"
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
    AGENT_META = "agent_meta"
    PAUSE = "pause"
    RESUME = "resume"
    START_TIMER = "start_timer"
    TIMER_STATUS = "timer_status"
    PREVIEW_STEP = "preview_step"
    STEP_RANGE = "step_range"
    LAB_DOMAIN_QA = "lab_domain_qa"
    REPORT_HANDOFF = "report_handoff"


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


#: The fact key a translation of the protocol's purpose is stored under.
PURPOSE_FACT_KEY = "protocol/protocol_purpose"


@dataclass(frozen=True)
class WorkflowExecutionFingerprint:
    protocol_id: str
    configuration_id: int | None
    run_id: str | None
    active: bool
    workflow_status: str
    current_step_id: str | None
    current_index: int
    experiment_started_at: float | None


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
    #: source page number -> the evidence segment ids on that page that the
    #: analysis neither cited nor declined.
    #:
    #: STEP 28 stopped throwing a document away because a page was not fully
    #: read, which is right -- discarding twenty-five correct instructions to
    #: punish eighteen unaccounted segments protects nobody. It also means a
    #: Protocol can now reach an experimenter with a page the machine did not
    #: finish reading, and that is only safe if the experimenter is told so at
    #: the moment it matters, shown the page's own words, and never told the
    #: page is complete on the system's authority. Empty means every page was
    #: fully accounted for, which is the ordinary case.
    unread_pages: dict[int, tuple[str, ...]] | None = None
    #: step_id -> duration in seconds, loaded only from a manifest whose every
    #: entry the loader verified against the source. Empty when no manifest is
    #: present, which is why one document's timings cannot reach another's.
    timer_manifest: dict[str, int] | None = None
    #: "<step_id>/<fact_id>" -> a stored machine translation that passed the
    #: mechanical check for this revision. Used only where ``localizations``
    #: (reviewed) has nothing; never presented as reviewed.
    machine_localizations: dict[str, str] | None = None

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
        """Reviewed Korean first, then a stored machine translation, else None."""

        key = f"{step_id}/{fact_id}"
        if self.localizations is not None and key in self.localizations:
            return self.localizations[key]
        if self.machine_localizations is not None:
            return self.machine_localizations.get(key)
        return None

    def localization_source(self, step_id: str, fact_id: str) -> str | None:
        """``reviewed``, ``machine`` or None for what ``localized_fact`` returns."""

        key = f"{step_id}/{fact_id}"
        if self.localizations is not None and key in self.localizations:
            return "reviewed"
        if self.machine_localizations is not None and key in self.machine_localizations:
            return "machine"
        return None

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
    visual_intent: str | None = None
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
    timer_payload: dict[str, Any] | None = None
    display_document: dict[str, Any] | None = None
    speech_policy: Literal["speak", "silent"] = "speak"

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
    r"(?:\d{2}:\d{2}:\d{2}|"
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:mg/mL|ng/uL|mm3|mm³|µL|uL|mL|ml|mM|°C|rpm|min|v/v|C|h|%)(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9]))",
    re.IGNORECASE,
)


def _presentation_tokens(value: str) -> frozenset[str]:
    return frozenset(
        token.casefold().replace(" ", "").replace("ul", "µl").replace("μ", "µ")
        .replace("mm3", "mm³")
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


_TIMER_CLOCK = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")
_TIMER_WORDED = re.compile(r"(\d{1,3})\s*(min|h)\b", re.I)


def _timer_literal_seconds(literal: str) -> int | None:
    """The seconds a source literal states, in the two forms the source uses.

    This reads a value the document prints, which is the one thing the server
    may do with source text. It never infers a duration that is not written,
    and anything outside those two forms returns None so the manifest entry is
    refused rather than guessed at.
    """

    clock = _TIMER_CLOCK.fullmatch(literal.strip())
    if clock is not None:
        hours, minutes, seconds = (int(part) for part in clock.groups())
        return hours * 3600 + minutes * 60 + seconds
    worded = _TIMER_WORDED.fullmatch(literal.strip())
    if worded is not None:
        count, unit = int(worded.group(1)), worded.group(2).lower()
        return count * 60 if unit == "min" else count * 3600
    return None


def _load_timer_manifest(
    path: Path,
    *,
    fixture_sha256: str,
    extraction: ProtocolPdfExtraction,
    steps: tuple[domain.ProtocolSourceStep, ...],
) -> dict[str, int]:
    """Step timers, loaded only where the source states the duration.

    This replaces a dict hardcoded in this module and keyed by step *index*.
    That table was bound to no document, no page and no segment, so it applied
    to whatever protocol sat at those positions -- and the previous step's work
    changed how many steps a protocol has, which widened the hazard rather
    than narrowing it.

    Every entry is verified against the source here, and one bad entry refuses
    the whole manifest: a timer nobody can point to in the document does not
    exist as far as execution is concerned. An absent manifest means no timers
    at all, which is how one document's timings cannot reach another's.
    """

    if not path.exists():
        return {}
    payload, raw = _load_json_object(path)
    required = {
        "version", "document_sha256", "fixture_sha256", "status", "candidates",
    }
    if _canonical_json_bytes(payload) != raw or set(payload) != required:
        raise CuratedProtocolFixtureError("Timer manifest has an invalid shape.")
    if (
        payload["version"] != 1
        or payload["document_sha256"] != extraction.sha256
        or payload["fixture_sha256"] != fixture_sha256
        or payload["status"] != DEVELOPMENT_FIXTURE_STATUS
        or not isinstance(payload["candidates"], list)
    ):
        raise CuratedProtocolFixtureError("Timer manifest identity is invalid.")

    from voiney_lab.protocol_claim_analysis import (
        generate_page_evidence_segments,
    )

    by_step = {step.step_id: step for step in steps}
    order = [step.step_id for step in steps]
    expected_fields = {
        "linked_step_id", "page_number", "step_anchor_page",
        "duration_seconds", "source_literal", "evidence_segment_ids",
        "confidence",
    }
    resolved: dict[str, int] = {}
    for item in payload["candidates"]:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise CuratedProtocolFixtureError(
                "Timer candidate has an invalid shape."
            )
        step = by_step.get(item["linked_step_id"])
        if step is None or item["confidence"] != "verified":
            raise CuratedProtocolFixtureError(
                "Timer candidate is unknown or unverified."
            )
        if item["linked_step_id"] in resolved:
            raise CuratedProtocolFixtureError("Timer candidate is duplicated.")
        anchor = step.evidence.source_page_number
        if item["step_anchor_page"] != anchor:
            raise CuratedProtocolFixtureError(
                "Timer candidate misstates its anchor page."
            )
        # A step's own text may cross a page break, so its duration can sit on
        # a later page than its label -- in-gel step 24 is exactly that. The
        # cited page must lie between this step's anchor and the next step's,
        # which is derived from the fixture rather than assumed.
        position = order.index(step.step_id)
        last = (
            by_step[order[position + 1]].evidence.source_page_number
            if position + 1 < len(order)
            else extraction.page_count
        )
        if not anchor <= item["page_number"] <= last:
            raise CuratedProtocolFixtureError(
                "Timer candidate cites a page outside its step."
            )
        duration = item["duration_seconds"]
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or duration <= 0
        ):
            raise CuratedProtocolFixtureError(
                "Timer duration is not a positive whole number."
            )
        if _timer_literal_seconds(str(item["source_literal"])) != duration:
            raise CuratedProtocolFixtureError(
                "Timer literal does not state that duration."
            )
        handles = item["evidence_segment_ids"]
        if not isinstance(handles, list) or not handles:
            raise CuratedProtocolFixtureError("Timer candidate cites no evidence.")
        segments = {
            segment.segment_id: segment.text
            for segment in generate_page_evidence_segments(
                extraction,
                source_revision="pdf-1",
                page_number=item["page_number"],
            )
        }
        if any(handle not in segments for handle in handles):
            raise CuratedProtocolFixtureError(
                "Timer evidence does not resolve on that page."
            )
        cited = "".join(segments[handle] for handle in handles)
        if str(item["source_literal"]) not in cited:
            raise CuratedProtocolFixtureError(
                "Timer evidence does not contain its literal."
            )
        resolved[step.step_id] = duration
    return resolved


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
        # Stored provenance value, not an import path: it keeps the old package name.
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
    timer_path = fixture_file.with_name(f"{fixture_file.stem}.timers.json")
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
        timer_manifest=_load_timer_manifest(
            timer_path,
            fixture_sha256=fixture_sha256,
            extraction=extraction,
            steps=base_fixture.steps,
        ),
    )


#: Words of a catalog row that are never the item's name.
_CATALOG_ROW_WORDS = frozenset({
    "catalog", "grade", "scientific", "international", "brand",
})


def _resource_is_referenced(resource_text: str, claim_text: str) -> bool:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", resource_text.casefold())
    return any(
        token not in _CATALOG_ROW_WORDS and token in claim_text
        for token in tokens[:8]
    )


def _normalized_transcript(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def _utterance_key(value: str) -> str:
    key = _normalized_transcript(value).rstrip(" .?!。！？")
    key = re.sub(r"해\s*주세요$", "해줘", key)
    return re.sub(r"해\s*줘$", "해줘", key)


def normalize_conversational_utterance(value: str) -> str:
    """Normalize speech variation, disfluencies, stuttering, and conversational fillers
    without altering any operational numbers, units, solutions, or step numbers."""

    key = _utterance_key(value)
    # Decimal points are operational tokens, not punctuation. Preserve them so
    # 1.5 mL cannot become the unrelated value 5 mL during routing.
    key = re.sub(r"(?<!\d)\.(?!\d)|[,!?。！？;:]", " ", key)

    # Safe conversational filler words at utterance start
    while True:
        stripped = re.sub(
            r"^(?:okay|ok|오케이|응|네|예|어+|음+|저기|자|좋아|그래|well|um|uh|so|ah|oh)\s+",
            "",
            key,
            flags=re.I,
        )
        if stripped == key:
            break
        key = stripped

    # Repetition / stutter reduction for non-numeric words (up to 4 iterations)
    for _ in range(4):
        reduced = re.sub(
            r"\b([가-힣a-zA-Z]{1,10})\s+\1\b",
            r"\1",
            key,
            flags=re.I,
        )
        if reduced == key:
            break
        key = reduced

    return " ".join(key.split())


def _semantic_utterance_key(value: str) -> str:
    """Normalize harmless speech variation without fuzzy intent inference."""
    return normalize_conversational_utterance(value)


def _utterance_looks_like_new_command(transcript: str) -> bool:
    """True for a new workflow/control command, false for descriptive follow-ups."""

    key = _semantic_utterance_key(transcript)
    if not key:
        return False
    if key in _WORKFLOW_COMMANDS or key in _FULL_DETAIL_COMMANDS:
        return True
    if _front_control_intent(transcript, "ko") is not None:
        return True
    if _READ_ALOUD_REQUEST.fullmatch(key):
        return True
    if _SPECIFIC_STEP_PATTERN.fullmatch(key):
        return True
    if any(pattern.fullmatch(key) for pattern in _CURRENT_INFORMATION_PATTERNS):
        return True
    if any(pattern.fullmatch(key) for pattern in _UNDERSPECIFIED_RESULT_PATTERNS):
        return True
    command_groups = (
        _PAUSE_PATTERNS,
        _RESUME_PATTERNS,
        _TIMER_START_PATTERNS,
        _TIMER_QUERY_PATTERNS,
        _AGENT_META_PATTERNS,
        _NATURAL_STOP_PATTERNS,
        _NAVIGATION_PATTERNS,
        _NEXT_INFORMATION_PATTERNS,
        _AUDIO_RECOVERY_PATTERNS,
        _REPORT_REQUEST_PATTERNS,
        _OBSERVATION_COMMAND_PATTERNS,
        _COMPLETION_AND_NEXT_PATTERNS,
        _COMPLETION_ONLY_PATTERNS,
        _PREVIEW_STEP_PATTERNS,
        _CANCEL_READONLY_PATTERNS,
        _SOURCE_REQUEST_PATTERNS,
        _STEP_ELABORATION_PATTERNS,
        _EXPECTED_RESULT_PATTERNS,
        _EXTERNAL_MORE_PATTERNS,
        _REPEAT_PATTERNS,
        _UNRELIABLE_TRANSCRIPT_PATTERNS,
        _AMBIGUOUS_COMPLETION_PATTERNS,
    )
    if any(pattern.search(key) for group in command_groups for pattern in group):
        return True
    if any(pattern.search(key) for _scope, pattern in _PROTOCOL_SCOPE_PATTERNS):
        return True
    if any(pattern.search(key) for pattern, _category in _ANOMALY_PATTERNS):
        return True
    if _COMPLETION_CLAIM.search(key) or _NEXT_STEP_REQUEST.search(key):
        return True
    return False


def _utterance_looks_like_anomaly_follow_up(transcript: str) -> bool:
    """True only for a descriptive color/appearance answer to the pending anomaly prompt."""

    key = _semantic_utterance_key(transcript)
    if not key or _utterance_looks_like_new_command(transcript):
        return False
    if re.search(
        r"(?:무슨\s*의미|무슨\s*뜻|의미야|뜻이야|왜\s*(?:변|그래|이래)|원인|설명해|"
        r"뭐가\s*문제|어떤\s*의미|what\s+does|what\s+is\s+the\s+meaning|"
        r"why\s+(?:did|does|is)|explain)",
        key,
    ):
        return False
    return bool(re.search(
        r"(?:노란|갈색|투명|흐려|탁해|하얗|검게|붉|파란|초록|주황|분홍|"
        r"변색|색이\s*변|색깔이\s*변|turned|became|yellow|brown|cloudy|"
        r"clear|white|dark|orange|pink|purple|green|red|blue)",
        key,
    ))


@dataclass(frozen=True)
class TurnInterpretation:
    """Structured semantic turn interpretation proposing meaning without workflow authority."""

    language: str
    raw_utterance: str
    normalized_utterance: str
    speech_acts: tuple[str, ...] = ()
    explicit_entities: tuple[str, ...] = ()
    referenced_entities: tuple[str, ...] = ()
    requested_claims: tuple[str, ...] = ()
    requested_result_scope: str | None = None
    workflow_command: CuratedProtocolAction | None = None
    parameters: tuple[str, ...] = ()
    discourse_references: tuple[str, ...] = ()
    confidence: float = 1.0
    clarification_required: bool = False
    interpretation_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowContextCapsule:
    """Compact server-owned milestone injected into interpretation and Brain contexts."""

    session_phase: str = "preview"
    protocol_id: str | None = None
    workflow_revision: int = 0
    current_step_id: str | None = None
    current_step_label: str | None = None
    pending_interaction: str | None = None
    pending_completion_gate: bool = False
    pending_observation_gate: bool = False
    active_timer_state: dict[str, Any] | None = None
    recent_semantic_focus: str | None = None
    recent_explicit_entities: tuple[str, ...] = ()
    last_user_intent: str | None = None
    last_committed_workflow_action: str | None = None
    last_report_acknowledgment: str | None = None

    def summary_capsule(self) -> str:
        parts = [
            f"phase={self.session_phase}",
            f"step={self.current_step_label or 'none'}",
            f"revision={self.workflow_revision}",
        ]
        if self.pending_interaction:
            parts.append(f"pending={self.pending_interaction}")
        if self.active_timer_state:
            parts.append("timer=active")
        if self.recent_semantic_focus:
            parts.append(f"focus={self.recent_semantic_focus}")
        return " · ".join(parts)


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
    visual_intent: str | None = None
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
    range_start_step: int | None = None
    range_end_step: int | None = None


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
class EntitySourceAnswer:
    """One entity answered only from the active protocol's own statements."""

    entity: str
    label: str
    text: str
    speech: str
    evidence_ids: tuple[str, ...]
    found: bool
    #: The long form the PDF itself gives the label, as in
    #: "ammonium bicarbonate (AMBIC)"; the one definition it can supply.
    defined_as: str | None = None
    #: For a question about this entity alone: the first mention's reviewed
    #: Korean content spoken too, since there is nothing else to hear.
    spoken_detail: str = ""

    def spoken(self, *, alone: bool) -> str:
        if alone and not self.defined_as and self.spoken_detail:
            return self.spoken_detail
        return self.speech


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
        protocol=fixture.draft.protocol
        pages=protocol.metadata.pdf.pages
        page2=next((page.text for page in pages if page.source_page_number==2),"")

        def span(start:str,end:str)->str:
            start_at=page2.find(start)
            end_at=page2.find(end,start_at+len(start)) if start_at>=0 else -1
            if start_at<0 or end_at<0:
                raise CuratedProtocolFixtureError(
                    "Protocol-wide source span is unavailable.")
            return page2[start_at+len(start):end_at].strip()

        candidate_layout=(
            "Abstract\n" in page2
            and "\nProtocol materials" in page2
            and "Safety warnings\n" in page2
            and "\nBefore start" in page2
        )
        if candidate_layout:
            purpose_fact=CuratedProtocolFact(
                "protocol_purpose","purpose",
                span("Abstract\n","\nProtocol materials"),2,
            )
            safety_facts=(CuratedProtocolFact(
                "protocol_safety_warning","warning",
                span("Safety warnings\n","\nBefore start"),2,
            ),)
        else:
            description=protocol.description
            metadata_evidence=protocol.metadata.evidence
            purpose_text=(
                description.source_text if description is not None
                else protocol.metadata.title
            )
            purpose_page=(
                description.evidence.source_page_number
                if description is not None
                else metadata_evidence.source_page_number
                if metadata_evidence is not None
                else 1
            )
            purpose_fact=CuratedProtocolFact(
                "protocol_purpose","purpose",purpose_text,purpose_page,
            )
            warnings=[]
            for section in protocol.sections:
                for step in section.steps:
                    warnings.extend(step.warnings)
                    for action in step.sub_actions:
                        warnings.extend(action.warnings)
            safety_facts=tuple(CuratedProtocolFact(
                f"protocol_safety_warning_{index}","warning",
                warning.source_text,warning.evidence.source_page_number,
            ) for index,warning in enumerate(warnings,1))
        return cls(
            title=fixture.title,status=fixture.status,
            purpose=purpose_fact,
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
            safety=safety_facts,
            sections=tuple(CuratedProtocolFact(
                f"protocol_section_{index}","protocol_section",
                (
                    f"{section.title_source_text}: steps "
                    f"{section.steps[0].source_label}-{section.steps[-1].source_label}"
                    if section.steps else section.title_source_text
                ),
                section.evidence.source_page_number,
            ) for index,section in enumerate(protocol.sections,1)),
        )


#: Lab terms a protocol's own wording may contain. Being listed here sends
#: nothing to STT: ProtocolVocabulary keeps a candidate only where the active
#: protocol's text contains it, spelled the way that text spells it. These are
#: the in-gel (Candidate A) document's terms, which is where the list came
#: from; any other protocol is heard through its own materials, equipment and
#: labelled reagents, so it needs no entry here.
_STT_TERM_CANDIDATES = (
    "AMBIC", "ammonium bicarbonate", "HPLC water", "acetonitrile",
    "Solution A", "Solution B", "DTT", "iodoacetamide", "trypsin",
    "formic acid", "LC-MS", "SDS-PAGE", "gel plug",
    "stained protein band", "Thermomixer", "rpm", "incubation",
    "keratin", "contamination", "Evotip",
)
#: The xAI STT request takes at most this many keyterms of 1-50 characters.
_STT_KEYTERM_CAP = 100
_STT_KEYTERM_MAX_CHARS = 50
#: Shapes protocol prose gives a technical name whatever the document: a
#: labelled reagent ("Solution A", "Buffer 2"), an abbreviation defined in
#: parentheses ("ammonium bicarbonate (AMBIC)") and a hyphenated acronym
#: ("SDS-PAGE").
_LABELLED_REAGENT = re.compile(
    r"(?<![A-Za-z0-9])(?i:solution|buffer|reagent)\s+[A-Z0-9](?![A-Za-z0-9])"
)
_DEFINED_ABBREVIATION = re.compile(r"\(([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\)")
_HYPHENATED_ACRONYM = re.compile(
    r"(?<![A-Za-z0-9-])[A-Z]{2,}(?:-[A-Z]{2,})+(?![A-Za-z0-9-])"
)


#: The in-gel reagents and gel pieces the mis-hearing repairs and entity
#: patterns resolve to, with the protocol wording that shows a protocol has
#: one. Those repairs apply only where the active protocol's text uses one of
#: these surfaces; an entity not listed here is general lab vocabulary and is
#: recognised in any protocol.
_SUBSTANCE_PRESENCE_ALIASES: dict[str, tuple[str, ...]] = {
    "ambic": ("ambic", "ammonium bicarbonate"),
    "hplc_water": ("hplc water",),
    "solution_a": ("solution a",),
    "solution_b": ("solution b",),
    "acetonitrile": ("acetonitrile",),
    "gel_plug": ("gel plug",),
    "stained_protein_band": ("stained protein band",),
    "dtt": ("dtt", "dithiothreitol"),
    "iodoacetamide": ("iodoacetamide",),
    "trypsin": ("trypsin",),
    "formic_acid": ("formic acid",),
}


def _term_pattern(term: str) -> re.Pattern[str]:
    """Match ``term`` as whole words, with any whitespace and a plural ending."""

    words = r"\s+".join(re.escape(word) for word in term.split())
    return re.compile(
        rf"(?<![A-Za-z0-9])({words})(?:e?s)?(?![A-Za-z0-9])", re.IGNORECASE
    )


def _resource_keyterm(name_source_text: str, prose: str) -> str:
    """The name a protocol's steps use for one material or equipment row.

    A protocols.io row carries vendor, catalog and model text after the name
    ("Promega trypsin Promega Catalog #V5113"), and an equipment row names its
    brand on a line of its own, labelled by the next one ("Eppendorf\\nBRAND").
    The longest run of the row's words that the steps also use is the name the
    experimenter hears and says, spelled as the steps spell it. A row the steps
    never name keeps the words before its catalog text.
    """

    lines = [line.strip() for line in name_source_text.splitlines() if line.strip()]
    if not lines:
        return ""
    brands = {
        lines[index - 1].casefold()
        for index, line in enumerate(lines)
        if line.casefold() == "brand" and index > 0
    }
    head = re.split(
        r"\s+(?:catalog|cat\.?|model|sku)\b|\s+#|,", lines[0],
        maxsplit=1, flags=re.IGNORECASE,
    )[0]
    words = [word for word in head.split() if word.casefold() not in brands]
    for size in range(len(words), 0, -1):
        for start in range(len(words) - size + 1):
            phrase = " ".join(words[start:start + size])
            if len(phrase) < 3 or phrase.casefold() in _CATALOG_ROW_WORDS:
                continue
            used = _term_pattern(phrase).search(prose)
            if used is not None:
                return " ".join(used.group(1).split())
    name = ""
    for word in words:
        if len(f"{name} {word}".strip()) > _STT_KEYTERM_MAX_CHARS:
            break
        name = f"{name} {word}".strip()
    return name


@dataclass(frozen=True)
class ProtocolTerm:
    """One technical term, spelled as the active protocol spells it."""

    text: str
    #: Steps whose facts contain the term. Empty for a term that only the
    #: protocol's overview (purpose, before-start, materials list) uses.
    step_indexes: frozenset[int]
    #: A material or equipment name.
    resource: bool = False


@dataclass(frozen=True)
class ProtocolVocabulary:
    """The technical terms one protocol's own text contains.

    Derived from the fixture and nothing else, so one document's vocabulary
    cannot reach another's speech recognition: a term is here because the
    protocol's materials, equipment or prose contain it.
    """

    terms: tuple[ProtocolTerm, ...]
    #: Everything the protocol says, whitespace-collapsed and casefolded.
    corpus: str = ""
    #: Each material row as the steps name it, in the protocol's order.
    materials: tuple[str, ...] = ()

    def mentions(self, surface: str) -> bool:
        """Whether the protocol's text uses ``surface`` as whole words."""

        return _term_pattern(surface).search(self.corpus) is not None

    def mentions_entity(self, entity: str) -> bool:
        """Whether a substance-scoped entity is one this protocol has.

        An entity outside ``_SUBSTANCE_PRESENCE_ALIASES`` is general lab
        vocabulary, so it is always allowed.
        """

        aliases = _SUBSTANCE_PRESENCE_ALIASES.get(entity)
        return aliases is None or any(self.mentions(alias) for alias in aliases)

    @classmethod
    def from_fixture(cls, fixture: CuratedProtocolFixture) -> "ProtocolVocabulary":
        protocol = fixture.draft.protocol
        section_titles = {
            step.step_id: section.title_source_text
            for section in protocol.sections
            for step in section.steps
        }
        step_texts: list[str] = []
        prose: list[str] = []
        for index, step in enumerate(fixture.steps):
            facts = fixture.facts_for_step(index)
            title = section_titles.get(step.step_id, "")
            step_texts.append(" ".join(
                " ".join((title, *(fact.text for fact in facts))).split()
            ))
            if title not in prose:
                prose.append(title)
            prose.extend(
                fact.text for fact in facts
                if fact.kind not in {"material", "equipment", "prerequisite"}
            )
        overview = [item.source_text for item in protocol.before_start]
        overview.extend(item.name_source_text for item in protocol.materials)
        overview.extend(item.name_source_text for item in protocol.equipment)
        overview.append(protocol.metadata.title)
        if protocol.description is not None:
            overview.append(protocol.description.source_text)
        try:
            view = ProtocolKnowledgeView.from_fixture(fixture)
        except CuratedProtocolFixtureError:
            view = None
        if view is not None:
            overview.append(view.purpose.text)
            overview.extend(fact.text for fact in view.safety)
        prose_text = "\n".join(" ".join(text.split()) for text in prose)
        corpus = tuple(" ".join(text.split()) for text in (*prose, *overview))

        materials = tuple(
            _resource_keyterm(item.name_source_text, prose_text)
            for item in protocol.materials
        )
        found: list[tuple[str, bool]] = [(name, True) for name in materials]
        found.extend(
            (_resource_keyterm(item.name_source_text, prose_text), True)
            for item in protocol.equipment
        )
        found.extend((candidate, False) for candidate in _STT_TERM_CANDIDATES)
        for text in corpus:
            found.extend((match.group(0), False) for match in _LABELLED_REAGENT.finditer(text))
            found.extend(
                (match.group(1), False)
                for match in _DEFINED_ABBREVIATION.finditer(text)
                if sum(char.isupper() for char in match.group(1)) >= 2
            )
            found.extend((match.group(0), False) for match in _HYPHENATED_ACRONYM.finditer(text))

        terms: dict[str, ProtocolTerm] = {}
        for raw, resource in found:
            key = " ".join(raw.split()).casefold()
            if not key or key in terms:
                continue
            pattern = _term_pattern(raw)
            spelled = next(
                (match.group(1) for text in corpus if (match := pattern.search(text))),
                None,
            )
            if spelled is None:
                # A candidate the protocol never uses is not its vocabulary.
                if not resource:
                    continue
                spelled = raw
            spelled = " ".join(spelled.split())
            if not 1 <= len(spelled) <= _STT_KEYTERM_MAX_CHARS:
                continue
            terms[key] = ProtocolTerm(
                text=spelled,
                step_indexes=frozenset(
                    index for index, text in enumerate(step_texts)
                    if pattern.search(text)
                ),
                resource=resource,
            )
        return cls(
            terms=tuple(terms.values()),
            corpus="\n".join(corpus).casefold(),
            materials=tuple(name for name in materials if name),
        )

    def keyterms_near(self, index: int | None) -> tuple[str, ...]:
        """Every term, the current step's neighbourhood first.

        The terms used in the steps just before, at and after ``index`` come
        first, then the protocol's materials and equipment, then the rest by
        distance from ``index``, so the ones the experimenter is about to say
        survive a cap. ``None`` keeps the protocol's own order.
        """

        if index is None:
            return tuple(term.text for term in self.terms)

        def rank(item: tuple[int, ProtocolTerm]) -> tuple[int, int, int]:
            order, term = item
            distance = min(
                (abs(step - index) for step in term.step_indexes),
                default=None,
            )
            if distance is not None and distance <= 1:
                return (0, distance, order)
            if term.resource:
                return (1, 0, order)
            if distance is not None:
                return (2, distance, order)
            return (3, 0, order)

        return tuple(
            term.text for _, term in sorted(enumerate(self.terms), key=rank)
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
    requested_target_step: str | None = None


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
    #: How many times this prompt has been asked again after a reply it could
    #: not read. Bounded by _OBSERVATION_REPROMPT_LIMIT.
    reprompt_count: int = 0
    #: Whether a bare yes/no answers it. False after an anomaly was recorded
    #: under it: the last question then was about the problem, so a "네"
    #: says nothing about the endpoint until the question is asked again.
    accepts_yes_no: bool = True


@dataclass(frozen=True)
class PendingTranscriptConfirmation:
    """One step/version-bound confirmation for a mutation-sensitive repaired transcript."""

    configuration_id: int | None
    step_id: str
    step_index: int
    step_label: str
    workflow_revision: int
    requested_turn_id: int
    requested_generation: int | None
    proposed_transcript: str
    proposed_action: CuratedProtocolAction
    proposed_target_step: str | None = None


@dataclass(frozen=True)
class TranscriptRepairDecision:
    status: Literal["exact", "safe_autocorrection", "confirmation_required", "unresolved"]
    normalized_transcript: str
    proposed_transcript: str | None = None
    proposed_action: CuratedProtocolAction | None = None
    proposed_target_step: str | None = None
    note: str | None = None


_TYPO_NORMALIZATIONS = (
    (re.compile(r"\b단게\b"), "단계"),
    (re.compile(r"\b완로\b"), "완료"),
    (re.compile(r"\b완려\b"), "완료"),
    (re.compile(r"\b완뇨\b"), "완료"),
    (re.compile(r"\b다\s*헸어\b"), "다 했어"),
    (re.compile(r"\b끝냇어\b"), "끝냈어"),
)

_HIGH_RISK_COMPLETION_CORRUPTION_PATTERNS = (
    # e.g. "탐방대도 완료했어", "삼방대도 완료했어", "삼당계도 완료했어", "산단계 완료했어", "탐단계 완료했어"
    re.compile(r"^(?:(?:현재|지금|이번|이)\s*)?(?:탐방대|삼방대|탐단계|삼당계|산단계|사단계|탐당계|탄단계|단방대|담방대)\s*(?:도|는|은|를|을|이|가|로)?\s*(?:미리|이미|벌써|방금|아까|다|완전히)?\s*(?:완료(?:했어|했어요|했습니다)?|끝(?:냈어|냈어요|냈습니다|났어|났어요)|다\s*했어|마쳤어)"),
)


def classify_contextual_transcript_repair(
    transcript: str,
    current_step_label: str,
    language: str = "ko",
) -> TranscriptRepairDecision:
    """Bounded contextual repair distinguishing safe typos from high-risk corruptions."""
    if language != "ko" or not isinstance(transcript, str):
        return TranscriptRepairDecision(status="exact", normalized_transcript=transcript)

    raw = transcript.strip()
    if not raw:
        return TranscriptRepairDecision(status="exact", normalized_transcript="")

    # 1. Check for low-risk safe autocorrection
    normalized = raw
    notes: list[str] = []
    for pattern, repl in _TYPO_NORMALIZATIONS:
        if pattern.search(normalized):
            normalized = pattern.sub(repl, normalized)
            notes.append(f"'{pattern.pattern}' -> '{repl}'")

    if normalized != raw:
        return TranscriptRepairDecision(
            status="safe_autocorrection",
            normalized_transcript=normalized,
            note=", ".join(notes) if notes else None,
        )

    # 2. Check for high-risk phonetic completion corruptions
    for pattern in _HIGH_RISK_COMPLETION_CORRUPTION_PATTERNS:
        if pattern.search(raw):
            proposed = f"{current_step_label}단계도 완료했어"
            return TranscriptRepairDecision(
                status="confirmation_required",
                normalized_transcript=raw,
                proposed_transcript=proposed,
                proposed_action=CuratedProtocolAction.NEXT,
                proposed_target_step=current_step_label,
            )

    return TranscriptRepairDecision(status="exact", normalized_transcript=raw)


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
    r"그\s*용액|그\s*시약|그\s*사진|그\s*이미지|그\s*구조|그\s*화학\s*구조|"
    r"관련\s*(?:사진|이미지|자료|그림|시각\s*자료)|관련된\s*(?:사진|이미지|자료|그림)|"
    r"어떻게\s*생겼|어떤\s*모습|그거\s*(?:사진|이미지|구조|설명)|"
    r"그중\s*첫\s*번째|that(?:\s+(?:material|water|solution|reagent|structure|photo|image|picture|one))?|"
    r"the\s+first\s+one|how\s+is\s+that\s+different|tell\s+me\s+more\s+about\s+it|"
    r"related\s+(?:photo|image|picture|visual|structure)|"
    r"show\s+(?:me\s+)?(?:the\s+)?(?:photo|image|picture|structure|visual)|"
    r"what\s+does\s+it\s+look\s+like|show\s+its\s+structure)"
)


def resolve_bounded_coreference(
    transcript: str,
    *,
    explicit_entities: tuple[str, ...],
    context: ProtocolDiscourseContext | None,
) -> CoreferenceResolution:
    """Resolve one recent semantic focus without granting workflow authority."""

    if explicit_entities:
        return CoreferenceResolution(
            CoreferenceStatus.RESOLVED, explicit_entities, "explicit_entity_wins"
        )
    key = _semantic_utterance_key(transcript)
    if _COREFERENCE_REFERENCE.search(key) is None and not re.search(r"(?:그중\s*첫\s*번째|the\s+first\s+one)", key):
        return CoreferenceResolution(
            CoreferenceStatus.UNRESOLVED, (), "no_coreference_expression"
        )
    if context is None:
        return CoreferenceResolution(
            CoreferenceStatus.UNRESOLVED, (), "no_owned_recent_context"
        )
    if re.search(r"(?:그중\s*첫\s*번째|the\s+first\s+one)", key):
        if context.comparison_entities:
            return CoreferenceResolution(
                CoreferenceStatus.RESOLVED,
                context.comparison_entities[:1],
                "ordered_comparison_first",
            )
        return CoreferenceResolution(
            CoreferenceStatus.UNRESOLVED, (), "comparison_context_missing"
        )
    candidates = tuple(dict.fromkeys(
        context.comparison_entities or context.focused_entities
    ))
    if len(candidates) == 1:
        return CoreferenceResolution(
            CoreferenceStatus.RESOLVED, candidates, "single_recent_focus"
        )
    if len(candidates) > 1:
        return CoreferenceResolution(
            CoreferenceStatus.AMBIGUOUS, candidates, "multiple_recent_entities"
        )
    return CoreferenceResolution(
        CoreferenceStatus.UNRESOLVED, (), "recent_focus_empty"
    )


_COMPLETION_AND_NEXT_PATTERNS = (
    re.compile(
        r"(?:현재\s+){0,2}(?:이\s*|이번\s*)?(?:단계|작업|것)?(?:도|은|는|를|이|가)?\s*"
        r"(?:완료(?:했어|했어요|했습니다|했으니|했으니까)?|끝냈어|끝냈어요|끝났어|끝났어요|끝났으니|끝났으니까|다\s*했어|다\s*했어요|마쳤어|마쳤어요)"
        r".*(?:다음(?:\s*단계)?|다음으로|넘어가|넘어가자).*(?:안내|알려|넘어|진행|가자)?",
    ),
    re.compile(
        r"(?:다음(?:\s*단계)?|다음으로).*(?:안내|알려|넘어|진행|가자).*"
        r"(?:현재\s+){0,2}(?:이\s*|이번\s*)?(?:단계|작업|것)?(?:도|은|는|를|이|가)?\s*"
        r"(?:완료(?:했어|했어요|했습니다|했으니)?|끝냈어|끝냈어요|끝났어|끝났어요|다\s*했어|다\s*했어요|마쳤어|마쳤어요)",
    ),
    re.compile(
        r"여기까지\s*(?:했어|했고|했습니다).*(?:이제\s*)?다음(?:으로|\s*단계)",
    ),
    re.compile(
        r"(?:i\s+)?(?:finished|completed|am\s+done\s+with)\s+(?:this|the\s+current)?\s*(?:step|one)?"
        r".*(?:next|what\s+comes\s+next|take\s+me\s+to)",
        re.I,
    ),
    re.compile(
        r"(?:take\s+me\s+to\s+(?:the\s+)?next\s+step|guide\s+me\s+to\s+(?:the\s+)?next\s+step|go\s+to\s+(?:the\s+)?next\s+step)"
        r".*(?:finished|completed|done\s+with|complete)",
        re.I,
    ),
    re.compile(r"this\s+step\s+is\s+complete.*(?:next|what\s+comes\s+next)", re.I),
)
_COMPLETION_ONLY_PATTERNS = (
    re.compile(
        r"^(?:(?:네|예|응|그래|자|그럼)?,?\s*)?"
        r"(?:(?:현재|지금|이번|이)\s*(?:단계|작업)?\s*(?:도|는|은|를|을|이|가|로)?\s*)"
        r"(?:미리|이미|벌써|방금|아까|다|완전히|모두)?\s*"
        r"(?:완료(?:했어|했어요|했습니다|함)?|끝냈어|끝냈어요|끝났습니다|"
        r"끝났어|끝났어요|마쳤어|마쳤어요|마쳤습니다|다\s*했어|다\s*했어요)$"
    ),
    re.compile(r"^(?:현재|지금|이번|이)\s*(?:단계|작업)\s*완료$"),
    re.compile(r"^(?:여기까지|방금\s*(?:작업|단계)?|벌써|이미|미리)\s*(?:다\s*했어|다\s*했어요|마쳤어|마쳤어요|끝났어|끝났어요|끝냈어|끝냈어요|완료했어|완료했어요|완료했습니다|끝났습니다)$"),
    re.compile(r"^(?:i\s+)?(?:already\s+)?completed\s+(?:the\s+)?current\s+step$"),
    re.compile(r"^(?:this|the\s+current)\s+step\s+is\s+(?:already\s+)?(?:finished|complete)$"),
    re.compile(r"^(?:completed|finished|done)\s*했어$"),
)
_COMPLETION_CLAIM = re.compile(
    r"(?:"
    r"(?:(?:현재|지금|이번|이)\s*(?:단계|작업)?\s*(?:도|는|은|를|을|이|가|로)?\s*)"
    r"(?:미리|이미|벌써|방금|아까|다|완전히|모두)?\s*"
    r"(?:완료(?:했어|했어요|했습니다|해서|했으니|했으니까|함)|"
    r"끝(?:났어|났어요|났습니다|냈어|냈어요|냈고)|"
    r"다\s*했어|다\s*했어요|다\s*했고|다\s*했으니까|마쳤어|마쳤어요|마쳤습니다|"
    r"completed\s*했어|finish\s*했어)"
    r"|(?:completed|finished|done)\s*했어"
    r"|(?:여기까지|방금\s*(?:작업|단계)?|벌써|이미|미리)\s*(?:다\s*했어|다\s*했어요|다\s*했고|다\s*했으니까|마쳤어|마쳤어요|끝났어|끝냈어|완료했어)"
    r"|(?:i\s+)?(?:already\s+)?(?:completed|finished|done)\s+(?:this|the\s+current)\s+step"
    r"|(?:this|the\s+current)\s+step\s+is\s+(?:already\s+)?(?:complete|finished|done)"
    r"|this\s+step\s+is\s+done"
    r")"
)
#: A completion said with no step named: "완료했어", "다 했어", "끝났어",
#: "마쳤어", "다했다", and the endings people put on them. It is asked about
#: once ("N단계 완료하셨나요?") instead of completing a step nobody named.
_UNTARGETED_COMPLETION = re.compile(
    r"(?:(?:벌써|이미|방금|아까|지금|여기까지|모두|전부)\s*)?"
    r"(?:완료|(?:완료했|끝냈|끝났|끝낫|마쳤|다\s*했|모두\s*했)"
    r"(?:어|어요|어용|습니다|다|음|지|네|네요|심더|당께|슈|소))"
)
#: The words that name the step a completion is about: "현재 단계", "이번
#: 단계", "이 단계", "4단계", "step 4", "this step". Such a completion is
#: taken as said.
_COMPLETION_NAMES_A_STEP = re.compile(
    r"(?:현재|지금|이번|이)\s*(?:단계|작업)|[0-9]+\s*단계|"
    r"(?:[이삼사오육칠팔구]?십[일이삼사오육칠팔구]?|[일이삼사오육칠팔구])\s*단계|"
    r"\bstep\s*[0-9]|(?:this|the\s+current)\s+step",
    re.I,
)
_UNTARGETED_COMPLETION_KINDS = frozenset({
    "report_completion_untargeted", "completion_and_next_untargeted",
})


def _completion_intent_kind(key: str, kind: str) -> str:
    """``kind``, or its untargeted form when no step is named in ``key``."""

    return kind if _COMPLETION_NAMES_A_STEP.search(key) else f"{kind}_untargeted"


_NEXT_STEP_REQUEST = re.compile(
    r"(?:다음(?:\s*단계)?|next(?:\s+step|\s+one)|what\s+comes\s+next)"
    r".*(?:안내|알려|넘어|진행|가자|show|tell|move|go|proceed)?"
)
_AFFIRMATIVE_COMPLETION_CONFIRMATION = re.compile(
    r"^(?:(?:네|예|응|그래|맞아|맞아요)(?:\s+(?:(?:현재|지금|이번|이)\s*"
    r"(?:단계|작업)?\s*(?:도|를|을|는|은|이|가)?\s*)?(?:미리|이미|벌써|다)?\s*(?:완료(?:했어|했어요|했습니다)?|"
    r"끝냈어|끝냈어요|다\s*했어|다\s*했어요|했어|했어요))?|"
    r"(?:(?:현재|지금|이번|이)\s*(?:단계|작업)?\s*(?:도|를|을|는|은|이|가)?\s*)?(?:미리|이미|벌써|다)?\s*(?:완료(?:했어|했어요|했습니다)|"
    r"끝냈어|끝냈어요|다\s*했어|다\s*했어요|마쳤어|마쳤어요)|"
    r"yes(?:\s*,?\s*(?:i\s+)?(?:already\s+)?(?:finished|completed)(?:\s+it|\s+the\s+step)?)?|"
    r"(?:i\s+)?(?:already\s+)?(?:finished|completed)(?:\s+it|\s+the\s+step)?|"
    r"done|"
    r"(?:다음\s*단계로\s*)?(?:이동할게|이동해\s*줘|넘어갈게|넘어가자|진행해|옮겨)|"
    r"yes[, ]*move\s+on|let(?:'|’)s\s+continue|proceed|"
    r"go\s+to\s+(?:the\s+)?next\s+step)$"
)
# The negative reply to any server-owned yes/no question. _binary_frame_reply
# and _observation_binary_reply read this same pattern, so the completion and
# observation prompts cannot disagree about whether an answer declined.
_NEGATIVE_COMPLETION_CONFIRMATION = re.compile(
    r"^(?:아니|아니요|아니오|아뇨|아니에요|아닙니다|아니야|"
    r"아직|아직이요|아직요|아직\s*아니야|아직\s*아니에요|"
    r"아직\s*안\s*(?:끝났어|끝났어요|했어|했어요|됐어|됐어요)|"
    r"아직\s*못\s*했어|안\s*했어|안\s*했어요|"
    r"아니[요오]?\s+아직\s+안\s*(?:끝났어|했어|했어요)|"
    r"아니[,.]?\s*아직\s*안\s+끝났어|no|not\s+yet|no,?\s+not\s+yet)$"
)
# A reply that asks or hedges is not assent, whatever words it shares with a
# report. These read the transcript as spoken: the utterance key turns "?" into
# a space, so a guard on the key cannot tell "네?" -- asking to hear the
# question again -- from "네", or "젤이 투명해?" from "젤이 투명해".
# "니까" is excluded from the question ending because it is also "because"
# ("상태니까"), and "아까" because it is "a moment ago".
_QUESTION_MARKERS = re.compile(
    r"[?？]"
    r"|(?:나요|습니까|냐)(?=$|[\s.,!~])"
    r"|(?<![니아])까요?(?=$|[\s.,!~])"
    r"|(?:했|됐|되|하|있|없|맞|났|졌|봤)니(?=$|[\s.,!~])"
    r"|(?:한|된|인|건|는|은)가요?(?=$|[\s.,!~])"
    r"|뭐(?:야|예요|에요|지|죠|니|냐)|뭔(?:가요|데|지)|무엇|무슨|왜|어떻게|얼마나|언제|어디|몇"
    r"|알려|설명|의미|(?<!따)뜻"
    r"|\b(?:what|why|how|when|where|which|explain|meaning)\b"
)
_UNCERTAIN_MARKERS = re.compile(
    r"(?:는|은|인|한|된|건|던)지(?=$|[\s.,!~]|도|를|가|는|요)"
    r"|모르(?:겠|는)|몰라|잘\s*모르"
    r"|확인해\s*볼|확인할게|봐야|볼게"
    r"|(?:해|돼|되어|되|보여|있어|어|아|여)야\s*(?:해|돼|되|하|합|됩)"
    r"|(?:것|거|듯)\s*같|듯(?:해|하|싶)|같기도"
    r"|아마|혹시|글쎄|애매|헷갈"
    r"|\b(?:not\s+sure|unsure|maybe|perhaps|probably|might|whether|should)\b"
    r"|don(?:'|’)?t\s+know|do\s+not\s+know|\bi\s+think\b|let\s+me\s+check"
    r"|(?:i(?:'|’)ll|will)\s+check|need\s+to|have\s+to"
)
# Reading the source line back ("…탈색될 때까지 2-7단계를 반복합니다") names
# the endpoint without reporting it.
_SOURCE_RECITATION = re.compile(
    r"때까지|\buntil\b|반복(?:합니다|하세요|한다|하십시오|하라)|\brepeat\s+steps?\b"
)
# A negation after the endpoint phrase undoes it: "완전히 탈색 안 됐어",
# "젤이 투명해지지 않았어". A standalone 안/못/덜 only, so 안내 or 잘못 does
# not read as one.
_POST_FRAME_NEGATION = re.compile(
    r"(?<![가-힣])안(?=$|\s|[됐되돼된했해보빠변말])"
    r"|않"
    r"|(?<![가-힣])못(?=$|\s|[했해하봤보됐되돼])"
    r"|(?<![가-힣])덜(?=$|\s|[됐되돼빠말했])"
    r"|아닌|아니(?!면)"
)
_PRE_FRAME_NEGATION_EN = re.compile(r"\b(?:not|never)\b|n(?:'|’)t\b")
# How people in the 2026-10-01 voice test said the step-7 endpoint was reached:
# "탈색으로 바뀌었어", "탈색 상태야", "탈색이 됐어". Each has to end as a
# statement, so "탈색됐으면 좋겠어" or "탈색으로 바뀌었는지" is not one; the
# question and hedge guards in _observation_predicate run before this.
_STATEMENT_END = r"(?:어요|어|습니다|다|네요|네|음)(?=$|[\s.,!~])"
_STEP_7_SPOKEN_ENDPOINT = re.compile(
    r"탈색으로\s*(?:바뀌었|바꼈|변했)" + _STATEMENT_END
    + r"|탈색(?:된|이\s*된)?\s*상태(?:야|예요|에요|이에요|입니다|이다|임)(?=$|[\s.,!~])"
    + r"|탈색이?\s*(?:됐|되었)" + _STATEMENT_END
    # Pilot log B and C: "탈색돼 있어", "탈색이 완료됐어"; and the bare
    # "탈색 완료", which is not followed by what would make it a time or a
    # plan ("탈색 완료 전", "탈색 완료하면", "탈색 완료 시간").
    + r"|탈색이?\s*(?:돼|되어)\s*있" + _STATEMENT_END
    + r"|탈색이?\s*완료\s*(?:됐|되었)" + _STATEMENT_END
    # Lane O §7-4: "탈색 완료했어" was asked again where "탈색 완료" was not.
    + r"|탈색(?:을|이)?\s*완료\s*(?:했|하였)" + _STATEMENT_END
    + r"|탈색\s*완료(?:야|예요|에요|요|입니다|이다|임)?"
    r"(?=$|[.,!~]|\s+(?!전|후|시|때|하|되|될|까지|여부|조건|기준|라고|라는|인지|단계))"
)
# "아니, 아니. … 지금 탈색 상태야" opens with a correction, not a negation.
_LEADING_CORRECTION = re.compile(r"^(?:(?:아니(?:요|야|에요)?|아뇨)\s*)+")
# A wish or a condition beside it ("탈색되면", "탈색이 됐으면 좋겠어",
# "바뀌었다면", "될 거야", "탈색 완료했으면") says what should happen, not
# what was seen.
_ENDPOINT_CONDITION = re.compile(
    r"(?:되|됐|되었|돼|바뀌|바뀌었|바꼈|변하|변했|빠지|빠졌|해지|해졌|했|하였)으?면"
    r"|(?:됐|되었|바뀌었|바꼈|변했|했|하였)다면"
    r"|(?:되|바뀌|변하)(?:길|기를|도록|려면|기\s*전)"
    r"|(?:될|바뀔|변할)\s*(?:때|거|것|수)"
)
# What an outstanding observation prompt does with a reply it could not read.
# Control commands go through as before, and so do record/report commands the
# person issues on their own. A read-only answer keeps the prompt only for an
# explicit question: a description the classifier happens to read as a term
# question ("결과는 탈색돼 있어") is an unread answer, not a question.
_OBSERVATION_PROMPT_PASS_THROUGH = frozenset({
    CuratedProtocolAction.STOP,
    CuratedProtocolAction.PAUSE,
    CuratedProtocolAction.RESUME,
    CuratedProtocolAction.START,
    CuratedProtocolAction.START_TIMER,
    CuratedProtocolAction.TIMER_STATUS,
    CuratedProtocolAction.AUDIO_RECOVERY,
    CuratedProtocolAction.CANCEL_READONLY,
    CuratedProtocolAction.RECORD_OBSERVATION,
    CuratedProtocolAction.REPORT_ANOMALY,
    CuratedProtocolAction.SHOW_REPORT,
    CuratedProtocolAction.REPORT_HANDOFF,
})
_OBSERVATION_PROMPT_READ_ONLY = frozenset({
    CuratedProtocolAction.QUESTION,
    CuratedProtocolAction.RELATED_QUESTION,
    CuratedProtocolAction.FULL_DETAIL,
    CuratedProtocolAction.CURRENT,
    CuratedProtocolAction.REPEAT,
    CuratedProtocolAction.NEXT_INFORMATION,
    CuratedProtocolAction.COMPLETION_CRITERIA,
    CuratedProtocolAction.OPERATIONAL_DEVIATION,
    CuratedProtocolAction.PROTOCOL_QUERY,
    CuratedProtocolAction.PREVIEW_STEP,
    CuratedProtocolAction.STEP_RANGE,
    CuratedProtocolAction.LAB_DOMAIN_QA,
    CuratedProtocolAction.VISUAL_REQUEST,
    CuratedProtocolAction.AGENT_META,
    CuratedProtocolAction.CLARIFY_REFERENCE,
    CuratedProtocolAction.UNSUPPORTED,
})
#: While an observation prompt is open, an anomaly is taken only from words
#: that say something went wrong. The anomaly reader alone also takes a plain
#: description of a change ("결과는 탈색으로 바뀌었어" matched "결과 … 바뀌"),
#: which is the observation the prompt asked for, not a problem.
_OBSERVATION_PROMPT_PROBLEM = re.compile(
    r"이상(?:해|하(?!지\s*않)|한|했|함|\s*(?:상황|현상|발생|사항|있|생겼))|뭔가\s*이상"
    r"|문제(?!(?:\s*(?:가|는|도))?\s*(?:전혀\s*)?(?:없|안\s*(?:돼|되)))"
    r"|(?:예상|생각)(?:과|이랑|하고|했던\s*(?:것|거)(?:과|랑)?)\s*(?:달라|다르|다른|틀려)"
    r"|잘못|실수"
    r"|터졌|흘렸|쏟았|쏟아졌|깨졌|부서졌|금이\s*갔|튀었|누출|새고\s*있|샜"
    r"|오염|고장|멈췄"
    r"|\b(?:wrong|problem|issue|anomal\w*|abnormal|unexpected\w*|spill\w*|broke\w*|leak\w*|contaminat\w*)\b"
)
#: Times an observation prompt is asked again before it is let go.
_OBSERVATION_REPROMPT_LIMIT = 2
#: Asking to hear the observation question again ("다시 말해줘"). It is not
#: a reply to the question, so it does not count toward the limit above.
_OBSERVATION_REPEAT_REQUEST = re.compile(
    r"^(?:다시(?:\s*한\s*번)?|한\s*번\s*더)\s*(?:말해|들려)\s*(?:줘|주세요)$"
)


def _reply_withholds_assent(transcript: str) -> bool:
    """True when the spoken reply asks a question or hedges."""

    raw = " ".join(str(transcript).casefold().split())
    return bool(_QUESTION_MARKERS.search(raw) or _UNCERTAIN_MARKERS.search(raw))


def _frame_negated(key: str, match: re.Match[str]) -> bool:
    """True when a negation sits right after (Korean) or before (English) a frame."""

    tail = key[match.end():match.end() + 12]
    head = key[max(0, match.start() - 24):match.start()]
    return bool(
        _POST_FRAME_NEGATION.search(tail) or _PRE_FRAME_NEGATION_EN.search(head)
    )


def _endpoint_report_reading(key: str) -> str:
    """How an utterance holding an endpoint phrase reads as a whole.

    A negation anywhere but an opening "아니" correction makes it a negative,
    so "탈색이 안 됐어 … 완전히 탈색됐어" is never read as the endpoint
    reached; and "아직" beside it ("아직 탈색 됐어", an "안" the transcript
    may have dropped), or a wish or condition ("완전히 탈색되면"), says
    neither, so it is asked again (``"unread"``).
    """

    said = _LEADING_CORRECTION.sub("", key)
    if _POST_FRAME_NEGATION.search(said):
        return "negative"
    if re.search(r"(?<![가-힣])아직", said) or _ENDPOINT_CONDITION.search(said):
        return "unread"
    return "positive"


def _observation_predicate(step_label: str, transcript: str) -> str | None:
    """Classify only explicit source-defined endpoint observations.

    The phrases below do not invent a vision result: they preserve a user's
    report and bind it to the source endpoint for the current step. Negative
    patterns and question guards run first so phrases such as "not transparent"
    or questions like "투명한가요?" never become a positive result by substring overlap.
    A question, a hedge, or the source line read back is no report at all, and
    a negation after a positive phrase makes it a negative one.
    """
    key = _semantic_utterance_key(transcript)
    if step_label in {"7", "9", "20"} and (
        _reply_withholds_assent(transcript)
        or _SOURCE_RECITATION.search(" ".join(str(transcript).casefold().split()))
    ):
        return None
    if step_label in {"7"}:
        if re.search(
            r"(?:투명한가요|투명한가\??|투명해져야\s*(?:하나요|해요|돼)|"
            r"투명해지면\s*어떻게|is\s+it\s+transparent|should\s+it\s+be\s+transparent|"
            r"투명해졌다고\s*말하면|탈색된\s*건가요|탈색된다는\s*(?:게|건)|"
            r"의미|뜻|무슨|왜|알려|설명|meaning|what\s+does|explain)\??",
            key,
        ):
            return None
        if re.search(
            r"(?:아직.*(?:색|색깔|염색|탈색).*(?:남아|안|있어|덜|남았)|"
            r"투명하지\s*않|완전히\s*탈색되지\s*않|완전히\s*탈색된\s*건\s*아닌|"
            r"still.*(?:stain|color)|not\s+(?:fully\s+)?(?:destained|transparent))",
            key,
        ):
            return "negative"
        if positive := re.search(
            r"(?:완전히\s*탈색(?:됐|되었|되어|됐어|됐습니다|된|돼서)?|"
            r"젤(?:이|은|이\s*(?:이제\s*)?)?\s*(?:이제\s*)?(?:완전히\s*)?투명(?:해|합니다|해졌어|해요|해졌습니다)|"
            r"색(?:이|은)?\s*(?:완전히\s*)?(?:빠졌|빠졌어|빠졌습니다)|"
            r"fully\s+destained|gel\s+is\s+(?:now\s+)?transparent|color\s+is\s+(?:now\s+)?gone)",
            key,
        ) or _STEP_7_SPOKEN_ENDPOINT.search(key):
            # The negation right after the frame is the reading these frames
            # always had; the whole-utterance reading below was added for the
            # spoken wordings and then given to these too, since a negation
            # before the frame ("탈색이 안 됐어 완전히 탈색됐어"), "아직" or a
            # wish ("완전히 탈색되면") passed them as the endpoint reached.
            if _frame_negated(key, positive):
                return "negative"
            reading = _endpoint_report_reading(key)
            return None if reading == "unread" else reading
    if step_label in {"9", "20"}:
        if re.search(
            r"(?:흰색인가요|탈수된\s*건가요|is\s+it\s+white|is\s+it\s+dehydrated)\??",
            key,
        ):
            return None
        if re.search(
            r"(?:아직.*(?:투명|젖어|수분|건조)|흰색이\s*아니|"
            r"not\s+(?:white|whitish|dehydrated|dry)|still\s+(?:clear|wet))",
            key,
        ):
            return "negative"
        if positive := re.search(
            r"(?:흰색(?:으로\s*변했|이\s*됐|이야|입니다|으로\s*바뀌|으로\s*변함)|"
            r"탈수(?:됐|되었|됐어|됐습니다)|완전히\s*말랐|"
            r"탈수(?:를|가)?\s*완료\s*(?:했|하였)" + _STATEMENT_END + r"|"
            r"(?:turned|is)\s+(?:white|whitish)|fully\s+(?:dehydrated|dry))",
            key,
        ):
            # Read as a whole as at step 7: "안 됐어 흰색이야", "흰색이야
            # 아직" and "흰색이 됐으면 좋겠어" passed as the endpoint reached.
            if _frame_negated(key, positive):
                return "negative"
            reading = _endpoint_report_reading(key)
            return None if reading == "unread" else reading
    return None
_NON_MUTATING_COMPLETION = (
    ("completion_criteria_question", re.compile(
        r"(?:완료|끝)(?:\s*조건|하려면|이라는\s*건)|"
        r"(?:condition|criteria).*(?:complete|finish)"
    )),
    ("future_completion", re.compile(
        r"(?:완료|끝|마칠)\s*(?:할게|할\s*거야|하겠|예정|하려고|하려\s*함)"
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
    # F8 (lane R part 2-b): "한 번 더 말해 줄래요", "뭐라고?", "방금 거 다시".
    # Whole utterances only: "뭐라고 써 있어?" asks what is written, and stays
    # a question.
    re.compile(
        r"^(?:한\s*번\s*더|다시\s*한\s*번)\s*(?:말|얘기|설명|안내)\s*(?:해|하)?\s*"
        r"(?:줘|줘요|줄래|줄래요|주세요|줄\s*수\s*있어(?:요)?)?$"
    ),
    re.compile(r"^뭐라고(?:요|\s*(?:했어|했어요|하셨어요|하셨죠|그랬어|그랬어요))?$"),
    re.compile(
        r"^(?:방금|아까)\s*(?:거|것|말|답|답변|안내)?\s*다시"
        r"(?:\s*(?:말해|들려|해)\s*(?:줘|줘요|줄래|줄래요|주세요)?)?$"
    ),
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
    re.compile(r"(?:이미지|사진|그림|삽화|시각\s*자료|구조|화학\s*구조).*(?:보여|찾아|만들|설명)"),
    re.compile(r"(?:보여|찾아|만들|설명).*(?:이미지|사진|그림|삽화|시각\s*자료|구조|화학\s*구조)"),
    re.compile(r"(?:show|find|make|generate).*(?:image|photo|illustration|visual|structure|chemical\s+structure)"),
    re.compile(r"(?:structure|chemical\s+structure).*(?:show|find|view|display)"),
)
_WEB_VISUAL_REQUEST_PATTERNS = (
    re.compile(r"(?:원본|실제|인터넷|웹).*(?:사진|이미지).*(?:보여|찾아)"),
    re.compile(r"(?:find|show).*(?:real|web|source).*(?:photo|image)"),
)
_TERM_QUESTION_PATTERNS = (
    (re.compile(r"(?<![a-z0-9])ambic(?![a-z0-9])|ammonium\s+bicarbonate|암빅|엠빅|암비크", re.I), "ambic"),
    (re.compile(r"hplc\s*(?:grade\s*)?water|hplc\s*워터|hplc\s*물|에이치\s*피\s*엘\s*씨\s*워터|에이치피엘씨\s*워터", re.I), "hplc_water"),
    (re.compile(r"solution\s*a|용액\s*a|용액\s*에이|a\s*용액|솔루션\s*a|솔루션\s*에이", re.I), "solution_a"),
    (re.compile(r"solution\s*b|용액\s*b|용액\s*비|b\s*용액|솔루션\s*b|솔루션\s*비", re.I), "solution_b"),
    (re.compile(r"acetonitrile|아세토니트릴|아세토나이트릴", re.I), "acetonitrile"),
    (re.compile(r"gel\s*plug|젤\s*플러그|제트\s*플러그|젤\s*플럭", re.I), "gel_plug"),
    (re.compile(r"stained\s+protein\s+band|염색된\s*단백질\s*(?:밴드|뱀드|뱄드|밸드|밴트)|단백질\s*(?:밴드|뱀드|뱄드|밸드|밴트)|(?:염색된\s*)?단백질\s*(?:겔|젤)\s*밴드|gel\s*band|(?:젤|겔)\s*(?:밴드|뱀드|뱄드|밸드|밴트)|단백질\s*밴드|염색된\s*밴드", re.I), "stained_protein_band"),
    (re.compile(r"(?<![a-z0-9])(?:tube|microcentrifuge\s*tube|eppendorf|ep\s*tube)(?![a-z0-9])|튜브|마이크로센트리퓨즈\s*튜브|반응\s*튜브|원심분리\s*관|에펜도르프\s*튜브", re.I), "tube"),
    (re.compile(r"(?<![a-z0-9])dtt(?![a-z0-9])|dithiothreitol|디티오트레이톨|디티티", re.I), "dtt"),
    (re.compile(r"iodoacetamide|요오도아세트아미드|아이오도아세트아마이드|아이오도아세트아미드|이오도아세트아미드", re.I), "iodoacetamide"),
    (re.compile(r"trypsin|트립신|트립씬", re.I), "trypsin"),
    (re.compile(r"formic\s*acid|포름산|폼산", re.I), "formic_acid"),
    (re.compile(r"(?<![a-z0-9])rpm(?![a-z0-9])|분당\s*회전", re.I), "rpm"),
    (re.compile(r"incubat(?:e|ion)|배양", re.I), "incubation"),
    (re.compile(r"contamination|오염|케라틴", re.I), "contamination"),
    (re.compile(r"(?<![a-z0-9])sds[\s_-]*page(?![a-z0-9])|에스디에스[\s_-]*페이지|단백질\s*전기영동|겔\s*전기영동|polyacrylamide\s+gel\s+electrophoresis", re.I), "sds_page"),
    (re.compile(r"electrophoresis|전기영동", re.I), "electrophoresis"),
    (re.compile(r"coomassie(?:\s*blue)?|쿠마시(?:\s*블루)?|쿠마씨", re.I), "coomassie"),
    (re.compile(r"destain(?:ing)?|탈색", re.I), "destaining"),
    (re.compile(r"centrifuge|원심분리기|원심분리", re.I), "centrifuge"),
    (re.compile(r"pipette|파이펫|피펫", re.I), "pipette"),
    (re.compile(r"mass\s*spectrometr(?:y|ic)|질량분석", re.I), "mass_spectrometry"),
)
_AGENT_META_PATTERNS = (
    re.compile(r"(?:이\s*)?(?:에이전트|보이스\s*에이전트|너|네|당신|ai|시스템)\s*(?:의)?\s*(?:목적|역할|목표).*(?:뭐|무엇|알려|설명|있어)"),
    re.compile(r"(?:이\s*)?(?:에이전트|보이스\s*에이전트|너|네|당신|ai|시스템)\s*(?:는|가|는\s*대체)?\s*(?:하는\s*기능|무슨\s*기능|어떤\s*기능|주요\s*기능|무슨\s*일|어떤\s*일|기능이|역할이).*(?:뭐|무엇|알려|설명|있어|해)"),
    re.compile(r"^(?:너|네|당신|에이전트|이\s*시스템)(?:는)?\s*(?:뭐\s*(?:할\s*수\s*있어|하는\s*(?:거야|애야|에이전트야|일이야|로봇이야|것이야)|해)|무슨\s*(?:기능이\s*있어|일을\s*해)|어떤\s*역할을\s*해)\??$"),
    re.compile(r"^(?:하는\s*기능(?:이|을)?|무슨\s*기능(?:이|을)?|어떤\s*기능(?:이|을)?|주요\s*기능(?:이|을)?|기능(?:이|을)?|역할(?:이|을)?)\s*(?:뭐야?|무엇|알려줘|설명해줘|소개해줘|있어)\??$"),
    re.compile(r"(?:에이전트|시스템|너|네|당신).*(?:기능|역할|목적|능력|소개).*(?:뭐|무엇|알려|설명|소개)"),
    re.compile(r"(?:what\s+(?:is|are|'s|’s)\s+(?:the\s+)?(?:purpose|function|functions|role|roles|capabilities|features?)\s+(?:of\s+this\s+agent|of\s+you|of\s+the\s+agent)?|what\s+(?:can|do|does)\s+(?:this\s+agent|you|the\s+agent)\s+do|what\s+are\s+(?:your\s+)?(?:capabilities|functions|roles)|what\s+are\s+you(?:\s+for)?|what(?:'s|’s|\s+is|\s+are)\s+(?:your\s+)?(?:function|functions|role|roles|purpose|capability|capabilities))\??$", re.I),
    re.compile(r"(?:tell\s+me\s+(?:about\s+)?(?:yourself|your\s+(?:function|functions|role|roles|capabilities|purpose))|explain\s+(?:your|the)\s+(?:function|functions|role|roles|capabilities|purpose)|how\s+can\s+you\s+help(?:\s+me)?|who\s+are\s+you.*)\??$", re.I),
)
_START_COMMAND_PATTERNS = (
    re.compile(
        r"^(?:그러면|그럼|자|이제|네|응|그래|음|자\s*그럼)?,?\s*(?:"
        r"(?:실험|프로토콜|절차)(?:을|를|은|는)?\s*(?:시작\s*해(?:\s*(?:줘|주세요|줄래|줄\s*수\s*있어))?|시작\s*하자|시작\s*할게(?:요)?|시작\s*하겠습니다|시작\s*부탁해|시작|진행\s*하자|진행\s*해(?:\s*(?:줘|주세요))?)"
        r"|"
        r"(?:시작\s*해(?:\s*(?:줘|주세요|줄래|줄\s*수\s*있어))?|시작\s*하자|시작\s*할게(?:요)?|시작\s*하겠습니다|시작\s*부탁해|시작|진행\s*하자|진행\s*해\s*(?:줘|주세요|줄래)|1단계부터\s*(?:시작하자|시작\s*해(?:\s*줘)?|하자|진행하자)|1단계\s*(?:시작\s*해(?:\s*줘)?|시작\s*하자|시작))"
        r")$",
        re.I,
    ),
    re.compile(
        r"^(?:(?:so|then|well|now|okay|ok|yes),?\s*)?(?:start(?:\s+it)?|yes,?\s*start|start\s+(?:the\s+)?(?:protocol|experiment)|"
        r"begin\s+(?:the\s+)?(?:protocol|experiment)|let(?:'|’)?s\s+start)$",
        re.I,
    ),
)
_HANDOFF_PATTERNS = (
    re.compile(r"(?:교수님|교수|안전관리자|관리자|지도교수).*(?:보고서|이상사항|기록|이메일).*(?:보내|전송|인계|전달)"),
    re.compile(r"(?:보고서|이상사항|기록).*(?:교수님|교수|안전관리자|관리자|지도교수).*(?:보내|전송|인계|전달)"),
    re.compile(r"^(?:(?:교수님|안전관리자|관리자)에게\s*)?(?:보고서|기록)\s*(?:보내줘|전송해줘|인계해줘)$"),
    re.compile(r"(?:send|email|forward|handoff)\s+(?:the\s+)?(?:report|anomaly|summary)\s+to\s+(?:the\s+)?(?:professor|advisor|safety\s+officer|manager)", re.I),
)
_UNDERSPECIFIED_RESULT_PATTERNS = (
    re.compile(r"^(?:그\s*)?(?:실험\s*)?결과(?:가|를|는)?\s*(?:알려줘|말해줘|알려\s*줘|보여줘|어떻게\s*돼|어때|뭐야)\??$"),
    re.compile(r"^(?:지금\s*)?(?:나온\s*)?(?:실험\s*)?결과(?:가|를|는)?\s*(?:알려줘|보여줘|말해줘|어떻게\s*돼)\??$"),
    re.compile(r"^(?:tell\s+me\s+the\s+result|what\s+is\s+the\s+result|what\s+was\s+the\s+result|show\s+me\s+the\s+result)\??$", re.I),
)
_PAUSE_PATTERNS = (
    re.compile(r"(?:잠깐|잠시)?\s*(?:실험|프로토콜|안내)?\s*(?:일시\s*중지|일시\s*정지|멈춰|잠깐\s*멈|잠시\s*멈|나갔다\s*올게|나가\s*있을게)"),
    # "그만" and "정지" said on their own pause, as "멈춰" does: the place in
    # the protocol is kept. Ending the session takes "종료".
    re.compile(r"^(?:그만|정지)(?:해(?:줘|요)?)?$"),
    re.compile(r"^(?:pause(?:\s+(?:the\s+)?(?:protocol|experiment))?|take\s+a\s+break|hold\s+on)$", re.I),
)
#: The pause words decided on 2026-10-02, read ahead of every other rule: a
#: pause that is missed, or waits on a slower reading, is a bench hazard.
#: Matched whole against the utterance key, so a sentence that merely
#: contains one of them ("정지 버튼이 뭐야") is not a pause. "중지" and "stop"
#: pause; ending the session takes "종료" (_END_COMMAND).
_FRONT_PAUSE_COMMAND = re.compile(
    r"(?:(?:잠깐|잠깐만|잠시|잠시만)\s+)?"
    r"(?:(?:실험|프로토콜|안내|절차)(?:을|를)?\s*)?"
    r"(?:잠깐(?:만(?:요|예)?)?|잠시만(?:요)?|멈춰|멈처|정지|일시\s*정지|일시\s*중지|"
    r"중지|중단|스톱|stop|pause|"
    # "쫌만 기다려 봐", "잠깐 기다려 줘" (lane R part 2-b).
    r"(?:(?:쫌|좀|조금|잠깐|잠시)\s*만?\s*)?기다려)"
    r"(?:\s*(?:봐|봐요|줘|줘요|요|해|해요|해줘|해\s*줘|주세요|해\s*주세요))?",
    re.I,
)
#: Wanting to stop without saying "종료": the session pauses and says how to
#: end it, instead of ending on a word that never asked for that.
_END_REQUEST_WITHOUT_END_WORD = re.compile(
    r"(?:(?:오늘은|오늘|이제|그럼|자)\s*)*(?:여기서\s*)?"
    r"(?:(?:실험|프로토콜)(?:을|를|은|는)?\s*)?"
    r"(?:그만(?:할래|할래요|할게|할게요|하자|합시다|하겠습니다|둘래|둘게)|"
    r"끝낼게(?:요)?|끝낼래(?:요)?|끝내자|끝냅시다|끝내겠습니다)"
    r"|(?:오늘은\s*)?여기까지(?:\s*(?:할게|할게요|하자|합시다|만\s*할게))?"
    r"|stop\s+(?:the\s+)?(?:protocol|experiment)|(?:i(?:'|’)ll\s+|let(?:'|’)s\s+)?stop\s+here",
    re.I,
)
#: The commands that end the session: "종료" said, or "end session". They are
#: asked about once ("실험을 종료할까요?") before anything ends.
_END_COMMAND = re.compile(
    r"(?:(?:오늘은|오늘|이제|그럼)\s*)*(?:여기서\s*)?"
    r"(?:(?:실험|프로토콜|세션|절차)(?:을|를)?\s*)?종료"
    r"(?:\s*(?:해|해요|해줘|해\s*줘|할게|할게요|하자|합시다|할래|하겠습니다))?"
    r"|end\s+(?:the\s+)?(?:session|experiment|protocol)",
    re.I,
)
#: A question about pausing or ending ("멈춰도 돼?", "중지해야 돼?") is not
#: the command. Read on the transcript as spoken, since the key drops "?".
_CONTROL_QUESTION = re.compile(
    r"[?？]|(?:도|면)\s*(?:돼|되|될|괜찮)|해야|할까|나요|(?:되|돼)\s*(?:나|니)"
)
END_CONFIRMATION_QUESTION = {
    "ko": "실험을 종료할까요?",
    "en": "Do you want to end the experiment?",
}
END_REQUEST_HINT = {
    "ko": "실험을 끝내려면 '실험 종료'라고 말씀해 주세요.",
    "en": "To end the experiment, say 'end session'.",
}
#: The marks the server puts on an explanation the PDF does not give
#: (decision D4): on the screen above it, and said before it.
OUTSIDE_PDF_NOTICE = {"ko": "PDF 밖 설명이니 유의", "en": "Not from the PDF -- take care"}
OUTSIDE_PDF_SPOKEN_LEAD = {
    "ko": "PDF에는 따로 설명이 없어요.",
    "en": "The PDF does not explain this.",
}
#: Said when a model answer was dropped by a check and the rules had none.
ANSWER_NOT_CONFIRMED = {
    "ko": "PDF에서 확인할 수 없어요.",
    "en": "I could not confirm that in the PDF.",
}
#: A start said after the experiment ended (decision 2, 2026-10-03): an ended
#: experiment is never started again by voice; the next one is chosen on the
#: screen.
EXPERIMENT_ENDED_START_REPLY = {
    "ko": "이 실험은 이미 끝났어요. 다음 실험은 화면에서 프로토콜이나 세션을 골라 시작해 주세요.",
    "en": (
        "This experiment has already ended. To run the next one, choose a "
        "protocol or session on the screen and start it there."
    ),
}


def _completion_question(step_label: str) -> str:
    """The completion question, naming the step it asks about."""

    return f"{step_label}단계 완료하셨나요?"


def _front_control_intent(transcript: str, language: str) -> CuratedControlIntent | None:
    """Pause, end-request and end commands, read before any other rule."""

    key = _utterance_key(transcript)
    if not key or _CONTROL_QUESTION.search(" ".join(transcript.casefold().split())):
        return None
    if _FRONT_PAUSE_COMMAND.fullmatch(key):
        return CuratedControlIntent(
            intent_kind="pause_workflow", action=CuratedProtocolAction.PAUSE,
            language=language, normalized_transcript=key,
        )
    if _END_REQUEST_WITHOUT_END_WORD.fullmatch(key):
        return CuratedControlIntent(
            intent_kind="end_request_paused", action=CuratedProtocolAction.PAUSE,
            language=language, normalized_transcript=key,
        )
    if _END_COMMAND.fullmatch(key):
        return CuratedControlIntent(
            intent_kind="workflow_command", action=CuratedProtocolAction.STOP,
            language=language, allows_state_mutation=True,
            normalized_transcript=key,
        )
    return None


#: The front rules: the turns the server plans by rule without waiting on a
#: model (lane R design §1-2, decisions D2, D3, D5, D9 of 2026-10-02). The
#: names are the ones the lane R evaluation set uses. F1, the emergency gate,
#: runs in server.py before any planning and is not listed here.
FRONT_RULES: dict[str, str] = {
    "stt_unreliable": "F2 the transcript cannot be trusted: provider quality, "
                      "a reply in another language, a word that needs repair",
    "pause": "F3 a pause word, or wanting to stop without saying 종료",
    "end_command": "F3 a command with 종료 in it, asked about once (D5)",
    "yes_no_open_question": "F4 a yes or no to an open completion, end or "
                            "transcript question",
    "observation_reply": "F5 any reply while an endpoint question is open, an "
                         "endpoint stated at a repeat-until step with no "
                         "question open (D9), and the reply to a note or "
                         "problem the server is waiting on",
    "timer_remaining": "F6 the step timer's time left, from the server clock",
    "coreference_clarify": "F7 '그거' and other references asked back (D3)",
    "repeat_last_reply": "F8 say it again, or the sound did not play",
    "cancel_background_job": "F9 cancel a read-only lookup",
    "targeted_completion": "a completion that names the current step (D2)",
    "start_command": "an explicit start of an experiment that has never "
                     "started, and any start or resume after it ended, "
                     "which is not restarted by voice (decision 2, 2026-10-03)",
}

#: The front rule an action the rules read belongs to, whatever its wording.
_FRONT_RULE_BY_ACTION: dict[CuratedProtocolAction, str] = {
    CuratedProtocolAction.TRANSCRIPT_UNRELIABLE: "stt_unreliable",
    CuratedProtocolAction.PAUSE: "pause",
    CuratedProtocolAction.STOP: "end_command",
    CuratedProtocolAction.TIMER_STATUS: "timer_remaining",
    CuratedProtocolAction.CLARIFY_REFERENCE: "coreference_clarify",
    CuratedProtocolAction.REPEAT: "repeat_last_reply",
    CuratedProtocolAction.AUDIO_RECOVERY: "repeat_last_reply",
    CuratedProtocolAction.CANCEL_READONLY: "cancel_background_job",
}


@dataclass(frozen=True)
class _OpenQuestions:
    """Which one-turn server questions the turn being planned can answer."""

    completion: bool
    observation: bool
    transcript: bool
    note: bool
    stop: bool
    timer: bool
    anomaly: bool = False

    @property
    def first_open(self) -> str | None:
        """The name of the question this turn can answer, or None."""

        for name in (
            "completion", "observation", "transcript", "note", "stop", "timer", "anomaly",
        ):
            if getattr(self, name):
                return name
        return None


@dataclass(frozen=True)
class AppliedToolProposal:
    """A turn's tool proposals as the server ruled on them, and what it did."""

    verdict: ProposalVerdict
    plan: CuratedProtocolTurnPlan


def _duration_words(seconds: int, language: str) -> str:
    """A timer length as said aloud: "15분", "1분 30초", "45초"."""

    minutes, rest = divmod(int(seconds), 60)
    if language == "ko":
        if minutes and rest:
            return f"{minutes}분 {rest}초"
        return f"{minutes}분" if minutes else f"{rest}초"
    if minutes and rest:
        return f"{minutes} min {rest} s"
    return f"{minutes} min" if minutes else f"{rest} s"


def _with_ro(word: str) -> str:
    """``word`` with 으로/로, as its last syllable takes it ("15분으로", "45초로")."""

    last = word[-1]
    if "가" <= last <= "힣":
        final = (ord(last) - ord("가")) % 28
        return f"{word}{'로' if final in (0, 8) else '으로'}"
    return f"{word}로"


_RESUME_PATTERNS = (
    re.compile(r"(?:다시\s*(?:시작|진행)|재개|계속\s*(?:하자|할게|해줘)|계속\s*진행)"),
    re.compile(r"^(?:resume(?:\s+(?:the\s+)?(?:protocol|experiment))?|continue(?:\s+the\s+protocol)?)$", re.I),
)
_TIMER_START_PATTERNS = (
    re.compile(r"(?:타이머|시간\s*측정|배양\s*시간).*(?:시작|재기\s*시작|재줘|틀어)"),
    re.compile(r"^(?:지금\s*)?시작했어$"),
    re.compile(r"^(?:지금\s*)?timer\s*(?:를|을)?\s+시작(?:\s*해(?:줘|줄래)?)?$", re.I),
    re.compile(r"^(?:start\s+(?:the\s+)?timer|timer\s+start)$", re.I),
)
_TIMER_QUERY_PATTERNS = (
    re.compile(r"타이머.*(?:얼마나|몇\s*분|몇\s*초|남았|상태|어떻게)"),
    re.compile(r"^타임\s*(?:얼마나\s*남았어|몇\s*(?:분|초)\s*남았어|남은\s*시간\s*알려\s*(?:줘|주세요))$"),
    re.compile(r"^(?:몇\s*분\s*남았어|얼마나\s*남았어)\??$"),
    # F6 (lane R part 2-b): "시간 얼마나 남았지", "Time 얼마나 남았어?". Whole
    # utterances only: "시간이 얼마나 걸려?" asks the protocol, not the timer.
    re.compile(
        r"^(?:시간|타임|time)\s*(?:이|은)?\s*(?:얼마나|몇\s*분|몇\s*초)\s*(?:더\s*)?"
        r"남았(?:지|어|어요|나|나요|니|습니까)?$",
        re.I,
    ),
    re.compile(r"^(?:how\s+much\s+time\s+(?:is\s+)?left|timer\s+status|how\s+long\s+remaining)\??$", re.I),
)
_PREVIEW_STEP_PATTERNS = (
    re.compile(r"^(?:(?P<label>[1-9][0-9]?)\s*단계|step\s*(?P<en>[1-9][0-9]?))\s*(?:미리\s*알려줘|미리보기|미리\s*설명|예습)$", re.I),
    re.compile(r"^(?:preview\s+step\s*(?P<pen>[1-9][0-9]?))$", re.I),
)

_KOREAN_STEP_NUMBERS = {
    "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9, "십": 10,
    "십일": 11, "십이": 12, "십삼": 13, "십사": 14, "십오": 15, "십육": 16, "십칠": 17,
    "십팔": 18, "십구": 19, "이십": 20, "이십일": 21, "이십이": 22, "이십삼": 23, "이십사": 24, "이십오": 25,
    "첫": 1, "첫번째": 1, "첫_번째": 1,
}

_ENGLISH_STEP_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "twenty-one": 21, "twenty-two": 22, "twenty-three": 23,
    "twenty-four": 24, "twenty-five": 25,
}


def _parse_step_number_or_label(token: str, current_step: int, max_step: int) -> int | None:
    t = " ".join(token.casefold().split())
    if t in {"이", "현재", "지금", "this", "current", "here"}:
        return current_step
    if t in {"마지막", "끝", "최종", "last", "final", "end"}:
        return max_step
    num_match = re.match(r"^(\d+)", t)
    if num_match:
        val = int(num_match.group(1))
        return val if 1 <= val <= max_step else None
    if t in _KOREAN_STEP_NUMBERS:
        return _KOREAN_STEP_NUMBERS[t]
    if t in _ENGLISH_STEP_NUMBERS:
        return _ENGLISH_STEP_NUMBERS[t]
    return None


def _extract_step_range(text: str, current_step: int = 1, max_step: int = 25) -> tuple[int, int] | None:
    raw = text.casefold().strip()
    patterns = (
        r"(?:(?:step|단계)\s*)?(?P<start>\d+|이|현재|지금|this|current|[가-힣]+)\s*(?:단계)?\s*(?:부터|에서)\s*(?:(?:step|단계)\s*)?(?P<end>\d+|마지막|끝|최종|last|final|[가-힣]+)\s*(?:단계)?\s*(?:까지)?",
        r"(?P<start>\d+|[가-힣]+)\s*(?:에서|~|-)\s*(?P<end>\d+|[가-힣]+)\s*단계",
        r"from\s+(?:steps?\s+)?(?P<start>\d+|this|current|[a-z]+)\s+(?:through|to)\s+(?:steps?\s+)?(?P<end>\d+|last|final|[a-z]+)",
        r"steps?\s+(?P<start>\d+|[a-z]+)\s+(?:through|to)\s+(?P<end>\d+|[a-z]+)",
    )
    for pattern in patterns:
        for m in re.finditer(pattern, raw):
            start_tok = m.group("start")
            end_tok = m.group("end")
            start_val = _parse_step_number_or_label(start_tok, current_step, max_step)
            end_val = _parse_step_number_or_label(end_tok, current_step, max_step)
            if start_val is not None and end_val is not None and 1 <= start_val <= end_val <= max_step:
                return (start_val, end_val)
    return None
_PARAMETER_RATIONALE_PATTERNS = (
    re.compile(r"(?:근거|이유|기준|배경).*(?:어디|뭐|무엇|어디서|왜)"),
    re.compile(r"(?:왜|어째서).*(?:기준|값|조건|온도|교반|rpm|시간|부피).*(?:잡았|정했|선택|설정)"),
    re.compile(r"(?:what\s+is\s+the\s+basis|why\s+(?:was|were)\s+this|why\s+(?:is|are)\s+the\s+condition)", re.I),
)
_TERM_QUESTION_DIMENSIONS = frozenset({
    "뭐", "무엇", "물질", "성분", "구성", "차이", "왜", "역할", "준비",
    "만들", "일반 물", "증류수", "위험", "주의", "안전", "알려", "대해", "대해서", "설명",
    "어떤", "뜻", "의미", "튜브", "용기", "플러그", "밴드",
    "what", "which", "define", "difference", "why", "role", "contain",
    "prepare", "hazard", "safe", "explain", "meaning", "structure",
})
_REPORT_REQUEST_PATTERNS = (
    re.compile(r"(?:현재\s*)?(?:실험\s*)?(?:기록|보고서).*(?:보여|열어|내보내|export)"),
    re.compile(r"(?:show|export|open).*(?:experiment\s*)?(?:report|record)"),
)
_OBSERVATION_COMMAND_PATTERNS = (
    re.compile(
        r"^(?:관찰(?:\s*(?:사항|결과))?|메모|노트)(?:를|을)?\s*"
        r"(?:기록|추가|남겨)(?:해\s*줘|해줘|해|할게|합니다)?"
        r"(?:\s*[:：]\s*|\s+)?(?P<content>.*)$"
    ),
    re.compile(
        r"^(?:record|add|log)\s+(?:an?\s+)?(?:observation|note)"
        r"(?:\s+that|\s*[:：])?\s*(?P<content>.*)$",
        re.I,
    ),
)
_APPEARANCE_OBSERVATION = re.compile(
    r"(?:(?:시료|샘플).*(?:평소와|처음과|예상과)?\s*(?:다르게|달라|다르|변해|변했)|"
    r"(?:sample|specimen).*(?:looks?|appears?)\s+different)",
    re.I,
)


def _observation_capture(transcript: str) -> tuple[str, str | None] | None:
    key = _semantic_utterance_key(transcript)
    for pattern in _OBSERVATION_COMMAND_PATTERNS:
        if match := pattern.fullmatch(key):
            content = (match.groupdict().get("content") or "").strip(" .,:;：")
            return "note", content or None
    if _APPEARANCE_OBSERVATION.search(key):
        return "appearance", transcript.strip()[:4000]
    return None


_ANOMALY_PATTERNS = (
    (re.compile(r"(?:이상\s*(?:상황|현상|발생|사항|있어|생겼)|문제가\s*(?:생겼|발생|있어)|뭔가\s*이상|실수가\s*있었|something\s+went\s+wrong|there\s+is\s+an\s+issue|anomaly|abnormal)"), "protocol_block"),
    (re.compile(r"(?:용액|시약).*(?:잘못|틀리게).*(?:넣|준비)"), "reagent_preparation_issue"),
    (re.compile(r"(?:시료|샘플).*(?:흘렸|쏟았|묻었|떨어뜨)"), "sample_deviation"),
    (re.compile(
        r"(?:색|색깔|투명|침전|결과|외관|용액).*(?:남아|이상|다르|예상과\s*다르|변했|변형됐|바뀌|변경|탁해|흐려|달라|"
        r"something\s+seems\s+wrong|looks\s+different|changed\s+unexpectedly|turned\s+(?:yellow|brown|cloudy))"
    ), "protocol_block"),
    (re.compile(r"(?:갑자기).*(?:색|색깔|변했|바뀌|변경|이상|탁해|거품|연기|냄새)"), "protocol_block"),
    (re.compile(
        r"(?:오염(?:된\s*것\s*같|됐|된\s*것이\s*보|을\s*발견)|"
        r"(?:sample|시료).*(?:contaminated|오염됐))"
    ), "contamination_concern"),
    (re.compile(r"(?:장비|기기|thermomixer|교반기|speedvac).*(?:멈췄|고장|이상|안\s*돌아가|이상한\s*소리)"), "equipment_issue"),
    (re.compile(r"(?:타이머|시간|온도).*(?:끝|지났|벗어|이상|너무\s*높|너무\s*낮)"), "timing_temperature_deviation"),
    (re.compile(r"(?:노출|spill|쏟|누출|피부|눈에|튀었)"), "spill_exposure_safety_event"),
    (re.compile(r"(?:깨졌|부서졌|금이\s*갔|터졌)"), "sample_deviation"),
)


def _anomaly_category(key: str) -> str:
    """The anomaly reader's category for an utterance, or the catch-all."""

    return next(
        (category for pattern, category in _ANOMALY_PATTERNS if pattern.search(key)),
        "protocol_block",
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
# Only "종료" ends the session (2026-10-02). "중단", "중지" and "stop" pause,
# and "그만할래", "여기서 끝낼게", "stop here" pause and say how to end; all of
# them are read first, by _front_control_intent.
_NATURAL_STOP_PATTERNS = (
    re.compile(r"^(?:프로토콜(?:을)?\s*)?종료(?:해\s*줘|할게|할게요|하겠습니다)?$"),
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
    r"^(?:(?P<ko>[1-9][0-9]?)\s*단계|step\s*(?P<en>[1-9][0-9]?))"
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

_POLITE_VERB_ENDINGS = (
    r"(?:해\s*줘|해줘|해\s*줄래|해줄래|해\s*주세요|해주세요|해\s*줄\s*수\s*있어\??|해줄\s*수\s*있어\??|"
    r"해\s*줄\s*수\s*있나요\??|해줄\s*수\s*있나요\??|해\s*줄\s*수\s*있으세요\??|"
    r"하자|하죠|할까\??|할래\??|해도\s*돼\??|해도\s*될까\??|해도\s*되나요\??|"
    r"가\s*줘|가줘|갈래\??|갈까\??|가도\s*돼\??|가도\s*될까\??|가자|가죠|가주세요|"
    r"가능해\??|가능할까\??|가능한가요\??|가능합니까\??|부탁해|부탁해요)"
)

_NAVIGATION_PATTERNS = (
    re.compile(
        r"^(?:(?:이제|그럼|자|혹시)\s*)?"
        r"(?:다음(?:으로)?|다음\s*(?:단계|스텝)(?:에\s*대해서|에\s*대해|에|로)?)\s*"
        r"(?:"
        r"(?:안내|진행|이동)"
        r"(?:\s*(?:해\s*줘|해줘|해\s*줄래|해줄래|해\s*주세요|해주세요|해\s*줄\s*수\s*있어\??|해줄\s*수\s*있어\??|"
        r"해\s*줄\s*수\s*있나요\??|해줄\s*수\s*있나요\??|해\s*줄\s*수\s*있으세요\??|"
        r"하자|하죠|할까\??|할래\??|해도\s*돼\??|해도\s*될까\??|해도\s*되나요\??|가능해\??|가능할까\??|가능한가요\??|부탁해|부탁해요))?|"
        r"(?:넘어\s*가|넘어가)"
        r"(?:자|죠|줘|줄래|주세요|도\s*돼\??|도\s*될까\??|도\s*되나요\??|요)?"
        r"|"
        r"(?:넘어\s*갈|넘어갈|갈)"
        r"(?:\s*(?:까\??|래\??|수\s*있어\??|수\s*있나요\??|수\s*있으세요\??))|"
        r"(?:가)"
        r"(?:자|죠|줘|줄래|주세요|도\s*돼\??|도\s*될까\??|도\s*되나요\??|요)?"
        r")\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:(?:이제|그럼|자)\s*)?"
        r"(?:다음(?:으로)?|다음\s*(?:단계|스텝)(?:에\s*대해서|에\s*대해|에|로)?)\s*"
        r"(?:해\s*줘|해줘|해\s*줄래|해줄래|해\s*주세요|해주세요|해\s*줄\s*수\s*있어\??|해줄\s*수\s*있어\??|"
        r"해\s*줄\s*수\s*있나요\??|해줄\s*수\s*있나요\??|하자|하죠|할까\??|할래\??|해도\s*돼\??|해도\s*될까\??|해도\s*되나요\??|"
        r"가\s*줘|가줘|갈래\??|갈까\??|가도\s*돼\??|가도\s*될까\??|가자|가죠|가주세요|"
        r"가능해\??|가능할까\??|가능한가요\??|부탁해|부탁해요)\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:please\s+)?(?:guide\s+me\s+to|proceed\s+to|move\s+to|go\s+to|can\s+you\s+(?:guide|proceed|move|go)\s+to)\s+"
        r"(?:the\s+)?next\s+step\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:let'?s\s+)?(?:move|proceed|go)\s+(?:on\s+)?to\s+(?:the\s+)?next\s*(?:step)?\??$",
        re.IGNORECASE,
    ),
)
_NEXT_INFORMATION_PATTERNS = (
    re.compile(
        r"^(?:(?:이제|그럼|혹시)\s*)?"
        r"(?:다음\s*(?:단계|스텝)|next\s*step)"
        r"(?:(?:는|가|에\s*대해|에\s*대해서|의|은)?\s*)"
        r"(?:(?:내용(?:만)?\s*)?(?:미리\s*)?"
        r"(?:뭐야|무엇|어떤\s*(?:단계(?:야)?|거야?|것이야?|내용이야?|작업이야?)|"
        r"(?:뭘|무엇을?|뭐)\s*(?:하는\s*(?:단계(?:야)?|거(?:야)?|것(?:이야)?|일(?:이야)?|동작(?:이야)?)|해)|"
        r"알려\s*(?:줘|줄래|주세요|줄\s*수\s*있어\??)|보기\s*(?:해\s*줘|해줘)?|설명해\s*줘|보여\s*줘)|"
        r"(?:미리\s*(?:보기|알려\s*(?:줘|줄래|주세요|줄\s*수\s*있어\??))))\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:please\s+)?(?:what(?:'s|\s+is)\s+(?:the\s+)?next\s+step|"
        r"tell\s+me\s+(?:about\s+)?(?:what\s+the\s+)?next\s+step(?:\s+is)?|"
        r"preview\s+(?:the\s+)?next\s+step|"
        r"what\s+does\s+the\s+next\s+step\s+do|"
        r"(?:the\s+)?next\s+step)\??$",
        re.IGNORECASE,
    ),
)
_CURRENT_INFORMATION_PATTERNS = (
    re.compile(r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?current\s+step)$"),
    re.compile(r"^(?:what\s+should\s+i\s+do\s+(?:now|next)|what\s+do\s+i\s+do\s+now)$"),
    re.compile(r"^(?:(?:이\s*)?실험에서\s*)?(?:지금|현재)\s*단계(?:는|가)?\s*(?:뭐야|무엇|어디야)$"),
    re.compile(
        r"^(?:지금|현재)\s*(?:뭐|무엇을?)\s*(?:해야|할)\s*(?:돼|되|하나요|합니까|해)$"
    ),
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
    """The entities a near-miss may be repaired toward: only those supplied.

    An empty inventory stays empty. It used to fall back to the in-gel
    document's entity list, which repaired a near-miss toward an in-gel
    reagent on a protocol that has none ("trypsun" became trypsin anywhere).
    """

    return tuple(dict.fromkeys(value))


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


#: A request that reports progress or asks to move is not a term question.
_WORKFLOW_CUE = re.compile(
    r"(?:다음|넘어가|넘겨|완료|끝났|끝냈|다\s*했|했어|했습니다|됐어|됐습니다|되었|"
    r"시작|멈춰|그만|정지|타이머|next|done|finished|complete|start|stop|timer)",
    re.IGNORECASE,
)
_TERM_QUESTION_CUE = re.compile(
    r"(?:뭐|무엇|무슨|왜|어떻게|어떤|설명|알려|의미|뜻|역할|안전|위험|주의|"
    r"\?|what|why|how|explain|mean|role|safe)",
    re.IGNORECASE,
)


def _asks_about_a_term(key: str) -> bool:
    return bool(_TERM_QUESTION_CUE.search(key)) and not _WORKFLOW_CUE.search(key)


def normalize_scientific_request(
    transcript: str,
    *,
    entity_inventory: tuple[str, ...] = (),
    protocol_vocabulary: ProtocolVocabulary | None = None,
) -> tuple[
    str, tuple[str, ...], str | None, tuple[tuple[str, str], ...]
]:
    """Resolve ordered known entities while preserving auditable corrections.

    The mis-hearing repairs below ("엠빅" -> AMBIC, "아세토나이트릴" ->
    acetonitrile) and the reagent entities they lead to are the in-gel
    document's. With ``protocol_vocabulary`` they apply only to a substance
    that protocol's text contains, so a protocol without AMBIC never hears
    it. ``None`` -- a caller with no active protocol -- keeps every rule.
    """

    def present(entity: str) -> bool:
        return protocol_vocabulary is None or protocol_vocabulary.mentions_entity(entity)

    raw = _semantic_utterance_key(transcript)
    key = re.sub(
        r"(?<![a-z0-9])(?:[a-z]\s+){2,}[a-z](?![a-z0-9])",
        lambda match: match.group(0).replace(" ", ""),
        raw,
    )
    corrections: list[tuple[str, str]] = []

    def repair(pattern: str, replacement: str, label: str, entity: str) -> None:
        nonlocal key
        if not present(entity):
            return
        match = re.search(pattern, key)
        if match is None:
            return
        observed = match.group(0)
        key = re.sub(pattern, replacement, key)
        if observed.casefold() != label.casefold():
            corrections.append((observed, label))

    repair(r"(?<![a-z0-9])anbi[-\s]*c?(?![a-z0-9])", "ambic", "AMBIC", "ambic")
    repair(r"(?<![a-z0-9])am\s+bic(?![a-z0-9])", "ambic", "AMBIC", "ambic")
    repair(r"(?<![a-z0-9])jel\s+tug(?![a-z0-9])", "gel plug", "gel plug", "gel_plug")
    repair(r"제트\s*플러그", "젤 플러그", "gel plug", "gel_plug")
    repair(
        r"(?:염색된\s*)?단백질\s*(?:뱀드|뱄드|밸드|밴트)", "단백질 밴드", "단백질 밴드",
        "stained_protein_band",
    )
    repair(r"에이\s*엠\s*빅", "ambic", "AMBIC", "ambic")
    repair(r"엠빅|암비크", "ambic", "AMBIC", "ambic")
    repair(r"트립씬", "트립신", "trypsin", "trypsin")
    repair(r"폼산", "포름산", "formic acid", "formic_acid")
    repair(
        r"아이오도아세트아마이드|이오도아세트아미드", "아이오도아세트아미드",
        "iodoacetamide", "iodoacetamide",
    )
    repair(r"디티티", "dtt", "DTT", "dtt")
    if present("ambic"):
        key = re.sub(r"에이\s*엠\s*빅", "ambic", key)
    key = re.sub(
        r"솔루션\s*([ab])",
        lambda match: (
            f"solution {match.group(1)}"
            if present(f"solution_{match.group(1)}") else match.group(0)
        ),
        key,
    )
    hplc_spaced = (
        re.search(r"(?<![a-z0-9])h\s+plc\s*(?:water|워터)", key)
        if present("hplc_water") else None
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
    matches: list[tuple[int, int, str]] = []
    for pattern, name in _TERM_QUESTION_PATTERNS:
        if not present(name):
            continue
        for match in pattern.finditer(key):
            matches.append((match.start(), match.end(), name))
    if protocol_vocabulary is not None and _asks_about_a_term(key):
        # The active protocol's own terms -- its material and equipment
        # names, labelled reagents and defined abbreviations -- are entities
        # too, named as the protocol spells them, so a question about any
        # registered PDF's material is answered from that PDF. Only a
        # question: "ethanol 준비됐어 다음으로" keeps reaching the semantic
        # fallback as before.
        claimed = {
            surface for surfaces in _ENTITY_SOURCE_ALIASES.values()
            for surface in surfaces
        }
        for term in protocol_vocabulary.terms:
            folded = " ".join(term.text.split()).casefold()
            if folded in claimed or len(folded) < 3:
                continue
            match = _term_pattern(term.text).search(key)
            if match is not None:
                matches.append((match.start(), match.end(), term.text))
    # One stretch of the request names one entity: "microcentrifuge" is not
    # also a "centrifuge", and a span the reviewed table already names keeps
    # that name.
    kept = [
        item for index, item in enumerate(matches)
        if not any(
            (other[0] <= item[0] and item[1] <= other[1])
            and ((other[1] - other[0]) > (item[1] - item[0]) or other_index < index)
            for other_index, other in enumerate(matches)
            if other_index != index
        )
    ]
    entities = tuple(dict.fromkeys(
        name for _, _, name in sorted(kept, key=lambda item: item[0])
    ))
    labels = {
        "ambic": "AMBIC", "hplc_water": "HPLC water",
        "solution_a": "Solution A", "solution_b": "Solution B",
        "acetonitrile": "acetonitrile", "gel_plug": "gel plug",
        "stained_protein_band": "stained protein band",
        "dtt": "DTT", "iodoacetamide": "iodoacetamide",
        "trypsin": "trypsin", "formic_acid": "formic acid",
        "rpm": "rpm", "incubation": "incubation",
        "contamination": "contamination",
    }
    if key != raw and not corrections and entities:
        corrections.append((raw, labels.get(entities[0], entities[0])))
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
    protocol_vocabulary: ProtocolVocabulary | None = None,
) -> tuple[str, str | None, str | None]:
    """Backward-compatible single-entity view of normalized scientific input."""

    key, entities, correction_note, _ = normalize_scientific_request(
        transcript, entity_inventory=entity_inventory,
        protocol_vocabulary=protocol_vocabulary,
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


def steps_anchoring_a_repetition(
    fixture: CuratedProtocolFixture,
) -> frozenset[str]:
    """Step ids the source states a repeat at, read off the analysis.

    The anchor rather than the last step of the range: a repeat sentence can
    sit in a later step's text than the range it names -- in-gel's page 8 says
    "repeat steps 17-18" inside step 20's block -- and an operator meets the
    instruction where it is written, not where the range ends.

    A construct that names neither a repeated set nor a start is not a
    repetition -- a source ambiguity, say -- and contributes nothing. A
    document whose analysis carries no repetition yields an empty set, which
    is the fail-closed answer: no step gets an endpoint predicate, so no
    utterance is read as an endpoint observation.
    """

    found = set()
    for construct in getattr(fixture.draft.protocol, "constructs", ()) or ():
        if (
            getattr(construct, "repeated_step_ids", None) is None
            and getattr(construct, "start_step_id", None) is None
        ):
            continue
        anchor = getattr(construct, "step_id", None)
        if isinstance(anchor, str) and anchor:
            found.add(anchor)
    return frozenset(found)


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
    # Which steps have a visible endpoint to report is the document's
    # statement, not this module's: the labels {"7", "9", "20"} that stood
    # here were in-gel's own numbering, so the predicate -- and the gate that
    # depends on it -- existed for that one PDF and for nothing else
    # (principle 1). A step the source states a repeat at is the step whose
    # continuation depends on an observation, so that is the step that gets a
    # predicate id. The id's shape is unchanged; only its condition is.
    predicate = (
        f"candidate_a_step_{step.source_label}_endpoint"
        if step.step_id in steps_anchoring_a_repetition(fixture) else None
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

    if _reply_withholds_assent(value):
        return None
    key = _semantic_utterance_key(value)
    if re.fullmatch(
        r"(?:(?:네|예|응응|응|그래|맞아|맞아요|물론)(?:\s+|$))*"
        r"(?:(?:전부|모두)\s*)?(?:다\s*(?:했어|했어요|끝냈어|끝냈어요)|"
        r"마쳤어|마쳤어요|끝냈어|끝냈어요|"
        r"완료(?:했어|했어요|했습니다)?|했어|했어요|됐어|됐어요)?"
        r"|yes(?:\s+i\s+(?:did|finished))?|done|correct|that(?:'|’)s\s+right",
        key,
    ) and key:
        return "affirmative"
    if _NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(key):
        return "negative"
    return None


#: The only replies that say yes to the endpoint prompt. It asks whether the
#: source endpoint was seen, and "완료했어요", "했어", "다 했어" say the work
#: was done -- a different claim -- so they answer the completion prompt only.
#: The shape is _binary_frame_reply's, cut down: 됐어/됐어요 may only come
#: last, so every reply read as yes here was already read as yes there.
_OBSERVATION_AFFIRMATIVE = re.compile(
    r"(?:(?:네|예|응|맞아|맞아요)\s+){0,2}(?:네|예|응|맞아|맞아요|됐어|됐어요)"
    r"|yes|correct|that(?:'|’)s\s+right"
)


def _observation_binary_reply(value: str) -> str | None:
    """Interpret a short answer to the server-owned endpoint prompt."""

    if _reply_withholds_assent(value):
        return None
    key = _semantic_utterance_key(value)
    if _OBSERVATION_AFFIRMATIVE.fullmatch(key):
        return "affirmative"
    if _NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(key):
        return "negative"
    return None


def resolve_question_focus(
    transcript: str,
    entities: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Distinguish FOCUS / REQUESTED entities from CONTEXT / MODIFIER entities.

    Returns:
        (focus_entities, context_entities)
    """
    if len(entities) <= 1:
        return entities, ()

    key = transcript.casefold()

    # Explicit coordination check (A와 B, A 그리고 B, 둘 다, 각각 등)
    if re.search(
        r"(?:(?:와|과|랑|하고|,)\s+|그리고\s+|둘\s*다|각각|모두|차이|비교|vs|-vs-)",
        key,
    ):
        return entities, ()

    # Relative modifier clause check:
    # Pattern: [Context Entity] (이|가|를|을)? (들어있는|담긴|담은|사용하는|안에 있는|포함된|넣은|넣어둔) [Focus Entity]
    entity_spans: list[tuple[int, int, str]] = []
    for pattern, name in _TERM_QUESTION_PATTERNS:
        if name in entities:
            for match in pattern.finditer(key):
                entity_spans.append((match.start(), match.end(), name))

    entity_spans.sort(key=lambda s: s[0])
    unique_spans: list[tuple[int, int, str]] = []
    last_end = -1
    for s_start, s_end, ent in entity_spans:
        if s_start >= last_end:
            unique_spans.append((s_start, s_end, ent))
            last_end = s_end

    if len(unique_spans) >= 2:
        first_span = unique_spans[0]
        second_span = unique_spans[1]
        between = key[first_span[1]:second_span[0]].strip()
        modifier_match = re.search(
            r"^(?:(?:이|가|를|을|에)?\s*)?(?:들어\s*있는|담긴|담은|사용하는|안에\s*있는|포함된|넣은|넣어\s*둔|채운|섞은)",
            between,
        )
        if modifier_match:
            focus = (second_span[2],)
            context = (first_span[2],)
            return focus, context

    return entities, ()


def classify_agent_meta_intent(transcript: str, language: str = "ko") -> CuratedControlIntent | None:
    key = _utterance_key(transcript)
    if not key:
        return None

    if re.search(
        r"(?:what\s+(?:is|are|'s|’s)\s+(?:the\s+)?(?:purpose|function|functions|role|roles|capabilities|features?)"
        r"|what\s+(?:can|do|does)\s+(?:this\s+agent|you|the\s+agent)\s+do"
        r"|what\s+are\s+(?:your\s+)?(?:capabilities|functions|roles)"
        r"|what\s+are\s+you(?:\s+for)?"
        r"|tell\s+me\s+(?:about\s+)?(?:yourself|your\s+(?:function|functions|role|roles|capabilities|purpose))"
        r"|explain\s+(?:your|the)\s+(?:function|functions|role|roles|capabilities|purpose)"
        r"|how\s+can\s+you\s+help|who\s+are\s+you)",
        key,
        re.I,
    ):
        return CuratedControlIntent(
            intent_kind="agent_meta",
            action=CuratedProtocolAction.AGENT_META,
            question_kind="agent_meta",
            language=language,
            normalized_transcript=key,
        )

    has_subject = bool(re.search(r"(?:너|네|당신|에이전트|보이스\s*에이전트|시스템|ai|누구)", key))
    has_identity = bool(re.search(r"(?:뭐\s*하는\s*(?:애|거|사람|에이전트|로봇|시스템|일)|누구|정체|이름)", key))
    has_purpose = bool(re.search(r"(?:목표|목적|존재\s*이유|만들(?:어|어진)|취지)", key))
    has_capability = bool(re.search(r"(?:기능|역할|할\s*수\s*있는\s*(?:일|거)|수행|도움|지원|지원해|도와줘)", key))

    is_meta = False
    if has_subject and (has_identity or has_purpose or has_capability):
        is_meta = True
    elif any(pattern.search(key) for pattern in _AGENT_META_PATTERNS):
        is_meta = True

    if is_meta:
        return CuratedControlIntent(
            intent_kind="agent_meta",
            action=CuratedProtocolAction.AGENT_META,
            question_kind="agent_meta",
            language=language,
            normalized_transcript=key,
        )
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
    current_step: int | str | None = None,
    max_steps: int = 25,
    protocol_vocabulary: ProtocolVocabulary | None = None,
) -> CuratedControlIntent:
    """Classify reviewed workflow shapes before any knowledge or model route."""

    if front := _front_control_intent(transcript, language):
        return front
    if meta_intent := classify_agent_meta_intent(transcript, language=language):
        return meta_intent

    key, normalized_entities, correction_note, corrections = (
        normalize_scientific_request(
        transcript, entity_inventory=entity_inventory,
        protocol_vocabulary=protocol_vocabulary,
        )
    )
    focus_entities, _context_entities = resolve_question_focus(
        transcript, normalized_entities
    )
    if focus_entities:
        normalized_entities = focus_entities
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
    if any(pattern.fullmatch(key) for pattern in _START_COMMAND_PATTERNS) or any(pattern.search(key) for pattern in _START_COMMAND_PATTERNS):
        if not re.search(r"(?:타이머|시간\s*측정|timer|반응|미리)", key):
            return CuratedControlIntent(
                intent_kind="workflow_command",
                action=CuratedProtocolAction.START,
                requested_transition="start",
                requested_followup="describe_new_current_step",
                language=language,
                allows_state_mutation=True,
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
    if not normalized_entities and any(pattern.search(key) for pattern in _AGENT_META_PATTERNS):
        return CuratedControlIntent(
            intent_kind="agent_meta",
            action=CuratedProtocolAction.AGENT_META,
            question_kind="agent_meta",
            language=language,
            normalized_transcript=key,
        )
    if any(pattern.fullmatch(key) for pattern in _UNDERSPECIFIED_RESULT_PATTERNS):
        return CuratedControlIntent(
            intent_kind="underspecified_result_request",
            action=CuratedProtocolAction.CLARIFY_REFERENCE,
            question_kind="underspecified_request",
            language=language,
            normalized_transcript=key,
        )
    if any(pattern.search(key) for pattern in _PAUSE_PATTERNS) and not _CONTROL_QUESTION.search(
        " ".join(transcript.casefold().split())
    ):
        return CuratedControlIntent(
            intent_kind="pause_workflow",
            action=CuratedProtocolAction.PAUSE,
            language=language,
            normalized_transcript=key,
        )
    if any(pattern.search(key) for pattern in _RESUME_PATTERNS):
        return CuratedControlIntent(
            intent_kind="resume_workflow",
            action=CuratedProtocolAction.RESUME,
            language=language,
            allows_state_mutation=True,
            normalized_transcript=key,
        )
    if any(pattern.search(key) for pattern in _HANDOFF_PATTERNS):
        return CuratedControlIntent(
            intent_kind="report_handoff",
            action=CuratedProtocolAction.REPORT_HANDOFF,
            language=language,
            normalized_transcript=key,
        )
    if any(pattern.search(key) for pattern in _TIMER_START_PATTERNS):
        return CuratedControlIntent(
            intent_kind="start_step_timer",
            action=CuratedProtocolAction.START_TIMER,
            language=language,
            normalized_transcript=key,
        )
    if any(pattern.search(key) for pattern in _TIMER_QUERY_PATTERNS):
        return CuratedControlIntent(
            intent_kind="step_timer_status",
            action=CuratedProtocolAction.TIMER_STATUS,
            language=language,
            normalized_transcript=key,
        )
    curr_step_val = 1
    if current_step is not None:
        try:
            curr_step_val = int(current_step)
        except (ValueError, TypeError):
            curr_step_val = 1
    if step_range := _extract_step_range(key, current_step=curr_step_val, max_step=max_steps):
        r_start, r_end = step_range
        return CuratedControlIntent(
            intent_kind="step_range_summary",
            action=CuratedProtocolAction.STEP_RANGE,
            target_step=f"{r_start}-{r_end}",
            language=language,
            normalized_transcript=key,
            range_start_step=r_start,
            range_end_step=r_end,
        )
    for pattern in _PREVIEW_STEP_PATTERNS:
        if match := pattern.search(key):
            target = match.groupdict().get("label") or match.groupdict().get("en") or match.groupdict().get("pen") or "1"
            return CuratedControlIntent(
                intent_kind="preview_step",
                action=CuratedProtocolAction.PREVIEW_STEP,
                target_step=target,
                language=language,
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
    decision = resolve_korean_completion_decision(transcript, language=language)
    completion_claimed = (
        bool(_COMPLETION_CLAIM.search(key))
        or decision.is_completion
        or (
            bool(_UNTARGETED_COMPLETION.fullmatch(key))
            and not _reply_withholds_assent(transcript)
        )
    )
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
        target_step = "authoritative_current_step"
        intent_kind = _completion_intent_kind(
            key, "completion_and_next" if next_requested else "report_completion"
        )
        if decision.is_completion and decision.target_kind == "explicit_step" and decision.target_step_label:
            target_step = decision.target_step_label
        return CuratedControlIntent(
            intent_kind=intent_kind,
            action=CuratedProtocolAction.NEXT,
            reported_completion=True,
            requested_transition="next",
            requested_followup="describe_new_current_step",
            target_step=target_step,
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
            intent_kind=_completion_intent_kind(key, "completion_and_next"),
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
            intent_kind=_completion_intent_kind(key, "report_completion"),
            action=CuratedProtocolAction.NEXT,
            reported_completion=True,
            requested_transition="next",
            requested_followup="describe_new_current_step",
            target_step="authoritative_current_step",
            language=language,
            allows_state_mutation=True,
        )
    if key in _FULL_DETAIL_COMMANDS or _READ_ALOUD_REQUEST.fullmatch(key):
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
        if any(term in key for term in ("화학 구조", "화학구조", "구조식", "2d 구조", "chemical structure", "molecular structure")):
            visual_intent = "chemical_structure"
            visual_kind = "web_photo"
        elif any(term in key for term in ("장치", "장비", "기구", "기기", "전기영동", "sds-page", "sds_page", "electrophoresis", "centrifuge", "pipette", "apparatus", "equipment")):
            visual_intent = "lab_equipment_image"
            visual_kind = "web_photo"
        elif any(pattern.search(key) for pattern in _WEB_VISUAL_REQUEST_PATTERNS) or any(term in key for term in ("이미지", "사진", "photo", "image", "실제", "시약", "sample")):
            visual_intent = "real_reference_image"
            visual_kind = "web_photo"
        else:
            visual_intent = "instructional_illustration"
            visual_kind = "instructional_illustration"

        return CuratedControlIntent(
            intent_kind="visual_request",
            action=CuratedProtocolAction.VISUAL_REQUEST,
            target_step="authoritative_current_step",
            visual_requested=True,
            visual_kind=visual_kind,
            visual_intent=visual_intent,
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
    observation_capture = _observation_capture(transcript)
    if observation_capture is not None:
        category, content = observation_capture
        return CuratedControlIntent(
            intent_kind=(
                "record_observation" if content is not None
                else "observation_content_required"
            ),
            action=CuratedProtocolAction.RECORD_OBSERVATION,
            target_step="authoritative_current_step",
            reported_observation=content is not None,
            observation_predicate=category,
            observation_outcome=content,
            language=language,
            normalized_transcript=key,
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
    if requested_entity == "tube" and len(normalized_entities) <= 1:
        return CuratedControlIntent(
            intent_kind="lab_domain_qa",
            action=CuratedProtocolAction.LAB_DOMAIN_QA,
            question_kind="lab_domain_qa",
            target_step="authoritative_current_step",
            requested_entity="tube",
            requested_entities=("tube",),
            resolved_entity="tube",
            language=language,
            normalized_transcript=key,
            question_dimensions=dimensions or ("definition",),
        )
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
    if re.search(
        r"(?:튜브|tube|용기|vial|팁|tip|피펫|pipette|반응기|센트리퓨지|centrifuge|"
        r"마이크로튜브|microcentrifuge|시약병|시험관|플레이트|well\s*plate|비커|beaker)",
        key,
    ):
        return CuratedControlIntent(
            intent_kind="lab_domain_qa",
            action=CuratedProtocolAction.LAB_DOMAIN_QA,
            question_kind="lab_domain_qa",
            target_step="authoritative_current_step",
            requested_entities=normalized_entities or (("tube",) if re.search(r"(?:튜브|tube)", key) else ()),
            requested_entity=normalized_entity or ("tube" if re.search(r"(?:튜브|tube)", key) else None),
            resolved_entity=normalized_entity or ("tube" if re.search(r"(?:튜브|tube)", key) else None),
            language=language,
            normalized_transcript=key,
            question_dimensions=dimensions or ("definition", "role"),
        )
    return CuratedControlIntent(
        intent_kind="off_topic",
        action=CuratedProtocolAction.OFF_TOPIC,
        language=language,
    )


def curated_intent_from_arbitration(
    decision: RequestArbitration,
    *,
    language: str,
) -> CuratedControlIntent | None:
    """Project shared read-only intents into the curated runtime contract.

    Workflow-control candidates deliberately return ``None``: the curated
    controller's stricter completion, timer, observation, pause, and stop gates
    remain the only authority for those actions.
    """

    common = {
        "language": language,
        "normalized_transcript": decision.normalized_text,
        "confidence": decision.confidence,
        "confidence_source": "authoritative_request_arbiter",
        "question_dimensions": decision.dimensions,
    }
    if decision.intent is RequestIntent.LEARNING:
        warning_only = decision.reason_code == "current_step_warning"
        return CuratedControlIntent(
            intent_kind=("current_step_warning" if warning_only else "current_step_learning"),
            action=CuratedProtocolAction.QUESTION,
            requested_followup=("explain_warning" if warning_only else "explain_step_rationale"),
            target_step="authoritative_current_step",
            question_kind=("safety" if warning_only else "learning"),
            **common,
        )
    if decision.intent is RequestIntent.COMBINED_LEARNING_NEXT:
        return CuratedControlIntent(
            intent_kind="learning_and_next_preview",
            action=CuratedProtocolAction.CLARIFY_COMPLETION,
            requested_transition="next",
            requested_followup="explain_rationale_preview_next_confirm_completion",
            target_step="authoritative_current_step",
            question_kind="combined_information",
            requires_confirmation=True,
            allows_state_mutation=False,
            **common,
        )
    if decision.intent is RequestIntent.PROTOCOL_AUDIT:
        return CuratedControlIntent(
            intent_kind="protocol_audit",
            action=CuratedProtocolAction.PROTOCOL_QUERY,
            requested_followup="show_protocol_version",
            question_kind="protocol_metadata",
            protocol_scope="version",
            **common,
        )
    if decision.intent is RequestIntent.HISTORY_RESUME:
        return CuratedControlIntent(
            intent_kind=(
                "previous_experiment_resume"
                if decision.history_action == "resume"
                else "experiment_history"
            ),
            action=CuratedProtocolAction.QUESTION,
            requested_followup=(
                "find_resumable_session"
                if decision.history_action == "resume"
                else "list_experiment_history"
            ),
            question_kind="experiment_history",
            **common,
        )
    if decision.intent is RequestIntent.UNCERTAINTY:
        return CuratedControlIntent(
            intent_kind="bounded_outcome_uncertainty",
            action=CuratedProtocolAction.QUESTION,
            requested_followup="state_evidence_needed",
            target_step="authoritative_current_step",
            question_kind="uncertainty",
            **common,
        )
    return None


#: The complete allowlist of curated actions a semantic proposal may reach.
#: A meaning absent from this table cannot be projected at all, so the resolver
#: can never introduce a workflow action.  Read-only meanings map to the
#: existing read-only actions; bounded control maps to the existing bounded
#: control action; ``COMPLETE_CURRENT_STEP`` deliberately maps to the explicit
#: completion confirmation rather than to a transition, so the semantic path
#: holds no mutation authority of its own.  ``STOP`` has no entry on purpose.
_SEMANTIC_INTENT_PROJECTION: dict[SemanticIntent, dict[str, Any]] = {
    SemanticIntent.CURRENT_STEP: {
        "intent_kind": "semantic_current_step",
        "action": CuratedProtocolAction.CURRENT,
        "target_step": "authoritative_current_step",
    },
    SemanticIntent.NEXT_STEP_INFORMATION: {
        "intent_kind": "semantic_next_step_information",
        "action": CuratedProtocolAction.NEXT_INFORMATION,
        "requested_followup": "describe_next_step_without_transition",
        "target_step": "authoritative_current_step",
    },
    SemanticIntent.REPEAT: {
        "intent_kind": "semantic_repeat",
        "action": CuratedProtocolAction.REPEAT,
        "target_step": "authoritative_current_step",
    },
    SemanticIntent.TIMER_STATUS: {
        "intent_kind": "semantic_step_timer_status",
        "action": CuratedProtocolAction.TIMER_STATUS,
    },
    SemanticIntent.TIMER_INFORMATION: {
        "intent_kind": "semantic_step_timer_information",
        "action": CuratedProtocolAction.TIMER_STATUS,
    },
    SemanticIntent.START_TIMER: {
        "intent_kind": "semantic_start_step_timer",
        "action": CuratedProtocolAction.START_TIMER,
    },
    SemanticIntent.PAUSE: {
        "intent_kind": "semantic_pause_workflow",
        "action": CuratedProtocolAction.PAUSE,
    },
    SemanticIntent.RESUME: {
        "intent_kind": "semantic_resume_workflow",
        "action": CuratedProtocolAction.RESUME,
        "allows_state_mutation": True,
    },
    SemanticIntent.NOT_DONE: {
        "intent_kind": "semantic_not_done",
        "action": CuratedProtocolAction.DECLINE_COMPLETION,
        "target_step": "authoritative_current_step",
    },
    SemanticIntent.RELATED_QUESTION: {
        "intent_kind": "semantic_related_question",
        "action": CuratedProtocolAction.RELATED_QUESTION,
        "target_step": "authoritative_current_step",
        "question_kind": "related_knowledge",
    },
    SemanticIntent.COMPLETE_CURRENT_STEP: {
        "intent_kind": "semantic_completion_confirmation_required",
        "action": CuratedProtocolAction.CLARIFY_COMPLETION,
        "requested_transition": "next",
        "requested_followup": "confirm_current_step_completion",
        "target_step": "authoritative_current_step",
        "requires_confirmation": True,
        "allows_state_mutation": False,
    },
}


def curated_intent_from_semantic_decision(
    decision: SemanticIntentDecision,
    *,
    language: str,
    normalized_transcript: str,
) -> CuratedControlIntent | None:
    """Project one *accepted* semantic proposal into the curated contract.

    A refused decision, or an accepted one whose meaning has no entry in
    ``_SEMANTIC_INTENT_PROJECTION``, returns ``None`` so the deterministic
    outcome stands unchanged.
    """

    if not decision.accepted or decision.intent is None:
        return None
    projection = _SEMANTIC_INTENT_PROJECTION.get(decision.intent)
    if projection is None:
        return None
    return CuratedControlIntent(
        language=language,
        normalized_transcript=normalized_transcript,
        confidence=decision.confidence,
        confidence_source="semantic_intent_fallback",
        **projection,
    )


_WORKFLOW_COMMANDS = {
    "시작": CuratedProtocolAction.START,
    "시작해": CuratedProtocolAction.START,
    "시작해줘": CuratedProtocolAction.START,
    "시작하자": CuratedProtocolAction.START,
    "시작할게": CuratedProtocolAction.START,
    "시작하겠습니다": CuratedProtocolAction.START,
    "응 시작하자": CuratedProtocolAction.START,
    "그래 시작해": CuratedProtocolAction.START,
    "1단계부터 하자": CuratedProtocolAction.START,
    "진행하자": CuratedProtocolAction.START,
    "start it": CuratedProtocolAction.START,
    "yes start": CuratedProtocolAction.START,
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
    "지금 뭐 해야 돼": CuratedProtocolAction.CURRENT,
    "지금 뭐 해야 되": CuratedProtocolAction.CURRENT,
    "what should i do now": CuratedProtocolAction.CURRENT,
    "current step": CuratedProtocolAction.CURRENT,
    "다시 말해 줘": CuratedProtocolAction.REPEAT,
    "다시 말해줘": CuratedProtocolAction.REPEAT,
    "반복": CuratedProtocolAction.REPEAT,
    "repeat": CuratedProtocolAction.REPEAT,
    "다음": CuratedProtocolAction.NEXT,
    "다음 단계": CuratedProtocolAction.NEXT,
    "다음 단계로 넘어가줘": CuratedProtocolAction.NEXT,
    "다음 단계로 넘어가 줘": CuratedProtocolAction.NEXT,
    "다음 단계로 넘어가죠": CuratedProtocolAction.NEXT,
    "단계로 넘어가죠": CuratedProtocolAction.NEXT,
    "다음 단계를 진행해줘": CuratedProtocolAction.NEXT,
    "다음 단계로 진행해줘": CuratedProtocolAction.NEXT,
    "다음 단계 진행해줘": CuratedProtocolAction.NEXT,
    "next": CuratedProtocolAction.NEXT,
    # "중지" and "stop" pause now; only "종료" ends (_front_control_intent).
    "종료": CuratedProtocolAction.STOP,
    "프로토콜 종료": CuratedProtocolAction.STOP,
    "프로토콜을 종료해줘": CuratedProtocolAction.STOP,
    "프로토콜 종료해줘": CuratedProtocolAction.STOP,
    "end session": CuratedProtocolAction.STOP,
}


_FULL_DETAIL_COMMANDS = frozenset({
    "전체 내용을 읽어줘",
    "현재 단계 전체를 읽어줘",
    "상세 내용을 읽어줘",
    "현재 단계 상세 내용을 읽어줘",
})

#: Asking to hear the current step read aloud: "이 단계 읽어줘", "단계 내용
#: 읽어 주세요", "한국어로 읽어줘", "다시 읽어줘". Only an explicit request
#: reads a step's content; moving between steps keeps its short sentence.
#: "원문 그대로 읽어줘" is not one -- it asks for the English, not Korean.
_READ_ALOUD_REQUEST = re.compile(
    r"(?:(?:이|현재|지금)\s*)?(?:단계\s*)?(?:(?:내용|전체|안내)(?:을|를)?\s*)?"
    r"(?:한국어로\s*)?(?:다시\s*)?(?:소리\s*내(?:어|서)\s*)?"
    r"읽어\s*(?:줘|줘요|주세요|줄래|줄래요|줄래\?)"
)


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


def _display_document(
    *,
    title: str,
    lead: str | None = None,
    primary: str | None = None,
    bullets: tuple[str, ...] = (),
    source: str | None = None,
    citation: str | None = None,
    extra_sections: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    if isinstance(lead, str) and lead.strip():
        sections.append({"kind": "lead", "heading": "", "text": lead.strip()})
    if bullets:
        clean_bullets = [b.strip() for b in bullets if isinstance(b, str) and b.strip()]
        if clean_bullets:
            sections.append({
                "kind": "bullets",
                "heading": "직접 답변" if not primary else "세부 항목",
                "items": clean_bullets,
            })
    if isinstance(primary, str) and primary.strip():
        sections.append({"kind": "section", "heading": "답변", "text": primary.strip()})
    if isinstance(source, str) and source.strip():
        sections.append({"kind": "source", "heading": "원문 · English", "text": source.strip()})
    if isinstance(citation, str) and citation.strip():
        sections.append({"kind": "citation", "heading": "출처", "text": citation.strip()})
    for heading, text in extra_sections:
        if isinstance(text, str) and text.strip():
            sections.append({"kind": "section", "heading": heading, "text": text.strip()})
    return {"title": title, "sections": sections}


#: A source note that offers the reader another way to do the step.
_SOURCE_ALTERNATIVE = re.compile(
    r"\b(?:alternatively|optionally|you\s+(?:can|may)\s+(?:also\s+)?"
    r"(?:use|increase|decrease|reduce|extend|replace|substitute|choose|skip|"
    r"shake|incubate|perform|do|leave|instead)|can\s+be\s+(?:replaced|substituted|"
    r"used\s+instead))\b",
    re.IGNORECASE,
)
#: A note that forbids something is not an alternative, whatever it names.
_SOURCE_PROHIBITION = re.compile(
    r"\b(?:do\s+not|don't|never|must\s+not|should\s+not|cannot|can't|avoid)\b",
    re.IGNORECASE,
)


def _offers_source_alternative(text: str) -> bool:
    derived = _derived_source_text(text)
    return bool(_SOURCE_ALTERNATIVE.search(derived)) and not _SOURCE_PROHIBITION.search(derived)

_FACT_POINT_LABELS_EN = {
    "step": "Verified action",
    "note": "Source note",
    "warning": "Source warning",
    "expected_result": "Expected result",
    "prerequisite": "Before starting",
    "material": "Verified material",
    "equipment": "Verified equipment",
}

_FACT_POINT_LABELS = {
    "step": "확인된 동작",
    "note": "원문 참고",
    "warning": "원문 주의",
    "expected_result": "확인 기준",
    "prerequisite": "시작 전 확인",
    "material": "확인된 재료",
    "equipment": "확인된 장비",
}


def _fact_point_label(fact: CuratedProtocolFact, *, korean: bool) -> str:
    """Label one admitted statement by its kind; a note offering another way
    to do the step is labelled as the source's own alternative."""

    if fact.kind == "note" and _offers_source_alternative(fact.text):
        return "원문이 허용한 대안" if korean else "Source-approved alternative"
    if korean:
        return _FACT_POINT_LABELS.get(fact.kind, "확인된 내용")
    return _FACT_POINT_LABELS_EN.get(fact.kind, "Verified detail")


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
        # Every point is one of the step's own reviewed statements, labelled
        # by its kind -- the same for every step of every protocol.
        points = "\n".join(
            f"- {_fact_point_label(fact, korean=True)}: {text}"
            for fact, text in localized_items
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
            f"- {_fact_point_label(fact, korean=False)}: {text}"
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
        # The purpose is the document's own statement of it, for every
        # protocol: nothing here may say what one particular PDF is for.
        page=knowledge.purpose.source_page
        step_id, _, fact_id = PURPOSE_FACT_KEY.partition("/")
        korean = fixture.localized_fact(step_id, fact_id)
        if language=="ko" and korean:
            speech="원문에 적힌 실험 목적을 화면에 표시했습니다."
            display=(
                f"실험 목적\n{korean}\n\n원문 · English · PDF p.{page}\n"
                f"{knowledge.purpose.text}"
            )
        elif language=="ko":
            speech="원문에 적힌 실험 목적을 화면에 표시했습니다."
            display=(
                f"실험 목적\n원문에 적힌 실험 목적입니다.\n\n원문 · English · PDF p.{page}\n"
                f"{knowledge.purpose.text}"
            )
        else:
            speech=f"The protocol states its purpose as: {knowledge.purpose.text}"
            display=f"Experiment purpose\n{speech}\n\nOriginal · PDF p.{page}\n{knowledge.purpose.text}"
        return display,speech,tuple(facts)

    if scope == "overview":
        facts.extend((knowledge.purpose,*knowledge.sections))
        if language == "ko":
            speech = (
                f"이 프로토콜은 전체 {total}단계이고, 원문은 "
                f"{len(knowledge.sections)}개 구간으로 나뉩니다. 구간 이름은 화면에 표시했습니다."
                if knowledge.sections else
                f"이 프로토콜은 전체 {total}단계입니다."
            )
            display = f"전체 흐름\n{speech}\n\n구간\n" + "\n".join(
                f"- {fact.text} · 원문 p.{fact.source_page}"
                for fact in knowledge.sections
            )
        else:
            speech = (
                f"The {total}-step protocol is organized into "
                f"{len(knowledge.sections)} ordered sections."
            )
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
    step_index: int | None = None,
    timer_active: bool = False,
    step_timer_seconds: int = 0,
) -> str:
    timer_hint = ""
    if step_index is not None and step_timer_seconds > 0 and not timer_active:
        if language == "ko":
            timer_hint = " 타이머를 시작하려면 말씀해주세요."
        elif language == "en":
            timer_hint = " Say start timer when you are ready to begin the timer."
        elif language == "vi":
            timer_hint = " Hãy nói bắt đầu hẹn giờ khi bạn sẵn sàng."

    english_subject = "Protocol"
    if language == "en":
        if action is CuratedProtocolAction.START:
            verb = "redisplayed" if resumed else "displayed"
            return f"{english_subject} step {label} guidance is {verb} on screen.{timer_hint}"
        if action is CuratedProtocolAction.CURRENT:
            return f"The current step is {label}. Its guidance is displayed on screen.{timer_hint}"
        if action is CuratedProtocolAction.REPEAT:
            return f"Current step {label} guidance is displayed again on screen.{timer_hint}"
        return f"Moved to step {label}. Its guidance is displayed on screen.{timer_hint}"
    if language == "vi":
        subject = "quy trình"
        if action is CuratedProtocolAction.START:
            verb = "hiển thị lại" if resumed else "hiển thị"
            return f"Hướng dẫn bước {label} của {subject} đã được {verb} trên màn hình.{timer_hint}"
        if action is CuratedProtocolAction.CURRENT:
            return f"Hiện tại là bước {label}. Hướng dẫn được hiển thị trên màn hình.{timer_hint}"
        if action is CuratedProtocolAction.REPEAT:
            return f"Hướng dẫn bước {label} hiện tại đã được hiển thị lại trên màn hình.{timer_hint}"
        return f"Đã chuyển sang bước {label}. Hướng dẫn được hiển thị trên màn hình.{timer_hint}"
    korean_subject = ""
    if action is CuratedProtocolAction.START:
        if resumed:
            return f"{korean_subject}{label}단계 안내를 화면에 다시 표시했습니다.{timer_hint}"
        return f"{korean_subject}{label}단계 안내를 화면에 표시했습니다.{timer_hint}"
    if action is CuratedProtocolAction.CURRENT:
        return f"현재 {label}단계입니다. 안내를 화면에 표시했습니다.{timer_hint}"
    if action is CuratedProtocolAction.REPEAT:
        return f"현재 {label}단계 안내를 다시 표시했습니다.{timer_hint}"
    return f"{label}단계로 이동했습니다. 안내를 화면에 표시했습니다.{timer_hint}"


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


CANONICAL_RESEARCH_ENTITIES: dict[str, dict[str, Any]] = {
    "ambic": {
        "requested_label": "AMBIC",
        "canonical_name": "ammonium bicarbonate",
        "aliases": ("NH4HCO3", "ammonium hydrogen carbonate", "중탄산암모늄", "탄산수소암모늄"),
        "formula": "CH5NO3",
        "cid": 14013,
        "protocol_relation": "25 mM buffer component used for destain and digestion wash in Solutions A and B",
        "query_variants": (
            "ammonium bicarbonate chemical structure",
            "ammonium bicarbonate laboratory reagent appearance photo",
            "ammonium bicarbonate properties",
        ),
        "visual_intents": ("chemical_structure", "reagent_appearance", "product_reference"),
    },
    "dtt": {
        "requested_label": "DTT",
        "canonical_name": "dithiothreitol",
        "aliases": ("Cleland's reagent", "디티티", "디티오트레이톨"),
        "formula": "C4H10O2S2",
        "cid": 439196,
        "protocol_relation": "Reducing agent (10 mM) used to reduce protein disulfide bonds",
        "query_variants": (
            "dithiothreitol chemical structure",
            "dithiothreitol reducing agent laboratory reagent",
        ),
        "visual_intents": ("chemical_structure", "reagent_appearance"),
    },
    "iodoacetamide": {
        "requested_label": "Iodoacetamide",
        "canonical_name": "iodoacetamide",
        "aliases": ("IAA", "2-iodoacetamide", "아이오도아세트아마이드"),
        "formula": "C2H4INO",
        "cid": 3727,
        "protocol_relation": "Alkylating agent (55 mM) used to alkylate free cysteine thiols and prevent disulfide reformation",
        "query_variants": (
            "iodoacetamide chemical structure",
            "iodoacetamide alkylating agent laboratory reagent",
        ),
        "visual_intents": ("chemical_structure", "reagent_appearance"),
    },
    "acetonitrile": {
        "requested_label": "Acetonitrile",
        "canonical_name": "acetonitrile",
        "aliases": ("ACN", "methyl cyanide", "아세토나이트릴"),
        "formula": "C2H3N",
        "cid": 6342,
        "protocol_relation": "Organic solvent used in 2:1 ratio with 25 mM AMBIC for gel shrinkage and destaining (Solution A)",
        "query_variants": (
            "acetonitrile chemical structure",
            "acetonitrile HPLC grade solvent reagent",
        ),
        "visual_intents": ("chemical_structure", "reagent_appearance"),
    },
    "trypsin": {
        "requested_label": "Trypsin",
        "canonical_name": "trypsin",
        "aliases": ("trypsin protease", "트립신"),
        "protocol_relation": "Serine protease enzyme that specifically cleaves peptide chains at lysine and arginine residues",
        "query_variants": (
            "trypsin enzyme structure mass spectrometry grade",
            "trypsin in-gel digestion protease",
        ),
        "visual_intents": ("structure", "product_reference"),
    },
    "formic_acid": {
        "requested_label": "Formic acid",
        "canonical_name": "formic acid",
        "aliases": ("methanoic acid", "HCOOH", "포름산", "폼산"),
        "formula": "CH2O2",
        "cid": 284,
        "protocol_relation": "Extraction and LC-MS acidification agent used in peptide recovery",
        "query_variants": (
            "formic acid chemical structure",
            "formic acid LC-MS grade reagent",
        ),
        "visual_intents": ("chemical_structure", "reagent_appearance"),
    },
    "gel_plug": {
        "requested_label": "Gel plug",
        "canonical_name": "SDS-PAGE gel plug",
        "aliases": ("단백질 밴드", "stained protein band", "1 mm3 plug"),
        "protocol_relation": "1 mm³ excised piece of Coomassie/silver stained polyacrylamide gel containing target protein",
        "query_variants": (
            "SDS-PAGE stained protein band excision gel plug",
            "in-gel digestion gel piece excision",
        ),
        "visual_intents": ("laboratory_appearance", "procedure_visual"),
    },
    "stained_protein_band": {
        "requested_label": "Stained protein band",
        "canonical_name": "stained protein band",
        "aliases": ("염색된 단백질 밴드", "protein band", "SDS-PAGE band"),
        "protocol_relation": "Visualized protein band on polyacrylamide gel matrix",
        "query_variants": (
            "Coomassie stained protein gel band excision",
            "destained gel plug appearance",
        ),
        "visual_intents": ("laboratory_appearance", "procedure_visual"),
    },
    "hplc_water": {
        "requested_label": "HPLC water",
        "canonical_name": "HPLC grade water",
        "aliases": ("HPLC grade water", "초순수"),
        "protocol_relation": "High purity chromatography water used to prepare 25 mM AMBIC solutions",
        "query_variants": (
            "HPLC grade water laboratory reagent",
        ),
        "visual_intents": ("product_reference",),
    },
    "solution_a": {
        "requested_label": "Solution A",
        "canonical_name": "Solution A (AMBIC / Acetonitrile)",
        "aliases": ("용액 A", "Solution A"),
        "protocol_relation": "Destain wash solution composed of 2 parts 25 mM AMBIC and 1 part acetonitrile",
        "query_variants": (
            "ammonium bicarbonate acetonitrile destaining solution SDS-PAGE",
        ),
        "visual_intents": ("procedure_visual",),
    },
    "solution_b": {
        "requested_label": "Solution B",
        "canonical_name": "Solution B (25 mM AMBIC)",
        "aliases": ("용액 B", "Solution B"),
        "protocol_relation": "Wash and trypsin reconstitution buffer consisting of 25 mM ammonium bicarbonate in HPLC water",
        "query_variants": (
            "ammonium bicarbonate buffer solution",
        ),
        "visual_intents": ("procedure_visual",),
    },
}


def canonical_research_plan(entity_key: str) -> dict[str, Any]:
    norm = entity_key.strip().casefold().replace(" ", "_")
    if norm in CANONICAL_RESEARCH_ENTITIES:
        return CANONICAL_RESEARCH_ENTITIES[norm]
    label = entity_key.replace("_", " ").title()
    return {
        "requested_label": label,
        "canonical_name": label,
        "aliases": (),
        "protocol_relation": f"Laboratory reagent or material: {label}",
        "query_variants": (f"{label} laboratory scientific reference",),
        "visual_intents": ("general_reference",),
    }


#: How the protocol text may spell an entity the request inventory names.
#: Lower-case surfaces, matched against the source's own statements; a
#: surface only finds a statement the active PDF actually contains, so the
#: table names spellings, never what any one protocol does with them.
_ENTITY_SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "ambic": ("ambic", "ammonium bicarbonate"),
    "hplc_water": ("hplc water", "hplc"),
    "solution_a": ("solution a",),
    "solution_b": ("solution b",),
    "acetonitrile": ("acetonitrile",),
    "gel_plug": ("gel plug", "plug of a stained protein band"),
    "stained_protein_band": (
        "stained protein band", "protein band", "gel band", "band",
    ),
    "tube": ("microcentrifuge tube", "tube"),
    "dtt": ("dtt", "dithiothreitol"),
    "iodoacetamide": ("iodoacetamide",),
    "trypsin": ("trypsin",),
    "formic_acid": ("formic acid", "formic"),
    "rpm": ("rpm",),
    "incubation": ("incubat",),
    "contamination": ("contaminat", "keratin"),
    "sds_page": ("sds-page", "sds page", "polyacrylamide gel"),
    "electrophoresis": ("electrophoresis",),
    "coomassie": ("coomassie",),
    "destaining": ("destain",),
    "centrifuge": ("centrifug",),
    "pipette": ("pipett",),
    "mass_spectrometry": ("mass spectrometr", "mass spec"),
}

#: The named solutions a definition statement can be found for.
_SOLUTION_SURFACES = {"solution_a": "solution a", "solution_b": "solution b"}

#: The spelling to fall back on when the active PDF does not use the entity.
_ENTITY_FALLBACK_LABELS = {
    "ambic": "AMBIC", "hplc_water": "HPLC water",
    "solution_a": "Solution A", "solution_b": "Solution B",
    "dtt": "DTT", "formic_acid": "formic acid", "gel_plug": "gel plug",
    "stained_protein_band": "stained protein band",
}


#: Surfaces that are word stems ("incubat" for incubate/incubation); every
#: other surface must match as whole words, so "ethanol" is not "methanol"
#: and "PBS" is not "DPBS".
_ENTITY_STEMS = frozenset({
    "incubat", "contaminat", "centrifug", "pipett", "mass spectrometr", "destain",
})


def _entity_surfaces(entity: str) -> tuple[str, ...]:
    return _ENTITY_SOURCE_ALIASES.get(
        entity, (" ".join(entity.replace("_", " ").split()).casefold(),)
    )


def _entity_patterns(entity: str) -> tuple[re.Pattern[str], ...]:
    patterns = []
    for surface in _entity_surfaces(entity):
        words = r"\s+".join(re.escape(word) for word in surface.split())
        tail = r"[a-z]*" if surface in _ENTITY_STEMS else r"(?:e?s)?"
        patterns.append(re.compile(
            rf"(?<![0-9A-Za-z]){words}{tail}(?![0-9A-Za-z])", re.IGNORECASE
        ))
    return tuple(patterns)


def _names_entity(text: str, entity: str) -> bool:
    derived = _derived_source_text(text)
    return any(pattern.search(derived) for pattern in _entity_patterns(entity))


def _source_spelling(entity: str, texts: tuple[str, ...]) -> str | None:
    """The entity as the protocol itself writes it, from the first statement naming it."""

    for pattern in _entity_patterns(entity):
        for text in texts:
            match = pattern.search(_derived_source_text(text))
            if match:
                return match.group(0)
    return None


def _source_abbreviation(label: str, texts: tuple[str, ...]) -> str | None:
    """The long form a statement defines an abbreviation with: "long form (LABEL)"."""

    if not (1 < len(label) <= 10 and any(char.isupper() for char in label)):
        return None
    pattern = re.compile(
        r"(?<![0-9A-Za-z])((?:[A-Za-z][A-Za-z-]*\s+){0,4}[A-Za-z][A-Za-z-]*)"
        rf"\s*\(\s*{re.escape(label)}\s*\)"
    )
    letters = [char for char in label.casefold() if char.isalpha()]

    def spells(words: list[str]) -> bool:
        # The long form starts with the acronym's first letter and holds
        # its letters in order, as "ammonium bicarbonate" holds A-M-B-I-C.
        phrase = " ".join(words).casefold()
        if not letters or not phrase.startswith(letters[0]):
            return False
        position = 0
        for char in letters:
            position = phrase.find(char, position)
            if position < 0:
                return False
            position += 1
        return True

    for text in texts:
        for match in pattern.finditer(_derived_source_text(text)):
            words = match.group(1).split()
            for start in range(len(words) - 1, -1, -1):
                candidate = words[start:]
                if " ".join(candidate).casefold() == label.casefold():
                    continue
                if spells(candidate):
                    return " ".join(candidate)
    return None


def _without_step_prefix(label: str, text: str) -> str:
    """A reviewed step translation without its own leading "N단계:" label."""

    return re.sub(rf"^\s*{re.escape(label)}\s*단계\s*:\s*", "", text).strip()


def _spoken_summary_sentence(summary: str) -> str:
    """End one summary as a sentence: a finished Korean sentence keeps its own ending."""

    text = summary.strip().rstrip(".")
    return f"{text}." if text.endswith("다") else f"{text}입니다."


#: Units a reader translation must keep, each with its canonical name.
#: Prefixes stay distinct (mM is not µM, mL is not µL); a Korean word may
#: stand for a unit only directly after its number ("15분", "37도").
_READER_UNIT_FORMS: tuple[tuple[str, str], ...] = (
    (r"ng\s*/\s*(?:µ|μ|u)[lL]", "ng/µL"),
    (r"mg\s*/\s*m[lL]", "mg/mL"),
    (r"(?:µ|μ|u)[lL]", "µL"),
    (r"m[lL]", "mL"),
    (r"(?:µ|μ|u)M", "µM"),
    (r"nM", "nM"),
    (r"mM", "mM"),
    (r"M", "M"),
    (r"mm³", "mm³"),
    (r"°\s*C|℃|도|C", "°C"),
    (r"[rR][pP][mM]|분당\s*회전", "rpm"),
    (r"min(?:ute)?s?|분", "min"),
    (r"h(?:ours?|rs?)?|시간", "h"),
    (r"s(?:ec(?:ond)?s?)?|초", "s"),
    (r"%|퍼센트", "%"),
    (r"[x×]\s*g", "x g"),
    (r"parts?", "part"),
    (r"times?|번|회", "times"),
)
_READER_QUANTITY = re.compile(
    r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(?:("
    + "|".join(f"(?:{form})" for form, _ in _READER_UNIT_FORMS)
    + r")(?![A-Za-z]))?"
)
_READER_CLOCK = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
_ENGLISH_COUNTS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}
_ENGLISH_REPEATS = {"once": "1 times", "twice": "2 times", "thrice": "3 times"}
#: "Once" opening a sentence as a conjunction ("Once your gel is dry" --
#: "~하면") is not a count. The words that can follow it there.
_ONCE_CONJUNCTION = re.compile(
    r"(?:^|(?<=[.!?;:]\s)|(?<=^\d\s)|(?<=^\d\d\s))once"
    r"(?=\s+(?:you|your|the|it|its|they|their|we|our|this|these|that|those|"
    r"all|each|every|everything|a|an|he|she|samples?|cells?|gels?|bands?)\b)",
    re.IGNORECASE | re.MULTILINE,
)
#: Korean counts, counted the same way on both sides. 하나·둘·셋·넷 are
#: counts wherever they stand as a word; 두·세·네 and 다섯-아홉 before any
#: following word ("두 세척 용액", "세 번"); 한 and 열 only before a counter,
#: because "~을 한 후" and "열 블록" are not counts.
_KOREAN_STANDALONE_COUNTS = {"하나": "1", "둘": "2", "셋": "3", "넷": "4"}
_KOREAN_COUNTS = {
    "두": "2", "세": "3", "네": "4", "다섯": "5", "여섯": "6",
    "일곱": "7", "여덟": "8", "아홉": "9",
}
_KOREAN_COUNTER_ONLY = {"한": "1", "열": "10"}
_KOREAN_COUNTERS = (
    r"(?:번|회|개|방울|조각|배|가지|알|장|쌍|병|통|줄|칸|판|사이클|시간|분|초)"
)
_SOURCE_NEGATION = re.compile(
    r"\b(?:not|no|never|don't|do\s+not|avoid(?:s|ing|ed)?|without|must\s+not|"
    r"cannot|can't)\b",
    re.IGNORECASE,
)
_KOREAN_NEGATION = re.compile(
    r"(?:지\s*(?:마|말|않)|안\s*(?:되|돼|됩)|금지|없이|말고|피(?:하|합|해|했|할)|못\s|않)"
)
#: Protocol terms a Korean reading may put in Korean. The terms a reading
#: must keep in their original spelling are the names of reagents,
#: materials and equipment (resource names, "Solution A", defined
#: abbreviations, acronyms); these are general nouns and descriptions that
#: the protocol vocabulary picks up for speech recognition, and the
#: reviewed in-gel Korean already writes them in Korean (젤 조각, 염색된
#: 단백질 밴드, 배양, 오염, 케라틴).
_TRANSLATABLE_TERMS = frozenset({
    "gel plug", "stained protein band", "incubation", "contamination",
    "keratin", "rpm",
})


def _normalized_counts(value: str) -> str:
    """Count words as digits, the same rules for a source and a reading."""

    value = _ONCE_CONJUNCTION.sub("when", value)
    for word, digits in _ENGLISH_REPEATS.items():
        value = re.sub(rf"\b{word}\b", digits, value, flags=re.IGNORECASE)
    for word, digit in _ENGLISH_COUNTS.items():
        value = re.sub(rf"\b{word}\b", digit, value, flags=re.IGNORECASE)
    for word, digit in _KOREAN_STANDALONE_COUNTS.items():
        value = re.sub(
            rf"(?<![가-힣]){word}(?=(?:를|가|이|의|와|과|씩|로|에|만|도|는|은)?(?![가-힣]))",
            f"{digit} ", value,
        )
    for word, digit in sorted(_KOREAN_COUNTS.items(), key=lambda item: -len(item[0])):
        value = re.sub(rf"(?<![가-힣]){word}\s+(?=[가-힣])", f"{digit} ", value)
        value = re.sub(
            rf"(?<![가-힣]){word}\s*(?={_KOREAN_COUNTERS}(?![가-힣]))", f"{digit} ", value)
    for word, digit in _KOREAN_COUNTER_ONLY.items():
        value = re.sub(
            rf"(?<![가-힣]){word}\s*(?={_KOREAN_COUNTERS})", f"{digit} ", value)
    return value


def _count_quantities(value: str) -> list[tuple[str, str | None]]:
    """``_reader_quantities`` with a repeat count the same as a bare count.

    "two cycles" and "두 번의 사이클", "twice" and "2", say the same number.
    """

    return sorted(
        ((number, None if unit == "times" else unit)
         for number, unit in _reader_quantities(value)),
        key=lambda item: (item[0], item[1] or ""),
    )


def _reader_quantities(value: str) -> list[tuple[str, str | None]]:
    """The (number, unit) pairs a statement states, clock times as one each."""

    value = re.sub(r"(?<=\d),(?=\d{3}\b)", "", value)
    found: list[tuple[str, str | None]] = [
        (match.group(0), "clock") for match in _READER_CLOCK.finditer(value)
    ]
    value = _READER_CLOCK.sub(" ", value)
    for match in _READER_QUANTITY.finditer(value):
        unit = None
        if match.group(2):
            unit = next(
                canonical for form, canonical in _READER_UNIT_FORMS
                if re.fullmatch(form, match.group(2))
            )
        number = match.group(1)
        if "." in number:
            number = number.rstrip("0").rstrip(".")
        found.append((number, unit))
    return sorted(found, key=lambda item: (item[0], item[1] or ""))


def reader_translation_issue(
    source_text: str,
    translation: str,
    *,
    required_terms: tuple[str, ...] = (),
    step_label: str | None = None,
) -> str | None:
    """Why a model's Korean reading of one source statement may not be used.

    The check is mechanical and fails closed. The reading must state the
    same quantities -- each number with its own unit, prefixes distinct, a
    Korean unit word only right after its number, counts written as words
    included, counted by the same rules on both sides ("Once" opening a
    sentence is a conjunction, not a count; a repeat count is a count) --
    and no other; it must keep every reagent, material and equipment name
    the statement uses in its original spelling (general nouns in
    ``_TRANSLATABLE_TERMS`` may be Korean); and it must neither add nor
    drop a negation ("avoid" in any form pairs with "피하"). A leading
    "N단계:" for the statement's own label is allowed. ``None`` means the
    reading may be spoken.
    """

    text = " ".join(str(translation or "").split())
    if not text:
        return "empty"
    if not re.search(r"[가-힣]", text):
        return "not_korean"
    source = _derived_source_text(" ".join(source_text.split()))
    if len(text) > max(600, 3 * len(source)):
        return "too_long"
    reading = _derived_source_text(text)
    if step_label:
        reading = re.sub(
            rf"^\s*{re.escape(step_label)}\s*단계\s*[:：]?\s*", "", reading)
        source = re.sub(rf"^\s*{re.escape(step_label)}(?![0-9.])\s*", "", source)
    if _count_quantities(_normalized_counts(source)) != _count_quantities(
            _normalized_counts(reading)):
        return "quantities_changed"
    if bool(_SOURCE_NEGATION.search(source)) != bool(_KOREAN_NEGATION.search(reading)):
        return "negation_changed"
    folded = text.casefold()
    for term in required_terms:
        if term.casefold() in _TRANSLATABLE_TERMS:
            continue
        if term.casefold() not in folded:
            return "term_missing"
    return None


class CuratedProtocolSession:
    """Server-owned in-memory state for one validated structured fixture.

    There are two ways in, and only one is gated. ``ProtocolCatalog``'s
    ``load_executable_fixture`` builds a fixture from a stored analysis and
    refuses unless the Protocol is approved or development-activated, which
    requires readiness to be clear. Direct construction, which the replay
    harness and the demo path use, has no such check. Nothing produced by the
    analysis pipeline has ever come through the gated route -- see
    docs/PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md -- so this is currently the
    only route that carries anything at all.
    """

    def __init__(self, fixture: CuratedProtocolFixture) -> None:
        self.fixture = fixture
        #: Presentation only: a stored machine translation has been shown in
        #: this session, so the page keeps its automatic-translation line.
        self.machine_translation_shown = False
        self.active = False
        self.current_index = 0
        self._revision = 0
        self._block_reason: str | None = None
        self._replay: dict[int, CuratedProtocolTurnPlan] = {}
        self._recent_verified_entities: list[str] = []
        self._vocabulary_cache: tuple[CuratedProtocolFixture, ProtocolVocabulary] | None = None
        self._pending_clarification: str | None = None
        self._last_related_query: str | None = None
        self._last_related_entities: tuple[str, ...] = ()
        self._discourse_context = ProtocolDiscourseContext()
        self._pending_completion_confirmation: PendingCompletionConfirmation | None = None
        self._pending_observation_confirmation: PendingObservationConfirmation | None = None
        self._pending_transcript_confirmation: PendingTranscriptConfirmation | None = None
        self._workflow_status: str = "preview"
        self._last_semantic_decision: SemanticIntentDecision | None = None
        #: The front rule (FRONT_RULES) that planned the last turn, or None
        #: when no front rule owned it. Telemetry only; never rolled back.
        self._last_front_rule: str | None = None
        self._operator_repetition_counts: dict[str, dict[str, object]] = {}
        # Experiment-session facts, not reviewer findings: cleared on reset
        # with the rest of the session, and never written to the approval
        # ledger.
        self._acknowledged_unread_pages: dict[int, dict[str, object]] = {}
        #: step_id -> the endpoint observation that released that step's gate.
        #:
        #: A repeat-until step waits on a visible endpoint the source states,
        #: and only the person at the bench can report it. Until STEP 41 that
        #: report was a one-turn authorisation: it satisfied the gate inside
        #: the turn that carried it and left nothing behind, so the gate could
        #: only ever rest on readiness saying the construct was unsupported.
        #: Written down, the report becomes the gate's own footing -- which is
        #: what lets the capability be declared without the gate falling with
        #: it. What is stored is what was said and who said it, never a count
        #: of rounds run: the source states no number and neither does this.
        self._endpoint_observations: dict[str, dict[str, object]] = {}
        #: repetition_id -> what happened at a human-led repeat interval on
        #: this run: when the agent handed it over, and when a person said it
        #: was finished. The number of rounds is deliberately absent -- the
        #: agent does not count them, so it has nothing truthful to record.
        self._repeat_intervals: dict[str, dict[str, object]] = {}
        #: Step indices whose source warning text has been read out on this
        #: run. Not an acknowledgement: nobody agrees to anything by hearing a
        #: warning, and nothing here clears a readiness gate.
        self._disclosed_safety_warnings: dict[int, dict[str, object]] = {}
        self._timer_started_at: float | None = None
        self._timer_duration_seconds: int | None = None
        self._timer_step_index: int | None = None
        self._experiment_started_at: float | None = None
        self._experiment_ended_at: float | None = None
        self._pending_anomaly: dict[str, Any] | None = None
        self._pending_note_capture: dict[str, Any] | None = None
        self._pause_state: str = "active"
        self._paused_at: float | None = None
        #: The workflow status a pause replaced, for undo_pause().
        self._status_before_pause: str = "preview"
        self._total_paused_seconds: float = 0.0
        self._pause_intervals: list[dict[str, Any]] = []
        #: Whether this pause's notice was already said aloud. Only the first
        #: word turned away in a pause is answered by voice; the rest are
        #: shown only. A new pause starts the count again.
        self._paused_notice_spoken: bool = False
        #: The one-turn "실험을 종료할까요?" question: a "종료" command asks it,
        #: and only a yes to it on the next turn ends the session.
        self._pending_stop_confirmation: dict[str, Any] | None = None
        #: A completion, observation or end question that was open when a
        #: voice pause was taken. It is asked again when the pause is lifted
        #: by voice, and dropped by anything else that ends the pause.
        self._frozen_question: dict[str, Any] | None = None
        #: The one-turn "원문은 15분입니다. 15분으로 시작할까요?" question
        #: (decision D6): a tool proposal that names a duration other than
        #: the source's asks it, and only a yes to it starts the timer.
        self._pending_timer_confirmation: dict[str, Any] | None = None
        #: "이상 사항으로 기록할까요?" (decision 1), asked about an anomaly a
        #: model proposed in words the rules do not read as a problem. Only
        #: apply_tool_proposal opens it; a yes on the next turn records them.
        self._pending_anomaly_confirmation: dict[str, Any] | None = None
        self._pending_handoff_confirmation: dict[str, Any] | None = None
        self.safety_pack: Any = None

    def set_safety_pack(self, safety_pack: Any) -> None:
        self.safety_pack = safety_pack

    @property
    def pending_completion_confirmation(self) -> PendingCompletionConfirmation | None:
        return self._pending_completion_confirmation

    @property
    def pending_observation_confirmation(self) -> PendingObservationConfirmation | None:
        return self._pending_observation_confirmation

    @property
    def pending_transcript_confirmation(self) -> PendingTranscriptConfirmation | None:
        return self._pending_transcript_confirmation

    @property
    def workflow_status(self) -> str:
        if not self.active:
            return self._workflow_status or "preview"
        return self._workflow_status or "active"

    @property
    def awaiting_server_confirmation(self) -> bool:
        """True while a server-owned gate already owns this turn's meaning."""

        return any((
            self._pending_completion_confirmation is not None,
            self._pending_observation_confirmation is not None,
            self._pending_transcript_confirmation is not None,
            self._pending_stop_confirmation is not None,
            self._pending_timer_confirmation is not None,
            self._pending_anomaly_confirmation is not None,
            bool(self._pending_note_capture),
            bool(self._pending_anomaly),
        ))

    def timer_seconds_for_step(self, index: int) -> int:
        """The verified duration for one step, or zero when there is none.

        Looked up by step id from a manifest the loader checked against the
        source. This replaced a dict keyed by step *index* and hardcoded in
        this module: it was bound to no document, no page and no segment, so it
        applied to whatever protocol happened to sit at those positions.
        """

        if not 0 <= index < len(self.fixture.steps):
            return 0
        step_id = self.fixture.steps[index].step_id
        return int((self.fixture.timer_manifest or {}).get(step_id, 0))

    def start_timer(self, step_index: int | None = None, now: float | None = None) -> tuple[bool, int, str]:
        idx = self.current_index if step_index is None else step_index
        duration = self.timer_seconds_for_step(idx)
        if duration <= 0:
            return False, 0, "No timer configured for this step"
        current_time = time.time() if now is None else now
        self._timer_started_at = current_time
        self._timer_duration_seconds = duration
        self._timer_step_index = idx
        return True, duration, f"Started {duration}s timer"

    def timer_status(self, now: float | None = None) -> dict[str, Any]:
        step = (
            self.fixture.steps[self.current_index]
            if 0 <= self.current_index < len(self.fixture.steps)
            else None
        )
        current_time = time.time() if now is None else now
        if self._timer_started_at is None or self._timer_duration_seconds is None or self._timer_step_index is None:
            duration = self.timer_seconds_for_step(self.current_index)
            return {
                "state": "not_started",
                "duration_seconds": duration,
                "remaining_seconds": duration,
                "step_index": self.current_index,
                "step_id": step.step_id if step is not None else None,
                "step_label": step.source_label if step is not None else None,
                "deadline_at": None,
            }
        elapsed = current_time - self._timer_started_at
        remaining = max(0, int(round(self._timer_duration_seconds - elapsed)))
        state = "running" if remaining > 0 else "expired"
        deadline = datetime.fromtimestamp(
            self._timer_started_at + self._timer_duration_seconds,
            tz=timezone.utc,
        ).isoformat()
        timed_step = (
            self.fixture.steps[self._timer_step_index]
            if 0 <= self._timer_step_index < len(self.fixture.steps)
            else step
        )
        return {
            "state": state,
            "duration_seconds": self._timer_duration_seconds,
            "remaining_seconds": remaining,
            "elapsed_seconds": int(round(elapsed)),
            "step_index": self._timer_step_index,
            "step_id": timed_step.step_id if timed_step is not None else None,
            "step_label": timed_step.source_label if timed_step is not None else None,
            "deadline_at": deadline,
            "started_at": datetime.fromtimestamp(
                self._timer_started_at, tz=timezone.utc
            ).isoformat(),
        }

    def experiment_timer_status(self, now: float | None = None) -> dict[str, Any]:
        current_time = time.time() if now is None else now
        if self._experiment_started_at is None:
            return {
                "state": "not_started",
                "started_at": None,
                "ended_at": None,
                "elapsed_seconds": 0,
            }
        ended_at = self._experiment_ended_at
        elapsed_end = ended_at if ended_at is not None else current_time
        elapsed = max(0, int(round(elapsed_end - self._experiment_started_at)))
        if ended_at is not None:
            state = "stopped"
        else:
            state = "running"
        return {
            "state": state,
            "started_at": datetime.fromtimestamp(
                self._experiment_started_at, tz=timezone.utc
            ).isoformat(),
            "ended_at": (
                datetime.fromtimestamp(ended_at, tz=timezone.utc).isoformat()
                if ended_at is not None
                else None
            ),
            "elapsed_seconds": elapsed,
        }

    def pause_timer_status(self, now: float | None = None) -> dict[str, Any]:
        current_time = time.time() if now is None else now
        current_pause = 0
        if self._pause_state == "paused" and self._paused_at is not None:
            current_pause = max(0, int(round(current_time - self._paused_at)))
        total = int(round(self._total_paused_seconds + current_pause))
        return {
            "state": self._pause_state,
            "paused_at": (
                datetime.fromtimestamp(self._paused_at, tz=timezone.utc).isoformat()
                if self._paused_at is not None
                else None
            ),
            "total_paused_seconds": total,
            "current_pause_seconds": current_pause,
            "interval_count": len(self._pause_intervals) + (1 if self._pause_state == "paused" else 0),
            "intervals": list(self._pause_intervals),
        }

    @property
    def experiment_ended(self) -> bool:
        """The experiment ran and ended (stopped or completed): it is not running.

        Nothing pauses or resumes it any more (decision 2 of 2026-10-03, lane
        R3); the next experiment is chosen on the screen.
        """

        return not self.active and self._experiment_ended()

    def pause_workflow(self, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        if self._pause_state == "paused" or self.experiment_ended:
            return False
        self._status_before_pause = self._workflow_status
        self._pause_state = "paused"
        self._paused_at = current_time
        self._workflow_status = "paused"
        self._paused_notice_spoken = False
        return True

    def undo_pause(self) -> None:
        """Take back a pause the durable record refused, and nothing else.

        The bench button's rollback. It used to call resume_workflow(), which
        also starts a protocol that is not running -- from step 1. This only
        puts the pause state back as it was before pause_workflow(); no pause
        interval is recorded, because none was kept.
        """

        if self._pause_state != "paused":
            return
        self._pause_state = "active"
        self._paused_at = None
        self._workflow_status = self._status_before_pause
        self._paused_notice_spoken = False

    def resume_workflow(self, now: float | None = None) -> bool:
        current_time = time.time() if now is None else now
        if self.experiment_ended:
            # An ended experiment is not run again from step 1 (decision 2):
            # the bench pause's rollback used to come through here and
            # restart it while its durable record stayed stopped.
            return False
        if not self.active:
            self.active = True
            self.current_index = 0
            self._start_experiment_clock_once(now=current_time)
        if self._pause_state != "paused":
            self._workflow_status = "active"
            return False
        duration = max(0.0, current_time - (self._paused_at or current_time))
        self._total_paused_seconds += duration
        step_label = (
            self.fixture.steps[self.current_index].source_label
            if 0 <= self.current_index < len(self.fixture.steps)
            else None
        )
        self._pause_intervals.append({
            "started_at": (
                datetime.fromtimestamp(self._paused_at, tz=timezone.utc).isoformat()
                if self._paused_at is not None
                else None
            ),
            "resumed_at": datetime.fromtimestamp(current_time, tz=timezone.utc).isoformat(),
            "duration_seconds": round(duration, 2),
            "step_index": self.current_index,
            "step_label": step_label,
        })
        self._pause_state = "active"
        self._paused_at = None
        self._workflow_status = "active"
        self._paused_notice_spoken = False
        # A question held through the pause is asked again only by the voice
        # resume in plan(), which takes it before calling this.
        self._frozen_question = None
        return True

    def _clear_step_timer(self) -> None:
        self._timer_started_at = None
        self._timer_duration_seconds = None
        self._timer_step_index = None

    def _stop_experiment_clock(self, now: float | None = None) -> None:
        if self._experiment_started_at is None:
            return
        if self._experiment_ended_at is None:
            self._experiment_ended_at = time.time() if now is None else now

    def _experiment_ended(self) -> bool:
        """The experiment ran and has ended: stopped, or every step completed."""

        return (
            self._experiment_started_at is not None
            and self._experiment_ended_at is not None
        )

    def _start_experiment_clock_once(self, now: float | None = None) -> bool:
        if self._experiment_started_at is not None:
            return False
        self._experiment_started_at = time.time() if now is None else now
        self._experiment_ended_at = None
        return True

    def _record_early_step_timer_exit(self) -> dict[str, Any] | None:
        if (
            self._timer_started_at is None
            or self._timer_duration_seconds is None
            or self._timer_step_index is None
        ):
            return None
        status = self.timer_status()
        if status.get("state") != "running":
            return None
        return {
            "demo_bypassed": True,
            "step_exited_before_timer_elapsed": True,
            "step_id": status.get("step_id"),
            "step_label": status.get("step_label"),
            "source_duration_seconds": status.get("duration_seconds"),
            "started_at": status.get("started_at"),
            "elapsed_seconds": status.get("elapsed_seconds"),
            "remaining_seconds": status.get("remaining_seconds"),
            "completion_state": "step_exited_before_timer_elapsed",
        }

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
            "dtt": ("dtt", "dithiothreitol"),
            "iodoacetamide": ("iodoacetamide",),
            "trypsin": ("trypsin",),
            "formic acid": ("formic acid", "formic"),
            "rpm": ("rpm",),
            "incubation": ("incubat",),
            "contamination": ("contaminat", "오염"),
        }
        present = [
            entity for entity, terms in aliases.items()
            if any(term in text for term in terms)
        ]
        return tuple(dict.fromkeys((*present, *self._recent_verified_entities)))

    def execution_fingerprint(self, configuration_id: int | None = None) -> WorkflowExecutionFingerprint:
        step = self.fixture.steps[self.current_index] if 0 <= self.current_index < len(self.fixture.steps) else None
        return WorkflowExecutionFingerprint(
            protocol_id=self.fixture.protocol_id,
            configuration_id=configuration_id,
            run_id=getattr(self, "_session_run_id", None),
            active=self.active,
            workflow_status=self.workflow_status,
            current_step_id=step.step_id if step else None,
            current_index=self.current_index,
            experiment_started_at=self._experiment_started_at,
        )

    def canonical_research_plan(self, entity_key: str) -> dict[str, Any]:
        norm = entity_key.strip().casefold().replace(" ", "_")
        if norm in CANONICAL_RESEARCH_ENTITIES:
            return CANONICAL_RESEARCH_ENTITIES[norm]
        label = entity_key.replace("_", " ").title()
        return {
            "requested_label": label,
            "canonical_name": label,
            "aliases": (),
            "protocol_relation": f"Laboratory reagent or material: {label}",
            "query_variants": (f"{label} laboratory scientific reference",),
            "visual_intents": ("general_reference",),
        }

    def _protocol_vocabulary(self) -> ProtocolVocabulary:
        """The active fixture's vocabulary, derived once per fixture."""

        cached = self._vocabulary_cache
        if cached is None or cached[0] is not self.fixture:
            cached = (self.fixture, ProtocolVocabulary.from_fixture(self.fixture))
            self._vocabulary_cache = cached
        return cached[1]

    def research_scope(self) -> dict[str, Any]:
        """What ``plan_research_query`` needs to judge this protocol's substances.

        Passed as keyword arguments, it lets the query use an in-gel search
        label only where this protocol has the substance, judged on the whole
        protocol, and name this protocol's materials as its steps do.
        """

        vocabulary = self._protocol_vocabulary()
        return {
            "protocol_materials": vocabulary.materials,
            "protocol_text": vocabulary.corpus,
        }

    def stt_keyterms(self, *, include_control_terms: bool = False) -> tuple[str, ...]:
        """Return the active protocol's technical terms within xAI's cap.

        The STT request receives a bounded technical vocabulary (chemical reagents,
        materials, and scientific nouns), not command sentences or workflow phrases.
        Ordinary command concepts are handled by the intent classifier after transcription.

        The vocabulary is the active protocol's own (``ProtocolVocabulary``):
        protocol-wide, with the terms around the current step first. The step
        and control phrases are kept whole and the protocol's terms take the
        rest of the cap.
        """

        step_tokens: tuple[str, ...] = ()
        index = (
            self.current_index
            if 0 <= self.current_index < len(self.fixture.steps)
            else None
        )
        if self.active and index is not None:
            lbl = self.fixture.steps[index].source_label
            step_tokens = (f"{lbl}단계", f"{lbl} 단계", f"현재 {lbl}단계", f"이번 {lbl}단계")
        control_korean: tuple[str, ...] = ()
        if include_control_terms:
            control_korean = (
                "아니", "네", "현재 단계", "이번 단계", "완료", "완료했어",
                "시작", "다음 단계", "다시 알려줘",
            )
        fixed = tuple(dict.fromkeys(step_tokens + control_korean))
        scientific = tuple(
            term for term in self._protocol_vocabulary().keyterms_near(index)
            if term not in fixed
        )[:max(0, _STT_KEYTERM_CAP - len(fixed))]
        return tuple(dict.fromkeys(scientific + fixed))[:_STT_KEYTERM_CAP]

    def operator_repetition_counts(self) -> dict[str, dict[str, object]]:
        """Counts an experimenter has supplied in this session, with provenance."""

        return {
            repetition_id: dict(record)
            for repetition_id, record in self._operator_repetition_counts.items()
        }

    def provide_operator_repetition_count(
        self,
        repetition_id: str,
        count: int,
        *,
        actor_principal_id: str,
        actor_role: str,
    ) -> None:
        """Record the number an experimenter chose for one repetition.

        The document deliberately does not state it -- "for the required number
        of replicates" hands the choice to the experimenter -- so it is decided
        here, at session start, and not at approval: the number differs per
        experimental design. Neither the server nor a model may supply it, and
        who supplied it and when are recorded with it.
        """

        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("A supplied repetition count must be positive.")
        if not actor_principal_id.strip() or not actor_role.strip():
            raise ValueError("A supplied repetition count needs a named actor.")
        if repetition_id not in self._operator_determined_repetitions():
            raise ValueError("This protocol has no such open repetition.")
        self._operator_repetition_counts[repetition_id] = {
            "count": count,
            "actor_principal_id": actor_principal_id,
            "actor_role": actor_role,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    def _operator_determined_repetitions(self) -> dict[str, tuple[str, ...]]:
        """Open repetitions, mapped to the step ids each one covers."""

        found: dict[str, tuple[str, ...]] = {}
        for construct in self.fixture.draft.protocol.constructs:
            if type(construct).__name__ != "OperatorDeterminedRepetition":
                continue
            found[construct.repetition_id] = (
                construct.start_step_id,
                construct.end_step_id,
            )
        return found

    # --- repeat intervals a person leads ---------------------------------

    def _repeat_intervals_by_id(self) -> dict[str, dict[str, object]]:
        """Every repetition the analysis carries, as a range of step ids.

        Read off the assembled constructs, which is where the server put what
        it resolved. Repeats the analysis never captured are absent by
        construction -- they cannot be led from data that does not exist, and
        ``source_states_an_uncaptured_repetition`` is what keeps a document
        with any of those out of execution.
        """

        order = [step.step_id for step in self.fixture.steps]
        found: dict[str, dict[str, object]] = {}
        for construct in self.fixture.draft.protocol.constructs:
            covered = getattr(construct, "repeated_step_ids", None)
            if covered is None:
                start = getattr(construct, "start_step_id", None)
                end = getattr(construct, "end_step_id", None)
                if start is None or end is None:
                    continue
                if start in order and end in order:
                    covered = tuple(
                        order[index]
                        for index in range(order.index(start), order.index(end) + 1)
                    )
                else:
                    covered = tuple(item for item in (start, end) if item)
            if not covered:
                continue
            found[construct.repetition_id] = {
                "repetition_id": construct.repetition_id,
                "kind": type(construct).__name__,
                "repeated_step_ids": tuple(covered),
                # The step whose text carries the repeat sentence, which is
                # not always the last step of the range: in-gel's page 8
                # writes "repeat steps 17-18" inside step 20's block.
                "anchor_step_id": getattr(construct, "step_id", None),
                # The document's own sentence. Whichever construct this is, the
                # text is the source's, reconstructed from its bytes.
                "source_text": (
                    getattr(construct, "condition_source_text", None)
                    or getattr(construct, "range_source_text", "")
                ),
                "source_page_number": getattr(
                    getattr(construct, "evidence", None), "source_page_number", None
                ),
            }
        return found

    def repetition_anchored_at(self, step_id: str) -> dict[str, object] | None:
        """The repetition whose sentence is written in this step's text."""

        for interval in self._repeat_intervals_by_id().values():
            if interval.get("anchor_step_id") == step_id:
                return interval
        return None

    def _range_labels(self, interval: dict[str, object]) -> tuple[str, str]:
        """The first and last source labels of a repeated range."""

        labels = {step.step_id: step.source_label for step in self.fixture.steps}
        covered = tuple(interval["repeated_step_ids"])  # type: ignore[arg-type]
        return (
            labels.get(covered[0], str(covered[0])),
            labels.get(covered[-1], str(covered[-1])),
        )

    def repeat_interval_starting_at(self, index: int) -> dict[str, object] | None:
        """The interval this step opens, if it opens one."""

        if not 0 <= index < len(self.fixture.steps):
            return None
        step_id = self.fixture.steps[index].step_id
        for interval in self._repeat_intervals_by_id().values():
            if interval["repeated_step_ids"][0] == step_id:
                return interval
        return None

    def human_led_repeat_disclosure(self, index: int) -> dict[str, object] | None:
        """What the agent says when a repeat interval opens, and nothing more.

        Four things: that this is a repeat interval, which steps it covers,
        the document's own sentence read exactly as written, and that the
        person decides when it is done. There is no fifth thing. The agent
        does not say how many rounds to run, does not say a round was enough,
        and does not report an observation -- all three would be a completion
        criterion the source never gave.
        """

        interval = self.repeat_interval_starting_at(index)
        if interval is None:
            return None
        labels = {step.step_id: step.source_label for step in self.fixture.steps}
        covered = [
            labels.get(step_id, step_id)
            for step_id in interval["repeated_step_ids"]
        ]
        record = self._repeat_intervals.get(str(interval["repetition_id"]))
        return {
            "repetition_id": interval["repetition_id"],
            "kind": interval["kind"],
            "repeated_step_labels": covered,
            "source_page_number": interval["source_page_number"],
            # Read aloud verbatim. Never summarized, never paraphrased.
            "source_text": interval["source_text"],
            "notice": (
                f"{covered[0]}번부터 {covered[-1]}번까지는 반복 구간입니다. "
                "원문 조건을 그대로 읽어 드립니다. 언제 끝낼지는 원문을 보고 "
                "직접 판단해 주시고, 끝나면 말씀해 주세요."
            ),
            "who_decides_completion": "operator",
            "handed_over_at": None if record is None else record.get("handed_over_at"),
            "completed": bool(record and record.get("completed_at")),
        }

    def enter_repeat_interval(
        self,
        index: int,
        *,
        at: str,
    ) -> dict[str, object] | None:
        """Record that the agent handed an interval over, and return the words."""

        disclosure = self.human_led_repeat_disclosure(index)
        if disclosure is None:
            return None
        if not str(at).strip():
            raise ValueError("A repeat interval hand-over needs a timestamp.")
        identifier = str(disclosure["repetition_id"])
        record = self._repeat_intervals.setdefault(identifier, {})
        record.setdefault("handed_over_at", at)
        disclosure["handed_over_at"] = record["handed_over_at"]
        return disclosure

    def declare_repeat_interval_complete(
        self,
        repetition_id: str,
        *,
        at: str,
        actor_principal_id: str,
        actor_role: str,
    ) -> None:
        """Record that a person said the interval is finished.

        Only a person may say this. The agent has no view on it, and the
        number of rounds is not recorded because the agent did not count them
        -- a figure it inferred would be a guess in an experiment record.
        """

        if repetition_id not in self._repeat_intervals_by_id():
            raise ValueError("That repetition is not part of this Protocol.")
        if not str(at).strip():
            raise ValueError("A completion declaration needs a timestamp.")
        if not str(actor_principal_id).strip() or not str(actor_role).strip():
            raise ValueError("A completion declaration needs an actor.")
        record = self._repeat_intervals.setdefault(str(repetition_id), {})
        record["completed_at"] = at
        record["declared_by_principal_id"] = actor_principal_id
        record["declared_by_role"] = actor_role

    def repeat_intervals_awaiting_completion(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                identifier
                for identifier, interval in self._repeat_intervals_by_id().items()
                if not (
                    self._repeat_intervals.get(identifier, {}).get("completed_at")
                )
            )
        )

    def repeat_interval_record(self, repetition_id: str) -> dict[str, object]:
        """What the session recorded: hand-over, declaration, and who."""

        return dict(self._repeat_intervals.get(str(repetition_id), {}))

    def may_leave_repeat_interval(self, step_id: str) -> bool:
        """False while a step sits past a repeat nobody has closed.

        Its own predicate rather than a clause inside ``may_begin_step``: the
        question is not whether this step can start but whether the run may
        walk out of a repeat, and only a person answers that.

        Neither this nor ``may_begin_step`` is consulted by the turn handler
        today -- the advance is a bare ``current_index += 1`` -- so this states
        the rule without yet enforcing it. That gap is why the capability
        profile still does not declare repeat-until support.
        """

        for index, step in enumerate(self.fixture.steps):
            if step.step_id == step_id:
                return not self._step_is_after_an_open_interval(index)
        return True

    def _step_is_after_an_open_interval(self, index: int) -> bool:
        """True when a step follows a repeat nobody has declared finished."""

        if not 0 <= index < len(self.fixture.steps):
            return False
        order = [step.step_id for step in self.fixture.steps]
        step_id = order[index]
        for identifier, interval in self._repeat_intervals_by_id().items():
            covered = interval["repeated_step_ids"]
            if step_id in covered:
                continue
            last = covered[-1]
            if last in order and order.index(last) < index:
                if not self._repeat_intervals.get(identifier, {}).get(
                    "completed_at"
                ):
                    return True
        return False

    # --- pages the machine did not finish reading ------------------------

    def _unread_pages(self) -> dict[int, tuple[str, ...]]:
        return dict(getattr(self.fixture, "unread_pages", None) or {})

    def step_is_on_an_unread_page(self, index: int) -> bool:
        """Whether this step's own evidence page was not fully accounted for."""

        if not 0 <= index < len(self.fixture.steps):
            return False
        page = self.fixture.steps[index].evidence.source_page_number
        return page in self._unread_pages()

    def unread_page_disclosure(self, index: int) -> dict[str, object] | None:
        """What the experimenter must be told before this step, verbatim.

        The page's own text, and the text of every segment the analysis left
        unaccounted, are returned as they are in the source. No summary and no
        paraphrase: the reason this disclosure exists is that the system's
        reading of the page is not trustworthy, so a reading of it is the one
        thing that must not be offered in its place.
        """

        if not self.step_is_on_an_unread_page(index):
            return None
        page = self.fixture.steps[index].evidence.source_page_number
        extraction = self.fixture.draft.extraction
        segment_ids = self._unread_pages().get(page, ())
        page_text = ""
        if 1 <= page <= extraction.page_count:
            page_text = extraction.pages[page - 1].text
        omitted: list[dict[str, object]] = []
        if segment_ids:
            from .protocol_claim_analysis import (
                generate_page_evidence_segments,
            )

            try:
                segments = generate_page_evidence_segments(
                    extraction,
                    source_revision=self.fixture.revision_id or "pdf-1",
                    page_number=page,
                )
            except Exception:  # noqa: BLE001 - no text rather than a guess
                segments = ()
            wanted = set(segment_ids)
            omitted = [
                {
                    "segment_id": segment.segment_id,
                    "segment_index": segment.segment_index,
                    "source_text": segment.text,
                }
                for segment in segments
                if segment.segment_id in wanted
            ]
        return {
            "source_page_number": page,
            "reading_status": "incomplete",
            "notice": (
                "이 페이지는 시스템이 완전히 읽지 못했습니다. "
                "원문을 직접 확인해 주세요."
            ),
            "source_page_text": page_text,
            "unaccounted_segments": omitted,
            "acknowledged": page in self._acknowledged_unread_pages,
        }

    def acknowledge_unread_page(
        self,
        page_number: int,
        *,
        actor_principal_id: str,
        actor_role: str,
    ) -> None:
        """Record that the experimenter read the page's own words themselves.

        This is an experiment-session fact -- who was standing there and what
        they looked at -- and it is deliberately not a reviewer finding: it
        clears no readiness gate and appears in no approval ledger. It is
        sayable, because the hands it belongs to are in gloves.
        """

        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number not in self._unread_pages()
        ):
            raise ValueError("That page is not recorded as incompletely read.")
        if not str(actor_principal_id).strip() or not str(actor_role).strip():
            raise ValueError("An unread-page acknowledgement needs an actor.")
        self._acknowledged_unread_pages[page_number] = {
            "actor_principal_id": actor_principal_id,
            "actor_role": actor_role,
        }

    def unread_pages_awaiting_acknowledgement(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                page
                for page in self._unread_pages()
                if page not in self._acknowledged_unread_pages
            )
        )

    def may_report_step_complete(self, index: int) -> bool:
        """False on a page the system did not finish reading.

        Completion is the one claim this system must never make on its own
        authority about text it did not read. The experimenter may still say
        the step is done; what is refused is the agent saying so.
        """

        if not self.step_is_on_an_unread_page(index):
            return True
        page = self.fixture.steps[index].evidence.source_page_number
        return page in self._acknowledged_unread_pages

    def step_declares_safety_warnings(self, index: int) -> bool:
        """Whether the source states a hazard at this step."""

        return bool(self._safety_warning_statements(index))

    def _safety_warning_statements(self, index: int) -> tuple[Any, ...]:
        """This step's warnings, its sub-actions' included, in source order."""

        if not 0 <= index < len(self.fixture.steps):
            return ()
        step = self.fixture.steps[index]
        statements = list(step.warnings)
        for action in step.sub_actions:
            statements.extend(action.warnings)
        return tuple(statements)

    def safety_warning_disclosure(self, index: int) -> dict[str, object] | None:
        """What must be read out at this step, in the source's own words.

        The provider chose which passage is a hazard; it did not write the
        passage. What is returned here is ``evidence.source_excerpt``, which
        the server reconstructed from the document's own bytes, so a
        disclosure carries the source's wording even when the model's
        reasoning about it was poor. A summary would put the model between an
        operator and a hazard, which is the one place it must never be.

        None means the source states no hazard at this step. That is not the
        same as a safe step, and nothing here says it is.
        """

        statements = self._safety_warning_statements(index)
        if not statements:
            return None
        record = self._disclosed_safety_warnings.get(index)
        return {
            "step_index": index,
            "step_id": self.fixture.steps[index].step_id,
            "notice": (
                "이 단계에는 원문에 기재된 안전 경고가 있습니다. "
                "원문 그대로 읽어 드립니다."
            ),
            "warnings": [
                {
                    "statement_id": statement.statement_id,
                    "source_page_number": statement.evidence.source_page_number,
                    # The document's words, not ours.
                    "source_text": statement.evidence.source_excerpt,
                    "evidence_segment_ids": list(
                        statement.evidence.evidence_segment_ids
                    ),
                }
                for statement in statements
            ],
            "disclosed": record is not None,
            "disclosed_by": record,
        }

    def record_safety_warning_disclosure(
        self,
        index: int,
        *,
        actor_principal_id: str,
        actor_role: str,
    ) -> None:
        """Record that this step's warning text was read out on this run.

        An experiment-session fact, like an unread-page acknowledgement and for
        the same reason: it says what happened at the bench. It is not an
        approval, it settles no reviewer finding, and it clears no readiness
        gate -- a hazard that was read out is still a hazard.
        """

        if not self._safety_warning_statements(index):
            raise ValueError("That step declares no safety warning.")
        if not str(actor_principal_id).strip() or not str(actor_role).strip():
            raise ValueError("A safety warning disclosure needs an actor.")
        self._disclosed_safety_warnings[index] = {
            "actor_principal_id": actor_principal_id,
            "actor_role": actor_role,
        }

    def steps_awaiting_a_safety_disclosure(self) -> tuple[int, ...]:
        """Steps whose warnings the source states and this run has not read."""

        return tuple(
            index
            for index in range(len(self.fixture.steps))
            if self._safety_warning_statements(index)
            and index not in self._disclosed_safety_warnings
        )

    def repetitions_awaiting_a_count(self) -> tuple[str, ...]:
        """Open repetitions with no number yet. Execution must not start these."""

        return tuple(
            sorted(
                repetition_id
                for repetition_id in self._operator_determined_repetitions()
                if repetition_id not in self._operator_repetition_counts
            )
        )

    def may_begin_step(self, step_id: str) -> bool:
        """False while something this step depends on is still unsettled.

        Three reasons. Two are the same shape: the agent would otherwise
        proceed past a thing only a person can settle. A repetition with no
        number would be run to some default and announced complete on work
        the experimenter never sized. A page the machine did not finish
        reading would be executed from a partial reading of it.

        The third is not something a person settles but something the system
        owes: a step whose source states a hazard does not begin until that
        hazard's own words have been read out on this run.

        The repeat interval is deliberately *not* one of them. Leaving a
        repeat is a person's judgement too, but it is a different question
        from anything above and it has its own predicate --
        ``may_leave_repeat_interval`` -- rather than being folded in here.
        Folding it in made a test that proves the unread-page rule leaves a
        clean document alone fail for a reason that has nothing to do with
        unread pages, which is a sign the two do not belong in one answer.
        """

        for index, step in enumerate(self.fixture.steps):
            if step.step_id != step_id:
                continue
            if self.step_is_on_an_unread_page(index) and not (
                self.may_report_step_complete(index)
            ):
                return False
            # A warning the source states is read before the step it governs,
            # not after. This is the duty that replaced the reviewer gate for
            # a document as registered, so it has to bind on the run rather
            # than be available to it.
            if (
                self._safety_warning_statements(index)
                and index not in self._disclosed_safety_warnings
            ):
                return False
        for repetition_id, bounds in (
            self._operator_determined_repetitions().items()
        ):
            if step_id not in bounds:
                continue
            if repetition_id not in self._operator_repetition_counts:
                return False
        return True

    def reader_translation_target(
        self, plan: CuratedProtocolTurnPlan,
    ) -> tuple[str, str, tuple[str, ...]] | None:
        """The one source statement a reader asked to hear, with no reviewed translation.

        Returns the step label, the statement, and the protocol's own terms
        it uses -- what ``reader_translation_issue`` holds a model's Korean
        reading to. Only an explicit request to read a step qualifies.
        """

        if plan.action is not CuratedProtocolAction.FULL_DETAIL:
            return None
        if plan.intent_kind not in {"full_detail", "step_elaboration"}:
            return None
        if plan.translation_status not in {"source_language", "unavailable"}:
            return None
        if len(plan.source_texts) != 1 or not plan.step_label:
            return None
        statement = plan.source_texts[0]
        terms = tuple(dict.fromkeys(
            term.text for term in self._protocol_vocabulary().terms
            if _term_pattern(term.text).search(statement)
        ))
        return plan.step_label, statement, terms

    def entity_source_answer(
        self,
        entity: str,
        *,
        language: str,
        facts: tuple[CuratedProtocolFact, ...] = (),
    ) -> EntitySourceAnswer:
        """What the active PDF itself says about one requested entity.

        Every sentence is the protocol's own: which steps name the entity,
        and the first statement naming it, where the source introduces it --
        its reviewed Korean translation where the fixture carries one, else
        the source line. A definition is given only where the PDF writes the
        long form ("ammonium bicarbonate (AMBIC)"); any other definition
        stays for the approved and external references.
        """

        steps = self.fixture.steps

        def names(fact: CuratedProtocolFact) -> bool:
            return _names_entity(fact.text, entity)

        # A step's own statements, not the material rows bound to it.
        mentions: list[tuple[int, CuratedProtocolFact]] = [
            (index, fact)
            for index in range(len(steps))
            for fact in self.fixture.facts_for_step(index)
            if fact.kind not in {"material", "equipment"} and names(fact)
        ]
        if entity in _SOLUTION_SURFACES:
            # Where the source defines a named solution is where it is
            # introduced, whichever step names it first.
            defining = self._solution_definition(entity)
            if defining is not None:
                mentions.sort(key=lambda item: item[1] is not defining[1])
        try:
            knowledge = ProtocolKnowledgeView.from_fixture(self.fixture)
        except CuratedProtocolFixtureError:
            knowledge = None
        overview_kinds: dict[str, tuple[str, str]] = {}
        overview: tuple[CuratedProtocolFact, ...] = ()
        if knowledge is not None:
            overview_kinds = {
                **{fact.fact_id: ("재료 목록", "materials list") for fact in knowledge.materials},
                **{fact.fact_id: ("장비 목록", "equipment list") for fact in knowledge.equipment},
                **{fact.fact_id: ("구간 이름", "section titles") for fact in knowledge.sections},
            }
            overview = (
                *knowledge.materials, *knowledge.equipment, *knowledge.sections,
                knowledge.purpose, *knowledge.before_start, *knowledge.safety,
            )
        materials = tuple(fact for fact in overview if names(fact))
        supplied = tuple(fact for fact in facts if names(fact))
        texts = tuple(fact.text for fact in (*supplied, *(f for _, f in mentions), *materials))
        label = (
            _source_spelling(entity, texts)
            or _ENTITY_FALLBACK_LABELS.get(entity)
            or entity.replace("_", " ")
        )
        if not mentions and not materials:
            text = (
                f"활성 프로토콜 원문에서 {label}에 대한 내용을 찾지 못했습니다. "
                "별도 승인 자료를 참조해 주세요."
                if language == "ko" else
                f"The active protocol does not mention {label}. "
                "Please consult approved reference materials."
            )
            return EntitySourceAnswer(entity, label, text, text, (), False)
        defined_as = _source_abbreviation(label, texts)
        definition = (
            (
                f"원문에서 {label}는 {defined_as}의 약어입니다."
                if language == "ko" else
                f"The protocol writes {label} for {defined_as}."
            )
            if defined_as else None
        )
        evidence: list[str] = []
        if mentions:
            labels = tuple(dict.fromkeys(steps[index].source_label for index, _ in mentions))
            # Where the source first names it is where it introduces it.
            nearest_index, nearest = mentions[0]
            nearest_label = steps[nearest_index].source_label
            localized = self._localized_fact(steps[nearest_index].step_id, nearest.fact_id)
            if localized is not None:
                localized = _without_step_prefix(nearest_label, localized)
            listed = ", ".join(
                (f"{item}단계" if language == "ko" else item) for item in labels[:6]
            )
            more = (" 등" if language == "ko" else ", ...") if len(labels) > 6 else ""
            evidence.append(nearest.fact_id)
            if language == "ko":
                lead = f"{label}: 이 프로토콜 원문에서 {listed}{more}에 나옵니다."
                body = (
                    f"{nearest_label}단계 내용: {localized}"
                    if localized is not None else
                    f"{nearest_label}단계 원문: “{nearest.text.strip()}”"
                )
            else:
                lead = f"{label} appears in the active protocol at step {listed}{more}."
                body = f"Step {nearest_label} source: “{nearest.text.strip()}”"
            speech = f"{definition} {lead}" if definition else lead
            spoken_detail = (
                f"{lead} {body}" if localized is not None or language != "ko"
                else f"{lead} 해당 원문은 화면에 표시했습니다."
            )
            text = "\n".join(item for item in (definition, lead, body) if item)
        else:
            material = materials[0]
            evidence.append(material.fact_id)
            where = overview_kinds.get(material.fact_id, ("개요", "overview"))
            if language == "ko":
                lead = f"{label}: 이 프로토콜 원문의 {where[0]}에 나옵니다."
                body = f"원문(p.{material.source_page}): “{material.text.strip()}”"
            else:
                lead = f"{label} appears in the active protocol's {where[1]}."
                body = f"Source (p.{material.source_page}): “{material.text.strip()}”"
            speech = f"{definition} {lead}" if definition else lead
            spoken_detail = (
                f"{lead} 해당 원문은 화면에 표시했습니다." if language == "ko"
                else f"{lead} {body}"
            )
            text = "\n".join(item for item in (definition, lead, body) if item)
        return EntitySourceAnswer(
            entity, label, text, speech, tuple(evidence), True, defined_as,
            spoken_detail,
        )

    def _solution_definition(
        self, entity: str,
    ) -> tuple[int, CuratedProtocolFact] | None:
        """The step statement that defines a named solution, if the source has one.

        "Solution A: 2 parts of ...", "Prepare Solution A by ...", "Solution A
        is/contains/made with ..." -- not a line that only uses it.
        """

        surface = _SOLUTION_SURFACES.get(entity)
        if surface is None:
            return None
        name = rf"(?<![a-z0-9]){surface}(?![a-z0-9])"
        defines = re.compile(
            rf"{name}\s*[:=]|\bprepare\b[^.]*{name}|"
            rf"{name}[^.]*\b(?:is|consists\s+of|contains|made\s+(?:up\s+)?(?:of|with|in|by))\b",
            re.IGNORECASE,
        )
        for index in range(len(self.fixture.steps)):
            for fact in self.fixture.facts_for_step(index):
                if fact.kind in {"material", "equipment"}:
                    continue
                if defines.search(_derived_source_text(fact.text).casefold()):
                    return index, fact
        return None

    def entity_pair_statement(
        self, first: str, second: str, *, language: str,
    ) -> tuple[str, str] | None:
        """The protocol statement naming both entities, as (answer, fact id).

        The relationship is whatever that one statement says -- its reviewed
        translation or the source line -- never a sentence written for it.
        """

        steps = self.fixture.steps
        found: list[tuple[int, CuratedProtocolFact]] = [
            (index, fact)
            for index in range(len(steps))
            for fact in self.fixture.facts_for_step(index)
            if fact.kind not in {"material", "equipment"}
            and _names_entity(fact.text, first)
            and _names_entity(fact.text, second)
        ]
        if not found:
            return None
        index, fact = min(
            found,
            key=lambda item: (
                item[0] != self.current_index,
                item[0] < self.current_index,
                abs(item[0] - self.current_index),
            ),
        )
        first_label = _source_spelling(first, (fact.text,)) or first.replace("_", " ")
        second_label = _source_spelling(second, (fact.text,)) or second.replace("_", " ")
        label = steps[index].source_label
        localized = self._localized_fact(steps[index].step_id, fact.fact_id)
        if localized is not None:
            localized = _without_step_prefix(label, localized)
        if language == "ko":
            content = (
                f"{label}단계 내용: {localized}" if localized is not None
                else f"{label}단계 원문: “{fact.text.strip()}”"
            )
            answer = (
                f"관계: 원문에서 {first_label}와 {second_label}가 함께 나오는 곳은 "
                f"{label}단계입니다. {content}"
            )
        else:
            answer = (
                f"Relationship: {first_label} and {second_label} appear together "
                f"at step {label}: “{fact.text.strip()}”"
            )
        return answer, fact.fact_id

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
                f"활성 프로토콜 원문에 표시된 목적 또는 제목은 다음과 같습니다: {knowledge.purpose.text}"
                if language == "ko" else
                f"The active protocol source states this purpose or title: {knowledge.purpose.text}"
            )
            add(
                ClaimTargetType.PROTOCOL_PROPOSITION, "protocol_purpose", "purpose",
                evidence_ids=(knowledge.purpose.fact_id,), local_answer=answer,
            )
        elif intent.question_kind == "protocol_benefit":
            answer = (
                f"활성 원문에서 확인되는 범위는 ‘{knowledge.purpose.text}’입니다. 별도의 과학적 이점은 원문 근거 없이 추정하지 않습니다."
                if language == "ko" else
                f"The active source supports only ‘{knowledge.purpose.text}’. I will not infer a separate scientific benefit without source evidence."
            )
            add(
                ClaimTargetType.PROTOCOL_PROPOSITION, "protocol_purpose",
                "rationale", evidence_ids=(knowledge.purpose.fact_id,),
                local_answer=answer,
            )

        action_words = bool(re.search(
            r"(?:discard|remove|버리|제거|폐기|비우|dispos|씻|wash|incubat|배양|넣|place|cut|자르)", key
        ))
        ratio_words = bool(re.search(
            r"(?:비율|parts?|ratio|몇\s*대\s*몇|\d+\s*대\s*\d+|대\s*일|:\s*1)",
            key,
        ))
        for entity in intent.requested_entities:
            # What the PDF says about the entity, and nothing it does not say.
            # A named solution's composition is the statement that defines it,
            # so only that is admitted locally; a definition the PDF does not
            # give is left for the approved and external references. A safety
            # question is about safety, which the envelope's safety branch
            # answers, not about what the entity is.
            if intent.question_kind == "safety":
                continue
            found = self.entity_source_answer(entity, language=language, facts=facts)
            if not found.found:
                continue
            composition = entity in _SOLUTION_SURFACES
            local = (
                self._solution_definition(entity) is not None if composition
                else found.defined_as is not None
            )
            add(
                ClaimTargetType.ENTITY, entity,
                "composition" if composition else "definition",
                evidence_ids=found.evidence_ids, local_answer=found.text,
                unresolved_reason=(
                    None if local else "definition_absent_from_active_protocol"
                ),
                authority=(
                    "ACTIVE_PROTOCOL" if local
                    else "AUTHORITATIVE_EXTERNAL_REFERENCE"
                ),
                status=(
                    None if local else ClaimAdmissionStatus.RESEARCH_REQUIRED
                ),
            )
        if "difference" in intent.question_dimensions and intent.requested_entities:
            target = "-vs-".join(intent.requested_entities)
            limitation = (
                "활성 프로토콜은 그 차이를 정의하지 않으므로, 비교에는 별도 권위 자료가 필요합니다."
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
            and len(intent.requested_entities) >= 2
            and (joint := self.entity_pair_statement(
                intent.requested_entities[0], intent.requested_entities[1],
                language=language,
            )) is not None
        ):
            add(
                ClaimTargetType.COMPARISON,
                "-".join(intent.requested_entities[:2]),
                "relationship", evidence_ids=(joint[1],),
                local_answer=joint[0],
            )

        current_source = self.fixture.steps[self.current_index]
        if re.search(
            r"(?:1\.5\s*mL\s*튜브|1\.5\s*mL\s*tube|그\s*크기\s*튜브)", key, re.I
        ) and re.search(
            r"1\.5\s*ml\s*tube",
            _derived_source_text(current_source.instruction_source_text),
            re.I,
        ):
            # Only where the current step itself names a 1.5 mL tube, and
            # answered with that step's own statement.
            current_evidence = ("current_step",)
            if re.search(r"(?:뭘|무엇|what).*(?:넣|들어|place|contain)|(?:튜브).*(?:뭘|무엇)", key):
                localized = self._localized_fact(current_source.step_id, "current_step")
                answer = (
                    f"현재 {current_source.source_label}단계 내용: "
                    f"{_without_step_prefix(current_source.source_label, localized)}"
                    if language == "ko" and localized is not None else
                    f"현재 {current_source.source_label}단계 원문: “{current_source.instruction_source_text.strip()}”"
                    if language == "ko" else
                    f"Step {current_source.source_label} source: “{current_source.instruction_source_text.strip()}”"
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
            if not any(
                claim.target_id in {binding.parameter_id, binding.role}
                and claim.dimension == "role"
                for claim in claims
            ):
                add(
                    ClaimTargetType.PARAMETER, binding.parameter_id, "role",
                    evidence_ids=(binding.evidence_id,), local_answer=answer,
                    operational=True,
                    authority=(
                        "SOURCE_APPROVED_ALTERNATIVE"
                        if binding.source_approved_alternative else "ACTIVE_PROTOCOL"
                    ),
                )
            if re.search(r"(?:왜|이유|근거|rationale|why|basis)", key):
                if not any(
                    claim.target_id in {binding.parameter_id, binding.role}
                    and claim.dimension == "rationale"
                    for claim in claims
                ):
                    limitation = (
                        f"활성 프로토콜 원문은 {frame.step_label}단계의 {label} 조건을 명시하고 있으나, 저자가 이 정확한 수치를 선택한 과학적 근거는 원문 문서 자체에 설명되어 있지 않습니다."
                        if language == "ko" else
                        f"The active protocol specifies the {label} condition for step {frame.step_label}, but the document itself does not explain why the author selected this exact value."
                    )
                    add(
                        ClaimTargetType.PARAMETER, binding.parameter_id, "rationale",
                        evidence_ids=(binding.evidence_id,), local_answer=limitation,
                        unresolved_reason="rationale_absent_from_active_protocol",
                        authority="AUTHORITATIVE_EXTERNAL_REFERENCE",
                        status=ClaimAdmissionStatus.RESEARCH_REQUIRED,
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
                and re.search(r"(?:discard|remove|버리|제거|폐기|dispos)", key)
            )
        )
        if len(matching_actions) == 1:
            action = matching_actions[0]
            target_label = "Solution B" if action.target_id == "solution_b" else "Solution A" if action.target_id == "solution_a" else "지정된 용액"
            target_label_en = "Solution B" if action.target_id == "solution_b" else "Solution A" if action.target_id == "solution_a" else "the specified solution"
            action_answer = (
                f"활성 프로토콜은 {target_label}를 제거해 버리라고 지시합니다."
                if language == "ko" and action.action_type == "remove_discard" else
                f"The active protocol instructs you to remove and discard {target_label_en}."
                if action.action_type == "remove_discard" else
                (f"현재 단계의 확인된 동작은 {_derived_source_text(action.source_text)}입니다.")
            )
            add(
                ClaimTargetType.ACTION, action.action_id, "value",
                evidence_ids=(action.evidence_id,), local_answer=action_answer,
                operational=True,
            )
            if action.target_id:
                target_entity = action.target_id
                target_name = "Solution B" if target_entity == "solution_b" else "Solution A" if target_entity == "solution_a" else target_entity
                add(
                    ClaimTargetType.ENTITY, target_entity, "handling",
                    evidence_ids=(action.evidence_id,),
                    local_answer=(
                        f"이 단계에서 다루는 용액은 {target_name}입니다. {target_name}를 제거해 버립니다."
                        if language == "ko" else
                        f"The solution handled in this step is {target_name}. Remove and discard {target_name}."
                    ),
                    operational=True,
                )
            if re.search(r"(?:왜|이유|rationale|why|근거)", key):
                limitation = (
                    "활성 프로토콜은 해당 용액을 제거해 버리라고 지시하지만 그에 대한 과학적 이유는 설명하지 않습니다."
                    if language == "ko" else
                    "The active protocol instructs removing and discarding the solution but does not specify a separate scientific rationale."
                )
                add(
                    ClaimTargetType.ACTION, action.action_id, "rationale",
                    evidence_ids=(action.evidence_id,), local_answer=limitation,
                    unresolved_reason="rationale_absent_from_active_protocol",
                    authority="AUTHORITATIVE_EXTERNAL_REFERENCE",
                    status=ClaimAdmissionStatus.RESEARCH_REQUIRED,
                )
            if re.search(r"(?:어떻게|폐기|처리\s*방법|waste|stream|how|dispos|discard|방법|분류)", key):
                limitation = (
                    f"활성 프로토콜은 {target_label}를 제거해 폐기하라고 지시하지만, 구체적인 폐기 방법이나 시설별 폐기물 분류는 이 PDF에 명시되어 있지 않습니다. 관련 안전자료와 외부 권위자료를 확인해보겠습니다."
                    if language == "ko" else
                    f"The active protocol instructs removing and discarding {target_label_en}, but specific laboratory disposal methods and facility waste streams are not detailed in this protocol PDF."
                )
                add(
                    ClaimTargetType.ACTION, action.action_id, "disposal_method",
                    evidence_ids=(action.evidence_id,), local_answer=limitation,
                    unresolved_reason="disposal_method_absent_from_active_protocol",
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

    def _step_learning_presentation(
        self,
        *,
        language: str,
        warning_only: bool = False,
    ) -> tuple[
        str,
        str,
        tuple[CuratedProtocolFact, ...],
        tuple[str, ...],
    ]:
        """Compose a fast, source-bounded purpose/warning answer for one step."""

        step = self.fixture.steps[self.current_index]
        step_facts = self.fixture.facts_for_step(self.current_index)
        warning_facts = tuple(fact for fact in step_facts if fact.kind == "warning")
        expected_facts = tuple(
            fact for fact in step_facts if fact.kind == "expected_result"
        )
        current_fact = next(
            (fact for fact in step_facts if fact.fact_id == "current_step"),
            None,
        )
        evidence = tuple(
            dict.fromkeys(
                fact
                for fact in (*expected_facts, *warning_facts, current_fact)
                if fact is not None
            )
        )
        localized_warnings = tuple(
            self._localized_fact(step.step_id, fact.fact_id) or fact.text
            for fact in warning_facts
        )

        def concise(value: str, limit: int = 220) -> str:
            first = re.split(r"(?<=[.!?])\s+|\n+", value.strip(), maxsplit=1)[0]
            return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"

        section_title = next(
            (
                section.title_source_text
                for section in self.fixture.draft.protocol.sections
                if any(item.step_id == step.step_id for item in section.steps)
            ),
            "",
        )
        if language == "ko":
            # The step's purpose is the section the source puts it in, named
            # as the source names it -- never a sentence written for one PDF.
            if section_title:
                purpose = f"원문의 ‘{concise(section_title, 90)}’ 구간을 수행하는 것"
            else:
                purpose = "현재 PDF에 정의된 순서에 따라 후속 단계를 준비하는 것"

            if warning_only:
                speech = (
                    f"이 단계의 핵심 주의사항은 {concise(localized_warnings[0])}"
                    if localized_warnings
                    else "현재 PDF에는 이 단계의 별도 주의사항이 명시되어 있지 않습니다. 추가 안전 판단은 담당자에게 확인해 주세요."
                )
            else:
                speech = f"이 단계의 수행 목적은 {purpose}입니다."
                if expected_facts:
                    localized_expected = (
                        self._localized_fact(step.step_id, expected_facts[0].fact_id)
                        or expected_facts[0].text
                    )
                    speech += f" 원문이 제시한 확인 결과는 {concise(localized_expected)}"
                else:
                    speech += " 원문에는 별도의 과학적 작용 기전은 명시되어 있지 않습니다."
                if localized_warnings:
                    speech += f" 주의할 점은 {concise(localized_warnings[0])}"
        else:
            purpose = (
                f"perform the source section '{concise(section_title, 100)}'"
                if section_title
                else "prepare the protocol-defined next operation"
            )
            if warning_only:
                speech = (
                    f"The source warning for this step is: {concise(warning_facts[0].text)}"
                    if warning_facts
                    else "The active PDF states no separate warning for this step. Ask the responsible supervisor for any additional safety decision."
                )
            else:
                speech = f"The operational purpose of this step is to {purpose}."
                speech += (
                    f" The source-defined expected result is: {concise(expected_facts[0].text)}"
                    if expected_facts
                    else " The source does not state a separate scientific mechanism."
                )
                if warning_facts:
                    speech += f" The source warning is: {concise(warning_facts[0].text)}"

        display_lines = [speech, "", "Source evidence"]
        display_lines.extend(
            f"- PDF p.{fact.source_page} · {fact.text}" for fact in evidence
        )
        limitations = (
            ()
            if expected_facts
            else ("scientific_rationale_not_explicit_in_active_protocol",)
        )
        return "\n".join(display_lines), speech, evidence, limitations

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
        """Resolve one dominant recent Solution A/B reference from adjacent facts.

        Read off the source for any protocol: the current step names exactly
        one of the solutions, and the statement that resolves the question is
        the step statement that defines it ("Solution A: ...", "Prepare
        Solution A ..."), never one that only uses it. Only a question about
        how it is made qualifies. On in-gel that is step 2.
        """

        current_text = _derived_source_text(
            self.fixture.steps[self.current_index].instruction_source_text
        ).casefold()
        named_here = tuple(
            name for name, surface in _SOLUTION_SURFACES.items()
            if re.search(rf"(?<![a-z0-9]){surface}(?![a-z0-9])", current_text)
        )
        if len(named_here) != 1:
            return None
        key = _semantic_utterance_key(transcript)
        # Only a question about how the solution is made: "그거 어떻게 버려?"
        # asks about disposal and is not answered with the recipe.
        if not any(
            term in key
            for term in (
                "준비", "만들", "조성", "구성", "비율", "들어가", "뭐로", "무엇으로",
                "prepare", "make", "contain", "composition",
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
        mentions_ambic = bool(re.search(
            r"(?<![a-z0-9])ambic(?![a-z0-9])", key
        ))
        if not (explicit_a or explicit_b or vague or mentions_ambic):
            return None
        entity = (
            "solution_a" if explicit_a else
            "solution_b" if explicit_b else
            named_here[0]
        )
        defining = self._solution_definition(entity)
        if defining is None or defining[0] == self.current_index:
            return None
        source_index, fact = defining
        if fact.fact_id != "current_step":
            return None
        if mentions_ambic and not (explicit_a or explicit_b or vague) and (
            "ambic" not in _derived_source_text(fact.text).casefold()
        ):
            return None
        return source_index, fact, entity

    def _needs_solution_clarification(self, transcript: str) -> bool:
        key = _semantic_utterance_key(transcript)
        current_text = _derived_source_text(
            self.fixture.steps[self.current_index].instruction_source_text
        ).casefold()
        return bool(
            # A step that names both solutions leaves "그 용액" ambiguous.
            "solution a" in current_text
            and "solution b" in current_text
            and re.search(r"(?:(?:그|해당)\s*용액|그거|방금\s*말한\s*것)", key)
            and any(term in key for term in ("어떻게", "준비", "만들", "구성", "비율"))
        )

    def activate_configured(self) -> None:
        """Make one successfully configured structured protocol usable."""
        opening = (
            self.active, self.current_index, self._block_reason, self._workflow_status,
        )
        self.active = False
        self._workflow_status = "ready"
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
        self._pending_transcript_confirmation = None
        self._timer_started_at = None
        self._timer_duration_seconds = None
        self._timer_step_index = None
        self._experiment_started_at = None
        self._experiment_ended_at = None
        self._pending_anomaly = None
        self._pending_note_capture = None
        self._pause_state = "active"
        self._paused_at = None
        self._total_paused_seconds = 0.0
        self._pause_intervals.clear()
        self._paused_notice_spoken = False
        self._pending_stop_confirmation = None
        self._frozen_question = None
        self._pending_timer_confirmation = None
        self._pending_anomaly_confirmation = None
        self._pending_handoff_confirmation = None
        if opening != (
            self.active, self.current_index, self._block_reason, self._workflow_status,
        ):
            self._revision += 1

    def restore_experiment_progress(
        self,
        *,
        current_step_id: str,
        completed_step_ids: tuple[str, ...],
    ) -> None:
        """Restore only a server-persisted exact-revision progress checkpoint.

        Timers, pending confirmations, conversational context, and model output
        are intentionally not restored.  The durable session may select the
        current authoritative step, but it cannot bypass an incomplete earlier
        step or alter any protocol instruction.
        """

        indexes = {
            step.step_id: index for index, step in enumerate(self.fixture.steps)
        }
        if current_step_id not in indexes:
            raise CuratedProtocolFixtureError(
                "Experiment recovery step is not in the exact protocol revision."
            )
        current_index = indexes[current_step_id]
        completed = tuple(dict.fromkeys(completed_step_ids))
        if any(step_id not in indexes for step_id in completed):
            raise CuratedProtocolFixtureError(
                "Experiment recovery contains an unknown completed step."
            )
        expected = tuple(
            step.step_id for step in self.fixture.steps[:current_index]
        )
        if completed != expected:
            raise CuratedProtocolFixtureError(
                "Experiment recovery cannot bypass an incomplete protocol step."
            )
        self.activate_configured()
        self.current_index = current_index
        self.active = True
        self._workflow_status = "active"
        self._experiment_started_at = time.time()
        self._revision += 1

    def reset(self) -> None:
        opening = (self.active, self.current_index, self._block_reason)
        self.active = False
        self._workflow_status = "preview"
        self.current_index = 0
        self._block_reason = None
        self._replay.clear()
        self._recent_verified_entities.clear()
        # A new run re-reads the pages the machine could not finish. The
        # acknowledgement is a fact about one experimenter at one bench,
        # not a property of the document, so it does not carry over.
        self._acknowledged_unread_pages.clear()
        # A new run owes the warnings again. The duty is per execution, not
        # per protocol: the person at the bench this time has not heard them.
        self._disclosed_safety_warnings.clear()
        # A new run re-enters every repeat interval. Whether the last
        # person judged one finished says nothing about this one.
        self._repeat_intervals.clear()
        # A new run owes its own observations. That the last operator saw the
        # gel go transparent is not a fact about this run's gel.
        self._endpoint_observations.clear()
        self._pending_clarification = None
        self._last_related_query = None
        self._last_related_entities = ()
        self._discourse_context = ProtocolDiscourseContext()
        self._pending_completion_confirmation = None
        self._pending_observation_confirmation = None
        self._pending_transcript_confirmation = None
        self._timer_started_at = None
        self._timer_duration_seconds = None
        self._timer_step_index = None
        self._experiment_started_at = None
        self._experiment_ended_at = None
        self._pending_anomaly = None
        self._pending_note_capture = None
        self._pause_state = "active"
        self._paused_at = None
        self._total_paused_seconds = 0.0
        self._pause_intervals.clear()
        self._paused_notice_spoken = False
        self._pending_stop_confirmation = None
        self._frozen_question = None
        self._pending_timer_confirmation = None
        self._pending_anomaly_confirmation = None
        self._pending_handoff_confirmation = None
        if opening != (self.active, self.current_index, self._block_reason):
            self._revision += 1

    def configure_ready(self) -> None:
        self.reset()

    def deterministic_control_intent(
        self,
        transcript: str,
        *,
        language: str,
        arbitration: RequestArbitration | None = None,
    ) -> CuratedControlIntent:
        """Read-only preview of the classification ``plan`` will use this turn.

        ``plan`` calls this too, so the semantic fallback probe can never drift
        away from the routing decision it is deciding about.  Nothing here
        touches session state.
        """

        step = (
            self.fixture.steps[self.current_index]
            if 0 <= self.current_index < len(self.fixture.steps)
            else None
        )
        discourse = (
            self._discourse_context
            if step is not None
            and self._discourse_context.step_id == step.step_id
            and self._discourse_context.workflow_revision == self._revision
            else None
        )
        shared_decision = arbitration or arbitrate_request(transcript)
        return _front_control_intent(transcript, language) or curated_intent_from_arbitration(
            shared_decision,
            language=language,
        ) or classify_curated_control_intent(
            transcript,
            language=language,
            entity_inventory=self._entity_inventory(),
            recent_related_query=self._last_related_query,
            recent_related_entities=self._last_related_entities,
            discourse_context=discourse,
            completion_context=self.active,
            current_step=self.current_index + 1,
            max_steps=len(self.fixture.steps),
            protocol_vocabulary=self._protocol_vocabulary(),
        )

    def semantic_intent_context(
        self,
        transcript: str,
        *,
        language: str,
        deterministic_reason: str,
        now: float | None = None,
    ) -> SemanticIntentContext:
        """Build the server-owned context one semantic proposal may reason over.

        Only workflow milestones travel here - never protocol prose, quantities,
        or safety text - so a proposal can never be grounded in fabricated
        scientific content.
        """

        step = (
            self.fixture.steps[self.current_index]
            if 0 <= self.current_index < len(self.fixture.steps)
            else None
        )
        timer = self.timer_status(now=now)
        timer_state = str(timer.get("state") or "unavailable")
        duration = int(timer.get("duration_seconds") or 0)
        remaining = timer.get("remaining_seconds")
        capsule_pending = None
        if not self.active:
            capsule_pending = "greeting_pending"
        elif self._pending_completion_confirmation is not None:
            capsule_pending = "completion_gate"
        elif self._pending_observation_confirmation is not None:
            capsule_pending = "observation_gate"
        elif self._pending_transcript_confirmation is not None:
            capsule_pending = "transcript_gate"
        elif self._pending_note_capture:
            capsule_pending = "observation_note"
        return SemanticIntentContext(
            utterance=normalize_semantic_utterance(transcript),
            normalized_utterance=_utterance_key(transcript),
            language=language,
            session_phase=self.workflow_status,
            workflow_active=bool(self.active),
            current_step_label=step.source_label if step is not None else None,
            step_timer_state=timer_state,
            step_timer_configured=duration > 0,
            step_timer_remaining_seconds=(
                int(remaining) if isinstance(remaining, (int, float)) else None
            ),
            pending_interaction=capsule_pending,
            deterministic_reason=deterministic_reason,
        )

    @property
    def last_semantic_decision(self) -> SemanticIntentDecision | None:
        """The most recent semantic policy ruling, for turn telemetry only."""

        return self._last_semantic_decision

    @property
    def last_front_rule(self) -> str | None:
        """The FRONT_RULES name that planned the last turn, for telemetry only."""

        return self._last_front_rule

    def _apply_semantic_intent_fallback(
        self,
        intent: CuratedControlIntent,
        proposal: SemanticIntentProposal | None,
        *,
        transcript: str,
        language: str,
        arbitration_intent: str | None,
        settings: SemanticIntentSettings,
    ) -> CuratedControlIntent:
        """Validate one proposal against server context and project it, or not.

        The eligibility gate is re-evaluated here rather than trusted from the
        caller: a proposal that arrives for an utterance the deterministic path
        actually resolved is discarded, not applied.
        """

        if proposal is None:
            return intent
        reason = semantic_fallback_reason(
            deterministic_action=intent.action.value,
            deterministic_intent_kind=intent.intent_kind,
            arbitration_intent=arbitration_intent,
        )
        if reason is None:
            self._last_semantic_decision = SemanticIntentDecision(
                False, "deterministic_route_owns_turn"
            )
            return intent
        context = self.semantic_intent_context(
            transcript, language=language, deterministic_reason=reason
        )
        decision = evaluate_semantic_proposal(proposal, context, settings)
        self._last_semantic_decision = decision
        projected = curated_intent_from_semantic_decision(
            decision, language=language, normalized_transcript=context.normalized_utterance
        )
        if projected is None:
            return intent
        return projected

    def context_capsule(self) -> WorkflowContextCapsule:
        step = self.fixture.steps[self.current_index] if 0 <= self.current_index < len(self.fixture.steps) else None
        timer_state = None
        if self._timer_started_at is not None and self._timer_duration_seconds is not None:
            timer_state = {
                "step_index": self._timer_step_index,
                "duration_seconds": self._timer_duration_seconds,
                "started_at": self._timer_started_at,
            }
        pending = None
        if not self.active:
            pending = "greeting_pending"
        elif self._pending_completion_confirmation:
            pending = "completion_gate"
        elif self._pending_observation_confirmation:
            pending = "observation_gate"
        elif self._pending_note_capture:
            pending = "observation_note"

        last_intent = None
        if self._replay:
            last_intent = self._replay[max(self._replay.keys())].normalized_transcript

        return WorkflowContextCapsule(
            session_phase=self.workflow_status,
            protocol_id=self.fixture.protocol_id,
            workflow_revision=self._revision,
            current_step_id=step.step_id if step else None,
            current_step_label=step.source_label if step else None,
            pending_interaction=pending,
            pending_completion_gate=bool(self._pending_completion_confirmation),
            pending_observation_gate=bool(self._pending_observation_confirmation),
            active_timer_state=timer_state,
            recent_semantic_focus=(
                (
                    self._discourse_context.focused_entities[0]
                    if self._discourse_context.focused_entities
                    else self._discourse_context.semantic_topic
                )
                or self._discourse_context.focus_kind.value
            ),
            recent_explicit_entities=tuple(self._recent_verified_entities),
            last_user_intent=last_intent,
            last_committed_workflow_action=None,
        )

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
        str,
        float | None,
        int | None,
        int | None,
        float | None,
        float | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any],
        tuple[str, float | None, float, tuple[dict[str, Any], ...], bool],
        tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None],
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
            self._workflow_status,
            self._timer_started_at,
            self._timer_duration_seconds,
            self._timer_step_index,
            self._experiment_started_at,
            self._experiment_ended_at,
            self._pending_anomaly,
            self._pending_note_capture,
            dict(self._endpoint_observations),
            (
                self._pause_state,
                self._paused_at,
                self._total_paused_seconds,
                tuple(dict(interval) for interval in self._pause_intervals),
                self._paused_notice_spoken,
            ),
            # The end question, a question held through a pause and the timer
            # duration question roll back with the turn, as the completion
            # question does.
            (
                dict(self._pending_stop_confirmation)
                if self._pending_stop_confirmation is not None else None,
                dict(self._frozen_question)
                if self._frozen_question is not None else None,
                dict(self._pending_timer_confirmation)
                if self._pending_timer_confirmation is not None else None,
                dict(self._pending_anomaly_confirmation)
                if self._pending_anomaly_confirmation is not None else None,
            ),
        )

    def _restore(
        self,
        checkpoint: tuple[Any, ...],
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
            self._workflow_status,
            self._timer_started_at,
            self._timer_duration_seconds,
            self._timer_step_index,
        ) = checkpoint[:16]
        if len(checkpoint) >= 19:
            self._experiment_started_at = checkpoint[16]
            self._experiment_ended_at = checkpoint[17]
            self._pending_anomaly = checkpoint[18]
            self._pending_note_capture = (
                checkpoint[19] if len(checkpoint) >= 20 else None
            )
            # A turn whose persistence failed is rolled back whole. An
            # observation left behind would open the gate on the retry with
            # nothing durable behind it, which is the hole this closes.
            if len(checkpoint) >= 21:
                self._endpoint_observations = dict(checkpoint[20])
            # The pause itself rolls back with the workflow status. Restoring
            # the status alone left a refused voice resume "paused" by status
            # but not by pause state, so the paused guard stopped catching
            # turns and "완료했어" got the scope reminder (2026-10-01 test).
            if len(checkpoint) >= 22:
                (
                    self._pause_state,
                    self._paused_at,
                    self._total_paused_seconds,
                    intervals,
                ) = checkpoint[21][:4]
                self._pause_intervals = [dict(item) for item in intervals]
                # Whether the paused notice was said rolls back too: a refused
                # resume leaves the pause it was said in, so it is not said
                # again.
                self._paused_notice_spoken = (
                    bool(checkpoint[21][4]) if len(checkpoint[21]) >= 5 else False
                )
            held = checkpoint[22] if len(checkpoint) >= 23 else (None, None)
            stop_pending, frozen = held[:2]
            timer_pending = held[2] if len(held) >= 3 else None
            anomaly_pending = held[3] if len(held) >= 4 else None
            self._pending_anomaly_confirmation = (
                dict(anomaly_pending) if anomaly_pending is not None else None
            )
            self._pending_stop_confirmation = (
                dict(stop_pending) if stop_pending is not None else None
            )
            self._frozen_question = dict(frozen) if frozen is not None else None
            self._pending_timer_confirmation = (
                dict(timer_pending) if timer_pending is not None else None
            )
        else:
            self._experiment_started_at = None
            self._experiment_ended_at = None
            self._pending_anomaly = None
            self._pending_note_capture = None
            self._pending_stop_confirmation = None
            self._frozen_question = None
            self._pending_timer_confirmation = None
            self._pending_anomaly_confirmation = None
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
            transcript, entity_inventory=self._entity_inventory(),
            protocol_vocabulary=self._protocol_vocabulary(),
        )
        indexes = {self.current_index}
        if entities:
            indexes.update(range(len(self.fixture.steps)))
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
            "dtt": ("dtt", "dithiothreitol"),
            "iodoacetamide": ("iodoacetamide",),
            "trypsin": ("trypsin",),
            "formic_acid": ("formic acid", "formic"),
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
                if language == "ko":
                    summaries = []
                    entity_claims = tuple(
                        claim for claim in visible
                        if claim.target_type is ClaimTargetType.ENTITY
                    )
                    for claim in visible:
                        if claim.target_type is ClaimTargetType.ENTITY:
                            summaries.append(self.entity_source_answer(
                                claim.target_id, language=language, facts=facts,
                            ).spoken(alone=len(entity_claims) == 1 and len(visible) == 1))
                            continue
                        if claim.local_answer:
                            first_sent = re.split(
                                r"(?<=다)\.\s|\n", claim.local_answer.strip(), maxsplit=1
                            )[0].strip()
                            if first_sent:
                                summaries.append(first_sent)
                    if summaries:
                        speech = " ".join(
                            _spoken_summary_sentence(item) for item in summaries[:2]
                        )
                        if "화면에" not in speech:
                            speech += " 자세한 내용은 화면에 정리했습니다."
                    else:
                        speech = "요청하신 내용을 정리했습니다. 자세한 내용은 화면을 확인해 주세요."
                else:
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
        spoken_sections: list[str] = []
        # Each entity is answered from what this protocol's own statements say
        # about it -- for every protocol alike. A definition the PDF does not
        # give stays unresolved, for the approved and external references.
        answers = tuple(
            self.entity_source_answer(entity, language=language, facts=facts)
            for entity in entities
        )
        for answer in answers:
            sections.append((answer.label, answer.text))
            spoken_sections.append(answer.spoken(alone=len(answers) == 1))
        relation = ""
        joint = (
            self.entity_pair_statement(entities[0], entities[1], language=language)
            if len(entities) >= 2 else None
        )
        if joint is not None:
            relation = joint[0]
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
            formatted_blocks = []
            for label, text in sections:
                formatted_blocks.append(f"### {label}\n{text}")
            if relation:
                rel_title = "프로토콜 내 관계" if language == "ko" else "Protocol Relationship"
                formatted_blocks.append(f"### {rel_title}\n{relation}")
            direct = "\n\n".join(formatted_blocks)
            speech_parts = list(spoken_sections[:2])
            if relation and len(sections) <= 2:
                speech_parts.append(relation)
            speech = " ".join(speech_parts)
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
        # Only what the PDF's statements answer is local: where an entity
        # appears is not its definition or its role, so those dimensions stay
        # for the references, as they always did on every other document.
        locally_supported: set[str] = set()
        if answers and all(answer.defined_as for answer in answers):
            locally_supported.add("definition")
        if any(fact.kind == "warning" for fact in facts):
            locally_supported.add("safety")
        if relation:
            # Kept in step with ``relation`` above: claiming the relationship
            # is locally supported while emitting no relationship text would
            # strand the dimension instead of escalating it.
            locally_supported.add("relationship")
        if any(
            answer.found and self._solution_definition(answer.entity) is not None
            for answer in answers
        ):
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
        preview = (
            not self.active
            and self.workflow_status in {"preview", "ready"}
            and 0 <= self.current_index < len(steps)
        )
        show_step = self.active or preview
        current_step = steps[self.current_index] if show_step else None
        current_visual = (
            self.fixture.visual_for_step(self.current_index)
            if show_step
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
                steps[self.current_index].source_label if show_step else None
            ),
            "current_step_id": (
                steps[self.current_index].step_id if show_step else None
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
            "workflow_status": self.workflow_status,
            "step_safety_guidance": (
                self.safety_pack.guidance_for_step(current_step, self.current_index).public_dict()
                if self.safety_pack is not None and current_step is not None
                else None
            ),
            "timer": self.timer_status(),
            "timers": {
                "experiment": self.experiment_timer_status(),
                "step": self.timer_status(),
                "pause": self.pause_timer_status(),
            },
        }

    #: Why a forward move was refused, as a short code. The turn layer turns
    #: these into one sentence; nothing here writes prose.
    ADVANCE_REFUSED_REPEAT_INTERVAL_OPEN = "repeat_interval_open"
    ADVANCE_REFUSED_STEP_NOT_STARTABLE = "next_step_not_startable"
    ADVANCE_REFUSED_AT_FINAL_STEP = "already_at_final_step"
    ADVANCE_REFUSED_NOT_ACTIVE = "run_not_active"

    def _peek_advance_refusal(self) -> str | None:
        """Would a forward move be refused right now, without moving?

        Separate from ``advance_one_step`` so the turn layer can ask before it
        commits to a branch, and so the answer cannot be obtained by trying.
        """

        if not self.active:
            return self.ADVANCE_REFUSED_NOT_ACTIVE
        steps = self.fixture.steps
        if not 0 <= self.current_index < len(steps) - 1:
            return self.ADVANCE_REFUSED_AT_FINAL_STEP
        following = steps[self.current_index + 1]
        if not self.may_begin_step(following.step_id):
            return self.ADVANCE_REFUSED_STEP_NOT_STARTABLE
        return None

    def _advance_refusal_sentence(self, refusal: str, step, language: str) -> str:
        """One sentence: what stopped, and what is needed. Not the rulebook.

        Deliberately short. The whole rule set belongs in the system prompt;
        what belongs here is the one thing that is true right now.
        """

        if refusal == self.ADVANCE_REFUSED_REPEAT_INTERVAL_OPEN:
            interval = None
            for candidate in self._repeat_intervals_by_id().values():
                if step.step_id in candidate["repeated_step_ids"]:
                    interval = candidate
                    break
            labels = {item.step_id: item.source_label for item in self.fixture.steps}
            covered = (
                [labels.get(item, item) for item in interval["repeated_step_ids"]]
                if interval
                else []
            )
            span = f"{covered[0]}~{covered[-1]}번" if covered else "이 반복 구간"
            return {
                "en": (
                    f"{span} is a repeat interval. It stays open until you say it is "
                    "finished, so nothing has advanced."
                ),
                "ko": (
                    f"{span}은 반복 구간입니다. 끝났다고 말씀해 주실 때까지 열려 "
                    "있으므로 단계를 넘기지 않았습니다."
                ),
            }.get(language, f"{span}은 반복 구간입니다. 끝났다고 말씀해 주세요.")
        if refusal == self.ADVANCE_REFUSED_STEP_NOT_STARTABLE:
            return {
                "en": (
                    "The next step is not startable yet: something on it needs a "
                    "person. Nothing has advanced."
                ),
                "ko": (
                    "다음 단계는 아직 시작할 수 없습니다. 사람이 확인해야 할 것이 "
                    "남아 있어 단계를 넘기지 않았습니다."
                ),
            }.get(language, "다음 단계는 아직 시작할 수 없습니다.")
        return {
            "en": "Nothing has advanced.",
            "ko": "단계를 넘기지 않았습니다.",
        }.get(language, "단계를 넘기지 않았습니다.")

    def advance_one_step(self) -> str | None:
        """Move the run forward by one step, or refuse and say why.

        The only place ``current_index`` moves forward. It used to be a bare
        ``current_index += 1`` inside the turn handler, with the gates that
        were supposed to guard it -- ``may_begin_step``,
        ``may_leave_repeat_interval`` -- sitting beside it as methods nobody
        called. A rule enforced nowhere is a rule the model is trusted to
        follow, and this system's whole premise is that it must not have to be.

        Returns None when the move happened, or a short refusal code when it
        did not. A refusal moves nothing and changes nothing: it does not
        advance, does not mark the step complete, and does not decide what the
        run should do instead. The person's next words are still theirs -- this
        closes a door, it does not open a different one.
        """

        if not self.active:
            return self.ADVANCE_REFUSED_NOT_ACTIVE
        steps = self.fixture.steps
        if not 0 <= self.current_index < len(steps):
            return self.ADVANCE_REFUSED_AT_FINAL_STEP
        if self.current_index >= len(steps) - 1:
            return self.ADVANCE_REFUSED_AT_FINAL_STEP
        following = steps[self.current_index + 1]
        # Everything may_begin_step already answered for: an unread page
        # nobody acknowledged, a hazard whose words have not been read out, a
        # repetition with no count. Consulted here rather than left unused
        # beside the advance, which is what it was.
        #
        # may_leave_repeat_interval is deliberately *not* consulted here, and
        # the reason is a measurement rather than a preference. A repeat-until
        # step is already gated: readiness carries
        # UNSUPPORTED_REPEAT_UNTIL for it, the turn handler refuses the
        # transition, and only a user-reported positive observation releases
        # it. Adding a second gate over the same transition failed fourteen
        # tests that prove that path works, because the observation satisfies
        # the existing gate and not this one. Two mechanisms for one judgement
        # need reconciling, not stacking, and the observation is recorded as a
        # one-turn authorisation rather than a durable fact, so there is
        # nothing here for this predicate to read yet.
        if not self.may_begin_step(following.step_id):
            return self.ADVANCE_REFUSED_STEP_NOT_STARTABLE
        self.current_index += 1
        return None

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
        if (
            observation_predicate == "positive"
            and blocker is not None
            and step_id in self._steps_anchoring_a_repetition()
        ):
            # This is a user-reported, source-defined observation. It does not
            # claim machine vision or model approval; the normal transactional
            # completion/report path still owns the single mutation.
            #
            # Which steps it may release used to be written here as the labels
            # {"7", "9"} and "20", paired with a blocker kind each. Those are
            # in-gel's numbers. The gate worked on in-gel and on nothing else,
            # and a second document would have found no gate at all where its
            # own source states a repeat -- a rule that reads a document's page
            # numbers is not a rule, and principle 1 says so.
            #
            # The steps come from the document now: a repetition construct is
            # anchored to the step whose text carries the repeat instruction,
            # and that is where an operator meets it and where the judgement
            # belongs. On in-gel this derives {"7", "9", "20"} exactly -- the
            # same three, for a reason rather than by name.
            #
            # The blocker *kind* is no longer paired either. At a step the
            # source states a repeat for, the reason readiness raises is about
            # that repeat whichever code it carries: step 20 holds both
            # unsupported_repeat_until and unresolved_ambiguity, and the
            # ambiguity is literally the repeat's range. A document with no
            # repeat has no such step, so it has no gate to release.
            return None
        return blocker

    def _steps_anchoring_a_repetition(self) -> frozenset[str]:
        """This session's fixture, through the one module-level derivation."""

        return steps_anchoring_a_repetition(self.fixture)

    def record_endpoint_observation(
        self,
        index: int,
        *,
        predicate: str,
        utterance: str,
        actor_principal_id: str | None,
        actor_role: str,
    ) -> dict[str, object]:
        """Write down the observation a repeat step's gate is released on.

        Six things, and not a seventh: which protocol and which revision, the
        step it was reported at, the words the person used, when they said
        them, and who said them. The wording is kept as spoken -- only
        whitespace is collapsed -- because a paraphrase of an endpoint report
        is the system's reading of the bench rather than the bench's own.

        What is deliberately absent is any count. The source says "repeat
        until", never how many times, so a round number here would be a
        completion criterion the document does not state (principle 8). The
        record answers "was the stated endpoint reported, by whom, when", and
        that is the only question the gate asks it.

        ``actor_principal_id`` may be None, and is not filled in when it is:
        where no workspace is configured there is no principal to name, and
        writing "local" would be an identity nobody holds (principle 7). The
        attributed row is the experiment-session observation the same turn
        writes; see ``_record_workspace_observation`` in the server, which the
        release now requires rather than merely attempts.
        """

        if not 0 <= index < len(self.fixture.steps):
            raise ValueError("That step index is not in this protocol.")
        if predicate != "positive":
            raise ValueError("Only a reported endpoint releases the gate.")
        spoken = " ".join(str(utterance).split())
        if not spoken:
            raise ValueError("An endpoint observation needs the user's words.")
        if not str(actor_role).strip():
            raise ValueError("An endpoint observation needs a declaring role.")
        step = self.fixture.steps[index]
        record = {
            "protocol_id": self.fixture.draft.protocol.protocol_id,
            "protocol_revision_id": self.fixture.revision_id,
            "step_id": step.step_id,
            "step_label": step.source_label,
            "observation_predicate": predicate,
            "utterance": spoken,
            "declared_at": datetime.now(timezone.utc).isoformat(),
            "declared_by_principal_id": (
                actor_principal_id if actor_principal_id else None
            ),
            "declared_by_role": str(actor_role).strip(),
        }
        self._endpoint_observations[step.step_id] = record
        return dict(record)

    def endpoint_observations(self) -> dict[str, dict[str, object]]:
        """Every endpoint observation this run has on record, by step id."""

        return {
            step_id: dict(record)
            for step_id, record in self._endpoint_observations.items()
        }

    def _record_release_if_reported(
        self,
        intent: CuratedControlIntent,
        transcript: str,
        actor_principal_id: str | None,
        actor_role: str,
    ) -> None:
        """Write the gate down at the moment it opens, and only then.

        Called immediately before the single forward-progression control
        point, on the two branches that reach it. Earlier would record a
        release for a turn some other gate then refused; later would leave
        the transition already made with nothing behind it.
        """

        if not (
            intent.reported_observation
            and intent.observation_predicate == "positive"
            and self.fixture.steps[self.current_index].step_id
            in self._steps_anchoring_a_repetition()
        ):
            return
        self.record_endpoint_observation(
            self.current_index,
            predicate="positive",
            # The user's own words for this turn. observation_outcome is the
            # normalized form the confirmation machinery compares against;
            # where a bare "네" answered the prompt that form is all there is,
            # and it is still what was said.
            utterance=(transcript or intent.observation_outcome or ""),
            actor_principal_id=actor_principal_id,
            actor_role=actor_role,
        )

    def endpoint_observation_outstanding(
        self,
        index: int,
        observation_predicate: str | None = None,
    ) -> bool:
        """True while a repeat step's stated endpoint has not been reported.

        The gate, on its own footing. It stands where the source states a
        repeat -- read off the analysis, not off a label list -- and it comes
        down for one reason: somebody reported the endpoint the document
        states. A report arriving in this very turn counts, which is what the
        predicate argument is; a report already written down counts too, so
        the gate does not re-close behind an experimenter who answered it.

        A step the source states no repeat at has no such gate, so a document
        with no repetition is unaffected by any of this.
        """

        if not 0 <= index < len(self.fixture.steps):
            return False
        step_id = self.fixture.steps[index].step_id
        if step_id not in self._steps_anchoring_a_repetition():
            return False
        if observation_predicate == "positive":
            return False
        return step_id not in self._endpoint_observations

    def _stated_endpoint(self, index: int) -> tuple[str, int | None, str | None]:
        """The source's endpoint sentence at a repeat step, its page, and a translation.

        The translation is the verified sidecar of the one fact whose text is
        that sentence. A sidecar for a longer passage that merely contains
        the sentence is not a translation of it, so none is returned there.
        """

        step = self.fixture.steps[index]
        interval = self.repetition_anchored_at(step.step_id)
        if interval is None:
            return "", None, None
        stated = " ".join(str(interval["source_text"]).split())
        page = interval.get("source_page_number")
        sidecar = next(
            (
                self._localized_fact(step.step_id, fact.fact_id)
                for fact in self.fixture.facts_for_step(index)
                if " ".join(fact.text.split()) == stated
            ),
            None,
        )
        return stated, page if isinstance(page, int) else None, sidecar

    def observation_anomaly_reply(
        self, step_label: str | None, language: str, *, recorded: bool
    ) -> str:
        """The short reply to an endpoint reported with a problem.

        ``recorded`` only once the experiment record took the problem: the
        server says it after the write succeeded, and this session, which
        cannot know, says the problem was taken. Either way the endpoint is
        asked for again, in that step's own words, so it is heard even where
        the server replaces the rest of the reply.
        """

        if language == "en":
            endpoint = {
                "7": "Once destaining is complete",
                "9": "Once the gel looks white and dry",
                "20": "Once the gel looks white and dry",
            }.get(step_label or "", "Once the endpoint is reached")
            opening = (
                "I recorded the issue." if recorded
                else "I took the issue to record."
            )
            return f"{opening} {endpoint}, please tell me once more."
        if language == "vi":
            opening = (
                "Tôi đã ghi vấn đề." if recorded
                else "Tôi đã nhận vấn đề để ghi."
            )
            return f"{opening} Khi đạt điểm kết thúc, vui lòng nói lại một lần nữa."
        endpoint = {
            "7": "탈색이 끝났으면",
            "9": "젤이 하얗게 말랐으면",
            "20": "젤이 하얗게 말랐으면",
        }.get(step_label or "", "종점에 도달했으면")
        opening = "이상 사항은 기록했어요." if recorded else "이상 사항 기록 요청을 받았어요."
        return f"{opening} {endpoint} 한 번 더 말씀해 주세요."

    def _endpoint_answer_record(
        self, index: int, transcript: str, language: str
    ) -> str:
        """The endpoint a yes/no answered, quoted, beside the words spoken."""

        spoken = " ".join(transcript.split())
        stated, _page, _sidecar = self._stated_endpoint(index)
        if not stated:
            return spoken
        # The report ledger refuses wording over 800 characters
        # (experiment_reports._clean_text), and a refused write fails the
        # turn's acknowledgement; a yes/no answer is a few characters.
        if len(stated) > 600:
            stated = stated[:599].rstrip() + "…"
        if language == "en":
            return f"Source endpoint “{stated}” — answer: {spoken}"
        return f"원문 종점 “{stated}” — 답: {spoken}"

    def _names_another_step(self, intent: CuratedControlIntent) -> bool:
        """True for a completion report that names a step other than the current one."""

        return bool(
            intent.action is CuratedProtocolAction.NEXT
            and intent.target_step not in (None, "authoritative_current_step")
            and self.active
            and 0 <= self.current_index < len(self.fixture.steps)
            and intent.target_step
            != self.fixture.steps[self.current_index].source_label
        )

    def _observation_prompt_reply(
        self,
        held: PendingObservationConfirmation,
        routed: CuratedControlIntent,
        transcript: str,
        *,
        turn_id: int,
        generation: int | None,
        language: str,
        normalized_transcript: str,
    ) -> CuratedControlIntent:
        """Keep, ask again, or let go of a prompt this reply did not answer.

        Reached only after the reply was read as neither an endpoint phrase
        nor a yes/no, with the prompt already cleared. Nothing here advances:
        a control command -- or a completion naming another step, which is
        asked about first -- keeps the route it had, an explicit question keeps
        its read-only answer and the prompt, and anything else asks again --
        at most _OBSERVATION_REPROMPT_LIMIT times, after which the prompt is
        let go with no observation recorded.
        """

        if routed.action is CuratedProtocolAction.REPORT_ANOMALY:
            if _OBSERVATION_PROMPT_PROBLEM.search(normalized_transcript):
                # A problem is recorded, and the endpoint is still owed: the
                # question stays open. Recording it used to let it go, so the
                # next description had nothing to answer (2026-10-01 test).
                self._pending_observation_confirmation = replace(
                    held, requested_turn_id=turn_id,
                    requested_generation=generation, accepts_yes_no=False,
                )
                return routed
            if not held.accepts_yes_no and isinstance(self._pending_anomaly, dict):
                # The problem just recorded under this question was answered
                # with "어떤 종류의 색 변화를 보셨나요?"; a change described
                # now answers that, and is added to it rather than asked past.
                self._pending_observation_confirmation = replace(
                    held, requested_turn_id=turn_id,
                    requested_generation=generation, accepts_yes_no=False,
                )
                return replace(
                    routed,
                    intent_kind="enrich_pending_anomaly",
                    anomaly_category=str(
                        self._pending_anomaly.get("category") or "protocol_block"
                    ),
                    normalized_transcript=_utterance_key(transcript),
                )
            # Otherwise a change described without a problem is an answer
            # this reader could not place, not an anomaly: it is asked again.
        elif (
            routed.action in _OBSERVATION_PROMPT_PASS_THROUGH
            or self._names_another_step(routed)
        ):
            return routed
        if _OBSERVATION_REPEAT_REQUEST.fullmatch(_utterance_key(transcript)):
            # The question is said again as it was last asked, and the count
            # of times it was asked again stays where it was.
            self._pending_observation_confirmation = replace(
                held, requested_turn_id=turn_id, requested_generation=generation,
                accepts_yes_no=True,
            )
            return CuratedControlIntent(
                intent_kind="observation_confirmation_repeated",
                action=CuratedProtocolAction.CLARIFY_COMPLETION,
                target_step="authoritative_current_step",
                requires_confirmation=True,
                confidence_source="server_pending_observation",
                language=language,
                normalized_transcript=normalized_transcript,
            )
        if (
            routed.action in _OBSERVATION_PROMPT_READ_ONLY
            and not routed.allows_state_mutation
            and _QUESTION_MARKERS.search(" ".join(transcript.casefold().split()))
        ):
            self._pending_observation_confirmation = replace(
                held, requested_turn_id=turn_id, requested_generation=generation,
            )
            return routed
        if held.reprompt_count < _OBSERVATION_REPROMPT_LIMIT:
            self._pending_observation_confirmation = replace(
                held,
                requested_turn_id=turn_id,
                requested_generation=generation,
                reprompt_count=held.reprompt_count + 1,
                accepts_yes_no=True,
            )
            return CuratedControlIntent(
                intent_kind="observation_confirmation_reasked",
                action=CuratedProtocolAction.CLARIFY_COMPLETION,
                target_step="authoritative_current_step",
                requires_confirmation=True,
                confidence_source="server_pending_observation",
                language=language,
                normalized_transcript=normalized_transcript,
            )
        return CuratedControlIntent(
            intent_kind="observation_confirmation_released",
            action=CuratedProtocolAction.DECLINE_COMPLETION,
            target_step="authoritative_current_step",
            confidence_source="server_pending_observation",
            language=language,
            normalized_transcript=normalized_transcript,
        )

    def _ask_held_question_again(
        self,
        plan: CuratedProtocolTurnPlan,
        held: dict[str, Any],
        *,
        turn_id: int,
        generation: int | None,
        language: str,
    ) -> CuratedProtocolTurnPlan:
        """Reopen the question a pause held, and say it after the resume.

        The question is reopened for the turn after this one, at the revision
        the resume left, so the answer it gets is read as it would have been
        before the pause.
        """

        kind = held["kind"]
        pending = held["pending"]
        if kind == "stop":
            self._pending_stop_confirmation = {
                **pending, "requested_turn_id": turn_id,
                "requested_generation": generation,
            }
            question = END_CONFIRMATION_QUESTION.get(
                language, END_CONFIRMATION_QUESTION["ko"]
            )
            shown = question
        elif kind == "completion":
            self._pending_completion_confirmation = replace(
                pending, requested_turn_id=turn_id,
                requested_generation=generation,
                workflow_revision=self._revision,
            )
            question = (
                _completion_question(self.fixture.steps[self.current_index].source_label)
                if language == "ko" else
                "Have you completed the current step? No state has changed."
            )
            shown = question
        elif kind in {"timer", "anomaly"}:
            reopened = {
                **pending, "requested_turn_id": turn_id,
                "requested_generation": generation,
                "workflow_revision": self._revision,
            }
            if kind == "timer":
                self._pending_timer_confirmation = reopened
            else:
                self._pending_anomaly_confirmation = reopened
            question = str(pending.get("question") or "")
            shown = question
            if not question:
                return plan
        else:
            self._pending_observation_confirmation = replace(
                pending, requested_turn_id=turn_id,
                requested_generation=generation,
                workflow_revision=self._revision,
            )
            question = held.get("speech") or ""
            shown = held.get("display") or question
            if not question:
                return plan
        return replace(
            plan,
            display_text=f"{plan.display_text}\n\n{shown}",
            speech_text=f"{plan.speech_text} {question}".strip(),
        )

    def _open_questions(
        self,
        *,
        turn_id: int,
        configuration_id: int | None,
        generation: int | None,
    ) -> _OpenQuestions:
        """Which server questions this turn can answer. Reads, never writes.

        A question is answerable only on the turn right after it was asked,
        at the step, revision, configuration and generation it was asked at.
        plan() reads its open questions here, and so does a tool proposal's
        validation: a turn an open question owns is the front rules' turn.
        """

        pending = self._pending_completion_confirmation
        observation_pending = self._pending_observation_confirmation
        transcript_pending = self._pending_transcript_confirmation
        note_pending = self._pending_note_capture
        stop_pending = self._pending_stop_confirmation
        timer_pending = self._pending_timer_confirmation
        anomaly_pending = self._pending_anomaly_confirmation
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
        transcript_pending_valid = bool(
            transcript_pending is not None
            and self.active
            and self.current_index == transcript_pending.step_index
            and self.fixture.steps[self.current_index].step_id == transcript_pending.step_id
            and self._revision == transcript_pending.workflow_revision
            and turn_id == transcript_pending.requested_turn_id + 1
            and (
                transcript_pending.configuration_id is None
                or configuration_id == transcript_pending.configuration_id
            )
            and (
                transcript_pending.requested_generation is None
                or generation is None
                or generation >= transcript_pending.requested_generation
            )
        )
        note_pending_valid = bool(
            note_pending is not None
            and self.active
            and self.current_index == note_pending.get("step_index")
            and self.fixture.steps[self.current_index].step_id
            == note_pending.get("step_id")
            and self._revision == note_pending.get("workflow_revision")
            and turn_id == note_pending.get("requested_turn_id", -2) + 1
            and (
                note_pending.get("configuration_id") is None
                or configuration_id == note_pending.get("configuration_id")
            )
            and (
                note_pending.get("requested_generation") is None
                or generation is None
                or generation >= note_pending.get("requested_generation")
            )
        )
        stop_pending_valid = bool(
            stop_pending is not None
            and turn_id == stop_pending.get("requested_turn_id", -2) + 1
            and (
                stop_pending.get("configuration_id") is None
                or configuration_id == stop_pending.get("configuration_id")
            )
            and (
                stop_pending.get("requested_generation") is None
                or generation is None
                or generation >= stop_pending.get("requested_generation")
            )
        )
        timer_pending_valid = bool(
            timer_pending is not None
            and self.active
            and self.current_index == timer_pending.get("step_index")
            and self.fixture.steps[self.current_index].step_id
            == timer_pending.get("step_id")
            and self._revision == timer_pending.get("workflow_revision")
            and turn_id == timer_pending.get("requested_turn_id", -2) + 1
            and (
                timer_pending.get("configuration_id") is None
                or configuration_id == timer_pending.get("configuration_id")
            )
            and (
                timer_pending.get("requested_generation") is None
                or generation is None
                or generation >= timer_pending.get("requested_generation")
            )
        )
        anomaly_pending_valid = bool(
            anomaly_pending is not None
            and self.active
            and self.current_index == anomaly_pending.get("step_index")
            and self.fixture.steps[self.current_index].step_id
            == anomaly_pending.get("step_id")
            and self._revision == anomaly_pending.get("workflow_revision")
            and turn_id == anomaly_pending.get("requested_turn_id", -2) + 1
            and (
                anomaly_pending.get("configuration_id") is None
                or configuration_id == anomaly_pending.get("configuration_id")
            )
            and (
                anomaly_pending.get("requested_generation") is None
                or generation is None
                or generation >= anomaly_pending.get("requested_generation")
            )
        )
        return _OpenQuestions(
            completion=pending_valid,
            observation=observation_pending_valid,
            transcript=transcript_pending_valid,
            note=note_pending_valid,
            stop=stop_pending_valid,
            timer=timer_pending_valid,
            anomaly=anomaly_pending_valid,
        )

    def _front_rule_for(
        self,
        intent: CuratedControlIntent,
        classified: CuratedControlIntent | None,
    ) -> str | None:
        """The front rule a turn the rules read falls under, or None.

        ``intent`` is the turn's final reading; ``classified`` is what the
        rules made of the words before the step's own gates adjusted it
        (None when an open question, not the words, decided the turn).
        """

        if intent.confidence_source == "semantic_intent_fallback":
            # A model's reading is never a front rule.
            return None
        rule = _FRONT_RULE_BY_ACTION.get(intent.action)
        if rule is not None:
            return rule
        if not self.active and (
            (
                intent.action in {
                    CuratedProtocolAction.START, CuratedProtocolAction.RESUME,
                }
                and self._experiment_ended()
            )
            or (
                intent.action is CuratedProtocolAction.START
                and intent.intent_kind == "workflow_command"
                and self._experiment_started_at is None
            )
        ):
            return "start_command"
        if (
            classified is not None
            and classified.action is CuratedProtocolAction.NEXT
            and classified.reported_completion
            and classified.intent_kind not in _UNTARGETED_COMPLETION_KINDS
            and not self._names_another_step(classified)
            and (
                intent.action is CuratedProtocolAction.NEXT
                # At a repeat-until step the step is held for its endpoint.
                or intent.intent_kind == "observation_confirmation_required"
            )
        ):
            return "targeted_completion"
        return None

    def plan(
        self,
        transcript: str,
        *,
        turn_id: int,
        language: str,
        transcript_quality: str | None = None,
        configuration_id: int | None = None,
        generation: int | None = None,
        arbitration: RequestArbitration | None = None,
        semantic_proposal: SemanticIntentProposal | None = None,
        semantic_settings: SemanticIntentSettings | None = None,
        #: Who is speaking, for the endpoint-observation record. Optional
        #: because a session may run with no workspace and therefore no
        #: principal; the record then names the role and leaves the identity
        #: empty rather than inventing one.
        actor_principal_id: str | None = None,
        actor_role: str = "voice_operator",
    ) -> CuratedProtocolTurnPlan:
        """Plan one turn by the rules: the front rules, then all the others."""

        planned = self._plan(
            transcript,
            turn_id=turn_id,
            language=language,
            transcript_quality=transcript_quality,
            configuration_id=configuration_id,
            generation=generation,
            arbitration=arbitration,
            semantic_proposal=semantic_proposal,
            semantic_settings=semantic_settings,
            actor_principal_id=actor_principal_id,
            actor_role=actor_role,
            front_only=False,
        )
        if planned is None:
            raise CuratedProtocolFixtureError("A turn was left unplanned.")
        return planned

    def front_plan(
        self,
        transcript: str,
        *,
        turn_id: int,
        language: str,
        transcript_quality: str | None = None,
        configuration_id: int | None = None,
        generation: int | None = None,
        arbitration: RequestArbitration | None = None,
        actor_principal_id: str | None = None,
        actor_role: str = "voice_operator",
    ) -> CuratedProtocolTurnPlan | None:
        """The turn as a front rule plans it, or None with nothing touched.

        The front rules (FRONT_RULES) are the turns that never wait on a
        model: F2 an untrustworthy transcript, F3 pause words and "종료"
        commands, F4 a yes/no to an open question, F5 a reply while an
        endpoint question is open or an endpoint stated at a repeat-until
        step (D9), F6 time left, F7 "그거" (D3), F8 say it again, F9 cancel a
        lookup, a completion naming the current step (D2), and a start of an
        experiment never started or already ended (decision 2, 2026-10-03).
        F1, the emergency gate, stays in server.py ahead of all planning.

        This is plan() itself, stopped once the rules have read the turn. A
        front rule's turn is planned by the very branches plan() uses, so the
        plan is the one plan() would have made, and last_front_rule names the
        rule. Any other turn returns None before anything is acted on, with
        the open questions the reading cleared put back: the session is as it
        was, for plan() or a validated tool proposal to take the turn.
        A turn already planned is answered from the replay, as plan() does.
        """

        return self._plan(
            transcript,
            turn_id=turn_id,
            language=language,
            transcript_quality=transcript_quality,
            configuration_id=configuration_id,
            generation=generation,
            arbitration=arbitration,
            actor_principal_id=actor_principal_id,
            actor_role=actor_role,
            front_only=True,
        )

    def _plan(
        self,
        transcript: str,
        *,
        turn_id: int,
        language: str,
        transcript_quality: str | None = None,
        configuration_id: int | None = None,
        generation: int | None = None,
        arbitration: RequestArbitration | None = None,
        semantic_proposal: SemanticIntentProposal | None = None,
        semantic_settings: SemanticIntentSettings | None = None,
        actor_principal_id: str | None = None,
        actor_role: str = "voice_operator",
        front_only: bool,
    ) -> CuratedProtocolTurnPlan | None:
        if turn_id in self._replay:
            return self._replay[turn_id]
        # What reading the turn may clear before the rules know who owns it.
        # A front-only reading that hands the turn on puts these back.
        untouched = (
            self._last_semantic_decision,
            self._pending_completion_confirmation,
            self._pending_observation_confirmation,
            self._pending_transcript_confirmation,
            self._pending_note_capture,
            self._pending_stop_confirmation,
            self._pending_timer_confirmation,
            self._pending_anomaly_confirmation,
        ) if front_only else None
        self._last_semantic_decision = None
        # The front rule that owns this turn, once one does (FRONT_RULES).
        front_rule: str | None = None
        # What the rules made of the words, before the step's gates.
        classified: CuratedControlIntent | None = None
        command_key = _utterance_key(transcript)
        pending = self._pending_completion_confirmation
        observation_pending = self._pending_observation_confirmation
        transcript_pending = self._pending_transcript_confirmation
        note_pending = self._pending_note_capture
        open_questions = self._open_questions(
            turn_id=turn_id,
            configuration_id=configuration_id,
            generation=generation,
        )
        pending_valid = open_questions.completion
        normalized_confirmation = _semantic_utterance_key(transcript)
        observation_pending_valid = open_questions.observation
        transcript_pending_valid = open_questions.transcript
        note_pending_valid = open_questions.note
        stop_pending = self._pending_stop_confirmation
        stop_pending_valid = open_questions.stop
        if stop_pending is not None and not stop_pending_valid:
            self._pending_stop_confirmation = None
        timer_pending = self._pending_timer_confirmation
        timer_pending_valid = open_questions.timer
        if timer_pending is not None and not timer_pending_valid:
            self._pending_timer_confirmation = None
        anomaly_pending = self._pending_anomaly_confirmation
        anomaly_pending_valid = open_questions.anomaly
        if anomaly_pending is not None and not anomaly_pending_valid:
            self._pending_anomaly_confirmation = None
        # The question this turn could answer, kept aside in case the turn is
        # a pause: the pause holds it, and the voice resume asks it again.
        open_question: dict[str, Any] | None = None
        if pending_valid and pending is not None:
            open_question = {
                "kind": "completion", "pending": pending,
                "step_index": pending.step_index, "step_id": pending.step_id,
            }
        elif observation_pending_valid and observation_pending is not None:
            asked = self._replay.get(observation_pending.requested_turn_id)
            open_question = {
                "kind": "observation", "pending": observation_pending,
                "step_index": observation_pending.step_index,
                "step_id": observation_pending.step_id,
                "speech": asked.speech_text if asked is not None else None,
                "display": asked.display_text if asked is not None else None,
            }
        elif stop_pending_valid and stop_pending is not None:
            open_question = {"kind": "stop", "pending": dict(stop_pending)}
        elif timer_pending_valid and timer_pending is not None:
            open_question = {
                "kind": "timer", "pending": dict(timer_pending),
                "step_index": timer_pending.get("step_index"),
                "step_id": timer_pending.get("step_id"),
            }
        elif anomaly_pending_valid and anomaly_pending is not None:
            open_question = {
                "kind": "anomaly", "pending": dict(anomaly_pending),
                "step_index": anomaly_pending.get("step_index"),
                "step_id": anomaly_pending.get("step_id"),
            }
        if pending is not None and not pending_valid:
            self._pending_completion_confirmation = None
        if observation_pending is not None and not observation_pending_valid:
            self._pending_observation_confirmation = None
        if transcript_pending is not None and not transcript_pending_valid:
            self._pending_transcript_confirmation = None
        if note_pending is not None and not note_pending_valid:
            self._pending_note_capture = None
        binary_reply = _binary_frame_reply(transcript)
        # "네?" asks to hear the question again. Its key is "네", so the
        # confirmation patterns below read the key only when the spoken reply
        # neither asks nor hedges.
        reply_withheld = _reply_withholds_assent(transcript)
        pending_language_mismatch = bool(
            (pending_valid or observation_pending_valid or transcript_pending_valid)
            and language == "ko"
            and re.fullmatch(r"[a-z]{1,4}", normalized_confirmation)
            and normalized_confirmation not in {"yes", "no", "done"}
        )
        # An observation prompt was outstanding and went stale, and this
        # utterance is not an answer to it. The endpoint-phrase guard near the
        # end of this method reads it, and that guard is reachable from every
        # branch of the chain below -- so it is derived here, once, from state
        # already fixed above, rather than inside one branch. Both inputs are
        # bound once before this point and never reassigned, so this is the
        # same value the branch-local assignment produced.
        stale_observation_reply = (
            observation_pending is not None and not observation_pending_valid
        )
        stop_reply = (
            (
                "affirmative"
                if (
                    not reply_withheld
                    and _AFFIRMATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
                )
                or binary_reply == "affirmative"
                or _END_COMMAND.fullmatch(command_key)
                else "negative"
                if (
                    not reply_withheld
                    and _NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
                )
                or binary_reply == "negative"
                else None
            )
            if stop_pending_valid else None
        )
        if stop_reply == "affirmative":
            front_rule = "yes_no_open_question"
            self._pending_stop_confirmation = None
            intent = CuratedControlIntent(
                intent_kind="stop_confirmed",
                action=CuratedProtocolAction.STOP,
                confidence_source="server_pending_stop_confirmation",
                allows_state_mutation=True,
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        elif stop_reply == "negative":
            self._pending_stop_confirmation = None
            response = {
                "ko": "알겠습니다. 실험을 종료하지 않았습니다.",
                "en": "Understood. The experiment has not been ended.",
            }.get(language, "알겠습니다. 실험을 종료하지 않았습니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.DECLINE_COMPLETION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=(
                    self.fixture.steps[self.current_index].source_label
                    if self.active else None
                ),
                final_step=self.active and self.current_index == len(self.fixture.steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind="stop_confirmation_declined",
            )
            self._last_front_rule = "yes_no_open_question"
            self._replay[turn_id] = plan
            return plan
        elif timer_pending_valid and (
            (
                not reply_withheld
                and _AFFIRMATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            )
            or binary_reply == "affirmative"
        ):
            # A yes to "원문은 15분입니다. 15분으로 시작할까요?" starts the
            # source timer (decision D6).
            front_rule = "yes_no_open_question"
            self._pending_timer_confirmation = None
            intent = CuratedControlIntent(
                intent_kind="pending_timer_confirmed",
                action=CuratedProtocolAction.START_TIMER,
                confidence_source="server_pending_timer_confirmation",
                allows_state_mutation=True,
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        elif timer_pending_valid and (
            (
                not reply_withheld
                and _NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            )
            or binary_reply == "negative"
        ):
            self._pending_timer_confirmation = None
            response = {
                "ko": "알겠습니다. 타이머를 시작하지 않았습니다.",
                "en": "Understood. The timer was not started.",
            }.get(language, "알겠습니다. 타이머를 시작하지 않았습니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.TIMER_STATUS,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=self.fixture.steps[self.current_index].source_label,
                final_step=self.current_index == len(self.fixture.steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind="pending_timer_declined",
            )
            self._last_front_rule = "yes_no_open_question"
            self._replay[turn_id] = plan
            return plan
        elif anomaly_pending_valid and transcript_quality is None and (
            (
                not reply_withheld
                and _AFFIRMATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            )
            or binary_reply == "affirmative"
        ):
            # A yes to "이상 사항으로 기록할까요?" (decision 1) records the
            # words the question was asked about -- not this "네" -- as the
            # model's anomaly would have been recorded.
            said = str((anomaly_pending or {}).get("utterance") or "")
            self._pending_anomaly_confirmation = None
            self._last_front_rule = "yes_no_open_question"
            key = _utterance_key(said)
            return self._execute_turn_intent(
                CuratedControlIntent(
                    intent_kind="pending_anomaly_confirmed",
                    action=CuratedProtocolAction.REPORT_ANOMALY,
                    question_kind="anomaly",
                    reported_anomaly=True,
                    anomaly_category=_anomaly_category(key),
                    confidence_source="server_pending_anomaly_confirmation",
                    language=language,
                    normalized_transcript=key,
                ),
                transcript=said,
                command_key=key,
                turn_id=turn_id,
                language=language,
                configuration_id=configuration_id,
                generation=generation,
                actor_principal_id=actor_principal_id,
                actor_role=actor_role,
                open_question=None,
            )
        elif anomaly_pending_valid and (
            (
                not reply_withheld
                and _NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            )
            or binary_reply == "negative"
        ):
            self._pending_anomaly_confirmation = None
            response = {
                "ko": "알겠습니다. 이상 사항으로 기록하지 않았습니다.",
                "en": "Understood. Nothing was recorded as an issue.",
            }.get(language, "알겠습니다. 이상 사항으로 기록하지 않았습니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.DECLINE_COMPLETION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=self.fixture.steps[self.current_index].source_label,
                final_step=self.current_index == len(self.fixture.steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind="pending_anomaly_declined",
            )
            self._last_front_rule = "yes_no_open_question"
            self._replay[turn_id] = plan
            return plan
        elif (
            note_pending_valid
            and not _utterance_looks_like_new_command(transcript)
            and transcript.strip()
        ):
            front_rule = "observation_reply"
            self._pending_note_capture = None
            intent = CuratedControlIntent(
                intent_kind="pending_observation_note_received",
                action=CuratedProtocolAction.RECORD_OBSERVATION,
                target_step="authoritative_current_step",
                reported_observation=True,
                observation_predicate=str(note_pending.get("category") or "note"),
                observation_outcome=transcript.strip()[:4000],
                confidence_source="server_pending_observation_note",
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        elif transcript_pending_valid and (
            (
                not reply_withheld
                and _AFFIRMATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            )
            or binary_reply == "affirmative"
        ):
            proposed_tx = transcript_pending.proposed_transcript
            self._pending_transcript_confirmation = None
            confirmed = self.plan(
                proposed_tx,
                turn_id=turn_id,
                language=language,
                transcript_quality=transcript_quality,
                configuration_id=configuration_id,
                generation=generation,
            )
            self._last_front_rule = "yes_no_open_question"
            return confirmed
        elif transcript_pending_valid and (
            (
                not reply_withheld
                and _NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            )
            or binary_reply == "negative"
        ):
            self._pending_transcript_confirmation = None
            response = (
                "알겠습니다. 다시 말씀해 주세요."
                if language == "ko" else
                "Understood. Please say it again."
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.DECLINE_COMPLETION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=(
                    self.fixture.steps[self.current_index].source_label
                    if self.active else None
                ),
                final_step=self.active and self.current_index == len(self.fixture.steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind="pending_transcript_declined",
                target_step="authoritative_current_step",
            )
            self._last_front_rule = "yes_no_open_question"
            self._replay[turn_id] = plan
            return plan
        elif pending_valid and (
            (
                not reply_withheld
                and _AFFIRMATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            )
            or binary_reply == "affirmative"
        ):
            front_rule = "yes_no_open_question"
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
            (
                not reply_withheld
                and _NEGATIVE_COMPLETION_CONFIRMATION.fullmatch(normalized_confirmation)
            )
            or binary_reply == "negative"
        ):
            front_rule = "yes_no_open_question"
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
        ) == "positive" and _OBSERVATION_PROMPT_PROBLEM.search(
            normalized_confirmation
        ):
            # "완전히 탈색됐는데 튜브가 터졌어": the endpoint is reported in
            # the same breath as something going wrong. Reading the endpoint
            # first used to advance on it and drop the problem. Now the
            # problem is recorded and nothing moves; the question stays open,
            # without taking a bare yes, and the endpoint is asked for again.
            front_rule = "observation_reply"
            self._pending_observation_confirmation = replace(
                observation_pending, requested_turn_id=turn_id,
                requested_generation=generation, accepts_yes_no=False,
            )
            intent = CuratedControlIntent(
                intent_kind="observation_with_anomaly",
                action=CuratedProtocolAction.REPORT_ANOMALY,
                question_kind="anomaly",
                target_step="authoritative_current_step",
                confidence_source="server_pending_observation",
                language=language,
                reported_anomaly=True,
                anomaly_category=_anomaly_category(normalized_confirmation),
                normalized_transcript=normalized_confirmation,
            )
        elif observation_pending_valid and observed is not None:
            front_rule = "observation_reply"
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
        elif observation_pending_valid and observation_pending.accepts_yes_no and (
            observation_reply := _observation_binary_reply(transcript)
        ) is not None:
            front_rule = "observation_reply"
            self._pending_observation_confirmation = None
            observed = (
                observation_pending.affirmative_outcome
                if observation_reply == "affirmative"
                else observation_pending.negative_outcome
            )
            intent = CuratedControlIntent(
                intent_kind="pending_observation_confirmed",
                action=CuratedProtocolAction.NEXT,
                reported_completion=observed == "positive",
                reported_observation=True,
                observation_predicate=observed,
                # A bare "네" says nothing on its own; the record keeps the
                # endpoint it answered, quoted, beside the words spoken.
                observation_outcome=self._endpoint_answer_record(
                    observation_pending.step_index, transcript, language
                ),
                requested_transition=("next" if observed == "positive" else None),
                requested_followup="describe_new_current_step",
                target_step="authoritative_current_step",
                confidence_source="server_pending_observation_binary_reply",
                allows_state_mutation=observed == "positive",
                language=language,
                normalized_transcript=normalized_confirmation,
            )
        elif pending_language_mismatch:
            front_rule = "stt_unreliable"
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
            if stop_pending_valid:
                # Anything but a yes or a no leaves the end question unanswered.
                self._pending_stop_confirmation = None
            if timer_pending_valid:
                # And the timer duration question.
                self._pending_timer_confirmation = None
            if anomaly_pending_valid:
                # And "이상 사항으로 기록할까요?".
                self._pending_anomaly_confirmation = None
            if note_pending_valid:
                # A new command cancels the one-turn note prompt before routing.
                self._pending_note_capture = None
            if pending_valid:
                # A non-answer invalidates the one-turn gate before normal routing.
                self._pending_completion_confirmation = None
            # An observation prompt this turn still owns is not given up on a
            # reply it could not read. Dropping it here sent every unread
            # description ("결과는 탈색돼 있어") to the general classifier,
            # where the endpoint's own words -- 탈색, 밴드 -- made it a term
            # question and the server attached a web search. It is cleared
            # now and _observation_prompt_reply decides, after routing,
            # whether it is kept, asked again, or let go.
            observation_hold = (
                observation_pending
                if observation_pending_valid and self._pause_state != "paused"
                else None
            )
            if observation_pending is not None:
                self._pending_observation_confirmation = None
            pending_anomaly = self._pending_anomaly
            if (
                pending_anomaly
                and self.active
                and _utterance_looks_like_anomaly_follow_up(transcript)
            ):
                if observation_hold is not None:
                    # Detail added to a problem recorded under an open
                    # endpoint question leaves that question open, still
                    # without taking a bare yes.
                    self._pending_observation_confirmation = replace(
                        observation_hold, requested_turn_id=turn_id,
                        requested_generation=generation, accepts_yes_no=False,
                    )
                observation_hold = None
                front_rule = "observation_reply"
                intent = CuratedControlIntent(
                    intent_kind="enrich_pending_anomaly",
                    action=CuratedProtocolAction.REPORT_ANOMALY,
                    question_kind="anomaly",
                    language=language,
                    reported_anomaly=True,
                    anomaly_category=str(
                        pending_anomaly.get("category") or "protocol_block"
                    ),
                    normalized_transcript=_utterance_key(transcript),
                )
            else:
                if self.active and 0 <= self.current_index < len(self.fixture.steps):
                    step_lbl = self.fixture.steps[self.current_index].source_label
                    repair = classify_contextual_transcript_repair(transcript, step_lbl, language)
                    if repair.status == "safe_autocorrection":
                        transcript = repair.normalized_transcript
                    elif (
                        repair.status == "confirmation_required"
                        and repair.proposed_transcript
                        and observation_hold is None
                    ):
                        self._pending_transcript_confirmation = PendingTranscriptConfirmation(
                            configuration_id=configuration_id,
                            step_id=self.fixture.steps[self.current_index].step_id,
                            step_index=self.current_index,
                            step_label=step_lbl,
                            workflow_revision=self._revision,
                            requested_turn_id=turn_id,
                            requested_generation=generation,
                            proposed_transcript=repair.proposed_transcript,
                            proposed_action=repair.proposed_action or CuratedProtocolAction.NEXT,
                            proposed_target_step=repair.proposed_target_step,
                        )
                        clarification = (
                            f"‘{repair.proposed_transcript}’라고 말씀하신 건가요?"
                            if language == "ko" else
                            f"Did you say '{repair.proposed_transcript}'?"
                        )
                        plan = CuratedProtocolTurnPlan(
                            action=CuratedProtocolAction.CLARIFY_COMPLETION,
                            display_text=clarification,
                            speech_text=clarification,
                            speech_mode=CuratedProtocolSpeechMode.CONTROL,
                            facts=self.fixture.facts_for_step(self.current_index),
                            step_label=step_lbl,
                            final_step=self.current_index == len(self.fixture.steps) - 1,
                            state_changed=False,
                            primary_text=clarification,
                            intent_kind="corrupted_completion_confirmation_required",
                            target_step=step_lbl,
                        )
                        self._last_front_rule = "stt_unreliable"
                        self._replay[turn_id] = plan
                        return plan

                shared_decision = arbitration or arbitrate_request(transcript)
                intent = self.deterministic_control_intent(
                    transcript,
                    language=language,
                    arbitration=shared_decision,
                )
                classified = intent
                # The deterministic result above is the fast path and the
                # default.  A semantic proposal only ever gets to replace a
                # catch-all outcome, and only after server-owned policy accepts
                # it; every gate below still runs on whatever survives.
                if semantic_proposal is not None:
                    intent = self._apply_semantic_intent_fallback(
                        intent,
                        semantic_proposal,
                        transcript=transcript,
                        language=language,
                        arbitration_intent=shared_decision.intent.value,
                        settings=(
                            semantic_settings or SemanticIntentSettings()
                        ),
                    )
                if observation_hold is not None:
                    # An open endpoint question owns the whole turn (F5).
                    front_rule = "observation_reply"
                    intent = self._observation_prompt_reply(
                        observation_hold,
                        intent,
                        transcript,
                        turn_id=turn_id,
                        generation=generation,
                        language=language,
                        normalized_transcript=normalized_confirmation,
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
        # Same derivation as the predicate id above, for the same reason: an
        # utterance is only read as an endpoint observation at a step the
        # source states a repeat at. Where the phrase families below do not
        # recognise the wording -- which is every document but in-gel, since
        # those phrases are in-gel's endpoints -- the elif falls through to
        # observation_confirmation_required, and the operator's "네" answers
        # it. That path is label-agnostic, so the gate is releasable on a
        # document this module has never seen, by confirmation rather than by
        # phrase recognition.
        # A completion naming another step ("8단계 완료했어" at 7) is first
        # asked which step was meant, as at any other step. Read as an
        # endpoint question instead, a "네" to it released this step on a
        # report that was about a different one.
        if (
            self.active
            and self.fixture.steps[self.current_index].step_id
            in self._steps_anchoring_a_repetition()
            and not intent.reported_observation
            and intent.intent_kind != "observation_with_anomaly"
            and not stale_observation_reply
            and not self._names_another_step(intent)
        ):
            observed = _observation_predicate(
                self.fixture.steps[self.current_index].source_label, transcript
            )
            if observed == "positive" and _OBSERVATION_PROMPT_PROBLEM.search(
                normalized_confirmation
            ):
                # The endpoint with a problem, said with no question open, is
                # taken as it is under one: the problem is recorded, nothing
                # moves, and the endpoint question is opened -- without taking
                # a bare yes -- once the turn is planned (below). It used to
                # advance on the endpoint and drop the problem (lane O §8-2).
                front_rule = "observation_reply"
                intent = CuratedControlIntent(
                    intent_kind="observation_with_anomaly",
                    action=CuratedProtocolAction.REPORT_ANOMALY,
                    question_kind="anomaly",
                    target_step="authoritative_current_step",
                    confidence_source="server_observation_reading",
                    language=language,
                    reported_anomaly=True,
                    anomaly_category=_anomaly_category(normalized_confirmation),
                    normalized_transcript=normalized_confirmation,
                )
            elif observed is not None:
                front_rule = "observation_reply"
                intent = replace(
                    intent,
                    action=CuratedProtocolAction.NEXT,
                    reported_completion=observed == "positive",
                    reported_observation=True,
                    observation_predicate=observed,
                    observation_outcome=normalized_confirmation,
                    allows_state_mutation=observed == "positive",
                    requested_transition=("next" if observed == "positive" else None),
                    target_step="authoritative_current_step",
                    intent_kind=(
                        "direct_positive_observation"
                        if observed == "positive"
                        else "direct_negative_observation"
                    ),
                )
            elif intent.action is CuratedProtocolAction.NEXT and intent.reported_completion:
                intent = replace(
                    intent,
                    intent_kind="observation_confirmation_required",
                    action=CuratedProtocolAction.CLARIFY_COMPLETION,
                    reported_completion=False,
                    requires_confirmation=True,
                    allows_state_mutation=False,
                )
        if (
            intent.action is CuratedProtocolAction.NEXT
            and intent.intent_kind in _UNTARGETED_COMPLETION_KINDS
        ):
            # "완료했어" names no step: asked about once, "N단계 완료하셨나요?",
            # rather than completing whatever step is current. A step with an
            # observed endpoint asked for the observation above instead.
            intent = replace(
                intent,
                intent_kind="next_step_confirmation_required",
                action=CuratedProtocolAction.CLARIFY_COMPLETION,
                reported_completion=False,
                requested_transition="next",
                requested_followup="confirm_current_step_completion",
                target_step="authoritative_current_step",
                requires_confirmation=True,
                allows_state_mutation=False,
            )
        if (
            transcript_quality is not None
            and (
                intent.action not in {
                    CuratedProtocolAction.STOP,
                    CuratedProtocolAction.AUDIO_RECOVERY,
                }
                # A yes that would end the session is not taken from a
                # transcript the provider was unsure of.
                or intent.intent_kind == "stop_confirmed"
            )
        ):
            front_rule = "stt_unreliable"
            intent = CuratedControlIntent(
                intent_kind="transcript_unreliable",
                action=CuratedProtocolAction.TRANSCRIPT_UNRELIABLE,
                transcript_quality=transcript_quality,
                confidence=None,
                confidence_source="provider_metadata",
                language=language,
            )
        if (
            self._pause_state == "paused"
            and intent.action is CuratedProtocolAction.START
            and any(
                pattern.search(intent.normalized_transcript or "")
                for pattern in _RESUME_PATTERNS
            )
        ):
            # "재개", "프로토콜 재개" and "resume" are in the workflow-command
            # table as START, which is looked up before the resume patterns
            # that also claim them. While paused, START is turned away by the
            # paused guard below -- silently -- so a bare "재개" got no answer
            # where "재개해줘" resumed (2026-10-01 test). Paused, they resume.
            intent = replace(
                intent,
                intent_kind="resume_workflow",
                action=CuratedProtocolAction.RESUME,
                requested_transition=None,
                requested_followup=None,
            )
        if front_rule is None:
            front_rule = self._front_rule_for(intent, classified)
        self._last_front_rule = front_rule
        if front_only and front_rule is None:
            # No front rule owns this turn: it is handed on untouched.
            (
                self._last_semantic_decision,
                self._pending_completion_confirmation,
                self._pending_observation_confirmation,
                self._pending_transcript_confirmation,
                self._pending_note_capture,
                self._pending_stop_confirmation,
                self._pending_timer_confirmation,
                self._pending_anomaly_confirmation,
            ) = untouched
            return None
        return self._execute_turn_intent(
            intent,
            transcript=transcript,
            command_key=command_key,
            turn_id=turn_id,
            language=language,
            configuration_id=configuration_id,
            generation=generation,
            actor_principal_id=actor_principal_id,
            actor_role=actor_role,
            open_question=open_question,
        )

    def _execute_turn_intent(
        self,
        intent: CuratedControlIntent,
        *,
        transcript: str,
        command_key: str,
        turn_id: int,
        language: str,
        configuration_id: int | None,
        generation: int | None,
        actor_principal_id: str | None,
        actor_role: str,
        open_question: dict[str, Any] | None,
    ) -> CuratedProtocolTurnPlan:
        """Carry out what a turn was read as: its branch, then the turn's gates.

        plan() reaches this once the rules have read the turn, and so does a
        tool proposal the server accepted (apply_tool_proposal): one set of
        branches and one set of post-turn gates for both, never a second
        state machine. ``open_question`` is the question a pause would hold.
        """

        command = intent.action
        steps = self.fixture.steps
        opening_projection = (
            self.active, self.current_index, self._block_reason, self._workflow_status,
        )
        changed = False
        # Set when a voice resume lifts a pause that held a question; asked
        # again once the turn is planned.
        reasked_question: dict[str, Any] | None = None

        if (
            command in {
                CuratedProtocolAction.START, CuratedProtocolAction.RESUME,
                CuratedProtocolAction.PAUSE,
            }
            and self.experiment_ended
        ):
            # Decision 2 (2026-10-03): an ended experiment is not started
            # again by voice -- a start used to begin it over from step 1,
            # and a resume did the same through resume_workflow(). Nothing
            # changes; the next experiment is chosen on the screen. Nor is
            # it paused (lane R3): there is nothing running to pause.
            response = EXPERIMENT_ENDED_START_REPLY.get(
                language, EXPERIMENT_ENDED_START_REPLY["ko"]
            )
            return CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.INACTIVE,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=None,
                final_step=False,
                state_changed=False,
                primary_text=response,
                intent_kind=(
                    "pause_after_experiment_ended"
                    if command is CuratedProtocolAction.PAUSE
                    else "start_after_experiment_ended"
                ),
            )

        if self._pause_state == "paused" and command not in {
            CuratedProtocolAction.RESUME,
            CuratedProtocolAction.STOP,
            CuratedProtocolAction.PAUSE,
        }:
            response = (
                "현재 실험 안내가 일시정지 상태입니다. '재개'라고 말씀하시거나 재개 버튼을 눌러주세요."
                if language == "ko" else
                "Workflow guidance is currently paused. Please say 'resume' or click the resume button to continue."
            )
            # The first word turned away in a pause is answered aloud, once,
            # so a researcher who forgot the pause hears why nothing happens;
            # after that the notice is shown only. Nothing else changes.
            spoken = "" if self._paused_notice_spoken else (
                "지금 일시정지 중이에요. '재개'라고 말씀해 주세요."
                if language == "ko" else
                "Guidance is paused right now. Say 'resume' to continue."
            )
            self._paused_notice_spoken = True
            return CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.PAUSE,
                display_text=response,
                speech_text=spoken,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind=intent.intent_kind,
                speech_policy="speak" if spoken else "silent",
            )

        if command is CuratedProtocolAction.STOP and intent.intent_kind != "stop_confirmed" and (
            self.active
            or (
                self._experiment_started_at is not None
                and self._experiment_ended_at is None
            )
        ):
            # Ending closes the experiment record, so a "종료" command is
            # asked about once; only a yes on the next turn ends it.
            self._pending_stop_confirmation = {
                "configuration_id": configuration_id,
                "requested_turn_id": turn_id,
                "requested_generation": generation,
            }
            response = END_CONFIRMATION_QUESTION.get(
                language, END_CONFIRMATION_QUESTION["ko"]
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.STOP,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind="stop_confirmation_required",
            )
        elif command is CuratedProtocolAction.STOP:
            changed = self.active or self._experiment_started_at is not None
            stopped_at = (
                steps[self.current_index].source_label
                if self.active and 0 <= self.current_index < len(steps) else None
            )
            self.active = False
            self._block_reason = None
            self._workflow_status = "stopped"
            self._stop_experiment_clock()
            self._clear_step_timer()
            self._pending_anomaly = None
            self._pending_stop_confirmation = None
            self._frozen_question = None
            action = CuratedProtocolAction.STOP
            if changed and stopped_at is not None:
                # Decision 2: how the experiment ended, said as it happened.
                # Whether the record was saved is the server's to add, once
                # the report store has answered.
                response = {
                    "en": f"The experiment was ended at step {stopped_at}.",
                    "vi": f"Thí nghiệm đã kết thúc ở bước {stopped_at}.",
                    "ko": f"{stopped_at}단계에서 실험을 종료했습니다.",
                }.get(language, f"{stopped_at}단계에서 실험을 종료했습니다.")
            elif changed:
                response = {
                    "en": "The experiment was ended.",
                    "vi": "Thí nghiệm đã kết thúc.",
                    "ko": "실험을 종료했습니다.",
                }.get(language, "실험을 종료했습니다.")
            else:
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
            resumed = self.active
            clock_started = self._start_experiment_clock_once()
            if not self.active:
                self.active = True
                self._workflow_status = "active"
                self.current_index = 0
                self._block_reason = None
                changed = True
            else:
                self._workflow_status = "active"
                if clock_started:
                    changed = True
            step = steps[self.current_index]
            timer_active = (self._timer_started_at is not None and self._timer_step_index == self.current_index)
            control_text = _control_speech(
                CuratedProtocolAction.START,
                language,
                step.source_label,
                resumed=resumed,
                development_only=self.fixture.development_only,
                step_index=self.current_index,
                timer_active=timer_active,
                step_timer_seconds=self.timer_seconds_for_step(
                    self.current_index
                ),
            )
            if language == "ko" and not resumed:
                # The opening names the first step in the protocol's own
                # reviewed Korean translation; with none, the step is on
                # screen from its source. Nothing here is one PDF's wording.
                first = self._localized_fact(step.step_id, "current_step")
                if first is not None:
                    first = _without_step_prefix(step.source_label, first)
                control_text = (
                    f"실험을 시작합니다. 현재 {step.source_label}단계입니다. "
                    + (first or "안내를 화면에 표시했습니다.")
                    + control_text.partition("표시했습니다.")[2]
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
                display_document=_display_document(
                    title=f"{step.source_label}단계",
                    primary=primary,
                    source=sources[0] if sources else None,
                ),
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
        elif command is CuratedProtocolAction.AGENT_META:
            response = (
                "저는 승인된 실험 프로토콜의 단계별 음성 안내, 배양 타이머 관리, 이상 사항 및 관찰 기록, 실험 보고서 생성, 그리고 프로토콜 및 승인된 참고자료 기반 질의응답을 지원하는 실험실 보이스 워크플로 에이전트입니다. 프로토콜을 시작하시려면 '실험 시작'이라고 말씀해 주세요."
                if language == "ko" else
                "I am a laboratory voice workflow assistant that provides step-by-step voice guidance for approved protocols, timer management, observation and anomaly recording, experiment report generation, and grounded QA over protocols and approved reference sources. To begin the workflow, please say 'start protocol'."
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.AGENT_META,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind=intent.intent_kind,
                question_kind="agent_meta",
            )
        elif command is CuratedProtocolAction.PAUSE:
            # A pause the running protocol actually took is a state change, as
            # the bench button's is, so the server mirrors it into the durable
            # experiment record. Reported as unchanged, the record stayed
            # in_progress and the voice resume that followed was refused as
            # in_progress -> in_progress (2026-10-01 test, "재개해줘").
            # A protocol that has not started has no running record to pause.
            paused = self.pause_workflow() and self.active
            # The timer is mentioned only when a step timer is running.
            timer_running = self.timer_status().get("state") == "running"
            response = (
                "일시정지했어요. '재개'라고 하시면 이어서 할게요."
                + (" 타이머는 실제로는 계속 흐르고 있어요." if timer_running else "")
                if language == "ko" else
                "Paused. Say 'resume' to continue."
                + (" The timer is still running in real time." if timer_running else "")
            )
            if intent.intent_kind == "end_request_paused":
                # "그만할래" wants to stop but did not say "종료": the place is
                # kept, and how to end is said.
                response += " " + END_REQUEST_HINT.get(language, END_REQUEST_HINT["ko"])
            if paused and open_question is not None:
                # The question that was open is held, not dropped: the voice
                # resume asks it again (decision D10).
                self._frozen_question = open_question
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.PAUSE,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=paused,
                primary_text=response,
                intent_kind=intent.intent_kind,
            )
        elif command is CuratedProtocolAction.RESUME:
            # A resume changes state only when it lifts a pause or starts a
            # protocol that was not running. "계속 진행" mid-step used to
            # report a change anyway, and the server's resume mirror was then
            # refused by a record that was never paused.
            was_active = self.active
            frozen = self._frozen_question
            lifted = self.resume_workflow()
            resumed = lifted or not was_active
            if (
                lifted
                and frozen is not None
                and self.active
                and (
                    frozen["kind"] == "stop"
                    or (
                        self.current_index == frozen["step_index"]
                        and steps[self.current_index].step_id == frozen["step_id"]
                    )
                )
            ):
                reasked_question = frozen
            step = steps[self.current_index]
            timer_info = self.timer_status()
            timer_suffix = ""
            if timer_info.get("state") == "running":
                rem = timer_info.get("remaining_seconds", 0)
                timer_suffix = f" (진행 중인 타이머 {rem // 60}분 {rem % 60}초 남음)" if language == "ko" else f" (Timer running: {rem // 60}m {rem % 60}s remaining)"
            elif timer_info.get("state") == "expired":
                timer_suffix = " (타이머가 완료되었습니다)" if language == "ko" else " (Timer expired)"
            control_text = (
                f"워크플로를 재개합니다. 현재 {step.source_label}단계입니다.{timer_suffix}"
                if language == "ko" else
                f"Resuming protocol. Currently at step {step.source_label}.{timer_suffix}"
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
                action=CuratedProtocolAction.RESUME,
                display_text=response,
                speech_text=control_text,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=resumed,
                primary_text=primary,
                source_texts=sources,
                source_pages=pages,
                evidence_ids=evidence_ids,
                translation_status=translation_status,
                intent_kind=intent.intent_kind,
            )
        elif command is CuratedProtocolAction.REPORT_HANDOFF:
            recip_label = "지도교수님" if any(t in transcript for t in ("교수", "교수님", "advisor", "professor")) else "연구실 안전관리자"
            recip_email = "advisor@university.edu" if "교수" in recip_label else "safety@university.edu"
            response = (
                f"등록된 {recip_label}({recip_email})로 현재 실험 보고서를 전송할까요? 전송을 진행하시려면 '응, 보내줘'라고 말씀해 주세요."
                if language == "ko" else
                f"Shall I send the current laboratory report to {recip_label} ({recip_email})? Please say 'yes, send it' to confirm."
            )
            self._pending_handoff_confirmation = {
                "recipient_name": recip_label,
                "recipient_email": recip_email,
            }
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.REPORT_HANDOFF,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind="report_handoff",
            )
        elif command is CuratedProtocolAction.START_TIMER:
            step = steps[self.current_index]
            existing_timer = self.timer_status()
            if (
                existing_timer.get("state") == "running"
                and existing_timer.get("step_index") == self.current_index
            ):
                remaining = int(existing_timer.get("remaining_seconds", 0))
                minutes = remaining // 60
                seconds = remaining % 60
                time_str = f"{minutes}분 {seconds}초" if minutes > 0 else f"{seconds}초"
                time_str_en = f"{minutes} min {seconds} s" if minutes > 0 else f"{seconds} s"
                response = (
                    f"현재 {step.source_label}단계 타이머가 이미 진행 중입니다. 남은 시간은 약 {time_str}입니다."
                    if language == "ko" else
                    f"The Step {step.source_label} timer is already running with approximately {time_str_en} remaining."
                )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.START_TIMER,
                    display_text=response,
                    speech_text=response,
                    speech_mode=CuratedProtocolSpeechMode.CONTROL,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=False,
                    primary_text=response,
                    intent_kind=intent.intent_kind,
                    timer_payload=existing_timer,
                    display_document=_display_document(
                        title=f"{step.source_label}단계 타이머",
                        primary=response,
                    ),
                )
            else:
                success, duration, _ = self.start_timer()
                if success:
                    minutes = duration // 60
                    seconds = duration % 60
                    time_str = f"{minutes}분" if seconds == 0 else f"{minutes}분 {seconds}초" if minutes > 0 else f"{seconds}초"
                    time_str_en = f"{minutes} min" if seconds == 0 else f"{minutes} min {seconds} s" if minutes > 0 else f"{seconds} s"
                    response = (
                        f"{time_str} 타이머를 시작했습니다. 화면에서 남은 시간을 확인할 수 있습니다."
                        if language == "ko" else
                        f"Started a {time_str_en} timer. You can watch the remaining time on screen."
                    )
                else:
                    response = (
                        f"현재 {step.source_label}단계에는 프로토콜에 정의된 별도 타이머가 없습니다. 전체 실험 경과 시간은 계속 기록 중입니다."
                        if language == "ko" else
                        f"Step {step.source_label} has no separate protocol-defined timer. The overall experiment elapsed time is still being recorded."
                    )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.START_TIMER,
                    display_text=response,
                    speech_text=response,
                    speech_mode=CuratedProtocolSpeechMode.CONTROL,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=True,
                    primary_text=response,
                    intent_kind=intent.intent_kind,
                    timer_payload=self.timer_status(),
                    display_document=_display_document(
                        title=f"{step.source_label}단계 타이머",
                        primary=response,
                    ),
                )
        elif command is CuratedProtocolAction.TIMER_STATUS:
            timer_info = self.timer_status()
            step = steps[self.current_index]
            state = timer_info.get("state")
            rem = timer_info.get("remaining_seconds", 0)
            minutes = rem // 60
            seconds = rem % 60
            if intent.intent_kind == "semantic_step_timer_information":
                if state == "running":
                    time_str = f"{minutes}분 {seconds}초" if minutes > 0 else f"{seconds}초"
                    time_str_en = f"{minutes} min {seconds} s" if minutes > 0 else f"{seconds} s"
                    response = (
                        f"현재 {step.source_label}단계 타이머가 이미 진행 중이라 다시 시작하거나 초기화하지 않습니다. 남은 시간은 약 {time_str}입니다. 이 질문으로 상태는 변경되지 않았습니다."
                        if language == "ko" else
                        f"The Step {step.source_label} timer is already running, so it will not be restarted or reset. Approximately {time_str_en} remains. This question did not change state."
                    )
                elif int(timer_info.get("duration_seconds", 0)) > 0:
                    duration = int(timer_info["duration_seconds"])
                    response = (
                        f"명확히 타이머 시작을 요청하면 현재 {step.source_label}단계에 정의된 {duration // 60}분 타이머를 시작합니다. 이 질문만으로는 타이머를 시작하지 않았습니다."
                        if language == "ko" else
                        f"An explicit start request would start the protocol-defined {duration // 60}-minute timer for Step {step.source_label}. This question did not start it."
                    )
                else:
                    response = (
                        f"현재 {step.source_label}단계에는 프로토콜에 정의된 별도 타이머가 없습니다. 이 질문으로 타이머를 만들거나 상태를 변경하지 않았습니다."
                        if language == "ko" else
                        f"Step {step.source_label} has no protocol-defined timer. This question did not create a timer or change state."
                    )
            elif state == "running":
                time_str = f"{minutes}분 {seconds}초" if minutes > 0 else f"{seconds}초"
                time_str_en = f"{minutes} min {seconds} s" if minutes > 0 else f"{seconds} s"
                response = (
                    f"현재 {step.source_label}단계 타이머가 진행 중이며, 약 {time_str} 남았습니다."
                    if language == "ko" else
                    f"Step {step.source_label} timer is running with approximately {time_str_en} remaining."
                )
            elif state == "expired":
                response = (
                    f"현재 {step.source_label}단계 타이머가 이미 완료되었습니다. 다음 작업으로 진행할 수 있습니다."
                    if language == "ko" else
                    f"Step {step.source_label} timer has expired. You can proceed with the next action."
                )
            else:
                dur = timer_info.get("duration_seconds", 0)
                if dur > 0:
                    time_str = f"{dur // 60}분"
                    response = (
                        f"현재 {step.source_label}단계는 {time_str} 배양 단계입니다. 아직 타이머가 시작되지 않았습니다. 시작하시려면 '타이머 시작해'라고 말씀해 주세요."
                        if language == "ko" else
                        f"Step {step.source_label} is a {dur // 60}-minute step. The timer has not been started yet. Say 'start timer' to begin."
                    )
                else:
                    response = (
                        f"현재 {step.source_label}단계에는 프로토콜에 정의된 별도 타이머가 없습니다. 전체 실험 경과 시간은 계속 기록 중입니다."
                        if language == "ko" else
                        f"Step {step.source_label} has no separate protocol-defined timer. The overall experiment elapsed time is still being recorded."
                    )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.TIMER_STATUS,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind=intent.intent_kind,
            )
        elif command is CuratedProtocolAction.PREVIEW_STEP and (
            self._step_index_for_label(intent.target_step or "1") is None
        ):
            # A step the protocol does not have is refused, as the full-detail
            # lookup refuses it, rather than previewing step 1 in its place.
            response = {
                "en": "That step is not present in the selected protocol. The current step did not change.",
                "vi": "Bước đó không có trong quy trình đã chọn. Bước hiện tại không thay đổi.",
                "ko": "선택한 절차에 해당 단계가 없습니다. 현재 단계는 변경하지 않았습니다.",
            }.get(language, "해당 단계를 확인할 수 없습니다.")
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.PREVIEW_STEP,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(),
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind=intent.intent_kind,
                target_step=intent.target_step,
            )
        elif command is CuratedProtocolAction.PREVIEW_STEP:
            target_idx = self._step_index_for_label(intent.target_step or "1")
            target_step = steps[target_idx]
            localized = self._localized_fact(target_step.step_id, "current_step")
            instruction = localized if language == "ko" and localized else target_step.instruction_source_text
            current_label = steps[self.current_index].source_label if self.active else "시작 전"
            current_label_en = steps[self.current_index].source_label if self.active else "not started"
            response = (
                f"{target_step.source_label}단계 미리보기: {instruction} (현재 상태: {current_label}, 상태는 변경하지 않았습니다)"
                if language == "ko" else
                f"Step {target_step.source_label} preview: {instruction} (Current status: {current_label_en}, state not changed)"
            )
            facts = self.fixture.facts_for_step(target_idx)
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.PREVIEW_STEP,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                facts=facts,
                step_label=(steps[self.current_index].source_label if self.active else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                source_texts=tuple(fact.text for fact in facts[:4]),
                source_pages=tuple(fact.source_page for fact in facts[:4]),
                evidence_ids=tuple(fact.fact_id for fact in facts[:4]),
                translation_status=("verified_sidecar" if language == "ko" else "source_language"),
                intent_kind=intent.intent_kind,
                target_step=target_step.source_label,
            )

        elif not self.active and (
            command not in (
                CuratedProtocolAction.PROTOCOL_QUERY,
                CuratedProtocolAction.AGENT_META,
                CuratedProtocolAction.PREVIEW_STEP,
                CuratedProtocolAction.CURRENT,
                CuratedProtocolAction.FULL_DETAIL,
                CuratedProtocolAction.QUESTION,
                CuratedProtocolAction.RELATED_QUESTION,
                CuratedProtocolAction.VISUAL_REQUEST,
                CuratedProtocolAction.REPORT_HANDOFF,
            )
            or (
                command is CuratedProtocolAction.RELATED_QUESTION
                and _utterance_key(transcript) in {
                    "현재 온도는",
                    "이 작업의 온도는",
                }
            )
        ):
            if self._workflow_status in {"preview", "ready"}:
                response = {
                    "en": "The experiment has not started yet. When started, it will begin from Step 1. Please say start protocol to begin.",
                    "vi": "Thí nghiệm chưa bắt đầu. Khi bắt đầu sẽ tiến hành từ bước 1.",
                    "ko": "아직 실험을 시작하지 않았습니다. 프로토콜을 시작해 주세요. 시작하면 1단계부터 진행합니다.",
                }.get(language, "아직 실험을 시작하지 않았습니다. 프로토콜을 시작해 주세요.")
            else:
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
        elif command is CuratedProtocolAction.RECORD_OBSERVATION:
            step = steps[self.current_index]
            category = intent.observation_predicate or "note"
            content = (intent.observation_outcome or "").strip()[:4000]
            if content:
                self._pending_note_capture = None
                response = (
                    f"I recognized an observation for Step {step.source_label}. "
                    "I will confirm it only after the experiment record accepts it."
                    if language == "en" else
                    f"현재 {step.source_label}단계의 관찰 내용을 확인했습니다. "
                    "실험 기록 저장이 성공한 뒤에만 기록 완료를 확인합니다."
                )
                reported = True
            else:
                self._pending_note_capture = {
                    "configuration_id": configuration_id,
                    "requested_generation": generation,
                    "workflow_revision": self._revision,
                    "step_index": self.current_index,
                    "step_id": step.step_id,
                    "requested_turn_id": turn_id,
                    "category": category,
                }
                response = (
                    "What should I record as the observation? No protocol state has changed."
                    if language == "en" else
                    "어떤 관찰 내용을 기록할까요? 프로토콜 상태는 변경하지 않았습니다."
                )
                reported = False
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.RECORD_OBSERVATION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind=intent.intent_kind,
                target_step="authoritative_current_step",
                reported_observation=reported,
                observation_predicate=category,
                observation_outcome=content or None,
            )
        elif command is CuratedProtocolAction.REPORT_ANOMALY:
            step = steps[self.current_index]
            category = intent.anomaly_category or "protocol_block"
            existing = self._pending_anomaly if isinstance(self._pending_anomaly, dict) else None
            if intent.intent_kind == "enrich_pending_anomaly" and existing:
                notes = list(existing.get("notes") or [])
                note = transcript.strip()[:800]
                if note and note not in notes:
                    notes.append(note)
                self._pending_anomaly = {
                    **existing,
                    "notes": notes,
                    "text": note or existing.get("text"),
                    "step_label": step.source_label,
                    "step_id": step.step_id,
                }
                response = {
                    "en": (
                        f"I added that detail to the pending issue for step {step.source_label}. "
                        "I will confirm only after the experiment record accepts it."
                    ),
                    "vi": (
                        f"Tôi đã bổ sung chi tiết vào sự cố đang chờ ghi ở bước {step.source_label}. "
                        "Tôi chỉ xác nhận sau khi bản ghi thí nghiệm lưu thành công."
                    ),
                    "ko": (
                        f"현재 {step.source_label}단계의 대기 중인 이상 기록에 그 내용을 추가했습니다. "
                        "실험 기록 저장이 성공한 뒤에만 기록 완료를 확인합니다."
                    ),
                }.get(language, "이상 사항 추가 내용을 확인했습니다.")
            else:
                self._pending_anomaly = {
                    "category": category,
                    "text": transcript.strip()[:800],
                    "step_label": step.source_label,
                    "step_id": step.step_id,
                    "notes": [transcript.strip()[:800]],
                }
                if intent.intent_kind == "observation_with_anomaly":
                    # Short, as it is spoken; nothing is said to be recorded
                    # before the record has answered.
                    response = self.observation_anomaly_reply(
                        step.source_label, language, recorded=False
                    )
                elif category == "protocol_block":
                    response = {
                        "en": (
                            f"I recognized an issue to record against step {step.source_label}. "
                            "I will confirm only after the experiment record accepts it. "
                            "What kind of color change did you observe?"
                        ),
                        "vi": (
                            f"Tôi đã nhận yêu cầu ghi vấn đề ở bước {step.source_label}. "
                            "Tôi chỉ xác nhận sau khi bản ghi thí nghiệm lưu thành công. "
                            "Bạn quan sát thấy màu thay đổi như thế nào?"
                        ),
                        "ko": (
                            f"현재 {step.source_label}단계의 이상 사항 기록 요청을 확인했습니다. "
                            "실험 기록 저장이 성공한 뒤에만 기록 완료를 확인합니다. "
                            "어떤 종류의 색 변화를 보셨나요?"
                        ),
                    }.get(language, "이상 사항 기록 요청을 확인했습니다.")
                else:
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
                anomaly_category=category,
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
            if intent.protocol_scope == "version":
                document_version = (
                    self.fixture.draft.protocol.metadata.version
                    or "not stated in source metadata"
                )
                protocol_version = self.fixture.revision_id
                checksum = self.fixture.source_pdf_sha256 or "unavailable"
                if language == "ko":
                    speech = (
                        f"현재 프로토콜은 {self.fixture.title}, 실행 버전 {protocol_version}입니다. "
                        f"원문 문서 버전은 {document_version}이며 전체 해시는 화면에 표시했습니다."
                    )
                    display = (
                        f"프로토콜 감사 정보\n- 제목: {self.fixture.title}\n"
                        f"- 실행 버전: {protocol_version}\n"
                        f"- 원문 문서 버전: {document_version}\n"
                        f"- 원문 PDF SHA-256: {checksum}\n"
                        f"- 구조화 fixture SHA-256: {self.fixture.fixture_sha256}"
                    )
                else:
                    speech = (
                        f"The active protocol is {self.fixture.title}, revision {protocol_version}. "
                        f"The source document version is {document_version}; full hashes are shown on screen."
                    )
                    display = (
                        f"Protocol audit\n- Title: {self.fixture.title}\n"
                        f"- Runtime revision: {protocol_version}\n"
                        f"- Source document version: {document_version}\n"
                        f"- Source PDF SHA-256: {checksum}\n"
                        f"- Structured fixture SHA-256: {self.fixture.fixture_sha256}"
                    )
                source_page = self.fixture.steps[0].evidence.source_page_number
                protocol_facts = (
                    CuratedProtocolFact(
                        "protocol_audit_identity",
                        "protocol_metadata",
                        f"{self.fixture.title}; revision={protocol_version}; document_version={document_version}; sha256={checksum}",
                        source_page,
                    ),
                )
            else:
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
                answer_origin=(
                    "protocol_metadata"
                    if intent.protocol_scope == "version"
                    else "current_protocol"
                ),
            )
        elif command is CuratedProtocolAction.QUESTION and intent.intent_kind in {
            "current_step_learning",
            "current_step_warning",
        }:
            step = steps[self.current_index]
            display, speech, learning_facts, limitations = (
                self._step_learning_presentation(
                    language=language,
                    warning_only=intent.intent_kind == "current_step_warning",
                )
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.QUESTION,
                display_text=display,
                speech_text=speech,
                speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                facts=learning_facts,
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=speech,
                source_texts=tuple(fact.text for fact in learning_facts),
                source_pages=tuple(fact.source_page for fact in learning_facts),
                evidence_ids=tuple(fact.fact_id for fact in learning_facts),
                translation_status=(
                    "verified_sidecar" if language == "ko" else "source_language"
                ),
                intent_kind=intent.intent_kind,
                requested_followup=intent.requested_followup,
                target_step=step.source_label,
                question_kind=intent.question_kind,
                answer_origin="approved_step_metadata",
                limitations=limitations,
                normalized_transcript=intent.normalized_transcript,
                question_dimensions=intent.question_dimensions,
            )
        elif command is CuratedProtocolAction.QUESTION and intent.intent_kind in {
            "previous_experiment_resume",
            "experiment_history",
        }:
            step = steps[self.current_index]
            response = (
                "저장된 진행 중인 이전 실험 세션을 찾지 못했습니다. 현재 프로토콜을 이전 실험으로 간주하지 않았습니다."
                if language == "ko" and intent.intent_kind == "previous_experiment_resume"
                else "이 실행 경로에서 확인할 수 있는 이전 실험 기록이 없습니다. 현재 세션 상태는 변경하지 않았습니다."
                if language == "ko"
                else "No saved previous in-progress experiment session was found. The current protocol was not treated as a previous experiment."
                if intent.intent_kind == "previous_experiment_resume"
                else "No previous experiment history is available to this runtime. The current session was not changed."
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.QUESTION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind=intent.intent_kind,
                requested_followup=intent.requested_followup,
                question_kind=intent.question_kind,
                answer_origin="session_history",
                limitations=("curated_runtime_has_no_resumable_persisted_snapshot",),
                normalized_transcript=intent.normalized_transcript,
            )
        elif command is CuratedProtocolAction.QUESTION and intent.intent_kind == "bounded_outcome_uncertainty":
            step = steps[self.current_index]
            response = (
                "현재 정보만으로 실험 성공 여부를 판단할 수 없습니다. 관찰 결과나 측정값을 기록하면 확인 가능한 범위에서 함께 살펴볼 수 있습니다."
                if language == "ko"
                else "The current information cannot determine whether the experiment will succeed. Record observations or measurements so they can be assessed within the available evidence."
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.QUESTION,
                display_text=response,
                speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=(),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                primary_text=response,
                intent_kind=intent.intent_kind,
                requested_followup=intent.requested_followup,
                question_kind=intent.question_kind,
                answer_origin="bounded_uncertainty",
                limitations=("outcome_not_determinable_from_current_evidence",),
                normalized_transcript=intent.normalized_transcript,
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
                # An open endpoint gate counts as an open execution gate
                # here. It used to be visible only through
                # unsupported_repeat_until, so declaring the capability would
                # have taken this notice away while the gate itself stayed
                # shut -- the preview would have read as an authorisation.
                held = (
                    self._current_step_readiness_blocker() is not None
                    or self.endpoint_observation_outstanding(self.current_index)
                )
                blocker_text = (
                    " The current step has an unresolved execution gate, so this preview does not authorize entry."
                    if held and language == "en" else
                    " 현재 단계의 실행 제어가 미해결이므로 이 미리보기는 진입 승인이 아닙니다."
                    if held else ""
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
            current_fact = next(
                (fact for fact in step_facts if fact.fact_id == "current_step"),
                None,
            )
            current_task = (
                self._localized_fact(step.step_id, "current_step")
                or (current_fact.text if current_fact is not None else step.instruction_source_text)
            )
            expected_text = " ".join(
                self._localized_fact(step.step_id, fact.fact_id) or fact.text
                for fact in expected
            )
            blocker = self._current_step_readiness_blocker()
            if blocker is not None:
                review_reason = (
                    "The repeat end point still needs review before it can be used to confirm completion."
                    if blocker is domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL else
                    "The source instruction is ambiguous and still needs review."
                )
                if language == "en":
                    response = (
                        f"The documented check for this step is: {expected_text} "
                        f"{review_reason} Do not mark this step complete until that review is resolved."
                        if expected else
                        f"The document does not state a separate completion criterion for this step. "
                        f"The current task is: {current_task} {review_reason} "
                        "Do not mark this step complete until that review is resolved."
                    )
                else:
                    review_reason = (
                        "반복 종료 확인 방식은 아직 검토가 필요합니다."
                        if blocker is domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL else
                        "원문의 실행 의미가 모호해 아직 검토가 필요합니다."
                    )
                    response = (
                        f"문서에 명시된 이 단계의 확인 기준은 다음과 같습니다: {expected_text} "
                        f"다만 {review_reason} 검토가 끝날 때까지 이 단계를 완료 처리하지 마세요."
                        if expected else
                        "이 단계에는 별도의 완료 기준이 문서에 명시되어 있지 않습니다. "
                        f"현재 해야 할 작업은 {current_task}입니다. 다만 {review_reason} "
                        "검토가 끝날 때까지 이 단계를 완료 처리하지 마세요."
                    )
            elif self.endpoint_observation_outstanding(self.current_index):
                # The caveat above was reached at a repeat step only because
                # readiness carried unsupported_repeat_until for it. The
                # step's completion waits on the operator's observation
                # whatever readiness says, so the answer states that on its
                # own footing rather than losing the caveat along with the
                # reason. Unlike the branch above there is no review to wait
                # for: the person at the bench resolves this by looking.
                interval = self.repetition_anchored_at(step.step_id)
                stated = (
                    " ".join(str(interval["source_text"]).split())
                    if interval is not None else ""
                )
                quoted_en = f' The source states its endpoint as: “{stated}”' if stated else ""
                quoted_ko = f' 원문이 적은 종점은 “{stated}”입니다.' if stated else ""
                if language == "en":
                    response = (
                        f"The documented check for this step is: {expected_text} "
                        "This step repeats until an observed endpoint is reached."
                        f"{quoted_en} Do not mark it complete until you report that observation."
                        if expected else
                        "The document does not state a separate completion criterion for this step. "
                        f"The current task is: {current_task} This step repeats until an "
                        "observed endpoint is reached."
                        f"{quoted_en} Do not mark it complete until you report that observation."
                    )
                else:
                    response = (
                        f"문서에 명시된 이 단계의 확인 기준은 다음과 같습니다: {expected_text} "
                        "다만 이 단계는 관찰 결과가 충족될 때까지 반복하는 단계입니다."
                        f"{quoted_ko} 그 관찰 결과를 말씀하기 전에는 이 단계를 완료 처리하지 마세요."
                        if expected else
                        "이 단계에는 별도의 완료 기준이 문서에 명시되어 있지 않습니다. "
                        f"현재 해야 할 작업은 {current_task}입니다. 다만 이 단계는 "
                        "관찰 결과가 충족될 때까지 반복하는 단계입니다."
                        f"{quoted_ko} 그 관찰 결과를 말씀하기 전에는 이 단계를 완료 처리하지 마세요."
                    )
            elif expected:
                response = (
                    f"The documented check for this step is: {expected_text} "
                    "After confirming it and finishing the current task, say, 'I completed the current step.'"
                    if language == "en" else
                    f"문서에 명시된 이 단계의 확인 기준은 다음과 같습니다: {expected_text} "
                    "이 기준을 확인하고 현재 작업을 마쳤다면 '현재 단계 완료했어'라고 말해 주세요."
                )
            else:
                response = (
                    "The document does not state a separate completion criterion for this step. "
                    f"The current task is: {current_task} "
                    "After finishing it, say, 'I completed the current step.'"
                    if language == "en" else
                    "이 단계에는 별도의 완료 기준이 문서에 명시되어 있지 않습니다. "
                    f"현재 해야 할 작업은 {current_task}입니다. "
                    "이 작업을 마쳤다면 '현재 단계 완료했어'라고 말해 주세요."
                )
            evidence = expected or ((current_fact,) if current_fact is not None else ())
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
            if self._names_another_step(intent):
                current_label = steps[self.current_index].source_label
                self._pending_completion_confirmation = PendingCompletionConfirmation(
                    configuration_id=configuration_id,
                    requested_generation=generation,
                    workflow_revision=self._revision,
                    step_index=self.current_index,
                    step_id=steps[self.current_index].step_id,
                    requested_turn_id=turn_id,
                    requested_target_step=intent.target_step,
                )
                if language == "ko":
                    clarification = (
                        f"현재 진행 중인 단계는 {current_label}단계입니다. "
                        f"{current_label}단계를 완료하셨다는 뜻인가요?"
                    )
                else:
                    clarification = (
                        f"The current step in progress is Step {current_label}. "
                        f"Did you mean you completed Step {current_label}?"
                    )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.CLARIFY_COMPLETION,
                    display_text=clarification,
                    speech_text=clarification,
                    speech_mode=CuratedProtocolSpeechMode.CONTROL,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=current_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=False,
                    primary_text=clarification,
                    intent_kind="explicit_step_mismatch_clarification",
                    target_step=current_label,
                )
                self._replay[turn_id] = plan
                return plan
            blocker = self._current_step_readiness_blocker(
                intent.observation_predicate
            )
            if (
                intent.reported_observation
                and intent.observation_predicate == "negative"
            ):
                step = steps[self.current_index]
                # What this step's endpoint is, and which steps repeat, are the
                # document's statements. Both were written here as in-gel's own
                # phrases and ranges chosen by label -- "fully destained",
                # "Steps 2-7", "Steps 17-18" -- so on any other source this
                # sentence would have told an experimenter to repeat steps 17-18 of
                # a document that never mentions them. That is principle 1, and a
                # fabricated source instruction is principle 8 as well. Both come
                # from the repetition construct the analysis anchored at this step
                # now, and the endpoint is quoted instead of paraphrased.
                interval = self.repetition_anchored_at(step.step_id)
                endpoint = (
                    " ".join(str(interval["source_text"]).split())
                    if interval is not None else ""
                )
                unresolved = any(
                    reason.step_id == step.step_id
                    and reason.code
                    is domain.ReadinessReasonCode.UNRESOLVED_AMBIGUITY
                    for reason in self.fixture.draft.readiness.reasons
                )
                if interval is None:
                    # Nothing captured to name. The report is still honoured and
                    # the step still held; no range is invented to fill the gap.
                    followup_en = ""
                    followup_ko = ""
                else:
                    first, last = self._range_labels(interval)
                    if unresolved:
                        followup_en = f"The source mentions repeating Steps {first}–{last}, but that sequence remains unresolved here; no loop transition was executed."
                        followup_ko = f"원문에는 {first}–{last}단계 반복이 적혀 있지만 그 순서가 미해결이므로 반복 이동을 실행하지 않습니다."
                    else:
                        followup_en = f"Continue the source-authorized Steps {first}–{last} repeat cycle, then report the visible endpoint again."
                        followup_ko = f"원문이 지시한 {first}–{last}단계 반복 주기를 계속한 뒤 관찰 결과를 다시 말씀해 주세요."
                stated = f' (\u201c{endpoint}\u201d)' if endpoint else ""
                response = (
                    f"Your report means the source-defined endpoint{stated} is not yet satisfied for Step {step.source_label}. "
                    f"The step remains current and no transition was made. {followup_en}"
                    if language == "en" else
                    f"말씀한 결과는 {step.source_label}단계의 원문 관찰 기준이 아직 충족되지 않았다는 뜻입니다. "
                    f"현재 단계를 유지하며 다음 단계로 이동하지 않습니다. {followup_ko}"
                ).strip()
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
            elif self.endpoint_observation_outstanding(
                self.current_index, intent.observation_predicate
            ):
                # The gate, standing on the document rather than on the
                # capability profile. It used to be reached only because
                # readiness carried unsupported_repeat_until for this step,
                # which meant declaring the capability would have taken the
                # gate with it. The reason the step is held is the source's
                # own sentence, so that is what is read out, and the sentence
                # comes from the construct anchored here.
                self._block_reason = "endpoint_observation_not_reported"
                step = steps[self.current_index]
                interval = self.repetition_anchored_at(step.step_id)
                stated = (
                    " ".join(str(interval["source_text"]).split())
                    if interval is not None else ""
                )
                quoted_en = f' The source states: “{stated}”' if stated else ""
                quoted_ko = f' 원문은 이렇게 적고 있습니다: “{stated}”' if stated else ""
                response = {
                    "en": (
                        f"Step {step.source_label} repeats until an observed endpoint is reached, "
                        "and only you can report that."
                        f"{quoted_en} The step has not been marked complete, and no transition was made. "
                        "Tell me what you see when you have checked it."
                    ),
                    "vi": (
                        f"Bước {step.source_label} lặp lại cho đến khi đạt điểm kết thúc quan sát, "
                        "và chỉ bạn có thể báo điều đó. Bước vẫn chưa hoàn thành."
                    ),
                    "ko": (
                        f"{step.source_label}단계는 관찰 결과가 충족될 때까지 반복하는 단계이고, "
                        "그 결과는 사용자만 확인할 수 있습니다."
                        f"{quoted_ko} 완료 처리하지 않았고 단계 이동도 하지 않았습니다. "
                        "확인하신 상태를 말씀해 주세요."
                    ),
                }.get(
                    language,
                    "관찰 결과가 보고되지 않아 이 단계를 완료 처리하지 않았습니다.",
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
                    reported_completion=intent.reported_completion,
                    requested_transition=intent.requested_transition,
                    requested_followup=intent.requested_followup,
                    target_step=intent.target_step,
                    reported_observation=intent.reported_observation,
                    observation_predicate=intent.observation_predicate,
                    observation_outcome=intent.observation_outcome,
                )
            elif blocker is not None:
                self._block_reason = blocker.value
                step = steps[self.current_index]
                if blocker is domain.ReadinessReasonCode.UNSUPPORTED_REPEAT_UNTIL:
                    response = {
                        "en": (
                            f"Step {step.source_label} is a repeat-until step, but its observed endpoint is not connected to a supported server completion signal. "
                            "You cannot satisfy that gate through the current development session. The step has not been marked complete, and no transition was made."
                        ),
                        "vi": (
                            f"Bước {step.source_label} yêu cầu lặp lại đến khi đạt điểm kết thúc quan sát, nhưng tín hiệu hoàn thành đó chưa được máy chủ hỗ trợ. Bước vẫn chưa hoàn thành."
                        ),
                        "ko": (
                            f"{step.source_label}단계는 관찰 결과가 충족될 때까지 반복해야 하지만, 그 관찰 종점이 지원되는 서버 완료 신호에 연결되어 있지 않습니다. "
                            "현재 개발용 세션에서는 사용자가 이 확인 조건을 충족할 수 없습니다. 완료 처리되지 않았습니다. 단계 이동도 하지 않았습니다."
                        ),
                    }.get(language, "관찰 기반 반복 종료 신호가 지원되지 않아 진행할 수 없습니다.")
                else:
                    response = {
                        "en": (
                            f"Step {step.source_label} contains an unresolved source ambiguity, so the current development session cannot validate completion. "
                            "The step has not been marked complete, and no transition was made."
                        ),
                        "vi": f"Bước {step.source_label} còn mơ hồ trong nguồn nên chưa thể xác nhận hoàn thành.",
                        "ko": (
                            f"{step.source_label}단계는 원문의 실행 의미가 미해결 상태여서 현재 개발용 세션에서 완료를 검증할 수 없습니다. "
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
            elif (
                self.current_index < len(steps) - 1
                and (advance_refusal := self._peek_advance_refusal()) is not None
            ):
                # A gate said no. Say which one, in one sentence, and stop.
                # Nothing is advanced and nothing is marked complete.
                self._block_reason = advance_refusal
                step = steps[self.current_index]
                response = self._advance_refusal_sentence(
                    advance_refusal, step, language
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
                    reported_completion=intent.reported_completion,
                    requested_transition=intent.requested_transition,
                    requested_followup=intent.requested_followup,
                    target_step=intent.target_step,
                    reported_observation=intent.reported_observation,
                    observation_predicate=intent.observation_predicate,
                    observation_outcome=intent.observation_outcome,
                )
            elif self.current_index < len(steps) - 1:
                self._record_release_if_reported(
                    intent, transcript, actor_principal_id, actor_role
                )
                early_exit = self._record_early_step_timer_exit()
                self._clear_step_timer()
                if self.advance_one_step() is not None:
                    raise CuratedProtocolFixtureError(
                        "Step advance was refused after its gates had passed."
                    )
                self._block_reason = None
                # Detail said at the new step is about the new step. A problem
                # left pending from the last one took it ("하얗게 변했어" at
                # step 9 was added to step 7's spill) and kept the endpoint
                # question there from taking a "네". What was recorded stays
                # recorded; only the open invitation to add to it ends here.
                # (Here and not in advance_one_step, which writes nothing but
                # the index.)
                self._pending_anomaly = None
                changed = True
                prefix = "Advanced once."
                step = steps[self.current_index]
                control_text = _control_speech(
                    CuratedProtocolAction.NEXT,
                    language,
                    step.source_label,
                    development_only=self.fixture.development_only,
                    step_index=self.current_index,
                    timer_active=False,
                    step_timer_seconds=self.timer_seconds_for_step(
                        self.current_index
                    ),
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
                    timer_payload=early_exit,
                    display_document=_display_document(
                        title=f"{step.source_label}단계",
                        primary=primary,
                        source=sources[0] if sources else None,
                    ),
                )
            else:
                self._record_release_if_reported(
                    intent, transcript, actor_principal_id, actor_role
                )
                early_exit = self._record_early_step_timer_exit()
                self._clear_step_timer()
                self._stop_experiment_clock()
                self.active = False
                self._workflow_status = "completed"
                self._block_reason = "final_step_boundary"
                self._pending_anomaly = None
                step = steps[self.current_index]
                # Decision 2 (2026-10-03): the experiment ended by finishing
                # every step. Whether the record was saved is the server's to
                # add, once the report store has answered.
                if language == "ko":
                    speech = "모든 단계를 마쳐 실험이 끝났습니다."
                elif language == "vi":
                    speech = "Đã hoàn thành tất cả các bước, thí nghiệm đã kết thúc."
                else:
                    speech = "Every step is done, so the experiment has ended."
                response = _step_reply(
                    language,
                    step.source_label,
                    step.instruction_source_text,
                    prefix=speech,
                    development_only=self.fixture.development_only,
                )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.NEXT,
                    display_text=response,
                    speech_text=speech,
                    speech_mode=CuratedProtocolSpeechMode.STOP,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=step.source_label,
                    final_step=True,
                    state_changed=True,
                    timer_payload=early_exit,
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
            if not self.active:
                preview_index = 0
                preview_step = steps[preview_index]
                if language == "ko":
                    localized = self._localized_fact(preview_step.step_id, "current_step")
                    if localized:
                        fact = localized.split(":", 1)[-1].strip().rstrip(".")
                        control_text = (
                            f"아직 실험 시작 전입니다. 1단계는 {fact} 하는 단계입니다. "
                            "지금 실험을 시작할까요?"
                        )
                    else:
                        control_text = (
                            "아직 실험 시작 전입니다. 1단계 안내를 화면에 표시했습니다. "
                            "지금 실험을 시작할까요?"
                        )
                else:
                    control_text = (
                        "The experiment has not started yet. I displayed Step 1 on the screen. "
                        "Would you like to start the experiment now?"
                    )
                response, primary, sources, pages, evidence_ids, translation_status = (
                    _step_presentation(
                        self.fixture,
                        preview_index,
                        language,
                        control_text,
                    )
                )
                plan = CuratedProtocolTurnPlan(
                    action=command,
                    display_text=response,
                    speech_text=control_text,
                    speech_mode=CuratedProtocolSpeechMode.CONTROL,
                    facts=self.fixture.facts_for_step(preview_index),
                    step_label=None,
                    final_step=False,
                    state_changed=False,
                    primary_text=primary,
                    source_texts=sources,
                    source_pages=pages,
                    evidence_ids=evidence_ids,
                    translation_status=translation_status,
                    intent_kind=intent.intent_kind,
                    target_step=preview_step.source_label,
                    display_document=_display_document(
                        title=f"{preview_step.source_label}단계",
                        primary=primary,
                        source=sources[0] if sources else None,
                    ),
                )
            else:
                step = steps[self.current_index]
                action = command
                timer_active = (self._timer_started_at is not None and self._timer_step_index == self.current_index)
                control_text = _control_speech(
                    action,
                    language,
                    step.source_label,
                    development_only=self.fixture.development_only,
                    step_index=self.current_index,
                    timer_active=timer_active,
                    step_timer_seconds=self.timer_seconds_for_step(
                        self.current_index
                    ),
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
                    display_document=_display_document(
                        title=f"{step.source_label}단계",
                        primary=primary,
                        source=sources[0] if sources else None,
                    ),
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
                # Asked to hear the step, a Korean reader hears its reviewed
                # Korean translation; with none, the source as before.
                speech = (
                    localized
                    if language == "ko" and localized is not None
                    else step.instruction_source_text
                )
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
                    if any(
                        fact.kind == "note" and _offers_source_alternative(fact.text)
                        for fact in admitted_facts
                    )
                    else ("ACTIVE_PROTOCOL",)
                ),
                display_document=_display_document(
                    title=f"{step.source_label}단계",
                    primary=primary,
                    source=sources[0] if sources else None,
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
                visual_intent=intent.visual_intent,
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
            reask_speech: str | None = None
            # A repeat request says again what was last asked: the opening
            # question, or the yes/no it was narrowed to.
            repeated = intent.intent_kind == "observation_confirmation_repeated"
            repeated_after_reask = bool(
                repeated
                and self._pending_observation_confirmation is not None
                and self._pending_observation_confirmation.reprompt_count > 0
            )
            if intent.intent_kind == "learning_and_next_preview":
                learning_display, learning_speech, learning_facts, limitations = (
                    self._step_learning_presentation(language=language)
                )
                if self.current_index < len(steps) - 1:
                    next_step = steps[self.current_index + 1]
                    next_instruction = (
                        self._localized_fact(next_step.step_id, "current_step")
                        if language == "ko"
                        else None
                    ) or next_step.instruction_source_text
                    next_facts = self.fixture.facts_for_step(self.current_index + 1)
                    preview = (
                        f"다음 단계는 {next_step.source_label}단계이며, {next_instruction}"
                        if language == "ko"
                        else f"The next step is Step {next_step.source_label}: {next_instruction}"
                    )
                else:
                    next_step = None
                    next_facts = ()
                    preview = (
                        "현재 단계가 마지막 단계라 미리 볼 다음 단계가 없습니다."
                        if language == "ko"
                        else "The current step is the final step, so there is no next step to preview."
                    )
                confirmation = (
                    "현재 단계를 실제로 완료하셨나요? 확인 전에는 상태를 변경하지 않습니다."
                    if language == "ko"
                    else "Have you actually completed the current step? The state will not change before confirmation."
                )
                response = f"{learning_display}\n\nNext-step preview\n{preview}\n\n{confirmation}"
                speech = f"{learning_speech} {preview} {confirmation}"
                combined_facts = (*learning_facts, *next_facts[:4])
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.CLARIFY_COMPLETION,
                    display_text=response,
                    speech_text=speech,
                    speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                    facts=combined_facts,
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=False,
                    primary_text=speech,
                    source_texts=tuple(fact.text for fact in combined_facts),
                    source_pages=tuple(fact.source_page for fact in combined_facts),
                    evidence_ids=tuple(fact.fact_id for fact in combined_facts),
                    translation_status=(
                        "verified_sidecar" if language == "ko" else "source_language"
                    ),
                    intent_kind=intent.intent_kind,
                    requested_transition="next",
                    requested_followup=intent.requested_followup,
                    target_step=step.source_label,
                    intent_confidence=intent.confidence,
                    answer_origin="approved_step_metadata",
                    limitations=limitations,
                    normalized_transcript=intent.normalized_transcript,
                    question_dimensions=intent.question_dimensions,
                )
            elif (
                intent.intent_kind == "observation_confirmation_required"
                or (repeated and not repeated_after_reask)
            ):
                # The endpoint the agent asks about is the document's, quoted.
                # Selecting between two hand-written questions by label meant
                # every step that was not in-gel's step 7 was asked whether
                # the gel had dehydrated -- on a source with no gel, and no
                # such endpoint, and no sentence saying so. Asking about a
                # criterion the document does not state is principle 8 at the
                # one moment the operator is being asked to judge.
                interval = self.repetition_anchored_at(step.step_id)
                stated = (
                    " ".join(str(interval["source_text"]).split())
                    if interval is not None else ""
                )
                if stated:
                    response = (
                        f"The source states this step's endpoint as: “{stated}” "
                        "Has it been reached? I will not record completion until "
                        "you report that observation."
                        if language == "en" else
                        f"원문은 이 단계의 종점을 이렇게 적고 있습니다: “{stated}” "
                        "그 관찰 결과를 말씀해 주셔야 완료를 기록할 수 있어요."
                    )
                else:
                    response = (
                        "This step repeats until an observed endpoint is reached. "
                        "I will not record completion until you report what you see."
                        if language == "en" else
                        "이 단계는 관찰 결과가 충족될 때까지 반복하는 단계입니다. "
                        "확인하신 관찰 결과를 말씀해 주셔야 완료를 기록할 수 있어요."
                    )
            elif (
                intent.intent_kind == "observation_confirmation_reasked"
                or repeated_after_reask
            ):
                # A description the phrase families could not read is asked
                # again as a yes/no. The criterion is shown, not rephrased:
                # the source sentence as quoted, and the verified sidecar
                # where one translates exactly that sentence. A Korean
                # question naming the endpoint in new words would be a
                # criterion the document does not state (principle 8), so
                # the spoken question points at the screen instead.
                stated, page, sidecar = self._stated_endpoint(self.current_index)
                reask_speech = (
                    "Has the endpoint shown on screen been reached? "
                    "Please answer yes or no."
                    if language == "en" else
                    "화면에 보이는 원문 기준대로 되었나요? 네 또는 아니요로 답해 주세요."
                ) if stated else (
                    "Has the observed endpoint been reached? Please answer yes or no."
                    if language == "en" else
                    "관찰 결과가 충족되었나요? 네 또는 아니요로 답해 주세요."
                )
                cited = f" (PDF p.{page})" if page is not None else ""
                if not stated:
                    response = reask_speech
                elif language == "en":
                    response = f"Source endpoint{cited}: “{stated}”\n\n{reask_speech}"
                else:
                    translated = f"\n검증된 한국어 번역: {sidecar}" if sidecar else ""
                    response = (
                        f"원문 기준{cited}: “{stated}”{translated}\n\n{reask_speech}"
                    )
            else:
                response = ({
                "en": "Have you completed the current step? No state has changed.",
                "vi": "Bạn đã hoàn thành bước hiện tại chưa? Trạng thái chưa thay đổi.",
                "ko": _completion_question(step.source_label),
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
            if intent.intent_kind != "learning_and_next_preview":
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.CLARIFY_COMPLETION,
                    display_text=response,
                    speech_text=reask_speech or response,
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
            if intent.intent_kind == "observation_confirmation_released":
                # The observation prompt was asked again and still not
                # answered, so it is let go. Only that fact is said:
                # restating the endpoint once more, or in other words, would
                # be a criterion the source does not state.
                response = {
                    "en": f"The Step {step.source_label} observation was not recorded.",
                    "vi": f"Quan sát của bước {step.source_label} chưa được ghi lại.",
                    "ko": f"{step.source_label}단계 관찰 결과는 기록하지 않았습니다.",
                }.get(language, f"{step.source_label}단계 관찰 결과는 기록하지 않았습니다.")
            else:
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
            step = steps[self.current_index] if self.active else None
            candidates = intent.requested_entities
            labels = {
                "solution_a": "Solution A", "solution_b": "Solution B",
                "hplc_water": "HPLC water", "ambic": "AMBIC",
                "acetonitrile": "acetonitrile", "gel_plug": "gel plug",
                "stained_protein_band": "stained protein band",
                "dtt": "DTT", "iodoacetamide": "iodoacetamide",
                "trypsin": "trypsin", "formic_acid": "formic acid",
            }
            if intent.intent_kind == "underspecified_result_request":
                response = (
                    "어떤 결과를 말씀하시나요? 현재 단계의 예상 관찰 결과, 지금까지의 실험 기록, 또는 최근 질의 답변 중 원하시는 내용을 말씀해 주세요."
                    if language == "ko" else
                    "Which result do you mean: the expected observation of the current step, the experiment record, or the recent answer?"
                )
            elif intent.coreference_status == CoreferenceStatus.AMBIGUOUS.value:
                choices = "와 ".join(labels.get(item, item) for item in candidates[:2])
                response = (
                    f"{choices} 중 어느 것을 말씀하시나요? 프로토콜 상태는 변경하지 않았습니다."
                    if language == "ko" else
                    f"Which do you mean: {' or '.join(labels.get(item, item) for item in candidates[:2])}? The protocol state did not change."
                )
            else:
                response = (
                    "어떤 물질이나 용액을 말씀하시는지 이름을 알려 주세요. 프로토콜 상태는 변경하지 않았습니다."
                    if language == "ko" else
                    "Please name the material or solution you mean. The protocol state did not change."
                )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.CLARIFY_REFERENCE,
                display_text=response, speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(), step_label=(step.source_label if step is not None else None),
                final_step=self.active and self.current_index == len(steps) - 1,
                state_changed=False, intent_kind=intent.intent_kind,
                primary_text=response,
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
        elif command is CuratedProtocolAction.STEP_RANGE:
            start_step = intent.range_start_step or 1
            end_step = intent.range_end_step or len(steps)
            start_idx = max(0, start_step - 1)
            end_idx = min(len(steps) - 1, end_step - 1)
            target_steps = steps[start_idx : end_idx + 1]
            bullet_lines = []
            speech_bits = []
            for s in target_steps:
                localized = self._localized_fact(s.step_id, "current_step")
                # With no reviewed Korean, the source line itself. ``s.text``
                # is no field of a source step, so a protocol without a
                # translation failed here with an AttributeError.
                desc = localized if localized else s.instruction_source_text.strip()
                bullet_lines.append(f"• {s.source_label}단계: {desc}")
                speech_bits.append(f"{s.source_label}단계: {desc}")
            header_text = (
                f"{start_step}단계부터 {end_step}단계까지의 절차 요약입니다."
                if language == "ko"
                else f"Here is the summary of steps {start_step} through {end_step}:"
            )
            display_text = header_text + "\n\n" + "\n".join(bullet_lines)
            speech_text = header_text + " " + " ".join(speech_bits)
            current_step = steps[self.current_index]
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.STEP_RANGE,
                display_text=display_text,
                speech_text=speech_text,
                speech_mode=CuratedProtocolSpeechMode.FULL_DETAIL,
                facts=(),
                step_label=current_step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind="step_range_summary",
                target_step=f"{start_step}-{end_step}",
                primary_text=display_text,
            )
        elif command is CuratedProtocolAction.LAB_DOMAIN_QA:
            current_step = steps[self.current_index]
            localized_step = (
                self._localized_fact(current_step.step_id, "current_step")
                or current_step.instruction_source_text
            )
            speech_text = None
            if re.search(r"(?:튜브|tube|용기|vial|container)", transcript.casefold()):
                # What this protocol's own statements say about its tubes.
                tube = self.entity_source_answer("tube", language=language)
                keep = (
                    f"현재 프로토콜 상태는 {current_step.source_label}단계를 그대로 유지합니다."
                    if language == "ko" else
                    f"The protocol state remains at Step {current_step.source_label}."
                )
                response = f"{tube.text}\n{keep}"
                speech_text = f"{tube.spoken(alone=True)} {keep}"
            else:
                if language == "ko":
                    response = (
                        f"현재 진행 중인 {current_step.source_label}단계 작업은 다음과 같습니다: {localized_step} "
                        f"현재 프로토콜은 {current_step.source_label}단계를 유지합니다."
                    )
                else:
                    response = (
                        f"The active Step {current_step.source_label} is: {localized_step}. "
                        f"The protocol state remains at Step {current_step.source_label}."
                    )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.LAB_DOMAIN_QA,
                display_text=response,
                speech_text=speech_text or response,
                speech_mode=CuratedProtocolSpeechMode.FULL_DETAIL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=current_step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
                intent_kind="lab_domain_qa",
                target_step=current_step.source_label,
                primary_text=response,
                requested_entity=intent.requested_entity or "tube",
                requested_entities=intent.requested_entities or ("tube",),
            )
        elif command is CuratedProtocolAction.OFF_TOPIC:
            step = steps[self.current_index]
            if intent.intent_kind in {
                "hypothetical_completion", "quoted_completion"
            }:
                response = (
                    "현재 단계를 실제로 완료했다고 명확히 말하면 서버가 현재 단계의 승인된 완료 확인 조건과 관찰 확인 조건을 먼저 검사합니다. 필요한 조건이 충족된 경우에만 완료를 기록하고 다음 단계로 이동합니다. 지금 질문은 상태를 변경하지 않았습니다."
                    if language == "ko" else
                    "If you explicitly report the current step complete, the server first checks its approved completion and observation gates. It records completion and advances only when those gates pass. This question did not change state."
                )
            else:
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
                    else command
                    if command in (CuratedProtocolAction.RELATED_QUESTION, CuratedProtocolAction.VISUAL_REQUEST)
                    else CuratedProtocolAction.UNSUPPORTED
                ),
                display_text=response,speech_text=response,
                speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                facts=(
                    self.related_facts(transcript)
                    if command in (CuratedProtocolAction.RELATED_QUESTION, CuratedProtocolAction.VISUAL_REQUEST)
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
                command in (CuratedProtocolAction.RELATED_QUESTION, CuratedProtocolAction.VISUAL_REQUEST)
                and (plan.facts or plan.requested_entities)
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
                        "PDF 기준\n현재 적용된 실험 PDF에서 확인된 내용이에요."
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
            self._workflow_status,
        ):
            self._revision += 1
        if reasked_question is not None:
            plan = self._ask_held_question_again(
                plan, reasked_question, turn_id=turn_id,
                generation=generation, language=language,
            )
        if (
            plan.action is CuratedProtocolAction.CLARIFY_COMPLETION
            and plan.intent_kind in {
                "next_step_confirmation_required",
                "learning_and_next_preview",
                # A semantic proposal can only ever reach this gate; the
                # researcher's explicit answer, not the model, commits the step.
                "semantic_completion_confirmation_required",
            }
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
        elif (
            plan.action is CuratedProtocolAction.REPORT_ANOMALY
            and plan.intent_kind == "observation_with_anomaly"
            and self._pending_observation_confirmation is None
        ):
            # The endpoint was reported with a problem and no question was
            # open: the question is opened now, as one asked again after a
            # problem is, so a bare yes does not answer it.
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
                accepts_yes_no=False,
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

    def proposal_basis(self, *, turn_id: int, generation: int | None) -> ProposalBasis:
        """What a model is shown for this turn, to fence its proposal with."""

        step = (
            self.fixture.steps[self.current_index]
            if 0 <= self.current_index < len(self.fixture.steps) else None
        )
        return ProposalBasis(
            turn_id=turn_id,
            generation=generation,
            workflow_revision=self._revision,
            step_id=step.step_id if step is not None else None,
        )

    def router_turn_facts(
        self,
        transcript: str,
        *,
        turn_id: int,
        language: str,
        transcript_quality: str | None = None,
        configuration_id: int | None = None,
        generation: int | None = None,
    ) -> RouterTurnFacts:
        """The server-owned facts a tool proposal for this turn is ruled on."""

        step = (
            self.fixture.steps[self.current_index]
            if 0 <= self.current_index < len(self.fixture.steps) else None
        )
        timer = self.timer_status()
        return RouterTurnFacts(
            utterance=transcript,
            language=language,
            turn_id=turn_id,
            generation=generation,
            workflow_revision=self._revision,
            step_id=step.step_id if step is not None else None,
            current_step_label=step.source_label if step is not None else None,
            workflow_active=bool(self.active),
            workflow_status=self.workflow_status,
            paused=self._pause_state == "paused",
            experiment_started=self._experiment_started_at is not None,
            experiment_running=(
                self._experiment_started_at is not None
                and self._experiment_ended_at is None
            ),
            open_question=self._open_questions(
                turn_id=turn_id,
                configuration_id=configuration_id,
                generation=generation,
            ).first_open,
            observation_step=bool(
                self.active and step is not None
                and step.step_id in self._steps_anchoring_a_repetition()
            ),
            step_timer_seconds=(
                self.timer_seconds_for_step(self.current_index) if self.active else 0
            ),
            timer_running=(
                timer.get("state") == "running"
                and timer.get("step_index") == self.current_index
            ),
            control_question=bool(
                _CONTROL_QUESTION.search(" ".join(transcript.casefold().split()))
            ),
            transcript_unreliable=transcript_quality is not None,
        )

    def router_context(
        self,
        *,
        turn_id: int,
        language: str,
        configuration_id: int | None = None,
        generation: int | None = None,
    ) -> RouterContext:
        """What the router model is shown this turn (design §3-1, D13). Reads only.

        The snapshot is the server's state now; the protocol part is the
        current step with the two on either side and their facts, every
        step's label and title, the protocol-wide facts and the protocol's
        terms. Each fact carries an id ("S7.current_step", "P.protocol_purpose")
        an answer cites, and its reviewed Korean reading where there is one.
        """

        steps = self.fixture.steps
        index = self.current_index if 0 <= self.current_index < len(steps) else 0
        titles = {
            step.step_id: section.title_source_text
            for section in self.fixture.draft.protocol.sections
            for step in section.steps
        }
        evidence: dict[str, tuple[str, int]] = {}
        localized: dict[str, str] = {}

        def entry(fact_id: str, fact: CuratedProtocolFact, step_id: str | None) -> dict[str, Any]:
            evidence[fact_id] = (fact.text, fact.source_page)
            item: dict[str, Any] = {
                "id": fact_id, "kind": fact.kind, "text": fact.text, "page": fact.source_page,
            }
            reading = self._localized_fact(step_id, fact.fact_id) if step_id else None
            if reading:
                localized[fact_id] = reading
                item["ko"] = reading
            return item

        near = []
        for at in range(max(0, index - 2), min(len(steps), index + 3)):
            step = steps[at]
            near.append({
                "label": step.source_label,
                "title": titles.get(step.step_id, ""),
                "current": bool(self.active and at == index),
                "source_timer_seconds": self.timer_seconds_for_step(at) or None,
                "facts": [
                    entry(f"S{step.source_label}.{fact.fact_id}", fact, step.step_id)
                    for fact in self.fixture.facts_for_step(at)
                ],
            })
        protocol_facts: list[dict[str, Any]] = []
        try:
            view = ProtocolKnowledgeView.from_fixture(self.fixture)
        except CuratedProtocolFixtureError:
            view = None
        if view is not None:
            for fact in (view.purpose, *view.before_start, *view.materials,
                         *view.equipment, *view.safety):
                protocol_facts.append(entry(f"P.{fact.fact_id}", fact, None))
        terms = tuple(term.text for term in self._protocol_vocabulary().terms)
        protocol = {
            "title": self.fixture.title,
            "step_count": len(steps),
            "steps_near_current": near,
            "all_steps": [
                {
                    "label": step.source_label,
                    "title": titles.get(step.step_id, ""),
                    "summary": " ".join(step.instruction_source_text.split())[:80],
                }
                for step in steps
            ],
            "protocol_facts": protocol_facts,
            "terms": list(terms),
        }
        if self.active:
            phase = "paused" if self._pause_state == "paused" else "active"
        elif self._experiment_ended():
            phase = "completed" if self._workflow_status == "completed" else "stopped"
        else:
            phase = "not_started"
        timer = self.timer_status()
        snapshot = {
            "protocol_title": self.fixture.title,
            "step_count": len(steps),
            "phase": phase,
            "current_step": steps[index].source_label if self.active else None,
            "open_question": self._open_questions(
                turn_id=turn_id, configuration_id=configuration_id, generation=generation,
            ).first_open,
            "step_timer": {
                "state": timer.get("state"),
                "step": timer.get("step_label"),
                "remaining_seconds": timer.get("remaining_seconds"),
                "duration_seconds": timer.get("duration_seconds"),
            },
            "workflow_revision": self._revision,
            "reply_language": language,
        }
        return RouterContext(
            snapshot=snapshot, protocol=protocol, evidence=evidence,
            localized=localized, terms=terms,
            server_values=ServerValues(
                title=self.fixture.title,
                revision_id=self.fixture.revision_id,
                hashes=tuple(
                    item for item in (
                        self.fixture.fixture_sha256, self.fixture.source_pdf_sha256,
                    ) if item
                ),
                step_count=len(steps),
                current_step_label=steps[index].source_label if self.active else None,
            ),
        )

    def apply_router_answer(
        self,
        answer: RouterAnswer,
        context: RouterContext,
        *,
        turn_id: int,
        language: str,
    ) -> CuratedProtocolTurnPlan:
        """A model's checked answer as this turn's plan. Nothing changes.

        Reached only for a turn front_plan() handed on, after the server's
        checks passed. A one-turn question left open lapses, as it does when
        plan() reads any reply that is not its answer.
        """

        if turn_id in self._replay:
            return self._replay[turn_id]
        self._last_semantic_decision = None
        self._last_front_rule = None
        self._pending_completion_confirmation = None
        self._pending_transcript_confirmation = None
        self._pending_note_capture = None
        self._pending_stop_confirmation = None
        self._pending_timer_confirmation = None
        self._pending_anomaly_confirmation = None
        cited = tuple(item for item in answer.evidence_ids if item in context.evidence)
        facts = tuple(
            CuratedProtocolFact(item, "router_evidence", *context.evidence[item])
            for item in cited
        )
        body = answer.display or answer.spoken
        spoken = answer.spoken
        outside = answer.source_kind == "outside_pdf"
        # The body, its source, the citation and the outside-PDF mark go to
        # the screen as separate values: no label is written into the text
        # (lane N's screen draws them).
        sections: list[dict[str, Any]] = []
        if outside:
            # Decision D4: the server's own marks, never the model's words.
            sections.append({"kind": "notice", "text": OUTSIDE_PDF_NOTICE.get(
                language, OUTSIDE_PDF_NOTICE["ko"])})
            spoken = f"{OUTSIDE_PDF_SPOKEN_LEAD.get(language, OUTSIDE_PDF_SPOKEN_LEAD['ko'])} {spoken}"
        sections.append({"kind": "section", "heading": "", "text": body})
        if facts:
            sections.append({"kind": "source", "text": "\n\n".join(fact.text for fact in facts)})
            sections.append({"kind": "citation", "text": ", ".join(
                f"{fact.fact_id} · p.{fact.source_page}" for fact in facts
            )})
        step = self.fixture.steps[self.current_index] if self.active else None
        plan = CuratedProtocolTurnPlan(
            action=CuratedProtocolAction.QUESTION,
            display_text=body,
            speech_text=spoken,
            speech_mode=(
                CuratedProtocolSpeechMode.VERIFIED_FACT
                if answer.source_kind == "pdf" else CuratedProtocolSpeechMode.CONTROL
            ),
            facts=facts,
            step_label=step.source_label if step is not None else None,
            final_step=bool(step is not None and self.current_index == len(self.fixture.steps) - 1),
            state_changed=False,
            fact_id=cited[0] if cited else None,
            primary_text=body,
            source_texts=tuple(fact.text for fact in facts),
            source_pages=tuple(fact.source_page for fact in facts),
            evidence_ids=cited,
            translation_status="llm_router_answer",
            intent_kind="llm_router_answer",
            answer_origin=(
                "supplemental_model_knowledge" if outside else "current_protocol"
            ),
            limitations=(("outside_pdf_explanation",) if outside else ()),
            display_document={
                "title": f"{step.source_label}단계" if step is not None else "",
                "sections": sections,
            },
        )
        self._replay[turn_id] = plan
        if len(self._replay) > 64:
            self._replay.pop(next(iter(self._replay)))
        return plan

    def answer_not_confirmed(
        self, *, turn_id: int, language: str,
    ) -> CuratedProtocolTurnPlan | None:
        """"PDF에서 확인할 수 없어요." for a turn whose model answer was dropped.

        Only where the rules had no answer either -- a scope reminder or an
        unsupported question that changed nothing; any other rules' reply
        stands.
        """

        plan = self._replay.get(turn_id)
        if plan is None or plan.state_changed or plan.action not in {
            CuratedProtocolAction.OFF_TOPIC, CuratedProtocolAction.UNSUPPORTED,
        }:
            return None
        text = ANSWER_NOT_CONFIRMED.get(language, ANSWER_NOT_CONFIRMED["ko"])
        replaced = replace(
            plan, display_text=text, speech_text=text, primary_text=text,
            source_texts=(), source_pages=(), evidence_ids=(), display_document=None,
            intent_kind="router_answer_not_confirmed",
        )
        self._replay[turn_id] = replaced
        return replaced

    def apply_tool_proposal(
        self,
        proposals: Sequence[ToolProposal | str],
        *,
        transcript: str,
        basis: ProposalBasis,
        turn_id: int,
        language: str,
        transcript_quality: str | None = None,
        configuration_id: int | None = None,
        generation: int | None = None,
        actor_principal_id: str | None = None,
        actor_role: str = "voice_operator",
    ) -> AppliedToolProposal:
        """Rule on a model's tool proposals for a turn, and carry out what passes.

        Reached only for a turn front_plan() handed on. ``proposals`` are the
        turn's parsed tool calls (llm_router.parse_tool_call; a string is a
        call it rejected) and ``basis`` is what the model was shown. The
        server's validation (llm_router.validate_tool_proposals) decides; a
        proposal that passes becomes a CuratedControlIntent with
        ``confidence_source="llm_tool_proposal"`` and runs through the same
        branches and post-turn gates as a rules turn, so "next" opens the
        completion or endpoint question and "stop" the end question exactly
        as the rules do. A refused proposal changes nothing and gets a reply
        the server writes; if a question was open it is asked again. Every
        reply is the server's, never the model's text.
        """

        if turn_id in self._replay:
            # The turn was already planned; a late proposal is dropped.
            return AppliedToolProposal(
                ProposalVerdict("refuse", "stale_proposal"), self._replay[turn_id]
            )
        facts = self.router_turn_facts(
            transcript,
            turn_id=turn_id,
            language=language,
            transcript_quality=transcript_quality,
            configuration_id=configuration_id,
            generation=generation,
        )
        verdict = validate_tool_proposals(proposals, facts, basis)
        self._last_semantic_decision = None
        self._last_front_rule = None
        if verdict.effect == "refuse" and facts.open_question is not None:
            plan = self._ask_open_question_again(
                facts.open_question, turn_id=turn_id, generation=generation,
                language=language,
            )
            return AppliedToolProposal(verdict, plan)
        # No question is open for this turn: whatever is left over has gone
        # stale and lapses, as it does when plan() reads a turn.
        self._pending_completion_confirmation = None
        self._pending_observation_confirmation = None
        self._pending_transcript_confirmation = None
        self._pending_note_capture = None
        self._pending_stop_confirmation = None
        self._pending_timer_confirmation = None
        self._pending_anomaly_confirmation = None
        execute = dict(
            transcript=transcript,
            command_key=_utterance_key(transcript),
            turn_id=turn_id,
            language=language,
            configuration_id=configuration_id,
            generation=generation,
            actor_principal_id=actor_principal_id,
            actor_role=actor_role,
            open_question=None,
        )
        common = dict(
            language=language,
            normalized_transcript=_utterance_key(transcript),
            confidence_source="llm_tool_proposal",
        )
        if verdict.reason_code == "completion_not_reached":
            # "덜 됐는데 그냥 넘어가자" (decision D7): the step stays, and its
            # source completion criterion is said.
            plan = self._execute_turn_intent(
                CuratedControlIntent(
                    intent_kind="completion_not_reached",
                    action=CuratedProtocolAction.COMPLETION_CRITERIA,
                    target_step="authoritative_current_step",
                    **common,
                ),
                **execute,
            )
            label = plan.step_label or ""
            stay = (
                f"아직 끝나지 않았다고 하셔서 {label}단계에 머뭅니다. "
                if language == "ko" else
                f"You said it is not done, so Step {label} stays current. "
            )
            plan = replace(
                plan,
                display_text=stay + plan.display_text,
                speech_text=stay + plan.speech_text,
            )
            self._replay[turn_id] = plan
            return AppliedToolProposal(verdict, plan)
        if verdict.effect == "refuse":
            plan = self._refused_proposal_plan(verdict, facts, language=language)
            self._replay[turn_id] = plan
            return AppliedToolProposal(verdict, plan)
        if verdict.question == "timer":
            plan = self._ask_timer_duration(
                facts, turn_id=turn_id, generation=generation,
                configuration_id=configuration_id, language=language,
            )
            return AppliedToolProposal(verdict, plan)
        if verdict.question == "anomaly":
            plan = self._ask_anomaly_record(
                transcript, turn_id=turn_id, generation=generation,
                configuration_id=configuration_id, language=language,
            )
            return AppliedToolProposal(verdict, plan)
        intent = self._intent_for_proposal(verdict, common)
        return AppliedToolProposal(verdict, self._execute_turn_intent(intent, **execute))

    def _intent_for_proposal(
        self, verdict: ProposalVerdict, common: dict[str, Any]
    ) -> CuratedControlIntent:
        """The intent an accepted proposal is carried out as -- the rules' own."""

        proposal = verdict.proposal
        assert proposal is not None
        if proposal.tool == RECORD_LOG:
            if proposal.log_type == "observation":
                # Recorded as a note: a model's reading never reports an
                # endpoint, so it can never release a repeat-until step.
                return CuratedControlIntent(
                    intent_kind="record_observation",
                    action=CuratedProtocolAction.RECORD_OBSERVATION,
                    target_step="authoritative_current_step",
                    reported_observation=True,
                    observation_predicate="note",
                    observation_outcome=proposal.value,
                    **common,
                )
            return CuratedControlIntent(
                intent_kind="record_anomaly",
                action=CuratedProtocolAction.REPORT_ANOMALY,
                question_kind="anomaly",
                reported_anomaly=True,
                anomaly_category=_anomaly_category(common["normalized_transcript"]),
                **common,
            )
        if proposal.action == "next":
            return CuratedControlIntent(
                intent_kind=(
                    "observation_confirmation_required"
                    if verdict.question == "observation"
                    else "next_step_confirmation_required"
                ),
                action=CuratedProtocolAction.CLARIFY_COMPLETION,
                requested_transition="next",
                requested_followup="confirm_current_step_completion",
                target_step="authoritative_current_step",
                requires_confirmation=True,
                allows_state_mutation=False,
                **common,
            )
        if proposal.action == "stop":
            # The STOP branch asks "실험을 종료할까요?" for anything but a yes
            # to that question (decision D5).
            return CuratedControlIntent(
                intent_kind="workflow_command",
                action=CuratedProtocolAction.STOP,
                allows_state_mutation=True,
                **common,
            )
        if proposal.action == "pause":
            return CuratedControlIntent(
                intent_kind="pause_workflow", action=CuratedProtocolAction.PAUSE,
                **common,
            )
        if proposal.action == "resume":
            return CuratedControlIntent(
                intent_kind="resume_workflow", action=CuratedProtocolAction.RESUME,
                allows_state_mutation=True, **common,
            )
        if proposal.action == "start":
            return CuratedControlIntent(
                intent_kind="workflow_command",
                action=CuratedProtocolAction.START,
                requested_transition="start",
                requested_followup="describe_new_current_step",
                allows_state_mutation=True,
                **common,
            )
        return CuratedControlIntent(
            intent_kind="start_step_timer", action=CuratedProtocolAction.START_TIMER,
            **common,
        )

    def _ask_timer_duration(
        self,
        facts: RouterTurnFacts,
        *,
        turn_id: int,
        generation: int | None,
        configuration_id: int | None,
        language: str,
    ) -> CuratedProtocolTurnPlan:
        """"원문은 15분입니다. 15분으로 시작할까요?" -- nothing starts (D6)."""

        step = self.fixture.steps[self.current_index]
        spoken = _duration_words(facts.step_timer_seconds, language)
        question = (
            f"원문은 {spoken}입니다. {_with_ro(spoken)} 시작할까요?"
            if language == "ko" else
            f"The source timer for this step is {spoken}. Start {spoken}?"
        )
        self._pending_timer_confirmation = {
            "configuration_id": configuration_id,
            "requested_turn_id": turn_id,
            "requested_generation": generation,
            "workflow_revision": self._revision,
            "step_index": self.current_index,
            "step_id": step.step_id,
            "duration_seconds": facts.step_timer_seconds,
            "question": question,
        }
        plan = CuratedProtocolTurnPlan(
            action=CuratedProtocolAction.CLARIFY_PARAMETER,
            display_text=question,
            speech_text=question,
            speech_mode=CuratedProtocolSpeechMode.CONTROL,
            facts=self.fixture.facts_for_step(self.current_index),
            step_label=step.source_label,
            final_step=self.current_index == len(self.fixture.steps) - 1,
            state_changed=False,
            primary_text=question,
            intent_kind="timer_duration_confirmation_required",
        )
        self._replay[turn_id] = plan
        return plan

    def _ask_anomaly_record(
        self,
        transcript: str,
        *,
        turn_id: int,
        generation: int | None,
        configuration_id: int | None,
        language: str,
    ) -> CuratedProtocolTurnPlan:
        """"이상 사항으로 기록할까요?" -- nothing is recorded yet (decision 1)."""

        step = self.fixture.steps[self.current_index]
        question = (
            "이상 사항으로 기록할까요?" if language == "ko" else
            "Should I record this as an issue?"
        )
        self._pending_anomaly_confirmation = {
            "configuration_id": configuration_id,
            "requested_turn_id": turn_id,
            "requested_generation": generation,
            "workflow_revision": self._revision,
            "step_index": self.current_index,
            "step_id": step.step_id,
            "utterance": transcript.strip()[:800],
            "question": question,
        }
        plan = CuratedProtocolTurnPlan(
            action=CuratedProtocolAction.CLARIFY_PARAMETER,
            display_text=question,
            speech_text=question,
            speech_mode=CuratedProtocolSpeechMode.CONTROL,
            facts=(),
            step_label=step.source_label,
            final_step=self.current_index == len(self.fixture.steps) - 1,
            state_changed=False,
            primary_text=question,
            intent_kind="anomaly_record_confirmation_required",
        )
        self._replay[turn_id] = plan
        return plan

    def _ask_open_question_again(
        self,
        kind: str,
        *,
        turn_id: int,
        generation: int | None,
        language: str,
    ) -> CuratedProtocolTurnPlan:
        """A refused proposal leaves the open question open, and asks it again."""

        step = self.fixture.steps[self.current_index] if self.active else None
        label = step.source_label if step is not None else ""
        action = CuratedProtocolAction.CLARIFY_COMPLETION
        if kind == "completion":
            pending = self._pending_completion_confirmation
            self._pending_completion_confirmation = replace(
                pending, requested_turn_id=turn_id, requested_generation=generation,
            )
            question = (
                _completion_question(label) if language == "ko" else
                "Have you completed the current step? No state has changed."
            )
        elif kind == "observation":
            pending = self._pending_observation_confirmation
            asked = self._replay.get(pending.requested_turn_id)
            self._pending_observation_confirmation = replace(
                pending, requested_turn_id=turn_id, requested_generation=generation,
            )
            question = (asked.speech_text if asked is not None else "") or (
                "확인하신 관찰 결과를 말씀해 주세요." if language == "ko" else
                "Please tell me what you observed."
            )
        elif kind == "transcript":
            pending = self._pending_transcript_confirmation
            self._pending_transcript_confirmation = replace(
                pending, requested_turn_id=turn_id, requested_generation=generation,
            )
            question = (
                f"‘{pending.proposed_transcript}’라고 말씀하신 건가요?"
                if language == "ko" else
                f"Did you say '{pending.proposed_transcript}'?"
            )
        elif kind == "note":
            self._pending_note_capture = {
                **(self._pending_note_capture or {}),
                "requested_turn_id": turn_id, "requested_generation": generation,
            }
            action = CuratedProtocolAction.RECORD_OBSERVATION
            question = (
                "어떤 관찰 내용을 기록할까요? 프로토콜 상태는 변경하지 않았습니다."
                if language == "ko" else
                "What should I record as the observation? No protocol state has changed."
            )
        elif kind == "stop":
            self._pending_stop_confirmation = {
                **(self._pending_stop_confirmation or {}),
                "requested_turn_id": turn_id, "requested_generation": generation,
            }
            action = CuratedProtocolAction.STOP
            question = END_CONFIRMATION_QUESTION.get(language, END_CONFIRMATION_QUESTION["ko"])
        elif kind == "anomaly":
            self._pending_anomaly_confirmation = {
                **(self._pending_anomaly_confirmation or {}),
                "requested_turn_id": turn_id, "requested_generation": generation,
            }
            action = CuratedProtocolAction.CLARIFY_PARAMETER
            question = str((self._pending_anomaly_confirmation or {}).get("question") or "")
        else:
            self._pending_timer_confirmation = {
                **(self._pending_timer_confirmation or {}),
                "requested_turn_id": turn_id, "requested_generation": generation,
            }
            action = CuratedProtocolAction.CLARIFY_PARAMETER
            question = str((self._pending_timer_confirmation or {}).get("question") or "")
        lead = (
            "먼저 질문에 답해 주세요. " if language == "ko" else
            "Please answer the question first. "
        )
        plan = CuratedProtocolTurnPlan(
            action=action,
            display_text=lead + question,
            speech_text=lead + question,
            speech_mode=CuratedProtocolSpeechMode.CONTROL,
            facts=(),
            step_label=label or None,
            final_step=self.active and self.current_index == len(self.fixture.steps) - 1,
            state_changed=False,
            primary_text=lead + question,
            intent_kind="open_question_asked_again",
        )
        self._replay[turn_id] = plan
        return plan

    def _refused_proposal_plan(
        self,
        verdict: ProposalVerdict,
        facts: RouterTurnFacts,
        *,
        language: str,
    ) -> CuratedProtocolTurnPlan:
        """The server's reply to a refused proposal. Nothing changes."""

        code = verdict.reason_code
        label = facts.current_step_label or ""
        ko = language == "ko"
        action = CuratedProtocolAction.UNSUPPORTED
        if code == "workflow_not_active":
            action = CuratedProtocolAction.INACTIVE
            if self._workflow_status in {"preview", "ready"}:
                response = (
                    "아직 실험을 시작하지 않았습니다. 프로토콜을 시작해 주세요. 시작하면 1단계부터 진행합니다."
                    if ko else
                    "The experiment has not started yet. Please say start protocol to begin."
                )
            else:
                response = (
                    "프로토콜 세션이 중지되었습니다. 상태는 바꾸지 않았습니다."
                    if ko else
                    "The protocol session is stopped. Nothing was changed."
                )
        elif code in {"workflow_paused", "already_paused"}:
            action = CuratedProtocolAction.PAUSE
            response = (
                "지금 일시정지 중이에요. '재개'라고 말씀해 주세요."
                if ko else
                "Guidance is paused right now. Say 'resume' to continue."
            )
        elif code == "not_paused":
            action = CuratedProtocolAction.RESUME
            response = (
                f"지금은 일시정지 상태가 아닙니다. 현재 {label}단계입니다."
                if ko else
                f"Guidance is not paused. The current step is Step {label}."
            )
        elif code == "already_started":
            response = (
                f"이미 {label}단계를 진행 중입니다. 처음부터 다시 시작하지 않았습니다."
                if ko else
                f"Step {label} is already in progress. Nothing was restarted."
            )
        elif code == "session_ended":
            action = CuratedProtocolAction.INACTIVE
            response = EXPERIMENT_ENDED_START_REPLY.get(
                language, EXPERIMENT_ENDED_START_REPLY["ko"]
            )
        elif code == "end_word_missing":
            response = END_REQUEST_HINT.get(language, END_REQUEST_HINT["ko"]) + (
                " 지금은 그대로 둡니다." if ko else " Nothing has changed."
            )
        elif code == "no_step_timer":
            action = CuratedProtocolAction.TIMER_STATUS
            response = (
                f"현재 {label}단계에는 프로토콜에 정의된 별도 타이머가 없습니다. 상태는 바꾸지 않았습니다."
                if ko else
                f"Step {label} has no protocol-defined timer. Nothing was changed."
            )
        elif code == "target_not_current_step":
            response = (
                f"현재 {label}단계가 아닌 단계로는 옮기지 않습니다. 상태는 바꾸지 않았습니다."
                if ko else
                f"I only act on the current step, Step {label}. Nothing was changed."
            )
        elif code == "value_not_in_utterance":
            response = (
                "말씀하신 내용과 기록할 값이 달라 기록하지 않았습니다. 기록할 내용을 다시 말씀해 주세요."
                if ko else
                "What would be recorded is not what you said, so nothing was recorded. Please say it again."
            )
        elif code == "transcript_unreliable":
            action = CuratedProtocolAction.TRANSCRIPT_UNRELIABLE
            response = (
                "방금 음성을 정확히 인식하지 못했습니다. 짧게 다시 말씀해 주세요. 프로토콜 상태는 변경하지 않았습니다."
                if ko else
                "I could not reliably recognize that utterance. Please repeat it clearly. No procedure state changed."
            )
        else:
            response = (
                "말씀만으로는 바꿀 내용을 확인하지 못해 상태를 바꾸지 않았습니다."
                if ko else
                "I could not confirm a change from what was said, so nothing was changed."
            )
        return CuratedProtocolTurnPlan(
            action=action,
            display_text=response,
            speech_text=response,
            speech_mode=CuratedProtocolSpeechMode.BLOCKED,
            facts=(),
            step_label=(label or None) if self.active else None,
            final_step=self.active and self.current_index == len(self.fixture.steps) - 1,
            state_changed=False,
            primary_text=response,
            intent_kind="tool_proposal_refused",
            limitations=(f"refused:{code}",),
        )

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
