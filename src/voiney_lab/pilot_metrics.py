"""Pilot metrics (lane LOG): what the pilot helped with, in numbers, codes and times.

The pilot measures (2026-10-16): experiments a week and how often people come
back, the steps finished without hands, records and deviations, the time to a
report, how often an uploaded protocol ran at once or after a review and how
long the review took, wrong state changes (zero), usability and willingness
to pay. This module keeps what the server can count for that.

What it keeps is numbers, codes and times only. No words that were said, no
audio, no protocol text, no model output: the experiment record keeps those,
as it was designed to, and this store never copies them. The one free text is
the optional line under the end-of-experiment rating (decision 3), which the
experimenter typed for the team; it is kept beside the rating and never enters
a metric, the admin screen or a metrics CSV.

Where it is kept: ``pilot_metrics.sqlite`` in the workspace data folder
(``VOINEY_LAB_WORKSPACE_DATA_DIR``; ``data/runtime/pilot/workspace`` for the
pilot), so lane PL's ``scripts/pilot_data_delete.py`` deletes it with the
workspace records. Without a workspace nothing is stored; the server log line
is still written.

Every row names the account it belongs to: the signed-in account's id, or the
development identity's when there is no sign-in. The log line names none.

A write that fails is logged as ``pilot.metrics.failed`` and dropped; the
experiment goes on (the server calls every entry point here inside its own
guard as well).
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import statistics
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from voiney_lab.experiment_reports import ExperimentReportStore
from voiney_lab.llm_router import router_reason_code

log = logging.getLogger("voiney_lab.pilot")

#: The store's file name inside the workspace data folder.
PILOT_METRICS_FILENAME = "pilot_metrics.sqlite"
#: The development identity's account id (identity.DevIdentityProvider), for
#: rows written where nobody signed in.
DEVELOPMENT_ACCOUNT_ID = "dev-local-admin"
#: A metrics write waits at most this long for the database; then it is dropped.
_BUSY_SECONDS = 0.5

ROUTER_RESULTS = frozenset({"off", "front", "used", "fallback", "stopped"})
REASON_CODES = frozenset({"none", "timeout", "rate_limit", "format", "refused", "error"})
TURN_SOURCES = frozenset({"voice", "candidate"})
#: Plan actions that ask the experimenter back (되묻기).
CLARIFY_ACTIONS = frozenset({
    "clarify_completion", "clarify_reference", "clarify_parameter", "transcript_unreliable",
})
#: Experiment-level marks and the codes each may carry.
MARK_CODES: Mapping[str, frozenset[str]] = {
    "button_change": frozenset({"pause", "resume"}),
    # Decision 2: the account an experiment record belongs to.
    "experiment_opened": frozenset(),
    "voice_connected": frozenset(),
    # The voice connection closed while the experiment was still running.
    "connection_lost": frozenset(),
    "timer_alert": frozenset({"last_minute", "end"}),
    # The experimenter's next words after a timer notice (value_ms: how long after).
    "alert_answered": frozenset(),
    "report_exported": frozenset({"docx", "md", "json", "csv"}),
}
ANALYSIS_OUTCOMES = frozenset({"passed", "review_pending", "notice", "failed"})
#: The pilot's days are the labs' days: Korea, UTC+9 all year.
PILOT_DAY_ZONE = timezone(timedelta(hours=9))
#: A connection that did not come back within this long is counted, not timed.
RECONNECT_WINDOW = timedelta(hours=2)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_turns(
 id INTEGER PRIMARY KEY,
 recorded_at TEXT NOT NULL,
 account_id TEXT NOT NULL,
 session_id TEXT,
 report_id TEXT,
 turn_id INTEGER NOT NULL,
 router TEXT NOT NULL CHECK(router IN ('off','front','used','fallback','stopped')),
 reason TEXT NOT NULL CHECK(reason IN ('none','timeout','rate_limit','format','refused','error')),
 action TEXT,
 source TEXT NOT NULL CHECK(source IN ('voice','candidate')),
 state_changed INTEGER NOT NULL CHECK(state_changed IN (0,1)),
 step_advanced INTEGER NOT NULL CHECK(step_advanced IN (0,1)),
 clarified INTEGER NOT NULL CHECK(clarified IN (0,1)),
 first_audio_ms INTEGER,
 total_ms INTEGER
);
CREATE TABLE IF NOT EXISTS pilot_marks(
 id INTEGER PRIMARY KEY,
 recorded_at TEXT NOT NULL,
 account_id TEXT NOT NULL,
 session_id TEXT,
 report_id TEXT,
 kind TEXT NOT NULL,
 code TEXT,
 value_ms INTEGER
);
CREATE TABLE IF NOT EXISTS pilot_protocol_events(
 id INTEGER PRIMARY KEY,
 recorded_at TEXT NOT NULL,
 account_id TEXT NOT NULL,
 protocol_id TEXT NOT NULL,
 revision_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('analysis','review_registered')),
 outcome TEXT CHECK(outcome IS NULL OR outcome IN ('passed','review_pending','notice','failed')),
 duration_ms INTEGER,
 card_count INTEGER
);
CREATE TABLE IF NOT EXISTS pilot_feedback(
 id INTEGER PRIMARY KEY,
 recorded_at TEXT NOT NULL,
 account_id TEXT NOT NULL,
 report_id TEXT NOT NULL,
 rating INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 5),
 skipped INTEGER NOT NULL CHECK(skipped IN (0,1)),
 note TEXT,
 CHECK((skipped=1 AND rating IS NULL) OR (skipped=0 AND rating IS NOT NULL))
);
"""


