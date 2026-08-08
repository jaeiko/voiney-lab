"""Separate schema-v1 persistence for immutable Experiment Protocol records."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolConfigurationError,
    ProtocolFeatureDisabledError,
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_files import (
    ProtocolFileStore,
    ProtocolFileStoreError,
    ProtocolObjectIntegrityError,
    ProtocolPdfObject,
    StoredProtocolPdf,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)


PROTOCOL_DATABASE_FILENAME = "protocol_workspace.sqlite"
PROTOCOL_SCHEMA_VERSION = 1
ANALYSIS_SCHEMA_VERSION = 1
_STABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolPersistenceError(RuntimeError):
    """Base class for sanitized Protocol persistence failures."""


class ProtocolStorageInitializationError(ProtocolPersistenceError):
    pass


class UnsupportedProtocolSchemaError(ProtocolPersistenceError):
    pass


class InvalidExperimentIdentifierError(ProtocolPersistenceError):
    pass


class ExperimentProtocolRequiredError(ProtocolPersistenceError):
    pass


class DuplicateProtocolIdentifierError(ProtocolPersistenceError):
    pass


class UnknownProtocolReferenceError(ProtocolPersistenceError):
    pass


class ImmutableProtocolRecordError(ProtocolPersistenceError):
    pass


class ProtocolSerializationError(ProtocolPersistenceError):
    pass


class ProtocolTransactionError(ProtocolPersistenceError):
    pass


SCHEMA = """
CREATE TABLE schema_metadata(
 schema_version INTEGER PRIMARY KEY CHECK(schema_version=1)
);
INSERT INTO schema_metadata(schema_version) VALUES(1);

CREATE TABLE pdf_objects(
 checksum TEXT PRIMARY KEY CHECK(length(checksum)=64 AND checksum=lower(checksum)),
 byte_size INTEGER NOT NULL CHECK(byte_size>=0),
 media_type TEXT NOT NULL CHECK(media_type='application/pdf'),
 relative_path TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);

