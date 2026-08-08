"""Voice Workflow Agent: hands-free voice cascade with M2 Dispatcher tools."""
from __future__ import annotations
import asyncio, logging, math, os, sqlite3, time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from voice_workflow_agent.audio import FRAME_BYTES, FrameBuffer, clean_path, pcm_to_wav
from voice_workflow_agent.brain import (
    REPORT_CONFIRMATION_CLARIFICATION_TEXT,
    ConversationHistory,
    SentenceSegment,
    confirmation_intent,
    stream_brain_turn,
)
from voice_workflow_agent.configuration import (
    ConfigurationError,
    VoiceVadSettings,
)
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolFixture,
    CuratedProtocolSession,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.document_store import CATALOG_SCHEMA_VERSION
from voice_workflow_agent.emergency import recognize_emergency
from voice_workflow_agent.language import (
    CLARIFICATION_TEXT, Transcription, normalize_provider_language, resolve_turn_language,
)
from voice_workflow_agent.moss_retrieval import (
    start_moss_runtime_from_environment,
    stop_moss_runtime,
)
from voice_workflow_agent.native_realtime import (
    NATIVE_SAMPLE_RATE,
    NativeRealtimeConfig,
    NativeRealtimeError,
    NativeRealtimeSession,
)
from voice_workflow_agent.tools import (
    COMPLETE_CURRENT_STEP_TOOL_NAME,
    CREATE_REPORT_TOOL_NAME,
    GET_CURRENT_STEP_TOOL_NAME,
    PROCEDURE_TOOL_NAMES,
    RECORD_STEP_OBSERVATION_TOOL_NAME,
    START_STEP_TIMER_TOOL_NAME,
    ToolContext,
    check_safety_report_status,
    execute_tool,
)
from voice_workflow_agent.procedure_definitions import load_procedure_definitions
from voice_workflow_agent.procedure_store import ProcedureStore
from voice_workflow_agent.procedures import (
    ProcedureController, authorized_completion_step_id,
    authorized_observation_arguments,
    authorized_timer_start_step_id,
    deterministic_procedure_text, korean_timer_status_question,
    unattached_procedure_state,
)
from voice_workflow_agent.protocol import ProtocolError, audio_segment_start, event, parse_control
from voice_workflow_agent.vad import EndpointDetector, EndpointResult, TurnState, VadConfig

PROJECT_ROOT=Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("voice_workflow_agent")


