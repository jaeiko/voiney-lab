"""Idempotent, event-driven experiment records owned by the server.

The service stores only explicit workflow events and user-confirmed observations.
It never infers completion, approval, or a laboratory result from model output.
"""

from __future__ import annotations

import asyncio
import hashlib
import csv
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from voiney_lab.report_projection import project_protocol_for_report, project_step_for_report

log = logging.getLogger(__name__)


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_STATUSES = frozenset({"in_progress", "completed", "blocked", "stopped", "incomplete"})


@dataclass(frozen=True)
class ExperimentReportSettings:
    enabled: bool
    database_path: Path | None = None

    @classmethod
    def from_environment(cls) -> "ExperimentReportSettings":
        raw = os.environ.get(
            "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", ""
        ).strip().casefold()
        if raw in _FALSE:
            return cls(False)
        if raw not in _TRUE:
            raise ValueError(
                "VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED must be a boolean"
            )
        path = os.environ.get(
            "VOINEY_LAB_EXPERIMENT_REPORT_DB",
            os.environ.get("VOINEY_LAB_EXPERIMENT_REPORTS_DATABASE", ""),
        ).strip()
        if not path:
            raise ValueError(
                "VOINEY_LAB_EXPERIMENT_REPORT_DB is required"
            )
        return cls(True, Path(path))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _clean_text(value: str, *, maximum: int = 1600) -> str:
    if not isinstance(value, str):
        raise ValueError("report text is invalid")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum:
        raise ValueError("report text is invalid")
    return cleaned


class ExperimentReportStore:
    """Small SQLite store with one report per procedure session."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @property
    def database_path(self) -> Path:
        return self.path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiment_report_metadata (
              schema_version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiment_reports (
              report_id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL UNIQUE,
              protocol_id TEXT NOT NULL,
              protocol_title TEXT NOT NULL,
              protocol_revision TEXT NOT NULL,
              protocol_sha256 TEXT NOT NULL,
              readiness_status TEXT NOT NULL,
              development_only INTEGER NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              ended_at TEXT,
              timezone TEXT NOT NULL,
              anomaly_count INTEGER NOT NULL DEFAULT 0,
              blocker_count INTEGER NOT NULL DEFAULT 0,
              finalization_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS experiment_report_events (
              id INTEGER PRIMARY KEY,
              report_id TEXT NOT NULL REFERENCES experiment_reports(report_id),
              event_key TEXT NOT NULL,
              event_type TEXT NOT NULL,
              step_id TEXT,
              step_label TEXT,
              user_wording TEXT,
              category TEXT,
              severity TEXT,
              confirmation_state TEXT,
              source_tier TEXT,
              citation_identities TEXT NOT NULL,
              payload TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(report_id, event_key)
            );
            -- Lane RP, decision 9: the report's prose, written once when the
            -- experiment ends. Derived output, not part of the event ledger.
            CREATE TABLE IF NOT EXISTS experiment_report_prose (
              report_id TEXT PRIMARY KEY REFERENCES experiment_reports(report_id),
              writer TEXT NOT NULL,
              reply TEXT,
              failure TEXT NOT NULL DEFAULT '',
              event_count INTEGER NOT NULL,
              prepared_at TEXT NOT NULL
            );
            """
        )
        rows = connection.execute(
            "SELECT schema_version FROM experiment_report_metadata"
        ).fetchall()
        if not rows:
            connection.execute(
                "INSERT INTO experiment_report_metadata(schema_version) VALUES (?)",
                (self.SCHEMA_VERSION,),
            )
        elif len(rows) != 1 or rows[0][0] != self.SCHEMA_VERSION:
            connection.close()
            raise RuntimeError("experiment report schema is unsupported")
        return connection

    def open_report(
        self,
        *,
        session_id: str,
        protocol_id: str,
        protocol_title: str,
        protocol_revision: str,
        protocol_sha256: str,
        readiness_status: str,
        development_only: bool,
    ) -> dict[str, Any]:
        session_id = _clean_identifier(session_id, "session_id")
        protocol_id = _clean_identifier(protocol_id, "protocol_id")
        protocol_revision = _clean_identifier(protocol_revision, "protocol_revision")
        if not re.fullmatch(r"[0-9a-f]{64}", protocol_sha256):
            raise ValueError("protocol_sha256 is invalid")
        title = _clean_text(protocol_title, maximum=400)
        readiness = _clean_identifier(readiness_status, "readiness_status")
        report_id = "ER-" + hashlib.sha256(
            f"{session_id}\x1f{protocol_id}\x1f{protocol_revision}".encode()
        ).hexdigest()[:20].upper()
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO experiment_reports(
                  report_id,session_id,protocol_id,protocol_title,
                  protocol_revision,protocol_sha256,readiness_status,
                  development_only,status,started_at,timezone
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    report_id, session_id, protocol_id, title,
                    protocol_revision, protocol_sha256, readiness,
                    int(bool(development_only)), "in_progress", now, "UTC",
                ),
            )
        return self.get_report(report_id)

    def append_event(
        self,
        report_id: str,
        *,
        event_key: str,
        event_type: str,
        step_id: str | None = None,
        step_label: str | None = None,
        user_wording: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        confirmation_state: str | None = None,
        source_tier: str | None = None,
        citation_identities: tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        report_id = _clean_identifier(report_id, "report_id")
        event_key = _clean_identifier(event_key, "event_key")
        event_type = _clean_identifier(event_type, "event_type")
        for value, field in (
            (step_id, "step_id"), (step_label, "step_label"),
            (category, "category"), (severity, "severity"),
            (confirmation_state, "confirmation_state"),
            (source_tier, "source_tier"),
        ):
            if value is not None:
                _clean_identifier(value, field)
        wording = (
            _clean_text(user_wording, maximum=800)
            if user_wording is not None else None
        )
        citations = tuple(
            _clean_identifier(item, "citation_identity")
            for item in citation_identities[:20]
        )
        safe_payload = payload or {}
        encoded = json.dumps(
            safe_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if len(encoded) > 12000:
            raise ValueError("report payload is too large")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO experiment_report_events(
                  report_id,event_key,event_type,step_id,step_label,user_wording,
                  category,severity,confirmation_state,source_tier,
                  citation_identities,payload,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    report_id, event_key, event_type, step_id, step_label,
                    wording, category, severity, confirmation_state, source_tier,
                    json.dumps(citations), encoded, _now(),
                ),
            )
            if cursor.rowcount and event_type == "anomaly":
                connection.execute(
                    "UPDATE experiment_reports SET anomaly_count=anomaly_count+1 "
                    "WHERE report_id=?",
                    (report_id,),
                )
            if cursor.rowcount and event_type == "blocked":
                connection.execute(
                    "UPDATE experiment_reports SET blocker_count=blocker_count+1 "
                    "WHERE report_id=?",
                    (report_id,),
                )
        result = self.get_report(report_id)
        result["event_inserted"] = bool(cursor.rowcount)
        return result

    def finalize(
        self, report_id: str, *, status: str, event_key: str
    ) -> dict[str, Any]:
        report_id = _clean_identifier(report_id, "report_id")
        event_key = _clean_identifier(event_key, "event_key")
        if status not in _STATUSES - {"in_progress"}:
            raise ValueError("report status is invalid")
        self.append_event(
            report_id, event_key=event_key, event_type="report_finalized",
            payload={"status": status},
        )
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE experiment_reports
                   SET status=?, ended_at=COALESCE(ended_at,?),
                       finalization_version=CASE
                         WHEN ended_at IS NULL THEN finalization_version+1
                         ELSE finalization_version END
                 WHERE report_id=?
                """,
                (status, _now(), report_id),
            )
        return self.get_report(report_id)

    def get_report(self, report_id: str) -> dict[str, Any]:
        report_id = _clean_identifier(report_id, "report_id")
        with self._connect() as connection:
            report = connection.execute(
                "SELECT * FROM experiment_reports WHERE report_id=?", (report_id,)
            ).fetchone()
            if report is None:
                raise KeyError("experiment report not found")
            events = connection.execute(
                """
                SELECT event_key,event_type,step_id,step_label,user_wording,
                       category,severity,confirmation_state,source_tier,
                       citation_identities,payload,created_at
                  FROM experiment_report_events
                 WHERE report_id=? ORDER BY id
                """,
                (report_id,),
            ).fetchall()
        result = dict(report)
        result["development_only"] = bool(result["development_only"])
        result.update(self._day_sequence(result))
        result["events"] = [
            {
                **{key: row[key] for key in (
                    "event_key", "event_type", "step_id", "step_label",
                    "user_wording", "category", "severity",
                    "confirmation_state", "source_tier", "created_at",
                )},
                "citation_identities": json.loads(row["citation_identities"]),
                "payload": json.loads(row["payload"]),
            }
            for row in events
        ]
        return result

    def list_reports(self, *, session_id: str | None = None) -> list[dict[str, Any]]:
        """List report summaries in stable start order for one development session."""

        parameters: tuple[Any, ...] = ()
        where = ""
        if session_id is not None:
            where = "WHERE session_id=?"
            parameters = (_clean_identifier(session_id, "session_id"),)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT report_id,session_id,protocol_id,protocol_title,status,started_at,ended_at,
                       anomaly_count,blocker_count,finalization_version,
                       development_only
                  FROM experiment_reports {where}
                 ORDER BY started_at,report_id
                """,
                parameters,
            ).fetchall()
        return [
            {
                **dict(row), "development_only": bool(row["development_only"]),
                **self._day_sequence(dict(row)),
            }
            for row in rows
        ]

    def _day_sequence(self, report: dict[str, Any]) -> dict[str, Any]:
        """The record's place among those started the same day (lane R6, decision 5).

        Records keep their start in UTC (``timezone``), so the day is the UTC
        date of ``started_at``; 1 is the first record started that day.
        """

        day = str(report["started_at"])[:10]
        with self._connect() as connection:
            sequence = connection.execute(
                """
                SELECT COUNT(*) FROM experiment_reports
                 WHERE substr(started_at,1,10)=?
                   AND (started_at<? OR (started_at=? AND report_id<=?))
                """,
                (day, report["started_at"], report["started_at"], report["report_id"]),
            ).fetchone()[0]
        return {"day_sequence": sequence, "day_sequence_date": day}

    def aggregate_metrics(self) -> dict[str, Any]:
        """Return privacy-minimized operational aggregates for administrators.

        This projection intentionally omits report/session identifiers, protocol
        titles, free-form user wording, event payloads, and citation identities.
        """

        tracked_events = (
            "session_started",
            "step_presented",
            "step_completed",
            "timer_started",
            "observation",
            "anomaly",
            "blocked",
            "workflow_paused",
            "workflow_resumed",
            "source_consulted",
            "workflow_completed",
        )
        with self._connect() as connection:
            report_row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(anomaly_count) AS anomalies,
                       SUM(blocker_count) AS blockers
                  FROM experiment_reports
                """
            ).fetchone()
            status_rows = connection.execute(
                """
                SELECT status,COUNT(*) AS count
                  FROM experiment_reports
                 GROUP BY status ORDER BY status
                """
            ).fetchall()
            placeholders = ",".join("?" for _ in tracked_events)
            event_rows = connection.execute(
                f"""
                SELECT event_type,COUNT(*) AS count
                  FROM experiment_report_events
                 WHERE event_type IN ({placeholders})
                 GROUP BY event_type ORDER BY event_type
                """,
                tracked_events,
            ).fetchall()
            blocked_rows = connection.execute(
                """
                SELECT COALESCE(step_label,'unlabeled') AS step_label,
                       COUNT(*) AS count
                  FROM experiment_report_events
                 WHERE event_type='blocked'
                 GROUP BY COALESCE(step_label,'unlabeled')
                 ORDER BY count DESC,step_label
                 LIMIT 10
                """
            ).fetchall()
        total = int(report_row["total"] or 0)
        completed = int(report_row["completed"] or 0)
        event_counts = {item: 0 for item in tracked_events}
        event_counts.update(
            {str(row["event_type"]): int(row["count"]) for row in event_rows}
        )
        return {
            "reports": {
                "total": total,
                "completed": completed,
                "completion_rate": round(completed / total, 4) if total else None,
                "by_status": {
                    str(row["status"]): int(row["count"])
                    for row in status_rows
                },
            },
            "workflow_events": event_counts,
            "quality": {
                "anomalies": int(report_row["anomalies"] or 0),
                "blockers": int(report_row["blockers"] or 0),
                "common_blocked_steps": [
                    {
                        "step_label": str(row["step_label"]),
                        "count": int(row["count"]),
                    }
                    for row in blocked_rows
                ],
            },
            "privacy": {
                "raw_audio_included": False,
                "transcripts_included": False,
                "free_text_included": False,
                "report_identifiers_included": False,
            },
        }

    def export_json(self, report_id: str) -> bytes:
        return (
            json.dumps(
                self.get_report(report_id), ensure_ascii=False,
                sort_keys=True, indent=2,
            ) + "\n"
        ).encode()

    def export_markdown(
        self, report_id: str, narrative: ReportNarrative | None = None,
        *, fixture: Any = None,
    ) -> bytes:
        """The researcher's report as Markdown (same structure as the .docx).

        Without a narrative the server's own sentences fill every section.
        ``fixture`` is the protocol the session ran; left out, it is looked up
        the way the server loads it.
        """

        return render_markdown(self._narrative(report_id, narrative, fixture)).encode()

    def _narrative(
        self, report_id: str, narrative: ReportNarrative | None, fixture: Any,
    ) -> ReportNarrative:
        """The given narrative, else the prepared prose, else the server's sentences.

        Prepared prose is checked again against the record as it is now, so a
        photo or note added after it was written still appears in the tables.
        """

        if narrative is not None and narrative.facts is not None:
            return narrative
        report = self.get_report(report_id)
        facts = ReportWriterBrain.facts_for(
            report, list(report["events"]), fixture=_AUTO if fixture is None else fixture)
        prose = self.get_prose(report_id)
        if prose is None or prose["writer"] == SERVER_WRITER:
            return narrative_from_sections(facts)
        return narrative_from_reply(
            facts, prose["reply"], failure=prose["failure"], writer=prose["writer"],
            written_at=prose["prepared_at"],
        )

    def save_prose(
        self, report_id: str, *, writer: str, reply: Mapping[str, Any] | None,
        failure: str, event_count: int,
    ) -> None:
        """Keep one report's prose (replacing any earlier one)."""

        report_id = _clean_identifier(report_id, "report_id")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO experiment_report_prose(
                  report_id,writer,reply,failure,event_count,prepared_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (report_id, writer[:200],
                 None if reply is None else json.dumps(reply, ensure_ascii=False),
                 failure[:200], int(event_count), _now()),
            )

    def get_prose(self, report_id: str) -> dict[str, Any] | None:
        report_id = _clean_identifier(report_id, "report_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiment_report_prose WHERE report_id=?", (report_id,)
            ).fetchone()
        if row is None:
            return None
        prose = dict(row)
        prose["reply"] = json.loads(prose["reply"]) if prose["reply"] is not None else None
        return prose

    def export_csv(self, report_id: str) -> bytes:
        """Export the stable event timeline as UTF-8 CSV."""

        report = self.get_report(report_id)
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow((
            "report_id", "event_key", "event_type", "step_id", "step_label",
            "category", "severity", "confirmation_state", "source_tier",
            "user_wording", "created_at",
            "source_duration_seconds", "timer_started_at", "elapsed_seconds",
            "remaining_seconds", "completion_state", "demo_bypassed",
        ))
        for event in report["events"]:
            payload = event.get("payload") or {}
            timer = payload.get("timer") if isinstance(payload.get("timer"), dict) else {}
            writer.writerow((
                report["report_id"], event["event_key"], event["event_type"],
                event["step_id"] or "", event["step_label"] or "",
                event["category"] or "", event["severity"] or "",
                event["confirmation_state"] or "", event["source_tier"] or "",
                event["user_wording"] or "", event["created_at"],
                timer.get("source_duration_seconds", ""),
                timer.get("started_at", ""),
                timer.get("elapsed_seconds", ""),
                timer.get("remaining_seconds", ""),
                timer.get("completion_state", ""),
                timer.get("demo_bypassed", ""),
            ))
        return output.getvalue().encode("utf-8-sig")

    def docx_bytes(self, report_id: str) -> bytes:
        return self.export_docx(report_id)

    def export_docx(
        self, report_id: str, narrative: ReportNarrative | None = None,
        *, fixture: Any = None,
    ) -> bytes:
        """The researcher's report as .docx (same structure as the Markdown).

        The SQLite event ledger remains the authority; this document is a
        derived, human-readable projection of it.
        """

        return render_docx(self._narrative(report_id, narrative, fixture))


