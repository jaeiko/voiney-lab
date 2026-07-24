"""SafeBridge Voice: hands-free voice cascade with M2 Dispatcher tools."""
from __future__ import annotations
import asyncio, logging, os, time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from safebridge_voice.audio import FRAME_BYTES, FrameBuffer, clean_path, pcm_to_wav
from safebridge_voice.brain import (
    REPORT_CONFIRMATION_CLARIFICATION_TEXT,
    ConversationHistory,
    SentenceSegment,
    confirmation_intent,
    stream_brain_turn,
)
from safebridge_voice.emergency import recognize_emergency
from safebridge_voice.language import (
    CLARIFICATION_TEXT, Transcription, normalize_provider_language, resolve_turn_language,
)
from safebridge_voice.tools import ToolContext
from safebridge_voice.protocol import ProtocolError, audio_segment_start, event, parse_control
from safebridge_voice.vad import EndpointDetector, EndpointResult, TurnState, VadConfig

PROJECT_ROOT=Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("safebridge")
app=FastAPI(title="SafeBridge Voice")
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

def server_config()->ServerConfig:
    """Load server-wide policy without exposing configuration values."""
    catalog=os.environ.get("SAFEBRIDGE_SAFETY_CATALOG","").strip()
    scope=os.environ.get("SAFEBRIDGE_USAGE_SCOPE","").strip()
    catalog_path=Path(catalog)
    if not catalog or not catalog_path.is_absolute() or scope not in ("operational","demo","reference_only"):
        raise RuntimeError("safety catalog configuration is incomplete")
    facility=os.environ.get("SAFEBRIDGE_FACILITY_ID","").strip() or None
    raw_allowed=os.environ.get("SAFEBRIDGE_ALLOWED_LANGUAGES","ko,en,vi")
    try:
        allowed=frozenset(normalize_session_language(value) for value in raw_allowed.split(",") if value.strip())
        default=normalize_session_language(os.environ.get("SAFEBRIDGE_SESSION_LANGUAGE","ko"))
    except ValueError as exc:
        raise RuntimeError("session language configuration is invalid") from exc
    if not allowed or default not in allowed:
        raise RuntimeError("session language configuration is invalid")
    return ServerConfig(catalog_path,facility,scope,allowed,default)

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
    kind:str; turn_id:int; result:EndpointResult

class ListenerSession:
    def __init__(self,detector:EndpointDetector|None=None,clock:Callable[[],float]=time.monotonic,
                 tool_context:ToolContext|None=None)->None:
        self.detector=detector or EndpointDetector(); self.clock=clock; self.active=False
        self.framer=FrameBuffer(); self.next_turn_id=1; self.active_turn_id=None
        self.cooldown_until=0.0; self.endpoint_at=0.0; self.history=ConversationHistory(); self.generation=0
        self.tool_context=tool_context
        self.language_mode="manual"
        self.manual_language=tool_context.language if tool_context else None
        self.last_confirmed_language=None
    @property
    def state(self): return self.detector.state
    def start(self):
        self.generation+=1
        self.active=True; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self.detector.reset(); self.history.reset()
        self.last_confirmed_language=None
    def stop(self):
        self.generation+=1
        self.active=False; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self.detector.reset(); self.history.reset()
        self.last_confirmed_language=None
    def set_tool_context(self,context:ToolContext)->None:
        """Change trusted language and clear all language-sensitive session state."""
        self.generation+=1
        self.tool_context=context
        self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self.detector.reset(); self.history.reset()
        self.language_mode="manual"; self.manual_language=context.language
        self.last_confirmed_language=None
    def set_language_mode(self,mode:str,context:ToolContext|None=None)->None:
        if mode=="manual":
            if context is None: raise ValueError("manual mode requires context")
            self.set_tool_context(context)
            return
        if mode!="auto": raise ValueError("invalid language mode")
        self.language_mode="auto"; self.manual_language=None
    def reset_sensitive_state(self)->None:
        self.generation+=1; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self.detector.reset(); self.history.reset()
        self.last_confirmed_language=None
    def is_current(self,turn_id:int,generation:int)->bool:
        return self.active and self.generation==generation and self.active_turn_id==turn_id
    def refresh_cooldown(self):
        if self.state==TurnState.COOLDOWN and self.clock()>=self.cooldown_until:
            self.active_turn_id=None; self.framer=FrameBuffer(); self.detector.reset(); return True
        return False
    def accept_chunk(self,chunk:bytes)->list[ListenerEvent]:
        if not self.active:return []
        self.refresh_cooldown()
        if self.state not in (TurnState.IDLE,TurnState.USER_SPEAKING):return []
        output=[]
        for frame in self.framer.push(chunk):
            result=self.detector.process(frame)
            if result.speech_started:
                self.active_turn_id=self.next_turn_id; self.next_turn_id+=1
                output.append(ListenerEvent("speech.start",self.active_turn_id,result))
            if result.rejected:
                output.append(ListenerEvent("speech.rejected",self.active_turn_id or 0,result))
                self.active_turn_id=None; self.framer=FrameBuffer()
            elif result.utterance is not None:
                if self.active_turn_id is None: raise RuntimeError("committed utterance has no turn_id")
                self.endpoint_at=self.clock()
                output.append(ListenerEvent("speech.end",self.active_turn_id,result)); self.framer=FrameBuffer()
            if self.state not in (TurnState.IDLE,TurnState.USER_SPEAKING): break
        return output
    def start_playback(self,turn_id:int)->bool:
        if self.state!=TurnState.PROCESSING or turn_id!=self.active_turn_id:return False
        self.detector.state=TurnState.AGENT_SPEAKING; return True
    def playback_ended(self,turn_id:int)->bool:
        if self.state!=TurnState.AGENT_SPEAKING or turn_id!=self.active_turn_id:return False
        self.detector.reset(TurnState.COOLDOWN); self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000
        self.framer=FrameBuffer(); return True
    def cascade_failed(self,turn_id:int):
        if turn_id==self.active_turn_id:
            self.active_turn_id=None; self.framer=FrameBuffer(); self.detector.reset()
    def reject_empty_transcript(self,turn_id:int)->bool:
        if self.state!=TurnState.PROCESSING or turn_id!=self.active_turn_id:return False
        self.active_turn_id=None; self.framer=FrameBuffer(); self.detector.reset(TurnState.COOLDOWN)
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000; return True
    def complete_without_playback(self,turn_id:int)->bool:
        if self.state!=TurnState.PROCESSING or turn_id!=self.active_turn_id:return False
        self.active_turn_id=None; self.framer=FrameBuffer(); self.detector.reset(TurnState.COOLDOWN)
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000; return True

