"""Tenant-scoped commercial workspace records with append-only governance."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from voice_workflow_agent.identity import (
    AuthorizationDeniedError,
    Permission,
    Principal,
    Role,
    require_permission,
    require_same_tenant,
)


WORKSPACE_DATABASE_FILENAME = "commercial_workspace.sqlite"
WORKSPACE_SCHEMA_VERSION = 5
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCIENTIFIC_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:%|°\s*C|°C|mM|µL|uL|mL|L|mg|g|kg|s|min|h|rpm|×|x)",
    re.IGNORECASE,
)
_KNOWLEDGE_KINDS = {
    "approved_protocol_fact",
    "lab_tip",
    "historical_observation",
    "troubleshooting_note",
}
_CONNECTOR_KINDS = {"google_drive", "protocols_io", "github", "elabftw"}
_ANALYTICS_CATEGORIES = {
    "voice",
    "agent",
    "workflow",
    "protocol",
    "connector",
}
_ANALYTICS_DIMENSIONS = {
    "intent",
    "route",
    "answer_origin",
    "fallback",
    "tool",
    "status",
    "reason_code",
    "language_preference",
    "detected_language",
    "source_kind",
    "connector_kind",
    "event_kind",
    "step_bucket",
}
_EXPERIMENT_STATUSES = {
    "ready",
    "in_progress",
    "paused",
    "completed",
    "stopped",
    "blocked",
}
_EXPERIMENT_TRANSITIONS = {
    "ready": {"in_progress", "stopped", "blocked"},
    "in_progress": {"paused", "completed", "stopped", "blocked"},
    "paused": {"in_progress", "stopped", "blocked"},
    "blocked": {"in_progress", "stopped"},
    "completed": set(),
    "stopped": set(),
}
_OBSERVATION_CATEGORIES = {
    "note",
    "appearance",
    "measurement",
    "deviation",
    "other",
}
_EVIDENCE_KINDS = {"image", "document"}
_ADAPTATION_KINDS = {
    "equipment_difference",
    "reagent_substitution",
    "lab_note",
    "troubleshooting_tip",
}


class WorkspaceError(RuntimeError):
    code = "workspace_error"


class WorkspaceNotFoundError(WorkspaceError):
    code = "workspace_resource_not_found"


class WorkspaceConflictError(WorkspaceError):
    code = "workspace_conflict"


class TranslationIntegrityError(WorkspaceError):
    code = "translation_scientific_value_mismatch"


class ApprovalReplayError(WorkspaceError):
    code = "approval_request_replayed"


@dataclass(frozen=True)
class WorkspaceSettings:
    enabled: bool
    data_dir: Path | None
    analytics_retention_days: int = 90

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> WorkspaceSettings:
        env = os.environ if environment is None else environment
        enabled = env.get(
            "VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED", "false"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        raw_dir = env.get("VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR", "").strip()
        data_dir = Path(raw_dir) if raw_dir else None
        if enabled and (data_dir is None or not data_dir.is_absolute()):
            raise WorkspaceError(
                "Workspace data directory must be an absolute path when enabled."
            )
        retention = int(
            env.get("VOICE_WORKFLOW_AGENT_ANALYTICS_RETENTION_DAYS", "90")
        )
        if retention < 1 or retention > 3650:
            raise WorkspaceError("Analytics retention is outside allowed bounds.")
        return cls(enabled, data_dir, retention)


@dataclass(frozen=True)
class ProtocolFamily:
    family_id: str
    organization_id: str
    title: str
    owner_principal_id: str
    created_at: str


@dataclass(frozen=True)
class ProtocolSource:
    source_id: str
    organization_id: str
    connector_kind: str
    external_id: str
    version_identity: str
    source_hash: str
    canonical_url: str | None
    metadata: dict[str, object]
    created_at: str


@dataclass(frozen=True)
class ProtocolLineageRevision:
    revision_id: str
    family_id: str
    organization_id: str
    revision_number: int
    parent_revision_id: str | None
    source_id: str
    author_principal_id: str
    created_at: str
    change_summary: str
    content_hash: str
    source_hash: str
    language: str
    translation_status: str
    content: dict[str, object]


@dataclass(frozen=True)
class LabAdaptationRevision:
    adaptation_id: str
    organization_id: str
    family_id: str
    base_revision_id: str
    adapted_revision_id: str
    author_principal_id: str
    changes: tuple[dict[str, object], ...]
    created_at: str


@dataclass(frozen=True)
class ApprovalEvent:
    sequence_id: int
    approval_id: str
    organization_id: str
    revision_id: str
    action: str
    actor_principal_id: str
    actor_role: str
    comment: str
    replacement_revision_id: str | None
    created_at: str


@dataclass(frozen=True)
class ConnectorConfiguration:
    connector_id: str
    organization_id: str
    connector_kind: str
    display_name: str
    credential_reference: str
    webhook_secret_reference: str | None
    allowed_roots: tuple[str, ...]
    enabled: bool
    created_at: str


@dataclass(frozen=True)
class ExperimentSession:
    session_id: str
    organization_id: str
    owner_principal_id: str
    protocol_id: str
    protocol_revision_id: str
    status: str
    current_step_id: str | None
    current_step_label: str | None
    version: int
    started_at: str
    paused_at: str | None
    ended_at: str | None
    updated_at: str
    last_voice_connection_id: str | None


SCHEMA = """
CREATE TABLE schema_metadata(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=1)
);
INSERT INTO schema_metadata(schema_version) VALUES(1);

CREATE TABLE organizations(
 organization_id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE principals(
 principal_id TEXT PRIMARY KEY,
 subject TEXT NOT NULL,
 display_name TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE memberships(
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 role TEXT NOT NULL CHECK(role IN ('researcher','reviewer','lab_admin','organization_admin')),
 active INTEGER NOT NULL CHECK(active IN (0,1)),
 created_at TEXT NOT NULL,
 PRIMARY KEY(organization_id,principal_id,role)
);
CREATE TABLE organization_settings(
 organization_id TEXT PRIMARY KEY REFERENCES organizations(organization_id),
 analytics_retention_days INTEGER NOT NULL CHECK(analytics_retention_days BETWEEN 1 AND 3650),
 updated_by_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 updated_at TEXT NOT NULL
);
CREATE TABLE resource_bindings(
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 resource_type TEXT NOT NULL,
 resource_id TEXT NOT NULL,
 owner_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(resource_type,resource_id)
);
CREATE TABLE protocol_families(
 family_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 title TEXT NOT NULL,
 owner_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 created_at TEXT NOT NULL
);
CREATE TABLE protocol_sources(
 source_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 connector_kind TEXT NOT NULL,
 external_id TEXT NOT NULL,
 version_identity TEXT NOT NULL,
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
 canonical_url TEXT,
 metadata_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(organization_id,connector_kind,external_id,version_identity,source_hash)
);
CREATE TABLE protocol_lineage_revisions(
 revision_id TEXT PRIMARY KEY,
 family_id TEXT NOT NULL REFERENCES protocol_families(family_id),
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 revision_number INTEGER NOT NULL CHECK(revision_number>0),
 parent_revision_id TEXT REFERENCES protocol_lineage_revisions(revision_id),
 source_id TEXT NOT NULL REFERENCES protocol_sources(source_id),
 author_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 created_at TEXT NOT NULL,
 change_summary TEXT NOT NULL,
 content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
 language TEXT NOT NULL,
 translation_status TEXT NOT NULL CHECK(translation_status IN ('original','machine','reviewed')),
 content_json TEXT NOT NULL,
 UNIQUE(family_id,revision_number)
);
CREATE TABLE protocol_translations(
 translation_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 revision_id TEXT NOT NULL REFERENCES protocol_lineage_revisions(revision_id),
 language TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('machine','reviewed')),
 content_text TEXT NOT NULL,
 content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 created_at TEXT NOT NULL,
 UNIQUE(revision_id,language,content_hash)
);
CREATE TABLE protocol_approval_events(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 approval_id TEXT NOT NULL UNIQUE,
 idempotency_key TEXT NOT NULL,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 revision_id TEXT NOT NULL REFERENCES protocol_lineage_revisions(revision_id),
 action TEXT NOT NULL CHECK(action IN ('approved','rejected','revoked')),
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 actor_role TEXT NOT NULL,
 comment TEXT NOT NULL,
 replacement_revision_id TEXT REFERENCES protocol_lineage_revisions(revision_id),
 created_at TEXT NOT NULL,
 UNIQUE(organization_id,idempotency_key)
);
CREATE TABLE source_inbox(
 item_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 source_id TEXT NOT NULL REFERENCES protocol_sources(source_id),
 revision_id TEXT REFERENCES protocol_lineage_revisions(revision_id),
 change_kind TEXT NOT NULL CHECK(change_kind IN ('new','changed')),
 status TEXT NOT NULL CHECK(status IN ('unread','reviewing','resolved')),
 created_at TEXT NOT NULL
);
CREATE TABLE connector_configurations(
 connector_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 connector_kind TEXT NOT NULL,
 display_name TEXT NOT NULL,
 credential_reference TEXT NOT NULL,
 webhook_secret_reference TEXT,
 allowed_roots_json TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 created_at TEXT NOT NULL
);
CREATE TABLE connector_sync_state(
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 connector_id TEXT NOT NULL REFERENCES connector_configurations(connector_id),
 cursor_kind TEXT NOT NULL,
 opaque_cursor TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(organization_id,connector_id,cursor_kind)
);
CREATE TABLE github_webhook_deliveries(
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 connector_id TEXT NOT NULL REFERENCES connector_configurations(connector_id),
 delivery_id TEXT NOT NULL,
 body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),
 event_name TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('processing','completed','failed')),
 received_at TEXT NOT NULL,
 completed_at TEXT,
 PRIMARY KEY(connector_id,delivery_id)
);
CREATE TABLE knowledge_entries(
 knowledge_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 revision_id TEXT REFERENCES protocol_lineage_revisions(revision_id),
 kind TEXT NOT NULL,
 body TEXT NOT NULL,
 provenance_json TEXT NOT NULL,
 author_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 created_at TEXT NOT NULL
);
CREATE TABLE knowledge_promotion_events(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 promotion_id TEXT NOT NULL UNIQUE,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 knowledge_id TEXT NOT NULL REFERENCES knowledge_entries(knowledge_id),
 promoted_kind TEXT NOT NULL CHECK(promoted_kind='approved_protocol_fact'),
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 comment TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE asset_card_versions(
 version_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 asset_id TEXT NOT NULL,
 asset_kind TEXT NOT NULL CHECK(asset_kind IN ('reagent','equipment')),
 name TEXT NOT NULL,
 location_json TEXT NOT NULL,
 photo_url TEXT,
 barcode TEXT,
 sds_url TEXT,
 author_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 review_status TEXT NOT NULL CHECK(review_status IN ('draft','reviewed')),
 created_at TEXT NOT NULL
);
CREATE TABLE protocol_library_preferences(
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 family_id TEXT NOT NULL REFERENCES protocol_families(family_id),
 favorite INTEGER NOT NULL CHECK(favorite IN (0,1)),
 last_opened_at TEXT,
 tags_json TEXT NOT NULL,
 PRIMARY KEY(organization_id,principal_id,family_id)
);
CREATE TABLE computational_workflow_families(
 workflow_family_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 name TEXT NOT NULL,
 owner_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 created_at TEXT NOT NULL
);
CREATE TABLE computational_workflow_revisions(
 workflow_revision_id TEXT PRIMARY KEY,
 workflow_family_id TEXT NOT NULL REFERENCES computational_workflow_families(workflow_family_id),
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 revision_number INTEGER NOT NULL CHECK(revision_number>0),
 parent_revision_id TEXT REFERENCES computational_workflow_revisions(workflow_revision_id),
 engine TEXT NOT NULL CHECK(engine IN ('snakemake','nextflow')),
 repository TEXT NOT NULL,
 commit_sha TEXT NOT NULL,
 source_path TEXT NOT NULL,
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
 metadata_json TEXT NOT NULL,
 approval_state TEXT NOT NULL CHECK(approval_state IN ('review_required','approved','revoked')),
 reviewer_principal_id TEXT REFERENCES principals(principal_id),
 review_comment TEXT,
 reviewed_at TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(workflow_family_id,revision_number)
);
CREATE TABLE computational_workflow_review_events(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 workflow_review_id TEXT NOT NULL UNIQUE,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 workflow_revision_id TEXT NOT NULL REFERENCES computational_workflow_revisions(workflow_revision_id),
 action TEXT NOT NULL CHECK(action IN ('approved','revoked')),
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 actor_role TEXT NOT NULL,
 comment TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE wet_dry_workflow_links(
 link_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 experiment_session_id TEXT NOT NULL,
 protocol_revision_id TEXT NOT NULL REFERENCES protocol_lineage_revisions(revision_id),
 workflow_revision_id TEXT NOT NULL REFERENCES computational_workflow_revisions(workflow_revision_id),
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 created_at TEXT NOT NULL,
 UNIQUE(organization_id,experiment_session_id,workflow_revision_id)
);
CREATE TABLE eln_writeback_events(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 writeback_id TEXT NOT NULL UNIQUE,
 idempotency_key TEXT NOT NULL,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 connector_id TEXT NOT NULL REFERENCES connector_configurations(connector_id),
 report_id TEXT NOT NULL,
 protocol_revision_id TEXT NOT NULL REFERENCES protocol_lineage_revisions(revision_id),
 external_experiment_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 created_at TEXT NOT NULL,
 UNIQUE(organization_id,idempotency_key)
);
CREATE TABLE eln_writeback_requests(
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 idempotency_key TEXT NOT NULL,
 connector_id TEXT NOT NULL REFERENCES connector_configurations(connector_id),
 report_id TEXT NOT NULL,
 protocol_revision_id TEXT NOT NULL REFERENCES protocol_lineage_revisions(revision_id),
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 status TEXT NOT NULL CHECK(status IN ('processing','completed','failed')),
 created_at TEXT NOT NULL,
 completed_at TEXT,
 PRIMARY KEY(organization_id,idempotency_key)
);
CREATE TABLE analytics_events(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 category TEXT NOT NULL,
 metric_name TEXT NOT NULL,
 metric_value REAL NOT NULL,
 dimensions_json TEXT NOT NULL,
 recorded_at TEXT NOT NULL
);

CREATE TRIGGER protocol_sources_no_update BEFORE UPDATE ON protocol_sources BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER protocol_sources_no_delete BEFORE DELETE ON protocol_sources BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER lineage_no_update BEFORE UPDATE ON protocol_lineage_revisions BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER lineage_no_delete BEFORE DELETE ON protocol_lineage_revisions BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER approvals_no_update BEFORE UPDATE ON protocol_approval_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER approvals_no_delete BEFORE DELETE ON protocol_approval_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER translations_no_update BEFORE UPDATE ON protocol_translations BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER translations_no_delete BEFORE DELETE ON protocol_translations BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER knowledge_no_update BEFORE UPDATE ON knowledge_entries BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER knowledge_no_delete BEFORE DELETE ON knowledge_entries BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER promotions_no_update BEFORE UPDATE ON knowledge_promotion_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER promotions_no_delete BEFORE DELETE ON knowledge_promotion_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER assets_no_update BEFORE UPDATE ON asset_card_versions BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER assets_no_delete BEFORE DELETE ON asset_card_versions BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER workflow_families_no_update BEFORE UPDATE ON computational_workflow_families BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER workflow_families_no_delete BEFORE DELETE ON computational_workflow_families BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER workflow_links_no_update BEFORE UPDATE ON wet_dry_workflow_links BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER workflow_links_no_delete BEFORE DELETE ON wet_dry_workflow_links BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER workflow_reviews_no_update BEFORE UPDATE ON computational_workflow_review_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER workflow_reviews_no_delete BEFORE DELETE ON computational_workflow_review_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER eln_writebacks_no_update BEFORE UPDATE ON eln_writeback_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER eln_writebacks_no_delete BEFORE DELETE ON eln_writeback_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
"""


MIGRATION_1_TO_2 = """
CREATE TABLE experiment_sessions(
 session_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 owner_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 protocol_id TEXT NOT NULL,
 protocol_revision_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('ready','in_progress','paused','completed','stopped','blocked')),
 current_step_id TEXT,
 current_step_label TEXT,
 version INTEGER NOT NULL CHECK(version>0),
 started_at TEXT NOT NULL,
 paused_at TEXT,
 ended_at TEXT,
 updated_at TEXT NOT NULL,
 last_voice_connection_id TEXT,
 UNIQUE(organization_id,session_id)
);
CREATE INDEX experiment_sessions_tenant_status_started
 ON experiment_sessions(organization_id,status,started_at DESC);
CREATE INDEX experiment_sessions_owner_started
 ON experiment_sessions(organization_id,owner_principal_id,started_at DESC);

CREATE TABLE experiment_session_events(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 session_id TEXT NOT NULL REFERENCES experiment_sessions(session_id),
 event_key TEXT NOT NULL,
 event_type TEXT NOT NULL,
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 step_id TEXT,
 step_label TEXT,
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(session_id,event_key)
);
CREATE INDEX experiment_events_tenant_session_sequence
 ON experiment_session_events(organization_id,session_id,sequence_id);

CREATE TABLE experiment_completed_steps(
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 session_id TEXT NOT NULL REFERENCES experiment_sessions(session_id),
 step_id TEXT NOT NULL,
 step_label TEXT,
 completed_by_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 completed_at TEXT NOT NULL,
 event_id TEXT NOT NULL REFERENCES experiment_session_events(event_id),
 PRIMARY KEY(session_id,step_id)
);

CREATE TRIGGER experiment_events_no_update BEFORE UPDATE ON experiment_session_events
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER experiment_events_no_delete BEFORE DELETE ON experiment_session_events
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER experiment_completed_no_update BEFORE UPDATE ON experiment_completed_steps
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER experiment_completed_no_delete BEFORE DELETE ON experiment_completed_steps
 BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE schema_metadata_next(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=2)
);
INSERT INTO schema_metadata_next(schema_version) VALUES(2);
DROP TABLE schema_metadata;
ALTER TABLE schema_metadata_next RENAME TO schema_metadata;
"""


MIGRATION_2_TO_3 = """
CREATE TABLE experiment_observations(
 observation_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 session_id TEXT NOT NULL REFERENCES experiment_sessions(session_id),
 protocol_step_id TEXT NOT NULL,
 protocol_step_label TEXT,
 author_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 content TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('note','appearance','measurement','deviation','other')),
 capture_source TEXT NOT NULL CHECK(capture_source IN ('voice','manual')),
 knowledge_effect TEXT NOT NULL CHECK(knowledge_effect='observation_only'),
 created_at TEXT NOT NULL,
 UNIQUE(session_id,observation_id)
);
CREATE INDEX experiment_observations_tenant_session_created
 ON experiment_observations(organization_id,session_id,created_at);

