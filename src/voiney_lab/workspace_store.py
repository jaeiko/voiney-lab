"""Tenant-scoped commercial workspace records with append-only governance."""

from __future__ import annotations

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
from typing import Any, Iterable

from voiney_lab.identity import (
    AuthorizationDeniedError,
    Principal,
    require_same_tenant,
)


WORKSPACE_DATABASE_FILENAME = "commercial_workspace.sqlite"
WORKSPACE_SCHEMA_VERSION = 8
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
#: Events that move an experiment from one step to the next, or that carry a
#: completion over from the experiment a checkpoint session began from.
_STEP_PROGRESS_EVENTS = frozenset({
    "step_completed",
    "step_advanced",
    "step_completion_carried_over",
})
_OBSERVATION_CATEGORIES = {
    "note",
    "appearance",
    "measurement",
    "deviation",
    "other",
}
_EVIDENCE_KINDS = {"image", "document"}


class WorkspaceError(RuntimeError):
    code = "workspace_error"


class WorkspaceNotFoundError(WorkspaceError):
    code = "workspace_resource_not_found"


class WorkspaceConflictError(WorkspaceError):
    code = "workspace_conflict"


class ExperimentCheckpointUnavailableError(WorkspaceConflictError):
    code = "experiment_checkpoint_unavailable"


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
            "VOINEY_LAB_WORKSPACE_ENABLED", "false"
        ).strip().casefold() in {"1", "true", "yes", "on"}
        raw_dir = env.get("VOINEY_LAB_WORKSPACE_DATA_DIR", "").strip()
        data_dir = Path(raw_dir) if raw_dir else None
        if enabled and (data_dir is None or not data_dir.is_absolute()):
            raise WorkspaceError(
                "Workspace data directory must be an absolute path when enabled."
            )
        retention = int(
            env.get("VOINEY_LAB_ANALYTICS_RETENTION_DAYS", "90")
        )
        if retention < 1 or retention > 3650:
            raise WorkspaceError("Analytics retention is outside allowed bounds.")
        return cls(enabled, data_dir, retention)


@dataclass(frozen=True)
class FactTranslationRecord:
    """One sentence of one Protocol revision in another language.

    ``revision_id`` names the executable revision a session runs (a catalog
    revision or a development fixture revision), not a workspace lineage
    revision, so a changed revision never reuses another's sentences.
    ``source_sha256`` is the hash of the source sentence the translation was
    made from. ``check_result`` is ``passed`` or the reason the mechanical
    check refused it; a refused sentence is kept so it is not requested
    again, and is never shown.
    """

    revision_id: str
    fact_key: str
    language: str
    source_sha256: str
    translated_text: str
    status: str
    check_result: str
    model: str
    model_version: str
    created_at: str


@dataclass(frozen=True)
class TranslationGlossaryRecord:
    """The Korean form one revision's translation fixes for its words.

    ``entries_json`` is a list of {"source", "korean", "keep_english"}.
    One per revision and language: every sentence of the revision is
    translated with it.
    """

    revision_id: str
    language: str
    entries_json: str
    model: str
    model_version: str
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


MIGRATION_5_TO_6 = """
ALTER TABLE connector_configurations
 ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'untested'
 CHECK(validation_status IN ('untested','configuration_verified','failed'));
ALTER TABLE connector_configurations ADD COLUMN last_checked_at TEXT;
ALTER TABLE connector_configurations ADD COLUMN last_failure_code TEXT;
ALTER TABLE connector_configurations ADD COLUMN updated_at TEXT;
UPDATE connector_configurations SET updated_at=created_at WHERE updated_at IS NULL;

CREATE TABLE admin_audit_events(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 actor_principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 action TEXT NOT NULL,
 target_kind TEXT NOT NULL,
 target_id TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('success','failure')),
 reason_code TEXT,
 created_at TEXT NOT NULL
);
CREATE INDEX admin_audit_tenant_sequence
 ON admin_audit_events(organization_id,sequence_id DESC);
CREATE TRIGGER admin_audit_no_update BEFORE UPDATE ON admin_audit_events
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER admin_audit_no_delete BEFORE DELETE ON admin_audit_events
 BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE schema_metadata_next(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=6)
);
INSERT INTO schema_metadata_next(schema_version) VALUES(6);
DROP TABLE schema_metadata;
ALTER TABLE schema_metadata_next RENAME TO schema_metadata;
"""