@dataclass(frozen=True)
class StepExecutionContext:
    """Detailed factual context for one protocol step and its execution status."""

    step_id: str
    step_label: str
    section_title: str
    instruction_source_text: str
    sub_actions: tuple[str, ...] = ()
    quantities: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    expected_results: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    tips: tuple[str, ...] = ()
    source_page: int = 1
    evidence_ids: tuple[str, ...] = ()
    entered_at: str | None = None
    completed_at: str | None = None
    completion_state: str = "not_started"  # "completed", "in_progress", "not_started"
    timer_configuration: str | None = None
    timer_actuals: str | None = None
    user_confirmed_observations: tuple[str, ...] = ()
    anomalies_deviations: tuple[str, ...] = ()
    applicable_safety_references: tuple[str, ...] = ()

    @property
    def instruction(self) -> str:
        return self.instruction_source_text

    def public_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_label": self.step_label,
            "section_title": self.section_title,
            "instruction_source_text": self.instruction_source_text,
            "sub_actions": list(self.sub_actions),
            "quantities": list(self.quantities),
            "conditions": list(self.conditions),
            "expected_results": list(self.expected_results),
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "tips": list(self.tips),
            "source_page": self.source_page,
            "evidence_ids": list(self.evidence_ids),
            "entered_at": self.entered_at,
            "completed_at": self.completed_at,
            "completion_state": self.completion_state,
            "timer_configuration": self.timer_configuration,
            "timer_actuals": self.timer_actuals,
            "user_confirmed_observations": list(self.user_confirmed_observations),
            "anomalies_deviations": list(self.anomalies_deviations),
            "applicable_safety_references": list(self.applicable_safety_references),
        }


@dataclass(frozen=True)
class GroundedReportContext:
    """Rich factual context constructed from the ledger and verified protocol store."""

    report_metadata: dict[str, Any]
    protocol_metadata: dict[str, Any]
    experiment_objective: str
    materials: tuple[str, ...]
    equipment: tuple[str, ...]
    prerequisites: tuple[str, ...]
    executed_steps: tuple[StepExecutionContext, ...]
    all_protocol_steps: tuple[StepExecutionContext, ...]
    observations: tuple[dict[str, Any], ...]
    timers: tuple[dict[str, Any], ...]
    anomalies: tuple[dict[str, Any], ...]
    deviations: tuple[dict[str, Any], ...]
    safety_pack_summary: dict[str, Any] | None
    source_references: tuple[str, ...]
    session_timing: dict[str, Any]
    event_ledger: tuple[dict[str, Any], ...]

    @property
    def protocol_id(self) -> str:
        return str(self.protocol_metadata.get("protocol_id") or "")

    @property
    def report_id(self) -> str:
        return str(self.report_metadata.get("report_id") or "")

    @property
    def all_steps(self) -> tuple[StepExecutionContext, ...]:
        return self.all_protocol_steps

    def public_dict(self) -> dict[str, Any]:
        return {
            "report_metadata": self.report_metadata,
            "protocol_metadata": self.protocol_metadata,
            "experiment_objective": self.experiment_objective,
            "materials": list(self.materials),
            "equipment": list(self.equipment),
            "prerequisites": list(self.prerequisites),
            "executed_steps": [s.public_dict() for s in self.executed_steps],
            "observations": list(self.observations),
            "timers": list(self.timers),
            "anomalies": list(self.anomalies),
            "deviations": list(self.deviations),
            "safety_pack_summary": self.safety_pack_summary,
            "source_references": list(self.source_references),
            "session_timing": self.session_timing,
            "total_event_count": len(self.event_ledger),
        }


def build_grounded_report_context(
    report_data: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
    *,
    fixture: Any = None,
    lookup: bool = True,
) -> GroundedReportContext:
    """Rehydrate protocol steps and merge execution event timestamps, observations, and timers."""
    if events is None:
        events = list(report_data.get("events") or [])
    protocol_id = str(report_data.get("protocol_id") or "")
    protocol_rev = str(report_data.get("protocol_revision") or "")
    protocol_title = str(report_data.get("protocol_title") or "Experiment Protocol")

    step_events: dict[str, list[dict[str, Any]]] = {}
    obs_list: list[dict[str, Any]] = []
    timer_list: list[dict[str, Any]] = []
    anomaly_list: list[dict[str, Any]] = []
    deviation_list: list[dict[str, Any]] = []
    step_snapshots: dict[str, dict[str, Any]] = {}

    for e in events:
        s_lbl = str(e.get("step_label") or "")
        if s_lbl and s_lbl != "—":
            step_events.setdefault(s_lbl, []).append(e)
        ev_type = str(e.get("event_type") or "")
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}

        if "step_snapshot" in payload and isinstance(payload["step_snapshot"], dict):
            snap = payload["step_snapshot"]
            snap_lbl = str(snap.get("step_label") or s_lbl)
            if snap_lbl:
                step_snapshots[snap_lbl] = snap

        if ev_type == "observation":
            obs_list.append(e)
        elif ev_type in ("timer_started", "timer_expired") or "timer" in payload:
            timer_list.append(e)
        elif ev_type in ("anomaly", "system_anomaly") or e.get("anomaly_category"):
            anomaly_list.append(e)
        elif ev_type in ("workflow_paused", "workflow_blocked"):
            deviation_list.append(e)

    # The protocol the session ran, looked up the way the server loads it
    # (report_protocol_fixture) unless the caller passes it or asks not to.
    if fixture is None and lookup:
        fixture, _ = report_protocol_fixture(report_data)
    stored_protocol = fixture.draft.protocol if fixture is not None else None

    all_steps: list[StepExecutionContext] = []
    executed_steps: list[StepExecutionContext] = []
    all_materials: set[str] = set()
    all_equipment: set[str] = set()
    all_prereqs: set[str] = set()
    objective = f"본 실험은 '{protocol_title}' 지침에 따라 표준화된 실험 절차를 수행하고 검증된 실험 데이터를 기록하는 것을 목적으로 한다."

    if stored_protocol is not None:
        projected = project_protocol_for_report(stored_protocol)
        if projected.objective:
            objective = projected.objective
        all_materials = set(projected.materials)
        all_equipment = set(projected.equipment)
        all_prereqs = set(projected.prerequisites)

        for p_step in projected.steps:
            lbl = p_step.step_label
            evs = step_events.get(lbl, [])
            entered = next((_human_event_clock(ev.get("created_at")) for ev in evs if ev.get("event_type") in ("step_entered", "step_presented", "session_started")), None)
            completed = next((_human_event_clock(ev.get("created_at")) for ev in evs if ev.get("event_type") == "step_completed"), None)
            state = "completed" if completed else ("in_progress" if entered else "not_started")

            # Timers
            t_cfg = None
            t_act = None
            t_ev = next((ev for ev in evs if ev.get("event_type") == "timer_started" or "timer" in (ev.get("payload") or {})), None)
            if t_ev:
                t_pay = (t_ev.get("payload") or {}).get("timer", {})
                dur = t_pay.get("source_duration_seconds", t_pay.get("duration_seconds"))
                elap = t_pay.get("elapsed_seconds")
                if dur:
                    t_cfg = f"{dur}초 ({_format_elapsed_clock(dur)})"
                if elap:
                    t_act = f"{elap}초 경과 ({_format_elapsed_clock(elap)})"

            # Observations
            obs_collected = []
            for ev in evs:
                if ev.get("event_type") == "observation":
                    text = str(ev.get("user_wording") or (ev.get("payload") or {}).get("text") or "관찰 기록")
                    if text:
                        obs_collected.append(text)
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                if "observations" in payload and isinstance(payload["observations"], (list, tuple)):
                    for o in payload["observations"]:
                        if o and str(o).strip():
                            obs_collected.append(str(o).strip())
            step_obs = tuple(obs_collected)

            # Anomalies
            step_anom = tuple(
                str(ev.get("user_wording") or (ev.get("payload") or {}).get("text") or "이상 보고")
                for ev in evs if ev.get("event_type") in ("anomaly", "system_anomaly") or ev.get("anomaly_category")
            )

            ctx_step = StepExecutionContext(
                step_id=p_step.step_id,
                step_label=lbl,
                section_title=p_step.section_title,
                instruction_source_text=p_step.instruction_source_text,
                sub_actions=p_step.sub_actions,
                quantities=p_step.quantities,
                conditions=p_step.conditions,
                expected_results=p_step.expected_results,
                warnings=p_step.warnings,
                notes=p_step.notes,
                tips=p_step.tips,
                source_page=p_step.source_page,
                evidence_ids=p_step.evidence_ids,
                entered_at=entered,
                completed_at=completed,
                completion_state=state,
                timer_configuration=t_cfg,
                timer_actuals=t_act,
                user_confirmed_observations=step_obs,
                anomalies_deviations=step_anom,
            )
            all_steps.append(ctx_step)
            if state in ("completed", "in_progress") or step_obs or step_anom:
                executed_steps.append(ctx_step)

    else:
        # Fall back to step_snapshots or step_events
        for lbl, evs in sorted(step_events.items(), key=lambda x: str(x[0])):
            snap = step_snapshots.get(lbl, {})
            entered = next((_human_event_clock(ev.get("created_at")) for ev in evs if ev.get("event_type") in ("step_entered", "step_presented", "session_started")), None)
            completed = next((_human_event_clock(ev.get("created_at")) for ev in evs if ev.get("event_type") == "step_completed"), None)
            state = "completed" if completed else "in_progress"

            # Lane RP: no placeholder text. A step whose words are unknown
            # stays empty, and the report says the source could not be read.
            inst = snap.get("instruction_source_text") or snap.get("instruction") or ""
            sec_title = snap.get("section_title") or "Experiment Execution"
            quantities = tuple(snap.get("quantities") or ())
            conditions = tuple(snap.get("conditions") or ())
            exp_res = tuple(snap.get("expected_results") or ())
            warnings = tuple(snap.get("warnings") or ())
            page = int(snap.get("source_page") or 1)

            obs_collected = []
            for ev in evs:
                if ev.get("event_type") == "observation":
                    text = str(ev.get("user_wording") or (ev.get("payload") or {}).get("text") or "관찰 기록")
                    if text:
                        obs_collected.append(text)
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                if "observations" in payload and isinstance(payload["observations"], (list, tuple)):
                    for o in payload["observations"]:
                        if o and str(o).strip():
                            obs_collected.append(str(o).strip())
            step_obs = tuple(obs_collected)

            step_anom = tuple(
                str(ev.get("user_wording") or (ev.get("payload") or {}).get("text") or "이상 보고")
                for ev in evs if ev.get("event_type") in ("anomaly", "system_anomaly") or ev.get("anomaly_category")
            )

            ctx_step = StepExecutionContext(
                step_id=snap.get("step_id") or f"step-{lbl}",
                step_label=lbl,
                section_title=sec_title,
                instruction_source_text=inst,
                quantities=quantities,
                conditions=conditions,
                expected_results=exp_res,
                warnings=warnings,
                source_page=page,
                entered_at=entered,
                completed_at=completed,
                completion_state=state,
                user_confirmed_observations=step_obs,
                anomalies_deviations=step_anom,
            )
            all_steps.append(ctx_step)
            executed_steps.append(ctx_step)

    refs = [f"실험 PDF: {report_data.get('protocol_title') or protocol_title} (ID: {protocol_id}, Rev: {protocol_rev})"]
    if report_data.get("protocol_sha256"):
        refs.append(f"PDF SHA-256: {report_data.get('protocol_sha256')}")

    return GroundedReportContext(
        report_metadata={
            "report_id": report_data.get("report_id"),
            "session_id": report_data.get("session_id"),
            "status": report_data.get("status"),
            "anomaly_count": report_data.get("anomaly_count", len(anomaly_list)),
            "blocker_count": report_data.get("blocker_count", 0),
            "finalization_version": report_data.get("finalization_version", 0),
        },
        protocol_metadata={
            "protocol_id": protocol_id,
            "protocol_title": protocol_title,
            "protocol_revision": protocol_rev,
            "protocol_sha256": report_data.get("protocol_sha256"),
            "total_steps": len(all_steps),
        },
        experiment_objective=objective,
        materials=tuple(sorted(all_materials)),
        equipment=tuple(sorted(all_equipment)),
        prerequisites=tuple(sorted(all_prereqs)),
        executed_steps=tuple(executed_steps),
        all_protocol_steps=tuple(all_steps),
        observations=tuple(obs_list),
        timers=tuple(timer_list),
        anomalies=tuple(anomaly_list),
        deviations=tuple(deviation_list),
        safety_pack_summary=None,
        source_references=tuple(refs),
        session_timing={
            "started_at": report_data.get("started_at"),
            "ended_at": report_data.get("ended_at"),
            "timezone": report_data.get("timezone", "UTC"),
        },
        event_ledger=tuple(events),
    )