class LockedSender:
    def __init__(self,websocket): self.websocket=websocket; self.lock=asyncio.Lock()
    async def text(self,kind:str,**fields):
        async with self.lock: await self.websocket.send_text(event(kind,**fields))
    async def segment(self,turn_id:int,index:int,frames:list[bytes]):
        async with self.lock:
            await self.websocket.send_text(audio_segment_start(turn_id,index,len(frames)))
            for frame in frames: await self.websocket.send_bytes(frame)
            await self.websocket.send_text(event("audio.segment.end",turn_id=turn_id,segment_index=index))

async def run_turn(websocket:WebSocket,session:ListenerSession,source_pcm:bytes,turn_id:int,
                   input_frames:int,voiced_frames:int=0,clock:Callable[[],float]=time.monotonic)->None:
    sender=LockedSender(websocket); endpoint=session.endpoint_at or clock(); timings={}; generation=session.generation
    async def current_text(kind:str,**fields)->bool:
        if not session.is_current(turn_id,generation): return False
        await sender.text(kind,**fields); return True
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
    emergency=recognize_emergency(transcript)
    if emergency is not None:
        text=emergency.response
        if not await current_text("reply.delta",turn_id=turn_id,segment_index=0,text=text): return
        try:
            pcm=await asyncio.to_thread(synthesize,text,emergency.language)
            frames=frame_complete_audio(pcm)
        except Exception:
            log.exception("emergency TTS failed")
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
            await current_text("state.changed",state=session.state.value,turn_id=turn_id)
            await sender.segment(turn_id,0,frames)
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
            try:
                pcm=await asyncio.to_thread(synthesize,text,fallback)
                frames=frame_complete_audio(pcm)
            except Exception:
                log.exception("language clarification TTS failed")
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
                await current_text("state.changed",state=session.state.value,turn_id=turn_id)
                await sender.segment(turn_id,0,frames)
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
    if session.tool_context is None: raise RuntimeError("trusted Tool context is required")
    turn_context=ToolContext(session.tool_context.catalog_path,session.tool_context.facility_id,
                             turn_language,session.tool_context.usage_scope,
                             session.tool_context.report_language)
    queue=asyncio.Queue(); output_frames=0; segment_count=0; first_token=False; first_sentence=False; first_audio=False
    def mark_token():
        nonlocal first_token
        if not first_token: first_token=True; timings["first_grok_token_ms"]=round((clock()-endpoint)*1000)
    async def sentence(segment:SentenceSegment):
        nonlocal first_sentence
        if not first_sentence: first_sentence=True; timings["first_sentence_ms"]=round((clock()-endpoint)*1000)
        if not await current_text("reply.delta",turn_id=turn_id,segment_index=segment.segment_index,text=segment.text): return
        await queue.put(segment)
    async def tool_event(kind,fields):
        if not await current_text(kind,turn_id=turn_id,**fields): return
        log.info("%s turn_id=%s tool=%s status=%s elapsed_ms=%s",kind,turn_id,fields.get("tool"),fields.get("status"),fields.get("elapsed_ms"))
    async def consume():
        nonlocal output_frames,segment_count,first_audio
        while True:
            segment=await queue.get()
            if segment is None:return
            if "first_tts_request_ms" not in timings: timings["first_tts_request_ms"]=round((clock()-endpoint)*1000)
            pcm=await asyncio.to_thread(synthesize,segment.text,turn_language); frames=frame_complete_audio(pcm)
            if not session.is_current(turn_id,generation):return
            if not first_audio:
                if not session.start_playback(turn_id):return
                first_audio=True; timings["first_audio_ms"]=round((clock()-endpoint)*1000)
                if not await current_text("state.changed",state=session.state.value,turn_id=turn_id): return
            if not session.is_current(turn_id,generation): return
            await sender.segment(turn_id,segment.segment_index,frames)
            output_frames+=len(frames); segment_count+=1
    consumer=asyncio.create_task(consume())
    try:
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
    try: await run_turn(websocket,session,source_pcm,turn_id,input_frames,voiced_frames)
    except asyncio.CancelledError: session.cascade_failed(turn_id); raise
    except WebSocketDisconnect: session.cascade_failed(turn_id)
    except Exception as exc:
        log.exception("voice turn failed"); session.cascade_failed(turn_id)
        await websocket.send_text(event("error",turn_id=turn_id,message=str(exc)))
        await websocket.send_text(event("state.changed",state=session.state.value,turn_id=turn_id))