#: The memo under the rating: one line, at most this many characters.
FEEDBACK_NOTE_MAX = 200


@dataclass(frozen=True)
class FeedbackAnswer:
    """Decision 3: "이번 실험에 도움이 됐나요?" -- a rating 1-5 and a memo, or a skip."""

    report_id: str
    rating: int | None
    skipped: bool
    note: str | None

    @classmethod
    def from_request(cls, payload: Mapping[str, Any]) -> "FeedbackAnswer":
        """The page's answer, or ValueError for anything that is not one."""

        report_id = payload.get("report_id")
        if not isinstance(report_id, str) or _IDENTIFIER.fullmatch(report_id) is None:
            raise ValueError("report_id is invalid")
        skipped = payload.get("skipped", False)
        if not isinstance(skipped, bool):
            raise ValueError("skipped is invalid")
        rating = payload.get("rating")
        if skipped:
            if rating is not None:
                raise ValueError("a skipped answer has no rating")
        elif isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ValueError("rating must be a whole number from 1 to 5")
        note = payload.get("note")
        if note is not None:
            if not isinstance(note, str):
                raise ValueError("note is invalid")
            note = " ".join(note.split()) or None
            if note is not None and len(note) > FEEDBACK_NOTE_MAX:
                raise ValueError("note is too long")
        return cls(report_id=report_id, rating=rating, skipped=skipped, note=note)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("pilot metrics identifier is malformed")
    return value