# --- The report a researcher reads (lane RP, decisions of 2026-10-06) -------------
#
# The report is an experiment report, not a log of the system: purpose,
# background, materials and methods, results, discussion, conclusion,
# and references. The record's identifiers, hashes and raw event list stay in
# the ledger and the JSON export; the researcher's report leaves them out.
#
# The server builds every fact from the ledger and the protocol it ran
# (``build_report_facts``): the step tables, the results table, what was done
# differently from the source, and what still needs checking. The model writes
# only the prose around those facts, from a projection with no identifiers,
# hashes, status values or command names in it, and each of its sections is
# checked before it is used (``check_report_sections``); a section that fails
# is replaced by the server's own sentence for it. The report uses no outside
# knowledge: Google Search grounding was taken out (decision of 2026-10-06,
# option (a)) because the service terms forbid caching, storing, or rewriting
# grounded results, and a report is a stored file other people read.

REPORT_TIMEZONE_SETTING = "VOINEY_LAB_REPORT_TIMEZONE"
DEFAULT_REPORT_TIMEZONE = "Asia/Seoul"

#: The model-written sections, in report order.
MODEL_SECTIONS = (
    "purpose", "background", "methods_summary",
    "results_summary", "discussion_review", "conclusion", "next_steps",
)
_SECTION_NAMES = {
    "purpose": "1. 실험 목적",
    "background": "2. 배경·원리",
    "methods_summary": "3. 방법 요약",
    "results_summary": "4. 결과 요약",
    "discussion_review": "5. 고찰 (다)",
    "conclusion": "6. 결론",
    "next_steps": "6. 다음 할 일",
}
_WEEKDAYS = "월화수목금토일"


def report_timezone(environment: Mapping[str, str] | None = None) -> ZoneInfo:
    """The researcher's time zone for the report's clock times."""

    env = os.environ if environment is None else environment
    name = env.get(REPORT_TIMEZONE_SETTING, "").strip() or DEFAULT_REPORT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("report timezone setting is not a known zone; using %s", DEFAULT_REPORT_TIMEZONE)
        return ZoneInfo(DEFAULT_REPORT_TIMEZONE)


@dataclass(frozen=True)
class ReportSource:
    """One numbered reference: [1] is the protocol's own PDF."""

    number: int
    kind: str  # "protocol"
    title: str
    url: str = ""
    note: str = ""


@dataclass(frozen=True)
class ReportRecord:
    """One thing the researcher recorded, in their own words."""

    number: int
    step_label: str
    kind: str  # "관찰" | "이상" | "사진"
    text: str
    at: str  # local HH:MM


@dataclass(frozen=True)
class ReportItem:
    """A material or a piece of equipment, as the protocol lists it."""

    kind: str  # "재료" | "장비"
    name: str  # as listed, with its whitespace made single
    steps: tuple[str, ...]  # source steps whose text names it (decision 3)


@dataclass(frozen=True)
class ReportStepFacts:
    label: str
    step_id: str
    section: str
    text: str
    source_text: str
    translated: bool
    expected: tuple[str, ...]
    source_timer_seconds: int | None
    completed: bool
    completed_at: str
    timer_note: str
    records: tuple[ReportRecord, ...]


@dataclass(frozen=True)
class ReportFacts:
    """Everything the report states, built by the server from the record and the PDF."""

    protocol_title: str
    protocol_loaded: bool
    total_steps: int
    run_rows: tuple[tuple[str, str], ...]
    completed_labels: tuple[str, ...]
    outcome: str  # "completed" | "stopped" | "in_progress"
    stop_label: str | None
    steps: tuple[ReportStepFacts, ...]
    records: tuple[ReportRecord, ...]
    purpose_from_pdf: str
    keywords: str
    sections: tuple[str, ...]
    materials: tuple[str, ...]
    equipment: tuple[str, ...]
    deviations: tuple[str, ...]
    confirmed: tuple[str, ...]
    to_check: tuple[str, ...]
    protocol_reference: ReportSource
    zone: ZoneInfo
    items: tuple[ReportItem, ...] = ()

    @property
    def record_texts(self) -> tuple[str, ...]:
        return tuple(record.text for record in self.records)