MIGRATION_6_TO_7 = """
CREATE TABLE protocol_fact_translations(
 translation_id TEXT PRIMARY KEY,
 revision_id TEXT NOT NULL,
 fact_key TEXT NOT NULL,
 language TEXT NOT NULL,
 source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
 translated_text TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('machine','reviewed')),
 check_result TEXT NOT NULL,
 model TEXT NOT NULL,
 model_version TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(revision_id,fact_key,language,source_sha256,status)
);
CREATE INDEX protocol_fact_translations_revision
 ON protocol_fact_translations(revision_id,language);
CREATE TRIGGER fact_translations_no_update BEFORE UPDATE ON protocol_fact_translations
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER fact_translations_no_delete BEFORE DELETE ON protocol_fact_translations
 BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE protocol_translation_glossaries(
 glossary_id TEXT PRIMARY KEY,
 revision_id TEXT NOT NULL,
 language TEXT NOT NULL,
 entries_json TEXT NOT NULL,
 model TEXT NOT NULL,
 model_version TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(revision_id,language)
);
CREATE TRIGGER translation_glossaries_no_update BEFORE UPDATE ON protocol_translation_glossaries
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER translation_glossaries_no_delete BEFORE DELETE ON protocol_translation_glossaries
 BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE schema_metadata_next(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=7)
);
INSERT INTO schema_metadata_next(schema_version) VALUES(7);
DROP TABLE schema_metadata;
ALTER TABLE schema_metadata_next RENAME TO schema_metadata;
"""


#: Lane CF (2026-10-08): the experimenter's settings, one row per change,
#: never updated or deleted; the latest row of a name is its value. Written
#: to run on a store that already has the table (a fresh v8 store rebuilt as
#: an older one keeps it).
MIGRATION_7_TO_8 = """
CREATE TABLE IF NOT EXISTS experimenter_settings(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 organization_id TEXT NOT NULL REFERENCES organizations(organization_id),
 principal_id TEXT NOT NULL REFERENCES principals(principal_id),
 name TEXT NOT NULL CHECK(name IN ('confirm_mode','question_timing')),
 value TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('voice','screen')),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS experimenter_settings_principal
 ON experimenter_settings(organization_id,principal_id,name,sequence_id);
CREATE TRIGGER IF NOT EXISTS experimenter_settings_no_update BEFORE UPDATE ON experimenter_settings
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS experimenter_settings_no_delete BEFORE DELETE ON experimenter_settings
 BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE schema_metadata_next(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=8)
);
INSERT INTO schema_metadata_next(schema_version) VALUES(8);
DROP TABLE schema_metadata;
ALTER TABLE schema_metadata_next RENAME TO schema_metadata;
"""