def _code(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise ValueError("pilot metrics code is malformed")
    return value


def _milliseconds(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(round(value))


@dataclass(frozen=True)
class TurnRecord:
    """One finished voice turn, as the record keeps it."""

    account_id: str
    session_id: str | None
    report_id: str | None
    turn_id: int
    router: str
    reason: str
    action: str | None
    source: str
    state_changed: bool
    step_advanced: bool
    clarified: bool
    first_audio_ms: int | None
    total_ms: int | None

    def __post_init__(self) -> None:
        _identifier(self.account_id)
        _identifier(self.session_id)
        _identifier(self.report_id)
        _code(self.action)
        if self.router not in ROUTER_RESULTS or self.reason not in REASON_CODES:
            raise ValueError("router result or reason is not a known code")
        if self.source not in TURN_SOURCES:
            raise ValueError("turn source is not a known code")

    def log_line(self) -> str:
        """The server log's one line for the turn: codes and numbers, no account."""

        def number(value: int | None) -> str:
            return "-" if value is None else str(value)

        return (
            f"pilot.turn turn_id={self.turn_id} router={self.router} reason={self.reason} "
            f"action={self.action or '-'} changed={int(self.state_changed)} "
            f"step={int(self.step_advanced)} clarify={int(self.clarified)} "
            f"source={self.source} first_audio_ms={number(self.first_audio_ms)} "
            f"total_ms={number(self.total_ms)}"
        )


class PilotMetricsStore:
    """The pilot metrics SQLite file: append-only rows of codes and numbers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=_BUSY_SECONDS)
        connection.row_factory = sqlite3.Row
        connection.executescript(_SCHEMA)
        return connection

    def _read(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Rows of a read, opening the file read-only; a missing file reads empty."""

        if not self.path.is_file():
            return []
        connection = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=_BUSY_SECONDS)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(sql, parameters)]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
        finally:
            connection.close()

    def record_turn(self, record: TurnRecord) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """INSERT INTO pilot_turns(recorded_at,account_id,session_id,report_id,
                    turn_id,router,reason,action,source,state_changed,step_advanced,
                    clarified,first_audio_ms,total_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        _now(), record.account_id, record.session_id, record.report_id,
                        record.turn_id, record.router, record.reason, record.action,
                        record.source, int(record.state_changed), int(record.step_advanced),
                        int(record.clarified), record.first_audio_ms, record.total_ms,
                    ),
                )
        finally:
            connection.close()

    def record_mark(
        self, *, account_id: str, kind: str, session_id: str | None = None,
        report_id: str | None = None, code: str | None = None, value_ms: int | None = None,
    ) -> None:
        if kind not in MARK_CODES:
            raise ValueError("pilot metrics mark kind is unknown")
        if code is not None and code not in MARK_CODES[kind]:
            raise ValueError("pilot metrics mark code is unknown")
        values = (
            _now(), _identifier(account_id), _identifier(session_id), _identifier(report_id),
            kind, code, _milliseconds(value_ms),
        )
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """INSERT INTO pilot_marks(recorded_at,account_id,session_id,report_id,
                    kind,code,value_ms) VALUES(?,?,?,?,?,?,?)""",
                    values,
                )
        finally:
            connection.close()

    def record_protocol_event(
        self, *, account_id: str, protocol_id: str, kind: str, revision_id: str | None = None,
        outcome: str | None = None, duration_ms: int | None = None,
        card_count: int | None = None,
    ) -> None:
        """An analysis that ended (its result, time, cards) or a review registered."""

        if kind not in {"analysis", "review_registered"}:
            raise ValueError("pilot protocol event kind is unknown")
        if outcome is not None and outcome not in ANALYSIS_OUTCOMES:
            raise ValueError("analysis outcome is unknown")
        values = (
            _now(), _identifier(account_id), _identifier(protocol_id),
            _identifier(revision_id), kind, outcome, _milliseconds(duration_ms),
            _milliseconds(card_count),
        )
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """INSERT INTO pilot_protocol_events(recorded_at,account_id,protocol_id,
                    revision_id,kind,outcome,duration_ms,card_count) VALUES(?,?,?,?,?,?,?,?)""",
                    values,
                )
        finally:
            connection.close()

    def record_feedback(self, *, account_id: str, answer: FeedbackAnswer) -> None:
        values = (
            _now(), _identifier(account_id), _identifier(answer.report_id), answer.rating,
            int(answer.skipped), answer.note,
        )
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """INSERT INTO pilot_feedback(recorded_at,account_id,report_id,rating,
                    skipped,note) VALUES(?,?,?,?,?,?)""",
                    values,
                )
        finally:
            connection.close()

    def turns(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM pilot_turns ORDER BY id")

    def feedback(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM pilot_feedback ORDER BY id")

    def marks(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM pilot_marks ORDER BY id")

    def protocol_events(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM pilot_protocol_events ORDER BY id")


@dataclass(frozen=True)
class PilotContext:
    """Who and which experiment a server event belongs to."""

    account_id: str
    session_id: str | None = None
    report_id: str | None = None
    router_enabled: bool = False
    store: PilotMetricsStore | None = None


@dataclass
class _TurnNotes:
    router: str | None = None
    reason: str = "none"
    action: str | None = None
    state_changed: bool = False
    clarified: bool = False
    source: str = "voice"


def _router_result(router: Mapping[str, Any]) -> tuple[str, str]:
    handled_by = str(router.get("handled_by") or "")
    reason = router_reason_code(router.get("fallback_reason"))
    if handled_by.startswith("front:"):
        return "front", "none"
    if handled_by == "fallback_rules":
        return "fallback", reason if reason != "none" else "error"
    if handled_by == "model_unavailable":
        return "stopped", reason
    return "used", "none"


class PilotRecorder:
    """Collects one turn's codes from its server events and writes them when it ends."""

    #: Turns whose end never came (a cancelled turn) are forgotten past this.
    _MAX_OPEN_TURNS = 256

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: OrderedDict[tuple[str | None, int], _TurnNotes] = OrderedDict()
        #: session id -> when (the session's clock) the last unanswered timer notice was shown.
        self._alerts: dict[str | None, float] = {}

    def alert_shown(self, session_id: str | None, at: float) -> None:
        """A timer notice went to the screen; the next words are timed from here."""

        with self._lock:
            self._alerts[session_id] = at
            while len(self._alerts) > self._MAX_OPEN_TURNS:
                self._alerts.pop(next(iter(self._alerts)))

    def alert_answered(self, session_id: str | None, heard_at: float) -> int | None:
        """Milliseconds from the last notice to words heard after it, once; else None."""

        with self._lock:
            shown = self._alerts.get(session_id)
            if shown is None or heard_at < shown:
                return None
            del self._alerts[session_id]
        return int(round((heard_at - shown) * 1000))

    def _notes(self, session_id: str | None, turn_id: int) -> _TurnNotes:
        key = (session_id, turn_id)
        notes = self._turns.get(key)
        if notes is None:
            notes = self._turns[key] = _TurnNotes()
            while len(self._turns) > self._MAX_OPEN_TURNS:
                self._turns.popitem(last=False)
        return notes

    def note_input(self, session_id: str | None, turn_id: int, source: str) -> None:
        """The turn's words came from ``source`` (a tapped candidate) instead of a voice."""

        if source not in TURN_SOURCES:
            raise ValueError("turn source is not a known code")
        with self._lock:
            self._notes(session_id, turn_id).source = source

    def turn_event(
        self, context: PilotContext, turn_id: int, kind: str, fields: Mapping[str, Any],
    ) -> TurnRecord | None:
        """Read one of a turn's server events; at ``turn.done`` the turn is recorded."""

        with self._lock:
            notes = self._notes(context.session_id, turn_id)
            if kind == "turn.route_decision":
                notes.action = str(fields.get("action") or "") or None
                notes.state_changed = fields.get("state_mutation") is True
                notes.clarified = notes.clarified or notes.action in CLARIFY_ACTIONS
                router = fields.get("router")
                if isinstance(router, Mapping):
                    notes.router, notes.reason = _router_result(router)
                    notes.clarified = notes.clarified or router.get("read_only") == "clarify"
                return None
            if kind == "router.stopped":
                notes.router = "stopped"
                notes.reason = router_reason_code(fields.get("fallback_reason"))
                return None
            if kind != "turn.done":
                return None
            self._turns.pop((context.session_id, turn_id), None)
        timings = fields.get("timings_ms") if isinstance(fields.get("timings_ms"), Mapping) else {}
        record = TurnRecord(
            account_id=context.account_id,
            session_id=context.session_id,
            report_id=context.report_id,
            turn_id=turn_id,
            router=notes.router or ("front" if context.router_enabled else "off"),
            reason=notes.reason,
            action=notes.action,
            source=notes.source,
            state_changed=notes.state_changed,
            step_advanced=notes.state_changed and notes.action == "next",
            clarified=notes.clarified,
            first_audio_ms=_milliseconds(timings.get("first_audio_ms")),
            total_ms=_milliseconds(timings.get("total_ms")),
        )
        log.info(record.log_line())
        if context.store is not None:
            context.store.record_turn(record)
        return record