def _source_steps_from_fixture(fixture: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    timers = getattr(fixture, "timer_manifest", None) or {}
    for section in fixture.draft.protocol.sections:
        title = " ".join(str(getattr(section, "title_source_text", "") or "").split())
        for step in section.steps:
            source = " ".join(str(step.instruction_source_text or "").split())
            korean = None
            localized = getattr(fixture, "localized_fact", None)
            if callable(localized):
                korean = localized(step.step_id, "current_step")
            if korean:
                korean = re.sub(r"^\s*\d+\s*단계\s*[:：]\s*", "", " ".join(korean.split()))
            expected = [" ".join(str(item.source_text).split()) for item in step.expected_results]
            whole = [source]
            for action in getattr(step, "sub_actions", ()) or ():
                whole.append(" ".join(str(getattr(action, "instruction_source_text", "") or "").split()))
                for item in getattr(action, "expected_results", ()) or ():
                    text = " ".join(str(item.source_text).split())
                    if text not in expected:
                        expected.append(text)
            steps.append({
                "label": str(step.source_label), "step_id": str(step.step_id),
                "section": title, "source_text": source,
                "text": korean or source, "translated": bool(korean),
                "expected": tuple(expected),
                "timer": timers.get(step.step_id),
                # The step's whole source text, its sub-actions too: where a
                # material or piece of equipment is looked for (decision 3).
                "whole_source_text": " ".join(part for part in whole if part),
            })
    return steps


def _source_steps_from_context(context: "GroundedReportContext") -> list[dict[str, Any]]:
    return [
        {
            "label": step.step_label, "step_id": step.step_id,
            "section": step.section_title if step.section_title != "Experiment Execution" else "",
            "source_text": step.instruction_source_text, "text": step.instruction_source_text,
            "translated": False, "expected": tuple(step.expected_results), "timer": None,
        }
        for step in context.all_protocol_steps
    ]


def report_protocol_fixture(report_data: Mapping[str, Any]) -> tuple[Any, str]:
    """The protocol a report's session ran, as sessions load it, or (None, why).

    The configured development fixture when the record names it, otherwise the
    catalog's executable fixture for the protocol, accepted only when its
    revision is the one the record names. Lane RP: this lookup used to call
    ``load_executable_fixture`` with a revision argument it does not take, and
    any failure was swallowed, so the report printed placeholder steps.
    """

    protocol_id = str(report_data.get("protocol_id") or "")
    revision = str(report_data.get("protocol_revision") or "")
    try:
        from voiney_lab.server import (
            _configured_candidate_fixture,
            _open_protocol_catalog,
            server_config,
        )

        candidate = _configured_candidate_fixture(server_config())
        if candidate is not None and candidate.protocol_id == protocol_id:
            fixture = candidate
        else:
            catalog, store = _open_protocol_catalog()
            try:
                fixture = catalog.load_executable_fixture(protocol_id)
            finally:
                store.close()
    except Exception as exc:  # noqa: BLE001 -- reported on the report, never raised
        log.warning("report protocol lookup failed error=%s", type(exc).__name__)
        return None, "프로토콜 원문을 불러오지 못했다"
    if revision and getattr(fixture, "revision_id", revision) != revision:
        return None, "기록의 프로토콜 버전과 지금 프로토콜 버전이 달라 원문을 싣지 않았다"
    return fixture, ""


def _local(value: Any, zone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(zone)


def _duration_words(seconds: float | int | None) -> str:
    if seconds is None:
        return ""
    minutes = int(round(float(seconds) / 60))
    hours, minutes = divmod(max(minutes, 0), 60)
    if hours and minutes:
        return f"{hours}시간 {minutes}분"
    if hours:
        return f"{hours}시간"
    return f"{minutes}분"


def _label_runs(labels: Sequence[str]) -> str:
    """'1, 2, 3, 5' -> '1–3, 5' for numeric step labels."""

    numbers: list[int] = []
    for label in labels:
        if not str(label).isdigit():
            return ", ".join(str(item) for item in labels)
        numbers.append(int(label))
    if not numbers:
        return ""
    numbers = sorted(set(numbers))
    parts: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:] + [None]:  # type: ignore[list-item]
        if number is not None and number == previous + 1:
            previous = number
            continue
        parts.append(f"{start}–{previous}" if previous != start else f"{start}")
        if number is not None:
            start = previous = number
    return ", ".join(parts)


def _ranges(labels: Sequence[str]) -> str:
    """'1, 2, 3, 5' -> '1–3, 5단계' for numeric step labels."""

    runs = _label_runs(labels)
    return runs + "단계" if runs else ""


# --- Where each material and piece of equipment is used (decision 3) -----------
#
# The server looks for an item's name in each source step's text, and lists
# the steps it appears in; nothing is inferred. Names are looked for in this
# order, and the first that the steps hold is used:
#   1. the name as listed, without what is in parentheses, a catalog number,
#      or what follows a comma ("Formic acid, LC-MS grade" -> "Formic acid");
#   2. if no step holds that, the name without its company and grade words:
#      capitalized words, codes with digits and grade marks at its end
#      ("Ammonium bicarbonate Merck MilliporeSigma" -> "Ammonium bicarbonate"),
#      and a capitalized word in front of a lower-case one ("Promega trypsin"
#      -> "trypsin"). A name left with one word is used only when no other
#      listed item has that word, so "Glass pipettes" is not "pipettes".
# The last word may be singular or plural. Besides its name, an item is also
# found by the short name the source calls it: one in its own parentheses
# ("Lysogeny Broth (LB)") or one a step writes right after its name
# ("ammonium bicarbonate (AMBIC)"). Where items share a first word and a
# short name ("Lysogeny Broth (LB)", "Lysogeny Agar"), the word after the
# short name tells them apart ("LB agar" is the agar).

#: Words in a listed name that say its grade or how it is listed, not what it is.
_GRADE_WORDS = frozenset({"lc-ms", "hplc", "grade", "catalog", "cat.", "model", "name",
                          "type", "brand", "sku"})
_SHORT_NAME = re.compile(r"[A-Z][A-Z0-9]{1,7}")
_ASCII_WORD = "A-Za-z0-9"


def _word_forms(word: str) -> set[str]:
    """A word and its singular or plural, compared without case."""

    lower = word.casefold()
    forms = {lower, lower + "s", lower + "es"}
    if lower.endswith("es") and len(lower) > 4:
        forms.add(lower[:-2])
    if lower.endswith("s") and len(lower) > 3:
        forms.add(lower[:-1])
    return forms


def _name_pattern(words: Sequence[str]) -> re.Pattern[str]:
    *head, last = words
    last_forms = sorted((re.escape(form) for form in _word_forms(last)), key=len, reverse=True)
    body = r"\s+".join([re.escape(word) for word in head] + [f"(?:{'|'.join(last_forms)})"])
    return re.compile(rf"(?<![{_ASCII_WORD}]){body}(?![{_ASCII_WORD}])", re.I)


def _listed_words(name: str) -> list[str]:
    """The name's words without parentheses, a catalog number, or what follows a comma."""

    first_line = next((line for line in str(name).splitlines() if line.strip()), "")
    text = re.sub(r"\([^)]*\)", " ", first_line.replace("’", "'"))
    text = re.split(r"\bCat(?:alog)?\.?\s*#|#|,", text)[0]
    return text.split()


def _core_words(words: Sequence[str]) -> list[str]:
    """The name without its company and grade words (see above)."""

    core = list(words)
    while len(core) > 1 and (
        core[-1][:1].isupper() or any(ch.isdigit() for ch in core[-1])
        or core[-1].casefold() in _GRADE_WORDS
    ):
        core.pop()
    while len(core) > 1 and core[0][:1].isupper() and core[1][:1].islower():
        core.pop(0)
    return core


def item_step_labels(
    names: Sequence[tuple[str, str]], source_steps: Sequence[Mapping[str, Any]],
) -> tuple[ReportItem, ...]:
    """Each listed (kind, name) with the source steps whose text names it."""

    texts = [(str(step["label"]), str(step.get("whole_source_text") or step.get("source_text") or "")
              .replace("’", "'")) for step in source_steps]
    words_of = [_listed_words(name) for _, name in names]
    other_words = [
        {form for j, words in enumerate(words_of) if j != i for word in words for form in _word_forms(word)}
        for i in range(len(names))
    ]
    found: list[set[str]] = [set() for _ in names]
    patterns: list[re.Pattern[str] | None] = []
    for index, words in enumerate(words_of):
        pattern = None
        core = _core_words(words)
        for candidate, shortened in ((words, False), (core, core != words)):
            if not candidate or (shortened and len(candidate) == 1
                                 and _word_forms(candidate[0]) & other_words[index]):
                continue
            compiled = _name_pattern(candidate)
            if any(compiled.search(text) for _, text in texts):
                pattern = compiled
                break
        patterns.append(pattern)
        if pattern is not None:
            found[index] |= {label for label, text in texts if pattern.search(text)}

    for index, (_, name) in enumerate(names):
        first_line = next((line for line in str(name).splitlines() if line.strip()), "")
        short = {match.group(1) for match in re.finditer(r"\(\s*([^()]*?)\s*\)", first_line)
                 if _SHORT_NAME.fullmatch(match.group(1))}
        if patterns[index] is not None:
            for _, text in texts:
                for match in patterns[index].finditer(text):
                    after = re.match(r"\s*\(\s*([A-Z][A-Z0-9]{1,7})(?![A-Za-z0-9])", text[match.end():])
                    if after:
                        short.add(after.group(1))
        words = words_of[index]
        siblings = [
            (j, other[-1]) for j, other in enumerate(words_of)
            if j != index and other and words and other[0].casefold() == words[0].casefold()
        ]
        for abbreviation in short:
            use = re.compile(rf"(?<![{_ASCII_WORD}]){re.escape(abbreviation)}(?![{_ASCII_WORD}])\s*([^\s,.;:()]*)")
            for label, text in texts:
                for match in use.finditer(text):
                    owner = next((j for j, last in siblings
                                  if match.group(1).casefold() in _word_forms(last)), index)
                    found[owner].add(label)
    order = {label: position for position, (label, _) in enumerate(texts)}
    return tuple(
        ReportItem(kind=kind, name=" ".join(str(name).split()),
                   steps=tuple(sorted(found[index], key=lambda label: order.get(label, 0))))
        for index, (kind, name) in enumerate(names)
    )


_ENDPOINT_ANSWER = re.compile(r"^원문 종점 “(?P<quote>.*)” — 답: (?P<answer>.+)$", re.S)


def _readable_record_text(text: str) -> str:
    """An endpoint answer the rules stored as one line, in plain words."""

    match = _ENDPOINT_ANSWER.match(text.strip())
    if match is None:
        return text.strip()
    quote = " ".join(match.group("quote").split())
    if len(quote) > 90:
        quote = quote[:88].rstrip() + "…"
    answer = match.group("answer").strip()
    verdict = "충족했다고 답했다" if answer in {"네", "응", "예", "yes"} else "아직 충족하지 않았다고 답했다"
    return f"원문의 끝 조건(“{quote}”)을 {verdict}"


def _approval_words(report_data: Mapping[str, Any]) -> str:
    readiness = {
        "guidance_ready": "안내 준비 완료",
        "analysis_required": "분석 검토가 끝나지 않음",
    }.get(str(report_data.get("readiness_status") or ""), "준비 상태 확인 필요")
    if report_data.get("development_only"):
        return f"개발용 시험 프로토콜 — 승인된 개정본이 아님 ({readiness})"
    return f"실행이 허가된 프로토콜 개정본 ({readiness})"


def _pdf_front_matter(fixture: Any) -> tuple[str, str, str, str]:
    """(purpose sentence, keywords, DOI url, author line) from the PDF's first page."""

    try:
        pages = fixture.draft.protocol.metadata.pdf.pages
        first = pages[0].text if pages else ""
    except AttributeError:
        return "", "", "", ""
    text = first.replace("\xa0", " ")
    keywords = ""
    match = re.search(r"Keywords:\s*(.+?)(?:\n[A-Za-z ]+:|\nprotocols\.io|\Z)", text, re.S)
    if match:
        keywords = " ".join(match.group(1).split())
    doi = ""
    match = re.search(r"https?://(?:dx\.)?doi\.org/\S+", text)
    if match:
        doi = match.group(0).rstrip(".,)")
    author = ""
    match = re.search(r"Protocol Citation:\s*(.+?)(?:\n|$)", text)
    if match:
        author = " ".join(match.group(1).split())
    purpose = ""
    match = re.search(r"(?:Abstract|Purpose|Aim|Objective)s?\s*\n(.+?)(?:\n\s*\n|\Z)", text, re.S)
    if match:
        purpose = " ".join(match.group(1).split())[:600]
    return purpose, keywords, doi, author


_SECTION_DURATION = re.compile(r"\s+\d+\s*(?:m|min|h|hr|s)\s*$", re.I)


def build_report_facts(
    report_data: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]] | None = None,
    *,
    fixture: Any = None,
    context: "GroundedReportContext | None" = None,
    zone: ZoneInfo | None = None,
    protocol_problem: str = "",
) -> ReportFacts:
    """The report's facts from the record and the protocol the session ran."""

    zone = zone or report_timezone()
    events = list(report_data.get("events") or ()) if events is None else list(events)
    if fixture is not None:
        source_steps = _source_steps_from_fixture(fixture)
    elif context is not None:
        source_steps = _source_steps_from_context(context)
    else:
        source_steps = []
    by_label = {step["label"]: step for step in source_steps}
    order = {step["label"]: index for index, step in enumerate(source_steps)}

    completed_at: dict[str, datetime | None] = {}
    advanced: list[str] = []
    timer_started: dict[str, tuple[datetime | None, int | None]] = {}
    early: dict[str, tuple[int, int]] = {}
    records: list[ReportRecord] = []
    pauses: list[tuple[str, datetime | None, datetime | None]] = []
    stop_label: str | None = None
    workflow_completed = False
    blocked: list[str] = []
    experimenter = ""
    gates_skipped = False

    def add_record(label: str, kind: str, text: str, at: datetime | None) -> None:
        records.append(ReportRecord(
            number=len(records) + 1, step_label=label, kind=kind,
            text=_readable_record_text(text), at=at.strftime("%H:%M") if at else "",
        ))

    for event in events:
        kind = str(event.get("event_type") or "")
        label = str(event.get("step_label") or "")
        at = _local(event.get("created_at"), zone)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        wording = str(event.get("user_wording") or "").strip()
        legacy = [str(item).strip() for item in payload.get("observations") or () if str(item).strip()]
        if kind == "step_completed" and label:
            completed_at.setdefault(label, at)
            if wording:
                add_record(label, "관찰", wording, at)
            timer = payload.get("timer") if isinstance(payload.get("timer"), dict) else {}
            if timer.get("step_exited_before_timer_elapsed") or (
                timer.get("completion_state") == "step_exited_before_timer_elapsed"
            ):
                try:
                    early[label] = (
                        int(timer.get("elapsed_seconds") or 0),
                        int(timer.get("source_duration_seconds") or timer.get("duration_seconds") or 0),
                    )
                except (TypeError, ValueError):
                    pass
        elif kind == "step_advanced" and label:
            advanced.append(label)
        elif kind == "observation" and label:
            add_record(label, "관찰", wording or str(payload.get("text") or "관찰 기록"), at)
        elif kind == "anomaly" and label:
            add_record(label, "이상", wording or str(payload.get("text") or "이상 보고"), at)
        elif kind == "photo_attached" and label:
            add_record(label, "사진", f"사진 첨부 — {wording}" if wording else "사진 첨부", at)
        elif kind == "timer_started" and label:
            timer = payload.get("timer") if isinstance(payload.get("timer"), dict) else {}
            seconds = timer.get("duration_seconds") or timer.get("source_duration_seconds")
            try:
                seconds = int(seconds) if seconds else None
            except (TypeError, ValueError):
                seconds = None
            if seconds:
                timer_started[label] = (at, seconds)
        elif kind == "workflow_paused":
            pauses.append((label, at, None))
        elif kind == "workflow_resumed" and pauses and pauses[-1][2] is None:
            pauses[-1] = (pauses[-1][0], pauses[-1][1], at)
        elif kind == "session_stopped":
            stop_label = label or None
        elif kind == "workflow_completed":
            workflow_completed = True
        elif kind == "blocked" and label:
            blocked.append(label)
        elif kind == "experimenter_recorded" and not experimenter:
            # The person who started the experiment, as the server knew them
            # (decision 5): a development account's name today, the signed-in
            # person's once sign-in is attached.
            experimenter = " ".join(str(payload.get("display_name") or "").split())[:120]
        elif kind == "test_mode_readiness_gates_skipped":
            gates_skipped = True
        for text in legacy:
            add_record(label or "—", "관찰", text, at)

    started = _local(report_data.get("started_at"), zone)
    ended = _local(report_data.get("ended_at"), zone)
    status = str(report_data.get("status") or "in_progress")
    outcome = "completed" if workflow_completed or status == "completed" else (
        "stopped" if status in {"stopped", "blocked", "incomplete"} else "in_progress")
    completed_labels = sorted(completed_at, key=lambda item: order.get(item, 10_000 + len(item)))
    total = len(source_steps) or len(completed_labels)

    steps: list[ReportStepFacts] = []
    shown = list(completed_labels)
    for record in records:
        if record.step_label not in shown and record.step_label != "—":
            shown.append(record.step_label)
    shown.sort(key=lambda item: order.get(item, 10_000))
    for label in shown:
        source = by_label.get(label, {})
        note = ""
        defined = source.get("timer")
        if label in early:
            elapsed, duration = early[label]
            note = f"원문 {_duration_words(duration or defined)} / {_duration_words(elapsed)}에 끝냄"
        elif label in timer_started:
            start, seconds = timer_started[label]
            end = completed_at.get(label)
            actual = _duration_words((end - start).total_seconds()) if start and end else ""
            note = f"원문 {_duration_words(seconds)} / 실제 {actual}" if actual else f"원문 {_duration_words(seconds)}"
        elif defined:
            note = f"원문 {_duration_words(defined)} / 타이머 기록 없음"
        done = completed_at.get(label)
        steps.append(ReportStepFacts(
            label=label, step_id=str(source.get("step_id") or ""),
            section=_SECTION_DURATION.sub("", str(source.get("section") or "")).strip(),
            text=str(source.get("text") or ""), source_text=str(source.get("source_text") or ""),
            translated=bool(source.get("translated")), expected=tuple(source.get("expected") or ()),
            source_timer_seconds=defined, completed=label in completed_at,
            completed_at=done.strftime("%H:%M") if done else "",
            timer_note=note, records=tuple(r for r in records if r.step_label == label),
        ))

    # What was done differently from the source, from the record only.
    deviations: list[str] = []
    if source_steps and completed_labels:
        first = min(order.get(label, 0) for label in completed_labels)
        before = [step["label"] for step in source_steps[:first]]
        if before:
            deviations.append(f"{_ranges(before)}는 이 기록에 없다({completed_labels[0]}단계부터 기록됨).")
    for label in completed_labels:
        if label in early:
            elapsed, duration = early[label]
            deviations.append(
                f"{label}단계: 원문 시간 {_duration_words(duration)} 중 {_duration_words(elapsed)}에 단계를 끝냈다(타이머를 일찍 끝냄)."
            )
    for record in records:
        if record.kind == "관찰" and "반복" in record.text:
            deviations.append(f"{record.step_label}단계: 연구자 기록 — “{record.text}”.")
    for label, paused_at, resumed_at in pauses:
        if paused_at and resumed_at:
            deviations.append(
                f"{label}단계에서 {_duration_words((resumed_at - paused_at).total_seconds())} 동안 멈췄다가 다시 진행했다"
                f"({paused_at:%H:%M}–{resumed_at:%H:%M})."
            )
        elif paused_at:
            deviations.append(f"{label}단계에서 {paused_at:%H:%M}에 멈춘 뒤 다시 시작한 기록이 없다.")
    for label in advanced:
        deviations.append(f"{label}단계: 완료를 말하지 않고 다음 단계로 넘어갔다.")
    if outcome == "stopped" and source_steps:
        after = [step["label"] for step in source_steps
                 if step["label"] not in completed_at
                 and order.get(step["label"], 0) > max((order.get(l, -1) for l in completed_labels), default=-1)]
        if after:
            where = f"{stop_label}단계에서 " if stop_label else ""
            deviations.append(f"{where}실험을 끝내 {_ranges(after)}는 수행하지 않았다.")

    observations = [r for r in records if r.kind == "관찰"]
    anomalies = [r for r in records if r.kind == "이상"]
    photos = [r for r in records if r.kind == "사진"]
    confirmed: list[str] = []
    if completed_labels:
        confirmed.append(f"완료로 기록된 단계는 {_ranges(completed_labels)}이다.")
    for record in observations:
        if record.text.startswith("원문의 끝 조건"):
            confirmed.append(f"{record.step_label}단계: 연구자가 {record.text}.")
        else:
            confirmed.append(f"{record.step_label}단계에서 연구자가 “{record.text}”라고 기록했다.")
    waited = [label for label in completed_labels if label in timer_started and label not in early]
    if waited:
        confirmed.append(f"타이머를 켠 {_ranges(waited)}는 타이머를 켠 뒤 완료했다(실제 걸린 시간은 3-2의 표).")
    to_check: list[str] = []
    if fixture is None:
        to_check.append(f"{protocol_problem or '프로토콜 원문을 불러오지 못했다'} — 단계 원문과 원문 조건을 이 보고서에 싣지 못했다.")
    for step in steps:
        if step.completed and step.expected and not any(r.kind == "관찰" for r in step.records):
            quote = step.expected[0] if len(step.expected[0]) <= 90 else step.expected[0][:88].rstrip() + "…"
            to_check.append(f"{step.label}단계: 원문은 끝 조건(“{quote}”)을 적고 있으나 관찰이 기록되지 않았다.")
        if step.completed and step.source_timer_seconds and step.label not in timer_started:
            to_check.append(f"{step.label}단계: 원문 시간({_duration_words(step.source_timer_seconds)})이 있으나 타이머 기록이 없다.")
        if step.label in early:
            to_check.append(f"{step.label}단계: 타이머를 일찍 끝낸 것이 결과에 영향을 주었는지 확인한다.")
    for record in anomalies:
        to_check.append(f"{record.step_label}단계 이상(“{record.text}”) 뒤에 어떻게 처리했는지 기록되지 않았다.")
    for record in photos:
        to_check.append(f"{record.step_label}단계 사진은 시스템이 해석하지 않았다 — 사진에서 본 것을 연구자가 적는다.")
    for label in blocked:
        to_check.append(f"{label}단계에서 진행이 막힌 기록이 있다.")

    if started and ended and ended.date() != started.date():
        date_words = (f"{started.year}년 {started.month}월 {started.day}일 ({_WEEKDAYS[started.weekday()]}) – "
                      f"{ended.month}월 {ended.day}일 ({_WEEKDAYS[ended.weekday()]})")
        end_words = f"{ended.month}월 {ended.day}일 {ended:%H:%M}"
    else:
        date_words = (f"{started.year}년 {started.month}월 {started.day}일 ({_WEEKDAYS[started.weekday()]})"
                      if started else "기록 없음")
        end_words = f"{ended:%H:%M}" if ended else "기록 없음 (진행 중)"
    if outcome == "completed":
        outcome_words = "끝까지 완료"
    elif outcome == "stopped":
        outcome_words = f"중단 — {stop_label}단계에서 종료" if stop_label else "중단"
    else:
        outcome_words = "진행 중"
    zone_name = "한국 시간 (Asia/Seoul)" if zone.key == "Asia/Seoul" else zone.key
    run_rows = (
        ("프로토콜", str(report_data.get("protocol_title") or "")),
        ("날짜", date_words),
        ("시작", f"{started:%H:%M}" if started else "기록 없음"),
        ("끝", end_words),
        ("시간 기준", zone_name),
        ("실험자", experimenter),
        ("걸린 시간", _duration_words((ended - started).total_seconds()) if started and ended else "진행 중"),
        ("완료 단계", f"{len(completed_labels)} / {total}" if total else str(len(completed_labels))),
        ("결과", outcome_words),
        ("기록", f"관찰 {len(observations)}건 · 이상 {len(anomalies)}건 · 사진 {len(photos)}건"),
        ("프로토콜 승인 상태", _approval_words(report_data)),
    ) + ((("준비 검사", "시험 모드로 실행 — 프로토콜 준비 검사를 건너뜀"),) if gates_skipped else ())

    purpose, keywords, doi, author = _pdf_front_matter(fixture) if fixture is not None else ("", "", "", "")
    protocol = getattr(getattr(fixture, "draft", None), "protocol", None)
    materials = tuple(" ".join(m.name_source_text.split()) for m in getattr(protocol, "materials", ()) or ())
    equipment = tuple(" ".join(e.name_source_text.split()) for e in getattr(protocol, "equipment", ()) or ())
    items = item_step_labels(
        [("재료", str(m.name_source_text)) for m in getattr(protocol, "materials", ()) or ()]
        + [("장비", str(e.name_source_text)) for e in getattr(protocol, "equipment", ()) or ()],
        source_steps,
    )
    sections: list[str] = []
    for step in source_steps:
        title = _SECTION_DURATION.sub("", step["section"]).strip()
        if title and title not in sections:
            sections.append(title)
    title = str(report_data.get("protocol_title") or "")
    reference = ReportSource(
        number=1, kind="protocol",
        title=f"{author}" if author else f"{title} (실험 PDF)",
        url=doi, note="실험에 쓴 프로토콜 원문 PDF",
    )
    return ReportFacts(
        protocol_title=title, protocol_loaded=bool(source_steps), total_steps=total,
        run_rows=run_rows, completed_labels=tuple(completed_labels), outcome=outcome,
        stop_label=stop_label, steps=tuple(steps), records=tuple(records),
        purpose_from_pdf=purpose, keywords=keywords, sections=tuple(sections),
        materials=materials, equipment=equipment, deviations=tuple(deviations),
        confirmed=tuple(confirmed), to_check=tuple(to_check), protocol_reference=reference,
        zone=zone, items=items,
    )