CREATE TABLE experiment_evidence(
 evidence_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 session_id TEXT NOT NULL REFERENCES experiment_sessions(session_id),
 protocol_step_id TEXT NOT NULL,
 protocol_step_label TEXT,
 uploader_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('image','document')),
 original_filename TEXT NOT NULL,
 media_type TEXT NOT NULL,
 byte_size INTEGER NOT NULL CHECK(byte_size>=0 AND byte_size<=1073741824),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 storage_reference TEXT NOT NULL,
 caption TEXT,
 interpretation_status TEXT NOT NULL CHECK(interpretation_status='not_interpreted'),
 created_at TEXT NOT NULL,
 UNIQUE(session_id,sha256,protocol_step_id)
);
CREATE INDEX experiment_evidence_tenant_session_created
 ON experiment_evidence(organization_id,session_id,created_at);

CREATE TRIGGER experiment_observations_no_update BEFORE UPDATE ON experiment_observations
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER experiment_observations_no_delete BEFORE DELETE ON experiment_observations
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER experiment_evidence_no_update BEFORE UPDATE ON experiment_evidence
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER experiment_evidence_no_delete BEFORE DELETE ON experiment_evidence
 BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE schema_metadata_next(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=3)
);
INSERT INTO schema_metadata_next(schema_version) VALUES(3);
DROP TABLE schema_metadata;
ALTER TABLE schema_metadata_next RENAME TO schema_metadata;
"""


MIGRATION_3_TO_4 = """
CREATE TABLE protocol_adaptation_revisions(
 adaptation_id TEXT PRIMARY KEY,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 family_id TEXT NOT NULL REFERENCES protocol_families(family_id),
 base_revision_id TEXT NOT NULL REFERENCES protocol_lineage_revisions(revision_id),
 adapted_revision_id TEXT NOT NULL UNIQUE REFERENCES protocol_lineage_revisions(revision_id),
 author_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 changes_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX protocol_adaptations_tenant_family_created
 ON protocol_adaptation_revisions(organization_id,family_id,created_at);
CREATE TRIGGER protocol_adaptations_no_update
 BEFORE UPDATE ON protocol_adaptation_revisions
 BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER protocol_adaptations_no_delete
 BEFORE DELETE ON protocol_adaptation_revisions
 BEGIN SELECT RAISE(ABORT,'immutable'); END;

CREATE TABLE schema_metadata_next(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=4)
);
INSERT INTO schema_metadata_next(schema_version) VALUES(4);
DROP TABLE schema_metadata;
ALTER TABLE schema_metadata_next RENAME TO schema_metadata;
"""


MIGRATION_4_TO_5 = """
ALTER TABLE eln_writeback_events
 ADD COLUMN experiment_session_id TEXT REFERENCES experiment_sessions(session_id);
ALTER TABLE eln_writeback_requests
 ADD COLUMN experiment_session_id TEXT REFERENCES experiment_sessions(session_id);
CREATE INDEX eln_writebacks_tenant_session_created
 ON eln_writeback_events(organization_id,experiment_session_id,created_at);

CREATE TABLE schema_metadata_next(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=5)
);
INSERT INTO schema_metadata_next(schema_version) VALUES(5);
DROP TABLE schema_metadata;
ALTER TABLE schema_metadata_next RENAME TO schema_metadata;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WorkspaceError("Workspace payload is not deterministic JSON.") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise WorkspaceError(f"{label} is invalid.")
    return value


