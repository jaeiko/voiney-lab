"""Voice Workflow Agent: hands-free voice cascade with M2 Dispatcher tools."""
from __future__ import annotations
import asyncio, hashlib, logging, math, os, secrets, sqlite3, tempfile, textwrap, time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI, OpenAI
from voice_workflow_agent.audio import FRAME_BYTES, FrameBuffer, clean_path, pcm_to_wav
from voice_workflow_agent.brain import (
    REPORT_CONFIRMATION_CLARIFICATION_TEXT,
    ConversationHistory,
    SentenceSegment,
    answer_approved_reference_question,
    answer_curated_protocol_question,
    confirmation_intent,
    stream_brain_turn,
)
from voice_workflow_agent.configuration import (
    ConfigurationError,
    VoiceVadSettings,
    cascade_filler_delay_ms,
)
from voice_workflow_agent.cascade_filler import CascadeFiller
from voice_workflow_agent.curated_protocol import (
    CuratedProtocolAction,
    CuratedProtocolFixture,
    CuratedProtocolSession,
    CuratedProtocolSpeechMode,
    ProtocolVisualKind,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_protocol_analysis import (
    OpenAICompatibleProtocolAnalysisModel,
)
from voice_workflow_agent.experiment_protocol_config import (
    ProtocolConfigurationError,
    ProtocolFeatureDisabledError,
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    MAX_PROTOCOL_PDF_BYTES,
    ProtocolPdfEncryptedError,
    ProtocolPdfError,
    ProtocolPdfMalformedError,
    ProtocolPdfTooLargeError,
    ProtocolPdfTypeError,
    extract_protocol_pdf,
)
from voice_workflow_agent.experiment_protocol_store import (
    PROTOCOL_DATABASE_FILENAME,
    initialize_protocol_store,
)
from voice_workflow_agent.experiment_reports import (
    ExperimentReportSettings,
    ExperimentReportStore,
    new_session_id,
)
from voice_workflow_agent.external_references import (
    ExternalReferenceSettings,
    XaiAuthoritativeWebSearch,
    plan_research_query,
)
from voice_workflow_agent.generated_visuals import (
    GENERATED_VISUALS,
    GeneratedVisualSettings,
    VisualSpecification,
    XaiImageGenerator,
)
from voice_workflow_agent.web_visuals import (
    WebVisualSettings,
    XaiAuthoritativeImageSearch,
)
from voice_workflow_agent.protocol_catalog import (
    ProtocolApprovalError,
    ProtocolCatalog,
    ProtocolCatalogError,
    ProtocolCatalogNotFoundError,
    ProtocolCatalogUnavailableError,
    ProtocolRegistrationError,
    SharedSecretApprovalPolicy,
)
from voice_workflow_agent.document_store import CATALOG_SCHEMA_VERSION
from voice_workflow_agent.emergency import recognize_emergency
from voice_workflow_agent.language import (
    CLARIFICATION_TEXT, Transcription, normalize_provider_language,
    resolve_turn_language, transcription_quality_issue,
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
    APPROVED_LAB_REFERENCE_TOOL_NAME,
    COMPLETE_CURRENT_STEP_TOOL_NAME,
    CREATE_REPORT_TOOL_NAME,
    GET_CURRENT_STEP_TOOL_NAME,
    PROCEDURE_TOOL_NAMES,
    RECORD_STEP_OBSERVATION_TOOL_NAME,
    START_STEP_TIMER_TOOL_NAME,
    ToolContext,
    check_safety_report_status,
    execute_tool,
    search_approved_lab_references,
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
    await asyncio.to_thread(log_protocol_catalog_runtime_configuration)
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


def _protocol_store_settings()->ProtocolPersistenceSettings:
    return ProtocolPersistenceSettings.from_environment()


def _open_protocol_catalog()->tuple[ProtocolCatalog,object]:
    settings=_protocol_store_settings()
    if not settings.enabled:
        raise ProtocolCatalogUnavailableError("Protocol catalog is disabled.")
    store=initialize_protocol_store(settings)
    return ProtocolCatalog(store),store

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
    confidence=payload.get("confidence")
    if not isinstance(confidence,(int,float)) or isinstance(confidence,bool) or not 0<=confidence<=1:
        confidence=None
    no_speech=payload.get("no_speech_probability")
    if not isinstance(no_speech,(int,float)) or isinstance(no_speech,bool) or not 0<=no_speech<=1:
        no_speech=None
    alternatives=payload.get("alternatives",())
    if not isinstance(alternatives,list):
        alternatives=()
    else:
        alternatives=tuple(
            value.strip() for value in alternatives[:3]
            if isinstance(value,str) and value.strip()
        )
    return Transcription(
        text.strip() if isinstance(text,str) else "",
        normalize_provider_language(payload.get("language")),
        float(confidence) if confidence is not None else None,
        float(no_speech) if no_speech is not None else None,
        alternatives,
    )

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


def _configured_candidate_fixture(config:ServerConfig)->CuratedProtocolFixture|None:
    if config.curated_protocol_fixture_path is None:
        return None
    return load_curated_protocol_fixture(
        config.curated_protocol_fixture_path,
        config.curated_protocol_provenance_path,
        config.curated_protocol_source_pdf_path,
    )


def _candidate_catalog_dict(fixture:CuratedProtocolFixture)->dict[str,object]:
    return {
        "protocol_id":fixture.protocol_id,
        "title":fixture.title,
        "source_filename":fixture.source_pdf_path.name if fixture.source_pdf_path else "source.pdf",
        "source_sha256":fixture.source_pdf_sha256,
        "revision_id":fixture.revision_id,
        "readiness_status":fixture.draft.readiness.status.value,
        "approval_status":"development_only_not_final_acceptance",
        "analysis_status":"validated_curated_fixture",
        "step_count":len(fixture.steps),
        "created_at":None,
        "available_for_execution":True,
        "development_only":True,
    }


def _public_protocol_catalog_entries(
)->tuple[list[dict[str,object]],ProtocolPersistenceSettings,bool]:
    """Resolve visible catalog entries without analysis or approval side effects."""

    config=server_config()
    candidate=_configured_candidate_fixture(config)
    entries=[]
    if candidate is not None:
        entries.append(_candidate_catalog_dict(candidate))
    settings=_protocol_store_settings()
    if not settings.enabled:
        return entries,settings,candidate is not None
    catalog,store=_open_protocol_catalog()
    try:
        for item in catalog.list_entries():
            if candidate is not None and item.protocol_id==candidate.protocol_id:
                if catalog.development_fixture_is_materialized(candidate):
                    continue
                raise ProtocolCatalogUnavailableError(
                    "Configured development fixture conflicts with catalog state."
                )
            public=item.public_dict()
            public["analysis_run"]=catalog.analysis_run_status(
                item.protocol_id).public_dict()
            entries.append(public)
    finally:
        store.close()
    return entries,settings,candidate is not None


def log_protocol_catalog_runtime_configuration()->None:
    """Log only sanitized protocol backend identity and visible entry count."""

    try:
        entries,settings,development_fixture_enabled=(
            _public_protocol_catalog_entries()
        )
        if settings.enabled and settings.data_dir is not None:
            data_dir=settings.data_dir.resolve()
            backend="sqlite+curated_fixture" if development_fixture_enabled else "sqlite"
            catalog_path=str(data_dir/PROTOCOL_DATABASE_FILENAME)
            asset_root=str(data_dir/"objects"/"sha256")
        else:
            backend="curated_fixture_only" if development_fixture_enabled else "disabled"
            catalog_path="disabled"
            asset_root="disabled"
        log.info(
            "protocol.catalog.configuration backend=%s catalog_path=%s "
            "asset_root=%s visible_protocols=%d development_fixtures_enabled=%s",
            backend,catalog_path,asset_root,len(entries),development_fixture_enabled,
        )
    except Exception as exc:
        log.warning(
            "protocol.catalog.configuration unavailable error=%s",
            type(exc).__name__,
        )


def _catalog_http_error(exc:Exception)->HTTPException:
    if isinstance(exc,ProtocolPdfTooLargeError):
        return HTTPException(status_code=413,detail="protocol_pdf_too_large")
    if isinstance(exc,ProtocolPdfTypeError):
        return HTTPException(status_code=415,detail="unsupported_pdf_media_type")
    if isinstance(exc,ProtocolPdfMalformedError):
        return HTTPException(status_code=422,detail="invalid_pdf")
    if isinstance(exc,ProtocolPdfEncryptedError):
        return HTTPException(status_code=422,detail="encrypted_pdf")
    if isinstance(exc,ProtocolPdfError):
        return HTTPException(status_code=422,detail="invalid_pdf")
    if isinstance(
        exc,
        (
            ProtocolCatalogUnavailableError,
            ProtocolConfigurationError,
            ProtocolFeatureDisabledError,
        ),
    ):
        return HTTPException(status_code=503,detail="protocol_catalog_unavailable")
    if isinstance(exc,ProtocolCatalogNotFoundError):
        return HTTPException(status_code=404,detail=getattr(exc,"code","not_found"))
    if isinstance(exc,ProtocolApprovalError):
        return HTTPException(status_code=403,detail=exc.code)
    if isinstance(exc,ProtocolRegistrationError):
        return HTTPException(status_code=400,detail=exc.code)
    return HTTPException(
        status_code=409 if isinstance(exc,ProtocolCatalogError) else 500,
        detail=getattr(exc,"code","protocol_catalog_error"),
    )


async def _spool_protocol_pdf_upload(
    request:Request,
    destination:Path,
    *,
    max_bytes:int|None=None,
)->int:
    """Write one bounded raw PDF request without buffering it in memory."""

    limit=MAX_PROTOCOL_PDF_BYTES if max_bytes is None else max_bytes
    byte_size=0
    try:
        with destination.open("xb") as stream:
            async for chunk in request.stream():
                if not chunk:
                    continue
                byte_size+=len(chunk)
                if byte_size>limit:
                    raise HTTPException(
                        status_code=413,detail="protocol_pdf_too_large")
                stream.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    if byte_size==0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422,detail="invalid_pdf")
    return byte_size


@app.get("/api/protocols")
def list_protocol_catalog()->dict[str,object]:
    """Read catalog metadata only; never trigger automated analysis."""

    try:
        entries,_,_=_public_protocol_catalog_entries()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc
    return {"protocols":entries}


@app.get("/api/protocols/{protocol_id}")
def get_protocol_catalog_entry(protocol_id:str)->dict[str,object]:
    try:
        config=server_config()
        candidate=_configured_candidate_fixture(config)
        if candidate is not None and candidate.protocol_id==protocol_id:
            return _candidate_catalog_dict(candidate)
        catalog,store=_open_protocol_catalog()
        try:
            public=catalog.get_entry(protocol_id).public_dict()
            public["analysis_run"]=catalog.analysis_run_status(
                protocol_id).public_dict()
            return public
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols",status_code=201)
async def register_protocol_pdf(request:Request,filename:str)->dict[str,object]:
    media_type=request.headers.get("content-type","").casefold().split(";",1)[0].strip()
    if media_type!=PDF_MEDIA_TYPE:
        raise HTTPException(status_code=415,detail="unsupported_pdf_media_type")
    length=request.headers.get("content-length")
    if length is not None:
        try:
            parsed_length=int(length)
            if parsed_length<0:
                raise ValueError
            if parsed_length>MAX_PROTOCOL_PDF_BYTES:
                raise HTTPException(
                    status_code=413,detail="protocol_pdf_too_large")
        except ValueError as exc:
            raise HTTPException(status_code=400,detail="invalid_content_length") from exc
    with tempfile.TemporaryDirectory(prefix="protocol-registration-") as root:
        temporary=Path(root)/"upload.pdf"
        await _spool_protocol_pdf_upload(request,temporary)
        try:
            catalog,store=_open_protocol_catalog()
            try:
                result=catalog.register(
                    temporary,
                    source_filename=filename,
                    media_type=media_type,
                )
                return {
                    "protocol":result.entry.public_dict(),
                    "deduplicated":result.deduplicated,
                }
            finally:
                store.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise _catalog_http_error(exc) from exc


def _protocol_analysis_model()->OpenAICompatibleProtocolAnalysisModel:
    client=OpenAI(
        base_url=api_url(""),api_key=require_env("XAI_API_KEY"),
        max_retries=0,timeout=120.0)
    return OpenAICompatibleProtocolAnalysisModel(
        client,require_env("PROTOCOL_ANALYSIS_MODEL"))


@app.post("/api/protocols/{protocol_id}/analysis")
async def trigger_protocol_analysis(protocol_id:str)->dict[str,object]:
    """The only HTTP boundary that may explicitly request PDF analysis."""

    def run_explicit_analysis()->dict[str,object]:
        # SQLite connections are thread-affine.  Construct and close the
        # catalog in the same worker that performs bounded Provider work.
        catalog,store=_open_protocol_catalog()
        try:
            entry=catalog.analyze(
                protocol_id,
                _protocol_analysis_model(),
                analysis_id=f"analysis-{secrets.token_hex(16)}",
            )
            public=entry.public_dict()
            public["analysis_run"]=catalog.analysis_run_status(
                protocol_id).public_dict()
            return public
        finally:
            store.close()

    try:
        return await asyncio.to_thread(run_explicit_analysis)
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}/analysis/status")
def get_protocol_analysis_status(protocol_id:str)->dict[str,object]:
    """Read persisted lifecycle state without starting or resuming analysis."""

    try:
        catalog,store=_open_protocol_catalog()
        try:
            return catalog.analysis_run_status(protocol_id).public_dict()
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/revisions/{revision_id}/approve")
def approve_protocol_revision(
    protocol_id:str,
    revision_id:str,
    x_protocol_approval_token:str|None=Header(default=None),
)->dict[str,object]:
    """Service-authorized approval; deliberately absent from the public UI."""

    try:
        catalog,store=_open_protocol_catalog()
        try:
            policy=SharedSecretApprovalPolicy(
                os.environ.get("VOICE_WORKFLOW_AGENT_PROTOCOL_APPROVAL_TOKEN"))
            return catalog.approve(
                protocol_id,
                revision_id,
                policy=policy,
                presented_secret=x_protocol_approval_token,
            ).public_dict()
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}/revisions/{revision_id}/assets/{asset_id}")
def get_protocol_visual_asset(
    protocol_id:str,revision_id:str,asset_id:str,
):
    try:
        config=server_config()
        candidate=_configured_candidate_fixture(config)
        if candidate is not None and candidate.protocol_id==protocol_id:
            if candidate.revision_id!=revision_id:
                raise ProtocolCatalogNotFoundError("Protocol revision is unknown.")
            matches=tuple(
                (index,asset) for index in range(len(candidate.steps))
                if (asset:=candidate.visual_for_step(index)) is not None
                and asset.asset_id==asset_id)
            if len(matches)!=1 or candidate.source_pdf_path is None:
                raise ProtocolCatalogNotFoundError("Protocol visual asset is unknown.")
            index,asset=matches[0]
            resolved_asset,content=candidate.visual_content(index)
            if resolved_asset!=asset:
                raise ProtocolCatalogUnavailableError(
                    "Protocol visual identity changed.")
            return Response(
                content=content,
                media_type=asset.mime_type,
                headers={
                    "Cache-Control":"private, no-store",
                    "X-Content-Type-Options":"nosniff",
                    "Content-Security-Policy":(
                        "default-src 'none'; style-src 'unsafe-inline'; sandbox"),
                    "Content-Disposition":(
                        f'inline; filename="{asset.asset_id}"'),
                    "X-Protocol-Source-SHA256":asset.source_document_id,
                    "X-Protocol-Asset-SHA256":asset.sha256,
                    "X-Protocol-Source-Page":str(asset.source_page),
                    "X-Protocol-Visual-Kind":asset.kind,
                },
            )
        else:
            catalog,store=_open_protocol_catalog()
            try:
                fixture=catalog.load_executable_fixture(protocol_id)
                if fixture.revision_id!=revision_id:
                    raise ProtocolCatalogNotFoundError(
                        "Protocol revision is unknown.")
                matches=tuple(
                    (index,asset) for index in range(len(fixture.steps))
                    if (asset:=fixture.visual_for_step(index)) is not None
                    and asset.asset_id==asset_id)
                if len(matches)!=1:
                    raise ProtocolCatalogNotFoundError(
                        "Protocol visual asset is unknown.")
                index,asset=matches[0]
                resolved_asset,content=fixture.visual_content(index)
                if resolved_asset!=asset:
                    raise ProtocolCatalogUnavailableError(
                        "Protocol visual identity changed.")
            finally:
                store.close()
        return Response(
            content=content,
            media_type=asset.mime_type,
            headers={
                "Cache-Control":"private, no-store",
                "X-Content-Type-Options":"nosniff",
                "Content-Security-Policy":(
                    "default-src 'none'; style-src 'unsafe-inline'; sandbox"),
                "Content-Disposition":(
                    f'inline; filename="{asset.asset_id}"'),
                "X-Protocol-Source-SHA256":asset.source_document_id,
                "X-Protocol-Asset-SHA256":asset.sha256,
                "X-Protocol-Source-Page":str(asset.source_page),
                "X-Protocol-Visual-Kind":asset.kind,
            },
        )
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/generated-visuals/{asset_id}")
def get_generated_visual_asset(asset_id:str):
    """Serve one validated generated image through an opaque same-origin ID."""

    asset=GENERATED_VISUALS.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404,detail="Generated visual is unknown.")
    return Response(
        content=asset.content,media_type=asset.mime_type,
        headers={
            "Cache-Control":"private, max-age=3600, immutable",
            "X-Content-Type-Options":"nosniff",
            "Content-Security-Policy":"default-src 'none'; sandbox",
            "Content-Disposition":f'inline; filename="{asset.asset_id}"',
            "X-Generated-Visual-SHA256":asset.content_sha256,
            "X-Protocol-Source-SHA256":asset.source_document_hash,
            "X-Protocol-Visual-Kind":"generated_instructional",
        },
    )


