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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
    UNSUPPORTED = "unsupported"
    STOP = "stop"
    INACTIVE = "inactive"


class CuratedProtocolSpeechMode(str, Enum):
    CONTROL = "control"
    FULL_DETAIL = "full_detail"
    VERIFIED_FACT = "verified_fact"
    BLOCKED = "blocked"
    STOP = "stop"


class ProtocolVisualKind(str, Enum):
    FULL_SOURCE_PAGE_PREVIEW = "full_source_page_preview"
    SOURCE_PAGE_CROP = "source_page_crop"
    EXTRACTED_SOURCE_IMAGE = "extracted_source_image"


@dataclass(frozen=True)
class CuratedProtocolFact:
    fact_id: str
    kind: str
    text: str


@dataclass(frozen=True)
class ProtocolVisualAsset:
    """A source-preserving visual reference; never model-generated content."""

    asset_id: str
    protocol_id: str
    revision_id: str
    kind: str
    source_document_id: str
    source_page: int
    mime_type: str
    sha256: str
    alt_text: str
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
            "normalized_bounding_box": self.normalized_bounding_box,
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
            )
        ]
        for item_index, action in enumerate(step.sub_actions, 1):
            facts.append(CuratedProtocolFact(
                fact_id=f"sub_action_{item_index}",
                kind="sub_action",
                text=action.instruction_source_text,
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
                ))

        claim_text = "\n".join(fact.text for fact in facts).casefold()
        for item_index, material in enumerate(self.draft.protocol.materials, 1):
            if _resource_is_referenced(material.name_source_text, claim_text):
                facts.append(CuratedProtocolFact(
                    fact_id=f"material_{item_index}",
                    kind="material",
                    text=material.name_source_text,
                ))
        for item_index, equipment in enumerate(self.draft.protocol.equipment, 1):
            if _resource_is_referenced(equipment.name_source_text, claim_text):
                facts.append(CuratedProtocolFact(
                    fact_id=f"equipment_{item_index}",
                    kind="equipment",
                    text=equipment.name_source_text,
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
                ))
        return tuple(facts)

    def visual_for_step(self, index: int) -> ProtocolVisualAsset | None:
        """Return a safe full-page fallback for the step's exact evidence page."""

        if self.source_pdf_path is None:
            return None
        step = self.steps[index]
        page = step.evidence.source_page_number
        if not isinstance(page, int) or page <= 0:
            return None
        checksum = self.source_pdf_sha256
        if not isinstance(checksum, str) or len(checksum) != 64:
            return None
        return ProtocolVisualAsset(
            asset_id=f"source-page-{page}",
            protocol_id=self.protocol_id,
            revision_id=self.revision_id,
            kind=ProtocolVisualKind.FULL_SOURCE_PAGE_PREVIEW.value,
            source_document_id=checksum,
            source_page=page,
            mime_type="application/pdf",
            sha256=checksum,
            alt_text=f"Source PDF page {page} preview",
        )


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
    return CuratedProtocolFixture(
        draft=draft,
        status=DEVELOPMENT_FIXTURE_STATUS,
        ordered_step_labels=labels,
        fixture_sha256=fixture_sha256,
        source_pdf_path=source_pdf_file.resolve(),
        source_pdf_sha256=extraction.sha256,
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
    if not development_only:
        return {
            "en": "The approved revision has no authorized answer for this question at the current step.",
            "vi": "Bản sửa đổi đã duyệt không có câu trả lời được phép cho câu hỏi này ở bước hiện tại.",
            "ko": "승인된 리비전의 현재 단계에는 이 질문에 대해 허용된 답변이 없습니다.",
        }.get(language, "현재 단계에는 허용된 답변이 없습니다.")
    return {
        "en": (
            "The verified development fixture has no authorized answer for "
            "this question at the current step."
        ),
        "vi": (
            "Dữ liệu phát triển đã kiểm tra không có câu trả lời được phép "
            "cho câu hỏi này ở bước hiện tại."
        ),
        "ko": (
            "검증된 개발용 픽스처의 현재 단계에는 이 질문에 대해 허용된 "
            "답변이 없습니다."
        ),
    }.get(
        language,
        "검증된 개발용 픽스처의 현재 단계에는 허용된 답변이 없습니다.",
    )


def _control_speech(
    action: CuratedProtocolAction,
    language: str,
    label: str,
    *,
    resumed: bool = False,
    development_only: bool = True,
) -> str:
    english_subject = "Development fixture" if development_only else "Protocol"
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
        subject = "dữ liệu phát triển" if development_only else "quy trình"
        if action is CuratedProtocolAction.START:
            verb = "hiển thị lại" if resumed else "hiển thị"
            return f"Hướng dẫn bước {label} của {subject} đã được {verb} trên màn hình."
        if action is CuratedProtocolAction.CURRENT:
            return f"Hiện tại là bước {label}. Hướng dẫn được hiển thị trên màn hình."
        if action is CuratedProtocolAction.REPEAT:
            return f"Hướng dẫn bước {label} hiện tại đã được hiển thị lại trên màn hình."
        return f"Đã chuyển sang bước {label}. Hướng dẫn được hiển thị trên màn hình."
    korean_subject = "개발용 픽스처 " if development_only else ""
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
        noun = "Development fixture step" if development_only else "Protocol step"
        return f"{prefix} {noun} {label}: {text}"
    if language == "vi":
        noun = "dữ liệu phát triển" if development_only else "quy trình"
        return f"{prefix} Bước {label} của {noun}: {text}"
    noun = "개발용 픽스처 " if development_only else ""
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
        command = _WORKFLOW_COMMANDS.get(command_key)
        steps = self.fixture.steps
        opening_projection = (self.active, self.current_index, self._block_reason)
        changed = False

        if command is CuratedProtocolAction.STOP:
            changed = self.active
            self.active = False
            self._block_reason = None
            action = CuratedProtocolAction.STOP
            response = {
                "en": "The development-fixture protocol session has ended.",
                "vi": "Phiên quy trình dùng dữ liệu phát triển đã kết thúc.",
                "ko": "개발용 픽스처 프로토콜 세션을 종료했습니다.",
            }.get(language, "개발용 픽스처 프로토콜 세션을 종료했습니다.")
            if not self.fixture.development_only:
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
            response = _step_reply(
                language,
                step.source_label,
                step.instruction_source_text,
                prefix="Starting or resuming." if language == "en" else "시작하거나 재개합니다.",
                development_only=self.fixture.development_only,
            )
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.START,
                display_text=response,
                speech_text=_control_speech(
                    CuratedProtocolAction.START,
                    language,
                    step.source_label,
                    resumed=resumed,
                    development_only=self.fixture.development_only,
                ),
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=changed,
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
                    "en": (
                        "This development fixture cannot advance from the current "
                        "step because its execution control is unresolved or "
                        "unsupported. The step has not been marked complete."
                    ),
                    "vi": (
                        "Dữ liệu phát triển không thể chuyển khỏi bước hiện tại vì "
                        "điều khiển thực hiện chưa được giải quyết hoặc chưa được hỗ "
                        "trợ. Bước này chưa được đánh dấu hoàn thành."
                    ),
                    "ko": (
                        "현재 단계의 실행 제어가 해결되지 않았거나 지원되지 않아 "
                        "개발용 픽스처를 다음 단계로 진행할 수 없습니다. 이 단계는 "
                        "완료 처리되지 않았습니다."
                    ),
                }.get(
                    language,
                    "현재 단계는 실행 제어가 해결될 때까지 진행할 수 없습니다.",
                )
                if not self.fixture.development_only:
                    response = {
                        "en": "The current step cannot advance because its execution control is unresolved or unsupported. It was not marked complete.",
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
                )
            elif self.current_index < len(steps) - 1:
                self.current_index += 1
                self._block_reason = None
                changed = True
                prefix = "Advanced once."
                step = steps[self.current_index]
                response = _step_reply(
                    language,
                    step.source_label,
                    step.instruction_source_text,
                    prefix=(
                        prefix if language == "en" else "한 단계 이동했습니다."
                    ),
                    development_only=self.fixture.development_only,
                )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.NEXT,
                    display_text=response,
                    speech_text=_control_speech(
                        CuratedProtocolAction.NEXT,
                        language,
                        step.source_label,
                        development_only=self.fixture.development_only,
                    ),
                    speech_mode=CuratedProtocolSpeechMode.CONTROL,
                    facts=self.fixture.facts_for_step(self.current_index),
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=changed,
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
                )
        elif command in (
            CuratedProtocolAction.CURRENT,
            CuratedProtocolAction.REPEAT,
        ):
            step = steps[self.current_index]
            action = command
            response = _step_reply(
                language,
                step.source_label,
                step.instruction_source_text,
                prefix="Repeating." if action is CuratedProtocolAction.REPEAT and language == "en" else (
                    "Current step."
                    if language == "en"
                    else (
                        "다시 안내합니다."
                        if action is CuratedProtocolAction.REPEAT
                        else "현재 단계입니다."
                    )
                ),
                development_only=self.fixture.development_only,
            )
            plan = CuratedProtocolTurnPlan(
                action=action,
                display_text=response,
                speech_text=_control_speech(
                    action,
                    language,
                    step.source_label,
                    development_only=self.fixture.development_only,
                ),
                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
            )
        elif command_key in _FULL_DETAIL_COMMANDS:
            step = steps[self.current_index]
            plan = CuratedProtocolTurnPlan(
                action=CuratedProtocolAction.FULL_DETAIL,
                display_text=step.instruction_source_text,
                speech_text=step.instruction_source_text,
                speech_mode=CuratedProtocolSpeechMode.FULL_DETAIL,
                facts=self.fixture.facts_for_step(self.current_index),
                step_label=step.source_label,
                final_step=self.current_index == len(steps) - 1,
                state_changed=False,
            )
        else:
            step = steps[self.current_index]
            facts = self.fixture.facts_for_step(self.current_index)
            selected_fact = _select_verified_fact(transcript, facts)
            if selected_fact is not None:
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.QUESTION,
                    display_text=selected_fact.text,
                    speech_text=selected_fact.text,
                    speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                    facts=(selected_fact,),
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=False,
                    fact_id=selected_fact.fact_id,
                )
            else:
                response = _unsupported_fact_reply(
                    language,
                    development_only=self.fixture.development_only,
                )
                plan = CuratedProtocolTurnPlan(
                    action=CuratedProtocolAction.UNSUPPORTED,
                    display_text=response,
                    speech_text=response,
                    speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                    facts=(),
                    step_label=step.source_label,
                    final_step=self.current_index == len(steps) - 1,
                    state_changed=False,
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
