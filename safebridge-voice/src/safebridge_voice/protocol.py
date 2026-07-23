"""JSON helpers for M4 segmented audio."""
import json
from typing import Any

class ProtocolError(ValueError): pass

def parse_control(raw: str) -> dict[str, Any]:
    try: message=json.loads(raw)
    except json.JSONDecodeError as exc: raise ProtocolError("control message must be valid JSON") from exc
    if not isinstance(message,dict) or not isinstance(message.get("type"),str): raise ProtocolError("control message needs a string type")
    if message["type"]=="session.start":
        language=message.get("language")
        if language is not None and not isinstance(language,str):
            raise ProtocolError("session.start language must be a string")
        return {"type":"session.start",**({"language":language} if language is not None else {})}
    if message["type"]=="session.set_language":
        language=message.get("language")
        if not isinstance(language,str): raise ProtocolError("session.set_language needs a string language")
        return {"type":"session.set_language","language":language}
    if message["type"]=="session.set_language_mode":
        mode=message.get("mode")
        if mode not in ("auto","manual"):
            raise ProtocolError("session.set_language_mode needs auto or manual mode")
        language=message.get("language")
        if mode=="manual" and not isinstance(language,str):
            raise ProtocolError("manual language mode needs a string language")
        if mode=="auto" and language is not None:
            raise ProtocolError("automatic language mode cannot include language")
        return {"type":"session.set_language_mode","mode":mode,
                **({"language":language} if mode=="manual" else {})}
    if message["type"]=="session.reset": return {"type":"session.reset"}
    if message["type"]=="session.stop": return {"type":"session.stop"}
    if message["type"]=="playback.ended":
        turn_id=message.get("turn_id")
        if not isinstance(turn_id,int) or isinstance(turn_id,bool) or turn_id<=0: raise ProtocolError("playback.ended needs a positive integer turn_id")
        return {"type":"playback.ended","turn_id":turn_id}
    raise ProtocolError(f"unknown control type: {message.get("type")}")

def event(event_type: str, **fields: Any) -> str:
    return json.dumps({"type":event_type,**fields},ensure_ascii=False,separators=(",",":"))

def audio_segment_start(turn_id:int,segment_index:int,frame_count:int,sample_rate:int=16000)->str:
    if turn_id<=0 or segment_index<0 or frame_count<0: raise ProtocolError("invalid outbound audio metadata")
    return event("audio.segment.start",turn_id=turn_id,segment_index=segment_index,frame_count=frame_count,sample_rate=sample_rate,encoding="pcm_s16le",frame_ms=20)

def audio_start(stream:str,frame_count:int,turn_id:int,sample_rate:int=16000)->str:
    if not stream: raise ProtocolError("invalid outbound audio metadata")
    return audio_segment_start(turn_id,0,frame_count,sample_rate)