@app.get("/api/experiment-reports/{report_id}.{format_name}")
def export_experiment_report(report_id:str,format_name:str):
    """Export one configured report without exposing its database location."""

    try:
        settings=ExperimentReportSettings.from_environment()
        if not settings.enabled or settings.database_path is None:
            raise HTTPException(status_code=404,detail="experiment report unavailable")
        store=ExperimentReportStore(settings.database_path)
        if format_name=="json":
            content=store.export_json(report_id)
            media_type="application/json"
        elif format_name=="md":
            content=store.export_markdown(report_id)
            media_type="text/markdown; charset=utf-8"
        else:
            raise HTTPException(status_code=404,detail="experiment report unavailable")
    except HTTPException:
        raise
    except (ValueError,KeyError,RuntimeError,OSError,sqlite3.Error) as exc:
        raise HTTPException(
            status_code=404,detail="experiment report unavailable"
        ) from exc
    return Response(
        content=content,media_type=media_type,
        headers={
            "Cache-Control":"no-store",
            "Content-Disposition":f'attachment; filename="{report_id}.{format_name}"',
            "X-Content-Type-Options":"nosniff",
        },
    )


@app.get(
    "/api/protocols/{protocol_id}/revisions/{revision_id}/source-pages/{source_page}"
)
def get_protocol_source_page(
    protocol_id:str,revision_id:str,source_page:int,
):
    """Secondary same-origin exact-page view; it is never labelled a source image."""

    try:
        config=server_config()
        candidate=_configured_candidate_fixture(config)
        if candidate is not None and candidate.protocol_id==protocol_id:
            fixture=candidate
        else:
            catalog,store=_open_protocol_catalog()
            try:
                fixture=catalog.load_executable_fixture(protocol_id)
            finally:
                store.close()
        if (
            fixture.revision_id!=revision_id
            or fixture.source_pdf_path is None
            or fixture.source_pdf_sha256 is None
        ):
            raise ProtocolCatalogNotFoundError("Protocol source page is unknown.")
        extraction=extract_protocol_pdf(fixture.source_pdf_path)
        if extraction.sha256!=fixture.source_pdf_sha256:
            raise ProtocolCatalogUnavailableError(
                "Protocol source identity changed.")
        page=next((item for item in extraction.pages
                   if item.source_page_number==source_page),None)
        if page is None:
            raise ProtocolCatalogNotFoundError("Protocol source page is unknown.")
        lines=[]
        for raw_line in page.text.replace("\r\n","\n").replace("\r","\n").split("\n"):
            cleaned="".join(character for character in raw_line
                            if character in "\t" or ord(character)>=32)
            lines.extend(textwrap.wrap(
                cleaned,88,replace_whitespace=False,drop_whitespace=False) or [""])
        height=max(840,96+len(lines)*22)
        text_nodes="".join(
            f'<text x="48" y="{82+index*22}">{xml_escape(line)}</text>'
            for index,line in enumerate(lines))
        preview=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" '
            f'viewBox="0 0 900 {height}" role="img" aria-label="Source page {source_page}">'
            '<rect width="100%" height="100%" fill="#fffdf8"/>'
            f'<text x="48" y="42" font-family="sans-serif" font-size="18" '
            f'font-weight="700">Source page {source_page}</text>'
            '<g font-family="ui-monospace,monospace" font-size="15" fill="#17211b">'
            f'{text_nodes}</g></svg>').encode("utf-8")
        return Response(
            content=preview,media_type="image/svg+xml",
            headers={
                "Cache-Control":"private, no-store",
                "X-Content-Type-Options":"nosniff",
                "Content-Security-Policy":(
                    "default-src 'none'; style-src 'unsafe-inline'; sandbox"),
                "X-Protocol-Source-SHA256":extraction.sha256,
                "X-Protocol-Source-Page":str(source_page),
            })
    except Exception as exc:
        raise _catalog_http_error(exc) from exc