def _text(value: str, label: str, *, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise WorkspaceError(f"{label} is invalid.")
    return value.strip()


class WorkspaceStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        database_path: Path,
        *,
        default_analytics_retention_days: int = 90,
    ) -> None:
        self._connection = connection
        self.database_path = database_path
        self.default_analytics_retention_days = default_analytics_retention_days

    def close(self) -> None:
        self._connection.close()

    def bootstrap_principal(
        self, principal: Principal, *, organization_name: str | None = None
    ) -> None:
        """Idempotently materialize verified identity claims for local ownership."""

        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "INSERT OR IGNORE INTO organizations VALUES(?,?,?)",
                (
                    principal.organization_id,
                    organization_name or principal.organization_id,
                    now,
                ),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO principals VALUES(?,?,?,?)",
                (
                    principal.principal_id,
                    principal.subject,
                    principal.display_name,
                    now,
                ),
            )
            existing_membership = self._connection.execute(
                """SELECT 1 FROM memberships
                WHERE organization_id=? AND principal_id=? LIMIT 1""",
                (principal.organization_id, principal.principal_id),
            ).fetchone()
            # Verified claims bootstrap a principal exactly once.  Thereafter the
            # tenant's administrator controls local role activation instead of a
            # stale token silently undoing an explicit suspension.
            if existing_membership is None:
                for role in principal.roles:
                    self._connection.execute(
                        "INSERT INTO memberships VALUES(?,?,?,?,?)",
                        (
                            principal.organization_id,
                            principal.principal_id,
                            role.value,
                            1,
                            now,
                        ),
                    )
            self._connection.execute(
                """INSERT OR IGNORE INTO organization_settings
                VALUES(?,?,?,?)""",
                (
                    principal.organization_id,
                    self.default_analytics_retention_days,
                    principal.principal_id,
                    now,
                ),
            )
            self._connection.commit()
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise WorkspaceError("Identity could not be stored.") from exc

    def verify_membership(self, principal: Principal) -> None:
        rows = self._connection.execute(
            """SELECT role FROM memberships
            WHERE organization_id=? AND principal_id=? AND active=1""",
            (principal.organization_id, principal.principal_id),
        ).fetchall()
        stored = frozenset(Role(row[0]) for row in rows)
        if not principal.roles.intersection(stored):
            raise AuthorizationDeniedError("No active tenant membership exists.")

    def effective_principal(self, principal: Principal) -> Principal:
        """Intersect verified claims with active, tenant-managed memberships."""

        rows = self._connection.execute(
            """SELECT role FROM memberships
            WHERE organization_id=? AND principal_id=? AND active=1""",
            (principal.organization_id, principal.principal_id),
        ).fetchall()
        effective_roles = principal.roles.intersection(
            Role(row["role"]) for row in rows
        )
        if not effective_roles:
            raise AuthorizationDeniedError("No active tenant membership exists.")
        return Principal(
            principal_id=principal.principal_id,
            subject=principal.subject,
            organization_id=principal.organization_id,
            display_name=principal.display_name,
            roles=frozenset(effective_roles),
            authentication_method=principal.authentication_method,
        )

    def membership_summaries(
        self, principal: Principal
    ) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.MEMBERSHIP_MANAGE)
        rows = self._connection.execute(
            """SELECT p.principal_id,p.display_name,m.role,m.active,m.created_at
            FROM memberships m JOIN principals p ON p.principal_id=m.principal_id
            WHERE m.organization_id=?
            ORDER BY p.display_name,p.principal_id,m.role""",
            (principal.organization_id,),
        ).fetchall()
        return tuple(
            {
                "principal_id": row["principal_id"],
                "display_name": row["display_name"],
                "role": row["role"],
                "active": bool(row["active"]),
                "created_at": row["created_at"],
            }
            for row in rows
        )

    def set_membership(
        self,
        principal: Principal,
        *,
        target_principal_id: str,
        target_subject: str,
        display_name: str,
        role: str,
        active: bool,
    ) -> dict[str, object]:
        require_permission(principal, Permission.MEMBERSHIP_MANAGE)
        self.verify_membership(principal)
        target_principal_id = _identifier(target_principal_id, "Principal identifier")
        target_subject = _text(target_subject, "Principal subject", maximum=512)
        display_name = _text(display_name, "Display name", maximum=300)
        try:
            selected_role = Role(role)
        except ValueError as exc:
            raise WorkspaceError("Membership role is invalid.") from exc
        if (
            target_principal_id == principal.principal_id
            and selected_role in {Role.LAB_ADMIN, Role.ORGANIZATION_ADMIN}
            and not active
        ):
            raise WorkspaceConflictError(
                "Administrators cannot deactivate their own administrative role."
            )
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "INSERT OR IGNORE INTO principals VALUES(?,?,?,?)",
                (target_principal_id, target_subject, display_name, now),
            )
            row = self._connection.execute(
                "SELECT subject FROM principals WHERE principal_id=?",
                (target_principal_id,),
            ).fetchone()
            if row is None or row["subject"] != target_subject:
                raise WorkspaceConflictError("Principal identity is already assigned.")
            self._connection.execute(
                """INSERT INTO memberships VALUES(?,?,?,?,?)
                ON CONFLICT(organization_id,principal_id,role)
                DO UPDATE SET active=excluded.active""",
                (
                    principal.organization_id,
                    target_principal_id,
                    selected_role.value,
                    int(active),
                    now,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return {
            "principal_id": target_principal_id,
            "display_name": display_name,
            "role": selected_role.value,
            "active": active,
        }

    def bind_resource(
        self,
        principal: Principal,
        resource_type: str,
        resource_id: str,
    ) -> None:
        self.verify_membership(principal)
        resource_type = _identifier(resource_type, "Resource type")
        resource_id = _identifier(resource_id, "Resource identifier")
        row = self._connection.execute(
            "SELECT organization_id FROM resource_bindings WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        ).fetchone()
        if row is not None:
            require_same_tenant(principal, row[0])
            return
        self._connection.execute(
            "INSERT INTO resource_bindings VALUES(?,?,?,?,?)",
            (
                principal.organization_id,
                resource_type,
                resource_id,
                principal.principal_id,
                _now(),
            ),
        )
        self._connection.commit()

    def require_resource(
        self, principal: Principal, resource_type: str, resource_id: str
    ) -> None:
        row = self._connection.execute(
            "SELECT organization_id FROM resource_bindings WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError("The resource is not available.")
        try:
            require_same_tenant(principal, row[0])
        except AuthorizationDeniedError as exc:
            raise WorkspaceNotFoundError("The resource is not available.") from exc

    def resource_ids(
        self, principal: Principal, resource_type: str
    ) -> frozenset[str]:
        self.verify_membership(principal)
        rows = self._connection.execute(
            """SELECT resource_id FROM resource_bindings
            WHERE organization_id=? AND resource_type=?""",
            (principal.organization_id, _identifier(resource_type, "Resource type")),
        ).fetchall()
        return frozenset(row[0] for row in rows)

    def _experiment_row(
        self, principal: Principal, session_id: str, *, write: bool = False
    ) -> sqlite3.Row:
        require_permission(
            principal, Permission.REPORT_WRITE if write else Permission.REPORT_READ
        )
        self.verify_membership(principal)
        session_id = _identifier(session_id, "Experiment session identifier")
        row = self._connection.execute(
            "SELECT * FROM experiment_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Experiment session is not available.")
        elevated = bool(
            principal.roles.intersection(
                {Role.REVIEWER, Role.LAB_ADMIN, Role.ORGANIZATION_ADMIN}
            )
        )
        if not elevated and row["owner_principal_id"] != principal.principal_id:
            raise WorkspaceNotFoundError("Experiment session is not available.")
        return row

    @staticmethod
    def _experiment(row: sqlite3.Row) -> ExperimentSession:
        return ExperimentSession(
            session_id=row["session_id"],
            organization_id=row["organization_id"],
            owner_principal_id=row["owner_principal_id"],
            protocol_id=row["protocol_id"],
            protocol_revision_id=row["protocol_revision_id"],
            status=row["status"],
            current_step_id=row["current_step_id"],
            current_step_label=row["current_step_label"],
            version=int(row["version"]),
            started_at=row["started_at"],
            paused_at=row["paused_at"],
            ended_at=row["ended_at"],
            updated_at=row["updated_at"],
            last_voice_connection_id=row["last_voice_connection_id"],
        )

    def _append_experiment_event(
        self,
        principal: Principal,
        *,
        session_id: str,
        event_key: str,
        event_type: str,
        step_id: str | None = None,
        step_label: str | None = None,
        payload: Mapping[str, object] | None = None,
        created_at: str | None = None,
    ) -> tuple[str, bool]:
        event_key = _identifier(event_key, "Experiment event key")
        event_type = _identifier(event_type, "Experiment event type")
        if step_id is not None:
            step_id = _identifier(step_id, "Protocol step identifier")
        if step_label is not None:
            step_label = _text(step_label, "Protocol step label", maximum=200)
        payload_json = _canonical_json(dict(payload or {}))
        if len(payload_json) > 16_000:
            raise WorkspaceError("Experiment event payload is too large.")
        event_id = "event-" + hashlib.sha256(
            f"{principal.organization_id}:{session_id}:{event_key}".encode("utf-8")
        ).hexdigest()[:32]
        cursor = self._connection.execute(
            """INSERT OR IGNORE INTO experiment_session_events(
            event_id,organization_id,session_id,event_key,event_type,
            actor_principal_id,step_id,step_label,payload_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                principal.organization_id,
                session_id,
                event_key,
                event_type,
                principal.principal_id,
                step_id,
                step_label,
                payload_json,
                created_at or _now(),
            ),
        )
        return event_id, bool(cursor.rowcount)

    def start_experiment(
        self,
        principal: Principal,
        *,
        protocol_id: str,
        protocol_revision_id: str,
        session_id: str | None = None,
        current_step_id: str | None = None,
        current_step_label: str | None = None,
        voice_connection_id: str | None = None,
    ) -> dict[str, object]:
        """Create one durable experiment bound to an exact protocol revision."""

        require_permission(principal, Permission.REPORT_WRITE)
        self.verify_membership(principal)
        selected_id = session_id or f"experiment-{secrets.token_hex(16)}"
        selected_id = _identifier(selected_id, "Experiment session identifier")
        protocol_id = _identifier(protocol_id, "Protocol identifier")
        protocol_revision_id = _identifier(
            protocol_revision_id, "Protocol revision identifier"
        )
        if current_step_id is not None:
            current_step_id = _identifier(current_step_id, "Protocol step identifier")
        if current_step_label is not None:
            current_step_label = _text(
                current_step_label, "Protocol step label", maximum=200
            )
        if voice_connection_id is not None:
            voice_connection_id = _identifier(
                voice_connection_id, "Voice connection identifier"
            )
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """INSERT INTO experiment_sessions(
                session_id,organization_id,owner_principal_id,protocol_id,
                protocol_revision_id,status,current_step_id,current_step_label,
                version,started_at,paused_at,ended_at,updated_at,
                last_voice_connection_id
                ) VALUES(?,?,?,?,?,'ready',?,?,1,?,NULL,NULL,?,?)""",
                (
                    selected_id,
                    principal.organization_id,
                    principal.principal_id,
                    protocol_id,
                    protocol_revision_id,
                    current_step_id,
                    current_step_label,
                    now,
                    now,
                    voice_connection_id,
                ),
            )
            self._connection.execute(
                "INSERT INTO resource_bindings VALUES(?,?,?,?,?)",
                (
                    principal.organization_id,
                    "experiment_session",
                    selected_id,
                    principal.principal_id,
                    now,
                ),
            )
            self._append_experiment_event(
                principal,
                session_id=selected_id,
                event_key="session-started",
                event_type="session_started",
                step_id=current_step_id,
                step_label=current_step_label,
                payload={
                    "protocol_id": protocol_id,
                    "protocol_revision_id": protocol_revision_id,
                    "voice_bound": voice_connection_id is not None,
                },
                created_at=now,
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise WorkspaceConflictError(
                "Experiment session already exists."
            ) from exc
        except Exception:
            self._connection.rollback()
            raise
        return self.get_experiment(principal, selected_id)

    def get_experiment(
        self, principal: Principal, session_id: str
    ) -> dict[str, object]:
        session = self._experiment(self._experiment_row(principal, session_id))
        completed = self._connection.execute(
            """SELECT step_id,step_label,completed_by_principal_id,completed_at,event_id
            FROM experiment_completed_steps WHERE session_id=?
            ORDER BY completed_at,step_id""",
            (session.session_id,),
        ).fetchall()
        events = self._connection.execute(
            """SELECT event_id,event_key,event_type,actor_principal_id,step_id,
            step_label,payload_json,created_at FROM experiment_session_events
            WHERE session_id=? ORDER BY sequence_id""",
            (session.session_id,),
        ).fetchall()
        return {
            **session.__dict__,
            "completed_steps": [dict(row) for row in completed],
            "events": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "event_id",
                            "event_key",
                            "event_type",
                            "actor_principal_id",
                            "step_id",
                            "step_label",
                            "created_at",
                        )
                    },
                    "payload": json.loads(row["payload_json"]),
                }
                for row in events
            ],
        }

    def list_experiments(
        self, principal: Principal, *, active_only: bool = False
    ) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.REPORT_READ)
        self.verify_membership(principal)
        clauses = ["organization_id=?"]
        parameters: list[object] = [principal.organization_id]
        if not principal.roles.intersection(
            {Role.REVIEWER, Role.LAB_ADMIN, Role.ORGANIZATION_ADMIN}
        ):
            clauses.append("owner_principal_id=?")
            parameters.append(principal.principal_id)
        if active_only:
            clauses.append("status IN ('ready','in_progress','paused','blocked')")
        rows = self._connection.execute(
            f"""SELECT * FROM experiment_sessions WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC,session_id DESC""",
            tuple(parameters),
        ).fetchall()
        return tuple(
            {
                **self._experiment(row).__dict__,
                "completed_step_count": self._connection.execute(
                    "SELECT COUNT(*) FROM experiment_completed_steps WHERE session_id=?",
                    (row["session_id"],),
                ).fetchone()[0],
            }
            for row in rows
        )

    def resume_experiment(
        self,
        principal: Principal,
        session_id: str,
        *,
        expected_version: int,
        protocol_id: str,
        protocol_revision_id: str,
        voice_connection_id: str,
    ) -> dict[str, object]:
        """Recover an existing exact-revision session with optimistic locking."""

        row = self._experiment_row(principal, session_id, write=True)
        protocol_id = _identifier(protocol_id, "Protocol identifier")
        protocol_revision_id = _identifier(
            protocol_revision_id, "Protocol revision identifier"
        )
        voice_connection_id = _identifier(
            voice_connection_id, "Voice connection identifier"
        )
        if row["protocol_id"] != protocol_id or row["protocol_revision_id"] != protocol_revision_id:
            raise WorkspaceConflictError(
                "Experiment recovery requires the original exact protocol revision."
            )
        if row["status"] not in {"ready", "in_progress", "paused", "blocked"}:
            raise WorkspaceConflictError("Experiment session cannot be resumed.")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise WorkspaceError("Experiment version is invalid.")
        target_version = expected_version + 1
        target_status = (
            "in_progress" if row["status"] in {"paused", "blocked"} else row["status"]
        )
        now = _now()
        event_key = f"voice-recovery-v{target_version}"
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                """UPDATE experiment_sessions SET status=?,paused_at=NULL,
                ended_at=NULL,updated_at=?,version=?,last_voice_connection_id=?
                WHERE session_id=? AND organization_id=? AND version=?""",
                (
                    target_status,
                    now,
                    target_version,
                    voice_connection_id,
                    session_id,
                    principal.organization_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkspaceConflictError(
                    "Experiment session changed; refresh before resuming."
                )
            self._append_experiment_event(
                principal,
                session_id=session_id,
                event_key=event_key,
                event_type=(
                    "session_resumed" if row["status"] == "paused" else "session_recovered"
                ),
                step_id=row["current_step_id"],
                step_label=row["current_step_label"],
                payload={"previous_status": row["status"]},
                created_at=now,
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.get_experiment(principal, session_id)

    def transition_experiment(
        self,
        principal: Principal,
        session_id: str,
        *,
        action: str,
        expected_version: int,
        event_key: str,
        reason: str | None = None,
    ) -> dict[str, object]:
        row = self._experiment_row(principal, session_id, write=True)
        target = {
            "pause": "paused",
            "resume": "in_progress",
            "complete": "completed",
            "stop": "stopped",
            "block": "blocked",
        }.get(action)
        if target is None or target not in _EXPERIMENT_TRANSITIONS[row["status"]]:
            raise WorkspaceConflictError("Experiment transition is not allowed.")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise WorkspaceError("Experiment version is invalid.")
        if reason is not None:
            reason = _text(reason, "Experiment transition reason", maximum=2000)
        event_key = _identifier(event_key, "Experiment event key")
        now = _now()
        target_version = expected_version + 1
        paused_at = now if target == "paused" else None
        ended_at = now if target in {"completed", "stopped"} else None
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                """UPDATE experiment_sessions SET status=?,paused_at=?,ended_at=?,
                updated_at=?,version=? WHERE session_id=? AND organization_id=?
                AND version=?""",
                (
                    target,
                    paused_at,
                    ended_at,
                    now,
                    target_version,
                    session_id,
                    principal.organization_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkspaceConflictError(
                    "Experiment session changed; refresh before retrying."
                )
            self._append_experiment_event(
                principal,
                session_id=session_id,
                event_key=event_key,
                event_type=f"session_{target}",
                step_id=row["current_step_id"],
                step_label=row["current_step_label"],
                payload={"reason": reason} if reason is not None else {},
                created_at=now,
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.get_experiment(principal, session_id)

    def record_experiment_progress(
        self,
        principal: Principal,
        session_id: str,
        *,
        event_key: str,
        event_type: str,
        step_id: str | None,
        step_label: str | None,
        next_step_id: str | None = None,
        next_step_label: str | None = None,
        mark_completed: bool = False,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Append a server-authorized step event and update the recovery projection."""

        row = self._experiment_row(principal, session_id, write=True)
        protocol_start = event_type == "protocol_started" and row["status"] == "ready"
        if row["status"] != "in_progress" and not protocol_start:
            raise WorkspaceConflictError(
                "Only an in-progress experiment can record step progress."
            )
        if step_id is not None:
            step_id = _identifier(step_id, "Protocol step identifier")
        if step_label is not None:
            step_label = _text(step_label, "Protocol step label", maximum=200)
        if next_step_id is not None:
            next_step_id = _identifier(next_step_id, "Next step identifier")
        if next_step_label is not None:
            next_step_label = _text(next_step_label, "Next step label", maximum=200)
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            event_id, inserted = self._append_experiment_event(
                principal,
                session_id=session_id,
                event_key=event_key,
                event_type=event_type,
                step_id=step_id,
                step_label=step_label,
                payload=payload,
                created_at=now,
            )
            if inserted:
                if mark_completed:
                    if step_id is None:
                        raise WorkspaceError(
                            "Completed progress requires a protocol step."
                        )
                    self._connection.execute(
                        """INSERT INTO experiment_completed_steps(
                        organization_id,session_id,step_id,step_label,
                        completed_by_principal_id,completed_at,event_id
                        ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            principal.organization_id,
                            session_id,
                            step_id,
                            step_label,
                            principal.principal_id,
                            now,
                            event_id,
                        ),
                    )
                self._connection.execute(
                    """UPDATE experiment_sessions SET current_step_id=?,
                    current_step_label=?,status=?,updated_at=?,version=version+1
                    WHERE session_id=? AND organization_id=?""",
                    (
                        next_step_id if next_step_id is not None else step_id,
                        next_step_label if next_step_label is not None else step_label,
                        "in_progress" if protocol_start else row["status"],
                        now,
                        session_id,
                        principal.organization_id,
                    ),
                )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise WorkspaceConflictError(
                "Experiment step was already completed."
            ) from exc
        except Exception:
            self._connection.rollback()
            raise
        return self.get_experiment(principal, session_id)

    @staticmethod
    def _capture_allowed(row: sqlite3.Row) -> None:
        if row["status"] not in {"in_progress", "paused", "blocked"}:
            raise WorkspaceConflictError(
                "Observations and evidence require a started experiment."
            )

    def _require_experiment_step(
        self, row: sqlite3.Row, step_id: str
    ) -> tuple[str, str | None]:
        step_id = _identifier(step_id, "Protocol step identifier")
        if row["current_step_id"] == step_id:
            return step_id, row["current_step_label"]
        completed = self._connection.execute(
            """SELECT step_label FROM experiment_completed_steps
            WHERE session_id=? AND step_id=?""",
            (row["session_id"], step_id),
        ).fetchone()
        if completed is None:
            raise WorkspaceConflictError(
                "Capture can only reference the current or a completed step."
            )
        return step_id, completed["step_label"]

    def record_observation(
        self,
        principal: Principal,
        session_id: str,
        *,
        event_key: str,
        content: str,
        category: str,
        capture_source: str,
        protocol_step_id: str | None = None,
    ) -> dict[str, object]:
        """Persist researcher wording without changing approved knowledge."""

        row = self._experiment_row(principal, session_id, write=True)
        self._capture_allowed(row)
        event_key = _identifier(event_key, "Observation idempotency key")
        content = _text(content, "Observation content", maximum=4000)
        if category not in _OBSERVATION_CATEGORIES:
            raise WorkspaceError("Observation category is invalid.")
        if capture_source not in {"voice", "manual"}:
            raise WorkspaceError("Observation capture source is invalid.")
        selected_step = protocol_step_id or row["current_step_id"]
        if not isinstance(selected_step, str):
            raise WorkspaceConflictError(
                "Observation capture requires an authoritative protocol step."
            )
        step_id, step_label = self._require_experiment_step(row, selected_step)
        observation_id = "observation-" + hashlib.sha256(
            f"{principal.organization_id}:{session_id}:{event_key}".encode("utf-8")
        ).hexdigest()[:32]
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing_event = self._connection.execute(
                """SELECT event_type,payload_json FROM experiment_session_events
                WHERE session_id=? AND event_key=?""",
                (session_id, event_key),
            ).fetchone()
            existing = self._connection.execute(
                "SELECT * FROM experiment_observations WHERE observation_id=?",
                (observation_id,),
            ).fetchone()
            if existing_event is not None:
                event_payload = json.loads(existing_event["payload_json"])
                if (
                    existing_event["event_type"] != "observation_recorded"
                    or event_payload.get("observation_id") != observation_id
                    or existing is None
                    or existing["session_id"] != session_id
                    or existing["protocol_step_id"] != step_id
                    or existing["content"] != content
                    or existing["category"] != category
                    or existing["capture_source"] != capture_source
                ):
                    raise WorkspaceConflictError(
                        "Observation idempotency key was reused with different content."
                    )
            elif existing is not None:
                raise WorkspaceConflictError(
                    "Observation record is missing its append-only event."
                )
            else:
                self._connection.execute(
                    """INSERT INTO experiment_observations(
                    observation_id,organization_id,session_id,protocol_step_id,
                    protocol_step_label,author_principal_id,content,category,
                    capture_source,knowledge_effect,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,'observation_only',?)""",
                    (
                        observation_id,
                        principal.organization_id,
                        session_id,
                        step_id,
                        step_label,
                        principal.principal_id,
                        content,
                        category,
                        capture_source,
                        now,
                    ),
                )
                self._append_experiment_event(
                    principal,
                    session_id=session_id,
                    event_key=event_key,
                    event_type="observation_recorded",
                    step_id=step_id,
                    step_label=step_label,
                    payload={
                        "observation_id": observation_id,
                        "category": category,
                        "capture_source": capture_source,
                        "knowledge_effect": "observation_only",
                    },
                    created_at=now,
                )
                self._connection.execute(
                    """UPDATE experiment_sessions SET updated_at=?,version=version+1
                    WHERE session_id=? AND organization_id=?""",
                    (now, session_id, principal.organization_id),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        stored = self._connection.execute(
            "SELECT * FROM experiment_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        assert stored is not None
        return dict(stored)

    def record_evidence(
        self,
        principal: Principal,
        session_id: str,
        *,
        event_key: str,
        evidence_kind: str,
        original_filename: str,
        media_type: str,
        byte_size: int,
        sha256: str,
        storage_reference: str,
        caption: str | None = None,
        protocol_step_id: str | None = None,
    ) -> dict[str, object]:
        """Attach opaque file metadata without interpreting scientific content."""

        row = self._experiment_row(principal, session_id, write=True)
        self._capture_allowed(row)
        event_key = _identifier(event_key, "Evidence idempotency key")
        if evidence_kind not in _EVIDENCE_KINDS:
            raise WorkspaceError("Evidence kind is invalid.")
        filename = _text(original_filename, "Evidence filename", maximum=255)
        if (
            Path(filename).name != filename
            or any(ord(character) < 32 for character in filename)
        ):
            raise WorkspaceError("Evidence filename is invalid.")
        media_type = _text(media_type, "Evidence media type", maximum=200)
        allowed_media = {
            "image": {"image/jpeg", "image/png", "image/webp"},
            "document": {
                "application/pdf",
                "text/plain",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            },
        }
        if media_type not in allowed_media[evidence_kind]:
            raise WorkspaceError("Evidence media type is invalid.")
        if (
            not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or not 0 < byte_size <= 32 * 1024 * 1024
        ):
            raise WorkspaceError("Evidence size is outside allowed bounds.")
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise WorkspaceError("Evidence checksum is invalid.")
        storage_reference = _text(
            storage_reference, "Evidence storage reference", maximum=1000
        )
        if (
            storage_reference.startswith("/")
            or ".." in Path(storage_reference).parts
            or "\\" in storage_reference
        ):
            raise WorkspaceError("Evidence storage reference is invalid.")
        if caption is not None:
            caption = _text(caption, "Evidence caption", maximum=1000)
        selected_step = protocol_step_id or row["current_step_id"]
        if not isinstance(selected_step, str):
            raise WorkspaceConflictError(
                "Evidence capture requires an authoritative protocol step."
            )
        step_id, step_label = self._require_experiment_step(row, selected_step)
        evidence_id = "evidence-" + hashlib.sha256(
            f"{principal.organization_id}:{session_id}:{event_key}".encode("utf-8")
        ).hexdigest()[:32]
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing_event = self._connection.execute(
                """SELECT event_type,payload_json FROM experiment_session_events
                WHERE session_id=? AND event_key=?""",
                (session_id, event_key),
            ).fetchone()
            existing = self._connection.execute(
                "SELECT * FROM experiment_evidence WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
            if existing_event is not None:
                event_payload = json.loads(existing_event["payload_json"])
                expected = (
                    session_id,
                    step_id,
                    evidence_kind,
                    filename,
                    media_type,
                    byte_size,
                    sha256,
                    storage_reference,
                    caption,
                )
                actual = (
                    tuple(
                        existing[key]
                        for key in (
                            "session_id",
                            "protocol_step_id",
                            "evidence_kind",
                            "original_filename",
                            "media_type",
                            "byte_size",
                            "sha256",
                            "storage_reference",
                            "caption",
                        )
                    )
                    if existing is not None else None
                )
                if (
                    existing_event["event_type"] != "evidence_attached"
                    or event_payload.get("evidence_id") != evidence_id
                    or existing is None
                    or actual != expected
                ):
                    raise WorkspaceConflictError(
                        "Evidence idempotency key was reused with different metadata."
                    )
            elif existing is not None:
                raise WorkspaceConflictError(
                    "Evidence record is missing its append-only event."
                )
            else:
                self._connection.execute(
                    """INSERT INTO experiment_evidence(
                    evidence_id,organization_id,session_id,protocol_step_id,
                    protocol_step_label,uploader_principal_id,evidence_kind,
                    original_filename,media_type,byte_size,sha256,
                    storage_reference,caption,interpretation_status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'not_interpreted',?)""",
                    (
                        evidence_id,
                        principal.organization_id,
                        session_id,
                        step_id,
                        step_label,
                        principal.principal_id,
                        evidence_kind,
                        filename,
                        media_type,
                        byte_size,
                        sha256,
                        storage_reference,
                        caption,
                        now,
                    ),
                )
                self._append_experiment_event(
                    principal,
                    session_id=session_id,
                    event_key=event_key,
                    event_type="evidence_attached",
                    step_id=step_id,
                    step_label=step_label,
                    payload={
                        "evidence_id": evidence_id,
                        "evidence_kind": evidence_kind,
                        "interpretation_status": "not_interpreted",
                    },
                    created_at=now,
                )
                self._connection.execute(
                    """UPDATE experiment_sessions SET updated_at=?,version=version+1
                    WHERE session_id=? AND organization_id=?""",
                    (now, session_id, principal.organization_id),
                )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise WorkspaceConflictError(
                "Evidence is already attached to this protocol step."
            ) from exc
        except Exception:
            self._connection.rollback()
            raise
        stored = self._connection.execute(
            "SELECT * FROM experiment_evidence WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
        assert stored is not None
        return dict(stored)

    def record_experiment_review_action(
        self,
        principal: Principal,
        session_id: str,
        *,
        event_key: str,
        action: str,
        comment: str,
    ) -> dict[str, object]:
        require_permission(principal, Permission.PROTOCOL_REVIEW)
        row = self._experiment_row(principal, session_id)
        event_key = _identifier(event_key, "Reviewer action idempotency key")
        action = _identifier(action, "Review action")
        if action not in {
            "review_requested",
            "reviewed",
            "flagged",
            "commented",
            "acknowledged",
        }:
            raise WorkspaceError("Experiment reviewer action is invalid.")
        comment = _text(comment, "Review comment", maximum=4000)
        payload = {"action": action, "comment": comment}
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                """SELECT event_type,payload_json FROM experiment_session_events
                WHERE session_id=? AND event_key=?""",
                (session_id, event_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"] != "reviewer_action"
                    or json.loads(existing["payload_json"]) != payload
                ):
                    raise WorkspaceConflictError(
                        "Reviewer idempotency key was reused with different content."
                    )
            else:
                self._append_experiment_event(
                    principal,
                    session_id=session_id,
                    event_key=event_key,
                    event_type="reviewer_action",
                    step_id=row["current_step_id"],
                    step_label=row["current_step_label"],
                    payload=payload,
                    created_at=now,
                )
                self._connection.execute(
                    """UPDATE experiment_sessions SET updated_at=?,version=version+1
                    WHERE session_id=? AND organization_id=?""",
                    (now, session_id, principal.organization_id),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.get_experiment(principal, session_id)

    def experiment_timeline(
        self, principal: Principal, session_id: str
    ) -> dict[str, object]:
        session = self.get_experiment(principal, session_id)
        observations = {
            row["observation_id"]: dict(row)
            for row in self._connection.execute(
                """SELECT * FROM experiment_observations WHERE session_id=?
                ORDER BY created_at,observation_id""",
                (session_id,),
            ).fetchall()
        }
        evidence = {
            row["evidence_id"]: {
                key: row[key]
                for key in (
                    "evidence_id",
                    "protocol_step_id",
                    "protocol_step_label",
                    "uploader_principal_id",
                    "evidence_kind",
                    "original_filename",
                    "media_type",
                    "byte_size",
                    "sha256",
                    "caption",
                    "interpretation_status",
                    "created_at",
                )
            }
            for row in self._connection.execute(
                """SELECT * FROM experiment_evidence WHERE session_id=?
                ORDER BY created_at,evidence_id""",
                (session_id,),
            ).fetchall()
        }
        timeline = []
        for event in session["events"]:
            payload = event.get("payload") or {}
            item = dict(event)
            observation_id = payload.get("observation_id")
            evidence_id = payload.get("evidence_id")
            if isinstance(observation_id, str):
                item["observation"] = observations.get(observation_id)
            if isinstance(evidence_id, str):
                item["evidence"] = evidence.get(evidence_id)
            timeline.append(item)
        return {
            "session": {
                key: session[key]
                for key in (
                    "session_id",
                    "protocol_id",
                    "protocol_revision_id",
                    "status",
                    "current_step_id",
                    "current_step_label",
                    "version",
                    "started_at",
                    "paused_at",
                    "ended_at",
                    "updated_at",
                )
            },
            "timeline": timeline,
            "observation_count": len(observations),
            "evidence_count": len(evidence),
            "separation": {
                "observations_are_instructions": False,
                "evidence_autonomously_interpreted": False,
                "approved_protocol_knowledge_unchanged": True,
            },
        }

    def create_protocol_family(
        self, principal: Principal, *, title: str, family_id: str | None = None
    ) -> ProtocolFamily:
        require_permission(principal, Permission.PROTOCOL_IMPORT)
        self.verify_membership(principal)
        selected = family_id or f"family-{secrets.token_hex(12)}"
        _identifier(selected, "Protocol family identifier")
        now = _now()
        try:
            self._connection.execute(
                "INSERT INTO protocol_families VALUES(?,?,?,?,?)",
                (
                    selected,
                    principal.organization_id,
                    _text(title, "Protocol title", maximum=500),
                    principal.principal_id,
                    now,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            raise WorkspaceConflictError("Protocol family already exists.") from exc
        return ProtocolFamily(
            selected,
            principal.organization_id,
            title.strip(),
            principal.principal_id,
            now,
        )

    def _family_row(self, principal: Principal, family_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM protocol_families WHERE family_id=?", (family_id,)
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Protocol family is not available.")
        return row

    def register_source(
        self,
        principal: Principal,
        *,
        connector_kind: str,
        external_id: str,
        version_identity: str,
        source_hash: str,
        canonical_url: str | None,
        metadata: Mapping[str, object],
    ) -> ProtocolSource:
        require_permission(principal, Permission.PROTOCOL_IMPORT)
        self.verify_membership(principal)
        if connector_kind not in {"local_pdf", *_CONNECTOR_KINDS}:
            raise WorkspaceError("Protocol source connector is unsupported.")
        if _SHA256.fullmatch(source_hash) is None:
            raise WorkspaceError("Protocol source hash is invalid.")
        external_id = _text(external_id, "External source identifier", maximum=1000)
        version_identity = _text(version_identity, "Source version", maximum=500)
        metadata_json = _canonical_json(dict(metadata))
        identity = hashlib.sha256(
            "\x1f".join(
                (
                    principal.organization_id,
                    connector_kind,
                    external_id,
                    version_identity,
                    source_hash,
                )
            ).encode("utf-8")
        ).hexdigest()[:32]
        source_id = f"source-{identity}"
        now = _now()
        self._connection.execute(
            "INSERT OR IGNORE INTO protocol_sources VALUES(?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                principal.organization_id,
                connector_kind,
                external_id,
                version_identity,
                source_hash,
                canonical_url,
                metadata_json,
                now,
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM protocol_sources WHERE source_id=?", (source_id,)
        ).fetchone()
        return self._source(row)

    @staticmethod
    def _source(row: sqlite3.Row) -> ProtocolSource:
        return ProtocolSource(
            source_id=row["source_id"],
            organization_id=row["organization_id"],
            connector_kind=row["connector_kind"],
            external_id=row["external_id"],
            version_identity=row["version_identity"],
            source_hash=row["source_hash"],
            canonical_url=row["canonical_url"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
        )

    def add_protocol_revision(
        self,
        principal: Principal,
        *,
        family_id: str,
        source_id: str,
        content: Mapping[str, object],
        change_summary: str,
        parent_revision_id: str | None = None,
        language: str = "en",
        translation_status: str = "original",
        _adaptation: Mapping[str, object] | None = None,
    ) -> ProtocolLineageRevision:
        require_permission(principal, Permission.PROTOCOL_IMPORT)
        family = self._family_row(principal, family_id)
        source = self._connection.execute(
            "SELECT * FROM protocol_sources WHERE source_id=?", (source_id,)
        ).fetchone()
        if source is None or source["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Protocol source is not available.")
        if translation_status not in {"original", "machine", "reviewed"}:
            raise WorkspaceError("Translation status is invalid.")
        parent_number = 0
        if parent_revision_id is not None:
            parent = self._connection.execute(
                "SELECT * FROM protocol_lineage_revisions WHERE revision_id=?",
                (parent_revision_id,),
            ).fetchone()
            if (
                parent is None
                or parent["organization_id"] != principal.organization_id
                or parent["family_id"] != family_id
            ):
                raise WorkspaceNotFoundError("Parent revision is not available.")
            parent_number = int(parent["revision_number"])
        current = self._connection.execute(
            "SELECT COALESCE(MAX(revision_number),0) FROM protocol_lineage_revisions WHERE family_id=?",
            (family_id,),
        ).fetchone()[0]
        if parent_revision_id is not None and parent_number != int(current):
            raise WorkspaceConflictError("Parent revision is stale.")
        revision_number = int(current) + 1
        content_json = _canonical_json(dict(content))
        content_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()
        revision_id = f"revision-{hashlib.sha256(f'{family_id}:{revision_number}:{content_hash}'.encode()).hexdigest()[:32]}"
        now = _now()
        adaptation_id = None
        adaptation_changes_json = None
        if _adaptation is not None:
            base_revision_id = _adaptation.get("base_revision_id")
            changes = _adaptation.get("changes")
            if (
                not isinstance(base_revision_id, str)
                or base_revision_id != parent_revision_id
                or not isinstance(changes, list)
                or not changes
            ):
                raise WorkspaceError("Lab adaptation metadata is invalid.")
            adaptation_changes_json = _canonical_json(changes)
            adaptation_id = "adaptation-" + hashlib.sha256(
                f"{family_id}:{base_revision_id}:{revision_id}".encode("utf-8")
            ).hexdigest()[:32]
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "INSERT INTO protocol_lineage_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    family_id,
                    family["organization_id"],
                    revision_number,
                    parent_revision_id,
                    source_id,
                    principal.principal_id,
                    now,
                    _text(change_summary, "Change summary", maximum=2000),
                    content_hash,
                    source["source_hash"],
                    language,
                    translation_status,
                    content_json,
                ),
            )
            if adaptation_id is not None:
                assert adaptation_changes_json is not None
                self._connection.execute(
                    """INSERT INTO protocol_adaptation_revisions(
                    adaptation_id,organization_id,family_id,base_revision_id,
                    adapted_revision_id,author_principal_id,changes_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        adaptation_id,
                        principal.organization_id,
                        family_id,
                        parent_revision_id,
                        revision_id,
                        principal.principal_id,
                        adaptation_changes_json,
                        now,
                    ),
                )
            change_kind = "new" if revision_number == 1 else "changed"
            item_id = f"inbox-{hashlib.sha256(revision_id.encode()).hexdigest()[:32]}"
            self._connection.execute(
                "INSERT INTO source_inbox VALUES(?,?,?,?,?,?,?)",
                (
                    item_id,
                    principal.organization_id,
                    source_id,
                    revision_id,
                    change_kind,
                    "unread",
                    now,
                ),
            )
            self._connection.commit()
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise WorkspaceConflictError("Protocol revision could not be created.") from exc
        return self.get_revision(principal, revision_id)

    def get_revision(
        self, principal: Principal, revision_id: str
    ) -> ProtocolLineageRevision:
        require_permission(principal, Permission.PROTOCOL_READ)
        row = self._connection.execute(
            "SELECT * FROM protocol_lineage_revisions WHERE revision_id=?",
            (revision_id,),
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Protocol revision is not available.")
        return ProtocolLineageRevision(
            revision_id=row["revision_id"],
            family_id=row["family_id"],
            organization_id=row["organization_id"],
            revision_number=row["revision_number"],
            parent_revision_id=row["parent_revision_id"],
            source_id=row["source_id"],
            author_principal_id=row["author_principal_id"],
            created_at=row["created_at"],
            change_summary=row["change_summary"],
            content_hash=row["content_hash"],
            source_hash=row["source_hash"],
            language=row["language"],
            translation_status=row["translation_status"],
            content=json.loads(row["content_json"]),
        )

    def revision_diff(
        self, principal: Principal, revision_id: str
    ) -> dict[str, object]:
        revision = self.get_revision(principal, revision_id)
        if revision.parent_revision_id is None:
            before: dict[str, object] = {}
        else:
            before = self.get_revision(principal, revision.parent_revision_id).content
        before_lines = json.dumps(
            before, ensure_ascii=False, sort_keys=True, indent=2
        ).splitlines()
        after_lines = json.dumps(
            revision.content, ensure_ascii=False, sort_keys=True, indent=2
        ).splitlines()
        lines = tuple(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=revision.parent_revision_id or "empty",
                tofile=revision.revision_id,
                lineterm="",
            )
        )
        truncated = len(lines) > 500
        return {
            "revision_id": revision.revision_id,
            "parent_revision_id": revision.parent_revision_id,
            "lines": list(lines[:500]),
            "truncated": truncated,
        }

    @staticmethod
    def _normalize_adaptation_changes(
        changes: tuple[Mapping[str, object], ...],
    ) -> tuple[dict[str, object], ...]:
        if not isinstance(changes, tuple) or not 1 <= len(changes) <= 50:
            raise WorkspaceError("Lab adaptation changes are invalid.")
        allowed_fields = {
            "kind",
            "protocol_step_id",
            "summary",
            "rationale",
            "original_value",
            "adapted_value",
        }
        normalized: list[dict[str, object]] = []
        for raw in changes:
            if not isinstance(raw, Mapping) or set(raw) - allowed_fields:
                raise WorkspaceError("Lab adaptation change is invalid.")
            kind = raw.get("kind")
            if kind not in _ADAPTATION_KINDS:
                raise WorkspaceError("Lab adaptation kind is invalid.")
            step_id = raw.get("protocol_step_id")
            if not isinstance(step_id, str):
                raise WorkspaceError("Lab adaptation protocol step is required.")
            step_id = _identifier(step_id, "Adaptation protocol step")
            summary = _text(raw.get("summary"), "Adaptation summary", maximum=1000)
            rationale = _text(
                raw.get("rationale"), "Adaptation rationale", maximum=2000
            )
            original = raw.get("original_value")
            adapted = raw.get("adapted_value")
            if adapted is None:
                raise WorkspaceError("Adaptation value is required.")
            adapted = _text(adapted, "Adapted value", maximum=4000)
            if original is not None:
                original = _text(original, "Original value", maximum=4000)
            if kind in {"equipment_difference", "reagent_substitution"}:
                if original is None or original.casefold() == adapted.casefold():
                    raise WorkspaceError(
                        "Equipment and reagent adaptations require a real before/after change."
                    )
            normalized.append(
                {
                    "kind":kind,
                    "protocol_step_id":step_id,
                    "summary":summary,
                    "rationale":rationale,
                    "original_value":original,
                    "adapted_value":adapted,
                }
            )
        return tuple(normalized)

    def create_lab_adaptation(
        self,
        principal: Principal,
        *,
        base_revision_id: str,
        changes: tuple[Mapping[str, object], ...],
        change_summary: str,
    ) -> dict[str, object]:
        """Create a review-required immutable child without editing its source."""

        require_permission(principal, Permission.PROTOCOL_IMPORT)
        base = self.get_revision(principal, base_revision_id)
        base_state = self.revision_operational_state(
            principal,base_revision_id
        )["state"]
        if base_state in {"rejected", "revoked"}:
            raise WorkspaceConflictError(
                "A rejected or revoked revision cannot be adapted."
            )
        nested = self._connection.execute(
            """SELECT 1 FROM protocol_adaptation_revisions
            WHERE adapted_revision_id=?""",
            (base_revision_id,),
        ).fetchone()
        if nested is not None:
            raise WorkspaceConflictError(
                "Create a new adaptation from the original protocol revision."
            )
        normalized = self._normalize_adaptation_changes(changes)
        content = json.loads(_canonical_json(base.content))
        if "lab_adaptation" in content:
            raise WorkspaceConflictError(
                "The base revision already contains adaptation metadata."
            )
        content["lab_adaptation"] = {
            "base_revision_id":base_revision_id,
            "review_state":"review_required",
            "changes":list(normalized),
            "approved_protocol_knowledge_unchanged":True,
        }
        revision = self.add_protocol_revision(
            principal,
            family_id=base.family_id,
            source_id=base.source_id,
            content=content,
            change_summary=change_summary,
            parent_revision_id=base_revision_id,
            language=base.language,
            translation_status=base.translation_status,
            _adaptation={
                "base_revision_id":base_revision_id,
                "changes":list(normalized),
            },
        )
        return self.lab_adaptation(principal,revision.revision_id)

    def lab_adaptation(
        self, principal: Principal, adapted_revision_id: str
    ) -> dict[str, object]:
        revision = self.get_revision(principal,adapted_revision_id)
        row = self._connection.execute(
            """SELECT * FROM protocol_adaptation_revisions
            WHERE adapted_revision_id=? AND organization_id=?""",
            (adapted_revision_id,principal.organization_id),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError("Lab adaptation is not available.")
        operational = self.revision_operational_state(
            principal,adapted_revision_id
        )
        return {
            "adaptation_id":row["adaptation_id"],
            "organization_id":row["organization_id"],
            "family_id":row["family_id"],
            "base_revision_id":row["base_revision_id"],
            "adapted_revision_id":row["adapted_revision_id"],
            "author_principal_id":row["author_principal_id"],
            "changes":json.loads(row["changes_json"]),
            "created_at":row["created_at"],
            "review_state":operational["state"],
            "executable":operational["available_for_new_operational_sessions"],
            "immutable":True,
            "original_protocol_unchanged":True,
            "revision_number":revision.revision_number,
        }

    def list_lab_adaptations(
        self, principal: Principal
    ) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.PROTOCOL_READ)
        rows = self._connection.execute(
            """SELECT adapted_revision_id FROM protocol_adaptation_revisions
            WHERE organization_id=? ORDER BY created_at DESC,adaptation_id DESC""",
            (principal.organization_id,),
        ).fetchall()
        return tuple(
            self.lab_adaptation(principal,row["adapted_revision_id"])
            for row in rows
        )

    def latest_revision_for_source(
        self,
        principal: Principal,
        *,
        connector_kind: str,
        external_id: str,
    ) -> ProtocolLineageRevision | None:
        """Resolve only the caller tenant's latest lineage for a source identity."""

        require_permission(principal, Permission.PROTOCOL_READ)
        row = self._connection.execute(
            """SELECT r.revision_id FROM protocol_lineage_revisions r
            JOIN protocol_sources s ON s.source_id=r.source_id
            WHERE r.organization_id=? AND s.connector_kind=? AND s.external_id=?
            ORDER BY r.created_at DESC, r.revision_number DESC LIMIT 1""",
            (principal.organization_id, connector_kind, external_id),
        ).fetchone()
        return self.get_revision(principal, row[0]) if row is not None else None

    def source_for_revision(
        self, principal: Principal, revision_id: str
    ) -> ProtocolSource:
        revision = self.get_revision(principal, revision_id)
        row = self._connection.execute(
            "SELECT * FROM protocol_sources WHERE source_id=?",
            (revision.source_id,),
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Protocol source is not available.")
        return self._source(row)

    def source_inbox(self, principal: Principal) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.PROTOCOL_REVIEW)
        rows = self._connection.execute(
            """SELECT i.item_id,i.change_kind,i.status,i.created_at,
            s.connector_kind,s.external_id,s.version_identity,s.canonical_url,
            r.revision_id,r.family_id,r.change_summary
            FROM source_inbox i
            JOIN protocol_sources s ON s.source_id=i.source_id
            LEFT JOIN protocol_lineage_revisions r ON r.revision_id=i.revision_id
            WHERE i.organization_id=? ORDER BY i.created_at DESC""",
            (principal.organization_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def set_inbox_status(
        self, principal: Principal, item_id: str, *, status: str
    ) -> None:
        require_permission(principal, Permission.PROTOCOL_REVIEW)
        if status not in {"unread", "reviewing", "resolved"}:
            raise WorkspaceError("Inbox status is invalid.")
        cursor = self._connection.execute(
            "UPDATE source_inbox SET status=? WHERE item_id=? AND organization_id=?",
            (status, item_id, principal.organization_id),
        )
        if cursor.rowcount != 1:
            self._connection.rollback()
            raise WorkspaceNotFoundError("Inbox item is not available.")
        self._connection.commit()

    def set_protocol_preference(
        self,
        principal: Principal,
        family_id: str,
        *,
        favorite: bool,
        tags: tuple[str, ...] = (),
    ) -> None:
        self._family_row(principal, family_id)
        if len(tags) > 20 or any(
            not isinstance(tag, str) or not tag.strip() or len(tag) > 50
            for tag in tags
        ):
            raise WorkspaceError("Protocol tags are invalid.")
        self._connection.execute(
            """INSERT INTO protocol_library_preferences
            (organization_id,principal_id,family_id,favorite,last_opened_at,tags_json)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(organization_id,principal_id,family_id) DO UPDATE SET
            favorite=excluded.favorite,last_opened_at=excluded.last_opened_at,
            tags_json=excluded.tags_json""",
            (
                principal.organization_id,
                principal.principal_id,
                family_id,
                int(favorite),
                _now(),
                _canonical_json(sorted(set(tag.strip() for tag in tags))),
            ),
        )
        self._connection.commit()

    def protocol_library(
        self, principal: Principal, *, search: str = ""
    ) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.PROTOCOL_READ)
        search = search.strip()
        if len(search) > 200:
            raise WorkspaceError("Protocol search is invalid.")
        rows = self._connection.execute(
            """SELECT f.family_id,f.title,f.owner_principal_id,
            r.revision_id,r.revision_number,r.created_at,r.translation_status,
            s.connector_kind,s.version_identity,s.metadata_json,
            COALESCE(p.favorite,0) AS favorite,p.last_opened_at,p.tags_json,
            (SELECT a.action FROM protocol_approval_events a
             WHERE a.revision_id=r.revision_id ORDER BY a.sequence_id DESC LIMIT 1)
             AS approval_state,
            (SELECT a.actor_principal_id FROM protocol_approval_events a
             WHERE a.revision_id=r.revision_id ORDER BY a.sequence_id DESC LIMIT 1)
             AS reviewer_principal_id
            FROM protocol_families f
            JOIN protocol_lineage_revisions r ON r.family_id=f.family_id
            JOIN protocol_sources s ON s.source_id=r.source_id
            LEFT JOIN protocol_library_preferences p
              ON p.organization_id=f.organization_id
             AND p.principal_id=? AND p.family_id=f.family_id
            WHERE f.organization_id=?
              AND r.revision_number=(SELECT MAX(r2.revision_number)
                FROM protocol_lineage_revisions r2 WHERE r2.family_id=f.family_id)
              AND (?='' OR lower(f.title) LIKE '%'||lower(?)||'%')
            ORDER BY favorite DESC,COALESCE(p.last_opened_at,r.created_at) DESC""",
            (
                principal.principal_id,
                principal.organization_id,
                search,
                search,
            ),
        ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            metadata = json.loads(item.pop("metadata_json"))
            item["favorite"] = bool(item["favorite"])
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            item["source_status"] = metadata.get("source_status")
            item["owner"] = metadata.get("owner") or item["owner_principal_id"]
            item["department"] = metadata.get("department")
            item["risk_state"] = metadata.get("risk_state", "review_required")
            item["catalog_protocol_id"] = metadata.get("catalog_protocol_id")
            item["executable"] = item["approval_state"] == "approved"
            item["quick_link"] = f"/workspace/researcher?protocol={item['family_id']}"
            items.append(item)
        return tuple(items)

    def add_translation(
        self,
        principal: Principal,
        *,
        revision_id: str,
        language: str,
        original_text: str,
        translated_text: str,
        status: str = "machine",
    ) -> str:
        require_permission(principal, Permission.PROTOCOL_REVIEW)
        self.get_revision(principal, revision_id)
        if status not in {"machine", "reviewed"}:
            raise WorkspaceError("Translation status is invalid.")
        source_tokens = tuple(
            re.sub(r"\s+", "", item.casefold())
            for item in _SCIENTIFIC_TOKEN.findall(original_text)
        )
        translated_tokens = tuple(
            re.sub(r"\s+", "", item.casefold())
            for item in _SCIENTIFIC_TOKEN.findall(translated_text)
        )
        if sorted(source_tokens) != sorted(translated_tokens):
            raise TranslationIntegrityError(
                "Translation changed one or more scientific numeric values."
            )
        content_hash = hashlib.sha256(translated_text.encode("utf-8")).hexdigest()
        translation_id = f"translation-{hashlib.sha256(f'{revision_id}:{language}:{content_hash}'.encode()).hexdigest()[:32]}"
        self._connection.execute(
            "INSERT OR IGNORE INTO protocol_translations VALUES(?,?,?,?,?,?,?,?,?)",
            (
                translation_id,
                principal.organization_id,
                revision_id,
                language,
                status,
                translated_text,
                content_hash,
                principal.principal_id,
                _now(),
            ),
        )
        self._connection.commit()
        return translation_id

    def record_approval(
        self,
        principal: Principal,
        *,
        revision_id: str,
        action: str,
        comment: str,
        idempotency_key: str,
        replacement_revision_id: str | None = None,
    ) -> ApprovalEvent:
        permission = (
            Permission.PROTOCOL_REVOKE
            if action == "revoked"
            else Permission.PROTOCOL_APPROVE
        )
        require_permission(principal, permission)
        revision = self.get_revision(principal, revision_id)
        if action not in {"approved", "rejected", "revoked"}:
            raise WorkspaceError("Approval action is invalid.")
        comment = _text(comment, "Approval comment", maximum=4000)
        idempotency_key = _identifier(idempotency_key, "Idempotency key")
        if replacement_revision_id is not None:
            replacement = self.get_revision(principal, replacement_revision_id)
            if replacement.family_id != revision.family_id:
                raise WorkspaceConflictError("Replacement belongs to another family.")
        source = self._connection.execute(
            """SELECT s.metadata_json FROM protocol_sources s
            JOIN protocol_lineage_revisions r ON r.source_id=s.source_id
            WHERE r.revision_id=?""",
            (revision_id,),
        ).fetchone()
        metadata = json.loads(source[0]) if source is not None else {}
        source_status = str(metadata.get("source_status", "")).casefold()
        adaptation = self._connection.execute(
            """SELECT 1 FROM protocol_adaptation_revisions
            WHERE adapted_revision_id=? AND organization_id=?""",
            (revision_id,principal.organization_id),
        ).fetchone()
        if action == "approved" and source_status in {
            "in development",
            "development",
            "draft",
        } and adaptation is None:
            raise WorkspaceConflictError(
                "An in-development source must be adapted into a reviewed lab revision before operational approval."
            )
        role = next(
            role
            for role in (
                Role.ORGANIZATION_ADMIN,
                Role.LAB_ADMIN,
                Role.REVIEWER,
            )
            if role in principal.roles
        )
        approval_id = f"approval-{secrets.token_hex(16)}"
        now = _now()
        try:
            cursor = self._connection.execute(
                """INSERT INTO protocol_approval_events
                (approval_id,idempotency_key,organization_id,revision_id,action,
                 actor_principal_id,actor_role,comment,replacement_revision_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    approval_id,
                    idempotency_key,
                    principal.organization_id,
                    revision_id,
                    action,
                    principal.principal_id,
                    role.value,
                    comment,
                    replacement_revision_id,
                    now,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ApprovalReplayError("Approval request was already used.") from exc
        return ApprovalEvent(
            cursor.lastrowid,
            approval_id,
            principal.organization_id,
            revision_id,
            action,
            principal.principal_id,
            role.value,
            comment,
            replacement_revision_id,
            now,
        )

    def approval_history(
        self, principal: Principal, revision_id: str
    ) -> tuple[ApprovalEvent, ...]:
        self.get_revision(principal, revision_id)
        rows = self._connection.execute(
            "SELECT * FROM protocol_approval_events WHERE revision_id=? ORDER BY sequence_id",
            (revision_id,),
        ).fetchall()
        return tuple(
            ApprovalEvent(
                row["sequence_id"],
                row["approval_id"],
                row["organization_id"],
                row["revision_id"],
                row["action"],
                row["actor_principal_id"],
                row["actor_role"],
                row["comment"],
                row["replacement_revision_id"],
                row["created_at"],
            )
            for row in rows
        )

    def revision_operational_state(
        self, principal: Principal, revision_id: str
    ) -> dict[str, object]:
        history = self.approval_history(principal, revision_id)
        state = "review_required"
        replacement = None
        if history:
            latest = history[-1]
            state = latest.action
            replacement = latest.replacement_revision_id
        return {
            "revision_id": revision_id,
            "state": state,
            "available_for_new_operational_sessions": state == "approved",
            "replacement_revision_id": replacement,
            "history": [event.__dict__ for event in history],
        }

    def configure_connector(
        self,
        principal: Principal,
        *,
        connector_kind: str,
        display_name: str,
        credential_reference: str,
        allowed_roots: tuple[str, ...],
        webhook_secret_reference: str | None = None,
        enabled: bool = True,
    ) -> ConnectorConfiguration:
        require_permission(principal, Permission.CONNECTOR_MANAGE)
        self.verify_membership(principal)
        if connector_kind not in _CONNECTOR_KINDS:
            raise WorkspaceError("Connector kind is unsupported.")
        if not credential_reference.startswith("secret://"):
            raise WorkspaceError("Only a server-side secret reference may be stored.")
        if webhook_secret_reference is not None:
            if connector_kind != "github" or not webhook_secret_reference.startswith(
                "secret://"
            ):
                raise WorkspaceError("Webhook secret reference is invalid.")
        if not allowed_roots or len(allowed_roots) > 50:
            raise WorkspaceError("Connector allowed roots are invalid.")
        connector_id = f"connector-{secrets.token_hex(12)}"
        now = _now()
        self._connection.execute(
            "INSERT INTO connector_configurations VALUES(?,?,?,?,?,?,?,?,?)",
            (
                connector_id,
                principal.organization_id,
                connector_kind,
                _text(display_name, "Connector name", maximum=200),
                credential_reference,
                webhook_secret_reference,
                _canonical_json(list(allowed_roots)),
                int(enabled),
                now,
            ),
        )
        self._connection.commit()
        return ConnectorConfiguration(
            connector_id,
            principal.organization_id,
            connector_kind,
            display_name,
            credential_reference,
            webhook_secret_reference,
            allowed_roots,
            enabled,
            now,
        )

    def get_connector(
        self, principal: Principal, connector_id: str
    ) -> ConnectorConfiguration:
        require_permission(principal, Permission.CONNECTOR_READ)
        row = self._connection.execute(
            "SELECT * FROM connector_configurations WHERE connector_id=?",
            (connector_id,),
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Connector is not available.")
        return ConnectorConfiguration(
            row["connector_id"],
            row["organization_id"],
            row["connector_kind"],
            row["display_name"],
            row["credential_reference"],
            row["webhook_secret_reference"],
            tuple(json.loads(row["allowed_roots_json"])),
            bool(row["enabled"]),
            row["created_at"],
        )

    def connector_for_use(
        self,
        principal: Principal,
        connector_id: str,
        *,
        expected_kind: str,
    ) -> ConnectorConfiguration:
        require_permission(principal, Permission.PROTOCOL_IMPORT)
        row = self._connection.execute(
            "SELECT * FROM connector_configurations WHERE connector_id=?",
            (connector_id,),
        ).fetchone()
        if (
            row is None
            or row["organization_id"] != principal.organization_id
            or row["connector_kind"] != expected_kind
            or not row["enabled"]
        ):
            raise WorkspaceNotFoundError("Enabled connector is not available.")
        return ConnectorConfiguration(
            row["connector_id"],
            row["organization_id"],
            row["connector_kind"],
            row["display_name"],
            row["credential_reference"],
            row["webhook_secret_reference"],
            tuple(json.loads(row["allowed_roots_json"])),
            bool(row["enabled"]),
            row["created_at"],
        )

    def connector_summaries(
        self, principal: Principal
    ) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.CONNECTOR_READ)
        rows = self._connection.execute(
            """SELECT connector_id,connector_kind,display_name,allowed_roots_json,
            enabled,created_at FROM connector_configurations
            WHERE organization_id=? ORDER BY display_name""",
            (principal.organization_id,),
        ).fetchall()
        return tuple(
            {
                "connector_id": row["connector_id"],
                "connector_kind": row["connector_kind"],
                "display_name": row["display_name"],
                "allowed_roots": json.loads(row["allowed_roots_json"]),
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "credential_configured": True,
            }
            for row in rows
        )

    def connector_cursor(
        self, principal: Principal, connector_id: str, *, cursor_kind: str
    ) -> str | None:
        self.get_connector(principal, connector_id)
        row = self._connection.execute(
            """SELECT opaque_cursor FROM connector_sync_state
            WHERE organization_id=? AND connector_id=? AND cursor_kind=?""",
            (
                principal.organization_id,
                connector_id,
                _identifier(cursor_kind, "Connector cursor kind"),
            ),
        ).fetchone()
        return str(row["opaque_cursor"]) if row is not None else None

    def set_connector_cursor(
        self,
        principal: Principal,
        connector_id: str,
        *,
        cursor_kind: str,
        opaque_cursor: str,
    ) -> None:
        self.get_connector(principal, connector_id)
        cursor_kind = _identifier(cursor_kind, "Connector cursor kind")
        opaque_cursor = _text(opaque_cursor, "Connector cursor", maximum=2000)
        self._connection.execute(
            """INSERT INTO connector_sync_state VALUES(?,?,?,?,?)
            ON CONFLICT(organization_id,connector_id,cursor_kind)
            DO UPDATE SET opaque_cursor=excluded.opaque_cursor,
                          updated_at=excluded.updated_at""",
            (
                principal.organization_id,
                connector_id,
                cursor_kind,
                opaque_cursor,
                _now(),
            ),
        )
        self._connection.commit()

    def github_webhook_configuration(
        self, connector_id: str
    ) -> tuple[str, ConnectorConfiguration]:
        """Server-only lookup; callers must authenticate the raw webhook body."""

        row = self._connection.execute(
            "SELECT * FROM connector_configurations WHERE connector_id=?",
            (_identifier(connector_id, "Connector identifier"),),
        ).fetchone()
        if (
            row is None
            or row["connector_kind"] != "github"
            or not row["enabled"]
            or not row["webhook_secret_reference"]
        ):
            raise WorkspaceNotFoundError("Webhook connector is not available.")
        return row["organization_id"], ConnectorConfiguration(
            row["connector_id"],
            row["organization_id"],
            row["connector_kind"],
            row["display_name"],
            row["credential_reference"],
            row["webhook_secret_reference"],
            tuple(json.loads(row["allowed_roots_json"])),
            bool(row["enabled"]),
            row["created_at"],
        )

    def begin_github_webhook_delivery(
        self,
        *,
        organization_id: str,
        connector_id: str,
        delivery_id: str,
        body_sha256: str,
        event_name: str,
    ) -> None:
        if _SHA256.fullmatch(body_sha256) is None:
            raise WorkspaceError("Webhook payload hash is invalid.")
        try:
            self._connection.execute(
                "INSERT INTO github_webhook_deliveries VALUES(?,?,?,?,?,?,?,?)",
                (
                    _identifier(organization_id, "Organization identifier"),
                    _identifier(connector_id, "Connector identifier"),
                    _identifier(delivery_id, "Webhook delivery identifier"),
                    body_sha256,
                    _identifier(event_name, "Webhook event name"),
                    "processing",
                    _now(),
                    None,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ApprovalReplayError("Webhook delivery was already processed.") from exc

    def finish_github_webhook_delivery(
        self, connector_id: str, delivery_id: str, *, succeeded: bool
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE github_webhook_deliveries SET status=?,completed_at=?
            WHERE connector_id=? AND delivery_id=? AND status='processing'""",
            (
                "completed" if succeeded else "failed",
                _now(),
                _identifier(connector_id, "Connector identifier"),
                _identifier(delivery_id, "Webhook delivery identifier"),
            ),
        )
        if cursor.rowcount != 1:
            raise WorkspaceConflictError("Webhook delivery state is unavailable.")
        self._connection.commit()

    def add_knowledge(
        self,
        principal: Principal,
        *,
        kind: str,
        body: str,
        provenance: Mapping[str, object],
        revision_id: str | None = None,
    ) -> str:
        require_permission(principal, Permission.KNOWLEDGE_WRITE)
        if kind not in _KNOWLEDGE_KINDS or kind == "approved_protocol_fact":
            raise WorkspaceError("Knowledge kind requires reviewer promotion.")
        if revision_id is not None:
            self.get_revision(principal, revision_id)
        knowledge_id = f"knowledge-{secrets.token_hex(16)}"
        self._connection.execute(
            "INSERT INTO knowledge_entries VALUES(?,?,?,?,?,?,?,?)",
            (
                knowledge_id,
                principal.organization_id,
                revision_id,
                kind,
                _text(body, "Knowledge body", maximum=20_000),
                _canonical_json(dict(provenance)),
                principal.principal_id,
                _now(),
            ),
        )
        self._connection.commit()
        return knowledge_id

    def promote_knowledge(
        self,
        principal: Principal,
        *,
        knowledge_id: str,
        comment: str,
    ) -> str:
        require_permission(principal, Permission.KNOWLEDGE_PROMOTE)
        row = self._connection.execute(
            "SELECT * FROM knowledge_entries WHERE knowledge_id=?",
            (knowledge_id,),
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Knowledge entry is not available.")
        promotion_id = f"promotion-{secrets.token_hex(16)}"
        self._connection.execute(
            """INSERT INTO knowledge_promotion_events
            (promotion_id,organization_id,knowledge_id,promoted_kind,
             actor_principal_id,comment,created_at) VALUES(?,?,?,?,?,?,?)""",
            (
                promotion_id,
                principal.organization_id,
                knowledge_id,
                "approved_protocol_fact",
                principal.principal_id,
                _text(comment, "Promotion comment", maximum=4000),
                _now(),
            ),
        )
        self._connection.commit()
        return promotion_id

    def knowledge_entries(
        self, principal: Principal
    ) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.PROTOCOL_READ)
        rows = self._connection.execute(
            """SELECT k.*,
            CASE WHEN EXISTS(SELECT 1 FROM knowledge_promotion_events p
              WHERE p.knowledge_id=k.knowledge_id)
              THEN 'approved_protocol_fact' ELSE k.kind END AS effective_kind
            FROM knowledge_entries k WHERE k.organization_id=?
            ORDER BY k.created_at DESC""",
            (principal.organization_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["provenance"] = json.loads(item.pop("provenance_json"))
            result.append(item)
        return tuple(result)

    def add_asset_card_version(
        self,
        principal: Principal,
        *,
        asset_id: str,
        asset_kind: str,
        name: str,
        location: Mapping[str, str],
        review_status: str = "draft",
        photo_url: str | None = None,
        barcode: str | None = None,
        sds_url: str | None = None,
    ) -> str:
        require_permission(principal, Permission.ASSET_MANAGE)
        if asset_kind not in {"reagent", "equipment"}:
            raise WorkspaceError("Asset kind is invalid.")
        if review_status not in {"draft", "reviewed"}:
            raise WorkspaceError("Asset review status is invalid.")
        allowed_location = {"building", "room", "storage", "shelf", "drawer"}
        if set(location) - allowed_location or not any(location.values()):
            raise WorkspaceError("Asset location is invalid.")
        version_id = f"asset-version-{secrets.token_hex(16)}"
        self._connection.execute(
            "INSERT INTO asset_card_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                version_id,
                principal.organization_id,
                _identifier(asset_id, "Asset identifier"),
                asset_kind,
                _text(name, "Asset name", maximum=500),
                _canonical_json(dict(location)),
                photo_url,
                barcode,
                sds_url,
                principal.principal_id,
                review_status,
                _now(),
            ),
        )
        self._connection.commit()
        return version_id

    def asset_cards(self, principal: Principal) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.ASSET_READ)
        rows = self._connection.execute(
            """SELECT a.* FROM asset_card_versions a
            WHERE a.organization_id=? AND a.created_at=(
              SELECT MAX(a2.created_at) FROM asset_card_versions a2
              WHERE a2.organization_id=a.organization_id AND a2.asset_id=a.asset_id)
            ORDER BY a.name""",
            (principal.organization_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["location"] = json.loads(item.pop("location_json"))
            result.append(item)
        return tuple(result)

    def asset_card_history(
        self, principal: Principal, asset_id: str
    ) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.ASSET_READ)
        rows = self._connection.execute(
            """SELECT * FROM asset_card_versions
            WHERE organization_id=? AND asset_id=? ORDER BY created_at,version_id""",
            (principal.organization_id, asset_id),
        ).fetchall()
        if not rows:
            raise WorkspaceNotFoundError("Asset card is not available.")
        result = []
        for row in rows:
            item = dict(row)
            item["location"] = json.loads(item.pop("location_json"))
            result.append(item)
        return tuple(result)

    def asset_card_diff(
        self, principal: Principal, asset_id: str
    ) -> dict[str, object]:
        history = self.asset_card_history(principal, asset_id)
        if len(history) < 2:
            return {"asset_id": asset_id, "from_version": None, "to_version": history[-1]["version_id"], "changes": {}}
        before, after = history[-2:]
        fields = ("name", "location", "photo_url", "barcode", "sds_url", "review_status")
        changes = {
            field: {"before": before[field], "after": after[field]}
            for field in fields
            if before[field] != after[field]
        }
        return {
            "asset_id": asset_id,
            "from_version": before["version_id"],
            "to_version": after["version_id"],
            "changes": changes,
        }

    def register_computational_workflow(
        self,
        principal: Principal,
        *,
        name: str,
        engine: str,
        repository: str,
        commit_sha: str,
        source_path: str,
        source_hash: str,
        metadata: Mapping[str, object],
    ) -> dict[str, object]:
        require_permission(principal, Permission.PROTOCOL_IMPORT)
        self.verify_membership(principal)
        if engine not in {"snakemake", "nextflow"}:
            raise WorkspaceError("Computational workflow engine is invalid.")
        if _SHA256.fullmatch(source_hash) is None:
            raise WorkspaceError("Computational workflow source hash is invalid.")
        if (
            metadata.get("engine") != engine
            or metadata.get("repository") != repository
            or metadata.get("commit_sha") != commit_sha
            or metadata.get("entry_point") != source_path
            or metadata.get("validation_state") != "metadata_only_unexecuted"
            or metadata.get("execution_supported") is not False
        ):
            raise WorkspaceError(
                "Computational workflow metadata boundary is invalid."
            )
        identity = hashlib.sha256(
            f"{principal.organization_id}:{engine}:{repository}:{source_path}".encode()
        ).hexdigest()[:32]
        family_id = f"workflow-family-{identity}"
        row = self._connection.execute(
            "SELECT * FROM computational_workflow_families WHERE workflow_family_id=?",
            (family_id,),
        ).fetchone()
        if row is not None and row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Computational workflow is not available.")
        now = _now()
        if row is None:
            self._connection.execute(
                "INSERT INTO computational_workflow_families VALUES(?,?,?,?,?)",
                (family_id, principal.organization_id, _text(name, "Workflow name", maximum=500), principal.principal_id, now),
            )
        latest = self._connection.execute(
            """SELECT * FROM computational_workflow_revisions
            WHERE workflow_family_id=? ORDER BY revision_number DESC LIMIT 1""",
            (family_id,),
        ).fetchone()
        if latest is not None and latest["source_hash"] == source_hash:
            self._connection.commit()
            return {**dict(latest), "metadata": json.loads(latest["metadata_json"]), "changed": False}
        revision_number = 1 if latest is None else int(latest["revision_number"]) + 1
        revision_id = f"workflow-revision-{hashlib.sha256(f'{family_id}:{revision_number}:{source_hash}'.encode()).hexdigest()[:32]}"
        self._connection.execute(
            """INSERT INTO computational_workflow_revisions VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                revision_id,
                family_id,
                principal.organization_id,
                revision_number,
                latest["workflow_revision_id"] if latest is not None else None,
                engine,
                _text(repository, "Workflow repository", maximum=300),
                _text(commit_sha, "Workflow commit", maximum=128),
                _text(source_path, "Workflow source path", maximum=1000),
                source_hash,
                _canonical_json(dict(metadata)),
                "review_required",
                None,
                None,
                None,
                now,
            ),
        )
        self._connection.commit()
        return {
            "workflow_revision_id": revision_id,
            "workflow_family_id": family_id,
            "organization_id": principal.organization_id,
            "revision_number": revision_number,
            "parent_revision_id": latest["workflow_revision_id"] if latest is not None else None,
            "engine": engine,
            "repository": repository,
            "commit_sha": commit_sha,
            "source_path": source_path,
            "source_hash": source_hash,
            "metadata": dict(metadata),
            "approval_state": "review_required",
            "created_at": now,
            "changed": True,
        }

    def review_computational_workflow(
        self,
        principal: Principal,
        workflow_revision_id: str,
        *,
        action: str,
        comment: str,
    ) -> dict[str, object]:
        require_permission(principal, Permission.PROTOCOL_APPROVE)
        if action not in {"approved", "revoked"}:
            raise WorkspaceError("Computational workflow review action is invalid.")
        row = self._connection.execute(
            "SELECT * FROM computational_workflow_revisions WHERE workflow_revision_id=?",
            (workflow_revision_id,),
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Computational workflow is not available.")
        current_state = row["approval_state"]
        if (
            action == "approved" and current_state != "review_required"
        ) or (action == "revoked" and current_state != "approved"):
            raise WorkspaceConflictError(
                "Computational workflow review transition is not allowed."
            )
        actor_role = next(
            role.value
            for role in (
                Role.ORGANIZATION_ADMIN,
                Role.LAB_ADMIN,
                Role.REVIEWER,
            )
            if role in principal.roles
        )
        reviewed_at = _now()
        self._connection.execute(
            """INSERT INTO computational_workflow_review_events
            (workflow_review_id,organization_id,workflow_revision_id,action,
            actor_principal_id,actor_role,comment,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                f"workflow-review-{secrets.token_hex(16)}",
                principal.organization_id,
                workflow_revision_id,
                action,
                principal.principal_id,
                actor_role,
                _text(comment, "Workflow review comment", maximum=4000),
                reviewed_at,
            ),
        )
        self._connection.execute(
            """UPDATE computational_workflow_revisions SET approval_state=?,
            reviewer_principal_id=?,review_comment=?,reviewed_at=?
            WHERE workflow_revision_id=? AND organization_id=?""",
            (
                action,
                principal.principal_id,
                _text(comment, "Workflow review comment", maximum=4000),
                reviewed_at,
                workflow_revision_id,
                principal.organization_id,
            ),
        )
        self._connection.commit()
        updated = self._connection.execute(
            "SELECT * FROM computational_workflow_revisions WHERE workflow_revision_id=?",
            (workflow_revision_id,),
        ).fetchone()
        return dict(updated)

    def computational_workflows(
        self, principal: Principal
    ) -> tuple[dict[str, object], ...]:
        require_permission(principal, Permission.PROTOCOL_READ)
        rows = self._connection.execute(
            """SELECT * FROM computational_workflow_revisions
            WHERE organization_id=?
            ORDER BY created_at DESC,workflow_revision_id""",
            (principal.organization_id,),
        ).fetchall()
        output=[]
        for row in rows:
            item=dict(row)
            item["metadata"]=json.loads(item.pop("metadata_json"))
            reviews=self._connection.execute(
                """SELECT action,actor_principal_id,actor_role,comment,created_at
                FROM computational_workflow_review_events
                WHERE workflow_revision_id=? ORDER BY sequence_id""",
                (row["workflow_revision_id"],),
            ).fetchall()
            item["review_history"]=[dict(review) for review in reviews]
            item["execution_supported"]=False
            output.append(item)
        return tuple(output)

    def link_wet_dry_workflow(
        self,
        principal: Principal,
        *,
        experiment_session_id: str,
        protocol_revision_id: str,
        workflow_revision_id: str,
    ) -> str:
        require_permission(principal, Permission.PROTOCOL_EXECUTE)
        experiment = self.get_experiment(principal, experiment_session_id)
        revision = self.get_revision(principal, protocol_revision_id)
        identity = revision.content.get("execution_identity")
        if (
            not isinstance(identity, dict)
            or identity.get("protocol_id") != experiment["protocol_id"]
            or identity.get("source_sha256") != revision.source_hash
        ):
            raise WorkspaceConflictError(
                "Experiment and wet-lab protocol lineage do not match."
            )
        lineage_catalog_revision = identity.get("catalog_revision_id")
        runtime_revision = experiment["protocol_revision_id"]
        if isinstance(lineage_catalog_revision, str) and not (
            runtime_revision == lineage_catalog_revision
            or runtime_revision.startswith(f"{lineage_catalog_revision}-analysis-")
        ):
            raise WorkspaceConflictError(
                "Experiment runtime revision does not match the wet-lab lineage."
            )
        workflow = self._connection.execute(
            "SELECT * FROM computational_workflow_revisions WHERE workflow_revision_id=?",
            (workflow_revision_id,),
        ).fetchone()
        if (
            workflow is None
            or workflow["organization_id"] != principal.organization_id
            or workflow["approval_state"] != "approved"
        ):
            raise WorkspaceNotFoundError("Approved computational workflow is not available.")
        workflow_metadata = json.loads(workflow["metadata_json"])
        if (
            workflow_metadata.get("execution_supported") is not False
            or workflow_metadata.get("validation_state")
            != "metadata_only_unexecuted"
        ):
            raise WorkspaceConflictError(
                "Computational workflow is not metadata-only."
            )
        link_id = f"wet-dry-link-{secrets.token_hex(16)}"
        try:
            self._connection.execute(
                "INSERT INTO wet_dry_workflow_links VALUES(?,?,?,?,?,?,?)",
                (
                    link_id,
                    principal.organization_id,
                    experiment_session_id,
                    protocol_revision_id,
                    workflow_revision_id,
                    principal.principal_id,
                    _now(),
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise WorkspaceConflictError(
                "Experiment and computational workflow are already linked."
            ) from exc
        return link_id

    def wet_dry_workflow_links(
        self,
        principal: Principal,
        *,
        experiment_session_id: str,
    ) -> tuple[dict[str, object], ...]:
        """Return metadata-only links for one visible durable experiment."""

        experiment = self.get_experiment(principal, experiment_session_id)
        rows = self._connection.execute(
            """SELECT link.link_id,link.experiment_session_id,
            link.protocol_revision_id,link.workflow_revision_id,
            link.actor_principal_id,link.created_at,workflow.engine,
            workflow.repository,workflow.commit_sha,workflow.source_path,
            workflow.source_hash,workflow.approval_state,workflow.metadata_json
            FROM wet_dry_workflow_links AS link
            JOIN computational_workflow_revisions AS workflow
              ON workflow.workflow_revision_id=link.workflow_revision_id
            WHERE link.organization_id=? AND link.experiment_session_id=?
            ORDER BY link.created_at,link.link_id""",
            (principal.organization_id, experiment_session_id),
        ).fetchall()
        return tuple(
            {
                **{
                    key: row[key]
                    for key in (
                        "link_id",
                        "experiment_session_id",
                        "protocol_revision_id",
                        "workflow_revision_id",
                        "actor_principal_id",
                        "created_at",
                        "engine",
                        "repository",
                        "commit_sha",
                        "source_path",
                        "source_hash",
                        "approval_state",
                    )
                },
                "experiment_protocol_id": experiment["protocol_id"],
                "experiment_runtime_revision_id": experiment[
                    "protocol_revision_id"
                ],
                "metadata": json.loads(row["metadata_json"]),
                "execution_supported": False,
                "execution_started": False,
            }
            for row in rows
        )

    def record_eln_writeback(
        self,
        principal: Principal,
        *,
        connector_id: str,
        experiment_session_id: str,
        report_id: str,
        protocol_revision_id: str,
        external_experiment_id: str,
        request_sha256: str,
        idempotency_key: str,
    ) -> str:
        require_permission(principal, Permission.ELN_WRITEBACK)
        connector = self._connection.execute(
            """SELECT connector_kind,enabled FROM connector_configurations
            WHERE connector_id=? AND organization_id=?""",
            (connector_id, principal.organization_id),
        ).fetchone()
        if connector is None:
            raise WorkspaceNotFoundError("Connector is not available.")
        if connector["connector_kind"] != "elabftw" or not connector["enabled"]:
            raise WorkspaceError("Connector does not support ELN write-back.")
        self._experiment_row(principal, experiment_session_id)
        self.require_resource(principal, "experiment_report", report_id)
        self.get_revision(principal, protocol_revision_id)
        if _SHA256.fullmatch(request_sha256) is None:
            raise WorkspaceError("ELN request hash is invalid.")
        writeback_id = f"eln-writeback-{secrets.token_hex(16)}"
        try:
            self._connection.execute(
                """INSERT INTO eln_writeback_events
                (writeback_id,idempotency_key,organization_id,connector_id,report_id,
                protocol_revision_id,external_experiment_id,request_sha256,
                actor_principal_id,created_at,experiment_session_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    writeback_id,
                    _identifier(idempotency_key, "Idempotency key"),
                    principal.organization_id,
                    connector_id,
                    _identifier(report_id, "Report identifier"),
                    protocol_revision_id,
                    _text(external_experiment_id, "ELN experiment identifier", maximum=500),
                    request_sha256,
                    principal.principal_id,
                    _now(),
                    _identifier(
                        experiment_session_id, "Experiment session identifier"
                    ),
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ApprovalReplayError("ELN write-back request was already used.") from exc
        return writeback_id

    def claim_eln_writeback_request(
        self,
        principal: Principal,
        *,
        connector_id: str,
        experiment_session_id: str,
        report_id: str,
        protocol_revision_id: str,
        idempotency_key: str,
    ) -> None:
        require_permission(principal, Permission.ELN_WRITEBACK)
        connector = self.connector_for_use(
            principal, connector_id, expected_kind="elabftw"
        )
        self._experiment_row(principal, experiment_session_id)
        self.require_resource(principal, "experiment_report", report_id)
        self.get_revision(principal, protocol_revision_id)
        try:
            self._connection.execute(
                """INSERT INTO eln_writeback_requests(
                organization_id,idempotency_key,connector_id,report_id,
                protocol_revision_id,actor_principal_id,status,created_at,
                completed_at,experiment_session_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    principal.organization_id,
                    _identifier(idempotency_key, "Idempotency key"),
                    connector.connector_id,
                    _identifier(report_id, "Report identifier"),
                    protocol_revision_id,
                    principal.principal_id,
                    "processing",
                    _now(),
                    None,
                    _identifier(
                        experiment_session_id, "Experiment session identifier"
                    ),
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ApprovalReplayError("ELN write-back request was already used.") from exc

    def finish_eln_writeback_request(
        self, principal: Principal, idempotency_key: str, *, succeeded: bool
    ) -> None:
        cursor = self._connection.execute(
            """UPDATE eln_writeback_requests SET status=?,completed_at=?
            WHERE organization_id=? AND idempotency_key=? AND status='processing'""",
            (
                "completed" if succeeded else "failed",
                _now(),
                principal.organization_id,
                _identifier(idempotency_key, "Idempotency key"),
            ),
        )
        if cursor.rowcount != 1:
            raise WorkspaceConflictError("ELN write-back request state is unavailable.")
        self._connection.commit()

    def record_analytics(
        self,
        principal: Principal,
        *,
        category: str,
        metric_name: str,
        metric_value: float = 1.0,
        dimensions: Mapping[str, str] | None = None,
    ) -> None:
        self.verify_membership(principal)
        if category not in _ANALYTICS_CATEGORIES:
            raise WorkspaceError("Analytics category is invalid.")
        metric_name = _identifier(metric_name, "Metric name")
        if not isinstance(metric_value, (int, float)) or isinstance(metric_value, bool):
            raise WorkspaceError("Metric value is invalid.")
        selected = dict(dimensions or {})
        if set(selected) - _ANALYTICS_DIMENSIONS or any(
            not isinstance(value, str) or len(value) > 100
            for value in selected.values()
        ):
            raise WorkspaceError("Analytics dimensions are not privacy-safe.")
        self._connection.execute(
            "INSERT INTO analytics_events(organization_id,category,metric_name,metric_value,dimensions_json,recorded_at) VALUES(?,?,?,?,?,?)",
            (
                principal.organization_id,
                category,
                metric_name,
                float(metric_value),
                _canonical_json(selected),
                _now(),
            ),
        )
        setting = self._connection.execute(
            """SELECT analytics_retention_days FROM organization_settings
            WHERE organization_id=?""",
            (principal.organization_id,),
        ).fetchone()
        retention_days = (
            int(setting["analytics_retention_days"])
            if setting is not None
            else self.default_analytics_retention_days
        )
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        self._connection.execute(
            "DELETE FROM analytics_events WHERE organization_id=? AND recorded_at<?",
            (principal.organization_id, cutoff),
        )
        self._connection.commit()

    def analytics_summary(self, principal: Principal) -> dict[str, object]:
        require_permission(principal, Permission.ANALYTICS_READ)
        rows = self._connection.execute(
            """SELECT category,metric_name,COUNT(*) AS samples,SUM(metric_value) AS total,
            AVG(metric_value) AS average FROM analytics_events
            WHERE organization_id=? GROUP BY category,metric_name
            ORDER BY category,metric_name""",
            (principal.organization_id,),
        ).fetchall()
        setting = self._connection.execute(
            """SELECT analytics_retention_days FROM organization_settings
            WHERE organization_id=?""",
            (principal.organization_id,),
        ).fetchone()
        return {
            "organization_id": principal.organization_id,
            "metrics": [dict(row) for row in rows],
            "analytics_retention_days": (
                int(setting["analytics_retention_days"])
                if setting is not None
                else self.default_analytics_retention_days
            ),
            "privacy": {
                "raw_audio": False,
                "transcripts": False,
                "model_reasoning": False,
                "secrets": False,
            },
        }

    def enforce_analytics_retention(
        self, principal: Principal, *, retention_days: int
    ) -> int:
        require_permission(principal, Permission.RETENTION_MANAGE)
        if retention_days < 1 or retention_days > 3650:
            raise WorkspaceError("Analytics retention is outside allowed bounds.")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        cursor = self._connection.execute(
            "DELETE FROM analytics_events WHERE organization_id=? AND recorded_at<?",
            (principal.organization_id, cutoff),
        )
        self._connection.commit()
        return cursor.rowcount

    def update_analytics_retention(
        self, principal: Principal, *, retention_days: int
    ) -> dict[str, int]:
        require_permission(principal, Permission.RETENTION_MANAGE)
        if retention_days < 1 or retention_days > 3650:
            raise WorkspaceError("Analytics retention is outside allowed bounds.")
        self._connection.execute(
            """INSERT INTO organization_settings VALUES(?,?,?,?)
            ON CONFLICT(organization_id) DO UPDATE SET
            analytics_retention_days=excluded.analytics_retention_days,
            updated_by_principal_id=excluded.updated_by_principal_id,
            updated_at=excluded.updated_at""",
            (
                principal.organization_id,
                retention_days,
                principal.principal_id,
                _now(),
            ),
        )
        self._connection.commit()
        deleted = self.enforce_analytics_retention(
            principal, retention_days=retention_days
        )
        return {"analytics_retention_days": retention_days, "events_purged": deleted}


def initialize_workspace_store(settings: WorkspaceSettings) -> WorkspaceStore:
    if not settings.enabled or settings.data_dir is None:
        raise WorkspaceError("Commercial workspace is disabled.")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / WORKSPACE_DATABASE_FILENAME
    new = not path.exists()
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    if new:
        connection.executescript(SCHEMA)
        connection.commit()
    row = connection.execute(
        "SELECT schema_version FROM schema_metadata"
    ).fetchone()
    if row is None:
        connection.close()
        raise WorkspaceError("Commercial workspace schema is unsupported.")
    version = int(row[0])
    if version == 1:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + MIGRATION_1_TO_2 + "\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            connection.rollback()
            connection.close()
            raise WorkspaceError(
                "Commercial workspace migration failed."
            ) from exc
        version = 2
    if version == 2:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + MIGRATION_2_TO_3 + "\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            connection.rollback()
            connection.close()
            raise WorkspaceError(
                "Commercial workspace migration failed."
            ) from exc
        version = 3
    if version == 3:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + MIGRATION_3_TO_4 + "\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            connection.rollback()
            connection.close()
            raise WorkspaceError(
                "Commercial workspace migration failed."
            ) from exc
        version = 4
    if version == 4:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + MIGRATION_4_TO_5 + "\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            connection.rollback()
            connection.close()
            raise WorkspaceError(
                "Commercial workspace migration failed."
            ) from exc
        version = 5
    if version != WORKSPACE_SCHEMA_VERSION:
        connection.close()
        raise WorkspaceError("Commercial workspace schema is unsupported.")
    return WorkspaceStore(
        connection,
        path,
        default_analytics_retention_days=settings.analytics_retention_days,
    )