#: The server's one recorder.
PILOT_METRICS = PilotRecorder()


def analysis_outcome(public: Mapping[str, Any]) -> str:
    """An analysis that ended, as a code: passed, review_pending or notice.

    "notice" is an analysis that ended but may not run, with the reasons shown
    to the experimenter (an execution blocker); "failed" (an analysis that
    raised) is the server's to say, not this projection's.
    """

    if public.get("analysis_status") == "review_pending":
        return "review_pending"
    if public.get("available_for_execution") is True:
        return "passed"
    return "notice"


def review_card_count(public: Mapping[str, Any]) -> int:
    pending = public.get("review_pending")
    count = pending.get("needs_review_count") if isinstance(pending, Mapping) else None
    return count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 0


# --- The summaries (decision 2): read from copies, never from the live files


@dataclass(frozen=True)
class PilotSources:
    """The three files the summaries read; any of them may be absent."""

    metrics_db: Path | None
    reports_db: Path | None
    workspace_db: Path | None

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "PilotSources":
        """The pilot folder's layout (``scripts/run_pilot.sh``: data/runtime/pilot)."""

        folder = Path(data_dir)
        return cls(
            metrics_db=folder / "workspace" / PILOT_METRICS_FILENAME,
            reports_db=folder / "experiment_reports.sqlite",
            workspace_db=folder / "workspace" / "commercial_workspace.sqlite",
        )