@dataclass(frozen=True)
class ListenerEvent:
    kind:str
    turn_id:int
    result:EndpointResult
    generation:int|None=None
    superseding_turn_id:int|None=None
    superseding_generation:int|None=None
    reason:str|None=None
    latency_ms:int|None=None
    diagnostics:dict[str,int|float|bool|str]|None=None

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
    "checking_protocol":frozenset({
        "checking_approved_information","synthesizing","cancelled","error",
    }),
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
                 curated_protocol_session:CuratedProtocolSession|None=None,
                 experiment_report_store:ExperimentReportStore|None=None,
                 external_reference_settings:ExternalReferenceSettings|None=None,
                 web_visual_settings:WebVisualSettings|None=None,
                 generated_visual_settings:GeneratedVisualSettings|None=None)->None:
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
        self.experiment_report_store=experiment_report_store
        self.external_reference_settings=(
            external_reference_settings or ExternalReferenceSettings(False))
        self.web_visual_settings=web_visual_settings or WebVisualSettings(False)
        self.generated_visual_settings=(
            generated_visual_settings or GeneratedVisualSettings(False))
        self.experiment_report_id:str|None=None
        self.session_id=new_session_id()
        self.accepted_configuration_id:int|None=None
        self.accepted_mode:str|None=None
        self.accepted_language:str|None=None
        self.accepted_protocol_id:str|None=None
        self.accepted_revision_id:str|None=None
        self.turn_generations:dict[int,int]={}
        self.turn_progress:dict[tuple[int,int],TurnProgress]={}
        self.visual_tasks:set[asyncio.Task]=set()
        self._interrupted_generations:set[tuple[int,int]]=set()
        self._cascade_vad_config=self.detector.config
        self._vad_classifier=self.detector.classifier
        self._listening_onset=self.detector.listening_onset
        self._interrupt_detector=self._new_interrupt_detector()
        self._interrupt_framer=FrameBuffer()
        self._interrupt_candidate_identity:tuple[int,int]|None=None
        self._interrupt_candidate_started_at:float|None=None
        self._interrupt_candidate_endpoint_at:float|None=None
        self._interrupt_candidate_diagnostics:dict[str,int|bool]={}
        self._microphone_chunk_sequence=0
    @property
    def state(self): return self.detector.state
    def _new_interrupt_detector(self,*,playback:bool=False)->EndpointDetector:
        config=self._cascade_vad_config
        if playback:
            config=replace(
                config,
                onset_voiced_frames=config.playback_onset_voiced_frames,
                onset_window_frames=config.playback_onset_window_frames,
                prefix_frames=config.barge_in_prefix_frames,
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
        self._interrupt_candidate_identity=None
        self._interrupt_candidate_started_at=None
        self._interrupt_candidate_endpoint_at=None
        self._interrupt_candidate_diagnostics={}
    def _reset_turn_identity(self)->None:
        for task in tuple(self.visual_tasks):
            task.cancel()
        self.visual_tasks.clear()
        self.turn_generations.clear()
        self.turn_progress.clear()
        self._interrupted_generations.clear()
        self._reset_interrupt_input()
    def owns_visual_result(
        self,turn_id:int,generation:int,configuration_id:int|None,
        protocol_id:str,
    )->bool:
        return bool(
            self.active
            and self.accepted_configuration_id==configuration_id
            and self.accepted_protocol_id==protocol_id
            and self.turn_generations.get(turn_id)==generation
            and (turn_id,generation) not in self._interrupted_generations
        )
    def track_visual_task(self,task:asyncio.Task)->None:
        self.visual_tasks.add(task)
        task.add_done_callback(self.visual_tasks.discard)
    def start(self):
        self.generation+=1
        self.active=True; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self._restore_primary_detector(TurnState.IDLE)
        self.history.reset()
        self.last_confirmed_language=None
        self.turn_committed_at.clear(); self.playback_completion_metrics.clear()
        self._reset_turn_identity()
        self.session_id=new_session_id()
        self.experiment_report_id=None
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
        self.accepted_revision_id=None
        self._reset_turn_identity()
        if self.curated_protocol_session is not None:
            self.curated_protocol_session.reset()
    def accept_configuration(
        self,configuration_id:int,mode:str,language:str,
        protocol_id:str|None,revision_id:str|None=None,
    )->None:
        """Record only the exact non-secret configuration accepted by the server."""
        self.accepted_configuration_id=configuration_id
        self.accepted_mode=mode
        self.accepted_language=language
        self.accepted_protocol_id=protocol_id
        self.accepted_revision_id=revision_id
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
        self._microphone_chunk_sequence+=1
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
        for frame in framer.push(chunk):
            result=detector.process(frame)
            if result.speech_started:
                interrupted_turn_id=self.active_turn_id
                if interrupted_turn_id is None:
                    self._reset_interrupt_input()
                    return []
                interrupted_generation=self.turn_generations.get(
                    interrupted_turn_id,self.generation)
                identity=(interrupted_turn_id,interrupted_generation)
                if identity in self._interrupted_generations:
                    continue
                self._interrupt_candidate_identity=identity
                self._interrupt_candidate_started_at=self.clock()
                self._interrupt_candidate_diagnostics={
                    "microphone_chunk_sequence":self._microphone_chunk_sequence,
                    "candidate_onset_monotonic_ms":round(
                        self._interrupt_candidate_started_at*1000),
                    "prefix_frames_retained":result.total_frames,
                    "configured_barge_in_prefix_frames":detector.config.prefix_frames,
                    "configured_barge_in_prefix_ms":detector.config.prefix_frames*20,
                    "playback_onset_voiced_frames":(
                        detector.config.onset_voiced_frames),
                    "playback_onset_window_frames":(
                        detector.config.onset_window_frames),
                    "playback_active":self.state==TurnState.AGENT_SPEAKING,
                }
                output.append(ListenerEvent(
                    "barge_in_candidate",interrupted_turn_id,result,
                    interrupted_generation,
                    diagnostics=dict(self._interrupt_candidate_diagnostics)))
            if result.rejected:
                candidate=self._interrupt_candidate_identity
                latency=(
                    max(0,round((self.clock()-
                                 self._interrupt_candidate_started_at)*1000))
                    if self._interrupt_candidate_started_at is not None
                    else None)
                output.append(ListenerEvent(
                    "barge_in_rejected",
                    candidate[0] if candidate else self.active_turn_id or 0,
                    result,
                    candidate[1] if candidate else self.generation,
                    reason=result.rejection_reason,latency_ms=latency,
                    diagnostics=dict(self._interrupt_candidate_diagnostics)))
                self._reset_interrupt_input(
                    playback=self.state==TurnState.AGENT_SPEAKING)
                break
            elif result.utterance is not None:
                candidate=self._interrupt_candidate_identity
                if candidate is None:
                    self._reset_interrupt_input(
                        playback=self.state==TurnState.AGENT_SPEAKING)
                    continue
                self._interrupt_candidate_endpoint_at=self.clock()
                self._interrupt_candidate_diagnostics[
                    "candidate_endpoint_monotonic_ms"
                ]=round(self._interrupt_candidate_endpoint_at*1000)
                self._interrupt_candidate_diagnostics[
                    "captured_utterance_frames"
                ]=result.total_frames
                latency=(
                    max(0,round((self._interrupt_candidate_endpoint_at-
                                 self._interrupt_candidate_started_at)*1000))
                    if self._interrupt_candidate_started_at is not None
                    else None)
                output.append(ListenerEvent(
                    "barge_in_audio_ready",candidate[0],result,candidate[1],
                    latency_ms=latency,
                    diagnostics=dict(self._interrupt_candidate_diagnostics)))
            if detector.state not in (TurnState.IDLE,TurnState.USER_SPEAKING):
                break
        return output
    def reject_interrupt_candidate(
        self,event:ListenerEvent,reason:str,
    )->ListenerEvent|None:
        identity=self._interrupt_candidate_identity
        if (event.kind!="barge_in_audio_ready" or identity is None or
                identity!=(event.turn_id,event.generation)):
            return None
        rejected=replace(
            event.result,utterance=None,rejected=True,
            rejection_reason=reason)
        self._reset_interrupt_input(
            playback=self.state==TurnState.AGENT_SPEAKING)
        return ListenerEvent(
            "barge_in_rejected",event.turn_id,rejected,event.generation,
            reason=reason,latency_ms=event.latency_ms,
            diagnostics=dict(event.diagnostics or {}))
    def commit_interrupt_candidate(
        self,event:ListenerEvent,*,stt_ms:int|None=None,
    )->list[ListenerEvent]:
        identity=self._interrupt_candidate_identity
        if (event.kind!="barge_in_audio_ready" or identity is None or
                identity!=(event.turn_id,event.generation) or
                self.active_turn_id!=event.turn_id or
                self.turn_generations.get(event.turn_id)!=event.generation or
                self.state not in (TurnState.PROCESSING,TurnState.AGENT_SPEAKING)
                or identity in self._interrupted_generations):
            return []
        self._interrupted_generations.add(identity)
        candidate_endpoint_at=self._interrupt_candidate_endpoint_at
        self.generation+=1
        superseding_turn_id=self.next_turn_id
        self.next_turn_id+=1
        self.active_turn_id=superseding_turn_id
        self.turn_generations[superseding_turn_id]=self.generation
        self._restore_primary_detector(TurnState.PROCESSING)
        self.framer=FrameBuffer()
        self.endpoint_at=candidate_endpoint_at or self.clock()
        self.turn_committed_at[superseding_turn_id]=self.endpoint_at
        latency=event.latency_ms
        diagnostics=dict(event.diagnostics or {})
        if stt_ms is not None:
            diagnostics["barge_in_stt_ms"]=max(0,stt_ms)
        diagnostics["captured_utterance_frames"]=event.result.total_frames
        self._reset_interrupt_input()
        return [
            ListenerEvent(
                "assistant.interrupted",event.turn_id,event.result,
                event.generation,superseding_turn_id,self.generation,
                reason="confirmed_speech",latency_ms=latency,
                diagnostics=diagnostics),
            ListenerEvent(
                "barge_in_committed",event.turn_id,event.result,
                event.generation,superseding_turn_id,self.generation,
                reason="confirmed_speech",latency_ms=latency,
                diagnostics=diagnostics),
            ListenerEvent(
                "speech.start",superseding_turn_id,event.result,
                self.generation),
            ListenerEvent(
                "speech.end",superseding_turn_id,event.result,
                self.generation),
        ]
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
    async def filler_segment(
        self,turn_id:int,generation:int,pcm:bytes,configuration_id:int|None,
    )->None:
        frames=frame_complete_audio(pcm)
        if not frames:return
        async with self.lock:
            await self.websocket.send_text(event(
                "filler.audio.start",configuration_id=configuration_id,
                turn_id=turn_id,generation=generation,
                frame_count=len(frames),sample_rate=16000,
                encoding="pcm_s16le"))
            for frame in frames:await self.websocket.send_bytes(frame)
            await self.websocket.send_text(event(
                "filler.audio.end",configuration_id=configuration_id,
                turn_id=turn_id,generation=generation,
                frame_count=len(frames)))
    async def native_audio(
        self,turn_id:int,response_id:str,item_id:str|None,pcm:bytes,*,sample_rate:int
    ):
        async with self.lock:
            await self.websocket.send_text(event(
                "native.audio.delta",turn_id=turn_id,response_id=response_id,
                item_id=item_id,sample_rate=sample_rate,encoding="pcm_s16le",
                byte_length=len(pcm)))
            await self.websocket.send_bytes(pcm)


def _curated_visual_specification(
    curated:CuratedProtocolSession,
) -> VisualSpecification|None:
    fixture=curated.fixture
    if not curated.active or fixture.source_pdf_sha256 is None:
        return None
    index=curated.current_index
    existing=fixture.visual_for_step(index)
    if existing is not None and existing.kind==ProtocolVisualKind.SOURCE_CROP.value:
        return None
    step=fixture.steps[index]
    facts=fixture.facts_for_step(index)
    return VisualSpecification(
        document_sha256=fixture.source_pdf_sha256,
        protocol_id=fixture.protocol_id,
        revision_id=fixture.revision_id,
        step_id=step.step_id,
        step_label=step.source_label,
        source_page=step.evidence.source_page_number,
        source_evidence_ids=tuple(fact.fact_id for fact in facts),
        action_summary=step.instruction_source_text,
        verified_materials=tuple(
            fact.text for fact in facts if fact.kind=="material"),
        verified_tools=tuple(
            fact.text for fact in facts if fact.kind=="equipment"),
        verified_relations=(step.instruction_source_text,),
        forbidden_inferences=(
            "unverified colors","unverified equipment","unverified PPE",
            "unverified quantities","unverified results","completion status",
        ),
    )


async def _queue_curated_generated_visual(
    *,session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    endpoint:float,clock:Callable[[],float],
    specification:VisualSpecification,
    settings:GeneratedVisualSettings,
) -> None:
    configuration_id=session.accepted_configuration_id
    job_id=specification.cache_key(settings.model)
    identity={
        "configuration_id":configuration_id,"turn_id":turn_id,
        "generation":generation,"protocol_id":specification.protocol_id,
        "step_id":specification.step_id,
        "source_document_hash":specification.document_sha256,
        "visual_job_id":job_id,
    }
    if not session.owns_visual_result(
        turn_id,generation,configuration_id,specification.protocol_id):
        return
    await sender.text(
        "protocol.visual.state",**identity,status="visual_pending",
        visual_requested_ms=max(0,round((clock()-endpoint)*1000)))

    async def worker() -> None:
        provider_called=False
        provider_started=0.0

        async def generate(spec:VisualSpecification)->bytes:
            nonlocal provider_called,provider_started
            provider_called=True
            provider_started=clock()
            if session.owns_visual_result(
                turn_id,generation,configuration_id,specification.protocol_id):
                await sender.text(
                    "tool.call",**identity,tool="generate_instructional_visual",
                    round=2)
            client=AsyncOpenAI(
                base_url=api_url(""),api_key=require_env("XAI_API_KEY"),
                max_retries=0)
            return await XaiImageGenerator(client,settings).generate(spec)

        try:
            asset,cache_hit=await GENERATED_VISUALS.obtain(
                specification,settings,generate)
            if not session.owns_visual_result(
                turn_id,generation,configuration_id,specification.protocol_id):
                return
            elapsed=max(0,round((clock()-endpoint)*1000))
            if provider_called:
                await sender.text(
                    "tool.result",**identity,
                    tool="generate_instructional_visual",round=2,
                    status="success",
                    elapsed_ms=max(0,round((clock()-provider_started)*1000)))
            await sender.text(
                "protocol.visual.state",**identity,
                status="visual_cache_hit" if cache_hit else "visual_ready",
                visual_ready_ms=elapsed,asset=asset.public_dict())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "generated visual failed closed turn_id=%s error=%s",
                turn_id,type(exc).__name__)
            if not session.owns_visual_result(
                turn_id,generation,configuration_id,specification.protocol_id):
                return
            if provider_called:
                await sender.text(
                    "tool.result",**identity,
                    tool="generate_instructional_visual",round=2,
                    status="error",
                    elapsed_ms=max(0,round((clock()-provider_started)*1000)))
            await sender.text(
                "protocol.visual.state",**identity,status="visual_failed",
                visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                fallback="none")

    task=asyncio.create_task(worker())
    session.track_visual_task(task)