# --- The server's checks on model sections (decision 5) ------------------------

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_CONDITION_NUMBER = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:°\s*C|℃|도\b|분|min\b|시간|hours?\b|h\b|초|sec\b|s\b|mM\b|µM|μM|nM|M\b|"
    r"µL|μL|uL|ul\b|mL|ml\b|L\b|rpm|x\s*g\b|×\s*g\b|g\b|mg|µg|ug|ng|%|v/v|w/v|mm\b|cm\b|일\b)",
    re.I,
)
_IDENTIFIER_SHAPES = (
    ("16자 이상 16진수", re.compile(r"\b[0-9a-fA-F]{16,}\b")),
    ("ER-/fixture-/candidate- 식별자", re.compile(r"\b(?:ER|fixture|candidate)-[A-Za-z0-9-]+")),
    ("밀리초 시각", re.compile(r"\d{1,2}:\d{2}:\d{2}\.\d+")),
    ("영어 상태값·명령 이름", re.compile(
        r"\b(?:[a-z]+(?:_[a-z]+)+|stopped|completed|blocked|incomplete|in progress)\b")),
)
#: A quotation is closed by its own closing mark: a straight quote by a
#: straight quote, “ by ”, and so on. A record that itself holds curly quotes
#: (an endpoint answer quotes the source) can then sit inside straight ones.
_QUOTE_MARKS = (('"', '"'), ("“", "”"), ("‘", "’"), ("'", "'"), ("「", "」"), ("『", "』"))
_QUOTED = re.compile("|".join(
    f"{re.escape(opening)}([^{re.escape(closing)}]{{2,}}){re.escape(closing)}"
    for opening, closing in _QUOTE_MARKS
))
_SPECULATION = ("추정", "것으로 보인", "보인다", "아마", "가능성", "것 같", "듯하", "듯이", "인 듯", "한 듯")
_SENTENCE = re.compile(r"(?<=[.!?。])\s+")


#: Units a number may carry, each written the ways a protocol or a person
#: writes it, and the one form they are compared in (decision 3: "15분" is
#: "15 min", "µL" is "uL"). A number with a unit must appear with the same
#: unit in the record or the source; a bare number is checked as before.
_UNIT_FORMS = (
    (r"°\s*C|℃|도", "°C"),
    (r"시간|hours?|hrs?|h", "h"),
    (r"분|minutes?|mins?|min|m", "min"),
    (r"초|seconds?|secs?|sec|s", "s"),
    (r"[µμu][Ll]", "µL"),
    (r"m[Ll]", "mL"),
    (r"L", "L"),
    (r"mM", "mM"),
    (r"[µμu]M", "µM"),
    (r"nM", "nM"),
    (r"M", "M"),
    (r"rpm|RPM", "rpm"),
    (r"[x×]\s*g", "×g"),
    (r"mg", "mg"),
    (r"[µμu]g", "µg"),
    (r"ng", "ng"),
    (r"g", "g"),
    (r"mm", "mm"),
    (r"cm", "cm"),
    (r"[µμ]m", "µm"),
    (r"kDa", "kDa"),
    (r"%", "%"),
)
_UNIT_PATTERNS = tuple((re.compile(pattern), unit) for pattern, unit in _UNIT_FORMS)
_MEASURE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(" + "|".join(
        # Longer spellings first so "min" is not read as "m" and "mL" not as "m".
        sorted((p for pattern, _ in _UNIT_FORMS for p in pattern.split("|")), key=len, reverse=True)
    ) + r")(?![A-Za-zµμ])"
)


def _plain(text: str) -> str:
    """Text without citation marks or thousands separators."""

    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", _CITATION.sub(" ", text))


def _number_form(item: str) -> str:
    return item if "." in item else (item.lstrip("0") or "0")


def _numbers(text: str) -> set[str]:
    return {_number_form(item) for item in _NUMBER.findall(_plain(text))}


def _measures(text: str) -> list[tuple[str, str, str]]:
    """(number, unit, as written) for every number that carries a unit."""

    found = []
    for match in _MEASURE.finditer(_plain(text)):
        written = match.group(2)
        unit = next(unit for pattern, unit in _UNIT_PATTERNS if pattern.fullmatch(written))
        found.append((_number_form(match.group(1)), unit, match.group(0)))
    return found


