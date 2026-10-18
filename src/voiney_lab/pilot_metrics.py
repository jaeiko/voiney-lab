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
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
}

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
"""


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

    def turns(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM pilot_turns ORDER BY id")

    def marks(self) -> list[dict[str, Any]]:
        return self._read("SELECT * FROM pilot_marks ORDER BY id")


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