def _copy(path: Path | None, folder: str, name: str) -> Path | None:
    """A copy of a SQLite file (with its write-ahead log) to read without touching it."""

    if path is None or not Path(path).is_file():
        return None
    target = Path(folder) / f"{name}.sqlite"
    shutil.copyfile(path, target)
    wal = Path(f"{path}-wal")
    if wal.is_file():
        shutil.copyfile(wal, Path(f"{target}-wal"))
    return target


def _owners(path: Path | None) -> dict[str, str]:
    """Experiment session id -> its owner's account, from the workspace's sessions."""

    if path is None:
        return {}
    connection = sqlite3.connect(path)
    try:
        return {
            str(session_id): str(owner)
            for session_id, owner in connection.execute(
                "SELECT session_id,owner_principal_id FROM experiment_sessions")
        }
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


@dataclass
class _Snapshot:
    ledger: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    marks: list[dict[str, Any]]
    protocol_events: list[dict[str, Any]]
    feedback: list[dict[str, Any]]
    owners: dict[str, str]
    metrics_present: bool


def _snapshot(sources: PilotSources) -> _Snapshot:
    with tempfile.TemporaryDirectory(prefix="pilot-metrics-") as folder:
        reports = _copy(sources.reports_db, folder, "reports")
        metrics = _copy(sources.metrics_db, folder, "metrics")
        workspace = _copy(sources.workspace_db, folder, "workspace")
        store = PilotMetricsStore(metrics) if metrics is not None else None
        return _Snapshot(
            ledger=ExperimentReportStore(reports).summary_rows() if reports else [],
            turns=store.turns() if store else [],
            marks=store.marks() if store else [],
            protocol_events=store.protocol_events() if store else [],
            feedback=store.feedback() if store else [],
            owners=_owners(workspace),
            metrics_present=store is not None,
        )


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def pilot_day(value: Any) -> str | None:
    """The lab's date (Korea) of a stored UTC time, as YYYY-MM-DD."""

    moment = _moment(value)
    return moment.astimezone(PILOT_DAY_ZONE).date().isoformat() if moment else None


def _median(values: Iterable[Any]) -> int | None:
    numbers = [value for value in values if isinstance(value, (int, float))]
    return int(round(statistics.median(numbers))) if numbers else None


def _within(day: str | None, since: date | None, until: date | None) -> bool:
    if day is None:
        return since is None and until is None
    if since is not None and day < since.isoformat():
        return False
    return not (until is not None and day > until.isoformat())


EXPERIMENT_COLUMNS = (
    "day", "account_id", "report_id", "protocol_id", "development_only", "status",
    "started_at", "ended_at", "duration_s", "account_experiment_number", "protocol_run_number",
    "steps", "steps_by_voice", "steps_by_button", "hands_free_ratio", "reverts",
    "turns", "router_used", "router_fallbacks", "router_timeouts", "router_rate_limits",
    "router_format", "router_refused", "router_errors", "clarifies", "first_audio_median_ms",
    "button_changes", "timer_last_minute_alerts", "timer_end_alerts", "alert_answer_median_ms",
    "disconnects", "disconnected_ms", "records", "deviations", "anomalies", "system_anomalies",
    "corrections", "report_made", "report_minutes_after_end", "rating", "feedback_skipped",
)