CREATE TABLE experiments(
 experiment_id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 initial_protocol_revision INTEGER NOT NULL DEFAULT 1
     CHECK(initial_protocol_revision=1),
 FOREIGN KEY(experiment_id,initial_protocol_revision)
     REFERENCES protocol_revisions(experiment_id,revision_number)
     DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE protocol_revisions(
 experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
 revision_number INTEGER NOT NULL CHECK(revision_number>0),
 pdf_checksum TEXT NOT NULL REFERENCES pdf_objects(checksum),
 original_filename TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(experiment_id,revision_number)
);

CREATE TABLE analysis_payloads(
 payload_sha256 TEXT PRIMARY KEY
     CHECK(length(payload_sha256)=64 AND payload_sha256=lower(payload_sha256)),
 analysis_schema_version INTEGER NOT NULL CHECK(analysis_schema_version>0),
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE analysis_revisions(
 experiment_id TEXT NOT NULL,
 protocol_revision_number INTEGER NOT NULL,
 analysis_revision_number INTEGER NOT NULL CHECK(analysis_revision_number>0),
 analysis_id TEXT NOT NULL UNIQUE,
 payload_sha256 TEXT NOT NULL REFERENCES analysis_payloads(payload_sha256),
 analysis_schema_version INTEGER NOT NULL CHECK(analysis_schema_version>0),
 capability_policy_id TEXT NOT NULL,
 readiness_status TEXT NOT NULL
     CHECK(readiness_status IN ('guidance_ready','analysis_required')),
 readiness_label TEXT NOT NULL
     CHECK(readiness_label IN ('안내 준비 완료','Protocol 분석 필요')),
 readiness_reason_codes_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(
     experiment_id,protocol_revision_number,analysis_revision_number
 ),
 FOREIGN KEY(experiment_id,protocol_revision_number)
     REFERENCES protocol_revisions(experiment_id,revision_number)
);

CREATE TABLE clarifications(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 clarification_id TEXT NOT NULL UNIQUE,
 experiment_id TEXT NOT NULL,
 protocol_revision_number INTEGER NOT NULL,
 analysis_revision_number INTEGER NOT NULL,
 related_step_id TEXT,
 ambiguity_source_text TEXT NOT NULL,
 interpretation TEXT NOT NULL,
 researcher TEXT NOT NULL,
 reason TEXT NOT NULL,
 recorded_at TEXT NOT NULL,
 FOREIGN KEY(
     experiment_id,protocol_revision_number,analysis_revision_number
 ) REFERENCES analysis_revisions(
     experiment_id,protocol_revision_number,analysis_revision_number
 )
);

CREATE TABLE protocol_events(
 sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE,
 experiment_id TEXT NOT NULL,
 protocol_revision_number INTEGER NOT NULL,
 analysis_revision_number INTEGER,
 event_type TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 recorded_at TEXT NOT NULL,
 FOREIGN KEY(experiment_id,protocol_revision_number)
     REFERENCES protocol_revisions(experiment_id,revision_number),
 FOREIGN KEY(
     experiment_id,protocol_revision_number,analysis_revision_number
 ) REFERENCES analysis_revisions(
     experiment_id,protocol_revision_number,analysis_revision_number
 )
);

CREATE TRIGGER schema_metadata_no_update
BEFORE UPDATE ON schema_metadata
BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER schema_metadata_no_delete
BEFORE DELETE ON schema_metadata
BEGIN SELECT RAISE(ABORT,'immutable'); END;

CREATE TRIGGER pdf_objects_no_update
BEFORE UPDATE ON pdf_objects
BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER pdf_objects_no_delete
BEFORE DELETE ON pdf_objects
BEGIN SELECT RAISE(ABORT,'immutable'); END;

CREATE TRIGGER experiments_no_update
BEFORE UPDATE ON experiments
BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER experiments_no_delete
BEFORE DELETE ON experiments
BEGIN SELECT RAISE(ABORT,'immutable'); END;

CREATE TRIGGER protocol_revisions_no_update
BEFORE UPDATE ON protocol_revisions
BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER protocol_revisions_no_delete
BEFORE DELETE ON protocol_revisions
BEGIN SELECT RAISE(ABORT,'immutable'); END;

CREATE TRIGGER analysis_payloads_no_update
BEFORE UPDATE ON analysis_payloads
BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER analysis_payloads_no_delete
BEFORE DELETE ON analysis_payloads
BEGIN SELECT RAISE(ABORT,'immutable'); END;

CREATE TRIGGER analysis_revisions_no_update
BEFORE UPDATE ON analysis_revisions
BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER analysis_revisions_no_delete
BEFORE DELETE ON analysis_revisions
BEGIN SELECT RAISE(ABORT,'immutable'); END;

CREATE TRIGGER clarifications_no_update
BEFORE UPDATE ON clarifications
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER clarifications_no_delete
BEFORE DELETE ON clarifications
BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TRIGGER protocol_events_no_update
BEFORE UPDATE ON protocol_events
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER protocol_events_no_delete
BEFORE DELETE ON protocol_events
BEGIN SELECT RAISE(ABORT,'append-only'); END;
"""


@dataclass(frozen=True)
class PdfObjectRecord:
    checksum: str
    byte_size: int
    media_type: str
    relative_path: str
    created_at: str


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    created_at: str
    initial_protocol_revision: int


@dataclass(frozen=True)
class ProtocolRevisionRecord:
    experiment_id: str
    revision_number: int
    pdf_checksum: str
    original_filename: str
    created_at: str


@dataclass(frozen=True)
class ExperimentCreation:
    experiment: ExperimentRecord
    protocol_revision: ProtocolRevisionRecord


@dataclass(frozen=True)
class AnalysisRevisionRecord:
    experiment_id: str
    protocol_revision_number: int
    analysis_revision_number: int
    analysis_id: str
    payload_sha256: str
    analysis_schema_version: int
    capability_policy_id: str
    readiness_status: str
    readiness_label: str
    readiness_reason_codes: tuple[str, ...]
    created_at: str
    protocol: domain.ExperimentProtocol
    readiness: domain.ReadinessAssessment


@dataclass(frozen=True)
class ClarificationRecord:
    sequence_id: int
    clarification_id: str
    experiment_id: str
    protocol_revision_number: int
    analysis_revision_number: int
    related_step_id: str | None
    ambiguity_source_text: str
    interpretation: str
    researcher: str
    reason: str
    recorded_at: str


@dataclass(frozen=True)
class ProtocolEventRecord:
    sequence_id: int
    event_id: str
    experiment_id: str
    protocol_revision_number: int
    analysis_revision_number: int | None
    event_type: str
    payload: Any
    recorded_at: str


_DOMAIN_DATACLASS_NAMES = (
    "SourceEvidence",
    "ProtocolMetadata",
    "ScientificValue",
    "SourceStatement",
    "EstimatedDuration",
    "ProcessTimerSpecification",
    "OneTimeReminder",
    "RecurringReminder",
    "ActualElapsedTime",
    "BeforeStartPrerequisite",
    "Material",
    "Equipment",
    "RequiredObservation",
    "MissingExecutionValue",
    "DependencyTarget",
    "ProtocolSubAction",
    "ProtocolSourceStep",
    "ProtocolSection",
    "ConditionalBranch",
    "FixedRangeRepetition",
    "RepeatUntil",
    "ParallelWork",
    "RecurringAction",
    "ReusableSubprocedure",
    "SourceAmbiguity",
    "ProtocolConflict",
    "ExperimentProtocol",
    "DetectedFeature",
    "CapabilityPolicy",
    "ReadinessReason",
    "ReadinessAssessment",
)
_PDF_DATACLASSES = (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
_ALLOWED_DATACLASSES = {
    value.__name__: value
    for value in (
        *(getattr(domain, name) for name in _DOMAIN_DATACLASS_NAMES),
        *_PDF_DATACLASSES,
    )
}
_DOMAIN_ENUM_NAMES = (
    "ProtocolValidationCode",
    "ReadinessStatus",
    "FeatureCode",
    "ReadinessReasonCode",
    "BranchKind",
    "ConflictLevel",
)
_ALLOWED_ENUMS = {
    value.__name__: value
    for value in (getattr(domain, name) for name in _DOMAIN_ENUM_NAMES)
}


def _identifier(value: object, *, experiment: bool = False) -> str:
    if not isinstance(value, str) or not _STABLE_IDENTIFIER.fullmatch(value):
        if experiment:
            raise InvalidExperimentIdentifierError(
                "Experiment identifier is invalid."
            )
        raise ProtocolPersistenceError("Stable identifier is invalid.")
    return value


def _text(value: object, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolPersistenceError(message)
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolSerializationError(
            "Protocol payload is not valid deterministic JSON."
        ) from exc


def _encode_domain(value: Any) -> Any:
    if isinstance(value, Enum):
        enum_name = type(value).__name__
        if enum_name not in _ALLOWED_ENUMS:
            raise ProtocolSerializationError(
                "Protocol analysis contains an unsupported enum."
            )
        return {"$enum": enum_name, "value": value.value}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if is_dataclass(value):
        type_name = type(value).__name__
        if type_name not in _ALLOWED_DATACLASSES:
            raise ProtocolSerializationError(
                "Protocol analysis contains an unsupported record type."
            )
        return {
            "$type": type_name,
            "fields": {
                field.name: _encode_domain(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode_domain(item) for item in value]}
    if isinstance(value, frozenset):
        encoded = [_encode_domain(item) for item in value]
        encoded.sort(key=_canonical_json)
        return {"$frozenset": encoded}
    raise ProtocolSerializationError(
        "Protocol analysis contains an unsupported value type."
    )


def _decode_domain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if not isinstance(value, dict):
        raise ProtocolSerializationError(
            "Stored Protocol analysis has an invalid value shape."
        )
    if set(value) == {"$tuple"} and isinstance(value["$tuple"], list):
        return tuple(_decode_domain(item) for item in value["$tuple"])
    if set(value) == {"$frozenset"} and isinstance(
        value["$frozenset"], list
    ):
        return frozenset(
            _decode_domain(item) for item in value["$frozenset"]
        )
    if set(value) == {"$enum", "value"}:
        enum_type = _ALLOWED_ENUMS.get(value["$enum"])
        if enum_type is None:
            raise ProtocolSerializationError(
                "Stored Protocol analysis uses an unsupported enum."
            )
        try:
            return enum_type(value["value"])
        except (TypeError, ValueError) as exc:
            raise ProtocolSerializationError(
                "Stored Protocol analysis contains an invalid enum value."
            ) from exc
    if set(value) == {"$type", "fields"} and isinstance(
        value["fields"], dict
    ):
        record_type = _ALLOWED_DATACLASSES.get(value["$type"])
        if record_type is None:
            raise ProtocolSerializationError(
                "Stored Protocol analysis uses an unsupported record type."
            )
        expected_fields = {field.name for field in fields(record_type)}
        if set(value["fields"]) != expected_fields:
            raise ProtocolSerializationError(
                "Stored Protocol analysis record fields are malformed."
            )
        try:
            return record_type(
                **{
                    name: _decode_domain(item)
                    for name, item in value["fields"].items()
                }
            )
        except (TypeError, ValueError) as exc:
            raise ProtocolSerializationError(
                "Stored Protocol analysis record could not be reconstructed."
            ) from exc
    raise ProtocolSerializationError(
        "Stored Protocol analysis contains an unknown tagged value."
    )


def serialize_analysis(
    protocol: domain.ExperimentProtocol,
    readiness: domain.ReadinessAssessment,
    capability_policy_id: str,
) -> tuple[str, str]:
    """Return canonical JSON and its deterministic SHA-256 identity."""

    try:
        domain.validate_protocol(protocol)
    except domain.ProtocolValidationError as exc:
        raise ProtocolSerializationError(
            "Structured Protocol is invalid and cannot be stored."
        ) from exc
    _identifier(capability_policy_id)
    if (
        readiness.status
        not in {
            domain.ReadinessStatus.GUIDANCE_READY,
            domain.ReadinessStatus.ANALYSIS_REQUIRED,
        }
        or readiness.label
        not in {domain.GUIDANCE_READY_LABEL, domain.ANALYSIS_REQUIRED_LABEL}
    ):
        raise ProtocolSerializationError(
            "Readiness assessment has an unsupported public outcome."
        )
    payload = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "capability_policy_id": capability_policy_id,
        "protocol": _encode_domain(protocol),
        "readiness": _encode_domain(readiness),
    }
    encoded = _canonical_json(payload)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def deserialize_analysis(
    payload_json: str,
) -> tuple[
    domain.ExperimentProtocol,
    domain.ReadinessAssessment,
    str,
    int,
]:
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProtocolSerializationError(
            "Stored Protocol analysis is not valid JSON."
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "analysis_schema_version",
        "capability_policy_id",
        "protocol",
        "readiness",
    }:
        raise ProtocolSerializationError(
            "Stored Protocol analysis envelope is malformed."
        )
    if payload["analysis_schema_version"] != ANALYSIS_SCHEMA_VERSION:
        raise ProtocolSerializationError(
            "Stored Protocol analysis schema version is unsupported."
        )
    capability_policy_id = _identifier(payload["capability_policy_id"])
    protocol = _decode_domain(payload["protocol"])
    readiness = _decode_domain(payload["readiness"])
    if not isinstance(protocol, domain.ExperimentProtocol) or not isinstance(
        readiness, domain.ReadinessAssessment
    ):
        raise ProtocolSerializationError(
            "Stored Protocol analysis has incorrect root record types."
        )
    try:
        domain.validate_protocol(protocol)
    except domain.ProtocolValidationError as exc:
        raise ProtocolSerializationError(
            "Stored structured Protocol failed validation."
        ) from exc
    return (
        protocol,
        readiness,
        capability_policy_id,
        ANALYSIS_SCHEMA_VERSION,
    )


class ProtocolStore:
    """Explicitly initialized storage; construction itself has no filesystem API.

    Content-addressed PDF publication is intentionally outside SQLite
    transactions.  Database rollback removes every reference record but does
    not delete an already verified immutable object, which a later retry may
    safely deduplicate.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        data_dir: Path,
        file_store: ProtocolFileStore,
    ) -> None:
        self._connection = connection
        self._data_dir = data_dir
        self.file_store = file_store

    @property
    def database_path(self) -> Path:
        return self._data_dir / PROTOCOL_DATABASE_FILENAME

    def close(self) -> None:
        self._connection.close()

    def foreign_keys_enabled(self) -> bool:
        return bool(
            self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
        )

    def schema_version(self) -> int:
        row = self._connection.execute(
            "SELECT schema_version FROM schema_metadata"
        ).fetchone()
        if row is None:
            raise UnsupportedProtocolSchemaError(
                "Protocol storage schema metadata is unavailable."
            )
        return int(row[0])

    def _begin(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise ProtocolTransactionError(
                "Protocol storage transaction could not begin."
            ) from exc

    def _rollback(self) -> None:
        try:
            self._connection.rollback()
        except sqlite3.Error:
            pass

    def _execute_write(
        self,
        statement: str,
        parameters: tuple[Any, ...] = (),
    ) -> sqlite3.Cursor:
        """Execute a store-owned write and sanitize trigger rejections."""

        try:
            return self._connection.execute(statement, parameters)
        except sqlite3.DatabaseError as exc:
            if str(exc) in {"immutable", "append-only"}:
                raise ImmutableProtocolRecordError(
                    "Stored Protocol records cannot be changed or deleted."
                ) from exc
            raise

    def _pdf_record(self, stored: StoredProtocolPdf, now: str) -> None:
        row = self._connection.execute(
            "SELECT * FROM pdf_objects WHERE checksum=?",
            (stored.object.checksum,),
        ).fetchone()
        if row is not None:
            if (
                row["byte_size"] != stored.object.byte_size
                or row["media_type"] != stored.object.media_type
                or row["relative_path"] != stored.object.relative_path
            ):
                raise ProtocolObjectIntegrityError(
                    "Stored Protocol object metadata conflicts with its checksum."
                )
            return
        self._execute_write(
            """INSERT INTO pdf_objects
            (checksum,byte_size,media_type,relative_path,created_at)
            VALUES (?,?,?,?,?)""",
            (
                stored.object.checksum,
                stored.object.byte_size,
                stored.object.media_type,
                stored.object.relative_path,
                now,
            ),
        )

    def get_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        row = self._connection.execute(
            "SELECT * FROM experiments WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()
        return ExperimentRecord(**dict(row)) if row is not None else None

    def list_experiments(self) -> tuple[ExperimentRecord, ...]:
        """List immutable experiment identities without creating new state."""

        return tuple(
            ExperimentRecord(**dict(row))
            for row in self._connection.execute(
                "SELECT * FROM experiments ORDER BY created_at,experiment_id"
            )
        )

    def get_pdf_object(self, checksum: str) -> PdfObjectRecord | None:
        if not isinstance(checksum, str) or not _LOWERCASE_SHA256.fullmatch(
            checksum
        ):
            raise ProtocolObjectIntegrityError(
                "Stored Protocol object checksum is malformed."
            )
        row = self._connection.execute(
            "SELECT * FROM pdf_objects WHERE checksum=?", (checksum,)
        ).fetchone()
        return PdfObjectRecord(**dict(row)) if row is not None else None

    def find_protocol_revision_by_checksum(
        self,
        checksum: str,
    ) -> ProtocolRevisionRecord | None:
        if not isinstance(checksum, str) or not _LOWERCASE_SHA256.fullmatch(
            checksum
        ):
            raise ProtocolObjectIntegrityError(
                "Stored Protocol object checksum is malformed."
            )
        row = self._connection.execute(
            """SELECT * FROM protocol_revisions
            WHERE pdf_checksum=?
            ORDER BY created_at,experiment_id,revision_number LIMIT 1""",
            (checksum,),
        ).fetchone()
        return ProtocolRevisionRecord(**dict(row)) if row is not None else None

    def list_protocol_revisions(
        self,
        experiment_id: str,
    ) -> tuple[ProtocolRevisionRecord, ...]:
        return tuple(
            ProtocolRevisionRecord(**dict(row))
            for row in self._connection.execute(
                """SELECT * FROM protocol_revisions
                WHERE experiment_id=? ORDER BY revision_number""",
                (experiment_id,),
            )
        )

    def get_protocol_revision(
        self,
        experiment_id: str,
        revision_number: int,
    ) -> ProtocolRevisionRecord | None:
        row = self._connection.execute(
            """SELECT * FROM protocol_revisions
            WHERE experiment_id=? AND revision_number=?""",
            (experiment_id, revision_number),
        ).fetchone()
        return ProtocolRevisionRecord(**dict(row)) if row is not None else None

    def create_experiment(
        self,
        experiment_id: str,
        source_pdf: str | Path | None,
        *,
        original_filename: str | None = None,
    ) -> ExperimentCreation:
        _identifier(experiment_id, experiment=True)
        if source_pdf is None:
            raise ExperimentProtocolRequiredError(
                "An experiment requires an initial Protocol PDF."
            )
        # File publication is deliberately committed before the database
        # transaction.  A rollback can leave this valid object unreferenced;
        # object presence alone never establishes an Experiment or revision.
        try:
            stored = self.file_store.store(source_pdf)
        except ProtocolFileStoreError:
            raise
        now = _now()
        recorded_filename = (
            stored.original_filename
            if original_filename is None
            else _text(original_filename, "Protocol source filename is required.")
        )
        if (
            Path(recorded_filename).name != recorded_filename
            or "/" in recorded_filename
            or "\\" in recorded_filename
            or "\x00" in recorded_filename
        ):
            raise ProtocolPersistenceError(
                "Protocol source filename is not a plain filename."
            )
        self._begin()
        try:
            existing = self.get_experiment(experiment_id)
            if existing is not None:
                revision = self.get_protocol_revision(experiment_id, 1)
                if (
                    revision is not None
                    and revision.pdf_checksum == stored.object.checksum
                ):
                    self._connection.rollback()
                    return ExperimentCreation(existing, revision)
                raise DuplicateProtocolIdentifierError(
                    "Experiment identifier already has different content."
                )
            self._pdf_record(stored, now)
            self._execute_write(
                """INSERT INTO experiments
                (experiment_id,created_at,initial_protocol_revision)
                VALUES (?,?,1)""",
                (experiment_id, now),
            )
            self._execute_write(
                """INSERT INTO protocol_revisions
                (experiment_id,revision_number,pdf_checksum,
                 original_filename,created_at)
                VALUES (?,1,?,?,?)""",
                (
                    experiment_id,
                    stored.object.checksum,
                    recorded_filename,
                    now,
                ),
            )
            self._connection.commit()
        except ProtocolPersistenceError:
            self._rollback()
            raise
        except ProtocolFileStoreError:
            self._rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback()
            raise ProtocolTransactionError(
                "Experiment creation transaction failed."
            ) from exc
        except sqlite3.Error as exc:
            self._rollback()
            raise ProtocolTransactionError(
                "Experiment creation transaction failed."
            ) from exc
        experiment = self.get_experiment(experiment_id)
        revision = self.get_protocol_revision(experiment_id, 1)
        if experiment is None or revision is None:
            raise ProtocolTransactionError(
                "Experiment creation did not persist its Protocol revision."
            )
        return ExperimentCreation(experiment, revision)

    def append_protocol_revision(
        self,
        experiment_id: str,
        source_pdf: str | Path,
    ) -> ProtocolRevisionRecord:
        _identifier(experiment_id, experiment=True)
        stored = self.file_store.store(source_pdf)
        now = _now()
        self._begin()
        try:
            if self.get_experiment(experiment_id) is None:
                raise UnknownProtocolReferenceError(
                    "Experiment reference does not exist."
                )
            self._pdf_record(stored, now)
            row = self._connection.execute(
                """SELECT COALESCE(MAX(revision_number),0)+1
                FROM protocol_revisions WHERE experiment_id=?""",
                (experiment_id,),
            ).fetchone()
            revision_number = int(row[0])
            self._execute_write(
                """INSERT INTO protocol_revisions
                (experiment_id,revision_number,pdf_checksum,
                 original_filename,created_at)
                VALUES (?,?,?,?,?)""",
                (
                    experiment_id,
                    revision_number,
                    stored.object.checksum,
                    stored.original_filename,
                    now,
                ),
            )
            self._connection.commit()
        except (ProtocolPersistenceError, ProtocolFileStoreError):
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise ProtocolTransactionError(
                "Protocol revision transaction failed."
            ) from exc
        revision = self.get_protocol_revision(experiment_id, revision_number)
        if revision is None:
            raise ProtocolTransactionError(
                "Protocol revision was not preserved."
            )
        return revision

    def _analysis_row_by_id(self, analysis_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM analysis_revisions WHERE analysis_id=?",
            (analysis_id,),
        ).fetchone()

    def _append_analysis_revision_write(
        self,
        experiment_id: str,
        protocol_revision_number: int,
        analysis_id: str,
        payload_json: str,
        payload_sha256: str,
        readiness: domain.ReadinessAssessment,
        capability_policy_id: str,
        reason_codes_json: str,
        now: str,
    ) -> tuple[int, bool]:
        """Write one analysis inside the caller-owned transaction."""

        existing = self._analysis_row_by_id(analysis_id)
        if existing is not None:
            if (
                existing["experiment_id"] == experiment_id
                and existing["protocol_revision_number"]
                == protocol_revision_number
                and existing["payload_sha256"] == payload_sha256
            ):
                return existing["analysis_revision_number"], True
            raise DuplicateProtocolIdentifierError(
                "Analysis identifier already has different content."
            )
        payload_row = self._connection.execute(
            "SELECT * FROM analysis_payloads WHERE payload_sha256=?",
            (payload_sha256,),
        ).fetchone()
        if payload_row is None:
            self._execute_write(
                """INSERT INTO analysis_payloads
                (payload_sha256,analysis_schema_version,payload_json,created_at)
                VALUES (?,?,?,?)""",
                (
                    payload_sha256,
                    ANALYSIS_SCHEMA_VERSION,
                    payload_json,
                    now,
                ),
            )
        elif (
            payload_row["payload_json"] != payload_json
            or payload_row["analysis_schema_version"]
            != ANALYSIS_SCHEMA_VERSION
        ):
            raise ProtocolSerializationError(
                "Analysis payload identity conflicts with stored content."
            )
        row = self._connection.execute(
            """SELECT COALESCE(MAX(analysis_revision_number),0)+1
            FROM analysis_revisions
            WHERE experiment_id=? AND protocol_revision_number=?""",
            (experiment_id, protocol_revision_number),
        ).fetchone()
        analysis_revision_number = int(row[0])
        self._execute_write(
            """INSERT INTO analysis_revisions
            (experiment_id,protocol_revision_number,
             analysis_revision_number,analysis_id,payload_sha256,
             analysis_schema_version,capability_policy_id,
             readiness_status,readiness_label,
             readiness_reason_codes_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                experiment_id,
                protocol_revision_number,
                analysis_revision_number,
                analysis_id,
                payload_sha256,
                ANALYSIS_SCHEMA_VERSION,
                capability_policy_id,
                readiness.status.value,
                readiness.label,
                reason_codes_json,
                now,
            ),
        )
        return analysis_revision_number, False

    def append_analysis_revision(
        self,
        experiment_id: str,
        protocol_revision_number: int,
        analysis_id: str,
        protocol: domain.ExperimentProtocol,
        readiness: domain.ReadinessAssessment,
        capability_policy_id: str,
    ) -> AnalysisRevisionRecord:
        _identifier(experiment_id, experiment=True)
        _identifier(analysis_id)
        revision = self.get_protocol_revision(
            experiment_id,
            protocol_revision_number,
        )
        if revision is None:
            raise UnknownProtocolReferenceError(
                "Experiment Protocol revision reference does not exist."
            )
        if protocol.metadata.file_checksum != revision.pdf_checksum:
            raise UnknownProtocolReferenceError(
                "Structured analysis does not match the referenced Protocol bytes."
            )
        payload_json, payload_sha256 = serialize_analysis(
            protocol,
            readiness,
            capability_policy_id,
        )
        reason_codes_json = _canonical_json(list(readiness.reason_codes))
        now = _now()
        self._begin()
        try:
            analysis_revision_number, replayed = (
                self._append_analysis_revision_write(
                    experiment_id,
                    protocol_revision_number,
                    analysis_id,
                    payload_json,
                    payload_sha256,
                    readiness,
                    capability_policy_id,
                    reason_codes_json,
                    now,
                )
            )
            if replayed:
                self._connection.rollback()
                return self.get_analysis_revision(
                    experiment_id,
                    protocol_revision_number,
                    analysis_revision_number,
                )
            self._connection.commit()
        except ProtocolPersistenceError:
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise ProtocolTransactionError(
                "Analysis revision transaction failed."
            ) from exc
        return self.get_analysis_revision(
            experiment_id,
            protocol_revision_number,
            analysis_revision_number,
        )

    def create_experiment_with_analysis(
        self,
        experiment_id: str,
        source_pdf: str | Path,
        analysis_id: str,
        protocol: domain.ExperimentProtocol,
        readiness: domain.ReadinessAssessment,
        capability_policy_id: str,
    ) -> AnalysisRevisionRecord:
        """Atomically create an experiment and its initial analysis records."""

        _identifier(experiment_id, experiment=True)
        _identifier(analysis_id)
        stored = self.file_store.store(source_pdf)
        if protocol.metadata.file_checksum != stored.object.checksum:
            raise UnknownProtocolReferenceError(
                "Structured analysis does not match the selected Protocol bytes."
            )
        payload_json, payload_sha256 = serialize_analysis(
            protocol,
            readiness,
            capability_policy_id,
        )
        reason_codes_json = _canonical_json(list(readiness.reason_codes))
        now = _now()
        self._begin()
        try:
            existing_experiment = self.get_experiment(experiment_id)
            if existing_experiment is None:
                self._pdf_record(stored, now)
                self._execute_write(
                    """INSERT INTO experiments
                    (experiment_id,created_at,initial_protocol_revision)
                    VALUES (?,?,1)""",
                    (experiment_id, now),
                )
                self._execute_write(
                    """INSERT INTO protocol_revisions
                    (experiment_id,revision_number,pdf_checksum,
                     original_filename,created_at)
                    VALUES (?,1,?,?,?)""",
                    (
                        experiment_id,
                        stored.object.checksum,
                        stored.original_filename,
                        now,
                    ),
                )
            else:
                revision = self.get_protocol_revision(experiment_id, 1)
                if (
                    revision is None
                    or revision.pdf_checksum != stored.object.checksum
                ):
                    raise DuplicateProtocolIdentifierError(
                        "Experiment identifier already has different content."
                    )
            analysis_revision_number, replayed = (
                self._append_analysis_revision_write(
                    experiment_id,
                    1,
                    analysis_id,
                    payload_json,
                    payload_sha256,
                    readiness,
                    capability_policy_id,
                    reason_codes_json,
                    now,
                )
            )
            if replayed and existing_experiment is not None:
                self._connection.rollback()
                return self.get_analysis_revision(
                    experiment_id,
                    1,
                    analysis_revision_number,
                )
            self._connection.commit()
        except (ProtocolPersistenceError, ProtocolFileStoreError):
            self._rollback()
            raise
        except sqlite3.Error as exc:
            self._rollback()
            raise ProtocolTransactionError(
                "Experiment analysis transaction failed."
            ) from exc
        return self.get_analysis_revision(
            experiment_id,
            1,
            analysis_revision_number,
        )

    def get_analysis_revision(
        self,
        experiment_id: str,
        protocol_revision_number: int,
        analysis_revision_number: int,
    ) -> AnalysisRevisionRecord:
        row = self._connection.execute(
            """SELECT ar.*,ap.payload_json
            FROM analysis_revisions ar
            JOIN analysis_payloads ap
              ON ap.payload_sha256=ar.payload_sha256
            WHERE ar.experiment_id=?
              AND ar.protocol_revision_number=?
              AND ar.analysis_revision_number=?""",
            (
                experiment_id,
                protocol_revision_number,
                analysis_revision_number,
            ),
        ).fetchone()
        if row is None:
            raise UnknownProtocolReferenceError(
                "Analysis revision reference does not exist."
            )
        payload_json = row["payload_json"]
        actual_payload_sha256 = hashlib.sha256(
            payload_json.encode("utf-8")
        ).hexdigest()
        if actual_payload_sha256 != row["payload_sha256"]:
            raise ProtocolSerializationError(
                "Stored analysis payload failed identity verification."
            )
        protocol, readiness, capability_policy_id, schema_version = (
            deserialize_analysis(payload_json)
        )
        try:
            reason_codes = tuple(
                json.loads(row["readiness_reason_codes_json"])
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProtocolSerializationError(
                "Stored readiness reason codes are malformed."
            ) from exc
        if (
            capability_policy_id != row["capability_policy_id"]
            or schema_version != row["analysis_schema_version"]
            or readiness.status.value != row["readiness_status"]
            or readiness.label != row["readiness_label"]
            or readiness.reason_codes != reason_codes
        ):
            raise ProtocolSerializationError(
                "Stored analysis envelope conflicts with its payload."
            )
        return AnalysisRevisionRecord(
            experiment_id=row["experiment_id"],
            protocol_revision_number=row["protocol_revision_number"],
            analysis_revision_number=row["analysis_revision_number"],
            analysis_id=row["analysis_id"],
            payload_sha256=row["payload_sha256"],
            analysis_schema_version=row["analysis_schema_version"],
            capability_policy_id=row["capability_policy_id"],
            readiness_status=row["readiness_status"],
            readiness_label=row["readiness_label"],
            readiness_reason_codes=reason_codes,
            created_at=row["created_at"],
            protocol=protocol,
            readiness=readiness,
        )

    def list_analysis_revisions(
        self,
        experiment_id: str,
        protocol_revision_number: int,
    ) -> tuple[AnalysisRevisionRecord, ...]:
        rows = self._connection.execute(
            """SELECT analysis_revision_number FROM analysis_revisions
            WHERE experiment_id=? AND protocol_revision_number=?
            ORDER BY analysis_revision_number""",
            (experiment_id, protocol_revision_number),
        ).fetchall()
        return tuple(
            self.get_analysis_revision(
                experiment_id,
                protocol_revision_number,
                row["analysis_revision_number"],
            )
            for row in rows
        )

    def append_clarification(
        self,
        clarification_id: str,
        experiment_id: str,
        protocol_revision_number: int,
        analysis_revision_number: int,
        *,
        ambiguity_source_text: str,
        interpretation: str,
        researcher: str,
        reason: str,
        related_step_id: str | None = None,
    ) -> ClarificationRecord:
        _identifier(clarification_id)
        _identifier(experiment_id, experiment=True)
        if related_step_id is not None:
            _identifier(related_step_id)
        for value, message in (
            (ambiguity_source_text, "Clarification ambiguity is required."),
            (interpretation, "Clarification interpretation is required."),
            (researcher, "Clarification researcher is required."),
            (reason, "Clarification reason is required."),
        ):
            _text(value, message)
        try:
            self.get_analysis_revision(
                experiment_id,
                protocol_revision_number,
                analysis_revision_number,
            )
        except UnknownProtocolReferenceError as exc:
            raise UnknownProtocolReferenceError(
                "Clarification analysis reference does not exist."
            ) from exc
        existing = self._connection.execute(
            "SELECT * FROM clarifications WHERE clarification_id=?",
            (clarification_id,),
        ).fetchone()
        expected = (
            experiment_id,
            protocol_revision_number,
            analysis_revision_number,
            related_step_id,
            ambiguity_source_text,
            interpretation,
            researcher,
            reason,
        )
        if existing is not None:
            actual = (
                existing["experiment_id"],
                existing["protocol_revision_number"],
                existing["analysis_revision_number"],
                existing["related_step_id"],
                existing["ambiguity_source_text"],
                existing["interpretation"],
                existing["researcher"],
                existing["reason"],
            )
            if actual != expected:
                raise DuplicateProtocolIdentifierError(
                    "Clarification identifier already has different content."
                )
            return ClarificationRecord(**dict(existing))
        try:
            with self._connection:
                cursor = self._execute_write(
                    """INSERT INTO clarifications
                    (clarification_id,experiment_id,protocol_revision_number,
                     analysis_revision_number,related_step_id,
                     ambiguity_source_text,interpretation,researcher,reason,
                     recorded_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        clarification_id,
                        experiment_id,
                        protocol_revision_number,
                        analysis_revision_number,
                        related_step_id,
                        ambiguity_source_text,
                        interpretation,
                        researcher,
                        reason,
                        _now(),
                    ),
                )
        except sqlite3.Error as exc:
            raise ProtocolTransactionError(
                "Clarification append transaction failed."
            ) from exc
        row = self._connection.execute(
            "SELECT * FROM clarifications WHERE sequence_id=?",
            (cursor.lastrowid,),
        ).fetchone()
        return ClarificationRecord(**dict(row))

    def list_clarifications(
        self,
        experiment_id: str,
    ) -> tuple[ClarificationRecord, ...]:
        return tuple(
            ClarificationRecord(**dict(row))
            for row in self._connection.execute(
                """SELECT * FROM clarifications
                WHERE experiment_id=? ORDER BY sequence_id""",
                (experiment_id,),
            )
        )

    def append_event(
        self,
        event_id: str,
        experiment_id: str,
        protocol_revision_number: int,
        event_type: str,
        payload: Any,
        *,
        analysis_revision_number: int | None = None,
    ) -> ProtocolEventRecord:
        _identifier(event_id)
        _identifier(experiment_id, experiment=True)
        _identifier(event_type)
        if self.get_protocol_revision(
            experiment_id,
            protocol_revision_number,
        ) is None:
            raise UnknownProtocolReferenceError(
                "Protocol event revision reference does not exist."
            )
        if analysis_revision_number is not None:
            self.get_analysis_revision(
                experiment_id,
                protocol_revision_number,
                analysis_revision_number,
            )
        payload_json = _canonical_json(payload)
        existing = self._connection.execute(
            "SELECT * FROM protocol_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        expected = (
            experiment_id,
            protocol_revision_number,
            analysis_revision_number,
            event_type,
            payload_json,
        )
        if existing is not None:
            actual = (
                existing["experiment_id"],
                existing["protocol_revision_number"],
                existing["analysis_revision_number"],
                existing["event_type"],
                existing["payload_json"],
            )
            if actual != expected:
                raise DuplicateProtocolIdentifierError(
                    "Protocol event identifier already has different content."
                )
            return self._event_record(existing)
        try:
            with self._connection:
                cursor = self._execute_write(
                    """INSERT INTO protocol_events
                    (event_id,experiment_id,protocol_revision_number,
                     analysis_revision_number,event_type,payload_json,recorded_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        event_id,
                        experiment_id,
                        protocol_revision_number,
                        analysis_revision_number,
                        event_type,
                        payload_json,
                        _now(),
                    ),
                )
        except sqlite3.Error as exc:
            raise ProtocolTransactionError(
                "Protocol event append transaction failed."
            ) from exc
        row = self._connection.execute(
            "SELECT * FROM protocol_events WHERE sequence_id=?",
            (cursor.lastrowid,),
        ).fetchone()
        return self._event_record(row)

    @staticmethod
    def _event_record(row: sqlite3.Row) -> ProtocolEventRecord:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProtocolSerializationError(
                "Stored Protocol event payload is malformed."
            ) from exc
        return ProtocolEventRecord(
            sequence_id=row["sequence_id"],
            event_id=row["event_id"],
            experiment_id=row["experiment_id"],
            protocol_revision_number=row["protocol_revision_number"],
            analysis_revision_number=row["analysis_revision_number"],
            event_type=row["event_type"],
            payload=payload,
            recorded_at=row["recorded_at"],
        )

    def list_events(
        self,
        experiment_id: str,
    ) -> tuple[ProtocolEventRecord, ...]:
        return tuple(
            self._event_record(row)
            for row in self._connection.execute(
                """SELECT * FROM protocol_events
                WHERE experiment_id=? ORDER BY sequence_id""",
                (experiment_id,),
            )
        )


def initialize_protocol_store(
    settings: ProtocolPersistenceSettings,
) -> ProtocolStore:
    """Explicit enabled path that creates only the separate Protocol store."""

    if not settings.enabled:
        raise ProtocolFeatureDisabledError(
            "Protocol persistence is disabled."
        )
    if settings.data_dir is None or not settings.data_dir.is_absolute():
        raise ProtocolConfigurationError(
            "Protocol persistence requires an absolute data directory."
        )
    data_dir = settings.data_dir
    database_path = data_dir / PROTOCOL_DATABASE_FILENAME
    existed = database_path.exists()
    connection: sqlite3.Connection | None = None
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        file_store = ProtocolFileStore(data_dir)
        connection = sqlite3.connect(database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if existed:
            try:
                row = connection.execute(
                    "SELECT schema_version FROM schema_metadata"
                ).fetchone()
            except sqlite3.Error as exc:
                raise UnsupportedProtocolSchemaError(
                    "Protocol storage schema is unsupported."
                ) from exc
            if row is None or row["schema_version"] != PROTOCOL_SCHEMA_VERSION:
                raise UnsupportedProtocolSchemaError(
                    "Protocol storage schema version is unsupported."
                )
        else:
            connection.executescript(SCHEMA)
        return ProtocolStore(connection, data_dir, file_store)
    except (
        ProtocolConfigurationError,
        ProtocolFeatureDisabledError,
        ProtocolFileStoreError,
        UnsupportedProtocolSchemaError,
    ):
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise ProtocolStorageInitializationError(
            "Protocol storage could not be initialized."
        ) from exc
