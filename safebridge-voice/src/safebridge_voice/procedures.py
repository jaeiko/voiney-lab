"""Server-owned ProcedureSession controller and canonical workflow state."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import time
from typing import Any, Callable

from .procedure_definitions import ProcedureDefinition, ProcedureStep
from .procedure_store import ProcedureStore, ProcedureTransitionError

KOREAN_COMPLETION_PHRASES=frozenset({
    "현재 단계를 완료했습니다",
    "이 단계를 완료했습니다",
    "현재 단계 완료했습니다",
    "현재 단계를 완료했어요",
    "이 단계를 완료했어요",
    "현재 단계 완료했어요",
    "현재 단계를 완료했어",
    "이 단계를 완료했어",
    "현재 단계 완료했어",
    "현재 단계 완료",
})
KOREAN_COMPLETION_INSTRUCTION="현재 단계를 완료했습니다"
KOREAN_TIMER_START_PHRASES=frozenset({
    "고정 타이머를 시작해 줘",
    "고정 타이머를 시작해줘",
    "고정 타이머를 시작해 주세요",
    "고정 타이머 시작해 줘",
    "고정 타이머 시작해줘",
    "고정 타이머 시작해 주세요",
    "타이머를 시작해 줘",
    "타이머를 시작해줘",
    "타이머를 시작해 주세요",
    "현재 단계 타이머를 시작해 줘",
    "현재 단계 타이머를 시작해줘",
    "현재 단계 타이머를 시작해 주세요",
})
REPORT_ID_PATTERN=re.compile(r"^SR-[0-9]{8}-[0-9A-F]{6}$")


def _normalized_korean_command(transcript:str)->str:
    return re.sub(r"[\s.!?。？！]+$", "", transcript.strip())


def authorized_completion_step_id(
    transcript:str, language:str, controller:"ProcedureController"|None
)->str|None:
    """Authorize one current step for this turn using exact reviewed utterances."""
    if language!="ko" or controller is None or not isinstance(transcript,str):
        return None
    normalized=_normalized_korean_command(transcript)
    if normalized not in KOREAN_COMPLETION_PHRASES:
        return None
    return _active_step_id(controller,allow_completed_replay=True)


def _active_step_id(
    controller:"ProcedureController",*,allow_completed_replay:bool=False
)->str|None:
    definition,row=controller._attached()
    if not definition or not row:
        return None
    if allow_completed_replay and row["status"]=="completed" and definition.steps:
        return definition.steps[-1].step_id
    if (row["status"]!="active" or
            row["current_step_index"]>=len(definition.steps)):
        return None
    return definition.steps[row["current_step_index"]].step_id


def authorized_timer_start_step_id(
    transcript:str,language:str,controller:"ProcedureController"|None
)->str|None:
    """Bind an exact Korean timer-start command to the latest current step."""
    if language!="ko" or controller is None or not isinstance(transcript,str):
        return None
    if _normalized_korean_command(transcript) not in KOREAN_TIMER_START_PHRASES:
        return None
    return _active_step_id(controller)


def korean_timer_status_question(transcript:str,language:str)->bool:
    """Recognize a bounded Korean timer-state question, never a completion."""
    if language!="ko" or not isinstance(transcript,str):
        return False
    text=transcript.strip()
    normalized=_normalized_korean_command(text)
    if (normalized in KOREAN_COMPLETION_PHRASES or
            normalized in KOREAN_TIMER_START_PHRASES):
        return False
    mentions_timer="타이머" in text or "초" in text
    asks_status=any(token in text for token in (
        "왜","얼마","남았","끝났","끝나","0초","영 초","안 끝","진행 중","지났",
    ))
    return mentions_timer and asks_status


def deterministic_procedure_text(result:dict[str,Any],language:str)->str:
    """Return server-owned speech for deterministic completion/status routes."""
    state=result.get("state")
    code=result.get("code")
    if code=="timer_not_elapsed":
        remaining=result.get("remaining_seconds")
        return {
            "ko":(
                f"타이머가 아직 끝나지 않았습니다. 약 {remaining}초 남았습니다. "
                f"타이머가 0초가 된 뒤 “{KOREAN_COMPLETION_INSTRUCTION}”라고 다시 말해 주세요."
            ),
            "en":f"The timer has not finished. About {remaining} seconds remain.",
            "vi":f"Bộ hẹn giờ chưa kết thúc. Còn khoảng {remaining} giây.",
        }[language]
    if code=="timer_not_started":
        return {
            "ko":"현재 단계의 고정 타이머를 먼저 시작해 주세요.",
            "en":"Start the fixed timer for the current step first.",
            "vi":"Trước tiên, hãy bắt đầu bộ hẹn giờ cố định cho bước hiện tại.",
        }[language]
    if code=="timer_not_configured":
        return {
            "ko":"현재 단계에는 시작할 고정 타이머가 없습니다. 화면의 현재 단계 안내를 확인해 주세요.",
            "en":"The current step has no fixed timer to start.",
            "vi":"Bước hiện tại không có bộ hẹn giờ cố định để bắt đầu.",
        }[language]
    if code=="observation_required":
        return {
            "ko":"현재 단계의 필수 관찰값을 먼저 말해 주세요. 관찰값을 기록한 뒤 단계를 완료할 수 있습니다.",
            "en":"State the required observation for this step first. The step can finish after it is recorded.",
            "vi":"Trước tiên, hãy nêu giá trị quan sát bắt buộc. Bước này có thể hoàn thành sau khi giá trị được ghi lại.",
        }[language]
    if code=="procedure_blocked_for_handoff":
        return {
            "ko":"현재 워크플로는 관리자 인계를 위해 차단되어 다음 단계로 진행할 수 없습니다.",
            "en":"The workflow is blocked for manager handoff and cannot advance.",
            "vi":"Quy trình bị chặn để bàn giao cho quản lý và không thể tiếp tục.",
        }[language]
    if code in {
        "no_active_procedure","procedure_not_available",
        "procedure_already_completed","procedure_store_unavailable",
    }:
        return {
            "ko":"현재 워크플로 상태에서는 단계를 완료할 수 없습니다. 화면의 절차 상태를 확인해 주세요.",
            "en":"The step cannot be completed in the current workflow state. Check the procedure state on screen.",
            "vi":"Không thể hoàn thành bước trong trạng thái quy trình hiện tại. Hãy kiểm tra trạng thái trên màn hình.",
        }[language]
    if code:
        return {
            "ko":"현재 단계의 완료 조건과 맞지 않아 실행하지 않았습니다. 화면의 현재 단계 안내를 확인해 주세요.",
            "en":"The request did not meet the current step's completion conditions. Check the current step on screen.",
            "vi":"Yêu cầu chưa đáp ứng điều kiện hoàn thành bước hiện tại. Hãy kiểm tra bước trên màn hình.",
        }[language]
    if result.get("operation")=="complete" and not result.get("idempotent"):
        if result.get("completed"):
            return {
                "ko":"현재 단계를 완료했고 전체 절차가 완료되었습니다.",
                "en":"The current step and the procedure are complete.",
                "vi":"Bước hiện tại và toàn bộ quy trình đã hoàn thành.",
            }[language]
        return {
            "ko":"현재 단계를 완료하고 다음 단계로 이동했습니다.",
            "en":"The current step is complete and the workflow moved to the next step.",
            "vi":"Bước hiện tại đã hoàn thành và quy trình đã chuyển sang bước tiếp theo.",
        }[language]
    if result.get("operation")=="start_timer":
        timer=result.get("timer")
        state_timer=state.get("timer") if isinstance(state,dict) else None
        duration=(
            timer.get("duration_seconds") if isinstance(timer,dict) else None
        )
        remaining=(
            state_timer.get("remaining_seconds")
            if isinstance(state_timer,dict) else None
        )
        if result.get("idempotent"):
            return {
                "ko":f"고정 타이머가 이미 실행 중입니다. 약 {remaining}초 남았습니다.",
                "en":f"The fixed timer is already running with about {remaining} seconds left.",
                "vi":f"Bộ hẹn giờ cố định đang chạy và còn khoảng {remaining} giây.",
            }[language]
        return {
            "ko":f"현재 단계의 고정 {duration}초 타이머를 시작했습니다.",
            "en":f"Started the current step's fixed {duration}-second timer.",
            "vi":f"Đã bắt đầu bộ hẹn giờ cố định {duration} giây cho bước hiện tại.",
        }[language]
    if (result.get("operation")=="complete" and result.get("idempotent") or
            isinstance(state,dict) and state.get("status")=="completed"):
        return {
            "ko":"전체 절차가 이미 완료되어 있습니다.",
            "en":"The procedure is already complete.",
            "vi":"Quy trình đã hoàn thành.",
        }[language]
    if isinstance(state,dict):
        if state.get("status")=="blocked_for_handoff":
            return {
                "ko":"현재 워크플로는 관리자 인계를 위해 차단되어 다음 단계로 진행할 수 없습니다.",
                "en":"The workflow is blocked for manager handoff and cannot advance.",
                "vi":"Quy trình bị chặn để bàn giao cho quản lý và không thể tiếp tục.",
            }[language]
        timer=state.get("timer")
        if isinstance(timer,dict):
            remaining=timer.get("remaining_seconds")
            if timer.get("state")=="elapsed":
                return {
                    "ko":f"타이머가 0초입니다. 단계를 완료하려면 “{KOREAN_COMPLETION_INSTRUCTION}”라고 말해 주세요.",
                    "en":"The timer has elapsed. Say that the current step is complete to finish it.",
                    "vi":"Bộ hẹn giờ đã kết thúc. Hãy nói rằng bước hiện tại đã hoàn thành để hoàn tất.",
                }[language]
            if timer.get("state")=="running":
                return {
                    "ko":f"현재 타이머에 약 {remaining}초 남았습니다.",
                    "en":f"About {remaining} seconds remain on the timer.",
                    "vi":f"Bộ hẹn giờ còn khoảng {remaining} giây.",
                }[language]
            return {
                "ko":"현재 단계의 타이머는 아직 시작되지 않았습니다.",
                "en":"The timer for the current step has not started.",
                "vi":"Bộ hẹn giờ của bước hiện tại chưa bắt đầu.",
            }[language]
        return {
            "ko":"현재 단계에는 고정 타이머가 없습니다.",
            "en":"The current step has no fixed timer.",
            "vi":"Bước hiện tại không có bộ hẹn giờ cố định.",
        }[language]
    return {
        "ko":"현재 워크플로 상태를 확인할 수 없습니다.",
        "en":"The current workflow state is unavailable.",
        "vi":"Không thể kiểm tra trạng thái quy trình hiện tại.",
    }[language]


def unattached_procedure_state() -> dict[str,Any]:
    return {"attached":False}


def _timer_state(
    timer:dict[str,Any]|None, duration_seconds:int|None, now_epoch:float
)->dict[str,Any]|None:
    if duration_seconds is None:
        return None
    if timer is None:
        return {
            "state":"not_started","duration_seconds":duration_seconds,
            "started_at":None,"deadline_at":None,
            "remaining_seconds":duration_seconds,
        }
    remaining=max(0,math.ceil(float(timer["deadline_epoch"])-now_epoch))
    return {
        "state":"elapsed" if remaining==0 else "running",
        "duration_seconds":int(timer["duration_seconds"]),
        "started_at":timer["started_at"],"deadline_at":timer["deadline_at"],
        "remaining_seconds":remaining,
    }


def _step_observation_state(
    step:ProcedureStep|None, observations:list[dict[str,Any]]
)->dict[str,Any]|None:
    if step is None or step.observation_schema is None:
        return None
    schema=step.observation_schema
    latest=observations[-1] if observations else None
    return {
        "type":schema["type"],"required":schema["required"],
        "label":schema.get("label") or "관찰 내용","unit":schema.get("unit"),
        "recorded_count":len(observations),
        "latest_value":latest.get("value") if latest else None,
        "latest_recorded_at":latest.get("recorded_at") if latest else None,
    }


def public_procedure_state(
    definition:ProcedureDefinition,
    row:dict[str,Any],
    store:ProcedureStore|None=None,
    *,
    now_epoch:float|None=None,
)->dict[str,Any]:
    now_epoch=time.time() if now_epoch is None else now_epoch
    total=len(definition.steps)
    index=row["current_step_index"]
    handoff=store.get_handoff(row["session_id"]) if store else None
    effective_status=(
        "blocked_for_handoff"
        if handoff is not None and row["status"]=="active"
        else row["status"]
    )
    current=(
        definition.steps[index]
        if effective_status in ("active","blocked_for_handoff") and index<total
        else None
    )
    current_observations=(
        store.list_observations(row["session_id"],current.step_id)
        if store and current else []
    )
    all_observations=store.list_observations(row["session_id"]) if store else []
    all_timers=store.list_timers(row["session_id"]) if store else []
    step_events=store.list_events(row["session_id"]) if store else []
    timer_duration=(
        int(current.timer["duration_seconds"])
        if current and current.timer else None
    )
    timer=(
        store.get_timer(row["session_id"],current.step_id)
        if store and current and timer_duration is not None else None
    )
    source=current.source if current else definition.document_source
    return {
        "attached":True,
        "procedure_id":definition.procedure_id,
        "title":definition.title,
        "version":definition.version,
        "status":effective_status,
        "total_step_count":total,
        "completed_step_count":min(index,total),
        "current_step_number":current.order if current else None,
        "current_step_id":current.step_id if current else None,
        "current_step_title":current.title if current else None,
        "approved_current_instruction":current.instruction if current else None,
        "source":{
            "document_id":definition.document_id,
            "document_version":definition.document_version,
            "section_reference":source.section_reference,
            "page_start":source.page_start,
            "page_end":source.page_end,
            "usage_scope":definition.usage_scope,
        },
        "timer":_timer_state(timer,timer_duration,now_epoch),
        "observation":_step_observation_state(current,current_observations),
        "handoff":({
            "report_id":handoff["report_id"],
            "blocked_step_id":handoff["step_id"],
            "reason":handoff["reason"],
            "blocked_at":handoff["blocked_at"],
        } if handoff else None),
        "audit":{
            "started_at":row["started_at"],
            "updated_at":row["updated_at"],
            "completed_step_count":min(index,total),
            "observation_count":len(all_observations),
            "timer_count":len(all_timers),
            "event_count":len(step_events)+len(all_observations)+len(all_timers)+(1 if handoff else 0),
        },
    }


def _normalize_observation(value:Any,schema:dict[str,Any])->tuple[Any|None,str|None]:
    kind=schema["type"]
    if kind=="text":
        if not isinstance(value,str) or not value.strip():
            return None,"observation_value_invalid"
        cleaned=" ".join(value.split())
        if len(cleaned)>500:
            return None,"observation_value_invalid"
        return cleaned,None
    if kind=="number":
        if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):
            return None,"observation_value_invalid"
        return value,None
    if kind=="boolean":
        if not isinstance(value,bool):
            return None,"observation_value_invalid"
        return value,None
    return None,"observation_value_invalid"


@dataclass
class ProcedureController:
    definitions:dict[str,ProcedureDefinition]
    store:ProcedureStore
    attached_session_id:str|None=None
    clock:Callable[[],float]=time.time

    def detach(self)->None:
        self.attached_session_id=None

    def _attached(self):
        if not self.attached_session_id:
            return None,None
        row=self.store.get_session(self.attached_session_id)
        definition=self.definitions.get(row["procedure_id"]) if row else None
        return definition,row

    def _public(self,definition:ProcedureDefinition,row:dict[str,Any])->dict[str,Any]:
        return public_procedure_state(
            definition,row,self.store,now_epoch=self.clock())

    def start(
        self,procedure_id:Any,*,facility_id:str|None=None,
        language:str|None=None,usage_scope:str|None=None,
    )->dict[str,Any]:
        if not isinstance(procedure_id,str) or not procedure_id.strip():
            return {"status":"error","code":"procedure_not_available"}
        wanted=self.definitions.get(procedure_id.strip())
        if wanted is None:
            return {"status":"error","code":"procedure_not_available"}
        if (wanted.facility_id!=facility_id or wanted.language!=language or
                wanted.usage_scope!=usage_scope):
            return {"status":"error","code":"procedure_scope_mismatch"}
        _,row=self._attached()
        if row:
            if row["procedure_id"]==wanted.procedure_id and row["status"]=="active":
                return {
                    "status":"success","operation":"start","idempotent":True,
                    "state":self._public(wanted,row),
                }
            return {"status":"error","code":"procedure_conflict"}
        try:
            row=self.store.create_session(wanted.procedure_id,wanted.version)
        except Exception:
            return {"status":"error","code":"procedure_store_unavailable"}
        self.attached_session_id=row["session_id"]
        return {
            "status":"success","operation":"start","idempotent":False,
            "state":self._public(wanted,row),
        }

    def current(self)->dict[str,Any]:
        definition,row=self._attached()
        if not definition or not row:
            return {"status":"error","code":"no_active_procedure"}
        return {
            "status":"success","operation":"read",
            "state":self._public(definition,row),
        }

    def record_observation(self,expected_step_id:Any,value:Any)->dict[str,Any]:
        definition,row=self._attached()
        if not definition or not row:
            return {"status":"error","code":"no_active_procedure"}
        state=self._public(definition,row)
        if state["status"]=="blocked_for_handoff":
            return {"status":"error","code":"procedure_blocked_for_handoff","state":state}
        if row["status"]!="active" or row["current_step_index"]>=len(definition.steps):
            return {"status":"error","code":"procedure_already_completed","state":state}
        current=definition.steps[row["current_step_index"]]
        if expected_step_id!=current.step_id:
            return {"status":"error","code":"step_mismatch","state":state}
        if current.observation_schema is None:
            return {"status":"error","code":"observation_not_allowed","state":state}
        normalized,error=_normalize_observation(value,current.observation_schema)
        if error:
            return {"status":"error","code":error,"state":state}
        try:
            observation=self.store.record_observation(
                row["session_id"],current.step_id,normalized)
        except ProcedureTransitionError:
            return {"status":"error","code":"observation_conflict","state":self._public(definition,row)}
        except Exception:
            return {"status":"error","code":"procedure_store_unavailable","state":state}
        updated=self.store.get_session(row["session_id"]) or row
        return {
            "status":"success","operation":"record_observation","idempotent":False,
            "recorded_step_id":current.step_id,"observation":observation,
            "state":self._public(definition,updated),
        }

    def start_timer(self,expected_step_id:Any)->dict[str,Any]:
        definition,row=self._attached()
        if not definition or not row:
            return {"status":"error","code":"no_active_procedure"}
        state=self._public(definition,row)
        if state["status"]=="blocked_for_handoff":
            return {"status":"error","code":"procedure_blocked_for_handoff","state":state}
        if row["status"]!="active" or row["current_step_index"]>=len(definition.steps):
            return {"status":"error","code":"procedure_already_completed","state":state}
        current=definition.steps[row["current_step_index"]]
        if expected_step_id!=current.step_id:
            return {"status":"error","code":"step_mismatch","state":state}
        if current.timer is None:
            return {"status":"error","code":"timer_not_configured","state":state}
        try:
            timer,idempotent=self.store.start_timer(
                row["session_id"],current.step_id,
                int(current.timer["duration_seconds"]),now_epoch=self.clock())
        except ProcedureTransitionError:
            return {"status":"error","code":"timer_conflict","state":self._public(definition,row)}
        except Exception:
            return {"status":"error","code":"procedure_store_unavailable","state":state}
        updated=self.store.get_session(row["session_id"]) or row
        return {
            "status":"success","operation":"start_timer","idempotent":idempotent,
            "timer_step_id":current.step_id,
            "timer":{
                "duration_seconds":timer["duration_seconds"],
                "started_at":timer["started_at"],"deadline_at":timer["deadline_at"],
            },
            "state":self._public(definition,updated),
        }

    def report_context(self)->dict[str,Any]|None:
        definition,row=self._attached()
        if not definition or not row or row["status"]!="active":
            return None
        state=self._public(definition,row)
        if state["status"]=="blocked_for_handoff":
            return None
        observation=state.get("observation")
        return {
            "workflow_session_id":row["session_id"],
            "procedure_id":definition.procedure_id,
            "procedure_title":definition.title,
            "procedure_version":definition.version,
            "step_id":state["current_step_id"],
            "step_number":state["current_step_number"],
            "step_title":state["current_step_title"],
            "completed_step_count":state["completed_step_count"],
            "source":state["source"],
            "timer":state.get("timer"),
            "latest_observation":(
                {
                    "label":observation.get("label"),
                    "value":observation.get("latest_value"),
                    "recorded_at":observation.get("latest_recorded_at"),
                }
                if isinstance(observation,dict) and observation.get("recorded_count",0)>0
                else None
            ),
        }

    def block_for_handoff(self,report_id:Any,reason:Any)->dict[str,Any]:
        definition,row=self._attached()
        if not definition or not row:
            return {"status":"success","operation":"block_for_handoff",
                    "idempotent":True,"state":unattached_procedure_state()}
        if (not isinstance(report_id,str) or
                REPORT_ID_PATTERN.fullmatch(report_id.strip().upper()) is None or
                not isinstance(reason,str) or not reason.strip()):
            return {"status":"error","code":"procedure_block_failed",
                    "state":self._public(definition,row)}
        if row["status"]!="active" or row["current_step_index"]>=len(definition.steps):
            return {"status":"error","code":"procedure_already_completed",
                    "state":self._public(definition,row)}
        current=definition.steps[row["current_step_index"]]
        try:
            handoff,idempotent=self.store.block_for_handoff(
                row["session_id"],current.step_id,report_id.strip().upper(),reason)
        except ProcedureTransitionError:
            return {"status":"error","code":"procedure_block_failed",
                    "state":self._public(definition,row)}
        except Exception:
            return {"status":"error","code":"procedure_store_unavailable",
                    "state":self._public(definition,row)}
        return {
            "status":"success","operation":"block_for_handoff",
            "idempotent":idempotent,"report_id":handoff["report_id"],
            "blocked_step_id":handoff["step_id"],
            "state":self._public(definition,row),
        }

    def summary(self)->dict[str,Any]:
        definition,row=self._attached()
        if not definition or not row:
            return {"status":"error","code":"no_active_procedure"}
        observations=self.store.list_observations(row["session_id"])
        timers=self.store.list_timers(row["session_id"])
        completed=min(row["current_step_index"],len(definition.steps))
        return {
            "status":"success","operation":"summary",
            "state":self._public(definition,row),
            "audit_summary":{
                "completed_steps":[
                    {"step_id":step.step_id,"step_number":step.order,"title":step.title}
                    for step in definition.steps[:completed]
                ],
                "observations":[
                    {
                        "step_id":item["step_id"],"value":item["value"],
                        "recorded_at":item["recorded_at"],
                    }
                    for item in observations
                ],
                "timers":[
                    {
                        "step_id":item["step_id"],
                        "duration_seconds":item["duration_seconds"],
                        "started_at":item["started_at"],
                        "deadline_at":item["deadline_at"],
                    }
                    for item in timers
                ],
                "handoff":self.store.get_handoff(row["session_id"]),
            },
        }

    def complete(self,expected_step_id:Any)->dict[str,Any]:
        definition,row=self._attached()
        if not definition or not row:
            return {"status":"error","code":"no_active_procedure"}
        state=self._public(definition,row)
        if not isinstance(expected_step_id,str) or not expected_step_id.strip():
            return {"status":"error","code":"step_mismatch","state":state}
        expected_step_id=expected_step_id.strip()
        index=row["current_step_index"]
        completed_ids={
            step.step_id for step in definition.steps[:min(index,len(definition.steps))]
        }
        if row["status"]=="completed":
            if definition.steps and expected_step_id==definition.steps[-1].step_id:
                return {
                    "status":"already_completed","operation":"complete",
                    "idempotent":True,"completed_step_id":expected_step_id,
                    "state":state,
                }
            return {
                "status":"error",
                "code":"stale_step" if expected_step_id in completed_ids
                else "procedure_already_completed",
                "state":state,
            }
        current=definition.steps[index]
        if expected_step_id!=current.step_id:
            if index>0 and expected_step_id==definition.steps[index-1].step_id:
                return {
                    "status":"already_completed","operation":"complete",
                    "idempotent":True,"completed_step_id":expected_step_id,
                    "state":state,
                }
            return {
                "status":"error",
                "code":"stale_step" if expected_step_id in completed_ids else "step_mismatch",
                "state":state,
            }
        if state["status"]=="blocked_for_handoff":
            return {
                "status":"error","code":"procedure_blocked_for_handoff","state":state,
            }
        if (current.observation_schema is not None and
                current.observation_schema.get("required") and
                not self.store.list_observations(row["session_id"],current.step_id)):
            return {"status":"error","code":"observation_required","state":state}
        if current.timer is not None:
            timer=state.get("timer")
            if not isinstance(timer,dict) or timer.get("state")=="not_started":
                return {"status":"error","code":"timer_not_started","state":state}
            if timer.get("state")!="elapsed":
                return {
                    "status":"error","code":"timer_not_elapsed",
                    "remaining_seconds":timer.get("remaining_seconds"),"state":state,
                }
        final=index==len(definition.steps)-1
        try:
            updated=self.store.complete_step(
                row["session_id"],current.step_id,index,index+1,final=final)
        except ProcedureTransitionError:
            return {"status":"error","code":"step_mismatch","state":self._public(definition,row)}
        except Exception:
            return {"status":"error","code":"procedure_store_unavailable","state":state}
        return {
            "status":"success","operation":"complete","idempotent":False,
            "completed_step_id":current.step_id,"completed":final,
            "state":self._public(definition,updated),
        }