def _record_texts(facts: ReportFacts) -> list[str]:
    """Every text a number in a model section may come from."""

    texts: list[str] = [facts.protocol_title, facts.keywords, facts.purpose_from_pdf]
    texts += [value for _, value in facts.run_rows]
    texts += list(facts.deviations) + list(facts.confirmed) + list(facts.to_check)
    texts += list(facts.materials) + list(facts.equipment) + list(facts.sections)
    for step in facts.steps:
        texts += [step.label, step.text, step.source_text, step.completed_at, step.timer_note, *step.expected]
        if step.source_timer_seconds:
            texts.append(_duration_words(step.source_timer_seconds))
    texts += [f"{record.number} {record.text} {record.at}" for record in facts.records]
    texts.append(str(facts.total_steps))
    texts.append(" ".join(str(n) for n in range(1, facts.total_steps + 1)))
    texts.append(" ".join(str(n) for n in range(0, len(facts.records) + 1)))
    texts.append(facts.protocol_reference.title)
    return [str(text) for text in texts]


def _allowed_numbers(facts: ReportFacts) -> set[str]:
    allowed: set[str] = set()
    for text in _record_texts(facts):
        allowed |= _numbers(text)
    return allowed


def _allowed_measures(facts: ReportFacts) -> set[tuple[str, str]]:
    return {(number, unit) for text in _record_texts(facts) for number, unit, _ in _measures(text)}


def _as_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, Mapping):
                parts.append(" ".join(str(v) for v in item.values()))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return "" if value is None else str(value)


def check_report_sections(
    sections: Mapping[str, Any],
    facts: ReportFacts,
) -> dict[str, list[str]]:
    """Why each model section may not be used ({} for a section that passes)."""

    allowed = _allowed_numbers(facts)
    allowed_measures = _allowed_measures(facts)
    record_texts = [" ".join(text.split()) for text in facts.record_texts]
    reviewable = set(range(1, len(facts.records) + len(facts.deviations) + 1))
    reasons: dict[str, list[str]] = {}
    for key in MODEL_SECTIONS:
        value = sections.get(key)
        text = _as_text(value)
        problems: list[str] = []
        for name, shape in _IDENTIFIER_SHAPES:
            if shape.search(_CITATION.sub(" ", text)):
                problems.append(f"본문에 {name}")
        cited = {int(n) for group in _CITATION.findall(text) for n in group.split(",")}
        if key in {"purpose", "background"}:
            if _CONDITION_NUMBER.search(_CITATION.sub(" ", text)):
                problems.append("실험 조건 숫자")
            if cited - {1}:
                problems.append("없는 출처 번호")
            if key == "purpose":
                for sentence in filter(None, (s.strip() for s in _SENTENCE.split(text))):
                    if not _CITATION.search(sentence):
                        problems.append("출처 번호가 없는 문장")
                        break
        else:
            unknown = [written for number, unit, written in _measures(text)
                       if (number, unit) not in allowed_measures]
            if unknown:
                problems.append("기록·원문에 없는 값 " + ", ".join(dict.fromkeys(unknown)))
            extra = _numbers(_MEASURE.sub(" ", _plain(text))) - allowed
            if extra:
                problems.append("기록·원문에 없는 숫자 " + ", ".join(sorted(extra, key=lambda x: (len(x), x))[:6]))
            if cited:
                problems.append("방법·결과·고찰 칸의 출처 번호")
        if key in {"results_summary", "methods_summary"}:
            for word in _SPECULATION:
                if word in text:
                    problems.append(f"추측 표현 “{word}”")
                    break
        if key == "results_summary":
            for match in _QUOTED.finditer(text):
                quoted = " ".join(next(group for group in match.groups() if group is not None).split())
                if not any(quoted in record or record in quoted for record in record_texts):
                    problems.append(f"기록에 없는 관찰 “{quoted[:30]}”")
                    break
        if key == "discussion_review" and value:
            for item in value if isinstance(value, (list, tuple)) else [value]:
                number = item.get("항목 번호") if isinstance(item, Mapping) else None
                try:
                    number = int(number)
                except (TypeError, ValueError):
                    number = None
                if number not in reviewable:
                    problems.append("기록된 이상·편차에 붙지 않은 원인 추정")
                    break
        if problems:
            reasons[key] = problems
    return reasons


# --- The narrative and the server's own sentences -------------------------------


@dataclass(frozen=True)
class ReportNarrative:
    """The report's prose: model sections that passed, server sentences for the rest."""

    title: str
    purpose: str
    background: str
    methods_summary: str
    results_summary: str
    discussion_confirmed: tuple[str, ...]
    discussion_to_check: tuple[str, ...]
    discussion_review: tuple[str, ...]
    conclusion: str
    next_steps: tuple[str, ...]
    facts: ReportFacts | None = None
    sources: tuple[ReportSource, ...] = ()
    section_origin: Mapping[str, str] = field(default_factory=dict)
    rejected: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    writer: str = "서버 대체 문장"
    written_at: str = ""

    # Earlier names, kept for callers written before lane RP.
    @property
    def objective(self) -> str:
        return self.purpose

    @property
    def materials_and_methods(self) -> str:
        return self.methods_summary

    @property
    def results_and_observations(self) -> str:
        lines = []
        if self.facts is not None:
            lines = [f"{r.step_label}단계 {r.kind}: {r.text}" for r in self.facts.records]
            for step in self.facts.steps:
                if not step.records:
                    lines.append(f"{step.label}단계: 기록된 관찰이 없다.")
        return "\n".join(lines + [self.results_summary])

    @property
    def discussion(self) -> str:
        return "\n".join(self.discussion_confirmed + self.discussion_to_check + self.discussion_review)


def deterministic_sections(facts: ReportFacts) -> dict[str, Any]:
    """The server's own sentence for every model section."""

    title = facts.protocol_title or "이 프로토콜"
    if facts.purpose_from_pdf:
        purpose = f"원문은 이 실험의 목적을 다음과 같이 적고 있다: “{facts.purpose_from_pdf}”[1]."
    elif facts.keywords:
        purpose = (f"이 실험은 '{title}' 프로토콜에 따라 수행했다[1]. "
                   f"원문에는 목적을 따로 밝힌 문장이 없으며, 원문 키워드는 '{facts.keywords}'이다[1].")
    else:
        purpose = f"이 실험은 '{title}' 프로토콜에 따라 수행했다[1]. 원문에는 목적을 따로 밝힌 문장이 없다[1]."
    background = (
        "원문은 다음 순서의 단계 묶음으로 되어 있다: " + " → ".join(facts.sections) + "[1]."
        if facts.sections else "원문에서 원리를 설명하는 부분을 찾지 못했다[1]."
    )
    groups: list[tuple[str, list[str]]] = []
    for step in facts.steps:
        if not step.completed:
            continue
        if groups and groups[-1][0] == step.section:
            groups[-1][1].append(step.label)
        else:
            groups.append((step.section, [step.label]))
    if groups:
        methods = " ".join(
            (f"‘{section}’ 묶음의 {_ranges(labels)}를 수행했다." if section else f"{_ranges(labels)}를 수행했다.")
            for section, labels in groups
        ) + " 단계별 절차와 원문 조건은 3-2의 표에 원문 그대로 적었다."
    else:
        methods = "완료로 기록된 단계가 없다."
    counts = {kind: sum(1 for r in facts.records if r.kind == kind) for kind in ("관찰", "이상", "사진")}
    if facts.records:
        results = (f"관찰 {counts['관찰']}건, 이상 {counts['이상']}건, 사진 {counts['사진']}건이 기록되었다. "
                   "내용은 위 표에 연구자가 말한 그대로 적었다.")
    else:
        results = "기록된 관찰이 없습니다."
    done = len(facts.completed_labels)
    if facts.outcome == "completed":
        conclusion = f"{facts.total_steps}단계 중 {done}단계를 완료로 기록하고 실험을 끝까지 마쳤다."
    elif facts.outcome == "stopped":
        where = f" {facts.stop_label}단계에서" if facts.stop_label else ""
        conclusion = f"{facts.total_steps}단계 중 {done}단계를 완료로 기록하고{where} 실험을 중단했다."
    else:
        conclusion = f"{facts.total_steps}단계 중 {done}단계가 완료로 기록되었고 실험이 진행 중이다."
    conclusion += f" 관찰 {counts['관찰']}건, 이상 {counts['이상']}건, 사진 {counts['사진']}건이 기록되었다."
    next_steps: list[str] = []
    if facts.outcome == "stopped" and facts.stop_label:
        next_steps.append(f"{facts.stop_label}단계부터 이어서 진행한다.")
    if counts["이상"]:
        next_steps.append("이상 기록의 처리 결과를 적는다.")
    if counts["사진"]:
        next_steps.append("사진에서 본 것을 결과에 적는다.")
    next_steps.append("‘연구자 해석’ 칸을 채운다.")
    return {
        "purpose": purpose,
        "background": background,
        "methods_summary": methods,
        "results_summary": results,
        "discussion_confirmed": tuple(facts.confirmed) or ("기록에서 확인되는 완료 단계가 없다.",),
        "discussion_to_check": tuple(facts.to_check) or ("기록에서 따로 확인할 점을 찾지 못했다.",),
        "discussion_review": (),
        "conclusion": conclusion,
        "next_steps": tuple(next_steps),
    }


def _review_items(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, (list, tuple)) else []
    texts = []
    for item in items:
        if isinstance(item, Mapping):
            text = " ".join(str(item.get("제안") or "").split())
            number = item.get("항목 번호")
            if text:
                texts.append(f"검토 제안 (항목 {number}): {text}")
    return tuple(texts)


