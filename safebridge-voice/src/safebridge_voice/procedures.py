"""Server-owned ProcedureSession controller and canonical public state."""
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Any

from .procedure_definitions import ProcedureDefinition
from .procedure_store import ProcedureStore, ProcedureTransitionError

KOREAN_COMPLETION_PHRASES=frozenset({
    "현재 단계를 완료했습니다",
    "이 단계를 완료했습니다",
    "현재 단계 완료했습니다",
})


def authorized_completion_step_id(
    transcript:str, language:str, controller:"ProcedureController"|None
)->str|None:
    """Authorize one current step for this turn using exact reviewed utterances."""
    if language!="ko" or controller is None or not isinstance(transcript,str):
        return None
    normalized=re.sub(r"[\s.!?。？！]+$", "", transcript.strip())
    if normalized not in KOREAN_COMPLETION_PHRASES:
        return None
    definition,row=controller._attached()
    if not definition or not row:
        return None
    if row["status"]=="completed" and definition.steps:
        return definition.steps[-1].step_id
    if (row["status"]!="active" or
            row["current_step_index"]>=len(definition.steps)):
        return None
    return definition.steps[row["current_step_index"]].step_id


def unattached_procedure_state() -> dict[str,Any]:
    return {"attached":False}


def public_procedure_state(definition:ProcedureDefinition,row:dict[str,Any])->dict[str,Any]:
    total=len(definition.steps); index=row["current_step_index"]
    current=definition.steps[index] if row["status"]=="active" and index<total else None
    return {
        "attached":True,"procedure_id":definition.procedure_id,"title":definition.title,
        "version":definition.version,"status":row["status"],"total_step_count":total,
        "completed_step_count":min(index,total),
        "current_step_number":current.order if current else None,
        "current_step_id":current.step_id if current else None,
        "current_step_title":current.title if current else None,
        "approved_current_instruction":current.instruction if current else None,
    }


@dataclass
class ProcedureController:
    definitions:dict[str,ProcedureDefinition]
    store:ProcedureStore
    attached_session_id:str|None=None

    def detach(self)->None: self.attached_session_id=None
    def _attached(self):
        if not self.attached_session_id: return None,None
        row=self.store.get_session(self.attached_session_id)
        definition=self.definitions.get(row["procedure_id"]) if row else None
        return definition,row
    def start(self,procedure_id:Any,*,facility_id:str|None=None,
              language:str|None=None,usage_scope:str|None=None)->dict[str,Any]:
        if not isinstance(procedure_id,str) or not procedure_id.strip():
            return {"status":"error","code":"procedure_not_available"}
        wanted=self.definitions.get(procedure_id.strip())
        if wanted is None: return {"status":"error","code":"procedure_not_available"}
        if (wanted.facility_id!=facility_id or wanted.language!=language or
                wanted.usage_scope!=usage_scope):
            return {"status":"error","code":"procedure_scope_mismatch"}
        current,row=self._attached()
        if row:
            if row["procedure_id"]==wanted.procedure_id and row["status"]=="active":
                return {"status":"success","operation":"start","idempotent":True,
                        "state":public_procedure_state(wanted,row)}
            return {"status":"error","code":"procedure_conflict"}
        try: row=self.store.create_session(wanted.procedure_id,wanted.version)
        except Exception: return {"status":"error","code":"procedure_store_unavailable"}
        self.attached_session_id=row["session_id"]
        return {"status":"success","operation":"start","idempotent":False,
                "state":public_procedure_state(wanted,row)}
    def current(self)->dict[str,Any]:
        definition,row=self._attached()
        if not definition or not row: return {"status":"error","code":"no_active_procedure"}
        return {"status":"success","operation":"read",
                "state":public_procedure_state(definition,row)}
    def complete(self,expected_step_id:Any)->dict[str,Any]:
        definition,row=self._attached()
        if not definition or not row: return {"status":"error","code":"no_active_procedure"}
        if not isinstance(expected_step_id,str) or not expected_step_id.strip():
            return {"status":"error","code":"step_mismatch"}
        expected_step_id=expected_step_id.strip(); index=row["current_step_index"]
        completed_ids={step.step_id for step in definition.steps[:min(index,len(definition.steps))]}
        if row["status"]=="completed":
            if definition.steps and expected_step_id==definition.steps[-1].step_id:
                return {"status":"already_completed","operation":"complete","idempotent":True,
                        "completed_step_id":expected_step_id,
                        "state":public_procedure_state(definition,row)}
            return {"status":"error","code":"stale_step" if expected_step_id in completed_ids
                    else "procedure_already_completed"}
        current=definition.steps[index]
        if expected_step_id!=current.step_id:
            if index>0 and expected_step_id==definition.steps[index-1].step_id:
                return {"status":"already_completed","operation":"complete","idempotent":True,
                        "completed_step_id":expected_step_id,
                        "state":public_procedure_state(definition,row)}
            return {"status":"error","code":"stale_step" if expected_step_id in completed_ids else "step_mismatch"}
        final=index==len(definition.steps)-1
        try:
            updated=self.store.complete_step(row["session_id"],current.step_id,index,index+1,final=final)
        except ProcedureTransitionError:
            return {"status":"error","code":"step_mismatch"}
        except Exception:
            return {"status":"error","code":"procedure_store_unavailable"}
        return {"status":"success","operation":"complete","idempotent":False,
                "completed_step_id":current.step_id,"completed":final,
                "state":public_procedure_state(definition,updated)}