#: The values each setting may take (lane CF, decisions 1 and 4).
EXPERIMENTER_SETTING_VALUES: dict[str, tuple[str, ...]] = {
    "confirm_mode": ("readback", "confirm", "quiet"),
    "question_timing": ("before_start", "during"),
}


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

    def _record_admin_audit(
        self,
        principal: Principal,
        *,
        action: str,
        target_kind: str,
        target_id: str,
        outcome: str = "success",
        reason_code: str | None = None,
        created_at: str | None = None,
    ) -> None:
        """Append one privacy-safe administrative control-plane event."""

        if outcome not in {"success", "failure"}:
            raise WorkspaceError("Administrative audit outcome is invalid.")
        self._connection.execute(
            """INSERT INTO admin_audit_events(
            event_id,organization_id,actor_principal_id,action,target_kind,
            target_id,outcome,reason_code,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                f"admin-audit-{secrets.token_hex(16)}",
                principal.organization_id,
                principal.principal_id,
                _identifier(action, "Administrative action"),
                _identifier(target_kind, "Administrative target kind"),
                _identifier(target_id, "Administrative target identifier"),
                outcome,
                (
                    _identifier(reason_code, "Administrative reason code")
                    if reason_code is not None
                    else None
                ),
                created_at or _now(),
            ),
        )

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

    def record_workspace_access(self, principal: Principal) -> None:
        """Record one authenticated workspace entry without request content."""

        self.verify_membership(principal)
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._record_admin_audit(
                principal,
                action="workspace.accessed",
                target_kind="workspace",
                target_id=principal.organization_id,
                created_at=now,
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def verify_membership(self, principal: Principal) -> None:
        rows = self._connection.execute(
            """SELECT role FROM memberships
            WHERE organization_id=? AND principal_id=? AND active=1""",
            (principal.organization_id, principal.principal_id),
        ).fetchall()
        # Lane DI (2026-10-08): an active membership row is the whole check;
        # its role text is history, not a permission.
        if not rows:
            raise AuthorizationDeniedError("No active tenant membership exists.")

    def effective_principal(self, principal: Principal) -> Principal:
        """The principal once an active, tenant-managed membership is confirmed."""

        self.verify_membership(principal)
        return principal

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
        del write  # every member of the tenant may read and write their own experiments
        self.verify_membership(principal)
        session_id = _identifier(session_id, "Experiment session identifier")
        row = self._connection.execute(
            "SELECT * FROM experiment_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Experiment session is not available.")
        if row["owner_principal_id"] != principal.principal_id:
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
        self.verify_membership(principal)
        clauses = ["organization_id=?"]
        parameters: list[object] = [principal.organization_id]
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
                **self._experiment_display_values(row),
            }
            for row in rows
        )

    def _experiment_display_values(self, row: sqlite3.Row) -> dict[str, object]:
        """What the screen names an experiment by (lane R6, decision 5).

        ``protocol_title`` is the protocol family's title where the revision
        is one of this workspace's (None otherwise; the server fills it from
        the protocol catalog). ``day_sequence`` is the experiment's place
        among the organization's experiments started the same UTC day
        (``day_sequence_date``), counted in start order: 1 for the first.
        """

        family = self._connection.execute(
            """SELECT f.title FROM protocol_lineage_revisions r
            JOIN protocol_families f ON f.family_id=r.family_id
            WHERE r.revision_id=? AND r.organization_id=?""",
            (row["protocol_revision_id"], row["organization_id"]),
        ).fetchone()
        day = str(row["started_at"])[:10]
        sequence = self._connection.execute(
            """SELECT COUNT(*) FROM experiment_sessions
            WHERE organization_id=? AND substr(started_at,1,10)=?
              AND (started_at<? OR (started_at=? AND session_id<=?))""",
            (row["organization_id"], day, row["started_at"], row["started_at"],
             row["session_id"]),
        ).fetchone()[0]
        return {
            "protocol_title": family["title"] if family is not None else None,
            "day_sequence": sequence,
            "day_sequence_date": day,
        }

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
        expected_version: int,
        expected_voice_connection_id: str | None = None,
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
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise WorkspaceError("Experiment version is invalid.")
        if expected_voice_connection_id is not None:
            expected_voice_connection_id = _identifier(
                expected_voice_connection_id,
                "Voice connection identifier",
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
            existing = self._connection.execute(
                """SELECT event_type,step_id,step_label,payload_json
                FROM experiment_session_events
                WHERE session_id=? AND event_key=?""",
                (session_id, event_key),
            ).fetchone()
            expected_payload = _canonical_json(dict(payload or {}))
            if existing is not None:
                if (
                    existing["event_type"] != event_type
                    or existing["step_id"] != step_id
                    or existing["step_label"] != step_label
                    or existing["payload_json"] != expected_payload
                ):
                    raise WorkspaceConflictError(
                        "Experiment progress idempotency key was reused with different content."
                    )
                self._connection.commit()
                return self.get_experiment(principal, session_id)
            row = self._connection.execute(
                """SELECT * FROM experiment_sessions
                WHERE session_id=? AND organization_id=?""",
                (session_id, principal.organization_id),
            ).fetchone()
            assert row is not None
            current_version = int(row["version"])
            if current_version != expected_version:
                same_voice_projection = bool(
                    expected_voice_connection_id is not None
                    and row["last_voice_connection_id"]
                    == expected_voice_connection_id
                    and row["current_step_id"] == step_id
                )
                if not same_voice_projection:
                    raise WorkspaceConflictError(
                        "Experiment session changed; refresh before recording progress."
                    )
            protocol_start = (
                event_type == "protocol_started" and row["status"] == "ready"
            )
            if row["status"] != "in_progress" and not protocol_start:
                raise WorkspaceConflictError(
                    "Only an in-progress experiment can record step progress."
                )
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
            assert inserted
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
            cursor = self._connection.execute(
                """UPDATE experiment_sessions SET current_step_id=?,
                current_step_label=?,status=?,updated_at=?,version=version+1
                WHERE session_id=? AND organization_id=? AND version=?""",
                (
                    next_step_id if next_step_id is not None else step_id,
                    next_step_label if next_step_label is not None else step_label,
                    "in_progress" if protocol_start else row["status"],
                    now,
                    session_id,
                    principal.organization_id,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                raise WorkspaceConflictError(
                    "Experiment session changed; refresh before recording progress."
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

    def record_observation_fix(
        self,
        principal: Principal,
        session_id: str,
        *,
        event_key: str,
        target_event_key: str,
        content: str | None,
        capture_source: str,
    ) -> dict[str, object]:
        """Append a correction (``content``) or a withdrawal (None) of an observation.

        Lane N, decision 2. The observation and the event that recorded it are
        never changed: the correction is its own event, and the timeline shows
        the latest words with the first ones kept. Allowed in any status of
        the experiment, since a correction is part of the record rather than
        new work at the bench (the report's values are confirmed after the
        experiment ends).
        """

        row = self._experiment_row(principal, session_id, write=True)
        event_key = _identifier(event_key, "Observation correction idempotency key")
        target_event_key = _identifier(target_event_key, "Observation event key")
        if capture_source not in {"voice", "manual"}:
            raise WorkspaceError("Observation capture source is invalid.")
        if content is not None:
            content = _text(content, "Observation content", maximum=4000)
        target = self._connection.execute(
            """SELECT event_type,step_id,step_label,payload_json
            FROM experiment_session_events WHERE session_id=? AND event_key=?""",
            (row["session_id"], target_event_key),
        ).fetchone()
        observation_id = (
            json.loads(target["payload_json"]).get("observation_id")
            if target is not None and target["event_type"] == "observation_recorded"
            else None
        )
        if not isinstance(observation_id, str):
            raise WorkspaceNotFoundError("The observation to correct is not available.")
        event_type = "observation_retracted" if content is None else "observation_corrected"
        payload = {
            "observation_id": observation_id,
            "content": content,
            "capture_source": capture_source,
            "knowledge_effect": "observation_only",
        }
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                """SELECT event_type,payload_json FROM experiment_session_events
                WHERE session_id=? AND event_key=?""",
                (row["session_id"], event_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"] != event_type
                    or json.loads(existing["payload_json"]) != payload
                ):
                    raise WorkspaceConflictError(
                        "Observation correction key was reused with different content."
                    )
            else:
                self._append_experiment_event(
                    principal,
                    session_id=row["session_id"],
                    event_key=event_key,
                    event_type=event_type,
                    step_id=target["step_id"],
                    step_label=target["step_label"],
                    payload=payload,
                    created_at=now,
                )
                self._connection.execute(
                    """UPDATE experiment_sessions SET updated_at=?,version=version+1
                    WHERE session_id=? AND organization_id=?""",
                    (now, row["session_id"], principal.organization_id),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return {"event_key": event_key, "event_type": event_type, **payload}

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

    def evidence_for_download(
        self, principal: Principal, session_id: str, evidence_id: str
    ) -> dict[str, object]:
        """Return tenant-scoped evidence metadata including its server-only path."""

        self._experiment_row(principal, session_id)
        selected_id = _identifier(evidence_id, "Evidence identifier")
        row = self._connection.execute(
            """SELECT evidence_id,session_id,evidence_kind,original_filename,
            media_type,byte_size,sha256,storage_reference,interpretation_status,
            created_at FROM experiment_evidence
            WHERE evidence_id=? AND session_id=? AND organization_id=?""",
            (selected_id, session_id, principal.organization_id),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFoundError("Experiment evidence is not available.")
        return dict(row)

    @staticmethod
    def _experiment_checkpoints(session: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
        """The places a stopped experiment can be continued from.

        After each completed step the experiment stood at the next one, which
        is the step the following progress event names -- or, after the last
        completion, the step it was stopped at. A place is offered only while
        every move before it completed its step, so a session started there
        carries exactly the steps before it; restore_experiment_progress
        checks that again against the exact protocol revision.
        """

        if session.get("status") != "stopped":
            return ()
        # Lane N, decision 9: a run opened with a later start carries the
        # steps it skipped to every place it can be continued from.
        skipped: list[str] = []
        for event in session.get("events", ()):
            record = (event.get("payload") or {}).get("step_record")
            if isinstance(record, Mapping) and record.get("kind") == "start_at_step":
                skipped = [str(item) for item in record.get("skipped_step_ids") or ()]
                break
        completions = {
            item["event_id"]: item for item in session.get("completed_steps", ())
        }
        carried: tuple[Mapping[str, Any], ...] = ()
        places: list[dict[str, object]] = []

        def place(step_id: object, step_label: object, *, stopped_here: bool) -> None:
            if not isinstance(step_id, str) or any(
                item["step_id"] == step_id for item in carried
            ):
                return
            places.append({
                "step_id": step_id,
                "step_label": step_label,
                "carried_step_count": len(carried),
                "carried_step_ids": [item["step_id"] for item in carried],
                "last_carried_step_label": carried[-1]["step_label"],
                "last_carried_completed_at": carried[-1]["completed_at"],
                "stopped_here": stopped_here,
                "skipped_step_ids": list(skipped),
            })

        waiting = False
        for event in session.get("events", ()):
            if event.get("event_type") not in _STEP_PROGRESS_EVENTS:
                continue
            if waiting:
                place(event.get("step_id"), event.get("step_label"), stopped_here=False)
                waiting = False
            completed = completions.get(event.get("event_id"))
            if completed is None:
                # It moved on without completing this step: any later place
                # would carry over a step that was never done.
                break
            carried = (*carried, completed)
            waiting = True
        else:
            if waiting:
                place(
                    session.get("current_step_id"),
                    session.get("current_step_label"),
                    stopped_here=True,
                )
        return tuple(places)

    def restart_experiment_from_checkpoint(
        self,
        principal: Principal,
        session_id: str,
        *,
        checkpoint_step_id: str,
        expected_version: int,
    ) -> dict[str, object]:
        """Start a new experiment where a stopped one stood at a checkpoint.

        The stopped experiment stays stopped and keeps its record and report.
        The new session is bound to the same exact protocol revision, carries
        the steps completed before the checkpoint -- each pointing back at the
        completion it came from, with its original time and person -- and
        waits paused, so the voice session continues it at the checkpoint
        through the ordinary recovery path. Only the experiment's owner can
        continue it.
        """

        row = self._experiment_row(principal, session_id, write=True)
        if row["owner_principal_id"] != principal.principal_id:
            raise WorkspaceNotFoundError("Experiment session is not available.")
        checkpoint_step_id = _identifier(
            checkpoint_step_id, "Checkpoint step identifier"
        )
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise WorkspaceError("Experiment version is invalid.")
        source = self.get_experiment(principal, session_id)
        if source["status"] != "stopped":
            raise ExperimentCheckpointUnavailableError(
                "Only a stopped experiment is continued from a checkpoint."
            )
        if int(source["version"]) != expected_version:
            raise WorkspaceConflictError(
                "Experiment session changed; refresh before continuing it."
            )
        checkpoint = next(
            (
                item for item in self._experiment_checkpoints(source)
                if item["step_id"] == checkpoint_step_id
            ),
            None,
        )
        if checkpoint is None:
            raise ExperimentCheckpointUnavailableError(
                "That checkpoint is not available for this experiment."
            )
        carried = [
            item for step_id in checkpoint["carried_step_ids"]
            for item in source["completed_steps"] if item["step_id"] == step_id
        ]
        new_id = f"experiment-{secrets.token_hex(16)}"
        step_label = checkpoint["step_label"]
        now = _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                """INSERT INTO experiment_sessions(
                session_id,organization_id,owner_principal_id,protocol_id,
                protocol_revision_id,status,current_step_id,current_step_label,
                version,started_at,paused_at,ended_at,updated_at,
                last_voice_connection_id
                ) VALUES(?,?,?,?,?,'paused',?,?,1,?,?,NULL,?,NULL)""",
                (
                    new_id,
                    principal.organization_id,
                    principal.principal_id,
                    source["protocol_id"],
                    source["protocol_revision_id"],
                    checkpoint_step_id,
                    step_label,
                    now,
                    now,
                    now,
                ),
            )
            self._connection.execute(
                "INSERT INTO resource_bindings VALUES(?,?,?,?,?)",
                (
                    principal.organization_id,
                    "experiment_session",
                    new_id,
                    principal.principal_id,
                    now,
                ),
            )
            self._append_experiment_event(
                principal,
                session_id=new_id,
                event_key="session-started",
                event_type="session_started",
                step_id=checkpoint_step_id,
                step_label=step_label,
                payload={
                    "protocol_id": source["protocol_id"],
                    "protocol_revision_id": source["protocol_revision_id"],
                    "voice_bound": False,
                },
                created_at=now,
            )
            self._append_experiment_event(
                principal,
                session_id=new_id,
                event_key="checkpoint-restart",
                event_type="session_restarted_from_checkpoint",
                step_id=checkpoint_step_id,
                step_label=step_label,
                payload={
                    "source_session_id": session_id,
                    "source_version": expected_version,
                    "carried_step_count": len(carried),
                },
                created_at=now,
            )
            start = next(
                (
                    (event.get("payload") or {}).get("step_record")
                    for event in source["events"]
                    if isinstance((event.get("payload") or {}).get("step_record"), Mapping)
                    and (event.get("payload") or {})["step_record"].get("kind") == "start_at_step"
                ),
                None,
            )
            if start is not None:
                # Lane N, decision 9: the later start the run was opened with
                # holds for the new session too, so its recovery accepts the
                # skipped steps as it does the carried completions.
                self._append_experiment_event(
                    principal,
                    session_id=new_id,
                    event_key="skipped-steps-carried",
                    event_type="steps_skipped_carried_over",
                    step_id=checkpoint_step_id,
                    step_label=step_label,
                    payload={
                        "source_session_id": session_id,
                        "step_record": {
                            "kind": "start_at_step",
                            "start_step": start.get("start_step"),
                            "skipped_step_labels": list(start.get("skipped_step_labels") or ()),
                            "skipped_step_ids": list(start.get("skipped_step_ids") or ()),
                        },
                    },
                    created_at=now,
                )
            for index, item in enumerate(carried, 1):
                event_id, _ = self._append_experiment_event(
                    principal,
                    session_id=new_id,
                    event_key=f"carried-step-{index}",
                    event_type="step_completion_carried_over",
                    step_id=item["step_id"],
                    step_label=item["step_label"],
                    payload={
                        "source_session_id": session_id,
                        "source_event_id": item["event_id"],
                        "source_completed_at": item["completed_at"],
                        "source_completed_by_principal_id": item[
                            "completed_by_principal_id"
                        ],
                    },
                    created_at=now,
                )
                self._connection.execute(
                    """INSERT INTO experiment_completed_steps(
                    organization_id,session_id,step_id,step_label,
                    completed_by_principal_id,completed_at,event_id
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        principal.organization_id,
                        new_id,
                        item["step_id"],
                        item["step_label"],
                        item["completed_by_principal_id"],
                        item["completed_at"],
                        event_id,
                    ),
                )
            self._append_experiment_event(
                principal,
                session_id=session_id,
                event_key=f"checkpoint-restart-{new_id}",
                event_type="checkpoint_restart_created",
                step_id=checkpoint_step_id,
                step_label=step_label,
                payload={
                    "new_session_id": new_id,
                    "carried_step_count": len(carried),
                },
                created_at=now,
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.get_experiment(principal, new_id)

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
        # Lane N, decision 2: an observation shows its latest words, and
        # whether they were corrected or withdrawn; the first words stay.
        for event in session["events"]:
            if event.get("event_type") not in {
                "observation_corrected", "observation_retracted",
            }:
                continue
            payload = event.get("payload") or {}
            observation = observations.get(str(payload.get("observation_id")))
            if observation is None:
                continue
            if event["event_type"] == "observation_retracted":
                observation["retracted"] = True
            else:
                observation["current_content"] = payload.get("content")
                observation["corrected"] = True
                observation["retracted"] = False
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
        recovery_events = tuple(
            event
            for event in timeline
            if event.get("event_type") in {"session_recovered", "session_resumed"}
        )
        status = str(session["status"])
        checkpoints = (
            self._experiment_checkpoints(session)
            if session["owner_principal_id"] == principal.principal_id
            else ()
        )
        recovery = {
            "eligible": status in {"ready", "in_progress", "paused", "blocked"},
            "last_event_type": (
                recovery_events[-1]["event_type"] if recovery_events else None
            ),
            "restored": {
                "protocol_id": session["protocol_id"],
                "protocol_revision_id": session["protocol_revision_id"],
                "current_step_id": session["current_step_id"],
                "current_step_label": session["current_step_label"],
                "completed_step_count": len(session["completed_steps"]),
            },
            "not_restored": [
                "pending_confirmations",
                "conversation_history",
                "active_timers",
            ],
            "next_action": (
                "resume_voice_session"
                if status in {"ready", "in_progress", "paused", "blocked"}
                else "restart_from_checkpoint"
                if checkpoints
                else "start_new_experiment"
            ),
        }
        if status == "stopped":
            recovery["checkpoints"] = list(checkpoints)
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
            "recovery": recovery,
            "separation": {
                "observations_are_instructions": False,
                "evidence_autonomously_interpreted": False,
                "approved_protocol_knowledge_unchanged": True,
            },
        }

    def _family_row(self, principal: Principal, family_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM protocol_families WHERE family_id=?", (family_id,)
        ).fetchone()
        if row is None or row["organization_id"] != principal.organization_id:
            raise WorkspaceNotFoundError("Protocol family is not available.")
        return row

    def record_fact_translations(
        self, records: Iterable[FactTranslationRecord]
    ) -> int:
        """Append sentence translations; a sentence already held is kept as is.

        Service-level, like the protocol catalog the revisions come from: the
        rows are derived from a revision's own text at activation, not
        entered by a tenant member. Returns how many rows were new.
        """

        rows = []
        for record in records:
            if record.status not in {"machine", "reviewed"}:
                raise WorkspaceError("Translation status is invalid.")
            if _SHA256.fullmatch(record.source_sha256) is None:
                raise WorkspaceError("Translation source hash is invalid.")
            for value, label in (
                (record.revision_id, "Translation revision"),
                (record.fact_key, "Translation fact"),
                (record.language, "Translation language"),
                (record.check_result, "Translation check"),
                (record.model, "Translation model"),
                (record.model_version, "Translation model version"),
                (record.created_at, "Translation time"),
            ):
                if not isinstance(value, str) or not value.strip() or len(value) > 400:
                    raise WorkspaceError(f"{label} is invalid.")
            if not isinstance(record.translated_text, str) or len(
                record.translated_text
            ) > 20000:
                raise WorkspaceError("Translation text is invalid.")
            identity = (
                f"{record.revision_id}:{record.fact_key}:{record.language}:"
                f"{record.source_sha256}:{record.status}"
            )
            rows.append((
                "fact-translation-"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
                record.revision_id, record.fact_key, record.language,
                record.source_sha256, record.translated_text, record.status,
                record.check_result, record.model, record.model_version,
                record.created_at,
            ))
        before = self._connection.total_changes
        try:
            self._connection.executemany(
                "INSERT OR IGNORE INTO protocol_fact_translations"
                "(translation_id,revision_id,fact_key,language,source_sha256,"
                "translated_text,status,check_result,model,model_version,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self._connection.total_changes - before

    def fact_translations(
        self, revision_id: str, language: str
    ) -> tuple[FactTranslationRecord, ...]:
        """Every stored sentence translation of one revision, oldest first."""

        return tuple(
            FactTranslationRecord(
                revision_id=row["revision_id"],
                fact_key=row["fact_key"],
                language=row["language"],
                source_sha256=row["source_sha256"],
                translated_text=row["translated_text"],
                status=row["status"],
                check_result=row["check_result"],
                model=row["model"],
                model_version=row["model_version"],
                created_at=row["created_at"],
            )
            for row in self._connection.execute(
                "SELECT * FROM protocol_fact_translations "
                "WHERE revision_id=? AND language=? ORDER BY created_at,rowid",
                (revision_id, language),
            )
        )

    def record_translation_glossary(self, record: TranslationGlossaryRecord) -> bool:
        """Keep a revision's glossary; one already held is kept as is."""

        for value, label in (
            (record.revision_id, "Glossary revision"),
            (record.language, "Glossary language"),
            (record.model, "Glossary model"),
            (record.model_version, "Glossary model version"),
            (record.created_at, "Glossary time"),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > 400:
                raise WorkspaceError(f"{label} is invalid.")
        try:
            entries = json.loads(record.entries_json)
        except (TypeError, ValueError) as exc:
            raise WorkspaceError("Glossary entries are invalid.") from exc
        if not isinstance(entries, list) or len(record.entries_json) > 200000:
            raise WorkspaceError("Glossary entries are invalid.")
        glossary_id = "glossary-" + hashlib.sha256(
            f"{record.revision_id}:{record.language}".encode("utf-8")
        ).hexdigest()[:32]
        before = self._connection.total_changes
        try:
            self._connection.execute(
                "INSERT OR IGNORE INTO protocol_translation_glossaries"
                "(glossary_id,revision_id,language,entries_json,model,"
                "model_version,created_at) VALUES(?,?,?,?,?,?,?)",
                (glossary_id, record.revision_id, record.language,
                 record.entries_json, record.model, record.model_version,
                 record.created_at),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self._connection.total_changes > before

    def translation_glossary(
        self, revision_id: str, language: str
    ) -> TranslationGlossaryRecord | None:
        row = self._connection.execute(
            "SELECT * FROM protocol_translation_glossaries "
            "WHERE revision_id=? AND language=?",
            (revision_id, language),
        ).fetchone()
        if row is None:
            return None
        return TranslationGlossaryRecord(
            revision_id=row["revision_id"], language=row["language"],
            entries_json=row["entries_json"], model=row["model"],
            model_version=row["model_version"], created_at=row["created_at"],
        )

    def add_knowledge(
        self,
        principal: Principal,
        *,
        kind: str,
        body: str,
        provenance: Mapping[str, object],
        revision_id: str | None = None,
    ) -> str:
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

    def knowledge_entries(
        self, principal: Principal
    ) -> tuple[dict[str, object], ...]:
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

    def record_experimenter_setting(
        self,
        principal: Principal,
        *,
        name: str,
        value: str,
        source: str,
    ) -> dict[str, str]:
        """Append one setting change of this experimenter; the settings after it."""

        if value not in EXPERIMENTER_SETTING_VALUES.get(name, ()):
            raise WorkspaceError("Experimenter setting is invalid.")
        if source not in {"voice", "screen"}:
            raise WorkspaceError("Experimenter setting source is invalid.")
        self._connection.execute(
            """INSERT INTO experimenter_settings(
            organization_id,principal_id,name,value,source,created_at
            ) VALUES(?,?,?,?,?,?)""",
            (principal.organization_id, principal.principal_id, name, value, source, _now()),
        )
        self._connection.commit()
        return self.experimenter_settings(principal)

    def experimenter_settings(self, principal: Principal) -> dict[str, str]:
        """The latest value of each setting this experimenter changed; unset ones are absent."""

        rows = self._connection.execute(
            """SELECT name,value FROM experimenter_settings
            WHERE organization_id=? AND principal_id=?
            ORDER BY sequence_id""",
            (principal.organization_id, principal.principal_id),
        ).fetchall()
        return {str(row["name"]): str(row["value"]) for row in rows}

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
    if version == 5:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + MIGRATION_5_TO_6 + "\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            connection.rollback()
            connection.close()
            raise WorkspaceError(
                "Commercial workspace migration failed."
            ) from exc
        version = 6
    if version == 6:
        # The whole-revision rows in protocol_translations stay where they
        # are and are still read by add_translation's callers; sentences get
        # their own table because a revision-wide text cannot be split.
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + MIGRATION_6_TO_7 + "\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            connection.rollback()
            connection.close()
            raise WorkspaceError(
                "Commercial workspace migration failed."
            ) from exc
        version = 7
    if version == 7:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + MIGRATION_7_TO_8 + "\nCOMMIT;"
            )
        except sqlite3.Error as exc:
            connection.rollback()
            connection.close()
            raise WorkspaceError(
                "Commercial workspace migration failed."
            ) from exc
        version = 8
    if version != WORKSPACE_SCHEMA_VERSION:
        connection.close()
        raise WorkspaceError("Commercial workspace schema is unsupported.")
    return WorkspaceStore(
        connection,
        path,
        default_analytics_retention_days=settings.analytics_retention_days,
    )