async def _queue_curated_web_visual(
    *,session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    endpoint:float,clock:Callable[[],float],curated:CuratedProtocolSession,
    settings:WebVisualSettings,
) -> None:
    fixture=curated.fixture
    step=fixture.steps[curated.current_index]
    configuration_id=session.accepted_configuration_id
    job_id=hashlib.sha256(
        f"web-image\x1f{fixture.source_pdf_sha256}\x1f{step.step_id}".encode()
    ).hexdigest()
    identity={
        "configuration_id":configuration_id,"turn_id":turn_id,
        "generation":generation,"protocol_id":fixture.protocol_id,
        "step_id":step.step_id,"source_document_hash":fixture.source_pdf_sha256,
        "visual_job_id":job_id,
    }
    if not session.owns_visual_result(
        turn_id,generation,configuration_id,fixture.protocol_id):
        return
    await sender.text(
        "protocol.visual.state",**identity,status="web_visual_pending",
        visual_requested_ms=max(0,round((clock()-endpoint)*1000)))

    async def worker() -> None:
        started=clock()
        try:
            await sender.text(
                "tool.call",**identity,tool="search_authoritative_web",round=2)
            client=AsyncOpenAI(
                base_url=api_url(""),api_key=require_env("XAI_API_KEY"),
                max_retries=0)
            query="\n".join((
                "Find a real, authoritative image example relevant to this laboratory step.",
                f"Protocol: {fixture.title}",
                f"Step {step.source_label}: {step.instruction_source_text}",
                "Do not treat the image as protocol evidence or an observed result.",
            ))
            result=await XaiAuthoritativeImageSearch(client,settings).search(query)
            if not session.owns_visual_result(
                turn_id,generation,configuration_id,fixture.protocol_id):
                return
            elapsed=max(0,round((clock()-started)*1000))
            if result.get("status")=="success" and result.get("matches"):
                await sender.text(
                    "tool.result",**identity,tool="search_authoritative_web",
                    round=2,status="success",elapsed_ms=elapsed,
                    retrieval_backend=result.get("backend"),match_count=1)
                await sender.text(
                    "protocol.visual.state",**identity,status="web_visual_ready",
                    visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                    candidate=result["matches"][0])
            else:
                await sender.text(
                    "tool.result",**identity,tool="search_authoritative_web",
                    round=2,status="not_found",elapsed_ms=elapsed,match_count=0)
                await sender.text(
                    "protocol.visual.state",**identity,status="visual_failed",
                    visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                    fallback="none")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "web visual search failed closed turn_id=%s error=%s",
                turn_id,type(exc).__name__)
            if session.owns_visual_result(
                turn_id,generation,configuration_id,fixture.protocol_id):
                await sender.text(
                    "tool.result",**identity,tool="search_authoritative_web",
                    round=2,status="error",
                    elapsed_ms=max(0,round((clock()-started)*1000)))
                await sender.text(
                    "protocol.visual.state",**identity,status="visual_failed",
                    visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                    fallback="none")

    task=asyncio.create_task(worker())
    session.track_visual_task(task)


