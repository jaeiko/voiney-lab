"""Separate durable SQLite state for ProcedureSession transitions."""
from __future__ import annotations
import secrets
import sqlite3
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
"""


class ProcedureStore:
    def __init__(self,path:str|Path):
        target=Path(path); target.parent.mkdir(parents=True,exist_ok=True)
        self._connection=sqlite3.connect(target)
        self._connection.row_factory=sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
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
    def complete_step(self,session_id:str,step_id:str,expected_index:int,resulting_index:int,*,final:bool)->dict[str,Any]:
        if (not isinstance(step_id,str) or not step_id.strip() or
                not isinstance(expected_index,int) or isinstance(expected_index,bool) or
                not isinstance(resulting_index,int) or isinstance(resulting_index,bool) or
                expected_index<0 or resulting_index<0 or resulting_index!=expected_index+1):
            raise ProcedureTransitionError("invalid transition")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row=self._connection.execute(
                "SELECT * FROM procedure_sessions WHERE session_id=?",(session_id,)).fetchone()
            if row is None or row["status"]!="active" or row["current_step_index"]!=expected_index:
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