def _text_items(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(" ".join(str(item).split()) for item in value if str(item).strip())
    text = " ".join(str(value or "").split())
    return (text,) if text else ()


def narrative_from_sections(
    facts: ReportFacts,
    model: Mapping[str, Any] | None = None,
    *,
    rejected: Mapping[str, Sequence[str]] | None = None,
    writer: str = "서버 대체 문장",
    written_at: str | None = None,
) -> ReportNarrative:
    fallback = deterministic_sections(facts)
    rejected = {key: tuple(value) for key, value in (rejected or {}).items()}
    chosen: dict[str, Any] = {}
    origin: dict[str, str] = {}
    for key in MODEL_SECTIONS:
        value = None if model is None or key in rejected else model.get(key)
        if key == "next_steps":
            value = _text_items(value) if value else None
        elif key == "discussion_review":
            value = _review_items(value) if value else None
        elif value is not None:
            value = " ".join(str(value).split()) or None
        if value:
            chosen[key], origin[key] = value, "모델"
        else:
            chosen[key] = fallback[key]
            if model is None:
                origin[key] = "서버"
            elif key in rejected or not model:
                origin[key] = "대체"
            else:
                # The model left it empty: not a refusal, but not its words.
                origin[key] = "모델이 비움 — 서버 문장"
    # Discussion (가) and (나) are lists the server builds from the record
    # (decision 2); the model does not write them.
    for key in ("discussion_confirmed", "discussion_to_check"):
        chosen[key], origin[key] = fallback[key], "서버"
    return ReportNarrative(
        title=f"{facts.protocol_title} 실험 보고서" if facts.protocol_title else "실험 보고서",
        purpose=chosen["purpose"], background=chosen["background"],
        methods_summary=chosen["methods_summary"], results_summary=chosen["results_summary"],
        discussion_confirmed=tuple(chosen["discussion_confirmed"]),
        discussion_to_check=tuple(chosen["discussion_to_check"]),
        discussion_review=tuple(chosen["discussion_review"]),
        conclusion=chosen["conclusion"], next_steps=tuple(chosen["next_steps"]),
        facts=facts, sources=(facts.protocol_reference,), section_origin=origin,
        rejected=rejected, writer=writer, written_at=written_at or _now(),
    )


@dataclass(frozen=True)
class ReportDraftState:
    """Server-owned structured draft state for specialized report writing."""

    report_id: str
    session_id: str
    protocol_id: str
    status: str
    experiment_summary: str = ""
    materials_summary: str = ""
    equipment_summary: str = ""
    observations_narrative: str = ""
    anomalies_narrative: str = ""
    timeline_narrative: str = ""
    conclusion_narrative: str = ""
    committed_event_ids: tuple[str, ...] = ()
    last_updated_at: str = ""


@dataclass(frozen=True)
class ReportWriterSettings:
    enabled: bool = True
    model: str = "grok-4.6"
    timeout_seconds: float = 25.0

    @classmethod
    def from_environment(cls) -> "ReportWriterSettings":
        enabled_val = os.environ.get(
            "VOINEY_LAB_REPORT_WRITER_ENABLED", "true"
        ).strip().casefold()
        enabled = enabled_val in _TRUE
        model = os.environ.get(
            "VOINEY_LAB_REPORT_MODEL",
            os.environ.get("VOINEY_LAB_SUPPLEMENTAL_MODEL", "grok-4.6"),
        ).strip() or "grok-4.6"
        try:
            timeout = float(os.environ.get(
                "VOINEY_LAB_REPORT_WRITER_TIMEOUT_SECONDS", "25"
            ).strip())
        except ValueError:
            timeout = 25.0
        return cls(enabled=enabled, model=model, timeout_seconds=timeout)


_NARRATIVE_CACHE: dict[tuple[Any, ...], ReportNarrative] = {}
_AUTO = object()

_WRITER_INSTRUCTIONS = """너는 실험 보고서를 쓰는 연구자를 돕는다. 아래 JSON 은 서버가 실험 기록과 프로토콜 원문에서 뽑은 사실이다. 이 사실로 한국어 실험 보고서의 서술 칸만 쓴다. 표(수행 정보, 수행한 단계, 결과 표, 원문과 다르게 한 점)는 서버가 따로 넣으므로 다시 쓰지 않는다.

문체: 간결하고 명확한 한국어 학술 문체(~했다/~이다). 핵심만 쓴다.

지킬 것:
- 기록에 없는 결과·관찰·측정·수치·오차 원인을 지어내지 않는다. 결과 칸은 기록된 사실만 쓰고 추측하지 않는다.
- 숫자와 단위는 사실 JSON 에 있는 그대로 쓴다. 바꾸거나 계산해서 새 숫자를 만들지 않는다.
- 목적·배경 칸에는 온도·시간·농도·부피·회전수 같은 실험 조건 숫자를 쓰지 않는다.
- 목적 칸의 모든 문장 끝에 출처 번호 [1](프로토콜 원문)을 단다.
- 'background' 는 프로토콜 원문에 있는 내용만 쓴다. 원문 밖의 지식(교과서·웹 지식)은 쓰지 않는다. 원문에 원리 설명이 없으면 없다고 쓴다.
- 기록 ID, 버전, 해시, 영어 상태값, 명령 이름, 밀리초 시각은 쓰지 않는다.
- 원인 추정은 'discussion_review' 에만, '검토할 수 있는 항목' 의 번호에 붙여서 쓴다. 그런 항목이 없으면 빈 목록이다.
- 고찰의 '기록에서 확인되는 점'과 '확인이 필요한 점'은 서버가 기록에서 목록으로 만든다. 다시 쓰지 않는다.

JSON 객체 하나만 돌려준다. 키:
purpose (1–3문장), background, methods_summary (수행한 단계를 묶어 요약, 주요 조건은 원문 값 그대로),
results_summary (기록된 관찰·이상·사진을 1–3문장으로, 관찰은 기록 문구를 따옴표로 그대로),
discussion_review (목록, 각 항목 {"항목 번호": 숫자, "제안": 문장}), conclusion (어디까지 했고 무엇이 기록됐는지), next_steps (문장 목록)."""


def _writer_facts(facts: ReportFacts) -> dict[str, Any]:
    """What the model reads: experiment content only, no identifiers (decision 3)."""

    reviewable = [
        {"항목 번호": record.number, "단계": record.step_label, "내용": f"이상: {record.text}"}
        for record in facts.records if record.kind == "이상"
    ] + [
        {"항목 번호": len(facts.records) + index, "내용": f"원문과 다르게 한 점: {text}"}
        for index, text in enumerate(facts.deviations, 1)
    ]
    return {
        "프로토콜": facts.protocol_title,
        "원문 키워드": facts.keywords,
        "원문에 적힌 목적": facts.purpose_from_pdf,
        "원문 단계 묶음": list(facts.sections),
        "재료": list(facts.materials),
        "장비": list(facts.equipment),
        "수행 정보": {name: value for name, value in facts.run_rows if value and name != "실험자"},
        "수행한 단계": [
            {
                "단계": step.label, "묶음": step.section,
                "원문": step.text, "완료": step.completed,
                **({"완료 시각": step.completed_at} if step.completed_at else {}),
                **({"타이머": step.timer_note} if step.timer_note else {}),
                **({"원문 기대 결과": list(step.expected)} if step.expected else {}),
            }
            for step in facts.steps
        ],
        "기록": [
            {"번호": r.number, "단계": r.step_label, "종류": r.kind, "내용": r.text, "시각": r.at}
            for r in facts.records
        ],
        "원문과 다르게 한 점": list(facts.deviations),
        "서버가 찾은 확인이 필요한 점": list(facts.to_check),
        "검토할 수 있는 항목": reviewable,
    }


def _parse_reply(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("report reply is not an object")
    return data


class ReportWriterBrain:
    """Writes the report's prose sections; the server checks each before using it."""

    def __init__(
        self,
        client: Any = None,
        model: str = "grok-4.6",
        timeout_seconds: float = 25.0,
    ) -> None:
        self.client = client
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.last_usage: dict[str, Any] = {}

    def build_deterministic_draft(
        self,
        report_data: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> ReportDraftState:
        """Create a faithful, unhallucinated report draft directly from SQLite events."""
        event_ids = tuple(
            str(e.get("event_key") or e.get("event_id"))
            for e in events
            if (e.get("event_key") or e.get("event_id"))
        )
        obs_count = sum(1 for e in events if e.get("event_type") == "observation")
        anomaly_count = sum(1 for e in events if e.get("event_type") in ("anomaly", "system_anomaly"))
        completed_steps = [
            e.get("step_label") for e in events if e.get("event_type") == "step_completed"
        ]

        summary = (
            f"Protocol {report_data.get('protocol_title', 'Protocol')} "
            f"execution recorded {len(events)} total ledger events across "
            f"{len(completed_steps)} completed steps."
        )
        obs_narrative = (
            f"Operator recorded {obs_count} direct qualitative observations during the session."
            if obs_count > 0 else "해당 단계에 대해 별도의 관찰 결과가 기록되지 않았다."
        )
        anomaly_narrative = (
            f"A total of {anomaly_count} unexpected condition(s) were triaged and logged."
            if anomaly_count > 0 else "Execution proceeded without unresolved abnormal incidents."
        )
        conclusion = (
            f"Experiment concluded with status '{report_data.get('status', 'in_progress')}'. "
            f"All {len(event_ids)} event records are verified in the server SQLite ledger."
        )

        return ReportDraftState(
            report_id=str(report_data.get("report_id", "")),
            session_id=str(report_data.get("session_id", "")),
            protocol_id=str(report_data.get("protocol_id", "")),
            status=str(report_data.get("status", "in_progress")),
            experiment_summary=summary,
            observations_narrative=obs_narrative,
            anomalies_narrative=anomaly_narrative,
            conclusion_narrative=conclusion,
            committed_event_ids=event_ids,
            last_updated_at=_now(),
        )

    @staticmethod
    def facts_for(
        report_data_or_context: Mapping[str, Any] | GroundedReportContext,
        events: Sequence[Mapping[str, Any]] | None = None,
        *,
        fixture: Any = _AUTO,
    ) -> ReportFacts:
        if isinstance(report_data_or_context, GroundedReportContext):
            context = report_data_or_context
            report_data = {**context.report_metadata, **context.protocol_metadata,
                           **context.session_timing}
            return build_report_facts(report_data, list(events or context.event_ledger), context=context)
        report_data = report_data_or_context
        problem = ""
        if fixture is _AUTO:
            fixture, problem = report_protocol_fixture(report_data)
        context = None
        if fixture is None:
            context = build_grounded_report_context(
                dict(report_data), list(events or report_data.get("events") or ()), lookup=False)
        return build_report_facts(report_data, events, fixture=fixture, context=context,
                                  protocol_problem=problem)

    def build_deterministic_narrative(
        self,
        report_data_or_context: dict[str, Any] | GroundedReportContext,
        events: list[dict[str, Any]] | None = None,
        context: GroundedReportContext | None = None,
        *,
        fixture: Any = _AUTO,
    ) -> ReportNarrative:
        """The report with the server's own sentence in every section."""

        facts = self.facts_for(context or report_data_or_context, events, fixture=fixture)
        return narrative_from_sections(facts)

    async def write_reply(self, facts: ReportFacts) -> tuple[dict[str, Any] | None, str]:
        """One model call for the report's prose: (reply, "") or (None, why it failed)."""

        if not self.client:
            return None, ""
        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": _WRITER_INSTRUCTIONS},
                        {"role": "user", "content": json.dumps(
                            _writer_facts(facts), ensure_ascii=False, indent=1)},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=4000,
                ),
                timeout=self.timeout_seconds,
            )
            self.last_usage["writer"] = getattr(response, "usage", None)
            return _parse_reply(response.choices[0].message.content or ""), ""
        except Exception as exc:  # noqa: BLE001 -- the server's sentences stand in
            log.warning("report writer failed error=%s; using the server's sentences", type(exc).__name__)
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                return None, "모델 시간 초과"
            return None, f"모델 답 실패({type(exc).__name__})"

    async def generate_narrative(
        self,
        report_data: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        fixture: Any = _AUTO,
    ) -> ReportNarrative:
        """The report's prose: one model call, each section checked before use."""

        facts = self.facts_for(report_data, events, fixture=fixture)
        latest_key = str(events[-1].get("event_key") if events else "")
        cache_key = (
            str(report_data.get("report_id") or ""), int(report_data.get("finalization_version") or 0),
            latest_key, str(report_data.get("protocol_revision") or ""), self.model,
        )
        cached = _NARRATIVE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        if not self.client:
            narrative = narrative_from_sections(facts)
        else:
            reply, failure = await self.write_reply(facts)
            narrative = narrative_from_reply(facts, reply, failure=failure, writer=self.model)
        _NARRATIVE_CACHE[cache_key] = narrative
        return narrative


def narrative_from_reply(
    facts: ReportFacts, reply: Mapping[str, Any] | None, *, failure: str = "", writer: str,
    written_at: str | None = None,
) -> ReportNarrative:
    """The report from one model reply, checked against the record as it is now."""

    if reply is None:
        return narrative_from_sections(
            facts, {}, rejected={key: (failure or "모델 답 없음",) for key in MODEL_SECTIONS},
            writer=f"{writer} (실패)", written_at=written_at,
        )
    return narrative_from_sections(
        facts, reply, rejected=check_report_sections(reply, facts), writer=writer,
        written_at=written_at,
    )


# --- Preparing the prose when the experiment ends (decision 9) --------------------

#: The prose row's writer when no model wrote it.
SERVER_WRITER = "서버"


class ReportProsePreparer:
    """Writes each finished report's prose once, in the background.

    When an experiment ends the server asks for its report's prose; the report
    role's model is called once on a background thread (or not at all when no
    model is configured) and the reply is kept with the report. Downloads use
    what was kept. A person asking for it again is the only other call.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[tuple[str, str], threading.Event] = {}

    @staticmethod
    def _key(store: ExperimentReportStore, report_id: str) -> tuple[str, str]:
        return (str(store.path), report_id)

    def start(
        self, store: ExperimentReportStore, report_id: str, make_brain: Any, *, again: bool = False,
    ) -> str:
        """"preparing", "ready" (already kept), or "not_finished"."""

        key = self._key(store, report_id)
        with self._lock:
            if key in self._running:
                return "preparing"
            report = store.get_report(report_id)
            if report["status"] == "in_progress":
                return "not_finished"
            if not again and store.get_prose(report_id) is not None:
                return "ready"
            brain = make_brain()
            if brain is None or not brain.client:
                store.save_prose(report_id, writer=SERVER_WRITER, reply=None,
                                 failure="보고서 모델이 설정되지 않음",
                                 event_count=len(report["events"]))
                return "ready"
            done = threading.Event()
            self._running[key] = done
        threading.Thread(
            target=self._prepare, args=(store, report_id, brain, key, done),
            name=f"report-prose-{report_id}", daemon=True,
        ).start()
        return "preparing"

    def _prepare(
        self, store: ExperimentReportStore, report_id: str, brain: "ReportWriterBrain",
        key: tuple[str, str], done: threading.Event,
    ) -> None:
        count = 0
        try:
            report = store.get_report(report_id)
            events = list(report["events"])
            count = len(events)
            reply, failure = asyncio.run(brain.write_reply(brain.facts_for(report, events)))
            store.save_prose(report_id, writer=brain.model, reply=reply, failure=failure,
                             event_count=count)
        except Exception as exc:  # noqa: BLE001 -- the server's sentences stand in
            log.warning("report prose not prepared error=%s", type(exc).__name__)
            try:
                store.save_prose(report_id, writer=brain.model, reply=None,
                                 failure=f"준비 실패({type(exc).__name__})", event_count=count)
            except Exception:  # noqa: BLE001
                log.warning("report prose failure not kept")
        finally:
            with self._lock:
                self._running.pop(key, None)
            done.set()

    def wait(self, store: ExperimentReportStore, report_id: str, timeout: float) -> bool:
        with self._lock:
            done = self._running.get(self._key(store, report_id))
        return True if done is None else done.wait(timeout)

    def status(self, store: ExperimentReportStore, report_id: str) -> dict[str, Any]:
        """What the screen shows next to the record (decision 9)."""

        with self._lock:
            preparing = self._key(store, report_id) in self._running
        report = store.get_report(report_id)
        prose = None if preparing else store.get_prose(report_id)
        status: dict[str, Any] = {"report_id": report_id, "ai_written": False, "new_records": 0}
        if preparing:
            status.update(state="preparing", label="보고서 준비 중 — 실험 기록으로 보고서 문장을 쓰고 있습니다.")
        elif prose is not None:
            ai = prose["reply"] is not None
            status.update(state="ready", ai_written=ai, prepared_at=prose["prepared_at"],
                          new_records=max(0, len(report["events"]) - int(prose["event_count"])))
            if ai:
                label = f"보고서 준비됨 — AI({prose['writer']}) 문장 중 서버 검사를 통과한 것만 씁니다."
            elif prose["writer"] == SERVER_WRITER:
                label = "보고서 준비됨 — 서버 문장 (보고서 모델이 설정되지 않음)."
            else:
                label = f"보고서 준비됨 — 서버 문장 (AI 문장을 쓰지 못함: {prose['failure']})."
            if status["new_records"]:
                label += f" 준비한 뒤 기록이 {status['new_records']}건 늘었습니다. 표에는 들어가며, 문장은 다시 만들 수 있습니다."
            status["label"] = label
        elif report["status"] == "in_progress":
            status.update(state="not_finished", label="실험이 끝나면 보고서 문장을 준비합니다.")
        else:
            status.update(state="not_prepared", label="보고서 문장이 아직 준비되지 않았습니다.")
        return status


REPORT_PROSE = ReportProsePreparer()


# --- One document, two renderings (Word and Markdown) -----------------------------


def report_blocks(narrative: ReportNarrative) -> list[tuple[str, Any]]:
    """The report as blocks: ("title"|"h1"|"h2"|"h3"|"p"|"list"|"table"|"note", content)."""

    facts = narrative.facts
    assert facts is not None
    blocks: list[tuple[str, Any]] = [("title", narrative.title)]
    blocks.append(_table(("항목", "내용"), tuple(
        (name, value or "(직접 적어 주세요)") for name, value in facts.run_rows)))

    blocks += [("h1", "1. 실험 목적"), ("p", narrative.purpose)]

    blocks += [("h1", "2. 배경·원리"), ("p", narrative.background),
               ("note", "외부 자료는 쓰지 않았다. 이 칸은 프로토콜 원문만으로 썼다.")]

    blocks += [("h1", "3. 재료 및 방법"), ("h2", "3-1. 재료와 장비")]
    if facts.items:
        # Decisions 2-3: materials and equipment each in their own table, the
        # name as listed and the source steps that name it. A caption stands
        # between the two, so Word does not join them into one table.
        for kind in ("재료", "장비"):
            rows = tuple((item.name, _label_runs(item.steps) or "—")
                         for item in facts.items if item.kind == kind)
            if rows:
                blocks += [("h3", kind), _table(ITEM_HEADER, rows)]
        blocks.append(("note", "사용 단계는 서버가 원문 단계 글에서 그 이름(또는 원문이 쓰는 줄임말)을 찾아 적었다. "
                               "찾지 못하면 —."))
    else:
        blocks.append(("note", "원문에서 재료 목록을 불러오지 못했다."))
    blocks.append(("h2", "3-2. 수행한 단계"))
    rows = tuple(
        (step.label, (step.text + ("" if step.translated else " (원문 영어)")) if step.text else "(원문을 불러오지 못함)",
         step.completed_at or ("완료 기록 없음" if not step.completed else ""), step.timer_note or "—")
        for step in facts.steps
    )
    if rows:
        blocks.append(_table(("단계", "원문 단계", "완료 시각", "타이머 (원문 / 실제)"), rows))
    else:
        blocks.append(("note", "수행한 단계가 기록되지 않았다."))
    blocks += [("h2", "3-3. 방법 요약"), ("p", narrative.methods_summary),
               ("h2", "3-4. 원문과 다르게 한 점 (기록에서)")]
    blocks.append(("list", facts.deviations) if facts.deviations else ("note", "기록에서 원문과 다르게 한 점을 찾지 못했다."))

    blocks.append(("h1", "4. 결과"))
    if facts.records:
        blocks.append(_table(("단계", "종류", "기록 내용 (연구자가 말한 그대로)", "시각"), tuple(
            (r.step_label, r.kind, r.text, r.at) for r in facts.records)))
    else:
        blocks.append(("p", "기록된 관찰이 없습니다."))
    if facts.records:
        blocks += [("h2", "요약"), ("p", narrative.results_summary)]

    blocks += [("h1", "5. 고찰"),
               ("h2", "(가) 기록에서 확인되는 점"), ("list", narrative.discussion_confirmed),
               ("h2", "(나) 확인이 필요한 점"), ("list", narrative.discussion_to_check),
               ("h2", "(다) 원인 검토 제안")]
    if narrative.discussion_review:
        blocks += [("note", "아래는 기록된 이상·편차에 붙인 검토 제안이며 확인된 원인이 아니다."),
                   ("list", narrative.discussion_review)]
    else:
        blocks.append(("note", "기록된 이상·편차에 붙일 검토 제안이 없다."))
    blocks += [("h2", "(라) 연구자 해석"), ("p", "(연구자가 직접 적는다)\n\n\n")]

    blocks += [("h1", "6. 결론"), ("p", narrative.conclusion), ("h2", "다음 할 일"),
               ("list", narrative.next_steps)]

    blocks.append(("h1", "참고문헌"))
    references = []
    for source in narrative.sources:
        text = f"[{source.number}] {source.title}"
        if source.url:
            text += f" {source.url}"
        references.append(text + " — 실험에 쓴 프로토콜 원문")
    blocks.append(("list", tuple(references)))
    blocks.append(("note", authorship_line(narrative)))
    return blocks


def authorship_line(narrative: ReportNarrative) -> str:
    """Who wrote the report's sentences, under the references (decision 6)."""

    facts = narrative.facts
    written = _local(narrative.written_at, facts.zone if facts is not None else report_timezone())
    when = f"{written.year}년 {written.month}월 {written.day}일 {written:%H:%M}" if written else "시각 기록 없음"
    if any(narrative.section_origin.get(key) == "모델" for key in MODEL_SECTIONS):
        return (f"이 보고서의 문장 일부는 AI({narrative.writer})가 실험 기록과 프로토콜 원문을 바탕으로 "
                f"작성했으며, 외부 자료는 쓰지 않았다. 작성 {when}.")
    return (f"이 보고서의 문장은 서버가 실험 기록과 프로토콜 원문에서 만들었으며 AI 가 쓴 문장은 없다. "
            f"작성 {when}.")


#: Each report table's column widths as shares of the text width (decision 4):
#: short values (step, kind, time, timer, item) narrow, content wide.
#: The materials and equipment tables (decisions 2-3).
ITEM_HEADER = ("이름 (원문 그대로)", "사용 단계")

TABLE_WIDTHS: dict[tuple[str, ...], tuple[float, ...]] = {
    ("항목", "내용"): (0.22, 0.78),
    ITEM_HEADER: (0.80, 0.20),
    ("단계", "원문 단계", "완료 시각", "타이머 (원문 / 실제)"): (0.07, 0.59, 0.12, 0.22),
    ("단계", "종류", "기록 내용 (연구자가 말한 그대로)", "시각"): (0.07, 0.09, 0.73, 0.11),
}


def _table(header: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> tuple[str, Any]:
    return ("table", (header, rows, TABLE_WIDTHS[header]))


def render_markdown(narrative: ReportNarrative) -> str:
    def cell(value: Any) -> str:
        return " ".join(str(value).split()).replace("|", "\\|") or " "

    lines: list[str] = []
    for kind, content in report_blocks(narrative):
        if kind == "title":
            lines += [f"# {content}", ""]
        elif kind == "h1":
            lines += [f"## {content}", ""]
        elif kind == "h2":
            lines += [f"### {content}", ""]
        elif kind == "h3":
            lines += [f"#### {content}", ""]
        elif kind == "p":
            lines += [str(content).strip(), ""]
        elif kind == "note":
            lines += [f"> {content}", ""]
        elif kind == "list":
            lines += [f"- {item}" for item in content] + [""]
        elif kind == "table":
            header, rows, widths = content
            lines.append("| " + " | ".join(cell(h) for h in header) + " |")
            # Markdown has no column widths; the dash counts carry the
            # shares, which Pandoc reads as relative widths.
            lines.append("|" + "|".join("-" * max(3, round(share * 40)) for share in widths) + "|")
            lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


#: Word paragraph spacing (decision 1, 2026-10-06): points before, points
#: after, and line spacing as a multiple of one line, by block kind. The
#: template's default (10 pt after every paragraph, 1.15 lines) applied to
#: body lines, list items and table text alike and left long gaps; headings
#: keep room above them. A list's last item takes the body's space after.
DOCX_SPACING: dict[str, tuple[float, float, float]] = {
    "title": (0, 8, 1.0),
    "h1": (12, 4, 1.0),
    "h2": (8, 3, 1.0),
    "h3": (6, 2, 1.0),
    "p": (0, 4, 1.15),
    "note": (2, 4, 1.15),
    "list": (0, 1, 1.15),
    "table": (0, 0, 1.0),
}


def _space(paragraph: Any, kind: str, *, after: float | None = None) -> None:
    from docx.shared import Pt

    before, default_after, line = DOCX_SPACING[kind]
    form = paragraph.paragraph_format
    form.space_before = Pt(before)
    form.space_after = Pt(default_after if after is None else after)
    form.line_spacing = line


def render_docx(narrative: ReportNarrative) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(2.2))
    normal = document.styles["Normal"]
    normal.font.size = Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "맑은 고딕")
    # Paragraphs a person adds in Word take the body's spacing, not the template's.
    body_before, body_after, body_line = DOCX_SPACING["p"]
    normal.paragraph_format.space_before = Pt(body_before)
    normal.paragraph_format.space_after = Pt(body_after)
    normal.paragraph_format.line_spacing = body_line
    text_width = section.page_width - section.left_margin - section.right_margin

    for kind, content in report_blocks(narrative):
        if kind == "title":
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = paragraph.add_run(str(content))
            run.bold = True
            run.font.size = Pt(16)
            _space(paragraph, "title")
        elif kind in {"h1", "h2", "h3"}:
            paragraph = document.add_paragraph()
            run = paragraph.add_run(str(content))
            run.bold = True
            run.font.size = Pt({"h1": 13, "h2": 11, "h3": 10.5}[kind])
            run.font.color.rgb = RGBColor(20, 50, 35)
            _space(paragraph, kind)
        elif kind == "p":
            for line in str(content).split("\n"):
                _space(document.add_paragraph(line), "p")
        elif kind == "note":
            paragraph = document.add_paragraph()
            run = paragraph.add_run(str(content))
            run.italic = True
            run.font.color.rgb = RGBColor(90, 90, 90)
            _space(paragraph, "note")
        elif kind == "list":
            for index, item in enumerate(content):
                last = index == len(content) - 1
                _space(document.add_paragraph(f"• {item}"), "list",
                       after=DOCX_SPACING["p"][1] if last else None)
        elif kind == "table":
            header, rows, widths = content
            table = document.add_table(rows=1, cols=len(header))
            table.style = "Table Grid"
            for index, text in enumerate(header):
                table.rows[0].cells[index].text = str(text)
                for run in table.rows[0].cells[index].paragraphs[0].runs:
                    run.bold = True
                    run.font.size = Pt(9)
            for row in rows:
                cells = table.add_row().cells
                for index, value in enumerate(row):
                    cells[index].text = str(value)
                    for paragraph in cells[index].paragraphs:
                        for run in paragraph.runs:
                            run.font.size = Pt(9)
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        _space(paragraph, "table")
            _fix_column_widths(table, [int(text_width * share) for share in widths])
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _format_elapsed_clock(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        total = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _human_event_clock(created_at: str | None) -> str:
    if not created_at:
        return ""
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.strftime("%H:%M:%S")


def _fix_column_widths(table: Any, widths: Sequence[int]) -> None:
    """Fixed column widths in Word: the grid, every cell, and a fixed layout."""

    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    table.autofit = False
    properties = table._tbl.tblPr
    layout = properties.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        properties.append(layout)
    layout.set(qn("w:type"), "fixed")
    for column, width in zip(table.columns, widths):
        column.width = width
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = width


def new_session_id() -> str:
    return "session-" + secrets.token_hex(16)