def _open_experiment_report(
    session:ListenerSession,
    curated:CuratedProtocolSession,
)->dict:
    store=session.experiment_report_store
    if store is None:
        raise RuntimeError("experiment reports are disabled")
    if session.experiment_report_id is None:
        report=store.open_report(
            session_id=session.session_id,
            protocol_id=curated.fixture.protocol_id,
            protocol_title=curated.fixture.title,
            protocol_revision=curated.fixture.revision_id,
            protocol_sha256=curated.fixture.source_pdf_sha256 or "",
            readiness_status=curated.fixture.draft.readiness.status.value,
            development_only=curated.fixture.development_only,
        )
        session.experiment_report_id=report["report_id"]
    return store.get_report(session.experiment_report_id)


def _record_experiment_report_plan(
    session:ListenerSession,
    curated:CuratedProtocolSession,
    plan,
    *,
    turn_id:int,
    generation:int,
)->dict:
    report=_open_experiment_report(session,curated)
    store=session.experiment_report_store
    assert store is not None and session.experiment_report_id is not None
    event_key=(
        f"turn-{turn_id}-generation-{generation}-{plan.action.value}"
    )
    step=(
        curated.fixture.steps[curated.current_index]
        if curated.active else None
    )
    step_id=step.step_id if step is not None else None
    step_label=plan.step_label
    event_type=None
    payload={
        "intent_kind":plan.intent_kind,
        "state_changed":bool(plan.state_changed),
        "answer_origin":plan.answer_origin,
        "development_only":curated.fixture.development_only,
    }
    if plan.action is CuratedProtocolAction.START:
        event_type="session_started"
    elif plan.action is CuratedProtocolAction.NEXT and plan.state_changed:
        event_type=(
            "step_completed" if plan.reported_completion else "step_advanced"
        )
    elif plan.action is CuratedProtocolAction.NEXT and plan.speech_mode.value=="blocked":
        event_type="blocked"
    elif plan.action is CuratedProtocolAction.REPORT_ANOMALY:
        event_type="anomaly"
    elif plan.action is CuratedProtocolAction.STOP:
        event_type="session_stopped"
    elif plan.answer_origin in {
        "approved_lab_corpus","external_authoritative_reference",
    }:
        event_type="source_consulted"
    if event_type is not None:
        report=store.append_event(
            session.experiment_report_id,
            event_key=event_key,
            event_type=event_type,
            step_id=step_id,
            step_label=step_label,
            user_wording=plan.anomaly_text,
            category=plan.anomaly_category,
            severity=("unknown" if plan.reported_anomaly else None),
            confirmation_state=(
                "user_reported" if plan.reported_anomaly else None
            ),
            source_tier=(
                plan.answer_origin
                if plan.answer_origin != "current_protocol" else None
            ),
            citation_identities=tuple(
                hashlib.sha256(str(item).encode()).hexdigest()
                for item in plan.evidence_ids
            ),
            payload=payload,
        )
    if plan.action is CuratedProtocolAction.STOP:
        report=store.finalize(
            session.experiment_report_id,
            status="stopped",
            event_key=f"{event_key}-finalize",
        )
    return report


def _public_experiment_report_state(report:dict)->dict:
    return {
        key:report.get(key) for key in (
            "report_id","status","started_at","ended_at","anomaly_count",
            "blocker_count","finalization_version","development_only",
        )
    } | {"event_count":len(report.get("events",()))}

