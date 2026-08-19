"""JSON helpers for M4 segmented audio."""
import json
import re
from typing import Any

class ProtocolError(ValueError): pass

REPORT_ID_PATTERN=re.compile(r"^SR-[0-9]{8}-[0-9A-F]{6}$")

def parse_control(raw: str) -> dict[str, Any]:
    try: message=json.loads(raw)
    except json.JSONDecodeError as exc: raise ProtocolError("control message must be valid JSON") from exc
    if not isinstance(message,dict) or not isinstance(message.get("type"),str): raise ProtocolError("control message needs a string type")
    if message["type"]=="session.start":
        mode=message.get("mode")
        legacy_pipeline=message.get("pipeline")
        if mode is not None and legacy_pipeline is not None and mode!=legacy_pipeline:
            raise ProtocolError("session.start mode and pipeline disagree")
        mode=mode if mode is not None else legacy_pipeline
        if mode is None:
            mode = "cascade"
        if mode != "cascade":
            raise ProtocolError("session.start mode must be cascade")
        language=message.get("language")
        if not isinstance(language,str) or not language.strip():
            raise ProtocolError("session.start language must be a non-empty string")
        if "protocol_id" not in message:
            raise ProtocolError("session.start needs an explicit protocol_id")
        protocol_id=message["protocol_id"]
        if protocol_id is not None and (
            not isinstance(protocol_id,str)
            or not protocol_id
            or protocol_id!=protocol_id.strip()
        ):
            raise ProtocolError("session.start protocol_id must be null or an exact non-empty string")
        configuration_id=message.get("configuration_id")
        if (not isinstance(configuration_id,int) or isinstance(configuration_id,bool)
                or configuration_id<=0):
            raise ProtocolError("session.start needs a positive configuration_id")
        return {
            "type":"session.start",
            "mode":mode,
            "language":language,
            "protocol_id":protocol_id,
            "configuration_id":configuration_id,
        }
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
    if message["type"]=="report.status.get":
        report_id=message.get("report_id")
        if not isinstance(report_id,str):
            raise ProtocolError("report.status.get needs a report_id")
        normalized=report_id.strip().upper()
        if REPORT_ID_PATTERN.fullmatch(normalized) is None:
            raise ProtocolError("report.status.get report_id is invalid")
        return {"type":"report.status.get","report_id":normalized}
    if message["type"] in {"experiment.report.get", "experiment.report.status.get"}:
        report_id = message.get("report_id")
        configuration_id = message.get("configuration_id")
        return {
            "type": message["type"],
            "report_id": report_id,
            "configuration_id": configuration_id,
        }
    if message["type"]=="playback.ended":
        turn_id=message.get("turn_id")
        if not isinstance(turn_id,int) or isinstance(turn_id,bool) or turn_id<=0: raise ProtocolError("playback.ended needs a positive integer turn_id")
        return {"type":"playback.ended","turn_id":turn_id}
    if message["type"]=="client.audio_constraints":
        requested=message.get("requested")
        actual=message.get("actual")
        names={"echoCancellation","noiseSuppression","autoGainControl"}
        if (not isinstance(requested,dict) or set(requested)!=names or
                any(not isinstance(requested[name],bool) for name in names) or
                not isinstance(actual,dict) or set(actual)!=names or
                any(actual[name] is not None and
                    not isinstance(actual[name],bool) for name in names)):
            raise ProtocolError("client.audio_constraints metadata is invalid")
        return {
            "type":"client.audio_constraints",
            "requested":{name:requested[name] for name in sorted(names)},
            "actual":{name:actual[name] for name in sorted(names)},
        }
    if message["type"]=="client.audio_ready":
        configuration_id=message.get("configuration_id")
        generation=message.get("generation")
        state=message.get("audio_context_state")
        sample_rate=message.get("sample_rate")
        if (not isinstance(configuration_id,int) or isinstance(configuration_id,bool)
                or configuration_id<=0 or
                not isinstance(generation,int) or isinstance(generation,bool)
                or generation<0 or state!="running" or
                not isinstance(sample_rate,int) or isinstance(sample_rate,bool)
                or sample_rate<=0):
            raise ProtocolError("client.audio_ready metadata is invalid")
        return {
            "type":"client.audio_ready","configuration_id":configuration_id,
            "generation":generation,"audio_context_state":state,
            "sample_rate":sample_rate,
        }
    raise ProtocolError(f"unknown control type: {message.get('type')}")

def event(event_type: str, **fields: Any) -> str:
    return json.dumps({"type":event_type,**fields},ensure_ascii=False,separators=(",",":"))

def audio_segment_start(
    turn_id:int,segment_index:int,frame_count:int,sample_rate:int=16000,
    generation:int|None=None,
)->str:
    if (turn_id<=0 or segment_index<0 or frame_count<0 or
            (generation is not None and
             (not isinstance(generation,int) or isinstance(generation,bool)
              or generation<0))):
        raise ProtocolError("invalid outbound audio metadata")
    fields={
        "turn_id":turn_id,"segment_index":segment_index,
        "frame_count":frame_count,"sample_rate":sample_rate,
        "encoding":"pcm_s16le","frame_ms":20,
    }
    if generation is not None: fields["generation"]=generation
    return event("audio.segment.start",**fields)

def audio_start(stream:str,frame_count:int,turn_id:int,sample_rate:int=16000)->str:
    if not stream: raise ProtocolError("invalid outbound audio metadata")
    return audio_segment_start(turn_id,0,frame_count,sample_rate)