@app.websocket("/ws")
async def voice_socket(websocket:WebSocket):
    await websocket.accept(); config=VadConfig(); session=ListenerSession(EndpointDetector(config)); task=None; trusted_config=None
    await websocket.send_text(event("ready",sample_rate=16000,frame_ms=20,frame_bytes=FRAME_BYTES,vad_mode=config.mode,endpoint_silence_ms=config.endpoint_silence_frames*20,prefix_padding_ms=config.prefix_frames*20))
    try:
        while True:
            message=await websocket.receive()
            if message.get("type")=="websocket.disconnect": break
            if message.get("bytes") is not None:
                if session.refresh_cooldown(): await websocket.send_text(event("state.changed",state="IDLE"))
                for item in session.accept_chunk(message["bytes"]):
                    await websocket.send_text(event(item.kind,turn_id=item.turn_id,state=session.state.value,voiced_frames=item.result.voiced_frames,total_frames=item.result.total_frames,duration_ms=item.result.total_frames*20,reason=item.result.rejection_reason,forced=item.result.forced))
                    if item.kind=="speech.end":
                        task=asyncio.create_task(run_turn_safely(websocket,session,item.result.utterance or b"",item.turn_id,item.result.total_frames,item.result.voiced_frames))
                continue
            if message.get("text") is None:continue
            control=parse_control(message["text"])
            if control["type"]=="session.start":
                if session.active:raise ProtocolError("session already active")
                try:
                    trusted_config=trusted_config or server_config()
                    session.tool_context=server_tool_context(trusted_config,control.get("language"))
                except (RuntimeError,ValueError):
                    await websocket.send_text(event("error",message="invalid session configuration"))
                    continue
                session.start(); await websocket.send_text(event("session.started",state=session.state.value))
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.set_language":
                if not session.active:
                    await websocket.send_text(event("error",message="session is not active"))
                    continue
                try:
                    trusted_config=trusted_config or server_config()
                    context=server_tool_context(trusted_config,control["language"])
                except (RuntimeError,ValueError):
                    await websocket.send_text(event("error",message="invalid session language"))
                    continue
                if task and not task.done(): task.cancel()
                session.set_tool_context(context)
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
                    session.set_language_mode(control["mode"],context)
                except (RuntimeError,ValueError):
                    await websocket.send_text(event("error",message="invalid language mode"))
                    continue
                if task and not task.done(): task.cancel()
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.reset":
                if not session.active:
                    await websocket.send_text(event("error",message="session is not active"))
                    continue
                if task and not task.done(): task.cancel()
                session.reset_sensitive_state()
                await websocket.send_text(event("session.reset",state=session.state.value))
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.stop":
                if task and not task.done(): task.cancel()
                session.stop(); await websocket.send_text(event("session.stopped",state=session.state.value))
            elif control["type"]=="playback.ended" and session.playback_ended(control["turn_id"]):
                await websocket.send_text(event("state.changed",state=session.state.value,turn_id=control["turn_id"],cooldown_ms=config.cooldown_ms))
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
        session.stop()

app.mount("/",StaticFiles(directory=STATIC_DIR,html=True),name="static")