async def run_turn(websocket:WebSocket,session:ListenerSession,source_pcm:bytes,turn_id:int,
                   input_frames:int,voiced_frames:int=0,clock:Callable[[],float]=time.perf_counter,
                   sender:LockedSender|None=None,filler:CascadeFiller|None=None,
                   accepted_transcription:Transcription|None=None,
                   accepted_stt_ms:int|None=None)->None:
    sender=sender or LockedSender(websocket); endpoint=session.endpoint_at or clock(); timings={}; generation=session.generation
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
    timings["utterance_to_status_ms"]=round((clock()-endpoint)*1000)
    await progress(
        "transcribing",
        timings_ms={"utterance_to_status":timings["utterance_to_status_ms"]})
    if not await current_text("turn.processing",turn_id=turn_id,input_frames=input_frames): return
    if accepted_transcription is None:
        started=clock()
        transcription=await asyncio.to_thread(
            transcribe,clean_path(source_pcm))
        timings["stt"]=round((clock()-started)*1000)
    else:
        transcription=accepted_transcription
        timings["stt"]=max(0,accepted_stt_ms or 0)
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
    timings["route_started_ms"]=round((clock()-endpoint)*1000)
    await progress(
        "routing",timings_ms={"route_started":timings["route_started_ms"]})
    emergency=recognize_emergency(transcript)
    if emergency is not None:
        text=emergency.response
        timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
        if not await current_text("reply.delta",turn_id=turn_id,segment_index=0,text=text): return
        try:
            await progress("synthesizing",route="deterministic_emergency")
            pcm=await asyncio.to_thread(synthesize,text,emergency.language)
            frames=frame_complete_audio(pcm)
            if filler is not None:await filler.primary_ready()
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
            timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
            await current_text("session.language_confirmation_required",turn_id=turn_id,
                               reason=resolution.reason,languages=["ko","en"])
            await current_text("reply.delta",turn_id=turn_id,segment_index=0,text=text)
            session.set_turn_terminal_outcome(turn_id,generation,"blocked")
            try:
                await progress("synthesizing",route="language_clarification")
                pcm=await asyncio.to_thread(synthesize,text,fallback)
                frames=frame_complete_audio(pcm)
                if filler is not None:await filler.primary_ready()
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
        curated_tools_used=[]
        try:
            timings["protocol_lookup_started_ms"]=round((clock()-endpoint)*1000)
            await progress("checking_protocol",route="curated_protocol")
            plan=curated.plan(
                transcript,turn_id=turn_id,language=turn_language,
                transcript_quality=transcription_quality_issue(transcription))
            if plan.action is CuratedProtocolAction.RELATED_QUESTION and curated.active:
                step = curated.fixture.steps[curated.current_index]
                facts = plan.facts or (
                    curated.related_facts(transcript)
                    if plan.requested_entity is not None
                    else curated.fixture.facts_for_step(curated.current_index)
                )
                resolved_query = curated.reference_query_for(transcript, plan)
                if resolved_query is None:
                    raise RuntimeError("related reference query is unavailable")
                reference_query=plan_research_query(
                    resolved_query,
                    protocol_title=curated.fixture.title,
                    step_label=step.source_label,
                    step_text=step.instruction_source_text,
                    evidence_texts=tuple(fact.text for fact in facts),
                    requested_entity=plan.requested_entity,
                    question_kind=plan.question_kind,
                    question_dimensions=plan.question_dimensions,
                )
                client=None
                force_external=(
                    plan.requested_followup=="search_external_reference"
                )
                if not force_external:
                    try:
                        client=AsyncOpenAI(
                            base_url=api_url(""),api_key=require_env("XAI_API_KEY"),
                            max_retries=0)
                        client.model=require_env("CHAT_MODEL")
                        answer=await answer_curated_protocol_question(
                            client,
                            resolved_query,
                            language=turn_language,
                            protocol_id=curated.fixture.protocol_id,
                            protocol_title=curated.fixture.title,
                            step_id=step.step_id,
                            step_label=step.source_label,
                            facts=tuple(
                                (fact.fact_id,fact.kind,fact.text,fact.source_page)
                                for fact in facts
                            ),
                        )
                        if not session.is_current(turn_id,generation):
                            curated._restore(checkpoint)
                            return
                        requested_dimensions=set(plan.question_dimensions)
                        requires_external_dimension=bool(
                            plan.requested_entity
                            and requested_dimensions & {
                                "definition", "role", "difference", "safety",
                            }
                            or plan.question_kind=="safety"
                        )
                        local_coverage_complete=(
                            not requires_external_dimension
                            and not answer.unsupported_parts
                        )
                        if answer.intent != "unsupported" and local_coverage_complete:
                            plan=curated.apply_grounded_answer(
                                turn_id=turn_id,
                                language=turn_language,
                                primary_text=answer.primary_text,
                                evidence_ids=answer.evidence_ids,
                                inference_labels=answer.inference_labels,
                                unsupported_parts=answer.unsupported_parts,
                            )
                    except Exception:
                        log.info(
                            "curated grounded QA failed closed turn_id=%s",turn_id)
                if (
                    plan.action is CuratedProtocolAction.RELATED_QUESTION
                    and session.tool_context is not None
                    and not force_external
                ):
                    await progress(
                        "checking_approved_information",
                        route="approved_information")
                    await current_text(
                        "tool.call",turn_id=turn_id,
                        tool=APPROVED_LAB_REFERENCE_TOOL_NAME,round=0)
                    reference_started=clock()
                    reference_result=await asyncio.to_thread(
                        search_approved_lab_references,
                        reference_query,
                        context=session.tool_context,
                        protocol_id=curated.fixture.protocol_id,
                        top_k=5,
                    )
                    if not session.is_current(turn_id,generation):
                        curated._restore(checkpoint)
                        return
                    reference_elapsed=round((clock()-reference_started)*1000)
                    reference_backend=(
                        reference_result.get("retrieval",{}).get("backend")
                        if isinstance(reference_result,dict) else None)
                    await current_text(
                        "tool.result",turn_id=turn_id,
                        tool=APPROVED_LAB_REFERENCE_TOOL_NAME,round=0,
                        status=reference_result.get("status","error"),
                        elapsed_ms=reference_elapsed,
                        retrieval_backend=reference_backend,
                        match_count=len(reference_result.get("matches",[])))
                    curated_tools_used.append(APPROVED_LAB_REFERENCE_TOOL_NAME)
                    matches=tuple(reference_result.get("matches",()))
                    if reference_result.get("answerable") and matches:
                        try:
                            if client is None:
                                client=AsyncOpenAI(
                                    base_url=api_url(""),
                                    api_key=require_env("XAI_API_KEY"),
                                    max_retries=0)
                                client.model=require_env("CHAT_MODEL")
                            await progress("composing",route="approved_information")
                            reference_answer=await answer_approved_reference_question(
                            client,resolved_query,language=turn_language,
                                protocol_id=curated.fixture.protocol_id,
                                step_id=step.step_id,evidence=matches)
                            if not session.is_current(turn_id,generation):
                                curated._restore(checkpoint)
                                return
                            plan=curated.apply_reference_answer(
                                turn_id=turn_id,language=turn_language,
                                primary_text=reference_answer.primary_text,
                                origin="approved_lab_corpus",
                                citations=reference_answer.citations,
                                retrieval_backend=reference_backend or "sqlite",
                                retrieval_scores=tuple(
                                    float(item["score"]) for item in matches
                                    if isinstance(item.get("score"),(int,float))
                                ),
                                limitations=reference_answer.limitations,
                            )
                        except Exception:
                            log.info(
                                "approved reference answer failed closed turn_id=%s",
                                turn_id)
                if plan.action is CuratedProtocolAction.RELATED_QUESTION:
                    external_settings=session.external_reference_settings
                    if external_settings.enabled:
                        external_tool="search_authoritative_web"
                        await progress(
                            "checking_approved_information",
                            route="approved_information")
                        await current_text(
                            "tool.call",turn_id=turn_id,
                            tool=external_tool,round=1)
                        external_started=clock()
                        try:
                            external_client=AsyncOpenAI(
                                base_url=api_url(""),
                                api_key=require_env("XAI_API_KEY"),
                                max_retries=0)
                            result=await XaiAuthoritativeWebSearch(
                                external_client,external_settings).search(
                                    reference_query,language=turn_language)
                        except Exception:
                            result={"status":"error","matches":[]}
                        if not session.is_current(turn_id,generation):
                            curated._restore(checkpoint)
                            return
                        external_elapsed=round((clock()-external_started)*1000)
                        await current_text(
                            "tool.result",turn_id=turn_id,
                            tool=external_tool,round=1,
                            status=result.get("status","error"),
                            elapsed_ms=external_elapsed,
                            retrieval_backend=result.get("backend"),
                            match_count=len(result.get("matches",[])))
                        curated_tools_used.append(external_tool)
                        if result.get("status")=="success" and result.get("matches"):
                            plan=curated.apply_reference_answer(
                                turn_id=turn_id,language=turn_language,
                                primary_text=result["answer"],
                                origin="external_authoritative_reference",
                                citations=tuple(result["matches"]),
                                retrieval_backend=result["backend"],
                                limitations=(
                                    "External guidance cannot modify the active protocol.",
                                ),
                            )
                        else:
                            local_facts=tuple(plan.facts)
                            local_summary=(
                                "\n".join(f"- {fact.text}" for fact in local_facts[:4])
                                if local_facts else "- 확인된 추가 사실 없음"
                            )
                            status=result.get("status","error")
                            if turn_language=="ko":
                                display=(
                                    "현재 프로토콜에서 확인되는 내용\n"
                                    f"{local_summary}\n\n"
                                    "외부 권위자료 검색을 완료하지 못해 요청하신 추가 "
                                    "설명을 검증하지 못했습니다. 현재 프로토콜과 단계 "
                                    "상태는 변경하지 않았습니다."
                                )
                                speech=(
                                    "현재 프로토콜에서 확인되는 내용은 화면에 남겨 "
                                    "두었습니다. 외부 권위자료 검색이 완료되지 않아 "
                                    "추가 설명을 검증하지 못했습니다."
                                )
                            else:
                                display=(
                                    "What the active protocol establishes\n"
                                    f"{local_summary}\n\n"
                                    "The authoritative web search did not complete, so "
                                    "the requested additional explanation could not be "
                                    "verified. Protocol state was not changed."
                                )
                                speech=(
                                    "The protocol facts remain visible, but the external "
                                    "search did not complete, so I could not verify the "
                                    "additional explanation."
                                )
                            plan=replace(
                                plan,display_text=display,speech_text=speech,
                                limitations=(*plan.limitations,
                                    f"authoritative_web_search_{status}"),
                            )
                    elif (
                        plan.requested_entity is not None
                        or plan.question_kind=="safety"
                        or force_external
                    ):
                        local_facts = tuple(plan.facts)
                        local_summary = (
                            "\n".join(f"- {fact.text}" for fact in local_facts[:4])
                            if local_facts else ""
                        )
                        unavailable = (
                            "현재 프로토콜에서 확인되는 내용"
                            + (f"\n{local_summary}" if local_summary else "은 제한적입니다.")
                            + "\n\n요청하신 추가 설명은 활성 프로토콜만으로 확인되지 않았고, "
                            "이 세션에서는 외부 권위자료 검색을 사용할 수 없습니다. "
                            "프로토콜 상태는 변경하지 않았습니다."
                            if turn_language == "ko" else
                            "The active protocol has only partial information for this "
                            "question, and authoritative web research is unavailable in "
                            "this session. Protocol state was not changed."
                        )
                        plan=replace(
                            plan,display_text=unavailable,speech_text=unavailable,
                            limitations=(*plan.limitations,
                                "authoritative_web_search_disabled"),
                        )
            report_prepared=False
            if plan.action in {
                CuratedProtocolAction.REPORT_ANOMALY,
                CuratedProtocolAction.SHOW_REPORT,
            }:
                if session.experiment_report_store is None:
                    unavailable=(
                        "실험 기록 기능이 이 세션에서 활성화되지 않았습니다. "
                        "프로토콜 상태는 변경하지 않았습니다."
                        if turn_language=="ko" else
                        "Experiment reporting is not enabled for this session. "
                        "The protocol state did not change."
                    )
                    plan=replace(
                        plan,display_text=unavailable,speech_text=unavailable,
                        speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                    )
                else:
                    report=await asyncio.to_thread(
                        _record_experiment_report_plan,
                        session,curated,plan,
                        turn_id=turn_id,generation=generation,
                    )
                    report_prepared=True
                    await current_text(
                        "experiment.report.state",turn_id=turn_id,
                        report=_public_experiment_report_state(report))
            display_text=plan.display_text
            speech_text=plan.speech_text
            if not isinstance(display_text,str) or not display_text.strip():
                raise RuntimeError("curated protocol produced no display text")
            if not isinstance(speech_text,str) or not speech_text.strip():
                raise RuntimeError("curated protocol produced no speech text")
            timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
            if plan.speech_mode.value=="blocked":
                session.set_turn_terminal_outcome(
                    turn_id,generation,"blocked")
            timings["first_tts_request_ms"]=round((clock()-endpoint)*1000)
            await progress("synthesizing",route="curated_protocol")
            pcm=await asyncio.to_thread(synthesize,speech_text,turn_language)
            frames=frame_complete_audio(pcm)
            if filler is not None:await filler.primary_ready()
            if not frames or not session.start_playback(turn_id):
                raise RuntimeError("curated protocol produced no playable audio")
            if (
                session.experiment_report_store is not None
                and not report_prepared
                and plan.action in {
                    CuratedProtocolAction.START,
                    CuratedProtocolAction.NEXT,
                    CuratedProtocolAction.STOP,
                    CuratedProtocolAction.QUESTION,
                }
            ):
                try:
                    report=await asyncio.to_thread(
                        _record_experiment_report_plan,
                        session,curated,plan,
                        turn_id=turn_id,generation=generation,
                    )
                    await current_text(
                        "experiment.report.state",turn_id=turn_id,
                        report=_public_experiment_report_state(report))
                except Exception:
                    log.warning(
                        "experiment report update failed turn_id=%s",turn_id)
                    await current_text(
                        "experiment.report.error",turn_id=turn_id,
                        code="report_update_failed")
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
            state=curated.state(spoken_summary=plan.spoken_summary),
            action=plan.action.value)
        operation_labels={
            CuratedProtocolAction.START:"protocol_start",
            CuratedProtocolAction.CURRENT:"current_step_read",
            CuratedProtocolAction.REPEAT:"current_step_repeat",
            CuratedProtocolAction.FULL_DETAIL:(
                "current_step_elaboration"
                if plan.intent_kind=="step_elaboration"
                else "expected_result_explanation"
                if plan.intent_kind=="expected_result_explanation"
                else "current_step_full_detail"
            ),
            CuratedProtocolAction.NEXT:"next_step_transition",
            CuratedProtocolAction.QUESTION:(
                "approved_reference_qa"
                if plan.answer_origin=="approved_lab_corpus" else
                "external_reference_qa"
                if plan.answer_origin=="external_authoritative_reference" else
                "grounded_qa" if plan.translation_status=="grounded_model" else
                "verified_fact_read"),
            CuratedProtocolAction.RELATED_QUESTION:"related_question_unresolved",
            CuratedProtocolAction.VISUAL_REQUEST:"instructional_visual_request",
            CuratedProtocolAction.AUDIO_RECOVERY:"audio_replay_request",
            CuratedProtocolAction.TRANSCRIPT_UNRELIABLE:"transcript_retry_required",
            CuratedProtocolAction.CANCEL_READONLY:"readonly_operation_cancelled",
            CuratedProtocolAction.REPORT_ANOMALY:"experiment_anomaly_recorded",
            CuratedProtocolAction.SHOW_REPORT:"experiment_report_view",
            CuratedProtocolAction.CLARIFY_COMPLETION:"completion_confirmation_required",
            CuratedProtocolAction.CLARIFY_REFERENCE:"reference_clarification_required",
            CuratedProtocolAction.OFF_TOPIC:"scope_reminder",
            CuratedProtocolAction.UNSUPPORTED:"unsupported_question",
            CuratedProtocolAction.STOP:"protocol_stop",
            CuratedProtocolAction.INACTIVE:"inactive_session_guard",
        }
        operation=(
            "completion_and_next_transition"
            if plan.action is CuratedProtocolAction.NEXT
            and plan.reported_completion
            else operation_labels[plan.action]
        )
        await current_text(
            "server.operation",turn_id=turn_id,
            route="curated_protocol",operation=operation)
        await current_text(
            "reply.delta",turn_id=turn_id,segment_index=0,text=display_text,
            primary_text=plan.primary_text,
            source_texts=list(plan.source_texts),
            source_pages=list(plan.source_pages),
            evidence_ids=list(plan.evidence_ids),
            translation_status=plan.translation_status,
            source_language="en",speech_text=speech_text,
            answer_origin=plan.answer_origin,
            citations=list(plan.citations),
            retrieval_backend=plan.retrieval_backend,
            retrieval_scores=list(plan.retrieval_scores),
            limitations=list(plan.limitations),
            transcript_correction_note=plan.transcript_correction_note,
            question_dimensions=list(plan.question_dimensions))
        await current_text(
            "state.changed",state=session.state.value,turn_id=turn_id)
        await sender.segment(turn_id,0,frames,generation)
        await current_text(
            "reply.complete",turn_id=turn_id,text=display_text)
        await current_text(
            "audio.complete",turn_id=turn_id,segment_count=1)
        if plan.action is CuratedProtocolAction.AUDIO_RECOVERY:
            await current_text(
                "audio.replay.request",turn_id=turn_id,
                replay_count=1,state_mutation=False)
        else:
            await current_text(
                "audio.replay.available",turn_id=turn_id,
                replay_count=1,state_mutation=False)
        timings["total_ms"]=round((clock()-endpoint)*1000)
        await current_text(
            "turn.done",turn_id=turn_id,timings_ms=timings,
            segment_count=1,input_frames=input_frames,
            output_frames=len(frames),tools_used=curated_tools_used,
            route="curated_protocol",result_kind=plan.action.value,
            fact_id=plan.fact_id,speech_mode=plan.speech_mode.value,
            critical_warning_present=plan.critical_warning_text is not None,
            intent_kind=plan.intent_kind,
            reported_completion=plan.reported_completion,
            requested_transition=plan.requested_transition)
        if plan.action is CuratedProtocolAction.VISUAL_REQUEST:
            existing_visual=curated.fixture.visual_for_step(curated.current_index)
            if plan.visual_kind=="web_photo" and existing_visual is None:
                web_visual_settings=session.web_visual_settings
                if web_visual_settings.enabled:
                    await _queue_curated_web_visual(
                        session=session,sender=sender,turn_id=turn_id,
                        generation=generation,endpoint=endpoint,clock=clock,
                        curated=curated,settings=web_visual_settings)
            elif existing_visual is None:
                visual_settings=session.generated_visual_settings
                visual_spec=(
                    _curated_visual_specification(curated)
                    if visual_settings.enabled else None)
                if visual_spec is not None:
                    await _queue_curated_generated_visual(
                        session=session,sender=sender,turn_id=turn_id,
                        generation=generation,endpoint=endpoint,clock=clock,
                        specification=visual_spec,settings=visual_settings)
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
        timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
        if deterministic_result.get("code"):
            session.set_turn_terminal_outcome(turn_id,generation,"blocked")
        await current_text(
            "reply.delta",turn_id=turn_id,segment_index=0,text=text)
        try:
            timings["first_tts_request_ms"]=round((clock()-endpoint)*1000)
            await progress("synthesizing",route="deterministic_procedure")
            pcm=await asyncio.to_thread(synthesize,text,turn_language)
            frames=frame_complete_audio(pcm)
            if filler is not None:await filler.primary_ready()
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
            timings["primary_text_ready_ms"]=timings["first_sentence_ms"]
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
                if filler is not None:await filler.primary_ready()
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