def _experiments(snapshot: _Snapshot) -> list[dict[str, Any]]:
    opened = {
        str(mark["session_id"]): str(mark["account_id"])
        for mark in snapshot.marks if mark["kind"] == "experiment_opened" and mark["session_id"]
    }
    connected: dict[str, list[datetime]] = {}
    for mark in snapshot.marks:
        if mark["kind"] == "voice_connected" and (moment := _moment(mark["recorded_at"])):
            connected.setdefault(str(mark["account_id"]), []).append(moment)
    turns_by: dict[str, list[dict[str, Any]]] = {}
    for turn in snapshot.turns:
        turns_by.setdefault(str(turn["session_id"]), []).append(turn)
    marks_by: dict[str, list[dict[str, Any]]] = {}
    for mark in snapshot.marks:
        marks_by.setdefault(str(mark["session_id"]), []).append(mark)
        if mark["report_id"]:
            marks_by.setdefault(f"report:{mark['report_id']}", []).append(mark)
    # The last answer about an experiment is the one that counts (decision 3).
    answers = {str(item["report_id"]): item for item in snapshot.feedback}
    numbers: dict[tuple[str, ...], int] = {}
    rows: list[dict[str, Any]] = []
    for record in sorted(snapshot.ledger, key=lambda item: (item["started_at"], item["report_id"])):
        session_id = record["session_id"]
        account = (
            opened.get(session_id) or snapshot.owners.get(session_id) or DEVELOPMENT_ACCOUNT_ID
        )
        turns = turns_by.get(session_id, [])
        marks = marks_by.get(session_id, []) + [
            mark for mark in marks_by.get(f"report:{record['report_id']}", [])
            if mark["session_id"] != session_id
        ]
        measured = snapshot.metrics_present and bool(turns or marks)
        kinds = lambda kind, code=None: [  # noqa: E731 -- a local filter
            mark for mark in marks if mark["kind"] == kind and (code is None or mark["code"] == code)
        ]
        by_button = sum(1 for turn in turns if turn["step_advanced"] and turn["source"] == "candidate")
        steps = int(record["steps"])
        by_voice = max(0, steps - by_button)
        started, ended = _moment(record["started_at"]), _moment(record["ended_at"])
        lost_ms: list[int] = []
        for mark in kinds("connection_lost"):
            lost = _moment(mark["recorded_at"])
            back = next((
                moment for moment in sorted(connected.get(account, []))
                if lost is not None and lost < moment <= lost + RECONNECT_WINDOW
            ), None)
            if lost is not None and back is not None:
                lost_ms.append(int((back - lost).total_seconds() * 1000))
        made = [
            moment for mark in kinds("report_exported")
            if mark["code"] in {"docx", "md"} and (moment := _moment(mark["recorded_at"]))
        ]
        report_made = bool(record["report_prose"]) or bool(made)
        numbers[(account,)] = numbers.get((account,), 0) + 1
        protocol_key = (account, record["protocol_id"])
        numbers[protocol_key] = numbers.get(protocol_key, 0) + 1

        def count(predicate) -> int | None:
            return sum(1 for turn in turns if predicate(turn)) if measured else None

        rows.append({
            "day": pilot_day(record["started_at"]),
            "account_id": account,
            "report_id": record["report_id"],
            "protocol_id": record["protocol_id"],
            "development_only": int(bool(record["development_only"])),
            "status": record["status"],
            "started_at": record["started_at"],
            "ended_at": record["ended_at"],
            "duration_s": (
                int((ended - started).total_seconds()) if started and ended else None),
            "account_experiment_number": numbers[(account,)],
            "protocol_run_number": numbers[protocol_key],
            "steps": steps,
            "steps_by_voice": by_voice,
            "steps_by_button": by_button,
            "hands_free_ratio": round(by_voice / steps, 4) if steps else None,
            "reverts": record["reverts"],
            "turns": len(turns) if measured else None,
            "router_used": count(lambda turn: turn["router"] == "used"),
            "router_fallbacks": count(lambda turn: turn["router"] == "fallback"),
            "router_timeouts": count(lambda turn: turn["reason"] == "timeout"),
            "router_rate_limits": count(lambda turn: turn["reason"] == "rate_limit"),
            "router_format": count(lambda turn: turn["reason"] == "format"),
            "router_refused": count(lambda turn: turn["reason"] == "refused"),
            "router_errors": count(lambda turn: turn["reason"] == "error"),
            "clarifies": count(lambda turn: bool(turn["clarified"])),
            "first_audio_median_ms": _median(turn["first_audio_ms"] for turn in turns),
            "button_changes": len(kinds("button_change")) if measured else None,
            "timer_last_minute_alerts": record["timer_last_minute_alerts"],
            "timer_end_alerts": record["timer_end_alerts"],
            "alert_answer_median_ms": _median(
                mark["value_ms"] for mark in kinds("alert_answered")),
            "disconnects": len(kinds("connection_lost")) if measured else None,
            "disconnected_ms": sum(lost_ms) if lost_ms else None,
            "records": record["records"],
            "deviations": record["deviations"],
            "anomalies": record["anomalies"],
            "system_anomalies": record["system_anomalies"],
            "corrections": record["corrections"],
            "report_made": int(report_made),
            "report_minutes_after_end": (
                round((min(made) - ended).total_seconds() / 60, 1)
                if made and ended and min(made) >= ended else None),
            "rating": (answers.get(record["report_id"]) or {}).get("rating"),
            "feedback_skipped": (
                (answers[record["report_id"]]["skipped"])
                if record["report_id"] in answers else None),
        })
    return rows


