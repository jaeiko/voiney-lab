"""Idempotent, event-driven experiment records owned by the server.

The service stores only explicit workflow events and user-confirmed observations.
It never infers completion, approval, or a laboratory result from model output.
"""

from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
            "VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED", ""
        ).strip().casefold()
        if raw in _FALSE:
            return cls(False)
        if raw not in _TRUE:
            raise ValueError(
                "VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED must be a boolean"
            )
        path = os.environ.get(
            "VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB", ""
        ).strip()
        if not path:
            raise ValueError(
                "VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB is required"
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

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
                SELECT report_id,session_id,protocol_id,status,started_at,ended_at,
                       anomaly_count,blocker_count,finalization_version,
                       development_only
                  FROM experiment_reports {where}
                 ORDER BY started_at,report_id
                """,
                parameters,
            ).fetchall()
        return [
            {**dict(row), "development_only": bool(row["development_only"])}
            for row in rows
        ]

    def export_json(self, report_id: str) -> bytes:
        return (
            json.dumps(
                self.get_report(report_id), ensure_ascii=False,
                sort_keys=True, indent=2,
            ) + "\n"
        ).encode()

    def export_markdown(self, report_id: str) -> bytes:
        report = self.get_report(report_id)
        lines = [
            f"# Experiment report {report['report_id']}",
            "",
            f"- Protocol: {report['protocol_title']} ({report['protocol_id']})",
            f"- Revision: {report['protocol_revision']}",
            f"- Source SHA-256: {report['protocol_sha256']}",
            f"- Readiness: {report['readiness_status']}",
            f"- Development only: {str(report['development_only']).lower()}",
            f"- Status: {report['status']}",
            f"- Started: {report['started_at']}",
            f"- Ended: {report['ended_at'] or '—'}",
            "",
            "## Event timeline",
            "",
        ]
        for event in report["events"]:
            payload = event.get("payload") or {}
            detail = event["user_wording"] or payload.get("summary") or ""
            timer = payload.get("timer") if isinstance(payload.get("timer"), dict) else {}
            timer_bits = []
            for key, label in (
                ("source_duration_seconds", "defined"),
                ("started_at", "started"),
                ("elapsed_seconds", "elapsed"),
                ("remaining_seconds", "remaining"),
                ("completion_state", "completion"),
                ("demo_bypassed", "demo_bypassed"),
            ):
                if timer.get(key) not in (None, ""):
                    timer_bits.append(f"{label}={timer[key]}")
            extra = " · ".join(item for item in (detail, "; ".join(timer_bits)) if item)
            lines.append(
                f"- {event['created_at']} · {event['event_type']} · "
                f"step {event['step_label'] or '—'}"
                + (f" · {extra}" if extra else "")
            )
        return ("\n".join(lines) + "\n").encode()

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

    def export_docx(self, report_id: str) -> bytes:
        """Export a bounded undergraduate lab-report .docx from the event ledger.

        Student metadata fields stay blank. The SQLite event ledger remains the
        authority; this document is a derived, human-readable projection.
        """

        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Cm, Pt

        report = self.get_report(report_id)
        document = Document()
        section = document.sections[0]
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)

        title = document.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run("Undergraduate Laboratory Report")
        run.bold = True
        run.font.size = Pt(16)

        # Faithful header fields. Never invent student metadata.
        for label in ("Title", "Course", "Student number", "Name", "Advisor"):
            paragraph = document.add_paragraph()
            paragraph.add_run(f"{label}: ").bold = True

        events = list(report.get("events") or ())
        event_cap = 80
        truncated = len(events) > event_cap
        visible_events = events[:event_cap]
        timeline = [
            _human_event_label(event) for event in visible_events
        ]
        if truncated:
            timeline.append(
                "Additional events omitted to keep the report within 20 pages."
            )

        started = _human_event_clock(report.get("started_at"))
        ended = _human_event_clock(report.get("ended_at"))
        sections = (
            (
                "I. Purpose",
                (
                    f"Protocol: {report.get('protocol_title') or '—'} "
                    f"({report.get('protocol_id') or '—'})."
                ),
            ),
            (
                "II. Materials and Methods",
                (
                    f"Revision {report.get('protocol_revision') or '—'}. "
                    f"Readiness: {report.get('readiness_status') or '—'}."
                ),
            ),
            (
                "III. Results",
                "\n".join(timeline) if timeline else "No recorded events.",
            ),
            (
                "IV. Discussion",
                (
                    f"Recorded anomalies: {int(report.get('anomaly_count') or 0)}. "
                    f"Recorded blockers: {int(report.get('blocker_count') or 0)}."
                ),
            ),
            (
                "V. Conclusion",
                (
                    f"Status: {report.get('status') or '—'}. "
                    f"Started: {started or '—'}. "
                    f"Ended: {ended or '—'}."
                ),
            ),
        )
        for heading, body in sections:
            heading_paragraph = document.add_paragraph()
            heading_run = heading_paragraph.add_run(heading)
            heading_run.bold = True
            heading_run.font.size = Pt(12)
            for line in str(body).split("\n"):
                document.add_paragraph(line)

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


_HUMAN_EVENT_VERBS = {
    "session_started": "시작",
    "step_completed": "완료",
    "step_advanced": "이동",
    "step_presented": "안내",
    "timer_started": "타이머 시작",
    "workflow_paused": "일시정지",
    "workflow_resumed": "재개",
    "workflow_completed": "완료",
    "session_stopped": "종료",
    "anomaly": "이상 보고",
    "blocked": "진행 차단",
    "observation": "관찰",
    "source_consulted": "참고 자료 확인",
}


def _human_event_label(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    verb = _HUMAN_EVENT_VERBS.get(event_type, event_type.replace("_", " "))
    step_label = event.get("step_label")
    head = f"Step {step_label} {verb}" if step_label else verb
    parts = [head]
    clock = _human_event_clock(event.get("created_at"))
    if clock:
        parts.append(clock)
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    timer = payload.get("timer") if isinstance(payload.get("timer"), dict) else {}
    duration = timer.get("source_duration_seconds", timer.get("duration_seconds"))
    elapsed = timer.get("elapsed_seconds")
    remaining = timer.get("remaining_seconds")
    timer_bits = []
    if duration not in (None, ""):
        timer_bits.append(f"총 {_format_elapsed_clock(duration)}")
    if elapsed not in (None, ""):
        timer_bits.append(f"경과 {_format_elapsed_clock(elapsed)}")
    if remaining not in (None, ""):
        timer_bits.append(f"잔여 {_format_elapsed_clock(remaining)}")
    if timer_bits:
        parts.append("타이머 " + " · ".join(timer_bits))
    wording = event.get("user_wording")
    if wording:
        parts.append(str(wording))
    return " / ".join(parts)


def new_session_id() -> str:
    return "session-" + secrets.token_hex(16)