async def run_turn_safely(
    websocket,session,source_pcm,turn_id,input_frames,voiced_frames=0,
    *,accepted_transcription:Transcription|None=None,
    accepted_stt_ms:int|None=None,
):
    generation=session.turn_generations.get(turn_id,session.generation)
    sender=LockedSender(websocket)
    language=(session.accepted_language or session.manual_language or
              (session.tool_context.language if session.tool_context else "ko"))
    async def filler_event(kind:str,**fields):
        await sender.text(
            kind,configuration_id=session.accepted_configuration_id,**fields)
    async def filler_audio(target_turn:int,target_generation:int,pcm:bytes):
        await sender.filler_segment(
            target_turn,target_generation,pcm,session.accepted_configuration_id)
    async def filler_clear(target_turn:int,target_generation:int):
        await sender.text(
            "filler.audio.clear",
            configuration_id=session.accepted_configuration_id,
            turn_id=target_turn,generation=target_generation,
            reason="primary_audio_ready")
    filler=CascadeFiller(
        turn_id=turn_id,generation=generation,language=language,
        delay_ms=cascade_filler_delay_ms(),
        synthesize=lambda text,selected:asyncio.to_thread(
            synthesize,text,selected),
        send_audio=filler_audio,send_event=filler_event,
        send_clear=filler_clear,is_current=session.is_current,
        clock=session.clock)
    filler.start()
    try: await run_turn(
        websocket,session,source_pcm,turn_id,input_frames,voiced_frames,
        sender=sender,filler=filler,
        accepted_transcription=accepted_transcription,
        accepted_stt_ms=accepted_stt_ms)
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
    finally:
        await filler.cancel()

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
    fields={
        **progress,
        "superseding_turn_id":interruption.superseding_turn_id,
        "superseding_generation":interruption.superseding_generation,
        "reason":"confirmed_speech",
    }
    if interruption.latency_ms is not None:
        fields["barge_in_to_silence_ms"]=interruption.latency_ms
    await websocket.send_text(event("cascade.playback.clear",**fields))