def log_effective_vad_configuration(settings:VoiceVadSettings)->None:
    """Log non-secret endpoint settings once when the application starts."""
    cascade=settings.cascade
    native=settings.native
    log.info(
        "vad.configuration "
        "cascade_mode=%d cascade_processing_onset_voiced_frames=%d "
        "cascade_processing_onset_window_frames=%d "
        "cascade_listening_onset_voiced_frames=%d "
        "cascade_listening_onset_window_frames=%d "
        "cascade_listening_resume_voiced_frames=%d "
        "cascade_listening_resume_window_frames=%d cascade_prefix_ms=%d "
        "cascade_endpoint_silence_ms=%d cascade_min_speech_ms=%d "
        "cascade_max_utterance_ms=%d cascade_cooldown_ms=%d "
        "cascade_playback_onset_voiced_frames=%d "
        "cascade_playback_onset_window_frames=%d "
        "native_threshold=%s native_prefix_padding_ms=%d "
        "native_silence_duration_ms=%d",
        cascade.mode,cascade.onset_voiced_frames,cascade.onset_window_frames,
        cascade.listening_onset_voiced_frames,
        cascade.listening_onset_window_frames,
        cascade.listening_resume_voiced_frames,
        cascade.listening_resume_window_frames,
        cascade.prefix_ms,cascade.endpoint_silence_ms,
        cascade.minimum_speech_ms,cascade.maximum_utterance_ms,
        cascade.cooldown_ms,cascade.playback_onset_voiced_frames,
        cascade.playback_onset_window_frames,native.threshold,native.prefix_padding_ms,
        native.silence_duration_ms,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Warm optional in-memory retrieval without making it a startup dependency."""
    log_effective_vad_configuration(VoiceVadSettings.from_environment())
    await asyncio.to_thread(start_moss_runtime_from_environment)
    try:
        yield
    finally:
        await asyncio.to_thread(stop_moss_runtime)


app=FastAPI(title="Voice Workflow Agent",lifespan=lifespan)
STATIC_DIR=Path(__file__).with_name("static")

def normalize_session_language(value:str)->str:
    normalized=value.strip().casefold().replace("_","-")
    if normalized in ("ko","ko-kr"): return "ko"
    if normalized in ("en","en-us","en-gb"): return "en"
    if normalized in ("vi","vi-vn"): return "vi"
    raise ValueError("unsupported session language")

@dataclass(frozen=True)
class ServerConfig:
    catalog_path:Path
    facility_id:str|None
    usage_scope:str
    allowed_languages:frozenset[str]
    default_language:str
    procedure_catalog_path:Path|None=None
    procedure_store_path:Path|None=None
    curated_protocol_fixture_path:Path|None=None
    curated_protocol_provenance_path:Path|None=None
    curated_protocol_source_pdf_path:Path|None=None

class ServerConfigurationError(RuntimeError):
    """Invalid server policy with safe environment field names for diagnostics."""
    def __init__(self,message:str,*field_names:str):
        super().__init__(message)
        self.field_names=field_names

def validate_approved_catalog(catalog_path:Path,usage_scope:str)->None:
    """Fail closed unless the configured SQLite catalog is usable for its scope."""
    rendered=repr(str(catalog_path))
    if not catalog_path.is_file():
        raise ServerConfigurationError(
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG must point to an existing "
            f"regular file: {rendered}",
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG",
        )
    try:
        connection=sqlite3.connect(
            f"{catalog_path.resolve().as_uri()}?mode=ro",uri=True)
        try:
            metadata=connection.execute(
                "SELECT schema_version FROM catalog_metadata").fetchall()
            if (len(metadata)!=1 or
                    metadata[0][0]!=CATALOG_SCHEMA_VERSION):
                raise sqlite3.DatabaseError("unsupported catalog schema")
            approved=connection.execute(
                """
                SELECT COUNT(*) FROM documents
                WHERE usage_scope=? AND approval_status='approved' AND active=1
                """,
                (usage_scope,),
            ).fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ServerConfigurationError(
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG is not a usable approved "
            f"catalog: {rendered}",
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG",
        ) from exc
    if approved<1:
        raise ServerConfigurationError(
            "VOICE_WORKFLOW_AGENT_USAGE_SCOPE has no approved active documents "
            "in VOICE_WORKFLOW_AGENT_SAFETY_CATALOG: "
            f"{rendered}",
            "VOICE_WORKFLOW_AGENT_USAGE_SCOPE",
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG",
        )

def server_config()->ServerConfig:
    """Load server-wide policy without exposing configuration values."""
    catalog=os.environ.get("VOICE_WORKFLOW_AGENT_SAFETY_CATALOG","").strip()
    scope=os.environ.get("VOICE_WORKFLOW_AGENT_USAGE_SCOPE","").strip()
    catalog_path=Path(catalog)
    if not catalog:
        raise ServerConfigurationError(
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG must not be empty",
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG",
        )
    if not catalog_path.is_absolute():
        raise ServerConfigurationError(
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG must be an absolute path: "
            f"{catalog!r}",
            "VOICE_WORKFLOW_AGENT_SAFETY_CATALOG",
        )
    invalid_policy_fields=[]
    if scope not in ("operational","demo","reference_only","test_only"):
        invalid_policy_fields.append("VOICE_WORKFLOW_AGENT_USAGE_SCOPE")
    if invalid_policy_fields:
        raise ServerConfigurationError(
            "safety catalog configuration is incomplete",
            *invalid_policy_fields,
        )
    validate_approved_catalog(catalog_path,scope)
    facility=os.environ.get("VOICE_WORKFLOW_AGENT_FACILITY_ID","").strip() or None
    raw_allowed=os.environ.get("VOICE_WORKFLOW_AGENT_ALLOWED_LANGUAGES","ko,en,vi")
    try:
        allowed=frozenset(normalize_session_language(value) for value in raw_allowed.split(",") if value.strip())
        default=normalize_session_language(os.environ.get("VOICE_WORKFLOW_AGENT_SESSION_LANGUAGE","ko"))
    except ValueError as exc:
        raise ServerConfigurationError(
            "session language configuration is invalid",
            "VOICE_WORKFLOW_AGENT_ALLOWED_LANGUAGES",
            "VOICE_WORKFLOW_AGENT_SESSION_LANGUAGE",
        ) from exc
    if not allowed or default not in allowed:
        raise ServerConfigurationError(
            "session language configuration is invalid",
            "VOICE_WORKFLOW_AGENT_ALLOWED_LANGUAGES",
            "VOICE_WORKFLOW_AGENT_SESSION_LANGUAGE",
        )
    procedure_catalog=os.environ.get("VOICE_WORKFLOW_AGENT_PROCEDURE_CATALOG","").strip()
    procedure_store=os.environ.get("VOICE_WORKFLOW_AGENT_PROCEDURE_STORE","").strip()
    procedure_catalog_path=Path(procedure_catalog) if procedure_catalog else None
    procedure_store_path=Path(procedure_store) if procedure_store else None
    if ((procedure_catalog_path is None)!=(procedure_store_path is None) or
        procedure_catalog_path is not None and
        (not procedure_catalog_path.is_absolute() or not procedure_store_path.is_absolute())):
        raise ServerConfigurationError(
            "procedure configuration is invalid",
            "VOICE_WORKFLOW_AGENT_PROCEDURE_CATALOG",
            "VOICE_WORKFLOW_AGENT_PROCEDURE_STORE",
        )
    curated_fixture=os.environ.get(
        "VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE","").strip()
    curated_provenance=os.environ.get(
        "VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_PROVENANCE","").strip()
    curated_source_pdf=os.environ.get(
        "VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_SOURCE_PDF","").strip()
    curated_values=(curated_fixture,curated_provenance,curated_source_pdf)
    curated_paths=tuple(Path(value) if value else None for value in curated_values)
    if any(curated_paths) and (
        not all(curated_paths)
        or any(path is not None and not path.is_absolute() for path in curated_paths)
    ):
        raise ServerConfigurationError(
            "curated development protocol configuration is invalid",
            "VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE",
            "VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_PROVENANCE",
            "VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_SOURCE_PDF",
        )
    return ServerConfig(catalog_path,facility,scope,allowed,default,
                        procedure_catalog_path,procedure_store_path,
                        curated_paths[0],curated_paths[1],curated_paths[2])

def server_tool_context(
    config:ServerConfig|None=None,
    language:str|None=None,
)->ToolContext:
    """Construct an independent session context from trusted server policy."""
    config=config or server_config()
    selected=config.default_language if language is None else normalize_session_language(language)
    if selected not in config.allowed_languages:
        raise ValueError("unsupported session language")
    return ToolContext(config.catalog_path,config.facility_id,selected,config.usage_scope)

def require_env(name:str)->str:
    value=os.environ.get(name,"").strip()
    if not value: raise RuntimeError(f"{name} is not set")
    return value
def api_url(path:str)->str:
    return os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/") + "/" + path.lstrip("/")

def transcribe(pcm:bytes)->Transcription:
    response=requests.post(api_url("stt"),headers={"Authorization":f"Bearer {require_env("XAI_API_KEY")}"},
        files={"file":("utterance.wav",pcm_to_wav(pcm),"audio/wav")},timeout=120)
    response.raise_for_status()
    payload=response.json()
    text=payload.get("text","")
    return Transcription(text.strip() if isinstance(text,str) else "",
                         normalize_provider_language(payload.get("language")))

def validate_tts_pcm(response:requests.Response)->bytes:
    if not response.ok:
        detail=response.text[:500] if response.content else "empty response"
        raise RuntimeError(f"xAI TTS failed ({response.status_code}): {detail}")
    if "json" in response.headers.get("content-type","").lower():
        raise RuntimeError("xAI TTS returned JSON instead of requested raw PCM")
    if not response.content or len(response.content)%2: raise RuntimeError("xAI TTS returned invalid PCM")
    return response.content

def synthesize(text:str,language:str|None=None)->bytes:
    response=requests.post(api_url("tts"),headers={"Authorization":f"Bearer {require_env("XAI_API_KEY")}"},
        json={"text":text,"voice_id":require_env("TTS_VOICE"),"language":language or "auto",
              "output_format":{"codec":"pcm","sample_rate":16000}},timeout=120)
    return validate_tts_pcm(response)

def frame_complete_audio(pcm:bytes)->list[bytes]:
    buffer=FrameBuffer(); frames=buffer.push(pcm); tail=buffer.finish(pad=True)
    if tail is not None: frames.append(tail)
    return frames

@dataclass(frozen=True)
class ListenerEvent:
    kind:str
    turn_id:int
    result:EndpointResult
    generation:int|None=None
    superseding_turn_id:int|None=None
    superseding_generation:int|None=None

TURN_PROGRESS_TERMINAL_STATES=frozenset({
    "complete","blocked","cancelled","error",
})
TURN_PROGRESS_SAFE_ROUTES=frozenset({
    "approved_information","brain","curated_protocol",
    "deterministic_emergency","deterministic_procedure",
    "language_clarification",
})
TURN_PROGRESS_TRANSITIONS={
    "listening":frozenset({"transcribing","cancelled","error"}),
    "transcribing":frozenset({"routing","cancelled","error"}),
    "routing":frozenset({
        "checking_protocol","checking_approved_information","composing",
        "synthesizing","cancelled","error",
    }),
    "checking_protocol":frozenset({"synthesizing","cancelled","error"}),
    "checking_approved_information":frozenset({
        "composing","synthesizing","cancelled","error",
    }),
    "composing":frozenset({
        "checking_approved_information","synthesizing","cancelled","error",
    }),
    "synthesizing":frozenset({"playing","cancelled","error"}),
    "playing":frozenset({"complete","blocked","cancelled","error"}),
}

@dataclass
class TurnProgress:
    revision:int=0
    state:str|None=None
    route:str|None=None
    terminal_outcome:str="complete"

class ListenerSession:
    def __init__(self,detector:EndpointDetector|None=None,clock:Callable[[],float]=time.perf_counter,
                 tool_context:ToolContext|None=None,
                 curated_protocol_session:CuratedProtocolSession|None=None)->None:
        self.detector=detector or EndpointDetector(listening_onset=True)
        self.clock=clock; self.active=False
        self.framer=FrameBuffer(); self.next_turn_id=1; self.active_turn_id=None
        self.cooldown_until=0.0; self.endpoint_at=0.0; self.history=ConversationHistory(); self.generation=0
        self.tool_context=tool_context
        self.language_mode="manual"
        self.manual_language=tool_context.language if tool_context else None
        self.last_confirmed_language=None
        self.turn_committed_at:dict[int,float]={}
        self.playback_completion_metrics:dict[int,int]={}
        self.curated_protocol_session=curated_protocol_session
        self.accepted_configuration_id:int|None=None
        self.accepted_mode:str|None=None
        self.accepted_language:str|None=None
        self.accepted_protocol_id:str|None=None
        self.turn_generations:dict[int,int]={}
        self.turn_progress:dict[tuple[int,int],TurnProgress]={}
        self._interrupted_generations:set[tuple[int,int]]=set()
        self._cascade_vad_config=self.detector.config
        self._vad_classifier=self.detector.classifier
        self._listening_onset=self.detector.listening_onset
        self._interrupt_detector=self._new_interrupt_detector()
        self._interrupt_framer=FrameBuffer()
    @property
    def state(self): return self.detector.state
    def _new_interrupt_detector(self,*,playback:bool=False)->EndpointDetector:
        config=self._cascade_vad_config
        if playback:
            config=replace(
                config,
                onset_voiced_frames=config.playback_onset_voiced_frames,
                onset_window_frames=config.playback_onset_window_frames,
            )
        return EndpointDetector(
            config,
            classifier=self._vad_classifier,
            listening_onset=False,
        )
    def _restore_primary_detector(self,state:TurnState)->None:
        self.detector=EndpointDetector(
            self._cascade_vad_config,classifier=self._vad_classifier,
            listening_onset=self._listening_onset)
        self.detector.reset(state)
    def _reset_interrupt_input(self,*,playback:bool=False)->None:
        self._interrupt_detector=self._new_interrupt_detector(playback=playback)
        self._interrupt_framer=FrameBuffer()
    def _reset_turn_identity(self)->None:
        self.turn_generations.clear()
        self.turn_progress.clear()
        self._interrupted_generations.clear()
        self._reset_interrupt_input()
    def start(self):
        self.generation+=1
        self.active=True; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self._restore_primary_detector(TurnState.IDLE)
        self.history.reset()
        self.last_confirmed_language=None
        self.turn_committed_at.clear(); self.playback_completion_metrics.clear()
        self._reset_turn_identity()
        if self.curated_protocol_session is not None:
            self.curated_protocol_session.reset()
    def stop(self):
        self.generation+=1
        self.active=False; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self._restore_primary_detector(TurnState.IDLE)
        self.history.reset()
        self.last_confirmed_language=None
        self.turn_committed_at.clear(); self.playback_completion_metrics.clear()
        self.accepted_configuration_id=None; self.accepted_mode=None
        self.accepted_language=None; self.accepted_protocol_id=None
        self._reset_turn_identity()
        if self.curated_protocol_session is not None:
            self.curated_protocol_session.reset()
    def accept_configuration(
        self,configuration_id:int,mode:str,language:str,
        protocol_id:str|None,
    )->None:
        """Record only the exact non-secret configuration accepted by the server."""
        self.accepted_configuration_id=configuration_id
        self.accepted_mode=mode
        self.accepted_language=language
        self.accepted_protocol_id=protocol_id
    def set_curated_protocol_fixture(
        self,fixture:CuratedProtocolFixture|None,
    )->None:
        self.curated_protocol_session=(
            CuratedProtocolSession(fixture) if fixture is not None else None)
        self.reset_sensitive_state()
    def set_tool_context(self,context:ToolContext)->None:
        """Change trusted language and clear all language-sensitive session state."""
        self.generation+=1
        self.tool_context=context
        self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self._restore_primary_detector(TurnState.IDLE)
        self.history.reset()
        self.language_mode="manual"; self.manual_language=context.language
        self.last_confirmed_language=None
        self.turn_committed_at.clear(); self.playback_completion_metrics.clear()
        self._reset_turn_identity()
    def set_language_mode(self,mode:str,context:ToolContext|None=None)->None:
        if mode=="manual":
            if context is None: raise ValueError("manual mode requires context")
            self.set_tool_context(context)
            return
        if mode!="auto": raise ValueError("invalid language mode")
        self.language_mode="auto"; self.manual_language=None
    def reset_sensitive_state(self)->None:
        self.generation+=1; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self._restore_primary_detector(TurnState.IDLE)
        self.history.reset()
        self.last_confirmed_language=None
        self.turn_committed_at.clear(); self.playback_completion_metrics.clear()
        self._reset_turn_identity()
    def is_current(self,turn_id:int,generation:int)->bool:
        return self.active and self.generation==generation and self.active_turn_id==turn_id
    def advance_turn_progress(
        self,turn_id:int,generation:int,state:str,*,route:str|None=None,
        timings_ms:dict[str,int|float]|None=None,
    )->dict|None:
        """Advance one exact Cascade turn through an observable server boundary."""
        if (not isinstance(turn_id,int) or isinstance(turn_id,bool) or turn_id<=0
                or not isinstance(generation,int) or isinstance(generation,bool)
                or generation<0 or state not in TURN_PROGRESS_TRANSITIONS
                and state not in TURN_PROGRESS_TERMINAL_STATES):
            return None
        known_generation=self.turn_generations.get(turn_id)
        if known_generation is not None and known_generation!=generation:
            return None
        if known_generation is None:
            self.turn_generations[turn_id]=generation
        if route is not None and route not in TURN_PROGRESS_SAFE_ROUTES:
            return None
        safe_timings=None
        if timings_ms is not None:
            if (not isinstance(timings_ms,dict) or any(
                    not isinstance(name,str) or not name or
                    not isinstance(value,(int,float)) or isinstance(value,bool) or
                    not math.isfinite(value) or value<0
                    for name,value in timings_ms.items())):
                return None
            safe_timings=dict(timings_ms)
        identity=(turn_id,generation)
        progress=self.turn_progress.setdefault(identity,TurnProgress())
        if progress.state in TURN_PROGRESS_TERMINAL_STATES or state==progress.state:
            return None
        if progress.state is None:
            if state in {"complete","blocked"}:
                return None
        elif state not in TURN_PROGRESS_TRANSITIONS.get(progress.state,frozenset()):
            return None
        progress.revision+=1
        progress.state=state
        if route is not None:
            progress.route=route
        fields={
            "configuration_id":self.accepted_configuration_id,
            "turn_id":turn_id,
            "generation":generation,
            "revision":progress.revision,
            "state":state,
        }
        if progress.route is not None:
            fields["route"]=progress.route
        if safe_timings:
            fields["timings_ms"]=safe_timings
        return fields
    def set_turn_terminal_outcome(
        self,turn_id:int,generation:int,outcome:str,
    )->bool:
        if outcome not in {"complete","blocked"}:
            return False
        progress=self.turn_progress.get((turn_id,generation))
        if progress is None or progress.state in TURN_PROGRESS_TERMINAL_STATES:
            return False
        progress.terminal_outcome=outcome
        return True
    def turn_terminal_outcome(self,turn_id:int,generation:int)->str:
        progress=self.turn_progress.get((turn_id,generation))
        return progress.terminal_outcome if progress is not None else "complete"
    def refresh_cooldown(self):
        if self.state==TurnState.COOLDOWN and self.clock()>=self.cooldown_until:
            self.active_turn_id=None; self.framer=FrameBuffer()
            self._restore_primary_detector(TurnState.IDLE)
            self._reset_interrupt_input(); return True
        return False
    def accept_chunk(self,chunk:bytes)->list[ListenerEvent]:
        if not self.active:return []
        self.refresh_cooldown()
        if self.state in (TurnState.PROCESSING,TurnState.AGENT_SPEAKING):
            return self._accept_interrupt_chunk(chunk)
        if self.state not in (TurnState.IDLE,TurnState.USER_SPEAKING):return []
        output=[]
        for frame in self.framer.push(chunk):
            result=self.detector.process(frame)
            if result.speech_started:
                self.active_turn_id=self.next_turn_id; self.next_turn_id+=1
                self.turn_generations[self.active_turn_id]=self.generation
                output.append(ListenerEvent(
                    "speech.start",self.active_turn_id,result,self.generation))
            if result.rejected:
                output.append(ListenerEvent(
                    "speech.rejected",self.active_turn_id or 0,result,
                    self.turn_generations.get(self.active_turn_id or 0,
                                              self.generation)))
                self.active_turn_id=None; self.framer=FrameBuffer()
            elif result.utterance is not None:
                if self.active_turn_id is None: raise RuntimeError("committed utterance has no turn_id")
                self.endpoint_at=self.clock()
                self.turn_committed_at[self.active_turn_id]=self.endpoint_at
                output.append(ListenerEvent(
                    "speech.end",self.active_turn_id,result,
                    self.turn_generations[self.active_turn_id]))
                self.framer=FrameBuffer(); self._reset_interrupt_input()
            if self.state not in (TurnState.IDLE,TurnState.USER_SPEAKING): break
        return output
    def _accept_interrupt_chunk(self,chunk:bytes)->list[ListenerEvent]:
        output=[]
        detector=self._interrupt_detector
        framer=self._interrupt_framer
        adopted=False
        for frame in framer.push(chunk):
            result=detector.process(frame)
            if result.speech_started and not adopted:
                interrupted_turn_id=self.active_turn_id
                if interrupted_turn_id is None:
                    detector.reset(); framer=FrameBuffer()
                    self._reset_interrupt_input()
                    return []
                interrupted_generation=self.turn_generations.get(
                    interrupted_turn_id,self.generation)
                identity=(interrupted_turn_id,interrupted_generation)
                if identity in self._interrupted_generations:
                    continue
                self._interrupted_generations.add(identity)
                self.generation+=1
                superseding_turn_id=self.next_turn_id
                self.next_turn_id+=1
                self.active_turn_id=superseding_turn_id
                self.turn_generations[superseding_turn_id]=self.generation
                self.detector=detector
                self.framer=framer
                self._reset_interrupt_input()
                adopted=True
                output.append(ListenerEvent(
                    "assistant.interrupted",interrupted_turn_id,result,
                    interrupted_generation,superseding_turn_id,
                    self.generation))
                output.append(ListenerEvent(
                    "speech.start",superseding_turn_id,result,self.generation))
            if not adopted:
                continue
            if result.rejected:
                output.append(ListenerEvent(
                    "speech.rejected",self.active_turn_id or 0,result,
                    self.generation))
                self.active_turn_id=None; self.framer=FrameBuffer()
                self._restore_primary_detector(TurnState.IDLE)
            elif result.utterance is not None:
                if self.active_turn_id is None:
                    raise RuntimeError("committed interruption has no turn_id")
                self.endpoint_at=self.clock()
                self.turn_committed_at[self.active_turn_id]=self.endpoint_at
                output.append(ListenerEvent(
                    "speech.end",self.active_turn_id,result,self.generation))
                self._restore_primary_detector(TurnState.PROCESSING)
                self.framer=FrameBuffer(); self._reset_interrupt_input()
            if self.state not in (TurnState.IDLE,TurnState.USER_SPEAKING):
                break
        return output
    def start_playback(self,turn_id:int)->bool:
        if self.state!=TurnState.PROCESSING or turn_id!=self.active_turn_id:return False
        self.detector.state=TurnState.AGENT_SPEAKING
        self._reset_interrupt_input(playback=True)
        return True
    def playback_ended(self,turn_id:int)->bool:
        if self.state!=TurnState.AGENT_SPEAKING or turn_id!=self.active_turn_id:return False
        received_at=self.clock()
        committed_at=self.turn_committed_at.get(turn_id)
        self._restore_primary_detector(TurnState.COOLDOWN)
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000
        self.framer=FrameBuffer()
        self._reset_interrupt_input()
        if committed_at is not None and turn_id not in self.playback_completion_metrics:
            self.playback_completion_metrics[turn_id]=max(
                0,round((received_at-committed_at)*1000))
        return True
    def playback_completion_ms(self,turn_id:int)->int|None:
        return self.playback_completion_metrics.get(turn_id)
    def cascade_failed(self,turn_id:int):
        if turn_id==self.active_turn_id:
            self.active_turn_id=None; self.framer=FrameBuffer()
            self._restore_primary_detector(TurnState.IDLE)
            self._reset_interrupt_input()
    def reject_empty_transcript(self,turn_id:int)->bool:
        if self.state!=TurnState.PROCESSING or turn_id!=self.active_turn_id:return False
        self.active_turn_id=None; self.framer=FrameBuffer()
        self._restore_primary_detector(TurnState.COOLDOWN)
        self._reset_interrupt_input()
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000; return True
    def complete_without_playback(self,turn_id:int)->bool:
        if self.state!=TurnState.PROCESSING or turn_id!=self.active_turn_id:return False
        self.active_turn_id=None; self.framer=FrameBuffer()
        self._restore_primary_detector(TurnState.COOLDOWN)
        self._reset_interrupt_input()
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000; return True

class LockedSender:
    def __init__(self,websocket): self.websocket=websocket; self.lock=asyncio.Lock()
    async def text(self,kind:str,**fields):
        async with self.lock: await self.websocket.send_text(event(kind,**fields))
    async def segment(
        self,turn_id:int,index:int,frames:list[bytes],generation:int|None=None,
    ):
        async with self.lock:
            await self.websocket.send_text(audio_segment_start(
                turn_id,index,len(frames),generation=generation))
            for frame in frames: await self.websocket.send_bytes(frame)
            await self.websocket.send_text(event(
                "audio.segment.end",turn_id=turn_id,segment_index=index,
                **({"generation":generation} if generation is not None else {})))
    async def native_audio(
        self,turn_id:int,response_id:str,item_id:str|None,pcm:bytes,*,sample_rate:int
    ):
        async with self.lock:
            await self.websocket.send_text(event(
                "native.audio.delta",turn_id=turn_id,response_id=response_id,
                item_id=item_id,sample_rate=sample_rate,encoding="pcm_s16le",
                byte_length=len(pcm)))
            await self.websocket.send_bytes(pcm)

async def run_turn(websocket:WebSocket,session:ListenerSession,source_pcm:bytes,turn_id:int,
                   input_frames:int,voiced_frames:int=0,clock:Callable[[],float]=time.perf_counter)->None:
    sender=LockedSender(websocket); endpoint=session.endpoint_at or clock(); timings={}; generation=session.generation
    async def current_text(kind:str,**fields)->bool:
        if not session.is_current(turn_id,generation): return False
        fields.setdefault("generation",generation)
        await sender.text(kind,**fields); return True
    async def progress(
        state:str,*,route:str|None=None,
        timings_ms:dict[str,int|float]|None=None,
    )->bool:
        if not session.is_current(turn_id,generation): return False
        fields=session.advance_turn_progress(
            turn_id,generation,state,route=route,timings_ms=timings_ms)
        if fields is None: return False
        await sender.text("turn.state",**fields); return True
    await progress("transcribing")
    if not await current_text("turn.processing",turn_id=turn_id,input_frames=input_frames): return
    started=clock(); transcription=await asyncio.to_thread(transcribe,clean_path(source_pcm)); timings["stt"]=round((clock()-started)*1000)
    # Keep test/custom adapters written to the pre-Phase-3 text-only boundary
    # working in manual mode while production returns structured metadata.
    if isinstance(transcription,str):
        transcription=Transcription(transcription,None)
    transcript=transcription.text
    if not transcript.strip():
        if session.reject_empty_transcript(turn_id):
            await sender.text("speech.rejected",turn_id=turn_id,reason="empty_transcript",voiced_frames=voiced_frames,total_frames=input_frames,duration_ms=input_frames*20)
            await sender.text("state.changed",state=session.state.value,turn_id=turn_id,cooldown_ms=session.detector.config.cooldown_ms)
        return
    if not await current_text("transcript",turn_id=turn_id,text=transcript): return
    await progress("routing")
    emergency=recognize_emergency(transcript)
    if emergency is not None:
        text=emergency.response
        if not await current_text("reply.delta",turn_id=turn_id,segment_index=0,text=text): return
        try:
            await progress("synthesizing",route="deterministic_emergency")
            pcm=await asyncio.to_thread(synthesize,text,emergency.language)
            frames=frame_complete_audio(pcm)
        except Exception:
            log.exception("emergency TTS failed")
            await progress("error",route="deterministic_emergency")
            await current_text("reply.complete",turn_id=turn_id,text=text)
            await current_text("audio.complete",turn_id=turn_id,segment_count=0)
            timings["total_ms"]=round((clock()-endpoint)*1000)
            await current_text("turn.done",turn_id=turn_id,timings_ms=timings,
                               segment_count=0,input_frames=input_frames,
                               output_frames=0,tools_used=[],
                               route="deterministic_emergency")
            if session.complete_without_playback(turn_id):
                await sender.text("state.changed",state=session.state.value,turn_id=turn_id,
                                  cooldown_ms=session.detector.config.cooldown_ms)
            return
        if session.start_playback(turn_id):
            timings["first_audio_ms"]=round((clock()-endpoint)*1000)
            await progress(
                "playing",route="deterministic_emergency",
                timings_ms={"time_to_playable_audio":timings["first_audio_ms"]})
            await current_text("state.changed",state=session.state.value,turn_id=turn_id)
            await sender.segment(turn_id,0,frames,generation)
            await current_text("reply.complete",turn_id=turn_id,text=text)
            await current_text("audio.complete",turn_id=turn_id,segment_count=1)
            timings["total_ms"]=round((clock()-endpoint)*1000)
            await current_text("turn.done",turn_id=turn_id,timings_ms=timings,
                               segment_count=1,input_frames=input_frames,
                               output_frames=len(frames),tools_used=[],
                               route="deterministic_emergency")
        return
    pending=session.history.pending_report
    pending_language=(
        pending.get("language")
        if isinstance(pending,dict) and pending.get("language") in ("ko","en","vi")
        else None
    )
    pending_intent=(
        confirmation_intent(transcript,pending_language)
        if pending_language is not None
        else None
    )
    if pending_intent is not None:
        # A validated draft owns the language of this bounded confirmation.
        # Provider metadata and the current auto-language state cannot override it.
        turn_language=pending_language
        session.last_confirmed_language=turn_language
        await current_text("session.turn_language_resolved",turn_id=turn_id,
                           language=turn_language)
    else:
        resolution=resolve_turn_language(
            transcript,transcription.detected_language,mode=session.language_mode,
            manual_language=session.manual_language,
        )
        if not resolution.resolved:
            fallback=pending_language or session.last_confirmed_language or (
                session.tool_context.language if session.tool_context else "ko")
            clarification=(
                REPORT_CONFIRMATION_CLARIFICATION_TEXT
                if pending_language is not None
                else CLARIFICATION_TEXT
            )
            text=clarification.get(fallback,clarification["ko"])
            await current_text("session.language_confirmation_required",turn_id=turn_id,
                               reason=resolution.reason,languages=["ko","en"])
            await current_text("reply.delta",turn_id=turn_id,segment_index=0,text=text)
            session.set_turn_terminal_outcome(turn_id,generation,"blocked")
            try:
                await progress("synthesizing",route="language_clarification")
                pcm=await asyncio.to_thread(synthesize,text,fallback)
                frames=frame_complete_audio(pcm)
            except Exception:
                log.exception("language clarification TTS failed")
                await progress("error",route="language_clarification")
                await current_text("reply.complete",turn_id=turn_id,text=text)
                await current_text("audio.complete",turn_id=turn_id,segment_count=0)
                timings["total_ms"]=round((clock()-endpoint)*1000)
                await current_text("turn.done",turn_id=turn_id,timings_ms=timings,
                                   segment_count=0,input_frames=input_frames,
                                   output_frames=0,tools_used=[],
                                   route="language_clarification")
                if session.complete_without_playback(turn_id):
                    await sender.text("state.changed",state=session.state.value,turn_id=turn_id,
                                      cooldown_ms=session.detector.config.cooldown_ms)
                return
            if session.start_playback(turn_id):
                timings["first_audio_ms"]=round((clock()-endpoint)*1000)
                await progress(
                    "playing",route="language_clarification",
                    timings_ms={"time_to_playable_audio":timings["first_audio_ms"]})
                await current_text("state.changed",state=session.state.value,turn_id=turn_id)
                await sender.segment(turn_id,0,frames,generation)
                await current_text("reply.complete",turn_id=turn_id,text=text)
                await current_text("audio.complete",turn_id=turn_id,segment_count=1)
                timings["total_ms"]=round((clock()-endpoint)*1000)
                await current_text("turn.done",turn_id=turn_id,timings_ms=timings,
                                   segment_count=1,input_frames=input_frames,
                                   output_frames=len(frames),tools_used=[],
                                   route="language_clarification")
            return
        turn_language=resolution.language
        session.last_confirmed_language=turn_language
        await current_text("session.turn_language_resolved",turn_id=turn_id,
                           language=turn_language)
    if session.curated_protocol_session is not None:
        curated=session.curated_protocol_session
        checkpoint=curated._checkpoint()
        try:
            await progress("checking_protocol",route="curated_protocol")
            plan=curated.plan(
                transcript,turn_id=turn_id,language=turn_language)
            display_text=plan.display_text
            speech_text=plan.speech_text
            if not isinstance(display_text,str) or not display_text.strip():
                raise RuntimeError("curated protocol produced no display text")
            if not isinstance(speech_text,str) or not speech_text.strip():
                raise RuntimeError("curated protocol produced no speech text")
            if plan.speech_mode.value=="blocked":
                session.set_turn_terminal_outcome(
                    turn_id,generation,"blocked")
            timings["first_tts_request_ms"]=round((clock()-endpoint)*1000)
            await progress("synthesizing",route="curated_protocol")
            pcm=await asyncio.to_thread(synthesize,speech_text,turn_language)
            frames=frame_complete_audio(pcm)
            if not frames or not session.start_playback(turn_id):
                raise RuntimeError("curated protocol produced no playable audio")
        except BaseException:
            curated._restore(checkpoint)
            raise
        timings["first_audio_ms"]=round((clock()-endpoint)*1000)
        await progress(
            "playing",route="curated_protocol",
            timings_ms={"time_to_playable_audio":timings["first_audio_ms"]})
        await current_text(
            "protocol.fixture.state",turn_id=turn_id,
            configuration_id=session.accepted_configuration_id,
            state=curated.state(),action=plan.action.value)
        await current_text(
            "reply.delta",turn_id=turn_id,segment_index=0,text=display_text)
        await current_text(
            "state.changed",state=session.state.value,turn_id=turn_id)
        await sender.segment(turn_id,0,frames,generation)
        await current_text(
            "reply.complete",turn_id=turn_id,text=display_text)
        await current_text(
            "audio.complete",turn_id=turn_id,segment_count=1)
        timings["total_ms"]=round((clock()-endpoint)*1000)
        await current_text(
            "turn.done",turn_id=turn_id,timings_ms=timings,
            segment_count=1,input_frames=input_frames,
            output_frames=len(frames),tools_used=[],
            route="curated_protocol",result_kind=plan.action.value,
            fact_id=plan.fact_id,speech_mode=plan.speech_mode.value,
            critical_warning_present=plan.critical_warning_text is not None)
        if session.is_current(turn_id,generation):
            session.history.commit([
                {"role":"user","content":transcript},
                {"role":"assistant","content":display_text},
            ])
        return
    if session.tool_context is None: raise RuntimeError("trusted Tool context is required")
    authorized_step_id=None
    authorized_timer_step_id=None
    if pending is None:
        authorized_step_id=authorized_completion_step_id(
            transcript,turn_language,session.tool_context.procedure_controller)
        authorized_timer_step_id=authorized_timer_start_step_id(
            transcript,turn_language,session.tool_context.procedure_controller)
        observation_arguments=authorized_observation_arguments(
            transcript,turn_language,session.tool_context.procedure_controller)
    else:
        observation_arguments=None
    turn_context=ToolContext(session.tool_context.catalog_path,session.tool_context.facility_id,
                             turn_language,session.tool_context.usage_scope,
                             session.tool_context.report_language,
                             session.tool_context.procedure_controller,
                             authorized_step_id,
                             transcript)
    deterministic_tool=None
    deterministic_arguments=None
    if authorized_step_id is not None:
        deterministic_tool=COMPLETE_CURRENT_STEP_TOOL_NAME
        deterministic_arguments={"expected_step_id":authorized_step_id}
    elif authorized_timer_step_id is not None:
        deterministic_tool=START_STEP_TIMER_TOOL_NAME
        deterministic_arguments={"expected_step_id":authorized_timer_step_id}
    elif observation_arguments is not None:
        deterministic_tool=RECORD_STEP_OBSERVATION_TOOL_NAME
        deterministic_arguments=observation_arguments
    elif pending is None and korean_timer_status_question(
            transcript,turn_language):
        deterministic_tool=GET_CURRENT_STEP_TOOL_NAME
        deterministic_arguments={}
    if deterministic_tool is not None:
        await progress("checking_protocol",route="deterministic_procedure")
        await current_text(
            "tool.call",turn_id=turn_id,tool=deterministic_tool,round=0)
        started_tool=clock()
        try:
            deterministic_result=execute_tool(
                deterministic_tool,deterministic_arguments,turn_context)
        except Exception:
            deterministic_result={
                "status":"error","code":"procedure_store_unavailable"}
        tool_elapsed_ms=round((clock()-started_tool)*1000)
        timings["tool_ms"]=tool_elapsed_ms
        fields={
            "tool":deterministic_tool,
            "status":deterministic_result.get("status","error"),
            "elapsed_ms":tool_elapsed_ms,
            "round":0,
        }
        if deterministic_result.get("code"):
            fields["code"]=deterministic_result["code"]
        if isinstance(deterministic_result.get("state"),dict):
            fields["procedure_state"]=deterministic_result["state"]
        for key in (
            "operation","idempotent","completed_step_id","recorded_step_id",
            "timer_step_id","observation","timer","audit_summary",
            "remaining_seconds",
        ):
            if deterministic_result.get(key) is not None:
                fields[key]=deterministic_result[key]
        fields["procedure_completed"]=bool(
            deterministic_result.get("completed"))
        await current_text("tool.result",turn_id=turn_id,**fields)
        if deterministic_result.get("code"):
            await current_text(
                "procedure.error",turn_id=turn_id,
                code=deterministic_result["code"])
        state=deterministic_result.get("state")
        if isinstance(state,dict):
            if (not deterministic_result.get("code") and
                    deterministic_result.get("operation")=="complete" and
                    not deterministic_result.get("idempotent")):
                await current_text(
                    "procedure.step_completed",turn_id=turn_id,
                    step_id=deterministic_result.get("completed_step_id"))
                if deterministic_result.get("completed"):
                    await current_text(
                        "procedure.completed",turn_id=turn_id,state=state)
            if (not deterministic_result.get("code") and
                    deterministic_result.get("operation")=="record_observation"):
                await current_text(
                    "procedure.observation_recorded",turn_id=turn_id,
                    step_id=deterministic_result.get("recorded_step_id"))
            if (not deterministic_result.get("code") and
                    deterministic_result.get("operation")=="start_timer" and
                    not deterministic_result.get("idempotent")):
                await current_text(
                    "procedure.timer_started",turn_id=turn_id,
                    step_id=deterministic_result.get("timer_step_id"),
                    timer=state.get("timer"))
            await current_text(
                "procedure.state",turn_id=turn_id,state=state)
        text=deterministic_procedure_text(
            deterministic_result,turn_language)
        if deterministic_result.get("code"):
            session.set_turn_terminal_outcome(turn_id,generation,"blocked")
        await current_text(
            "reply.delta",turn_id=turn_id,segment_index=0,text=text)
        try:
            timings["first_tts_request_ms"]=round((clock()-endpoint)*1000)
            await progress("synthesizing",route="deterministic_procedure")
            pcm=await asyncio.to_thread(synthesize,text,turn_language)
            frames=frame_complete_audio(pcm)
        except Exception:
            log.exception("deterministic procedure TTS failed")
            await progress("error",route="deterministic_procedure")
            frames=[]
        segment_count=0
        output_frames=0
        playback_started=bool(frames and session.start_playback(turn_id))
        if playback_started:
            timings["first_audio_ms"]=round((clock()-endpoint)*1000)
            await progress(
                "playing",route="deterministic_procedure",
                timings_ms={"time_to_playable_audio":timings["first_audio_ms"]})
            await current_text(
                "state.changed",state=session.state.value,turn_id=turn_id)
            await sender.segment(turn_id,0,frames,generation)
            segment_count=1
            output_frames=len(frames)
        await current_text("reply.complete",turn_id=turn_id,text=text)
        await current_text(
            "audio.complete",turn_id=turn_id,segment_count=segment_count)
        timings["total_ms"]=round((clock()-endpoint)*1000)
        await current_text(
            "turn.done",turn_id=turn_id,timings_ms=timings,
            segment_count=segment_count,input_frames=input_frames,
            output_frames=output_frames,tools_used=[deterministic_tool],
            route="deterministic_procedure")
        if session.is_current(turn_id,generation):
            session.history.commit([
                {"role":"user","content":transcript},
                {"role":"assistant","content":text},
            ])
        if not playback_started and session.complete_without_playback(turn_id):
            await sender.text(
                "state.changed",state=session.state.value,turn_id=turn_id,
                cooldown_ms=session.detector.config.cooldown_ms)
        return
    queue=asyncio.Queue(); output_frames=0; segment_count=0; first_token=False; first_sentence=False; first_audio=False
    def mark_token():
        nonlocal first_token
        if not first_token: first_token=True; timings["first_grok_token_ms"]=round((clock()-endpoint)*1000)
    async def sentence(segment:SentenceSegment):
        nonlocal first_sentence
        if not first_sentence:
            first_sentence=True; timings["first_sentence_ms"]=round((clock()-endpoint)*1000)
            await progress("composing",route="brain")
        if not await current_text("reply.delta",turn_id=turn_id,segment_index=segment.segment_index,text=segment.text): return
        await queue.put(segment)
    async def tool_event(kind,fields):
        if (kind=="tool.call" and
                fields.get("tool")=="search_approved_safety_manual"):
            await progress(
                "checking_approved_information",route="approved_information")
        if not await current_text(kind,turn_id=turn_id,**fields): return
        if kind=="tool.result" and fields.get("tool") in PROCEDURE_TOOL_NAMES:
            state=fields.get("procedure_state")
            if fields.get("code"):
                await current_text("procedure.error",turn_id=turn_id,code=fields["code"])
            elif isinstance(state,dict):
                operation=fields.get("operation")
                if operation=="start" and not fields.get("idempotent"):
                    await current_text("procedure.started",turn_id=turn_id,state=state)
                if operation=="complete" and not fields.get("idempotent"):
                    await current_text("procedure.step_completed",turn_id=turn_id,
                                       step_id=fields.get("completed_step_id"))
                    if fields.get("procedure_completed"):
                        await current_text("procedure.completed",turn_id=turn_id,state=state)
                if operation=="record_observation":
                    await current_text(
                        "procedure.observation_recorded",turn_id=turn_id,
                        step_id=fields.get("recorded_step_id"))
                if operation=="start_timer" and not fields.get("idempotent"):
                    await current_text(
                        "procedure.timer_started",turn_id=turn_id,
                        step_id=fields.get("timer_step_id"),
                        timer=state.get("timer"))
                if operation=="summary":
                    await current_text(
                        "procedure.audit_summary",turn_id=turn_id,
                        audit_summary=fields.get("audit_summary"))
                await current_text("procedure.state",turn_id=turn_id,state=state)
        if (kind=="tool.result" and fields.get("tool")==CREATE_REPORT_TOOL_NAME
                and fields.get("status")=="confirmed"
                and isinstance(fields.get("procedure_state"),dict)):
            await current_text(
                "procedure.blocked_for_handoff",turn_id=turn_id,
                report_id=fields.get("report_id"),
                state=fields["procedure_state"])
            await current_text(
                "procedure.state",turn_id=turn_id,
                state=fields["procedure_state"])
        log.info("%s turn_id=%s tool=%s status=%s elapsed_ms=%s",kind,turn_id,fields.get("tool"),fields.get("status"),fields.get("elapsed_ms"))
    async def consume():
        nonlocal output_frames,segment_count,first_audio
        while True:
            segment=await queue.get()
            if segment is None:return
            if "first_tts_request_ms" not in timings:
                timings["first_tts_request_ms"]=round((clock()-endpoint)*1000)
                await progress("synthesizing",route="brain")
            pcm=await asyncio.to_thread(synthesize,segment.text,turn_language); frames=frame_complete_audio(pcm)
            if not session.is_current(turn_id,generation):return
            if not first_audio:
                if not session.start_playback(turn_id):return
                first_audio=True; timings["first_audio_ms"]=round((clock()-endpoint)*1000)
                await progress(
                    "playing",route="brain",
                    timings_ms={"time_to_playable_audio":timings["first_audio_ms"]})
                if not await current_text("state.changed",state=session.state.value,turn_id=turn_id): return
            if not session.is_current(turn_id,generation): return
            await sender.segment(
                turn_id,segment.segment_index,frames,generation)
            output_frames+=len(frames); segment_count+=1
    consumer=asyncio.create_task(consume())
    try:
        await progress("composing",route="brain")
        client=AsyncOpenAI(base_url=api_url(""),api_key=require_env("XAI_API_KEY")); client.model=require_env("CHAT_MODEL")
        result=await stream_brain_turn(client,session.history,transcript,sentence,mark_token,tool_event,
                                       tool_context=turn_context)
        if result.tool_ms is not None: timings["tool_ms"]=result.tool_ms
        await queue.put(None); await consumer
        if not first_audio: raise RuntimeError("Grok produced no playable spoken response")
        if not await current_text("reply.complete",turn_id=turn_id,text=result.text): return
        if not await current_text("audio.complete",turn_id=turn_id,segment_count=segment_count): return
        timings["total_ms"]=round((clock()-endpoint)*1000)
        if not await current_text("turn.done",turn_id=turn_id,timings_ms=timings,segment_count=segment_count,input_frames=input_frames,output_frames=output_frames,tools_used=result.tools_used,route="brain"): return
        if not session.is_current(turn_id,generation): return
        session.history.commit(result.messages,result.source_references)
    finally:
        if not consumer.done(): consumer.cancel()
        while not queue.empty():
            try: queue.get_nowait()
            except asyncio.QueueEmpty: break

async def run_turn_safely(websocket,session,source_pcm,turn_id,input_frames,voiced_frames=0):
    generation=session.turn_generations.get(turn_id,session.generation)
    try: await run_turn(websocket,session,source_pcm,turn_id,input_frames,voiced_frames)
    except asyncio.CancelledError: session.cascade_failed(turn_id); raise
    except WebSocketDisconnect: session.cascade_failed(turn_id)
    except Exception:
        log.exception("voice turn failed")
        progress=session.advance_turn_progress(
            turn_id,generation,"error")
        session.cascade_failed(turn_id)
        if progress is not None:
            await websocket.send_text(event("turn.state",**progress))
        await websocket.send_text(event(
            "error",turn_id=turn_id,generation=generation,
            message="voice turn processing failed"))
        await websocket.send_text(event(
            "state.changed",state=session.state.value,turn_id=turn_id,
            generation=generation))

async def cancel_cascade_generation(
    websocket:WebSocket,session:ListenerSession,task:asyncio.Task|None,
    interruption:ListenerEvent,
)->None:
    """Cancel one superseded Cascade task, then clear that browser generation."""
    if interruption.kind!="assistant.interrupted":
        raise ValueError("Cascade cancellation requires an interruption event")
    if task is not None and not task.done():
        task.cancel()
        try: await task
        except (asyncio.CancelledError,WebSocketDisconnect): pass
    progress=session.advance_turn_progress(
        interruption.turn_id,interruption.generation,"cancelled")
    if progress is None:
        return
    await websocket.send_text(event(
        "cascade.playback.clear",
        **progress,
        superseding_turn_id=interruption.superseding_turn_id,
        superseding_generation=interruption.superseding_generation,
        reason="accepted_speech_onset",
    ))

@app.websocket("/ws")
async def voice_socket(websocket:WebSocket):
    await websocket.accept()
    try:
        vad_settings=VoiceVadSettings.from_environment()
        config=VadConfig.from_settings(vad_settings.cascade)
    except (ConfigurationError,ValueError) as exc:
        await websocket.send_text(event(
            "error",message=f"invalid VAD configuration: {exc}"))
        await websocket.close(code=1008,reason="invalid VAD configuration")
        return
    session=ListenerSession(EndpointDetector(
        config,listening_onset=True)); task=None; trusted_config=None; procedure_store=None
    curated_fixture=None
    sender=LockedSender(websocket); native_session=None; native_config=None; pipeline="cascade"
    await websocket.send_text(event("ready",sample_rate=16000,native_sample_rate=NATIVE_SAMPLE_RATE,
                                    pipelines=["cascade","native"],frame_ms=20,
                                    frame_bytes=FRAME_BYTES,vad_mode=config.mode,
                                    endpoint_silence_ms=config.endpoint_silence_frames*20,
                                    prefix_padding_ms=config.prefix_frames*20))
    try:
        while True:
            message=await websocket.receive()
            if message.get("type")=="websocket.disconnect": break
            if message.get("bytes") is not None:
                if native_session is not None:
                    await native_session.send_audio(message["bytes"])
                    continue
                if session.refresh_cooldown(): await websocket.send_text(event("state.changed",state="IDLE"))
                for item in session.accept_chunk(message["bytes"]):
                    if item.kind=="assistant.interrupted":
                        await cancel_cascade_generation(
                            websocket,session,task,item)
                        task=None
                        continue
                    await websocket.send_text(event(
                        item.kind,turn_id=item.turn_id,
                        generation=item.generation,state=session.state.value,
                        voiced_frames=item.result.voiced_frames,
                        total_frames=item.result.total_frames,
                        duration_ms=item.result.total_frames*20,
                        reason=item.result.rejection_reason,
                        forced=item.result.forced))
                    if item.kind=="speech.start":
                        progress=session.advance_turn_progress(
                            item.turn_id,item.generation,"listening")
                        if progress is not None:
                            await websocket.send_text(event(
                                "turn.state",**progress))
                    if item.kind=="speech.end":
                        task=asyncio.create_task(run_turn_safely(websocket,session,item.result.utterance or b"",item.turn_id,item.result.total_frames,item.result.voiced_frames))
                continue
            if message.get("text") is None:continue
            control=parse_control(message["text"])
            if control["type"]=="session.start":
                if session.active:raise ProtocolError("session already active")
                requested_mode=control["mode"]
                configuration_id=control["configuration_id"]
                requested_protocol_id=control["protocol_id"]
                configuration_stage="server_policy"
                try:
                    trusted_config=trusted_config or server_config()
                    configuration_stage="session_language"
                    context=server_tool_context(trusted_config,control["language"])
                    selected_curated_fixture=None
                    selected_procedure_definitions=None
                    selection_failure=None
                    if requested_mode=="cascade":
                        if requested_protocol_id is None:
                            selection_failure="protocol_selection_required"
                        else:
                            if trusted_config.curated_protocol_fixture_path is not None:
                                configuration_stage="curated_protocol_fixture"
                                curated_fixture=curated_fixture or load_curated_protocol_fixture(
                                    trusted_config.curated_protocol_fixture_path,
                                    trusted_config.curated_protocol_provenance_path,
                                    trusted_config.curated_protocol_source_pdf_path,
                                )
                                if curated_fixture.protocol_id==requested_protocol_id:
                                    selected_curated_fixture=curated_fixture
                            if (selected_curated_fixture is None and
                                    trusted_config.procedure_catalog_path and
                                    trusted_config.procedure_store_path):
                                configuration_stage="procedure_configuration"
                                definitions=load_procedure_definitions(
                                    trusted_config.procedure_catalog_path,
                                    trusted_config.catalog_path,
                                    facility_id=trusted_config.facility_id,
                                    language=context.language,
                                    usage_scope=trusted_config.usage_scope)
                                if requested_protocol_id in definitions:
                                    selected_procedure_definitions=definitions
                            if (selected_curated_fixture is None and
                                    selected_procedure_definitions is None):
                                selection_failure=(
                                    "protocol_selection_unknown"
                                    if (trusted_config.curated_protocol_fixture_path or
                                        trusted_config.procedure_catalog_path)
                                    else "protocol_selection_unavailable")
                    elif requested_protocol_id is not None:
                        selection_failure="protocol_selection_not_supported_for_mode"
                    elif (trusted_config.procedure_catalog_path and
                            trusted_config.procedure_store_path):
                        configuration_stage="procedure_configuration"
                        selected_procedure_definitions=load_procedure_definitions(
                            trusted_config.procedure_catalog_path,
                            trusted_config.catalog_path,
                            facility_id=trusted_config.facility_id,
                            language=context.language,
                            usage_scope=trusted_config.usage_scope)
                    if selection_failure is not None:
                        await websocket.send_text(event(
                            "session.configuration_required",
                            configuration_id=configuration_id,
                            mode=requested_mode,
                            language=context.language,
                            protocol_id=None,
                            reason=selection_failure,
                        ))
                        continue
                    if selected_procedure_definitions is not None:
                        configuration_stage="procedure_configuration"
                        procedure_store=procedure_store or ProcedureStore(trusted_config.procedure_store_path)
                        context=ToolContext(
                            context.catalog_path,context.facility_id,
                            context.language,context.usage_scope,
                            context.report_language,
                            ProcedureController(
                                selected_procedure_definitions,procedure_store))
                    configuration_stage="session_state"
                    session.set_tool_context(context)
                    session.set_curated_protocol_fixture(selected_curated_fixture)
                    session.start()
                    if session.curated_protocol_session is not None:
                        session.curated_protocol_session.activate_configured()
                    pipeline=requested_mode
                    if pipeline=="native":
                        configuration_stage="native_environment"
                        native_config=(
                            native_config
                            or NativeRealtimeConfig.from_environment(
                                vad_settings.native))
                        configuration_stage="native_session"
                        native_session=NativeRealtimeSession(
                            sender,session.tool_context,native_config,
                            language_mode=session.language_mode,
                            manual_language=session.manual_language)
                        configuration_stage="native_provider_session"
                        await native_session.start()
                    session.accept_configuration(
                        configuration_id,pipeline,context.language,
                        requested_protocol_id)
                except (RuntimeError,ValueError,NativeRealtimeError) as exc:
                    field_names=getattr(exc,"field_names",())
                    safe_detail=(
                        str(exc)
                        if isinstance(exc,ServerConfigurationError)
                        else "none"
                    )
                    log.warning(
                        "session.start rejected pipeline=%s stage=%s "
                        "exception=%s fields=%s detail=%s",
                        requested_mode,configuration_stage,
                        type(exc).__name__,
                        ",".join(field_names) if field_names else "none",
                        safe_detail,
                    )
                    if native_session is not None:
                        await native_session.stop()
                        native_session=None
                    session.stop()
                    load_failed=configuration_stage=="curated_protocol_fixture"
                    load_failure_messages={
                        "ko":"선택된 프로토콜을 불러오지 못했습니다.",
                        "vi":"Không thể tải quy trình đã chọn.",
                        "en":"The selected protocol could not be loaded.",
                    }
                    await websocket.send_text(event(
                        "error",
                        message=(
                            load_failure_messages.get(
                                context.language,
                                load_failure_messages["en"],
                            )
                            if load_failed
                            else "invalid session configuration"
                        ),
                    ))
                    continue
                await websocket.send_text(event(
                    "session.ready",
                    configuration_id=session.accepted_configuration_id,
                    mode=session.accepted_mode,
                    language=session.accepted_language,
                    protocol_id=session.accepted_protocol_id,
                ))
                if session.curated_protocol_session is not None:
                    await websocket.send_text(event(
                        "protocol.fixture.state",
                        configuration_id=session.accepted_configuration_id,
                        state=session.curated_protocol_session.state(),
                        action="attached",
                    ))
                await websocket.send_text(event("session.started",state=session.state.value,
                                                pipeline=pipeline))
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.set_language":
                if not session.active:
                    await websocket.send_text(event("error",message="session is not active"))
                    continue
                try:
                    trusted_config=trusted_config or server_config()
                    context=server_tool_context(trusted_config,control["language"])
                    if session.tool_context and session.tool_context.procedure_controller:
                        context=ToolContext(context.catalog_path,context.facility_id,context.language,
                                            context.usage_scope,context.report_language,
                                            session.tool_context.procedure_controller)
                except (RuntimeError,ValueError):
                    await websocket.send_text(event("error",message="invalid session language"))
                    continue
                if task and not task.done(): task.cancel()
                session.set_tool_context(context)
                if native_session is not None:
                    await native_session.update_language(
                        context,language_mode="manual",manual_language=context.language)
                await websocket.send_text(event("session.language_changed",state=session.state.value))
                await websocket.send_text(event("session.language_state",mode="manual",
                                                language=context.language))
            elif control["type"]=="session.set_language_mode":
                if not session.active:
                    await websocket.send_text(event("error",message="session is not active"))
                    continue
                try:
                    trusted_config=trusted_config or server_config()
                    context=(server_tool_context(trusted_config,control["language"])
                             if control["mode"]=="manual" else None)
                    if context and session.tool_context and session.tool_context.procedure_controller:
                        context=ToolContext(context.catalog_path,context.facility_id,context.language,
                                            context.usage_scope,context.report_language,
                                            session.tool_context.procedure_controller)
                    session.set_language_mode(control["mode"],context)
                except (RuntimeError,ValueError):
                    await websocket.send_text(event("error",message="invalid language mode"))
                    continue
                if task and not task.done(): task.cancel()
                if native_session is not None and session.tool_context is not None:
                    await native_session.update_language(
                        session.tool_context,language_mode=session.language_mode,
                        manual_language=session.manual_language)
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.reset":
                if not session.active:
                    await websocket.send_text(event("error",message="session is not active"))
                    continue
                if task and not task.done(): task.cancel()
                session.reset_sensitive_state()
                if session.tool_context and session.tool_context.procedure_controller:
                    session.tool_context.procedure_controller.detach()
                if native_session is not None:
                    await native_session.stop()
                    native_session=NativeRealtimeSession(
                        sender,session.tool_context,native_config,
                        language_mode=session.language_mode,
                        manual_language=session.manual_language)
                    try:
                        await native_session.start()
                    except NativeRealtimeError:
                        await native_session.stop()
                        native_session=None
                        session.stop()
                        await websocket.send_text(event(
                            "error",message="native session reset failed"))
                        continue
                await websocket.send_text(event("session.reset",state=session.state.value))
                await websocket.send_text(event("procedure.state",state=unattached_procedure_state()))
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.stop":
                if task and not task.done(): task.cancel()
                if native_session is not None:
                    await native_session.stop()
                    native_session=None
                pipeline="cascade"
                session.stop(); await websocket.send_text(event("session.stopped",state=session.state.value))
            elif control["type"]=="report.status.get":
                result=await asyncio.to_thread(
                    check_safety_report_status,control["report_id"])
                await websocket.send_text(event(
                    "report.status",
                    report_id=control["report_id"],
                    status=result.get("status","error"),
                    report_status=result.get("report_status"),
                    attempts=result.get("attempts",0),
                    workflow=result.get("workflow"),
                ))
            elif control["type"]=="playback.ended" and session.playback_ended(control["turn_id"]):
                generation=session.turn_generations.get(
                    control["turn_id"],session.generation)
                playback_completion_ms=session.playback_completion_ms(control["turn_id"])
                progress=session.advance_turn_progress(
                    control["turn_id"],generation,
                    session.turn_terminal_outcome(control["turn_id"],generation),
                    timings_ms=(
                        {"playback_completion":playback_completion_ms}
                        if playback_completion_ms is not None else None),
                )
                if progress is not None:
                    await websocket.send_text(event("turn.state",**progress))
                if playback_completion_ms is not None:
                    log.info(
                        "playback.completed pipeline=cascade turn_id=%s "
                        "playback_completion_ms=%s",
                        control["turn_id"],playback_completion_ms)
                    await websocket.send_text(event(
                        "playback.completed",pipeline="cascade",
                        turn_id=control["turn_id"],
                        generation=generation,
                        playback_completion_ms=playback_completion_ms))
                await websocket.send_text(event(
                    "state.changed",state=session.state.value,
                    turn_id=control["turn_id"],generation=generation,
                    cooldown_ms=config.cooldown_ms))
            elif control["type"]=="native.playback.truncate" and native_session is not None:
                await native_session.truncate_playback(
                    control["response_id"],control["item_id"],control["audio_end_ms"])
            elif control["type"]=="native.playback.ended" and native_session is not None:
                completion=await native_session.playback_ended(
                    control["response_id"])
                if completion is not None:
                    turn_id,playback_completion_ms=completion
                    log.info(
                        "playback.completed pipeline=native turn_id=%s "
                        "playback_completion_ms=%s",
                        turn_id,playback_completion_ms)
                    await websocket.send_text(event(
                        "playback.completed",pipeline="native",
                        turn_id=turn_id,
                        playback_completion_ms=playback_completion_ms))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("session failed")
        try: await websocket.send_text(event("error",message=str(exc)))
        except Exception: pass
    finally:
        if task and not task.done():
            task.cancel()
            try: await task
            except (asyncio.CancelledError, WebSocketDisconnect): pass
        if native_session is not None:
            await native_session.stop()
        session.stop()
        if procedure_store is not None: procedure_store.close()

app.mount("/",StaticFiles(directory=STATIC_DIR,html=True),name="static")