def experiment_rows(
    sources: PilotSources, *, since: date | None = None, until: date | None = None,
    account: str | None = None,
) -> list[dict[str, Any]]:
    """One row per experiment record (EXPERIMENT_COLUMNS), oldest first."""

    return [
        row for row in _experiments(_snapshot(sources))
        if _within(row["day"], since, until) and (account is None or row["account_id"] == account)
    ]


ANALYSIS_COLUMNS = (
    "day", "account_id", "protocol_id", "revision_id", "recorded_at", "outcome",
    "duration_ms", "card_count", "review_registered_after_s", "executed",
    "executed_after_review",
)


def _analyses(snapshot: _Snapshot) -> list[dict[str, Any]]:
    events = snapshot.protocol_events
    runs = [
        (moment, record["protocol_id"]) for record in snapshot.ledger
        if (moment := _moment(record["started_at"]))
    ]
    rows: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if event["kind"] != "analysis":
            continue
        ended = _moment(event["recorded_at"])
        protocol = event["protocol_id"]
        later = events[index + 1:]
        next_analysis = next((
            _moment(item["recorded_at"]) for item in later
            if item["kind"] == "analysis" and item["protocol_id"] == protocol
        ), None)
        review = next((
            moment for item in later
            if item["kind"] == "review_registered" and item["protocol_id"] == protocol
            and (moment := _moment(item["recorded_at"]))
            and (next_analysis is None or moment <= next_analysis)
        ), None)
        started_runs = [
            moment for moment, run_protocol in runs
            if run_protocol == protocol and ended is not None and moment >= ended
            and (next_analysis is None or moment < next_analysis)
        ]
        rows.append({
            "day": pilot_day(event["recorded_at"]),
            "account_id": event["account_id"],
            "protocol_id": protocol,
            "revision_id": event["revision_id"],
            "recorded_at": event["recorded_at"],
            "outcome": event["outcome"],
            "duration_ms": event["duration_ms"],
            "card_count": event["card_count"],
            "review_registered_after_s": (
                int((review - ended).total_seconds()) if review and ended else None),
            "executed": int(bool(started_runs)),
            "executed_after_review": int(
                review is not None and any(moment >= review for moment in started_runs)),
        })
    return rows


def analysis_rows(
    sources: PilotSources, *, since: date | None = None, until: date | None = None,
    account: str | None = None,
) -> list[dict[str, Any]]:
    """One row per analysis that ended (ANALYSIS_COLUMNS), oldest first."""

    return [
        row for row in _analyses(_snapshot(sources))
        if _within(row["day"], since, until) and (account is None or row["account_id"] == account)
    ]


# --- Decision 4: the day table and the memos


DAY_COLUMNS = (
    "day", "accounts", "returning_accounts", "experiments", "experiments_ended",
    "steps", "steps_by_voice", "steps_by_button", "hands_free_ratio", "reverts",
    "turns", "router_fallbacks", "router_timeouts", "router_rate_limits", "clarifies",
    "timer_alerts", "alert_answer_median_ms", "disconnects", "disconnected_ms",
    "records", "deviations", "anomalies", "reports_made", "ratings", "rating_mean",
    "analyses", "analyses_passed", "analyses_review_pending", "analyses_notice",
    "analyses_failed",
)