@app.websocket("/ws")
async def voice_socket(websocket:WebSocket):
    await websocket.accept()
    try:
        vad_settings=VoiceVadSettings.from_environment()
        config=VadConfig.from_settings(vad_settings.cascade)
        report_settings=ExperimentReportSettings.from_environment()
        external_settings=ExternalReferenceSettings.from_environment()
        web_visual_settings=WebVisualSettings.from_environment(external_settings)
        generated_visual_settings=GeneratedVisualSettings.from_environment()
    except (ConfigurationError,ValueError) as exc:
        await websocket.send_text(event(
            "error",message=f"invalid non-secret configuration: {exc}"))
        await websocket.close(code=1008,reason="invalid configuration")
        return
    research_capabilities={
        "external_text":external_settings.public_capability(),
        "web_image":{
            "status":"enabled" if web_visual_settings.enabled else "disabled",
        },
        "generated_visual":{
            "status":(
                "enabled" if generated_visual_settings.enabled else "disabled"),
            "model":(
                generated_visual_settings.model
                if generated_visual_settings.enabled else None),
        },
    }
    report_store=(
        ExperimentReportStore(report_settings.database_path)
        if report_settings.enabled and report_settings.database_path is not None
        else None
    )
    session=ListenerSession(
        EndpointDetector(config,listening_onset=True),
        experiment_report_store=report_store,
        external_reference_settings=external_settings,
        web_visual_settings=web_visual_settings,
        generated_visual_settings=generated_visual_settings,
    ); task=None; trusted_config=None; procedure_store=None
    curated_fixture=None
    sender=LockedSender(websocket); native_session=None; native_config=None; pipeline="cascade"
    await websocket.send_text(event("ready",sample_rate=16000,native_sample_rate=NATIVE_SAMPLE_RATE,
                                    pipelines=["cascade","native"],frame_ms=20,
                                    frame_bytes=FRAME_BYTES,vad_mode=config.mode,
                                    endpoint_silence_ms=config.endpoint_silence_frames*20,
                                    prefix_padding_ms=config.prefix_frames*20,
                                    barge_in_prefix_ms=config.barge_in_prefix_frames*20,
                                    research_capabilities=research_capabilities))
    try:
        while True:
            message=await websocket.receive()
            if message.get("type")=="websocket.disconnect": break
            if message.get("bytes") is not None:
                if native_session is not None:
                    await native_session.send_audio(message["bytes"])
                    continue
                if session.refresh_cooldown(): await websocket.send_text(event("state.changed",state="IDLE"))
                listener_events=[]
                accepted_interrupts={}
                for item in session.accept_chunk(message["bytes"]):
                    if item.kind!="barge_in_audio_ready":
                        listener_events.append(item)
                        continue
                    validation_started=session.clock()
                    try:
                        transcription=await asyncio.to_thread(
                            transcribe,clean_path(item.result.utterance or b""))
                        if isinstance(transcription,str):
                            transcription=Transcription(transcription,None)
                    except Exception:
                        log.warning(
                            "barge_in.rejected reason=transcription_failed "
                            "voiced_frames=%d total_frames=%d",
                            item.result.voiced_frames,item.result.total_frames)
                        rejected=session.reject_interrupt_candidate(
                            item,"transcription_failed")
                        if rejected is not None:
                            listener_events.append(rejected)
                        continue
                    stt_ms=max(
                        0,round((session.clock()-validation_started)*1000))
                    if not transcription.text.strip():
                        rejected=session.reject_interrupt_candidate(
                            item,"empty_transcript")
                        if rejected is not None:
                            listener_events.append(rejected)
                        continue
                    committed=session.commit_interrupt_candidate(item,stt_ms=stt_ms)
                    if not committed:
                        rejected=session.reject_interrupt_candidate(
                            item,"stale_candidate")
                        if rejected is not None:
                            listener_events.append(rejected)
                        continue
                    end=next(
                        event_item for event_item in committed
                        if event_item.kind=="speech.end")
                    accepted_interrupts[(end.turn_id,end.generation)]=(
                        transcription,stt_ms)
                    listener_events.extend(committed)
                for item in listener_events:
                    if item.kind=="assistant.interrupted":
                        await cancel_cascade_generation(
                            websocket,session,task,item)
                        task=None
                        continue
                    fields={
                        "turn_id":item.turn_id,
                        "generation":item.generation,
                        "state":session.state.value,
                        "voiced_frames":item.result.voiced_frames,
                        "total_frames":item.result.total_frames,
                        "duration_ms":item.result.total_frames*20,
                        "reason":item.reason or item.result.rejection_reason,
                        "forced":item.result.forced,
                    }
                    if item.superseding_turn_id is not None:
                        fields["superseding_turn_id"]=item.superseding_turn_id
                    if item.superseding_generation is not None:
                        fields["superseding_generation"]=(
                            item.superseding_generation)
                    if item.latency_ms is not None:
                        fields["barge_in_to_silence_ms"]=item.latency_ms
                    if item.diagnostics:
                        fields.update(item.diagnostics)
                    await websocket.send_text(event(item.kind,**fields))
                    if item.kind in {
                        "barge_in_candidate","barge_in_committed",
                        "barge_in_rejected",
                    }:
                        log.info(
                            "%s reason=%s voiced_frames=%d total_frames=%d "
                            "barge_in_to_silence_ms=%s",
                            item.kind,fields["reason"],
                            item.result.voiced_frames,item.result.total_frames,
                            item.latency_ms)
                    if item.kind=="speech.start":
                        progress=session.advance_turn_progress(
                            item.turn_id,item.generation,"listening")
                        if progress is not None:
                            await websocket.send_text(event(
                                "turn.state",**progress))
                    if item.kind=="speech.end":
                        accepted=accepted_interrupts.get(
                            (item.turn_id,item.generation))
                        task=asyncio.create_task(run_turn_safely(
                            websocket,session,item.result.utterance or b"",
                            item.turn_id,item.result.total_frames,
                            item.result.voiced_frames,
                            accepted_transcription=(
                                accepted[0] if accepted else None),
                            accepted_stt_ms=(
                                accepted[1] if accepted else None)))
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
                    selected_revision_id=None
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
                                    selected_revision_id=getattr(
                                        curated_fixture,"revision_id",
                                        f"fixture-{requested_protocol_id}")
                            if selected_curated_fixture is None:
                                settings=_protocol_store_settings()
                                if settings.enabled:
                                    configuration_stage="protocol_catalog"
                                    catalog,protocol_store=_open_protocol_catalog()
                                    try:
                                        try:
                                            entry=catalog.get_entry(requested_protocol_id)
                                        except ProtocolCatalogNotFoundError:
                                            entry=None
                                        if entry is not None:
                                            if entry.available_for_execution:
                                                selected_curated_fixture=(
                                                    catalog.load_executable_fixture(
                                                        requested_protocol_id))
                                                selected_revision_id=entry.revision_id
                                            else:
                                                selection_failure=(
                                                    "protocol_selection_unavailable")
                                    finally:
                                        protocol_store.close()
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
                                    selection_failure=None
                                    selected_revision_id=(
                                        f"approved-procedure-"
                                        f"{definitions[requested_protocol_id].version}")
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
                        requested_protocol_id,selected_revision_id)
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
                ready_fields={
                    "configuration_id":session.accepted_configuration_id,
                    "mode":session.accepted_mode,
                    "language":session.accepted_language,
                    "protocol_id":session.accepted_protocol_id,
                    "research_capabilities":research_capabilities,
                }
                if session.accepted_protocol_id is not None:
                    ready_fields["revision_id"]=session.accepted_revision_id
                await websocket.send_text(event("session.ready",**ready_fields))
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
            elif control["type"]=="client.audio_constraints":
                requested=control["requested"]
                actual=control["actual"]
                log.info(
                    "client.audio_constraints "
                    "requested_echo_cancellation=%s actual_echo_cancellation=%s "
                    "requested_noise_suppression=%s actual_noise_suppression=%s "
                    "requested_auto_gain_control=%s actual_auto_gain_control=%s",
                    requested["echoCancellation"],actual["echoCancellation"],
                    requested["noiseSuppression"],actual["noiseSuppression"],
                    requested["autoGainControl"],actual["autoGainControl"],
                )
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
            elif control["type"]=="native.playback.metrics" and native_session is not None:
                log.info(
                    "native.playback.metrics response_id=%s provider_gap_count=%s "
                    "provider_gap_ms=%s client_underrun_count=%s "
                    "client_underrun_ms=%s scheduled_chunks=%s audio_context_state=%s",
                    control["response_id"],control["provider_gap_count"],
                    control["provider_gap_ms"],control["client_underrun_count"],
                    control["client_underrun_ms"],control["scheduled_chunks"],
                    control["audio_context_state"],
                )
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
