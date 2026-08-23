"""Separate durable SQLite state for ProcedureSession transitions.

See procedures.py's module docstring: this backs the legacy, explicitly
config-gated ProcedureSession lane, not the production
ExperimentSession/WorkspaceStore authority.
"""
from __future__ import annotations
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProcedureTransitionError(RuntimeError): pass


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS procedure_sessions(
 session_id TEXT PRIMARY KEY, procedure_id TEXT NOT NULL, procedure_version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','completed')),
 current_step_index INTEGER NOT NULL CHECK(current_step_index>=0),
 started_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS procedure_step_events(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id TEXT NOT NULL REFERENCES procedure_sessions(session_id),
 step_id TEXT, event_type TEXT NOT NULL CHECK(event_type IN ('started','step_completed','completed')),
 previous_step_index INTEGER NOT NULL CHECK(previous_step_index>=0),
 resulting_step_index INTEGER NOT NULL CHECK(resulting_step_index>=0),
 event_at TEXT NOT NULL,
 UNIQUE(session_id,step_id,event_type)
);
CREATE TRIGGER IF NOT EXISTS procedure_events_no_update BEFORE UPDATE ON procedure_step_events
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS procedure_events_no_delete BEFORE DELETE ON procedure_step_events
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE UNIQUE INDEX IF NOT EXISTS one_started_event_per_session
ON procedure_step_events(session_id) WHERE event_type='started';
CREATE UNIQUE INDEX IF NOT EXISTS one_completed_event_per_session
ON procedure_step_events(session_id) WHERE event_type='completed';
CREATE TABLE IF NOT EXISTS procedure_observations(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id TEXT NOT NULL REFERENCES procedure_sessions(session_id),
 step_id TEXT NOT NULL,
 value_json TEXT NOT NULL,
 recorded_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS procedure_observations_no_update
BEFORE UPDATE ON procedure_observations
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS procedure_observations_no_delete
BEFORE DELETE ON procedure_observations
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TABLE IF NOT EXISTS procedure_step_timers(
 session_id TEXT NOT NULL REFERENCES procedure_sessions(session_id),
 step_id TEXT NOT NULL,
 duration_seconds INTEGER NOT NULL CHECK(duration_seconds>0),
 started_at TEXT NOT NULL,
 started_at_epoch REAL NOT NULL,
 deadline_at TEXT NOT NULL,
 deadline_epoch REAL NOT NULL,
 PRIMARY KEY(session_id,step_id)
);
CREATE TRIGGER IF NOT EXISTS procedure_timers_no_update
BEFORE UPDATE ON procedure_step_timers
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS procedure_timers_no_delete
BEFORE DELETE ON procedure_step_timers
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TABLE IF NOT EXISTS procedure_handoffs(
 session_id TEXT PRIMARY KEY REFERENCES procedure_sessions(session_id),
 report_id TEXT NOT NULL UNIQUE,
 step_id TEXT NOT NULL,
 reason TEXT NOT NULL,
 blocked_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS procedure_handoffs_no_update
BEFORE UPDATE ON procedure_handoffs
BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS procedure_handoffs_no_delete
BEFORE DELETE ON procedure_handoffs
BEGIN SELECT RAISE(ABORT,'append-only'); END;
"""


class ProcedureStore:
    def __init__(self,path:str|Path):
        target=Path(path); target.parent.mkdir(parents=True,exist_ok=True)
        self._connection=sqlite3.connect(target)
        self._connection.row_factory=sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(SCHEMA)
    def close(self): self._connection.close()
    def foreign_keys_enabled(self): return self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
    def _now(self): return datetime.now(timezone.utc).isoformat()
    def create_session(self,procedure_id:str,procedure_version:str)->dict[str,Any]:
        session_id=secrets.token_urlsafe(24); now=self._now()
        with self._connection:
            self._connection.execute(
                "INSERT INTO procedure_sessions VALUES (?,?,?,?,?,?,?,?)",
                (session_id,procedure_id,procedure_version,"active",0,now,now,None))
            self._connection.execute(
                """INSERT INTO procedure_step_events
                (session_id,step_id,event_type,previous_step_index,resulting_step_index,event_at)
                VALUES (?,?,?,?,?,?)""",(session_id,None,"started",0,0,now))
        return self.get_session(session_id)
    def get_session(self,session_id:str)->dict[str,Any]|None:
        row=self._connection.execute(
            "SELECT * FROM procedure_sessions WHERE session_id=?",(session_id,)).fetchone()
        return dict(row) if row else None
    def list_events(self,session_id:str)->list[dict[str,Any]]:
        return [dict(row) for row in self._connection.execute(
            "SELECT * FROM procedure_step_events WHERE session_id=? ORDER BY event_id",(session_id,))]
    def list_observations(self,session_id:str,step_id:str|None=None)->list[dict[str,Any]]:
        query=("SELECT * FROM procedure_observations WHERE session_id=?"
               + (" AND step_id=?" if step_id is not None else "")
               + " ORDER BY event_id")
        values=(session_id,step_id) if step_id is not None else (session_id,)
        result=[]
        for row in self._connection.execute(query,values):
            item=dict(row)
            try: item["value"]=json.loads(item.pop("value_json"))
            except (json.JSONDecodeError,TypeError): item["value"]=None
            result.append(item)
        return result
    def record_observation(self,session_id:str,step_id:str,value:Any)->dict[str,Any]:
        if not isinstance(step_id,str) or not step_id.strip():
            raise ProcedureTransitionError("invalid observation")
        now=self._now()
        try: encoded=json.dumps(value,ensure_ascii=False,separators=(",",":"))
        except (TypeError,ValueError) as exc:
            raise ProcedureTransitionError("invalid observation") from exc
        with self._connection:
            row=self._connection.execute(
                "SELECT status FROM procedure_sessions WHERE session_id=?",(session_id,)).fetchone()
            if row is None or row["status"]!="active" or self.get_handoff(session_id):
                raise ProcedureTransitionError("observation precondition failed")
            cursor=self._connection.execute(
                """INSERT INTO procedure_observations
                (session_id,step_id,value_json,recorded_at) VALUES (?,?,?,?)""",
                (session_id,step_id,encoded,now))
        return {
            "event_id":cursor.lastrowid,"session_id":session_id,"step_id":step_id,
            "value":value,"recorded_at":now,
        }
    def get_timer(self,session_id:str,step_id:str)->dict[str,Any]|None:
        row=self._connection.execute(
            "SELECT * FROM procedure_step_timers WHERE session_id=? AND step_id=?",
            (session_id,step_id)).fetchone()
        return dict(row) if row else None
    def list_timers(self,session_id:str)->list[dict[str,Any]]:
        return [dict(row) for row in self._connection.execute(
            """SELECT * FROM procedure_step_timers
            WHERE session_id=? ORDER BY started_at_epoch,step_id""",(session_id,))]
    def start_timer(
        self,session_id:str,step_id:str,duration_seconds:int,*,now_epoch:float|None=None
    )->tuple[dict[str,Any],bool]:
        if (not isinstance(step_id,str) or not step_id.strip() or
                not isinstance(duration_seconds,int) or isinstance(duration_seconds,bool) or
                duration_seconds<=0):
            raise ProcedureTransitionError("invalid timer")
        existing=self.get_timer(session_id,step_id)
        if existing is not None: return existing,True
        now_epoch=time.time() if now_epoch is None else float(now_epoch)
        deadline_epoch=now_epoch+duration_seconds
        started=datetime.fromtimestamp(now_epoch,timezone.utc).isoformat()
        deadline=datetime.fromtimestamp(deadline_epoch,timezone.utc).isoformat()
        try:
            with self._connection:
                row=self._connection.execute(
                    "SELECT status FROM procedure_sessions WHERE session_id=?",
                    (session_id,)).fetchone()
                if row is None or row["status"]!="active" or self.get_handoff(session_id):
                    raise ProcedureTransitionError("timer precondition failed")
                self._connection.execute(
                    """INSERT INTO procedure_step_timers
                    (session_id,step_id,duration_seconds,started_at,started_at_epoch,
                     deadline_at,deadline_epoch) VALUES (?,?,?,?,?,?,?)""",
                    (session_id,step_id,duration_seconds,started,now_epoch,
                     deadline,deadline_epoch))
        except sqlite3.IntegrityError:
            existing=self.get_timer(session_id,step_id)
            if existing is None: raise
            return existing,True
        timer=self.get_timer(session_id,step_id)
        if timer is None: raise ProcedureTransitionError("timer unavailable")
        return timer,False
    def get_handoff(self,session_id:str)->dict[str,Any]|None:
        row=self._connection.execute(
            "SELECT * FROM procedure_handoffs WHERE session_id=?",(session_id,)).fetchone()
        return dict(row) if row else None
    def block_for_handoff(
        self,session_id:str,step_id:str,report_id:str,reason:str
    )->tuple[dict[str,Any],bool]:
        if not all(isinstance(value,str) and value.strip()
                   for value in (step_id,report_id,reason)):
            raise ProcedureTransitionError("invalid handoff")
        existing=self.get_handoff(session_id)
        if existing is not None:
            if existing["report_id"]==report_id: return existing,True
            raise ProcedureTransitionError("handoff conflict")
        now=self._now()
        try:
            with self._connection:
                row=self._connection.execute(
                    "SELECT status FROM procedure_sessions WHERE session_id=?",
                    (session_id,)).fetchone()
                if row is None or row["status"]!="active":
                    raise ProcedureTransitionError("handoff precondition failed")
                self._connection.execute(
                    """INSERT INTO procedure_handoffs
                    (session_id,report_id,step_id,reason,blocked_at)
                    VALUES (?,?,?,?,?)""",
                    (session_id,report_id.strip(),step_id.strip(),reason.strip()[:800],now))
        except sqlite3.IntegrityError:
            existing=self.get_handoff(session_id)
            if existing is None or existing["report_id"]!=report_id:
                raise ProcedureTransitionError("handoff conflict")
            return existing,True
        handoff=self.get_handoff(session_id)
        if handoff is None: raise ProcedureTransitionError("handoff unavailable")
        return handoff,False

    def list_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM procedure_sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_active_session(self, procedure_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM procedure_sessions WHERE status='active'"
        params: list[Any] = []
        if procedure_id is not None:
            query += " AND procedure_id=?"
            params.append(procedure_id)
        query += " ORDER BY updated_at DESC LIMIT 1"
        row = self._connection.execute(query, params).fetchone()
        return dict(row) if row else None

    def complete_step(self,session_id:str,step_id:str,expected_index:int,resulting_index:int,*,final:bool,allow_branch:bool=False)->dict[str,Any]:
        if (not isinstance(step_id,str) or not step_id.strip() or
                not isinstance(expected_index,int) or isinstance(expected_index,bool) or
                not isinstance(resulting_index,int) or isinstance(resulting_index,bool) or
                expected_index<0 or resulting_index<0 or
                (resulting_index!=expected_index+1 and not (allow_branch and resulting_index>expected_index))):
            raise ProcedureTransitionError("invalid transition")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row=self._connection.execute(
                "SELECT * FROM procedure_sessions WHERE session_id=?",(session_id,)).fetchone()
            if (row is None or row["status"]!="active" or
                    row["current_step_index"]!=expected_index or
                    self.get_handoff(session_id) is not None):
                raise ProcedureTransitionError("transition precondition failed")
            now=self._now()
            self._connection.execute(
                """INSERT INTO procedure_step_events
                (session_id,step_id,event_type,previous_step_index,resulting_step_index,event_at)
                VALUES (?,?,?,?,?,?)""",
                (session_id,step_id,"step_completed",expected_index,resulting_index,now))
            status="completed" if final else "active"
            completed_at=now if final else None
            changed=self._connection.execute(
                """UPDATE procedure_sessions SET status=?,current_step_index=?,updated_at=?,
                   completed_at=? WHERE session_id=? AND status='active' AND current_step_index=?""",
                (status,resulting_index,now,completed_at,session_id,expected_index)).rowcount
            if changed!=1: raise ProcedureTransitionError("transition conflict")
            if final:
                self._connection.execute(
                    """INSERT INTO procedure_step_events
                    (session_id,step_id,event_type,previous_step_index,resulting_step_index,event_at)
                    VALUES (?,?,?,?,?,?)""",
                    (session_id,None,"completed",resulting_index,resulting_index,now))
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise ProcedureTransitionError("transition conflict") from exc
        except Exception:
            self._connection.rollback()
            raise
        return self.get_session(session_id)
