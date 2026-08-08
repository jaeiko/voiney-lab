"""Explicit, source-preserving multi-Protocol catalog service.

The service is deliberately constructed only by an authorized API/session
boundary.  Importing or listing it does not contact an analysis Provider.
All writes use the separate immutable Protocol store, never ProcedureStore.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.curated_protocol import CuratedProtocolFixture
from voice_workflow_agent.experiment_protocol_analysis import (
    MAX_SINGLE_PASS_INPUT_BYTES,
    ProtocolAnalysisDraft,
    ProtocolAnalysisInputTooLargeError,
    ProtocolAnalysisModel,
    analyze_protocol_extraction,
    prepare_protocol_analysis_request,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    ProtocolPdfExtraction,
    extract_protocol_pdf,
)
from voice_workflow_agent.experiment_protocol_store import (
    AnalysisRevisionRecord,
    ProtocolRevisionRecord,
    ProtocolStore,
)


_SAFE_FILENAME = re.compile(r"^[^/\\\x00]{1,255}\.pdf$", re.IGNORECASE)
_PROTOCOL_ID = re.compile(r"^protocol-[0-9a-f]{32}$")
_REVISION_ID = re.compile(r"^pdf-(\d+)(?:-analysis-(\d+))?$")
_APPROVAL_EVENT = "protocol_revision_approved"
_ANALYSIS_FAILED_EVENT = "protocol_analysis_failed"


class ProtocolCatalogError(RuntimeError):
    code = "protocol_catalog_error"


class ProtocolRegistrationError(ProtocolCatalogError):
    code = "protocol_registration_invalid"


class ProtocolCatalogNotFoundError(ProtocolCatalogError):
    code = "protocol_catalog_not_found"


class ProtocolCatalogUnavailableError(ProtocolCatalogError):
    code = "protocol_catalog_unavailable"


class ProtocolApprovalError(ProtocolCatalogError):
    code = "protocol_approval_denied"


class ProtocolOcrRequiredError(ProtocolCatalogError):
    code = "ocr_required"


class ProtocolChunkedAnalysisRequiredError(ProtocolCatalogError):
    code = "chunked_analysis_required"


class ApprovalPolicy(Protocol):
    def permits(self, presented_secret: str | None) -> bool: ...


@dataclass(frozen=True)
class SharedSecretApprovalPolicy:
    configured_secret: str | None

    def permits(self, presented_secret: str | None) -> bool:
        if not self.configured_secret or not presented_secret:
            return False
        return hmac.compare_digest(
            hashlib.sha256(presented_secret.encode("utf-8")).digest(),
            hashlib.sha256(self.configured_secret.encode("utf-8")).digest(),
        )


@dataclass(frozen=True)
class ProtocolCatalogEntry:
    protocol_id: str
    title: str
    source_filename: str
    source_sha256: str
    revision_id: str
    readiness_status: str
    approval_status: str
    analysis_status: str
    step_count: int
    created_at: str
    available_for_execution: bool

    def public_dict(self) -> dict[str, object]:
        return {
            "protocol_id": self.protocol_id,
            "title": self.title,
            "source_filename": self.source_filename,
            "source_sha256": self.source_sha256,
            "revision_id": self.revision_id,
            "readiness_status": self.readiness_status,
            "approval_status": self.approval_status,
            "analysis_status": self.analysis_status,
            "step_count": self.step_count,
            "created_at": self.created_at,
            "available_for_execution": self.available_for_execution,
        }


@dataclass(frozen=True)
class ProtocolRegistration:
    entry: ProtocolCatalogEntry
    deduplicated: bool


@dataclass(frozen=True)
class ProtocolAssetResolution:
    path: Path
    sha256: str
    source_page: int
    mime_type: str = PDF_MEDIA_TYPE


def _protocol_id(checksum: str) -> str:
    return f"protocol-{checksum[:32]}"


def _revision_id(
    protocol_revision_number: int,
    analysis_revision_number: int | None = None,
) -> str:
    value = f"pdf-{protocol_revision_number}"
    if analysis_revision_number is not None:
        value += f"-analysis-{analysis_revision_number}"
    return value


def _parse_revision_id(value: str) -> tuple[int, int | None]:
    match = _REVISION_ID.fullmatch(value)
    if match is None:
        raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
    return int(match.group(1)), (
        int(match.group(2)) if match.group(2) is not None else None
    )


def _display_filename(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_FILENAME.fullmatch(value):
        raise ProtocolRegistrationError(
            "Protocol filename must be one plain PDF filename."
        )
    return value


def _analysis_state(extraction: ProtocolPdfExtraction) -> str:
    extracted_chars = sum(len(page.text.strip()) for page in extraction.pages)
    if extraction.non_empty_page_count == 0 or extracted_chars < 32:
        return "ocr_required"
    try:
        prepare_protocol_analysis_request(
            extraction, max_input_bytes=MAX_SINGLE_PASS_INPUT_BYTES
        )
    except ProtocolAnalysisInputTooLargeError:
        return "chunked_analysis_required"
    return "structured_analysis_ready"


class ProtocolCatalog:
    """Catalog facade over immutable source, analysis, and approval records."""

    def __init__(self, store: ProtocolStore) -> None:
        self.store = store

    def _latest_protocol_revision(self, protocol_id: str) -> ProtocolRevisionRecord:
        if not _PROTOCOL_ID.fullmatch(protocol_id):
            raise ProtocolCatalogNotFoundError("Protocol identifier is unknown.")
        revisions = self.store.list_protocol_revisions(protocol_id)
        if not revisions:
            raise ProtocolCatalogNotFoundError("Protocol identifier is unknown.")
        return revisions[-1]

    def _latest_analysis(
        self, revision: ProtocolRevisionRecord
    ) -> AnalysisRevisionRecord | None:
        analyses = self.store.list_analysis_revisions(
            revision.experiment_id, revision.revision_number
        )
        return analyses[-1] if analyses else None

    def _is_approved(
        self,
        revision: ProtocolRevisionRecord,
        analysis: AnalysisRevisionRecord | None,
    ) -> bool:
        if analysis is None:
            return False
        return any(
            event.event_type == _APPROVAL_EVENT
            and event.protocol_revision_number == revision.revision_number
            and event.analysis_revision_number
            == analysis.analysis_revision_number
            and isinstance(event.payload, dict)
            and event.payload.get("decision") == "approved"
            for event in self.store.list_events(revision.experiment_id)
        )

    def _entry_for_revision(
        self, revision: ProtocolRevisionRecord
    ) -> ProtocolCatalogEntry:
        analysis = self._latest_analysis(revision)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source_path = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source_path)
        analysis_status = "validated" if analysis else _analysis_state(extraction)
        if analysis is None:
            failed = next(
                (
                    event
                    for event in reversed(
                        self.store.list_events(revision.experiment_id)
                    )
                    if event.protocol_revision_number == revision.revision_number
                    and event.event_type == _ANALYSIS_FAILED_EVENT
                ),
                None,
            )
            if failed is not None:
                analysis_status = "analysis_failed"
        approved = self._is_approved(revision, analysis)
        readiness = (
            analysis.readiness_status if analysis else "analysis_required"
        )
        available = bool(
            approved
            and analysis is not None
            and readiness == domain.ReadinessStatus.GUIDANCE_READY.value
        )
        title = (
            analysis.protocol.metadata.title
            if analysis is not None
            else extraction.metadata.title or Path(revision.original_filename).stem
        )
        return ProtocolCatalogEntry(
            protocol_id=revision.experiment_id,
            title=title,
            source_filename=revision.original_filename,
            source_sha256=revision.pdf_checksum,
            revision_id=_revision_id(
                revision.revision_number,
                analysis.analysis_revision_number if analysis else None,
            ),
            readiness_status=readiness,
            approval_status="approved" if approved else "unapproved",
            analysis_status=analysis_status,
            step_count=(
                sum(len(section.steps) for section in analysis.protocol.sections)
                if analysis
                else 0
            ),
            created_at=revision.created_at,
            available_for_execution=available,
        )

    def list_entries(self) -> tuple[ProtocolCatalogEntry, ...]:
        entries = []
        for experiment in self.store.list_experiments():
            entries.append(
                self._entry_for_revision(
                    self._latest_protocol_revision(experiment.experiment_id)
                )
            )
        return tuple(entries)

    def get_entry(self, protocol_id: str) -> ProtocolCatalogEntry:
        return self._entry_for_revision(
            self._latest_protocol_revision(protocol_id)
        )

    def register(
        self,
        source_pdf: str | Path,
        *,
        source_filename: str,
        media_type: str,
    ) -> ProtocolRegistration:
        filename = _display_filename(source_filename)
        if media_type.casefold().split(";", 1)[0].strip() != PDF_MEDIA_TYPE:
            raise ProtocolRegistrationError(
                "Protocol registration requires application/pdf."
            )
        source = Path(source_pdf)
        extraction = extract_protocol_pdf(source)
        existing = self.store.find_protocol_revision_by_checksum(
            extraction.sha256
        )
        if existing is not None:
            return ProtocolRegistration(
                self._entry_for_revision(existing), deduplicated=True
            )
        protocol_id = _protocol_id(extraction.sha256)
        creation = self.store.create_experiment(
            protocol_id, source, original_filename=filename
        )
        status = _analysis_state(extraction)
        self.store.append_event(
            f"registered-{extraction.sha256[:40]}",
            protocol_id,
            creation.protocol_revision.revision_number,
            "protocol_registered",
            {
                "source_filename": filename,
                "source_sha256": extraction.sha256,
                "analysis_status": status,
            },
        )
        return ProtocolRegistration(
            self._entry_for_revision(creation.protocol_revision),
            deduplicated=False,
        )

    def analyze(
        self,
        protocol_id: str,
        model: ProtocolAnalysisModel,
        *,
        analysis_id: str,
    ) -> ProtocolCatalogEntry:
        """Run analysis only at this explicit caller-owned boundary."""

        revision = self._latest_protocol_revision(protocol_id)
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source)
        status = _analysis_state(extraction)
        if status == "ocr_required":
            raise ProtocolOcrRequiredError(
                "Protocol requires OCR before structured analysis."
            )
        if status == "chunked_analysis_required":
            raise ProtocolChunkedAnalysisRequiredError(
                "Protocol requires bounded chunk analysis and reviewed merging."
            )
        try:
            draft = analyze_protocol_extraction(extraction, model)
        except Exception as exc:
            failure_code = getattr(exc, "code", "analysis_failed")
            if not isinstance(failure_code, str) or not failure_code:
                failure_code = "analysis_failed"
            failure_digest = hashlib.sha256(
                analysis_id.encode("utf-8")
            ).hexdigest()[:24]
            self.store.append_event(
                f"analysis-failed-{failure_digest}",
                protocol_id,
                revision.revision_number,
                _ANALYSIS_FAILED_EVENT,
                {"status": "failed", "failure_code": failure_code},
            )
            raise
        if draft.protocol.protocol_id != protocol_id:
            assigned_protocol = replace(draft.protocol, protocol_id=protocol_id)
            domain.validate_protocol(assigned_protocol)
            draft = replace(draft, protocol=assigned_protocol)
        self.store.append_analysis_revision(
            protocol_id,
            revision.revision_number,
            analysis_id,
            draft.protocol,
            draft.readiness,
            draft.capability_policy_id,
        )
        return self.get_entry(protocol_id)

    def approve(
        self,
        protocol_id: str,
        revision_id: str,
        *,
        policy: ApprovalPolicy,
        presented_secret: str | None,
    ) -> ProtocolCatalogEntry:
        if not policy.permits(presented_secret):
            raise ProtocolApprovalError("Protocol approval authorization failed.")
        protocol_revision_number, analysis_revision_number = _parse_revision_id(
            revision_id
        )
        if analysis_revision_number is None:
            raise ProtocolApprovalError(
                "A validated analysis revision is required for approval."
            )
        revision = self.store.get_protocol_revision(
            protocol_id, protocol_revision_number
        )
        if revision is None:
            raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
        analysis = self.store.get_analysis_revision(
            protocol_id, protocol_revision_number, analysis_revision_number
        )
        if analysis.readiness.status is not domain.ReadinessStatus.GUIDANCE_READY:
            raise ProtocolApprovalError(
                "Protocol analysis is not ready for execution approval."
            )
        self.store.append_event(
            (
                f"approved-{protocol_id[-16:]}-{protocol_revision_number}-"
                f"{analysis_revision_number}"
            ),
            protocol_id,
            protocol_revision_number,
            _APPROVAL_EVENT,
            {"decision": "approved", "authority": "service_policy"},
            analysis_revision_number=analysis_revision_number,
        )
        return self.get_entry(protocol_id)

    def load_executable_fixture(
        self, protocol_id: str
    ) -> CuratedProtocolFixture:
        revision = self._latest_protocol_revision(protocol_id)
        analysis = self._latest_analysis(revision)
        entry = self._entry_for_revision(revision)
        if analysis is None or not entry.available_for_execution:
            raise ProtocolCatalogUnavailableError(
                "Protocol revision is not approved and ready for execution."
            )
        pdf_object = self.store.get_pdf_object(revision.pdf_checksum)
        if pdf_object is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol source object is unavailable."
            )
        source = self.store.file_store.object_path(
            revision.pdf_checksum, expected_size=pdf_object.byte_size
        )
        extraction = extract_protocol_pdf(source)
        draft = ProtocolAnalysisDraft(
            extraction=extraction,
            protocol=analysis.protocol,
            readiness=analysis.readiness,
            capability_policy=domain.P1_CAPABILITY_POLICY,
            analysis_schema_version=analysis.analysis_schema_version,
            verified_evidence_count=0,
        )
        labels = tuple(
            step.source_label
            for section in analysis.protocol.sections
            for step in section.steps
        )
        return CuratedProtocolFixture(
            draft=draft,
            status="approved_revision",
            ordered_step_labels=labels,
            fixture_sha256=analysis.payload_sha256,
            revision_id=entry.revision_id,
            development_only=False,
            source_pdf_path=source,
            source_pdf_sha256=revision.pdf_checksum,
            source_filename=revision.original_filename,
        )

    def resolve_asset(
        self,
        protocol_id: str,
        revision_id: str,
        asset_id: str,
    ) -> ProtocolAssetResolution:
        fixture = self.load_executable_fixture(protocol_id)
        if fixture.revision_id != revision_id:
            raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
        matching = tuple(
            asset
            for index in range(len(fixture.steps))
            if (asset := fixture.visual_for_step(index)) is not None
            and asset.asset_id == asset_id
        )
        if not matching:
            raise ProtocolCatalogNotFoundError("Protocol visual asset is unknown.")
        if fixture.source_pdf_path is None or fixture.source_pdf_sha256 is None:
            raise ProtocolCatalogUnavailableError(
                "Protocol visual source is unavailable."
            )
        return ProtocolAssetResolution(
            path=fixture.source_pdf_path,
            sha256=fixture.source_pdf_sha256,
            source_page=matching[0].source_page,
        )
