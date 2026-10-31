"""Voice Workflow Agent: hands-free voice cascade with M2 Dispatcher tools."""
from __future__ import annotations
import asyncio, base64, collections, contextvars, hashlib, hmac, importlib.util, json, logging, math, os, re, secrets, sqlite3, stat, tempfile, textwrap, threading, time, unicodedata
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote, unquote_plus, unquote_to_bytes, urlsplit
from xml.sax.saxutils import escape as xml_escape
import requests
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI, OpenAI
from voiney_lab.audio import FRAME_BYTES, FRAME_MS, FrameBuffer, clean_path, pcm_to_wav
from voiney_lab.brain import (
    ConversationHistory,
    SentenceChunker,
    answer_curated_protocol_question,
    select_curated_protocol_answer,
)
from voiney_lab.configuration import (
    CascadeSttSettings,
    ConfigurationError,
    VoiceVadSettings,
    boolean_setting,
    cascade_filler_delay_ms,
    refuse_xai_only_features_without_key,
)
from voiney_lab.cascade_filler import (
    CascadeFiller,
    FillerSessionMemory,
    GROUNDED_ANSWER_STATUS_DELAY_MS,
    cascade_filler_mode,
    cascade_filler_status_delay_ms,
    cascade_filler_status_speech_enabled,
)
from voiney_lab.curated_protocol import (
    LANE_WV_PICTURE_KINDS,
    NO_FIGURE_WORDS,
    RESULT_QUESTION_KO,
    STT_CONTROL_KEYTERMS,
    control_words,
    ClaimAdmissionStatus,
    CuratedProtocolAction,
    CuratedProtocolFixture,
    CuratedProtocolFixtureError,
    CuratedProtocolSession,
    CuratedProtocolSpeechMode,
    TimerNotice,
    is_paused_resume_utterance,
    josa_ro,
    load_curated_protocol_fixture,
    ProtocolVocabulary,
    photo_asked,
    plan_without_internal_names,
    question_dimensions as _question_dimensions,
    spoken_korean,
    web_lookup_subject,
)
from voiney_lab.answer_checks import (
    SAFETY_INSTRUCTION_TOPICS,
    possible_cause_violations,
    problem_help_violations,
)
from voiney_lab.experiment_protocol_analysis import (
    OpenAICompatibleProtocolAnalysisModel,
)
from voiney_lab.experiment_protocol_config import (
    ProtocolConfigurationError,
    ProtocolFeatureDisabledError,
    ProtocolPersistenceSettings,
)
from voiney_lab.experiment_protocol_pdf import (
    PDF_MEDIA_TYPE,
    MAX_PROTOCOL_PDF_BYTES,
    SOURCE_MEDIA_TYPES,
    TEXT_MEDIA_TYPE,
    ProtocolPdfEncryptedError,
    ProtocolPdfError,
    ProtocolPdfMalformedError,
    ProtocolPdfTooLargeError,
    ProtocolPdfWorkerError,
    ProtocolPdfTypeError,
    ProtocolSourceConversionError,
    ProtocolSourceConversionUnavailableError,
    ProtocolSourceFormatError,
    ProtocolSourceTooLargeError,
    convert_source_to_pdf,
    extract_protocol_pdf,
    file_sha256,
    find_office_converter,
    pdf_filename,
    source_media_type,
)
from voiney_lab.experiment_protocol_store import (
    PROTOCOL_DATABASE_FILENAME,
    initialize_protocol_store,
)
from voiney_lab.experiment_reports import (
    ExperimentReportSettings,
    ExperimentReportStore,
    REPORT_PROSE,
    REVIEW_CONFIRMED,
    REVIEW_DEFERRED,
    pi_confirmations,
    record_amendments,
    report_review_items,
    report_protocol_fixture,
    report_timezone,
    render_notebook_binder_docx,
    render_notebook_binder_pdf,
    sample_in_text,
    ReportNarrative,
    ReportPdfError,
    ReportWriterBrain,
    ReportWriterSettings,
    new_session_id,
)
from voiney_lab.external_references import (
    SupplementalKnowledgeSettings,
    XaiSupplementalKnowledge,
    plan_research_query,
    public_reference_part,
    supplemental_explanation_query,
    supplemental_knowledge_allowed,
)
from voiney_lab.safety_pack import (
    NO_INSTITUTION_DOCUMENTS_NOTICE,
    SOURCE_PROTOCOL_ONLY_COVERAGE,
    SOURCE_PROTOCOL_ONLY_SCOPE,
    SafetyPack,
    resolve_safety_pack,
    unavailable_safety_pack,
)
from voiney_lab.protocol_catalog import (
    ProtocolAnalysisPageRangeInvalidError,
    ProtocolAnalysisUnavailableError,
    ProtocolCatalog,
    ProtocolCatalogEntry,
    ProtocolCatalogError,
    ProtocolCatalogNotFoundError,
    ProtocolCatalogUnavailableError,
    ProtocolRegistrationError,
    ProtocolSourceDisclosure,
    ProtocolSourceStatus,
    ProtocolReviewError,
    ProtocolReviewTargetChangedError,
    REAGENT_TABLE_MEDIA_TYPE,
    MAX_REAGENT_TABLE_BYTES,
    ReagentTableError,
    ReagentTableTooLargeError,
    reagent_table_summary,
    review_version_suggestion,
)
from voiney_lab.public_knowledge import (
    KnowledgeUnavailable as PublicKnowledgeUnavailable,
    PublicKnowledgeConfigurationError,
    PublicKnowledgeSettings,
    problem_query,
    start_public_knowledge,
)
from voiney_lab.protocol_link_import import (
    LinkImportError,
    fetch_google_link,
    parse_google_link,
    record_source_link,
    upload_request,
)
from voiney_lab.protocol_ocr_providers import TEXT_LAYER as OCR_TEXT_LAYER_PROVIDER
from voiney_lab.protocol_ocr import (
    ProtocolOcrError,
    ProtocolOcrProvider,
    ProtocolOcrUnavailableError,
)
from voiney_lab.document_store import CATALOG_SCHEMA_VERSION
from voiney_lab.emergency import recognize_emergency
from voiney_lab.language import (
    CLARIFICATION_TEXT, InputLanguagePreference, ServerVoicePolicy,
    Transcription, classify_input_event, classify_transcription_language,
    clean_speech_text,
    normalize_input_language_preference,
    normalize_provider_language,
    resolve_turn_language, transcription_quality_issue,
)
from voiney_lab.intent_arbitration import arbitrate_request
from voiney_lab.knowledge import (
    HistoryKnowledge,
    SafetyKnowledge,
    compose_grounded_problem_answer,
    compose_problem_help,
    search_tiers,
)
from voiney_lab.runtime_metrics import RUNTIME_METRICS
from voiney_lab.pilot_metrics import (
    ACCOUNT_ID_PATTERN as PILOT_ACCOUNT_PATTERN,
    DAY_COLUMNS,
    DEVELOPMENT_ACCOUNT_ID,
    PILOT_METRICS,
    PILOT_METRICS_FILENAME,
    FeedbackAnswer,
    PilotContext,
    PilotMetricsStore,
    PilotSources,
    analysis_outcome,
    day_rows,
    day_totals,
    review_card_count,
)
from voiney_lab.multi_brain import (
    AnswerBrainOutput,
    BrainClaim,
    BrainActivation,
    BrainFact,
    BrainSnapshot,
    HybridMultiBrain,
    MultiBrainSettings,
    SourceBrainOutput,
    VisualBrainOutput,
    activation_for,
)
from voiney_lab.tools import ToolContext
from voiney_lab.protocol import ProtocolError, audio_segment_start, event, parse_control
from voiney_lab.llm_router import (
    FRAGMENT_JOIN_SECONDS,
    LlmRouterSettings,
    RouterTurnOutcome,
    front_history_turn,
    history_before,
    history_turn,
    incomplete_fragment,
    route_turn_with_llm_router,
    screen_history_turn,
)
from voiney_lab.model_providers import RoleModel, chat_client
from voiney_lab import pdf_text_engine, source_figures
from voiney_lab.web_explanations import (
    WEB_PHOTOS,
    WebExplanationSettings,
    explain_with_web,
    find_commons_photo,
)
from voiney_lab.drawn_diagrams import (
    DRAWN_DIAGRAMS,
    DRAWN_LABEL,
    DrawnDiagramSettings,
    draw_step_diagram,
)
from voiney_lab.runtime_routing import (
    CuratedRuntimeRoute,
    route_curated_runtime_turn,
)
from voiney_lab.setting_names import refuse_old_setting_names
from voiney_lab.vad import (
    DEFAULT_SENSITIVITY, SENSITIVITY_MARGINS_DB, EndpointDetector, EndpointResult,
    NoiseFloor, SpeakerLevelReference, TurnState, VadConfig, WebRtcVadClassifier,
    level_reference_from, voiced_median_level_db,
)
from voiney_lab import voice_providers
from voiney_lab import accounts as accounts_module
from voiney_lab.voice_providers import (
    SpeechProviderError,
    SttAdaptationFallback,
    SttProviderSettings,
    TtsProviderSettings,
)
from voiney_lab.start_check import (
    RunChange,
    RunChangeError,
    RunChanges,
    StartCheck,
    StartCheckError,
    changeable_values,
    start_check_enabled,
)
from voiney_lab.accounts import (
    DEVICE_COOKIE_LIFETIME,
    DEVICE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME,
    ACCOUNTS_DATABASE_FILENAME,
    Account,
    AccountDisabledError,
    AccountError,
    AccountNameInvalidError,
    AccountNameTakenError,
    AccountNotFoundError,
    AccountStore,
    AdminRequiredError,
    AppAccessSettings,
    AuthSettings,
    InviteInvalidError,
    LastAdminError,
    LoginLockedError,
    LoginRefusedError,
    PasswordInvalidError,
    bearer_token,
    device_marker,
    new_device_marker,
    normalize_origin,
    socket_auth_token,
)
from voiney_lab.identity import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    DevIdentityProvider,
    IdentityConfigurationError,
    IdentityResolver,
    OidcSettings,
    Principal,
)
from voiney_lab.protocol_translation import (
    BATCH_TIMEOUT_SECONDS,
    lab_korean,
    STYLE_GUIDE as PROTOCOL_TRANSLATION_STYLE,
    STYLE_VERSION as TRANSLATION_STYLE_VERSION,
    generate_revision_translations,
    glossaries_by_version,
    glossary_entries,
    is_korean,
    openai_batch_translator,
    openai_glossary_maker,
    statement_issue,
    stored_checks,
    translation_revision_key,
    translation_units,
    with_stored_translations,
)
from voiney_lab.workspace_store import (
    experimenter_setting_value_allowed,
    EXPERIMENTER_SETTING_VALUES,
    ApprovalReplayError,
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WORKSPACE_DATABASE_FILENAME,
    WorkspaceSettings,
    initialize_workspace_store,
)

PROJECT_ROOT=Path(__file__).resolve().parents[2]


def _load_project_environment(path:Path|None=None)->bool:
    """Load development values without overriding the process environment.

    Under pytest (PYTEST_VERSION is set) the repository .env is not read
    (lane M1, decision 6): real provider or OCR keys kept there must never
    reach a test. Only the old-setting-name check still looks at the file
    (tests/conftest.py). An explicit ``path`` is always read.
    """

    if path is None and os.environ.get("PYTEST_VERSION"):
        return False
    return load_dotenv(path or PROJECT_ROOT/".env",override=False)


_load_project_environment()
# Refuse to start while an old setting name is set, in the process environment
# or the .env just loaded, so an unmigrated .env cannot fall back to defaults.
refuse_old_setting_names()
# xAI is no longer a default (lane XO, decision 2): a feature only xAI provides
# is refused at start-up, by name, when it is on without XAI_API_KEY.
refuse_xai_only_features_without_key()
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("voiney_lab")

_LOG_QUERY_KEY=re.compile(r"[A-Za-z0-9_.-]{1,64}")
_LOG_ANY_QUERY=re.compile(r"\?[^\s\"']+")
_LOG_QUERY_REMOVED="?<query removed>"
# Lane FX5, decision 4 (2026-10-20): the invite token is a path segment
# (GET /api/auth/invites/<token>), which uvicorn's access line prints whole.
_LOG_PATH_SECRET=re.compile(r"(/api/auth/invites/)[^/?#\s\"']+")
_LOG_PATH_SECRET_REMOVED=r"\1<token removed>"


def mask_path_secrets(value:object)->object:
    """``value`` without the secret a URL path carries (an invite's token)."""

    return _LOG_PATH_SECRET.sub(_LOG_PATH_SECRET_REMOVED,value) if isinstance(value,str) else value
_UVICORN_ACCESS_LINE='%s - "%s %s HTTP/%s" %d'
_UVICORN_WEBSOCKET_LINE='%s - "WebSocket %s"'


def query_lengths_only(path:str)->str:
    """``/p?search=abc&page=2`` -> ``/p?search=<3 chars>&page=<1 chars>``.

    A part that is not a plain ``key=value`` drops the whole query string,
    since a shape this does not expect may carry the very text it removes.
    """
    base,separator,query=path.partition("?")
    if not separator:
        return path
    parts=[]
    for part in query.split("&"):
        key,equals,value=part.partition("=")
        if not equals or not _LOG_QUERY_KEY.fullmatch(key):
            return base+_LOG_QUERY_REMOVED
        parts.append(f"{key}=<{len(unquote_plus(value))} chars>")
    return f"{base}?{'&'.join(parts)}"


class UvicornQueryStringFilter(logging.Filter):
    """Keep uvicorn's request lines, but only the length of each query value.

    uvicorn writes the full URL into its access line and its WebSocket line,
    which would put a typed search (``?search=``) or an uploaded file name
    (``?filename=``) in the log. A record in the shape uvicorn 0.52 emits keeps its path with each
    value replaced by its length; any other record on these loggers loses
    every query string it carries, so a format change errs toward removal.
    """

    def filter(self,record:logging.LogRecord)->bool:
        args=record.args
        if (record.msg==_UVICORN_ACCESS_LINE and isinstance(args,tuple)
                and len(args)==5 and isinstance(args[2],str)):
            record.args=(*args[:2],query_lengths_only(args[2]),*args[3:])
        elif (isinstance(record.msg,str) and record.msg.startswith(_UVICORN_WEBSOCKET_LINE)
                and isinstance(args,tuple) and len(args)>=2 and isinstance(args[1],str)):
            record.args=(args[0],query_lengths_only(args[1]),*args[2:])
        else:
            strip=lambda value:(_LOG_ANY_QUERY.sub(_LOG_QUERY_REMOVED,value)
                                if isinstance(value,str) else value)
            if isinstance(args,tuple):
                record.args=tuple(strip(value) for value in args)
            elif isinstance(args,dict):
                record.args={key:strip(value) for key,value in args.items()}
            if isinstance(record.msg,str) and record.args and "?" in record.msg:
                # The query may be split between the format and its arguments.
                try:
                    record.msg,record.args=strip(record.getMessage()),()
                except Exception:
                    record.msg,record.args=strip(record.msg),()
            else:
                record.msg=strip(record.msg)
        # Whatever shape the record has, a path's secret is removed (decision 4).
        if isinstance(record.args,tuple):
            record.args=tuple(mask_path_secrets(value) for value in record.args)
        elif isinstance(record.args,dict):
            record.args={key:mask_path_secrets(value) for key,value in record.args.items()}
        record.msg=mask_path_secrets(record.msg)
        return True


def install_uvicorn_query_string_filter()->None:
    """Attach the filter, once, to the loggers uvicorn writes request lines to.

    Importing this module is how uvicorn loads the app, and uvicorn has set up
    its logging by then; its dictConfig replaces handlers, not filters.
    """
    for name in ("uvicorn.access","uvicorn.error"):
        logger=logging.getLogger(name)
        if not any(isinstance(item,UvicornQueryStringFilter) for item in logger.filters):
            logger.addFilter(UvicornQueryStringFilter())


install_uvicorn_query_string_filter()


def log_effective_vad_configuration(settings:VoiceVadSettings)->None:
    """Log non-secret endpoint settings once when the application starts."""
    cascade=settings.cascade
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
        "cascade_playback_onset_window_frames=%d",
        cascade.mode,cascade.onset_voiced_frames,cascade.onset_window_frames,
        cascade.listening_onset_voiced_frames,
        cascade.listening_onset_window_frames,
        cascade.listening_resume_voiced_frames,
        cascade.listening_resume_window_frames,
        cascade.prefix_ms,cascade.endpoint_silence_ms,
        cascade.minimum_speech_ms,cascade.maximum_utterance_ms,
        cascade.cooldown_ms,cascade.playback_onset_voiced_frames,
        cascade.playback_onset_window_frames,
    )


def log_cascade_filler_configuration()->None:
    """Check the waiting-cue settings once at startup and say which cue is on."""
    log.info(
        "cascade_filler.configuration mode=%s delay_ms=%d status_delay_ms=%d "
        "status_speech=%s",
        cascade_filler_mode(),cascade_filler_delay_ms(),
        cascade_filler_status_delay_ms(),
        "on" if cascade_filler_status_speech_enabled() else "off",
    )


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Log the effective configuration, install the OCR provider, load public knowledge.

    The Moss retrieval of approved manuals this used to warm was deleted on
    2026-10-10 (lane CL) and is not back: what loads here (lane KNOW, human
    decision 2026-10-24) is the licence-checked public knowledge index, a
    different purpose, and only when ``VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED``
    says so. Its failure is its own: the server starts without that tier and
    says which setting is missing.
    """
    log_effective_vad_configuration(VoiceVadSettings.from_environment())
    log_cascade_filler_configuration()
    await asyncio.to_thread(log_protocol_catalog_runtime_configuration)
    await asyncio.to_thread(_migrate_report_ownership)
    _install_protocol_ocr_provider()
    _log_source_format_support()
    await _start_public_knowledge(application)
    try:
        yield
    finally:
        await _stop_public_knowledge(application)


def _public_knowledge_cache_dir()->str|None:
    """Where Moss keeps the downloaded index between starts: beside the protocol catalog."""

    try:
        settings=_protocol_store_settings()
    except Exception:  # noqa: BLE001 - the catalog's own start reports its settings
        return None
    if settings.enabled and settings.data_dir is not None:
        return str(settings.data_dir/"public-knowledge-cache")
    return None


async def _start_public_knowledge(application:FastAPI)->None:
    """Load the public knowledge index once at start (lane KNOW, decision 4).

    Off: nothing is built. On without ``MOSS_PROJECT_ID``/``MOSS_PROJECT_KEY``,
    or with a load that fails: this tier alone stays off, the reason is the
    setting's name (never a value), and everything else runs. No other
    provider is tried in its place.
    """

    application.state.public_knowledge=None
    status:dict[str,object]={"enabled":False,"loaded":False,"index":None,"reason":None}
    try:
        settings=PublicKnowledgeSettings.from_environment(cache_dir=_public_knowledge_cache_dir())
    except PublicKnowledgeConfigurationError as exc:
        status.update(enabled=True,reason=str(exc))
        application.state.public_knowledge_status=status
        log.warning("public_knowledge.unavailable reason=configuration detail=%s",exc)
        return
    status["enabled"]=settings.enabled
    if not settings.enabled:
        application.state.public_knowledge_status=status
        log.info("public_knowledge disabled (%s)","VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED")
        return
    status["index"]=settings.index_name
    try:
        source=await start_public_knowledge(settings)
    except PublicKnowledgeUnavailable as exc:
        status["reason"]=exc.setting
        application.state.public_knowledge_status=status
        log.warning("public_knowledge.unavailable setting=%s",exc.setting)
        return
    application.state.public_knowledge=source
    report=source.load_report if source is not None else None
    status.update(loaded=True,load_ms=report.elapsed_ms if report else None,
                  glossary_terms=report.glossary_terms if report else 0,
                  embedding=getattr(source,"embedding",None))
    application.state.public_knowledge_status=status
    log.info("public_knowledge.loaded index=%s load_ms=%s glossary_terms=%s embedding=%s",
             settings.index_name,status["load_ms"],status["glossary_terms"],status["embedding"])


async def _stop_public_knowledge(application:FastAPI)->None:
    source=getattr(application.state,"public_knowledge",None)
    application.state.public_knowledge=None
    if source is None:
        return
    try:
        await source.close()
    except Exception as exc:  # noqa: BLE001 - shutting down; the type is enough
        log.warning("public_knowledge.close_failed error=%s",type(exc).__name__)


def _public_knowledge_capability()->dict[str,object]:
    """The content-free state of the public tier for ``ready`` and ``/readyz``."""

    status=getattr(app.state,"public_knowledge_status",None) or {"enabled":False,"loaded":False,
                                                                  "index":None,"reason":None}
    if not status.get("enabled"):
        state="disabled"
    elif status.get("loaded"):
        state="enabled"
    else:
        state="unavailable"
    return {
        "status":state,
        "authority":"public_reference",
        "index":status.get("index") if state=="enabled" else None,
        "reason":status.get("reason"),
    }


def _docx_conversion_available()->bool:
    return find_office_converter() is not None


def _heic_photos_available()->bool:
    return importlib.util.find_spec("pillow_heif") is not None


def _log_source_format_support()->None:
    """Say at start-up what turns a .docx and a HEIC photo into a PDF (lane AN, FM).

    LibreOffice must be installed on the server (human decision 2026-10-13);
    without it every .docx upload is refused, so its absence is a warning.
    HEIC is optional (``pip install -e '.[heic]'``).
    """

    if not _docx_conversion_available():
        log.warning(
            "protocol.source_formats docx=unavailable reason=libreoffice_not_found "
            "setting=VOINEY_LAB_OFFICE_CONVERTER install='apt-get install "
            "libreoffice-writer-nogui fonts-nanum'")
    else:
        log.info("protocol.source_formats docx=available")
    log.info("protocol.source_formats heic=%s",
             "available" if _heic_photos_available()
             else "unavailable(optional: pip install -e '.[heic]')")


app=FastAPI(title="Voice Workflow Agent",lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def request_validation_without_credentials(request:Request,exc:RequestValidationError):
    """FastAPI's 422, but on the account routes without the value that was sent.

    Lane FX5, decision 4 (2026-10-20): the default answer repeats each
    rejected input, so a password (or a token) sent in a body of the wrong
    shape -- a JSON list, say -- came straight back, into whatever keeps
    responses. Every other route answers as FastAPI does.
    """

    if not request.url.path.startswith("/api/auth/"):
        return await request_validation_exception_handler(request,exc)
    errors=[{key:value for key,value in error.items() if key!="input"} for error in exc.errors()]
    return JSONResponse(status_code=422,content={"detail":jsonable_encoder(errors)})


@app.get("/healthz")
async def healthz()->dict[str,object]:
    """Liveness only: the process is up and can serve a request."""
    return {"status":"ok"}


@app.get("/readyz")
async def readyz()->JSONResponse:
    """Readiness: required configuration parses without exposing secrets.

    Optional providers (protocol analysis) report their configured
    state rather than being required for the process to be "ready" - this
    endpoint distinguishes configuration health from live external-provider
    reachability, which no local health check can verify without a real
    network call.
    """
    capabilities:dict[str,object]={}
    try:
        identity=_identity_resolver()
        workspace=_workspace_settings()
        protocol=_protocol_store_settings()
        reports=ExperimentReportSettings.from_environment()
        # Lane APP1, decision 4: the app origins parse (exact origins only).
        _app_access()
        start_check_enabled()
        server_config()
        if os.environ.get(voice_providers.STT_PROVIDER_ENV, "").strip().casefold() == voice_providers.GOOGLE_CLOUD_V2:
            SttProviderSettings.from_environment()
        # Lane ON, decision ① (2026-10-18): "accounts" when the pilot's
        # login is on (VOINEY_LAB_AUTH_MODE=accounts).
        capabilities["identity_mode"]=(
            "accounts" if _auth_settings().accounts
            else "oidc" if identity.oidc_settings is not None else "development"
        )
        capabilities["workspace_enabled"]=workspace.enabled
        capabilities["protocol_catalog_enabled"]=protocol.enabled
        capabilities["experiment_reports_enabled"]=reports.enabled
        # Lane AN, FM (human decision 2026-10-13): LibreOffice must be
        # installed on the server for .docx sources; HEIC photos need the
        # optional pillow-heif. Reported, not required for readiness.
        capabilities["docx_conversion_available"]=_docx_conversion_available()
        capabilities["heic_photos_available"]=_heic_photos_available()
        # Lane KNOW (2026-10-24): disabled, enabled (loaded) or unavailable
        # (on, but the credentials or the load are missing) -- never required.
        capabilities["public_knowledge"]=_public_knowledge_capability()["status"]
    except Exception as exc:
        if isinstance(exc,(ServerConfigurationError,voice_providers.GoogleSttV2ConfigurationError)):
            return JSONResponse(status_code=503,content={
                "status":"not_ready","reason":(
                    "speech_configuration_invalid" if isinstance(exc,voice_providers.GoogleSttV2ConfigurationError)
                    else "safety_configuration_invalid"),
                "fields":list(exc.field_names),
            })
        return JSONResponse(status_code=503,content={
            "status":"not_ready",
            "reason":type(exc).__name__,
        })
    return JSONResponse(status_code=200,content={
        "status":"ok",
        "capabilities":capabilities,
    })


STATIC_DIR=Path(__file__).with_name("static")
#: The analysis a worker holds for a protocol, or the slot reserved for
#: one that is being recorded (lane RN, decision 1).
_PROTOCOL_ANALYSIS_TASKS:dict[str,asyncio.Future[None]]={}
_PROTOCOL_OCR_TASKS:dict[str,asyncio.Future[None]]={}
_REQUEST_PRINCIPAL:contextvars.ContextVar[Principal|None]=contextvars.ContextVar(
    "voice_workflow_request_principal",default=None)


def _runtime_usage_scope()->str:
    scope=os.environ.get("VOINEY_LAB_USAGE_SCOPE","").strip()
    if scope not in {"operational","demo","reference_only","test_only","source_protocol_only"}:
        raise ServerConfigurationError(
            "VOINEY_LAB_USAGE_SCOPE must name a usage scope","VOINEY_LAB_USAGE_SCOPE")
    return scope


def _identity_resolver()->IdentityResolver:
    scope=_runtime_usage_scope()
    oidc=OidcSettings.from_environment()
    return IdentityResolver(
        usage_scope=scope,
        oidc_settings=oidc,
        dev_provider=(
            None if scope=="operational" else DevIdentityProvider.from_environment()
        ),
    )


def _workspace_settings()->WorkspaceSettings:
    return WorkspaceSettings.from_environment()


# --- Accounts: the pilot's login (lane ON, decision ①, 2026-10-18) -----------
# VOINEY_LAB_AUTH_MODE=accounts puts a session cookie in front of every /api
# request and the /ws connection (the health paths and the admin metrics,
# which carry their own token, excepted). An account is the experimenter
# principal of its lab; the workspace store sees it as it saw the development
# identity. In the default mode (dev) nothing below runs.
#: The account behind this request, when the door is accounts; None otherwise.
_REQUEST_ACCOUNT:contextvars.ContextVar[Account|None]=contextvars.ContextVar(
    "voice_workflow_request_account",default=None)
_ACCOUNT_VOICE_CONNECTIONS:dict[str,dict[asyncio.Task,WebSocket]]={}
_ACCOUNT_VOICE_REPORT_WRITES:dict[str,set[asyncio.Task]]={}


async def _voice_report_write(function:Callable[...,Any],*args:Any,**kwargs:Any)->Any:
    """Keep an already-started report write alive until account erasure can join it.

    Cancelling the voice coroutine does not stop its worker thread. Shield and
    track only that existing write, not another turn or a workflow authority.
    """

    account=_REQUEST_ACCOUNT.get()
    if account is None:
        return await asyncio.to_thread(function,*args,**kwargs)
    account_id=account.account_id

    async def run()->tuple[Any,Exception|None]:
        try:
            return await asyncio.to_thread(function,*args,**kwargs),None
        except Exception as exc:
            # A cancelled shield can log an inner exception's raw message on
            # Python 3.14. Keep it as data until the live caller can raise it.
            return None,exc

    worker=asyncio.create_task(run())
    _ACCOUNT_VOICE_REPORT_WRITES.setdefault(account_id,set()).add(worker)

    def finished(completed:asyncio.Task)->None:
        pending=_ACCOUNT_VOICE_REPORT_WRITES.get(account_id,set())
        pending.discard(completed)
        if not pending:
            _ACCOUNT_VOICE_REPORT_WRITES.pop(account_id,None)
        if not completed.cancelled():
            _,failure=completed.result()
            if failure is not None:
                log.warning("voice.report_write_failed error=%s",type(failure).__name__)

    worker.add_done_callback(finished)
    result,failure=await asyncio.shield(worker)
    if failure is not None:
        raise failure
    return result


async def _close_account_voice_connections(account_id:str)->None:
    """Finish voice tasks before account erasure can remove their stores."""

    connections=list(_ACCOUNT_VOICE_CONNECTIONS.pop(account_id,{}).items())
    current=asyncio.current_task()
    pending=[task for task,_ in connections if task is not current]
    for task in pending:
        if not task.cancelling():
            task.cancel()
    if pending:
        await asyncio.gather(*pending,return_exceptions=True)
    for _,socket in connections:
        try:
            await socket.close(code=1008,reason="authentication required")
        except (RuntimeError,WebSocketDisconnect):
            pass  # Already disconnected.
    # The voice tasks cannot start new writes now. Finish existing worker
    # threads before erasure takes its inventory, otherwise a late open_report
    # could recreate an owner's row after that inventory was deleted.
    while writes:=tuple(_ACCOUNT_VOICE_REPORT_WRITES.get(account_id,())):
        await asyncio.gather(*(asyncio.shield(write) for write in writes),return_exceptions=True)


async def _watch_account_voice_session(token:str,task:asyncio.Task,websocket:WebSocket)->None:
    """Also notice expiry and account changes made by an operator's tool."""

    while True:
        await asyncio.sleep(1)
        try:
            await asyncio.to_thread(_account_for_cookie,token)
        except Exception:
            if task.cancelling():
                return
            try:
                await websocket.close(code=1008,reason="authentication required")
            except (RuntimeError,WebSocketDisconnect):
                pass
            task.cancel()
            return
#: /api paths that answer without a session: the session question itself,
#: logging in, joining, reading an invite, and the admin metrics (own token).
_ACCOUNT_OPEN_PATHS=frozenset({
    "/api/auth/session","/api/auth/login","/api/auth/signup","/api/admin/metrics"})
_ACCOUNT_OPEN_PREFIXES=("/api/auth/invites/",)


def _auth_settings()->AuthSettings:
    return AuthSettings.from_environment()


def _account_store()->AccountStore:
    """The account store beside the workspace's database (accounts mode)."""

    settings=_workspace_settings()
    if not settings.enabled or settings.data_dir is None:
        raise IdentityConfigurationError("Accounts need the tenant workspace.")
    return AccountStore.open(settings.data_dir/ACCOUNTS_DATABASE_FILENAME)


def _account_for_cookie(token:str|None)->Account:
    """The signed-in account of a session token (cookie or app bearer), or the refusal."""

    if not token:
        raise AuthenticationRequiredError("Authentication is required.")
    store=_account_store()
    try:
        account=store.account_for_session(token)
    finally:
        store.close()
    if account is None:
        raise AuthenticationRequiredError("Authentication is required.")
    return account


# Lane APP1, decision 3 (2026-10-19): the app's bundled screen runs at its
# own origin, where the cookie never reaches us. For an origin listed in
# VOINEY_LAB_APP_ORIGINS -- only for one -- the session token travels in the
# login answer, an Authorization: Bearer header and the socket's first
# message (accounts.py, "The app's screen"). Empty setting: nothing changes.
def _app_access()->AppAccessSettings:
    return AppAccessSettings.from_environment()


def _from_app(headers)->bool:
    """Whether the request comes from a listed app origin (the browser sets Origin; a page cannot)."""

    origin=headers.get("origin")
    return bool(origin) and _app_access().allows(origin)


def _session_token(request:Request)->str|None:
    """The session token of this request: the app's bearer from a listed origin, else the cookie."""

    if _from_app(request.headers):
        token=bearer_token(request.headers.get("authorization"))
        if token:
            return token
    return request.cookies.get(SESSION_COOKIE_NAME)


def _signed_in_response(request:Request,content:dict[str,Any],token:str,*,status_code:int=200)->JSONResponse:
    """A login or signup answer: the cookie for the web, the token in the body for the app."""

    if _from_app(request.headers):
        return JSONResponse(status_code=status_code,content={
            **content,
            "app_session":{
                "token":token,
                "expires_in_seconds":int(SESSION_LIFETIME.total_seconds()),
            },
        })
    response=JSONResponse(status_code=status_code,content=content)
    _set_session_cookie(response,token,request)
    _ensure_device_cookie(response,request)
    return response


async def _app_socket_token(websocket:WebSocket)->str:
    """The token an app's socket sends first; anything else (or nothing in time) is the refusal."""

    try:
        message=await asyncio.wait_for(
            websocket.receive(),timeout=accounts_module.APP_SOCKET_AUTH_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise AuthenticationRequiredError("Authentication is required.") from exc
    token=(
        socket_auth_token(message.get("text"))
        if message.get("type")=="websocket.receive" else None)
    if token is None:
        raise AuthenticationRequiredError("Authentication is required.")
    return token


def _request_account()->Account|None:
    return _REQUEST_ACCOUNT.get()


def _account_is_admin()->bool:
    account=_REQUEST_ACCOUNT.get()
    return account is not None and account.admin


# Lane PRE, decision 3 (2026-10-24): the login lock counts by (name, device).
# Behind the tunnel the peer address is the proxy's, so the device is a random
# marker the browser keeps in an HttpOnly cookie (accounts.py, "The device a
# login comes from"); the app's screen, which has no cookie, sends its own in
# the request body. Without one the request is the one unknown device.
def _request_device(request:Request,body:dict[str,Any]|None=None)->str|None:
    """The device marker of this request: the body's from a listed app origin, else the cookie."""

    if _from_app(request.headers):
        return device_marker((body or {}).get("device"))
    return device_marker(request.cookies.get(DEVICE_COOKIE_NAME))


def _ensure_device_cookie(response:Response,request:Request)->None:
    """Give a browser without a (well-formed) device marker a new one, for a year."""

    if _from_app(request.headers) or device_marker(request.cookies.get(DEVICE_COOKIE_NAME)):
        return
    response.set_cookie(
        DEVICE_COOKIE_NAME,new_device_marker(),max_age=int(DEVICE_COOKIE_LIFETIME.total_seconds()),
        httponly=True,samesite="lax",secure=_request_is_secure(request),path="/")


def _request_is_secure(request:Request)->bool:
    """Whether the browser reached us over HTTPS (directly or through the proxy)."""

    forwarded=request.headers.get("x-forwarded-proto","").split(",")[0].strip().casefold()
    return request.url.scheme=="https" or forwarded=="https"


def _set_session_cookie(response:Response,token:str,request:Request)->None:
    response.set_cookie(
        SESSION_COOKIE_NAME,token,max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,samesite="lax",secure=_request_is_secure(request),path="/")


def _clear_session_cookie(response:Response,request:Request)->None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,path="/",httponly=True,samesite="lax",secure=_request_is_secure(request))


def _accounts_only()->None:
    """The account routes answer only when the door is accounts."""

    if not _auth_settings().accounts:
        raise HTTPException(status_code=409,detail="accounts_disabled")


def _review_body_with_account(body:dict[str,Any])->dict[str,Any]:
    """The reviewer's name is the account's name when someone is signed in (decision ①)."""

    account=_REQUEST_ACCOUNT.get()
    if account is None:
        return body
    return {**body,"reviewer_name":account.name}


def _workspace_http_error(exc:Exception)->HTTPException:
    if isinstance(exc,AccountError):
        status={
            InviteInvalidError:404,AccountNotFoundError:404,AccountNameTakenError:409,
            AccountNameInvalidError:422,PasswordInvalidError:422,LoginRefusedError:401,
            LoginLockedError:429,AccountDisabledError:403,AdminRequiredError:403,
            LastAdminError:409,
        }.get(type(exc),400)
        headers=(
            {"Retry-After":str(exc.retry_after_seconds)}
            if isinstance(exc,LoginLockedError) else None)
        return HTTPException(status_code=status,detail=exc.code,headers=headers)
    if isinstance(exc,AuthenticationRequiredError):
        return HTTPException(status_code=401,detail=exc.code)
    if isinstance(exc,AuthorizationDeniedError):
        return HTTPException(status_code=403,detail=exc.code)
    if isinstance(exc,WorkspaceNotFoundError):
        return HTTPException(status_code=404,detail=exc.code)
    if isinstance(exc,(ApprovalReplayError,WorkspaceConflictError)):
        return HTTPException(status_code=409,detail=exc.code)
    if isinstance(exc,(IdentityConfigurationError,ConfigurationError,ServerConfigurationError)):
        return HTTPException(status_code=503,detail=getattr(exc,"code","configuration_invalid"))
    return HTTPException(
        status_code=400 if isinstance(exc,WorkspaceError) else 500,
        detail=getattr(exc,"code","workspace_error"),
    )


@app.middleware("http")
async def commercial_identity_boundary(request:Request,call_next):
    """Authenticate every API request when the commercial workspace is enabled."""

    if (not request.url.path.startswith("/api/")
            and request.url.path not in {"/docs","/docs/oauth2-redirect","/redoc","/openapi.json"}):
        return await call_next(request)
    try:
        if request.method not in {"GET","HEAD","OPTIONS"}:
            origin=request.headers.get("origin")
            if origin and not _request_origin_allowed(request,origin):
                return JSONResponse(status_code=403,content={"detail":"origin_not_allowed"})
        auth=_auth_settings()
        settings=_workspace_settings()
        operational=_runtime_usage_scope()=="operational"
        account:Account|None=None
        if auth.accounts:
            # Lane ON, decision ①: the session cookie is the door.
            path=request.url.path
            if path in _ACCOUNT_OPEN_PATHS or path.startswith(_ACCOUNT_OPEN_PREFIXES):
                return await call_next(request)
            if not settings.enabled:
                raise IdentityConfigurationError("Accounts need the tenant workspace.")
            account=_account_for_cookie(_session_token(request))
            principal=account.principal()
        else:
            if not settings.enabled and not operational:
                return await call_next(request)
            if (
                operational
                and not settings.enabled
                and request.url.path.endswith("/activate-development")
            ):
                return await call_next(request)
            if not settings.enabled:
                raise IdentityConfigurationError(
                    "Operational scope requires the tenant workspace."
                )
            principal=_identity_resolver().resolve(request.headers.get("authorization"))
        store=initialize_workspace_store(settings)
        try:
            store.bootstrap_principal(principal)
            principal=store.effective_principal(principal)
        finally:
            store.close()
        request.state.principal=principal
        token=_REQUEST_PRINCIPAL.set(principal)
        account_token=_REQUEST_ACCOUNT.set(account)
        try:
            return await call_next(request)
        finally:
            _REQUEST_ACCOUNT.reset(account_token)
            _REQUEST_PRINCIPAL.reset(token)
    except Exception as exc:
        error=_workspace_http_error(exc)
        return JSONResponse(
            status_code=error.status_code,content={"detail":error.detail,
                **({"fields":list(exc.field_names)} if isinstance(exc,ServerConfigurationError) else {})},
            headers=error.headers or None)


# Lane APP1, decision 4 (2026-10-19): CORS for the app's origins. Registered
# after commercial_identity_boundary, so it wraps it: the preflight is
# answered before any session is asked for, and a refusal (401, 429) carries
# the origin so the app can read why. No Allow-Credentials -- the app sends a
# bearer, and a cookie session is never offered to another origin. Only
# /api and the health paths the screen reads; the app carries its own copy
# of the screen's files. With VOINEY_LAB_APP_ORIGINS empty nothing is added.
_APP_CORS_PATHS=frozenset({"/readyz","/healthz"})
_APP_CORS_METHODS="GET, POST, PUT, PATCH, DELETE"
_SOURCE_STATUS_HEADER="x-voiney-source-status"
_SOURCE_NOTE_HEADER="x-voiney-source-note"
_APP_CORS_HEADERS=(
    "Authorization, Content-Type, Accept, "
    f"{_SOURCE_STATUS_HEADER}, {_SOURCE_NOTE_HEADER}"
)
_APP_CORS_EXPOSE="Retry-After, Content-Disposition"
_APP_CORS_MAX_AGE_SECONDS=600


def _with_vary_origin(headers)->None:
    vary=headers.get("vary","")
    if "origin" not in {item.strip().casefold() for item in vary.split(",")}:
        headers["vary"]=f"{vary}, Origin" if vary.strip() else "Origin"


@app.middleware("http")
async def app_origin_cors(request:Request,call_next):
    """CORS for the origins in VOINEY_LAB_APP_ORIGINS, on /api and the health paths."""

    path=request.url.path
    origin=request.headers.get("origin")
    if not origin or not (path.startswith("/api/") or path in _APP_CORS_PATHS):
        return await call_next(request)
    try:
        allowed=_app_access().allows(origin)
    except ConfigurationError:
        # An invalid list lets no app in; /readyz reports it.
        allowed=False
    if not allowed:
        return await call_next(request)
    if request.method=="OPTIONS" and request.headers.get("access-control-request-method"):
        preflight=Response(status_code=204,headers={
            "Access-Control-Allow-Origin":origin,
            "Access-Control-Allow-Methods":_APP_CORS_METHODS,
            "Access-Control-Allow-Headers":_APP_CORS_HEADERS,
            "Access-Control-Max-Age":str(_APP_CORS_MAX_AGE_SECONDS),
        })
        _with_vary_origin(preflight.headers)
        return preflight
    response=await call_next(request)
    response.headers["Access-Control-Allow-Origin"]=origin
    response.headers["Access-Control-Expose-Headers"]=_APP_CORS_EXPOSE
    _with_vary_origin(response.headers)
    return response


def _socket_origin_refusal(websocket:WebSocket)->str|None:
    """Allow the server's own browser origin or an explicitly listed app.

    Browsers are checked even with no app allowlist. Clients without Origin
    still need authentication. Compare the scheme, host and effective port.
    """

    try:
        settings=_app_access()
    except ConfigurationError:
        return "configuration_invalid"
    headers=getattr(websocket,"headers",{})
    origin=headers.get("origin")
    if origin is None:
        return None
    if settings.allows(origin):
        return None
    normalized=normalize_origin(origin)
    if normalized is not None:
        scheme=headers.get("x-forwarded-proto","").split(",")[0].strip().casefold()
        if not scheme:
            scheme=getattr(getattr(websocket,"url",None),"scheme","")
        scheme={"ws":"http","wss":"https"}.get(scheme,scheme)
        if scheme not in {"http","https"}:
            return "configuration_invalid"
        own_hosts={
            value.split(",")[0].strip().casefold()
            for value in (headers.get("host",""),headers.get("x-forwarded-host",""))
            if value.strip()
        }
        if any(_same_browser_origin(origin,f"{scheme}://{host}") for host in own_hosts):
            return None
    return "origin_not_allowed"


def _same_browser_origin(first:str,second:str)->bool:
    if normalize_origin(first) is None or normalize_origin(second) is None:
        return False
    def identity(value:str)->tuple[str,str,int|None]:
        parsed=urlsplit(value.strip().casefold())
        port=parsed.port
        return (parsed.scheme,parsed.hostname or "",
                port if port is not None else {"http":80,"https":443}.get(parsed.scheme))
    return identity(first)==identity(second)


def _request_origin_allowed(request:Request,origin:str)->bool:
    normalized=normalize_origin(origin)
    if normalized is None:
        return False
    scheme=request.headers.get("x-forwarded-proto",request.url.scheme).split(",")[0].strip()
    host=request.headers.get("x-forwarded-host",request.headers.get("host","")).split(",")[0].strip()
    if _same_browser_origin(origin,f"{scheme}://{host}"):
        return True
    # App origins use bearer authentication; cookies never authorise a
    # cross-origin write, including one from an allowlisted app origin.
    return _app_access().allows(origin) and SESSION_COOKIE_NAME not in request.cookies


def _commercial_workspace()->tuple[Principal,object]:
    settings=_workspace_settings()
    if not settings.enabled:
        raise WorkspaceError("Commercial workspace is disabled.")
    principal=_REQUEST_PRINCIPAL.get()
    if principal is None:
        raise AuthenticationRequiredError("Authentication is required.")
    store=initialize_workspace_store(settings)
    try:
        principal=store.effective_principal(principal)
    except Exception:
        store.close()
        raise
    return principal,store


def _voice_turn_actor()->tuple[str|None,str]:
    """Name the person whose voice this turn is, or say that nobody is named.

    A gate-releasing endpoint observation is written down with an actor. Where
    a workspace is configured the authenticated principal is that actor. Where
    none is, this is a single-operator host and there is no principal to name,
    so the record carries the role and leaves the identity empty -- an unclear
    identity is written as unclear, not filled in with "local".

    This never raises. Refusing a turn because identity is unavailable is a
    decision for the caller that needs the identity, and the turn path has
    one such caller: the observation write, which the release requires.
    """

    try:
        if not _workspace_settings().enabled:
            return None,"voice_operator"
        actor=_REQUEST_PRINCIPAL.get()
    except Exception:  # noqa: BLE001 - an unreadable setting names nobody
        return None,"voice_operator"
    if actor is None:
        return None,"voice_operator"
    role=next(iter(sorted(item.value for item in actor.roles)),"voice_operator")
    return actor.principal_id,role


def _experimenter_display_name()->str|None:
    """The signed-in principal's display name, or None where nobody is named.

    Like ``_voice_turn_actor``: without a workspace this is a single-operator
    host with no principal, and the report leaves the name for the researcher
    to write. Never raises.
    """

    try:
        if not _workspace_settings().enabled:
            return None
        actor=_REQUEST_PRINCIPAL.get()
    except Exception:  # noqa: BLE001 - an unreadable setting names nobody
        return None
    name=" ".join(str(getattr(actor,"display_name","") or "").split())
    return name[:120] or None


def _scope_catalog_resource(
    protocol_id:str,*,bind:bool=False
)->None:
    _scope_tenant_resource("protocol_catalog",protocol_id,bind=bind)


def _migrate_report_ownership()->None:
    settings=ExperimentReportSettings.from_environment()
    workspace_settings=_workspace_settings()
    if not settings.enabled or settings.database_path is None or not workspace_settings.enabled:
        return
    workspace=initialize_workspace_store(workspace_settings)
    try:
        counts=ExperimentReportStore(settings.database_path).backfill_ownership(workspace.report_ownership)
        log.info("report.ownership_migrated owned=%d admin_only=%d unscoped=%d",
                 counts["owned"],counts["admin_only"],counts["unscoped"])
    finally:
        workspace.close()


def _scope_tenant_resource(
    resource_type:str,resource_id:str,*,bind:bool=False,write:bool=False
)->None:
    settings=_workspace_settings()
    if not settings.enabled:
        return
    principal,store=_commercial_workspace()
    try:
        if bind:
            store.bind_resource(principal,resource_type,resource_id)
        elif resource_type=="experiment_report":
            report_settings=ExperimentReportSettings.from_environment()
            if not report_settings.enabled or report_settings.database_path is None:
                raise WorkspaceNotFoundError("Experiment report is not available.")
            reports=ExperimentReportStore(report_settings.database_path)
            reports.backfill_ownership(store.report_ownership)
            try:
                reports.require_access(
                    resource_id,organization_id=principal.organization_id,
                    owner_principal_id=principal.principal_id,
                    every_owner=not write and _account_is_admin())
            except KeyError as exc:
                raise WorkspaceNotFoundError("Experiment report is not available.") from exc
        else:
            store.require_resource(principal,resource_type,resource_id)
    finally:
        store.close()


def _visible_catalog_resource_ids()->frozenset[str]|None:
    if not _workspace_settings().enabled:
        return None
    principal,store=_commercial_workspace()
    try:
        return store.resource_ids(principal,"protocol_catalog")
    finally:
        store.close()


def _record_workspace_metric(
    *,
    category:str,
    metric_name:str,
    metric_value:float=1.0,
    dimensions:dict[str,str]|None=None,
    principal:Principal|None=None,
)->None:
    """Best-effort privacy-safe pilot telemetry; never affects workflow authority."""

    try:
        settings=_workspace_settings()
        if not settings.enabled:
            return
        actor=principal or _REQUEST_PRINCIPAL.get()
        if actor is None:
            return
        store=initialize_workspace_store(settings)
        try:
            actor=store.effective_principal(actor)
            store.record_analytics(
                actor,category=category,metric_name=metric_name,
                metric_value=metric_value,dimensions=dimensions,
            )
        finally:
            store.close()
    except Exception as exc:
        log.warning(
            "workspace.analytics.record_failed category=%s metric=%s error=%s",
            category,metric_name,type(exc).__name__,
        )


# --- Lane LOG: the pilot metrics -- numbers, codes and times only

def _pilot_store()->PilotMetricsStore|None:
    """The pilot metrics file in the workspace folder, or None without a workspace."""

    settings=_workspace_settings()
    if not settings.enabled or settings.data_dir is None:
        return None
    return PilotMetricsStore(settings.data_dir/PILOT_METRICS_FILENAME)


def _pilot_account(principal:Principal|None=None)->str:
    """The signed-in account's id, or the development identity's."""

    actor=principal or _REQUEST_PRINCIPAL.get()
    return actor.principal_id if actor is not None else DEVELOPMENT_ACCOUNT_ID


_PILOT_TURN_EVENTS=frozenset({
    "turn.processing","turn.route_decision","router.stopped","turn.done"})


def _pilot_turn_event(session,turn_id:int,kind:str,fields:dict[str,Any])->None:
    """Lane LOG, decision 1: a turn's codes, and at its end one log line and row.

    Never raises: a metrics write that fails is logged and the turn goes on.
    """

    if kind not in _PILOT_TURN_EVENTS:
        return
    if kind=="turn.processing":
        # Decision 2: the first words after a timer notice, timed from it.
        try:
            waited=PILOT_METRICS.alert_answered(
                session.session_id,session.turn_committed_at.get(turn_id,session.clock()))
        except Exception as exc:
            log.warning("pilot.metrics.failed kind=%s error=%s",kind,type(exc).__name__)
            return
        if waited is not None:
            _pilot_mark("alert_answered",session=session,value_ms=waited)
        return
    try:
        PILOT_METRICS.turn_event(
            PilotContext(
                account_id=_pilot_account(),session_id=session.session_id,
                report_id=session.experiment_report_id,
                router_enabled=bool(session.llm_router_settings.enabled),
                store=_pilot_store(),
            ),
            turn_id,kind,fields,
        )
    except Exception as exc:
        log.warning("pilot.metrics.failed kind=%s error=%s",kind,type(exc).__name__)


def _pilot_mark(
    kind:str,*,session=None,report_id:str|None=None,**values:Any,
)->None:
    """Lane LOG: one experiment-level mark (a button, an alert...); never raises."""

    try:
        store=_pilot_store()
        if store is None:
            return
        store.record_mark(
            account_id=_pilot_account(),kind=kind,
            session_id=session.session_id if session is not None else None,
            report_id=(
                session.experiment_report_id if session is not None else report_id),
            **values,
        )
    except Exception as exc:
        log.warning("pilot.metrics.failed kind=%s error=%s",kind,type(exc).__name__)


def _pilot_protocol_event(
    protocol_id:str,kind:str,*,principal:Principal|None=None,
    public:dict[str,object]|None=None,failed:bool=False,started:float|None=None,
)->None:
    """Lane LOG, decision 2: an analysis that ended, or a review registered; never raises."""

    try:
        store=_pilot_store()
        if store is None:
            return
        outcome=None
        if kind=="analysis":
            outcome="failed" if failed else analysis_outcome(public or {})
        revision=(public or {}).get("revision_id")
        store.record_protocol_event(
            account_id=_pilot_account(principal),protocol_id=protocol_id,kind=kind,
            revision_id=revision if isinstance(revision,str) else None,outcome=outcome,
            duration_ms=(
                round((time.monotonic()-started)*1000) if started is not None else None),
            card_count=(
                review_card_count(public or {}) if kind=="analysis" else None),
        )
    except Exception as exc:
        log.warning("pilot.metrics.failed kind=%s error=%s",kind,type(exc).__name__)


def _start_or_resume_workspace_experiment(
    session:ListenerSession,
    *,
    protocol_id:str,
    protocol_revision_id:str,
    recovery_session_id:str|None=None,
    recovery_version:int|None=None,
    reattach:bool=False,
)->dict[str,object]|None:
    """Bind a voice connection to one durable, tenant-owned experiment.

    ``reattach`` (lane NET, decision 4): the page's reconnection after a lost
    connection; the experiment keeps its status (a pause stays a pause).
    """

    settings=_workspace_settings()
    if not settings.enabled:
        return None
    principal,store=_commercial_workspace()
    try:
        if recovery_session_id is not None:
            if recovery_version is None:
                raise WorkspaceError("Experiment recovery version is required.")
            state=store.resume_experiment(
                principal,recovery_session_id,
                expected_version=recovery_version,
                protocol_id=protocol_id,
                protocol_revision_id=protocol_revision_id,
                voice_connection_id=session.voice_connection_id,
                reattach=reattach,
            )
        else:
            first_step=(
                session.curated_protocol_session.fixture.steps[0]
                if session.curated_protocol_session is not None
                and session.curated_protocol_session.fixture.steps else None
            )
            state=store.start_experiment(
                principal,
                session_id=session.session_id,
                protocol_id=protocol_id,
                protocol_revision_id=protocol_revision_id,
                current_step_id=first_step.step_id if first_step else None,
                current_step_label=first_step.source_label if first_step else None,
                voice_connection_id=session.voice_connection_id,
            )
        session.session_id=str(state["session_id"])
        session.experiment_state_version=int(state["version"])
        return state
    finally:
        store.close()


def _recovery_revision_id(session_id:object,protocol_id:str)->str|None:
    """The exact revision a durable experiment started on, or None.

    Lane RV, decision f: continuing an experiment loads the revision it
    started on, not the latest one. None when the workspace is off or the
    experiment is not this caller's on this protocol -- the recovery itself
    then refuses as before.
    """

    if not isinstance(session_id,str) or not _workspace_settings().enabled:
        return None
    try:
        principal,store=_commercial_workspace()
    except Exception:  # noqa: BLE001 - the recovery itself reports it
        return None
    try:
        state=store.get_experiment(principal,session_id)
    except Exception:  # noqa: BLE001 - the recovery itself reports it
        return None
    finally:
        store.close()
    if state.get("protocol_id")!=protocol_id:
        return None
    revision=state.get("protocol_revision_id")
    return revision if isinstance(revision,str) else None


def _recovery_fixture(
    catalog:ProtocolCatalog,protocol_id:str,entry:Any,recovery_session_id:object,
)->tuple[CuratedProtocolFixture|None,str|None,str|None]|None:
    """(fixture, revision id, refusal) of a recovery onto an older revision, or None.

    Lane RV, decision f: a recovery gets the revision its experiment started
    on -- an analysis analysed again since, or an earlier review version --
    when that is not what the catalog runs now. None for a new run and for a
    recovery onto the current revision: the session start then loads what
    runs now (the latest review version, or the analysis that passed).
    """

    if recovery_session_id is None:
        return None
    started_on=_recovery_revision_id(recovery_session_id,protocol_id)
    if started_on is None or started_on==entry.revision_id:
        return None
    try:
        return catalog.load_fixture_for_revision(protocol_id,started_on),started_on,None
    except ProtocolCatalogError:
        return None,None,"protocol_selection_unavailable"


def _workspace_skipped_step_ids(state:Any)->tuple[str,...]:
    """The steps a confirmed later start skipped, as the durable record holds them.

    Lane N, decision 9: the start's own event (steps_skipped or
    protocol_started with the start_at_step record), or the copy a
    checkpoint restart carries into the new session.
    """

    for event in (state or {}).get("events") or ():
        payload=event.get("payload") if isinstance(event.get("payload"),dict) else {}
        record=payload.get("step_record")
        if isinstance(record,dict) and record.get("kind")=="start_at_step":
            return tuple(str(item) for item in record.get("skipped_step_ids") or ())
    return ()


def _workspace_branch_skipped_step_ids(state:Any)->tuple[str,...]:
    """The steps a source condition answered "no" passed over (lane CB, decision 1).

    Read from the durable record's step records -- the answer that skipped
    the step the run stood on, or an arrival that passed the branch's steps
    -- so a run can be continued past them after the connection dropped.
    """

    found:list[str]=[]
    for event in (state or {}).get("events") or ():
        payload=event.get("payload") if isinstance(event.get("payload"),dict) else {}
        record=payload.get("step_record")
        if isinstance(record,dict) and record.get("kind") in {
            "branch_answer","branch_steps_skipped",
        }:
            for item in record.get("skipped_step_ids") or ():
                if str(item) not in found:
                    found.append(str(item))
    return tuple(found)


def _workspace_reverted_step_ids(state:Any)->tuple[str,...]:
    """The steps a confirmed revert took the completion of (lane CF, decision 3).

    Read from the durable record's step records, so a run that went back can
    be continued after the connection dropped: the completions stay in the
    record, past the step the run stands on.
    """

    found:list[str]=[]
    for event in (state or {}).get("events") or ():
        payload=event.get("payload") if isinstance(event.get("payload"),dict) else {}
        record=payload.get("step_record")
        if isinstance(record,dict) and record.get("kind")=="step_revert":
            for item in record.get("reverted_step_ids") or ():
                if str(item) not in found:
                    found.append(str(item))
    return tuple(found)


def _workspace_run_changes(state:Any)->tuple[RunChange,...]|None:
    """Today's values as the durable record holds them (lane EASY, decision 2).

    The start's values (the "protocol_started" event), then each change and
    put-back in order; None when the run never started (its values are then
    the start's choice in the protocol's ledger)."""

    events=(state or {}).get("events") or ()
    if not any(isinstance(event,dict) and event.get("event_type")=="protocol_started" for event in events):
        return None
    kept:dict[str,RunChange]={}
    for event in events:
        payload=event.get("payload") if isinstance(event.get("payload"),dict) else {}
        try:
            if event.get("event_type")=="protocol_started":
                for item in payload.get("run_changes") or ():
                    change=RunChange.from_mapping(item)
                    kept[change.item_id]=change
            record=payload.get("step_record") if isinstance(payload.get("step_record"),dict) else {}
            if record.get("kind")=="run_value_changed" and isinstance(record.get("change"),dict):
                change=RunChange.from_mapping(record["change"])
                kept[change.item_id]=change
            elif record.get("kind")=="run_value_reverted":
                for item in record.get("reverted") or ():
                    kept.pop(str((item or {}).get("item_id")),None)
        except RunChangeError:
            continue
    return tuple(kept.values())


def _workspace_started_at(state:Any)->float|None:
    """When the experiment first started, as the durable record holds it, or None.

    Lane FX4, decision 1: its "protocol_started" event (the run's start), or
    for a run opened from a checkpoint, which has none, the session's own
    start. A restored run's elapsed clock goes on from this moment.
    """

    state=state or {}
    started=next(
        (event.get("created_at") for event in state.get("events") or ()
         if isinstance(event,dict) and event.get("event_type")=="protocol_started"),
        state.get("started_at"))
    try:
        return datetime.fromisoformat(str(started)).timestamp() if started else None
    except ValueError:
        return None


#: Lane HEAR2, decision 3: the record's event for each timer command.
_TIMER_EVENT_TYPES={
    CuratedProtocolAction.TIMER_PAUSE:"timer_paused",
    CuratedProtocolAction.TIMER_RESUME:"timer_resumed",
    CuratedProtocolAction.TIMER_CANCEL:"timer_cancelled",
    CuratedProtocolAction.TIMER_RESTART:"timer_restarted",
}


def _transition_workspace_experiment(
    session:ListenerSession,
    *,
    action:str,
    event_key:str,
    reason:str|None=None,
)->dict[str,object]|None:
    settings=_workspace_settings()
    if not settings.enabled or session.experiment_state_version is None:
        return None
    principal,store=_commercial_workspace()
    try:
        curated=session.curated_protocol_session
        state=store.transition_experiment(
            principal,session.session_id,action=action,
            expected_version=session.experiment_state_version,
            event_key=event_key,reason=reason,
            expected_voice_connection_id=session.voice_connection_id,
            expected_step_id=(curated.fixture.steps[curated.current_index].step_id if curated else None),
        )
        session.experiment_state_version=int(state["version"])
        return state
    finally:
        store.close()


def _record_workspace_experiment_progress(
    session:ListenerSession,
    curated:CuratedProtocolSession,
    plan,
    *,
    turn_id:int,
    generation:int,
    pre_transition_index:int,
)->dict[str,object]|None:
    """Mirror only deterministic committed protocol actions into recovery state."""

    settings=_workspace_settings()
    if not settings.enabled or session.experiment_state_version is None:
        return None
    if not plan.state_changed or plan.action not in {
        CuratedProtocolAction.START,
        CuratedProtocolAction.NEXT,
        CuratedProtocolAction.STOP,
        CuratedProtocolAction.PAUSE,
        CuratedProtocolAction.RESUME,
        CuratedProtocolAction.START_TIMER,
        # Lane HEAR2, decision 3: the timer's voice commands, for the restore
        # after a lost connection.
        *_TIMER_EVENT_TYPES,
        # Lane EASY, decision 2: today's values, for a run continued after a
        # lost connection.
        CuratedProtocolAction.CHANGE_VALUE,
    }:
        return None
    key=f"voice-{generation}-{turn_id}-{plan.action.value}"
    if plan.action is CuratedProtocolAction.STOP:
        return _transition_workspace_experiment(
            session,action="stop",event_key=key,reason="voice_command"
        )
    if plan.action is CuratedProtocolAction.PAUSE:
        return _transition_workspace_experiment(
            session,action="pause",event_key=key,reason="voice_command"
        )
    if plan.action is CuratedProtocolAction.RESUME:
        return _transition_workspace_experiment(
            session,action="resume",event_key=key,reason="voice_command"
        )
    previous=curated.fixture.steps[pre_transition_index]
    current=(
        curated.fixture.steps[curated.current_index]
        if curated.active else None
    )
    event_payload={
        "authority":"curated_protocol",
        "intent_kind":plan.intent_kind,
        "configuration_id":session.accepted_configuration_id,
        "turn_id":turn_id,
        "generation":generation,
    }
    if plan.action is CuratedProtocolAction.START_TIMER:
        planned_timer=plan.timer_payload
        if (
            not isinstance(planned_timer,dict)
            or planned_timer.get("state")!="running"
            or planned_timer.get("step_id")!=previous.step_id
            or planned_timer.get("step_label")!=previous.source_label
        ):
            raise WorkspaceError(
                "Timer progress requires a running current-step timer."
            )
        event_payload["timer"]={
            key:planned_timer[key] for key in (
                "state","duration_seconds","remaining_seconds",
                "elapsed_seconds","step_index","step_id","step_label",
                "deadline_at","started_at",
            )
        }
        # Lane VX, decision 4: the timer as it is named ("최소 2시간") and
        # what the source states its value as.
        event_payload["timer"].update({
            key:planned_timer[key] for key in ("name","bound") if key in planned_timer
        })
    elif plan.action in _TIMER_EVENT_TYPES:
        # Lane HEAR2, decision 3: the timer as it stood after the command
        # (paused: where it stands; resumed or restarted: its new start;
        # cancelled: what was cleared), for the restore after a lost connection.
        planned_timer=plan.timer_payload
        if not isinstance(planned_timer,dict) or not planned_timer.get("step_id"):
            raise WorkspaceError("A timer command requires the timer it acted on.")
        event_payload["timer"]={
            key:planned_timer.get(key) for key in (
                "state","duration_seconds","remaining_seconds","elapsed_seconds",
                "step_index","step_id","step_label","deadline_at","started_at",
                "paused_at","name","bound",
            )
        }
    # Lane R7: a return within a repeat, a later start and a step completed
    # again in a later round are recorded as what they are. A step already
    # marked completed is not marked again: the durable session keeps one
    # completion per step and refuses a second, which would roll the turn back.
    record=getattr(plan,"step_record",None) or {}
    move_kind=record.get("kind")
    if record:
        event_payload["step_record"]=record
    if plan.action is CuratedProtocolAction.START and curated.run_changes():
        # Lane EASY, decision 2: the values the start applied for today.
        event_payload["run_changes"]=[change.public_dict() for change in curated.run_changes()]
    principal,store=_commercial_workspace()
    try:
        state=store.record_experiment_progress(
            principal,session.session_id,
            expected_version=session.experiment_state_version,
            expected_voice_connection_id=session.voice_connection_id,
            event_key=key,
            event_type=(
                move_kind
                if plan.action is CuratedProtocolAction.CHANGE_VALUE else
                "repeat_returned"
                if move_kind in {"repeat_return","repeat_round"} else
                # Lane CF, decision 3: a confirmed revert, beside the
                # completions it takes back (they stay marked completed).
                "step_reverted"
                if move_kind=="step_revert" else
                "steps_skipped"
                if move_kind=="start_at_step" and not record.get("experiment_started") else
                # Lane CB, decision 1: the answer that skipped the step the run
                # stood on; a skip on arrival keeps the completion's own type
                # and carries the passed steps in its record.
                "branch_steps_skipped"
                if move_kind=="branch_answer" else
                "protocol_started"
                if plan.action is CuratedProtocolAction.START else
                "timer_started"
                if plan.action is CuratedProtocolAction.START_TIMER else
                _TIMER_EVENT_TYPES[plan.action]
                if plan.action in _TIMER_EVENT_TYPES else
                "step_completed" if plan.reported_completion else "step_advanced"
            ),
            step_id=previous.step_id,
            step_label=previous.source_label,
            next_step_id=current.step_id if current else None,
            next_step_label=current.source_label if current else None,
            mark_completed=bool(
                plan.action is CuratedProtocolAction.NEXT
                and plan.reported_completion
                and not record.get("completed_before")
            ),
            payload=event_payload,
        )
        session.experiment_state_version=int(state["version"])
    finally:
        store.close()
    if (
        plan.action is CuratedProtocolAction.NEXT
        and not curated.active
        and curated._workflow_status=="completed"
    ):
        state=_transition_workspace_experiment(
            session,action="complete",
            event_key=f"{key}-workflow-completed",
            reason="all_protocol_steps_completed",
        )
    return state


def _record_workspace_observation(
    session:ListenerSession,
    curated:CuratedProtocolSession,
    plan,
    *,
    turn_id:int,
    generation:int,
    pre_transition_index:int,
)->dict[str,object]|None:
    """Persist user wording as a non-authoritative timeline observation."""

    settings=_workspace_settings()
    if not settings.enabled or session.experiment_state_version is None:
        return None
    content=(plan.observation_outcome or plan.anomaly_text or "").strip()
    if not content:
        raise WorkspaceError("Observation content is unavailable.")
    if plan.action is CuratedProtocolAction.REPORT_ANOMALY:
        category="deviation"
    elif plan.observation_predicate in {
        "note","appearance","measurement","deviation","other",
    }:
        category=plan.observation_predicate
    elif plan.observation_predicate in {"positive","negative","observation"}:
        category="appearance"
    else:
        category="other"
    step=curated.fixture.steps[pre_transition_index]
    notebook=getattr(plan,"notebook_record",None)
    if notebook is not None and notebook.get("kind")=="observation":
        observed_step=next((candidate for candidate in curated.fixture.steps
                            if candidate.step_id==notebook.get("step_id")
                            and candidate.source_label==notebook.get("step_label")),None)
        if observed_step is None:
            raise WorkspaceError("Notebook observation step is unavailable.")
        step=observed_step
    principal,store=_commercial_workspace()
    try:
        store.record_observation(
            principal,
            session.session_id,
            event_key=(
                f"voice-{generation}-{turn_id}-observation-"
                f"{plan.action.value}"
            ),
            content=content,
            category=category,
            capture_source="voice",
            protocol_step_id=step.step_id,
            expected_voice_connection_id=session.voice_connection_id,
        )
        state=store.get_experiment(principal,session.session_id)
        session.experiment_state_version=int(state["version"])
        return state
    finally:
        store.close()


def _require_current_voice_connection(session:ListenerSession)->None:
    """A replaced connection cannot plan another workflow or record write."""
    if session.experiment_state_version is None:
        return
    principal,store=_commercial_workspace()
    try:
        state=store.get_experiment(principal,session.session_id)
        if state["last_voice_connection_id"]!=session.voice_connection_id:
            raise WorkspaceConflictError("The experiment has another voice connection.")
    finally:
        store.close()

def _record_workspace_record_fix(
    session:ListenerSession,
    plan,
    *,
    turn_id:int,
    generation:int,
)->dict[str,object]|None:
    """Append a correction of a voice note beside it (lane N, decision 2)."""

    settings=_workspace_settings()
    fix=getattr(plan,"record_fix",None)
    if not settings.enabled or session.experiment_state_version is None or not fix:
        return None
    target=fix.get("target") or {}
    principal,store=_commercial_workspace()
    try:
        store.record_observation_fix(
            principal,
            session.session_id,
            event_key=f"voice-{generation}-{turn_id}-record-fix",
            target_event_key=(
                f"voice-{target.get('generation')}-{target.get('turn_id')}-"
                f"observation-{target.get('action')}"
            ),
            content=fix.get("after") if fix.get("mode")=="correct" else None,
            capture_source="voice",
        )
        state=store.get_experiment(principal,session.session_id)
        session.experiment_state_version=int(state["version"])
        return state
    finally:
        store.close()


# --- Lane CF (2026-10-08): the experimenter's settings and the file followed --

#: The setting defaults (decisions 1 and 4): values read back and not asked
#: about, the source's conditions and counts asked before the start.
EXPERIMENTER_SETTING_DEFAULTS:dict[str,str]={
    "confirm_mode":"readback","question_timing":"before_start",
    # Lane WV, decision 2: the web is looked at for an explanation or a
    # photograph unless the experimenter turns it off.
    "web_lookup":"on",
    # Lane VT, decision 6: everything is said first unless the experimenter
    # keeps it to what is needed or turns it off.
    "proactive_mode":"all",
    # Lane SP1, decision 3: a quiet room until the experimenter says the
    # place is noisy (or takes the server's suggestion); decision 1: the
    # margin under the wearer's measured voice, "보통" (12 dB).
    "ambient_mode":"quiet",
    "speaker_sensitivity":"normal",
    # Lane ON, decision 2: the agent's voice ("default": the provider's
    # configured voice) and what the last 목소리 맞추기 came to.
    "agent_voice":"default",
    "voice_calibration":"none",
    # Lane HEAR, decision 2: every voice is heard until the person turns the
    # filter on after measuring; the measurement (level, time, microphone)
    # is written by POST /api/voice/calibration only.
    "speaker_filter":"off",
    "voice_reference":"none",
}
#: Settings the server writes itself (lane HEAR, decision 2): the screen's
#: PUT may not set them.
SERVER_ONLY_EXPERIMENTER_SETTINGS=frozenset({"voice_reference"})
#: Where the settings are kept while the server runs when there is no
#: workspace to keep them in, by the experimenter (one, "local", without one).
#: Lane WV's "web_lookup" used to be kept here even with a workspace; since
#: workspace schema 9 (lane VT, decision 7) it is kept with the others.
_EXPERIMENTER_SETTINGS_MEMORY:dict[str,dict[str,str]]={}


# --- Lane HEAR, decision 2: the measured voice, kept as a setting -----------
#: A measurement older than this, or made on another microphone, is not used.
VOICE_REFERENCE_MAX_AGE_DAYS=14
#: The sentence posted to POST /api/voice/calibration: 0.5-12 s of 16 kHz PCM16.
CALIBRATION_MIN_BYTES=16000
CALIBRATION_MAX_BYTES=12*32000
CALIBRATION_MAX_BASE64_CHARS=(CALIBRATION_MAX_BYTES*4)//3+8
#: At least 0.8 s of the sentence must be voiced to measure it.
CALIBRATION_MIN_VOICED_FRAMES=40
_MIC_ID=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
_VOICE_REFERENCE=re.compile(r"^v1:(-?\d{1,3}(?:\.\d)?):(\d{9,11}):([A-Za-z0-9][A-Za-z0-9_-]{0,39})$")


@dataclass(frozen=True)
class VoiceReference:
    """The wearer's measured level, when it was measured, and on which microphone."""

    level_db:float
    measured_at:float
    mic_id:str


def normalize_mic_id(value:object)->str|None:
    """The page's short microphone id, or None when it sent none or a bad one."""

    return value if isinstance(value,str) and _MIC_ID.fullmatch(value) else None


def voice_reference_value(level_db:float,measured_at:float,mic_id:str)->str:
    """The setting's value: "v1:<dBFS>:<unix seconds>:<microphone id>"."""

    return f"v1:{level_db:.1f}:{int(measured_at)}:{normalize_mic_id(mic_id) or 'unknown'}"


def parse_voice_reference(value:object)->VoiceReference|None:
    match=_VOICE_REFERENCE.fullmatch(value) if isinstance(value,str) else None
    if match is None:
        return None
    level=float(match.group(1))
    if not -100.0<=level<=0.0:
        return None
    return VoiceReference(level,float(match.group(2)),match.group(3))


def stored_voice_filter(
    settings:Mapping[str,str],mic_id:str|None,now:float,
)->tuple[str,str,VoiceReference|None]:
    """Whether a run filters by level, and why not (lane HEAR, decision 2).

    ("active", "on", reference) only when the person turned the filter on,
    a measurement exists, it is younger than VOICE_REFERENCE_MAX_AGE_DAYS
    and the run's microphone is the one measured. Otherwise ("inactive",
    reason, None): "off", "not_measured", "stale_age" or "stale_device".
    """

    if str(settings.get("speaker_filter","off"))!="on":
        return "inactive","off",None
    reference=parse_voice_reference(settings.get("voice_reference"))
    if reference is None:
        return "inactive","not_measured",None
    if now-reference.measured_at>VOICE_REFERENCE_MAX_AGE_DAYS*86400 or reference.measured_at>now+300:
        return "inactive","stale_age",None
    if mic_id is None or reference.mic_id=="unknown" or mic_id!=reference.mic_id:
        return "inactive","stale_device",None
    return "active","on",reference


def _experimenter_settings_key()->str:
    principal=_REQUEST_PRINCIPAL.get()
    return principal.principal_id if principal is not None else "local"


def _load_experimenter_settings()->dict[str,str]:
    """This experimenter's settings: the workspace's record, else the server's memory."""

    settings=dict(EXPERIMENTER_SETTING_DEFAULTS)
    if _workspace_settings().enabled:
        try:
            principal,store=_commercial_workspace()
            try:
                settings.update(store.experimenter_settings(principal))
                return settings
            finally:
                store.close()
        except Exception as exc:  # noqa: BLE001 - the defaults still run the session
            log.warning("experimenter settings unavailable error=%s",type(exc).__name__)
    settings.update(_EXPERIMENTER_SETTINGS_MEMORY.get(_experimenter_settings_key(),{}))
    return settings


def _save_experimenter_settings(changes:Mapping[str,Any],source:str)->dict[str,str]:
    """Keep a change (voice or screen) and return the settings after it.

    Appended to the workspace's record (never updated in place); without a
    workspace, kept in the server's memory while it runs.
    """

    if not isinstance(changes,Mapping) or not changes:
        raise WorkspaceError("Experimenter setting is invalid.")
    for name,value in changes.items():
        if not experimenter_setting_value_allowed(str(name),value):
            raise WorkspaceError("Experimenter setting is invalid.")
        if str(name)=="agent_voice" and not _agent_voice_known(str(value)):
            raise WorkspaceError("Experimenter setting is invalid.")
    changed={str(name):str(value) for name,value in changes.items()}
    if _workspace_settings().enabled:
        principal,store=_commercial_workspace()
        try:
            for name,value in changed.items():
                store.record_experimenter_setting(
                    principal,name=name,value=value,source=source)
            return {**EXPERIMENTER_SETTING_DEFAULTS,**store.experimenter_settings(principal)}
        finally:
            store.close()
    kept=_EXPERIMENTER_SETTINGS_MEMORY.setdefault(_experimenter_settings_key(),{})
    kept.update(changed)
    return {**EXPERIMENTER_SETTING_DEFAULTS,**kept}


@app.get("/api/experimenter/settings")
def get_experimenter_settings()->dict[str,object]:
    return {"settings":_load_experimenter_settings()}


@app.put("/api/experimenter/settings")
def put_experimenter_settings(body:dict[str,Any]=Body(...))->dict[str,object]:
    if isinstance(body,Mapping) and SERVER_ONLY_EXPERIMENTER_SETTINGS.intersection(map(str,body)):
        raise HTTPException(status_code=403,detail="setting_server_only")
    try:
        return {"settings":_save_experimenter_settings(body,"screen")}
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/voice/calibration")
def post_voice_calibration(body:dict[str,Any]=Body(...))->dict[str,object]:
    """목소리 맞추기 outside a run (lane HEAR, decisions 1 and 2).

    The page records the sentence on the microphone it uses for runs and
    posts it here as 16 kHz mono PCM16 (base64) with the microphone's short
    id. The median level of the frames the VAD calls voiced is kept as the
    experimenter's ``voice_reference`` -- the level, when, and the
    microphone id -- and the sound is dropped at once: nothing is
    transcribed, logged or stored. Measuring does not turn the filter on.
    """

    raw=body.get("pcm_base64") if isinstance(body,Mapping) else None
    if not isinstance(raw,str) or not raw or len(raw)>CALIBRATION_MAX_BASE64_CHARS:
        raise HTTPException(status_code=422,detail={"code":"audio_invalid"})
    try:
        pcm=base64.b64decode(raw,validate=True)
    except (ValueError,TypeError) as exc:
        raise HTTPException(status_code=422,detail={"code":"audio_invalid"}) from exc
    if len(pcm)%2 or not CALIBRATION_MIN_BYTES<=len(pcm)<=CALIBRATION_MAX_BYTES:
        raise HTTPException(status_code=422,detail={"code":"audio_length"})
    mic_id=normalize_mic_id(body.get("mic_id")) or "unknown"
    try:
        mode=VoiceVadSettings.from_environment().cascade.mode
    except Exception:  # noqa: BLE001 - the listener's own default mode then
        mode=VadConfig().mode
    classifier=WebRtcVadClassifier(mode)
    frames=[pcm[i:i+FRAME_BYTES] for i in range(0,len(pcm)-FRAME_BYTES+1,FRAME_BYTES)]
    flags=[(frame,bool(classifier(frame))) for frame in frames]
    voiced=sum(1 for _,flag in flags if flag)
    if voiced<CALIBRATION_MIN_VOICED_FRAMES:
        log.info("voice calibration refused reason=too_little_voice voiced_frames=%d frames=%d",voiced,len(frames))
        raise HTTPException(status_code=422,detail={"code":"too_little_voice"})
    level=voiced_median_level_db(flags)
    if level is None or not math.isfinite(level):
        raise HTTPException(status_code=422,detail={"code":"too_little_voice"})
    measured_at=time.time()
    try:
        settings=_save_experimenter_settings({
            "voice_reference":voice_reference_value(level,measured_at,mic_id),
            "voice_calibration":"measured",
        },"screen")
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    log.info(
        "voice calibration measured on screen level_db=%.1f voiced_frames=%d frames=%d",
        level,voiced,len(frames))
    return {
        "state":"measured","level_db":round(level,1),
        "measured_at":datetime.fromtimestamp(measured_at,tz=timezone.utc).isoformat(),
        "mic_id":mic_id,"speaker_filter":settings.get("speaker_filter","off"),
        "max_age_days":VOICE_REFERENCE_MAX_AGE_DAYS,
    }


@app.get("/api/voice/voices")
def get_voice_voices()->dict[str,object]:
    """The voices a person may choose for the agent, and the one chosen (lane ON, decision 2)."""

    try:
        provider=_tts_provider()
    except ConfigurationError as exc:
        raise HTTPException(status_code=503,detail="configuration_invalid") from exc
    voices=voice_providers.korean_voices(provider)
    settings=_load_experimenter_settings()
    selected=str(settings.get("agent_voice","default") or "default")
    if not _agent_voice_known(selected):
        selected="default"
    return {
        "provider":provider,
        "default_voice_id":voice_providers.DEFAULT_TTS_VOICES.get(provider,""),
        "voices":[{"voice_id":voice.voice_id,"label_ko":voice.label_ko} for voice in voices],
        "selected":selected,
        "preview_text_ko":voice_providers.PREVIEW_SENTENCE_KO,
    }


@app.post("/api/voice/preview")
def post_voice_preview(body:dict[str,Any]=Body(...))->Response:
    """[들어 보기]: the preview sentence in one voice, as a 16 kHz WAV.

    One real TTS call per voice per process (the audio is kept); a failure
    is said and nothing is kept. The pilot's cap for these is small (lane
    ON: USD 1 for the whole lane), so the sentence is short.
    """

    voice_id=str(body.get("voice_id") or "")
    try:
        provider=_tts_provider()
    except ConfigurationError as exc:
        raise HTTPException(status_code=503,detail="configuration_invalid") from exc
    if not any(voice.voice_id==voice_id for voice in voice_providers.korean_voices(provider)):
        raise HTTPException(status_code=422,detail="voice_unknown")
    key=(provider,voice_id)
    pcm=_VOICE_PREVIEW_CACHE.get(key)
    if pcm is None:
        settings=replace(TtsProviderSettings.from_environment(),voice=voice_id)
        try:
            if provider==voice_providers.XAI:
                session=ListenerSession()
                session.tts_voice=voice_id
                token=_SPEAKING_SESSION.set(session)
                try:
                    pcm=synthesize(voice_providers.PREVIEW_SENTENCE_KO,"ko")
                finally:
                    _SPEAKING_SESSION.reset(token)
            else:
                pcm=voice_providers.synthesize(voice_providers.PREVIEW_SENTENCE_KO,"ko",settings)
        except Exception as exc:  # noqa: BLE001 - the provider's failure, said by name only
            log.warning("voice.preview failed provider=%s error=%s",provider,type(exc).__name__)
            raise HTTPException(status_code=502,detail="voice_preview_failed") from exc
        if not pcm:
            raise HTTPException(status_code=502,detail="voice_preview_failed")
        _VOICE_PREVIEW_CACHE[key]=pcm
        log.info("voice.preview synthesized provider=%s voice=%s bytes=%d",provider,voice_id,len(pcm))
    return Response(content=pcm_to_wav(pcm),media_type="audio/wav",
                    headers={"Cache-Control":"private, max-age=3600"})


def _source_basis_from(fixture_filename:Any,entry:Mapping[str,Any]|None)->dict[str,Any]:
    """The file a run follows and when it was uploaded (decision 5).

    ``entry`` is the catalog entry the session was configured from (its
    ``source_filename`` and ``created_at``); without one, the fixture's own
    file name and no date. Only text is taken.
    """

    entry=entry or {}
    text=lambda value: value if isinstance(value,str) and value.strip() else None  # noqa: E731
    basis={
        "filename":text(entry.get("source_filename")) or text(fixture_filename),
        "uploaded_at":text(entry.get("created_at")),
    }
    disclosure=entry.get("source_disclosure")
    if isinstance(disclosure,Mapping):
        if disclosure.get("recorded") is False:
            basis["source_disclosure"]={
                "recorded":False,"status":None,"status_ko":"확인 기록 없음",
                "note":"","recorded_at":None,
            }
        elif disclosure.get("recorded") is True:
            try:
                parsed=ProtocolSourceDisclosure(
                    status=ProtocolSourceStatus(disclosure.get("status")),
                    note=disclosure.get("note"),
                    recorded_at=disclosure.get("recorded_at"),
                )
                basis["source_disclosure"]=parsed.public_dict()
            except (ProtocolRegistrationError,TypeError,ValueError):
                pass
    return basis


def _note_readback(plan:Any,language:str,confirm_mode:str="readback")->Any:
    """A stored note read back, never asked about (lane N, decision 1).

    A value is said value by value, decimal point included ("영 점 오
    밀리리터로 기록했어요"), however it was said -- "0점5", "영 점 오" and
    "0.5" read alike (lane CF, decision 2); any other note says where it went
    ("2단계에 기록했어요"). In the 조용히 way nothing is read back: the values
    are confirmed in the end-of-run review (lane CF, decision 1).
    """

    note=getattr(plan,"note_record",None) or {}
    if not note:
        return plan
    read=[str(value.get("text")) for value in note.get("values") or () if value.get("text")]
    values=read or (
        [value for value in note.get("measurements") or () if value]
        if note.get("category")=="measurement" else [])
    # Lane RA, decision (c): a value that differs from the source is read
    # back as a deviation, with the source's value beside it.
    deviations=[item for item in note.get("deviations") or () if isinstance(item,dict)]
    if language!="ko":
        display=speech=f"Recorded at step {note.get('step_label')}."
        if deviations:
            sources=", ".join(f"{item.get('said')} (source {item.get('source')})" for item in deviations)
            display=speech=f"Recorded as a deviation at step {note.get('step_label')}: {sources}."
    elif confirm_mode=="quiet":
        display=speech="기록했어요."
    elif deviations:
        said=[str(item.get("said") or "") for item in deviations]
        source=[str(item.get("source") or "") for item in deviations]
        shown=", ".join(said)
        spoken=", ".join(spoken_korean(value) for value in said)
        particle=josa_ro(shown)[len(shown):]
        display=f"편차로 기록했어요: {shown}{particle} 하셨고, 원문은 {' · '.join(source)}이에요."
        speech=f"편차로 기록했어요. {spoken}{particle} 하셨고, 원문은 {', '.join(spoken_korean(value) for value in source)}이에요."
    elif values:
        shown=", ".join(values)
        spoken=", ".join(spoken_korean(value) for value in values)
        display=f"{josa_ro(shown)} 기록했어요."
        speech=f"{spoken}{josa_ro(shown)[len(shown):]} 기록했어요."
    else:
        display=speech=f"{note.get('step_label')}단계에 기록했어요."
    return replace(
        plan,display_text=display,speech_text=speech,primary_text=display,
        speech_mode=CuratedProtocolSpeechMode.CONTROL,
    )


def _record_fix_readback(plan:Any,language:str)->Any:
    """What is said once a correction was appended (lane N, decision 2)."""

    fix=getattr(plan,"record_fix",None) or {}
    after=str(fix.get("after") or "")
    if fix.get("mode")=="retract":
        display=speech=(
            "방금 기록을 취소로 표시했어요. 처음 기록은 지우지 않고 남겨 두었어요."
            if language=="ko" else
            "The last note is marked withdrawn; the original stays in the record."
        )
    elif language=="ko":
        particle=josa_ro(after)[len(after):]
        display=f"방금 기록을 '{after}'{particle} 고쳤어요."
        # A measurement is heard value by value, as it was read back.
        heard=(
            spoken_korean(after)
            if (fix.get("target") or {}).get("category")=="measurement" else after
        )
        speech=f"방금 기록을 {heard}{particle} 고쳤어요."
    else:
        display=speech=f"The last note now reads '{after}'."
    return replace(
        plan,display_text=display,speech_text=speech,primary_text=display,
        speech_mode=CuratedProtocolSpeechMode.CONTROL,
    )


def normalize_session_language(value:str)->str:
    normalized=value.strip().casefold().replace("_","-")
    if normalized in ("ko","ko-kr"): return "ko"
    if normalized in ("en","en-us","en-gb"): return "en"
    if normalized in ("vi","vi-vn"): return "vi"
    raise ValueError("unsupported session language")

@dataclass(frozen=True)
class ServerConfig:
    catalog_path:Path|None
    facility_id:str|None
    usage_scope:str
    allowed_languages:frozenset[str]
    default_language:str
    curated_protocol_fixture_path:Path|None=None
    curated_protocol_provenance_path:Path|None=None
    curated_protocol_source_pdf_path:Path|None=None


def _protocol_store_settings()->ProtocolPersistenceSettings:
    return ProtocolPersistenceSettings.from_environment()


_CatalogResult=TypeVar("_CatalogResult")


async def _worker_until_complete(operation:Callable[[],_CatalogResult])->_CatalogResult:
    """Keep caller-owned files alive through repeated cancellation requests.

    Shield the executor future itself (not another task that shutdown may
    cancel). Copy request identity just as ``to_thread`` does. Cancellation
    remains the caller's outcome, even if the worker subsequently fails.
    """
    future=asyncio.get_running_loop().run_in_executor(None,contextvars.copy_context().run,operation)
    cancellation:asyncio.CancelledError|None=None
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as exc:
            cancellation=exc
        except BaseException:
            break  # Retrieve the worker's exception below, exactly once.
    if cancellation is not None:
        try:
            future.result()
        except BaseException as exc:
            log.warning("protocol.worker.failed_after_cancel error=%s",type(exc).__name__)
        raise cancellation
    return future.result()


async def _catalog_in_worker(
    operation:Callable[[ProtocolCatalog],_CatalogResult],
)->_CatalogResult:
    """Open, use and close SQLite on the same worker thread.

    Wait for that thread before cancelling the caller: its source may live
    in the caller's temporary directory, which must outlive all PDF work.
    """
    def run()->_CatalogResult:
        catalog,store=_open_protocol_catalog()
        try:
            return operation(catalog)
        finally:
            store.close()
    return await _worker_until_complete(run)


def _install_protocol_ocr_provider(environ=None)->None:
    """Build the OCR engines the deployment configured and inject them once.

    Reads VOINEY_LAB_OCR_PROVIDERS and the engines' credentials; an
    adapter already injected (a deployment hook or a test) is kept. Only
    engine names are logged, never a key, secret or URL.
    """

    if getattr(app.state,"protocol_ocr_provider",None) is not None:
        return
    from voiney_lab.protocol_ocr_providers import ocr_provider_from_environment

    provider=ocr_provider_from_environment(environ)
    if provider is None:
        log.info("protocol_ocr configured=false")
        return
    app.state.protocol_ocr_provider=provider
    log.info("protocol_ocr configured=true engines=%s",",".join(provider.engine_names))


def _protocol_ocr_provider()->ProtocolOcrProvider:
    """Return a trusted deployment-injected OCR adapter, never a client choice."""

    provider=getattr(app.state,"protocol_ocr_provider",None)
    if provider is None or not callable(getattr(provider,"recognize",None)):
        raise ProtocolOcrUnavailableError(
            "A trusted OCR provider has not been configured."
        )
    return provider


def _open_protocol_catalog()->tuple[ProtocolCatalog,object]:
    settings=_protocol_store_settings()
    if not settings.enabled:
        raise ProtocolCatalogUnavailableError("Protocol catalog is disabled.")
    store=initialize_protocol_store(settings)
    return ProtocolCatalog(store,on_analysis_ready=_translate_analyzed_revision),store


def _revision_translation_store():
    """The workspace store that holds sentence translations, or None."""

    settings=_workspace_settings()
    if not settings.enabled:
        return None
    return initialize_workspace_store(settings)


def _revision_translation_runner()->Callable[[CuratedProtocolFixture],None]|None:
    """Who generates a revision's translations here, or None when nobody may.

    Generation runs only where a stored translation can be kept and read
    (the tenant workspace is enabled) and the translation role has a key --
    whether or not the answer brain is on (lane F, decision 1).
    ``app.state.revision_translation_runner`` (a callable taking the
    fixture) stands in for it.
    """

    runner=getattr(app.state,"revision_translation_runner",None)
    if runner is not None:
        return runner
    settings=MultiBrainSettings.from_environment()
    if (not _workspace_settings().enabled
            or not settings.translation_enabled
            or not RoleModel.from_environment("translation").has_key()):
        return None
    return _start_revision_translation


def _translate_analyzed_revision(
    catalog:ProtocolCatalog,protocol_id:str,
)->None:
    """Start a passed analysis's Korean at once (lane PX, decision 1).

    Off the request, in the background, purpose and safety first and then
    the steps from step 1, so the first steps are Korean by the time a
    person presses "이 프로토콜로 시작". The fixture is the one a session will
    run (same revision id and content hash), read without any authority.
    """

    runner=_revision_translation_runner()
    if runner is not None:
        runner(catalog.load_analysis_fixture(protocol_id))


_REVISION_TRANSLATIONS_RUNNING:set[str]=set()
_REVISION_TRANSLATIONS_LOCK=threading.Lock()


@dataclass(eq=False)
class _TranslationSubscriber:
    """An open session showing one revision, and where to tell it."""

    session:Any
    sender:Any
    loop:asyncio.AbstractEventLoop
    updates:asyncio.Lock=field(default_factory=asyncio.Lock)


#: translation key (protocol id / revision id) -> the open sessions showing
#: that revision. A batch of translations reaches them as soon as it is
#: stored.
_TRANSLATION_SUBSCRIBERS:dict[str,list[_TranslationSubscriber]]={}


def _subscribe_translations(session:Any,sender:Any)->None:
    """Register an open session for its revision's translations (or none)."""

    _unsubscribe_translations(session)
    curated=getattr(session,"curated_protocol_session",None)
    if curated is None:
        return
    subscriber=_TranslationSubscriber(session,sender,asyncio.get_running_loop())
    with _REVISION_TRANSLATIONS_LOCK:
        _TRANSLATION_SUBSCRIBERS.setdefault(
            translation_revision_key(curated.fixture),[]).append(subscriber)


def _unsubscribe_translations(session:Any)->None:
    with _REVISION_TRANSLATIONS_LOCK:
        for revision_id in list(_TRANSLATION_SUBSCRIBERS):
            kept=[item for item in _TRANSLATION_SUBSCRIBERS[revision_id]
                  if item.session is not session]
            if kept:
                _TRANSLATION_SUBSCRIBERS[revision_id]=kept
            else:
                del _TRANSLATION_SUBSCRIBERS[revision_id]


def _subscribers(revision_id:str)->list[_TranslationSubscriber]:
    with _REVISION_TRANSLATIONS_LOCK:
        return list(_TRANSLATION_SUBSCRIBERS.get(revision_id,()))


def _open_session_steps(key:str)->list[int]:
    """The current and next step of every open session on this revision."""

    steps:list[int]=[]
    for subscriber in _subscribers(key):
        curated=getattr(subscriber.session,"curated_protocol_session",None)
        if curated is None or translation_revision_key(curated.fixture)!=key:
            continue
        index=curated.current_index
        steps.extend((index,index+1))
    return steps


def _translation_pending(fixture:Any)->bool:
    """Whether this revision's Korean is still being made right now."""

    try:
        key=translation_revision_key(fixture)
    except Exception:  # noqa: BLE001 - a fixture without ids has none pending
        return False
    with _REVISION_TRANSLATIONS_LOCK:
        return key in _REVISION_TRANSLATIONS_RUNNING


async def _apply_translations(
    subscriber:_TranslationSubscriber,key:str,rows:list[Any],
    glossary:Any=(),
)->None:
    # Batches published from the generator's thread are scheduled, not
    # awaited, on this session's loop. Serialize their read/prepare/install
    # cycle so yielding to a worker cannot drop another checked batch.
    configuration_id=subscriber.session.accepted_configuration_id
    async with subscriber.updates:
        if subscriber.session.accepted_configuration_id!=configuration_id:
            return
        await _apply_translation_batch(subscriber,key,rows,glossary)


async def _apply_translation_batch(
    subscriber:_TranslationSubscriber,key:str,rows:list[Any],glossary:Any,
)->None:
    """Put a stored batch on an open session's fixture and redraw its card.

    Presentation only: the session's fixture gains the rows that pass, and
    the page is sent the same state with the new Korean. No workflow state
    changes.
    """

    session=subscriber.session
    curated=getattr(session,"curated_protocol_session",None)
    if curated is None or translation_revision_key(curated.fixture)!=key:
        return
    original=curated.fixture
    configuration_id=session.accepted_configuration_id
    def prepare()->tuple[CuratedProtocolFixture,ProtocolVocabulary]:
        updated=with_stored_translations(original,rows,glossary)
        # Keep the expensive first calculation off the loop even when a
        # newly arrived glossary changes the vocabulary's content key.
        return updated,ProtocolVocabulary.from_fixture(updated)
    updated,vocabulary=await asyncio.to_thread(prepare)
    if (
        session.curated_protocol_session is not curated
        or curated.fixture is not original
        or session.accepted_configuration_id!=configuration_id
    ):
        return
    if updated is curated.fixture:
        return
    curated.fixture=updated
    curated._vocabulary_cache=(updated,vocabulary)
    if not session.active or session.accepted_configuration_id is None:
        return
    try:
        await subscriber.sender.text(
            "protocol.fixture.state",
            configuration_id=session.accepted_configuration_id,
            action="translation_update",
            state=curated.state(),
            screen=curated_screen_fields(curated),
        )
    except Exception as exc:  # noqa: BLE001 - a closed page misses a redraw
        log.info("revision_translation redraw_skipped error=%s",type(exc).__name__)


async def _publish_translations(key:str,rows:list[Any],glossary:Any=())->None:
    running=asyncio.get_running_loop()
    for subscriber in _subscribers(key):
        if subscriber.loop is running:
            await _apply_translations(subscriber,key,rows,glossary)
        elif not subscriber.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                _apply_translations(subscriber,key,rows,glossary),subscriber.loop)


async def store_revision_translations(
    fixture:CuratedProtocolFixture,translate:Any,*,model:str,
    make_glossary:Any=None,
)->Any:
    """Generate what this revision still lacks, storing and showing each batch."""

    store=_revision_translation_store()
    if store is None:
        return None
    key=translation_revision_key(fixture)
    try:
        # Lane KO2, decision 1: the current style version's glossary; an
        # older version's is not used for the new instructions' sentences.
        glossary_record=store.translation_glossary(
            key,"ko",style_version=TRANSLATION_STYLE_VERSION)
        current={"glossary":glossary_entries(glossary_record)}

        def stored_glossary(record:Any)->None:
            store.record_translation_glossary(record)
            current["glossary"]=glossary_entries(record)

        async def stored_batch(rows:list[Any])->None:
            store.record_fact_translations(rows)
            await _publish_translations(key,rows,current["glossary"])

        return await generate_revision_translations(
            fixture,translate,model=model,
            stored=store.fact_translations(key,"ko"),
            glossary=glossary_record,
            make_glossary=make_glossary,
            priority_steps=lambda:_open_session_steps(key),
            on_glossary=stored_glossary,
            on_batch=stored_batch,
        )
    finally:
        store.close()


def _start_revision_translation(fixture:CuratedProtocolFixture)->None:
    """Run the generation in its own thread, one at a time per revision."""

    key=translation_revision_key(fixture)
    with _REVISION_TRANSLATIONS_LOCK:
        if key in _REVISION_TRANSLATIONS_RUNNING:
            return
        _REVISION_TRANSLATIONS_RUNNING.add(key)
    role=RoleModel.from_environment("translation")
    model=role.model

    def client()->Any:
        return _role_client(role,timeout=BATCH_TIMEOUT_SECONDS)

    translate=openai_batch_translator(client,model)
    make_glossary=openai_glossary_maker(client,model)

    def work()->None:
        try:
            asyncio.run(store_revision_translations(
                fixture,translate,model=model,make_glossary=make_glossary))
        except Exception as exc:  # noqa: BLE001 - nothing stored, retried next time
            log.warning(
                "revision_translation failed revision=%s error=%s",
                key,type(exc).__name__)
        finally:
            with _REVISION_TRANSLATIONS_LOCK:
                _REVISION_TRANSLATIONS_RUNNING.discard(key)

    threading.Thread(
        target=work,name=f"revision-translation-{key[-48:]}",
        daemon=True).start()


def _with_revision_translations(
    fixture:CuratedProtocolFixture,
    *,
    start_missing:bool=True,
)->CuratedProtocolFixture:
    """The fixture carrying its revision's stored translations, if any.

    A revision with nothing stored that is not being translated -- one made
    executable before translations existed -- starts its generation here,
    once; the session picks up each batch as it is stored. A report reading
    the translations (lane N, decision 6) passes ``start_missing=False``: it
    only reads what is stored.
    """

    try:
        store=_revision_translation_store()
    except Exception as exc:  # noqa: BLE001 - the source is shown instead
        log.warning("revision_translation read_failed error=%s",type(exc).__name__)
        return fixture
    if store is None:
        return fixture
    key=translation_revision_key(fixture)
    try:
        rows=store.fact_translations(key,"ko")
        # Lane KO2, decision 1: each row checked with its own version's
        # glossary; the newest version's passing row is shown.
        attached=with_stored_translations(
            fixture,rows,glossaries=glossaries_by_version(
                store.translation_glossaries(key,"ko")))
    except Exception as exc:  # noqa: BLE001 - the source is shown instead
        log.warning("revision_translation read_failed error=%s",type(exc).__name__)
        return fixture
    finally:
        store.close()
    if not rows and start_missing:
        with _REVISION_TRANSLATIONS_LOCK:
            running=key in _REVISION_TRANSLATIONS_RUNNING
        runner=None if running else _revision_translation_runner()
        if runner is not None:
            try:
                runner(fixture)
            except Exception as exc:  # noqa: BLE001 - the source is shown instead
                log.warning(
                    "revision_translation start_failed error=%s",type(exc).__name__)
    return attached

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
            "VOINEY_LAB_SAFETY_CATALOG must point to an existing "
            f"regular file: {rendered}",
            "VOINEY_LAB_SAFETY_CATALOG",
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
            "VOINEY_LAB_SAFETY_CATALOG is not a usable approved "
            f"catalog: {rendered}",
            "VOINEY_LAB_SAFETY_CATALOG",
        ) from exc
    if approved<1:
        raise ServerConfigurationError(
            "VOINEY_LAB_USAGE_SCOPE has no approved active documents "
            "in VOINEY_LAB_SAFETY_CATALOG: "
            f"{rendered}",
            "VOINEY_LAB_USAGE_SCOPE",
            "VOINEY_LAB_SAFETY_CATALOG",
        )

def server_config()->ServerConfig:
    """Load server-wide policy without exposing configuration values."""
    catalog=os.environ.get("VOINEY_LAB_SAFETY_CATALOG","").strip()
    scope=_runtime_usage_scope()
    catalog_path:Path|None=Path(catalog)
    if scope==SOURCE_PROTOCOL_ONLY_SCOPE:
        # The statement that no institution safety document exists (human
        # decision of 2026-10-13): there is no approved catalog to require,
        # and one that is configured anyway is not read.
        catalog_path=None
    else:
        if not catalog:
            raise ServerConfigurationError(
                "VOINEY_LAB_SAFETY_CATALOG must not be empty",
                "VOINEY_LAB_SAFETY_CATALOG",
            )
        if not catalog_path.is_absolute():
            raise ServerConfigurationError(
                "VOINEY_LAB_SAFETY_CATALOG must be an absolute path: "
                f"{catalog!r}",
                "VOINEY_LAB_SAFETY_CATALOG",
            )
        invalid_policy_fields=[]
        if scope not in ("operational","demo","reference_only","test_only"):
            invalid_policy_fields.append("VOINEY_LAB_USAGE_SCOPE")
        if invalid_policy_fields:
            raise ServerConfigurationError(
                "safety catalog configuration is incomplete",
                *invalid_policy_fields,
            )
        validate_approved_catalog(catalog_path,scope)
    facility=os.environ.get("VOINEY_LAB_FACILITY_ID","").strip() or None
    raw_allowed=os.environ.get("VOINEY_LAB_ALLOWED_LANGUAGES","ko,en,vi")
    try:
        allowed=frozenset(normalize_session_language(value) for value in raw_allowed.split(",") if value.strip())
        default=normalize_session_language(os.environ.get("VOINEY_LAB_SESSION_LANGUAGE","ko"))
    except ValueError as exc:
        raise ServerConfigurationError(
            "session language configuration is invalid",
            "VOINEY_LAB_ALLOWED_LANGUAGES",
            "VOINEY_LAB_SESSION_LANGUAGE",
        ) from exc
    if not allowed or default not in allowed:
        raise ServerConfigurationError(
            "session language configuration is invalid",
            "VOINEY_LAB_ALLOWED_LANGUAGES",
            "VOINEY_LAB_SESSION_LANGUAGE",
        )
    curated_fixture=os.environ.get(
        "VOINEY_LAB_CURATED_PROTOCOL_FIXTURE","").strip()
    curated_provenance=os.environ.get(
        "VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE","").strip()
    curated_source_pdf=os.environ.get(
        "VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF","").strip()
    curated_values=(curated_fixture,curated_provenance,curated_source_pdf)
    curated_paths=tuple(Path(value) if value else None for value in curated_values)
    if any(curated_paths) and (
        not all(curated_paths)
        or any(path is not None and not path.is_absolute() for path in curated_paths)
    ):
        raise ServerConfigurationError(
            "curated development protocol configuration is invalid",
            "VOINEY_LAB_CURATED_PROTOCOL_FIXTURE",
            "VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE",
            "VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF",
        )
    return ServerConfig(catalog_path,facility,scope,allowed,default,
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


def _source_protocol_only(scope:str|None=None)->bool:
    """Whether the run has no institution safety documents (decision of 2026-10-13)."""

    return (_runtime_usage_scope() if scope is None else scope)==SOURCE_PROTOCOL_ONLY_SCOPE


def _safety_scope_public()->dict[str,object]:
    """What the start screen is told about the safety documents behind this run.

    ``notice`` is the server's own sentence, shown as it is: a screen never
    words it. None unless the run has only the protocol's own statements.
    """

    source_only=_source_protocol_only()
    return {
        "usage_scope":_runtime_usage_scope(),
        "source_protocol_only":source_only,
        "notice":NO_INSTITUTION_DOCUMENTS_NOTICE if source_only else None,
    }


#: The replies that tell the experimenter something and change nothing: an
#: answer, a step read again, a question the server turned away. Moving, timing
#: and recording are left out, so a "안전하게 다음으로" is not given the line.
_SAFETY_NOTICE_ACTIONS=frozenset({
    CuratedProtocolAction.QUESTION,
    CuratedProtocolAction.RELATED_QUESTION,
    CuratedProtocolAction.PROTOCOL_QUERY,
    CuratedProtocolAction.LAB_DOMAIN_QA,
    CuratedProtocolAction.UNSUPPORTED,
    CuratedProtocolAction.OFF_TOPIC,
    CuratedProtocolAction.CURRENT,
    CuratedProtocolAction.REPEAT,
    CuratedProtocolAction.FULL_DETAIL,
    CuratedProtocolAction.NEXT_INFORMATION,
    CuratedProtocolAction.COMPLETION_CRITERIA,
    CuratedProtocolAction.PREVIEW_STEP,
    CuratedProtocolAction.STEP_RANGE,
})


def _asks_about_safety(plan,transcript:str)->bool:
    """Whether a read-only answer is about safety: the rules say so, or the words do.

    Display only: nothing here decides a state change. It reads what the
    rules classified (kind, dimensions, intent) and, for a model's answer,
    the words of the question: a safety dimension, or a topic the answer
    checks name (gloves, ventilation, spill, ...).
    """

    if (
        getattr(plan,"question_kind",None)=="safety"
        or "safety" in (getattr(plan,"question_dimensions",()) or ())
        or getattr(plan,"intent_kind",None) in {"current_step_warning","related_safety_question"}
    ):
        return True
    if not isinstance(transcript,str) or not transcript.strip():
        return False
    return "safety" in _question_dimensions(transcript) or any(
        said.search(transcript) for _name,said,_ground in SAFETY_INSTRUCTION_TOPICS)


def _with_safety_scope_notice(plan,scope:str|None,transcript:str):
    """A safety answer of a run with no institution safety document says so, once.

    The line is the server's: "기관 안전 문서 없음 · 원문 안전 주의만". It is
    added to what the screen shows, not to what is spoken or recorded as the
    answer, and only to an answer that changes no state.
    """

    if (
        not _source_protocol_only(scope)
        or getattr(plan,"action",None) not in _SAFETY_NOTICE_ACTIONS
        or getattr(plan,"state_changed",False)
        or not _asks_about_safety(plan,transcript)
    ):
        return plan
    shown=getattr(plan,"display_text",None)
    if not isinstance(shown,str) or not shown.strip() or NO_INSTITUTION_DOCUMENTS_NOTICE in shown:
        return plan
    return replace(plan,display_text=f"{shown.rstrip()}\n\n{NO_INSTITUTION_DOCUMENTS_NOTICE}")


def require_env(name:str)->str:
    value=os.environ.get(name,"").strip()
    if not value: raise RuntimeError(f"{name} is not set")
    return value
def api_url(path:str)->str:
    return os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/") + "/" + path.lstrip("/")


_KEEP_SDK_RETRIES=object()


def _role_client(
    role:RoleModel,*,asynchronous:bool=True,timeout:Any=None,
    max_retries:Any=0,
)->Any:
    """The client one model role calls (lane M1, decision 1).

    For xAI it is the OpenAI SDK client this module always built, with the
    same arguments (``max_retries=_KEEP_SDK_RETRIES`` leaves the SDK's own
    retries, as the brain's call did); the other providers go through
    model_providers' adapters, which hand back the same reply shape.
    """

    if role.provider=="xai":
        options:dict[str,Any]={"base_url":api_url(""),"api_key":require_env("XAI_API_KEY")}
        if max_retries is not _KEEP_SDK_RETRIES:
            options["max_retries"]=max_retries
        if timeout is not None:
            options["timeout"]=timeout
        client=(AsyncOpenAI if asynchronous else OpenAI)(**options)
        return chat_client(role,asynchronous=asynchronous,sdk_client=client)
    return chat_client(
        role,asynchronous=asynchronous,timeout=timeout,
        max_retries=0 if max_retries is _KEEP_SDK_RETRIES else max_retries,
    )


@dataclass(frozen=True)
class SttDiagnosticSettings:
    """Opt-in local audio evidence; disabled and non-persistent by default."""

    enabled:bool=False
    directory:Path=PROJECT_ROOT / "data/runtime/stt-diagnostics"
    max_files:int=20

    @classmethod
    def from_environment(cls)->"SttDiagnosticSettings":
        raw=os.environ.get(
            "VOINEY_LAB_STT_DIAGNOSTICS_ENABLED","false"
        ).strip().casefold()
        if raw not in {"0","1","false","true","no","yes","off","on"}:
            raise ValueError("STT diagnostic mode must be a boolean")
        enabled=raw in {"1","true","yes","on"}
        configured=os.environ.get(
            "VOINEY_LAB_STT_DIAGNOSTIC_DIR","").strip()
        directory=(Path(configured) if configured else cls.directory).resolve()
        runtime_root=(PROJECT_ROOT / "data/runtime").resolve()
        if directory != runtime_root and runtime_root not in directory.parents:
            raise ValueError("STT diagnostic directory must be under data/runtime")
        maximum=int(os.environ.get(
            "VOINEY_LAB_STT_DIAGNOSTIC_MAX_FILES","20"))
        if not 2<=maximum<=100:
            raise ValueError("STT diagnostic file limit is outside bounds")
        return cls(enabled,directory,maximum)


@dataclass(frozen=True)
class CascadeTranscriptionContext:
    """One server-owned STT policy shared by ordinary and barge-in audio."""

    configuration_id:int|None
    session_id:str
    generation:int
    language:str|None
    protocol_id:str|None
    step_id:str|None
    pending_frame:str|None
    keyterms:tuple[str,...]
    audio_origin:str
    vad_threshold:float=0.5
    filler_words:bool=False
    google_v2_adaptation_disabled:bool=False

    def __post_init__(self)->None:
        if self.audio_origin not in {"ordinary","barge_in"}:
            raise ValueError("STT audio origin is invalid")
        if not isinstance(self.vad_threshold,(int,float)) or isinstance(self.vad_threshold,bool):
            raise ValueError("STT vad_threshold is invalid")
        if not 0.0 <= float(self.vad_threshold) <= 1.0:
            raise ValueError("STT vad_threshold is outside bounds")
        if not isinstance(self.filler_words,bool):
            raise ValueError("STT filler_words is invalid")
        if not isinstance(self.google_v2_adaptation_disabled,bool):
            raise ValueError("Google V2 adaptation state is invalid")

    def request_policy(self)->dict[str,object]:
        fields=["format"]
        if self.language is not None:
            fields.append("language")
        fields.append("vad_threshold")
        fields.append("filler_words")
        fields.extend("keyterm" for _ in self.keyterms)
        fields.append("file")
        return {
            "language":self.language,
            "keyterms":list(self.keyterms),
            "vad_threshold":float(self.vad_threshold),
            "filler_words":bool(self.filler_words),
            "request_field_order":fields,
            "pending_frame":self.pending_frame,
            "audio_origin":self.audio_origin,
        }


def _stt_multipart(
    pcm:bytes,*,language:str|None,keyterms:tuple[str,...],
    vad_threshold:float=0.5,filler_words:bool=False,
)->tuple[list[tuple[str,tuple]],bytes,tuple[str,...]]:
    """Build the documented xAI multipart order with the file last."""

    if language not in {None,"ko","en","vi"}:
        raise ValueError("STT language is invalid")
    if not isinstance(vad_threshold,(int,float)) or isinstance(vad_threshold,bool):
        raise ValueError("STT vad_threshold is invalid")
    if not isinstance(filler_words,bool):
        raise ValueError("STT filler_words is invalid")
    threshold=float(vad_threshold)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("STT vad_threshold is outside bounds")
    bounded=tuple(dict.fromkeys(
        value.strip() for value in keyterms
        if isinstance(value,str) and 1<=len(value.strip())<=50
    ))[:100]
    wav=pcm_to_wav(pcm)
    multipart:list[tuple[str,tuple]]=[("format",(None,"true"))]
    if language is not None:
        multipart.append(("language",(None,language)))
    multipart.append(("vad_threshold",(None,f"{threshold:g}")))
    multipart.append(("filler_words",(None,"true" if filler_words else "false")))
    multipart.extend(("keyterm",(None,value)) for value in bounded)
    multipart.append(("file",("utterance.wav",wav,"audio/wav")))
    return multipart,wav,bounded


def persist_stt_diagnostic(
    pcm:bytes,metadata:dict[str,object],*,identity:str,
    settings:SttDiagnosticSettings|None=None,
)->tuple[Path,Path]|None:
    """Persist one consented WAV and sanitized JSON record with bounded retention."""

    selected=settings or SttDiagnosticSettings.from_environment()
    if not selected.enabled:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}",identity):
        raise ValueError("STT diagnostic identity is invalid")
    directory=selected.directory
    directory.mkdir(parents=True,exist_ok=True)
    wav=pcm_to_wav(pcm)
    safe={
        key:value for key,value in metadata.items()
        if key in {
            "configuration_id","session_id","turn_id","generation",
            "input_sample_rate","frame_count","voiced_frame_count",
            "duration_ms","endpoint_reason","configured_prefix_frames",
            "configured_prefix_ms","retained_prefix_frames",
            "retained_prefix_ms","wav_sha256","wav_byte_count",
            "stt_endpoint","request_field_order","language","keyterms",
            "vad_threshold","filler_words",
            "response_status","response_duration_seconds","word_count",
            "detected_language","raw_transcript","normalized_transcript",
            "correction_class","clarification_required","intent_kind",
            "action","mutation_authorized","browser_audio_constraints",
            "audio_origin","pending_frame",
        }
    }
    safe["wav_sha256"]=hashlib.sha256(wav).hexdigest()
    safe["wav_byte_count"]=len(wav)
    wav_path=directory / f"{identity}.wav"
    json_path=directory / f"{identity}.json"
    wav_path.write_bytes(wav)
    json_path.write_text(
        json.dumps(safe,ensure_ascii=False,sort_keys=True,indent=2)+"\n",
        encoding="utf-8",
    )
    records=sorted(directory.glob("*.json"),key=lambda item:item.stat().st_mtime)
    while len(records)>selected.max_files:
        old=records.pop(0)
        old.with_suffix(".wav").unlink(missing_ok=True)
        old.unlink(missing_ok=True)
    return wav_path,json_path

def transcribe(
    pcm:bytes,
    *,
    language:str|None=None,
    keyterms:tuple[str,...]=(),
    vad_threshold:float=0.5,
    filler_words:bool=False,
    disable_google_v2_adaptation:bool=False,
)->Transcription:
    """Send one utterance to the configured STT provider (xAI by default).

    The xAI request below is the original one; the other providers live in
    ``voice_providers`` and keep the same contract (raise on failure or
    timeout, empty text on an empty result).
    """

    settings=SttProviderSettings.from_environment()
    language=voice_providers.stt_language(settings,language)
    if settings.provider!=voice_providers.XAI:
        provider_keyterms=(
            ()
            if settings.provider==voice_providers.GOOGLE_CLOUD_V2
            and disable_google_v2_adaptation
            else keyterms
        )
        return voice_providers.transcribe(
            pcm,settings,language=language,keyterms=provider_keyterms)
    multipart,_,_= _stt_multipart(
        pcm,language=language,keyterms=keyterms,vad_threshold=vad_threshold,
        filler_words=filler_words)
    try:
        response=requests.post(api_url("stt"),headers={"Authorization":f"Bearer {require_env("XAI_API_KEY")}"},
            files=multipart,timeout=settings.timeout_seconds)
    except requests.Timeout as exc:
        # Lane HEAR2: a failure the screen can name (no address, no key in the message).
        raise SpeechProviderError("xai STT timed out",code="timeout") from exc
    except requests.RequestException as exc:
        raise SpeechProviderError("xai STT could not be reached",code="unreachable") from exc
    if not response.ok:
        raise SpeechProviderError(
            f"xai STT failed ({response.status_code})",status=int(response.status_code))
    payload=response.json()
    text=payload.get("text","")
    duration=payload.get("duration")
    if (not isinstance(duration,(int,float)) or isinstance(duration,bool)
            or duration<0):
        duration=None
    raw_words=payload.get("words",())
    words=tuple(
        {
            key:value for key,value in item.items()
            if key in {"word","start","end","speaker"}
        }
        for item in raw_words[:500]
        if isinstance(item,dict)
    ) if isinstance(raw_words,list) else ()
    return Transcription(
        text.strip() if isinstance(text,str) else "",
        normalize_provider_language(payload.get("language")),
        duration_seconds=float(duration) if duration is not None else None,
        words=words,response_status=response.status_code,
    )

def validate_tts_pcm(response:requests.Response)->bytes:
    if not response.ok:
        detail=response.text[:500] if response.content else "empty response"
        raise RuntimeError(f"xAI TTS failed ({response.status_code}): {detail}")
    if "json" in response.headers.get("content-type","").lower():
        raise RuntimeError("xAI TTS returned JSON instead of requested raw PCM")
    if not response.content or len(response.content) % 2:
        raise RuntimeError("xAI TTS returned invalid PCM")
    return response.content


def _tts_voice() -> str:
    """Resolve the one active Cascade TTS voice configuration path."""

    return TtsProviderSettings.from_environment().voice


def _tts_provider() -> str:
    return TtsProviderSettings.from_environment().provider


# --- The agent's voice, chosen by the person (lane ON, decision ②) -----------
#: Previews already made in this process: (provider, voice id) -> 16 kHz PCM.
_VOICE_PREVIEW_CACHE:dict[tuple[str,str],bytes]={}


def _agent_voice_known(value:str)->bool:
    """``default`` or one of the configured provider's choosable voices."""

    if value=="default":
        return True
    try:
        provider=_tts_provider()
    except ConfigurationError:
        return False
    return any(voice.voice_id==value for voice in voice_providers.korean_voices(provider))


def _agent_voice_id(settings:Mapping[str,str])->str|None:
    """The voice id the experimenter chose, or None for the provider's default."""

    chosen=str(settings.get("agent_voice","default") or "default")
    return None if chosen=="default" or not _agent_voice_known(chosen) else chosen


def _session_voice_fallback()->str|None:
    """The provider's configured voice, or None when no provider is set."""

    try:
        return _tts_voice() or None
    except ConfigurationError:
        return None


def _session_tts_voice()->str|None:
    """The chosen voice of the session speaking now, when it chose one."""

    session=_SPEAKING_SESSION.get()
    voice=getattr(session,"tts_voice",None)
    return voice if isinstance(voice,str) and voice else None


def stt_endpoint_label() -> str:
    """The STT request the diagnostics name: the xAI path keeps "/v1/stt"."""

    provider=SttProviderSettings.from_environment().provider
    return {
        voice_providers.XAI:"/v1/stt",
        voice_providers.GOOGLE_CLOUD:"google_cloud:/v1/speech:recognize",
        voice_providers.GOOGLE_CLOUD_V2:"google_cloud_v2:/v2/recognizers/_:recognize",
        voice_providers.ELEVENLABS:"elevenlabs:/v1/speech-to-text",
    }[provider]

# --- Lane VOX, decision 3 (2026-10-23): a guidance sentence's audio kept -----
#: At most this many sentences, and this many bytes of 16 kHz PCM (about 8.7
#: minutes of speech), are kept in this process; the one used longest ago
#: goes first.
SENTENCE_AUDIO_MAX_ENTRIES=512
SENTENCE_AUDIO_MAX_BYTES=16*1024*1024


class SentenceAudioCache:
    """Sentence audio by (lab, provider, model, voice, language, sentence), bounded.

    ``synthesize`` runs in worker threads, so every read and write holds the
    lock. Audio larger than the whole memory is never kept.
    """

    def __init__(self,*,max_entries:int,max_bytes:int)->None:
        self.max_entries=max_entries
        self.max_bytes=max_bytes
        self.bytes=0
        self._audio:collections.OrderedDict[tuple[str,...],bytes]=collections.OrderedDict()
        self._lock=threading.Lock()

    def __len__(self)->int:
        with self._lock:
            return len(self._audio)

    def get(self,key:tuple[str,...])->bytes|None:
        with self._lock:
            pcm=self._audio.get(key)
            if pcm is not None:
                self._audio.move_to_end(key)
            return pcm

    def put(self,key:tuple[str,...],pcm:bytes)->None:
        if not pcm or len(pcm)>self.max_bytes:
            return
        with self._lock:
            old=self._audio.pop(key,None)
            if old is not None:
                self.bytes-=len(old)
            self._audio[key]=pcm
            self.bytes+=len(pcm)
            while len(self._audio)>self.max_entries or self.bytes>self.max_bytes:
                _,dropped=self._audio.popitem(last=False)
                self.bytes-=len(dropped)

    def clear(self)->None:
        with self._lock:
            self._audio.clear()
            self.bytes=0


SENTENCE_AUDIO=SentenceAudioCache(
    max_entries=SENTENCE_AUDIO_MAX_ENTRIES,max_bytes=SENTENCE_AUDIO_MAX_BYTES)
_KEEP_SENTENCE_AUDIO:contextvars.ContextVar[bool]=contextvars.ContextVar(
    "voiney_lab_keep_sentence_audio",default=False)


@contextmanager
def sentence_audio_kept(keep:bool=True)->Iterator[None]:
    """Inside, ``synthesize`` says a sentence it made before from memory.

    Only the guidance asks for it: the protocol's own sentences and the
    server's fixed ones. ``asyncio.to_thread`` copies the context, so a
    sentence made in a worker thread from inside is kept too. Everything
    else -- the experimenter's words read back, a record, the emergency
    reply -- is made anew each time.
    """

    token=_KEEP_SENTENCE_AUDIO.set(keep)
    try:
        yield
    finally:
        _KEEP_SENTENCE_AUDIO.reset(token)


def _speaking_organization()->str:
    """The lab (organisation) whose request or voice connection speaks now.

    Empty with no identity: a server without the workspace serves one
    development tenant. ``asyncio.to_thread`` copies the context, so a
    worker thread's synthesis knows it too.
    """

    principal=_REQUEST_PRINCIPAL.get()
    return principal.organization_id if principal is not None else ""


# The spoken representation alone changes; callers keep their source/display text.
_SPOKEN_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_SPOKEN_UNITS = (
    (r"(?:µ|μ|u)[lL]", "마이크로리터"), (r"m[lL]", "밀리리터"),
    (r"[lL]", "리터"), (r"(?:µ|μ|u)g", "마이크로그램"),
    (r"mg", "밀리그램"), (r"kg", "킬로그램"), (r"g", "그램"),
    (r"(?:µ|μ|u)M", "마이크로몰"), (r"mM", "밀리몰"),
    (r"nM", "나노몰"), (r"M", "몰"),
    (r"[rR][pP][mM]", "알피엠"), (r"[×x]\s*g", "지"),
    (r"°\s*C|˚C|℃", "도"), (r"%|퍼센트", "퍼센트"),
    (r"min(?:ute)?s?|분", "분"), (r"h(?:ours?|rs?)?|시간", "시간"),
    (r"s(?:ec(?:ond)?s?)?|초", "초"), (r"months?|개월", "개월"),
    (r"days?|일", "일"), (r"weeks?|주", "주"),
    (r"개", "개"),
)
_SPOKEN_UNIT = "(?:" + "|".join(pattern for pattern, _ in _SPOKEN_UNITS) + ")(?![A-Za-z])"
_SPOKEN_VALUE = re.compile(
    rf"(?<![A-Za-z0-9_.:−-])(?P<qualifier>>=|<=|[~≈><≥≤])?\s*"
    rf"(?P<first>[-−]?{_SPOKEN_NUMBER})"
    rf"(?:(?P<operator>\s*(?:[–—−±:]|-)\s*)(?P<second>[-−]?{_SPOKEN_NUMBER}))?"
    rf"\s*(?P<unit>{_SPOKEN_UNIT})?(?![A-Za-z0-9_:-]|\.\d)")


def _say_number(number: str) -> str:
    """Read digits literally after a decimal point; never round or use float."""
    if number.startswith(("-", "−")):
        return "마이너스 " + _say_number(number[1:])
    whole, dot, fraction = number.replace(",", "").partition(".")
    digits = "영일이삼사오육칠팔구"
    value = int(whole) if len(whole) <= 16 else 0
    if len(whole) > 16:
        # No guessed magnitude outside the explicit Korean place names.
        spoken = " ".join(digits[int(digit)] for digit in whole)
    elif value == 0:
        spoken = "영"
    else:
        groups = []
        for place, suffix in ((10**12, "조"), (10**8, "억"), (10**4, "만"), (1, "")):
            group, value = divmod(value, place)
            if not group:
                continue
            small = ""
            for power, name in ((1000, "천"), (100, "백"), (10, "십"), (1, "")):
                digit, group = divmod(group, power)
                if digit:
                    small += ("" if digit == 1 and power > 1 else digits[digit]) + name
            groups.append(("" if small == "일" and suffix else small) + suffix)
        spoken = " ".join(groups)
    if dot:
        spoken += " 점 " + " ".join(digits[int(digit)] for digit in fraction)
    return spoken


def _say_counter(number: str) -> str:
    """The Korean modifier before hours/items; decimals keep every digit."""
    plain = number.replace(",", "")
    if not plain.isdecimal() or len(plain) > 2 or not 1 <= int(plain) <= 99:
        return _say_number(number)
    value = int(plain)
    if value == 20:
        return "스무"
    tens = ("", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔")
    ones = ("", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉")
    return tens[value // 10] + ones[value % 10]


def normalize_korean_speech(text: str) -> str:
    """A deterministic reading for TTS, without changing any plan or evidence.

    Only explicit numeric expressions and known units are read. Digits inside
    scientific identifiers remain as written (Tn5, H3K4me3, Tris-HCl).
    """
    def clock(match: re.Match[str]) -> str:
        raw = match.group(0)
        negative = raw.startswith(("-", "−"))
        fields = raw.lstrip("-−").split(":")
        units = ("시간", "분", "초")
        parts = [f"{_say_counter(value) if unit == '시간' else _say_number(value)} {unit}"
                 for value, unit in zip(fields, units) if int(value)]
        spoken = " ".join(parts) or "영 초"
        return "마이너스 " + spoken if negative else spoken

    # Read dilution ranges before clocks and individual ratios.
    text = re.sub(
        rf"(?<![\w:-])({_SPOKEN_NUMBER}):({_SPOKEN_NUMBER})\s*[-–—]\s*"
        rf"({_SPOKEN_NUMBER}):({_SPOKEN_NUMBER})(?![\w:])",
        lambda match: f"{_say_number(match[1])} 대 {_say_number(match[2])}에서 "
                      f"{_say_number(match[3])} 대 {_say_number(match[4])}", text)
    # Only an explicit HH:MM:SS acquires time units; two fields may be a dilution.
    text = re.sub(r"(?<![\w:.−-])[-−]?\d{2}:\d{2}:\d{2}(?![\w:])", clock, text)

    def value(match: re.Match[str]) -> str:
        first, second = match["first"], match["second"]
        unit, operator, qualifier = match["unit"], match["operator"], match["qualifier"]
        if not unit and not operator and not qualifier and "." not in first:
            # Preserve labels, identifiers and bare numbers for the voice's
            # ordinary reading, including step numbers and citation numbers.
            return match.group(0)
        reading = next((word for pattern, word in _SPOKEN_UNITS if re.fullmatch(pattern, unit)), None) if unit else None
        count_unit = reading in {"시간", "개"} and (not operator or operator.strip() != ":")
        number_reader = _say_counter if count_unit else _say_number
        spoken = number_reader(first)
        if second:
            separator = " 대 " if operator.strip() == ":" else (
                " 플러스 마이너스 " if operator.strip() == "±" else "에서 ")
            if count_unit:
                spoken += f" {reading}"
            spoken += separator + number_reader(second)
        if unit:
            if reading == "도":
                spoken = "섭씨 " + ("영하 " + spoken.removeprefix("마이너스 ") if first.startswith(("-", "−")) else spoken)
                if second and second.startswith(("-", "−")):
                    spoken = spoken.replace(separator + "마이너스 ", separator + "영하 ")
            spoken += " " + reading
        if qualifier in {"~", "≈"}:
            spoken = "약 " + spoken
        elif qualifier:
            spoken += " " + {">": "넘게", "<": "미만", ">=": "이상", "≥": "이상",
                              "<=": "이하", "≤": "이하"}[qualifier]
        # The optional leading/trailing spaces belong to the caller's sentence.
        leading = " " if match.group(0).startswith(" ") else ""
        trailing = " " if match.group(0).endswith(" ") and not unit else ""
        return leading + spoken + trailing

    result = _SPOKEN_VALUE.sub(value, text)
    # A repeated unit (20%±2%) leaves the sign outside either numeric match.
    return " ".join(result.replace("±", " 플러스 마이너스 ").split()) if "±" in result else result


def _clean_tts_text(text: str) -> str:
    # A decimal or negative value at any line start is not a list marker.
    numeric = re.compile(r"^\s*(?:-\s*\d|\d+\.\d)")
    if any(numeric.match(line) for line in text.splitlines()):
        return " ".join(
            clean_speech_text("scientific_value " + line).removeprefix("scientific_value ")
            if numeric.match(line) else clean_speech_text(line)
            for line in text.splitlines())
    return clean_speech_text(text)


def synthesize(text:str,language:str|None=None)->bytes:
    spoken = normalize_korean_speech(text) if language in (None, "ko") else text
    clean_text = _clean_tts_text(spoken)
    if not clean_text:
        return b""
    settings=TtsProviderSettings.from_environment()
    # Lane ON, decision 2: the voice the person chose for this session.
    chosen=_session_tts_voice()
    if chosen is not None:
        settings=replace(settings,voice=chosen)
    # Lane VOX, decision 3: the same provider, model, voice, language and
    # sentence give the same audio. An unset provider keeps nothing and
    # fails below by its setting's name, as before. Lane FIXV, decision 3:
    # and the same lab -- another lab's kept sentence is made anew, so how
    # fast a sentence comes back tells no lab what another one said.
    key=(
        (_speaking_organization(),settings.provider,settings.model,settings.voice,
         language or "ko",clean_text)
        if _KEEP_SENTENCE_AUDIO.get() and settings.provider else None
    )
    if key is not None:
        kept=SENTENCE_AUDIO.get(key)
        if kept is not None:
            return kept
    pcm=_provider_speech(clean_text,language,settings)
    if key is not None:
        SENTENCE_AUDIO.put(key,pcm)
    return pcm


def _provider_speech(clean_text:str,language:str|None,settings:TtsProviderSettings)->bytes:
    """One request to the configured TTS provider for already cleaned text."""

    if settings.provider!=voice_providers.XAI:
        return voice_providers.synthesize(clean_text,language or "ko",settings)
    voice = settings.voice
    response=requests.post(
        api_url("tts"),
        headers={"Authorization":f"Bearer {require_env('XAI_API_KEY')}"},
        json={
            "text":clean_text,
            "voice_id":voice,
            "language":language or "ko",
            "output_format":{"codec":"pcm","sample_rate":16000},
        },
        timeout=settings.timeout_seconds,
    )
    return validate_tts_pcm(response)

# --- The server hears itself (lane XO, decision 6b of 2026-10-05) ------------
# A laptop speaker's sound reaches its microphone. The xAI STT request carries
# vad_threshold (0.5), and an empty transcript sends a barge-in candidate
# back to the playback it interrupted; Google's and ElevenLabs' batch STT take
# no such threshold, so the agent's own voice came back as a user turn. The
# server knows what it has just said, so a transcript heard while it speaks,
# or just after, that is mostly one of those sentences is dropped as an echo:
# nothing changes and the drop is logged. A pause or end word the agent did
# not say keeps the transcript -- a researcher speaking over the agent.

#: Sentences kept, and for how long, to compare a transcript with.
ECHO_MEMORY_SENTENCES=12
ECHO_MEMORY_SECONDS=60.0
#: After playback ends, the room and the browser's audio path still carry it.
ECHO_TAIL_SECONDS=3.0
#: Shorter transcripts ("다음 단계", "네", "잠깐만") are never taken for an
#: echo: a researcher's short command often repeats the agent's own words.
ECHO_MIN_CHARACTERS=12
#: An echo is the share of the transcript's characters inside a run of at
#: least ECHO_RUN_CHARACTERS that one spoken sentence also has.
ECHO_RUN_CHARACTERS=3
#: Set from ~/reports/lane_xo_echo_calibration.md (lane XO report §3): with
#: every recent sentence counted as just played, no researcher utterance of
#: the evaluation set reaches 0.8, and most simulated echoes do.
ECHO_OVERLAP_THRESHOLD=0.8

_ECHO_DROPPED=re.compile(r"[^0-9a-z가-힣µμ%]+")
_SPEAKING_SESSION:contextvars.ContextVar["ListenerSession|None"]=contextvars.ContextVar(
    "voiney_lab_speaking_session",default=None)


def echo_key(text:str)->str:
    """``text`` with case, spacing and punctuation removed, for comparison."""

    return _ECHO_DROPPED.sub("",unicodedata.normalize("NFKC",text or "").casefold())


def echo_overlap(transcript:str,spoken:str)->float:
    """The share of the transcript ``spoken`` also has, in runs of 3 or more.

    Each character of the normalized transcript counts once it sits in a
    run of ECHO_RUN_CHARACTERS that the spoken sentence contains. Containment
    rather than similarity: an echo is usually a stretch of a longer sentence,
    heard from wherever the microphone caught it.
    """

    heard=echo_key(transcript); said=echo_key(spoken)
    run=ECHO_RUN_CHARACTERS
    if len(heard)<run:
        return 0.0
    known={said[i:i+run] for i in range(len(said)-run+1)}
    covered=[False]*len(heard)
    for i in range(len(heard)-run+1):
        if heard[i:i+run] in known:
            covered[i:i+run]=[True]*run
    return sum(covered)/len(heard)


@dataclass(frozen=True)
class OwnSpeechEcho:
    """A transcript taken for the agent's own voice, and why."""

    spoken:str
    overlap:float


def said(text:str)->str:
    """Remember ``text`` as said in the current session, and hand it back.

    Wrapped round the text at every TTS call, ``asyncio.to_thread(synthesize,
    said(text), language)``, so the sentence is known before its audio exists.
    """

    session=_SPEAKING_SESSION.get()
    if session is not None:
        session.remember_spoken(text)
    return text


def frame_complete_audio(pcm:bytes)->list[bytes]:
    buffer=FrameBuffer(); frames=buffer.push(pcm); tail=buffer.finish(pad=True)
    if tail is not None: frames.append(tail)
    return frames


# --- Lane VOX (2026-10-23): the guidance said sentence by sentence -----------

def speech_sentences(text:str)->tuple[str,...]:
    """The pieces ``text`` is said in, one sentence or more each (decision 2).

    The sentence ends are ``brain.SentenceChunker``'s: a period between two
    digits, after an abbreviation such as "Fig." or ending a piece shorter
    than four characters ends no sentence, so a number keeps its decimals and
    its unit. The pieces are cut from the text as the TTS hears it
    (``clean_speech_text``) and only where a space follows, and a piece that
    the same cleaning would change when it stands alone -- "0.5 mL" or
    "-20 °C" at its start read as a list mark -- is joined to the one before
    it, so the pieces said one after another are exactly the words the whole
    would have been. ``(text,)`` itself when it is one piece or cannot be
    cut so.
    """

    # Keep numeric line starts intact: ordinary Markdown cleaning would
    # treat their decimal point/sign as a list marker before TTS sees them.
    if re.search(r"^\s*(?:-\s*\d|\d+\.\d)", text, flags=re.MULTILINE):
        return (text,)
    whole=clean_speech_text(text)
    chunker=SentenceChunker()
    found=[item.text for item in (*chunker.feed(whole),*chunker.flush())]
    spans:list[list[int]]=[]
    position=0
    for sentence in found:
        start=whole.find(sentence,position)
        if start<0 or whole[position:start].strip():
            return (text,)
        end=start+len(sentence)
        if spans and (start==position or clean_speech_text(sentence)!=sentence):
            spans[-1][1]=end
        else:
            spans.append([start,end])
        position=end
    pieces=tuple(whole[start:end] for start,end in spans)
    if (
        len(pieces)<2 or " ".join(pieces)!=whole
        or any(clean_speech_text(piece)!=piece for piece in pieces)
    ):
        return (text,)
    return pieces


def _configured_candidate_fixture(config:ServerConfig)->CuratedProtocolFixture|None:
    if config.curated_protocol_fixture_path is None:
        return None
    paths=(config.curated_protocol_fixture_path,config.curated_protocol_provenance_path,
           config.curated_protocol_source_pdf_path)
    fixture_file=Path(paths[0])
    checked=(*paths,*(fixture_file.with_name(f"{fixture_file.stem}.{suffix}")
                     for suffix in ("localization.ko.json","visuals.json","timers.json")))
    digests=tuple(file_sha256(path) if path is not None and Path(path).is_file() else None for path in checked)
    return _cached_candidate_fixture(paths,digests,load_curated_protocol_fixture)


@lru_cache(maxsize=4)
def _cached_candidate_fixture(
    paths:tuple[Path,Path|None,Path|None],digests:tuple[str|None,...],
    loader:Callable[...,CuratedProtocolFixture],
)->CuratedProtocolFixture:
    """Cache verified development inputs by bytes, including every sidecar."""
    return loader(*paths)


def _candidate_fixture_execution_state(
    fixture:CuratedProtocolFixture,
)->dict[str,object]:
    """Answer whether the configured development fixture may execute right now.

    The same question the catalog answers for every uploaded PDF (decision
    of 2026-10-08): the entry materialized from this exact fixture is
    executable when its analysis carries no execution blocker. Being the
    configured fixture says which file the server loaded, not that it may
    run, so a disabled store, a missing entry or a fixture the store does not
    match is a no. The curated fixture is development material and never runs
    under the operational usage scope.
    """

    state:dict[str,object]={
        "available_for_execution":False,
        "blocked_reason":"development_fixture_not_materialized",
        "execution_blockers":[],
        "execution_notices":[],
    }
    if not _development_fixture_allowed():
        state["blocked_reason"]="usage_scope_not_development"
        return state
    settings=_protocol_store_settings()
    if not settings.enabled:
        state["blocked_reason"]="protocol_store_disabled"
        return state
    try:
        catalog,store=_open_protocol_catalog()
    except Exception:  # noqa: BLE001 - an unreadable catalog grants nothing
        state["blocked_reason"]="protocol_catalog_unavailable"
        return state
    try:
        if not catalog.development_fixture_is_materialized(fixture):
            return state
        entry=catalog.get_entry(fixture.protocol_id)
        state["available_for_execution"]=bool(entry.available_for_execution)
        # Materialization above proves this is the stored analysis. These
        # reason projections use that same analysis without reopening its PDF.
        versions=catalog._review_versions(catalog._latest_protocol_revision(fixture.protocol_id))
        shown=versions[-1][1] if versions else fixture.draft
        state["execution_blockers"]=catalog._execution_reasons(shown,blocking=True)
        state["execution_notices"]=catalog._execution_reasons(shown,blocking=False)
        state["blocked_reason"]=(
            None if entry.available_for_execution else "execution_blocked")
    except Exception:  # noqa: BLE001 - see the docstring: unreadable is a no
        state["available_for_execution"]=False
        state["blocked_reason"]="protocol_catalog_unavailable"
    finally:
        store.close()
    return state


def _candidate_catalog_dict(fixture:CuratedProtocolFixture)->dict[str,object]:
    execution=_candidate_fixture_execution_state(fixture)
    return {
        "protocol_id":fixture.protocol_id,
        "title":fixture.title,
        "source_filename":fixture.source_pdf_path.name if fixture.source_pdf_path else "source.pdf",
        "source_sha256":fixture.source_pdf_sha256,
        "revision_id":fixture.revision_id,
        "readiness_status":fixture.draft.readiness.status.value,
        "analysis_status":"validated_curated_fixture",
        "step_count":len(fixture.steps),
        "created_at":None,
        "available_for_execution":execution["available_for_execution"],
        "execution_blocker_codes":[
            item.get("code") for item in execution.get("execution_blockers") or []
            if isinstance(item,dict)],
        "development_only":True,
        "lifecycle_state":(
            "ready" if execution["available_for_execution"] else "blocked"),
        "execution_blocked_reason":execution["blocked_reason"],
        "source_disclosure":{
            "recorded":False,"status":None,"status_ko":"확인 기록 없음",
            "note":"","recorded_at":None,
        },
    }


def _candidate_row_blocked(row:dict[str,object],reason:str)->dict[str,object]:
    """The development fixture's list row, taken out of the executable set."""

    return {
        **row,
        "available_for_execution":False,
        "lifecycle_state":"blocked",
        "execution_blocked_reason":reason,
    }


def _catalog_entry_projection(
    catalog:ProtocolCatalog,entry:ProtocolCatalogEntry,
)->dict[str,object]:
    """Project one catalog entry as the screen reads it."""

    return entry.public_dict()


def _public_protocol_catalog_entries(
    *,tenant_scoped:bool=True,
)->tuple[list[dict[str,object]],ProtocolPersistenceSettings,str|None]:
    """Resolve visible catalog entries without analysis or approval side effects.

    The configured development fixture follows the same tenant visibility as
    stored entries. Only process startup counts all entries without a caller.
    """

    config=server_config()
    include_development=(
        not tenant_scoped
        or not _auth_settings().accounts
        or _account_is_admin()
    )
    visible=_visible_catalog_resource_ids() if tenant_scoped else None
    # A development fixture that does not fit takes only itself out of the
    # list (lane PL, stage 2b; lane BT section 6 item 1): one that cannot be
    # read is left out, one the catalog does not match is shown as not
    # executable, and the protocols a person uploaded are listed either way.
    try:
        candidate=_configured_candidate_fixture(config)
    except Exception as exc:  # noqa: BLE001 - the fixture alone is left out
        log.warning(
            "protocol.catalog.development_fixture_unavailable error=%s",
            type(exc).__name__)
        candidate=None
    if candidate is not None and visible is not None and candidate.protocol_id not in visible:
        candidate=None
    candidate_protocol_id=candidate.protocol_id if candidate is not None else None
    entries=[]
    if candidate is not None:
        entries.append(_candidate_catalog_dict(candidate))
    settings=_protocol_store_settings()
    if not settings.enabled:
        if not include_development:
            entries=[item for item in entries if item.get("development_only") is not True]
        return entries,settings,candidate_protocol_id
    catalog,store=_open_protocol_catalog()
    try:
        selected=visible
        for item in (catalog.list_entries() if selected is None else catalog.list_entries(frozenset(selected))):
            if candidate is not None and item.protocol_id==candidate.protocol_id:
                try:
                    materialized=catalog.development_fixture_is_materialized(candidate)
                except Exception as exc:  # noqa: BLE001 - unreadable is a no
                    log.warning(
                        "protocol.catalog.development_fixture_check_failed error=%s",
                        type(exc).__name__)
                    materialized=False
                if materialized:
                    continue
                log.warning(
                    "protocol.catalog.development_fixture_conflict "
                    "fixture_not_executable=true")
                entries[0]=_candidate_row_blocked(
                    entries[0],"development_fixture_conflicts_with_catalog")
                continue
            public=_catalog_entry_projection(catalog,item)
            public["analysis_run"]=catalog.analysis_run_status(
                item.protocol_id).public_dict()
            entries.append(public)
    finally:
        store.close()
    if not include_development:
        entries=[item for item in entries if item.get("development_only") is not True]
    return entries,settings,candidate_protocol_id


def log_protocol_catalog_runtime_configuration()->None:
    """Log only sanitized protocol backend identity and visible entry count."""

    try:
        if _protocol_store_settings().enabled:
            catalog,store=_open_protocol_catalog()
            try:
                catalog.prepare_source_metadata()
                catalog.warm_analysis_records()
                catalog.recover_interrupted_ocr(frozenset(
                    protocol_id for protocol_id,task in _PROTOCOL_OCR_TASKS.items()
                    if not task.done()))
            finally:
                store.close()
        entries,settings,candidate_protocol_id=(
            _public_protocol_catalog_entries(tenant_scoped=False)
        )
        development_fixture_enabled=candidate_protocol_id is not None
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
    if isinstance(exc,(AuthenticationRequiredError,AuthorizationDeniedError,WorkspaceError)):
        return _workspace_http_error(exc)
    if isinstance(exc,ProtocolPdfTooLargeError):
        return HTTPException(status_code=413,detail="protocol_pdf_too_large")
    # Lane AN, FM: a Word document or photo that could not become its PDF.
    # A converter this server lacks is the server's state (503), not the
    # file's; a file that is not what its type names is the file's (422).
    if isinstance(exc,ProtocolSourceConversionUnavailableError):
        return HTTPException(status_code=503,detail=exc.code)
    if isinstance(exc,ProtocolSourceTooLargeError):
        return HTTPException(status_code=413,detail=exc.code)
    if isinstance(exc,(ProtocolSourceFormatError,ProtocolSourceConversionError)):
        return HTTPException(status_code=422,detail=exc.code)
    if isinstance(exc,ProtocolPdfTypeError):
        return HTTPException(status_code=415,detail="unsupported_pdf_media_type")
    if isinstance(exc,ProtocolPdfWorkerError):
        # The parser process died or hung. Not 422: the document is not the
        # thing that failed, and telling a reader it is invalid would be
        # untrue. Not a silent empty extraction either -- that is the fail
        # dead this boundary exists to remove.
        return HTTPException(status_code=503,detail=exc.code)
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
    if isinstance(exc,ProtocolAnalysisUnavailableError):
        return HTTPException(status_code=503,detail=exc.code)
    if isinstance(exc,ProtocolOcrUnavailableError):
        return HTTPException(status_code=503,detail=exc.code)
    if isinstance(exc,ProtocolOcrError):
        return HTTPException(status_code=422,detail=exc.code)
    if isinstance(exc,ProtocolCatalogNotFoundError):
        return HTTPException(status_code=404,detail=getattr(exc,"code","not_found"))
    if isinstance(exc,ProtocolRegistrationError):
        return HTTPException(status_code=400,detail=exc.code)
    if isinstance(exc,ReagentTableTooLargeError):
        return HTTPException(status_code=413,detail=exc.code)
    # Lane RV, decision b: a review decision the server does not take is the
    # request's (422); a review read against an analysis that has since
    # changed is a conflict (409).
    if isinstance(exc,ProtocolReviewTargetChangedError):
        return HTTPException(status_code=409,detail=exc.code)
    if isinstance(exc,ProtocolReviewError):
        return HTTPException(status_code=422,detail=exc.code)
    # Lane EASY, decision 1: pages to analyse that the document does not have.
    if isinstance(exc,ProtocolAnalysisPageRangeInvalidError):
        return HTTPException(status_code=422,detail=exc.code)
    if isinstance(exc,ReagentTableError):
        return HTTPException(status_code=422,detail=exc.code)
    return HTTPException(
        status_code=409 if isinstance(exc,ProtocolCatalogError) else 500,
        detail=getattr(exc,"code","protocol_catalog_error"),
    )


async def _json_object(request:Request)->dict[str,object]:
    try:
        payload=await request.json()
    except (json.JSONDecodeError,UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400,detail="invalid_json") from exc
    if not isinstance(payload,dict):
        raise HTTPException(status_code=400,detail="invalid_json")
    return payload


@app.get("/api/workspace/session")
async def get_workspace_session()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            store.record_workspace_access(principal)
            # Lane DI (2026-10-08): the experimenter's bench is the one
            # workspace; the reviewer and lab-admin screens are gone.
            return {
                "principal_id":principal.principal_id,
                "display_name":principal.display_name,
                "organization_id":principal.organization_id,
                "workspaces":["researcher"],
                "authentication_method":principal.authentication_method,
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/auth/session")
async def get_auth_session(request:Request,response:Response)->dict[str,object]:
    """Which door is in use and who is signed in (lane ON, decision ①).

    Answered without a session, so the screen can show the login step.
    In dev mode the one development identity is "signed in"; no cookie.
    In accounts mode a browser without a device marker gets one here, before
    its first login (lane PRE, decision 3).
    """

    auth=_auth_settings()
    if not auth.accounts:
        principal=_REQUEST_PRINCIPAL.get()
        if principal is None:
            try:
                principal=_identity_resolver().resolve(request.headers.get("authorization"))
            except Exception:  # noqa: BLE001 - nobody is named, which is the answer
                principal=None
        return {
            "mode":"dev","signed_in":principal is not None,
            "account":None if principal is None else {
                "account_id":principal.principal_id,"name":principal.display_name,
                "organization_id":principal.organization_id,"admin":False,
                "entitlement":None,"disabled":False,
            },
        }
    try:
        token=_session_token(request)
        store=_account_store()
        try:
            account=store.account_for_session(token)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    _ensure_device_cookie(response,request)
    return {
        "mode":"accounts","signed_in":account is not None,
        "account":None if account is None else account.public_dict(),
    }


@app.get("/api/auth/invites/{token}")
async def get_auth_invite(token:str)->dict[str,object]:
    """Whether an invite link still opens, and what it joins (the signup screen reads it)."""

    _accounts_only()
    try:
        store=_account_store()
        try:
            invite=store.invite_for_token(token)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    return {
        "valid":True,"organization_id":invite.organization_id,"admin":invite.admin,
        "entitlement":invite.entitlement,"expires_at":invite.expires_at,
    }


@app.post("/api/auth/signup",status_code=201)
async def post_auth_signup(request:Request,body:dict[str,Any]=Body(...))->Response:
    """Join through an invite (name and password); signed in at once."""

    _accounts_only()
    try:
        store=_account_store()
        try:
            account=store.sign_up(body.get("invite"),body.get("name"),body.get("password"))
            token=store.open_session(account)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    log.info("auth.signup account_id=%s admin=%s",account.account_id,account.admin)
    try:
        return _signed_in_response(request,{"account":account.public_dict()},token,status_code=201)
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/auth/login")
async def post_auth_login(request:Request,body:dict[str,Any]=Body(...))->Response:
    """Log in by name and password; failures are limited by name on this device, and by name."""

    _accounts_only()
    try:
        store=_account_store()
        try:
            account=store.log_in(
                body.get("name"),body.get("password"),device=_request_device(request,body))
            token=store.open_session(account)
        finally:
            store.close()
    except LoginLockedError as exc:
        log.info("auth.login locked retry_after=%s",exc.retry_after_seconds)
        return JSONResponse(
            status_code=429,content={"detail":exc.code},
            headers={"Retry-After":str(exc.retry_after_seconds)})
    except Exception as exc:
        log.info("auth.login refused reason=%s",getattr(exc,"code",type(exc).__name__))
        raise _workspace_http_error(exc) from exc
    log.info("auth.login account_id=%s",account.account_id)
    try:
        return _signed_in_response(request,{"account":account.public_dict()},token)
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/auth/logout")
async def post_auth_logout(request:Request)->Response:
    """End this device's session; the cookie is cleared."""

    _accounts_only()
    try:
        store=_account_store()
        try:
            store.close_session(_session_token(request))
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    account=_REQUEST_ACCOUNT.get()
    if account is not None:
        await _close_account_voice_connections(account.account_id)
    response=JSONResponse(status_code=200,content={"signed_out":True})
    _clear_session_cookie(response,request)
    return response


@app.post("/api/auth/password")
async def post_auth_password(request:Request,body:dict[str,Any]=Body(...))->dict[str,object]:
    """Change the password after the current one; every other device signs out."""

    _accounts_only()
    account=_REQUEST_ACCOUNT.get()
    if account is None:
        raise HTTPException(status_code=401,detail="authentication_required")
    try:
        store=_account_store()
        try:
            store.change_password(
                account.account_id,body.get("current"),body.get("new"),
                keep_session_token=_session_token(request),device=_request_device(request,body))
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    log.info("auth.password_changed account_id=%s",account.account_id)
    await _close_account_voice_connections(account.account_id)
    return {"changed":True}


@app.get("/api/auth/accounts")
async def get_auth_accounts()->dict[str,object]:
    """The lab's accounts (an administrator only)."""

    _accounts_only()
    account=_REQUEST_ACCOUNT.get()
    try:
        if account is None or not account.admin:
            raise AdminRequiredError("An administrator is required.")
        store=_account_store()
        try:
            listed=store.list_accounts(account.organization_id)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    return {"accounts":[item.public_dict() for item in listed]}


@app.post("/api/auth/accounts/{account_id}/disabled")
async def post_auth_account_disabled(
    account_id:str,body:dict[str,Any]=Body(...),
)->dict[str,object]:
    """Switch one of the lab's accounts off (its sessions end) or on (an administrator only)."""

    _accounts_only()
    account=_REQUEST_ACCOUNT.get()
    try:
        if account is None or not account.admin:
            raise AdminRequiredError("An administrator is required.")
        if not isinstance(body.get("disabled"),bool):
            raise AccountError("disabled must be true or false.")
        if account_id==account.account_id:
            raise HTTPException(status_code=409,detail="admin_self_disable")
        store=_account_store()
        try:
            target=store.account(account_id)
            if target.organization_id!=account.organization_id:
                raise AccountNotFoundError("The account is unknown.")
            changed=store.set_disabled(account_id,body["disabled"])
        finally:
            store.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    log.info("auth.account_disabled account_id=%s disabled=%s by=%s",
             account_id,changed.disabled,account.account_id)
    if changed.disabled:
        await _close_account_voice_connections(account_id)
    return {"account":changed.public_dict()}


# Lane FX5, decision 2 (2026-10-20): an account deletes itself (the store apps
# ask for it inside the app). The password is asked again; the scope is the
# operator tool's (accounts.py, "Erasing one account's data").
_LAST_ADMIN_MESSAGE_KO="이 연구실의 마지막 관리자라서 계정을 지울 수 없어요. 다른 사람을 관리자로 초대한 뒤에 다시 해 주세요."
_ACCOUNT_DELETED_MESSAGE_KO="계정과 그 계정이 남긴 기록을 지웠어요."


def _account_data_locations()->accounts_module.AccountDataLocations:
    """Where the stores an account leaves data in are, read from the server's own settings.

    The deletion record sits beside the workspace folder -- the pilot folder
    in ``scripts/run_pilot.sh``'s layout, where the operator's tool keeps its own.
    """

    settings=_workspace_settings()
    if not settings.enabled or settings.data_dir is None:
        raise IdentityConfigurationError("Accounts need the tenant workspace.")
    reports=ExperimentReportSettings.from_environment()
    protocol=_protocol_store_settings()
    return accounts_module.AccountDataLocations(
        workspace_dir=settings.data_dir,
        reports_db=reports.database_path if reports.enabled else None,
        catalog_db=(
            protocol.data_dir/accounts_module.CATALOG_DATABASE_FILENAME
            if protocol.enabled and protocol.data_dir is not None else None),
        log_path=settings.data_dir.parent/accounts_module.DELETION_LOG_NAME,
    )


@app.post("/api/auth/delete-account")
async def post_auth_delete_account(request:Request,body:dict[str,Any]=Body(...))->Response:
    """Delete the signed-in account and what it left (lane FX5, decision 2).

    Steps, in order: the password is checked as a login is (a wrong one
    counts toward the lock); the lab's last administrator is refused; the
    account is switched off (its sessions end); the data is deleted as the
    operator's ``--account`` deletes it; the account is deleted; one record
    of categories and counts is appended. A store that fails leaves the
    account switched off -- nobody signs in to half an account -- and the
    operator finishes it with the tool. Another lab's and another account's
    data is never touched.
    """

    _accounts_only()
    account=_REQUEST_ACCOUNT.get()
    if account is None:
        raise HTTPException(status_code=401,detail="authentication_required")
    try:
        locations=_account_data_locations()
        # As the tool does: no record to write, nothing is deleted.
        try:
            probe=os.open(locations.log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            os.close(probe)
        except OSError as exc:
            log.warning("auth.account_delete_refused reason=deletion_log_unavailable")
            raise HTTPException(status_code=503,detail="deletion_log_unavailable") from exc
        store=_account_store()
        try:
            store.switch_off_for_deletion(
                account.account_id,body.get("password"),device=_request_device(request,body))
        finally:
            store.close()
    except LastAdminError:
        log.info("auth.account_delete_refused reason=last_admin")
        return JSONResponse(
            status_code=409,content={"detail":"last_admin","message_ko":_LAST_ADMIN_MESSAGE_KO})
    except HTTPException:
        raise
    except Exception as exc:
        log.info("auth.account_delete_refused reason=%s",getattr(exc,"code",type(exc).__name__))
        raise _workspace_http_error(exc) from exc
    await _close_account_voice_connections(account.account_id)
    try:
        results=await asyncio.to_thread(
            accounts_module.erase_account_data,locations,account.account_id)
    except Exception as exc:  # noqa: BLE001 - recorded and answered below, never leaked
        results=[{"category":"account_erasure","entry":"","result":"failed","error":type(exc).__name__}]
    try:
        accounts_module.write_deletion_log(
            locations.log_path,word="password",results=results,scope="account",via="app")
    except OSError as exc:
        log.warning("auth.account_delete_log_failed error=%s",type(exc).__name__)
    if any(item.get("result")!="deleted" for item in results):
        log.warning(
            "auth.account_delete_incomplete failed=%s",
            ",".join(str(item.get("category")) for item in results if item.get("result")!="deleted"))
        raise HTTPException(status_code=500,detail="account_delete_incomplete")
    log.info("auth.account_deleted categories=%d",len(results))
    response=JSONResponse(status_code=200,content={
        "deleted":True,
        "message_ko":_ACCOUNT_DELETED_MESSAGE_KO,
        "removed":{item["category"]:item.get("rows",item.get("files")) for item in results},
    })
    _clear_session_cookie(response,request)
    return response


def _catalog_protocol_titles()->dict[str,str]:
    """protocol_id -> title from the configured fixture and the protocol
    catalog, for naming experiments on the screen. Empty on any failure: a
    title is a convenience, never a reason to refuse a list."""

    titles:dict[str,str]={}
    try:
        candidate=_configured_candidate_fixture(server_config())
        if candidate is not None:
            titles[candidate.protocol_id]=candidate.title
        if _protocol_store_settings().enabled:
            catalog,catalog_store=_open_protocol_catalog()
            try:
                for item in catalog.list_entries():
                    public=item.public_dict()
                    if isinstance(public.get("title"),str):
                        titles.setdefault(item.protocol_id,public["title"])
            finally:
                catalog_store.close()
    except Exception:
        log.info("experiment titles unavailable from the protocol catalog")
    return titles


@app.get("/api/workspace/experiments")
def get_workspace_experiments(active_only:bool=False)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            # Lane ON, decision ①: an administrator sees the lab's experiments,
            # an experimenter their own.
            experiments=list(
                store.list_experiments(
                    principal,active_only=active_only,every_owner=_account_is_admin()))
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    # Lane R6, decision 5: each item names its protocol and its place that
    # day; a title the workspace does not hold is the catalog's.
    if any(item.get("protocol_title") is None for item in experiments):
        titles=_catalog_protocol_titles()
        experiments=[
            item if item.get("protocol_title") is not None
            else {**item,"protocol_title":titles.get(str(item.get("protocol_id")))}
            for item in experiments
        ]
    return {"experiments":_record_list_ratings(experiments)}


def _record_list_ratings(experiments:list[dict[str,object]])->list[dict[str,object]]:
    """Only the latest answer by a visible report's owner; never feedback words."""
    result=[{**item,"rating":None,"rating_state":"unavailable"} for item in experiments]
    settings=ExperimentReportSettings.from_environment()
    pilot=_pilot_store()
    if not settings.enabled or settings.database_path is None or pilot is None:
        return result
    _migrate_report_ownership()
    reports=ExperimentReportStore(settings.database_path)
    for item in result:
        try:
            item["rating_state"]="unrated"
            for report in reports.list_reports(session_id=str(item["session_id"])):
                try:
                    _scope_tenant_resource("experiment_report",report["report_id"])
                except WorkspaceNotFoundError:
                    continue
                answers=pilot._read(
                    "SELECT rating FROM pilot_feedback WHERE report_id=? AND account_id=? ORDER BY id DESC LIMIT 1",
                    (report["report_id"],report["owner_principal_id"]),
                )
                item["rating"]=answers[0]["rating"] if answers else None
            if item["rating"] is not None:
                item["rating_state"]="rated"
        except (OSError,sqlite3.Error,ValueError):
            item["rating"]=None
            item["rating_state"]="unavailable"
    return result


@app.get("/api/workspace/experiments/{session_id}")
def get_workspace_experiment(session_id:str)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return store.get_experiment(
                principal,session_id,every_owner=_account_is_admin())
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/experiments/{session_id}/transition")
async def transition_workspace_experiment(
    session_id:str,request:Request
)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            action=str(payload.get("action", ""))
            expected_version=payload.get("expected_version")
            if action not in {"pause","resume","stop","block"}:
                raise WorkspaceError(
                    "Only explicit non-completion dashboard transitions are allowed."
                )
            if (
                not isinstance(expected_version,int)
                or isinstance(expected_version,bool)
                or expected_version<=0
            ):
                raise WorkspaceError("Experiment version is invalid.")
            store.get_experiment(principal,session_id)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    raise HTTPException(status_code=409,detail="voice_control_required")


@app.get("/api/workspace/experiments/{session_id}/timeline")
def get_workspace_experiment_timeline(session_id:str)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return store.experiment_timeline(
                principal,session_id,every_owner=_account_is_admin())
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/experiments/{session_id}/reports")
def get_workspace_experiment_reports(session_id:str)->dict[str,object]:
    """Discover this experiment's reports through the same OWN read gates."""
    get_workspace_experiment(session_id)
    try:
        settings=ExperimentReportSettings.from_environment()
        reports=[]
        if settings.enabled and settings.database_path is not None:
            _migrate_report_ownership()
            store=ExperimentReportStore(settings.database_path)
            for report in store.list_reports(session_id=session_id):
                _scope_tenant_resource("experiment_report",report["report_id"])
                reports.append(report)
        return {"session_id":session_id,"reports":reports}
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post(
    "/api/workspace/experiments/{session_id}/checkpoint-restart",
    status_code=201,
)
async def restart_workspace_experiment_from_checkpoint(
    session_id:str,request:Request
)->dict[str,object]:
    """Start a new experiment where a stopped one stood at a chosen checkpoint.

    The checkpoints are the ones the timeline lists for a stopped experiment.
    The new session waits paused; the voice session continues it through the
    ordinary recovery path, which checks the carried steps against the exact
    protocol revision before anything is restored.
    """

    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            checkpoint_step_id=payload.get("checkpoint_step_id")
            expected_version=payload.get("expected_version")
            if not isinstance(checkpoint_step_id,str):
                raise WorkspaceError("Checkpoint step identifier is invalid.")
            if (
                not isinstance(expected_version,int)
                or isinstance(expected_version,bool)
                or expected_version<=0
            ):
                raise WorkspaceError("Experiment version is invalid.")
            return store.restart_experiment_from_checkpoint(
                principal,session_id,
                checkpoint_step_id=checkpoint_step_id,
                expected_version=expected_version,
            )
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post(
    "/api/workspace/experiments/{session_id}/observations",
    status_code=201,
)
async def create_workspace_experiment_observation(
    session_id:str,request:Request
)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            return store.record_observation(
                principal,
                session_id,
                event_key=str(payload.get("idempotency_key", "")),
                content=str(payload.get("content", "")),
                category=str(payload.get("category", "note")),
                capture_source="manual",
                protocol_step_id=(
                    str(payload["protocol_step_id"])
                    if payload.get("protocol_step_id") is not None else None
                ),
            )
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post(
    "/api/workspace/experiments/{session_id}/evidence",
    status_code=201,
)
async def upload_workspace_experiment_evidence(
    session_id:str,
    request:Request,
    filename:str,
    idempotency_key:str,
    caption:str|None=None,
)->dict[str,object]:
    allowed={
        "image/jpeg":("image",".jpg"),
        "image/png":("image",".png"),
        "image/webp":("image",".webp"),
        "application/pdf":("document",".pdf"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document":(
            "document",".docx"),
    }
    media_type=(
        request.headers.get("content-type","")
        .split(";",1)[0].strip().casefold()
    )
    if media_type not in allowed:
        raise HTTPException(
            status_code=415,detail="evidence_media_type_unsupported"
        )
    declared=request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size=int(declared)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,detail="invalid_content_length"
            ) from exc
        if declared_size<1 or declared_size>32*1024*1024:
            raise HTTPException(status_code=413,detail="evidence_too_large")
    temporary:Path|None=None
    try:
        principal,store=_commercial_workspace()
        try:
            experiment=store.get_experiment(principal,session_id)
            safe_session_id=str(experiment["session_id"])
            settings=_workspace_settings()
            assert settings.data_dir is not None
            tenant_bucket=hashlib.sha256(
                principal.organization_id.encode("utf-8")
            ).hexdigest()[:24]
            directory=(
                settings.data_dir/"evidence"/tenant_bucket/safe_session_id
            )
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            descriptor,raw_path=tempfile.mkstemp(
                prefix=".evidence-upload-",dir=directory
            )
            temporary=Path(raw_path)
            digest=hashlib.sha256()
            byte_size=0
            try:
                with os.fdopen(descriptor,"wb") as stream:
                    async for chunk in request.stream():
                        if not chunk:
                            continue
                        byte_size+=len(chunk)
                        if byte_size>32*1024*1024:
                            raise HTTPException(
                                status_code=413,detail="evidence_too_large"
                            )
                        digest.update(chunk)
                        stream.write(chunk)
            except Exception:
                temporary.unlink(missing_ok=True)
                temporary=None
                raise
            if byte_size==0:
                temporary.unlink(missing_ok=True)
                temporary=None
                raise HTTPException(status_code=422,detail="evidence_empty")
            checksum=digest.hexdigest()
            evidence_kind,suffix=allowed[media_type]
            target=directory/f"{checksum}{suffix}"
            try:
                os.link(temporary,target)
            except FileExistsError:
                existing_descriptor=os.open(
                    target,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)
                )
                target_stat=os.fstat(existing_descriptor)
                if (
                    not stat.S_ISREG(target_stat.st_mode)
                    or target_stat.st_size!=byte_size
                ):
                    os.close(existing_descriptor)
                    raise WorkspaceError(
                        "Existing evidence storage object failed integrity checks."
                    )
                existing_digest=hashlib.sha256()
                with os.fdopen(existing_descriptor,"rb") as existing_stream:
                    for existing_chunk in iter(
                        lambda:existing_stream.read(1024*1024),b""
                    ):
                        existing_digest.update(existing_chunk)
                if not hmac.compare_digest(
                    existing_digest.hexdigest(),checksum
                ):
                    raise WorkspaceError(
                        "Existing evidence storage object failed integrity checks."
                    )
            finally:
                temporary.unlink(missing_ok=True)
                temporary=None
            relative=target.relative_to(settings.data_dir).as_posix()
            evidence=store.record_evidence(
                principal,
                safe_session_id,
                event_key=idempotency_key,
                evidence_kind=evidence_kind,
                original_filename=filename,
                media_type=media_type,
                byte_size=byte_size,
                sha256=checksum,
                storage_reference=relative,
                caption=caption or None,
            )
            if evidence["evidence_kind"]=="image":
                _record_report_photo(safe_session_id,evidence)
            return {
                key:evidence[key]
                for key in (
                    "evidence_id","session_id","protocol_step_id",
                    "protocol_step_label","evidence_kind","original_filename",
                    "media_type","byte_size","sha256","interpretation_status",
                    "created_at",
                )
            }
        finally:
            store.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise _workspace_http_error(exc) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _record_report_photo(session_id:str,evidence:dict)->None:
    """Put an uploaded image on the experiment report (lane RP, decision 7).

    The report's results table reads ``photo_attached`` events; the caption is
    what the researcher wrote, else the file name. The image itself is never
    read or interpreted. Best effort: the upload stands even when no report is
    open for the session or the report store is off.
    """

    try:
        settings=ExperimentReportSettings.from_environment()
        if not settings.enabled or settings.database_path is None:
            return
        store=ExperimentReportStore(settings.database_path)
        reports=store.list_reports(session_id=session_id)
        if not reports:
            return
        caption=" ".join(str(evidence.get("caption") or "").split())
        store.append_event(
            reports[0]["report_id"],
            event_key=f"photo-{evidence['evidence_id']}",
            event_type="photo_attached",
            step_id=evidence.get("protocol_step_id"),
            step_label=evidence.get("protocol_step_label"),
            user_wording=(caption or str(evidence["original_filename"]))[:800],
            confirmation_state="user_reported",
            payload={
                "evidence_id":evidence["evidence_id"],
                "media_type":evidence["media_type"],
                "interpretation_status":"not_interpreted",
            },
        )
    except Exception as exc:  # noqa: BLE001 - the upload itself already stands
        log.warning("report photo event not written error=%s",type(exc).__name__)


@app.get("/api/workspace/experiments/{session_id}/evidence/{evidence_id}")
def download_workspace_experiment_evidence(
    session_id:str,evidence_id:str
)->Response:
    try:
        principal,store=_commercial_workspace()
        try:
            evidence=store.evidence_for_download(
                principal,session_id,evidence_id,every_owner=_account_is_admin())
            settings=_workspace_settings()
            if settings.data_dir is None:
                raise WorkspaceNotFoundError(
                    "Experiment evidence storage is not available."
                )
            root=settings.data_dir.resolve()
            relative=Path(str(evidence["storage_reference"]))
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise WorkspaceError(
                    "Experiment evidence storage path is invalid."
                )
            path=root/relative
            try:
                resolved=path.resolve(strict=True)
            except FileNotFoundError as exc:
                raise WorkspaceNotFoundError(
                    "Experiment evidence file is not available."
                ) from exc
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise WorkspaceError(
                    "Experiment evidence storage path is invalid."
                ) from exc
            if resolved!=path:
                raise WorkspaceError(
                    "Experiment evidence storage path cannot contain links."
                )
            descriptor=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
            try:
                metadata=os.fstat(descriptor)
                expected_size=int(evidence["byte_size"])
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size!=expected_size
                    or expected_size<1
                    or expected_size>32*1024*1024
                ):
                    raise WorkspaceError(
                        "Experiment evidence failed its size check."
                    )
                digest=hashlib.sha256()
                chunks=[]
                with os.fdopen(descriptor,"rb") as stream:
                    descriptor=-1
                    for chunk in iter(lambda:stream.read(1024*1024),b""):
                        digest.update(chunk)
                        chunks.append(chunk)
                if not hmac.compare_digest(
                    digest.hexdigest(),str(evidence["sha256"])
                ):
                    raise WorkspaceError(
                        "Experiment evidence failed its checksum."
                    )
            finally:
                if descriptor>=0:
                    os.close(descriptor)
            suffix={
                "image/jpeg":".jpg","image/png":".png","image/webp":".webp",
                "application/pdf":".pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document":".docx",
            }.get(str(evidence["media_type"]),"")
            return Response(
                content=b"".join(chunks),
                media_type=str(evidence["media_type"]),
                headers={
                    "Content-Disposition":(
                        f'attachment; filename="evidence-{evidence_id}{suffix}"'
                    ),
                    "Cache-Control":"private, no-store",
                    "X-Evidence-SHA256":str(evidence["sha256"]),
                    "X-Evidence-Interpretation":"not_interpreted",
                },
            )
        finally:
            store.close()
    except FileNotFoundError as exc:
        raise _workspace_http_error(
            WorkspaceNotFoundError("Experiment evidence file is not available.")
        ) from exc
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/knowledge")
def get_workspace_knowledge()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {"entries":list(store.knowledge_entries(principal))}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/knowledge",status_code=201)
async def create_workspace_knowledge(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            knowledge_id=store.add_knowledge(
                principal,
                kind=str(payload.get("kind", "")),
                body=str(payload.get("body", "")),
                provenance=(payload.get("provenance")
                            if isinstance(payload.get("provenance"),dict) else {}),
                revision_id=(str(payload["revision_id"])
                             if payload.get("revision_id") else None),
            )
            return {"knowledge_id":knowledge_id,"effective_kind":str(payload.get("kind",""))}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/assets")
def get_workspace_assets()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {"assets":list(store.asset_cards(principal))}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/assets/{asset_id}/diff")
def get_workspace_asset_diff(asset_id:str)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return store.asset_card_diff(principal,asset_id)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


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
        stream=destination.open("xb")
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                byte_size+=len(chunk)
                if byte_size>limit:
                    raise HTTPException(
                        status_code=413,detail="protocol_pdf_too_large")
                await _worker_until_complete(lambda:stream.write(chunk))
        finally:
            # Closing also flushes buffered bytes. It must wait for the
            # current write and must not hold the event loop on disk I/O.
            await _worker_until_complete(stream.close)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    if byte_size==0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422,detail="invalid_pdf")
    return byte_size


PUBLIC_SOURCE_ONLY_ENV="VOINEY_LAB_PUBLIC_SOURCE_ONLY"


def _source_disclosure_error(code:str)->HTTPException:
    return HTTPException(status_code=422,detail={"code":code})


def _decoded_source_note(value:str)->str:
    """Decode the browser's UTF-8 URI-component header without guessing."""

    try:
        value.encode("ascii")
        if re.search(r"%(?![0-9A-Fa-f]{2})",value):
            raise ValueError
        return unquote_to_bytes(value).decode("utf-8","strict")
    except (UnicodeDecodeError,UnicodeEncodeError,ValueError) as exc:
        raise _source_disclosure_error("source_disclosure_invalid") from exc


def _source_disclosure_from_values(
    status_value:object,
    note_value:object,
)->ProtocolSourceDisclosure|None:
    missing=status_value is None or (
        isinstance(status_value,str) and not status_value.strip())
    if missing:
        if note_value is not None and note_value != "":
            raise _source_disclosure_error("source_disclosure_invalid")
        return None
    if not isinstance(status_value,str) or not isinstance(note_value,(str,type(None))):
        raise _source_disclosure_error("source_disclosure_invalid")
    try:
        return ProtocolSourceDisclosure(
            status=ProtocolSourceStatus(status_value.strip().casefold()),
            note=note_value or "",
        )
    except (ProtocolRegistrationError,ValueError) as exc:
        raise _source_disclosure_error("source_disclosure_invalid") from exc


def _source_disclosure_from_headers(request:Request)->ProtocolSourceDisclosure|None:
    encoded_note=request.headers.get(_SOURCE_NOTE_HEADER)
    note=_decoded_source_note(encoded_note) if encoded_note is not None else None
    return _source_disclosure_from_values(
        request.headers.get(_SOURCE_STATUS_HEADER),note)


def _enforce_public_source_policy(
    disclosure:ProtocolSourceDisclosure|None,
)->None:
    try:
        enabled=boolean_setting(os.environ,PUBLIC_SOURCE_ONLY_ENV,default=False)
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code":"configuration_invalid","setting":PUBLIC_SOURCE_ONLY_ENV},
        ) from exc
    if enabled and (
        disclosure is None or disclosure.status is not ProtocolSourceStatus.YES
    ):
        raise _source_disclosure_error("public_source_required")


@app.get("/api/protocols")
def list_protocol_catalog()->dict[str,object]:
    """Read catalog metadata only; never trigger automated analysis."""

    try:
        entries,_,candidate_protocol_id=_public_protocol_catalog_entries()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc
    return {"protocols":entries}


@app.get("/api/protocols/{protocol_id}")
def get_protocol_catalog_entry(protocol_id:str)->dict[str,object]:
    try:
        _scope_catalog_resource(protocol_id)
        config=server_config()
        candidate=_configured_candidate_fixture(config)
        if candidate is not None and candidate.protocol_id==protocol_id:
            return _candidate_catalog_dict(candidate)
        catalog,store=_open_protocol_catalog()
        try:
            public=_catalog_entry_projection(
                catalog,catalog.get_entry(protocol_id)
            )
            public["analysis_run"]=catalog.analysis_run_status(
                protocol_id).public_dict()
            return public
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols",status_code=201)
async def register_protocol_pdf(
    request:Request,filename:str,first_page:int|None=None,last_page:int|None=None,
)->dict[str,object]:
    """Register one protocol source: a PDF, a Word document or a photo.

    Lane AN, FM (human decision 2026-10-13): a .docx (through LibreOffice)
    and a JPG, PNG or HEIC photo are turned into the PDF that extraction, OCR
    and the analysis read -- one evidence path -- and registered as it; the
    upload's own checksum is kept with the registration.

    Lane EASY, decision 1: ``first_page``/``last_page`` are the pages to
    analyse, checked against the document; the analysis the upload-time OCR
    starts reads those pages alone (the screen sends them to the analysis
    request of a text PDF itself).
    """

    disclosure=_source_disclosure_from_headers(request)
    _enforce_public_source_policy(disclosure)
    return await _register_protocol_pdf(
        request,filename=filename,first_page=first_page,last_page=last_page,
        source_disclosure=disclosure)


async def _register_protocol_pdf(
    request:Request,
    *,
    filename:str,
    first_page:int|None=None,
    last_page:int|None=None,
    source_disclosure:ProtocolSourceDisclosure|None,
)->dict[str,object]:
    page_range=_requested_page_range(first_page,last_page)
    media_type=source_media_type(request.headers.get("content-type",""))
    if media_type not in SOURCE_MEDIA_TYPES:
        raise HTTPException(status_code=415,detail="unsupported_pdf_media_type")
    # A browser Methods paste has an explicit .txt name. Do not reinterpret a
    # non-PDF body renamed as .pdf as pasted Methods merely because its
    # Content-Type is text/plain; that established upload remains unsupported.
    if media_type==TEXT_MEDIA_TYPE and Path(filename).suffix.casefold()!=".txt":
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
        original=None
        if media_type==PDF_MEDIA_TYPE:
            await _spool_protocol_pdf_upload(request,temporary)
        else:
            original=Path(root)/"upload.source"
            await _spool_protocol_pdf_upload(request,original)
        try:
            def register(catalog:ProtocolCatalog)->tuple[Any,dict[str,object]]:
                converted=None
                principal=_REQUEST_PRINCIPAL.get()
                registration_scope={}
                visible_protocols=_visible_catalog_resource_ids()
                if principal is not None and visible_protocols is not None:
                    registration_scope={"organization_id":principal.organization_id,
                                        "visible_protocol_ids":visible_protocols}
                # A Word document or photo uploaded before is found by its own
                # checksum and not converted again.
                result=(
                    (catalog.find_converted_upload(file_sha256(original),visible_protocol_ids=visible_protocols)
                     if visible_protocols is not None else catalog.find_converted_upload(file_sha256(original)))
                    if original is not None else None
                )
                if result is None:
                    if original is not None:
                        converted=replace(
                            convert_source_to_pdf(original,media_type,temporary),
                            filename=filename,
                        )
                    result=catalog.register(
                        temporary,
                        source_filename=(
                            filename if converted is None else pdf_filename(filename)),
                        media_type=PDF_MEDIA_TYPE,
                        converted_from=converted,
                        **registration_scope,
                    )
                if source_disclosure is not None:
                    catalog.record_source_disclosure(
                        result.entry.protocol_id,source_disclosure)
                    result=replace(
                        result,
                        entry=catalog.get_entry(result.entry.protocol_id),
                    )
                _scope_catalog_resource(result.entry.protocol_id,bind=True)
                catalog.check_page_range(result.entry.protocol_id,page_range)
                _record_workspace_metric(
                    category="protocol",metric_name="upload",
                    dimensions={
                        "source_kind":(
                            "local_pdf" if converted is None
                            else "local_docx" if converted.converter.startswith("libreoffice")
                            else "pasted_methods" if converted.media_type=="text/plain"
                            else "local_photo"),
                        "status":"stored",
                    },
                )
                return result,_upload_ocr_status(catalog,result.entry)
            result,status=await _catalog_in_worker(register)
            ocr=_automatic_ocr_on_upload(None,result.entry,status=status,page_range=page_range)
            return _registration_response(result,ocr)
        except HTTPException:
            raise
        except Exception as exc:
            raise _catalog_http_error(exc) from exc


def _registration_response(result:Any,ocr:dict[str,object])->dict[str,object]:
    """The upload's answer; a file already registered with a review version
    suggests starting from it instead (lane RV, decision d)."""

    response:dict[str,object]={
        "protocol":result.entry.public_dict(),
        "deduplicated":result.deduplicated,
        "ocr":ocr,
    }
    suggestion=review_version_suggestion(result.entry) if result.deduplicated else None
    if suggestion is not None:
        response["review_suggestion"]=suggestion
    return response


@app.post("/api/protocols/{protocol_id}/reagent-tables",status_code=201)
async def attach_protocol_reagent_table(
    request:Request,protocol_id:str,filename:str,
)->dict[str,object]:
    """Attach an .xlsx workbook to a protocol as its reagent table (lane AN, FM).

    Kept cell by cell where the workbook prints it; never analysed and never
    a source of a step. Returns the table's summary.
    """

    media_type=request.headers.get("content-type","").casefold().split(";",1)[0].strip()
    if media_type!=REAGENT_TABLE_MEDIA_TYPE:
        raise HTTPException(status_code=415,detail="unsupported_reagent_table_media_type")
    with tempfile.TemporaryDirectory(prefix="protocol-reagent-table-") as root:
        temporary=Path(root)/"table.xlsx"
        try:
            await _spool_protocol_pdf_upload(
                request,temporary,max_bytes=MAX_REAGENT_TABLE_BYTES)
        except HTTPException as exc:
            # The spooler names a PDF; this upload is a workbook.
            raise HTTPException(
                status_code=exc.status_code,
                detail={413:"reagent_table_too_large",422:"invalid_reagent_table"}.get(
                    exc.status_code,exc.detail),
            ) from exc
        def attach()->tuple[dict[str,object],bool]:
            # The catalog's connection belongs to the thread that opens it.
            catalog,store=_open_protocol_catalog()
            try:
                return catalog.attach_reagent_table(
                    protocol_id,temporary,filename=filename)
            finally:
                store.close()

        try:
            _scope_catalog_resource(protocol_id)
            table,deduplicated=await asyncio.to_thread(attach)
        except HTTPException:
            raise
        except Exception as exc:
            raise _catalog_http_error(exc) from exc
    return {"reagent_table":reagent_table_summary(table),"deduplicated":deduplicated}


@app.post("/api/protocols/import-link",status_code=201)
async def import_protocol_link(body:dict[str,Any]=Body(...))->dict[str,object]:
    """Bring a protocol in from a Google Drive, Docs or Sheets link (lane DR, decision 1).

    For a signed-in person only. The file the link names is downloaded
    (``protocol_link_import``: allowed Google hosts, no cookie, size and time
    limits, format read from the bytes) and handed to the upload route itself;
    a Sheets link is attached as a reagent table to ``protocol_id``. The
    address is kept as the source in the protocol's ledger.
    """

    try:
        if _request_account() is None and _REQUEST_PRINCIPAL.get() is None:
            raise LinkImportError("link_sign_in_required")
        link=parse_google_link(body.get("url"))
        protocol_id=body.get("protocol_id")
        reagent_table=protocol_id is not None
        if reagent_table and (not isinstance(protocol_id,str) or link.kind=="document"):
            raise LinkImportError("link_not_a_reagent_table")
        if link.kind=="spreadsheet" and not reagent_table:
            raise LinkImportError("link_sheet_needs_protocol")
        source_disclosure=None
        if not reagent_table:
            source_disclosure=_source_disclosure_from_values(
                body.get("public_source_status"),body.get("source_note"))
            _enforce_public_source_policy(source_disclosure)
        if reagent_table:
            # The protocol is there before anything is downloaded for it.
            try:
                _scope_catalog_resource(protocol_id)
                await _catalog_in_worker(lambda catalog:catalog.get_entry(protocol_id))
            except HTTPException:
                raise
            except Exception as exc:
                raise _catalog_http_error(exc) from exc
        with tempfile.TemporaryDirectory(prefix="protocol-link-") as root:
            fetched=await asyncio.to_thread(
                fetch_google_link,link,Path(root),reagent_table=reagent_table)
            if reagent_table:
                response=await attach_protocol_reagent_table(
                    upload_request(fetched,f"/api/protocols/{protocol_id}/reagent-tables"),
                    protocol_id,filename=fetched.filename)
                target=protocol_id
                table_id=str(response["reagent_table"]["table_id"])
            else:
                # Lane EASY, decision 1: the pages to analyse, as for an upload.
                response=await _register_protocol_pdf(
                    upload_request(fetched,"/api/protocols"),filename=fetched.filename,
                    first_page=body.get("first_page"),last_page=body.get("last_page"),
                    source_disclosure=source_disclosure)
                target=str(response["protocol"]["protocol_id"])
                table_id=None
        try:
            response["source_link"]=await _catalog_in_worker(
                lambda catalog:record_source_link(
                    catalog.store,target,fetched,
                    attached_as="reagent_table" if reagent_table else "protocol_source",
                    table_id=table_id))
        except Exception as exc:
            raise _catalog_http_error(exc) from exc
        return response
    except LinkImportError as exc:
        raise HTTPException(status_code=exc.status_code,detail=exc.detail()) from exc


@app.get("/api/protocols/{protocol_id}/reagent-tables")
def list_protocol_reagent_tables(protocol_id:str)->dict[str,object]:
    """The reagent tables attached to a protocol, every cell where it prints."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return {"reagent_tables":catalog.reagent_tables(protocol_id)}
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


def _upload_ocr_status(catalog:ProtocolCatalog,entry:ProtocolCatalogEntry)->dict[str,object]:
    """A status failure must not turn an already stored upload into a failure."""
    try:
        return catalog.ocr_status(entry.protocol_id,include_text=False)
    except Exception as exc:  # noqa: BLE001 - the upload itself succeeded
        log.warning("protocol.ocr.status_failed protocol_id=%s error=%s",
                    entry.protocol_id,type(exc).__name__)
        return {"state":"unknown","automatic":False,"blocked":"protocol_ocr_status_failed"}


def _automatic_ocr_on_upload(
    catalog:ProtocolCatalog|None,entry:ProtocolCatalogEntry,
    *,status:dict[str,object]|None=None,page_range:tuple[int,int]|None=None,
)->dict[str,object]:
    """Start OCR for the pages without a usable text layer, if any (lane PX 3).

    Decided 2026-10-06: a page whose text layer is missing or unreadable
    (``experiment_protocol_pdf.page_ocr_reason``: no visible character, a
    glyph with no Unicode mapping, or 5% private-use/unassigned characters)
    is read by the configured OCR engines at upload, and the structured
    analysis starts as soon as the OCR text is in. Nothing else changes:
    the page text layer of every other page is kept, the OCR pages stay
    marked as OCR, and the one confirmation a person gives before execution
    covers the OCR text. Returns what the page shows: whether OCR was
    needed, whether it started, and why not when it did not.
    """

    if status is None:
        assert catalog is not None
        status=_upload_ocr_status(catalog,entry)
    if status.get("blocked")=="protocol_ocr_status_failed":
        return status
    state=str(status.get("state"))
    if state=="not_required":
        return {"state":state,"automatic":False}
    if state in {"accepted_for_analysis","in_progress","review_required"}:
        return {"state":state,"automatic":False,
                "ocr_page_numbers":_ocr_page_numbers(status)}
    running=_PROTOCOL_OCR_TASKS.get(entry.protocol_id)
    if running is not None and not running.done():
        return {"state":"queued","automatic":True}
    try:
        provider=_protocol_ocr_provider()
    except ProtocolOcrUnavailableError:
        return {"state":state,"automatic":False,"blocked":"protocol_ocr_not_configured"}
    if entry.analysis_status not in {
        "ocr_required","ocr_failed","ocr_rejected","structured_analysis_ready",
        "chunked_analysis_required",
    }:
        # An analysis already ran or is running: OCR now would not feed it.
        return {"state":state,"automatic":False,"blocked":"analysis_already_ran"}
    ocr_id=f"ocr-{secrets.token_hex(16)}"
    _schedule_ocr_then_analysis(
        entry.protocol_id,provider,ocr_id=ocr_id,principal=_REQUEST_PRINCIPAL.get(),
        page_range=page_range)
    return {"state":"queued","automatic":True,"ocr_id":ocr_id}


def _ocr_page_numbers(status:dict[str,object])->list[int]:
    pages=status.get("pages")
    return [
        int(page["source_page_number"]) for page in pages
        if isinstance(page,dict) and page.get("provider")!=OCR_TEXT_LAYER_PROVIDER
        and isinstance(page.get("source_page_number"),int)
    ] if isinstance(pages,list) else []


def _schedule_ocr_then_analysis(
    protocol_id:str,provider:ProtocolOcrProvider,*,ocr_id:str,
    principal:Principal|None,page_range:tuple[int,int]|None=None,
)->asyncio.Task[None]:
    """OCR in a worker, accepted automatically, then the analysis (lane PX 3)."""

    def run_ocr()->dict[str,object]:
        catalog,store=_open_protocol_catalog()
        try:
            return catalog.run_ocr(
                protocol_id,provider,ocr_id=ocr_id,accepted_automatically=True)
        finally:
            store.close()

    async def background_worker()->None:
        try:
            status=await asyncio.to_thread(run_ocr)
        except Exception as exc:
            log.warning(
                "protocol.ocr.failed protocol_id=%s error=%s",
                protocol_id,type(exc).__name__,
            )
            return
        if status.get("accepted_for_analysis") is not True:
            return
        try:
            await _begin_background_analysis(
                protocol_id,principal=principal,
                **({"page_range":page_range} if page_range is not None else {}))
        except Exception as exc:  # noqa: BLE001 - recorded by the catalog
            log.warning(
                "protocol.analysis.after_ocr_failed protocol_id=%s error=%s",
                protocol_id,type(exc).__name__,
            )

    task=asyncio.create_task(background_worker())
    _PROTOCOL_OCR_TASKS[protocol_id]=task
    task.add_done_callback(
        lambda completed,pid=protocol_id: (
            _PROTOCOL_OCR_TASKS.pop(pid,None)
            if _PROTOCOL_OCR_TASKS.get(pid) is completed else None
        )
    )
    return task


def _protocol_analysis_model()->OpenAICompatibleProtocolAnalysisModel:
    reasoning_effort=os.environ.get(
        "VOINEY_LAB_ANALYSIS_REASONING","high"
    ).strip().casefold()
    if reasoning_effort not in {"low","medium","high","xhigh"}:
        raise ServerConfigurationError(
            "VOINEY_LAB_ANALYSIS_REASONING is invalid.",
            "VOINEY_LAB_ANALYSIS_REASONING",
        )
    # The call runs in the background analysis task, never on a request
    # path, and measured calls took 236-394 s (lane P1), so the limit is a
    # setting with room above that (human decision 2026-10-05).
    raw_timeout=os.environ.get(
        "VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS","600"
    ).strip()
    try:
        timeout_seconds=float(raw_timeout)
    except ValueError:
        timeout_seconds=math.nan
    if not 30.0<=timeout_seconds<=3600.0:
        raise ServerConfigurationError(
            "VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS must be 30-3600 seconds.",
            "VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS",
        )
    role=RoleModel.from_environment("analysis")
    client=_role_client(role,asynchronous=False,timeout=timeout_seconds)
    return OpenAICompatibleProtocolAnalysisModel(
        client,require_env("VOINEY_LAB_ANALYSIS_MODEL"),reasoning_effort)


def _development_fixture_allowed() -> bool:
    """Whether the configured development fixture may run in this usage scope.

    The curated in-gel fixture is development material. It runs under the
    demo, reference_only and test_only scopes and never under operational;
    an unset scope is a no. This is the one place the usage scope still
    bears on execution (decision of 2026-10-08).
    """

    scope = (
        os.environ.get("VOINEY_LAB_USAGE_SCOPE", "")
        or os.environ.get("VOINEY_LAB_SAFETY_USAGE_SCOPE", "")
    ).strip().casefold()
    return scope in {"demo", "reference_only", "test_only"}


@app.post("/api/protocols/{protocol_id}/ocr",status_code=202)
async def trigger_protocol_ocr(protocol_id:str)->dict[str,object]:
    """Run one trusted OCR adapter outside the voice path and await review."""

    reservation:asyncio.Future[None]|None=None
    try:
        _scope_catalog_resource(protocol_id)
        provider=_protocol_ocr_provider()
        running=_PROTOCOL_OCR_TASKS.get(protocol_id)
        if running is not None and not running.done():
            current=await _catalog_in_worker(
                lambda catalog:catalog.ocr_status(protocol_id,include_text=False))
            current["request_deduplicated"]=True
            if current.get("state")=="ocr_required":
                current["state"]="queued"
            return current
        # Reserve before yielding to source/status work, just as analysis
        # does. Crossing requests must not schedule two OCR providers.
        reservation=asyncio.get_running_loop().create_future()
        _PROTOCOL_OCR_TASKS[protocol_id]=reservation
        current=await _catalog_in_worker(
            lambda catalog:catalog.ocr_status(protocol_id,include_text=False))
        if current.get("state") in {
            "in_progress","review_required","accepted_for_analysis",
        }:
            current["request_deduplicated"]=True
            return current
        if current.get("state")=="not_required":
            raise ProtocolCatalogError("Protocol PDF does not require OCR.")
        ocr_id=f"ocr-{secrets.token_hex(16)}"
        # Lane PX decision 3: a person's press runs the same chain an upload
        # runs -- OCR of the marked pages, accepted under a recorded
        # automatic authority, then the analysis. No separate approval step.
        _schedule_ocr_then_analysis(
            protocol_id,provider,ocr_id=ocr_id,principal=_REQUEST_PRINCIPAL.get())
        return {
            **current,
            "ocr_id":ocr_id,
            "state":"queued",
            "request_accepted":True,
            "review_required":False,
            "automatic":True,
            "executable":False,
        }
    except Exception as exc:
        raise _catalog_http_error(exc) from exc
    finally:
        if reservation is not None and _PROTOCOL_OCR_TASKS.get(protocol_id) is reservation:
            _PROTOCOL_OCR_TASKS.pop(protocol_id,None)
            reservation.cancel()


@app.get("/api/protocols/{protocol_id}/ocr")
def get_protocol_ocr_status(protocol_id:str)->dict[str,object]:
    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            status=catalog.ocr_status(protocol_id)
        finally:
            store.close()
        running=_PROTOCOL_OCR_TASKS.get(protocol_id)
        if running is not None and not running.done() and status.get("state")=="ocr_required":
            status["state"]="queued"
        return status
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/analysis",status_code=202)
async def trigger_protocol_analysis(
    protocol_id:str,background:bool=True,
    first_page:int|None=None,last_page:int|None=None,
)->dict[str,object]:
    """Persist a request, then run provider analysis outside the request path.

    ``first_page``/``last_page`` (lane EASY, decision 1): only those pages
    are analysed, and the request event keeps them."""

    page_range=_requested_page_range(first_page,last_page)
    _scope_catalog_resource(protocol_id)
    analysis_id=f"analysis-{secrets.token_hex(16)}"
    metric_principal=_REQUEST_PRINCIPAL.get()

    def run_explicit_analysis(*,request_first:bool)->dict[str,object]:
        # SQLite connections are thread-affine.  Construct and close the
        # catalog in the same worker that performs bounded Provider work.
        catalog,store=_open_protocol_catalog()
        try:
            if request_first:
                catalog.request_analysis(protocol_id,analysis_id,page_range=page_range)
            try:
                model=_protocol_analysis_model()
            except (RuntimeError,ValueError) as exc:
                catalog.fail_analysis_request(
                    protocol_id,analysis_id,
                    failure_code="provider_configuration_missing",
                )
                raise ProtocolAnalysisUnavailableError(
                    "Protocol analysis provider is not configured."
                ) from exc
            entry=catalog.analyze(
                protocol_id,
                model,
                analysis_id=analysis_id,
            )
            public=entry.public_dict()
            public["analysis_run"]=catalog.analysis_run_status(
                protocol_id).public_dict()
            return public
        finally:
            store.close()

    try:
        if not background:
            started=time.monotonic()
            try:
                completed=await asyncio.to_thread(
                    run_explicit_analysis,request_first=True)
            except Exception:
                _pilot_protocol_event(
                    protocol_id,"analysis",principal=metric_principal,failed=True,
                    started=started)
                raise
            _record_workspace_metric(
                category="protocol",metric_name="analysis",
                dimensions={
                    "status":str(completed.get("analysis_status") or "complete")[:100],
                    "source_kind":"local_pdf",
                },
                principal=metric_principal,
            )
            _pilot_protocol_event(
                protocol_id,"analysis",principal=metric_principal,public=completed,
                started=started)
            return completed
        deferred=await _analysis_deferred_for_ocr(protocol_id)
        if deferred is not None:
            return deferred
        return await _begin_background_analysis(
            protocol_id,principal=metric_principal,analysis_id=analysis_id,
            page_range=page_range)
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/reanalysis",status_code=202)
async def trigger_protocol_reanalysis(
    protocol_id:str,first_page:int|None=None,last_page:int|None=None,
)->dict[str,object]:
    """Analyse a protocol again over the analysis it has (lane RN, decision 2).

    The person's one confirmation is on the screen. Here the request is one
    more ledger event that names the analysis it is asked over, and a run off
    the request path; a pass is one more analysis revision, a failure leaves
    the old one as it is. The analysis route above keeps lane PA's rule and
    never calls a passed analysis again, so an upload cannot reach this.
    ``first_page``/``last_page`` (lane EASY, decision 1): the new analysis
    reads those pages alone; none is the whole document.
    """

    page_range=_requested_page_range(first_page,last_page)
    _scope_catalog_resource(protocol_id)
    try:
        deferred=await _analysis_deferred_for_ocr(protocol_id)
        if deferred is not None:
            return deferred
        return await _begin_background_analysis(
            protocol_id,principal=_REQUEST_PRINCIPAL.get(),reanalysis=True,
            page_range=page_range)
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


def _requested_page_range(first_page:object,last_page:object)->tuple[int,int]|None:
    """The pages to analyse as a request names them (lane EASY, decision 1).

    Both ends or neither; whether they are pages of the document is the
    catalog's to check against the source."""

    if first_page is None and last_page is None:
        return None
    if (not isinstance(first_page,int) or not isinstance(last_page,int)
            or isinstance(first_page,bool) or isinstance(last_page,bool)
            or not 1<=first_page<=last_page):
        raise HTTPException(status_code=422,detail="analysis_page_range_invalid")
    return first_page,last_page


async def _analysis_deferred_for_ocr(protocol_id:str)->dict[str,object]|None:
    """The answer while the upload-time OCR chain holds the protocol, else None.

    Lane PX decision 3: the OCR chain starts the analysis itself once the OCR
    text is in; an analysis now would read the pages without it.
    """

    ocr_running=_PROTOCOL_OCR_TASKS.get(protocol_id)
    if ocr_running is None or ocr_running.done():
        return None
    def project(catalog:ProtocolCatalog)->dict[str,object]:
        public=catalog.get_entry(protocol_id).public_dict()
        public["analysis_run"]=_analysis_run_public(catalog,protocol_id)
        public["analysis_request_deferred"]="ocr_in_progress"
        return public
    return await _catalog_in_worker(project)


async def _begin_background_analysis(
    protocol_id:str,*,principal:Principal|None,analysis_id:str|None=None,
    reanalysis:bool=False,page_range:tuple[int,int]|None=None,
)->dict[str,object]:
    """Record the request, then run the analysis off the request path.

    Shared by the analysis endpoint, the re-analysis endpoint and the
    upload-time OCR chain (lane PX, decision 3). A run already in progress,
    or an analysis that already ended, is reported and not started again --
    unless ``reanalysis`` asks for one more over the ended one (lane RN, 2).
    """

    analysis_id=analysis_id or f"analysis-{secrets.token_hex(16)}"

    def prepare_analysis()->dict[str,object]:
        catalog,store=_open_protocol_catalog()
        try:
            entry=catalog.request_analysis(
                protocol_id,analysis_id,reanalysis=reanalysis,page_range=page_range)
            public=entry.public_dict()
            public["analysis_run"]=_analysis_run_public(catalog,protocol_id)
            return public
        finally:
            store.close()

    def run_analysis()->dict[str,object]:
        catalog,store=_open_protocol_catalog()
        try:
            try:
                model=_protocol_analysis_model()
            except (RuntimeError,ValueError) as exc:
                catalog.fail_analysis_request(
                    protocol_id,analysis_id,
                    failure_code="provider_configuration_missing",
                )
                raise ProtocolAnalysisUnavailableError(
                    "Protocol analysis provider is not configured."
                ) from exc
            entry=catalog.analyze(protocol_id,model,analysis_id=analysis_id)
            public=entry.public_dict()
            public["analysis_run"]=catalog.analysis_run_status(
                protocol_id).public_dict()
            return public
        finally:
            store.close()

    async def background_worker()->None:
        started=time.monotonic()
        try:
            completed=await asyncio.to_thread(run_analysis)
            _record_workspace_metric(
                category="protocol",metric_name="analysis",
                dimensions={
                    "status":str(completed.get("analysis_status") or "complete")[:100],
                    "source_kind":"local_pdf",
                },
                principal=principal,
            )
            _pilot_protocol_event(
                protocol_id,"analysis",principal=principal,public=completed,
                started=started)
        except Exception as exc:
            # The catalog persists bounded failure codes.  Provider responses,
            # prompts, and source text never enter logs or the lifecycle record.
            diagnostic = getattr(exc, "diagnostic", None)
            evidence_failure = (
                diagnostic.privacy_safe_dict()
                if diagnostic is not None
                and callable(getattr(diagnostic, "privacy_safe_dict", None))
                else {}
            )
            log.warning(
                "protocol.analysis.background_failed error=%s stage=%s "
                "item_index=%s item_type=%s category=%s source_page=%s "
                "provider_handle_count=%s reason_code=%s mismatch_class=%s "
                "expected_count=%s actual_count=%s expected_length=%s "
                "actual_length=%s missing_numbered_action_count=%s "
                "page_coverage_count=%s",
                type(exc).__name__,
                evidence_failure.get("validation_stage"),
                evidence_failure.get("item_index"),
                evidence_failure.get("item_type"),
                evidence_failure.get("category"),
                evidence_failure.get("source_page"),
                evidence_failure.get("provider_handle_count"),
                evidence_failure.get("reason_code"),
                evidence_failure.get("mismatch_class"),
                evidence_failure.get("expected_count"),
                evidence_failure.get("actual_count"),
                evidence_failure.get("expected_length"),
                evidence_failure.get("actual_length"),
                evidence_failure.get("missing_numbered_action_count"),
                evidence_failure.get("page_coverage_count"),
            )
            _record_workspace_metric(
                category="protocol",metric_name="analysis",
                dimensions={
                    "status":"failed",
                    "reason_code":str(getattr(exc,"code","analysis_failed"))[:100],
                    "source_kind":"local_pdf",
                },
                principal=principal,
            )
            _pilot_protocol_event(
                protocol_id,"analysis",principal=principal,failed=True,started=started)

    if _analysis_worker_running(protocol_id):
        def project(catalog:ProtocolCatalog)->dict[str,object]:
            public=catalog.get_entry(protocol_id).public_dict()
            public["analysis_run"]=_analysis_run_public(catalog,protocol_id)
            public["analysis_request_deduplicated"]=True
            return public
        return await _catalog_in_worker(project)
    # Lane RN decision 1 (2026-10-11): the slot is taken before the ledger
    # is written, so a second request that crosses this one finds the run
    # going and starts nothing. Until the task exists the slot holds a bare
    # future; a request that is not recorded frees it.
    reservation:asyncio.Future[None]=asyncio.get_running_loop().create_future()
    _PROTOCOL_ANALYSIS_TASKS[protocol_id]=reservation
    try:
        public=await asyncio.to_thread(prepare_analysis)
        state=(public.get("analysis_run") or {}).get("state")
        # Lane RV, decision a: an analysis waiting for a person's review
        # ended too; a plain request starts nothing over it.
        if state in {"review_required","approved","revoked","review_pending"}:
            public["analysis_request_deduplicated"]=True
            return public
        task=asyncio.create_task(background_worker())
        _PROTOCOL_ANALYSIS_TASKS[protocol_id]=task
        task.add_done_callback(
            lambda completed,pid=protocol_id: (
                _PROTOCOL_ANALYSIS_TASKS.pop(pid,None)
                if _PROTOCOL_ANALYSIS_TASKS.get(pid) is completed else None
            )
        )
        public["analysis_request_accepted"]=True
        (public.get("analysis_run") or {})["worker_running"]=True
        return public
    finally:
        if _PROTOCOL_ANALYSIS_TASKS.get(protocol_id) is reservation:
            _PROTOCOL_ANALYSIS_TASKS.pop(protocol_id,None)
            reservation.cancel()


def _analysis_worker_running(protocol_id:str)->bool:
    """Whether this process holds a run (or a reserved slot) for the protocol."""

    running=_PROTOCOL_ANALYSIS_TASKS.get(protocol_id)
    return running is not None and not running.done()


def _analysis_run_public(catalog:ProtocolCatalog,protocol_id:str)->dict[str,object]:
    """The ledger's run status plus what only this process knows (lane RN, 1):
    whether a worker still holds the run. The ledger alone reads "analyzing"
    for a run a restart cut short, and the screen must not wait on that one.
    """

    status=catalog.analysis_run_status(protocol_id).public_dict()
    ocr=_PROTOCOL_OCR_TASKS.get(protocol_id)
    status["worker_running"]=(
        _analysis_worker_running(protocol_id) or (ocr is not None and not ocr.done()))
    # Lane RN decision 4: the catalog's own stage line travels with the run
    # status, so the screen says an automatic retry in the server's words
    # (a cut answer, the reasoning level asked) instead of its own sentence.
    status["pipeline"]=catalog.pipeline_status(protocol_id)
    return status


@app.get("/api/protocols/{protocol_id}/analysis/status")
def get_protocol_analysis_status(protocol_id:str)->dict[str,object]:
    """Read persisted lifecycle state without starting or resuming analysis."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return _analysis_run_public(catalog,protocol_id)
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/title")
def post_protocol_title(protocol_id:str,body:dict[str,Any]=Body(...))->dict[str,object]:
    """A person's own name for a protocol (lane RX1, decision 4): one more
    ledger event, the latest name read as the title everywhere. The source,
    its metadata and the analyses are untouched."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            entry=catalog.rename(
                protocol_id,body.get("title"),actor_principal_id=_start_check_principal_id())
        finally:
            store.close()
        return {"protocol":entry.public_dict(),"title":entry.title}
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}/review")
def get_protocol_review(protocol_id: str) -> dict[str, object]:
    """Expose the source-linked analysis, its notices and its safety statements."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog, store = _open_protocol_catalog()
        try:
            review = catalog.review(protocol_id)
            # The screen the experimenter reads before pressing start
            # (decision of 2026-10-08): the source's safety statements with
            # their stored Korean beside them, never collapsed.
            review["safety_notices"]=_review_safety_notices(catalog,protocol_id,review)
            review["safety_scope"]=_safety_scope_public()
            review["pipeline"]=_pipeline_with_translation(catalog,protocol_id,review)
            return review
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}/review-draft")
def get_protocol_review_draft(protocol_id:str)->dict[str,object]:
    """The review tab's items, source lines and draft decisions (lane RV, b).

    The analysis a person reviews now -- one waiting for review, one that
    passed, or the latest review version -- item by item, with where each
    stands in the source, whether it waits for a person and why, its origin
    (원문 · AI 제안 · 검토자 입력) and the draft decision on it.
    """

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return catalog.review_draft(protocol_id)
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/review-decisions")
def post_protocol_review_decision(
    protocol_id:str,body:dict[str,Any]=Body(...),
)->dict[str,object]:
    """One decision on one item (lane RV, decision b): confirm, fix, leave
    empty (원문에 없음), enter, a memo or taking a decision back. One more
    ledger event; nothing runs differently until a version is registered."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return catalog.record_review_decision(protocol_id,_review_body_with_account(body))
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/review-versions",status_code=201)
def post_protocol_review_version(
    protocol_id:str,body:dict[str,Any]=Body(...),
)->dict[str,object]:
    """"검토 완료하고 등록" (lane RV, decision c): the reviewer's name is
    required, every item that waited for a person must be decided, and the
    review becomes version N -- the one that runs from now on -- beside the
    analysis and the versions before it."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            registered=catalog.register_review(protocol_id,_review_body_with_account(body))
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc
    # Lane LOG, decision 2: the time from the analysis to its review.
    _pilot_protocol_event(protocol_id,"review_registered")
    return registered


@app.get("/api/protocols/{protocol_id}/start-check")
def get_protocol_start_check(protocol_id:str)->dict[str,object]:
    """The two key values to confirm before the start (lane ON, decision ③):
    the questions without their answers, or why there are none."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return StartCheck(catalog).questions(protocol_id)
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/start-check/answers")
def post_protocol_start_check_answer(
    protocol_id:str,body:dict[str,Any]=Body(...),
)->dict[str,object]:
    """One choice on one question, judged and recorded by the server; or the
    value taken as it stands after a wrong choice (``action: accept``)."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return StartCheck(catalog).answer(
                protocol_id,body.get("question_id"),
                choice_id=body.get("choice_id"),action=body.get("action"),
                actor_principal_id=_start_check_principal_id())
        finally:
            store.close()
    except StartCheckError as exc:
        raise HTTPException(status_code=422,detail=exc.code) from exc
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/start-check/safety")
def post_protocol_start_safety(
    protocol_id:str,body:dict[str,Any]=Body(...),
)->dict[str,object]:
    """Record the experimenter's explicit acknowledgement of this target's safety notices."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return StartCheck(catalog).acknowledge_safety(
                protocol_id,target=body.get("target"),
                actor_principal_id=_start_check_principal_id())
        finally:
            store.close()
    except StartCheckError as exc:
        raise HTTPException(status_code=422,detail=exc.code) from exc
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}/start-check/changes")
def get_protocol_run_changes(protocol_id:str)->dict[str,object]:
    """"오늘 원문과 다르게 할 값" (lane EASY, decision 2): the values the
    analysis (or review version) about to run states that today's run may
    change -- time, temperature, speed, volume, concentration -- each with
    its step and source words."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            # Lane RX1, decision 3: each step's sentence with its stored
            # Korean, read only (nothing starts a translation here).
            options=RunChanges(
                catalog,
                localize=lambda fixture:_with_revision_translations(fixture,start_missing=False),
            ).options(protocol_id,actor_principal_id=_start_check_principal_id())
        finally:
            store.close()
        # Lane EASY, decision 3: "지난번처럼" -- the same account's last run of
        # this protocol, its changed values on today's list.
        last=_last_run_values(protocol_id)
        matched=RunChanges.last_time(options["values"],(last or {}).get("changes") or ())
        options["last_time"]=({"changes":matched,"report_id":last.get("report_id"),
                               "when_ko":_local_day_words(last.get("started_at"))}
                              if matched else None)
        return options
    except RunChangeError as exc:
        raise HTTPException(status_code=422,detail={"code":exc.code,"message_ko":exc.message_ko}) from exc
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/start-check/changes")
def post_protocol_run_changes(
    protocol_id:str,body:dict[str,Any]=Body(...),
)->dict[str,object]:
    """The person's values for today (an empty list: the step skipped), checked
    item by item -- the same kind of unit, a value the list holds -- and kept
    in the protocol's ledger for the next start; the server's sentence when
    one is refused."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return RunChanges(catalog).choose(
                protocol_id,body.get("changes"),target=body.get("target"),
                actor_principal_id=_start_check_principal_id(),source=body.get("source","wizard"),
                confirm_large=body.get("confirm_large") or (),
                save_as_lab_revision=body.get("save_as_lab_revision",False))
        finally:
            store.close()
    except RunChangeError as exc:
        # A large value comes back with its question for the screen's second look.
        raise HTTPException(status_code=422,detail={
            "code":exc.code,"message_ko":exc.message_ko,
            **({"large":exc.large} if exc.large else {})}) from exc
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


def _last_run_values(protocol_id:str)->dict[str,Any]|None:
    """The signed-in person's last run of the protocol, as the records'
    ownership rule lets them read it (lane EASY, decision 3); None without a
    known owner or the records."""

    settings=ExperimentReportSettings.from_environment()
    principal=_REQUEST_PRINCIPAL.get() if _workspace_settings().enabled else None
    if principal is None or not settings.enabled or settings.database_path is None:
        return None
    try:
        return ExperimentReportStore(settings.database_path).last_run_values(
            protocol_id=protocol_id,organization_id=getattr(principal,"organization_id",None),
            owner_principal_id=getattr(principal,"principal_id",None))
    except Exception as exc:  # noqa: BLE001 - no last time is no button, never a refusal
        log.warning("run_changes.last_time_unavailable error=%s",type(exc).__name__)
        return None


def _local_day_words(moment:object)->str:
    try:
        when=datetime.fromisoformat(str(moment)).astimezone(report_timezone())
    except (TypeError,ValueError):
        return ""
    return f"{when.month}월 {when.day}일"


RUN_CHANGES_REFUSAL_KO={
    "run_changes_required":"오늘 원문과 다르게 할 값 단계를 지나야 시작할 수 있어요. 바꿀 값이 없으면 '바꿀 값 없이 다음'을 눌러 주세요.",
    "run_changes_stale":"오늘 바꾼 값이 지금 분석과 맞지 않아요. 시작 화면에서 다시 골라 주세요.",
}


def _start_run_changes(
    protocol_id:str,revision_id:str|None,fixture:CuratedProtocolFixture,
    change_set_id:str|None=None,
)->tuple[tuple[str|None,tuple[RunChange,...]],str|None]:
    """((change set id, today's values), refusal) for a new run's start.

    Lane EASY (human decisions 2026-10-24): the start names the choice it
    uses -- the "오늘 원문과 다르게 할 값" step it passed, "바꿀 값 없이 다음"
    included -- and nothing chosen earlier applies on its own. With the start
    check on (``VOINEY_LAB_START_CHECK_ENABLED``, the gate family the step
    joins) a start that names none is refused ``run_changes_required``. The
    named choice must be this person's, on the revision about to run or on the
    catalog entry's the wizard read (the development fixture runs from its
    file, "fixture-…", its catalog copy is "pdf-1-analysis-1"), unused, and
    every value must still be the fixture's as the source prints it;
    otherwise ``run_changes_stale``, never applied in part."""

    if not _protocol_store_settings().enabled:
        return (None,()),None
    if change_set_id is None:
        return (None,()),("run_changes_required" if start_check_enabled() else None)
    catalog,store=_open_protocol_catalog()
    try:
        try:
            listed=catalog.get_entry(protocol_id).revision_id
        except ProtocolCatalogError:
            listed=None
        try:
            changes=RunChanges(catalog).for_start(
                protocol_id,change_set_id,actor_principal_id=_start_check_principal_id(),
                revision_ids=(revision_id,listed))
        except RunChangeError:
            return (None,()),"run_changes_stale"
    finally:
        store.close()
    values={value.item_id:value for value in changeable_values(fixture.steps)}
    if any((value:=values.get(change.item_id)) is None or value.number!=change.source_number
           or value.unit!=change.source_unit or value.step_id!=change.step_id for change in changes):
        return (None,()),"run_changes_stale"
    return (change_set_id,changes),None


def _report_lab_revision(protocol_id:str,change_set_id:str|None)->dict[str,Any]|None:
    """The lab-adapted revision named by this run, without account identity."""

    if not change_set_id:
        return None
    catalog,store=_open_protocol_catalog()
    try:
        chosen=next((event.payload for event in catalog.store.list_events(protocol_id)
                     if event.event_type=="protocol_run_changes_chosen"
                     and str((event.payload or {}).get("change_set_id"))==change_set_id),None)
        revision=(chosen or {}).get("lab_adapted_revision")
        if not isinstance(revision,dict):
            return None
        kept={key:revision.get(key) for key in (
            "revision_id","version","label_ko","source_revision_id","recorded_at","changes")}
        if not isinstance(kept["changes"],list):
            return None
        return kept
    finally:
        store.close()


def _report_checkpoint_continuation(
    session_id:str,protocol_id:str,protocol_revision:str,
)->dict[str,str]|None:
    """The stopped report a workspace checkpoint session continues."""

    workspace=None
    try:
        principal,workspace=_commercial_workspace()
        state=workspace.get_experiment(principal,session_id)
    except Exception:  # noqa: BLE001 - ordinary starts have no checkpoint
        return None
    finally:
        if workspace is not None:
            workspace.close()
    event=next((item for item in state.get("events") or ()
                if item.get("event_type")=="session_restarted_from_checkpoint"),None)
    payload=event.get("payload") if isinstance((event or {}).get("payload"),dict) else {}
    source_session_id=str(payload.get("source_session_id") or "")
    if not source_session_id:
        return None
    source_report_id="ER-"+hashlib.sha256(
        f"{source_session_id}\x1f{protocol_id}\x1f{protocol_revision}".encode()
    ).hexdigest()[:20].upper()
    return {"source_session_id":source_session_id,"source_report_id":source_report_id}


def _mark_run_changes_applied(protocol_id:str,change_set_id:str,experiment_session_id:str|None)->None:
    catalog,store=_open_protocol_catalog()
    try:
        RunChanges(catalog).mark_applied(
            protocol_id,change_set_id,experiment_session_id=experiment_session_id,
            actor_principal_id=_start_check_principal_id())
    finally:
        store.close()


def _applied_run_changes(protocol_id:str,experiment_session_id:str)->tuple[RunChange,...]:
    if not _protocol_store_settings().enabled:
        return ()
    catalog,store=_open_protocol_catalog()
    try:
        return RunChanges(catalog).applied_to(protocol_id,experiment_session_id)
    finally:
        store.close()


def _start_check_principal_id()->str|None:
    principal=_REQUEST_PRINCIPAL.get()
    if (principal is None and not _auth_settings().accounts
            and not _workspace_settings().enabled and _runtime_usage_scope()!="operational"):
        principal=DevIdentityProvider.from_environment().authenticate()
    return principal.principal_id if principal is not None else None


def _protocol_start_readiness(protocol_id:str,revision_id:str|None)->dict[str,Any]:
    if not start_check_enabled():
        return {"ready":True,"reason":None}
    catalog,store=_open_protocol_catalog()
    try:
        return StartCheck(catalog).readiness(
            protocol_id,revision_id=revision_id,
            actor_principal_id=_start_check_principal_id())
    finally:
        store.close()


@app.get("/api/protocols/{protocol_id}/review-versions/{version}")
def get_protocol_review_version(protocol_id:str,version:int)->dict[str,object]:
    """One registered review version with what it changed and from what
    (lane RV, decision c): the review tab's version history."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return catalog.review_version(protocol_id,version)
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


#: Lane RX1, decision 1: the page picker's small pictures. A 612 x 792 pt
#: page becomes 306 x 396 px; enough to tell a methods page from a figure page.
SOURCE_THUMBNAIL_DPI=36
#: The thumbnails rendered so far, by (source SHA-256, page); a few hundred
#: small PNGs at most, in memory only.
_SOURCE_THUMBNAILS:collections.OrderedDict[tuple[str,int],bytes]=collections.OrderedDict()
_SOURCE_THUMBNAILS_MAX=256
_SOURCE_THUMBNAILS_LOCK=threading.Lock()


@app.get("/api/protocols/{protocol_id}/review-draft/source-pages")
def get_protocol_source_pages(protocol_id:str)->dict[str,object]:
    """How many pages the source has, before or after an analysis, and the
    resolutions the page images come in (lane RX1, decision 1: the upload
    asks which pages to analyse with small pictures of them)."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            page_count=catalog.source_page_count(protocol_id)
        finally:
            store.close()
        return {"protocol_id":protocol_id,"page_count":page_count,
                "thumbnail_dpi":SOURCE_THUMBNAIL_DPI,
                "image_dpi":source_figures.PAGE_IMAGE_DPI}
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}/review-draft/source-pages/{source_page}")
def get_protocol_review_source_page(protocol_id:str,source_page:int)->dict[str,object]:
    """One source page as the review read it: every line with its number,
    the lines holding a value and those an unclear OCR reading holds, and
    the OCR's value boxes (lane RV, decision b). Lane RX1, decision 2: each
    line's box in points and the page image's dpi, so the screen puts a band
    on the image where the card's lines stand."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            page=catalog.review_source_page(protocol_id,source_page)
        finally:
            store.close()
        page["image_dpi"]=source_figures.PAGE_IMAGE_DPI
        return page
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get(
    "/api/protocols/{protocol_id}/review-draft/source-pages/{source_page}"
    "/value-boxes/{box_index}/image"
)
def get_protocol_review_value_box_image(protocol_id:str,source_page:int,box_index:int):
    """The piece of a photo or handwritten page an OCR value was read from,
    for the review screen to show beside the value (lane RV, decision e)."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            png=catalog.review_value_box_image(protocol_id,source_page,box_index)
        finally:
            store.close()
        return Response(
            content=png,media_type="image/png",
            headers={
                "Cache-Control":"private, no-store",
                "X-Content-Type-Options":"nosniff",
                "Content-Security-Policy":(
                    "default-src 'none'; style-src 'unsafe-inline'; sandbox"),
                "Content-Disposition":(
                    f'inline; filename="value-box-{source_page}-{box_index}.png"'),
                "X-Protocol-Source-Page":str(source_page),
            })
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


def _source_thumbnail(path:Path,sha256:str,page_count:int,source_page:int)->bytes:
    """One page as a small PNG (lane RX1, decision 1), rendered by the one PDF
    engine in its child process and kept in memory for the next look."""

    if not isinstance(source_page,int) or not 1<=source_page<=page_count:
        raise ProtocolCatalogNotFoundError("Protocol source page is unknown.")
    key=(sha256,source_page)
    with _SOURCE_THUMBNAILS_LOCK:
        cached=_SOURCE_THUMBNAILS.get(key)
        if cached is not None:
            _SOURCE_THUMBNAILS.move_to_end(key)
            return cached
    png=pdf_text_engine.render_page_png(path,source_page,dpi=SOURCE_THUMBNAIL_DPI)
    with _SOURCE_THUMBNAILS_LOCK:
        _SOURCE_THUMBNAILS[key]=png
        _SOURCE_THUMBNAILS.move_to_end(key)
        while len(_SOURCE_THUMBNAILS)>_SOURCE_THUMBNAILS_MAX:
            _SOURCE_THUMBNAILS.popitem(last=False)
    return png


@app.get("/api/protocols/{protocol_id}/review-draft/source-pages/{source_page}/image")
def get_protocol_review_source_page_image(protocol_id:str,source_page:int,size:str|None=None):
    """The source page itself for the review screen (lane RV, decision b).

    The same rendering as "원본 쪽 보기", for any protocol the caller may see
    -- the review is of an analysis that may not run yet. ``?size=thumb``
    (lane RX1, decision 1) is the page picker's small picture of the page.
    """

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            path,sha256=catalog.source_pdf(protocol_id)
            page_count=catalog.source_page_count(protocol_id) if size=="thumb" else None
        finally:
            store.close()
        if size=="thumb":
            png=_source_thumbnail(path,sha256,page_count or 0,source_page)
        else:
            png=source_figures.page_image(path,sha256,source_page)
        if png is None:
            raise ProtocolCatalogNotFoundError("Protocol source page is unknown.")
        return Response(
            content=png,media_type="image/png",
            headers={
                "Cache-Control":"private, no-store",
                "X-Content-Type-Options":"nosniff",
                "Content-Security-Policy":(
                    "default-src 'none'; style-src 'unsafe-inline'; sandbox"),
                "Content-Disposition":f'inline; filename="source-page-{source_page}.png"',
                "X-Protocol-Source-SHA256":sha256,
                "X-Protocol-Source-Page":str(source_page),
            })
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


def _pipeline_with_translation(
    catalog:ProtocolCatalog,protocol_id:str,review:dict[str,object],
)->dict[str,object]:
    """The catalog's stage line, with what the server alone knows (lane PX 4):
    how far the revision's Korean has come.
    """

    pipeline=dict(review.get("pipeline") or {})
    if not pipeline:
        return pipeline
    if review.get("analysis_available") is True:
        try:
            pipeline["translation"]=_translation_progress(catalog,protocol_id)
        except Exception as exc:  # noqa: BLE001 - the stage line stands without it
            log.warning("revision_translation progress_failed error=%s",type(exc).__name__)
            pipeline["translation"]={"state":"unknown","message":"번역 상태를 읽지 못했습니다."}
    return pipeline


def _review_safety_notices(
    catalog:ProtocolCatalog,protocol_id:str,review:dict[str,object],
)->list[dict[str,object]]:
    """The source's safety statements with their Korean, for the start screen.

    The fixture a session will run, with its stored translations, so the
    Korean shown here is the Korean the step will show; a line whose
    translation is missing or fails the mechanical check shows the source
    alone. Empty before an analysis has passed.
    """

    if review.get("analysis_available") is not True:
        return []
    try:
        fixture=_with_revision_translations(
            catalog.load_analysis_fixture(protocol_id),start_missing=False)
    except Exception as exc:  # noqa: BLE001 - the sources stand without Korean
        log.warning("safety_notices fixture_unavailable error=%s",type(exc).__name__)
        return [
            {**item,"primary_text":None,"translation_check":"missing"}
            for item in review.get("safety_notice_sources") or []
            if isinstance(item,dict)
        ]
    return fixture_safety_notices(fixture)


def _translation_progress(catalog:ProtocolCatalog,protocol_id:str)->dict[str,object]:
    """How much of a passed analysis's Korean exists, in one Korean line."""

    fixture=catalog.load_analysis_fixture(protocol_id)
    key=translation_revision_key(fixture)
    units=translation_units(fixture)
    step_keys=[f"{step.step_id}/current_step" for step in fixture.steps]
    korean_source={unit.fact_key for unit in units if is_korean(unit.source_text)}
    needed=[unit for unit in units if unit.fact_key not in korean_source]
    store=_revision_translation_store()
    if store is None:
        return {
            "state":"off","units":len(units),"needed":len(needed),
            "steps_total":len(step_keys),
            "steps_korean":sum(1 for k in step_keys if k in korean_source),
            "message":"자동 번역이 꺼져 있습니다(작업공간이 꺼짐). 단계는 원문으로 안내합니다.",
        }
    try:
        rows=store.fact_translations(key,"ko")
        glossaries=glossaries_by_version(store.translation_glossaries(key,"ko"))
    finally:
        store.close()
    attached=with_stored_translations(fixture,rows,glossaries=glossaries)
    machine=getattr(attached,"machine_localizations",None) or {}
    reviewed=getattr(attached,"localizations",None) or {}
    passed={key_ for key_ in (*machine,*reviewed)}
    steps_korean=sum(1 for k in step_keys if k in passed or k in korean_source)
    # Lane FX, decision 2: refused by today's check, not by the verdict
    # stored with the row (each judged once; the rows themselves unchanged).
    # Lane KO2: a sentence is refused when none of its rows, of any style
    # version, passes.
    refused=len({
        record.fact_key
        for record,result in stored_checks(fixture,rows,glossaries=glossaries,units=units)
        if result!="passed"}-passed)
    with _REVISION_TRANSLATIONS_LOCK:
        running=key in _REVISION_TRANSLATIONS_RUNNING
    runner=_revision_translation_runner()
    if not needed:
        state,message="done","번역할 문장이 없습니다. 원문이 이미 한국어입니다."
    elif running:
        state="running"
        message=f"번역 준비 중 · 한국어 단계 {steps_korean}/{len(step_keys)} · 준비되면 다음 안내부터 씁니다."
    elif rows:
        state="done"
        message=f"번역 끝 · 한국어 단계 {steps_korean}/{len(step_keys)}"
        if refused:
            message+=f" · 검사 실패 {refused}문장은 원문으로 보입니다"
    elif runner is None:
        state="off"
        message=("자동 번역이 꺼져 있습니다(번역 역할 키 또는 작업공간 없음). 단계는 원문으로 "
                 "안내합니다.")
    else:
        state="pending"
        message="번역 대기 중입니다. 분석 통과 직후 시작됩니다."
    return {
        "state":state,"units":len(units),"needed":len(needed),"passed":len(passed),
        "refused":refused,"steps_total":len(step_keys),"steps_korean":steps_korean,
        "message":message,
    }


@app.get("/api/protocols/{protocol_id}/revisions/{revision_id}/assets/{asset_id}")
def get_protocol_visual_asset(
    protocol_id:str,revision_id:str,asset_id:str,
):
    try:
        _scope_catalog_resource(protocol_id)
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


def _require_admin_access(presented_token:str|None)->None:
    """Fail closed without retaining or logging the presented credential."""

    configured=os.environ.get("VOINEY_LAB_ADMIN_TOKEN","").strip()
    if not configured:
        raise HTTPException(status_code=503,detail="admin_access_not_configured")
    if not presented_token:
        raise HTTPException(status_code=403,detail="admin_access_denied")
    configured_digest=hashlib.sha256(configured.encode()).digest()
    presented_digest=hashlib.sha256(presented_token.encode()).digest()
    if not hmac.compare_digest(configured_digest,presented_digest):
        raise HTTPException(status_code=403,detail="admin_access_denied")


@app.get("/api/admin/metrics")
def get_admin_metrics(
    x_voice_workflow_admin_token:str|None=Header(default=None),
)->dict[str,object]:
    """Return aggregate product/operations signals without private lab content."""

    _require_admin_access(x_voice_workflow_admin_token)
    try:
        report_settings=ExperimentReportSettings.from_environment()
    except ValueError:
        report_settings=ExperimentReportSettings(False)
        report_status="invalid_configuration"
    else:
        report_status="enabled" if report_settings.enabled else "disabled"
    if report_settings.enabled and report_settings.database_path is not None:
        report_metrics=ExperimentReportStore(
            report_settings.database_path).aggregate_metrics()
    else:
        report_metrics={
            "reports":{
                "total":0,"completed":0,"completion_rate":None,
                "by_status":{},
            },
            "workflow_events":{},
            "quality":{
                "anomalies":0,"blockers":0,"common_blocked_steps":[],
            },
            "privacy":{
                "raw_audio_included":False,
                "transcripts_included":False,
                "free_text_included":False,
                "report_identifiers_included":False,
            },
        }
    try:
        catalog_entries,_,_=_public_protocol_catalog_entries()
        catalog_status="available"
    except Exception:
        catalog_entries=[]
        catalog_status="unavailable"
    analysis_counts:dict[str,int]={}
    for entry in catalog_entries:
        status=str(entry.get("analysis_status") or "unknown")
        analysis_counts[status]=analysis_counts.get(status,0)+1
    return {
        "schema_version":1,
        "voice":{
            "pipeline":"cascade",
            "provider":_tts_provider(),
            "voice_id":_tts_voice(),
            "persona":"professor",
        },
        "experiment_reporting":{
            "status":report_status,
            **report_metrics,
        },
        "protocol_catalog":{
            "status":catalog_status,
            "total":len(catalog_entries),
            "executable":sum(
                item.get("available_for_execution") is True
                for item in catalog_entries),
            "development_only":sum(
                item.get("development_only") is True
                for item in catalog_entries),
            "by_analysis_status":analysis_counts,
        },
        "runtime":RUNTIME_METRICS.snapshot(),
    }


def _require_pilot_admin(presented_token:str|None)->None:
    """Lane LOG, decision 4: an admin account, or else the admin token.

    Lane FX4, decision 3: an admin account is lane ON's administrator -- the
    account behind this request (``_account_is_admin``, the one judgment);
    a principal's role text is not read. Otherwise the token, as before.
    """

    if _account_is_admin():
        return
    _require_admin_access(presented_token)


def _pilot_sources()->PilotSources:
    """The files the pilot metrics are read from, as this server is configured."""

    metrics_db=workspace_db=reports_db=None
    workspace=_workspace_settings()
    if workspace.enabled and workspace.data_dir is not None:
        metrics_db=workspace.data_dir/PILOT_METRICS_FILENAME
        workspace_db=workspace.data_dir/WORKSPACE_DATABASE_FILENAME
    try:
        report_settings=ExperimentReportSettings.from_environment()
    except ValueError:
        report_settings=ExperimentReportSettings(False)
    if report_settings.enabled:
        reports_db=report_settings.database_path
    allowed_accounts=None
    account=_request_account()
    if account is not None and _account_is_admin():
        store=_account_store()
        try:
            allowed_accounts=frozenset(item.principal().principal_id
                for item in store.list_accounts(account.organization_id))
        finally:
            store.close()
    return PilotSources(metrics_db=metrics_db,reports_db=reports_db,workspace_db=workspace_db,
                        allowed_accounts=allowed_accounts,
                        organization_id=account.organization_id if allowed_accounts is not None else None)


@app.get("/api/admin/pilot-metrics")
def get_admin_pilot_metrics(
    since:str|None=Query(default=None,alias="from"),
    until:str|None=Query(default=None,alias="to"),
    account:str|None=Query(default=None),
    x_voice_workflow_admin_token:str|None=Header(default=None),
)->dict[str,object]:
    """Lane LOG, decision 4: the pilot's day table -- numbers, codes and times only.

    The admin screen (static/admin-metrics.html) reads it. The rating memos
    are not here; scripts/pilot_metrics.py notes is the only way to them.
    """

    _require_pilot_admin(x_voice_workflow_admin_token)
    try:
        first=date.fromisoformat(since) if since else None
        last=date.fromisoformat(until) if until else None
        if account is not None and not PILOT_ACCOUNT_PATTERN.fullmatch(account):
            raise ValueError("account id is malformed")
    except ValueError as exc:
        raise HTTPException(status_code=400,detail="pilot_metrics_query_invalid") from exc
    try:
        days=day_rows(_pilot_sources(),since=first,until=last,account=account)
    except Exception as exc:
        log.warning("pilot.metrics.failed kind=admin error=%s",type(exc).__name__)
        raise HTTPException(status_code=503,detail="pilot_metrics_unavailable") from exc
    return {
        "schema_version":1,
        "period":{"from":since,"to":until,"account":account},
        "columns":list(DAY_COLUMNS),
        "days":days,
        "totals":day_totals(days),
        "privacy":{
            "transcripts_included":False,"free_text_included":False,
            "protocol_text_included":False,"raw_audio_included":False,
        },
    }


@app.post("/api/pilot/feedback",status_code=201)
async def post_pilot_feedback(request:Request)->dict[str,object]:
    """Lane LOG, decision 3: "이번 실험에 도움이 됐나요?" -- kept with its experiment.

    A rating 1-5 with an optional one-line memo, or a skip; the last answer
    about an experiment is the one its summary carries. Kept in the pilot
    metrics store, so without a workspace there is nowhere to keep it (503).
    """

    payload=await _json_object(request)
    try:
        answer=FeedbackAnswer.from_request(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400,detail="feedback_invalid") from exc
    try:
        store=_pilot_store()
    except Exception as exc:
        raise HTTPException(status_code=503,detail="pilot_metrics_unavailable") from exc
    if store is None:
        raise HTTPException(status_code=503,detail="pilot_metrics_unavailable")
    try:
        _scope_tenant_resource("experiment_report",answer.report_id,write=True)
        report_settings=ExperimentReportSettings.from_environment()
        if report_settings.enabled and report_settings.database_path is not None:
            ExperimentReportStore(report_settings.database_path).get_report(answer.report_id)
    except Exception as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    try:
        store.record_feedback(account_id=_pilot_account(),answer=answer)
    except Exception as exc:
        log.warning("pilot.metrics.failed kind=feedback error=%s",type(exc).__name__)
        raise HTTPException(status_code=503,detail="pilot_metrics_unavailable") from exc
    return {"saved":True}


def _report_writer_brain()->ReportWriterBrain|None:
    """The report role's writer, or None when no report model is configured."""

    try:
        writer_settings=ReportWriterSettings.from_environment()
        if not writer_settings.enabled:
            return None
        report_role=RoleModel.from_environment("report")
        if not report_role.has_key():
            return None
        return ReportWriterBrain(
            client=_role_client(report_role,timeout=writer_settings.timeout_seconds),
            model=writer_settings.model,
            timeout_seconds=writer_settings.timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - the server's sentences stand in
        log.warning("report writer setup failed error=%s",type(exc).__name__)
        return None


def _prepare_report_prose(store:ExperimentReportStore,report_id:str,*,again:bool=False)->str:
    """Start the report's one model call in the background (lane RP, decision 9).

    Called when an experiment ends; a download or a person's "다시 만들기"
    calls it too. Never raises: a failure leaves the server's own sentences.
    """

    try:
        return REPORT_PROSE.start(store,report_id,_report_writer_brain,again=again)
    except Exception as exc:  # noqa: BLE001
        log.warning("report prose not started error=%s",type(exc).__name__)
        return "failed"


def _report_prose_store(report_id:str,*,write:bool=False)->ExperimentReportStore:
    _scope_tenant_resource("experiment_report",report_id,write=write)
    settings=ExperimentReportSettings.from_environment()
    if not settings.enabled or settings.database_path is None:
        raise HTTPException(status_code=404,detail="experiment report unavailable")
    return ExperimentReportStore(settings.database_path)


def _notebook_binder_scope()->tuple[Any,ExperimentReportStore]:
    principal,workspace=_commercial_workspace()
    workspace.close()
    settings=ExperimentReportSettings.from_environment()
    if not settings.enabled or settings.database_path is None:
        raise HTTPException(status_code=404,detail="notebook binder unavailable")
    return principal,ExperimentReportStore(settings.database_path)


def _notebook_binder_response(binder:Any,*,status_code:int=200)->JSONResponse:
    return JSONResponse(
        content=jsonable_encoder(binder.public_dict()),status_code=status_code,
        headers={"Cache-Control":"private, no-store"},
    )


@app.get("/api/notebook-binders/candidates")
def get_notebook_binder_candidates(
    date_from:str="",date_to:str="",protocol_id:str="",
)->Response:
    principal,store=_notebook_binder_scope()
    try:
        reports=store.notebook_binder_candidates(
            organization_id=principal.organization_id,
            owner_principal_id=principal.principal_id,
            date_from=date_from,date_to=date_to,protocol_id=protocol_id)
        binders=store.list_notebook_binders(
            organization_id=principal.organization_id,
            owner_principal_id=principal.principal_id)
        return JSONResponse(content=jsonable_encoder({
            "reports":reports,"binders":binders,
        }),headers={"Cache-Control":"private, no-store"})
    except ValueError as exc:
        raise HTTPException(status_code=422,detail="notebook binder filter is invalid") from exc


@app.post("/api/notebook-binders",status_code=201)
async def create_notebook_binder(request:Request)->Response:
    body=await request.json()
    if not isinstance(body,dict) or not isinstance(body.get("report_ids"),list):
        raise HTTPException(status_code=422,detail="notebook binder is invalid")
    principal,store=_notebook_binder_scope()
    try:
        binder=store.create_notebook_binder(
            organization_id=principal.organization_id,
            owner_principal_id=principal.principal_id,
            topic_name=body.get("topic_name"),
            date_from=body.get("date_from","") if isinstance(body.get("date_from",""),str) else "invalid",
            date_to=body.get("date_to","") if isinstance(body.get("date_to",""),str) else "invalid",
            protocol_id=body.get("protocol_id","") if isinstance(body.get("protocol_id",""),str) else "invalid",
            report_ids=body["report_ids"],
        )
        return _notebook_binder_response(binder,status_code=201)
    except KeyError as exc:
        raise HTTPException(status_code=404,detail="notebook binder unavailable") from exc
    except (ValueError,RuntimeError,sqlite3.Error) as exc:
        raise HTTPException(status_code=422,detail="notebook binder is invalid") from exc


def _notebook_binder_for_request(binder_id:str)->tuple[Any,ExperimentReportStore]:
    principal,store=_notebook_binder_scope()
    try:
        binder=store.notebook_binder(
            binder_id,organization_id=principal.organization_id,
            owner_principal_id=principal.principal_id)
    except (KeyError,ValueError,RuntimeError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="notebook binder unavailable") from exc
    return binder,store


@app.get("/api/notebook-binders/{binder_id}.{format_name}")
def export_notebook_binder(binder_id:str,format_name:str)->Response:
    binder,store=_notebook_binder_for_request(binder_id)

    def resolve(image:Any)->tuple[bytes,str]|None:
        if getattr(image,"kind","")=="photo" and getattr(image,"source_session_id",""):
            response=download_workspace_experiment_evidence(
                str(image.source_session_id),str(image.asset_id))
            media_type=str(response.media_type or "")
            return (bytes(response.body),media_type) if media_type.startswith("image/") else None
        if getattr(image,"kind","")!="source_figure":
            return None
        checked_documents=set()
        for entry in binder.entries:
            source_document_id=getattr(image,"source_document_id","")
            if (entry.protocol_sha256!=source_document_id
                    or entry.protocol_sha256 in checked_documents):
                continue
            checked_documents.add(entry.protocol_sha256)
            fixture,_problem=report_protocol_fixture(store.get_report(entry.report_id))
            visual_for_step=getattr(fixture,"visual_for_step",None)
            visual_content=getattr(fixture,"visual_content",None)
            if not callable(visual_for_step) or not callable(visual_content):
                continue
            for index in range(len(getattr(fixture,"steps",()) or ())):
                asset=visual_for_step(index)
                if (asset is None or asset.asset_id!=image.asset_id
                        or asset.source_document_id!=source_document_id):
                    continue
                resolved,content=visual_content(index)
                if (resolved.asset_id==image.asset_id
                        and resolved.source_document_id==source_document_id):
                    return content,resolved.mime_type
        return None

    if format_name=="docx":
        content=render_notebook_binder_docx(binder,image_resolver=resolve)
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif format_name=="pdf":
        try:
            content=render_notebook_binder_pdf(binder,image_resolver=resolve)
        except ReportPdfError as exc:
            raise HTTPException(status_code=503,detail=exc.code) from exc
        media_type="application/pdf"
    else:
        raise HTTPException(status_code=404,detail="notebook binder unavailable")
    return Response(
        content=content,media_type=media_type,
        headers={"Cache-Control":"private, no-store","Content-Disposition":(
            f'attachment; filename="{binder.binder_id}.{format_name}"')},
    )


@app.get("/api/notebook-binders/{binder_id}")
def get_notebook_binder(binder_id:str)->Response:
    binder,_store=_notebook_binder_for_request(binder_id)
    return _notebook_binder_response(binder)


@app.get("/api/experiment-reports/{report_id}/prose")
def get_experiment_report_prose(report_id:str):
    """Whether the report's prose is being prepared or ready (decision 9)."""

    store=_report_prose_store(report_id)
    try:
        return REPORT_PROSE.status(store,report_id)
    except (ValueError,KeyError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc


@app.post("/api/experiment-reports/{report_id}/prose")
def regenerate_experiment_report_prose(report_id:str):
    """Write the report's prose again -- only when a person asks (decision 9)."""

    store=_report_prose_store(report_id,write=True)
    try:
        _prepare_report_prose(store,report_id,again=True)
        return REPORT_PROSE.status(store,report_id)
    except (ValueError,KeyError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc


@app.get("/api/experiment-reports/{report_id}/notebook")
def get_experiment_report_notebook(report_id:str)->Response:
    """The export's existing notebook projection, with no prose preparation or write."""
    store=_report_prose_store(report_id)
    try:
        report=store.get_report(report_id)
        narrative=store._narrative(report_id,None,None)
        return JSONResponse(content=jsonable_encoder({
            "report":{key:report.get(key) for key in (
                "report_id","session_id","protocol_title","status","started_at","ended_at",
                "owner_principal_id")},
            "notebook":narrative.facts.notebook,
            "review":report_review_items(report["events"]),
            "events":report["events"],
        }),headers={"Cache-Control":"private, no-store"})
    except (ValueError,KeyError,RuntimeError,OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc


def _notebook_input(value:Any,field:str,maximum:int)->str:
    if not isinstance(value,str):
        raise HTTPException(status_code=422,detail=f"{field} is invalid")
    cleaned=" ".join(value.split())
    if len(cleaned)>maximum:
        raise HTTPException(status_code=422,detail=f"{field} is invalid")
    return cleaned


def _notebook_event_key(
    report_id:str,event_type:str,payload:Mapping[str,Any],*,sequence:int,
)->str:
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return "note1-"+hashlib.sha256(
        f"{report_id}\x1f{event_type}\x1f{sequence}\x1f{encoded}".encode("utf-8")
    ).hexdigest()[:32]


def _notebook_template_scope()->tuple[Any,ExperimentReportStore]:
    principal,workspace=_commercial_workspace()
    workspace.close()
    settings=ExperimentReportSettings.from_environment()
    if not settings.enabled or settings.database_path is None:
        raise HTTPException(status_code=404,detail="experiment report unavailable")
    return principal,ExperimentReportStore(settings.database_path)


@app.get("/api/notebook-template")
def get_notebook_template()->Response:
    principal,store=_notebook_template_scope()
    template=store.notebook_template(principal.organization_id)
    return JSONResponse(content=jsonable_encoder(template),headers={"Cache-Control":"private, no-store"})


@app.put("/api/notebook-template")
async def put_notebook_template(request:Request)->Response:
    if not _account_is_admin():
        raise HTTPException(status_code=403,detail="administrator required")
    principal,store=_notebook_template_scope()
    body=await _json_object(request)
    sections=body.get("sections")
    if not isinstance(sections,list) or not all(isinstance(item,dict) for item in sections):
        raise HTTPException(status_code=422,detail="notebook template is invalid")
    canonical=[{"id":str(item.get("id") or ""),"name":" ".join(str(item.get("name") or "").split())}
               for item in sections]
    try:
        current=store.notebook_template(principal.organization_id)
        if current["sections"]==canonical:
            saved=current
        else:
            encoded=json.dumps(canonical,ensure_ascii=False,separators=(",",":"))
            event_key="template-"+hashlib.sha256(
                f"{current['version']}\x1f{encoded}".encode("utf-8")
            ).hexdigest()[:32]
            saved=store.save_notebook_template(
                organization_id=principal.organization_id,
                actor_principal_id=principal.principal_id,
                sections=canonical,event_key=event_key,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422,detail="notebook template is invalid") from exc
    return JSONResponse(content=jsonable_encoder(saved),headers={"Cache-Control":"private, no-store"})


@app.post("/api/experiment-reports/{report_id}/notebook/domain")
async def select_experiment_notebook_domain(report_id:str,request:Request)->Response:
    """Append the owner's explicit notebook field profile; workflow is unchanged."""

    body=await _json_object(request)
    domain=str(body.get("domain") or "")
    if domain not in {"general","biology","chemistry","battery_materials"}:
        raise HTTPException(status_code=422,detail="notebook domain is invalid")
    store=_report_prose_store(report_id,write=True)
    payload={"domain":domain}
    try:
        report=store.get_report(report_id)
        domain_events=[event for event in report["events"]
                       if event["event_type"]=="notebook_domain_selected"]
        latest=(domain_events[-1].get("payload") or {}).get("domain") if domain_events else None
        if latest!=domain:
            report=store.append_event(
                report_id,event_key=_notebook_event_key(
                    report_id,"notebook_domain_selected",payload,
                    sequence=len(domain_events)+1,
                ),
                event_type="notebook_domain_selected",source_tier="operator",payload=payload)
        narrative=store._narrative(report_id,None,None)
    except (ValueError,KeyError,RuntimeError,OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    return JSONResponse(status_code=200,content=jsonable_encoder({
        "report_id":report_id,"domain":narrative.facts.notebook.domain,
        "event_inserted":bool(report.get("event_inserted")),
    }),headers={"Cache-Control":"private, no-store"})


@app.post("/api/experiment-reports/{report_id}/notebook/inventory")
async def record_experiment_notebook_inventory(report_id:str,request:Request)->Response:
    """Append optional vendor/catalog/Lot facts for a source-listed item."""

    body=await _json_object(request)
    item_name=_notebook_input(body.get("item_name"),"item_name",400)
    stage=str(body.get("stage") or "")
    if stage not in {"before","after"}:
        raise HTTPException(status_code=422,detail="inventory stage is invalid")
    values={field:_notebook_input(body.get(field,""),field,200)
            for field in ("vendor","catalog_number","lot_number")}
    if not any(values.values()):
        raise HTTPException(status_code=422,detail="an inventory value is required")
    store=_report_prose_store(report_id,write=True)
    try:
        narrative=store._narrative(report_id,None,None)
        facts=narrative.facts
        assert facts is not None
        item=next((entry for entry in facts.items
                   if item_name in {entry.listed or entry.name,entry.name}),None)
        projected=next((entry for entry in facts.notebook.inventory
                        if entry.name==item_name),None)
        if item is None and projected is None:
            raise HTTPException(status_code=422,detail="inventory item is not in the source")
        payload={
            "item_kind":item.kind if item is not None else projected.kind,
            "item_name":((item.listed or item.name) if item is not None else projected.name),
            **values,"stage":stage,
        }
        report=store.get_report(report_id)
        inventory_events=[event for event in report["events"]
                          if event["event_type"]=="notebook_inventory_recorded"]
        unchanged=(projected is not None and projected.stage==stage and all(
            not value or getattr(projected,field_name)==value
            for field_name,value in values.items()
        ))
        if not unchanged:
            report=store.append_event(
                report_id,event_key=_notebook_event_key(
                    report_id,"notebook_inventory_recorded",payload,
                    sequence=len(inventory_events)+1,
                ),
                event_type="notebook_inventory_recorded",source_tier="operator",payload=payload)
        notebook=store._narrative(report_id,None,None).facts.notebook
    except HTTPException:
        raise
    except (ValueError,KeyError,RuntimeError,OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    return JSONResponse(status_code=201,content=jsonable_encoder({
        "report_id":report_id,"inventory":notebook.inventory,
        "event_inserted":bool(report.get("event_inserted")),
    }),headers={"Cache-Control":"private, no-store"})


def _notebook_sharing_response(report:Mapping[str,Any],*,inserted:bool=False)->dict[str,Any]:
    sharing=ExperimentReportStore.notebook_sharing(report)
    return {
        "report_id":report["report_id"],"shared":sharing["shared"],
        "changed_at":sharing["changed_at"],"event_inserted":inserted,
    }


@app.get("/api/experiment-reports/{report_id}/sharing")
def get_experiment_notebook_sharing(report_id:str)->Response:
    """The owner's current record-level comparison consent."""

    store=_report_prose_store(report_id,write=True)
    try:
        report=store.get_report(report_id)
        content=_notebook_sharing_response(report)
    except (ValueError,KeyError,RuntimeError,OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    return JSONResponse(content=jsonable_encoder(content),headers={"Cache-Control":"private, no-store"})


@app.post("/api/experiment-reports/{report_id}/sharing")
async def change_experiment_notebook_sharing(report_id:str,request:Request)->Response:
    """Append the owner's opt-in or withdrawal; never changes workflow state."""

    body=await _json_object(request)
    if not isinstance(body.get("shared"),bool):
        raise HTTPException(status_code=422,detail="sharing choice is invalid")
    requested=bool(body["shared"])
    store=_report_prose_store(report_id,write=True)
    try:
        report=store.get_report(report_id)
        current=store.notebook_sharing(report)
        inserted=False
        if current["shared"]!=requested:
            payload={"shared":requested}
            event_key="note-share-"+hashlib.sha256(
                f"{report_id}\x1f{current['changes']+1}\x1f{int(requested)}".encode("utf-8")
            ).hexdigest()[:32]
            report=store.append_event(
                report_id,event_key=event_key,event_type="notebook_share_changed",
                source_tier="operator",payload=payload,
            )
            inserted=bool(report.get("event_inserted"))
        content=_notebook_sharing_response(report,inserted=inserted)
    except (ValueError,KeyError,RuntimeError,OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    return JSONResponse(content=jsonable_encoder(content),headers={"Cache-Control":"private, no-store"})


def _comparison_record(
    store:ExperimentReportStore,report:Mapping[str,Any],*,mine:bool,shared_number:int,
)->dict[str,Any]:
    """A fact-only comparison projection; no owner identity or model prose."""

    narrative=store._narrative(str(report["report_id"]),None,None)
    book=narrative.facts.notebook
    started=str(report.get("started_at") or "")
    when=started.replace("T"," ")[:16]
    label=("내 기록" if mine else f"공유 기록 {shared_number}")+(f" · {when}" if when else "")
    result={
        "record_scope":"mine" if mine else "shared",
        "label":label,"started_at":started,"ended_at":report.get("ended_at"),
        "protocol_revision":report.get("protocol_revision"),
        "value_rows":[{
            "step_label":row.label,"kind":row.kind,"source":row.source,
            "actual":row.actual,"unit":row.unit,
            "source_unit":row.source_unit,"actual_unit":row.actual_unit,
        } for row in book.value_bars],
        "method_rows":[{
            "step_label":row.label,"source_text":row.source_text,
            "actual_values":row.actual_values,"started_at":row.started_at,
            "ended_at":row.ended_at,"timer":row.timer,
        } for row in book.method_rows],
        "timeline":[{
            "at":row.at,"step_label":row.step_label,"kind":row.kind,"text":row.text,
        } for row in book.timeline],
        "notes":[{
            "step_label":row.label,"at":row.at,"kind":row.kind,"text":row.shown,
        } for row in book.observations],
    }
    if mine:
        result["report_id"]=report["report_id"]
    return result


@app.get("/api/experiment-reports/{report_id}/comparisons")
def compare_experiment_notebooks(report_id:str)->Response:
    """Owner-only anchor; other records are opt-in, fact-only and read-only."""

    principal,_workspace=_commercial_workspace()
    _workspace.close()
    store=_report_prose_store(report_id,write=True)
    try:
        reports=store.notebook_comparison_reports(
            report_id,requester_principal_id=principal.principal_id,
        )
        records=[]
        shared_number=0
        for report in reports:
            mine=report.get("owner_principal_id")==principal.principal_id
            if not mine:
                shared_number+=1
            records.append(_comparison_record(
                store,report,mine=mine,shared_number=shared_number,
            ))
    except (ValueError,KeyError,RuntimeError,OSError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    return JSONResponse(content=jsonable_encoder({
        "anchor_report_id":report_id,"records":records,
        "permission":{
            "scope":"same_organization_same_protocol",
            "other_records":"opt_in_read_only",
            "withdrawal":"latest_append_only_event",
            "judgment":"none",
        },
    }),headers={"Cache-Control":"private, no-store"})


@app.get("/api/experiment-reports/{report_id}/review")
def get_experiment_report_review(report_id:str):
    """The report's values and which the researcher confirmed (lane N, decision 3)."""

    store=_report_prose_store(report_id)
    try:
        report=store.get_report(report_id)
    except (ValueError,KeyError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    items=report_review_items(report["events"])
    return {
        "report_id":report_id,"items":items,
        "unconfirmed":sum(1 for item in items if not item["confirmed"]),
        "voice_review_open":REPORT_PROSE.held(store,report_id),
    }


@app.post("/api/experiment-reports/{report_id}/review/{item_id}")
async def answer_experiment_report_review(report_id:str,item_id:str,request:Request):
    """Confirm one value on the screen, or correct it and confirm (lane N, decision 3).

    A correction is appended beside the record (decision 2) and mirrored on the
    experiment timeline when the note is there. Once every value is confirmed
    the report's prose is written again -- its one model call then.
    """

    payload=await _json_object(request)
    action=str(payload.get("action") or "")
    store=_report_prose_store(report_id,write=True)
    try:
        report=store.get_report(report_id)
    except (ValueError,KeyError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    if report["status"]=="in_progress":
        raise HTTPException(status_code=409,detail="values are confirmed once the experiment ends")
    item=next(
        (entry for entry in report_review_items(report["events"]) if entry["item_id"]==item_id),
        None,
    )
    if item is None or action not in {"confirm","correct"}:
        raise HTTPException(status_code=404,detail="report value unavailable")
    text=" ".join(str(payload.get("text") or "").split())
    desired=text if action=="correct" and text else item["text"]
    if item["confirmed"] and desired==item["text"]:
        items=report_review_items(report["events"])
        return {"report_id":report_id,"items":items,
                "unconfirmed":sum(1 for entry in items if not entry["confirmed"]),
                "prose":REPORT_PROSE.status(store,report_id)}
    # A stable basis distinguishes a later correction back to an earlier
    # value, while simultaneous/retried submissions share one event key.
    basis=item["source_key"]
    latest_fix=None
    for entry in report["events"]:
        fix=(entry.get("payload") or {}).get("record_fix") or {}
        if fix.get("target_event_key")==item["source_key"]:
            basis=entry["event_key"]
            latest_fix=fix
    if (latest_fix is not None and latest_fix.get("text_after")==desired
            and basis.startswith("screen-review-") and basis.endswith("-fix")):
        # A retry can arrive after the correction but before its confirmation.
        key=basis[:-4]
    else:
        key="screen-review-"+hashlib.sha256(
            json.dumps([item_id,basis,desired],ensure_ascii=False).encode("utf-8")).hexdigest()
    try:
        if action=="correct" and text and text!=item["text"]:
            if len(text)>800:
                raise HTTPException(status_code=422,detail="correction is too long")
            store.append_event(
                report_id,event_key=f"{key}-fix",event_type="record_corrected",
                step_label=item.get("step_label") or None,user_wording=text,
                payload={"record_fix":{
                    "target_event_key":item["source_key"],
                    "text_before":item["text"],"text_after":text,"source":"screen",
                }},
            )
            _mirror_screen_fix(report,item,text,event_key=key)
        else:
            text=item["text"]
        store.append_event(
            report_id,event_key=key,event_type=REVIEW_CONFIRMED,
            step_label=item.get("step_label") or None,user_wording=text,
            payload={"item_id":item_id,"text":text,"source":"screen"},
        )
        items=report_review_items(store.get_report(report_id)["events"])
        if items and all(entry["confirmed"] for entry in items):
            _prepare_report_prose(
                store,report_id,again=store.get_prose(report_id) is not None)
        return {
            "report_id":report_id,"items":items,
            "unconfirmed":sum(1 for entry in items if not entry["confirmed"]),
            "prose":REPORT_PROSE.status(store,report_id),
        }
    except HTTPException:
        raise
    except (ValueError,KeyError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc


def _mirror_screen_fix(report:dict,item:dict,text:str,*,event_key:str)->None:
    """A screen correction on the experiment timeline too, when the note is there."""

    target=item.get("workspace_event_key")
    if not target or not _workspace_settings().enabled:
        return
    try:
        principal,store=_commercial_workspace()
    except Exception as exc:  # noqa: BLE001 - the report keeps the correction
        log.warning("timeline correction skipped error=%s",type(exc).__name__)
        return
    try:
        store.record_observation_fix(
            principal,str(report.get("session_id") or ""),event_key=event_key,
            target_event_key=str(target),content=text,capture_source="manual",
        )
    except Exception as exc:  # noqa: BLE001 - the report keeps the correction
        log.warning("timeline correction not recorded error=%s",type(exc).__name__)
    finally:
        store.close()


def _prepared_report_prose(store:ExperimentReportStore,report_id:str)->None:
    """Let a download use the prepared prose, waiting for one being written.

    A finished report with nothing prepared (one that ended before the server
    restarted, say) is prepared now; that is still its one model call. A
    report still in progress gets the server's own sentences.
    """

    try:
        _scope_tenant_resource("experiment_report",report_id,write=True)
    except WorkspaceNotFoundError:
        return  # Administrator exports use the existing prose or deterministic text.
    if _prepare_report_prose(store,report_id)=="preparing":
        REPORT_PROSE.wait(
            store,report_id,
            ReportWriterSettings.from_environment().timeout_seconds+5,
        )


PI_COMMENT_LIMIT=300


def _pi_confirmation_body(body:Any)->str:
    """The one line a PI leaves, cleaned; 422 when longer than the box."""

    comment=" ".join(str((body or {}).get("comment") or "").split()) if isinstance(body,dict) else ""
    if len(comment)>PI_COMMENT_LIMIT:
        raise HTTPException(status_code=422,detail="pi_confirmation_comment_too_long")
    return comment


def _pi_name()->str:
    """The administrator account's name (lane ON), the notebook's "PI"."""

    account=_REQUEST_ACCOUNT.get()
    return " ".join(str(getattr(account,"name","") or "").split())[:120]


@app.get("/api/experiment-reports/{report_id}/pi-confirmations")
def get_experiment_report_pi_confirmations(report_id:str)->dict[str,object]:
    """Every "확인함" a PI left on the notebook (lane RP3, decision ④)."""

    store=_report_prose_store(report_id)
    try:
        report=store.get_report(report_id)
    except (ValueError,KeyError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    return {"report_id":report_id,"confirmations":pi_confirmations(report["events"])}


@app.post("/api/experiment-reports/{report_id}/pi-confirmations",status_code=201)
def post_experiment_report_pi_confirmation(
    report_id:str,body:dict[str,Any]=Body(...),
)->dict[str,object]:
    """An administrator account leaves "확인함" on the notebook (lane RP3,
    decision ④): the account's name, the time, one line -- an append-only
    event that blocks nothing and changes no record. Anyone else is refused.
    """

    _scope_tenant_resource("experiment_report",report_id)
    if not _account_is_admin():
        raise HTTPException(status_code=403,detail="pi_confirmation_admin_only")
    comment=_pi_confirmation_body(body)
    store=_report_prose_store(report_id)
    try:
        report=store.get_report(report_id)
    except (ValueError,KeyError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    try:
        store.append_event(
            report_id,event_key=f"pi-confirmed-{secrets.token_hex(16)}",event_type="pi_confirmed",
            user_wording=comment or None,
            payload={"name":_pi_name(),"comment":comment,"source":"screen"},
        )
        report=store.get_report(report_id)
    except (ValueError,sqlite3.Error) as exc:
        raise HTTPException(status_code=404,detail="experiment report unavailable") from exc
    return {"report_id":report_id,"confirmations":pi_confirmations(report["events"])}


@app.post("/api/protocols/{protocol_id}/review-versions/{version}/pi-confirmations",status_code=201)
def post_protocol_review_version_pi_confirmation(
    protocol_id:str,version:int,body:dict[str,Any]=Body(...),
)->dict[str,object]:
    """An administrator account leaves "확인함" on a registered review
    version (lane RP3, decision ④): an append-only event in the protocol's
    ledger; the version's record shows it; the start is not held by it."""

    if not _account_is_admin():
        raise HTTPException(status_code=403,detail="pi_confirmation_admin_only")
    comment=_pi_confirmation_body(body)
    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return catalog.confirm_review_version(protocol_id,version,name=_pi_name(),comment=comment)
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


def _report_photo_resolver(report:Mapping[str,Any])->Callable[[Any],tuple[bytes,str]|None]:
    """Resolve only this report's authenticated workspace image evidence."""

    session_id=str(report.get("session_id") or "")

    def resolve(image:Any)->tuple[bytes,str]|None:
        if getattr(image,"kind","")!="photo" or not getattr(image,"asset_id",""):
            return None
        response=download_workspace_experiment_evidence(session_id,str(image.asset_id))
        media_type=str(response.media_type or "")
        if not media_type.startswith("image/"):
            return None
        return bytes(response.body),media_type

    return resolve


@app.get("/api/experiment-reports/{report_id}.{format_name}")
def export_experiment_report(report_id:str,format_name:str):
    """Export one configured report without exposing its database location."""
    _scope_tenant_resource("experiment_report",report_id)

    try:
        settings=ExperimentReportSettings.from_environment()
        if not settings.enabled or settings.database_path is None:
            raise HTTPException(status_code=404,detail="experiment report unavailable")
        store=ExperimentReportStore(settings.database_path)
        photo_resolver=_report_photo_resolver(store.get_report(report_id))
        if format_name=="json":
            content=store.export_json(report_id)
            media_type="application/json"
        elif format_name=="md":
            # Lane RP: the Markdown report has the Word report's structure and
            # the same writer (model sections checked, server sentences else).
            _prepared_report_prose(store,report_id)
            content=store.export_markdown(report_id)
            media_type="text/markdown; charset=utf-8"
        elif format_name=="csv":
            content=store.export_csv(report_id)
            media_type="text/csv; charset=utf-8"
        elif format_name=="docx":
            _prepared_report_prose(store,report_id)
            content=store.export_docx(report_id,image_resolver=photo_resolver)
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif format_name=="pdf":
            # Lane RP2, decision 2: the Word report printed by the server's
            # LibreOffice; without it (or when it fails) the format is refused by name.
            _prepared_report_prose(store,report_id)
            try:
                content=store.export_pdf(report_id,image_resolver=photo_resolver)
            except ReportPdfError as exc:
                raise HTTPException(status_code=503,detail=exc.code) from exc
            media_type="application/pdf"
        elif format_name=="meeting-docx":
            # Lane RP3, decision ③: the one-page lab-meeting summary.
            _prepared_report_prose(store,report_id)
            content=store.export_meeting_docx(report_id)
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif format_name=="meeting-pdf":
            _prepared_report_prose(store,report_id)
            try:
                content=store.export_meeting_pdf(report_id)
            except ReportPdfError as exc:
                raise HTTPException(status_code=503,detail=exc.code) from exc
            media_type="application/pdf"
        elif format_name=="xlsx":
            # Lane RP3, decision ③: the results table.
            _prepared_report_prose(store,report_id)
            content=store.export_xlsx(report_id)
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            raise HTTPException(status_code=404,detail="experiment report unavailable")
    except HTTPException:
        raise
    except (ValueError,KeyError,RuntimeError,OSError,sqlite3.Error) as exc:
        raise HTTPException(
            status_code=404,detail="experiment report unavailable"
        ) from exc
    # Lane LOG, decision 2: the report was made (Word, Markdown) or exported.
    _pilot_mark("report_exported",report_id=report_id,code=format_name)
    return Response(
        content=content,media_type=media_type,
        headers={
            "Cache-Control":"no-store",
            "Content-Disposition":f'attachment; filename="{report_id}.{format_name.replace("meeting-", "meeting.")}"',
            "X-Content-Type-Options":"nosniff",
        },
    )


@app.get("/api/web-visuals/{asset_id}")
def get_web_visual(asset_id:str):
    """A Commons photograph the server fetched and checked (lane WV, decision 2), same-origin."""

    photo=WEB_PHOTOS.get(asset_id)
    if photo is None:
        raise HTTPException(status_code=404,detail="web_visual_unknown")
    return Response(
        content=photo.content,
        media_type=photo.mime_type,
        headers={
            "Cache-Control":"private, no-store",
            "X-Content-Type-Options":"nosniff",
            "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Content-Disposition":f'inline; filename="{photo.asset_id[:16]}"',
            "X-Web-Visual-Source":quote(photo.page_url,safe=":/?#&=%"),
            "X-Web-Visual-Licence":quote(photo.licence,safe=" "),
        },
    )


@app.get("/api/drawn-visuals/{asset_id}")
def get_drawn_visual(asset_id:str):
    """A diagram the server drew from the step's words and checked (lane WV, decision 3)."""

    diagram=DRAWN_DIAGRAMS.get(asset_id)
    if diagram is None:
        raise HTTPException(status_code=404,detail="drawn_visual_unknown")
    return Response(
        content=diagram.content,
        media_type="image/svg+xml",
        headers={
            "Cache-Control":"private, no-store",
            "X-Content-Type-Options":"nosniff",
            "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Content-Disposition":f'inline; filename="{diagram.asset_id[:16]}.svg"',
            # Header values are Latin-1: the label is in the picture itself.
            "X-Drawn-Visual-Step":quote(diagram.step_id,safe=""),
        },
    )


@app.get("/api/protocols/{protocol_id}/revisions/{revision_id}/figures/{figure_id}")
def get_protocol_source_figure(protocol_id:str,revision_id:str,figure_id:str):
    """One figure cut from the uploaded PDF, same-origin, hash-labelled (lane WV, decision 1)."""

    try:
        _scope_catalog_resource(protocol_id)
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
            raise ProtocolCatalogNotFoundError("Protocol figure is unknown.")
        found=source_figures.figure_content(
            fixture.source_pdf_path,fixture.source_pdf_sha256,figure_id)
        if found is None:
            raise ProtocolCatalogNotFoundError("Protocol figure is unknown.")
        figure,content=found
        if hashlib.sha256(content).hexdigest()!=figure.sha256:
            raise ProtocolCatalogUnavailableError("Protocol figure identity changed.")
        return Response(
            content=content,
            media_type=figure.mime_type,
            headers={
                "Cache-Control":"private, no-store",
                "X-Content-Type-Options":"nosniff",
                "Content-Security-Policy":(
                    "default-src 'none'; style-src 'unsafe-inline'; sandbox"),
                "Content-Disposition":f'inline; filename="{figure.figure_id}.png"',
                "X-Protocol-Source-SHA256":str(fixture.source_pdf_sha256),
                "X-Protocol-Asset-SHA256":figure.sha256,
                "X-Protocol-Source-Page":str(figure.source_page),
                "X-Protocol-Visual-Kind":"source_figure",
            },
        )
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get(
    "/api/protocols/{protocol_id}/revisions/{revision_id}/source-pages/{source_page}"
)
def get_protocol_source_page(
    protocol_id:str,revision_id:str,source_page:int,
):
    """Secondary same-origin exact-page view; it is never labelled a source image."""

    try:
        _scope_catalog_resource(protocol_id)
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


@app.get(
    "/api/protocols/{protocol_id}/revisions/{revision_id}/source-pages/{source_page}/image"
)
def get_protocol_source_page_image(
    protocol_id:str,revision_id:str,source_page:int,
):
    """The source page itself as one image (lane VF, decision 8: "원본 쪽 보기").

    Same-origin, rendered from the uploaded PDF by the one PDF engine and
    kept in memory only; never labelled a figure.
    """

    try:
        _scope_catalog_resource(protocol_id)
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
        png=source_figures.page_image(
            fixture.source_pdf_path,fixture.source_pdf_sha256,source_page)
        if png is None:
            raise ProtocolCatalogNotFoundError("Protocol source page is unknown.")
        return Response(
            content=png,media_type="image/png",
            headers={
                "Cache-Control":"private, no-store",
                "X-Content-Type-Options":"nosniff",
                "Content-Security-Policy":(
                    "default-src 'none'; style-src 'unsafe-inline'; sandbox"),
                "Content-Disposition":f'inline; filename="source-page-{source_page}.png"',
                "X-Protocol-Source-SHA256":str(fixture.source_pdf_sha256),
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
    "deterministic_emergency",
    "language_clarification",
    # Lane RA, decision (g): a session without a protocol has no general
    # brain any more; its turn is answered by rule.
    "no_protocol",
    # Lane FX3, decision 8: the router's model was at its request limit.
    "model_unavailable",
})
#: Lane RA, decision (d): how long a turn whose words end in an unfinished
#: Korean ending waits for the words that follow before it is routed alone
#: -- the join window, plus the time the endpoint and speech recognition
#: take to deliver them.
FRAGMENT_HOLD_SECONDS=FRAGMENT_JOIN_SECONDS+1.0
#: Lane HEAR, decision 4 (2026-10-25): speech that starts within this long
#: of the last end, before that turn's answer plays and while it has changed
#: nothing, is the same sentence: the two are joined into one utterance. The
#: brief's example value; the log of the person's test had 54 of 66 ends
#: followed by speech within it (39 within 0.5 s).
CONTINUATION_JOIN_SECONDS=1.5
#: Lane HEAR2, decision 2 (2026-10-26): how much kept sound a continuation
#: may still join to. Room noise the VAD takes for speech every few hundred
#: milliseconds kept joining without end in the person's test (101 joins,
#: 31.9 s of sound, the words inside answered only when the noise paused);
#: at this bound the turn is transcribed as it stands and the next sound is
#: a candidate of its own.
CONTINUATION_JOIN_MAX_SECONDS=10.0
FRAGMENT_HOLD_POLL_SECONDS=0.05
TURN_PROGRESS_TRANSITIONS={
    "listening":frozenset({"transcribing","cancelled","error"}),
    "transcribing":frozenset({"routing","cancelled","error"}),
    "routing":frozenset({
        "checking_protocol","checking_approved_information","composing",
        "synthesizing","cancelled","error",
    }),
    # A silent curated turn (the paused notice) has no audio, so it ends
    # straight from checking_protocol instead of through playing.
    "checking_protocol":frozenset({
        "checking_approved_information","synthesizing","complete","blocked",
        "cancelled","error",
    }),
    "checking_approved_information":frozenset({
        "composing","synthesizing","cancelled","error",
    }),
    "composing":frozenset({
        # A router answer the server could not use falls back to the rules.
        "checking_approved_information","checking_protocol","synthesizing",
        "cancelled","error",
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
                 supplemental_knowledge_settings:SupplementalKnowledgeSettings|None=None,
                 multi_brain_settings:MultiBrainSettings|None=None,
                 llm_router_settings:LlmRouterSettings|None=None,
                 web_explanation_settings:WebExplanationSettings|None=None,
                 drawn_diagram_settings:DrawnDiagramSettings|None=None)->None:
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
        self.supplemental_knowledge_settings=(
            supplemental_knowledge_settings or SupplementalKnowledgeSettings(False))
        self.multi_brain_settings=multi_brain_settings or MultiBrainSettings(False)
        #: Lane WV, decision 2: the web explanation and photograph (off by default).
        self.web_explanation_settings=web_explanation_settings or WebExplanationSettings(False)
        #: Lane WV, decision 3: a diagram drawn from the step's words (off by default).
        self.drawn_diagram_settings=drawn_diagram_settings or DrawnDiagramSettings(False)
        # Checked Korean readings of source statements, so a step read twice
        # is translated once: (fixture sha, step label, statement) -> Korean.
        self.reader_translations:dict[tuple[str,str,str],str]={}
        # Lane SAY, decision 1: readings asked for and refused (or failed),
        # so a sentence is translated once a session, never again in a turn.
        self.reader_translation_refused:set[tuple[str,str,str]]=set()
        # "자동 번역입니다." is said once a session, before the first
        # unreviewed Korean it speaks; the page carries the label after that.
        self.auto_translation_announced=False
        # Waiting-status sentences already said and made in this session.
        self.filler_memory=FillerSessionMemory()
        # Exact turns currently waiting for the answer-role model to select
        # one supplied source excerpt. The filler reads this only; it is not
        # workflow state and cannot authorize a mutation.
        self.grounded_answer_turns:set[tuple[int,int]]=set()
        # What the agent has said lately, for the echo check (lane XO, 6b):
        # (when it was synthesized, the sentence).
        self.spoken_recently:collections.deque[tuple[float,str]]=collections.deque(
            maxlen=ECHO_MEMORY_SENTENCES)
        self.last_playback_ended_at:float|None=None
        #: The lane R router (off by default): a turn the front rules hand on
        #: goes to the model, and to the rules when the model cannot be used.
        self.llm_router_settings=llm_router_settings or LlmRouterSettings()
        self.experiment_report_id:str|None=None
        #: Lane HELP: the account's earlier runs of this protocol, read once
        #: per experiment record for a problem's help; and the public source
        #: (lane KNOW: ``voice_socket`` hands it the tier loaded at start,
        #: ``app.state.public_knowledge``; None when that tier is off, and
        #: the help then skips it).
        self.problem_history:HistoryKnowledge|None=None
        self.public_knowledge:Any=None
        #: Lane ON, decision 2: the voice the person chose (None: the
        #: provider's configured voice); ``synthesize`` reads it.
        self.tts_voice:str|None=None
        #: The words that open the report's value review (lane N, decision 3),
        #: said once after the end of the experiment.
        self.report_review_intro:tuple[str,str]|None=None
        #: The source's safety statements shown to the experimenter before
        #: the press of start that opened this session (decision of
        #: 2026-10-08); written into the experiment report when it opens.
        self.safety_notices_shown:list[dict[str,Any]]|None=None
        self.session_id=new_session_id()
        self.voice_connection_id="voice-"+secrets.token_hex(16)
        self.experiment_state_version:int|None=None
        self.accepted_configuration_id:int|None=None
        self.accepted_mode:str|None=None
        self.accepted_language:str|None=None
        self.accepted_input_language=InputLanguagePreference.AUTO
        self.accepted_protocol_id:str|None=None
        self.accepted_revision_id:str|None=None
        self.turn_generations:dict[int,int]={}
        #: Lane RA, decision (d): a turn's unfinished words, held for the next.
        self._held_fragment:dict[str,Any]|None=None
        self.turn_progress:dict[tuple[int,int],TurnProgress]={}
        self.visual_tasks:set[asyncio.Task]=set()
        self.research_operations:set[tuple[int,int]]=set()
        # A turn whose audio.complete waits for an outside-PDF explanation
        # to be said after it (lane R6, decision 6): (turn, generation).
        self.held_audio_complete:set[tuple[int,int]]=set()
        # A playback.ended refused while a barge-in candidate was open: the
        # turn it ends, to be completed if the candidate is rejected.
        self._deferred_playback_end:tuple[int,int]|None=None
        self._interrupted_generations:set[tuple[int,int]]=set()
        self._cascade_vad_config=self.detector.config
        self._vad_classifier=self.detector.classifier
        self._listening_onset=self.detector.listening_onset
        # LISTEN3: production WebRTC input always qualifies transient noise.
        # A deterministic test classifier may opt in on its detector; old
        # classifier fixtures retain their exact historical expectations.
        self._transient_noise_gate=(
            self.detector.transient_noise_gate
            or isinstance(self._vad_classifier,WebRtcVadClassifier)
        )
        self._interrupt_detector=self._new_interrupt_detector()
        self._interrupt_framer=FrameBuffer()
        self._interrupt_candidate_identity:tuple[int,int]|None=None
        self._interrupt_candidate_started_at:float|None=None
        self._interrupt_candidate_endpoint_at:float|None=None
        self._interrupt_candidate_diagnostics:dict[str,int|bool]={}
        self._microphone_chunk_sequence=0
        self.greeting_emitted=False
        self.greeting_audio_ready=False
        #: Lane VT: the timer notices said so far, for their turn ids.
        self.timer_notice_turns=0
        #: Lane VF, decision 2: this connection's notice tasks -- one saying
        #: (or preparing) a notice, and apart from them one ending a said
        #: notice's playback -- cancelled when the connection ends.
        self.timer_notice_tasks:set[asyncio.Task]=set()
        self.timer_notice_playback_tasks:set[asyncio.Task]=set()
        #: Spoken notices shown and not said yet, by notice id.
        self.timer_notice_pending:dict[str,TimerNotice]={}
        #: Notices whose voice was given up: a timer's end came first.
        self.timer_notice_superseded:set[str]=set()
        #: Lane VF, decision 3: a notice's audio made before it fell due --
        #: the sentence it was made from, and its frames -- by notice id.
        self.timer_notice_audio:dict[str,tuple[str,list[bytes]]]={}
        self.timer_notice_preparing:set[str]=set()
        self.client_audio_constraints:dict[str,object]={}
        self.stt_settings=CascadeSttSettings.from_environment()
        #: Lane SP1, decision 1: the wearer's voice level measured this
        #: session (never stored), the "민감도" its margin comes from, and
        #: whether the next utterance is the sentence read to measure it.
        self.level_reference:SpeakerLevelReference|None=None
        self.speaker_sensitivity:str=DEFAULT_SENSITIVITY
        self.calibration_pending:bool=False
        #: When utterances were taken for another person's voice (the
        #: session clock), for decision 3's "시끄러운 곳" suggestion.
        self.ignored_speech_at:collections.deque[float]=collections.deque(maxlen=64)
        self.ignored_speech_count:int=0
        #: Lane SP1, decision 3: when "시끄러운 곳 모드로 바꿀까요?" was last said
        #: -- or, since lane SP2, decision 2, the noise floor's suggestion: the
        #: two share one gap of ten minutes.
        self.ambient_suggested_at:float|None=None
        #: Lane SP2, decisions 1 and 2: the room's noise floor, from every frame
        #: heard while listening (never while the agent speaks), this session
        #: only: whether the level may end utterances, and when to say the
        #: room is loud.
        self.noise_floor=NoiseFloor()
        #: Whether the level ends utterances now; logged when it changes.
        self.level_endpoint_on=False
        #: Turns of the server's own words (a calibration prompt, a
        #: suggestion), numbered from _SERVER_WORDS_TURN_BASE.
        self.server_words_turns:int=0
        #: Lane HEAR, decision 3: how many voices in a row the filter dropped
        #: just under its line. (Lane HEAR2, decision 4: a drop is shown,
        #: never said, so no drop is counted toward a spoken notice.)
        self.near_line_drops:int=0
        #: Lane HEAR2: whether the provider outage under way was said aloud;
        #: a response that comes back ends the outage.
        self.stt_failure_said:bool=False
        #: Lane STT4: a 400/404 from Google V2 inline adaptation disables
        #: hints for the rest of this voice session only.
        self.google_v2_adaptation_disabled:bool=False
        #: Lane HEAR, decision 4: the last committed turn's audio (for a
        #: continuation to join) and events of a turn opened between chunks.
        self._joinable_turn:dict[str,Any]|None=None
        self._pending_listener_events:list[ListenerEvent]=[]
    @property
    def state(self): return self.detector.state
    def remember_spoken(self,text:str)->None:
        """Keep a sentence the agent is about to say, for the echo check."""

        if text and text.strip():
            self.spoken_recently.append((self.clock(),text.strip()))
    def own_speech_echo(
        self,transcript:str,*,heard_from:float|None=None,
    )->OwnSpeechEcho|None:
        """The agent's own sentence ``transcript`` repeats, or None.

        ``heard_from`` is when an ordinary turn's speech began; it is an echo
        only if that was while playback could still be heard. A barge-in
        candidate (``heard_from`` None) is heard during playback by
        definition. Short transcripts, and ones with a pause or end word the
        sentence lacks, are never an echo.
        """

        if heard_from is not None and (
            self.last_playback_ended_at is None
            or heard_from>self.last_playback_ended_at+ECHO_TAIL_SECONDS
        ):
            return None
        if len(echo_key(transcript))<ECHO_MIN_CHARACTERS:
            return None
        now=self.clock()
        best:OwnSpeechEcho|None=None
        for said_at,spoken in self.spoken_recently:
            if now-said_at>ECHO_MEMORY_SECONDS:
                continue
            if control_words(transcript)-control_words(spoken):
                continue
            overlap=echo_overlap(transcript,spoken)
            if overlap>=ECHO_OVERLAP_THRESHOLD and (best is None or overlap>best.overlap):
                best=OwnSpeechEcho(spoken,overlap)
        return best
    def _new_interrupt_detector(self,*,playback:bool=False)->EndpointDetector:
        config=self._cascade_vad_config
        if playback:
            config=replace(
                config,
                onset_voiced_frames=config.playback_onset_voiced_frames,
                onset_window_frames=config.playback_onset_window_frames,
                prefix_frames=config.barge_in_prefix_frames,
            )
        detector=EndpointDetector(
            config,
            classifier=self._vad_classifier,
            listening_onset=False,
        )
        detector.enable_transient_noise_gate(
            self._transient_noise_gate
            and detector.classifier is self._vad_classifier
        )
        return detector
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
        self._joinable_turn=None
        self._pending_listener_events=[]
        for task in tuple(self.visual_tasks):
            task.cancel()
        self.visual_tasks.clear()
        self.research_operations.clear()
        self.held_audio_complete.clear()
        self._deferred_playback_end=None
        self.turn_generations.clear()
        self.turn_progress.clear()
        self._interrupted_generations.clear()
        self._reset_interrupt_input()
    def begin_research(self,turn_id:int,generation:int)->None:
        self.research_operations.add((turn_id,generation))
    def finish_research(self,turn_id:int,generation:int)->bool:
        identity=(turn_id,generation)
        if identity not in self.research_operations:return False
        self.research_operations.remove(identity)
        return True
    def owns_visual_result(
        self,turn_id:int,generation:int,configuration_id:int|None,
        protocol_id:str,
    )->bool:
        return bool(
            self.active
            and self.accepted_configuration_id==configuration_id
            and self.accepted_protocol_id==protocol_id
            and self.turn_generations.get(turn_id)==generation
        )
    def owns_research_result(
        self,turn_id:int,generation:int,configuration_id:int|None=None,
    )->bool:
        return bool(
            self.active
            and (configuration_id is None or self.accepted_configuration_id==configuration_id)
            and self.turn_generations.get(turn_id)==generation
        )
    def track_visual_task(self,task:asyncio.Task)->None:
        self.visual_tasks.add(task)
        task.add_done_callback(self.visual_tasks.discard)
    def track_timer_notice_task(self,task:asyncio.Task,*,playback:bool=False)->None:
        tasks=self.timer_notice_playback_tasks if playback else self.timer_notice_tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    def forget_timer_notices(self)->None:
        """Drop what is kept for notices not said yet; their tasks see the session changed."""

        self.timer_notice_pending.clear(); self.timer_notice_superseded.clear()
        self.timer_notice_audio.clear(); self.timer_notice_preparing.clear()
    # --- lane SP1, decision 1: the wearer's voice level ---------------------
    def forget_level_reference(self)->None:
        """A new session measures again (decision 1: never stored)."""

        self.level_reference=None
        self.calibration_pending=False
        self.ignored_speech_at.clear()
        self.ignored_speech_count=0
        self.ambient_suggested_at=None
        self.noise_floor.clear()
        self.level_endpoint_on=False
    def set_speaker_sensitivity(self,sensitivity:str)->str:
        """Take the "민감도" setting; a reference already measured keeps its level."""

        chosen=sensitivity if sensitivity in SENSITIVITY_MARGINS_DB else DEFAULT_SENSITIVITY
        self.speaker_sensitivity=chosen
        if self.level_reference is not None:
            self.level_reference=self.level_reference.with_sensitivity(chosen)
        return chosen
    def arm_voice_calibration(self)->None:
        """The next utterance is the sentence read to measure the wearer's level."""

        self.calibration_pending=True
    def set_level_reference(self,level_db:float)->SpeakerLevelReference:
        """Measure the wearer at ``level_db`` (the calibration sentence's median voiced level)."""

        self.level_reference=level_reference_from(level_db,self.speaker_sensitivity)
        self.calibration_pending=False
        return self.level_reference
    def ignores_level(self,level_db:float|None)->bool:
        """Whether an utterance this loud is another person's voice (decision 1).

        Only with a reference measured, and never for the calibration
        sentence itself, which may be read at any level.
        """

        return (
            self.level_reference is not None
            and not self.calibration_pending
            and not self.level_reference.accepts(level_db)
        )
    def room_near_the_line(self)->bool|None:
        """Whether the room's noise floor is too near the wearer's line to part them (lane SP2).

        The line is the gate's, reference minus margin. A floor less than
        LEVEL_ENDPOINT_FLOOR_GAP_DB under it means the room's own voice
        crosses it: the level can no longer tell where the wearer stopped.
        None without a reference, while the calibration sentence is
        awaited, and before enough of the room was heard.
        """

        floor=self.noise_floor.level_db()
        if self.level_reference is None or self.calibration_pending or floor is None:
            return None
        return floor>self.level_reference.threshold_db-LEVEL_ENDPOINT_FLOOR_GAP_DB
    def room_keeps_the_vad_on(self)->bool:
        """Whether the room itself keeps the VAD hearing a voice (lane SP2, decision 1).

        Of what was heard under the wearer's line in the floor's window, at
        least ROOM_VOICED_SHARE voiced: babble, a pump, TV talk. A quiet room
        or a fan is not -- there the VAD finds the silence itself, and a
        quieter phrase of the wearer's own must not end the words.
        """

        share=(self.noise_floor.voiced_share_below(self.level_reference.threshold_db)
               if self.level_reference is not None else None)
        return share is not None and share>=ROOM_VOICED_SHARE
    def endpoint_threshold_db(self)->float|None:
        """The line an utterance's end is also found by (lane SP2, decision 1).

        Once the wearer's level was heard, staying under the line for the
        endpoint time while the VAD keeps hearing a voice ends the utterance.
        Only in a room that keeps the VAD on (``room_keeps_the_vad_on``) and
        sits well under the line (``room_near_the_line`` False): nearer, the
        level would cut the room's own voice into turns. Otherwise the VAD
        alone ends utterances, as before -- the same as without a reference,
        while the calibration sentence is awaited, and before the room was
        heard.
        """

        if self.room_near_the_line() is not False or not self.room_keeps_the_vad_on():
            return None
        assert self.level_reference is not None
        return self.level_reference.threshold_db
    def _apply_endpoint_threshold(self,detector:EndpointDetector)->None:
        """Give ``detector`` the two lines, and log when the level starts or stops ending utterances.

        The wearer's words begin at the wearer's own level, the reference
        less half the margin, and end under the gate's line, the reference
        less the margin: a voice between the two -- a neighbour, a TV --
        never starts the level's end (lane SP2, decision 1). Only between
        utterances: one already begun keeps the lines it began with, so a
        floor wavering at the guard cannot change them in mid-sentence.
        """

        # Lane HEAR2: without the optional wearer filter, a turn-local
        # relative gate keeps low continuous room sound from resetting the
        # 1000 ms endpoint forever.  Calibration and an explicit wearer line
        # retain their existing measured behaviour.
        detector.enable_transient_noise_gate(
            self._transient_noise_gate
            and detector.classifier is self._vad_classifier
        )
        detector.enable_adaptive_noise_gate(
            self.level_reference is None and not self.calibration_pending)
        if detector.state is not TurnState.IDLE:
            return
        threshold=self.endpoint_threshold_db()
        detector.wearer_threshold_db=threshold
        detector.wearer_onset_db=(
            self.level_reference.reference_db-self.level_reference.margin_db/2
            if threshold is not None and self.level_reference is not None else None)
        if (threshold is not None)!=self.level_endpoint_on:
            self.level_endpoint_on=threshold is not None
            floor=self.noise_floor.level_db()
            log.info(
                "level endpoint %s threshold_db=%s onset_db=%s floor_db=%s",
                "on" if self.level_endpoint_on else "off",
                round(threshold,1) if threshold is not None else None,
                round(detector.wearer_onset_db,1) if detector.wearer_onset_db is not None else None,
                round(floor,1) if floor is not None else None)
    def _ignored_speech_event(
        self,kind:str,turn_id:int,result:EndpointResult,generation:int|None,
        *,during_playback:bool,
    )->ListenerEvent:
        """The event an utterance taken for another person's voice becomes."""

        assert self.level_reference is not None
        now=self.clock()
        self.ignored_speech_at.append(now)
        self.ignored_speech_count+=1
        return ListenerEvent(
            kind,turn_id,
            replace(result,utterance=None),
            generation,reason="other_speaker_level",
            diagnostics={
                "level_db":round(result.voiced_level_db,1) if result.voiced_level_db is not None else -100.0,
                "reference_db":round(self.level_reference.reference_db,1),
                "margin_db":round(self.level_reference.margin_db,1),
                "sensitivity":self.level_reference.sensitivity,
                "during_playback":during_playback,
                "ignored_count":self.ignored_speech_count,
            })
    # --- lane HEAR, decision 3: no sound dropped in silence ------------------
    def note_stt_failure(self)->bool:
        """A provider failure; True the first time in an outage (lane HEAR2)."""

        if self.stt_failure_said:
            return False
        self.stt_failure_said=True
        return True
    def note_stt_ok(self)->None:
        """The provider answered: the outage, if one was under way, is over."""

        self.stt_failure_said=False
    def note_stt_adaptation_fallback(self)->bool:
        """Remember Google V2's rejected hints; True only on the transition."""

        if self.google_v2_adaptation_disabled:
            return False
        self.google_v2_adaptation_disabled=True
        return True
    def note_heard(self)->None:
        """Words reached routing: the drops before them no longer count."""

        self.near_line_drops=0
    def note_level_drop(self,level_db:float|None)->str|None:
        """A voice the filter dropped: "lowered", "off" or None (lane HEAR, decision 3).

        A voice within NEAR_LINE_DB under the line is where the wearer's own
        quieter words fall; NEAR_LINE_DROPS of them in a row move the line to
        the "낮음" margin, or, already there, turn the filter off for this
        session. A voice further under it is someone else's and resets the count.
        """

        reference=self.level_reference
        if reference is None or level_db is None:
            return None
        if level_db<reference.threshold_db-NEAR_LINE_DB:
            self.near_line_drops=0
            return None
        self.near_line_drops+=1
        if self.near_line_drops<NEAR_LINE_DROPS:
            return None
        self.near_line_drops=0
        if reference.margin_db<SENSITIVITY_MARGINS_DB["low"]:
            self.level_reference=reference.with_sensitivity("low")
            self.speaker_sensitivity="low"
            return "lowered"
        self.level_reference=None
        self.level_endpoint_on=False
        return "off"
    def forget_drops(self)->None:
        self.near_line_drops=0
    def ignored_speech_in_last(self,seconds:float)->int:
        """How many utterances were taken for another person's in the last ``seconds``."""

        now=self.clock()
        return sum(1 for at in self.ignored_speech_at if now-at<=seconds)
    def noisy_mode_suggestion_due(self)->bool:
        """Whether to suggest 시끄러운 곳 mode now (lane SP1, decision 3).

        Three or more voices taken for other people's within a minute, the
        room still "quiet", no suggestion open, and none made in the last
        ten minutes. Suggested only; the experimenter's answer changes it.
        """

        curated=self.curated_protocol_session
        if curated is None or getattr(curated,"ambient_mode","quiet")!="quiet":
            return False
        if getattr(curated,"ambient_suggestion_open",False):
            return False
        if self.ignored_speech_in_last(AMBIENT_SUGGESTION_WINDOW_SECONDS)<AMBIENT_SUGGESTION_IGNORED_COUNT:
            return False
        if (
            self.ambient_suggested_at is not None
            and self.clock()-self.ambient_suggested_at<AMBIENT_SUGGESTION_GAP_SECONDS
        ):
            return False
        return True
    def noise_floor_suggestion_due(self)->bool:
        """Whether to say the room is too loud for the level line (lane SP2, decision 2).

        The noise floor near the wearer's measured level -- the point where
        the level stops ending utterances (``room_near_the_line``) -- the
        room still "quiet", no suggestion open, and none, this one or lane
        SP1's, in the last ten minutes. Only with a reference measured: a
        session that measured nothing has no level to be near. Suggested
        only; nothing changes.
        """

        curated=self.curated_protocol_session
        if curated is None or getattr(curated,"ambient_mode","quiet")!="quiet":
            return False
        if getattr(curated,"ambient_suggestion_open",False):
            return False
        if (
            self.ambient_suggested_at is not None
            and self.clock()-self.ambient_suggested_at<AMBIENT_SUGGESTION_GAP_SECONDS
        ):
            return False
        return self.room_near_the_line() is True
    def start(self,experiment_session_id:str|None=None):
        self.generation+=1
        self.greeting_emitted=False
        self.greeting_audio_ready=False
        self.client_audio_constraints={}
        self.active=True; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self._restore_primary_detector(TurnState.IDLE)
        self.history.reset()
        self.last_confirmed_language=None
        self.google_v2_adaptation_disabled=False
        self.turn_committed_at.clear(); self.playback_completion_metrics.clear()
        self._reset_turn_identity()
        self.session_id=experiment_session_id or new_session_id()
        self.experiment_state_version=None
        self.experiment_report_id=None
        self.forget_timer_notices()
        self.forget_level_reference()
        self.forget_drops()
        if self.curated_protocol_session is not None:
            self.curated_protocol_session.reset()
    def stop(self):
        self.generation+=1
        self.active=False; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self._restore_primary_detector(TurnState.IDLE)
        self.history.reset()
        self.last_confirmed_language=None
        self.google_v2_adaptation_disabled=False
        self.turn_committed_at.clear(); self.playback_completion_metrics.clear()
        self.accepted_configuration_id=None; self.accepted_mode=None
        self.accepted_language=None; self.accepted_protocol_id=None
        self.accepted_input_language=InputLanguagePreference.AUTO
        self.accepted_revision_id=None
        self.safety_notices_shown=None
        self.greeting_audio_ready=False
        self.client_audio_constraints={}
        self._reset_turn_identity()
        self.forget_timer_notices()
        self.forget_level_reference()
        if self.curated_protocol_session is not None:
            # A review the researcher did not finish goes to the screen, and
            # the report's prose is prepared (lane N, decision 3).
            _close_report_review(self,source="session_closed")
            self.report_review_intro=None
            self.curated_protocol_session.reset()
    def accept_configuration(
        self,configuration_id:int,mode:str,language:str,
        protocol_id:str|None,revision_id:str|None=None,
        input_language:InputLanguagePreference|str=InputLanguagePreference.KOREAN,
    )->None:
        """Record only the exact non-secret configuration accepted by the server."""
        self.accepted_configuration_id=configuration_id
        self.accepted_mode=mode
        self.accepted_language=language
        self.accepted_input_language=normalize_input_language_preference(
            input_language
        )
        self.accepted_protocol_id=protocol_id
        self.accepted_revision_id=revision_id
    def set_curated_protocol_fixture(
        self,fixture:CuratedProtocolFixture|None,
    )->None:
        self.curated_protocol_session=(
            CuratedProtocolSession(fixture) if fixture is not None else None)
        if self.curated_protocol_session is not None:
            # Lane HELP: the protocol's sentences are indexed now, off the
            # turn path, so a problem's first help does not wait for it.
            threading.Thread(
                target=_warm_problem_knowledge,args=(self.curated_protocol_session,),
                name="problem-knowledge-index",daemon=True,
            ).start()
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
        if (
            self.curated_protocol_session is not None
            and getattr(self.curated_protocol_session,"experiment_ended",False)
        ):
            return []
        self._microphone_chunk_sequence+=1
        # Lane HEAR, decision 4: a turn opened outside this method (speech
        # kept when the turn before it was dropped) is announced here.
        output=self._pending_listener_events; self._pending_listener_events=[]
        self.refresh_cooldown()
        if self.state in (TurnState.PROCESSING,TurnState.AGENT_SPEAKING):
            return output+self._accept_interrupt_chunk(chunk)
        if self.state not in (TurnState.IDLE,TurnState.USER_SPEAKING):return output
        self._apply_endpoint_threshold(self.detector)
        output.extend(self._accept_primary_frames(self.framer.push(chunk)))
        return output
    def _accept_primary_frames(self,frames:list[bytes])->list[ListenerEvent]:
        output=[]
        for frame in frames:
            result=self.detector.process(frame)
            if self.detector.last_frame_level_db is not None:
                self.noise_floor.add(self.detector.last_frame_level_db,self.detector.last_frame_voiced)
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
            elif result.utterance is not None and self.ignores_level(result.voiced_level_db):
                # Lane SP1, decision 1: quieter than the wearer by more than
                # the margin -- another person's voice. Not sent to speech
                # recognition, not kept; the session listens again.
                output.append(self._ignored_speech_event(
                    "speech.ignored",self.active_turn_id or 0,result,
                    self.turn_generations.get(self.active_turn_id or 0,self.generation),
                    during_playback=False))
                self.active_turn_id=None; self.framer=FrameBuffer()
                self._restore_primary_detector(TurnState.COOLDOWN)
                self._reset_interrupt_input()
                self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000
            elif result.utterance is not None:
                if self.active_turn_id is None: raise RuntimeError("committed utterance has no turn_id")
                self.endpoint_at=self.clock()
                self.turn_committed_at[self.active_turn_id]=self.endpoint_at
                self._keep_turn_audio(
                    self.active_turn_id,self.turn_generations[self.active_turn_id],result.utterance)
                output.append(ListenerEvent(
                    "speech.end",self.active_turn_id,result,
                    self.turn_generations[self.active_turn_id]))
                self.framer=FrameBuffer(); self._reset_interrupt_input()
            if self.state not in (TurnState.IDLE,TurnState.USER_SPEAKING): break
        return output
    # --- lane HEAR, decision 4: a sentence with a pause in it ------------------
    def _workflow_revision(self)->tuple[Any,Any]:
        """What changes when a turn changes something: the run's revision and its record's version."""

        curated=self.curated_protocol_session
        return (getattr(curated,"_revision",None) if curated is not None else None,
                self.experiment_state_version)
    def _keep_turn_audio(self,turn_id:int,generation:int,pcm:bytes)->None:
        """The last committed turn's audio and the run's revision then, for a continuation to join."""

        self._joinable_turn={"identity":(turn_id,generation),"pcm":bytes(pcm),
                             "revision":self._workflow_revision()}
    def _join_continuation(
        self,identity:tuple[int,int],detector:EndpointDetector,framer:FrameBuffer,
        result:EndpointResult,
    )->list[ListenerEvent]|None:
        """Join speech that began soon after the last end to it (decision 4); None to interrupt as before.

        Only while the turn is still being answered (not playing), within
        CONTINUATION_JOIN_SECONDS of its end (FRAGMENT_JOIN_SECONDS when its
        words were held as unfinished or are still being written down), and
        when it has changed nothing --
        the run's revision and its record's version are as they were. The
        turn is cancelled; its audio goes in front of the words now under
        way, which carry on as the listener's own utterance of a new turn.
        """

        kept=self._joinable_turn
        if self.state is not TurnState.PROCESSING or kept is None or kept["identity"]!=identity:
            return None
        committed=self.turn_committed_at.get(identity[0])
        if committed is None:
            return None
        if len(kept["pcm"])>=CONTINUATION_JOIN_MAX_SECONDS*32000:
            # Lane HEAR2, decision 2: the chain ends here; the kept turn is
            # transcribed as it stands.
            log.info(
                "speech.continuation_bound turn_id=%s kept_ms=%d",
                identity[0],len(kept["pcm"])//32)
            return None
        held=self._held_fragment
        progress=self.turn_progress.get(identity)
        window=(
            FRAGMENT_JOIN_SECONDS
            if (held is not None and (held["turn_id"],held["generation"])==identity)
            # Still being written down: whether its words end unfinished is
            # not known yet, and nothing has been done with them.
            or (progress is not None and progress.state in {"listening","transcribing"})
            else CONTINUATION_JOIN_SECONDS)
        gap=self.clock()-committed
        if gap>window or kept["revision"]!=self._workflow_revision():
            return None
        self._interrupted_generations.add(identity)
        self._held_fragment=None
        self._joinable_turn=None
        self.generation+=1
        joined_turn_id=self.next_turn_id; self.next_turn_id+=1
        self.active_turn_id=joined_turn_id
        self.turn_generations[joined_turn_id]=self.generation
        pcm=kept["pcm"]
        detector.prepend_utterance(
            pcm[i:i+FRAME_BYTES] for i in range(0,len(pcm)-FRAME_BYTES+1,FRAME_BYTES))
        self.detector=detector
        self.framer=framer
        self._interrupt_detector=self._new_interrupt_detector()
        self._interrupt_framer=FrameBuffer()
        self._interrupt_candidate_identity=None
        self._interrupt_candidate_started_at=None
        self._interrupt_candidate_endpoint_at=None
        self._interrupt_candidate_diagnostics={}
        log.info(
            "speech.continued turn_id=%s joined_turn_id=%s gap_ms=%d joined_ms=%d",
            identity[0],joined_turn_id,round(gap*1000),len(pcm)//32)
        return [
            ListenerEvent(
                "assistant.interrupted",identity[0],result,identity[1],
                joined_turn_id,self.generation,reason="continued_speech"),
            ListenerEvent("speech.start",joined_turn_id,result,self.generation),
        ]
    def interrupt_candidate_open(self,turn_id:int,generation:int)->bool:
        """Whether words that began during this turn are still being heard or checked."""

        return self._interrupt_candidate_identity==(turn_id,generation)
    def _promote_open_candidate(self)->bool:
        """Speech under way when its turn is dropped becomes a turn of its own (decision 4).

        The turn before it wrote nothing (or was otherwise dropped); the words
        that began while it was being answered are kept, not thrown away with it.
        """

        detector=self._interrupt_detector
        if self._interrupt_candidate_identity is None or detector.state is not TurnState.USER_SPEAKING:
            return False
        turn_id=self.next_turn_id; self.next_turn_id+=1
        self.active_turn_id=turn_id
        self.turn_generations[turn_id]=self.generation
        self.detector=detector
        self.framer=self._interrupt_framer
        self._interrupt_detector=self._new_interrupt_detector()
        self._interrupt_framer=FrameBuffer()
        self._interrupt_candidate_identity=None
        self._interrupt_candidate_started_at=None
        self._interrupt_candidate_endpoint_at=None
        self._interrupt_candidate_diagnostics={}
        self._pending_listener_events.append(ListenerEvent(
            "speech.start",turn_id,EndpointResult(speech_started=True,
                                                  voiced_frames=detector.voiced_frames,
                                                  total_frames=detector.buffered_frames),
            self.generation))
        log.info("speech.kept_after_drop turn_id=%s",turn_id)
        return True
    def _accept_interrupt_chunk(self,chunk:bytes)->list[ListenerEvent]:
        output=[]
        detector=self._interrupt_detector
        framer=self._interrupt_framer
        self._apply_endpoint_threshold(detector)
        frames=framer.push(chunk)
        for index,frame in enumerate(frames):
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
                joined=self._join_continuation(identity,detector,framer,result)
                if joined is not None:
                    # The rest of this chunk is the joined utterance's.
                    output.extend(joined)
                    output.extend(self._accept_primary_frames(frames[index+1:]))
                    return output
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
                if self.ignores_level(result.voiced_level_db):
                    # Lane SP1, decision 1: another person's voice while the
                    # agent speaks or thinks -- playback goes on, nothing is
                    # transcribed.
                    rejected=replace(
                        result,utterance=None,rejected=True,
                        rejection_reason="other_speaker_level")
                    output.append(ListenerEvent(
                        "barge_in_rejected",candidate[0],rejected,candidate[1],
                        reason="other_speaker_level",
                        diagnostics=dict(self._interrupt_candidate_diagnostics)))
                    output.append(self._ignored_speech_event(
                        "speech.ignored",candidate[0],result,candidate[1],
                        during_playback=self.state==TurnState.AGENT_SPEAKING))
                    self._reset_interrupt_input(
                        playback=self.state==TurnState.AGENT_SPEAKING)
                    break
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
        reason:str="confirmed_speech",
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
        if event.result.utterance:
            self._keep_turn_audio(superseding_turn_id,self.generation,event.result.utterance)
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
                reason=reason,latency_ms=latency,
                diagnostics=diagnostics),
            ListenerEvent(
                "barge_in_committed",event.turn_id,event.result,
                event.generation,superseding_turn_id,self.generation,
                reason=reason,latency_ms=latency,
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
        if self._interrupt_candidate_identity is not None:
            # Lane HEAR, decision 4: words begun while the answer was being
            # prepared -- still heard, or being checked -- are not cut or
            # lost when it starts playing; they end as the interruption they are.
            return True
        self._reset_interrupt_input(playback=True)
        return True
    def playback_ended(self,turn_id:int)->bool:
        if self.state!=TurnState.AGENT_SPEAKING or turn_id!=self.active_turn_id:return False
        turn_gen=self.turn_generations.get(turn_id,self.generation)
        if (turn_id,turn_gen) in self._interrupted_generations:
            return False
        if self._interrupt_candidate_identity is not None:
            # Lane R6, decision 5: kept, so a rejected candidate still ends
            # this turn "complete" instead of leaving it playing forever.
            self._deferred_playback_end=(turn_id,turn_gen)
            return False
        self._deferred_playback_end=None
        received_at=self.clock()
        self.last_playback_ended_at=received_at
        committed_at=self.turn_committed_at.get(turn_id)
        self._restore_primary_detector(TurnState.COOLDOWN)
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000
        self.framer=FrameBuffer()
        self._reset_interrupt_input()
        if committed_at is not None and turn_id not in self.playback_completion_metrics:
            self.playback_completion_metrics[turn_id]=max(
                0,round((received_at-committed_at)*1000))
        return True
    def resume_deferred_playback_end(self,turn_id:int,generation:int)->bool:
        """Accept the playback.ended a rejected candidate held back, once."""

        if self._deferred_playback_end!=(turn_id,generation):return False
        self._deferred_playback_end=None
        return self.playback_ended(turn_id)
    def playback_completion_ms(self,turn_id:int)->int|None:
        return self.playback_completion_metrics.get(turn_id)
    def cascade_failed(self,turn_id:int):
        if turn_id==self.active_turn_id:
            self.active_turn_id=None; self.framer=FrameBuffer()
            self._restore_primary_detector(TurnState.IDLE)
            self._reset_interrupt_input()
    # --- Lane RA, decision (d): a fragment held for the words that follow ---------
    def hold_fragment(self,turn_id:int,generation:int,text:str,*,ended_at:float)->None:
        """Keep a turn's unfinished words for the turn that follows."""
        self._held_fragment={
            "turn_id":turn_id,"generation":generation,
            "text":" ".join(text.split()),"ended_at":float(ended_at),
        }
    def held_fragment(self)->dict[str,Any]|None:
        held=getattr(self,"_held_fragment",None)
        return dict(held) if held else None
    def release_fragment(self,turn_id:int,generation:int)->bool:
        """Let the fragment's own turn route its words: True when it is still this turn's."""
        held=getattr(self,"_held_fragment",None)
        if held is None:
            return True
        if held["turn_id"]!=turn_id or held["generation"]!=generation:
            return False
        self._held_fragment=None
        return True
    def take_held_fragment(
        self,turn_id:int,generation:int,*,speech_started_at:float|None,
    )->str|None:
        """The fragment a later turn joins, when its words started within the window."""
        held=getattr(self,"_held_fragment",None)
        if held is None:
            return None
        self._held_fragment=None
        if turn_id<=held["turn_id"]:
            return None
        if speech_started_at is not None and speech_started_at-held["ended_at"]>FRAGMENT_JOIN_SECONDS:
            return None
        return str(held["text"])
    def reject_empty_transcript(self,turn_id:int)->bool:
        if self.state!=TurnState.PROCESSING or turn_id!=self.active_turn_id:return False
        if self._promote_open_candidate():
            return True
        self.active_turn_id=None; self.framer=FrameBuffer()
        self._restore_primary_detector(TurnState.COOLDOWN)
        self._reset_interrupt_input()
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000; return True
    def stop_turn_for_pause(self)->tuple[int,int]|None:
        """[일시정지] pressed while a turn was answering: it ends here (lane HEAR, decision 7).

        Its playback is stopped by the page (``playback.stop``); the session
        listens again after the cooldown. None when nothing was answering.
        """

        turn_id=self.active_turn_id
        if turn_id is None or self.state not in (TurnState.PROCESSING,TurnState.AGENT_SPEAKING):
            return None
        generation=self.turn_generations.get(turn_id,self.generation)
        self._interrupted_generations.add((turn_id,generation))
        self._deferred_playback_end=None
        self.active_turn_id=None; self.framer=FrameBuffer()
        self._restore_primary_detector(TurnState.COOLDOWN)
        self._reset_interrupt_input()
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000
        return turn_id,generation
    def complete_without_playback(self,turn_id:int)->bool:
        if self.state!=TurnState.PROCESSING or turn_id!=self.active_turn_id:return False
        if self._promote_open_candidate():
            return True
        self.active_turn_id=None; self.framer=FrameBuffer()
        self._restore_primary_detector(TurnState.COOLDOWN)
        self._reset_interrupt_input()
        self.cooldown_until=self.clock()+self.detector.config.cooldown_ms/1000; return True
    def start_chosen_turn(self)->int|None:
        """Open the next turn for a candidate tapped on the screen (lane FX3, decision 4).

        As a committed utterance opens one -- the next turn id, this
        session's generation, committed now -- so the words then run
        exactly where an utterance's words run. Only while listening with
        nothing begun; None otherwise (speaking, answering, cooling down).
        """

        self.refresh_cooldown()
        if not self.active or self.state is not TurnState.IDLE:
            return None
        turn_id=self.next_turn_id; self.next_turn_id+=1
        self.active_turn_id=turn_id
        self.turn_generations[turn_id]=self.generation
        self.endpoint_at=self.clock()
        self.turn_committed_at[turn_id]=self.endpoint_at
        self.framer=FrameBuffer(); self._reset_interrupt_input()
        self.detector.reset(TurnState.PROCESSING)
        return turn_id


STT_KOREAN_COMMAND_KEYTERMS:tuple[str,...]=(
    "다음","타이머","일시정지","다시 시작","종료",
    "특이사항","메모","기록해 줘","화면 크게",
)
STT_EXISTING_WORKFLOW_KEYTERMS:tuple[str,...]=(
    "아니","네","현재 단계","이번 단계","완료","완료했어",
    "시작","다음 단계","다시 알려줘",*STT_CONTROL_KEYTERMS,
)
STT_KEYTERM_LIMIT=100
STT_KEYTERM_MAX_CHARS=49
STT_KEYTERM_MAX_WORDS=5
_STT_HANGUL=re.compile(r"[가-힣]")


def _stored_korean_stt_keyterms(curated:Any)->tuple[str,...]:
    """Checked Korean names stored with this revision's translations."""

    fixture=getattr(curated,"fixture",None)
    if isinstance(fixture,CuratedProtocolFixture):
        values=(
            name for name,_entity
            in curated._protocol_vocabulary().korean_names
        )
    else:
        raw=[]
        for pair in getattr(fixture,"translation_glossary",()) or ():
            if not isinstance(pair,(tuple,list)) or len(pair)!=2:
                continue
            korean=pair[1]
            if not isinstance(korean,str):
                continue
            raw.append(re.sub(r"\([^()]*\)"," ",korean))
            raw.extend(re.findall(r"\(([^()]*)\)",korean))
        values=iter(raw)
    return tuple(dict.fromkeys(
        term for value in values
        if isinstance(value,str)
        and (term:=" ".join(value.split()))
        and _STT_HANGUL.search(term)
        and len(term)<=STT_KEYTERM_MAX_CHARS
        and len(term.split())<=STT_KEYTERM_MAX_WORDS
    ))


def _cascade_stt_keyterms(curated:Any)->tuple[str,...]:
    try:
        source=curated.stt_keyterms(include_control_terms=True)
    except TypeError:
        source=curated.stt_keyterms()
    step_tokens=()
    fixture=getattr(curated,"fixture",None)
    index=getattr(curated,"current_index",None)
    if (getattr(curated,"active",False)
            and isinstance(index,int)
            and 0<=index<len(getattr(fixture,"steps",()))):
        label=getattr(fixture.steps[index],"source_label",None)
        if isinstance(label,str) and label:
            step_tokens=(
                f"{label}단계",f"{label} 단계",
                f"현재 {label}단계",f"이번 {label}단계",
            )
    ordered=(
        *STT_KOREAN_COMMAND_KEYTERMS,
        *_stored_korean_stt_keyterms(curated),
        *step_tokens,
        *STT_EXISTING_WORKFLOW_KEYTERMS,
        *source,
    )
    return tuple(dict.fromkeys(
        term for value in ordered
        if isinstance(value,str)
        and (term:=" ".join(value.split()))
        and len(term)<=STT_KEYTERM_MAX_CHARS
        and len(term.split())<=STT_KEYTERM_MAX_WORDS
    ))[:STT_KEYTERM_LIMIT]


def cascade_transcription_context(
    session:ListenerSession,
    *,
    audio_origin:str,
) -> CascadeTranscriptionContext:
    """Build the exact shared request policy for every Cascade speech origin."""

    curated=session.curated_protocol_session
    step_id=None
    pending_frame=None
    keyterms:tuple[str,...]=()
    if curated is not None:
        keyterms=_cascade_stt_keyterms(curated)
        if curated.active:
            step_id=curated.fixture.steps[curated.current_index].step_id
        if curated.pending_observation_confirmation is not None:
            pending_frame=(
                "observation:"
                f"{curated.pending_observation_confirmation.predicate_id}"
            )
        elif curated.pending_completion_confirmation is not None:
            pending_frame="completion"
    language = (
        None
        if session.accepted_input_language is InputLanguagePreference.AUTO
        else session.accepted_input_language.value
    )
    return CascadeTranscriptionContext(
        configuration_id=session.accepted_configuration_id,
        session_id=session.session_id,
        generation=session.generation,
        language=language,
        protocol_id=session.accepted_protocol_id,
        step_id=step_id,
        pending_frame=pending_frame,
        keyterms=keyterms,
        audio_origin=audio_origin,
        google_v2_adaptation_disabled=(
            session.google_v2_adaptation_disabled),
        filler_words=bool(getattr(
            getattr(session,"stt_settings",None),"filler_words",False)),
        vad_threshold=getattr(
            getattr(session,"stt_settings",None),"vad_threshold",0.5),
    )


def keyterm_dump_terms(keyterms:tuple[str,...])->tuple[str,...]:
    """The key terms a transcript may not be made only of, less the control words.

    The pause and resume words are sent to the STT provider (lane XO,
    decision 4) but are never read as a key-term dump: "잠깐 멈춰 정지 스톱"
    is a researcher, not the provider reading back its prompt.
    """

    control_terms=frozenset(STT_CONTROL_KEYTERMS+STT_KOREAN_COMMAND_KEYTERMS)
    return tuple(term for term in keyterms if term not in control_terms)


def transcribe_cascade_audio(
    pcm:bytes,
    context:CascadeTranscriptionContext,
) -> Transcription:
    """Apply one request contract without rewriting the provider transcript.

    Lane HEAR2: whatever adapter answers, a request that timed out or never
    reached the provider is a ``SpeechProviderError`` with its code, so the
    turn drops the sound with a reason instead of failing with a traceback.
    """

    try:
        options={
            "language":context.language,
            "keyterms":context.keyterms,
            "vad_threshold":getattr(context,"vad_threshold",0.5),
            "filler_words":bool(getattr(context,"filler_words",False)),
        }
        if context.google_v2_adaptation_disabled:
            options["disable_google_v2_adaptation"]=True
        return transcribe(clean_path(pcm),**options)
    except requests.Timeout as exc:
        raise SpeechProviderError("STT timed out",code="timeout") from exc
    except requests.RequestException as exc:
        raise SpeechProviderError("STT could not be reached",code="unreachable") from exc

class LockedSender:
    def __init__(self,websocket): self.websocket=websocket; self.lock=asyncio.Lock()
    async def text(self,kind:str,**fields):
        async with self.lock: await self.websocket.send_text(event(kind,**fields))
        RUNTIME_METRICS.observe(kind,fields)
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


READER_TRANSLATION_PROMPT=(
    "You translate one laboratory protocol step for a researcher who is new to the lab "
    "and reads Korean, so it can be followed at the bench. Say exactly what the step "
    "says. "+PROTOCOL_TRANSLATION_STYLE+" Begin with the step label followed by "
    "'단계: '. Do not add any step, reason, warning, quantity, condition or advice the "
    "text does not state, and do not leave out any instruction. Return only the JSON "
    "object."
)
# No label on the reading itself: the page says once, at the head of the
# protocol card, that Korean it shows is an automatic translation, and the
# spoken lead below still says so before it is heard.
READER_TRANSLATION_HEADING=""
# Said before an unreviewed reading, so it is never heard as a reviewed one.
READER_TRANSLATION_SPOKEN_LEAD="자동 번역입니다."
READER_TRANSLATION_UNAVAILABLE=(
    "한국어 자동 번역을 확인하지 못해 원문을 그대로 읽었습니다."
)


#: Where a safety-box line comes from, as the box prints it beside the line.
_SAFETY_DOCUMENT_KINDS={
    "facility_sop":"안전 SOP",
    "supplier_sds":"물질 SDS",
    "equipment_manual":"장비 매뉴얼",
}


def _safety_translation(source:str,translation:str|None)->tuple[str|None,str]:
    """A safety line's Korean, or None with why it may not be shown.

    The check every Korean reading passes (``statement_issue``: every
    number with its unit, a negation neither dropped nor added, as many
    negations on both sides, every hazard word carried over and no half of
    the sentences dropped -- lane TS, decisions 1 and 2), run on each line on
    its own, so one bad line falls back to its source alone. Reviewed or
    machine, a line is held to it.
    """

    if not isinstance(translation,str) or not translation.strip():
        return None,"missing"
    issue=statement_issue(source,translation)
    if issue is not None:
        return None,issue
    return " ".join(translation.split()),"passed"


def curated_safety_items(curated:CuratedProtocolSession)->list[dict[str,Any]]:
    """Every safety line for the current step, for the one safety box.

    The step's own PDF warnings first, then each approved safety document the
    safety pack matched to the step. Nothing is cut short: the box shows all
    of them. A line carries Korean only where a reviewed translation exists
    and passes the check; otherwise ``primary_text`` is None and the page
    shows the source itself.
    """

    steps=curated.fixture.steps
    show_step=curated.active or (
        curated.workflow_status in {"preview","ready"}
        and 0<=curated.current_index<len(steps))
    if not show_step:
        return []
    step=steps[curated.current_index]
    items:list[dict[str,Any]]=[]
    seen:set[str]=set()
    for index,warning in enumerate(step.warnings,1):
        source=" ".join(str(warning.source_text or "").split())
        if not source or source in seen:
            continue
        seen.add(source)
        primary,check=_safety_translation(
            source,curated.fixture.localized_fact(step.step_id,f"warning_{index}"))
        lookup=getattr(curated.fixture,"localization_source",None)
        page=warning.evidence.source_page_number
        items.append({
            "kind":"pdf_warning","origin":f"PDF p.{page}",
            "source_text":source,"source_language":"en",
            "primary_text":primary,"translation_check":check,
            "translation_source":(
                lookup(step.step_id,f"warning_{index}") if callable(lookup)
                else None),
        })
    pack=getattr(curated,"safety_pack",None)
    if pack is None:
        return items
    guidance=pack.guidance_for_step(step,curated.current_index)
    for warning in guidance.warnings:
        source=" ".join(str(warning or "").split())
        if not source or source in seen:
            continue
        seen.add(source)
        items.append({
            "kind":"pdf_warning","origin":f"PDF p.{step.evidence.source_page_number}",
            "source_text":source,"source_language":"en",
            "primary_text":None,"translation_check":"missing",
        })
    for doc in guidance.applicable_documents:
        source=" ".join(str(doc.summary_text or "").split())
        if not source or source in seen:
            continue
        seen.add(source)
        kind=_SAFETY_DOCUMENT_KINDS.get(doc.document_type,"안전 자료")
        if doc.language.casefold().startswith("ko"):
            primary,check=source,"source_is_korean"
        else:
            primary,check=_safety_translation(source,next(
                (text for language,text in doc.reviewed_translations
                 if language.casefold().startswith("ko")),None))
        items.append({
            "kind":"safety_document","origin":f"안전 문서 · {kind}",
            "document_title":doc.title,
            "source_text":source,"source_language":doc.language,
            "primary_text":primary,"translation_check":check,
        })
    return items


def fixture_safety_notices(fixture:CuratedProtocolFixture)->list[dict[str,Any]]:
    """Every safety statement the source declares, with its Korean, for the start screen.

    The step's own PDF warnings and its sub-actions' warnings, in source
    order, each with the page it stands on and its step label. A line
    carries Korean only where a stored translation exists and passes the
    same mechanical check the safety box applies; otherwise
    ``primary_text`` is None and the screen shows the source itself. The
    source text is the document's own excerpt, never a summary.
    """

    items:list[dict[str,Any]]=[]
    seen:set[tuple[str,str]]=set()
    lookup=getattr(fixture,"localization_source",None)
    for step in fixture.steps:
        statements=list(step.warnings)
        for action in step.sub_actions:
            statements.extend(action.warnings)
        for index,warning in enumerate(statements,1):
            source=" ".join(str(
                warning.evidence.source_excerpt or warning.source_text or "").split())
            if not source or (step.step_id,source) in seen:
                continue
            seen.add((step.step_id,source))
            primary,check=_safety_translation(
                source,fixture.localized_fact(step.step_id,f"warning_{index}"))
            items.append({
                "step_id":step.step_id,"step_label":step.source_label,
                "statement_id":warning.statement_id,"warning_index":index,
                "source_page_number":warning.evidence.source_page_number,
                "source_text":source,"source_language":"en",
                "primary_text":primary,"translation_check":check,
                "translation_source":(
                    lookup(step.step_id,f"warning_{index}") if callable(lookup)
                    else None),
            })
    return items


def _screen_translation_status(fixture:Any,step:Any)->str|None:
    """Presentation status for one step sentence; never a workflow verdict."""

    if step is None:
        return None
    source_text=getattr(step,"instruction_source_text",None)
    if not isinstance(source_text,str) or not source_text.strip():
        return None
    if is_korean(source_text) or fixture.localized_fact(
            step.step_id,"current_step"):
        return "available"
    fact_key=f"{step.step_id}/current_step"
    stored=getattr(fixture,"translation_failures",None) or {}
    try:
        spot=_spot_refusals(translation_revision_key(fixture))
    except Exception:  # noqa: BLE001 - a fixture without ids has no spot status
        spot={}
    if fact_key in stored or fact_key in spot:
        return "rejected"
    if _translation_pending(fixture) or _screen_translating(fixture):
        return "pending"
    return "missing"


def curated_screen_fields(curated:CuratedProtocolSession)->dict[str,Any]:
    """What the page draws beside a fixture state: the safety list, the round, the open question.

    Sent next to ``state``, not in it, so the state stays the session's own.
    ``translation_source`` says where the Korean on the step card comes from
    -- ``reviewed`` for a reviewed sidecar, ``none`` when there is none -- so
    the page can put its one line at the head of the card, and ``machine``
    once a stored machine translation has been shown in this session (on the
    card, in the safety box, or in a reply).
    """

    items=curated_safety_items(curated)
    fixture=curated.fixture
    machine_on_card=any(
        item.get("translation_source")=="machine" and item.get("primary_text")
        for item in items)
    steps=fixture.steps
    if 0<=curated.current_index<len(steps):
        lookup=getattr(fixture,"localization_source",None)
        if callable(lookup) and (curated.active or curated.workflow_status in {
                "preview","ready"}):
            machine_on_card=machine_on_card or lookup(
                steps[curated.current_index].step_id,"current_step")=="machine"
    if machine_on_card:
        curated.machine_translation_shown=True
    if getattr(curated,"machine_translation_shown",False):
        source="machine"
    elif getattr(fixture,"localizations",None):
        source="reviewed"
    else:
        source="none"
    # Lane PX decision 1: while the revision's Korean is still being made the
    # card says so ("번역 준비 중") instead of "no translation"; from the next
    # redraw after a batch lands, the Korean is simply there.
    # Lane CB, decision 4: the round the step is in ("2/3회차") and the server
    # question open at this step (the condition, the count, the next round),
    # both the session's own words.
    round_status=getattr(curated,"repeat_round_status",None)
    open_question=getattr(curated,"open_server_question",None)
    # Lane CF, decision 5: "지금 기준: {파일 이름} · {올린 날짜}".
    basis=getattr(curated,"source_basis",None)
    # Lane RA, decision (f): the next step's sentence, the kind of the open
    # confirmation question, the candidates asked back, the values read
    # back and the deviation a note recorded -- the session's own words, for
    # lanes UI2 and RV to draw (the page is not changed here).
    next_sentence=getattr(curated,"next_step_sentence",None)
    next_missing=getattr(curated,"next_step_translation_missing",None)
    confirm=getattr(curated,"confirm_question",None)
    candidates=getattr(curated,"clarify_candidates",None)
    readback=getattr(curated,"readback_values",None)
    deviation=getattr(curated,"record_deviation",None)
    current_step=(steps[curated.current_index] if (
        (curated.active or curated.workflow_status in {"preview","ready"})
        and 0<=curated.current_index<len(steps)) else None)
    next_step=(steps[curated.current_index+1] if (
        curated.active and 0<=curated.current_index<len(steps)-1) else None)
    return {
        "source_basis":basis() if callable(basis) else None,
        "safety_items":items,"translation_source":source,
        # Lane KO2: also while the screen's sentences are read in place.
        "translation_pending":_translation_pending(fixture) or _screen_translating(fixture),
        "translation_status":_screen_translation_status(fixture,current_step),
        "repeat_round":round_status() if callable(round_status) else None,
        "open_question":open_question() if callable(open_question) else None,
        # Lane WV, decision 1: the step's own figures, cut from the uploaded
        # PDF -- the list once its pages are cut, None while they are being.
        "source_figures":_screen_source_figures(curated),
        "next_step_sentence":next_sentence("ko") if callable(next_sentence) else None,
        "next_step_label":next_step.source_label if next_step is not None else None,
        # Lane KO2, decision 3: the next step line is the source for want of
        # Korean ("번역 준비 중" beside it).
        "next_step_translation_missing":(
            bool(next_missing("ko")) if callable(next_missing) else False),
        "next_step_translation_status":_screen_translation_status(fixture,next_step),
        "confirm_question":confirm() if callable(confirm) else None,
        "clarify_candidates":candidates() if callable(candidates) else [],
        "readback_values":readback() if callable(readback) else [],
        "record_deviation":deviation() if callable(deviation) else None,
    }


def _screen_source_figures(curated:Any)->list[dict[str,Any]]|None:
    """The current step's source figures for the screen, from the cache only."""

    fixture=getattr(curated,"fixture",None)
    index=getattr(curated,"current_index",None)
    if fixture is None or not isinstance(index,int) or not (
        getattr(curated,"active",False)
        or getattr(curated,"workflow_status",None) in {"preview","ready"}
    ):
        return []
    figures=source_figures.cached_figures_for_step(fixture,index)
    if figures is None:
        return None
    return [
        figure.public_dict(
            protocol_id=fixture.protocol_id,revision_id=fixture.revision_id,
            source_sha256=str(fixture.source_pdf_sha256),
        ) for figure in figures
    ]


async def _send_source_figures(
    session:ListenerSession,curated:CuratedProtocolSession,
    send:Callable[...,Awaitable[Any]],*,configuration_id:int|None,
)->None:
    """Cut the current step's pages in a thread and send the figures once (lane WV).

    Sent as ``protocol.figures.state`` for the step the pages were cut for;
    the page draws it only while that step is still the one shown. Nothing
    is sent when the session moved on, and nothing here changes state.
    """

    fixture=curated.fixture
    index=curated.current_index
    if getattr(fixture,"source_pdf_path",None) is None:
        return
    step=fixture.steps[index] if 0<=index<len(fixture.steps) else None
    if step is None:
        return
    try:
        figures=await asyncio.to_thread(source_figures.figures_for_step,fixture,index)
    except Exception as exc:  # noqa: BLE001 - a picture is never worth a failed turn
        log.warning("source figures: cut failed error=%s",type(exc).__name__)
        return
    if (
        not session.active or curated.current_index!=index
        or session.accepted_configuration_id!=configuration_id
    ):
        return
    try:
        await send(
            "protocol.figures.state",
            configuration_id=configuration_id,
            protocol_id=fixture.protocol_id,revision_id=fixture.revision_id,
            source_document_hash=fixture.source_pdf_sha256,
            step_id=step.step_id,step_label=step.source_label,
            figures=[
                figure.public_dict(
                    protocol_id=fixture.protocol_id,revision_id=fixture.revision_id,
                    source_sha256=str(fixture.source_pdf_sha256),
                ) for figure in figures
            ],
        )
    except Exception as exc:  # noqa: BLE001 - a closed page misses a redraw
        log.info("source figures: send skipped error=%s",type(exc).__name__)


# --- Lane WV, decision 2: the web, after the source ------------------------------

def _web_lookup_wanted(session:ListenerSession,curated:CuratedProtocolSession,plan:Any)->bool:
    """Whether this turn asks the web, and the web may be asked."""

    return bool(
        session.web_explanation_settings.enabled
        and getattr(curated,"web_lookup","on")=="on"
        and getattr(curated,"active",False)
        and (
            getattr(plan,"visual_kind",None)=="web_lookup"
            or getattr(plan,"requested_followup",None)=="search_external_reference"
        )
    )


def _web_lookup_subject(curated:CuratedProtocolSession,plan:Any,transcript:str)->str:
    words=getattr(curated,"web_lookup_subject_words",None)
    subject=words(plan,transcript) if callable(words) else ""
    if subject=="이 단계 내용" and getattr(plan,"requested_followup",None)=="search_external_reference":
        query=getattr(curated,"reference_query_for",None)
        resolved=query(transcript,plan) if callable(query) else None
        if isinstance(resolved,str) and resolved.strip():
            subject=" ".join(resolved.split())[:60]
    return subject or "이 단계 내용"


def _web_lookup_words(
    curated:CuratedProtocolSession,plan:Any,transcript:str,language:str,
)->Any:
    """The rules' words for a web lookup the server will now run."""

    subject=_web_lookup_subject(curated,plan,transcript)
    if language!="ko":
        words=f"Looking it up on the web; the result goes on the screen with its sources."
    else:
        words=(
            f"{subject}{_josa_eun_neun(subject)} 웹에서 찾아볼게요. "
            "찾으면 화면에 출처와 함께 띄울게요. 값과 안전 지시는 원문만 따라요."
        )
    if getattr(plan,"visual_kind",None)=="web_lookup":
        speech=str(plan.speech_text or "")
        if "웹에서 찾아볼게요" in speech:
            # Already said so (lane VF, decision 8: a photograph looked up
            # in place of a figure the source does not have).
            return plan
        lead="화면에 원문 그림을 띄웠어요. " if speech.startswith("화면에 원문 그림을 띄웠어요.") else ""
        display=str(plan.display_text or "")
        old_sentence=speech[len(lead):]
        return replace(
            plan,
            speech_text=f"{lead}{words}",
            display_text=display.replace(old_sentence,words) if old_sentence and old_sentence in display else display,
            primary_text=(
                str(plan.primary_text).replace(old_sentence,words)
                if isinstance(plan.primary_text,str) and old_sentence and old_sentence in plan.primary_text
                else plan.primary_text
            ),
        )
    return replace(
        plan,
        speech_text=f"{plan.speech_text} {words}" if plan.speech_text else words,
        display_text=f"{plan.display_text}\n\n{words}" if plan.display_text else words,
    )


def _josa_eun_neun(word:str)->str:
    last=word.strip()[-1:] if word.strip() else ""
    if last and "가"<=last<="힣":
        return "은" if (ord(last)-ord("가"))%28 else "는"
    return "는"


def _record_web_reference(
    session:ListenerSession,curated:CuratedProtocolSession,*,turn_id:int,generation:int,
    subject:str,links:list[str],photo_page_url:str|None,model:str,
)->None:
    """Links only, never the web's words or picture, in the experiment report."""

    store=session.experiment_report_store
    if store is None:
        return
    try:
        report=_open_experiment_report(session,curated)
        step=curated.fixture.steps[curated.current_index] if curated.active else None
        store.append_event(
            report["report_id"],
            event_key=f"turn-{turn_id}-generation-{generation}-web_reference",
            event_type="web_reference",
            step_id=step.step_id if step is not None else None,
            step_label=step.source_label if step is not None else None,
            user_wording=subject[:200],
            confirmation_state="shown_on_screen",
            source_tier="web_reference",
            payload={
                "links":links[:10],
                "photo_page_url":photo_page_url,
                "model":model,
                "text_kept":False,
                "note":"웹 자료는 화면에만 보였고 보고서에는 링크만 남김 (줄 WV)",
            },
        )
    except Exception as exc:  # noqa: BLE001 - the report never blocks the turn
        log.warning("web reference not recorded error=%s",type(exc).__name__)


async def _run_web_lookup(
    *,session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    curated:CuratedProtocolSession,plan:Any,transcript:str,language:str,
    speak_by:float|None,clock:Callable[[],float],answer_segments:int=1,
)->None:
    """Ask the web about the thing named, after the rules' answer (lane WV, decision 2).

    The source answered first and is never changed by this. What the web
    says is checked (no value, no safety instruction, no state claim), shown
    on the screen with its sources, and said in one or two sentences only
    when the answer's audio is still playing; otherwise the screen alone.
    """

    settings=session.web_explanation_settings
    configuration_id=session.accepted_configuration_id
    subject=_web_lookup_subject(curated,plan,transcript)
    fields={"configuration_id":configuration_id,"turn_id":turn_id,"generation":generation}
    try:
        await sender.text("protocol.web.state",status="running",subject=subject,**fields)
        step=curated.fixture.steps[curated.current_index]
        protocol=curated.fixture.draft.protocol
        names=", ".join(
            " ".join(str(getattr(item,"name_source_text","")).split())
            for item in (*getattr(protocol,"materials",()),*getattr(protocol,"equipment",()))
            if getattr(item,"name_source_text","")
        )[:600]
        client=AsyncOpenAI(
            api_key=require_env("OPENAI_API_KEY"),max_retries=0,
            timeout=settings.timeout_seconds)
        result=await explain_with_web(
            client,settings,subject=subject,question=transcript,
            step_text=step.instruction_source_text,protocol_names=names,language=language)
        photo=None
        if result.shown and settings.photos:
            try:
                photo=await asyncio.wait_for(
                    find_commons_photo(result.english_term or subject),timeout=8.0)
            except asyncio.TimeoutError:
                photo=None
        if not session.is_current(turn_id,generation):
            return
        spoken_aloud=False
        if (
            result.shown and result.spoken and speak_by is not None and language=="ko"
            and (turn_id,generation) in session.held_audio_complete and clock()<speak_by
        ):
            words=f"{result.spoken} 자세한 건 화면에 출처와 함께 띄웠어요."
            pcm=await asyncio.to_thread(synthesize,said(words),language)
            frames=frame_complete_audio(pcm)
            if (
                frames and session.is_current(turn_id,generation)
                and (turn_id,generation) in session.held_audio_complete
            ):
                # Lane VOX: after every sentence of the answer.
                await sender.segment(turn_id,answer_segments,frames,generation)
                await _release_held_audio(
                    sender,session,turn_id,generation,answer_segments+1)
                spoken_aloud=True
        await sender.text(
            "protocol.web.result",
            status=result.status if result.shown else result.status,
            subject=subject,spoken=result.spoken if result.shown else "",
            screen=result.screen if result.shown else "",
            english_term=result.english_term,
            citations=[item.public_dict() for item in result.citations] if result.shown else [],
            photo=photo.public_dict() if photo is not None else None,
            removed_count=len(result.removed_sentences),
            spoken_aloud=spoken_aloud,model=result.model,elapsed_ms=result.elapsed_ms,
            search_count=result.search_count,backend="openai_responses_web_search",
            **fields,
        )
        log.info(
            "web_lookup turn_id=%s status=%s searches=%s citations=%s removed=%s photo=%s "
            "spoken=%s elapsed_ms=%s",
            turn_id,result.status,result.search_count,len(result.citations),
            len(result.removed_sentences),photo is not None,spoken_aloud,result.elapsed_ms)
        if result.shown:
            _record_web_reference(
                session,curated,turn_id=turn_id,generation=generation,subject=subject,
                links=[item.url for item in result.citations],
                photo_page_url=photo.page_url if photo is not None else None,
                model=result.model)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the web never costs the turn
        log.warning("web lookup failed turn_id=%s error=%s",turn_id,type(exc).__name__)
        try:
            await sender.text("protocol.web.result",status="provider_error",subject=subject,**fields)
        except Exception:  # noqa: BLE001
            pass
    finally:
        # The held answer audio is let go whatever happened; a page that
        # closed meanwhile cannot be sent to, and that is not an error here.
        try:
            await _release_held_audio(sender,session,turn_id,generation,answer_segments)
        except Exception:  # noqa: BLE001
            pass


# --- Lane WV, decision 3: a drawing from the step's words ------------------------

def _drawing_wanted(session:ListenerSession,curated:CuratedProtocolSession,plan:Any)->bool:
    return bool(
        session.drawn_diagram_settings.enabled
        and getattr(curated,"active",False)
        and getattr(plan,"visual_kind",None)=="drawn_diagram"
    )


_DRAWING_OFF_WORDS="그림 그리기가 꺼져 있어 그리지 않았어요. 원문이 기준이에요."
_DRAWING_ON_WORDS=(
    "이 단계 원문을 바탕으로 그림을 그리고 있어요. 다 되면 화면에 띄울게요. "
    "실제와 다를 수 있고, 수치는 원문 그대로예요."
)


def _drawing_words(plan:Any,language:str)->Any:
    """The rules' words for a drawing the server will now make."""

    words=(
        _DRAWING_ON_WORDS if language=="ko"
        else "Drawing a diagram from this step's words; it goes on the screen, and it may differ from the real thing."
    )
    speech=str(plan.speech_text or "")
    display=str(plan.display_text or "")
    if words in speech:
        # Already said so (lane VF, decision 8: a drawing in place of a
        # figure the source does not have).
        return plan
    if _DRAWING_OFF_WORDS in speech:
        return replace(
            plan,
            speech_text=speech.replace(_DRAWING_OFF_WORDS,words),
            display_text=display.replace(_DRAWING_OFF_WORDS,words),
            primary_text=(
                str(plan.primary_text).replace(_DRAWING_OFF_WORDS,words)
                if isinstance(plan.primary_text,str) else plan.primary_text
            ),
        )
    return replace(
        plan,
        speech_text=f"{speech} {words}".strip(),
        display_text=f"{display}\n\n{words}" if display else words,
    )


# --- Lane VF, decision 8: no figure in the source -> drawn, or a photo looked up ---

_NO_FIGURE_DRAWING_WORDS="원문에는 그림이 없어서 그려 드릴게요. "+_DRAWING_ON_WORDS
_PICTURE_WORD_ONLY=re.compile(r"(?:실제|진짜|실물)?\s*(?:사진|그림|이미지|모습|삽화|도식)")


def _replace_words(plan:Any,old:str,new:str)->Any:
    changes={}
    for field in ("speech_text","display_text","primary_text"):
        value=getattr(plan,field,None)
        if isinstance(value,str) and old in value:
            changes[field]=value.replace(old,new)
    return replace(plan,**changes) if changes else plan


def _picture_fallback(
    session:ListenerSession,curated:CuratedProtocolSession,plan:Any,transcript:str,language:str,
)->Any:
    """A picture asked for that the source does not have: drawn, or a photo looked up.

    Lane VF, decision 8 (decision of 2026-10-10: with no figure in the
    source, draw at once instead of telling the experimenter to ask again).
    The rules' plan says the step's page has no figure; here, when drawing
    is on, the turn becomes a drawing -- "원문에는 그림이 없어서 그려 드릴게요."
    and lane WV's checks and display as they are -- and when a photograph
    was asked for ("사진", "실제 모습") of a thing named, the web is on and
    looking up is allowed, it becomes a web lookup of that thing instead.
    With both off the rules' words stand. Nothing here changes state.
    """

    if (
        getattr(plan,"action",None) is not CuratedProtocolAction.VISUAL_REQUEST
        or getattr(plan,"visual_kind",None)!="source_figure"
        or not getattr(curated,"active",False)
        or NO_FIGURE_WORDS not in str(plan.speech_text or "")
        or language!="ko"
    ):
        return plan
    if curated.source_figures_for_current_step():
        return plan
    subject=web_lookup_subject(transcript) if photo_asked(transcript) else None
    if subject and _PICTURE_WORD_ONLY.fullmatch(subject):
        # "사진 보여줘": the picture word itself is no thing to look up.
        subject=None
    if (
        subject
        and session.web_explanation_settings.enabled
        and getattr(curated,"web_lookup","on")=="on"
    ):
        words=(
            f"원문에는 사진이 없어서 {subject}{_josa_eun_neun(subject)} 웹에서 찾아볼게요. "
            "찾으면 화면에 출처와 함께 띄울게요. 값과 안전 지시는 원문만 따라요."
        )
        plan=_replace_words(plan,NO_FIGURE_WORDS,words)
        return replace(
            plan,visual_kind="web_lookup",visual_intent="web_explanation",
            requested_entity=subject,requested_entities=(subject,),
        )
    if session.drawn_diagram_settings.enabled:
        plan=_replace_words(plan,NO_FIGURE_WORDS,_NO_FIGURE_DRAWING_WORDS)
        return replace(plan,visual_kind="drawn_diagram",visual_intent="drawn_diagram")
    return plan


async def _run_drawing(
    *,session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    curated:CuratedProtocolSession,language:str,
)->None:
    """Draw the current step from its words, checked, screen only (lane WV, decision 3)."""

    settings=session.drawn_diagram_settings
    configuration_id=session.accepted_configuration_id
    fixture=curated.fixture
    step=fixture.steps[curated.current_index]
    fields={"configuration_id":configuration_id,"turn_id":turn_id,"generation":generation,
            "protocol_id":fixture.protocol_id,"revision_id":fixture.revision_id,
            "step_id":step.step_id,"step_label":step.source_label}
    try:
        await sender.text("protocol.drawing.state",status="running",label=DRAWN_LABEL,**fields)
        extra=tuple(
            " ".join(str(getattr(item,"source_text","")).split())
            for group in (step.sub_actions,step.notes,step.expected_results)
            for item in group if getattr(item,"source_text","")
        )
        client=AsyncOpenAI(
            api_key=require_env("OPENAI_API_KEY"),max_retries=0,
            timeout=settings.timeout_seconds)
        result=await draw_step_diagram(
            client,settings,step_id=step.step_id,step_label=step.source_label,
            step_text=step.instruction_source_text,
            source_sha256=str(fixture.source_pdf_sha256 or fixture.fixture_sha256),
            extra_texts=extra,language=language)
        await sender.text(
            "protocol.drawing.result",status=result.status,
            diagram=result.diagram.public_dict() if result.diagram is not None else None,
            label=DRAWN_LABEL,model=result.model,elapsed_ms=result.elapsed_ms,
            reason=result.reason,**fields)
        log.info(
            "drawing turn_id=%s status=%s elapsed_ms=%s removed_numbers=%s removed_texts=%s",
            turn_id,result.status,result.elapsed_ms,
            len(result.diagram.removed_numbers) if result.diagram else None,
            len(result.diagram.removed_texts) if result.diagram else None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the drawing never costs the turn
        log.warning("drawing failed turn_id=%s error=%s",turn_id,type(exc).__name__)
        try:
            await sender.text("protocol.drawing.result",status="provider_error",diagram=None,
                              label=DRAWN_LABEL,**fields)
        except Exception:  # noqa: BLE001
            pass


def _queue_source_figures(
    session:ListenerSession,curated:CuratedProtocolSession,
    send:Callable[...,Awaitable[Any]],*,force:bool=False,
)->None:
    """After a fixture state: cut and send the step's figures when not yet cut."""

    fixture=getattr(curated,"fixture",None)
    if fixture is None or getattr(fixture,"source_pdf_path",None) is None:
        return
    if not force and source_figures.cached_figures_for_step(fixture,curated.current_index) is not None:
        return
    session.track_visual_task(asyncio.create_task(_send_source_figures(
        session,curated,send,configuration_id=session.accepted_configuration_id)))


#: Statuses that would call a stored machine translation something else.
_MACHINE_RELABELLED_STATUSES=frozenset({
    "verified_sidecar","deterministic_protocol_structure",
    "source_language","unavailable",
})


def _machine_text_marks(text:str)->tuple[str,...]:
    """Forms a stored machine translation takes inside a reply."""

    core=re.sub(r"^\s*[0-9A-Za-z.]+\s*단계\s*[:：]\s*","",text).strip()
    marks={text.strip(),core}
    if len(core)>24:
        marks.add(core[:24])
    return tuple(mark for mark in marks if len(mark)>=6)


def _label_machine_translation(
    session:ListenerSession,curated:CuratedProtocolSession,plan:Any,*,commit:bool=True,
)->Any:
    """Name a reply carrying a stored machine translation as unreviewed.

    The curated session picks reviewed Korean, then a stored machine
    translation, and its replies call either one ``verified_sidecar``. Here
    a reply showing a machine translation is relabelled
    ``model_assisted_unreviewed`` (the page then puts its one line at the
    head of the card), and when the speech carries it, "자동 번역입니다." is
    said first -- once a session.
    """

    machine=getattr(curated.fixture,"machine_localizations",None)
    if not machine:
        return plan
    display=plan.display_text if isinstance(plan.display_text,str) else ""
    speech=plan.speech_text if isinstance(plan.speech_text,str) else ""
    shown=spoken=False
    for text in machine.values():
        marks=_machine_text_marks(text)
        if any(mark in display for mark in marks):
            shown=True
        if any(mark in speech for mark in marks):
            shown=spoken=True
    if not shown:
        return plan
    changes:dict[str,Any]={}
    if plan.translation_status in _MACHINE_RELABELLED_STATUSES:
        changes["translation_status"]="model_assisted_unreviewed"
    if spoken and not session.auto_translation_announced:
        changes["speech_text"]=f"{READER_TRANSLATION_SPOKEN_LEAD} {speech}"
    if commit:
        # ``commit=False`` reads what would be said without saying it (lane
        # VF, decision 3: a notice synthesized before it is due).
        curated.machine_translation_shown=True
        if spoken:
            session.auto_translation_announced=True
    return replace(plan,**changes) if changes else plan


def _with_reader_section(document:Any,heading:str,text:str)->Any:
    """The reply's document with one section first; otherwise unchanged."""

    if not isinstance(document,dict):
        return document
    sections=[{"kind":"section","heading":heading,"text":text},
              *(document.get("sections") or ())]
    return {**document,"sections":sections}


async def _request_reader_translation(
    session:ListenerSession,label:str,statement:str,
)->str:
    """One bounded model call for a Korean reading of one source statement."""

    settings=session.multi_brain_settings
    role=RoleModel.from_environment("translation")
    client=_role_client(role)
    response=await asyncio.wait_for(
        client.chat.completions.create(
            model=role.model,
            messages=[
                {"role":"system","content":READER_TRANSLATION_PROMPT},
                {"role":"user","content":json.dumps(
                    {"step_label":label,"source_text":statement},
                    ensure_ascii=False,
                )},
            ],
            response_format={
                "type":"json_schema",
                "json_schema":{
                    "name":"protocol_step_reader_translation_v1",
                    "strict":True,
                    "schema":{
                        "type":"object","additionalProperties":False,
                        "properties":{"korean":{"type":"string"}},
                        "required":["korean"],
                    },
                },
            },
            temperature=0,
        ),
        timeout=min(6.0,settings.answer_timeout_seconds),
    )
    payload=json.loads(response.choices[0].message.content or "{}")
    korean=payload.get("korean") if isinstance(payload,dict) else None
    return " ".join(korean.split()) if isinstance(korean,str) else ""


async def _apply_reader_translation(
    session:ListenerSession,curated:CuratedProtocolSession,plan:Any,
    target:tuple[str,str,tuple[str,...]],*,turn_id:int,generation:int,
    clock:Callable[[],float],
)->Any:
    """Speak a checked Korean reading of a step the reader asked to hear.

    Only for a step with no reviewed translation, only when translation is
    on (the translation role's own settings, not the answer brain -- lane F,
    decision 1), and only a reading that keeps every number,
    unit and protocol term, and says what the source says
    (``statement_issue``, lane TS). The reading is
    spoken after "자동 번역입니다." and shown above the unchanged source with
    no label of its own -- the page says once, at the head of the protocol
    card, that its Korean is an automatic translation; a failed or refused
    reading says so, and the source is read as before. No workflow state is
    involved.
    """

    settings=session.multi_brain_settings
    if not settings.translation_enabled:
        return plan
    label,statement,terms=target
    key=(curated.fixture.fixture_sha256,label,statement)
    korean=session.reader_translations.get(key)
    status="cached" if korean else "unavailable"
    started=clock()
    if korean is None and key in session.reader_translation_refused:
        # Lane SAY, decision 1: a sentence is asked for once a session.
        status="refused_before"
    elif korean is None:
        try:
            candidate=await _request_reader_translation(session,label,statement)
        except Exception as exc:
            status=f"provider_{type(exc).__name__}"
        else:
            issue=statement_issue(
                statement,candidate,required_terms=terms,step_label=label)
            if issue is None:
                korean=candidate
                session.reader_translations[key]=korean
                status="accepted"
            else:
                status=f"rejected_{issue}"
        if korean is None:
            session.reader_translation_refused.add(key)
    # Status and size only: the statement and the reading stay out of logs.
    log.info(
        "reader_translation turn_id=%s status=%s chars=%d elapsed_ms=%d",
        turn_id,status,len(korean or ""),round((clock()-started)*1000),
    )
    if not session.is_current(turn_id,generation):
        # Superseded while waiting: the turn's own fences drop it anyway.
        return plan
    if korean:
        lead=(
            "" if session.auto_translation_announced
            else f"{READER_TRANSLATION_SPOKEN_LEAD} ")
        session.auto_translation_announced=True
        return replace(
            plan,
            speech_text=f"{lead}{korean}",
            display_text=f"{korean}\n\n{plan.display_text}",
            display_document=_with_reader_section(
                plan.display_document,READER_TRANSLATION_HEADING,korean),
            translation_status="model_assisted_unreviewed",
        )
    # Lane SAY, decision 1: the source is not read aloud any more; the
    # turn's Korean-first step says the sentence is not translated yet and
    # leaves the source on the screen (``_korean_first``).
    return plan


#: Lane SAY, decision 1 (2026-10-25): readings the translation role made in a
#: turn, by translation key (protocol id / revision id) and sentence key
#: (``SourceQuote.key``), kept for the process so a sentence is translated
#: once and every session on the revision reads it. Presentation only; a
#: stored machine row that was refused cannot be replaced in the store
#: (append-only), so these live here. Bounded by revisions.
_SPOT_READINGS:"collections.OrderedDict[str,dict[str,str]]"=collections.OrderedDict()
#: Sentence keys whose generated Korean failed the mechanical check, with
#: the privacy-safe reason code. The source remains on screen. Bounded with
#: the accepted spot readings and never used as workflow state.
_SPOT_REFUSED:"collections.OrderedDict[str,dict[str,str]]"=collections.OrderedDict()
#: Sentence keys asked for already, by translation key, with when they may be
#: asked for again: never after a reading came back (accepted or refused),
#: ``SPOT_RETRY_AFTER_SECONDS`` after a provider failure.
_SPOT_ATTEMPTED:dict[str,dict[str,float]]={}
SPOT_RETRY_AFTER_SECONDS=60.0
_SPOT_LOCK=threading.Lock()
SPOT_READINGS_MAX_REVISIONS=32
#: At most this many sentences are translated in one turn; the rest are said
#: as not translated yet and tried in a later turn.
SPOT_TRANSLATIONS_PER_TURN=3
SPOT_TRANSLATION_PHRASE_PROMPT=(
    "You translate one sentence or phrase of a laboratory protocol into Korean for a "
    "researcher at the bench. Say exactly what it says. "+PROTOCOL_TRANSLATION_STYLE+
    " This is a phrase, not a step: do not begin with a step label or '예상 결과: '. "
    "Return only the JSON object."
)


def _spot_readings(key:str)->dict[str,str]:
    with _SPOT_LOCK:
        return dict(_SPOT_READINGS.get(key,{}))


def _spot_refusals(key:str)->dict[str,str]:
    with _SPOT_LOCK:
        return dict(_SPOT_REFUSED.get(key,{}))


def _spot_attempt(key:str,sentence:str,now:float)->bool:
    """Mark one sentence as asked for; False when it may not be asked for now."""

    with _SPOT_LOCK:
        attempted=_SPOT_ATTEMPTED.setdefault(key,{})
        if attempted.get(sentence,-math.inf)>now:
            return False
        attempted[sentence]=math.inf
        return True


def _spot_failed(key:str,sentence:str,now:float)->None:
    with _SPOT_LOCK:
        _SPOT_ATTEMPTED.setdefault(key,{})[sentence]=now+SPOT_RETRY_AFTER_SECONDS


def _spot_keep(key:str,sentence:str,korean:str)->None:
    with _SPOT_LOCK:
        readings=_SPOT_READINGS.setdefault(key,{})
        _SPOT_READINGS.move_to_end(key)
        readings[sentence]=korean
        refused=_SPOT_REFUSED.get(key)
        if refused is not None:
            refused.pop(sentence,None)
            if not refused:
                _SPOT_REFUSED.pop(key,None)
        while len(_SPOT_READINGS)>SPOT_READINGS_MAX_REVISIONS:
            dropped,_=_SPOT_READINGS.popitem(last=False)
            _SPOT_ATTEMPTED.pop(dropped,None)
            _SPOT_REFUSED.pop(dropped,None)


def _spot_refuse(key:str,sentence:str,reason:str)->None:
    with _SPOT_LOCK:
        refused=_SPOT_REFUSED.setdefault(key,{})
        _SPOT_REFUSED.move_to_end(key)
        refused[sentence]=reason
        while len(_SPOT_REFUSED)>SPOT_READINGS_MAX_REVISIONS:
            dropped,_=_SPOT_REFUSED.popitem(last=False)
            _SPOT_ATTEMPTED.pop(dropped,None)
            _SPOT_READINGS.pop(dropped,None)


async def _request_spot_translation(session:ListenerSession,quote:Any)->str:
    """One bounded call for a Korean reading of one source sentence or phrase."""

    if quote.kind=="step" and quote.label:
        return await _request_reader_translation(session,quote.label,quote.text)
    settings=session.multi_brain_settings
    role=RoleModel.from_environment("translation")
    client=_role_client(role)
    response=await asyncio.wait_for(
        client.chat.completions.create(
            model=role.model,
            messages=[
                {"role":"system","content":SPOT_TRANSLATION_PHRASE_PROMPT},
                {"role":"user","content":json.dumps(
                    {"source_text":quote.text},ensure_ascii=False)},
            ],
            response_format={
                "type":"json_schema",
                "json_schema":{
                    "name":"protocol_phrase_reader_translation_v1",
                    "strict":True,
                    "schema":{
                        "type":"object","additionalProperties":False,
                        "properties":{"korean":{"type":"string"}},
                        "required":["korean"],
                    },
                },
            },
            temperature=0,
        ),
        timeout=min(6.0,settings.answer_timeout_seconds),
    )
    payload=json.loads(response.choices[0].message.content or "{}")
    korean=payload.get("korean") if isinstance(payload,dict) else None
    return " ".join(korean.split()) if isinstance(korean,str) else ""


async def _korean_first(
    session:ListenerSession,curated:CuratedProtocolSession,plan:Any,*,
    turn_id:int,generation:int,clock:Callable[[],float],
)->Any:
    """Say every source sentence of a Korean reply in Korean (lane SAY, decision 1).

    ``CuratedProtocolSession.korean_first`` puts a sentence's reviewed or
    stored Korean in place of the source it quotes. A sentence with none is
    translated now by the translation role -- at most
    ``SPOT_TRANSLATIONS_PER_TURN``, each once (in this process), each bounded
    -- and used only when it passes the check every reading passes
    (``statement_issue``). One still without Korean is said as "이 문장은
    아직 번역되지 않았어요. 원문을 화면에 띄울게요." and shown in the source.
    A checked reading of a step's own sentence is put on the session's
    fixture for the screen, as a stored machine translation is. Nothing in
    the workflow changes.
    """

    fixture=curated.fixture
    key=translation_revision_key(fixture)
    # Read-only; off the loop, as the first reading of a long document builds
    # its index over every page's text.
    reading=await asyncio.to_thread(
        curated.korean_first,plan,language="ko",readings=_spot_readings(key))
    if not reading.untranslated:
        return _with_spot_readings(session,curated,reading)
    started=clock()
    readings,asked,statuses=await _translate_in_place(
        session,fixture,reading.untranslated,started)
    if not session.is_current(turn_id,generation):
        return reading.plan  # superseded: the turn's fences drop it anyway
    if readings:
        reading=await asyncio.to_thread(
            curated.korean_first,plan,language="ko",readings=readings)
    # Counts and codes only: no sentence or reading reaches the log.
    log.info(
        "korean_first turn_id=%s quotes=%d untranslated=%d asked=%d statuses=%s elapsed_ms=%d",
        turn_id,len(reading.quotes),len(reading.untranslated),asked,
        ",".join(statuses) or "none",round((clock()-started)*1000))
    return _with_spot_readings(session,curated,reading,readings)


async def _translate_in_place(
    session:ListenerSession,fixture:Any,quotes:Sequence[Any],started:float,
)->tuple[dict[str,str],int,list[str]]:
    """Checked Korean readings for source quotes, made now (lane SAY's path).

    The revision's readings already made in this process, and the session's
    reader readings, first; then, with the translation role on, at most
    ``SPOT_TRANSLATIONS_PER_TURN`` quotes are asked for -- each once in the
    process (``_spot_attempt``), each bounded -- and a reading is kept only
    when it passes ``statement_issue``. Lane KO2 uses the same path for the
    screen's sentences and the purpose. Returns (readings by quote key,
    how many were asked, status codes).
    """

    key=translation_revision_key(fixture)
    sha=fixture.fixture_sha256
    readings=_spot_readings(key)
    for quote in quotes:
        held=session.reader_translations.get((sha,quote.label or "",quote.text))
        if held:
            readings[quote.key]=held
    asked:list[Any]=[]
    if session.multi_brain_settings.translation_enabled:
        for quote in quotes:
            if len(asked)>=SPOT_TRANSLATIONS_PER_TURN or quote.key in readings:
                continue
            if (sha,quote.label or "",quote.text) in session.reader_translation_refused:
                continue
            if _spot_attempt(key,quote.key,started):
                asked.append(quote)
    statuses:list[str]=[]
    if asked:
        answers=await asyncio.gather(
            *(_request_spot_translation(session,quote) for quote in asked),
            return_exceptions=True)
        for quote,answer in zip(asked,answers):
            if isinstance(answer,BaseException):
                _spot_failed(key,quote.key,started)
                statuses.append(f"provider_{type(answer).__name__}")
                continue
            answer=lab_korean(answer,fixture.localizations) if isinstance(answer,str) else answer
            issue=statement_issue(
                quote.text,answer,required_terms=quote.terms,
                step_label=quote.label if quote.kind=="step" else None)
            if issue is None:
                _spot_keep(key,quote.key,answer)
                readings[quote.key]=answer
                statuses.append("accepted")
            else:
                _spot_refuse(key,quote.key,issue)
                statuses.append(f"rejected_{issue}")
    return {quote.key:readings[quote.key] for quote in quotes if quote.key in readings},len(asked),statuses


def _put_readings(curated:CuratedProtocolSession,readings:Mapping[str,str])->bool:
    """Put checked in-place readings on the session's fixture; True when one was new.

    As stored machine Korean of the current translation style version (lane
    KO2): the step card, the next step line and later replies read them. A
    phrase's reading ("span:…") is no sentence of the fixture, and a
    reviewed sentence keeps its reviewed Korean. Presentation only.
    """

    fixture=curated.fixture
    reviewed=getattr(fixture,"localizations",None) or {}
    machine=dict(getattr(fixture,"machine_localizations",None) or {})
    added={
        quote_key:text for quote_key,text in readings.items()
        if not quote_key.startswith("span:") and quote_key not in reviewed
        and machine.get(quote_key)!=text
    }
    if not added:
        return False
    machine.update(added)
    versions=dict(getattr(fixture,"translation_versions",None) or {})
    versions.update({quote_key:TRANSLATION_STYLE_VERSION for quote_key in added})
    failures=dict(getattr(fixture,"translation_failures",None) or {})
    for quote_key in added:
        failures.pop(quote_key,None)
    vocabulary=curated._vocabulary_cache
    curated.fixture=replace(
        fixture,machine_localizations=machine,translation_versions=versions,
        translation_failures=failures or None)
    if vocabulary is not None and vocabulary[0] is fixture:
        # Presentation only: the protocol's vocabulary is the same.
        curated._vocabulary_cache=(curated.fixture,vocabulary[1])
    return True


#: Translation keys (protocol id / revision id) whose screen sentences are
#: being read in place now: the screen says "번역 준비 중" meanwhile.
_SCREEN_TRANSLATING:dict[str,int]={}


async def _korean_for_screen(session:ListenerSession,curated:CuratedProtocolSession)->bool:
    """The screen's step sentences in Korean (lane KO2, decision 3).

    The current and next step sentences that have no Korean
    (``CuratedProtocolSession.screen_untranslated``) are read in place on
    lane SAY's path (``_translate_in_place``: the process's readings first,
    the translation role only when on, once a sentence, checked) and put on
    the session's fixture. True when the fixture gained a reading. Nothing
    in the workflow changes.
    """

    quotes=curated.screen_untranslated()
    if not quotes:
        return False
    fixture=curated.fixture
    key=translation_revision_key(fixture)
    refused_before=_spot_refusals(key)
    # The turn's clock (``run_turn``): one base for the retry times kept in
    # ``_SPOT_ATTEMPTED``.
    started=time.perf_counter()
    with _SPOT_LOCK:
        _SCREEN_TRANSLATING[key]=_SCREEN_TRANSLATING.get(key,0)+1
    try:
        readings,asked,statuses=await _translate_in_place(session,fixture,quotes,started)
    finally:
        with _SPOT_LOCK:
            left=_SCREEN_TRANSLATING.get(key,1)-1
            if left>0:
                _SCREEN_TRANSLATING[key]=left
            else:
                _SCREEN_TRANSLATING.pop(key,None)
    if asked:
        # Counts and codes only: no sentence or reading reaches the log.
        log.info(
            "screen_korean quotes=%d asked=%d statuses=%s elapsed_ms=%d",
            len(quotes),asked,",".join(statuses) or "none",
            round((time.perf_counter()-started)*1000))
    if curated.fixture is not fixture:
        # Another update put readings on meanwhile; they are kept as they are.
        fixture_now=curated.fixture
        readings={
            quote_key:text for quote_key,text in readings.items()
            if quote_key not in (getattr(fixture_now,"machine_localizations",None) or {})}
    readings_changed=_put_readings(curated,readings)
    return readings_changed or _spot_refusals(key)!=refused_before


def _screen_translating(fixture:Any)->bool:
    try:
        key=translation_revision_key(fixture)
    except Exception:  # noqa: BLE001 - a fixture without ids has none
        return False
    with _SPOT_LOCK:
        return key in _SCREEN_TRANSLATING


def _queue_screen_korean(
    session:ListenerSession,curated:CuratedProtocolSession,
    send:Callable[...,Awaitable[Any]],
)->None:
    """After a fixture state: put the screen's untranslated sentences in Korean, then redraw.

    In the background, so neither the reply nor the screen waits: the state
    already sent says ``translation_missing`` and carries the source; when a
    checked reading arrives the same state is sent again
    (``action="translation_update"``) with the Korean -- only while the
    session is on the same configuration and protocol session.
    """

    if not curated.screen_untranslated():
        return
    configuration_id=session.accepted_configuration_id

    async def work()->None:
        try:
            changed=await _korean_for_screen(session,curated)
        except Exception as exc:  # noqa: BLE001 - the source stays on the screen
            log.warning("screen_korean failed error=%s",type(exc).__name__)
            return
        if (
            not changed or not session.active
            or session.curated_protocol_session is not curated
            or session.accepted_configuration_id!=configuration_id
        ):
            return
        try:
            await send(
                "protocol.fixture.state",configuration_id=configuration_id,
                action="translation_update",state=curated.state(),
                screen=curated_screen_fields(curated))
        except Exception as exc:  # noqa: BLE001 - a closed page misses a redraw
            log.info("screen_korean redraw_skipped error=%s",type(exc).__name__)

    session.track_visual_task(asyncio.create_task(work()))


async def _fresh_purpose(
    session:ListenerSession,curated:CuratedProtocolSession,plan:Any,*,
    turn_id:int,generation:int,clock:Callable[[],float],
)->Any:
    """A purpose answer in the current translation style (lane KO2, decision 6).

    When the purpose's Korean is of an older style version (lane GE's
    "이것은 … 귀하의 …"), or there is none, it is read in place on lane SAY's
    path; a checked reading goes on the session's fixture and the answer is
    composed again from it. Otherwise the answer stays as it was (the older
    Korean, or the source with "원문에 적힌 실험 목적").
    """

    if (getattr(plan,"intent_kind",None)!="protocol_purpose"
            or plan.action is not CuratedProtocolAction.PROTOCOL_QUERY):
        return plan
    quote=curated.purpose_quote()
    if quote is None:
        return plan
    started=clock()
    readings,asked,statuses=await _translate_in_place(
        session,curated.fixture,(quote,),started)
    if asked:
        log.info(
            "purpose_korean turn_id=%s asked=%d statuses=%s elapsed_ms=%d",
            turn_id,asked,",".join(statuses) or "none",round((clock()-started)*1000))
    if not session.is_current(turn_id,generation) or not _put_readings(curated,readings):
        return plan
    return curated.with_purpose_again(plan,language="ko")


def _reply_complete_fields(plan:Any,*,speech_text:str|None,speech_policy:str)->dict[str,Any]:
    """What ``reply.complete`` carries besides the turn (lane KO2, decision 4).

    The display and its document as before, with what the delta carried for
    the screen's caption: the words said (``speech_text``; none when the
    reply is not spoken) and the Korean primary text. ``translation_missing``
    says a source sentence is shown for want of Korean.
    """

    return {
        "text":plan.display_text,
        # Lane F, decision 3: the delta's document again, so the screen
        # need not keep the delta's copy.
        "display_document":getattr(plan,"display_document",None),
        "primary_text":plan.primary_text,
        "speech_text":speech_text if speech_policy=="speak" else None,
        "translation_missing":bool(getattr(plan,"translation_missing",False)),
    }


def _with_spot_readings(
    session:ListenerSession,curated:CuratedProtocolSession,reading:Any,
    readings:Mapping[str,str]|None=None,
)->Any:
    """The reply, with the readings it used put on the session's fixture."""

    plan=reading.plan
    if not reading.readings_used:
        return plan
    readings=readings or _spot_readings(translation_revision_key(curated.fixture))
    _put_readings(curated,{
        quote_key:readings[quote_key] for quote_key in reading.readings_used
        if quote_key in readings})
    if any(quote_key.startswith("span:") for quote_key in reading.readings_used) and not (
            session.auto_translation_announced):
        # A phrase's reading is not a stored sentence the labelling below
        # knows: it is announced here, once a session.
        session.auto_translation_announced=True
        plan=replace(plan,speech_text=f"{READER_TRANSLATION_SPOKEN_LEAD} {plan.speech_text}")
    return plan


async def _queue_curated_research(
    *,session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    endpoint:float,clock:Callable[[],float],curated:CuratedProtocolSession,
    context:dict[str,Any],turn_language:str,pre_transition_index:int|None,
    plan:Any,source_output:Any=None,
) -> None:
    configuration_id=session.accepted_configuration_id
    if not session.owns_research_result(turn_id,generation,configuration_id):
        return

    async def report_state(report:dict)->None:
        public=_public_experiment_report_state(report)
        store=session.experiment_report_store
        public["reports"]=(await asyncio.to_thread(store.list_reports,session_id=session.session_id)
                           if store is not None else [])
        if session.owns_research_result(turn_id,generation,configuration_id):
            await sender.text(
                "experiment.report.state",turn_id=turn_id,generation=generation,
                configuration_id=configuration_id,session_id=session.session_id,report=public)

    async def worker() -> None:
        research_plan=None
        ctx=context
        if (
            isinstance(source_output,SourceBrainOutput)
            and source_output.needs_research
            and source_output.query
        ):
            ctx={**ctx,"reference_query":plan_research_query(
                source_output.query,
                protocol_title=curated.fixture.title,
                step_label=ctx["step"].source_label,
                step_text=ctx["step"].instruction_source_text,
                evidence_texts=tuple(fact.text for fact in ctx["facts"]),
                requested_entity=plan.requested_entity,
                requested_entities=plan.requested_entities,
                question_kind=plan.question_kind,
                question_dimensions=plan.question_dimensions,
                **curated.research_scope(),
            )}

        research_budget=min(120.0,max(
            10.0,
            (
                session.supplemental_knowledge_settings.timeout_seconds
                if session.supplemental_knowledge_settings.enabled else 0.0
            ),
        ))
        research_deadline=clock()+research_budget
        def research_remaining(cap:float)->float:
            return max(0.05,min(cap,research_deadline-clock()))

        # Lane R6, decision 8: a step is announced only once it has something
        # to show, so nothing on the screen waits for an answer that can only
        # end in "웹 참고 자료 확인 제한". (The xAI web reference search that
        # once announced "pending" up front was deleted in lane DOC3.)
        announced=False
        async def announce(phase:str)->None:
            nonlocal announced
            announced=True
            await sender.text(
                "research.state",turn_id=turn_id,status="running",phase=phase,
                correlation_id=f"research-{generation}-{turn_id}",
            )

        # 3a. A short outside-PDF explanation, said after the rules' answer
        # (lane R6, decision 6, restoring D4).
        supplemental_result=None
        spoken_explanation=False
        if research_plan is None and ctx.get("outside_pdf") is not None:
            try:
                supplemental_role=RoleModel.from_environment("supplemental")
                supplemental_result=await asyncio.wait_for(
                    XaiSupplementalKnowledge(
                        _role_client(supplemental_role),
                        replace(
                            session.supplemental_knowledge_settings,
                            model=supplemental_role.model
                            or session.supplemental_knowledge_settings.model,
                        ),
                    ).explain_outside_pdf(
                        ctx["question"],subject=ctx["outside_pdf"]["subject"],
                        kind=ctx["outside_pdf"]["kind"],language=turn_language),
                    timeout=research_remaining(
                        session.supplemental_knowledge_settings.timeout_seconds),
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                supplemental_result={"status":"timeout_total"}
            except Exception as exc:
                log.info(
                    "outside-PDF explanation failed turn_id=%s category=%s",
                    turn_id,type(exc).__name__)
                supplemental_result={"status":"provider_error"}
            if not session.owns_research_result(turn_id,generation,configuration_id):
                return
            explanation=(
                supplemental_result.get("answer","")
                if supplemental_result.get("status")=="success" else ""
            )
            violations=(
                curated.outside_pdf_explanation_violations(
                    explanation,question=ctx["question"],gap=ctx["outside_pdf"])
                if explanation else ("no_explanation",)
            )
            if violations:
                log.info(
                    "outside-PDF explanation dropped turn_id=%s status=%s checks=%s",
                    turn_id,supplemental_result.get("status"),"+".join(violations))
            elif (
                (turn_id,generation) in session.held_audio_complete
                and clock()<ctx.get("speak_by",0.0)
            ):
                explained=curated.apply_outside_pdf_explanation(
                    turn_id=turn_id,language=turn_language,explanation=explanation,
                    retrieval_backend=supplemental_result["backend"],
                )
                pcm=await asyncio.to_thread(
                    synthesize,said(explained.speech_text),turn_language)
                frames=frame_complete_audio(pcm)
                if (
                    frames and session.is_current(turn_id,generation)
                    and (turn_id,generation) in session.held_audio_complete
                ):
                    # Lane VOX: after every sentence of the answer.
                    after=int(ctx.get("answer_segments",1))
                    await sender.segment(turn_id,after,frames,generation)
                    await _release_held_audio(sender,session,turn_id,generation,after+1)
                    research_plan=explained
                    spoken_explanation=True
            else:
                log.info(
                    "outside-PDF explanation late turn_id=%s elapsed_ms=%s",
                    turn_id,supplemental_result.get("elapsed_ms"))

        # 3. Supplemental model knowledge (Grok 4.6 explanation)
        if (
            research_plan is None
            and ctx.get("outside_pdf") is None
            and not ctx.get("source_answers_quantity")
            and session.supplemental_knowledge_settings.enabled
            and supplemental_knowledge_allowed(
                ctx["query"],plan.question_dimensions)
        ):
            supplemental_started=clock()
            try:
                supplemental_client=_role_client(
                    RoleModel.from_environment("supplemental"))
                supplemental_result=await asyncio.wait_for(
                    XaiSupplementalKnowledge(
                        supplemental_client,
                        session.supplemental_knowledge_settings,
                    ).explain(
                        # Lane F, decision 2: with the step the rules answered.
                        supplemental_explanation_query(
                            ctx["reference_query"],
                            step_label=ctx["step"].source_label,
                            step_text=ctx["step"].instruction_source_text),
                        language=turn_language),
                    timeout=research_remaining(
                        session.supplemental_knowledge_settings.timeout_seconds),
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                supplemental_result={"status":"timeout_total"}
            except Exception as exc:
                log.info(
                    "supplemental explanation failed turn_id=%s category=%s",
                    turn_id,type(exc).__name__)
                supplemental_result={"status":"provider_error"}
            if not session.owns_research_result(turn_id,generation,configuration_id):
                return
            if supplemental_result.get("status")=="success":
                research_plan=curated.apply_supplemental_answer(
                    turn_id=turn_id,language=turn_language,
                    primary_text=supplemental_result["answer"],
                    retrieval_backend=supplemental_result["backend"],
                )

        if research_plan is not None and session.owns_research_result(turn_id,generation,configuration_id):
            if not announced:
                await announce(
                    "supplemental_model"
                    if research_plan.answer_origin=="supplemental_model_knowledge"
                    else "approved_references")
            outside_pdf_fields=(
                # Lane R6, decision 6: where the words come from, for the
                # screen -- AI general knowledge, outside the PDF.
                {"source_label":"AI 일반 지식","outside_pdf":True,
                 "spoken":spoken_explanation}
                if "outside_pdf_explanation" in research_plan.limitations else {}
            )
            await _finish_research_operation(
                sender,session,turn_id,generation,"success",
                primary_text=research_plan.primary_text,
                answer_origin=research_plan.answer_origin,
                citations=list(research_plan.citations),
                retrieval_backend=research_plan.retrieval_backend,
                limitations=list(research_plan.limitations),
                **outside_pdf_fields,
            )
            if session.experiment_report_store is not None:
                report=await _voice_report_write(
                    _record_experiment_report_plan,
                    session,curated,research_plan,
                    turn_id=turn_id,generation=generation,
                    pre_transition_index=pre_transition_index,
                )
                await report_state(report)
        elif session.owns_research_result(turn_id,generation,configuration_id):
            status=(
                supplemental_result.get("status","disabled")
                if isinstance(supplemental_result,dict) else "disabled"
            )
            if (
                session.experiment_report_store is not None
                and status not in {"disabled","not_found","no_allowed_citation"}
                # An outside-PDF explanation left unsaid is not a research
                # failure: the rules' answer was the answer (decision 6).
                and ctx.get("outside_pdf") is None
            ):
                report=_open_experiment_report(session,curated)
                store=session.experiment_report_store
                assert store is not None and session.experiment_report_id
                report=await asyncio.to_thread(
                    store.append_event,session.experiment_report_id,
                    event_key=f"turn-{turn_id}-generation-{generation}-research-{status}",
                    event_type="system_anomaly",
                    step_id=ctx["step"].step_id,
                    step_label=ctx["step"].source_label,
                    category="external_research_failure",
                    severity="development_diagnostic",
                    confirmation_state="server_observed",
                    payload={"status":status,"state_mutation":False},
                )
                await report_state(report)
            if not announced:
                # Nothing was shown as pending, so nothing is shown as
                # unfinished: the rules' answer stands alone (decision 8).
                session.finish_research(turn_id,generation)
                return
            await _finish_research_operation(
                sender,session,turn_id,generation,status,
                limitation=(
                    "현재 적용된 실험 PDF 근거는 유지했지만 요청한 추가 차원을 웹 참고 자료에서 확인하지 못했습니다."
                    if turn_language=="ko" else
                    "The protocol evidence remains available, but the additional dimension could not be verified."
                ),
            )

    async def worker_releasing_held_audio()->None:
        try:
            await worker()
        finally:
            await _release_held_audio(
                sender,session,turn_id,generation,
                int(context.get("answer_segments",1)))

    task=asyncio.create_task(worker_releasing_held_audio())
    session.track_visual_task(task)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        pass


def _safety_scope_record(session:ListenerSession)->dict[str,object]:
    """The start record's words about the institution safety documents.

    Nothing for a run that has them; for one that has none, the scope and the
    server's sentence, so the record says the experimenter started without them.
    """

    scope=getattr(getattr(session,"tool_context",None),"usage_scope",None)
    if not _source_protocol_only(scope):
        return {}
    return {
        "safety_scope":SOURCE_PROTOCOL_ONLY_SCOPE,
        "institution_safety_notice":NO_INSTITUTION_DOCUMENTS_NOTICE,
    }


def _open_experiment_report(
    session:ListenerSession,
    curated:CuratedProtocolSession,
)->dict:
    store=session.experiment_report_store
    if store is None:
        raise RuntimeError("experiment reports are disabled")
    if session.experiment_report_id is None:
        _migrate_report_ownership()
        principal=_REQUEST_PRINCIPAL.get() if _workspace_settings().enabled else None
        report=store.open_report(
            session_id=session.session_id,
            protocol_id=curated.fixture.protocol_id,
            protocol_title=curated.fixture.title,
            protocol_revision=curated.fixture.revision_id,
            protocol_sha256=curated.fixture.source_pdf_sha256 or "",
            readiness_status=curated.fixture.draft.readiness.status.value,
            development_only=curated.fixture.development_only,
            organization_id=getattr(principal,"organization_id",None),
            owner_principal_id=getattr(principal,"principal_id",None),
        )
        session.experiment_report_id=report["report_id"]
        _scope_tenant_resource(
            "experiment_report",session.experiment_report_id,bind=True)
        continuation=_report_checkpoint_continuation(
            session.session_id,curated.fixture.protocol_id,curated.fixture.revision_id)
        if continuation is not None:
            store.append_event(
                session.experiment_report_id,
                event_key="checkpoint-continuation",
                event_type="checkpoint_continuation",
                source_tier="operator",payload=continuation)
        _pilot_mark("experiment_opened",session=session)
        experimenter=_experimenter_display_name()
        if experimenter:
            # Lane RP, decision 5: the report's 실험자 is the display name of
            # the person who started the experiment. The first one recorded
            # stands (the key is fixed); none is written when nobody is named.
            store.append_event(
                session.experiment_report_id,
                event_key="experimenter",
                event_type="experimenter_recorded",
                payload={"display_name":experimenter},
            )
        shown=getattr(session,"safety_notices_shown",None)
        if shown is not None:
            # The one human confirmation of the MVP rule (decision of
            # 2026-10-08): the experimenter pressed start on the screen that
            # showed the source's safety statements. What was shown is
            # recorded in the document's own words.
            store.append_event(
                session.experiment_report_id,
                event_key="safety-notices-acknowledged",
                event_type="safety_notices_acknowledged",
                payload={
                    "notice_count":len(shown),
                    "notices":[
                        {
                            "step_label":item.get("step_label"),
                            "source_page_number":item.get("source_page_number"),
                            "source_text":item.get("source_text"),
                        }
                        for item in shown
                    ],
                    "confirmed_by":"experimenter_start",
                    **_safety_scope_record(session),
                },
            )
        basis=curated.source_basis() if callable(getattr(curated,"source_basis",None)) else None
        if basis:
            # Lane CF, decision 5: the file the run followed and its upload.
            store.append_event(
                session.experiment_report_id,
                event_key="protocol-basis",
                event_type="protocol_basis_recorded",
                payload={
                    "filename":basis.get("filename"),
                    "uploaded_at":basis.get("uploaded_at"),
                    "words":basis.get("words"),
                    "source_disclosure":basis.get("source_disclosure"),
                },
            )
    return store.get_report(session.experiment_report_id)


def _record_experiment_report_plan(
    session:ListenerSession,
    curated:CuratedProtocolSession,
    plan,
    *,
    turn_id:int,
    generation:int,
    pre_transition_index:int|None=None,
)->dict:
    report=_open_experiment_report(session,curated)
    store=session.experiment_report_store
    assert store is not None and session.experiment_report_id is not None
    event_key=(
        f"turn-{turn_id}-generation-{generation}-{plan.action.value}"
    )
    pre_step=(
        curated.fixture.steps[pre_transition_index]
        if pre_transition_index is not None
        and 0 <= pre_transition_index < len(curated.fixture.steps)
        else None
    )
    post_step=(
        curated.fixture.steps[curated.current_index]
        if curated.active else None
    )
    step=(
        pre_step
        if plan.action in {CuratedProtocolAction.NEXT,CuratedProtocolAction.STOP}
        else post_step or pre_step
    )
    step_id=step.step_id if step is not None else None
    step_label=step.source_label if step is not None else plan.step_label
    event_type=None
    payload={
        "intent_kind":plan.intent_kind,
        "state_changed":bool(plan.state_changed),
        "answer_origin":plan.answer_origin,
        "development_only":curated.fixture.development_only,
        "pre_transition_step_id":pre_step.step_id if pre_step else None,
        "post_transition_step_id":post_step.step_id if post_step else None,
        "reported_observation":bool(plan.reported_observation),
        "observation_predicate":plan.observation_predicate,
        "observation_outcome":plan.observation_outcome,
        "timers":{
            "experiment":curated.experiment_timer_status(),
            "step":curated.timer_status(),
        },
        "workflow_status":curated._workflow_status,
    }
    timer_payload=getattr(plan,"timer_payload",None)
    if isinstance(timer_payload,dict) and timer_payload:
        payload["timer"]=timer_payload
    # Lane RP3, decision ②: what the turn said for the notebook, as its own
    # event; a correction of it is appended beside what it amends (lane N).
    notebook=getattr(plan,"notebook_record",None) or {}
    if notebook.get("kind")=="purpose":
        event_type="purpose_recorded"
        payload["samples"]=[dict(item) for item in notebook.get("samples") or () if isinstance(item,dict)]
    elif notebook.get("kind")=="result":
        event_type="result_recorded"
        payload["result"]=str(notebook.get("result") or "")
        payload["next_change"]=str(notebook.get("next_change") or "")
    elif notebook.get("kind")=="observation":
        event_type="observation"
        if notebook.get("step_label"):
            step_id=notebook.get("step_id") or step_id
            step_label=str(notebook.get("step_label"))
    elif notebook.get("kind")=="fix":
        wanted={
            "purpose":"purpose_recorded",
            "result":"result_recorded",
            "observation":"observation",
        }.get(str(notebook.get("target") or ""),"result_recorded")
        amended=next(
            (item for item in reversed(report.get("events") or ()) if item.get("event_type")==wanted),
            None,
        )
        if amended is None:
            raise LookupError("nothing of that kind was said for the notebook")
        event_type="record_corrected"
        step_id=amended.get("step_id")
        step_label=amended.get("step_label")
        payload["record_fix"]={
            "target_event_key":amended.get("event_key"),
            "text_before":amended.get("user_wording"),
            "text_after":notebook.get("text"),
            "source":"voice",
        }
    elif plan.action is CuratedProtocolAction.START and plan.state_changed:
        event_type="session_started"
    elif plan.action is CuratedProtocolAction.NEXT and plan.state_changed:
        event_type=(
            "step_completed" if plan.reported_completion else "step_advanced"
        )
        if plan.reported_completion:
            payload["completion_source"]="user_command"
    elif plan.action is CuratedProtocolAction.NEXT and plan.speech_mode.value=="blocked":
        event_type=("observation" if plan.reported_observation else "blocked")
    elif plan.action is CuratedProtocolAction.RECORD_OBSERVATION:
        event_type="observation"
    elif plan.action is CuratedProtocolAction.REPORT_ANOMALY:
        event_type="anomaly"
    elif (
        plan.action is CuratedProtocolAction.RECORD_CORRECTION
        and getattr(plan,"record_fix",None)
    ):
        # Lane N, decision 2: a correction is appended beside the record it
        # amends, which stays as it was said.
        fix=plan.record_fix
        target=fix.get("target") or {}
        target_key=(
            f"turn-{target.get('turn_id')}-generation-{target.get('generation')}-"
            f"{target.get('action')}"
        )
        amended=next(
            (item for item in report.get("events") or () if item.get("event_key")==target_key),
            None,
        )
        if amended is None:
            raise LookupError("the record to correct is not in the experiment report")
        event_type=(
            "record_retracted" if fix.get("mode")=="retract" else "record_corrected"
        )
        step_id=amended.get("step_id")
        step_label=amended.get("step_label")
        payload["record_fix"]={
            "target_event_key":target_key,
            "text_before":fix.get("before"),
            "text_after":fix.get("after"),
            "source":"voice",
        }
    elif plan.action is CuratedProtocolAction.STOP and plan.state_changed:
        event_type="session_stopped"
        payload["stop_reason"]="stopped_by_user"
    # Lane N, decision 4: a timer start, a pause and a resume are kept when
    # they took effect -- asked again ("타이머가 이미 진행 중입니다") or
    # refused, nothing started or stopped, so nothing is kept.
    elif plan.action is CuratedProtocolAction.START_TIMER and plan.state_changed:
        event_type="timer_started"
    elif plan.action in _TIMER_EVENT_TYPES and plan.state_changed:
        # Lane HEAR2, decision 3: a timer paused, resumed, cancelled or run
        # again by voice, with the timer as it then stood.
        event_type=_TIMER_EVENT_TYPES[plan.action]
    elif plan.action is CuratedProtocolAction.PAUSE and plan.state_changed:
        event_type="workflow_paused"
    elif plan.action is CuratedProtocolAction.RESUME and plan.state_changed:
        event_type="workflow_resumed"
    elif plan.action in {
        CuratedProtocolAction.CURRENT,CuratedProtocolAction.REPEAT,
        CuratedProtocolAction.FULL_DETAIL,CuratedProtocolAction.PROTOCOL_QUERY,
        CuratedProtocolAction.PREVIEW_STEP,CuratedProtocolAction.STEP_RANGE,
        CuratedProtocolAction.LAB_DOMAIN_QA,
    }:
        event_type="step_presented"
    elif plan.answer_origin in {
        "approved_lab_corpus","external_authoritative_reference",
    }:
        event_type="source_consulted"
    # Lane R7, decisions 2-3: what a step move or a step done again keeps.
    # A return within a repeat is its own event, at the step returned to,
    # with its round by confirmed returns; a later start records the steps it
    # skipped (after the start itself when it began the experiment); a step
    # completed again says in which round.
    record=getattr(plan,"step_record",None) or {}
    move_kind=record.get("kind")
    skipped_after_start=False
    if record:
        payload["step_record"]=record
    if plan.state_changed and move_kind in {"repeat_return","repeat_round"}:
        event_type="repeat_returned"
        step_id=post_step.step_id if post_step is not None else step_id
        step_label=post_step.source_label if post_step is not None else step_label
    elif plan.state_changed and move_kind=="step_revert":
        # Lane CF, decision 3: at the step gone back to; the completions it
        # takes back stay in the record.
        event_type="step_reverted"
        step_id=post_step.step_id if post_step is not None else step_id
        step_label=post_step.source_label if post_step is not None else step_label
    elif plan.state_changed and move_kind=="start_at_step":
        if record.get("experiment_started"):
            skipped_after_start=True
        else:
            event_type="steps_skipped"
    # Lane CB (decisions 1-3): a source condition answered, with where its
    # value came from, at the step it was asked; a repeat's count given by
    # voice; a round declined, and an open count closed, after the completion
    # that carried them out; steps a "no" passed over, after the arrival.
    elif move_kind=="branch_answer":
        event_type="branch_answered"
        step_id=str(record.get("step_id") or step_id or "")
        step_label=str(record.get("step_label") or step_label or "")
    elif move_kind=="repeat_registered":
        event_type="repeat_registered"
    elif plan.state_changed and move_kind in {"run_value_changed","run_value_reverted"}:
        # Lane EASY, decision 2: at the step whose value changed, with the
        # analysis or review version it was changed on; a put-back is its own
        # event beside the change, which stays in the record.
        event_type=move_kind
        step_id=str(record.get("step_id") or step_id or "")
        step_label=str(record.get("step_label") or step_label or "")
    if event_type=="session_started" and curated.run_changes():
        # Lane EASY, decision 2: the values the start applied for today.
        payload["run_changes"]=[change.public_dict() for change in curated.run_changes()]
        payload["run_change_set_id"]=curated.run_change_set_id
        lab_revision=_report_lab_revision(
            curated.fixture.protocol_id,curated.run_change_set_id)
        if lab_revision is not None:
            payload["lab_adapted_revision"]=lab_revision
    followed_by=(
        "repeat_rounds_declined" if move_kind=="repeat_declined" else
        "repeat_closed" if move_kind=="repeat_closed" else
        "branch_steps_skipped" if move_kind=="branch_steps_skipped" else None
    )
    wording=plan.anomaly_text or plan.observation_outcome
    category=plan.anomaly_category or plan.observation_predicate
    if notebook:
        wording=str(notebook.get("text") or "")
        category="observed" if notebook.get("kind")=="observation" else None
    elif event_type in {"record_corrected","record_retracted"}:
        fix=plan.record_fix
        wording=fix.get("after") if event_type=="record_corrected" else fix.get("before")
        category=amended.get("category")
    elif event_type=="observation" and getattr(plan,"note_record",None):
        # Where the same note sits on the experiment timeline, so a fix made
        # on the screen later amends both (lane N, decision 2).
        payload["workspace_event_key"]=(
            f"voice-{generation}-{turn_id}-observation-{plan.action.value}"
        )
        if plan.note_record.get("experimenter_confirmed") is True:
            # Lane VX, decision 3: the value was said "네" to in the 바로
            # 확인 way; the end-of-experiment review does not ask it again.
            payload["experimenter_confirmed"]=True
        deviations=[
            dict(item) for item in plan.note_record.get("deviations") or ()
            if isinstance(item,dict)
        ]
        if deviations:
            # Lane FX3, decision 5: the values that differ from the source's
            # (said, number, unit, the source's value), for the record after
            # the run as the run card showed them.
            payload["deviations"]=deviations
    if event_type in {"observation","anomaly"} and wording:
        # Lane RP3, decision ② b: a note that names a sample carries it.
        named=sample_in_text(wording)
        if named:
            payload["sample"]=named
    if event_type is not None:
        report=store.append_event(
            session.experiment_report_id,
            event_key=event_key,
            event_type=event_type,
            step_id=step_id,
            step_label=step_label,
            user_wording=wording,
            category=category,
            severity=("unknown" if plan.reported_anomaly else None),
            confirmation_state=(
                str(notebook.get("confirmation_state"))
                if notebook.get("confirmation_state") else
                "experimenter_confirmed"
                if (getattr(plan,"note_record",None) or {}).get("experimenter_confirmed") else
                "user_reported"
                if plan.reported_anomaly or plan.reported_observation else None
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
    if plan.state_changed and move_kind=="prestart_answers":
        # Lane CF, decision 4: the answers given before the start, each as
        # lane CB records an answer at its step, in the protocol's order.
        order={item.step_id:index for index,item in enumerate(curated.fixture.steps)}
        by_label={item.source_label:item for item in curated.fixture.steps}
        answered=[
            ("branch_answered",item,str(item.get("step_label") or ""))
            for item in record.get("answers") or ()
        ]+[
            ("repeat_registered",item,str((item.get("repeated_step_labels") or [""])[0]))
            for item in record.get("registered") or ()
        ]
        answered.sort(key=lambda entry:order.get(
            getattr(by_label.get(entry[2]),"step_id",""),10_000))
        for kind,item,label in answered:
            step=by_label.get(label)
            report=store.append_event(
                session.experiment_report_id,
                event_key=f"{event_key}-before-start-{kind.replace('_','-')}-{label}",
                event_type=kind,
                step_id=step.step_id if step is not None else None,
                step_label=label,
                payload={**payload,"step_record":dict(item)},
            )
    if skipped_after_start:
        report=store.append_event(
            session.experiment_report_id,
            event_key=f"{event_key}-steps-skipped",
            event_type="steps_skipped",
            step_id=step_id,
            step_label=step_label,
            payload=payload,
        )
    if followed_by is not None and plan.state_changed:
        report=store.append_event(
            session.experiment_report_id,
            event_key=f"{event_key}-{followed_by.replace('_','-')}",
            event_type=followed_by,
            step_id=pre_step.step_id if pre_step is not None else step_id,
            step_label=pre_step.source_label if pre_step is not None else step_label,
            payload=payload,
        )
    if plan.action is CuratedProtocolAction.STOP and plan.state_changed:
        report=store.finalize(
            session.experiment_report_id,
            status="stopped",
            event_key=f"{event_key}-finalize",
        )
        _review_or_prepare_report(session,curated,store,session.experiment_report_id)
    elif (
        plan.action is CuratedProtocolAction.NEXT
        and plan.state_changed
        and not curated.active
        and curated._workflow_status=="completed"
    ):
        completed_key=f"{event_key}-workflow-completed"
        report=store.append_event(
            session.experiment_report_id,
            event_key=completed_key,
            event_type="workflow_completed",
            step_id=step_id,
            step_label=step_label,
            payload=payload,
        )
        report=store.finalize(
            session.experiment_report_id,
            status="completed",
            event_key=f"{event_key}-finalize",
        )
        _review_or_prepare_report(session,curated,store,session.experiment_report_id)
        _record_workspace_metric(
            category="workflow",metric_name="completion",
            dimensions={"event_kind":"workflow_completed","status":"completed"},
        )
    if event_type is not None:
        _record_workspace_metric(
            category="workflow",metric_name="report_event",
            dimensions={
                "event_kind":event_type,
                "status":"recorded",
                "step_bucket":str(step_label or "unlabeled")[:100],
            },
        )
    return report


def _review_or_prepare_report(
    session:ListenerSession,
    curated:CuratedProtocolSession,
    store:ExperimentReportStore,
    report_id:str,
)->None:
    """The ended experiment's values to confirm by voice, or its prose now.

    Lane N, decision 3: when the record holds values the report would state
    -- measurements, observations with a number, points done differently,
    anomalies -- the report's prose waits while the researcher confirms them
    one by one; the words that open the review are said after the end of the
    experiment. With none, the prose is prepared at once, as before.
    """

    items=[
        item for item in report_review_items(store.get_report(report_id)["events"])
        if not item["confirmed"]
    ]
    if items and callable(getattr(curated,"open_report_review",None)):
        REPORT_PROSE.hold(store,report_id)
        session.report_review_intro=curated.open_report_review(items,report_id=report_id)
        return
    _prepare_report_prose(store,report_id)


def _close_report_review(session:Any,*,source:str)->None:
    """A voice review left unanswered (the session closed): the rest goes to the screen."""

    curated=getattr(session,"curated_protocol_session",None)
    store=getattr(session,"experiment_report_store",None)
    report_id=getattr(curated,"report_review_report_id",None) if curated is not None else None
    if store is None or not report_id:
        return
    try:
        remaining=curated.report_review_remaining()
        store.append_event(
            report_id,
            event_key=f"review-closed-{session.generation}-{len(remaining)}",
            event_type=REVIEW_DEFERRED,
            payload={"item_ids":[item["item_id"] for item in remaining],"source":source},
        )
    except Exception as exc:  # noqa: BLE001 - the prose is prepared all the same
        log.warning("report review close not recorded error=%s",type(exc).__name__)
    REPORT_PROSE.release(store,report_id)
    _prepare_report_prose(store,report_id)


def _record_report_review(
    session:ListenerSession,
    plan:Any,
    *,
    turn_id:int,
    generation:int,
)->dict:
    """Store what one review turn answered (lane N, decision 3).

    A confirmation is its own event; a correction is appended beside the
    record it amends (decision 2), on the timeline too when the note is
    there, and then confirmed; a value left for later is listed. When the
    review is over the report's prose is prepared -- its one model call.
    """

    store=session.experiment_report_store
    review=plan.report_review or {}
    report_id=str(review.get("report_id") or "")
    if store is None or not report_id:
        raise LookupError("no experiment report to confirm")
    report=None
    for index,op in enumerate(review.get("ops") or ()):
        key=f"turn-{turn_id}-generation-{generation}-review-{index}"
        kind=op.get("op")
        if kind=="confirm":
            report=store.append_event(
                report_id,event_key=key,event_type=REVIEW_CONFIRMED,
                step_label=op.get("step_label") or None,
                user_wording=op.get("text") or None,
                payload={"item_id":op.get("item_id"),"text":op.get("text"),"source":"voice"},
            )
        elif kind in {"correct","retract"}:
            item=op.get("item") or {}
            after=op.get("after") if kind=="correct" else None
            report=store.append_event(
                report_id,event_key=f"{key}-fix",
                event_type="record_corrected" if kind=="correct" else "record_retracted",
                step_label=item.get("step_label") or None,
                user_wording=after or op.get("before") or None,
                payload={"record_fix":{
                    "target_event_key":item.get("source_key"),
                    "text_before":op.get("before"),
                    "text_after":after,
                    "source":"voice_review",
                }},
            )
            _mirror_timeline_fix(
                session,item.get("workspace_event_key"),after,
                event_key=f"voice-{generation}-{turn_id}-review-{index}",
            )
            if kind=="correct":
                report=store.append_event(
                    report_id,event_key=key,event_type=REVIEW_CONFIRMED,
                    step_label=item.get("step_label") or None,user_wording=after,
                    payload={"item_id":item.get("item_id"),"text":after,"source":"voice"},
                )
        elif kind=="defer":
            report=store.append_event(
                report_id,event_key=key,event_type=REVIEW_DEFERRED,
                payload={"item_ids":list(op.get("item_ids") or ()),"source":"voice"},
            )
    if review.get("closed"):
        REPORT_PROSE.release(store,report_id)
        _prepare_report_prose(store,report_id)
    return report or store.get_report(report_id)


def _mirror_timeline_fix(
    session:Any,workspace_event_key:Any,content:str|None,*,event_key:str,
)->None:
    """The same correction on the experiment timeline, when the note is there."""

    if not workspace_event_key or getattr(session,"experiment_state_version",None) is None:
        return
    settings=_workspace_settings()
    if not settings.enabled:
        return
    try:
        principal,store=_commercial_workspace()
    except Exception as exc:  # noqa: BLE001 - the report keeps the correction
        log.warning("timeline correction skipped error=%s",type(exc).__name__)
        return
    try:
        store.record_observation_fix(
            principal,session.session_id,event_key=event_key,
            target_event_key=str(workspace_event_key),content=content,
            capture_source="voice",
        )
        session.experiment_state_version=int(
            store.get_experiment(principal,session.session_id)["version"])
    except Exception as exc:  # noqa: BLE001 - the report keeps the correction
        log.warning("timeline correction not recorded error=%s",type(exc).__name__)
    finally:
        store.close()


_EXPERIMENT_REPORT_ACTIONS=frozenset({
    CuratedProtocolAction.START,CuratedProtocolAction.NEXT,
    CuratedProtocolAction.STOP,CuratedProtocolAction.QUESTION,
    CuratedProtocolAction.CURRENT,CuratedProtocolAction.REPEAT,
    CuratedProtocolAction.FULL_DETAIL,CuratedProtocolAction.NEXT_INFORMATION,
    CuratedProtocolAction.COMPLETION_CRITERIA,
    CuratedProtocolAction.OPERATIONAL_DEVIATION,
    CuratedProtocolAction.RECORD_OBSERVATION,
    CuratedProtocolAction.PROTOCOL_QUERY,CuratedProtocolAction.STEP_RANGE,
    CuratedProtocolAction.LAB_DOMAIN_QA,
    # Lane N, decision 4: the report's timer column and its "(나)" read
    # these; on the served path they never reached the report before.
    CuratedProtocolAction.START_TIMER,CuratedProtocolAction.PAUSE,
    CuratedProtocolAction.RESUME,
    # Lane HEAR2, decision 3: the timer's voice commands.
    *_TIMER_EVENT_TYPES,
})
#: Kept only when they took effect (lane N, decision 4): a timer asked for
#: again while it runs, or a pause or resume refused, leaves the report alone.
_REPORTED_WHEN_CHANGED=frozenset({
    CuratedProtocolAction.START_TIMER,CuratedProtocolAction.PAUSE,
    CuratedProtocolAction.RESUME,*_TIMER_EVENT_TYPES,
})


#: Lane CB: the records that reach the report although their turn moved
#: nothing -- a condition answered yes, a count given by voice.
_RECORDED_STEP_RECORD_KINDS=frozenset({"branch_answer","repeat_registered",
    # Lane EASY, decision 2: a value of today's run changed or put back.
    "run_value_changed","run_value_reverted"})


def _reportable(plan:Any)->bool:
    """Whether a turn's plan goes to the experiment report."""

    if (getattr(plan,"step_record",None) or {}).get("kind") in _RECORDED_STEP_RECORD_KINDS:
        return True
    if getattr(plan,"notebook_record",None):
        # Lane RP3, decision ②: what was said for the notebook.
        return True
    return plan.action in _EXPERIMENT_REPORT_ACTIONS and (
        plan.state_changed or plan.action not in _REPORTED_WHEN_CHANGED)


def _experiment_end_kind(plan:Any)->str|None:
    """How a turn ended the experiment: "completed", "stopped", or None."""

    if plan.action is CuratedProtocolAction.STOP and plan.state_changed:
        return "stopped"
    if (
        plan.action is CuratedProtocolAction.NEXT
        and plan.state_changed
        and plan.final_step
        and plan.speech_mode is CuratedProtocolSpeechMode.STOP
    ):
        return "completed"
    return None


def _experiment_ended_event(
    session:"ListenerSession",curated:Any,plan:Any,*,report:str,
)->dict[str,Any]|None:
    """What ``experiment.ended`` tells the screen, or None when nothing ended.

    Sent once, after the turn's ``protocol.fixture.state``, for the turn that
    ended the experiment -- the end confirmed by voice, or the last step
    completed -- and only when that end stands (a rolled-back end changed
    nothing). Lane R3 (decision 2 of 2026-10-03) fixes its name and content;
    what the screen does with it is the screen's work:

    * ``status``: "stopped" or "completed", as the session holds it;
    * ``step_label``: the step it ended at; ``step_count``: the protocol's;
    * ``ended_at``: ISO time from the session's experiment clock;
    * ``experiment_session_id``: the durable experiment, or None without one;
    * ``report``: "saved", "failed" (the end stands, the report did not
      save) or "not_recorded" (no report store);
    * ``bench_controls``: pause and resume are both refused from now on.
    """

    end=_experiment_end_kind(plan)
    if end is None or not plan.state_changed or not curated.experiment_ended:
        return None
    clock=curated.experiment_timer_status()
    return {
        "configuration_id":session.accepted_configuration_id,
        "status":curated.workflow_status,
        "step_label":plan.step_label or (
            curated.fixture.steps[curated.current_index].source_label
            if 0<=curated.current_index<len(curated.fixture.steps) else None
        ),
        "step_count":len(curated.fixture.steps),
        "ended_at":clock.get("ended_at"),
        "experiment_session_id":(
            session.session_id if session.experiment_state_version is not None else None
        ),
        "report":report,
        "bench_controls":{"pause":False,"resume":False},
    }


def _report_download_sentence(language:str)->str:
    """The report formats the screen offers now, said after a saved report.

    Markdown is always exported; Word only when python-docx is importable.
    A format the screen cannot give is not mentioned (decision 2).
    """

    word=importlib.util.find_spec("docx") is not None
    if language=="ko":
        return (
            "화면에서 Word나 마크다운 파일로 받을 수 있어요."
            if word else "화면에서 마크다운 파일로 받을 수 있어요."
        )
    return (
        "You can download it from the screen as Word or Markdown."
        if word else "You can download it from the screen as Markdown."
    )


def _with_experiment_end_sentence(plan:Any,sentence:str,*,spoken:str|None=None)->Any:
    """The end-of-experiment reply with ``sentence`` said right after it."""

    speech=plan.speech_text or ""
    display=plan.display_text or ""
    if speech and display.startswith(speech):
        display=f"{speech} {sentence}{display[len(speech):]}"
    else:
        display=f"{display}\n\n{sentence}" if display else sentence
    return replace(
        plan,display_text=display,
        speech_text=f"{speech} {spoken if spoken is not None else sentence}".strip())


def _before_notebook_question(text:str,sentence:str)->str:
    """``sentence`` before the notebook's end question when the reply ends
    with it (lane RP3, decision ② d), else after the reply."""

    if text.endswith(RESULT_QUESTION_KO):
        return f"{text[:-len(RESULT_QUESTION_KO)].rstrip()} {sentence} {RESULT_QUESTION_KO}"
    return f"{text} {sentence}"


def _acknowledge_report_persistence(
    plan:Any,language:str,*,review:tuple[str,str]|None=None,question:str|None=None,
)->Any:
    end=_experiment_end_kind(plan)
    if end is not None:
        # Decision 2 (2026-10-03): said only once the report store has taken
        # the experiment's end -- how it ended, then that it was saved.
        if language=="ko":
            saved=(
                "실험 기록을 보고서로 저장했어요."
                if end=="completed" else
                "지금까지의 기록을 보고서로 저장했어요."
            )
        else:
            saved=(
                "The experiment record was saved as a report."
                if end=="completed" else
                "The record so far was saved as a report."
            )
        if review is not None:
            # Lane N, decision 3: the values are confirmed first; where to
            # download it is said when the review is over.
            sentence,spoken=f"{saved} {review[0]}",f"{saved} {review[1]}"
        else:
            sentence=f"{saved} {_report_download_sentence(language)}"
            if question:
                # Lane RP3, decision ② d: nothing to confirm, so the result
                # and the next change are asked right after the end.
                sentence=f"{sentence} {question}"
            spoken=None
        if end=="completed" and plan.reported_observation:
            observed=(
                "말씀한 관찰 결과도 실험 기록에 반영했습니다. "
                if language=="ko" else
                "The reported observation was added to the experiment record. "
            )
            sentence=observed+sentence
            spoken=observed+spoken if spoken is not None else None
        return _with_experiment_end_sentence(plan,sentence,spoken=spoken)
    if (
        plan.action is CuratedProtocolAction.NEXT
        and plan.state_changed
        and plan.reported_completion
    ):
        acknowledgment=(
            (
                "말씀한 관찰 결과와 현재 단계 완료를 실험 기록에 반영했습니다."
                if plan.reported_observation else
                "현재 단계 완료를 실험 기록에 반영했습니다."
            )
            if language=="ko" else
            (
                "I added the reported observation and current-step completion to the experiment record."
                if plan.reported_observation else
                "I added the current-step completion to the experiment record."
            )
        )
        return replace(
            plan,
            display_text=f"{acknowledgment}\n\n{plan.display_text}",
            speech_text=f"{acknowledgment} {plan.speech_text}",
        )
    if (
        plan.action is CuratedProtocolAction.NEXT
        and plan.reported_observation
        and plan.observation_predicate=="negative"
    ):
        acknowledgment=(
            "말씀한 관찰 결과를 현재 단계 실험 기록에 남겼습니다."
            if language=="ko" else
            "I added the reported observation to the current-step experiment record."
        )
        return replace(
            plan,
            display_text=f"{acknowledgment}\n\n{plan.display_text}",
            speech_text=f"{acknowledgment} {plan.speech_text}",
        )
    return plan


def _notebook_end_question(curated:Any,plan:Any,language:str)->str|None:
    """The notebook's end question when the turn ended the experiment and
    no value is to be confirmed first (lane RP3, decision ② d)."""

    if language!="ko" or _experiment_end_kind(plan) is None:
        return None
    if getattr(curated,"report_review_report_id",None):
        return None
    opener=getattr(curated,"open_notebook_question",None)
    return opener("result") if callable(opener) else None


def _take_review_intro(session:Any)->tuple[str,str]|None:
    """The review's opening words, once, after the end of the experiment."""

    intro=getattr(session,"report_review_intro",None)
    session.report_review_intro=None
    return intro


def _public_experiment_report_state(report:dict)->dict:
    events=list(report.get("events") or ())
    # Lane N, decision 2: a note shows its latest words and that it was
    # amended; the event itself keeps the words as first said.
    amended=record_amendments(events)
    events=[
        {**event,"amended":amended[event["event_key"]]}
        if event.get("event_key") in amended else event
        for event in events
    ]
    return {
        key:report.get(key) for key in (
            "report_id","status","started_at","ended_at","anomaly_count",
            "blocker_count","finalization_version","development_only",
            "session_id","protocol_id",
            # Lane R6, decision 5: what the screen names the record by.
            "protocol_title","day_sequence","day_sequence_date","timezone",
        )
    } | {
        "event_count":len(events),"events":events,
        # Lane N, decision 3: the screen's checklist of the report's values.
        "review":report_review_items(events),
        # Lane RP3, decision ④: the PI's confirmations, for the screen's button.
        "pi_confirmations":pi_confirmations(events),
    }


def _research_terminal_status(status:str)->str:
    if status == "success": return "success"
    if status in {"cancelled","superseded"}: return status
    if status in {"disabled","unavailable","credentials_unavailable"}:
        return "unavailable"
    if status.startswith("timeout_"): return "timeout"
    return "failed"


async def _finish_research_operation(
    sender:LockedSender,
    session:ListenerSession,
    turn_id:int,
    generation:int,
    status:str,
    **fields,
)->bool:
    """Emit exactly one terminal result for one owned read-only operation."""

    if not session.finish_research(turn_id,generation): return False
    await sender.text(
        "research.result",
        configuration_id=session.accepted_configuration_id,
        turn_id=turn_id,
        generation=generation,
        status=status,
        terminal_status=_research_terminal_status(status),
        correlation_id=f"research-{generation}-{turn_id}",
        **fields,
    )
    return True


# --- Help with a problem (lane HELP, 2026-10-24) -----------------------------------

#: How long a problem's sources may take; a later one is left out of the help
#: (the protocol and the history are searched in this process).
PROBLEM_HELP_SEARCH_SECONDS=1.5
#: After the answer has played, how long the possible causes (③) and the
#: closing (④) they hold may take before the held audio.complete goes anyway.
PROBLEM_CAUSES_GRACE_SECONDS=10.0


def _warm_problem_knowledge(curated:CuratedProtocolSession)->None:
    """Index the protocol's sentences ahead of the first problem; a failure
    is left for the turn that needs the index, which builds it itself."""

    try:
        curated.problem_knowledge()
    except Exception as exc:
        log.info("problem knowledge index not built ahead category=%s",type(exc).__name__)


def _problem_help_sources(
    session:ListenerSession,curated:CuratedProtocolSession,marker:Mapping[str,Any]|None=None,
)->list[Any]:
    """The protocol, the account's earlier runs (with a record) and the
    public source when one is connected (lane KNOW); nothing else.

    The public source is asked with the problem's query -- its words of what
    went wrong and the names it is about, the person's and the current
    step's key words -- not the sentence, and is left out when there is
    nothing to look for (lane KNOW2, person's decision 2026-10-26, 1).
    """

    sources:list[Any]=[curated.problem_knowledge()]
    if marker is not None and marker.get("kind")=="grounded_question":
        if curated.safety_pack is not None:
            sources.append(SafetyKnowledge(curated.fixture,curated.safety_pack))
        # This answer is bounded to the current protocol revision and linked
        # approved safety documents. Earlier runs and public sources are not
        # evidence for it.
        return sources
    store=session.experiment_report_store
    if store is not None and session.experiment_report_id:
        held=session.problem_history
        if held is None or held.report_id!=session.experiment_report_id or held.store is not store:
            held=HistoryKnowledge(
                store,session.experiment_report_id,
                step_labels={step.step_id:step.source_label for step in curated.fixture.steps})
            session.problem_history=held
        sources.append(held)
    public=session.public_knowledge
    if public is not None:
        if marker is None or not hasattr(public,"for_problem"):
            sources.append(public)
        else:
            index=marker.get("step_index")
            step_text=(curated.fixture.steps[int(index)].instruction_source_text
                       if index is not None else "")
            query=problem_query(str(marker.get("words") or ""),step_text,public.glossary)
            if query is not None:
                sources.append(public.for_problem(query))
    return sources


def _problem_causes_allowed(session:ListenerSession,plan:Any,language:str)->bool:
    """③ from a model: only with the supplemental role on, for a spoken Korean
    or English help, and never for a spill, an exposure or a contamination."""

    marker=getattr(plan,"problem_help",None) or {}
    return bool(
        session.supplemental_knowledge_settings.enabled
        and language in {"ko","en"}
        and getattr(plan,"speech_policy","speak")=="speak"
        and not marker.get("safety")
        and marker.get("step_index") is not None
    )


async def _with_problem_help(
    session:ListenerSession,curated:CuratedProtocolSession,plan:Any,*,
    language:str,timings:dict[str,Any],turn_id:int,generation:int,
)->tuple[Any,dict[str,Any]|None]:
    """The help put after a problem's reply (decision 2), and ③'s job if any.

    The sources are searched at once (``knowledge.search_tiers``); the help
    is their own words and fixed sentences (``compose_problem_help``),
    checked (``problem_help_violations``) and put after the reply as it is
    -- an anomaly's read-back first -- or as the whole reply of a problem
    asked about. With ③ to follow, ④ is held for after it. Nothing moves.
    """

    marker=dict(plan.problem_help)
    sources=_problem_help_sources(session,curated,marker)
    history=next((item for item in sources if getattr(item,"tier","")=="history"),None)
    organization_id=account_id=None
    if history is not None:
        try:
            organization_id,account_id=await asyncio.to_thread(history.scope)
        except Exception as exc:
            log.info("problem help history scope unavailable category=%s",type(exc).__name__)
            sources=[item for item in sources if item is not history]
    context=curated.problem_help_context(
        marker,language=language,organization_id=organization_id,account_id=account_id)
    found=await search_tiers(
        sources,str(marker.get("words") or ""),context=context,limit=4,
        timeout_seconds=PROBLEM_HELP_SEARCH_SECONDS)
    timings["knowledge_search_ms"]=max(found.elapsed_ms.values(),default=0.0)
    grounded_question=marker.get("kind")=="grounded_question"
    selector_status="not_applicable"
    if grounded_question:
        session.grounded_answer_turns.add((turn_id,generation))
        eligible=tuple(hit for hit in found.hits if hit.tier in {"protocol","lab"})
        if not eligible:
            selector_status="no_evidence"
        elif session.multi_brain_settings.answer_brain_enabled:
            started=session.clock()
            try:
                role=RoleModel.from_environment("answer")
                selected=await asyncio.wait_for(
                    select_curated_protocol_answer(
                        _role_client(role),str(marker.get("words") or ""),
                        language=language,
                        protocol_id=curated.fixture.protocol_id,
                        protocol_title=curated.fixture.title,
                        step_label=curated.fixture.steps[curated.current_index].source_label,
                        facts=tuple(
                            (f"grounded_{index}",hit.detail("kind") or hit.tier,hit.text)
                            for index,hit in enumerate(eligible)
                        ),
                    ),
                    timeout=session.multi_brain_settings.answer_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The exact source search remains usable; no other provider is
                # substituted and no model prose reaches the answer.
                selector_status=f"provider_{type(exc).__name__}"
            else:
                selector_status=("unsupported" if selected.fact_id=="unsupported" else "selected")
                if selected.fact_id=="unsupported":
                    eligible=()
                else:
                    index=int(selected.fact_id.removeprefix("grounded_"))
                    eligible=(eligible[index],)
                found=replace(found,hits=eligible)
            finally:
                timings["grounded_answer_selector_ms"]=round((session.clock()-started)*1000)
        else:
            selector_status="disabled"
    causes_follow=_problem_causes_allowed(session,plan,language)
    if grounded_question:
        causes_follow=False
    public=public_reference_part(found.tier("public"),language=language)
    step=curated.fixture.steps[curated.current_index]
    if grounded_question:
        help_=compose_grounded_problem_answer(
            found,language=language,step_label=step.source_label,
            step_page=int(step.evidence.source_page_number),
        )
    else:
        help_=_with_public_reference(
            compose_problem_help(
                replace(found,hits=tuple(hit for hit in found.hits if hit.tier!="public")),
                language=language,causes_follow=causes_follow),
            public)
    violations=problem_help_violations(
        f"{help_.shown}\n{help_.spoken}",quoted=help_.quoted_text.splitlines())
    if violations:
        log.warning("problem help dropped checks=%s","+".join(violations))
        if grounded_question:
            found=replace(found,hits=())
            help_=compose_grounded_problem_answer(
                (),language=language,step_label=step.source_label,
                step_page=int(step.evidence.source_page_number),
            )
        else:
            help_=compose_problem_help((),language=language,causes_follow=causes_follow)
    if plan.action is CuratedProtocolAction.PROBLEM_HELP:
        display,speech=help_.shown,help_.spoken
    else:
        display=f"{plan.display_text}\n\n{help_.shown}"
        speech=f"{plan.speech_text} {help_.spoken}"
    # The composer shows at most three exact excerpts; expose and cite exactly
    # that admitted set, never an additional searched-but-unshown line.
    admitted=tuple(hit for hit in found.hits if hit.tier in {"protocol","lab"})[:3]
    marker.update({
        "found":dict(help_.found),"sources":[dict(item) for item in help_.sources],
        "search_ms":dict(found.elapsed_ms),"unavailable":list(found.unavailable),
        "failed":list(found.failed),"causes_follow":causes_follow,
        "answer_selector_status":selector_status,
    })
    plan=replace(
        plan,display_text=display,speech_text=speech,primary_text=display,
        problem_help=marker,
        source_texts=(tuple(hit.text for hit in admitted) if grounded_question else plan.source_texts),
        source_pages=(tuple(int(hit.detail("page") or 0) for hit in admitted)
                      if grounded_question else plan.source_pages),
        evidence_ids=(tuple(f"grounded_{index}" for index,_hit in enumerate(admitted))
                      if grounded_question else plan.evidence_ids),
        citations=(tuple({
            "kind":hit.tier,"label":hit.locator,"page":int(hit.detail("page") or 0),
            "step_label":hit.detail("step_label"),
        } for hit in admitted) if grounded_question else plan.citations),
        translation_status=(
            "answer_selector_exact_source" if selector_status=="selected"
            else "deterministic_protocol_structure"
        ),
        limitations=(
            tuple(dict.fromkeys((
                *plan.limitations,
                "원문 선택을 완료하지 못해 검색된 원문을 그대로 표시했어요.",
            )))
            if selector_status.startswith("provider_") else plan.limitations
        ),
    )
    if not causes_follow:
        return plan,None
    step=curated.fixture.steps[int(marker["step_index"])]
    return plan,{
        "problem":str(marker.get("words") or ""),
        "step_text":step.instruction_source_text,
        "source_lines":tuple(hit.text for hit in found.tier("protocol"))[:3],
        "public_lines":tuple(hit.text for hit in found.tier("public") if hit.detail("relevant"))[:2],
        "sources_text":"\n".join((help_.quoted_text,step.instruction_source_text)),
        "protocol_text":curated._protocol_vocabulary().corpus,
        "closing":help_.closing,
    }


def _with_public_reference(help_:Any,public:Any)->Any:
    """The help with its public pieces in decision 6's form (lane KNOW).

    The composer (``knowledge.compose_problem_help``) gets the protocol and
    history hits only; the public pieces follow as
    ``external_references.public_reference_part`` writes them -- on the screen
    the mark "공개 자료 · 일반 참고 — <출처 이름>", the title and place, the
    piece's own words, its address and licence; said, only "공개 자료에 관련
    내용이 있어요" (lane KNOW2, person's decision 2026-10-26, 1: no piece's
    sentence is read and none is paraphrased). Every line that is the
    source's or names it is quoted, so the help's own-words check judges only
    the fixed sentences.
    """

    if public is None:
        return help_
    found=dict(help_.found); found["public"]=public.count
    return replace(
        help_,
        display=f"{help_.display}\n\n{public.shown}",
        speech=f"{help_.speech} {public.spoken}",
        sources=(*help_.sources,*public.sources),
        found=found,
        quoted_text="\n".join(item for item in (help_.quoted_text,*public.quoted) if item),
    )


def _record_problem_help(
    session:ListenerSession,curated:CuratedProtocolSession,plan:Any,*,
    turn_id:int,generation:int,
)->dict:
    """The help given, as its own event beside the problem (decision 2 and the
    notebook's line): where each line came from, never the help's words."""

    report=_open_experiment_report(session,curated)
    store=session.experiment_report_store
    assert store is not None and session.experiment_report_id is not None
    marker=plan.problem_help or {}
    assert report is not None
    return store.append_event(
        session.experiment_report_id,
        event_key=f"turn-{turn_id}-generation-{generation}-problem-help",
        event_type="problem_help",
        step_id=marker.get("step_id"),
        step_label=marker.get("step_label"),
        user_wording=(str(marker.get("words") or "") or None) if marker.get("kind")!="statement" else None,
        category=str(marker.get("kind") or "question"),
        confirmation_state="server_observed",
        payload={
            "kind":marker.get("kind"),"found":marker.get("found") or {},
            "sources":list(marker.get("sources") or ())[:8],
            "search_ms":marker.get("search_ms") or {},
            "causes_follow":bool(marker.get("causes_follow")),
            "state_mutation":False,
        },
    )


async def _queue_problem_causes(
    *,session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    clock:Callable[[],float],causes:dict[str,Any],language:str,answer_segments:int,
)->None:
    """③ then ④, said after the answer (decision 2): possible causes from the
    supplemental role, marked "AI 일반 지식" and checked; ④ alone when they are
    not ready while the answer plays, fail or fail a check."""

    configuration_id=session.accepted_configuration_id
    speak_by=float(causes.get("speak_by") or clock())
    explanation=""
    status="disabled"
    try:
        role=RoleModel.from_environment("supplemental")
        explainer=XaiSupplementalKnowledge(
            _role_client(role),
            replace(session.supplemental_knowledge_settings,
                    model=role.model or session.supplemental_knowledge_settings.model),
        )
        timeout=max(0.05,min(session.supplemental_knowledge_settings.timeout_seconds,speak_by-clock()))
        result=await asyncio.wait_for(
            explainer.explain_possible_causes(
                causes["problem"],step_text=causes["step_text"],
                source_lines=tuple(causes.get("source_lines") or ()),
                public_lines=tuple(causes.get("public_lines") or ()),language=language),
            timeout=timeout,
        )
        status=str(result.get("status") or "provider_error")
        if status=="success":
            explanation=str(result.get("answer") or "")
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        status="late"
    except Exception as exc:
        log.info("possible causes failed turn_id=%s category=%s",turn_id,type(exc).__name__)
        status="provider_error"
    if explanation:
        violations=possible_cause_violations(
            explanation,sources_text=str(causes.get("sources_text") or ""),
            protocol_text=str(causes.get("protocol_text") or ""))
        if violations:
            log.info("possible causes dropped turn_id=%s checks=%s",turn_id,"+".join(violations))
            explanation,status="","rejected"
    if not session.owns_research_result(turn_id,generation,configuration_id):
        return
    lead=("AI 일반 지식으로는, " if language=="ko" else "From general AI knowledge, ")
    words=f"{lead}{explanation} {causes['closing']}" if explanation else str(causes["closing"])
    if explanation:
        await sender.text(
            "research.state",turn_id=turn_id,status="running",phase="supplemental_model",
            correlation_id=f"research-{generation}-{turn_id}")
        await _finish_research_operation(
            sender,session,turn_id,generation,"success",
            primary_text=f"{explanation}",answer_origin="supplemental_model_knowledge",
            citations=[],retrieval_backend="supplemental_possible_causes",
            limitations=["possible_causes"],source_label="AI 일반 지식",outside_pdf=True,spoken=True)
    else:
        session.finish_research(turn_id,generation)
    log.info("possible causes turn_id=%s status=%s",turn_id,status)
    try:
        pcm=await asyncio.to_thread(synthesize,said(words),language)
    except Exception:
        log.exception("possible causes TTS failed")
        await _release_held_audio(sender,session,turn_id,generation,answer_segments)
        return
    frames=frame_complete_audio(pcm)
    if (
        frames and session.is_current(turn_id,generation)
        and (turn_id,generation) in session.held_audio_complete
    ):
        await sender.segment(turn_id,answer_segments,frames,generation)
        await _release_held_audio(sender,session,turn_id,generation,answer_segments+1)
    else:
        await _release_held_audio(sender,session,turn_id,generation,answer_segments)


def _outside_pdf_gap(
    session:ListenerSession,curated:CuratedProtocolSession,plan:Any,
    transcript:str,language:str,
)->dict[str,str]|None:
    """What a spoken outside-PDF explanation would explain, when one may follow.

    Only with the supplemental role on, for a spoken Korean or English
    answer, where curated.outside_pdf_gap finds the source silent on the
    meaning or purpose asked (lane R6, decision 6).
    """

    if (
        not session.supplemental_knowledge_settings.enabled
        or language not in {"ko","en"}
        or getattr(plan,"speech_policy","speak")!="speak"
    ):
        return None
    return curated.outside_pdf_gap(plan,transcript)


async def _release_held_audio(
    sender:LockedSender,
    session:ListenerSession,
    turn_id:int,
    generation:int,
    segment_count:int,
)->bool:
    """Send the audio.complete an outside-PDF explanation held, once.

    Lane R6, decision 6: the rules' answer is played at once and the
    explanation is said after it only if it is ready while that answer
    plays. Whichever comes first -- the explanation, its failure, or the end
    of the answer's audio -- sends it; the others find it gone.
    """

    identity=(turn_id,generation)
    if identity not in session.held_audio_complete:return False
    session.held_audio_complete.discard(identity)
    if not session.is_current(turn_id,generation):return False
    fields:dict[str,Any]={"generation":generation}
    if session.accepted_configuration_id is not None:
        fields["configuration_id"]=session.accepted_configuration_id
    await sender.text(
        "audio.complete",turn_id=turn_id,segment_count=segment_count,**fields)
    return True


async def _finish_all_research_operations(
    sender:LockedSender,
    session:ListenerSession,
    status:str,
)->None:
    for turn_id,generation in tuple(session.research_operations):
        await _finish_research_operation(
            sender,session,turn_id,generation,status,
            limitation=(
                "새 요청으로 이전 근거 확인을 종료했습니다."
                if status=="superseded" else
                "근거 확인이 취소되었습니다."
            ),
        )


def _session_greeting_text(session:Any,language:str)->str:
    """The greeting; with the source's questions open before the start, the first of them.

    Lane CF, decision 4: the start pressed, the conditions and counts the run
    would ask at its steps are asked first; the greeting asks the first one.
    """

    curated=getattr(session,"curated_protocol_session",None)
    title=curated.fixture.title if curated is not None else "the selected protocol"
    asked=(
        curated.open_prestart_questions()
        if curated is not None and language=="ko"
        and callable(getattr(curated,"open_prestart_questions",None)) else None
    )
    if asked:
        return f"Voiney Lab입니다. 선택한 {title} 프로토콜이 준비되었습니다. {asked}"
    return {
        "ko":f"Voiney Lab입니다. 선택한 {title} 프로토콜이 준비되었습니다. 시작할까요, 아니면 먼저 질문하시겠어요?",
        "en":f"This is Voiney Lab. {title} is ready. Would you like to begin, or ask a question first?",
        "vi":f"Voiney Lab đã sẵn sàng với {title}. Bạn muốn bắt đầu hay hỏi trước?",
    }.get(language,"Voiney Lab is ready.")


def _korean_first_words(session:Any,text:str,language:str)->str:
    """Server words that quote the source, as they are said (lane SAY, decision 1).

    The greeting asks a question the source's conditions or counts raise
    before the start, quoting the source; said aloud, a quote with no Korean
    reading (reviewed, stored or made in an earlier turn) is "이 문장은 아직
    번역되지 않았어요. 원문을 화면에 띄울게요." -- no call is made here -- and the
    screen keeps the words as written.
    """

    curated=getattr(session,"curated_protocol_session",None)
    if language!="ko" or curated is None or not text:
        return text
    try:
        plan=curated._words_plan(text,intent_kind="server_words")
        reading=curated.korean_first(
            plan,language="ko",readings=_spot_readings(translation_revision_key(curated.fixture)))
    except Exception as exc:  # noqa: BLE001 - the words are said as written
        log.warning("korean_first server_words failed error=%s",type(exc).__name__)
        return text
    return reading.plan.speech_text or text


async def _send_session_greeting(
    sender:LockedSender,session:ListenerSession,*,language:str,
) -> None:
    """Emit one deterministic, interruptible greeting for a logical session."""

    if session.greeting_emitted or not session.greeting_audio_ready:
        return
    session.greeting_emitted=True
    greeting_turn_id=2_000_000_000  # Reserved display/audio identity; user turns start at 1.
    greeting=_session_greeting_text(session,language)
    spoken_greeting=_korean_first_words(session,greeting,language)
    generation=session.generation
    configuration_id=session.accepted_configuration_id
    greeting_id=hashlib.sha256(
        f"{session.session_id}\x1f{configuration_id}\x1fgreeting-v1".encode()
    ).hexdigest()
    log.info(
        "session.greeting.created configuration_id=%s generation=%s greeting_id=%s",
        configuration_id,generation,greeting_id,
    )
    await sender.text(
        "session.greeting",configuration_id=configuration_id,
        turn_id=greeting_turn_id,generation=generation,greeting_id=greeting_id,
        # Lane KO2, decision 4: the words said, for the caption.
        text=greeting,speech_text=spoken_greeting,language=language,
    )
    try:
        log.info("session.greeting.tts_started greeting_id=%s",greeting_id)
        # Lane VOX, decision 3: the same greeting next session, from memory.
        with sentence_audio_kept():
            pcm=await asyncio.to_thread(synthesize,said(spoken_greeting),language)
        frames=frame_complete_audio(pcm)
        log.info(
            "session.greeting.tts_completed greeting_id=%s frame_count=%d",
            greeting_id,len(frames),
        )
    except Exception:
        log.info("session.greeting.tts_failed greeting_id=%s",greeting_id)
        await sender.text(
            "session.greeting.failed",configuration_id=configuration_id,
            turn_id=greeting_turn_id,generation=generation,greeting_id=greeting_id,
            reason="tts_unavailable",
        )
        return
    if (
        not frames or not session.active
        or session.accepted_configuration_id!=configuration_id
        or session.active_turn_id is not None
        or session.state is not TurnState.IDLE
    ):
        await sender.text(
            "session.greeting.cancelled",configuration_id=configuration_id,
            turn_id=greeting_turn_id,generation=generation,
            greeting_id=greeting_id,reason="superseded_by_session_activity",
        )
        return
    session.active_turn_id=greeting_turn_id
    session.turn_generations[greeting_turn_id]=generation
    session.turn_committed_at[greeting_turn_id]=session.clock()
    session.detector.state=TurnState.PROCESSING
    if not session.start_playback(greeting_turn_id):
        return
    await sender.text(
        "reply.delta",configuration_id=configuration_id,
        turn_id=greeting_turn_id,generation=generation,segment_index=0,text=greeting,
        primary_text=greeting,speech_text=spoken_greeting,
        answer_origin="server_greeting",source_texts=[],source_pages=[],
        evidence_ids=[],translation_status="not_applicable",
    )
    await sender.text(
        "state.changed",configuration_id=configuration_id,
        turn_id=greeting_turn_id,generation=generation,state=session.state.value,
    )
    await sender.segment(greeting_turn_id,0,frames,generation)
    log.info(
        "session.greeting.segment_sent greeting_id=%s frame_count=%d",
        greeting_id,len(frames),
    )
    await sender.text(
        "reply.complete",configuration_id=configuration_id,
        turn_id=greeting_turn_id,generation=generation,text=greeting,
        speech_text=spoken_greeting,
    )
    await sender.text(
        "audio.complete",configuration_id=configuration_id,
        turn_id=greeting_turn_id,generation=generation,segment_count=1,
    )
    await sender.text(
        "turn.done",configuration_id=configuration_id,
        turn_id=greeting_turn_id,generation=generation,route="server_greeting",
        pipeline="cascade",result_kind="greeting",fact_id=None,
        speech_mode="control",
        segment_count=1,input_frames=0,output_frames=len(frames),
        tools_used=[],timings_ms={"stt":0,"first_audio_ms":0,"total_ms":0},
    )

# --- Lane VT, decisions 1-2 / lane VF, decisions 2-3: the step timer told before it is asked

#: How often the server looks at the step timer when no notice is near.
TIMER_NOTICE_TICK_SECONDS=1.0
#: The watcher wakes for a notice's due moment itself (lane VF, decision 3:
#: the screen within a second of due), never sleeping less than this.
TIMER_NOTICE_MIN_WAIT_SECONDS=0.02
#: A spoken notice's sentence is synthesized this long before it falls due,
#: so that it is said the moment it is due (lane VF, decision 3).
TIMER_NOTICE_PREPARE_SECONDS=15.0
#: Prepared notice audio kept for a session, at most.
TIMER_NOTICE_PREPARED_MAX=8
#: The wall clock the step timer runs on (the curated session's time.time),
#: for when the experimenter's words are heard (decision 5).
_wall_clock=time.time
#: How long a notice to be said waits for the experimenter to stop speaking
#: and an answer to stop playing; past it the notice stays on the screen only.
TIMER_NOTICE_SPEAK_WAIT_SECONDS=120.0
#: A last minute's notice waits less: said later it would say the wrong time.
TIMER_LAST_MINUTE_SPEAK_WAIT_SECONDS=30.0
TIMER_NOTICE_POLL_SECONDS=0.2
#: When the page never reports a notice's playback ended, the session is
#: returned to listening this long after the audio's own length (lane VF,
#: decision 2).
TIMER_NOTICE_PLAYBACK_GRACE_SECONDS=2.0
#: Turn ids the server's own notices are said under, beside the greeting's
#: (2_000_000_000); user turns start at 1.
_TIMER_NOTICE_TURN_BASE=2_000_000_100


def _session_quiet(session:ListenerSession)->bool:
    """No one is speaking and nothing is playing or being answered."""

    if not session.active:
        return False
    if session.state is TurnState.COOLDOWN:
        return session.clock()>=session.cooldown_until
    return session.state is TurnState.IDLE and session.active_turn_id is None


def _record_timer_notice(
    session:ListenerSession,curated:CuratedProtocolSession,notice:TimerNotice,*,said:bool,
)->None:
    """Append a timer notice to the experiment record (lane VT, decision 8).

    Its own event, never changed after -- "timer_end_notice" or
    "timer_last_minute_notice" -- at the timer's step: what it said, when it
    fell due, whether and when it was said aloud, the setting it was given
    under. Once per notice (its key). No record open, nothing is written.
    """

    store=session.experiment_report_store
    if store is None or session.experiment_report_id is None:
        return
    try:
        store.append_event(
            session.experiment_report_id,
            event_key=f"timer-notice-{notice.notice_id}",
            event_type=(
                "timer_end_notice" if notice.kind=="timer_ended" else "timer_last_minute_notice"
            ),
            step_id=notice.step_id,step_label=notice.step_label,
            payload={
                "notice":notice.public_dict(),
                "spoken":said,
                "said_at":datetime.now(timezone.utc).isoformat() if said else None,
                "proactive_mode":curated.proactive_mode,
                "next_step_label":notice.next_step_label,
                "development_only":curated.fixture.development_only,
            },
        )
    except Exception as exc:  # noqa: BLE001 - the notice was given; the log says it was not kept
        log.warning("timer notice not recorded error=%s",type(exc).__name__)


def _timer_key(notice:TimerNotice)->tuple[Any,...]:
    """The timer a notice speaks of: its step, start and length."""

    return (notice.step_id,notice.timer.get("started_at"),notice.timer.get("duration_seconds"))


async def _show_timer_notice(
    session:ListenerSession,sender:LockedSender,notice:TimerNotice,*,now:float|None=None,
)->tuple[float,float]:
    """Put a timer notice on the screen now; the moment it was shown.

    The screen gets every notice at once, a timer's end with a sound. Lane
    VF, decision 2: a timer's end shown while the same timer's last minute
    is still waiting to be said takes that minute's voice away -- said after
    the end it would say the wrong time -- and the screen keeps it. Returns
    the moment on the timer's clock and on the session's.
    """

    current=_wall_clock() if now is None else now
    await sender.text(
        "protocol.timer.notice",configuration_id=session.accepted_configuration_id,
        generation=session.generation,**notice.public_dict(),
    )
    if notice.kind=="timer_ended":
        for pending_id,pending in list(session.timer_notice_pending.items()):
            if pending.kind=="timer_last_minute" and _timer_key(pending)==_timer_key(notice):
                session.timer_notice_superseded.add(pending_id)
    if notice.spoken:
        session.timer_notice_pending[notice.notice_id]=notice
    log.info(
        "timer notice shown notice_id=%s kind=%s due=%s delay_ms=%d",
        notice.notice_id,notice.kind,notice.public_dict()["due_at"],
        round((current-notice.due_at)*1000),
    )
    # Lane LOG, decision 2: the notice, and the next words timed from it.
    _pilot_mark(
        "timer_alert",session=session,
        code="end" if notice.kind=="timer_ended" else "last_minute")
    PILOT_METRICS.alert_shown(session.session_id,session.clock())
    return current,session.clock()


async def _deliver_timer_notice(
    session:ListenerSession,sender:LockedSender,curated:CuratedProtocolSession,
    notice:TimerNotice,*,sleep:Callable[[float],Awaitable[Any]]=asyncio.sleep,
    wait_seconds:float=TIMER_NOTICE_SPEAK_WAIT_SECONDS,now:float|None=None,
    playback_sleep:Callable[[float],Awaitable[Any]]=asyncio.sleep,
)->bool:
    """Show a timer notice, say it once the session is quiet, keep it in the record."""

    shown=await _show_timer_notice(session,sender,notice,now=now)
    return await _say_and_record_timer_notice(
        session,sender,curated,notice,sleep=sleep,wait_seconds=wait_seconds,shown=shown,
        playback_sleep=playback_sleep)


async def _say_and_record_timer_notice(
    session:ListenerSession,sender:LockedSender,curated:CuratedProtocolSession,
    notice:TimerNotice,*,sleep:Callable[[float],Awaitable[Any]],wait_seconds:float,
    shown:tuple[float,float],playback_sleep:Callable[[float],Awaitable[Any]]=asyncio.sleep,
)->bool:
    """The part of a notice that waits: say it, then keep it in the record (decisions 1, 8)."""

    said_aloud=await _say_timer_notice(
        session,sender,curated,notice,sleep=sleep,wait_seconds=wait_seconds,shown=shown,
        playback_sleep=playback_sleep)
    await asyncio.to_thread(_record_timer_notice,session,curated,notice,said=said_aloud)
    return said_aloud


def _begin_saying_timer_notice(
    session:ListenerSession,sender:LockedSender,curated:CuratedProtocolSession,
    notice:TimerNotice,*,sleep:Callable[[float],Awaitable[Any]],wait_seconds:float,
    shown:tuple[float,float],
)->asyncio.Task:
    """Hand a shown notice to its own task (lane VF, decision 2): the watcher never waits on it."""

    task=asyncio.create_task(_say_and_record_timer_notice(
        session,sender,curated,notice,sleep=sleep,wait_seconds=wait_seconds,shown=shown,
        playback_sleep=sleep))
    session.track_timer_notice_task(task)
    return task


def _prepare_timer_notice(
    session:ListenerSession,curated:CuratedProtocolSession,notice:TimerNotice,
)->asyncio.Task|None:
    """Have a spoken notice's audio made before it falls due (lane VF, decision 3)."""

    if (
        not notice.spoken or notice.notice_id in session.timer_notice_audio
        or notice.notice_id in session.timer_notice_preparing
    ):
        return None
    session.timer_notice_preparing.add(notice.notice_id)
    task=asyncio.create_task(_prepare_timer_notice_audio(session,curated,notice))
    session.track_timer_notice_task(task)
    return task


async def _prepare_timer_notice_audio(
    session:ListenerSession,curated:CuratedProtocolSession,notice:TimerNotice,
)->None:
    """Synthesize a notice's sentence ahead of its due moment, and keep the frames.

    The sentence is the one that would be said now (a machine translation
    named as such, "자동 번역입니다." first when not announced yet) without
    announcing anything; when the sentence to be said differs at the time,
    the audio is made again then. Nothing is said or shown here, but the
    sentence is remembered for the echo check (lane XO, 6b) as at every
    TTS call: from now on its audio exists.
    """

    try:
        labelled=_label_machine_translation(session,curated,notice,commit=False)
        started=session.clock()
        try:
            pcm=await asyncio.to_thread(synthesize,said(labelled.speech_text),"ko")
        except Exception as exc:  # noqa: BLE001 - made again when it is due
            log.warning(
                "timer notice prepare tts failed notice_id=%s error=%s",
                notice.notice_id,type(exc).__name__)
            return
        frames=frame_complete_audio(pcm)
        if not frames:
            return
        audio=session.timer_notice_audio
        while len(audio)>=TIMER_NOTICE_PREPARED_MAX:
            audio.pop(next(iter(audio)))
        audio[notice.notice_id]=(labelled.speech_text,frames)
        log.info(
            "timer notice prepared notice_id=%s kind=%s due=%s synth_ms=%d",
            notice.notice_id,notice.kind,notice.public_dict()["due_at"],
            round((session.clock()-started)*1000))
    finally:
        session.timer_notice_preparing.discard(notice.notice_id)


async def _timer_notice_frames(
    session:ListenerSession,notice:TimerNotice,
)->tuple[list[bytes]|None,bool]:
    """The audio of a notice about to be said, and whether it was made ahead.

    Audio prepared for this notice is used when it was made from the very
    sentence to be said now; otherwise the sentence is synthesized here.
    None when it cannot be made.
    """

    prepared=session.timer_notice_audio.pop(notice.notice_id,None)
    if prepared is not None and prepared[0]==notice.speech_text:
        said(notice.speech_text)
        return prepared[1],True
    try:
        pcm=await asyncio.to_thread(synthesize,said(notice.speech_text),"ko")
    except Exception as exc:  # noqa: BLE001 - the screen keeps it
        log.warning("timer notice tts failed error=%s",type(exc).__name__)
        return None,False
    return frame_complete_audio(pcm),False


async def _say_timer_notice(
    session:ListenerSession,sender:LockedSender,curated:CuratedProtocolSession,
    notice:TimerNotice,*,sleep:Callable[[float],Awaitable[Any]],wait_seconds:float,
    shown:tuple[float,float],playback_sleep:Callable[[float],Awaitable[Any]]=asyncio.sleep,
)->bool:
    """Say a shown timer notice once the session is quiet (lane VT, decision 1).

    When "먼저 알려 주기" has it said, it waits for the experimenter to finish
    speaking and any answer to finish playing -- nothing is cut off -- and is
    then said the way the greeting is: one turn of the server's own, through
    the same TTS, playback and echo memory as every answer, so its sound
    coming back through the microphone is the agent's own voice and no
    command. Lane VF, decision 2: the experimenter interrupting an answer
    meanwhile (a new generation) does not lose the notice; it is given up
    only when the session or the timer it names is gone, when the same
    timer's end has come first, or when the wait runs out. Nothing here
    changes the workflow. Returns whether it was said.
    """

    configuration_id=session.accepted_configuration_id
    if not notice.spoken:
        return False
    shown_wall,shown_clock=shown
    deadline=session.clock()+wait_seconds
    frames:list[bytes]|None=None
    prepared=False
    quiet_at:float|None=None
    synth_ms=0
    reason:str|None=None
    try:
        while True:
            if not (session.active and session.accepted_configuration_id==configuration_id):
                reason="session_changed"; break
            if not curated.timer_notice_stands(notice):
                reason="timer_changed"; break
            if notice.notice_id in session.timer_notice_superseded:
                reason="superseded_by_end"; break
            if _session_quiet(session):
                if quiet_at is None:
                    quiet_at=session.clock()
                if frames is None:
                    # A stored machine translation is named as such, and "자동
                    # 번역입니다." said first once a session -- only now that it
                    # is about to be said.
                    notice=_label_machine_translation(session,curated,notice)
                    synth_started=session.clock()
                    frames,prepared=await _timer_notice_frames(session,notice)
                    synth_ms=round((session.clock()-synth_started)*1000)
                    if frames is None:
                        reason="tts_failed"; break
                    if not frames:
                        reason="no_audio"; break
                    # Speaking may have begun while it was synthesized.
                    continue
                break
            if session.clock()>=deadline:
                reason="not_quiet"; break
            await sleep(TIMER_NOTICE_POLL_SECONDS)
    finally:
        session.timer_notice_pending.pop(notice.notice_id,None)
        session.timer_notice_superseded.discard(notice.notice_id)
        session.timer_notice_audio.pop(notice.notice_id,None)
    if reason is not None:
        log.info(
            "timer notice not said notice_id=%s kind=%s reason=%s",
            notice.notice_id,notice.kind,reason)
        return False
    assert frames is not None
    session.refresh_cooldown()
    session.timer_notice_turns+=1
    turn_id=_TIMER_NOTICE_TURN_BASE+session.timer_notice_turns
    generation=session.generation
    session.active_turn_id=turn_id
    session.turn_generations[turn_id]=generation
    session.turn_committed_at[turn_id]=session.clock()
    session.detector.state=TurnState.PROCESSING
    if not session.start_playback(turn_id):
        return False
    fields={"configuration_id":configuration_id,"turn_id":turn_id,"generation":generation}
    await sender.text(
        "protocol.notice.speech",notice_id=notice.notice_id,notice_kind=notice.kind,
        text=notice.display_text,speech_text=notice.speech_text,**fields,
    )
    await sender.text(
        "reply.delta",segment_index=0,text=notice.display_text,
        primary_text=notice.display_text,speech_text=notice.speech_text,
        answer_origin="server_notice",source_texts=[],source_pages=[],
        evidence_ids=[],translation_status=notice.translation_status,**fields,
    )
    await sender.text("state.changed",state=session.state.value,**fields)
    await sender.segment(turn_id,0,frames,generation)
    await sender.text(
        "reply.complete",text=notice.display_text,speech_text=notice.speech_text,**fields)
    await sender.text("audio.complete",segment_count=1,**fields)
    await sender.text(
        "turn.done",route="server_notice",pipeline="cascade",
        result_kind=notice.kind,fact_id=None,speech_mode="control",
        segment_count=1,input_frames=0,output_frames=len(frames),tools_used=[],
        timings_ms={"stt":0,"first_audio_ms":0,"total_ms":0},**fields,
    )
    # Lane VF, decision 3: how late after its due moment the notice is
    # said, of which how long it waited for quiet and how long its audio took.
    log.info(
        "timer notice said notice_id=%s kind=%s turn_id=%s due=%s delay_ms=%d "
        "waited_ms=%d synth_ms=%d prepared=%s",
        notice.notice_id,notice.kind,turn_id,notice.public_dict()["due_at"],
        round(((shown_wall-notice.due_at)+(session.clock()-shown_clock))*1000),
        round(((quiet_at if quiet_at is not None else shown_clock)-shown_clock)*1000),
        synth_ms,prepared,
    )
    _end_notice_playback_later(session,sender,turn_id,generation,frames,sleep=playback_sleep)
    return True


def _end_notice_playback_later(
    session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    frames:list[bytes],*,sleep:Callable[[float],Awaitable[Any]],
)->None:
    """Return the session to listening when the page's playback.ended never comes.

    Lane VF, decision 2: a notice's playback is ended by the page as every
    answer's is; should that report not arrive, the session would stay
    "speaking" and every later notice would be left unsaid. So once the
    audio's own length and a grace have passed -- on ``sleep``'s clock, the
    wall clock in production -- the server ends the turn itself, unless the
    page already did, the experimenter interrupted, or a barge-in check is
    open (its outcome ends the turn).
    """

    audio_seconds=len(frames)*FRAME_MS/1000

    async def end_when_over()->None:
        await sleep(audio_seconds+TIMER_NOTICE_PLAYBACK_GRACE_SECONDS)
        if session.state is not TurnState.AGENT_SPEAKING or session.active_turn_id!=turn_id:
            return
        if not session.playback_ended(turn_id):
            return
        log.info(
            "timer notice playback ended by server turn_id=%s audio_ms=%d",
            turn_id,round(audio_seconds*1000))
        await sender.text(
            "state.changed",state=session.state.value,turn_id=turn_id,
            generation=generation,cooldown_ms=session.detector.config.cooldown_ms,
        )

    session.track_timer_notice_task(asyncio.create_task(end_when_over()),playback=True)


# --- Lane SP1 (2026-10-10), decision 1: the wearer's voice measured once ----

#: Turn ids of the server's own words beside the greeting's and the notices'.
_SERVER_WORDS_TURN_BASE=2_000_000_300
#: The short sentence the wearer reads so their level can be measured.
VOICE_CALIBRATION_SENTENCE="목소리를 맞출게요. 지금 이 문장을 평소처럼 읽어 주세요."
#: Lane HEAR, decision 1: the answer to a calibration asked for during a run.
VOICE_CALIBRATION_IN_RUN_WORDS="실험 중에는 목소리를 맞추지 않아요. 실험이 끝난 뒤 설정에서 맞춰 주세요."
VOICE_CALIBRATION_PROMPT=(
    "목소리를 맞출게요. 화면의 문장을 평소 목소리로 읽어 주세요. "
    "건너뛰려면 '건너뛰기'라고 하세요."
)
VOICE_CALIBRATION_DONE="목소리를 맞췄어요. 이제 이보다 많이 작은 말은 다른 사람 말로 보고 듣지 않아요."
VOICE_CALIBRATION_SKIPPED="알겠어요. 목소리 맞추기는 건너뛸게요."
VOICE_CALIBRATION_RETRY="목소리를 재지 못했어요. 화면의 문장을 한 번 더 읽어 주세요."
#: Lane FX3 (2026-10-16), decision 7: said, and shown, when the room's noise
#: floor is near the line just measured -- the sentence was read where the
#: room's own sound is heard as loud as the wearer. A suggestion only.
NOISY_CALIBRATION_WORDS="방이 시끄러워 맞추기가 정확하지 않아요. 조용한 곳에서 다시 맞추거나 시끄러운 곳 모드를 쓰세요."
VOICE_CALIBRATION_DONE_NOISY=f"목소리를 맞췄어요. {NOISY_CALIBRATION_WORDS}"
_VOICE_CALIBRATION_REQUEST=re.compile(
    r"^(?:내\s*)?목소리(?:를|도)?\s*(?:다시\s*|또\s*|한\s*번\s*더\s*)?"
    r"(?:맞춰|맞추|재|재어|측정해|등록해)(?:\s*(?:줘|줘요|주세요|줄래|보자|볼게|줄래요))?$"
)
_VOICE_CALIBRATION_SKIP=re.compile(
    r"^(?:건너\s*(?:뛰기|뛰어|뛸게|뛰자|뛰어\s*줘|뛰어도\s*돼)|넘어가(?:자|줘|요|도\s*돼)?|"
    r"괜찮아(?:요)?|됐어(?:요)?|아니(?:요|야|오)?|안\s*할래(?:요)?|나중에(?:\s*할게(?:요)?)?|skip)$"
)


#: Lane SP1, decision 5: what the STT writes for a sound that was no words
#: -- a vortex mixer, an alarm, a breath the VAD took for speech: nothing,
#: punctuation, or a filler syllable or two. Answering it would be a reply
#: to a machine. "응", "네", "아니" and every real word are not fillers.
_FILLER_ONLY=re.compile(
    r"^(?:(?:음+|어+|아+|에+|흠+|엄+|으음|어어|아아|음음|um+|uh+|hm+|mm+|ah+|eh+|er+)"
    r"[\s.,!?~…。，！？]*){1,3}$",re.IGNORECASE)


def transcript_is_filler_only(transcript:str)->bool:
    """Whether the words are no words: empty, punctuation, or filler syllables only."""

    said=" ".join(transcript.split())
    if not said:
        return True
    if not re.search(r"[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ\u00C0-\u024F\u3040-\u30FF\u4E00-\u9FFF]",said):
        return True
    return bool(_FILLER_ONLY.fullmatch(said))


def voice_calibration_asked(transcript:str)->bool:
    """Whether the words ask for the wearer's voice to be measured (again)."""

    said=" ".join(transcript.split()).strip(" .!。")
    return bool(said) and "?" not in said and "？" not in said and bool(
        _VOICE_CALIBRATION_REQUEST.fullmatch(said))


def voice_calibration_skipped(transcript:str)->bool:
    """Whether the words answer the calibration prompt with a skip."""

    said=" ".join(transcript.split()).strip(" .!。?？")
    return bool(_VOICE_CALIBRATION_SKIP.fullmatch(said))


async def _finish_voice_calibration(
    session:ListenerSession,current_text:Callable[...,Awaitable[bool]],
    finish:Callable[...,Awaitable[None]],transcript:str,*,
    voiced_level_db:float|None,turn_id:int,
)->None:
    """Take the sentence read as the wearer's level, or the skip said instead."""

    notice:str|None=None
    if voice_calibration_skipped(transcript):
        session.calibration_pending=False
        state,words="skipped",VOICE_CALIBRATION_SKIPPED
        log.info("voice calibration skipped by voice turn_id=%s",turn_id)
    elif voiced_level_db is None:
        state,words="prompted",VOICE_CALIBRATION_RETRY
        log.info("voice calibration no voiced level turn_id=%s",turn_id)
    else:
        reference=session.set_level_reference(voiced_level_db)
        state,words="measured",VOICE_CALIBRATION_DONE
        log.info(
            "voice calibration measured level_db=%.1f margin_db=%.1f sensitivity=%s turn_id=%s",
            reference.reference_db,reference.margin_db,reference.sensitivity,turn_id)
        if session.room_near_the_line() is True:
            # Lane FX3, decision 7: lane SP2's noise floor already within
            # LEVEL_ENDPOINT_FLOOR_GAP_DB of the line just measured. Said
            # once, with the measurement; nothing is changed. It is the
            # room's suggestion of the ten minutes (shared with lanes SP1
            # and SP2), so the noise floor's own does not follow at once.
            notice=NOISY_CALIBRATION_WORDS
            words=VOICE_CALIBRATION_DONE_NOISY
            session.ambient_suggested_at=session.clock()
            floor=session.noise_floor.level_db()
            log.info(
                "voice calibration noisy room floor_db=%s reference_db=%.1f turn_id=%s",
                round(floor,1) if floor is not None else None,reference.reference_db,turn_id)
    await current_text("transcript",turn_id=turn_id,text=transcript)
    if state in {"measured","skipped"}:
        # Lane ON, decision 2: what the calibration came to is the
        # experimenter's setting, so the start screen skips the step next time.
        try:
            await asyncio.to_thread(_save_experimenter_settings,{"voice_calibration":state},"voice")
        except Exception as exc:  # noqa: BLE001 - the measurement stands either way
            log.warning("voice calibration outcome not kept error=%s",type(exc).__name__)
    reference=session.level_reference
    fields:dict[str,Any]={
        "state":state,"sentence":VOICE_CALIBRATION_SENTENCE,
        "sensitivity":session.speaker_sensitivity,
    }
    if state=="measured" and reference is not None:
        fields.update(
            level_db=round(reference.reference_db,1),
            margin_db=round(reference.margin_db,1),
            threshold_db=round(reference.threshold_db,1))
        if notice is not None:
            fields["notice"]=notice
    await current_text("speaker.calibration",turn_id=turn_id,**fields)
    await finish(words,"voice_calibration",outcome="complete")


async def _say_server_words(
    session:ListenerSession,sender:LockedSender,text:str,*,kind:str,
    route:str="server_words",sleep:Callable[[float],Awaitable[Any]]=asyncio.sleep,
    wait_seconds:float=TIMER_NOTICE_SPEAK_WAIT_SECONDS,
)->int|None:
    """Say ``text`` as one turn of the server's own once the session is quiet.

    The way a timer notice is said (lane VT, decision 1; lane VF, decision
    2): after the experimenter's words and any answer, through the same TTS,
    playback and echo memory as every answer. Nothing here touches the
    workflow. Returns the turn id, or None when it was not said.
    """

    configuration_id=session.accepted_configuration_id
    deadline=session.clock()+wait_seconds
    frames:list[bytes]|None=None
    while True:
        if not (session.active and session.accepted_configuration_id==configuration_id):
            log.info("server words not said kind=%s reason=session_changed",kind)
            return None
        if _session_quiet(session):
            if frames is None:
                try:
                    pcm=await asyncio.to_thread(
                        synthesize,said(text),session.accepted_language or "ko")
                except Exception as exc:  # noqa: BLE001 - the screen keeps it
                    log.warning("server words tts failed kind=%s error=%s",kind,type(exc).__name__)
                    return None
                frames=frame_complete_audio(pcm)
                if not frames:
                    return None
                # Speaking may have begun while it was synthesized.
                continue
            break
        if session.clock()>=deadline:
            log.info("server words not said kind=%s reason=not_quiet",kind)
            return None
        await sleep(TIMER_NOTICE_POLL_SECONDS)
    session.refresh_cooldown()
    session.server_words_turns+=1
    turn_id=_SERVER_WORDS_TURN_BASE+session.server_words_turns
    generation=session.generation
    session.active_turn_id=turn_id
    session.turn_generations[turn_id]=generation
    session.turn_committed_at[turn_id]=session.clock()
    session.detector.state=TurnState.PROCESSING
    if not session.start_playback(turn_id):
        return None
    fields={"configuration_id":configuration_id,"turn_id":turn_id,"generation":generation}
    await sender.text("server.words",words_kind=kind,text=text,**fields)
    await sender.text(
        "reply.delta",segment_index=0,text=text,primary_text=text,speech_text=text,
        answer_origin="server_words",source_texts=[],source_pages=[],
        evidence_ids=[],translation_status="not_applicable",**fields,
    )
    await sender.text("state.changed",state=session.state.value,**fields)
    await sender.segment(turn_id,0,frames,generation)
    await sender.text("reply.complete",text=text,speech_text=text,**fields)
    await sender.text("audio.complete",segment_count=1,**fields)
    await sender.text(
        "turn.done",route=route,pipeline="cascade",result_kind=kind,fact_id=None,
        speech_mode="control",segment_count=1,input_frames=0,output_frames=len(frames),
        tools_used=[],timings_ms={"stt":0,"first_audio_ms":0,"total_ms":0},**fields,
    )
    log.info("server words said kind=%s turn_id=%s",kind,turn_id)
    _end_notice_playback_later(session,sender,turn_id,generation,frames,sleep=sleep)
    return turn_id


async def _prompt_voice_calibration(
    sender:LockedSender,session:ListenerSession,*,
    sleep:Callable[[float],Awaitable[Any]]=asyncio.sleep,
)->bool:
    """Ask the wearer to read the calibration sentence (decision 1), then listen for it."""

    await sender.text(
        "speaker.calibration",configuration_id=session.accepted_configuration_id,
        generation=session.generation,state="prompting",
        sentence=VOICE_CALIBRATION_SENTENCE,sensitivity=session.speaker_sensitivity,
    )
    turn_id=await _say_server_words(
        session,sender,VOICE_CALIBRATION_PROMPT,kind="voice_calibration",sleep=sleep)
    if turn_id is None:
        return False
    session.arm_voice_calibration()
    await sender.text(
        "speaker.calibration",configuration_id=session.accepted_configuration_id,
        generation=session.generation,state="prompted",turn_id=turn_id,
        sentence=VOICE_CALIBRATION_SENTENCE,sensitivity=session.speaker_sensitivity,
    )
    log.info("voice calibration prompted on screen turn_id=%s",turn_id)
    return True


#: Lane FX3, decision 8: said once when the router's model could not answer
#: the turn (llm_router.ROUTER_MODEL_UNAVAILABLE_KINDS); nothing changes.
ROUTER_UNAVAILABLE_WORDS="지금 답하지 못했어요. 다시 말씀해 주세요."


#: Lane FX3, decision 4: what the screen shows when a tapped candidate is
#: not taken -- the session changed, it is not one the last reply asked
#: back, or something is under way.
CLARIFY_CHOICE_REFUSED_WORDS={
    "stale_session":"세션이 바뀌어 이 후보를 고를 수 없어요.",
    "not_asked":"지금 묻고 있는 후보가 아니에요. 말로 말씀해 주세요.",
    "busy":"지금은 고를 수 없어요. 말이 끝난 뒤 다시 눌러 주세요.",
}


# --- Lane HEAR, decision 3 (2026-10-25): no sound is dropped in silence -------
#: Shown on the screen beside each dropped sound. Lane HEAR2, decision 4
#: (2026-10-26): shown only, never said -- lane HEAR's "said once after two
#: drops in 30 s" came with every stretch of room noise the provider wrote
#: nothing for, and the person could not bear it. The reason goes with it,
#: in plain words (``drop_reason_words``), so a run of drops says why.
DROP_SCREEN_WORDS="잘 안 들렸어요"
DROP_REASON_WORDS_KO={
    "empty_transcript":"받아 적은 말이 없어요",
    "self_echo":"안내 소리가 다시 들어왔어요",
    "minimum_voiced_frames":"너무 짧은 소리였어요",
    "language_unclear":"어느 나라 말인지 알 수 없었어요",
    "filler_only":"'음' 같은 소리만 들렸어요",
    "non_lexical_event":"말이 아닌 소리였어요",
    "non_speech":"말소리가 아니었어요",
    "transient_noise":"말이 아닌 짧은 소리였어요",
    "keyterm_echo":"용어만 나열돼 말로 보지 않았어요",
    "transcription_failed":"받아 적지 못했어요",
    "provider_no_speech_probability":"말소리가 아니었어요",
    "provider_low_confidence":"확실하게 알아듣지 못했어요",
    "other_speaker_level":"내 목소리보다 많이 작은 소리였어요",
    "paused":"일시정지 중이에요",
    "stale_candidate":"답이 바뀌는 사이에 들린 소리예요",
    "barge_in_rejected":"끼어든 말로 보지 않았어요",
    "stt_failed":"받아쓰기 서비스가 답하지 않았어요",
    "unknown":"들리지 않았어요",
}


def drop_reason_words(reason:str)->str:
    """The plain words for a drop's reason code (lane HEAR2, decision 4); never the code."""

    return DROP_REASON_WORDS_KO.get(str(reason or ""),DROP_REASON_WORDS_KO["unknown"])


#: Lane HEAR2, decisions 1-2 (2026-10-26): a speech-to-text provider that
#: fails (the person's test: 44 requests answered 401 quota_exceeded) is
#: one more dropped sound with its code, shown with the reason in plain
#: words, and said once per outage -- not a traceback, not "다시 말해
#: 주세요" (the words would fail again), never the provider's own text.
STT_FAILURE_SCREEN_WORDS="받아쓰기 서비스가 답하지 않아요"
STT_FAILURE_WORDS="받아쓰기 서비스가 답하지 않아요. 관리자에게 알려 주세요."
STT_ADAPTATION_FALLBACK_REASON="stt_adaptation_disabled"
STT_FAILURE_REASON_WORDS={
    "quota_exceeded":"받아쓰기 서비스의 한도가 다 됐어요",
    "unauthorized":"받아쓰기 서비스의 키에 문제가 있어요",
    "rate_limited":"받아쓰기 서비스가 바빠요",
    "timeout":"받아쓰기 서비스가 너무 늦어요",
    "unreachable":"받아쓰기 서비스에 연결되지 않아요",
    "provider_error":"받아쓰기 서비스에 오류가 났어요",
    "provider_refused":"받아쓰기 서비스가 요청을 받지 않았어요",
}
# Google V2's canonical status codes use the same established screen words.
for _google_code,_speech_code in {
    "unauthenticated":"unauthorized", "permission_denied":"unauthorized",
    "resource_exhausted":"rate_limited", "deadline_exceeded":"timeout",
    "invalid_argument":"provider_refused", "failed_precondition":"provider_refused",
    "unavailable":"provider_error", "internal":"provider_error",
}.items():
    STT_FAILURE_REASON_WORDS[_google_code]=STT_FAILURE_REASON_WORDS[_speech_code]
#: A voice the filter dropped within this many dB under its line is where
#: the wearer's own quieter words fall (a head turned, a step back: the SP1
#: bench varied the wearer by +/-6 dB); this many in a row move the line.
NEAR_LINE_DB=6.0
NEAR_LINE_DROPS=2
SPEAKER_FILTER_WORDS={
    "lowered":"내 말이 작게 들려서 옆 사람 말 거르기를 낮췄어요.",
    "off":"내 말이 계속 걸러져서 옆 사람 말 거르기를 껐어요. 이제 모든 말을 들어요.",
}


def _audio_ready_mic_id(raw:str)->str|None:
    """The page's short microphone id beside client.audio_ready (lane HEAR, decision 2).

    The control parser keeps only the fields every audio_ready has, so the
    id is read from the message itself; None when absent or malformed.
    """

    try:
        message=json.loads(raw)
    except ValueError:
        return None
    return normalize_mic_id(message.get("mic_id")) if isinstance(message,dict) else None


async def _apply_stored_voice_filter(
    sender:LockedSender,session:ListenerSession,mic_id:str|None,
)->None:
    """Filter this run by level only as the person chose and measured (decision 2)."""

    try:
        settings=await asyncio.to_thread(_load_experimenter_settings)
    except Exception:  # noqa: BLE001 - the defaults: every voice is heard
        settings=dict(EXPERIMENTER_SETTING_DEFAULTS)
    session.set_speaker_sensitivity(str(settings.get("speaker_sensitivity",DEFAULT_SENSITIVITY)))
    state,reason,reference=stored_voice_filter(settings,mic_id,time.time())
    if reference is not None:
        session.level_reference=level_reference_from(reference.level_db,session.speaker_sensitivity)
    else:
        session.level_reference=None
        session.level_endpoint_on=False
    log.info("speaker.filter %s reason=%s microphone_known=%s",state,reason,mic_id is not None)
    margin=session.level_reference.margin_db if session.level_reference is not None else None
    await sender.text(
        "speaker.filter",configuration_id=session.accepted_configuration_id,
        state=state,reason=reason,margin_db=margin,
        measured_at=(
            datetime.fromtimestamp(reference.measured_at,tz=timezone.utc).isoformat()
            if reference is not None else None),
    )


def _run_paused(session:ListenerSession)->bool:
    curated=session.curated_protocol_session
    return curated is not None and getattr(curated,"workflow_status",None)=="paused"


async def _report_speech_drop(
    session:ListenerSession,sender:LockedSender,*,reason:str,turn_id:int|None,
    generation:int|None,duration_ms:int|None,level_db:float|None,
    during_playback:bool=False,
)->None:
    """Record and show that a sound was dropped, with its reason (decision 3; lane HEAR2, decision 4: never said).

    The log and the metrics keep the reason code, the level (dB) and the
    length (ms) only, never the sound or any words (AGENTS.md rule 8). While
    the run is paused nothing is shown beyond the pause band (decision 7).
    """

    reason=str(reason or "unknown")[:100]
    paused=_run_paused(session)
    level=round(float(level_db),1) if isinstance(level_db,(int,float)) and math.isfinite(level_db) else None
    log.info(
        "speech.dropped reason=%s duration_ms=%s level_db=%s paused=%s during_playback=%s turn_id=%s",
        reason,duration_ms,level,paused,during_playback,turn_id)
    _record_workspace_metric(
        category="voice",metric_name="speech_dropped",
        dimensions={"status":"rejected","reason_code":reason},
    )
    await sender.text(
        "speech.dropped",configuration_id=session.accepted_configuration_id,
        turn_id=turn_id,generation=generation,reason=reason,
        duration_ms=duration_ms,level_db=level,paused=paused,
        during_playback=during_playback,
        notice=None if paused else DROP_SCREEN_WORDS,
        reason_ko=drop_reason_words(reason),
    )


async def _report_stt_failure(
    session:ListenerSession,sender:LockedSender,error:BaseException,*,
    turn_id:int|None,generation:int|None,duration_ms:int|None,level_db:float|None,
    during_playback:bool=False,
)->None:
    """A speech-to-text provider that failed: shown, recorded and said once per outage (lane HEAR2).

    The log and the metrics keep the code and the status only -- never the
    provider's message, which may quote the request. Shown as a drop with
    the reason in plain words; said aloud the first time in an outage, and
    again only after a response has come back (``note_stt_ok``). Nothing
    while paused beyond the pause band.
    """

    code=str(getattr(error,"code",None) or "provider_error")[:40]
    status=getattr(error,"status",None)
    paused=_run_paused(session)
    level=round(float(level_db),1) if isinstance(level_db,(int,float)) and math.isfinite(level_db) else None
    log.warning(
        "stt.failed code=%s status=%s duration_ms=%s turn_id=%s during_playback=%s",
        code,status,duration_ms,turn_id,during_playback)
    log.info(
        "speech.dropped reason=stt_failed duration_ms=%s level_db=%s paused=%s during_playback=%s turn_id=%s",
        duration_ms,level,paused,during_playback,turn_id)
    _record_workspace_metric(
        category="voice",metric_name="speech_dropped",
        # The provider's code rides in event_kind: the store takes only its
        # privacy-safe dimension names and refuses a row with any other.
        dimensions={"status":"rejected","reason_code":"stt_failed","event_kind":code},
    )
    await sender.text(
        "speech.dropped",configuration_id=session.accepted_configuration_id,
        turn_id=turn_id,generation=generation,reason="stt_failed",code=code,
        status=status if isinstance(status,int) else None,
        duration_ms=duration_ms,level_db=level,paused=paused,
        during_playback=during_playback,
        notice=None if paused else STT_FAILURE_SCREEN_WORDS,
        reason_ko=STT_FAILURE_REASON_WORDS.get(code,STT_FAILURE_REASON_WORDS["provider_error"]),
    )
    if paused:
        return
    if session.note_stt_failure():
        session.track_visual_task(asyncio.create_task(_say_server_words(
            session,sender,STT_FAILURE_WORDS,kind="stt_failure")))


def _note_stt_adaptation_fallback(
    session:ListenerSession,transcription:Transcription,
)->SttAdaptationFallback|None:
    """Record one privacy-safe session transition after a hint rejection."""

    fallback=getattr(transcription,"adaptation_fallback",None)
    if not isinstance(fallback,SttAdaptationFallback):
        return None
    if session.note_stt_adaptation_fallback():
        log.warning(
            "stt.adaptation_fallback status=%s code=%s",
            fallback.status,fallback.code)
        _record_workspace_metric(
            category="voice",metric_name="stt_adaptation_fallback",
            dimensions={
                "status":"recovered",
                "reason_code":STT_ADAPTATION_FALLBACK_REASON,
                "event_kind":fallback.code,
            },
        )
    return fallback


async def _report_level_drop(
    session:ListenerSession,sender:LockedSender,level_db:float|None,
)->None:
    """A voice the filter dropped may move its line (decision 3); said and shown when it does."""

    action=session.note_level_drop(level_db)
    if action is None:
        return
    reference=session.level_reference
    log.info(
        "speaker.filter %s margin_db=%s level_db=%s",action,
        round(reference.margin_db,1) if reference is not None else None,
        round(level_db,1) if level_db is not None else None)
    _record_workspace_metric(
        category="voice",metric_name="speaker_filter",
        dimensions={"status":action,"reason_code":"wearer_dropped"},
    )
    await sender.text(
        "speaker.filter",configuration_id=session.accepted_configuration_id,
        state=action,reason="wearer_dropped",
        margin_db=round(reference.margin_db,1) if reference is not None else None,
        words=SPEAKER_FILTER_WORDS[action],
    )
    if not _run_paused(session):
        session.track_visual_task(asyncio.create_task(_say_server_words(
            session,sender,SPEAKER_FILTER_WORDS[action],kind="speaker_filter")))


#: Lane SP1, decision 3: the room's condition after this many ignored voices
#: in this many seconds, at most once in the gap. Since lane HEAR, decision 9
#: (2026-10-25), it is recorded only -- no question during a run.
AMBIENT_SUGGESTION_IGNORED_COUNT=3
AMBIENT_SUGGESTION_WINDOW_SECONDS=60.0
AMBIENT_SUGGESTION_GAP_SECONDS=600.0
#: Lane HEAR, decision 9: the room's noise floor is logged as a number this often while listening.
NOISE_FLOOR_RECORD_SECONDS=60.0


def _record_noise_condition(session:ListenerSession,kind:str)->None:
    """Record, never say, that the room is loud (lane HEAR, decision 9).

    "other_voices": three voices taken for other people's within a minute
    (lane SP1, decision 3); "floor_near_line": the noise floor near the
    wearer's line (lane SP2, decision 2). Each used to make the server ask
    "시끄러운 곳 모드로 바꿀까요?" in the middle of a run. The room is now
    chosen once before the start; this keeps codes and numbers only, at
    most once in AMBIENT_SUGGESTION_GAP_SECONDS.
    """

    session.ambient_suggested_at=session.clock()
    floor=session.noise_floor.level_db()
    reference=session.level_reference
    log.info(
        "noise.condition kind=%s floor_db=%s reference_db=%s ignored_in_window=%d",
        kind,round(floor,1) if floor is not None else None,
        round(reference.reference_db,1) if reference is not None else None,
        session.ignored_speech_in_last(AMBIENT_SUGGESTION_WINDOW_SECONDS))
    _record_workspace_metric(
        category="voice",metric_name="noise_condition",
        dimensions={"status":"recorded","reason_code":kind},
    )


def _record_noise_floor(session:ListenerSession)->None:
    """The room's noise floor as a number, once a minute while listening (decision 9)."""

    floor=session.noise_floor.level_db()
    if floor is None:
        return
    now=session.clock()
    last=getattr(session,"noise_floor_recorded_at",None)
    if last is not None and now-last<NOISE_FLOOR_RECORD_SECONDS:
        return
    session.noise_floor_recorded_at=now
    log.info("noise.floor floor_db=%.1f",floor)
    _record_workspace_metric(
        category="voice",metric_name="noise_floor_db",metric_value=float(floor),
        dimensions={"status":"recorded"},
    )


#: Lane SP2, decision 1: the level ends utterances only while the room's
#: noise floor (vad.NoiseFloor, the median of what was heard) sits at least
#: this far under the wearer's line, reference minus margin. On the synthetic
#: bench (lane SP2 report) babble, a pump and TV talk 9.7 dB or more under
#: the line let the level find the wearer's end without cutting the room into
#: turns; at 6.5 dB or nearer it cut TV talk, babble and a pump into stray
#: turns. Decision 2 says the room is loud from the same point.
LEVEL_ENDPOINT_FLOOR_GAP_DB=8.0
#: Lane SP2, decision 1: the level ends utterances only in a room where at
#: least this share of what is heard under the wearer's line is voiced. In
#: the synthetic bench's 150 s sessions (the wearer and a neighbour at 1 m
#: talking now and then) babble gave 0.32-0.91, a pump 1.0 and TV talk
#: 0.47-0.70, while a fan, a centrifuge or a quiet room gave 0.02-0.09 (lane
#: SP2 report): the line sits in the empty gap between them, a quarter.
ROOM_VOICED_SHARE=0.25


async def _timer_notice_tick(
    session:ListenerSession,sender:LockedSender,*,now:float|None=None,
    sleep:Callable[[float],Awaitable[Any]]=asyncio.sleep,
)->tuple[TimerNotice,...]:
    """Give the step timer's notices that are due (decisions 1-2), once each.

    Each is shown here, at once; saying it is handed to a task of its own
    (lane VF, decision 2), so one notice waiting for the experimenter to
    finish never holds back the watcher or the next notice.
    """

    curated=session.curated_protocol_session
    if (
        curated is None or not session.active or session.accepted_mode!="cascade"
        or not session.greeting_audio_ready
    ):
        return ()
    current=_wall_clock() if now is None else now
    for upcoming in curated.upcoming_timer_notices(now=current,within=TIMER_NOTICE_PREPARE_SECONDS):
        _prepare_timer_notice(session,curated,upcoming)
    notices=curated.due_timer_notices(now=current)
    for notice in notices:
        shown=await _show_timer_notice(session,sender,notice,now=current)
        _begin_saying_timer_notice(
            session,sender,curated,notice,sleep=sleep,
            wait_seconds=(
                TIMER_LAST_MINUTE_SPEAK_WAIT_SECONDS if notice.kind=="timer_last_minute"
                else TIMER_NOTICE_SPEAK_WAIT_SECONDS
            ),
            shown=shown,
        )
    return notices


async def _settle_timer_notices(session:ListenerSession)->None:
    """Wait for this session's notice tasks (for tests and an orderly end)."""

    while session.timer_notice_tasks:
        await asyncio.gather(*list(session.timer_notice_tasks),return_exceptions=True)


def _timer_notice_wait(session:ListenerSession,now:float)->float:
    """How long the watcher sleeps: to the next notice's due moment, a second at most."""

    curated=session.curated_protocol_session
    due=curated.next_timer_notice_due() if curated is not None else None
    if due is None:
        return TIMER_NOTICE_TICK_SECONDS
    return min(TIMER_NOTICE_TICK_SECONDS,max(TIMER_NOTICE_MIN_WAIT_SECONDS,due-now))


async def _watch_timer_notices(
    session:ListenerSession,sender:LockedSender,*,
    clock:Callable[[],float]|None=None,
    sleep:Callable[[float],Awaitable[Any]]=asyncio.sleep,
)->None:
    """For one voice connection: look at the step timer every second, and at each due moment."""

    read_clock=_wall_clock if clock is None else clock
    while True:
        await sleep(_timer_notice_wait(session,read_clock()))
        try:
            await _timer_notice_tick(session,sender,now=read_clock(),sleep=sleep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the next second tries again
            log.warning("timer notice failed error=%s",type(exc).__name__)


def _claim_admitted_answer(
    output:AnswerBrainOutput,
    plan:object,
) -> AnswerBrainOutput|None:
    """Keep independently supported claim sections when another claim is open."""

    unresolved=tuple(getattr(plan,"unresolved_claim_ids",()) or ())
    if not unresolved:
        return output
    requests=tuple(getattr(plan,"claim_requests",()) or ())
    admitted_ids={
        claim.claim_id for claim in requests
        if claim.admission_status is ClaimAdmissionStatus.LOCAL_SUPPORTED
    }
    sections=tuple(
        section for section in output.claim_sections
        if section[0] in admitted_ids
    )
    if not sections:
        return None
    limitations=tuple(
        claim.local_answer for claim in requests
        if claim.claim_id in unresolved and claim.local_answer
    )
    display="\n".join(f"• {section[1]}" for section in sections)
    if limitations:
        display += "\n\n" + "\n".join(f"• {item}" for item in limitations)
    evidence_ids=tuple(dict.fromkeys(
        evidence_id for _,_,section_ids in sections
        for evidence_id in section_ids
    ))
    return AnswerBrainOutput(
        spoken_answer=" ".join(section[1] for section in sections[:3]),
        display_answer=display,
        evidence_ids=evidence_ids,
        limitations=tuple(dict.fromkeys((*output.limitations,*limitations))),
        claim_sections=sections,
    )


def _record_router_history(
    session:"ListenerSession",curated:CuratedProtocolSession,
    outcome:RouterTurnOutcome,before:tuple[str|None,str],*,
    user:str,plan:Any,turn_id:int,generation:int,said:str|None=None,
    interrupted:bool=False,
)->None:
    """Keep one routed turn in the router's history (D14), front turns too.

    Lane RT, decision 2: ``said`` is the reply that went out (the speech
    when spoken, the screen text otherwise), and the bundle says what the
    server did -- what it stored, the timer and the question left open.
    """

    try:
        session.history.record_router_turn(history_turn(
            curated,outcome,before,user=user,plan=plan,said=said,
            next_turn_id=turn_id+1,
            configuration_id=session.accepted_configuration_id,
            generation=generation,interrupted=interrupted,
        ))
    except ValueError:
        log.warning("router history bundle rejected handled_by=%s",outcome.handled_by)


def _record_emergency_history(
    session:"ListenerSession",transcript:str,*,turn_id:int,generation:int,
)->None:
    """The emergency gate's turn (F1), kept in the router's history.

    Lane RT, decision 2: the words and the state are kept, never the reply,
    so the model does not imitate a safety reply it did not make.
    """

    curated=session.curated_protocol_session
    if curated is None or not session.llm_router_settings.enabled:
        return
    session.history.record_router_turn(front_history_turn(
        curated,history_before(curated),user=transcript,rule="emergency",
        next_turn_id=turn_id+1,configuration_id=session.accepted_configuration_id,
        generation=generation,
    ))


def _record_screen_history(
    session:"ListenerSession",before:tuple[str|None,str]|None,*,
    control:str,changed:bool,
)->None:
    """A bench control the screen pressed, kept in the router's history.

    Lane RT, decision 2: with the router on, the screen's pause and resume
    and a recovered run are history like a spoken turn, so the next model
    call reads them in order with the turns around them.
    """

    curated=session.curated_protocol_session
    if (
        curated is None or before is None
        or not session.llm_router_settings.enabled
    ):
        return
    try:
        session.history.record_router_turn(screen_history_turn(
            curated,before,control=control,changed=changed,
        ))
    except ValueError:
        log.warning("router history screen bundle rejected control=%s",control)


def _router_route_fields(
    outcome:RouterTurnOutcome,settings:LlmRouterSettings,
)->dict[str,object]:
    """What turn.route_decision says about a routed turn (router on only)."""

    usage=dict(outcome.reply.usage or {}) if outcome.reply is not None else {}
    return {
        "router":{
            "handled_by":outcome.handled_by,
            "fallback_reason":outcome.fallback_reason,
            "model":settings.model if outcome.model_called else None,
            "verdict":(
                outcome.verdict.reason_code if outcome.verdict is not None else None),
            "timings_ms":dict(outcome.timings_ms),
            "usage":usage,
            # Lane RA: the router first -- the read-only call, who wrote the
            # answer, and the roles' statuses.
            "router_first":bool(settings.router_first),
            "read_only":outcome.read_only.tool if outcome.read_only is not None else None,
            "answered_by":outcome.answered_by,
            "roles":dict(outcome.brain_statuses),
        },
    }


class _RouterRoles:
    """Lane RA, decision (b): the Answer, Source and Visual roles beside the router.

    Started by the router line together with its own model call, with the
    router's context as their facts; never raced against the rules' answer.
    The Answer role's answer goes out when it passes the same checks as the
    router's; the Source and Visual roles' outputs go where they went before
    (the research step, the picture's kind). Off, or with no role enabled,
    nothing is started.
    """

    def __init__(
        self,session:"ListenerSession",curated:CuratedProtocolSession,*,
        turn_id:int,generation:int,language:str,clock:Callable[[],float],
    )->None:
        self.session=session
        self.curated=curated
        self.turn_id=turn_id
        self.generation=generation
        self.language=language
        self.clock=clock

    def start(self,context:Any,transcript:str)->Any:
        settings=self.session.multi_brain_settings
        curated=self.curated
        activation=BrainActivation(
            answer=bool(settings.answer_brain_enabled),
            source=bool(settings.source_brain_enabled),
            visual=bool(settings.visual_brain_enabled),
        )
        if not settings.enabled or not activation.roles or not curated.active:
            return None
        step=curated.fixture.steps[curated.current_index]
        facts=tuple(
            BrainFact(fact_id,"router_evidence",text,page)
            for fact_id,(text,page) in list(context.evidence.items())[:24]
        )
        if not facts:
            return None
        snapshot=BrainSnapshot(
            configuration_id=self.session.accepted_configuration_id,
            session_id=self.session.session_id,
            turn_id=self.turn_id,generation_id=self.generation,
            workflow_revision=int(context.snapshot.get("workflow_revision") or 0),
            protocol_id=curated.fixture.protocol_id,
            document_sha256=curated.fixture.source_pdf_sha256 or "",
            step_id=step.step_id,step_index=curated.current_index,
            language=self.language,transcript=transcript,
            intent_kind="llm_router_turn",question_kind=None,
            requested_entities=(),question_dimensions=(),
            facts=facts,claims=(),
        )
        client=_role_client(RoleModel.from_environment("answer"))
        return HybridMultiBrain(client,settings,clock=self.clock).start(snapshot,activation)


async def _route_with_llm_router(
    session:"ListenerSession",curated:CuratedProtocolSession,transcript:str,*,
    turn_id:int,language:str,generation:int,transcript_quality:str|None,
    arbitration:Any,actor_principal_id:str|None,actor_role:str,
    rules_route:Callable[[],Awaitable[CuratedRuntimeRoute]],
    progress:Callable[...,Awaitable[bool]],clock:Callable[[],float],
)->RouterTurnOutcome:
    """Route one turn through the lane R router (only when it is enabled)."""

    settings=session.llm_router_settings

    def client()->Any:
        return _role_client(settings.role_model)

    async def said(state:str)->None:
        await progress(
            state,
            route=("brain" if state=="composing" else "curated_protocol"),
        )

    roles=(
        _RouterRoles(
            session,curated,turn_id=turn_id,generation=generation,
            language=language,clock=clock,
        )
        if settings.router_first and session.multi_brain_settings.enabled else None
    )
    return await route_turn_with_llm_router(
        curated,transcript,turn_id=turn_id,language=language,
        settings=settings,client_factory=client,rule_route=rules_route,
        history=session.history.router_history(),
        transcript_quality=transcript_quality,
        configuration_id=session.accepted_configuration_id,
        generation=generation,arbitration=arbitration,
        actor_principal_id=actor_principal_id,actor_role=actor_role,
        on_progress=said,clock=clock,roles=roles,
    )


#: Lane VOX, decision 3: the plans whose sentences may be said from memory --
#: readings of the protocol and the server's fixed sentences. Not the
#: repeat of the last reply, a record, an answer to a question, nor anything
#: that carries the experimenter's own words.
_KEPT_SPEECH_ACTIONS=frozenset({
    CuratedProtocolAction.START,CuratedProtocolAction.CURRENT,
    CuratedProtocolAction.FULL_DETAIL,CuratedProtocolAction.NEXT_INFORMATION,
    CuratedProtocolAction.COMPLETION_CRITERIA,CuratedProtocolAction.PREVIEW_STEP,
    CuratedProtocolAction.STEP_RANGE,CuratedProtocolAction.PROTOCOL_QUERY,
    CuratedProtocolAction.CLARIFY_COMPLETION,CuratedProtocolAction.PAUSE,
    CuratedProtocolAction.RESUME,CuratedProtocolAction.START_TIMER,
})
#: A plan's speech a model wrote.
_MODEL_WRITTEN_SPEECH=frozenset({"answer_brain_grounded","grounded_model"})


def _speech_kept(plan:Any)->bool:
    """Whether this plan's sentences may be said from memory (lane VOX, decision 3)."""

    return (
        plan.action in _KEPT_SPEECH_ACTIONS
        and not plan.transcript_corrections
        and not plan.transcript_correction_note
        and getattr(plan,"step_record",None) is None
        and getattr(plan,"note_record",None) is None
        and plan.translation_status not in _MODEL_WRITTEN_SPEECH
    )


@dataclass(frozen=True)
class _SpokenAnswer:
    """What a turn's spoken answer sent (lane VOX, decision 2)."""

    segments:int
    frames:int
    #: When the page, playing the segments one after another as they came,
    #: ends the last one.
    ends_at:float
    #: A later sentence could not be made; the answer stopped there.
    failed:bool=False


async def _say_answer(
    session:ListenerSession,sender:LockedSender,*,turn_id:int,generation:int,
    first_frames:list[bytes],later:tuple[str,...],language:str,
    clock:Callable[[],float],keep:bool=False,
)->_SpokenAnswer:
    """Send the first sentence's audio, then make and send each later one.

    Lane VOX, decision 2: the page plays a turn's segments in order as they
    come, so the first sentence is heard while the rest are made. Before a
    later sentence is asked for and again before its audio is sent, the turn
    must still be current in its generation: a barge-in cancels this
    coroutine (a sentence being made is dropped when it returns) and a piece
    of an older generation is never sent. A later sentence the provider
    cannot make ends the answer there; what was sent stays and nothing more
    is asked for. ``keep``: the sentences may be said from memory
    (decision 3).
    """

    def seconds(frames:list[bytes])->float:
        return sum(len(frame) for frame in frames)/32000

    sent_at=clock()
    await sender.segment(turn_id,0,first_frames,generation)
    spoken=_SpokenAnswer(1,len(first_frames),sent_at+seconds(first_frames))
    for sentence in later:
        if not session.is_current(turn_id,generation):
            break
        try:
            with sentence_audio_kept(keep):
                pcm=await asyncio.to_thread(synthesize,sentence,language)
        except Exception:
            log.exception("curated protocol sentence TTS failed")
            return replace(spoken,failed=True)
        frames=frame_complete_audio(pcm)
        if not frames:
            continue
        if not session.is_current(turn_id,generation):
            break
        sent_at=clock()
        await sender.segment(turn_id,spoken.segments,frames,generation)
        spoken=_SpokenAnswer(
            spoken.segments+1,spoken.frames+len(frames),
            max(spoken.ends_at,sent_at)+seconds(frames),
        )
    return spoken


async def run_turn(websocket:WebSocket,session:ListenerSession,source_pcm:bytes,turn_id:int,
                   input_frames:int,voiced_frames:int=0,
                   retained_prefix_frames:int=0,
                   clock:Callable[[],float]=time.perf_counter,
                   sender:LockedSender|None=None,filler:CascadeFiller|None=None,
                   accepted_transcription:Transcription|None=None,
                   accepted_stt_ms:int|None=None,
                   accepted_stt_context:CascadeTranscriptionContext|None=None,
                   voiced_level_db:float|None=None)->None:
    sender=sender or LockedSender(websocket); endpoint=session.endpoint_at or clock(); timings={}; generation=session.generation
    stt_reason_code:str|None=None
    async def current_text(kind:str,**fields)->bool:
        if not session.is_current(turn_id,generation): return False
        fields.setdefault("generation",generation)
        if session.accepted_configuration_id is not None:
            fields.setdefault("configuration_id",session.accepted_configuration_id)
        if kind=="turn.done" and stt_reason_code is not None:
            fields.setdefault("reason_code",stt_reason_code)
        await sender.text(kind,**fields)
        _pilot_turn_event(session,turn_id,kind,fields); return True

    async def report_state(report:dict)->bool:
        public=_public_experiment_report_state(report)
        store=session.experiment_report_store
        public["reports"]=(
            await asyncio.to_thread(
                store.list_reports,session_id=session.session_id)
            if store is not None else []
        )
        return await current_text(
            "experiment.report.state",turn_id=turn_id,
            session_id=session.session_id,report=public,
        )
    async def progress(
        state:str,*,route:str|None=None,
        timings_ms:dict[str,int|float]|None=None,
    )->bool:
        if not session.is_current(turn_id,generation): return False
        fields=session.advance_turn_progress(
            turn_id,generation,state,route=route,timings_ms=timings_ms)
        if fields is None: return False
        await sender.text("turn.state",**fields); return True
    async def finish_blocked_voice(text:str,route:str,*,outcome:str="blocked")->None:
        """Complete one deterministic non-mutating clarification turn."""

        timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
        if not await current_text(
            "reply.delta",turn_id=turn_id,segment_index=0,text=text
        ):
            return
        session.set_turn_terminal_outcome(turn_id,generation,outcome)
        try:
            await progress("synthesizing",route=route)
            pcm=await asyncio.to_thread(
                synthesize,said(text),session.accepted_language or "ko"
            )
            frames=frame_complete_audio(pcm)
            if filler is not None:
                await filler.primary_ready()
        except Exception:
            log.exception("blocked clarification TTS failed")
            await progress("error",route=route)
            await current_text("reply.complete",turn_id=turn_id,text=text)
            await current_text("audio.complete",turn_id=turn_id,segment_count=0)
            timings["total_ms"]=round((clock()-endpoint)*1000)
            await current_text(
                "turn.done",turn_id=turn_id,timings_ms=timings,
                segment_count=0,input_frames=input_frames,output_frames=0,
                tools_used=[],route=route,
            )
            if session.complete_without_playback(turn_id):
                await sender.text(
                    "state.changed",state=session.state.value,turn_id=turn_id,
                    cooldown_ms=session.detector.config.cooldown_ms,
                )
            return
        if session.start_playback(turn_id):
            timings["first_audio_ms"]=round((clock()-endpoint)*1000)
            await progress(
                "playing",route=route,
                timings_ms={"time_to_playable_audio":timings["first_audio_ms"]},
            )
            await current_text(
                "state.changed",state=session.state.value,turn_id=turn_id
            )
            await sender.segment(turn_id,0,frames,generation)
            await current_text("reply.complete",turn_id=turn_id,text=text)
            await current_text("audio.complete",turn_id=turn_id,segment_count=1)
            timings["total_ms"]=round((clock()-endpoint)*1000)
            await current_text(
                "turn.done",turn_id=turn_id,timings_ms=timings,
                segment_count=1,input_frames=input_frames,
                output_frames=len(frames),tools_used=[],route=route,
            )
    timings["utterance_to_status_ms"]=round((clock()-endpoint)*1000)
    await progress(
        "transcribing",
        timings_ms={"utterance_to_status":timings["utterance_to_status_ms"]})
    if not await current_text("turn.processing",turn_id=turn_id,input_frames=input_frames): return
    transcription_context=(
        accepted_stt_context
        or cascade_transcription_context(session,audio_origin="ordinary")
    )
    if transcription_context.google_v2_adaptation_disabled:
        stt_reason_code=STT_ADAPTATION_FALLBACK_REASON
    stt_language=transcription_context.language
    stt_keyterms=transcription_context.keyterms
    if accepted_transcription is None:
        started=clock()
        try:
            transcription=await asyncio.to_thread(
                transcribe_cascade_audio,source_pcm,transcription_context)
        except SpeechProviderError as exc:
            # Lane HEAR2: the provider failed -- the sound is dropped with
            # the reason, not a turn processing failure with a traceback.
            timings["stt"]=round((clock()-started)*1000)
            _record_workspace_metric(
                category="voice",metric_name="stt_latency_ms",
                metric_value=float(timings["stt"]),
                dimensions={"status":"failed","reason_code":str(exc.code)[:40]},
            )
            if session.reject_empty_transcript(turn_id):
                await sender.text(
                    "speech.rejected",turn_id=turn_id,generation=generation,reason="stt_failed",
                    voiced_frames=voiced_frames,total_frames=input_frames,duration_ms=input_frames*20,
                )
                await sender.text(
                    "state.changed",state=session.state.value,turn_id=turn_id,
                    cooldown_ms=session.detector.config.cooldown_ms,
                )
                await _report_stt_failure(
                    session,sender,exc,turn_id=turn_id,generation=generation,
                    duration_ms=input_frames*20,level_db=voiced_level_db)
            return
        timings["stt"]=round((clock()-started)*1000)
        if _note_stt_adaptation_fallback(session,transcription) is not None:
            stt_reason_code=STT_ADAPTATION_FALLBACK_REASON
        session.note_stt_ok()
    else:
        transcription=accepted_transcription
        timings["stt"]=max(0,accepted_stt_ms or 0)
        if _note_stt_adaptation_fallback(session,transcription) is not None:
            stt_reason_code=STT_ADAPTATION_FALLBACK_REASON
    # Keep test/custom adapters written to the pre-Phase-3 text-only boundary
    # working in manual mode while production returns structured metadata.
    if isinstance(transcription,str):
        transcription=Transcription(transcription,None)
    _record_workspace_metric(
        category="voice",metric_name="stt_latency_ms",
        metric_value=float(timings["stt"]),
        dimensions={
            "language_preference":getattr(
                session.accepted_input_language,"value",str(session.accepted_input_language)
            ),
            "detected_language":transcription.detected_language or "unknown",
            "status":"ok",
        },
    )
    transcript=transcription.text

    def persist_dropped(reason:str)->None:
        """Lane HEAR: with the opt-in recording on, a dropped turn's sound is kept too."""

        try:
            if not SttDiagnosticSettings.from_environment().enabled:
                return
            try:
                endpoint_label=stt_endpoint_label()
            except (KeyError,ConfigurationError):
                endpoint_label="unknown"
            persist_stt_diagnostic(
                clean_path(source_pcm),
                {
                    "configuration_id":session.accepted_configuration_id,
                    "session_id":session.session_id,"turn_id":turn_id,"generation":generation,
                    "input_sample_rate":16000,"frame_count":input_frames,
                    "voiced_frame_count":voiced_frames,"duration_ms":input_frames*20,
                    "endpoint_reason":f"dropped_{reason}","stt_endpoint":endpoint_label,
                    "language":stt_language,"keyterms":list(stt_keyterms),
                    "audio_origin":transcription_context.audio_origin,
                    "response_status":transcription.response_status,
                    "response_duration_seconds":transcription.duration_seconds,
                    "word_count":len(transcription.words),
                    "detected_language":transcription.detected_language,
                    "raw_transcript":transcription.text,
                    "browser_audio_constraints":dict(session.client_audio_constraints),
                },
                identity=f"session-{session.session_id}-turn-{turn_id}-g-{generation}",
            )
        except (OSError,ValueError):
            log.warning("opt-in STT diagnostic persistence failed")

    if not transcript.strip():
        persist_dropped("empty_transcript")
        if session.reject_empty_transcript(turn_id):
            _record_workspace_metric(
                category="voice",metric_name="command_failure",
                dimensions={"status":"rejected","reason_code":"empty_transcript"},
            )
            await sender.text("speech.rejected",turn_id=turn_id,reason="empty_transcript",voiced_frames=voiced_frames,total_frames=input_frames,duration_ms=input_frames*20)
            await sender.text("state.changed",state=session.state.value,turn_id=turn_id,cooldown_ms=session.detector.config.cooldown_ms)
            await _report_speech_drop(
                session,sender,reason="empty_transcript",turn_id=turn_id,
                generation=generation,duration_ms=input_frames*20,level_db=voiced_level_db)
        return
    committed_at=session.turn_committed_at.get(turn_id)
    echo=(
        session.own_speech_echo(
            transcript,heard_from=committed_at-input_frames*0.02)
        if accepted_transcription is None and committed_at is not None
        else None
    )
    if echo is not None:
        # Just after playback, the agent's own sentence came back (6b).
        log.info(
            "speech.rejected reason=self_echo (메아리로 버림) overlap=%.2f "
            "turn_id=%s",echo.overlap,turn_id)
        if session.reject_empty_transcript(turn_id):
            _record_workspace_metric(
                category="voice",metric_name="command_failure",
                dimensions={"status":"rejected","reason_code":"self_echo"},
            )
            await sender.text(
                "speech.rejected",turn_id=turn_id,generation=generation,
                reason="self_echo",voiced_frames=voiced_frames,
                total_frames=input_frames,duration_ms=input_frames*20,
            )
            await sender.text(
                "state.changed",state=session.state.value,turn_id=turn_id,
                cooldown_ms=session.detector.config.cooldown_ms,
            )
            await _report_speech_drop(
                session,sender,reason="self_echo",turn_id=turn_id,
                generation=generation,duration_ms=input_frames*20,level_db=voiced_level_db)
        return
    # A recognized emergency uses the one fixed-response gate before soft
    # admission or calibration can consume it. Empty input and self-echo
    # are already refused above; this never authorizes a state change.
    emergency=recognize_emergency(transcript)
    if emergency is None and _run_paused(session) and not is_paused_resume_utterance(transcript):
        # Lane HEAR, decision 7: while paused only a resume word is heard;
        # anything else gets no sound -- no language question, no asking
        # back, no drop notice. The pause band on the screen says why.
        log.info("speech.paused_not_resume turn_id=%s duration_ms=%s",turn_id,input_frames*20)
        if session.reject_empty_transcript(turn_id):
            # The page puts its "듣는 중" card away on speech.rejected.
            await sender.text(
                "speech.rejected",turn_id=turn_id,generation=generation,reason="paused",
                voiced_frames=voiced_frames,total_frames=input_frames,duration_ms=input_frames*20,
            )
            await sender.text(
                "state.changed",state=session.state.value,turn_id=turn_id,
                cooldown_ms=session.detector.config.cooldown_ms,
            )
            await _report_speech_drop(
                session,sender,reason="paused",turn_id=turn_id,generation=generation,
                duration_ms=input_frames*20,level_db=voiced_level_db)
        return
    # Lane HEAR, decision 4: on the rules path as well as the router's.
    # Lane RA, decision (d): words cut by the endpoint. A fragment held
    # by the turn before this one is joined with these words when they
    # started within the join window; a turn whose words end in an
    # unfinished Korean ending waits for the words that follow and, when
    # they come and supersede it, is not routed at all.
    held=session.take_held_fragment(
        turn_id,generation,
        speech_started_at=(committed_at-input_frames*0.02) if committed_at is not None else None,
    )
    if held is not None and emergency is None:
        transcript=f"{held} {transcript}".strip()
        emergency=recognize_emergency(transcript)
        await current_text(
            "turn.fragment_joined",turn_id=turn_id,text=transcript,
            fragment=held,window_seconds=FRAGMENT_JOIN_SECONDS)
    if (
        committed_at is not None
        and emergency is None
        and incomplete_fragment(transcript)
    ):
        session.hold_fragment(turn_id,generation,transcript,ended_at=committed_at)
        deadline=committed_at+FRAGMENT_HOLD_SECONDS
        # Lane HEAR, decision 4: words already under way when the hold
        # would end are waited for, up to one utterance's length; the
        # wait is bounded on the wall clock as well as the session's.
        longest=session.detector.config.maximum_utterance_frames*FRAME_MS/1000
        polls=int((FRAGMENT_HOLD_SECONDS+longest)/FRAGMENT_HOLD_POLL_SECONDS)+1
        plain_polls=int(FRAGMENT_HOLD_SECONDS/FRAGMENT_HOLD_POLL_SECONDS)+1
        try:
            for poll in range(polls):
                if not session.is_current(turn_id,generation):
                    # The words that follow took the turn; they join this fragment.
                    return
                following=session.interrupt_candidate_open(turn_id,generation)
                if (session.clock()>=deadline or poll>=plain_polls) and not following:
                    break
                if session.clock()>=deadline+longest:
                    break
                await asyncio.sleep(FRAGMENT_HOLD_POLL_SECONDS)
        except asyncio.CancelledError:
            # Superseded: the fragment stays held for the turn that follows.
            raise
        if not session.release_fragment(turn_id,generation):
            return
    if emergency is not None:
        if not await current_text("transcript",turn_id=turn_id,text=transcript):
            return
        timings["route_started_ms"]=round((clock()-endpoint)*1000)
        await progress("routing",timings_ms={"route_started":timings["route_started_ms"]})
        _record_emergency_history(
            session,transcript,turn_id=turn_id,generation=generation)
        text=emergency.response
        timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
        if not await current_text("reply.delta",turn_id=turn_id,segment_index=0,text=text): return
        try:
            await progress("synthesizing",route="deterministic_emergency")
            pcm=await asyncio.to_thread(synthesize,said(text),emergency.language)
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
    input_decision=classify_input_event(
        transcription,
        keyterms=keyterm_dump_terms(stt_keyterms),
        duration_seconds=transcription.duration_seconds,
    )
    if not input_decision.accepted or transcript_is_filler_only(transcript):
        # Lane SP1, decision 5: a machine sound the VAD took for speech --
        # the STT wrote nothing, or a filler -- is passed over in silence.
        reason=(
            input_decision.reason or "non_speech"
            if not input_decision.accepted else "filler_only")
        if session.reject_empty_transcript(turn_id):
            _record_workspace_metric(
                category="voice",metric_name="command_failure",
                dimensions={
                    "status":"rejected",
                    "reason_code":str(reason)[:100],
                },
            )
            log.info(
                "speech.rejected reason=%s voiced_frames=%d total_frames=%d turn_id=%s",
                reason,voiced_frames,input_frames,turn_id)
            await sender.text(
                "speech.rejected",turn_id=turn_id,generation=generation,
                reason=reason,
                voiced_frames=voiced_frames,total_frames=input_frames,
                duration_ms=input_frames*20,
            )
            await sender.text(
                "state.changed",state=session.state.value,turn_id=turn_id,
                cooldown_ms=session.detector.config.cooldown_ms,
            )
            await _report_speech_drop(
                session,sender,reason=str(reason),turn_id=turn_id,
                generation=generation,duration_ms=input_frames*20,level_db=voiced_level_db)
        return
    if session.calibration_pending:
        # Lane SP1, decision 1: the sentence read to measure the wearer's
        # voice level. It is no command: nothing is routed, nothing changes.
        await _finish_voice_calibration(
            session,current_text,finish_blocked_voice,transcript,
            voiced_level_db=voiced_level_db,turn_id=turn_id)
        return
    admission = classify_transcription_language(
        transcription,session.accepted_input_language,
        # The protocol's own terms, not one document's list.
        known_terms=(
            tuple(stt_keyterms)
            if session.curated_protocol_session is not None else None
        ),
    )
    if admission.correction_class is not None:
        transcript = admission.admitted_text
    if admission.mismatch_status=="unclear":
        # Lane HEAR, decision 8: Latin letters for Korean speech -- a turn
        # misheard, not another language: dropped and shown, not asked about.
        persist_dropped("language_unclear")
        if session.reject_empty_transcript(turn_id):
            await sender.text(
                "speech.rejected",turn_id=turn_id,generation=generation,reason="language_unclear",
                voiced_frames=voiced_frames,total_frames=input_frames,duration_ms=input_frames*20,
            )
            await sender.text(
                "state.changed",state=session.state.value,turn_id=turn_id,
                cooldown_ms=session.detector.config.cooldown_ms,
            )
            await _report_speech_drop(
                session,sender,reason="language_unclear",turn_id=turn_id,
                generation=generation,duration_ms=input_frames*20,level_db=voiced_level_db)
        return
    cleaned_source=clean_path(source_pcm)
    diagnostic_wav=pcm_to_wav(cleaned_source)
    stt_diagnostic_metadata={
        "configuration_id":session.accepted_configuration_id,
        "session_id":session.session_id,
        "turn_id":turn_id,"generation":generation,
        "input_sample_rate":16000,
        "frame_count":input_frames,"voiced_frame_count":voiced_frames,
        "duration_ms":input_frames*20,
        "endpoint_reason":"accepted_endpoint",
        "configured_prefix_frames":session.detector.config.prefix_frames,
        "configured_prefix_ms":session.detector.config.prefix_frames*20,
        "retained_prefix_frames":max(0,retained_prefix_frames),
        "retained_prefix_ms":max(0,retained_prefix_frames)*20,
        "stt_endpoint":stt_endpoint_label(),
        "request_field_order":transcription_context.request_policy()[
            "request_field_order"],
        "language":stt_language,"keyterms":list(stt_keyterms),
        "audio_origin":transcription_context.audio_origin,
        "pending_frame":transcription_context.pending_frame,
        "response_status":transcription.response_status,
        "response_duration_seconds":transcription.duration_seconds,
        "word_count":len(transcription.words),
        "detected_language":transcription.detected_language,
        "raw_transcript":transcription.text,
        "normalized_transcript":" ".join(transcript.strip().split()),
        "correction_class":admission.correction_class,"clarification_required":admission.clarification_required,
        "intent_kind":None,"action":None,"mutation_authorized":False,
        "browser_audio_constraints":dict(session.client_audio_constraints),
        "wav_sha256":hashlib.sha256(diagnostic_wav).hexdigest(),
        "wav_byte_count":len(diagnostic_wav),
    }
    await current_text(
        "stt.diagnostics",turn_id=turn_id,
        detected_language=transcription.detected_language,
        input_sample_rate=16000,frame_count=input_frames,
        voiced_frame_count=voiced_frames,duration_ms=input_frames*20,
        endpoint_reason="accepted_endpoint",
        configured_prefix_frames=session.detector.config.prefix_frames,
        configured_prefix_ms=session.detector.config.prefix_frames*20,
        response_status=transcription.response_status,
        response_duration_seconds=transcription.duration_seconds,
        word_count=len(transcription.words),
        words=list(transcription.words),
        stt_endpoint=stt_diagnostic_metadata["stt_endpoint"],
        wav_sha256=stt_diagnostic_metadata["wav_sha256"],
        wav_byte_count=stt_diagnostic_metadata["wav_byte_count"],
        language_bias=stt_language,
        keyterm_count=len(stt_keyterms),
        keyterms=list(stt_keyterms),
        request_field_order=stt_diagnostic_metadata["request_field_order"],
        audio_origin=transcription_context.audio_origin,
        pending_frame=transcription_context.pending_frame,
        retained_prefix_frames=max(0,retained_prefix_frames),
        retained_prefix_ms=max(0,retained_prefix_frames)*20,
        browser_audio_constraints=dict(session.client_audio_constraints),
    )
    try:
        persist_stt_diagnostic(
            cleaned_source,stt_diagnostic_metadata,
            identity=f"session-{session.session_id}-turn-{turn_id}-g-{generation}",
        )
    except (OSError,ValueError):
        log.warning("opt-in STT diagnostic persistence failed")
    timings["route_started_ms"]=round((clock()-endpoint)*1000)
    await progress(
        "routing",timings_ms={"route_started":timings["route_started_ms"]})
    if admission.clarification_required:
        _record_workspace_metric(
            category="voice",metric_name="language_mismatch",
            dimensions={
                "language_preference":admission.expected_language or "auto",
                "detected_language":admission.detected_language or "unknown",
                "reason_code":admission.mismatch_status or "language_uncertain",
                "status":"blocked",
            },
        )
        await current_text(
            "stt.language_mismatch",turn_id=turn_id,
            configured_language=admission.expected_language,
            detected_language=admission.detected_language,
            reason=admission.mismatch_status or "language_uncertain",
            mutation_authorized=False,
        )
        await current_text(
            "session.language_confirmation_required",turn_id=turn_id,
            reason=admission.mismatch_status or "language_uncertain",
            languages=[admission.expected_language],
        )
        await finish_blocked_voice(
            admission.clarification_message
            or "음성 인식 언어가 불확실합니다. 다시 한 번 말씀해 주세요.",
            "language_clarification",
        )
        return
    # Lane HEAR, decision 3: words were heard; the drops before them no longer count.
    session.note_heard()
    if not await current_text("transcript",turn_id=turn_id,text=transcript):
        return
    if session.curated_protocol_session is not None:
        turn_language="ko"
        session.last_confirmed_language="ko"
    else:
        resolution=resolve_turn_language(
            transcript,transcription.detected_language,mode=session.language_mode,
            manual_language=session.manual_language,
        )
        if not resolution.resolved:
            fallback=session.last_confirmed_language or (
                session.tool_context.language if session.tool_context else "ko")
            text=CLARIFICATION_TEXT.get(fallback,CLARIFICATION_TEXT["ko"])
            timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
            await current_text("session.language_confirmation_required",turn_id=turn_id,
                               reason=resolution.reason,languages=["ko","en"])
            await current_text("reply.delta",turn_id=turn_id,segment_index=0,text=text)
            session.set_turn_terminal_outcome(turn_id,generation,"blocked")
            try:
                await progress("synthesizing",route="language_clarification")
                pcm=await asyncio.to_thread(synthesize,said(text),fallback)
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
    if voice_calibration_asked(transcript) and session.curated_protocol_session is not None:
        # Lane HEAR, decision 1: not during a run; it is done in the settings.
        await current_text(
            "speaker.calibration",turn_id=turn_id,state="refused",reason="settings_only",
            words=VOICE_CALIBRATION_IN_RUN_WORDS,sensitivity=session.speaker_sensitivity)
        log.info("voice calibration refused by voice reason=settings_only turn_id=%s",turn_id)
        await finish_blocked_voice(
            VOICE_CALIBRATION_IN_RUN_WORDS,"voice_calibration",outcome="complete")
        return
    if voice_calibration_asked(transcript):
        # Lane SP1, decision 1: "목소리 다시 맞춰 줘" -- the next sentence
        # measures the wearer again. A front rule of the server's: read-only.
        session.arm_voice_calibration()
        await current_text(
            "speaker.calibration",turn_id=turn_id,state="prompted",
            sentence=VOICE_CALIBRATION_SENTENCE,sensitivity=session.speaker_sensitivity)
        log.info("voice calibration prompted by voice turn_id=%s",turn_id)
        await finish_blocked_voice(
            VOICE_CALIBRATION_PROMPT,"voice_calibration",outcome="complete")
        return
    request_arbitration=arbitrate_request(transcript)
    if session.curated_protocol_session is not None:
        curated=session.curated_protocol_session
        try:
            await asyncio.to_thread(_require_current_voice_connection,session)
        except WorkspaceError as exc:
            await current_text(
                "experiment.session.error",turn_id=turn_id,
                code=getattr(exc,"code","workspace_error"))
            await finish_blocked_voice(
                "다른 연결에서 이 실험을 이어하고 있어요. 상태 변경을 확정하지 않았습니다. 현재 연결을 확인해 주세요.",
                "curated_protocol",outcome="blocked")
            return
        checkpoint=curated._checkpoint()
        curated_tools_used=[]
        report_prepared=False
        #: The report store refused the experiment's end after it committed.
        end_report_failed=False
        workflow_mutation_committed=False
        plan=None
        brain_run=None
        brain_activation=None
        brain_terminals={}
        answer_output=None
        brain_snapshot=None
        router_outcome:RouterTurnOutcome|None=None
        #: Lane WV, decision 2: whether this turn asks the web after the answer.
        web_lookup_now=False
        #: Lane WV, decision 3: whether this turn draws the step after the answer.
        drawing_now=False
        router_before=(
            history_before(curated)
            if session.llm_router_settings.enabled else None
        )
        #: Lane VT, decision 5: the silence before these words.
        silence=curated.note_heard(now=_wall_clock())
        try:
            timings["protocol_lookup_started_ms"]=round((clock()-endpoint)*1000)
            if not session.llm_router_settings.enabled:
                await progress("checking_protocol",route="curated_protocol")
            pre_transition_index=curated.current_index
            turn_actor_principal_id,turn_actor_role=_voice_turn_actor()
            async def rules_route()->CuratedRuntimeRoute:
                return route_curated_runtime_turn(
                    curated,
                    transcript,turn_id=turn_id,language=turn_language,
                    transcript_quality=transcription_quality_issue(transcription),
                    configuration_id=session.accepted_configuration_id,
                    generation=generation,
                    arbitration=request_arbitration,
                    actor_principal_id=turn_actor_principal_id,
                    actor_role=turn_actor_role)
            if session.llm_router_settings.enabled:
                router_outcome=await _route_with_llm_router(
                    session,curated,transcript,turn_id=turn_id,
                    language=turn_language,generation=generation,
                    transcript_quality=transcription_quality_issue(transcription),
                    arbitration=request_arbitration,
                    actor_principal_id=turn_actor_principal_id,
                    actor_role=turn_actor_role,rules_route=rules_route,
                    progress=progress,clock=clock)
                if router_outcome.handled_by=="model_unavailable":
                    # Lane FX3, decision 8: the router's model was at its
                    # request limit. Nothing was sent again and nothing was
                    # routed; the turn says so once and changes nothing.
                    log.info(
                        "router model unavailable turn_id=%s reason=%s",
                        turn_id,router_outcome.fallback_reason)
                    _pilot_turn_event(session,turn_id,"router.stopped",{
                        "fallback_reason":router_outcome.fallback_reason})
                    _record_workspace_metric(
                        category="voice",metric_name="command_failure",
                        dimensions={"status":"stopped","reason_code":str(router_outcome.fallback_reason)[:100]},
                    )
                    await finish_blocked_voice(ROUTER_UNAVAILABLE_WORDS,"model_unavailable")
                    return
                routed_turn=(
                    replace(router_outcome.rule_route,plan=router_outcome.plan)
                    if router_outcome.rule_route is not None else
                    CuratedRuntimeRoute(
                        arbitration=request_arbitration,
                        runtime_router="llm_router",
                        plan=router_outcome.plan,
                    )
                )
                for name,value in router_outcome.timings_ms.items():
                    timings[f"router_{name}"]=value
            else:
                routed_turn=await rules_route()
            plan=routed_turn.plan
            await current_text(
                "turn.route_decision",turn_id=turn_id,
                normalized_text=routed_turn.arbitration.normalized_text,
                intent=routed_turn.arbitration.intent.value,
                confidence=routed_turn.arbitration.confidence,
                reason_code=routed_turn.arbitration.reason_code,
                dimensions=list(routed_turn.arbitration.dimensions),
                runtime_router=routed_turn.runtime_router,
                action=plan.action.value,
                state_mutation=bool(plan.state_changed),
                answer_origin=plan.answer_origin,
                fallback_reason=(
                    None if plan.answer_origin not in {"unsupported","current_protocol"}
                    else "local_specialized_answer_unavailable"
                    if routed_turn.arbitration.intent.value in {
                        "learning","protocol_audit","history_resume",
                        "uncertainty","combined_learning_next",
                    }
                    else None
                ),
                **(
                    _router_route_fields(router_outcome,session.llm_router_settings)
                    if router_outcome is not None else {}
                ),
            )
            log.info(
                "turn.route_decision turn_id=%s generation=%s text_sha256=%s "
                "intent=%s runtime_router=%s action=%s state_mutation=%s "
                "answer_origin=%s fallback_reason=%s",
                turn_id,generation,
                hashlib.sha256(
                    routed_turn.arbitration.normalized_text.encode("utf-8")
                ).hexdigest()[:16],
                routed_turn.arbitration.intent.value,
                routed_turn.runtime_router,plan.action.value,
                bool(plan.state_changed),plan.answer_origin,
                (
                    None if plan.answer_origin not in {"unsupported","current_protocol"}
                    else "local_specialized_answer_unavailable"
                ),
            )
            _record_workspace_metric(
                category="agent",metric_name="turn_route",
                dimensions={
                    "intent":routed_turn.arbitration.intent.value,
                    "route":"curated_protocol",
                    "answer_origin":plan.answer_origin,
                    "reason_code":routed_turn.arbitration.reason_code or "none",
                    "status":"mutated" if plan.state_changed else "read_only",
                },
            )
            stt_diagnostic_metadata.update({
                "normalized_transcript":(
                    plan.normalized_transcript
                    or " ".join(transcript.strip().split())
                ),
                "correction_class":(
                    "bounded_protocol_vocabulary"
                    if plan.transcript_corrections else None
                ),
                "clarification_required":plan.action in {
                    CuratedProtocolAction.CLARIFY_COMPLETION,
                    CuratedProtocolAction.CLARIFY_REFERENCE,
                    CuratedProtocolAction.TRANSCRIPT_UNRELIABLE,
                },
                "intent_kind":plan.intent_kind,
                "action":plan.action.value,
                "mutation_authorized":bool(plan.state_changed),
            })
            await current_text(
                "turn.interpretation",turn_id=turn_id,
                raw_transcript=transcript,
                normalized_transcript=stt_diagnostic_metadata[
                    "normalized_transcript"],
                transcript_corrections=[list(item) for item in plan.transcript_corrections],
                intent_kind=plan.intent_kind,action=plan.action.value,
                mutation_authorized=bool(plan.state_changed),
                coreference_status=plan.coreference_status,
                coreference_reason=plan.coreference_reason,
            )
            try:
                persist_stt_diagnostic(
                    cleaned_source,stt_diagnostic_metadata,
                    identity=f"session-{session.session_id}-turn-{turn_id}-g-{generation}",
                )
            except (OSError,ValueError):
                log.warning("opt-in STT diagnostic update failed")
            brain_facts=tuple(plan.facts)
            brain_activation=activation_for(
                intent_kind=plan.intent_kind,
                visual_requested=plan.visual_requested,
                unresolved_dimensions=plan.unresolved_dimensions,
            )
            if (
                session.multi_brain_settings.enabled
                and brain_activation.roles
                and brain_facts
                and curated.active
            ):
                step=curated.fixture.steps[curated.current_index]
                brain_client=_role_client(RoleModel.from_environment("answer"))
                snapshot=BrainSnapshot(
                    configuration_id=session.accepted_configuration_id,
                    session_id=session.session_id,
                    turn_id=turn_id,generation_id=generation,
                    workflow_revision=curated.state()["revision"],
                    protocol_id=curated.fixture.protocol_id,
                    document_sha256=curated.fixture.source_pdf_sha256 or "",
                    step_id=step.step_id,step_index=curated.current_index,
                    language=turn_language,transcript=transcript,
                    intent_kind=plan.intent_kind,
                    question_kind=plan.question_kind,
                    requested_entities=plan.requested_entities,
                    question_dimensions=plan.question_dimensions,
                    facts=tuple(BrainFact(
                        fact.fact_id,fact.kind,fact.text,fact.source_page)
                        for fact in brain_facts[:24]
                    ),
                    claims=tuple(BrainClaim(
                        claim.claim_id,claim.target_type.value,claim.target_id,
                        claim.dimension,claim.required_authority,
                        claim.evidence_ids,claim.admission_status.value,
                        claim.unresolved_reason,
                    ) for claim in plan.claim_requests),
                )
                brain_snapshot=snapshot
                brain_run=HybridMultiBrain(
                    brain_client,session.multi_brain_settings,clock=clock,
                ).start(snapshot,brain_activation)
                await current_text(
                    "brain.state",turn_id=turn_id,status="running",
                    roles=list(brain_activation.roles),
                    workflow_revision=snapshot.workflow_revision,
                    step_id=snapshot.step_id,
                )
                # A slow model may enrich this Turn later, but it may not hold
                # the first locally grounded browser answer or TTS hostage.
                answer_terminal=await brain_run.terminal(
                    "answer",
                    timeout=session.multi_brain_settings.primary_answer_budget_seconds,
                )
                if answer_terminal is not None:
                    brain_terminals["answer"]=answer_terminal
                    timings["answer_brain_ms"]=answer_terminal.elapsed_ms
                    await current_text(
                        "brain.state",turn_id=turn_id,role="answer",
                        status=answer_terminal.status,
                        elapsed_ms=answer_terminal.elapsed_ms,
                    )
                    if (
                        answer_terminal.status=="success"
                        and isinstance(answer_terminal.output,AnswerBrainOutput)
                        and session.is_current(turn_id,generation)
                        and curated.state()["revision"]==snapshot.workflow_revision
                        and curated.fixture.steps[curated.current_index].step_id
                        ==snapshot.step_id
                    ):
                        answer_output=_claim_admitted_answer(
                            answer_terminal.output,plan)
                        if answer_output is None:
                            await current_text(
                                "brain.output.rejected",turn_id=turn_id,
                                role="answer",
                                reason="unresolved_claim_dimensions",
                                dimensions=list(plan.unresolved_dimensions),
                                unresolved_claim_ids=list(
                                    plan.unresolved_claim_ids),
                            )
                        if (
                            answer_output is not None
                            and plan.action is CuratedProtocolAction.OPERATIONAL_DEVIATION
                        ):
                            plan=replace(
                                plan,
                                display_text=(
                                    f"{plan.display_text}\n\nGeneral background (read-only)\n"
                                    f"{answer_output.display_answer}"
                                ),
                                limitations=tuple(dict.fromkeys((
                                    *plan.limitations,*answer_output.limitations,
                                    "Answer Brain output cannot authorize the deviation.",
                                ))),
                            )
                        elif answer_output is not None:
                            plan=replace(
                                plan,
                                display_text=answer_output.display_answer,
                                speech_text=answer_output.spoken_answer,
                                primary_text=answer_output.display_answer,
                                evidence_ids=answer_output.evidence_ids,
                                limitations=tuple(dict.fromkeys((
                                    *plan.limitations,*answer_output.limitations,
                                ))),
                                translation_status="answer_brain_grounded",
                            )
            research_context=None
            problem_causes:dict[str,Any]|None=None
            problem_record_failed=False
            explanatory_visual_request=bool(
                plan.action is CuratedProtocolAction.VISUAL_REQUEST
                and plan.requested_entities
                and re.search(
                    r"(?:의미|뜻|역할|왜|설명|뭐\s*하는|what|why|meaning|role|explain|purpose)",
                    transcript,
                    re.IGNORECASE,
                )
            )
            if (
                plan.action is CuratedProtocolAction.RELATED_QUESTION
                or explanatory_visual_request
            ) and curated.active:
                step=curated.fixture.steps[curated.current_index]
                facts=plan.facts or curated.related_facts(transcript)
                resolved_query=curated.reference_query_for(transcript,plan)
                if resolved_query is None:
                    raise RuntimeError("related reference query is unavailable")
                reference_query=plan_research_query(
                    resolved_query,protocol_title=curated.fixture.title,
                    step_label=step.source_label,
                    step_text=step.instruction_source_text,
                    evidence_texts=tuple(fact.text for fact in facts),
                    requested_entity=plan.requested_entity,
                    requested_entities=plan.requested_entities,
                    question_kind=plan.question_kind,
                    question_dimensions=(
                        plan.unresolved_dimensions or plan.question_dimensions
                    ),
                    **curated.research_scope(),
                )
                envelope=curated.protocol_answer_envelope(
                    replace(plan,facts=tuple(facts)),language=turn_language)
                speech=envelope.speech_summary
                # The answer alone is the reply.
                display=envelope.direct_answer
                plan=replace(
                    plan,display_text=display,speech_text=speech,
                    speech_mode=CuratedProtocolSpeechMode.VERIFIED_FACT,
                    facts=tuple(facts),primary_text=envelope.direct_answer,
                    source_texts=tuple(fact.text for fact in facts[:8]),
                    source_pages=tuple(fact.source_page for fact in facts[:8]),
                    evidence_ids=tuple(fact.fact_id for fact in facts[:8]),
                    translation_status="deterministic_protocol_structure",
                    answer_origin="current_protocol",
                    source_plan_scopes=envelope.source_plan.scopes,
                    unresolved_dimensions=(
                        envelope.source_plan.unresolved_dimensions),
                )
                if isinstance(answer_output,AnswerBrainOutput):
                    plan=replace(
                        plan,
                        display_text=answer_output.display_answer,
                        speech_text=answer_output.spoken_answer,
                        primary_text=answer_output.display_answer,
                        evidence_ids=answer_output.evidence_ids,
                        limitations=tuple(dict.fromkeys((
                            *plan.limitations,*answer_output.limitations,
                        ))),
                        translation_status="answer_brain_grounded",
                    )
                research_context={
                    "query":resolved_query,"reference_query":reference_query,
                    "step":step,"facts":tuple(facts),
                    "force_external":plan.requested_followup=="search_external_reference",
                    "question":transcript,
                    # Lane R6, decision 3: a quantity the source answers is
                    # never given model knowledge.
                    "source_answers_quantity":curated.quantity_answered_by_source(transcript),
                    "outside_pdf":_outside_pdf_gap(session,curated,plan,transcript,turn_language),
                }
                session.begin_research(turn_id,generation)
            elif (
                curated.active
                and _outside_pdf_gap(session,curated,plan,transcript,turn_language) is not None
            ):
                # "이 단계 왜 하는 거야?": the rules' answer, then a short
                # outside-PDF explanation and nothing else (lane R6, decision 6).
                step=curated.fixture.steps[curated.current_index]
                research_context={
                    "query":transcript,"reference_query":transcript,
                    "step":step,"facts":tuple(plan.facts),"force_external":False,
                    "question":transcript,"source_answers_quantity":False,
                    "outside_pdf":_outside_pdf_gap(session,curated,plan,transcript,turn_language),
                    "explain_only":True,
                }
                session.begin_research(turn_id,generation)
            if plan.action in {
                CuratedProtocolAction.REPORT_ANOMALY,
                CuratedProtocolAction.SHOW_REPORT,
            }:
                if (
                    session.experiment_report_store is None
                    and plan.action is CuratedProtocolAction.SHOW_REPORT
                ):
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
                elif session.experiment_report_store is not None:
                    try:
                        report=await _voice_report_write(
                            _record_experiment_report_plan,
                            session,curated,plan,
                            turn_id=turn_id,generation=generation,
                            pre_transition_index=pre_transition_index,
                        )
                    except Exception:
                        problem_record_failed=True
                        failed=(
                            "실험 기록을 저장하지 못해 이상 사항이 기록되었다고 확인할 수 없습니다. "
                            "프로토콜 상태는 변경하지 않았습니다."
                            if turn_language=="ko" else
                            "The experiment record could not be saved, so the issue was not acknowledged as recorded. The protocol state did not change."
                        )
                        plan=replace(
                            plan,display_text=failed,speech_text=failed,
                            speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                        )
                        await current_text(
                            "experiment.report.error",turn_id=turn_id,
                            code="report_persistence_failed")
                    else:
                        report_prepared=True
                        if plan.action is CuratedProtocolAction.REPORT_ANOMALY:
                            follow_up = (
                                " 어떤 종류의 색 변화를 보셨나요?"
                                if "What kind of color change" not in (plan.speech_text or "")
                                and "색 변화" in (plan.speech_text or "") else
                                " What kind of color change did you observe?"
                                if "What kind of color change" in (plan.speech_text or "") else
                                ""
                            )
                            if plan.intent_kind=="observation_with_anomaly":
                                # The endpoint was reported with a problem: the
                                # endpoint question asked again is what is heard.
                                acknowledged=curated.observation_anomaly_reply(
                                    plan.step_label,turn_language,recorded=True)
                            elif turn_language=="ko":
                                acknowledged=(
                                    f"말씀하신 이상 사항을 현재 {plan.step_label}단계 실험 기록에 남겼습니다. "
                                    "프로토콜 상태는 변경하지 않았습니다."
                                    + follow_up
                                )
                            else:
                                acknowledged=(
                                    f"The reported issue was added to the experiment record for step {plan.step_label}. "
                                    "The protocol state did not change."
                                    + follow_up
                                )
                            plan=replace(
                                plan,display_text=acknowledged,
                                speech_text=acknowledged,
                                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                            )
                        await report_state(report)
            if getattr(plan,"problem_help",None) is not None:
                # Lane HELP, decision 1-2: the sources' help after the reply
                # (an anomaly's read-back first), and the record of it.
                plan,problem_causes=await _with_problem_help(
                    session,curated,plan,language=turn_language,timings=timings,
                    turn_id=turn_id,generation=generation)
                if problem_causes is not None:
                    session.begin_research(turn_id,generation)
                if session.experiment_report_store is not None and not problem_record_failed:
                    # A problem whose own record failed gets the help, read-
                    # only, and no record of it beside a record that is not there.
                    try:
                        report=await _voice_report_write(
                            _record_problem_help,session,curated,plan,
                            turn_id=turn_id,generation=generation)
                    except Exception as exc:
                        log.warning(
                            "problem help not recorded turn_id=%s error=%s",
                            turn_id,type(exc).__name__)
                    else:
                        report_prepared=True
                        await report_state(report)
            # A Korean reader asked to hear a step that has no reviewed
            # translation: a checked automatic reading, or the source as before.
            reader_target=(
                curated.reader_translation_target(plan)
                if turn_language=="ko" else None
            )
            if reader_target is not None:
                plan=await _apply_reader_translation(
                    session,curated,plan,reader_target,
                    turn_id=turn_id,generation=generation,clock=clock,
                )
            if turn_language=="ko":
                # Lane KO2, decision 6: the purpose in the current style.
                plan=await _fresh_purpose(
                    session,curated,plan,turn_id=turn_id,generation=generation,clock=clock)
                # Lane SAY, decision 1: Korean first -- no source sentence is
                # read aloud in English unless it was asked for.
                plan=await _korean_first(
                    session,curated,plan,turn_id=turn_id,generation=generation,clock=clock)
            plan=_label_machine_translation(session,curated,plan)
            display_text=plan.display_text
            speech_text=plan.speech_text
            speech_policy=getattr(plan,"speech_policy","speak")
            if not isinstance(display_text,str) or not display_text.strip():
                raise RuntimeError("curated protocol produced no display text")
            if speech_policy=="speak":
                if not isinstance(speech_text,str) or not speech_text.strip():
                    raise RuntimeError("curated protocol produced no speech text")
            timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
            if plan.speech_mode.value=="blocked":
                session.set_turn_terminal_outcome(
                    turn_id,generation,"blocked")
            if (
                plan.action is CuratedProtocolAction.REPORT_REVIEW
                and plan.report_review is not None
            ):
                # Lane N, decision 3: what this reply confirmed, corrected or
                # left for the screen is stored before it is said.
                try:
                    review_report=await asyncio.to_thread(
                        _record_report_review,session,plan,
                        turn_id=turn_id,generation=generation,
                    )
                except Exception as exc:
                    log.warning(
                        "report review not recorded turn_id=%s error=%s",
                        turn_id,type(exc).__name__,
                    )
                    curated._restore(checkpoint)
                    failed=(
                        "확인한 내용을 기록하지 못했어요. 다시 말씀해 주세요."
                        if turn_language=="ko" else
                        "The answer could not be recorded. Please say it again."
                    )
                    plan=replace(
                        plan,display_text=failed,speech_text=failed,
                        speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                        report_review=None,
                    )
                    session.set_turn_terminal_outcome(turn_id,generation,"blocked")
                else:
                    report_prepared=True
                    await report_state(review_report)
                    if plan.report_review.get("closed"):
                        download=_report_download_sentence(turn_language)
                        plan=replace(
                            plan,
                            display_text=_before_notebook_question(plan.display_text,download),
                            speech_text=_before_notebook_question(plan.speech_text,download),
                        )
            # Lane VT, decision 5: after a long silence, an answer that is
            # not a command is led by where the run stands.
            plan=curated.with_return_summary(plan,silence=silence,now=_wall_clock())
            # Lane VF, decision 8: a figure the source does not have is drawn
            # (or, a photograph asked for, looked up) instead of asked again.
            plan=_picture_fallback(session,curated,plan,transcript,turn_language)
            web_lookup_now=_web_lookup_wanted(session,curated,plan)
            if web_lookup_now:
                plan=_web_lookup_words(curated,plan,transcript,turn_language)
            drawing_now=_drawing_wanted(session,curated,plan)
            if drawing_now:
                plan=_drawing_words(plan,turn_language)
            if getattr(plan,"setting_change",None):
                # Lane CF, decision 1: a setting said aloud is kept for the
                # experimenter's next session and shown on the screen.
                if "speaker_sensitivity" in plan.setting_change:
                    # Lane SP1, decision 1: and takes effect on this session's gate now.
                    session.set_speaker_sensitivity(str(plan.setting_change["speaker_sensitivity"]))
                try:
                    saved=await asyncio.to_thread(
                        _save_experimenter_settings,dict(plan.setting_change),"voice")
                except Exception as exc:  # noqa: BLE001 - this session keeps it
                    log.warning("experimenter setting not kept error=%s",type(exc).__name__)
                    saved={**EXPERIMENTER_SETTING_DEFAULTS,**curated.experimenter_settings()}
                    unsaved=" 설정을 저장하지 못해 이번 실험에만 적용돼요."
                    plan=replace(
                        plan,display_text=f"{plan.display_text}{unsaved}",
                        speech_text=f"{plan.speech_text}{unsaved}",
                    )
                await current_text("experimenter.settings",turn_id=turn_id,settings=saved)
            if getattr(plan,"display_mode",None) in {"far","normal"}:
                # HOOD1: this is a configuration-fenced instruction to the
                # browser, not durable experimenter or workflow state.
                await current_text(
                    "screen.display_mode",turn_id=turn_id,mode=plan.display_mode)
            if (
                plan.action is CuratedProtocolAction.RECORD_CORRECTION
                and plan.record_fix is not None
            ):
                # Lane N, decision 2: a confirmed correction is appended to
                # every record that holds the note -- the experiment report
                # and the experiment timeline -- and said done only after
                # both took it. Neither record is changed in place.
                fix_report=None
                fix_state=None
                try:
                    if (
                        session.experiment_report_store is None
                        and session.experiment_state_version is None
                    ):
                        raise LookupError("no experiment record is open")
                    if session.experiment_report_store is not None:
                        _open_experiment_report(session,curated)
                    fix_state=await asyncio.to_thread(
                        _record_workspace_record_fix,
                        session,plan,turn_id=turn_id,generation=generation,
                    )
                    if session.experiment_report_store is not None:
                        fix_report=await _voice_report_write(
                            _record_experiment_report_plan,
                            session,curated,plan,
                            turn_id=turn_id,generation=generation,
                            pre_transition_index=pre_transition_index,
                        )
                except Exception as exc:
                    log.warning(
                        "record correction failed turn_id=%s error=%s",
                        turn_id,type(exc).__name__,
                    )
                    curated._restore(checkpoint)
                    failed=(
                        "방금 기록을 고치지 못했어요. 기록은 그대로예요."
                        if turn_language=="ko" else
                        "The last note could not be corrected; the record is unchanged."
                    )
                    plan=replace(
                        plan,display_text=failed,speech_text=failed,
                        speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                        record_fix=None,
                    )
                    session.set_turn_terminal_outcome(turn_id,generation,"blocked")
                else:
                    report_prepared=fix_report is not None
                    if fix_report is not None:
                        await report_state(fix_report)
                    if fix_state is not None:
                        await current_text(
                            "experiment.session.state",turn_id=turn_id,
                            state=fix_state)
                    plan=_record_fix_readback(plan,turn_language)
            if (
                session.experiment_report_store is not None
                and not report_prepared
                and _reportable(plan)
                and not (
                    session.experiment_state_version is not None
                    and plan.state_changed
                )
            ):
                try:
                    report_worker=asyncio.create_task(_voice_report_write(
                        _record_experiment_report_plan,
                        session,curated,plan,
                        turn_id=turn_id,generation=generation,
                        pre_transition_index=pre_transition_index,
                    ))
                    try:
                        report=await asyncio.shield(report_worker)
                    except asyncio.CancelledError:
                        while not report_worker.done():
                            try:
                                await asyncio.shield(report_worker)
                            except asyncio.CancelledError:
                                continue
                        report_worker.result()
                        if plan.state_changed and session.experiment_state_version is None:
                            workflow_mutation_committed=True
                        raise
                    report_prepared=True
                    if (
                        plan.state_changed
                        and session.experiment_state_version is None
                    ):
                        workflow_mutation_committed=True
                except Exception:
                    if plan.state_changed:
                        _record_workspace_metric(
                            category="workflow",metric_name="mutation_failure",
                            dimensions={
                                "status":"rolled_back",
                                "reason_code":"report_persistence_failed",
                                "event_kind":plan.action.value,
                            },
                        )
                        curated._restore(checkpoint)
                        failed=(
                            (
                                "실험 기록을 저장하지 못해 단계 완료와 이동을 확정하지 않았습니다. 현재 단계를 유지합니다."
                                if plan.action is CuratedProtocolAction.NEXT else
                                "실험 기록을 저장하지 못해 워크플로 상태 변경을 확정하지 않았습니다. 이전 상태를 유지합니다."
                            )
                            if turn_language=="ko" else
                            (
                                "The experiment record could not be saved, so completion and transition were not committed. The current step is unchanged."
                                if plan.action is CuratedProtocolAction.NEXT else
                                "The experiment record could not be saved, so the workflow change was not committed. The previous state is unchanged."
                            )
                        )
                        plan=replace(
                            plan,display_text=failed,speech_text=failed,
                            speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                            state_changed=False,step_label=(
                                curated.fixture.steps[curated.current_index].source_label),
                            primary_text=None,source_texts=(),source_pages=(),
                            evidence_ids=(),translation_status="not_applicable",
                        )
                        session.set_turn_terminal_outcome(
                            turn_id,generation,"blocked")
                    elif (
                        plan.action is CuratedProtocolAction.NEXT
                        and plan.reported_observation
                    ):
                        failed=(
                            "관찰 결과를 실험 기록에 저장하지 못했습니다. 현재 단계는 그대로 유지합니다."
                            if turn_language=="ko" else
                            "The observation could not be saved to the experiment record. The current step remains unchanged."
                        )
                        plan=replace(
                            plan,display_text=failed,speech_text=failed,
                            speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                        )
                        session.set_turn_terminal_outcome(
                            turn_id,generation,"blocked")
                    log.warning(
                        "experiment report update failed turn_id=%s",turn_id)
                    await current_text(
                        "experiment.report.error",turn_id=turn_id,
                        code="report_persistence_failed")
                else:
                    plan=_acknowledge_report_persistence(
                        plan,turn_language,review=_take_review_intro(session),
                        question=_notebook_end_question(curated,plan,turn_language))
                    await report_state(report)
            workspace_observation_requested=bool(
                plan.reported_observation or plan.reported_anomaly
            )
            workspace_observation_required=bool(
                (
                    plan.action is CuratedProtocolAction.RECORD_OBSERVATION
                    and plan.reported_observation
                )
                # An endpoint observation that released a repeat step's gate
                # is the one report this system moves the workflow on, so it
                # is the one report that may not go unrecorded. Persisting it
                # was previously attempted and skipped in silence whenever no
                # experiment-session record was open -- the gate opened, the
                # step advanced, and nothing was written down. Required here,
                # the missing record blocks the turn instead, and the branch
                # below rolls the advance back.
                or (
                    plan.state_changed
                    and plan.reported_observation
                    and plan.observation_predicate=="positive"
                    and bool(curated.endpoint_observations())
                )
            )
            if workspace_observation_requested:
                if session.experiment_state_version is None:
                    if workspace_observation_required:
                        # Said state_changed=False, so make it false. The
                        # gate-releasing case advances a step before this
                        # point, and a plan that reports no change over a
                        # session that moved is the one outcome worse than
                        # either. The rollback also drops the endpoint
                        # observation, so the retry meets the gate again.
                        if plan.state_changed:
                            curated._restore(checkpoint)
                        failed=(
                            "실험 세션 기록이 활성화되지 않아 관찰 내용을 저장하지 못했습니다. 프로토콜 상태는 변경하지 않았습니다."
                            if turn_language=="ko" else
                            "Experiment-session recording is unavailable, so the observation was not saved. The protocol state did not change."
                        )
                        plan=replace(
                            plan,display_text=failed,speech_text=failed,
                            speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                            state_changed=False,
                        )
                        session.set_turn_terminal_outcome(
                            turn_id,generation,"blocked")
                else:
                    try:
                        observation_state=await asyncio.to_thread(
                            _record_workspace_observation,
                            session,curated,plan,
                            turn_id=turn_id,generation=generation,
                            pre_transition_index=pre_transition_index,
                        )
                    except Exception as exc:
                        _record_workspace_metric(
                            category="workflow",metric_name="mutation_failure",
                            dimensions={
                                "status":"rolled_back",
                                "reason_code":str(
                                    getattr(exc,"code","workspace_error"))[:100],
                                "event_kind":"observation_recorded",
                            },
                        )
                        if plan.state_changed:
                            curated._restore(checkpoint)
                        failed=(
                            "관찰 내용을 실험 세션 타임라인에 저장하지 못했습니다. 완료나 단계 이동은 확정하지 않았습니다."
                            if turn_language=="ko" else
                            "The observation could not be saved to the experiment-session timeline. Completion or step movement was not committed."
                        )
                        plan=replace(
                            plan,display_text=failed,speech_text=failed,
                            speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                            state_changed=False,
                        )
                        session.set_turn_terminal_outcome(
                            turn_id,generation,"blocked")
                        log.warning(
                            "experiment observation update failed turn_id=%s error=%s",
                            turn_id,type(exc).__name__,
                        )
                        await current_text(
                            "experiment.session.error",turn_id=turn_id,
                            code=getattr(exc,"code","workspace_error"))
                    else:
                        if observation_state is not None:
                            await current_text(
                                "experiment.session.state",turn_id=turn_id,
                                state=observation_state)
                        if (
                            plan.action is CuratedProtocolAction.RECORD_OBSERVATION
                            and plan.note_record
                            and turn_language=="ko"
                        ):
                            # Lane N, decision 1: read back, not asked about.
                            plan=_note_readback(
                                plan,turn_language,curated.confirm_mode)
                        elif plan.action is CuratedProtocolAction.RECORD_OBSERVATION:
                            acknowledgment=(
                                f"말씀한 관찰 내용을 현재 {plan.step_label}단계 실험 타임라인에 기록했습니다. 프로토콜 상태는 변경하지 않았습니다."
                                if turn_language=="ko" else
                                f"I recorded the observation in the experiment timeline for Step {plan.step_label}. The protocol state did not change."
                            )
                            plan=replace(
                                plan,display_text=acknowledgment,
                                speech_text=acknowledgment,
                                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                            )
                        elif (
                            plan.action is CuratedProtocolAction.REPORT_ANOMALY
                            and not report_prepared
                        ):
                            acknowledgment=(
                                f"말씀한 이상 사항을 현재 {plan.step_label}단계 실험 타임라인에 기록했습니다. 프로토콜 상태는 변경하지 않았습니다."
                                if turn_language=="ko" else
                                f"I recorded the reported issue in the experiment timeline for Step {plan.step_label}. The protocol state did not change."
                            )
                            plan=replace(
                                plan,display_text=acknowledgment,
                                speech_text=acknowledgment,
                                speech_mode=CuratedProtocolSpeechMode.CONTROL,
                            )
                        elif (
                            plan.action is CuratedProtocolAction.NEXT
                            and session.experiment_report_store is None
                        ):
                            acknowledgment=(
                                "말씀한 관찰 결과를 실험 세션 타임라인에 기록했습니다."
                                if turn_language=="ko" else
                                "I recorded the reported observation in the experiment-session timeline."
                            )
                            plan=replace(
                                plan,
                                display_text=f"{acknowledgment}\n\n{plan.display_text}",
                                speech_text=f"{acknowledgment} {plan.speech_text}",
                            )
            if (
                session.experiment_state_version is not None
                and plan.state_changed
            ):
                try:
                    mutation_worker=asyncio.create_task(asyncio.to_thread(
                        _record_workspace_experiment_progress,
                        session,curated,plan,
                        turn_id=turn_id,generation=generation,
                        pre_transition_index=pre_transition_index,
                    ))
                    try:
                        experiment_state=await asyncio.shield(mutation_worker)
                    except asyncio.CancelledError:
                        # The thread can already have committed. Join it before
                        # any rollback or a bench control touches this checkpoint.
                        while not mutation_worker.done():
                            try:
                                await asyncio.shield(mutation_worker)
                            except asyncio.CancelledError:
                                continue
                        experiment_state=mutation_worker.result()
                        if experiment_state is not None:
                            workflow_mutation_committed=True
                            if (session.experiment_report_store is not None
                                    and _reportable(plan) and not report_prepared):
                                await _voice_report_write(
                                    _record_experiment_report_plan,session,curated,plan,
                                    turn_id=turn_id,generation=generation,
                                    pre_transition_index=pre_transition_index)
                        raise
                    if experiment_state is None:
                        raise WorkspaceError(
                            "Experiment progress persistence is unavailable."
                        )
                except Exception as exc:
                    _record_workspace_metric(
                        category="workflow",metric_name="mutation_failure",
                        dimensions={
                            "status":"rolled_back",
                            "reason_code":str(
                                getattr(exc,"code","workspace_error"))[:100],
                            "event_kind":plan.action.value,
                        },
                    )
                    log.warning(
                        "experiment session update failed turn_id=%s error=%s",
                        turn_id,type(exc).__name__,
                    )
                    if not workflow_mutation_committed:
                        curated._restore(checkpoint)
                        failed=(
                            "실험 세션을 저장하지 못해 상태 변경을 확정하지 않았습니다. 현재 단계를 유지합니다."
                            if turn_language=="ko" else
                            "The experiment session could not be saved, so the workflow change was not committed. The current step is unchanged."
                        )
                        plan=replace(
                            plan,display_text=failed,speech_text=failed,
                            speech_mode=CuratedProtocolSpeechMode.BLOCKED,
                            state_changed=False,
                        )
                        session.set_turn_terminal_outcome(
                            turn_id,generation,"blocked")
                    await current_text(
                        "experiment.session.error",turn_id=turn_id,
                        code=getattr(exc,"code","workspace_error"))
                else:
                    if experiment_state is not None:
                        workflow_mutation_committed=True
                        await current_text(
                            "experiment.session.state",turn_id=turn_id,
                            state=experiment_state)
            if (
                session.experiment_report_store is not None
                and session.experiment_state_version is not None
                and plan.state_changed
                and _reportable(plan)
                and workflow_mutation_committed
                and not report_prepared
            ):
                try:
                    report=await _voice_report_write(
                        _record_experiment_report_plan,
                        session,curated,plan,
                        turn_id=turn_id,generation=generation,
                        pre_transition_index=pre_transition_index,
                    )
                except Exception:
                    if _experiment_end_kind(plan) is not None:
                        # Decision 2: the experiment did end (the session
                        # record holds it); only the report was not saved.
                        end_report_failed=True
                        plan=_with_experiment_end_sentence(
                            plan,
                            "실험은 끝났지만 기록 저장에 실패했어요. 화면에서 다시 시도해 주세요."
                            if turn_language=="ko" else
                            "The experiment has ended, but saving its record failed. Please try again on the screen.",
                        )
                    else:
                        warning=(
                            "실험 세션의 상태 변경은 저장되었지만 보조 실험 보고서를 갱신하지 못했습니다. 현재 단계는 실험 세션 타임라인에서 확인해 주세요."
                            if turn_language=="ko" else
                            "The experiment-session change was saved, but the auxiliary experiment report could not be updated. Verify the current step in the experiment timeline."
                        )
                        plan=replace(
                            plan,
                            display_text=f"{warning}\n\n{plan.display_text}",
                            speech_text=f"{warning} {plan.speech_text}",
                        )
                    log.warning(
                        "experiment report update failed after workspace commit turn_id=%s",
                        turn_id,
                    )
                    await current_text(
                        "experiment.report.error",turn_id=turn_id,
                        code="report_persistence_failed")
                else:
                    report_prepared=True
                    plan=_acknowledge_report_persistence(
                        plan,turn_language,review=_take_review_intro(session),
                        question=_notebook_end_question(curated,plan,turn_language))
                    await report_state(report)
            # Durable workspace persistence is the mutation gate; report
            # persistence is the reporting-acknowledgement gate. Re-read the
            # possibly replaced plan only after both so neither display nor
            # TTS can use pre-persistence success language.
            # Lane VF, decision 9: whatever path wrote the sentences, no
            # internal identifier reaches the screen or the voice.
            plan=plan_without_internal_names(plan,turn_language)
            # Decision of 2026-10-13: with no institution safety document, a
            # safety answer carries one line that says so.
            plan=_with_safety_scope_notice(
                plan,getattr(session.tool_context,"usage_scope",None),transcript)
            # Lane KO2, decision 5: every step label said and shown once.
            plan=curated.single_step_labels(plan)
            display_text=plan.display_text
            speech_text=plan.speech_text
            speech_language="en" if plan.intent_kind=="source_read_aloud" else turn_language
            speech_policy=getattr(plan,"speech_policy","speak")
            if not isinstance(display_text,str) or not display_text.strip():
                raise RuntimeError("curated protocol produced no display text")
            if speech_policy=="speak":
                if not isinstance(speech_text,str) or not speech_text.strip():
                    raise RuntimeError("curated protocol produced no speech text")
                timings["first_tts_request_ms"]=round((clock()-endpoint)*1000)
                await progress("synthesizing",route="curated_protocol")
                # Lane VOX, decision 2: the first sentence is made and plays
                # at once; the later ones are made while it plays (below).
                # The whole is remembered as said, for the echo check.
                spoken_sentences=speech_sentences(said(speech_text))
                # Decision 3: a reading of the protocol may come from memory.
                speech_kept=_speech_kept(plan)
                with sentence_audio_kept(speech_kept):
                    pcm=await asyncio.to_thread(
                        synthesize,spoken_sentences[0],speech_language)
                frames=frame_complete_audio(pcm)
                if filler is not None:await filler.primary_ready()
                if not frames or not session.start_playback(turn_id):
                    raise RuntimeError("curated protocol produced no playable audio")
            else:
                frames=[]
                spoken_sentences=()
                speech_kept=False
                if filler is not None:
                    await filler.cancel()
        except asyncio.CancelledError:
            if brain_run is not None:
                brain_run.cancel()
            if not workflow_mutation_committed:
                curated._restore(checkpoint)
            if router_outcome is not None and router_before is not None:
                _record_router_history(
                    session,curated,router_outcome,router_before,
                    user=transcript,plan=None,turn_id=turn_id,
                    generation=generation,interrupted=True,
                )
            raise
        except BaseException:
            if brain_run is not None:
                brain_run.cancel()
            if not workflow_mutation_committed:
                curated._restore(checkpoint)
            raise
        timings["first_audio_ms"]=round((clock()-endpoint)*1000)
        if speech_policy=="speak":
            await progress(
                "playing",route="curated_protocol",
                timings_ms={"time_to_playable_audio":timings["first_audio_ms"]})
        await current_text(
            "protocol.fixture.state",turn_id=turn_id,
            configuration_id=session.accepted_configuration_id,
            state=curated.state(spoken_summary=plan.spoken_summary),
            screen=curated_screen_fields(curated),
            action=plan.action.value)
        _queue_source_figures(session,curated,current_text)
        # Lane KO2, decision 3: the screen's step sentences in Korean.
        _queue_screen_korean(session,curated,current_text)
        ended=_experiment_ended_event(
            session,curated,plan,
            report=(
                "saved" if report_prepared
                else "failed" if end_report_failed
                else "not_recorded"
            ),
        )
        if ended is not None:
            await current_text("experiment.ended",turn_id=turn_id,**ended)
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
            CuratedProtocolAction.NEXT_INFORMATION:"next_step_preview",
            CuratedProtocolAction.COMPLETION_CRITERIA:"completion_criteria_read",
            CuratedProtocolAction.OPERATIONAL_DEVIATION:"operational_deviation_refused",
            CuratedProtocolAction.NEXT:"next_step_transition",
            CuratedProtocolAction.QUESTION:(
                "step_learning_read"
                if plan.intent_kind in {"current_step_learning","current_step_warning"}
                else "experiment_history_read"
                if plan.intent_kind in {"previous_experiment_resume","experiment_history"}
                else "bounded_uncertainty_response"
                if plan.intent_kind=="bounded_outcome_uncertainty"
                else
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
            CuratedProtocolAction.RECORD_OBSERVATION:"experiment_observation_recorded",
            CuratedProtocolAction.REPORT_ANOMALY:"experiment_anomaly_recorded",
            CuratedProtocolAction.SHOW_REPORT:"experiment_report_view",
            CuratedProtocolAction.PROTOCOL_QUERY:(
                "protocol_audit_read"
                if plan.intent_kind=="protocol_audit"
                else "protocol_structure_read"
            ),
            CuratedProtocolAction.CLARIFY_COMPLETION:(
                "learning_next_preview_confirmation"
                if plan.intent_kind=="learning_and_next_preview"
                else "completion_confirmation_required"
            ),
            CuratedProtocolAction.DECLINE_COMPLETION:"completion_confirmation_declined",
            CuratedProtocolAction.CLARIFY_REFERENCE:"reference_clarification_required",
            CuratedProtocolAction.CLARIFY_PARAMETER:"parameter_clarification_required",
            CuratedProtocolAction.OFF_TOPIC:"scope_reminder",
            CuratedProtocolAction.UNSUPPORTED:"unsupported_question",
            CuratedProtocolAction.STOP:"protocol_stop",
            CuratedProtocolAction.INACTIVE:"inactive_session_guard",
            CuratedProtocolAction.AGENT_META:"agent_meta_information",
            CuratedProtocolAction.DISPLAY_MODE:"display_mode_changed",
            CuratedProtocolAction.PAUSE:"workflow_paused",
            CuratedProtocolAction.RESUME:"workflow_resumed",
            CuratedProtocolAction.START_TIMER:"step_timer_started",
            CuratedProtocolAction.TIMER_STATUS:"step_timer_status_read",
            # Lane HEAR2, decision 3: the timer's voice commands.
            CuratedProtocolAction.TIMER_PAUSE:"step_timer_paused",
            CuratedProtocolAction.TIMER_RESUME:"step_timer_resumed",
            CuratedProtocolAction.TIMER_CANCEL:"step_timer_cancelled",
            CuratedProtocolAction.TIMER_RESTART:"step_timer_restarted",
            CuratedProtocolAction.PREVIEW_STEP:"step_preview_read",
            CuratedProtocolAction.STEP_RANGE:"step_range_read",
            CuratedProtocolAction.LAB_DOMAIN_QA:"lab_domain_qa_read",
            CuratedProtocolAction.REPORT_HANDOFF:"report_handoff_requested",
            CuratedProtocolAction.RECORD_CORRECTION:"experiment_record_corrected",
            CuratedProtocolAction.REPORT_REVIEW:"report_value_review",
            CuratedProtocolAction.PROBLEM_HELP:"problem_help_read",
            # Lane EASY, decision 2: a value of today's run changed or put back.
            CuratedProtocolAction.CHANGE_VALUE:"run_value_changed",
        }
        operation=(
            "completion_and_next_transition"
            if plan.action is CuratedProtocolAction.NEXT
            and plan.reported_completion
            # Lane CB: a condition's answer or a repeat's count recorded in
            # place is not a declined completion.
            else "branch_repeat_recorded"
            if plan.action is CuratedProtocolAction.DECLINE_COMPLETION
            and (getattr(plan,"step_record",None) or {}).get("kind")
            in _RECORDED_STEP_RECORD_KINDS
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
            transcript_corrections=[
                {"from":observed,"to":canonical}
                for observed,canonical in plan.transcript_corrections
            ],
            requested_entities=list(plan.requested_entities),
            question_dimensions=list(plan.question_dimensions),
            source_plan_scopes=list(plan.source_plan_scopes),
            unresolved_dimensions=list(plan.unresolved_dimensions),
            display_document=getattr(plan, "display_document", None),
            translation_missing=bool(getattr(plan,"translation_missing",False)),
            development_note=None)
        await current_text(
            "state.changed",state=session.state.value,turn_id=turn_id)
        def record_turn_metric()->None:
            _record_workspace_metric(
                category="workflow",metric_name="turn",
                metric_value=float(timings.get("total_ms",0)),
                dimensions={
                    "event_kind":plan.action.value,
                    "status":"mutated" if plan.state_changed else "read_only",
                    "route":"curated_protocol",
                    "answer_origin":plan.answer_origin,
                },
            )
        if speech_policy=="speak":
            try:
                spoken=await _say_answer(
                    session,sender,turn_id=turn_id,generation=generation,
                    first_frames=frames,later=spoken_sentences[1:],
                    language=speech_language,clock=clock,keep=speech_kept)
            except asyncio.CancelledError:
                # Lane VOX: a barge-in while the later sentences are made.
                # The turn did what it did; its history, pilot row and metric
                # are kept as when the whole answer was made before it was
                # heard and a barge-in came only after the turn had ended.
                if brain_run is not None:
                    brain_run.cancel()
                if router_outcome is not None and router_before is not None:
                    _record_router_history(
                        session,curated,router_outcome,router_before,
                        user=transcript,plan=plan,turn_id=turn_id,
                        generation=generation,said=speech_text,
                    )
                timings["total_ms"]=round((clock()-endpoint)*1000)
                _pilot_turn_event(session,turn_id,"turn.done",{"timings_ms":dict(timings)})
                record_turn_metric()
                raise
            if spoken.failed:
                # The same signals as a turn whose audio could not be made,
                # but the turn still ends below: the sentences sent play and
                # the page's playback.ended ends the turn, so the session
                # never listens to its own voice as a new turn.
                _record_workspace_metric(
                    category="voice",metric_name="command_failure",
                    dimensions={"status":"failed","reason_code":"turn_processing_failed"},
                )
                await progress("error",route="curated_protocol")
                await current_text(
                    "error",turn_id=turn_id,message="voice turn processing failed")
        else:
            spoken=_SpokenAnswer(0,0,clock())
        if research_context is not None:
            # Lane VOX: a follow-up's segment comes after every sentence of
            # the answer, and the audio.complete it holds counts them.
            research_context["answer_segments"]=spoken.segments
        await current_text(
            "reply.complete",turn_id=turn_id,
            **_reply_complete_fields(
                plan,speech_text=speech_text,speech_policy=speech_policy))
        web_speak_by:float|None=None
        if speech_policy=="speak" and problem_causes is not None:
            # Lane HELP, decision 2: ③ and the closing it holds are said after
            # the answer; ④ alone when ③ is not ready while the answer plays.
            session.held_audio_complete.add((turn_id,generation))
            answer_ends=max(clock(),spoken.ends_at)+0.3
            problem_causes["speak_by"]=answer_ends
            async def release_after_causes(deadline:float)->None:
                await asyncio.sleep(max(0.0,deadline-clock()))
                await _release_held_audio(
                    sender,session,turn_id,generation,spoken.segments)
            session.track_visual_task(asyncio.create_task(
                release_after_causes(answer_ends+PROBLEM_CAUSES_GRACE_SECONDS)))
        elif speech_policy=="speak" and (
            (research_context is not None and research_context.get("outside_pdf") is not None)
            or web_lookup_now
        ):
            # Lane R6, decision 6: the answer plays now; an outside-PDF
            # explanation ready before it ends is said right after it.
            # Otherwise audio.complete goes when the answer's audio ends.
            # Lane WV, decision 2: the web's one or two sentences the same
            # way, within the lookup's own time limit.
            session.held_audio_complete.add((turn_id,generation))
            answer_ends=max(clock(),spoken.ends_at)+0.3
            if research_context is not None and research_context.get("outside_pdf") is not None:
                research_context["speak_by"]=answer_ends
            if web_lookup_now:
                web_speak_by=answer_ends+min(
                    15.0,session.web_explanation_settings.timeout_seconds)
            async def release_when_answer_ends(deadline:float)->None:
                await asyncio.sleep(max(0.0,deadline-clock()))
                await _release_held_audio(
                    sender,session,turn_id,generation,spoken.segments)
            session.track_visual_task(asyncio.create_task(
                release_when_answer_ends(web_speak_by or answer_ends)))
        else:
            await current_text(
                "audio.complete",turn_id=turn_id,segment_count=spoken.segments)
        if web_lookup_now:
            session.track_visual_task(asyncio.create_task(_run_web_lookup(
                session=session,sender=sender,turn_id=turn_id,generation=generation,
                curated=curated,plan=plan,transcript=transcript,language=turn_language,
                speak_by=web_speak_by,clock=clock,answer_segments=spoken.segments)))
        if drawing_now:
            session.track_visual_task(asyncio.create_task(_run_drawing(
                session=session,sender=sender,turn_id=turn_id,generation=generation,
                curated=curated,language=turn_language)))
        if (
            router_outcome is not None and router_before is not None
            and session.is_current(turn_id,generation)
        ):
            _record_router_history(
                session,curated,router_outcome,router_before,
                user=transcript,plan=plan,turn_id=turn_id,
                generation=generation,
                said=speech_text if speech_policy=="speak" else display_text,
            )
        if plan.action is CuratedProtocolAction.AUDIO_RECOVERY:
            await current_text(
                "audio.replay.request",turn_id=turn_id,
                replay_count=1,state_mutation=False)
        elif speech_policy=="speak":
            await current_text(
                "audio.replay.available",turn_id=turn_id,
                replay_count=1,state_mutation=False)
        timings["total_ms"]=round((clock()-endpoint)*1000)
        await current_text(
            "turn.done",turn_id=turn_id,timings_ms=timings,
            segment_count=spoken.segments,input_frames=input_frames,
            output_frames=spoken.frames,tools_used=curated_tools_used,
            route="curated_protocol",result_kind=plan.action.value,
            fact_id=plan.fact_id,speech_mode=plan.speech_mode.value,
            critical_warning_present=plan.critical_warning_text is not None,
            intent_kind=plan.intent_kind,
            reported_completion=plan.reported_completion,
            requested_transition=plan.requested_transition,
            brains_enabled=(
                list(brain_activation.roles) if brain_activation else []),
            brain_terminals={
                role:{"status":terminal.status,"elapsed_ms":terminal.elapsed_ms}
                for role,terminal in brain_terminals.items()
            })
        if speech_policy!="speak":
            # A silent turn has no playback to end it, so it ends here, once
            # its reply and turn.done are out. Ended before them, each was
            # dropped as no longer current and the page waited on "절차 확인
            # 중…" with no answer (2026-10-01 voice test, the paused notice).
            # Its card reaches the end state here too: with no playback there
            # is no playback.ended to carry it there.
            await progress(session.turn_terminal_outcome(turn_id,generation))
            session.complete_without_playback(turn_id)
        record_turn_metric()
        # Lane RA, decision (b): with the router first the roles ran beside
        # the router; their outputs go where they went before.
        source_output=(
            router_outcome.brain_outputs.get("source") if router_outcome is not None else None)
        visual_output=(
            router_outcome.brain_outputs.get("visual") if router_outcome is not None else None)
        if brain_run is not None and brain_snapshot is not None:
            late_answer_output=None
            roles_to_finish=[]
            if "answer" not in brain_terminals:
                roles_to_finish.append(("answer",AnswerBrainOutput))
            roles_to_finish.extend((
                ("source",SourceBrainOutput),("visual",VisualBrainOutput),
            ))
            for role,expected in roles_to_finish:
                terminal=await brain_run.terminal(role)
                if terminal is None:
                    continue
                brain_terminals[role]=terminal
                timings[f"{role}_brain_ms"]=terminal.elapsed_ms
                if not session.is_current(turn_id,generation):
                    brain_run.cancel()
                    return
                current_revision=curated.state()["revision"]
                current_step=curated.fixture.steps[curated.current_index]
                owned=(
                    current_revision==brain_snapshot.workflow_revision
                    and current_step.step_id==brain_snapshot.step_id
                    and session.accepted_configuration_id
                    ==brain_snapshot.configuration_id
                )
                await current_text(
                    "brain.state",turn_id=turn_id,role=role,
                    status=(terminal.status if owned else "stale_rejected"),
                    elapsed_ms=terminal.elapsed_ms,
                )
                if terminal.status=="success" and owned and isinstance(terminal.output,expected):
                    if role=="answer":
                        admitted_late=_claim_admitted_answer(
                            terminal.output,plan)
                        if admitted_late is None:
                            await current_text(
                                "brain.output.rejected",turn_id=turn_id,
                                role="answer",
                                reason="unresolved_claim_dimensions",
                                dimensions=list(plan.unresolved_dimensions),
                                unresolved_claim_ids=list(
                                    plan.unresolved_claim_ids),
                            )
                        else:
                            late_answer_output=admitted_late
                    elif role=="source": source_output=terminal.output
                    else: visual_output=terminal.output
            if isinstance(late_answer_output,AnswerBrainOutput):
                # Written-only same-Turn enrichment. The browser keeps the
                # already spoken primary answer and renders this in details.
                await current_text(
                    "brain.answer.enrichment",turn_id=turn_id,
                    status="ready",display_answer=late_answer_output.display_answer,
                    evidence_ids=list(late_answer_output.evidence_ids),
                    limitations=list(late_answer_output.limitations),
                )
            await current_text(
                "brain.state",turn_id=turn_id,status="complete",
                roles=list(brain_activation.roles),
            )
        # The primary display/audio contract is complete. Explicit visual work
        # can now run alongside the independent evidence supplement instead of
        # waiting behind it; both paths retain the same Turn/generation fence.
        if plan.action is CuratedProtocolAction.VISUAL_REQUEST:
            existing_visual=curated.fixture.visual_for_step(curated.current_index)
            visual_kind=plan.visual_kind
            if isinstance(visual_output,VisualBrainOutput):
                visual_kind={
                    "authoritative_external_reference":"web_photo",
                    "generated_instructional_illustration":"instructional_illustration",
                    "original_source_visual":"original_source",
                    "approved_visual":"original_source",
                    "no_visual":"no_visual",
                }[visual_output.preferred_class]
            if plan.visual_kind in LANE_WV_PICTURE_KINDS:
                # Lane WV: the step's own figures are on the screen (the
                # fixture state carried them, or they are sent once cut); the
                # web (decision 2) and the drawing (decision 3) run as their
                # own tasks and say so themselves, so nothing is "failed" here.
                _queue_source_figures(session,curated,current_text,force=True)
            elif existing_visual is None:
                # Lane DI (2026-10-08): the xAI web-image search and image
                # generation are gone (lane WV builds their successor). The
                # PDF's own visual is the only one shown, so a request for
                # any other kind is answered once as unavailable.
                fixture=curated.fixture;step=fixture.steps[curated.current_index]
                unavailable_kind="no-visual" if visual_kind=="no_visual" else "visual-unavailable"
                await current_text(
                    "protocol.visual.state",turn_id=turn_id,
                    protocol_id=fixture.protocol_id,step_id=step.step_id,
                    source_document_hash=fixture.source_pdf_sha256,
                    visual_job_id=hashlib.sha256(
                        f"{unavailable_kind}\x1f{fixture.source_pdf_sha256}\x1f{step.step_id}".encode()
                    ).hexdigest(),status="visual_failed",
                    visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                    fallback="planner_no_visual" if visual_kind=="no_visual" else "feature_disabled")
        if problem_causes is not None and speech_policy=="speak":
            # As the research follow-up is: its own task, which a barge-in
            # or a new turn's cancellation of this one does not cut short.
            causes_task=asyncio.create_task(_queue_problem_causes(
                session=session,sender=sender,turn_id=turn_id,generation=generation,
                clock=clock,causes=problem_causes,language=turn_language,
                answer_segments=spoken.segments))
            session.track_visual_task(causes_task)
            try:
                await asyncio.shield(causes_task)
            except asyncio.CancelledError:
                pass
        elif problem_causes is not None:
            session.finish_research(turn_id,generation)
        if research_context is not None:
            await _queue_curated_research(
                session=session,
                sender=sender,
                turn_id=turn_id,
                generation=generation,
                endpoint=endpoint,
                clock=clock,
                curated=curated,
                context=research_context,
                turn_language=turn_language,
                pre_transition_index=pre_transition_index,
                plan=plan,
                source_output=source_output,
            )
        if session.is_current(turn_id,generation):
            session.history.commit([
                {"role":"user","content":transcript},
                {"role":"assistant","content":display_text},
            ])
        return
    # Lane RA, decision (g): a session without a protocol has no general
    # brain any more (session.start refuses cascade without a protocol, so
    # this is reached only by a session built without one). Answered by
    # rule, nothing called.
    _record_workspace_metric(
        category="agent",metric_name="turn_route",
        dimensions={
            "intent":request_arbitration.intent.value,
            "route":"no_protocol","answer_origin":"server",
            "reason_code":request_arbitration.reason_code or "none",
            "status":"blocked",
        },
    )
    await current_text(
        "turn.route_decision",turn_id=turn_id,
        normalized_text=request_arbitration.normalized_text,
        intent=request_arbitration.intent.value,
        confidence=request_arbitration.confidence,
        reason_code=request_arbitration.reason_code,
        dimensions=list(request_arbitration.dimensions),
        runtime_router="no_protocol",
        action="unsupported",
        state_mutation=False,
        answer_origin="server",
        fallback_reason="no_protocol_session",
    )
    await finish_blocked_voice(
        (
            "이 세션에는 프로토콜이 없어 음성 안내를 할 수 없어요. 화면에서 프로토콜을 골라 시작해 주세요."
            if turn_language=="ko" else
            "This session has no protocol, so there is no voice guidance. Choose a protocol on the screen to start."
        ),
        "no_protocol",
    )


async def run_turn_safely(
    websocket,session,source_pcm,turn_id,input_frames,voiced_frames=0,
    retained_prefix_frames=0,
    *,accepted_transcription:Transcription|None=None,
    accepted_stt_ms:int|None=None,
    accepted_stt_context:CascadeTranscriptionContext|None=None,
    voiced_level_db:float|None=None,
):
    generation=session.turn_generations.get(turn_id,session.generation)
    _SPEAKING_SESSION.set(session)
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
    async def filler_tone(target_turn:int,target_generation:int):
        # The browser makes the tone itself; no audio frames follow.
        await sender.text(
            "filler.tone",
            configuration_id=session.accepted_configuration_id,
            turn_id=target_turn,generation=target_generation)
    normal_status_speech=cascade_filler_status_speech_enabled()
    def filler_activity()->str|None:
        if (turn_id,generation) in session.grounded_answer_turns:
            return "grounding_answer"
        if not normal_status_speech:
            return None
        # The same progress state the Turn card shows; nothing else is claimed.
        progress=session.turn_progress.get((turn_id,generation))
        return progress.state if progress is not None else None
    filler=CascadeFiller(
        turn_id=turn_id,generation=generation,language=language,
        delay_ms=cascade_filler_delay_ms(),
        synthesize=lambda text,selected:asyncio.to_thread(
            synthesize,said(text),selected),
        send_audio=filler_audio,send_event=filler_event,
        send_clear=filler_clear,is_current=session.is_current,
        clock=session.clock,
        mode=cascade_filler_mode(),send_tone=filler_tone,
        # General status speech is off by default. Grounded answers still use
        # their fixed three-second source-check cue.
        status_delay_ms=(
            min(cascade_filler_status_delay_ms(),GROUNDED_ANSWER_STATUS_DELAY_MS)
            if normal_status_speech else GROUNDED_ANSWER_STATUS_DELAY_MS),
        activity=filler_activity,memory=session.filler_memory)
    filler.start()
    try: await run_turn(
        websocket,session,source_pcm,turn_id,input_frames,voiced_frames,
        retained_prefix_frames,
        sender=sender,filler=filler,
        accepted_transcription=accepted_transcription,
        accepted_stt_ms=accepted_stt_ms,
        accepted_stt_context=accepted_stt_context,
        voiced_level_db=voiced_level_db)
    except asyncio.CancelledError:
        log.info("voice turn audio interrupted by barge-in turn_id=%s generation=%s", turn_id, generation)
        session.cascade_failed(turn_id)
        raise
    except WebSocketDisconnect: session.cascade_failed(turn_id)
    except Exception:
        _record_workspace_metric(
            category="voice",metric_name="command_failure",
            dimensions={"status":"failed","reason_code":"turn_processing_failed"},
        )
        await _finish_research_operation(
            sender,session,turn_id,generation,"failed",
            limitation="근거 확인이 오류로 종료되었습니다.",
        )
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
        session.grounded_answer_turns.discard((turn_id,generation))
        await filler.cancel()


async def run_barge_in_stt_failure_turn(
    websocket:WebSocket,
    session:ListenerSession,
    *,
    turn_id:int,
    generation:int,
    input_frames:int,
    voiced_frames:int,
    context:CascadeTranscriptionContext,
    stt_ms:int,
) -> None:
    """Own one accepted interruption whose speech could not be transcribed."""

    if not session.is_current(turn_id,generation):
        return
    sender=LockedSender(websocket)
    language=context.language or session.accepted_language or "ko"
    text={
        "en": (
            "I stopped the previous answer, but I could not transcribe your interruption. "
            "Please say it once more."
        ),
        "vi": (
            "Tôi đã dừng câu trả lời trước nhưng không phiên âm được lời ngắt. Vui lòng nói lại."
        ),
        "ko": (
            "이전 답변은 중단했지만 방금 끼어든 말씀을 정확히 받아쓰지 못했습니다. "
            "한 번만 다시 말씀해 주세요."
        ),
    }.get(language, "방금 끼어든 말씀을 받아쓰지 못했습니다. 다시 말씀해 주세요.")
    for state in ("transcribing","routing","composing"):
        progress=session.advance_turn_progress(
            turn_id,generation,state,route="barge_in_stt_clarification"
        )
        if progress is not None:
            await sender.text("turn.state",**progress)
    await sender.text(
        "transcript.unavailable",
        configuration_id=session.accepted_configuration_id,
        turn_id=turn_id,generation=generation,
        reason="transcription_failed",
        audio_origin=context.audio_origin,
        pending_frame=context.pending_frame,
        stt_ms=max(0,stt_ms),
        voiced_frames=max(0,voiced_frames),
        total_frames=max(0,input_frames),
    )
    await sender.text(
        "reply.delta",configuration_id=session.accepted_configuration_id,
        turn_id=turn_id,generation=generation,segment_index=0,text=text,
    )
    try:
        progress=session.advance_turn_progress(
            turn_id,generation,"synthesizing",
            route="barge_in_stt_clarification",
        )
        if progress is not None:
            await sender.text("turn.state",**progress)
        pcm=await asyncio.to_thread(synthesize,said(text),language)
        frames=frame_complete_audio(pcm)
    except Exception:
        frames=[]
    if frames and session.is_current(turn_id,generation) and session.start_playback(turn_id):
        progress=session.advance_turn_progress(
            turn_id,generation,"playing",
            route="barge_in_stt_clarification",
        )
        if progress is not None:
            await sender.text("turn.state",**progress)
        await sender.segment(turn_id,0,frames,generation)
        segment_count=1
    else:
        segment_count=0
        session.complete_without_playback(turn_id)
    await sender.text(
        "reply.complete",configuration_id=session.accepted_configuration_id,
        turn_id=turn_id,generation=generation,text=text,
    )
    await sender.text(
        "audio.complete",configuration_id=session.accepted_configuration_id,
        turn_id=turn_id,generation=generation,segment_count=segment_count,
    )
    await sender.text(
        "turn.done",configuration_id=session.accepted_configuration_id,
        turn_id=turn_id,generation=generation,
        route="barge_in_stt_clarification",result_kind="clarification",
        segment_count=segment_count,input_frames=input_frames,
        output_frames=(len(frames) if frames else 0),tools_used=[],
        timings_ms={"stt":max(0,stt_ms)},
    )
    # Lane LOG, decision 1: the words were not heard, so they are asked again.
    _pilot_turn_event(session,turn_id,"turn.route_decision",{
        "action":"transcript_unreliable","state_mutation":False})
    _pilot_turn_event(session,turn_id,"turn.done",{"timings_ms":{}})

async def _send_playback_terminal(
    websocket:WebSocket,session:ListenerSession,turn_id:int,*,cooldown_ms:int,
)->None:
    """End a turn whose playback ended: its turn.state, then the cooldown.

    For the browser's playback.ended, and (lane R6, decision 5) for one a
    barge-in candidate held back until it was rejected.
    """

    generation=session.turn_generations.get(turn_id,session.generation)
    playback_completion_ms=session.playback_completion_ms(turn_id)
    progress=session.advance_turn_progress(
        turn_id,generation,
        session.turn_terminal_outcome(turn_id,generation),
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
            turn_id,playback_completion_ms)
        await websocket.send_text(event(
            "playback.completed",pipeline="cascade",
            turn_id=turn_id,
            generation=generation,
            playback_completion_ms=playback_completion_ms))
        RUNTIME_METRICS.observe("playback.completed",{
            "turn_id":turn_id,
            "generation":generation,
            "playback_completion_ms":playback_completion_ms,
        })
    await websocket.send_text(event(
        "state.changed",state=session.state.value,
        turn_id=turn_id,generation=generation,
        cooldown_ms=cooldown_ms))


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
    fields={
        **(progress or {
            "configuration_id":session.accepted_configuration_id,
            "turn_id":interruption.turn_id,
            "generation":interruption.generation,
            "revision":0,
            "state":"cancelled",
        }),
        "superseding_turn_id":interruption.superseding_turn_id,
        "superseding_generation":interruption.superseding_generation,
        "reason":interruption.reason or "confirmed_speech",
    }
    if interruption.latency_ms is not None:
        fields["barge_in_to_silence_ms"]=interruption.latency_ms
        _record_workspace_metric(
            category="voice",metric_name="barge_in_latency_ms",
            metric_value=float(interruption.latency_ms),
            dimensions={"status":"confirmed","event_kind":"barge_in"},
        )
    await websocket.send_text(event("cascade.playback.clear",**fields))
    if progress is not None:
        # Lane R6, decision 5: the interrupted turn's end state, as every
        # other turn's, so no card is left "재생 중".
        await websocket.send_text(event("turn.state",**progress))

# --- Lane NET (2026-10-18): a lost connection, seen at once and put back -----

from voiney_lab.curated_protocol import (  # noqa: E402 - the rule the screen timer follows
    TIMER_LAST_MINUTE_FROM_SECONDS,
    TIMER_LAST_MINUTE_SECONDS,
)

#: Decision 1: the server tells the page it is there this often while a
#: session runs. The page counts the connection lost when nothing at all
#: has come for three heartbeats' time, and sends its own "client.ping" as
#: often.
HEARTBEAT_SECONDS=1.0
#: The sleep the heartbeat waits on, taken at import: a test that replaces
#: asyncio.sleep to hurry another loop must not make this one spin.
_heartbeat_sleep=asyncio.sleep


@dataclass
class _Heartbeat:
    """The page's last "client.ping" as the server received it (decision 1)."""

    ping_seq:int|None=None
    ping_sent_at_ms:float|None=None
    ping_received_ms:int|None=None


def _client_ping(raw:str)->dict[str,Any]|None:
    """A page's "client.ping", read before the control parser (decision 1).

    The page sends one a second; it changes nothing. Its number and the
    page's own clock come back in the next heartbeat, from which the page
    measures how far its clock is from the server's (decision 3). None for
    any other message; {} for a ping whose fields cannot be used, which is
    dropped -- a heartbeat never ends a session.
    """

    if '"client.ping"' not in raw:
        return None
    try:
        message=json.loads(raw)
    except ValueError:
        return None
    if not isinstance(message,dict) or message.get("type")!="client.ping":
        return None
    seq,sent=message.get("seq"),message.get("sent_at_ms")
    if (
        not isinstance(seq,int) or isinstance(seq,bool) or seq<0
        or not isinstance(sent,(int,float)) or isinstance(sent,bool)
        or not math.isfinite(sent)
    ):
        return {}
    return {"seq":seq,"sent_at_ms":float(sent)}


async def _send_heartbeats(
    session:ListenerSession,sender:LockedSender,heartbeat:_Heartbeat,
)->None:
    """Tell the page the server is there, once a second while a session runs.

    Lane NET, decision 1. From its own task, so a turn the connection's loop
    is still reading (the words of an interruption being checked) never holds
    it up. Each carries the server's clock, the page's last ping as received
    (decision 3) and the server's rule for a timer's last-minute notice,
    which the page's screen timer follows while the connection is lost.
    Nothing changes here.
    """

    while True:
        await _heartbeat_sleep(HEARTBEAT_SECONDS)
        if not session.active:
            continue
        fields:dict[str,Any]={
            "server_time_ms":round(_wall_clock()*1000),
            "timer_last_minute_from_seconds":TIMER_LAST_MINUTE_FROM_SECONDS,
            "timer_last_minute_seconds":TIMER_LAST_MINUTE_SECONDS,
        }
        if heartbeat.ping_seq is not None:
            fields.update(
                ping_seq=heartbeat.ping_seq,ping_sent_at_ms=heartbeat.ping_sent_at_ms,
                ping_received_ms=heartbeat.ping_received_ms,
            )
        await sender.text("server.heartbeat",**fields)


@dataclass
class _HeardConnection:
    """The connection an experiment last continued on, and when it was last heard.

    ``level_reference`` is the wearer's measured voice on it (lane SP1), which
    a reconnection keeps instead of asking the wearer to read again.
    """

    connection_id:str
    heard_at:float
    level_reference:Any=None


#: Decision 5: for each durable experiment, the connection that started or
#: continued it last in this server process and the moment anything was
#: last received on it -- where a lost connection's gap begins. Kept in
#: memory only (the record keeps the gap); at most this many.
_HEARD_CONNECTIONS:dict[str,_HeardConnection]={}
_HEARD_CONNECTIONS_MAX=256


def _bind_heard_connection(session:ListenerSession)->None:
    """This connection now speaks for its experiment (it started or continued it)."""

    if session.experiment_state_version is None or not session.session_id:
        return
    if (
        session.session_id not in _HEARD_CONNECTIONS
        and len(_HEARD_CONNECTIONS)>=_HEARD_CONNECTIONS_MAX
    ):
        oldest=min(_HEARD_CONNECTIONS,key=lambda key:_HEARD_CONNECTIONS[key].heard_at)
        _HEARD_CONNECTIONS.pop(oldest,None)
    _HEARD_CONNECTIONS[session.session_id]=_HeardConnection(
        session.voice_connection_id,_wall_clock())


def _note_heard(session:ListenerSession)->None:
    """Anything received on the connection its experiment continues on: heard now."""

    entry=_HEARD_CONNECTIONS.get(session.session_id) if session.session_id else None
    if entry is not None and entry.connection_id==session.voice_connection_id:
        entry.heard_at=_wall_clock()
        entry.level_reference=session.level_reference


#: Why the page counted its last connection lost (decision 4), as it says it.
_RECONNECT_REASONS=frozenset({"socket_closed","socket_error","browser_offline","server_silent"})


def _reconnect_request(raw:str)->dict[str,Any]|None:
    """The page's own reconnection, beside a session.start that continues a run.

    Decision 4. The control parser keeps only the fields every session.start
    has, so the page's "reconnect" -- when its last connection was lost, on
    the server's clock as the page measured it, and why -- is read here from
    the message itself. None when the start is not a reconnection (the
    experimenter's "실험 이어하기" or a new run).
    """

    try:
        message=json.loads(raw)
    except ValueError:
        return None
    reconnect=message.get("reconnect") if isinstance(message,dict) else None
    if not isinstance(reconnect,dict):
        return None
    lost=reconnect.get("lost_at_ms")
    reason=reconnect.get("reason")
    return {
        "lost_at_ms":(
            float(lost) if isinstance(lost,(int,float)) and not isinstance(lost,bool)
            and math.isfinite(lost) else None),
        "reason":reason if reason in _RECONNECT_REASONS else None,
    }


#: Durable events after a timer's start that leave that timer standing: a
#: pause or a resume of the run, a recovery, a note or a photo. Any other
#: event (a step moved, a later start, a stop, an event this list does not
#: know) is taken to have ended it -- a timer is put back only when the
#: record shows it plainly still running.
_TIMER_KEEPING_EVENTS=frozenset({
    "session_paused","session_in_progress","session_blocked",
    "session_resumed","session_recovered",
    "observation_recorded","observation_corrected","observation_retracted",
    "evidence_attached",
})


def _standing_timer(experiment_state:Mapping[str,Any])->dict[str,Any]|None:
    """The step timer the durable record shows still running at its current step."""

    standing=None
    for item in experiment_state.get("events") or ():
        kind=item.get("event_type")
        if kind in {"timer_started","timer_restarted","timer_resumed","timer_paused"}:
            # Lane HEAR2, decision 3: the last timer event is the timer as
            # it stands -- paused where it was, or running from its new start.
            timer=(item.get("payload") or {}).get("timer")
            standing=timer if isinstance(timer,dict) else None
        elif kind=="timer_cancelled":
            standing=None
        elif kind not in _TIMER_KEEPING_EVENTS:
            standing=None
    if standing is None or standing.get("step_id")!=experiment_state.get("current_step_id"):
        return None
    return standing


def _restore_standing_timer(
    curated:CuratedProtocolSession,experiment_state:Mapping[str,Any],
)->dict[str,Any]|None:
    """Put back the step timer a lost connection left running (decision 4).

    The same timer -- its step, its length and its start as the record kept
    them -- so it ends when it would have ended and the screen counts it on
    from the same end; the source must still state that length for the step
    (``start_timer`` checks it). None when no timer stands.
    """

    timer=_standing_timer(experiment_state)
    if timer is None:
        return None
    try:
        started=datetime.fromisoformat(str(timer["started_at"])).timestamp()
        duration=int(timer["duration_seconds"])
    except (KeyError,TypeError,ValueError):
        return None
    index=next(
        (place for place,step in enumerate(curated.fixture.steps)
         if step.step_id==timer.get("step_id")),
        None)
    if index is None or index!=curated.current_index:
        return None
    ok,_seconds,_words=curated.start_timer(index,now=started,duration_seconds=duration)
    if ok and timer.get("state")=="paused":
        # Lane HEAR2, decision 3: paused where it was; no time passes until
        # the person resumes it.
        try:
            paused_at=datetime.fromisoformat(str(timer["paused_at"])).timestamp()
        except (KeyError,TypeError,ValueError):
            paused_at=None
        if paused_at is not None:
            curated.pause_timer(now=paused_at)
    return curated.timer_status() if ok else None


def _after_first_sentence(text:str)->str:
    """A timer notice's words after its first sentence: " 다음은 N단계, …", or ""."""

    place=text.find(" 다음은 ")
    return text[place:] if place>=0 else ""


def _reconnect_timer_notices(
    curated:CuratedProtocolSession,*,lost_at:float|None,now:float,
)->list[TimerNotice]:
    """The restored timer's notices that fell due while the connection was lost.

    Decision 4: they are taken now (never given again) and said as they
    stand now, in the words "몇 분 남았어?" gets (lane VF, decision 5): a timer
    over is "연결이 끊긴 동안 2단계 10분 타이머는 2분 전에 끝났어요.", a
    last minute already under way "2단계 타이머 40초 남았어요.", each with
    the notice's own next-step line. A notice that fell due before the loss
    was given on the old connection and is not given again.
    """

    notices=[]
    for notice in curated.due_timer_notices(now=now):
        if lost_at is not None and notice.due_at<=lost_at:
            continue
        lead="연결이 끊긴 동안 " if notice.kind=="timer_ended" and lost_at is not None else ""
        answer=curated._remaining_answer("ko")
        notices.append(replace(
            notice,
            display_text=f"{lead}{answer}{_after_first_sentence(notice.display_text)}",
            speech_text=f"{lead}{answer}{_after_first_sentence(notice.speech_text)}",
        ))
    return notices


def _connection_lost_at(
    previous:_HeardConnection|None,reconnect:Mapping[str,Any],session:ListenerSession,
    *,now:float,
)->tuple[float|None,str]:
    """When the lost connection was last heard, and whose note that is.

    "server": the server's own note of the connection, which holds while
    this process ran through the loss. "screen": after a restart only the
    page's moment is left (on the server's clock as the page measured it,
    within the last day). "unknown": neither.
    """

    if previous is not None and previous.connection_id!=session.voice_connection_id:
        return min(previous.heard_at,now),"server"
    page=reconnect.get("lost_at_ms")
    if page is not None and now-86400<=page/1000<=now:
        return page/1000,"screen"
    return None,"unknown"


@dataclass(frozen=True)
class _Reconnection:
    """What a reconnection found: the timer's moments to give, and the gap (decisions 4-5)."""

    notices:tuple[TimerNotice,...]
    lost_at:float|None
    lost_at_source:str
    restored_at:float
    #: Step timers whose end fell inside the gap; None when the gap's start is unknown.
    timers_ended:int|None


def _timers_ended_between(
    timer:Mapping[str,Any]|None,lost_at:float|None,restored_at:float,
)->int|None:
    if lost_at is None:
        return None
    try:
        deadline=datetime.fromisoformat(str((timer or {})["deadline_at"])).timestamp()
    except (KeyError,TypeError,ValueError):
        return 0
    return int(lost_at<deadline<=restored_at)


def _restore_lost_connection(
    session:ListenerSession,curated:CuratedProtocolSession,
    experiment_state:Mapping[str,Any],previous:_HeardConnection|None,
    reconnect:Mapping[str,Any],
)->_Reconnection:
    """A reconnected run as the lost connection left it (decision 4).

    A paused or blocked experiment is paused again from the moment it was
    paused (only the experimenter lifts it); the step timer still running
    is put back. Its notices that fell due while the connection was lost
    come back, to be given once the page has the session, with the gap the
    record keeps (decision 5).
    """

    if experiment_state.get("status") in {"paused","blocked"}:
        paused_at=experiment_state.get("paused_at")
        try:
            moment=datetime.fromisoformat(str(paused_at)).timestamp() if paused_at else None
        except ValueError:
            moment=None
        curated.pause_workflow(now=moment)
    now=_wall_clock()
    lost_at,source=_connection_lost_at(previous,reconnect,session,now=now)
    timer=_restore_standing_timer(curated,experiment_state)
    notices=(
        _reconnect_timer_notices(curated,lost_at=lost_at,now=now)
        if timer is not None else [])
    return _Reconnection(
        tuple(notices),lost_at,source,now,_timers_ended_between(timer,lost_at,now))


def _record_connection_restored(
    session:ListenerSession,curated:CuratedProtocolSession,reconnection:_Reconnection,
    *,reason:str|None,status:str,
)->None:
    """Keep a lost connection's gap in the experiment record (decision 5).

    One "connection_restored" event per reconnection, at the step the run
    stands on, in moments and codes only: when the lost connection was
    last heard and whose note that is, when the run was continued, the gap
    in seconds, how many step timers ended inside it, why the page counted
    the connection lost and the experiment's status. Only for a run that
    was under way; no record open, nothing is written.
    """

    def moment(value:float|None)->str|None:
        return datetime.fromtimestamp(value,timezone.utc).isoformat() if value is not None else None

    gap=(
        round(reconnection.restored_at-reconnection.lost_at,1)
        if reconnection.lost_at is not None else None)
    log.info(
        "connection.restored gap_seconds=%s timers_ended=%s lost_at_source=%s reason=%s",
        gap,reconnection.timers_ended,reconnection.lost_at_source,reason)
    store=session.experiment_report_store
    if store is None or not curated.active:
        return
    try:
        if session.experiment_report_id is None:
            _open_experiment_report(session,curated)
        step=curated.fixture.steps[curated.current_index]
        store.append_event(
            session.experiment_report_id,
            event_key=f"connection-restored-{session.voice_connection_id}",
            event_type="connection_restored",
            step_id=step.step_id,step_label=step.source_label,
            payload={
                "lost_at":moment(reconnection.lost_at),
                "lost_at_source":reconnection.lost_at_source,
                "restored_at":moment(reconnection.restored_at),
                "gap_seconds":gap,
                "timers_ended":reconnection.timers_ended,
                "reason":reason,
                "status":status,
            },
        )
    except Exception as exc:  # noqa: BLE001 - the run continues; the log says the gap was not kept
        log.warning("connection gap not recorded error=%s",type(exc).__name__)


@app.websocket("/ws")
async def voice_socket(websocket:WebSocket):
    await websocket.accept()
    if (origin_refusal:=_socket_origin_refusal(websocket)) is not None:
        await websocket.send_text(event("error",message=origin_refusal))
        await websocket.close(
            code=1008,
            reason="origin not allowed" if origin_refusal=="origin_not_allowed" else "invalid configuration")
        return
    workspace_context_token=None
    account_context_token=None
    try:
        settings=_workspace_settings()
        operational=_runtime_usage_scope()=="operational"
        auth=_auth_settings()
        if auth.accounts:
            # Lane ON, decision ①: the same session cookie as the HTTP API.
            if not settings.enabled:
                raise IdentityConfigurationError("Accounts need the tenant workspace.")
            cookies=getattr(websocket,"cookies",None) or {}
            token=cookies.get(SESSION_COOKIE_NAME)
            # Lane APP1, decision 3: the app's screen sends its token first.
            if not token and _from_app(getattr(websocket,"headers",{})):
                token=await _app_socket_token(websocket)
            account=_account_for_cookie(token)
            principal=account.principal()
            workspace=initialize_workspace_store(settings)
            try:
                workspace.bootstrap_principal(principal)
                principal=workspace.effective_principal(principal)
            finally:
                workspace.close()
            workspace_context_token=_REQUEST_PRINCIPAL.set(principal)
            account_context_token=_REQUEST_ACCOUNT.set(account)
        elif settings.enabled or operational:
            if not settings.enabled:
                raise IdentityConfigurationError(
                    "Operational scope requires the tenant workspace.")
            headers=getattr(websocket,"headers",{})
            principal=_identity_resolver().resolve(headers.get("authorization"))
            workspace=initialize_workspace_store(settings)
            try:
                workspace.bootstrap_principal(principal)
                principal=workspace.effective_principal(principal)
            finally:
                workspace.close()
            workspace_context_token=_REQUEST_PRINCIPAL.set(principal)
    except Exception as exc:
        if isinstance(exc,ServerConfigurationError):
            await websocket.send_text(event(
                "error",message="configuration_invalid",fields=list(exc.field_names)))
            await websocket.close(code=1008,reason="invalid configuration")
            return
        await websocket.send_text(event(
            "error",message=getattr(exc,"code","authentication_failed")))
        await websocket.close(code=1008,reason="authentication required")
        return
    try:
        vad_settings=VoiceVadSettings.from_environment()
        config=VadConfig.from_settings(vad_settings.cascade)
        report_settings=ExperimentReportSettings.from_environment()
        supplemental_settings=SupplementalKnowledgeSettings.from_environment()
        multi_brain_settings=MultiBrainSettings.from_environment()
        llm_router_settings=LlmRouterSettings.from_environment()
        web_explanation_settings=WebExplanationSettings.from_environment()
        drawn_diagram_settings=DrawnDiagramSettings.from_environment()
    except (ConfigurationError,ValueError) as exc:
        await websocket.send_text(event(
            "error",message=f"invalid non-secret configuration: {exc}"))
        await websocket.close(code=1008,reason="invalid configuration")
        return
    research_capabilities={
        # The xAI web reference search is gone (lane DOC3), and since lane FX3
        # (decision 3) the screen no longer reads an entry for it.
        "supplemental_model":supplemental_settings.public_capability(),
        "multi_brain":multi_brain_settings.public_capability(),
        "web_explanation":web_explanation_settings.public_capability(),
        "drawn_diagram":drawn_diagram_settings.public_capability(),
        # Lane KNOW (2026-10-24): the public knowledge tier, as loaded at start.
        "public_knowledge":_public_knowledge_capability(),
    }
    if llm_router_settings.enabled:
        research_capabilities["llm_router"]=llm_router_settings.public_capability()
    report_store=(
        ExperimentReportStore(report_settings.database_path)
        if report_settings.enabled and report_settings.database_path is not None
        else None
    )
    session=ListenerSession(
        EndpointDetector(config,listening_onset=True),
        experiment_report_store=report_store,
        supplemental_knowledge_settings=supplemental_settings,
        multi_brain_settings=multi_brain_settings,
        llm_router_settings=llm_router_settings,
        web_explanation_settings=web_explanation_settings,
        drawn_diagram_settings=drawn_diagram_settings,
    ); task=None; trusted_config=None
    # Lane KNOW: the public tier loaded at start (None when it is off), the
    # source a problem's help searches after the protocol and the history.
    session.public_knowledge=getattr(app.state,"public_knowledge",None)
    # Every sentence this connection synthesizes is remembered for the echo
    # check; tasks started from here inherit the binding.
    _SPEAKING_SESSION.set(session)
    # Lane ON, decision 2: the connection speaks with the voice the person
    # chose (their setting), and says which in ``ready``.
    try:
        agent_voice_settings=await asyncio.to_thread(_load_experimenter_settings)
    except Exception:  # noqa: BLE001 - the provider's voice then
        agent_voice_settings={}
    session.tts_voice=_agent_voice_id(agent_voice_settings)
    curated_fixture=None
    sender=LockedSender(websocket); pipeline="cascade"
    # Lane VT, decisions 1-2: the step timer is told when it runs out.
    timer_notices=asyncio.create_task(_watch_timer_notices(session,sender))
    # Lane NET, decision 1: the server's heartbeat, and the page's last ping.
    heartbeat=_Heartbeat()
    heartbeats=asyncio.create_task(_send_heartbeats(session,sender,heartbeat))
    # Decision 4: the session running now continues a run after a lost connection.
    restored_connection=False
    await websocket.send_text(event("ready",sample_rate=16000,
                                    pipelines=["cascade"],frame_ms=20,
                                    frame_bytes=FRAME_BYTES,vad_mode=config.mode,
                                    endpoint_silence_ms=config.endpoint_silence_frames*20,
                                    prefix_padding_ms=config.prefix_frames*20,
                                    barge_in_prefix_ms=config.barge_in_prefix_frames*20,
                                    voice_profile={
                                        "pipeline":"cascade",
                                        "provider":_tts_provider(),
                                        "voice_id":session.tts_voice or _tts_voice(),
                                        "agent_voice":session.tts_voice or "default",
                                        "persona":"professor",
                                    },
                                    research_capabilities=research_capabilities))
    account_watch=None
    voice_task=asyncio.current_task()
    voice_account=_REQUEST_ACCOUNT.get()
    if auth.accounts and voice_account is not None and voice_task is not None:
        # Recheck after setup awaits, before registering a connection.
        try:
            _account_for_cookie(token)
        except Exception:
            timer_notices.cancel()
            heartbeats.cancel()
            await asyncio.gather(timer_notices,heartbeats,return_exceptions=True)
            await websocket.close(code=1008,reason="authentication required")
            return
        _ACCOUNT_VOICE_CONNECTIONS.setdefault(voice_account.account_id,{})[voice_task]=websocket
        account_watch=asyncio.create_task(_watch_account_voice_session(token,voice_task,websocket))
    try:
        while True:
            message=await websocket.receive()
            if message.get("type")=="websocket.disconnect": break
            _note_heard(session)
            if message.get("bytes") is not None:
                if session.refresh_cooldown(): await websocket.send_text(event("state.changed",state="IDLE"))
                listener_events=[]
                accepted_interrupts={}
                #: Lane HEAR2: the provider failure behind a rejected candidate.
                provider_failures:dict[int,SpeechProviderError]={}
                for item in session.accept_chunk(message["bytes"]):
                    if item.kind!="barge_in_audio_ready":
                        listener_events.append(item)
                        continue
                    validation_started=session.clock()
                    stt_context=cascade_transcription_context(
                        session,audio_origin="barge_in")
                    try:
                        transcription=await asyncio.to_thread(
                            transcribe_cascade_audio,
                            item.result.utterance or b"",stt_context)
                        _note_stt_adaptation_fallback(session,transcription)
                        if isinstance(transcription,str):
                            transcription=Transcription(transcription,None)
                    except Exception as exc:
                        # Lane HEAR2: a provider that failed is its own reason,
                        # reported once below where every rejected candidate is.
                        failed_reason=(
                            "stt_failed" if isinstance(exc,SpeechProviderError)
                            else "transcription_failed")
                        log.warning(
                            "barge_in.rejected reason=%s "
                            "voiced_frames=%d total_frames=%d",
                            failed_reason,item.result.voiced_frames,item.result.total_frames)
                        rejected=session.reject_interrupt_candidate(
                            item,failed_reason)
                        if rejected is not None:
                            listener_events.append(rejected)
                            if isinstance(exc,SpeechProviderError):
                                provider_failures[id(rejected)]=exc
                        continue
                    stt_ms=max(
                        0,round((session.clock()-validation_started)*1000))
                    session.note_stt_ok()
                    if not transcription.text.strip():
                        rejected=session.reject_interrupt_candidate(
                            item,"transcription_failed")
                        if rejected is not None:
                            listener_events.append(rejected)
                        continue
                    echo=session.own_speech_echo(transcription.text)
                    if echo is not None:
                        # The agent's own voice: playback goes on (6b).
                        log.info(
                            "barge_in.rejected reason=self_echo (메아리로 버림) "
                            "overlap=%.2f voiced_frames=%d total_frames=%d",
                            echo.overlap,item.result.voiced_frames,
                            item.result.total_frames)
                        rejected=session.reject_interrupt_candidate(
                            item,"self_echo")
                        if rejected is not None:
                            listener_events.append(rejected)
                        continue
                    input_decision=classify_input_event(
                        transcription,
                        keyterms=keyterm_dump_terms(stt_context.keyterms),
                        duration_seconds=transcription.duration_seconds,
                    )
                    if not input_decision.accepted or transcript_is_filler_only(transcription.text):
                        # Lane SP1, decision 5: a filler is no interruption either.
                        rejected=session.reject_interrupt_candidate(
                            item,
                            input_decision.reason or "non_speech"
                            if not input_decision.accepted else "filler_only")
                        if rejected is not None:
                            listener_events.append(rejected)
                        continue
                    committed=session.commit_interrupt_candidate(item,stt_ms=stt_ms)
                    if not committed:
                        rejected=session.reject_interrupt_candidate(
                            item,"stale_candidate")
                        if rejected is not None:
                            listener_events.append(rejected)
                        else:
                            # Lane HEAR, decision 3: words the turn could no
                            # longer take are a drop like any other, not lost in silence.
                            await _report_speech_drop(
                                session,sender,reason="stale_candidate",
                                turn_id=item.turn_id,generation=item.generation,
                                duration_ms=item.result.total_frames*20,
                                level_db=item.result.voiced_level_db,during_playback=True)
                        continue
                    end=next(
                        event_item for event_item in committed
                        if event_item.kind=="speech.end")
                    accepted_interrupts[(end.turn_id,end.generation)]=(
                        {"transcription":transcription,"stt_ms":stt_ms,
                         "context":stt_context,"failed":False}
                    )
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
                        "prefix_frames_retained":(
                            item.result.prefix_frames_retained),
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
                    if item.kind=="speech.ignored":
                        # Lane SP1, decision 1: no words, no audio -- the
                        # level only (AGENTS.md rule 6).
                        log.info(
                            "speech.ignored reason=%s level_db=%s reference_db=%s "
                            "margin_db=%s voiced_frames=%d total_frames=%d "
                            "during_playback=%s turn_id=%s",
                            fields["reason"],fields.get("level_db"),
                            fields.get("reference_db"),fields.get("margin_db"),
                            item.result.voiced_frames,item.result.total_frames,
                            fields.get("during_playback"),item.turn_id)
                        # Lane HEAR, decision 3: "reason" is no dimension the
                        # analytics store takes, so this was never recorded.
                        _record_workspace_metric(
                            category="voice",metric_name="speech_ignored",
                            dimensions={"reason_code":str(fields["reason"])[:100]},
                        )
                        await _report_speech_drop(
                            session,sender,reason=str(fields["reason"] or "other_speaker_level"),
                            turn_id=item.turn_id,generation=item.generation,
                            duration_ms=item.result.total_frames*20,
                            level_db=fields.get("level_db"),
                            during_playback=bool(fields.get("during_playback")))
                        await _report_level_drop(session,sender,fields.get("level_db"))
                        if session.noisy_mode_suggestion_due():
                            # Lane HEAR, decision 9: three in a minute -- recorded, not asked.
                            _record_noise_condition(session,"other_voices")
                    if item.kind=="speech.rejected":
                        # Lane HEAR, decision 3: the detector's own drop (too short).
                        await _report_speech_drop(
                            session,sender,
                            reason=str(fields["reason"] or "minimum_voiced_frames"),
                            turn_id=item.turn_id,generation=item.generation,
                            duration_ms=item.result.total_frames*20,level_db=None)
                    elif item.kind=="barge_in_rejected" and id(item) in provider_failures:
                        # Lane HEAR2: the speech-to-text provider failed on words
                        # said while an answer played -- shown, said once per outage.
                        await _report_stt_failure(
                            session,sender,provider_failures.pop(id(item)),
                            turn_id=item.turn_id,generation=item.generation,
                            duration_ms=item.result.total_frames*20,
                            level_db=item.result.voiced_level_db,during_playback=True)
                    elif item.kind=="barge_in_rejected" and fields["reason"]!="other_speaker_level":
                        # Heard while an answer was coming: it goes on, nothing is said.
                        await _report_speech_drop(
                            session,sender,reason=str(fields["reason"] or "barge_in_rejected"),
                            turn_id=item.turn_id,generation=item.generation,
                            duration_ms=item.result.total_frames*20,
                            level_db=item.result.voiced_level_db,during_playback=True)
                    if (
                        item.kind=="barge_in_rejected"
                        and session.resume_deferred_playback_end(
                            item.turn_id,item.generation)
                    ):
                        # The playback.ended held back for this candidate:
                        # the turn now ends as it would have (decision 5).
                        await _send_playback_terminal(
                            websocket,session,item.turn_id,
                            cooldown_ms=config.cooldown_ms)
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
                        if task is not None and not task.done():
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                            task=None
                        progress=session.advance_turn_progress(
                            item.turn_id,item.generation,"listening")
                        if progress is not None:
                            await websocket.send_text(event(
                                "turn.state",**progress))
                    if item.kind=="speech.end":
                        accepted=accepted_interrupts.get(
                            (item.turn_id,item.generation))
                        if accepted and accepted["failed"]:
                            task=asyncio.create_task(
                                run_barge_in_stt_failure_turn(
                                    websocket,session,turn_id=item.turn_id,
                                    generation=item.generation,
                                    input_frames=item.result.total_frames,
                                    voiced_frames=item.result.voiced_frames,
                                    context=accepted["context"],
                                    stt_ms=accepted["stt_ms"],
                                )
                            )
                        else:
                            task=asyncio.create_task(run_turn_safely(
                                websocket,session,item.result.utterance or b"",
                                item.turn_id,item.result.total_frames,
                                item.result.voiced_frames,
                                item.result.prefix_frames_retained,
                                accepted_transcription=(
                                    accepted["transcription"] if accepted else None),
                                accepted_stt_ms=(
                                    accepted["stt_ms"] if accepted else None),
                                accepted_stt_context=(
                                    accepted["context"] if accepted else None),
                                voiced_level_db=item.result.voiced_level_db))
                if session.noise_floor_suggestion_due():
                    # Lane HEAR, decision 9: the room is near the wearer's level -- recorded, not said.
                    _record_noise_condition(session,"floor_near_line")
                _record_noise_floor(session)
                continue
            if message.get("text") is None:continue
            if (ping:=_client_ping(message["text"])) is not None:
                if ping:
                    heartbeat.ping_seq,heartbeat.ping_sent_at_ms=ping["seq"],ping["sent_at_ms"]
                    heartbeat.ping_received_ms=round(_wall_clock()*1000)
                continue
            try:
                control=parse_control(message["text"])
            except ProtocolError:
                await websocket.send_text(event(
                    "error",message="invalid control message",recoverable=True))
                continue
            if control["type"]=="session.start":
                if session.active:
                    same_configuration=(
                        control["configuration_id"]==session.accepted_configuration_id
                        and control["mode"]==session.accepted_mode
                        and control["language"]==session.accepted_language
                        and control["protocol_id"]==session.accepted_protocol_id
                        and normalize_input_language_preference(
                            control.get("input_language",control["language"]))
                        ==session.accepted_input_language
                        and control.get("experiment_session_id",session.session_id)
                        ==session.session_id)
                    if not same_configuration:
                        await websocket.send_text(event(
                            "error",message="session configuration conflicts with active session",
                            code="session_configuration_conflict",recoverable=True,
                            message_ko="이미 다른 설정으로 연결돼 있어요. 현재 실험을 계속해 주세요.",
                            configuration_id=session.accepted_configuration_id,
                            generation=session.generation))
                        continue
                    await websocket.send_text(event(
                        "session.ready",configuration_id=session.accepted_configuration_id,
                        generation=session.generation,mode=session.accepted_mode,
                        language=session.accepted_language,
                        input_language=session.accepted_input_language.value,
                        protocol_id=session.accepted_protocol_id,
                        revision_id=session.accepted_revision_id,
                        experiment_session_id=session.session_id,
                        experiment_session_version=session.experiment_state_version,
                        research_capabilities=research_capabilities))
                    if session.curated_protocol_session is not None:
                        await websocket.send_text(event(
                            "protocol.fixture.state",action="attached",
                            configuration_id=session.accepted_configuration_id,
                            state=session.curated_protocol_session.state(),
                            screen=curated_screen_fields(session.curated_protocol_session)))
                        # Lane KO2, decision 3.
                        _queue_screen_korean(
                            session,session.curated_protocol_session,
                            lambda kind,**fields: websocket.send_text(event(kind,**fields)))
                    continue
                requested_mode=control["mode"]
                configuration_id=control["configuration_id"]
                requested_protocol_id=control["protocol_id"]
                requested_input_language=normalize_input_language_preference(
                    control.get("input_language",control["language"])
                )
                # Lane NET, decision 4: the page continuing its run after a
                # lost connection, and what is said of the timer then.
                reconnect=(
                    _reconnect_request(message["text"])
                    if control.get("experiment_session_id") is not None else None)
                restored_connection=False
                reconnect_notices:list[TimerNotice]=[]
                reconnection:_Reconnection|None=None
                configuration_stage="server_policy"
                try:
                    trusted_config=trusted_config or server_config()
                    configuration_stage="session_language"
                    context=server_tool_context(trusted_config,control["language"])
                    selected_curated_fixture=None
                    selected_revision_id=None
                    # Lane CF, decision 5: the catalog entry's file and upload.
                    selected_basis_entry=None
                    selection_failure=None
                    if requested_mode=="cascade":
                        if requested_protocol_id is None:
                            selection_failure="protocol_selection_required"
                        else:
                            if trusted_config.curated_protocol_fixture_path is not None:
                                configuration_stage="curated_protocol_fixture"
                                curated_fixture=curated_fixture or await asyncio.to_thread(load_curated_protocol_fixture,
                                    trusted_config.curated_protocol_fixture_path,
                                    trusted_config.curated_protocol_provenance_path,
                                    trusted_config.curated_protocol_source_pdf_path,
                                )
                                if curated_fixture.protocol_id==requested_protocol_id:
                                    # Being the configured fixture says which
                                    # file was loaded, not that anyone judged
                                    # it fit to run.  This branch used to hand
                                    # the fixture straight to a voice session
                                    # with every readiness gate blocked; it now
                                    # asks the same question the catalog branch
                                    # below asks, and refuses on the same terms.
                                    if (await asyncio.to_thread(_candidate_fixture_execution_state,
                                        curated_fixture,
                                    ))["available_for_execution"]:
                                        selected_curated_fixture=curated_fixture
                                        selected_revision_id=getattr(
                                            curated_fixture,"revision_id",
                                            f"fixture-{requested_protocol_id}")
                                    else:
                                        selection_failure=(
                                            "protocol_selection_unavailable")
                            if selected_curated_fixture is None:
                                # The shared curated development fixture is not
                                # tenant-owned and is exempt above; every other
                                # protocol/procedure selection stays tenant-scoped.
                                configuration_stage="session_language"
                                _scope_catalog_resource(requested_protocol_id)
                                settings=_protocol_store_settings()
                                if settings.enabled:
                                    configuration_stage="protocol_catalog"
                                    def select(catalog:ProtocolCatalog)->tuple[
                                        CuratedProtocolFixture|None,str|None,str|None,dict[str,object]|None,
                                    ]:
                                        selected=None
                                        revision_id=None
                                        failure=None
                                        basis=None
                                        try:
                                            entry=catalog.get_entry(requested_protocol_id)
                                        except ProtocolCatalogNotFoundError:
                                            entry=None
                                        recovered=(
                                            _recovery_fixture(
                                                catalog,requested_protocol_id,entry,
                                                control.get("experiment_session_id"))
                                            if entry is not None else None)
                                        if recovered is not None:
                                            selected,revision_id,failure=recovered
                                        elif entry is not None:
                                            if entry.available_for_execution:
                                                selected=(
                                                    catalog.load_executable_fixture(
                                                        requested_protocol_id))
                                                revision_id=entry.revision_id
                                            else:
                                                failure=(
                                                    "protocol_selection_unavailable")
                                        if selected is not None:
                                            basis={
                                                "source_filename":getattr(
                                                    entry,"source_filename",None),
                                                "created_at":getattr(
                                                    entry,"created_at",None),
                                            }
                                        return selected,revision_id,failure,basis
                                    (selected_curated_fixture,selected_revision_id,
                                     selection_failure,selected_basis_entry)=await _catalog_in_worker(select)
                            if selected_curated_fixture is None:
                                # A refusal already recorded upstream is the
                                # true one.  Overwriting it made a protocol the
                                # server knows about, and is declining to run,
                                # report itself as unknown.
                                selection_failure=selection_failure or (
                                    "protocol_selection_unknown"
                                    if trusted_config.curated_protocol_fixture_path
                                    else "protocol_selection_unavailable")
                    elif requested_protocol_id is not None:
                        selection_failure="protocol_selection_not_supported_for_mode"
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
                    if selected_curated_fixture is not None:
                        configuration_stage="start_confirmation"
                        readiness=await asyncio.to_thread(
                            _protocol_start_readiness,requested_protocol_id,selected_revision_id)
                        if not readiness["ready"]:
                            await websocket.send_text(event(
                                "session.configuration_required",configuration_id=configuration_id,
                                mode=requested_mode,language=context.language,
                                protocol_id=requested_protocol_id,reason=readiness["reason"],
                                message_ko=("핵심 값을 먼저 확인해 주세요."
                                            if readiness["reason"]=="start_check_required"
                                            else "안전 주의를 읽고 확인해 주세요."
                                            if readiness["reason"]=="safety_acknowledgement_required"
                                            else "시작 화면에서 현재 프로토콜의 확인을 마쳐 주세요.")))
                            continue
                    # Lane EASY, decision 2: today's values the person chose
                    # for this start (a recovery keeps the run's own).
                    run_change_set:tuple[str|None,tuple[RunChange,...]]=(None,())
                    if selected_curated_fixture is not None and control.get("experiment_session_id") is None:
                        configuration_stage="run_changes"
                        run_change_set,run_changes_refused=await asyncio.to_thread(
                            _start_run_changes,requested_protocol_id,selected_revision_id,
                            selected_curated_fixture,control.get("run_change_set_id"))
                        if run_changes_refused is not None:
                            await websocket.send_text(event(
                                "session.configuration_required",configuration_id=configuration_id,
                                mode=requested_mode,language=context.language,
                                protocol_id=requested_protocol_id,reason=run_changes_refused,
                                message_ko=RUN_CHANGES_REFUSAL_KO[run_changes_refused]))
                            continue
                    configuration_stage="session_state"
                    session.set_tool_context(context)
                    if selected_curated_fixture is not None:
                        selected_curated_fixture=await asyncio.to_thread(_with_revision_translations,
                            selected_curated_fixture)
                    # The experimenter's press of start is the one human
                    # confirmation (decision of 2026-10-08). The source's
                    # safety statements shown before it are recorded in the
                    # experiment report when it opens.
                    session.safety_notices_shown=(
                        fixture_safety_notices(selected_curated_fixture)
                        if selected_curated_fixture is not None else None)
                    session.set_curated_protocol_fixture(selected_curated_fixture)
                    if session.curated_protocol_session is not None:
                        await asyncio.to_thread(
                            session.curated_protocol_session._protocol_vocabulary)
                    _subscribe_translations(session,sender)
                    recovery_session_id=control.get("experiment_session_id")
                    recovery_version=control.get("experiment_session_version")
                    if recovery_session_id is not None and not _workspace_settings().enabled:
                        raise WorkspaceError(
                            "Experiment recovery requires the tenant workspace."
                        )
                    session.start(
                        str(recovery_session_id)
                        if recovery_session_id is not None else None
                    )
                    if session.curated_protocol_session is not None and selected_curated_fixture is not None:
                        try:
                            log.info(
                                "safety_pack.resolve.start: protocol_id=%s revision=%s catalog=%s",
                                requested_protocol_id,
                                selected_revision_id,
                                bool(trusted_config.catalog_path),
                            )
                            safety_pack = await asyncio.to_thread(resolve_safety_pack,
                                selected_curated_fixture.draft.protocol,
                                catalog_path=trusted_config.catalog_path,
                                facility_id=trusted_config.facility_id,
                                usage_scope=trusted_config.usage_scope,
                                protocol_revision=selected_revision_id or "1",
                            )
                            log.info(
                                "safety_pack.resolve.complete: protocol_id=%s status=%s docs=%d (sop=%d, sds=%d, equip=%d)",
                                requested_protocol_id,
                                safety_pack.coverage_status,
                                safety_pack.total_document_count,
                                len(safety_pack.sop_documents),
                                len(safety_pack.sds_documents),
                                len(safety_pack.equipment_documents),
                            )
                        except Exception as exc:
                            log.exception(
                                "safety_pack.resolve.degraded: protocol_id=%s error=%s",
                                requested_protocol_id,
                                exc,
                            )
                            safety_pack = unavailable_safety_pack(
                                protocol_id=requested_protocol_id or "unknown_protocol",
                                protocol_revision=selected_revision_id or "1",
                                facility_id=trusted_config.facility_id,
                                status=(
                                    SOURCE_PROTOCOL_ONLY_COVERAGE
                                    if _source_protocol_only(trusted_config.usage_scope)
                                    else "unavailable"),
                                error_reason=str(exc),
                            )

                        session.curated_protocol_session.set_safety_pack(safety_pack)
                        session.curated_protocol_session.activate_configured()
                        # Lane CF: the experimenter's settings (decisions 1, 4),
                        # the file the run follows (decision 5), and -- the
                        # start pressed -- the source's questions asked before
                        # the start (decision 4). A recovered run skips them.
                        experimenter_settings=await asyncio.to_thread(
                            _load_experimenter_settings)
                        session.curated_protocol_session.apply_experimenter_settings(
                            experimenter_settings)
                        if run_change_set[1]:
                            # Lane EASY, decision 2: the state machine takes them.
                            session.curated_protocol_session.set_run_changes(
                                run_change_set[1],change_set_id=run_change_set[0])
                        # Lane SP1, decision 1: the "민감도" margin of the
                        # wearer's measurement is the experimenter's setting.
                        session.set_speaker_sensitivity(
                            str(experimenter_settings.get("speaker_sensitivity",DEFAULT_SENSITIVITY)))
                        session.curated_protocol_session.set_source_basis(
                            _source_basis_from(
                                getattr(selected_curated_fixture,"source_filename",None),
                                selected_basis_entry))
                        session.curated_protocol_session.open_prestart_questions()
                        await websocket.send_text(event(
                            "experimenter.settings",
                            configuration_id=configuration_id,
                            settings=experimenter_settings,
                        ))
                        pack_dict = safety_pack.public_dict()
                        if session.curated_protocol_session and hasattr(session.curated_protocol_session, "fixture") and session.curated_protocol_session.fixture:
                            pack_dict["step_guidance"] = [
                                safety_pack.guidance_for_step(step, i).public_dict()
                                for i, step in enumerate(session.curated_protocol_session.fixture.steps)
                            ]
                        await websocket.send_text(event(
                            "session.safety_pack",
                            configuration_id=configuration_id,
                            protocol_id=requested_protocol_id,
                            revision_id=selected_revision_id,
                            safety_pack=pack_dict,
                        ))
                    configuration_stage="experiment_session"
                    experiment_state=_start_or_resume_workspace_experiment(
                        session,
                        protocol_id=str(requested_protocol_id),
                        protocol_revision_id=str(selected_revision_id),
                        recovery_session_id=(
                            str(recovery_session_id)
                            if recovery_session_id is not None else None
                        ),
                        recovery_version=(
                            int(recovery_version)
                            if recovery_version is not None else None
                        ),
                        reattach=reconnect is not None,
                    )
                    if recovery_session_id is None and run_change_set[0] is not None:
                        # The choice is used once: by this experiment.
                        await asyncio.to_thread(
                            _mark_run_changes_applied,str(requested_protocol_id),run_change_set[0],
                            session.session_id)
                    previous_connection=(
                        _HEARD_CONNECTIONS.get(session.session_id)
                        if reconnect is not None else None)
                    _bind_heard_connection(session)
                    restored_before=None
                    if (
                        recovery_session_id is not None
                        and experiment_state is not None
                        and session.curated_protocol_session is not None
                        and experiment_state["status"] in {"in_progress","paused","blocked"}
                    ):
                        restored_before=history_before(
                            session.curated_protocol_session)
                        session.curated_protocol_session.restore_experiment_progress(
                            current_step_id=str(experiment_state["current_step_id"]),
                            completed_step_ids=tuple(
                                str(item["step_id"])
                                for item in experiment_state["completed_steps"]
                            ),
                            # Lane N, decision 9: a run opened with a later
                            # start, or continued from such a run's checkpoint.
                            skipped_step_ids=_workspace_skipped_step_ids(experiment_state),
                            # Lane CB, decision 1: steps a source condition
                            # answered "no" passed over.
                            branch_skipped_step_ids=_workspace_branch_skipped_step_ids(
                                experiment_state),
                            # Lane CF, decision 3: completions a revert took back.
                            reverted_step_ids=_workspace_reverted_step_ids(
                                experiment_state),
                            # Lane FX4, decision 1: the elapsed clock goes on
                            # from the experiment's own start.
                            started_at=_workspace_started_at(experiment_state),
                        )
                        if reconnect is not None:
                            # Lane NET, decision 4: the run as the lost
                            # connection left it -- a pause (or a block) is
                            # still one, and the step timer still runs.
                            reconnection=_restore_lost_connection(
                                session,session.curated_protocol_session,
                                experiment_state,previous_connection,reconnect)
                            reconnect_notices=list(reconnection.notices)
                        elif experiment_state["status"] in {"paused","blocked"}:
                            session.curated_protocol_session.pause_workflow()
                    if (recovery_session_id is not None and experiment_state is not None
                            and session.curated_protocol_session is not None):
                        # Lane EASY, decision 2: today's values as the run kept them.
                        kept_changes=_workspace_run_changes(experiment_state)
                        if kept_changes is None:
                            kept_changes=await asyncio.to_thread(
                                _applied_run_changes,str(requested_protocol_id),str(recovery_session_id))
                        try:
                            session.curated_protocol_session.set_run_changes(kept_changes)
                        except CuratedProtocolFixtureError:
                            log.warning("run_changes.restore_refused protocol_id=%s",requested_protocol_id)
                    if reconnect is not None and experiment_state is not None:
                        restored_connection=True
                        if (
                            previous_connection is not None
                            and previous_connection.level_reference is not None
                        ):
                            # The wearer's voice as measured on the lost
                            # connection (lane SP1); nobody reads again.
                            session.level_reference=(
                                previous_connection.level_reference.with_sensitivity(
                                    session.speaker_sensitivity))
                    pipeline="cascade"
                    session.accept_configuration(
                        configuration_id,pipeline,context.language,
                        requested_protocol_id,selected_revision_id,
                        requested_input_language)
                    if restored_before is not None:
                        # Lane RT, decision 2: the run recovered here is the
                        # first thing the router's history holds.
                        _record_screen_history(
                            session,restored_before,control="restore",changed=True)
                except (RuntimeError,ValueError,WorkspaceError) as exc:
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
                # Lane HEAR, decision 5: the voice saved before this start --
                # the page keeps one connection open across starts, so the
                # choice read when it opened may be an older one.
                try:
                    start_voice_settings=await asyncio.to_thread(_load_experimenter_settings)
                except Exception:  # noqa: BLE001 - the voice already chosen then
                    start_voice_settings=None
                if start_voice_settings is not None:
                    session.tts_voice=_agent_voice_id(start_voice_settings)
                ready_fields={
                    "configuration_id":session.accepted_configuration_id,
                    "generation":session.generation,
                    "mode":session.accepted_mode,
                    "language":session.accepted_language,
                    "input_language":session.accepted_input_language.value,
                    "protocol_id":session.accepted_protocol_id,
                    "research_capabilities":research_capabilities,
                    "agent_voice":session.tts_voice or "default",
                    "voice_id":session.tts_voice or _session_voice_fallback(),
                }
                if session.accepted_protocol_id is not None:
                    ready_fields["revision_id"]=session.accepted_revision_id
                ready_fields["experiment_session_id"]=session.session_id
                if session.experiment_state_version is not None:
                    ready_fields["experiment_session_version"]=(
                        session.experiment_state_version)
                    ready_fields["experiment_recovered"]=(
                        recovery_session_id is not None)
                if restored_connection:
                    # Lane NET, decision 4: the page's own reconnection was taken.
                    ready_fields["connection_restored"]=True
                await websocket.send_text(event("session.ready",**ready_fields))
                if experiment_state is not None:
                    await websocket.send_text(event(
                        "experiment.session.state",state=experiment_state))
                if session.curated_protocol_session is not None:
                    await websocket.send_text(event(
                        "protocol.fixture.state",
                        configuration_id=session.accepted_configuration_id,
                        state=session.curated_protocol_session.state(),
                        screen=curated_screen_fields(session.curated_protocol_session),
                        action="attached",
                    ))
                    _queue_source_figures(
                        session,session.curated_protocol_session,
                        lambda kind,**fields: websocket.send_text(event(kind,**fields)))
                    # Lane KO2, decision 3: the screen's step sentences in Korean.
                    _queue_screen_korean(
                        session,session.curated_protocol_session,
                        lambda kind,**fields: websocket.send_text(event(kind,**fields)))
                await websocket.send_text(event("session.started",state=session.state.value,
                                                pipeline=pipeline))
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
                for notice in reconnect_notices:
                    # Lane NET, decision 4: the timer's moments the lost
                    # connection missed, given as the server gives a notice.
                    session.track_timer_notice_task(asyncio.create_task(
                        _deliver_timer_notice(
                            session,sender,session.curated_protocol_session,notice)))
                if reconnection is not None and session.curated_protocol_session is not None:
                    # Decision 5: the gap, kept in the experiment record.
                    await asyncio.to_thread(
                        _record_connection_restored,session,
                        session.curated_protocol_session,reconnection,
                        reason=reconnect.get("reason") if reconnect else None,
                        status=str(experiment_state["status"]) if experiment_state else "unknown")
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
                if task and not task.done():
                    await _finish_all_research_operations(
                        sender,session,"cancelled")
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
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
                if task and not task.done():
                    await _finish_all_research_operations(
                        sender,session,"cancelled")
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.reset":
                if not session.active:
                    await websocket.send_text(event("error",message="session is not active"))
                    continue
                if task and not task.done():
                    await _finish_all_research_operations(
                        sender,session,"cancelled")
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                session.reset_sensitive_state()
                await websocket.send_text(event("session.reset",state=session.state.value))
                if session.curated_protocol_session is not None:
                    fixture_state = session.curated_protocol_session.state()
                    await websocket.send_text(event(
                        "protocol.fixture.state", state=fixture_state,
                        screen=curated_screen_fields(session.curated_protocol_session)))
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.stop":
                if task and not task.done():
                    await _finish_all_research_operations(
                        sender,session,"cancelled")
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                if session.experiment_state_version is not None:
                    try:
                        experiment_state=_transition_workspace_experiment(
                            session,action="stop",
                            event_key=(
                                f"connection-{session.voice_connection_id}-stop"
                            ),
                            reason="explicit_session_stop",
                        )
                    except WorkspaceError as exc:
                        _record_workspace_metric(
                            category="workflow",metric_name="mutation_failure",
                            dimensions={
                                "status":"rolled_back",
                                "reason_code":str(getattr(exc,"code","workspace_error"))[:100],
                                "event_kind":"session_stopped",
                            },
                        )
                        await websocket.send_text(event(
                            "error",message=getattr(exc,"code","workspace_error")))
                        continue
                    if experiment_state is not None:
                        await websocket.send_text(event(
                            "experiment.session.state",state=experiment_state))
                pipeline="cascade"
                session.stop(); await websocket.send_text(event("session.stopped",state=session.state.value))
            elif control["type"]=="workflow.resume" and (
                # Lane RX4, the person's decision 3 of 2026-10-26: a resume is
                # fenced by the accepted configuration alone, as a pause is.
                # The generation moves on with a joined utterance (lane HEAR)
                # or a barge-in while the screen still holds session.ready's,
                # so a [다시 시작] pressed after either was refused as stale.
                not session.active or session.curated_protocol_session is None
                or session.curated_protocol_session._experiment_started_at is None
                or type(control.get("configuration_id")) is not int
                or control["configuration_id"]!=session.accepted_configuration_id
            ):
                not_started=(session.curated_protocol_session is not None
                             and session.curated_protocol_session._experiment_started_at is None)
                await websocket.send_text(event(
                    "workflow.control.refused",action="resume",
                    configuration_id=session.accepted_configuration_id,
                    generation=session.generation,
                    reason="experiment_not_started" if not_started else "stale_configuration",
                    message_ko=("아직 실험을 시작하지 않았어요. 시작 화면에서 프로토콜로 시작해 주세요."
                                if not_started else "현재 연결의 설정으로 다시 요청해 주세요.")))
            elif control["type"] in {"workflow.pause","workflow.resume"} and (
                session.active and session.curated_protocol_session is not None
                and session.curated_protocol_session.experiment_ended
            ):
                # Decision 2 (lane R3): an ended experiment takes no bench
                # pause or resume. The pause used to succeed in the session,
                # be refused by the durable record, and be rolled back through
                # resume_workflow() -- which ran the experiment again from
                # step 1. Nothing changes; the screen is told why.
                await websocket.send_text(event(
                    "workflow.control.refused",
                    configuration_id=session.accepted_configuration_id,
                    action=control["type"].partition(".")[2],
                    reason="experiment_ended",
                    message_ko="이 실험은 이미 끝났어요. 새 실험은 시작 화면에서 골라 주세요.",
                    status=session.curated_protocol_session.workflow_status,
                ))
            elif control["type"]=="workflow.pause":
                if session.active and session.curated_protocol_session is not None:
                    if task and not task.done():
                        await _finish_all_research_operations(sender,session,"cancelled")
                        task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                    screen_before=history_before(session.curated_protocol_session)
                    changed=session.curated_protocol_session.pause_workflow()
                    if changed and session.experiment_state_version is not None:
                        try:
                            experiment_state=_transition_workspace_experiment(
                                session,action="pause",
                                event_key=(
                                    f"connection-{session.voice_connection_id}-"
                                    f"pause-v{session.experiment_state_version}"
                                ),
                                reason="bench_control",
                            )
                        except WorkspaceError as exc:
                            _record_workspace_metric(
                                category="workflow",metric_name="mutation_failure",
                                dimensions={
                                    "status":"rolled_back",
                                    "reason_code":str(getattr(exc,"code","workspace_error"))[:100],
                                    "event_kind":"session_paused",
                                },
                            )
                            # Only the pause is taken back: resume_workflow()
                            # would also start a protocol that is not running.
                            session.curated_protocol_session.undo_pause()
                            await websocket.send_text(event(
                                "error",message=getattr(
                                    exc,"code","workspace_error")))
                            continue
                        if experiment_state is not None:
                            await websocket.send_text(event(
                                "experiment.session.state",state=experiment_state))
                    if task and not task.done():
                        await _finish_all_research_operations(sender,session,"cancelled")
                        task.cancel()
                    if changed:
                        # Lane HEAR, decision 7: the guidance playing stops with the pause.
                        stopped=session.stop_turn_for_pause()
                        if stopped is not None:
                            await websocket.send_text(event(
                                "playback.stop",configuration_id=session.accepted_configuration_id,
                                turn_id=stopped[0],generation=stopped[1],reason="paused"))
                    _record_screen_history(
                        session,screen_before,control="pause",changed=changed)
                    if changed:
                        _pilot_mark("button_change",session=session,code="pause")
                    fixture_state=session.curated_protocol_session.state()
                    await websocket.send_text(event(
                        "protocol.fixture.state",
                        configuration_id=session.accepted_configuration_id,
                        action="pause",
                        state=fixture_state,
                        screen=curated_screen_fields(session.curated_protocol_session),
                    ))
            elif control["type"]=="workflow.resume":
                if session.active and session.curated_protocol_session is not None:
                    screen_before=history_before(session.curated_protocol_session)
                    changed=session.curated_protocol_session.resume_workflow()
                    if changed and session.experiment_state_version is not None:
                        try:
                            experiment_state=_transition_workspace_experiment(
                                session,action="resume",
                                event_key=(
                                    f"connection-{session.voice_connection_id}-"
                                    f"resume-v{session.experiment_state_version}"
                                ),
                                reason="bench_control",
                            )
                        except WorkspaceError as exc:
                            _record_workspace_metric(
                                category="workflow",metric_name="mutation_failure",
                                dimensions={
                                    "status":"rolled_back",
                                    "reason_code":str(getattr(exc,"code","workspace_error"))[:100],
                                    "event_kind":"session_resumed",
                                },
                            )
                            session.curated_protocol_session.pause_workflow()
                            await websocket.send_text(event(
                                "error",message=getattr(
                                    exc,"code","workspace_error")))
                            continue
                        if experiment_state is not None:
                            await websocket.send_text(event(
                                "experiment.session.state",state=experiment_state))
                    _record_screen_history(
                        session,screen_before,control="resume",changed=changed)
                    if changed:
                        _pilot_mark("button_change",session=session,code="resume")
                    fixture_state=session.curated_protocol_session.state()
                    await websocket.send_text(event(
                        "protocol.fixture.state",
                        configuration_id=session.accepted_configuration_id,
                        action="resume",
                        state=fixture_state,
                        screen=curated_screen_fields(session.curated_protocol_session),
                    ))
            elif control["type"]=="client.audio_constraints":
                requested=control["requested"]
                actual=control["actual"]
                session.client_audio_constraints={
                    "requested":dict(requested),"actual":dict(actual),
                }
                log.info(
                    "client.audio_constraints "
                    "requested_echo_cancellation=%s actual_echo_cancellation=%s "
                    "requested_noise_suppression=%s actual_noise_suppression=%s "
                    "requested_auto_gain_control=%s actual_auto_gain_control=%s",
                    requested["echoCancellation"],actual["echoCancellation"],
                    requested["noiseSuppression"],actual["noiseSuppression"],
                    requested["autoGainControl"],actual["autoGainControl"],
                )
            elif control["type"]=="client.voice_calibration":
                # Lane SP1, decision 1: "목소리 맞추기" on the screen, or its skip.
                valid=bool(
                    session.active and session.accepted_mode=="cascade"
                    and control["configuration_id"]
                    ==session.accepted_configuration_id
                    and control["generation"]==session.generation
                )
                if not valid:
                    log.info(
                        "client.voice_calibration rejected action=%s configuration_id=%s generation=%s",
                        control["action"],control["configuration_id"],control["generation"])
                    continue
                if control["action"]=="start" and session.curated_protocol_session is not None:
                    # Lane HEAR, decision 1: not during a run -- in the settings.
                    log.info("voice calibration refused on screen reason=settings_only")
                    await websocket.send_text(event(
                        "speaker.calibration",configuration_id=session.accepted_configuration_id,
                        generation=session.generation,state="refused",reason="settings_only",
                        words=VOICE_CALIBRATION_IN_RUN_WORDS,
                        sensitivity=session.speaker_sensitivity))
                    continue
                if control["action"]=="skip":
                    skipped=session.calibration_pending
                    session.calibration_pending=False
                    log.info("voice calibration skipped on screen pending=%s",skipped)
                    await websocket.send_text(event(
                        "speaker.calibration",configuration_id=session.accepted_configuration_id,
                        generation=session.generation,state="skipped",
                        sentence=VOICE_CALIBRATION_SENTENCE,
                        sensitivity=session.speaker_sensitivity))
                else:
                    session.track_visual_task(asyncio.create_task(
                        _prompt_voice_calibration(sender,session)))
            elif control["type"]=="client.clarify_choice":
                # Lane FX3, decision 4: a candidate the last reply asked back,
                # tapped instead of said. Taken only if it is one of them and
                # nothing else is under way; then it runs as an utterance's
                # words do -- the same rules, confirmations and state changes.
                curated=session.curated_protocol_session
                chosen_turn_id=None
                if not (
                    session.active and session.accepted_mode=="cascade"
                    and curated is not None
                    and control["configuration_id"]==session.accepted_configuration_id
                    and control["generation"]==session.generation
                ):
                    refusal="stale_session"
                elif (words:=curated.clarify_choice(control["candidate"])) is None:
                    refusal="not_asked"
                elif (
                    (task is not None and not task.done()) or session.calibration_pending
                    or (chosen_turn_id:=session.start_chosen_turn()) is None
                ):
                    refusal="busy"
                else:
                    refusal=None
                if refusal is not None:
                    log.info(
                        "client.clarify_choice refused reason=%s asked_turn_id=%s",
                        refusal,control["turn_id"])
                    await websocket.send_text(event(
                        "clarify.choice.refused",
                        configuration_id=session.accepted_configuration_id,
                        generation=session.generation,turn_id=control["turn_id"],
                        reason=refusal,text=CLARIFY_CHOICE_REFUSED_WORDS[refusal]))
                    continue
                assert chosen_turn_id is not None
                chosen_generation=session.turn_generations[chosen_turn_id]
                log.info(
                    "client.clarify_choice taken turn_id=%s asked_turn_id=%s",
                    chosen_turn_id,control["turn_id"])
                try:
                    PILOT_METRICS.note_input(session.session_id,chosen_turn_id,"candidate")
                except Exception as exc:
                    log.warning("pilot.metrics.failed kind=input error=%s",type(exc).__name__)
                await websocket.send_text(event(
                    "speech.start",turn_id=chosen_turn_id,generation=chosen_generation,
                    state=session.state.value,voiced_frames=0,total_frames=0,
                    prefix_frames_retained=0,duration_ms=0,reason=None,forced=False,
                    input="clarify_choice"))
                progress=session.advance_turn_progress(
                    chosen_turn_id,chosen_generation,"listening")
                if progress is not None:
                    await websocket.send_text(event("turn.state",**progress))
                task=asyncio.create_task(run_turn_safely(
                    websocket,session,b"",chosen_turn_id,0,0,0,
                    accepted_transcription=Transcription(
                        words,session.last_confirmed_language
                        or session.accepted_language),
                    accepted_stt_ms=0))
            elif control["type"]=="client.audio_ready":
                valid=bool(
                    session.active and session.accepted_mode=="cascade"
                    and control["configuration_id"]
                    ==session.accepted_configuration_id
                    and control["generation"]==session.generation
                )
                if not valid:
                    log.info(
                        "client.audio_ready rejected configuration_id=%s generation=%s",
                        control["configuration_id"],control["generation"],
                    )
                    continue
                if session.greeting_audio_ready:
                    log.info(
                        "client.audio_ready duplicate configuration_id=%s generation=%s",
                        control["configuration_id"],control["generation"],
                    )
                    continue
                session.greeting_audio_ready=True
                log.info(
                    "client.audio_ready accepted configuration_id=%s generation=%s sample_rate=%s",
                    control["configuration_id"],control["generation"],
                    control["sample_rate"],
                )
                # Lane HEAR, decision 2: the run's microphone is known now, and
                # with it whether this run filters by level.
                await _apply_stored_voice_filter(
                    sender,session,_audio_ready_mic_id(message["text"]))
                if restored_connection:
                    # Lane NET, decision 4: a reconnection continues the run;
                    # it is not greeted as a new session.
                    log.info("session.greeting skipped reason=connection_restored")
                    continue
                session.track_visual_task(asyncio.create_task(
                    _send_session_greeting(
                        sender,session,
                        language=session.accepted_language or "ko")))
            elif control["type"] in {"experiment.report.get", "experiment.report.status.get"}:
                try:
                    store = session.experiment_report_store
                    if store is None:
                        await websocket.send_text(event(
                            "experiment.report.error",
                            configuration_id=session.accepted_configuration_id,
                            generation=session.generation,
                            code="report_store_unavailable"))
                    else:
                        if session.experiment_report_id is None and session.curated_protocol_session is not None:
                            _open_experiment_report(session, session.curated_protocol_session)
                        report_id = control.get("report_id") or session.experiment_report_id
                        if not report_id:
                            await websocket.send_text(event(
                                "experiment.report.error",
                                configuration_id=session.accepted_configuration_id,
                                generation=session.generation,
                                code="report_id_missing"))
                        else:
                            try:
                                _scope_tenant_resource("experiment_report",report_id)
                                report = store.get_report(report_id)
                            except (KeyError, ValueError, WorkspaceNotFoundError):
                                await websocket.send_text(event(
                                    "experiment.report.error",
                                    configuration_id=session.accepted_configuration_id,
                                    generation=session.generation,
                                    report_id=report_id,
                                    code="report_not_found"))
                            else:
                                public=_public_experiment_report_state(report)
                                public["reports"]=store.list_reports(
                                    session_id=session.session_id)
                                await websocket.send_text(event(
                                    "experiment.report.state",
                                    configuration_id=session.accepted_configuration_id,
                                    generation=session.generation,
                                    session_id=session.session_id,
                                    report=public,
                                ))
                except Exception as err:
                    log.warning("experiment.report.get failed non-fatally: %s", err)
                    await websocket.send_text(event(
                        "experiment.report.error",
                        configuration_id=session.accepted_configuration_id,
                        generation=session.generation,
                        code="report_lookup_failed"))
            elif control["type"]=="playback.ended" and session.playback_ended(control["turn_id"]):
                await _send_playback_terminal(
                    websocket,session,control["turn_id"],
                    cooldown_ms=config.cooldown_ms)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("session failed")
        try: await websocket.send_text(event("error",message=str(exc)))
        except Exception as send_exc:
            log.warning(
                "session.error_event_send_failed error=%s",
                type(send_exc).__name__,
            )
    finally:
        if voice_account is not None and voice_task is not None:
            connections=_ACCOUNT_VOICE_CONNECTIONS.get(voice_account.account_id,{})
            connections.pop(voice_task,None)
            if not connections:
                _ACCOUNT_VOICE_CONNECTIONS.pop(voice_account.account_id,None)
        if account_watch is not None:
            account_watch.cancel()
            await asyncio.gather(account_watch,return_exceptions=True)
        if task and not task.done():
            task.cancel()
            try: await task
            except (asyncio.CancelledError, WebSocketDisconnect): pass
        timer_notices.cancel()
        try: await timer_notices
        except (asyncio.CancelledError, WebSocketDisconnect, Exception): pass
        heartbeats.cancel()
        try: await heartbeats
        except (asyncio.CancelledError, WebSocketDisconnect, Exception): pass
        for notice_task in list(session.timer_notice_tasks|session.timer_notice_playback_tasks):
            notice_task.cancel()
            try: await notice_task
            except (asyncio.CancelledError, WebSocketDisconnect, Exception): pass
        _unsubscribe_translations(session)
        curated_at_close=session.curated_protocol_session
        if (
            session.experiment_report_id is not None and curated_at_close is not None
            and not curated_at_close.experiment_ended
        ):
            # Lane LOG, decision 2: the connection closed mid-experiment.
            _pilot_mark("connection_lost",session=session)
        session.stop()
        if workspace_context_token is not None:
            _REQUEST_PRINCIPAL.reset(workspace_context_token)
        if account_context_token is not None:
            _REQUEST_ACCOUNT.reset(account_context_token)

# --- The new screen (lane RX0, human decision 2026-10-24) --------------------
# The React screen (web/) is built into static/app/ (git-ignored) and served
# at /app/ whether or not the setting is on; VOINEY_LAB_NEW_UI=true sends /
# there (the query string kept, so an invite link still opens the signup).
# The current screen stays at /index.html and, with the setting off, at /.
# A missing build never stops the server: /app/ says so and / stays the
# current screen even when the setting is on. The server reads the setting
# and serves files; the screen's contract (the HTTP API, /ws) is unchanged.
NEW_UI_DIR=STATIC_DIR/"app"
NEW_UI_ENV="VOINEY_LAB_NEW_UI"
NEW_UI_NOT_BUILT_HTML=(
    '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>Voiney Lab</title></head><body style=\"font-family:sans-serif;padding:2rem\">"
    "<p>새 화면이 아직 준비되지 않았어요. 관리자에게 알려 주세요.</p>"
    '<p><a href="/index.html">지금 화면으로 가기</a></p></body></html>'
)


def _new_ui_enabled()->bool:
    return boolean_setting(os.environ,NEW_UI_ENV,default=False)


def _new_ui_index()->Path|None:
    index=NEW_UI_DIR/"index.html"
    return index if index.is_file() else None


def _with_query(path:str,request:Request)->str:
    query=request.url.query
    return f"{path}?{query}" if query else path


@app.get("/",include_in_schema=False)
async def root_screen(request:Request)->Response:
    try:
        new_ui=_new_ui_enabled()
    except ConfigurationError:
        return JSONResponse(status_code=503,content={
            "status":"not_ready","reason":"configuration_invalid","fields":[NEW_UI_ENV]})
    if new_ui and _new_ui_index() is not None and request.query_params.get("legacy")!="1":
        # A Location without a fragment retains the caller's old #/… in browsers.
        return RedirectResponse(url=_with_query("/app/",request),status_code=302,
                                headers={"Cache-Control":"no-store"})
    return FileResponse(STATIC_DIR/"index.html",media_type="text/html",headers={"Cache-Control":"no-cache"})


@app.get("/index.html",include_in_schema=False)
async def legacy_screen_entry(request:Request)->Response:
    """Old bookmarks follow the opt-in switch; ?legacy=1 is the retained view."""
    return await root_screen(request)


@app.get("/manifest.webmanifest",include_in_schema=False)
async def install_manifest()->Response:
    """Existing installations keep their identity while following the selected screen."""
    try:
        new_ui=_new_ui_enabled() and _new_ui_index() is not None
    except ConfigurationError:
        return JSONResponse(status_code=503,content={"reason":"configuration_invalid","fields":[NEW_UI_ENV]})
    path=NEW_UI_DIR/"manifest.webmanifest" if new_ui else STATIC_DIR/"manifest.webmanifest"
    if new_ui and path.is_file():
        manifest=json.loads(path.read_text(encoding="utf-8"))
        manifest["id"]="/"
        manifest["scope"]="/"
        for icon in manifest.get("icons",[]):
            if not str(icon.get("src","")).startswith("/"):
                icon["src"]="/app/"+icon["src"]
        return JSONResponse(content=manifest,media_type="application/manifest+json",headers={"Cache-Control":"no-cache"})
    return FileResponse(STATIC_DIR/"manifest.webmanifest",media_type="application/manifest+json",headers={"Cache-Control":"no-cache"})


@app.get("/app",include_in_schema=False)
async def new_screen_without_slash(request:Request)->Response:
    return RedirectResponse(url=_with_query("/app/",request),status_code=307)


@app.get("/app/",include_in_schema=False)
async def new_screen()->Response:
    index=_new_ui_index()
    if index is None:
        return HTMLResponse(NEW_UI_NOT_BUILT_HTML,status_code=503)
    # The page names its hashed assets; a new build must be seen at once.
    return FileResponse(index,media_type="text/html",headers={"Cache-Control":"no-cache"})


def _new_ui_file(folder:str,name:str)->Path:
    """One file (one path segment) inside a folder of the build; anything that resolves outside it is 404."""

    root=(NEW_UI_DIR/folder).resolve()
    path=(root/name).resolve()
    if not path.is_relative_to(root) or path==root or not path.is_file():
        raise HTTPException(status_code=404,detail="not_found")
    return path


@app.get("/app/assets/{name}",include_in_schema=False)
async def new_screen_asset(name:str)->Response:
    """One built file (hashed name) from the build folder, cacheable for good."""

    return FileResponse(_new_ui_file("assets",name),headers={"Cache-Control":"public, max-age=31536000, immutable"})


@app.get("/app/brand/{name}",include_in_schema=False)
async def new_screen_brand(name:str)->Response:
    """A logo or icon from the brand bundle (web/public/brand/, unhashed names): cached for a day, not for good."""

    return FileResponse(_new_ui_file("brand",name),headers={"Cache-Control":"public, max-age=86400"})


@app.get("/app/manifest.webmanifest",include_in_schema=False)
async def new_screen_manifest()->Response:
    """The new screen's web app manifest under its registered type (lane UI2 decision 5), re-read on each visit."""

    return FileResponse(_new_ui_file("","manifest.webmanifest"),media_type="application/manifest+json",headers={"Cache-Control":"no-cache"})


# Lane UI2 decision 5 (2026-10-13): the web app manifest is served under its
# registered type whatever the host's own type table says, so a browser
# reads it as the manifest (Voiney Lab on the home screen; no service worker).
import mimetypes
mimetypes.add_type("application/manifest+json",".webmanifest")
app.mount("/",StaticFiles(directory=STATIC_DIR,html=True),name="static")