def _total(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [row[key] for row in rows if isinstance(row.get(key), int)]
    return sum(values) if values else None


def day_rows(
    sources: PilotSources, *, since: date | None = None, until: date | None = None,
    account: str | None = None,
) -> list[dict[str, Any]]:
    """One row per day (Korea) with an experiment or an analysis (DAY_COLUMNS).

    An experiment counts on the day it started. A returning account is one
    that ran an experiment on an earlier day, in all the data, not only the
    period asked for. Totals of a measure nobody measured stay empty.
    """

    snapshot = _snapshot(sources)
    experiments = _experiments(snapshot)
    analyses = _analyses(snapshot)
    first_day: dict[str, str] = {}
    for row in experiments:
        if row["day"] and row["account_id"] not in first_day:
            first_day[row["account_id"]] = row["day"]
    selected = [
        row for row in experiments
        if _within(row["day"], since, until) and (account is None or row["account_id"] == account)
    ]
    chosen = [
        row for row in analyses
        if _within(row["day"], since, until) and (account is None or row["account_id"] == account)
    ]
    days = sorted({row["day"] for row in selected + chosen if row["day"]})
    out: list[dict[str, Any]] = []
    for day in days:
        runs = [row for row in selected if row["day"] == day]
        runs_analysed = [row for row in chosen if row["day"] == day]
        accounts = {row["account_id"] for row in runs}
        steps = sum(row["steps"] for row in runs)
        by_voice = sum(row["steps_by_voice"] for row in runs)
        ratings = [row["rating"] for row in runs if isinstance(row["rating"], int)]
        alerts = [
            row["timer_last_minute_alerts"] + row["timer_end_alerts"] for row in runs]
        outcomes = [row["outcome"] for row in runs_analysed]
        out.append({
            "day": day,
            "accounts": len(accounts),
            "returning_accounts": sum(1 for item in accounts if first_day.get(item, day) < day),
            "experiments": len(runs),
            "experiments_ended": sum(1 for row in runs if row["ended_at"]),
            "steps": steps,
            "steps_by_voice": by_voice,
            "steps_by_button": sum(row["steps_by_button"] for row in runs),
            "hands_free_ratio": round(by_voice / steps, 4) if steps else None,
            "reverts": sum(row["reverts"] for row in runs),
            "turns": _total(runs, "turns"),
            "router_fallbacks": _total(runs, "router_fallbacks"),
            "router_timeouts": _total(runs, "router_timeouts"),
            "router_rate_limits": _total(runs, "router_rate_limits"),
            "clarifies": _total(runs, "clarifies"),
            "timer_alerts": sum(alerts),
            "alert_answer_median_ms": _median(
                row["alert_answer_median_ms"] for row in runs),
            "disconnects": _total(runs, "disconnects"),
            "disconnected_ms": _total(runs, "disconnected_ms"),
            "records": sum(row["records"] for row in runs),
            "deviations": sum(row["deviations"] for row in runs),
            "anomalies": sum(row["anomalies"] for row in runs),
            "reports_made": sum(row["report_made"] for row in runs),
            "ratings": len(ratings),
            "rating_mean": round(statistics.fmean(ratings), 2) if ratings else None,
            "analyses": len(runs_analysed),
            "analyses_passed": outcomes.count("passed"),
            "analyses_review_pending": outcomes.count("review_pending"),
            "analyses_notice": outcomes.count("notice"),
            "analyses_failed": outcomes.count("failed"),
        })
    return out


NOTE_COLUMNS = ("day", "account_id", "report_id", "recorded_at", "rating", "skipped", "note")


def note_rows(
    sources: PilotSources, *, since: date | None = None, until: date | None = None,
    account: str | None = None,
) -> list[dict[str, Any]]:
    """The memos under the ratings (decision 3), for the team; never part of a metric."""

    snapshot = _snapshot(sources)
    return [
        {
            "day": pilot_day(item["recorded_at"]),
            "account_id": item["account_id"],
            "report_id": item["report_id"],
            "recorded_at": item["recorded_at"],
            "rating": item["rating"],
            "skipped": item["skipped"],
            "note": item["note"],
        }
        for item in snapshot.feedback
        if item["note"]
        and _within(pilot_day(item["recorded_at"]), since, until)
        and (account is None or item["account_id"] == account)
    ]


#: An account id as the summaries and the admin route accept it.
ACCOUNT_ID_PATTERN = _IDENTIFIER


def day_totals(days: list[dict[str, Any]]) -> dict[str, Any]:
    """The period's totals for the admin screen's boxes, from its day rows."""

    def add(key: str) -> int | None:
        values = [day[key] for day in days if isinstance(day.get(key), int)]
        return sum(values) if values else None

    steps = add("steps") or 0
    ratings = add("ratings") or 0
    rated = sum(
        day["rating_mean"] * day["ratings"] for day in days
        if isinstance(day.get("rating_mean"), (int, float)) and day.get("ratings"))
    return {
        "experiments": add("experiments") or 0,
        "steps": steps,
        "hands_free_ratio": round((add("steps_by_voice") or 0) / steps, 4) if steps else None,
        "timer_alerts": add("timer_alerts") or 0,
        "disconnects": add("disconnects"),
        "ratings": ratings,
        "rating_mean": round(rated / ratings, 2) if ratings else None,
        "analyses": add("analyses") or 0,
    }
