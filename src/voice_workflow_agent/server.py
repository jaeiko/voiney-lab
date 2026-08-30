"""Voice Workflow Agent: hands-free voice cascade with M2 Dispatcher tools."""
from __future__ import annotations
import asyncio, contextvars, copy, hashlib, hmac, json, logging, math, os, re, secrets, sqlite3, stat, tempfile, textwrap, time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI, OpenAI
from voice_workflow_agent.audio import (
    FRAME_BYTES, FrameBuffer, clean_path, pcm16_rms, pcm_to_wav)
from voice_workflow_agent.barge_in import (
    InterruptionGate, InterruptionGateSettings, is_priority_stop_command)
from voice_workflow_agent.source_presentation import (
    TranslationSettings, build_presentation_translator)
from voice_workflow_agent.speaker_attribution import (
    Participant, SessionParticipants, SpeakerDiarizationSettings,
    UNKNOWN_SPEAKER_MESSAGE, diarization_diagnostics, evaluate_speaker_policy,
    transcript_segments)
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
    CascadeSttSettings,
    ConfigurationError,
    VoiceVadSettings,
    cascade_filler_delay_ms,
)
from voice_workflow_agent.cascade_filler import CascadeFiller
from voice_workflow_agent.curated_protocol import (
    ClaimAdmissionStatus,
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
    ReportNarrative,
    ReportWriterBrain,
    ReportWriterSettings,
    new_session_id,
)
from voice_workflow_agent.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
    XaiAuthoritativeWebSearch,
    XaiSupplementalKnowledge,
    plan_research_query,
    supplemental_knowledge_allowed,
)
from voice_workflow_agent.generated_visuals import (
    GENERATED_VISUALS,
    GeneratedVisualSettings,
    VisualSpecification,
    XaiImageGenerator,
)
from voice_workflow_agent.web_visuals import (
    PubChemChemistryAdapter,
    WEB_VISUAL_REGISTRY,
    WebVisualSettings,
    WikimediaVisualAdapter,
    XaiAuthoritativeImageSearch,
)
from voice_workflow_agent.notifications import (
    HandoffContact,
    NotificationProvider,
    NotificationResult,
    SMTPEmailProvider,
    FakeNotificationProvider,
    resolve_handoff_recipient,
)
from voice_workflow_agent.safety_pack import SafetyPack, resolve_safety_pack, unavailable_safety_pack
from voice_workflow_agent.protocol_catalog import (
    ProtocolApprovalError,
    ProtocolAnalysisUnavailableError,
    ProtocolCatalog,
    ProtocolCatalogEntry,
    ProtocolCatalogError,
    ProtocolCatalogNotFoundError,
    ProtocolCatalogUnavailableError,
    ProtocolRegistrationError,
    ProtocolResolutionError,
    SharedSecretApprovalPolicy,
)
from voice_workflow_agent.protocol_ocr import (
    ProtocolOcrError,
    ProtocolOcrProvider,
    ProtocolOcrUnavailableError,
)
from voice_workflow_agent.document_store import CATALOG_SCHEMA_VERSION
from voice_workflow_agent.emergency import recognize_emergency
from voice_workflow_agent.language import (
    CLARIFICATION_TEXT, InputLanguagePreference, ServerVoicePolicy,
    Transcription, classify_input_event, classify_transcription_language,
    clean_speech_text,
    normalize_input_language_preference,
    normalize_provider_language,
    resolve_turn_language, transcription_quality_issue,
)
from voice_workflow_agent.intent_arbitration import arbitrate_request
from voice_workflow_agent.runtime_metrics import RUNTIME_METRICS
from voice_workflow_agent.moss_retrieval import (
    get_moss_runtime,
    start_moss_runtime_from_environment,
    stop_moss_runtime,
)
from voice_workflow_agent.multi_brain import (
    AnswerBrainOutput,
    BrainClaim,
    BrainFact,
    BrainSnapshot,
    HybridMultiBrain,
    MultiBrainSettings,
    SourceBrainOutput,
    VisualBrainOutput,
    activation_for,
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
from voice_workflow_agent.runtime_routing import route_curated_runtime_turn
from voice_workflow_agent.vad import EndpointDetector, EndpointResult, TurnState, VadConfig
from voice_workflow_agent.identity import (
    AuthenticationRequiredError,
    AuthorizationDeniedError,
    DevIdentityProvider,
    IdentityConfigurationError,
    IdentityResolver,
    OidcSettings,
    Permission,
    Principal,
    Role,
    permissions_for_roles,
    require_permission,
)
from voice_workflow_agent.protocol_sources import (
    GitHubConnector,
    GoogleDriveConnector,
    ProtocolSourceHub,
    ProtocolsIoConnector,
    SourceSnapshot,
    SourceConnectorError,
    normalize_protocols_io_identifier,
    verify_github_webhook_signature,
)
from voice_workflow_agent.drylab_workflows import (
    DryLabWorkflowRegistry,
    inspect_nextflow_snapshot,
    inspect_snakemake_snapshot,
)
from voice_workflow_agent.eln_connectors import (
    CompletedStep,
    ELabFtwConnector,
    ElnConnectorError,
    ExperimentWriteback,
    Observation as ElnObservation,
)
from voice_workflow_agent.workspace_store import (
    ApprovalReplayError,
    TranslationIntegrityError,
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceSettings,
    initialize_workspace_store,
)

PROJECT_ROOT=Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
log=logging.getLogger("voice_workflow_agent")


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


@app.get("/healthz")
async def healthz()->dict[str,object]:
    """Liveness only: the process is up and can serve a request."""
    return {"status":"ok"}


@app.get("/readyz")
async def readyz()->JSONResponse:
    """Readiness: required configuration parses without exposing secrets.

    Optional providers (moss, protocol analysis) report their configured
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
        capabilities["identity_mode"]=(
            "oidc" if identity.oidc_settings is not None else "development"
        )
        capabilities["workspace_enabled"]=workspace.enabled
        capabilities["protocol_catalog_enabled"]=protocol.enabled
        capabilities["experiment_reports_enabled"]=reports.enabled
        capabilities["moss_enabled"]=get_moss_runtime() is not None
    except Exception as exc:
        return JSONResponse(status_code=503,content={
            "status":"not_ready",
            "reason":type(exc).__name__,
        })
    return JSONResponse(status_code=200,content={
        "status":"ok",
        "capabilities":capabilities,
    })


STATIC_DIR=Path(__file__).with_name("static")
_PROTOCOL_ANALYSIS_TASKS:dict[str,asyncio.Task[None]]={}
_PROTOCOL_OCR_TASKS:dict[str,asyncio.Task[None]]={}
_REQUEST_PRINCIPAL:contextvars.ContextVar[Principal|None]=contextvars.ContextVar(
    "voice_workflow_request_principal",default=None)


def _runtime_usage_scope()->str:
    return os.environ.get("VOICE_WORKFLOW_AGENT_USAGE_SCOPE","demo").strip() or "demo"


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


def _workspace_http_error(exc:Exception)->HTTPException:
    if isinstance(exc,AuthenticationRequiredError):
        return HTTPException(status_code=401,detail=exc.code)
    if isinstance(exc,AuthorizationDeniedError):
        return HTTPException(status_code=403,detail=exc.code)
    if isinstance(exc,WorkspaceNotFoundError):
        return HTTPException(status_code=404,detail=exc.code)
    if isinstance(exc,(ApprovalReplayError,WorkspaceConflictError)):
        return HTTPException(status_code=409,detail=exc.code)
    if isinstance(exc,(TranslationIntegrityError,SourceConnectorError,ElnConnectorError)):
        return HTTPException(status_code=422,detail=getattr(exc,"code","invalid_request"))
    if isinstance(exc,(IdentityConfigurationError,ConfigurationError)):
        return HTTPException(status_code=503,detail=getattr(exc,"code","configuration_invalid"))
    return HTTPException(
        status_code=400 if isinstance(exc,WorkspaceError) else 500,
        detail=getattr(exc,"code","workspace_error"),
    )


@app.middleware("http")
async def commercial_identity_boundary(request:Request,call_next):
    """Authenticate every API request when the commercial workspace is enabled."""

    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    if request.url.path.startswith("/api/workspace/webhooks/github/"):
        # GitHub authenticates this machine-to-machine boundary with the raw-body
        # HMAC and delivery identifier; it does not carry a user bearer token.
        return await call_next(request)
    try:
        settings=_workspace_settings()
        operational=_runtime_usage_scope()=="operational"
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
        principal=_identity_resolver().resolve(
            request.headers.get("authorization"),
            dev_profile_id=request.headers.get("x-voice-dev-profile"),
        )
        store=initialize_workspace_store(settings)
        try:
            store.bootstrap_principal(principal)
            principal=store.effective_principal(principal)
        finally:
            store.close()
        request.state.principal=principal
        token=_REQUEST_PRINCIPAL.set(principal)
        try:
            return await call_next(request)
        finally:
            _REQUEST_PRINCIPAL.reset(token)
    except Exception as exc:
        error=_workspace_http_error(exc)
        return JSONResponse(status_code=error.status_code,content={"detail":error.detail})


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


def _scope_catalog_resource(
    protocol_id:str,*,bind:bool=False
)->None:
    _scope_tenant_resource("protocol_catalog",protocol_id,bind=bind)


def _scope_tenant_resource(
    resource_type:str,resource_id:str,*,bind:bool=False
)->None:
    settings=_workspace_settings()
    if not settings.enabled:
        return
    principal,store=_commercial_workspace()
    try:
        if bind:
            store.bind_resource(principal,resource_type,resource_id)
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


def _resolve_server_secret(reference:str)->str:
    """Resolve an opaque credential reference through a server-owned env mapping."""

    raw=os.environ.get("VOICE_WORKFLOW_AGENT_SECRET_REFERENCES","").strip()
    try:
        mapping=json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise WorkspaceError("Server secret reference mapping is invalid.") from exc
    variable=mapping.get(reference) if isinstance(mapping,dict) else None
    if not isinstance(variable,str) or re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}",variable) is None:
        raise WorkspaceError("Connector credential is not configured.")
    value=os.environ.get(variable,"")
    if not value:
        raise WorkspaceError("Connector credential is not configured.")
    return value


def _server_credential_options(principal:Principal)->tuple[dict[str,object], ...]:
    """Expose tenant-scoped credential handles, never references or values."""

    raw=os.environ.get("VOICE_WORKFLOW_AGENT_SECRET_REFERENCES","").strip()
    try:
        mapping=json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise WorkspaceError("Server secret reference mapping is invalid.") from exc
    if not isinstance(mapping,dict):
        raise WorkspaceError("Server secret reference mapping is invalid.")
    prefix=f"secret://{principal.organization_id}/"
    options=[]
    for reference,variable in sorted(mapping.items()):
        if (
            not isinstance(reference,str)
            or not reference.startswith(prefix)
            or not isinstance(variable,str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}",variable) is None
        ):
            continue
        suffix=reference.removeprefix(prefix)
        if not suffix or len(suffix)>120:
            continue
        options.append({
            "credential_handle":(
                "credential-"+hashlib.sha256(reference.encode("utf-8")).hexdigest()[:24]
            ),
            "display_name":suffix.replace("-"," ").replace("_"," ").strip().title(),
            "available":bool(os.environ.get(variable,"")),
        })
    return tuple(options)


def _credential_reference_from_handle(principal:Principal,handle:str)->str:
    for option in _server_credential_options(principal):
        if hmac.compare_digest(str(option["credential_handle"]),handle):
            raw=json.loads(os.environ.get("VOICE_WORKFLOW_AGENT_SECRET_REFERENCES","{}"))
            prefix=f"secret://{principal.organization_id}/"
            for reference in raw:
                candidate=("credential-"+hashlib.sha256(
                    reference.encode("utf-8")).hexdigest()[:24]
                    if isinstance(reference,str) and reference.startswith(prefix)
                    else "")
                if candidate and hmac.compare_digest(candidate,handle):
                    return reference
    raise WorkspaceError("Secure connector credential is not available.")


def _connector_configuration_failure(connector:object)->str|None:
    """Validate server-owned credential availability and allowlisted scope syntax."""

    try:
        _resolve_server_secret(str(connector.credential_reference))
        if connector.webhook_secret_reference:
            _resolve_server_secret(str(connector.webhook_secret_reference))
    except WorkspaceError:
        return "credential_unavailable"
    roots=tuple(connector.allowed_roots)
    kind=str(connector.connector_kind)
    if kind=="google_drive":
        folders=[root.removeprefix("folder:") for root in roots if root.startswith("folder:")]
        shared=[root.removeprefix("shared-drive:") for root in roots if root.startswith("shared-drive:")]
        valid=(
            bool(folders)
            and len(shared)<=1
            and len(folders)+len(shared)==len(roots)
            and all(re.fullmatch(r"[A-Za-z0-9_-]{3,200}",value) for value in (*folders,*shared))
        )
    elif kind=="github":
        valid=all(
            re.fullmatch(r"[^/@\s]+/[^/@\s]+@[^:\s]+:[^\s]+",root)
            and ".." not in root
            and "\\" not in root
            for root in roots
        )
    elif kind=="elabftw":
        parsed=urlparse(roots[0]) if len(roots)==1 else None
        valid=bool(
            parsed
            and parsed.scheme=="https"
            and parsed.netloc
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    elif kind=="protocols_io":
        valid=all(
            root.startswith("protocol:")
            and len(root)>len("protocol:")
            and len(root)<=300
            for root in roots
        )
    else:
        valid=False
    return None if valid else "scope_invalid"


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


def _start_or_resume_workspace_experiment(
    session:ListenerSession,
    *,
    protocol_id:str,
    protocol_revision_id:str,
    recovery_session_id:str|None=None,
    recovery_version:int|None=None,
)->dict[str,object]|None:
    """Bind a voice connection to one durable, tenant-owned experiment."""

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
        state=store.transition_experiment(
            principal,session.session_id,action=action,
            expected_version=session.experiment_state_version,
            event_key=event_key,reason=reason,
        )
        session.experiment_state_version=int(state["version"])
        return state
    finally:
        store.close()


_HUMAN_CHECKPOINT_OBSERVATIONS={
    "repeat_scheduled":"연구자 확인 · 원문 조건 아직 충족되지 않음",
    "advanced":"연구자 확인 · 원문 조건 충족",
    "continuation_confirmation_required":"연구자 확인 · 원문 조건 아직 충족되지 않음",
    "review_requested":"연구자 요청 · 반복 단계 검토 요청",
    "paused":"연구자 요청 · 반복 단계에서 일시 중지",
}


def _record_workspace_experiment_progress(
    session:ListenerSession,
    curated:CuratedProtocolSession,
    plan,
    *,
    turn_id:int,
    generation:int,
    pre_transition_index:int,
    capture_source:str="voice",
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
    }:
        return None
    if capture_source not in {"voice","manual"}:
        raise WorkspaceError("Experiment progress capture source is invalid.")
    key=f"{capture_source}-{generation}-{turn_id}-{plan.action.value}"
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
    principal,store=_commercial_workspace()
    try:
        state=store.record_experiment_progress(
            principal,session.session_id,
            expected_version=session.experiment_state_version,
            expected_voice_connection_id=session.voice_connection_id,
            event_key=key,
            event_type=(
                # A human-confirmation checkpoint is its own kind of event so an
                # experiment record shows a source-authorized replay as a replay,
                # not as a plain forward step.
                plan.intent_kind
                if isinstance(plan.intent_kind,str)
                and plan.intent_kind.startswith("human_checkpoint_")
                else "protocol_started"
                if plan.action is CuratedProtocolAction.START else
                "step_completed" if plan.reported_completion else "step_advanced"
            ),
            step_id=previous.step_id,
            step_label=previous.source_label,
            next_step_id=current.step_id if current else None,
            next_step_label=current.source_label if current else None,
            mark_completed=bool(
                plan.action is CuratedProtocolAction.NEXT
                and plan.reported_completion
            ),
            payload={
                "authority":"curated_protocol",
                "intent_kind":plan.intent_kind,
                "configuration_id":session.accepted_configuration_id,
                "turn_id":turn_id,
                "generation":generation,
                "capture_source":capture_source,
            },
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


async def _record_human_checkpoint_decision(
    session:ListenerSession,
    curated:CuratedProtocolSession,
    outcome,
    *,
    pre_transition_index:int,
)->None:
    """Persist the researcher's own words and the resulting deterministic move.

    The observation is stored as the researcher's report - it never becomes
    approved protocol knowledge - and the step transition is stored separately as
    a workflow event, so an experiment record shows both what a human said and
    what the server then did.
    """

    settings=_workspace_settings()
    if not settings.enabled or session.experiment_state_version is None:
        return
    if outcome.status=="not_at_checkpoint":
        return
    step=curated.fixture.steps[pre_transition_index]
    current=(
        curated.fixture.steps[curated.current_index]
        if curated.active else None
    )
    label=_HUMAN_CHECKPOINT_OBSERVATIONS.get(outcome.status)
    def persist()->int:
        principal,store=_commercial_workspace()
        try:
            if label is not None:
                store.record_observation(
                principal,session.session_id,
                event_key=(
                    f"checkpoint-{session.voice_connection_id}-"
                    f"{outcome.checkpoint_id}-{outcome.iteration}-{outcome.status}"
                ),
                content=(
                    f"{label} · 원문 기준: "
                    f"{(outcome.condition_source_text or '')[:600]}"
                ),
                category="appearance",
                capture_source="manual",
                protocol_step_id=step.step_id,
                )
            if outcome.state_changed:
                state=store.record_experiment_progress(
                principal,session.session_id,
                expected_version=session.experiment_state_version,
                expected_voice_connection_id=session.voice_connection_id,
                event_key=(
                    f"checkpoint-{session.voice_connection_id}-"
                    f"{outcome.checkpoint_id}-{outcome.iteration}-"
                    f"{outcome.status}-progress"
                ),
                event_type=(
                    "human_checkpoint_confirmed"
                    if outcome.status=="advanced"
                    else "human_checkpoint_repeat_scheduled"
                    if outcome.status=="repeat_scheduled"
                    else f"human_checkpoint_{outcome.status}"
                ),
                step_id=step.step_id,
                step_label=step.source_label,
                next_step_id=current.step_id if current else None,
                next_step_label=current.source_label if current else None,
                mark_completed=outcome.status=="advanced",
                payload={
                    "authority":"researcher_confirmation",
                    "capture_source":"manual",
                    "checkpoint_id":outcome.checkpoint_id,
                    "condition_source_text":(
                        outcome.condition_source_text or "")[:1000],
                    "repeated_step_ids":list(outcome.repeated_step_ids),
                    "confirmed_repetitions":outcome.iteration,
                    "configuration_id":session.accepted_configuration_id,
                },
                )
            else:
                state=store.get_experiment(principal,session.session_id)
            if outcome.workflow_completed:
                store.transition_experiment(
                principal,session.session_id,action="complete",
                expected_version=int(state["version"]),
                event_key=(
                    f"checkpoint-{session.voice_connection_id}-"
                    f"{outcome.checkpoint_id}-completed"
                ),
                reason="all_protocol_steps_completed",
                )
                state=store.get_experiment(principal,session.session_id)
            return int(state["version"])
        finally:
            store.close()

    # Workspace persistence is synchronous SQLite work. Keep it ordered as one
    # unit, but do not block the WebSocket event loop while it runs.
    session.experiment_state_version=await asyncio.to_thread(persist)


async def _confirm_and_persist_human_checkpoint(
    session:ListenerSession,
    curated:CuratedProtocolSession,
    decision:str,
    *,
    pre_transition_index:int,
):
    """Apply one admitted checkpoint answer, rolling back on persistence failure."""

    restore_point=curated._checkpoint()
    outcome=curated.confirm_human_checkpoint(decision)
    try:
        await _record_human_checkpoint_decision(
            session,curated,outcome,
            pre_transition_index=pre_transition_index,
        )
    except Exception:
        curated._restore(restore_point)
        raise
    return outcome


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
    elif plan.observation_predicate in {"positive","negative"}:
        category="appearance"
    else:
        category="other"
    step=curated.fixture.steps[pre_transition_index]
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
        )
        state=store.get_experiment(principal,session.session_id)
        session.experiment_state_version=int(state["version"])
        return state
    finally:
        store.close()

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


@dataclass(frozen=True)
class SttDiagnosticSettings:
    """Opt-in local audio evidence; disabled and non-persistent by default."""

    enabled:bool=False
    directory:Path=PROJECT_ROOT / "data/runtime/stt-diagnostics"
    max_files:int=20

    @classmethod
    def from_environment(cls)->"SttDiagnosticSettings":
        raw=os.environ.get(
            "VOICE_WORKFLOW_AGENT_STT_DIAGNOSTICS_ENABLED","false"
        ).strip().casefold()
        if raw not in {"0","1","false","true","no","yes","off","on"}:
            raise ValueError("STT diagnostic mode must be a boolean")
        enabled=raw in {"1","true","yes","on"}
        configured=os.environ.get(
            "VOICE_WORKFLOW_AGENT_STT_DIAGNOSTIC_DIR","").strip()
        directory=(Path(configured) if configured else cls.directory).resolve()
        runtime_root=(PROJECT_ROOT / "data/runtime").resolve()
        if directory != runtime_root and runtime_root not in directory.parents:
            raise ValueError("STT diagnostic directory must be under data/runtime")
        maximum=int(os.environ.get(
            "VOICE_WORKFLOW_AGENT_STT_DIAGNOSTIC_MAX_FILES","20"))
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
    #: Documented batch field. When true the provider returns per-word speaker
    #: labels, which `speaker_attribution` turns into provider-neutral segments.
    diarize:bool=False

    def __post_init__(self)->None:
        if self.audio_origin not in {"ordinary","barge_in"}:
            raise ValueError("STT audio origin is invalid")
        if not isinstance(self.diarize,bool):
            raise ValueError("STT diarize is invalid")
        if not isinstance(self.vad_threshold,(int,float)) or isinstance(self.vad_threshold,bool):
            raise ValueError("STT vad_threshold is invalid")
        if not 0.0 <= float(self.vad_threshold) <= 1.0:
            raise ValueError("STT vad_threshold is outside bounds")
        if not isinstance(self.filler_words,bool):
            raise ValueError("STT filler_words is invalid")

    def request_policy(self)->dict[str,object]:
        fields=["format"]
        if self.language is not None:
            fields.append("language")
        fields.append("vad_threshold")
        fields.append("filler_words")
        if self.diarize:
            fields.append("diarize")
        fields.extend("keyterm" for _ in self.keyterms)
        fields.append("file")
        return {
            "language":self.language,
            "keyterms":list(self.keyterms),
            "vad_threshold":float(self.vad_threshold),
            "filler_words":bool(self.filler_words),
            "diarize":bool(self.diarize),
            "request_field_order":fields,
            "pending_frame":self.pending_frame,
            "audio_origin":self.audio_origin,
        }


def _stt_multipart(
    pcm:bytes,*,language:str|None,keyterms:tuple[str,...],
    vad_threshold:float=0.5,filler_words:bool=False,diarize:bool=False,
)->tuple[list[tuple[str,tuple]],bytes,tuple[str,...]]:
    """Build the documented xAI multipart order with the file last."""

    if language not in {None,"ko","en","vi"}:
        raise ValueError("STT language is invalid")
    if not isinstance(vad_threshold,(int,float)) or isinstance(vad_threshold,bool):
        raise ValueError("STT vad_threshold is invalid")
    if not isinstance(filler_words,bool):
        raise ValueError("STT filler_words is invalid")
    if not isinstance(diarize,bool):
        raise ValueError("STT diarize is invalid")
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
    # Only sent when enabled, so a default deployment's request body stays
    # byte-identical to the contract the previous pass validated.
    if diarize:
        multipart.append(("diarize",(None,"true")))
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
            "diarization_enabled","diarization_available","segment_count",
            "speaker_label_count","speaker_policy_outcome",
            "speaker_policy_reason","speaker_identity_verified",
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
    diarize:bool=False,
)->Transcription:
    """Call documented batch STT fields while retaining optional extensions."""

    multipart,_,_= _stt_multipart(
        pcm,language=language,keyterms=keyterms,vad_threshold=vad_threshold,
        filler_words=filler_words,diarize=diarize)
    response=requests.post(api_url("stt"),headers={"Authorization":f"Bearer {require_env("XAI_API_KEY")}"},
        files=multipart,timeout=120)
    response.raise_for_status()
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
            if key in {"word","text","start","end","speaker"}
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

    return os.environ.get("TTS_VOICE", "leo").strip() or "leo"

def synthesize(text:str,language:str|None=None)->bytes:
    clean_text = clean_speech_text(text)
    if not clean_text:
        return b""
    voice = _tts_voice()
    response=requests.post(
        api_url("tts"),
        headers={"Authorization":f"Bearer {require_env('XAI_API_KEY')}"},
        json={
            "text":clean_text,
            "voice_id":voice,
            "language":language or "ko",
            "output_format":{"codec":"pcm","sample_rate":16000},
        },
        timeout=120,
    )
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
        "approval":{
            "status":"development_only",
            "final_approval":False,
            "actor_principal_id":None,
            "actor_role":None,
            "recorded_at":None,
            "authority":"development_fixture",
        },
    }


def _catalog_entry_projection(
    catalog:ProtocolCatalog,entry:ProtocolCatalogEntry,
)->dict[str,object]:
    """Project one catalog entry with its existing approval evidence."""

    public=entry.public_dict()
    public["approval"]=catalog.approval_context(entry.protocol_id)
    return public


def _public_protocol_catalog_entries(
)->tuple[list[dict[str,object]],ProtocolPersistenceSettings,str|None]:
    """Resolve visible catalog entries without analysis or approval side effects.

    The third element is the configured development fixture's protocol_id (or
    None). It is not tenant-owned, so callers that apply per-tenant resource
    visibility must exempt this id the same way get_protocol_catalog_entry
    already does for single-entry lookups.
    """

    config=server_config()
    candidate=_configured_candidate_fixture(config)
    candidate_protocol_id=candidate.protocol_id if candidate is not None else None
    entries=[]
    settings=_protocol_store_settings()
    if not settings.enabled:
        if candidate is not None:
            entries.append(_candidate_catalog_dict(candidate))
        return entries,settings,candidate_protocol_id
    catalog,store=_open_protocol_catalog()
    try:
        candidate_superseded=(
            candidate is not None
            and catalog.development_fixture_is_superseded(candidate)
        )
        if candidate is not None and not candidate_superseded:
            entries.append(_candidate_catalog_dict(candidate))
        for item in catalog.list_entries():
            if candidate is not None and item.protocol_id==candidate.protocol_id:
                if not catalog.development_fixture_is_materialized(candidate):
                    raise ProtocolCatalogUnavailableError(
                        "Configured development fixture conflicts with catalog state."
                    )
                # Once a reviewer has resolved or authorized this protocol, the
                # catalog record supersedes the in-memory development fixture,
                # so the researcher sees the reviewed revision instead.
                if not candidate_superseded:
                    continue
            public=_catalog_entry_projection(catalog,item)
            public["analysis_run"]=catalog.analysis_run_status(
                item.protocol_id).public_dict()
            entries.append(public)
    finally:
        store.close()
    return entries,settings,candidate_protocol_id


def log_protocol_catalog_runtime_configuration()->None:
    """Log only sanitized protocol backend identity and visible entry count."""

    try:
        entries,settings,candidate_protocol_id=(
            _public_protocol_catalog_entries()
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
    if isinstance(exc,ProtocolAnalysisUnavailableError):
        return HTTPException(status_code=503,detail=exc.code)
    if isinstance(exc,ProtocolOcrUnavailableError):
        return HTTPException(status_code=503,detail=exc.code)
    if isinstance(exc,ProtocolOcrError):
        return HTTPException(status_code=422,detail=exc.code)
    if isinstance(exc,ProtocolCatalogNotFoundError):
        return HTTPException(status_code=404,detail=getattr(exc,"code","not_found"))
    if isinstance(exc,ProtocolApprovalError):
        return HTTPException(status_code=403,detail=exc.code)
    if isinstance(exc,ProtocolResolutionError):
        return HTTPException(status_code=422,detail=exc.code)
    if isinstance(exc,ProtocolRegistrationError):
        return HTTPException(status_code=400,detail=exc.code)
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
            routes=["researcher"]
            if any(role.value in {"reviewer","lab_admin","organization_admin"}
                   for role in principal.roles):
                routes.append("reviewer")
            if any(role.value in {"lab_admin","organization_admin"}
                   for role in principal.roles):
                routes.append("admin")
            return {
                "principal_id":principal.principal_id,
                "display_name":principal.display_name,
                "organization_id":principal.organization_id,
                "roles":sorted(role.value for role in principal.roles),
                "workspaces":routes,
                "authentication_method":principal.authentication_method,
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/experiments")
def get_workspace_experiments(active_only:bool=False)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {
                "experiments":list(
                    store.list_experiments(principal,active_only=active_only)
                )
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/experiments/{session_id}")
def get_workspace_experiment(session_id:str)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return store.get_experiment(principal,session_id)
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
            return store.transition_experiment(
                principal,session_id,action=action,
                expected_version=expected_version,
                event_key=str(payload.get("event_key", "")),
                reason=(
                    str(payload["reason"])
                    if payload.get("reason") is not None else None
                ),
            )
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/experiments/{session_id}/timeline")
def get_workspace_experiment_timeline(session_id:str)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return store.experiment_timeline(principal,session_id)
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
    "/api/workspace/reviewer/experiments/{session_id}/actions",
    status_code=201,
)
async def create_workspace_experiment_review_action(
    session_id:str,request:Request
)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            state=store.record_experiment_review_action(
                principal,
                session_id,
                event_key=str(payload.get("idempotency_key", "")),
                action=str(payload.get("action", "")),
                comment=str(payload.get("comment", "")),
            )
            return {
                "session_id":state["session_id"],
                "version":state["version"],
                "recorded":True,
            }
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
            )
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


@app.get("/api/workspace/experiments/{session_id}/evidence/{evidence_id}")
def download_workspace_experiment_evidence(
    session_id:str,evidence_id:str
)->Response:
    try:
        principal,store=_commercial_workspace()
        try:
            evidence=store.evidence_for_download(
                principal,session_id,evidence_id)
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


@app.get("/api/workspace/protocol-adaptations")
def get_workspace_protocol_adaptations()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {
                "adaptations":list(store.list_lab_adaptations(principal))
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/protocol-adaptations/{adapted_revision_id}")
def get_workspace_protocol_adaptation(
    adapted_revision_id:str,
)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return store.lab_adaptation(principal,adapted_revision_id)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post(
    "/api/workspace/protocols/{base_revision_id}/adaptations",
    status_code=201,
)
async def create_workspace_protocol_adaptation(
    base_revision_id:str,request:Request
)->dict[str,object]:
    payload=await _json_object(request)
    raw_changes=payload.get("changes")
    if not isinstance(raw_changes,list):
        raise HTTPException(status_code=400,detail="workspace_error")
    try:
        principal,store=_commercial_workspace()
        try:
            return store.create_lab_adaptation(
                principal,
                base_revision_id=base_revision_id,
                changes=tuple(raw_changes),
                change_summary=str(payload.get("change_summary", "")),
            )
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/protocol-library")
def get_workspace_protocol_library(search:str="")->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            protocols=list(store.protocol_library(principal,search=search))
        finally:
            store.close()
        catalog_protocols={
            str(item["catalog_protocol_id"])
            for item in protocols
            if isinstance(item.get("catalog_protocol_id"),str)
            and item["catalog_protocol_id"]
        }
        if catalog_protocols:
            try:
                catalog,catalog_store=_open_protocol_catalog()
            except (
                ProtocolCatalogError,
                ProtocolConfigurationError,
                ProtocolFeatureDisabledError,
            ):
                for item in protocols:
                    if item.get("catalog_protocol_id") in catalog_protocols:
                        item["executable"]=False
            else:
                try:
                    for item in protocols:
                        protocol_id=item.get("catalog_protocol_id")
                        if protocol_id not in catalog_protocols:
                            continue
                        try:
                            _scope_catalog_resource(str(protocol_id))
                            entry=catalog.get_entry(str(protocol_id))
                        except (HTTPException,ProtocolCatalogError):
                            item["executable"]=False
                            continue
                        item["executable"]=entry.available_for_execution
                        item["catalog_revision_id"]=entry.revision_id
                        item["approval_state"]=entry.approval_status
                        item["risk_state"]=entry.lifecycle_state
                finally:
                    catalog_store.close()
        return {"protocols":protocols}
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.put("/api/workspace/protocol-library/{family_id}/preference")
async def set_workspace_protocol_preference(
    family_id:str,request:Request
)->dict[str,object]:
    payload=await _json_object(request)
    tags=payload.get("tags")
    try:
        principal,store=_commercial_workspace()
        try:
            store.set_protocol_preference(
                principal,family_id,
                favorite=payload.get("favorite") is True,
                tags=(tuple(str(item) for item in tags)
                      if isinstance(tags,list) else ()),
            )
            return {"family_id":family_id,"saved":True}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/admin/memberships")
def get_workspace_memberships()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {
                "memberships":list(store.membership_summaries(principal)),
                "permission_levels":[
                    {
                        "role":role.value,
                        "permissions":list(permissions_for_roles((role,))),
                    }
                    for role in Role
                ],
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.put("/api/workspace/admin/memberships/{target_principal_id}")
async def set_workspace_membership(
    target_principal_id:str,request:Request
)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            return store.set_membership(
                principal,
                target_principal_id=target_principal_id,
                target_subject=str(payload.get("subject", "")),
                display_name=str(payload.get("display_name", "")),
                role=str(payload.get("role", "")),
                active=payload.get("active") is True,
            )
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.put("/api/workspace/admin/retention")
async def update_workspace_retention(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        retention_days=int(payload.get("analytics_retention_days", 0))
    except (TypeError,ValueError) as exc:
        raise HTTPException(status_code=422,detail="retention_invalid") from exc
    try:
        principal,store=_commercial_workspace()
        try:
            return store.update_analytics_retention(
                principal,retention_days=retention_days)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/reviewer/inbox")
def get_workspace_reviewer_inbox()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {"items":list(store.source_inbox(principal))}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/reviewer/revisions/{revision_id}/diff")
def get_workspace_revision_diff(revision_id:str)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            require_permission(principal,Permission.PROTOCOL_REVIEW)
            return store.revision_diff(principal,revision_id)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


def _catalog_execution_readiness(protocol_id:str)->dict[str,object]|None:
    """Read one protocol's execution-readiness projection without changing it."""

    if not _protocol_store_settings().enabled:
        return None
    catalog,store=_open_protocol_catalog()
    try:
        try:
            return catalog.review(protocol_id).get("execution_readiness")
        except ProtocolCatalogError:
            return None
    finally:
        store.close()


def _linked_catalog_protocol_id(store,principal,revision_id:str)->str|None:
    """Resolve the executable catalog protocol behind a workspace revision."""

    try:
        source=store.source_for_revision(principal,revision_id)
    except Exception:
        return None
    linked=source.metadata.get("catalog_protocol_id")
    return linked if isinstance(linked,str) and linked else None


def _apply_catalog_execution_decision(
    principal,protocol_id:str,action:str,comment:str,
)->dict[str,object]|None:
    """Carry one reviewer decision through to execution availability.

    A reviewer should not have to know that governance review and the executable
    catalog are separate stores. When the reviewed revision is linked to a
    catalog protocol, approving it here also authorizes bench execution - and
    only if every readiness gate already passes. Nothing bypasses those gates.
    """

    if not _protocol_store_settings().enabled:
        return None
    role=next(
        (item.value for item in principal.roles
         if item.value in {"reviewer","lab_admin","organization_admin"}),
        None,
    )
    catalog,store=_open_protocol_catalog()
    try:
        try:
            entry=catalog.get_entry(protocol_id)
        except ProtocolCatalogNotFoundError:
            return None
        policy=SharedSecretApprovalPolicy("tenant-rbac-authorized")
        if action=="approved":
            if entry.approval_status=="approved":
                return catalog.review(protocol_id)
            entry=catalog.approve(
                protocol_id,entry.revision_id,
                policy=policy,presented_secret="tenant-rbac-authorized",
                actor_principal_id=principal.principal_id,actor_role=role,
                comment=comment or None,
            )
        elif action=="revoked" and entry.approval_status=="approved":
            entry=catalog.revoke(
                protocol_id,entry.revision_id,
                policy=policy,presented_secret="tenant-rbac-authorized",
                actor_principal_id=principal.principal_id,actor_role=role,
                comment=comment or None,
            )
        else:
            return catalog.review(protocol_id)
        return catalog.review(protocol_id)
    finally:
        store.close()


@app.post("/api/workspace/reviewer/revisions/{revision_id}/decision")
async def decide_workspace_revision(revision_id:str,request:Request)->dict[str,object]:
    payload=await _json_object(request)
    action=str(payload.get("action", ""))
    comment=str(payload.get("comment", ""))
    try:
        principal,store=_commercial_workspace()
        try:
            linked=_linked_catalog_protocol_id(store,principal,revision_id)
            if action=="approved" and linked is not None:
                # Refuse before recording anything, so a reviewer never ends up
                # with "approved" in one store and "unapproved" in the other.
                review=_catalog_execution_readiness(linked)
                if review is not None and not review.get(
                    "can_approve_for_execution"
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="protocol_not_ready_for_execution_approval",
                    )
            event=store.record_approval(
                principal,
                revision_id=revision_id,
                action=action,
                comment=comment,
                idempotency_key=str(payload.get("idempotency_key", "")),
                replacement_revision_id=(
                    str(payload["replacement_revision_id"])
                    if payload.get("replacement_revision_id") else None
                ),
            )
            catalog_review=None
            if linked is not None:
                catalog_review=_apply_catalog_execution_decision(
                    principal,linked,action,comment,
                )
            store.record_analytics(
                principal,
                category="protocol",
                metric_name="review_decision",
                dimensions={"status":event.action,"event_kind":"approval"},
            )
            return {
                "event":event.__dict__,
                "state":store.revision_operational_state(principal,revision_id),
                "execution":(
                    catalog_review.get("execution_readiness")
                    if isinstance(catalog_review,dict) else None
                ),
            }
        finally:
            store.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/reviewer/revisions/{revision_id}/translations")
async def add_workspace_translation(revision_id:str,request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            translation_id=store.add_translation(
                principal,
                revision_id=revision_id,
                language=str(payload.get("language", "")),
                original_text=str(payload.get("original_text", "")),
                translated_text=str(payload.get("translated_text", "")),
                status=str(payload.get("status", "machine")),
            )
            return {"translation_id":translation_id,"label":str(payload.get("status","machine"))}
        finally:
            store.close()
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


@app.post("/api/workspace/reviewer/knowledge/{knowledge_id}/promote")
async def promote_workspace_knowledge(knowledge_id:str,request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            promotion_id=store.promote_knowledge(
                principal,knowledge_id=knowledge_id,
                comment=str(payload.get("comment", "")),
            )
            return {"promotion_id":promotion_id,"effective_kind":"approved_protocol_fact"}
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


@app.post("/api/workspace/admin/assets",status_code=201)
async def create_workspace_asset(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            version_id=store.add_asset_card_version(
                principal,
                asset_id=str(payload.get("asset_id", "")),
                asset_kind=str(payload.get("asset_kind", "")),
                name=str(payload.get("name", "")),
                location=(payload.get("location")
                          if isinstance(payload.get("location"),dict) else {}),
                review_status=str(payload.get("review_status", "draft")),
                photo_url=(str(payload["photo_url"]) if payload.get("photo_url") else None),
                barcode=(str(payload["barcode"]) if payload.get("barcode") else None),
                sds_url=(str(payload["sds_url"]) if payload.get("sds_url") else None),
            )
            return {"version_id":version_id}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/connectors")
def get_workspace_connectors()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {"connectors":list(store.connector_summaries(principal))}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/admin/connector-credentials")
def get_workspace_connector_credentials()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            require_permission(principal,Permission.CONNECTOR_MANAGE)
            return {
                "credentials":list(_server_credential_options(principal)),
                "credential_values_exposed":False,
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/admin/connectors",status_code=201)
async def configure_workspace_connector(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    roots=payload.get("allowed_roots")
    try:
        principal,store=_commercial_workspace()
        try:
            credential_reference=(
                _credential_reference_from_handle(
                    principal,str(payload.get("credential_handle", "")))
                if payload.get("credential_handle")
                else str(payload.get("credential_reference", ""))
            )
            webhook_reference=(
                _credential_reference_from_handle(
                    principal,str(payload.get("webhook_credential_handle", "")))
                if payload.get("webhook_credential_handle")
                else (
                    str(payload["webhook_secret_reference"])
                    if payload.get("webhook_secret_reference") else None
                )
            )
            tenant_prefix=f"secret://{principal.organization_id}/"
            if not credential_reference.startswith(tenant_prefix) or (
                webhook_reference is not None
                and not webhook_reference.startswith(tenant_prefix)
            ):
                raise WorkspaceError("Connector credential is outside the tenant scope.")
            connector=store.configure_connector(
                principal,
                connector_kind=str(payload.get("connector_kind", "")),
                display_name=str(payload.get("display_name", "")),
                credential_reference=credential_reference,
                allowed_roots=(tuple(str(item) for item in roots)
                               if isinstance(roots,list) else ()),
                webhook_secret_reference=webhook_reference,
                enabled=False,
            )
            return {
                "connector_id":connector.connector_id,
                "connector_kind":connector.connector_kind,
                "display_name":connector.display_name,
                "allowed_roots":list(connector.allowed_roots),
                "enabled":connector.enabled,
                "credential_configured":True,
                "validation_status":connector.validation_status,
                "next_action":"test_configuration",
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/admin/connectors/{connector_id}/test")
def test_workspace_connector_configuration(connector_id:str)->dict[str,object]:
    """Check server credential resolution and scope syntax without provider I/O."""

    try:
        principal,store=_commercial_workspace()
        try:
            connector=store.get_connector(principal,connector_id)
            require_permission(principal,Permission.CONNECTOR_MANAGE)
            failure_code=_connector_configuration_failure(connector)
            result=store.record_connector_configuration_test(
                principal,connector_id,
                succeeded=failure_code is None,
                failure_code=failure_code,
            )
            return {
                **result,
                "test_scope":"server_configuration",
                "provider_connection_tested":False,
                "next_action":("enable" if failure_code is None else "fix_configuration"),
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.put("/api/workspace/admin/connectors/{connector_id}/enabled")
async def set_workspace_connector_enabled(
    connector_id:str,request:Request
)->dict[str,object]:
    payload=await _json_object(request)
    if not isinstance(payload.get("enabled"),bool):
        raise HTTPException(status_code=422,detail="connector_state_invalid")
    try:
        principal,store=_commercial_workspace()
        try:
            return store.set_connector_enabled(
                principal,connector_id,enabled=payload["enabled"])
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/eln/elabftw/writeback",status_code=201)
async def write_experiment_to_elabftw(request:Request)->dict[str,object]:
    """Write one exact completed report only after an explicit user confirmation."""

    payload=await _json_object(request)
    if payload.get("confirmed") is not True:
        raise HTTPException(status_code=409,detail="eln_confirmation_required")
    connector_id=str(payload.get("connector_id", ""))
    report_id=str(payload.get("report_id", ""))
    revision_id=str(payload.get("protocol_revision_id", ""))
    idempotency_key=str(payload.get("idempotency_key", ""))
    principal=None
    store=None
    claimed=False
    try:
        principal,store=_commercial_workspace()
        configured=store.connector_for_use(
            principal,connector_id,expected_kind="elabftw")
        revision=store.get_revision(principal,revision_id)
        source=store.source_for_revision(principal,revision_id)
        report_settings=ExperimentReportSettings.from_environment()
        if not report_settings.enabled or report_settings.database_path is None:
            raise WorkspaceNotFoundError("Experiment report is not available.")
        store.require_resource(principal,"experiment_report",report_id)
        report=ExperimentReportStore(report_settings.database_path).get_report(report_id)
        if report.get("status")!="completed" or not report.get("ended_at"):
            raise WorkspaceConflictError(
                "Only a completed experiment report can be written back.")
        experiment_session_id=report.get("session_id")
        if not isinstance(experiment_session_id,str):
            raise WorkspaceConflictError(
                "Experiment report has no durable session identity.")
        experiment=store.get_experiment(principal,experiment_session_id)
        if experiment.get("status")!="completed":
            raise WorkspaceConflictError(
                "Only a completed experiment session can be written back.")
        if (
            experiment.get("protocol_id")!=report.get("protocol_id")
            or experiment.get("protocol_revision_id")
            !=report.get("protocol_revision")
        ):
            raise WorkspaceConflictError(
                "Experiment session and report protocol identities do not match."
            )
        identity=revision.content.get("execution_identity")
        identity_matches=(
            revision.source_hash==report.get("protocol_sha256")
            or (
                isinstance(identity,dict)
                and identity.get("protocol_id")==report.get("protocol_id")
                and identity.get("source_sha256")==report.get("protocol_sha256")
            )
        )
        if not identity_matches:
            raise WorkspaceConflictError(
                "The report and selected protocol lineage revision do not match.")
        bases=tuple(
            root.rstrip("/") for root in configured.allowed_roots
            if root.startswith("https://")
        )
        if len(bases)!=1:
            raise WorkspaceError("eLabFTW connector origin is invalid.")
        completed_steps=[]
        observations=[]
        timer_events=[]
        deviations=[]
        for item in report.get("events",[]):
            if not isinstance(item,dict):
                continue
            event_type=item.get("event_type")
            step_id=str(item.get("step_id") or item.get("step_label") or "unlabeled")
            created_at=str(item.get("created_at") or report["started_at"])
            wording=item.get("user_wording")
            if event_type=="step_completed":
                completed_steps.append(CompletedStep(step_id,created_at,None))
            elif event_type=="observation":
                value=(wording if isinstance(wording,str) and wording.strip()
                       else str((item.get("payload") or {}).get("summary") or "recorded"))
                observations.append(ElnObservation(step_id,created_at,value[:2000]))
            elif event_type=="timer_started":
                timer=(item.get("payload") or {}).get("timer")
                safe_timer={
                    key:value for key,value in (timer.items() if isinstance(timer,dict) else ())
                    if key in {
                        "source_duration_seconds","started_at","elapsed_seconds",
                        "remaining_seconds","completion_state","demo_bypassed",
                    } and isinstance(value,(str,int,float,bool))
                }
                timer_events.append({
                    "event_type":"timer_started","step_id":step_id,
                    "recorded_at":created_at,"timer":safe_timer,
                })
            elif event_type in {"anomaly","blocked"}:
                label=wording if isinstance(wording,str) and wording.strip() else event_type
                deviations.append(f"{step_id}: {label[:2000]}")
        experiment=ExperimentWriteback(
            report_id=report_id,
            protocol_id=str(report["protocol_id"]),
            protocol_revision_id=revision_id,
            protocol_title=str(report["protocol_title"]),
            protocol_version=(source.version_identity or str(revision.revision_number)),
            protocol_source_url=source.canonical_url,
            source_status=str(source.metadata.get("source_status") or "Imported draft"),
            started_at=str(report["started_at"]),
            ended_at=str(report["ended_at"]),
            completed_steps=tuple(completed_steps),
            observations=tuple(observations),
            timer_events=tuple(timer_events),
            deviations=tuple(deviations),
            report_url=None,
        )
        store.claim_eln_writeback_request(
            principal,connector_id=connector_id,
            experiment_session_id=experiment_session_id,report_id=report_id,
            protocol_revision_id=revision_id,idempotency_key=idempotency_key,
        )
        claimed=True
        result=await asyncio.to_thread(
            ELabFtwConnector(
                server_configured_base_url=bases[0],
                api_key=_resolve_server_secret(configured.credential_reference),
            ).write_completed_experiment,
            experiment,confirmed=True,
        )
        writeback_id=store.record_eln_writeback(
            principal,connector_id=connector_id,
            experiment_session_id=experiment_session_id,report_id=report_id,
            protocol_revision_id=revision_id,
            external_experiment_id=result.external_experiment_id,
            request_sha256=result.request_sha256,
            idempotency_key=idempotency_key,
        )
        store.finish_eln_writeback_request(
            principal,idempotency_key,succeeded=True)
        store.record_analytics(
            principal,category="connector",metric_name="eln_writeback",
            dimensions={"connector_kind":"elabftw","status":"ok"},
        )
        return {
            "writeback_id":writeback_id,
            "connector_kind":"elabftw",
            "experiment_session_id":experiment_session_id,
            "external_experiment_id":result.external_experiment_id,
            "location":result.location,
            "raw_audio_transmitted":False,
            "transcript_transmitted":False,
        }
    except Exception as exc:
        if store is not None and principal is not None and claimed:
            try:
                store.finish_eln_writeback_request(
                    principal,idempotency_key,succeeded=False)
            except Exception:
                pass
        raise _workspace_http_error(exc) from exc
    finally:
        if store is not None:
            store.close()


@app.post("/api/workspace/sources/protocols-io/import")
async def import_protocols_io_source(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            connector=store.connector_for_use(
                principal,str(payload.get("connector_id", "")),
                expected_kind="protocols_io",
            )
            selected_identifier=normalize_protocols_io_identifier(
                str(payload.get("identifier", ""))
            )
            allowed_prefixes=tuple(
                root.removeprefix("protocol:").casefold()
                for root in connector.allowed_roots
                if root.startswith("protocol:")
            )
            if not allowed_prefixes or not any(
                selected_identifier.casefold().startswith(prefix)
                for prefix in allowed_prefixes
            ):
                raise AuthorizationDeniedError(
                    "protocols.io source is outside the connector allowlist."
                )
            snapshot=await asyncio.to_thread(
                ProtocolsIoConnector(
                    access_token=_resolve_server_secret(connector.credential_reference)
                ).fetch,
                selected_identifier,
            )
            imported=ProtocolSourceHub(store).ingest(principal,snapshot)
            store.record_analytics(
                principal,category="connector",metric_name="source_import",
                dimensions={"connector_kind":"protocols_io","status":imported.inbox_state},
            )
            return {
                "family_id":imported.family_id,
                "revision_id":imported.revision.revision_id,
                "changed":imported.changed,
                "inbox_state":imported.inbox_state,
                "source":snapshot.metadata,
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/sources/google-drive/sync")
async def sync_google_drive_source(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            configured=store.connector_for_use(
                principal,str(payload.get("connector_id", "")),
                expected_kind="google_drive",
            )
            folder_id=str(payload.get("folder_id", ""))
            folder_roots=tuple(root.removeprefix("folder:") for root in configured.allowed_roots if root.startswith("folder:"))
            shared=next((root.removeprefix("shared-drive:") for root in configured.allowed_roots if root.startswith("shared-drive:")),None)
            connector=GoogleDriveConnector(
                access_token=_resolve_server_secret(configured.credential_reference),
                allowed_folder_ids=folder_roots,
                shared_drive_id=shared,
            )
            cursor=store.connector_cursor(
                principal,configured.connector_id,cursor_kind="drive_changes")
            if cursor is None:
                next_cursor=await asyncio.to_thread(connector.start_page_token)
                snapshots=await asyncio.to_thread(connector.list_snapshots,folder_id)
                sync_mode="initial_snapshot"
            else:
                changed_ids,next_cursor=await asyncio.to_thread(
                    connector.changed_file_ids,cursor)
                snapshots=(
                    await asyncio.to_thread(connector.list_snapshots,folder_id)
                    if changed_ids else ()
                )
                changed_set=frozenset(changed_ids)
                snapshots=tuple(
                    item for item in snapshots
                    if item.metadata.get("drive_file_id") in changed_set
                )
                sync_mode="change_log"
            results=[]
            hub=ProtocolSourceHub(store)
            for snapshot in snapshots:
                imported=hub.ingest(principal,snapshot)
                results.append({
                    "family_id":imported.family_id,
                    "revision_id":imported.revision.revision_id,
                    "changed":imported.changed,
                    "inbox_state":imported.inbox_state,
                })
            store.record_analytics(
                principal,category="connector",metric_name="source_sync",
                metric_value=len(results),dimensions={"connector_kind":"google_drive","status":"ok"},
            )
            store.set_connector_cursor(
                principal,configured.connector_id,
                cursor_kind="drive_changes",opaque_cursor=next_cursor,
            )
            return {
                "imports":results,
                "read_only":True,
                "sync_mode":sync_mode,
                "change_token_persisted":True,
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/sources/github/import")
async def import_github_source(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            configured=store.connector_for_use(
                principal,str(payload.get("connector_id", "")),expected_kind="github"
            )
            repository=str(payload.get("repository", ""))
            ref=str(payload.get("ref", ""))
            path=str(payload.get("path", ""))
            allowed=[]
            for root in configured.allowed_roots:
                match=re.fullmatch(r"([^@]+/[^@]+)@([^:]+):(.+)",root)
                if match:
                    allowed.append(match.groups())
            matching=[item for item in allowed if item[0]==repository and item[1]==ref and path.startswith(item[2].rstrip("/")+"/")]
            if not matching:
                raise AuthorizationDeniedError("GitHub source is outside the connector allowlist.")
            snapshot=await asyncio.to_thread(
                GitHubConnector(
                    installation_token=_resolve_server_secret(configured.credential_reference),
                    allowed_repositories=(repository,),allowed_refs=(ref,),
                    allowed_path_prefixes=tuple(item[2] for item in matching),
                ).fetch,
                repository,ref,path,
            )
            imported=ProtocolSourceHub(store).ingest(principal,snapshot)
            store.record_analytics(
                principal,category="connector",metric_name="source_import",
                dimensions={"connector_kind":"github","status":imported.inbox_state},
            )
            return {
                "family_id":imported.family_id,
                "revision_id":imported.revision.revision_id,
                "changed":imported.changed,
                "inbox_state":imported.inbox_state,
                "source":snapshot.metadata,
                "code_executed":False,
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/webhooks/github/{connector_id}",status_code=202)
async def receive_github_webhook(
    connector_id:str,
    request:Request,
    x_hub_signature_256:str|None=Header(default=None),
    x_github_delivery:str|None=Header(default=None),
    x_github_event:str|None=Header(default=None),
)->dict[str,object]:
    """Verify one GitHub delivery and import allowlisted changed source files."""

    content_length=request.headers.get("content-length")
    if content_length:
        try:
            parsed_content_length=int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400,detail="invalid_content_length") from exc
        if parsed_content_length<0 or parsed_content_length>1_000_000:
            raise HTTPException(status_code=413,detail="webhook_payload_too_large")
    raw=await request.body()
    if not raw or len(raw)>1_000_000:
        raise HTTPException(status_code=413,detail="webhook_payload_invalid")
    if not x_github_delivery or not x_github_event:
        raise HTTPException(status_code=400,detail="webhook_headers_missing")
    store=None
    delivery_started=False
    try:
        settings=_workspace_settings()
        if not settings.enabled:
            raise WorkspaceError("Commercial workspace is disabled.")
        store=initialize_workspace_store(settings)
        organization_id,configured=store.github_webhook_configuration(connector_id)
        webhook_reference=configured.webhook_secret_reference
        assert webhook_reference is not None
        if not verify_github_webhook_signature(
            raw,x_hub_signature_256,_resolve_server_secret(webhook_reference)
        ):
            raise AuthenticationRequiredError("Webhook signature is invalid.")
        body_sha256=hashlib.sha256(raw).hexdigest()
        store.begin_github_webhook_delivery(
            organization_id=organization_id,
            connector_id=connector_id,
            delivery_id=x_github_delivery,
            body_sha256=body_sha256,
            event_name=x_github_event,
        )
        delivery_started=True
        try:
            payload=json.loads(raw)
        except (json.JSONDecodeError,UnicodeDecodeError) as exc:
            raise SourceConnectorError("Webhook payload is invalid JSON.") from exc
        if not isinstance(payload,dict):
            raise SourceConnectorError("Webhook payload must be an object.")
        if x_github_event=="ping":
            store.finish_github_webhook_delivery(
                connector_id,x_github_delivery,succeeded=True)
            return {"accepted":True,"event":"ping","imports":[]}
        if x_github_event!="push":
            store.finish_github_webhook_delivery(
                connector_id,x_github_delivery,succeeded=True)
            return {"accepted":True,"event":x_github_event,"imports":[]}
        repository_value=payload.get("repository")
        repository=(
            repository_value.get("full_name")
            if isinstance(repository_value,dict) else None
        )
        raw_ref=payload.get("ref")
        commit_sha=payload.get("after")
        if (
            not isinstance(repository,str)
            or not isinstance(raw_ref,str)
            or not raw_ref.startswith("refs/heads/")
            or not isinstance(commit_sha,str)
            or re.fullmatch(r"[0-9a-f]{40,64}",commit_sha) is None
        ):
            raise SourceConnectorError("GitHub push identity is invalid.")
        branch=raw_ref.removeprefix("refs/heads/")
        allowed=[]
        for root in configured.allowed_roots:
            match=re.fullmatch(r"([^@]+/[^@]+)@([^:]+):(.+)",root)
            if match and match.group(1)==repository and match.group(2)==branch:
                allowed.append(match.group(3).rstrip("/"))
        if not allowed:
            raise AuthorizationDeniedError("GitHub push is outside the connector allowlist.")
        changed:set[str]=set()
        commits=payload.get("commits")
        for commit in commits if isinstance(commits,list) else []:
            if not isinstance(commit,dict):
                continue
            for key in ("added","modified"):
                values=commit.get(key)
                for value in values if isinstance(values,list) else []:
                    if isinstance(value,str) and len(value)<=1000:
                        changed.add(value)
        selected=tuple(sorted(
            path for path in changed
            if any(path==prefix or path.startswith(prefix+"/") for prefix in allowed)
        ))[:100]
        service=Principal(
            principal_id=("system:github:"+hashlib.sha256(
                f"{organization_id}:{connector_id}".encode()).hexdigest()[:24]),
            subject=f"system:github:{connector_id}",
            organization_id=organization_id,
            display_name="GitHub Source Connector",
            roles=frozenset({Role.RESEARCHER}),
            authentication_method="webhook",
        )
        store.bootstrap_principal(service)
        service=store.effective_principal(service)
        token=_resolve_server_secret(configured.credential_reference)
        hub=ProtocolSourceHub(store)
        imports=[]
        for path in selected:
            prefixes=tuple(
                prefix for prefix in allowed
                if path==prefix or path.startswith(prefix+"/")
            )
            snapshot=await asyncio.to_thread(
                GitHubConnector(
                    installation_token=token,
                    allowed_repositories=(repository,),
                    allowed_refs=(commit_sha,),
                    allowed_path_prefixes=prefixes,
                ).fetch,
                repository,commit_sha,path,
            )
            imported=hub.ingest(service,snapshot)
            imports.append({
                "revision_id":imported.revision.revision_id,
                "changed":imported.changed,
                "inbox_state":imported.inbox_state,
                "path":path,
            })
        store.record_analytics(
            service,category="connector",metric_name="webhook_import",
            metric_value=len(imports),
            dimensions={"connector_kind":"github","status":"ok"},
        )
        store.finish_github_webhook_delivery(
            connector_id,x_github_delivery,succeeded=True)
        return {
            "accepted":True,
            "event":"push",
            "commit_sha":commit_sha,
            "imports":imports,
            "code_executed":False,
        }
    except Exception as exc:
        if store is not None and delivery_started:
            try:
                store.finish_github_webhook_delivery(
                    connector_id,x_github_delivery or "invalid",succeeded=False)
            except Exception:
                pass
        raise _workspace_http_error(exc) from exc
    finally:
        if store is not None:
            store.close()


@app.get("/api/workspace/dry-lab/workflows")
def get_dry_lab_workflows()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {"workflows":list(store.computational_workflows(principal))}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/sources/github/dry-lab/import",status_code=201)
async def import_github_dry_lab_workflow(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            configured=store.connector_for_use(
                principal,str(payload.get("connector_id", "")),expected_kind="github"
            )
            repository=str(payload.get("repository", ""))
            ref=str(payload.get("ref", ""))
            path=str(payload.get("path", ""))
            engine=str(payload.get("engine", "")).casefold()
            allowed=[]
            for root in configured.allowed_roots:
                match=re.fullmatch(r"([^@]+/[^@]+)@([^:]+):(.+)",root)
                if match and match.group(1)==repository and match.group(2)==ref:
                    prefix=match.group(3).rstrip("/")
                    if path==prefix or path.startswith(prefix+"/"):
                        allowed.append(prefix)
            if not allowed:
                raise AuthorizationDeniedError(
                    "GitHub workflow is outside the connector allowlist.")
            snapshot=await asyncio.to_thread(
                GitHubConnector(
                    installation_token=_resolve_server_secret(
                        configured.credential_reference),
                    allowed_repositories=(repository,),allowed_refs=(ref,),
                    allowed_path_prefixes=tuple(allowed),
                ).fetch,
                repository,ref,path,
            )
            if engine=="snakemake":
                metadata=inspect_snakemake_snapshot(snapshot)
            elif engine=="nextflow":
                metadata=inspect_nextflow_snapshot(snapshot)
            else:
                raise WorkspaceError("Dry-lab workflow engine is invalid.")
            imported=DryLabWorkflowRegistry(store).import_metadata(
                principal,snapshot,metadata)
            store.record_analytics(
                principal,category="connector",metric_name="dry_lab_import",
                dimensions={"connector_kind":"github","status":"review_required"},
            )
            return {**imported,"code_executed":False,"metadata_only":True}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/reviewer/dry-lab/{workflow_revision_id}/decision")
async def decide_dry_lab_workflow(
    workflow_revision_id:str,request:Request
)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            return store.review_computational_workflow(
                principal,workflow_revision_id,
                action=str(payload.get("action", "")),
                comment=str(payload.get("comment", "")),
            )
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.post("/api/workspace/dry-lab/links",status_code=201)
async def link_dry_lab_workflow(request:Request)->dict[str,object]:
    payload=await _json_object(request)
    try:
        principal,store=_commercial_workspace()
        try:
            link_id=store.link_wet_dry_workflow(
                principal,
                experiment_session_id=str(payload.get("experiment_session_id", "")),
                protocol_revision_id=str(payload.get("protocol_revision_id", "")),
                workflow_revision_id=str(payload.get("workflow_revision_id", "")),
            )
            return {"link_id":link_id,"execution_started":False}
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/dry-lab/links")
def get_dry_lab_workflow_links(
    experiment_session_id:str,
)->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return {
                "experiment_session_id":experiment_session_id,
                "links":list(store.wet_dry_workflow_links(
                    principal,
                    experiment_session_id=experiment_session_id,
                )),
                "execution_supported":False,
            }
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/admin/analytics")
def get_workspace_analytics()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return store.analytics_summary(principal)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/admin/pilot-metrics")
def get_workspace_pilot_metrics()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            return store.pilot_metrics_summary(principal)
        finally:
            store.close()
    except Exception as exc:
        raise _workspace_http_error(exc) from exc


@app.get("/api/workspace/admin/security")
def get_workspace_admin_security()->dict[str,object]:
    try:
        principal,store=_commercial_workspace()
        try:
            overview=store.admin_security_overview(principal)
            overview["authentication"]={
                "current_method":principal.authentication_method,
                "production_requirement":"oidc",
                "development_identity_operationally_accepted":False,
            }
            return overview
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
        entries,_,candidate_protocol_id=_public_protocol_catalog_entries()
        visible=_visible_catalog_resource_ids()
        if visible is not None:
            entries=[
                item for item in entries
                if item.get("protocol_id") in visible
                or item.get("protocol_id")==candidate_protocol_id
            ]
    except Exception as exc:
        raise _catalog_http_error(exc) from exc
    return {"protocols":entries}


@app.get("/api/protocols/review-queue")
def get_protocol_review_queue()->dict[str,object]:
    """List protocols a reviewer can act on, with one status vocabulary.

    Each row already says what the next action is, so the reviewer never has to
    guess whether an "Approve" button will actually make the protocol runnable.
    """

    try:
        principal=_REQUEST_PRINCIPAL.get()
        if _workspace_settings().enabled:
            if principal is None:
                raise AuthenticationRequiredError("Authentication is required.")
            require_permission(principal,Permission.PROTOCOL_REVIEW)
        entries,settings,candidate_protocol_id=_public_protocol_catalog_entries()
        if not settings.enabled:
            return {"protocols":[]}
        visible=_visible_catalog_resource_ids()
        catalog,store=_open_protocol_catalog()
        try:
            rows=[]
            for item in entries:
                protocol_id=item.get("protocol_id")
                if not isinstance(protocol_id,str):
                    continue
                if (
                    visible is not None
                    and protocol_id not in visible
                    and protocol_id!=candidate_protocol_id
                ):
                    continue
                try:
                    review=catalog.review(protocol_id)
                except ProtocolCatalogError:
                    continue
                readiness=review.get("execution_readiness") or {}
                rows.append({
                    "protocol_id":protocol_id,
                    "title":review.get("title"),
                    "revision_id":review.get("revision_id"),
                    "source_filename":review.get("source_filename"),
                    "step_count":review.get("step_count"),
                    "analysis_available":review.get("analysis_available"),
                    "execution_readiness":readiness,
                    "display_labels":review.get("display_labels"),
                    "human_checkpoint_count":len(
                        review.get("human_checkpoints") or []),
                    "needs_resolution_count":len(
                        review.get("needs_resolution") or []),
                })
        finally:
            store.close()
        return {"protocols":rows}
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}")
def get_protocol_catalog_entry(protocol_id:str)->dict[str,object]:
    try:
        _scope_catalog_resource(protocol_id)
        config=server_config()
        candidate=_configured_candidate_fixture(config)
        if (
            candidate is not None
            and candidate.protocol_id==protocol_id
            and not _protocol_store_settings().enabled
        ):
            return _candidate_catalog_dict(candidate)
        catalog,store=_open_protocol_catalog()
        try:
            if (
                candidate is not None
                and candidate.protocol_id==protocol_id
                and not catalog.development_fixture_is_superseded(candidate)
            ):
                return _candidate_catalog_dict(candidate)
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
                _scope_catalog_resource(result.entry.protocol_id,bind=True)
                if _workspace_settings().enabled:
                    principal,workspace=_commercial_workspace()
                    try:
                        ProtocolSourceHub(workspace).ingest(
                            principal,
                            SourceSnapshot(
                                connector_kind="local_pdf",
                                external_id=f"upload:{result.entry.source_filename}",
                                version_identity=result.entry.source_sha256,
                                source_hash=result.entry.source_sha256,
                                canonical_url=None,
                                title=result.entry.title,
                                metadata={
                                    "source_status":"Uploaded draft",
                                    "risk_state":"review_required",
                                    "owner":principal.display_name,
                                    "catalog_protocol_id":result.entry.protocol_id,
                                },
                                content={
                                    "document":{
                                        "format":"pdf",
                                        "sha256":result.entry.source_sha256,
                                        "analysis_state":result.entry.analysis_status,
                                    },
                                    "execution_identity":{
                                        "protocol_id":result.entry.protocol_id,
                                        "source_sha256":result.entry.source_sha256,
                                        "catalog_revision_id":result.entry.revision_id,
                                    },
                                },
                            ),
                        )
                    finally:
                        workspace.close()
                _record_workspace_metric(
                    category="protocol",metric_name="upload",
                    dimensions={"source_kind":"local_pdf","status":"stored"},
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


def _auto_activate_ready_uploads_enabled() -> bool:
    scope = (
        os.environ.get("VOICE_WORKFLOW_AGENT_USAGE_SCOPE", "")
        or os.environ.get("VOICE_WORKFLOW_AGENT_SAFETY_USAGE_SCOPE", "")
    ).strip().casefold()
    if scope == "operational":
        return False  # NEVER silently bypass human/facility approval in operational mode
    raw = os.environ.get("VOICE_WORKFLOW_AGENT_AUTO_ACTIVATE_READY_UPLOADS", "false").strip().casefold()
    return raw in ("1", "true", "yes", "on")


def _development_activation_allowed() -> bool:
    """Fail closed outside an explicitly non-operational runtime scope."""

    scope = (
        os.environ.get("VOICE_WORKFLOW_AGENT_USAGE_SCOPE", "")
        or os.environ.get("VOICE_WORKFLOW_AGENT_SAFETY_USAGE_SCOPE", "")
    ).strip().casefold()
    return scope in {"demo", "reference_only", "test_only"}


@app.post("/api/protocols/{protocol_id}/ocr",status_code=202)
async def trigger_protocol_ocr(protocol_id:str)->dict[str,object]:
    """Run one trusted OCR adapter outside the voice path and await review."""

    try:
        _scope_catalog_resource(protocol_id)
        provider=_protocol_ocr_provider()
        running=_PROTOCOL_OCR_TASKS.get(protocol_id)
        if running is not None and not running.done():
            catalog,store=_open_protocol_catalog()
            try:
                current=catalog.ocr_status(protocol_id,include_text=False)
            finally:
                store.close()
            current["request_deduplicated"]=True
            if current.get("state")=="ocr_required":
                current["state"]="queued"
            return current
        catalog,store=_open_protocol_catalog()
        try:
            current=catalog.ocr_status(protocol_id,include_text=False)
        finally:
            store.close()
        if current.get("state") in {
            "in_progress","review_required","accepted_for_analysis",
        }:
            current["request_deduplicated"]=True
            return current
        if current.get("state")=="not_required":
            raise ProtocolCatalogError("Protocol PDF does not require OCR.")
        ocr_id=f"ocr-{secrets.token_hex(16)}"

        def run_ocr()->None:
            catalog,store=_open_protocol_catalog()
            try:
                catalog.run_ocr(
                    protocol_id,provider,ocr_id=ocr_id
                )
            finally:
                store.close()

        async def background_worker()->None:
            try:
                await asyncio.to_thread(run_ocr)
            except Exception as exc:
                log.warning(
                    "protocol.ocr.failed protocol_id=%s error=%s",
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
        return {
            **current,
            "ocr_id":ocr_id,
            "state":"queued",
            "request_accepted":True,
            "review_required":True,
            "executable":False,
        }
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


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


@app.post("/api/protocols/{protocol_id}/ocr/review")
async def review_protocol_ocr(
    protocol_id:str,
    request:Request,
    x_protocol_approval_token:str|None=Header(default=None),
)->dict[str,object]:
    payload=await _json_object(request)
    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            actor=None
            role=None
            if _workspace_settings().enabled:
                actor=_REQUEST_PRINCIPAL.get()
                if actor is None:
                    raise AuthenticationRequiredError(
                        "Authentication is required."
                    )
                require_permission(actor,Permission.PROTOCOL_REVIEW)
                role=next(
                    item.value for item in actor.roles
                    if item.value in {
                        "reviewer","lab_admin","organization_admin",
                    }
                )
                policy=SharedSecretApprovalPolicy("tenant-rbac-authorized")
                presented="tenant-rbac-authorized"
            else:
                policy=SharedSecretApprovalPolicy(
                    os.environ.get(
                        "VOICE_WORKFLOW_AGENT_PROTOCOL_APPROVAL_TOKEN"
                    )
                )
                presented=x_protocol_approval_token
            ocr=catalog.review_ocr(
                protocol_id,
                decision=str(payload.get("decision", "")),
                policy=policy,
                presented_secret=presented,
                actor_principal_id=(actor.principal_id if actor else None),
                actor_role=role,
                comment=str(
                    payload.get(
                        "comment",
                        "OCR page text reviewed against the source PDF.",
                    )
                ),
            )
            return {
                "ocr":ocr,
                "protocol":catalog.get_entry(protocol_id).public_dict(),
                "structured_analysis_started":False,
                "executable":False,
            }
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/analysis",status_code=202)
async def trigger_protocol_analysis(
    protocol_id:str,background:bool=True
)->dict[str,object]:
    """Persist a request, then run provider analysis outside the request path."""

    _scope_catalog_resource(protocol_id)
    analysis_id=f"analysis-{secrets.token_hex(16)}"
    metric_principal=_REQUEST_PRINCIPAL.get()

    def prepare_analysis()->dict[str,object]:
        catalog,store=_open_protocol_catalog()
        try:
            entry=catalog.request_analysis(protocol_id,analysis_id)
            public=entry.public_dict()
            public["analysis_run"]=catalog.analysis_run_status(
                protocol_id).public_dict()
            return public
        finally:
            store.close()

    def run_explicit_analysis(*,request_first:bool)->dict[str,object]:
        # SQLite connections are thread-affine.  Construct and close the
        # catalog in the same worker that performs bounded Provider work.
        catalog,store=_open_protocol_catalog()
        try:
            if request_first:
                catalog.request_analysis(protocol_id,analysis_id)
            try:
                model=_protocol_analysis_model()
            except RuntimeError as exc:
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
            if _auto_activate_ready_uploads_enabled():
                try:
                    rev = catalog._latest_protocol_revision(protocol_id)
                    analysis = catalog._latest_analysis(rev)
                    if analysis is not None and analysis.readiness.status.value == "guidance_ready":
                        entry = catalog.activate_development(protocol_id)
                except Exception as auto_exc:
                    log.warning("Auto-activation skipped for %s: %s", protocol_id, auto_exc)
            public=entry.public_dict()
            public["analysis_run"]=catalog.analysis_run_status(
                protocol_id).public_dict()
            return public
        finally:
            store.close()

    async def background_worker()->None:
        try:
            completed=await asyncio.to_thread(
                run_explicit_analysis,request_first=False)
            _record_workspace_metric(
                category="protocol",metric_name="analysis",
                dimensions={
                    "status":str(completed.get("analysis_status") or "complete")[:100],
                    "source_kind":"local_pdf",
                },
                principal=metric_principal,
            )
        except Exception as exc:
            # The catalog persists bounded failure codes.  Provider responses,
            # prompts, and source text never enter logs or the lifecycle record.
            log.warning(
                "protocol.analysis.background_failed protocol_id=%s error=%s",
                protocol_id,type(exc).__name__,
            )
            _record_workspace_metric(
                category="protocol",metric_name="analysis",
                dimensions={
                    "status":"failed",
                    "reason_code":str(getattr(exc,"code","analysis_failed"))[:100],
                    "source_kind":"local_pdf",
                },
                principal=metric_principal,
            )

    try:
        if not background:
            completed=await asyncio.to_thread(
                run_explicit_analysis,request_first=True)
            _record_workspace_metric(
                category="protocol",metric_name="analysis",
                dimensions={
                    "status":str(completed.get("analysis_status") or "complete")[:100],
                    "source_kind":"local_pdf",
                },
                principal=metric_principal,
            )
            return completed
        running=_PROTOCOL_ANALYSIS_TASKS.get(protocol_id)
        if running is not None and not running.done():
            catalog,store=_open_protocol_catalog()
            try:
                public=catalog.get_entry(protocol_id).public_dict()
                public["analysis_run"]=catalog.analysis_run_status(
                    protocol_id).public_dict()
                public["analysis_request_deduplicated"]=True
                return public
            finally:
                store.close()
        public=await asyncio.to_thread(prepare_analysis)
        state=(public.get("analysis_run") or {}).get("state")
        if state in {"review_required","approved","revoked"}:
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
        return public
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}/analysis/status")
def get_protocol_analysis_status(protocol_id:str)->dict[str,object]:
    """Read persisted lifecycle state without starting or resuming analysis."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            return catalog.analysis_run_status(protocol_id).public_dict()
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.get("/api/protocols/{protocol_id}/review")
def get_protocol_review(protocol_id: str) -> dict[str, object]:
    """Expose the source-linked analysis draft without approving or activating it."""

    try:
        _scope_catalog_resource(protocol_id)
        catalog, store = _open_protocol_catalog()
        try:
            review = catalog.review(protocol_id)
            readiness = review.get("readiness")
            review["development_activation_allowed"] = bool(
                _development_activation_allowed()
                and review.get("analysis_available") is True
                and isinstance(readiness, dict)
                and readiness.get("status") == "guidance_ready"
                and review.get("available_for_execution") is not True
            )
            return review
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


def _protocol_decision_authorization()->tuple[object|None,str|None,object,str|None]:
    """Resolve one reviewer identity and approval policy for catalog decisions."""

    if _workspace_settings().enabled:
        actor=_REQUEST_PRINCIPAL.get()
        if actor is None:
            raise AuthenticationRequiredError("Authentication is required.")
        require_permission(actor,Permission.PROTOCOL_APPROVE)
        role=next(
            item.value for item in actor.roles
            if item.value in {"reviewer","lab_admin","organization_admin"}
        )
        return (
            actor,role,
            SharedSecretApprovalPolicy("tenant-rbac-authorized"),
            "tenant-rbac-authorized",
        )
    return (
        None,None,
        SharedSecretApprovalPolicy(
            os.environ.get("VOICE_WORKFLOW_AGENT_PROTOCOL_APPROVAL_TOKEN")),
        None,
    )


@app.post("/api/protocols/{protocol_id}/resolutions",status_code=201)
async def resolve_protocol_source_ambiguity(
    protocol_id:str,request:Request
)->dict[str,object]:
    """Record a reviewer's explicit reading of an ambiguous source sentence.

    The original source and every earlier analysis revision stay exactly as they
    were; this appends a new reviewed revision that carries the reviewer's own
    words, actor, role, and time.
    """

    payload=await _json_object(request)
    raw_steps=payload.get("repeated_step_ids") or []
    if not isinstance(raw_steps,list) or any(
        not isinstance(item,str) for item in raw_steps
    ):
        raise HTTPException(status_code=400,detail="invalid_repeat_range")
    try:
        _scope_catalog_resource(protocol_id)
        actor=_REQUEST_PRINCIPAL.get()
        if _workspace_settings().enabled:
            if actor is None:
                raise AuthenticationRequiredError("Authentication is required.")
            require_permission(actor,Permission.PROTOCOL_REVIEW)
            actor_principal_id=actor.principal_id
            actor_role=next(
                item.value for item in actor.roles
                if item.value in {"reviewer","lab_admin","organization_admin"}
            )
        else:
            actor_principal_id=str(payload.get("actor_principal_id") or "").strip()
            actor_role=str(payload.get("actor_role") or "reviewer").strip()
            if not actor_principal_id:
                raise HTTPException(status_code=400,detail="actor_required")
        catalog,store=_open_protocol_catalog()
        try:
            entry=catalog.resolve_source_ambiguity(
                protocol_id,
                ambiguity_id=str(payload.get("issue_id") or ""),
                interpretation=str(payload.get("interpretation") or ""),
                rationale=str(payload.get("rationale") or ""),
                actor_principal_id=actor_principal_id,
                actor_role=actor_role,
                repeated_step_ids=tuple(raw_steps),
            )
            _record_workspace_metric(
                category="protocol",metric_name="source_resolution",
                dimensions={"status":entry.readiness_status,
                            "event_kind":"reviewer_resolution"},
            )
            return catalog.review(protocol_id)
        finally:
            store.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/revisions/{revision_id}/revoke")
async def revoke_protocol_execution_approval(
    protocol_id:str,revision_id:str,request:Request,
    x_protocol_approval_token:str|None=Header(default=None),
)->dict[str,object]:
    """Withdraw execution authorization while preserving the decision history."""

    payload=await _json_object(request)
    try:
        _scope_catalog_resource(protocol_id)
        actor,role,policy,presented=_protocol_decision_authorization()
        if presented is None:
            presented=x_protocol_approval_token
        catalog,store=_open_protocol_catalog()
        try:
            entry=catalog.revoke(
                protocol_id,revision_id,
                policy=policy,presented_secret=presented,
                actor_principal_id=actor.principal_id if actor else None,
                actor_role=role,
                comment=str(payload.get("comment") or "") or None,
            )
            return _catalog_entry_projection(catalog,entry)
        finally:
            store.close()
    except HTTPException:
        raise
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
        _scope_catalog_resource(protocol_id)
        catalog,store=_open_protocol_catalog()
        try:
            actor=None
            role=None
            if _workspace_settings().enabled:
                actor=_REQUEST_PRINCIPAL.get()
                if actor is None:
                    raise AuthenticationRequiredError("Authentication is required.")
                require_permission(actor,Permission.PROTOCOL_APPROVE)
                role=next(
                    item.value for item in actor.roles
                    if item.value in {"reviewer","lab_admin","organization_admin"}
                )
                policy=SharedSecretApprovalPolicy("tenant-rbac-authorized")
                presented="tenant-rbac-authorized"
            else:
                policy=SharedSecretApprovalPolicy(
                    os.environ.get("VOICE_WORKFLOW_AGENT_PROTOCOL_APPROVAL_TOKEN"))
                presented=x_protocol_approval_token
            entry=catalog.approve(
                protocol_id,
                revision_id,
                policy=policy,
                presented_secret=presented,
                actor_principal_id=actor.principal_id if actor else None,
                actor_role=role,
            )
            return _catalog_entry_projection(catalog,entry)
        finally:
            store.close()
    except Exception as exc:
        raise _catalog_http_error(exc) from exc


@app.post("/api/protocols/{protocol_id}/activate-development")
def activate_protocol_for_development(protocol_id: str) -> dict[str, object]:
    """Explicit developer action promoting an analyzed protocol draft to active development execution."""
    if not _development_activation_allowed():
        raise HTTPException(
            status_code=403,
            detail="development_activation_not_allowed",
        )
    try:
        _scope_catalog_resource(protocol_id)
        catalog, store = _open_protocol_catalog()
        try:
            entry = catalog.activate_development(protocol_id)
            return {
                "protocol_id": protocol_id,
                "status": "active_development",
                "development_only": True,
                "available_for_execution": entry.available_for_execution,
                "message": "Protocol draft activated for development session.",
            }
        finally:
            store.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise _catalog_http_error(exc) from exc



@app.get("/api/protocols/{protocol_id}/revisions/{revision_id}/assets/{asset_id}")
def get_protocol_visual_asset(
    protocol_id:str,revision_id:str,asset_id:str,
):
    try:
        _scope_catalog_resource(protocol_id)
        config=server_config()
        candidate=_configured_candidate_fixture(config)
        if (
            candidate is not None
            and candidate.protocol_id==protocol_id
            and candidate.revision_id==revision_id
        ):
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

    _scope_tenant_resource("generated_visual",asset_id)
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


@app.get("/api/web-visuals/{asset_id}")
def get_web_visual_asset(asset_id:str):
    """Serve one validated proxied web image through an opaque same-origin ID."""

    _scope_tenant_resource("web_visual",asset_id)
    asset=WEB_VISUAL_REGISTRY.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404,detail="Web visual is unknown.")
    return Response(
        content=asset.content,media_type=asset.mime_type,
        headers={
            "Cache-Control":"private, max-age=3600, immutable",
            "X-Content-Type-Options":"nosniff",
            "Content-Security-Policy":"default-src 'none'; sandbox",
            "Content-Disposition":f'inline; filename="{asset.asset_id}"',
            "X-Web-Visual-SHA256":asset.content_sha256,
            "X-Protocol-Visual-Kind":"web_reference_image",
        },
    )


def _require_admin_access(presented_token:str|None)->None:
    """Fail closed without retaining or logging the presented credential."""

    configured=os.environ.get("VOICE_WORKFLOW_AGENT_ADMIN_TOKEN","").strip()
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

    if _workspace_settings().enabled:
        try:
            principal,workspace=_commercial_workspace()
            try:
                return {
                    "workspace":workspace.analytics_summary(principal),
                    "legacy_global_metrics_disabled":True,
                }
            finally:
                workspace.close()
        except Exception as exc:
            raise _workspace_http_error(exc) from exc
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
            "provider":"xai",
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


@app.get("/api/experiment-reports/{report_id}.{format_name}")
def export_experiment_report(report_id:str,format_name:str):
    """Export one configured report without exposing its database location."""
    _scope_tenant_resource("experiment_report",report_id)

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
        elif format_name=="csv":
            content=store.export_csv(report_id)
            media_type="text/csv; charset=utf-8"
        elif format_name=="docx":
            narrative = None
            try:
                writer_settings = ReportWriterSettings.from_environment()
                if writer_settings.enabled:
                    api_key = os.environ.get("XAI_API_KEY", "").strip()
                    if api_key:
                        try:
                            async_client = AsyncOpenAI(
                                base_url=api_url(""),
                                api_key=api_key,
                                max_retries=0,
                                timeout=writer_settings.timeout_seconds,
                            )
                            brain = ReportWriterBrain(
                                client=async_client,
                                model=writer_settings.model,
                                timeout_seconds=writer_settings.timeout_seconds,
                            )
                            report_doc = store.get_report(report_id)
                            events = list(report_doc.get("events") or ())
                            narrative = asyncio.run(brain.generate_narrative(report_doc, events))
                        except Exception as llm_exc:
                            log.warning(
                                "Report LLM generation failed (%s), falling back to deterministic narrative",
                                llm_exc,
                            )
                            narrative = None
            except Exception as brain_exc:
                log.warning("Report writer setup failed (%s), using deterministic narrative", brain_exc)
                narrative = None
            content=store.export_docx(report_id, narrative=narrative)
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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
        _scope_catalog_resource(protocol_id)
        config=server_config()
        candidate=_configured_candidate_fixture(config)
        if (
            candidate is not None
            and candidate.protocol_id==protocol_id
            and candidate.revision_id==revision_id
        ):
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

def _seed_experiment_participants(session:ListenerSession)->int:
    """Put the authenticated researcher on this session's participant roster.

    The roster is built from application identity - who actually signed in -
    never from anything the browser asserts and never from the audio. A voice is
    only ever *associated* with one of these people by an explicit human
    confirmation, and that association lives and dies with the session.

    Returns how many participants were seeded, which is zero in development
    mode with no resolved principal. An empty roster deliberately means "no
    confirmed participants", which the speaker policy reads as "make no claim
    about who is speaking" rather than "refuse everyone".
    """

    session.participants.reset()
    principal=_REQUEST_PRINCIPAL.get()
    if principal is None:
        return 0
    identifier=getattr(principal,"principal_id",None) or getattr(
        principal,"subject",None)
    display=(
        getattr(principal,"display_name",None)
        or getattr(principal,"name",None)
        or "연구자")
    role=getattr(getattr(principal,"role",None),"value",None) or "researcher"
    if not isinstance(identifier,str) or not identifier.strip():
        return 0
    try:
        session.participants.enrol(
            Participant(identifier.strip(),str(display)[:120],role))
    except ValueError:
        log.warning("participant roster seed rejected an invalid principal id")
        return 0
    return 1


def _presentation_translation_client(settings:TranslationSettings):
    """Build the model client for presentation translation, or nothing.

    Returns ``None`` whenever the feature is off or no credential is present, so
    a deployment without the flag never constructs a client and a deployment
    with the flag but no key degrades to showing the approved source instead of
    raising on the voice path.
    """

    if not settings.enabled or not os.environ.get("XAI_API_KEY"):
        return None
    try:
        return OpenAI(
            base_url=api_url(""),api_key=require_env("XAI_API_KEY"),
            max_retries=0,timeout=float(settings.timeout_seconds))
    except Exception:
        log.warning("presentation translation client unavailable")
        return None


class ListenerSession:
    def __init__(self,detector:EndpointDetector|None=None,clock:Callable[[],float]=time.perf_counter,
                 tool_context:ToolContext|None=None,
                 curated_protocol_session:CuratedProtocolSession|None=None,
                 experiment_report_store:ExperimentReportStore|None=None,
                 external_reference_settings:ExternalReferenceSettings|None=None,
                 supplemental_knowledge_settings:SupplementalKnowledgeSettings|None=None,
                 web_visual_settings:WebVisualSettings|None=None,
                 generated_visual_settings:GeneratedVisualSettings|None=None,
                 multi_brain_settings:MultiBrainSettings|None=None,
                 interruption_settings:InterruptionGateSettings|None=None,
                 diarization_settings:SpeakerDiarizationSettings|None=None,
                 translation_settings:TranslationSettings|None=None,
                 presentation_translator:Callable[[str],str]|None=None,
                 )->None:
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
        self.supplemental_knowledge_settings=(
            supplemental_knowledge_settings or SupplementalKnowledgeSettings(False))
        self.web_visual_settings=web_visual_settings or WebVisualSettings(False)
        self.generated_visual_settings=(
            generated_visual_settings or GeneratedVisualSettings(False))
        self.multi_brain_settings=multi_brain_settings or MultiBrainSettings(False)
        self.experiment_report_id:str|None=None
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
        self.turn_progress:dict[tuple[int,int],TurnProgress]={}
        self.visual_tasks:set[asyncio.Task]=set()
        self.research_operations:set[tuple[int,int]]=set()
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
        self.greeting_emitted=False
        self.greeting_audio_ready=False
        self.client_audio_constraints:dict[str,object]={}
        # Session-local only: supports a content-free repeat-use counter. The
        # fingerprint is never persisted or returned to the browser.
        self._previous_utterance_fingerprint:bytes|None=None
        self.stt_settings=CascadeSttSettings.from_environment()
        # Noise-aware interruption gate. It never decides what a workflow does;
        # it decides whether a sound is worth ducking the agent for.
        self.interruption_settings=(
            interruption_settings or InterruptionGateSettings.from_environment())
        self._interruption_gate=InterruptionGate(self.interruption_settings)
        self._interrupt_candidate_announced=False
        self._interrupt_gate_reason:str|None=None
        # Session-scoped acoustic labels only. Never a voiceprint, never durable,
        # never an identity claim - see speaker_attribution's module docstring.
        self.diarization_settings=(
            diarization_settings or SpeakerDiarizationSettings.from_environment())
        self.participants=SessionParticipants()
        # Presentation-only translation. Enabled for the normal Korean pilot
        # profile, explicitly disableable by deployment, and never connected to
        # the mutation path - see source_presentation.
        self.translation_settings=(
            translation_settings or TranslationSettings.from_environment())
        self.presentation_translator=(
            presentation_translator
            if presentation_translator is not None
            else build_presentation_translator(
                _presentation_translation_client(self.translation_settings),
                self.translation_settings))
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
        self._interrupt_candidate_announced=False
        self._interrupt_gate_reason=None
        if playback:
            # Re-arm the acoustic gate for a fresh playback window, including
            # its onset cooldown. The measured noise floor survives on purpose.
            self._interruption_gate.playback_started()
        else:
            self._interruption_gate.playback_ended()
    def _reset_turn_identity(self)->None:
        for task in tuple(self.visual_tasks):
            task.cancel()
        self.visual_tasks.clear()
        self.research_operations.clear()
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
    def start(self,experiment_session_id:str|None=None):
        self.generation+=1
        self.greeting_emitted=False
        self.greeting_audio_ready=False
        self.client_audio_constraints={}
        self._previous_utterance_fingerprint=None
        self.active=True; self.active_turn_id=None; self.cooldown_until=0
        self.framer=FrameBuffer(); self._restore_primary_detector(TurnState.IDLE)
        self.history.reset()
        self.last_confirmed_language=None
        self.turn_committed_at.clear(); self.playback_completion_metrics.clear()
        self._reset_turn_identity()
        self.session_id=experiment_session_id or new_session_id()
        self.experiment_state_version=None
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
        self.accepted_input_language=InputLanguagePreference.AUTO
        self.accepted_revision_id=None
        self.greeting_audio_ready=False
        self.client_audio_constraints={}
        self._previous_utterance_fingerprint=None
        self._reset_turn_identity()
        if self.curated_protocol_session is not None:
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
            CuratedProtocolSession(
                fixture,
                translation_settings=self.translation_settings,
                presentation_translator=self.presentation_translator,
            ) if fixture is not None else None)
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
        self._previous_utterance_fingerprint=None
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
        self._previous_utterance_fingerprint=None
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
        """Turn playback-time audio into at most one announced interruption.

        Two independent things must agree before the researcher's answer is
        ducked. The endpoint detector decides whether frames are speech-shaped;
        `self._interruption_gate` decides whether they are loud enough for this
        room, sustained long enough, and clear of the echo window right after
        playback started. A detector onset that the gate never seconds is
        captured silently and discarded as noise: no `barge_in_candidate`
        reaches the browser, playback never ducks, no phantom command enters the
        voice history, and canonical workflow state is untouched.
        """

        output=[]
        detector=self._interrupt_detector
        framer=self._interrupt_framer
        gate=self._interruption_gate
        for frame in framer.push(chunk):
            assessment=gate.observe_frame(rms=pcm16_rms(frame))
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
                self._interrupt_candidate_announced=False
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
                    **gate.diagnostics(),
                }
            # The gate may second a detector onset a frame or two late, so the
            # announcement is retried until the utterance ends rather than being
            # decided on the onset frame alone.
            if (self._interrupt_candidate_identity is not None
                    and not self._interrupt_candidate_announced):
                if assessment.ready:
                    gate.mark_candidate()
                    self._interrupt_candidate_announced=True
                    self._interrupt_gate_reason=None
                    self._interrupt_candidate_diagnostics.update(
                        gate.diagnostics())
                    output.append(ListenerEvent(
                        "barge_in_candidate",
                        self._interrupt_candidate_identity[0],result,
                        self._interrupt_candidate_identity[1],
                        diagnostics=dict(
                            self._interrupt_candidate_diagnostics)))
                else:
                    self._interrupt_gate_reason=assessment.reason
            if result.rejected:
                candidate=self._interrupt_candidate_identity
                announced=self._interrupt_candidate_announced
                latency=(
                    max(0,round((self.clock()-
                                 self._interrupt_candidate_started_at)*1000))
                    if self._interrupt_candidate_started_at is not None
                    else None)
                reason=(
                    result.rejection_reason if announced
                    else self._interrupt_gate_reason or result.rejection_reason)
                diagnostics={
                    **self._interrupt_candidate_diagnostics,**gate.diagnostics()}
                self._reset_interrupt_input(
                    playback=self.state==TurnState.AGENT_SPEAKING)
                if announced:
                    output.append(ListenerEvent(
                        "barge_in_rejected",
                        candidate[0] if candidate else self.active_turn_id or 0,
                        result,
                        candidate[1] if candidate else self.generation,
                        reason=reason,latency_ms=latency,
                        diagnostics=diagnostics))
                break
            elif result.utterance is not None:
                candidate=self._interrupt_candidate_identity
                if candidate is None:
                    self._reset_interrupt_input(
                        playback=self.state==TurnState.AGENT_SPEAKING)
                    continue
                if not self._interrupt_candidate_announced:
                    # Speech-shaped but never loud or sustained enough for this
                    # room. Discard it without spending an STT call on noise and
                    # without ever telling the browser an interruption happened.
                    reason=(
                        self._interrupt_gate_reason
                        or "below_adaptive_noise_floor")
                    log.info(
                        "barge_in.ignored reason=%s voiced_frames=%d "
                        "total_frames=%d noise_floor_rms=%.5f",
                        reason,result.voiced_frames,result.total_frames,
                        gate.noise_floor_rms)
                    _record_workspace_metric(
                        category="voice",metric_name="barge_in_ignored",
                        dimensions={
                            "status":"ignored",
                            "reason_code":str(reason)[:100],
                        },
                    )
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
                self._interrupt_candidate_diagnostics.update(gate.diagnostics())
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
        # The candidate was noise after all: playback resumes, the workflow
        # transaction that produced this answer is untouched, and the gate
        # re-applies its cooldown so the same burst cannot immediately retry.
        self._interruption_gate.dismiss()
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
                or identity in self._interrupted_generations
                or not self._interrupt_candidate_announced):
            return []
        self._interruption_gate.confirm()
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
        self._reset_interrupt_input(playback=True)
        return True
    def playback_ended(self,turn_id:int)->bool:
        if self.state!=TurnState.AGENT_SPEAKING or turn_id!=self.active_turn_id:return False
        turn_gen=self.turn_generations.get(turn_id,self.generation)
        # Only an *announced* candidate defers the end of playback. An
        # unannounced one is ambient noise the gate already refused, and letting
        # it hold the session in AGENT_SPEAKING was how sustained equipment hum
        # could strand a turn that had already finished speaking.
        if ((turn_id,turn_gen) in self._interrupted_generations
                or (self._interrupt_candidate_identity is not None
                    and self._interrupt_candidate_announced)):
            return False
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
        try:
            keyterms = curated.stt_keyterms(include_control_terms=True)
        except TypeError:
            keyterms = curated.stt_keyterms()
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
        filler_words=bool(getattr(
            getattr(session,"stt_settings",None),"filler_words",False)),
        vad_threshold=getattr(
            getattr(session,"stt_settings",None),"vad_threshold",0.5),
        diarize=bool(getattr(
            getattr(session,"stt_settings",None),"diarize",False)),
    )


def transcribe_cascade_audio(
    pcm:bytes,
    context:CascadeTranscriptionContext,
) -> Transcription:
    """Apply one request contract without rewriting the provider transcript."""

    return transcribe(
        clean_path(pcm),
        language=context.language,
        keyterms=context.keyterms,
        vad_threshold=getattr(context, "vad_threshold", 0.5),
        filler_words=bool(getattr(context, "filler_words", False)),
        diarize=bool(getattr(context, "diarize", False)),
    )

class LockedSender:
    def __init__(self,websocket): self.websocket=websocket; self.lock=asyncio.Lock()
    async def text(self,kind:str,**fields):
        async with self.lock: await self.websocket.send_text(event(kind,**fields))
        RUNTIME_METRICS.observe(kind,fields)
        if kind=="turn.done" and fields.get("route")!="server_greeting":
            _record_workspace_metric(
                category="voice",metric_name="successful_turn",
                dimensions={
                    "status":"completed",
                    "route":str(fields.get("route") or "unknown")[:100],
                },
            )
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
            _scope_tenant_resource("generated_visual",asset.asset_id,bind=True)
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


async def _prepare_external_visual_candidate(
    candidate:dict[str,Any],
)->dict[str,Any]|None:
    """Proxy displayable bytes or reduce the result to a cited source link."""

    prepared=dict(candidate)
    source_url=prepared.get("source_page_url")
    image_url=prepared.get("image_url")
    publisher=prepared.get("publisher_domain")
    if (
        not isinstance(source_url,str) or not source_url.startswith("https://")
        or not isinstance(publisher,str) or not publisher.strip()
    ):
        return None
    rights=prepared.get("rights")
    if not isinstance(rights,str) or not rights.strip():
        prepared.pop("image_url",None)
        prepared["display_mode"]="source_link"
        prepared["verification_label"]=(
            "출처 링크만 제공 · 이미지 표시 권한 미확인")
        return prepared
    if not isinstance(image_url,str) or not image_url.startswith("https://"):
        prepared.pop("image_url",None)
        prepared["display_mode"]="source_link"
        return prepared
    asset=await WEB_VISUAL_REGISTRY.obtain_or_register(
        image_url=image_url,
        source_url=source_url,
        publisher_domain=publisher,
        title=str(prepared.get("title") or "Web reference image"),
    )
    if asset is None:
        prepared.pop("image_url",None)
        prepared["display_mode"]="source_link"
        prepared["verification_label"]=(
            "출처 링크만 제공 · 이미지 바이트 검증 실패")
        return prepared
    _scope_tenant_resource("web_visual",asset.asset_id,bind=True)
    prepared["image_url"]=f"/api/web-visuals/{asset.asset_id}"
    prepared["display_mode"]="web_image"
    prepared["rights"]=rights.strip()[:300]
    return prepared


async def _queue_curated_web_visual(
    *,session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    endpoint:float,clock:Callable[[],float],curated:CuratedProtocolSession,
    settings:WebVisualSettings,requested_entities:tuple[str,...]=(),
    visual_intent:str|None=None,
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
        started = clock()
        try:
            await sender.text(
                "tool.call", **identity, tool="search_authoritative_web", round=2,
                image_search_enabled=False,
                intent_triggered=True,
                max_results=1,
            )

            # 1. Fast PubChem chemistry structure lookup (strictly for chemical structure requests or known compounds)
            pubchem_match = None
            detected_entities = list(requested_entities)
            if not detected_entities:
                step_text_lower = f"{step.instruction_source_text} {fixture.title}".casefold()
                for comp in _KNOWN_PUBCHEM_COMPOUNDS:
                    if comp in step_text_lower:
                        detected_entities.append(comp)
                        break

            if visual_intent == "chemical_structure" or (
                visual_intent != "lab_equipment_image"
                and any(ent.casefold() in _KNOWN_PUBCHEM_COMPOUNDS for ent in detected_entities)
            ):
                pubchem_adapter = PubChemChemistryAdapter()
                for ent in detected_entities:
                    pubchem_match = await pubchem_adapter.lookup(ent)
                    if pubchem_match:
                        break

            if pubchem_match is not None:
                if not session.owns_visual_result(
                    turn_id, generation, configuration_id, fixture.protocol_id
                ):
                    return
                pubchem_match=await _prepare_external_visual_candidate(
                    pubchem_match)
                if pubchem_match is None:
                    raise RuntimeError("PubChem candidate identity is invalid")
                elapsed = max(0, round((clock() - started) * 1000))
                await sender.text(
                    "tool.result", **identity, tool="search_authoritative_web",
                    round=2, status="success", elapsed_ms=elapsed,
                    retrieval_backend="pubchem_pug_rest", match_count=1,
                    image_search_enabled=False,
                )
                await sender.text(
                    "protocol.visual.state", **identity, status="web_visual_ready",
                    visual_ready_ms=max(0, round((clock() - endpoint) * 1000)),
                    candidate=pubchem_match
                )
                return

            # 2. Try a bounded public catalog first.  Only contact the paid
            # provider if that local policy path cannot produce a candidate.
            search_terms = [*detected_entities, step.instruction_source_text[:50]] if detected_entities else [step.instruction_source_text[:50]]
            wiki_adapter = WikimediaVisualAdapter(timeout_seconds=3.5)

            async def _fast_public_search():
                for term in search_terms:
                    cand = await wiki_adapter.lookup(term)
                    if cand and cand.get("image_url"):
                        return cand
                return None

            web_visual_timeout = float(os.environ.get("VOICE_WORKFLOW_AGENT_WEB_VISUAL_TIMEOUT_SECONDS", "6.0"))

            async def _grok_image_search() -> dict[str, Any]:
                try:
                    client = AsyncOpenAI(
                        base_url=api_url(""),
                        api_key=require_env("XAI_API_KEY"),
                        max_retries=0,
                    )
                    query = "\n".join((
                        "Find a real, authoritative laboratory image for this request. Include Markdown link ![alt](image_url).",
                        f"Protocol: {fixture.title}",
                        f"Step {step.source_label}: {step.instruction_source_text}",
                        "Requested entities: " + (", ".join(detected_entities) or "current step"),
                    ))
                    search_settings = settings
                    if search_settings.references:
                        ref_copy = copy.deepcopy(search_settings.references)
                        object.__setattr__(ref_copy, "timeout_seconds", web_visual_timeout)
                        search_settings = WebVisualSettings(True, ref_copy)
                    await sender.text(
                        "tool.call", **identity,
                        tool="search_authoritative_web", round=3,
                        image_search_enabled=True,
                        intent_triggered=True,
                        max_results=1,
                    )
                    log.info(
                        "web_visual.provider_request turn_id=%s generation=%s "
                        "image_search_enabled=true max_results=1",
                        turn_id, generation,
                    )
                    res = await XaiAuthoritativeImageSearch(client, search_settings).search(query)
                    return res
                except Exception as exc:
                    log.info("grok image search failed turn_id=%s class=%s", turn_id, type(exc).__name__)
                return {
                    "status": "error",
                    "matches": [],
                    "image_search_enabled": True,
                    "image_search_count": 0,
                    "web_search_count": 0,
                    "max_results": 1,
                }

            fast_task = asyncio.create_task(_fast_public_search())

            # Wait for fast public search with up to 3.5s budget
            fast_candidate = None
            try:
                fast_candidate = await asyncio.wait_for(asyncio.shield(fast_task), timeout=3.5)
            except (asyncio.TimeoutError, Exception):
                fast_candidate = None

            if fast_candidate and session.owns_visual_result(turn_id, generation, configuration_id, fixture.protocol_id):
                fast_candidate=await _prepare_external_visual_candidate(
                    fast_candidate)
                if fast_candidate is None:
                    raise RuntimeError("Wikimedia candidate identity is invalid")
                elapsed = max(0, round((clock() - started) * 1000))
                await sender.text(
                    "tool.result", **identity, tool="search_authoritative_web",
                    round=2, status="success", elapsed_ms=elapsed,
                    retrieval_backend="wikimedia_rest", match_count=1,
                    image_search_enabled=False,
                )
                await sender.text(
                    "protocol.visual.state", **identity, status="web_visual_ready",
                    visual_ready_ms=max(0, round((clock() - endpoint) * 1000)),
                    candidate=fast_candidate
                )
                return

            # No public-catalog match: run exactly one intent-triggered xAI
            # image-search request under the global deadline.
            grok_result: dict[str, Any]
            try:
                grok_result = await asyncio.wait_for(
                    _grok_image_search(), timeout=web_visual_timeout
                )
            except (asyncio.TimeoutError, Exception):
                grok_result = {
                    "status": "timeout",
                    "matches": [],
                    "image_search_enabled": True,
                    "image_search_count": 0,
                    "web_search_count": 0,
                    "max_results": 1,
                }
            grok_candidate = (
                grok_result["matches"][0]
                if grok_result.get("status") == "success"
                and grok_result.get("matches")
                else None
            )

            if grok_candidate and session.owns_visual_result(turn_id, generation, configuration_id, fixture.protocol_id):
                grok_candidate=await _prepare_external_visual_candidate(
                    grok_candidate)
                if grok_candidate is None:
                    raise RuntimeError("xAI image candidate identity is invalid")
                elapsed = max(0, round((clock() - started) * 1000))
                await sender.text(
                    "tool.result", **identity, tool="search_authoritative_web",
                    round=3, status="success", elapsed_ms=elapsed,
                    retrieval_backend="xai_responses_web_image_search", match_count=1,
                    image_search_enabled=True,
                    web_search_count=grok_result.get("web_search_count", 0),
                    image_search_count=grok_result.get("image_search_count", 0),
                    max_results=1,
                )
                await sender.text(
                    "protocol.visual.state", **identity, status="web_visual_ready",
                    visual_ready_ms=max(0, round((clock() - endpoint) * 1000)),
                    candidate=grok_candidate
                )
                return

            if session.owns_visual_result(turn_id, generation, configuration_id, fixture.protocol_id):
                elapsed = max(0, round((clock() - started) * 1000))
                await sender.text(
                    "tool.result", **identity, tool="search_authoritative_web",
                    round=3, status="not_found", elapsed_ms=elapsed, match_count=0,
                    image_search_enabled=bool(
                        grok_result.get("image_search_enabled", False)
                    ),
                    web_search_count=grok_result.get("web_search_count", 0),
                    image_search_count=grok_result.get("image_search_count", 0),
                    max_results=1,
                )
                if visual_intent not in ("photo_only", "equipment_photo") and session.generated_visual_settings.enabled:
                    visual_spec = _curated_visual_specification(curated)
                    if visual_spec is not None:
                        await _queue_curated_generated_visual(
                            session=session, sender=sender, turn_id=turn_id,
                            generation=generation, endpoint=endpoint, clock=clock,
                            specification=visual_spec, settings=session.generated_visual_settings,
                        )
                        return
                await sender.text(
                    "protocol.visual.state", **identity, status="visual_failed",
                    visual_ready_ms=max(0, round((clock() - endpoint) * 1000)),
                    fallback="none"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "web visual search failed closed turn_id=%s error=%s",
                turn_id, type(exc).__name__
            )
            if session.owns_visual_result(
                turn_id, generation, configuration_id, fixture.protocol_id
            ):
                await sender.text(
                    "tool.result", **identity, tool="search_authoritative_web",
                    round=2, status="error",
                    elapsed_ms=max(0, round((clock() - started) * 1000))
                )
                if visual_intent not in ("photo_only", "equipment_photo") and session.generated_visual_settings.enabled:
                    visual_spec = _curated_visual_specification(curated)
                    if visual_spec is not None:
                        await _queue_curated_generated_visual(
                            session=session, sender=sender, turn_id=turn_id,
                            generation=generation, endpoint=endpoint, clock=clock,
                            specification=visual_spec, settings=session.generated_visual_settings,
                        )
                        return
                await sender.text(
                    "protocol.visual.state", **identity, status="visual_failed",
                    visual_ready_ms=max(0, round((clock() - endpoint) * 1000)),
                    fallback="none"
                )

    task=asyncio.create_task(worker())
    session.track_visual_task(task)


async def _queue_curated_research(
    *,session:ListenerSession,sender:LockedSender,turn_id:int,generation:int,
    endpoint:float,clock:Callable[[],float],curated:CuratedProtocolSession,
    context:dict[str,Any],turn_language:str,pre_transition_index:int|None,
    plan:Any,source_output:Any=None,
) -> None:
    configuration_id=session.accepted_configuration_id
    if not session.owns_research_result(turn_id,generation,configuration_id):
        return

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
            )}

        research_budget=min(120.0,max(
            10.0,
            (
                session.external_reference_settings.timeout_seconds
                if session.external_reference_settings.enabled else 0.0
            ) + (
                session.supplemental_knowledge_settings.timeout_seconds
                if session.supplemental_knowledge_settings.enabled else 0.0
            ),
        ))
        research_deadline=clock()+research_budget
        def research_remaining(cap:float)->float:
            return max(0.05,min(cap,research_deadline-clock()))

        # 1. Approved references (internal SQLite)
        if session.tool_context is not None and not ctx["force_external"]:
            await sender.text(
                "research.state",turn_id=turn_id,status="running",
                phase="approved_references",
                correlation_id=f"research-{generation}-{turn_id}",
            )
            await sender.text(
                "tool.call",turn_id=turn_id,
                tool=APPROVED_LAB_REFERENCE_TOOL_NAME,round=0)
            reference_started=clock()
            try:
                reference_result=await asyncio.wait_for(
                    asyncio.to_thread(
                        search_approved_lab_references,
                        ctx["reference_query"],context=session.tool_context,
                        protocol_id=curated.fixture.protocol_id,top_k=5,
                    ),
                    timeout=research_remaining(3.0),
                )
            except asyncio.TimeoutError:
                reference_result={
                    "status":"timeout_read","answerable":False,
                    "matches":[],"retrieval":{"backend":"sqlite"},
                }
            if not session.owns_research_result(turn_id,generation,configuration_id):
                return
            reference_elapsed=round((clock()-reference_started)*1000)
            reference_backend=(
                reference_result.get("retrieval",{}).get("backend")
                if isinstance(reference_result,dict) else None
            )
            matches=tuple(reference_result.get("matches",()))
            await sender.text(
                "tool.result",turn_id=turn_id,
                tool=APPROVED_LAB_REFERENCE_TOOL_NAME,round=0,
                status=reference_result.get("status","error"),
                elapsed_ms=reference_elapsed,
                retrieval_backend=reference_backend,
                match_count=len(matches))
            if reference_result.get("answerable") and matches:
                try:
                    client=AsyncOpenAI(
                        base_url=api_url(""),
                        api_key=require_env("XAI_API_KEY"),max_retries=0)
                    client.model=require_env("CHAT_MODEL")
                    answer=await asyncio.wait_for(
                        answer_approved_reference_question(
                            client,ctx["query"],language=turn_language,
                            protocol_id=curated.fixture.protocol_id,
                            step_id=ctx["step"].step_id,evidence=matches),
                        timeout=research_remaining(8.0),
                    )
                    if session.owns_research_result(turn_id,generation,configuration_id):
                        research_plan=curated.apply_reference_answer(
                            turn_id=turn_id,language=turn_language,
                            primary_text=answer.primary_text,
                            origin="approved_lab_corpus",
                            citations=answer.citations,
                            retrieval_backend=reference_backend or "sqlite",
                            retrieval_scores=tuple(
                                float(item["score"]) for item in matches
                                if isinstance(item.get("score"),(int,float))
                            ),limitations=answer.limitations,
                        )
                except Exception:
                    log.info(
                        "approved reference supplement failed closed turn_id=%s",
                        turn_id)

        # 2. External Web Search (Grok 4.6)
        result=None
        if research_plan is None and session.external_reference_settings.enabled:
            await sender.text(
                "research.state",turn_id=turn_id,status="running",
                phase="authoritative_web",
                correlation_id=f"research-{generation}-{turn_id}",
            )
            await sender.text(
                "tool.call",turn_id=turn_id,
                tool="search_authoritative_web",round=1)
            external_started=clock()
            include_images=bool(
                getattr(plan,"visual_requested",False)
                or getattr(plan,"action",None) is CuratedProtocolAction.VISUAL_REQUEST
            )
            log.info(
                "external_search.request_config model=%s open_mode=%s total_timeout=%s connect_timeout=%s read_timeout=%s image_search_enabled=%s",
                session.external_reference_settings.model,
                bool(session.external_reference_settings.domain_profile=="open" or not session.external_reference_settings.allowed_domains),
                session.external_reference_settings.timeout_seconds,
                session.external_reference_settings.connect_timeout_seconds,
                session.external_reference_settings.read_timeout_seconds,
                include_images,
            )
            log.info(
                "external_search.provider_started turn_id=%s generation=%s query=%s",
                turn_id,generation,ctx["reference_query"][:120],
            )
            async def _on_partial_sources(srcs: list[dict[str, Any]]) -> None:
                if session.owns_research_result(turn_id, generation, configuration_id):
                    await sender.text(
                        "research.state",
                        turn_id=turn_id,
                        generation=generation,
                        status="running",
                        phase="authoritative_web",
                        correlation_id=f"research-{generation}-{turn_id}",
                        sources_found=len(srcs),
                        message=f"웹에서 참고 자료 {len(srcs)}개를 찾았어요.",
                    )

            try:
                external_client=AsyncOpenAI(
                    base_url=api_url(""),api_key=require_env("XAI_API_KEY"),
                    max_retries=0)
                external_searcher=XaiAuthoritativeWebSearch(
                    external_client,session.external_reference_settings,
                )
                try:
                    search_call=external_searcher.search(
                        ctx["reference_query"],
                        language=turn_language,
                        include_images=include_images,
                        on_partial_sources=_on_partial_sources,
                    )
                except TypeError:
                    search_call=external_searcher.search(
                        ctx["reference_query"],language=turn_language)
                result=await asyncio.wait_for(
                    search_call,
                    timeout=research_remaining(
                        session.external_reference_settings.timeout_seconds),
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                log.warning("external_search.timeout turn_id=%s layer=server_research_remaining", turn_id)
                result={"status":"timeout_total","matches":[],"images":[]}
            except Exception as exc:
                log.warning(
                    "external_search.error turn_id=%s class=%s",
                    turn_id,type(exc).__name__)
                result={"status":"connect_error","matches":[],"images":[]}
            if not session.owns_research_result(turn_id,generation,configuration_id):
                return
            external_elapsed=round((clock()-external_started)*1000)
            log.info(
                "external_search.provider_completed turn_id=%s elapsed_ms=%s status=%s web_search_count=%s image_search_count=%s source_count=%s markdown_image_count=%s",
                turn_id,external_elapsed,result.get("status"),
                result.get("web_search_count",result.get("tool_usage_count",0)),
                result.get("image_search_count",0),
                len(result.get("matches",[])),
                len(result.get("images",[])),
            )
            log.info(
                "external_search.result_admission citation_count=%s image_candidate_count=%s turn_id=%s generation=%s",
                len(result.get("matches",[])),
                len(result.get("images",[])),
                turn_id,generation,
            )
            await sender.text(
                "tool.result",turn_id=turn_id,
                tool="search_authoritative_web",round=1,
                status=result.get("status","response_schema_error"),
                elapsed_ms=external_elapsed,
                retrieval_backend=result.get("backend"),
                match_count=len(result.get("matches",[])),
                provider_request_id=result.get("provider_request_id"),
                streaming=bool(result.get("streaming",False)),
                provider_event_count=result.get("event_count",0),
                provider_tool_event_count=result.get("tool_event_count",0),
                first_provider_event_ms=result.get("first_event_ms"),
                first_provider_text_ms=result.get("first_text_ms"),
                provider_tool_started_ms=result.get("tool_started_ms"),
                provider_tool_ended_ms=result.get("tool_ended_ms"),
            )
            if result.get("status")=="success" and result.get("matches"):
                research_plan=curated.apply_reference_answer(
                    turn_id=turn_id,language=turn_language,
                    primary_text=result["answer"],
                    origin="external_authoritative_reference",
                    citations=tuple(result["matches"]),
                    retrieval_backend=result["backend"],
                    limitations=(
                        "External guidance cannot modify the active protocol.",
                    ),
                )
                if result.get("images") and getattr(plan, "visual_requested", False):
                    img_match = result["images"][0]
                    fixture = curated.fixture
                    step = fixture.steps[curated.current_index]
                    job_id = hashlib.sha256(
                        f"web-image\x1f{fixture.source_pdf_sha256}\x1f{step.step_id}".encode()
                    ).hexdigest()
                    await sender.text(
                        "protocol.visual.state",
                        configuration_id=configuration_id,turn_id=turn_id,
                        generation=generation,protocol_id=fixture.protocol_id,
                        step_id=step.step_id,source_document_hash=fixture.source_pdf_sha256,
                        visual_job_id=job_id,status="web_visual_ready",
                        visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                        candidate=img_match)

        # 3. Supplemental model knowledge (Grok 4.6 explanation)
        supplemental_result=None
        if (
            research_plan is None
            and session.supplemental_knowledge_settings.enabled
            and supplemental_knowledge_allowed(
                ctx["query"],plan.question_dimensions)
        ):
            await sender.text(
                "research.state",turn_id=turn_id,status="running",
                phase="supplemental_model",
                correlation_id=f"research-{generation}-{turn_id}",
            )
            supplemental_started=clock()
            try:
                supplemental_client=AsyncOpenAI(
                    base_url=api_url(""),api_key=require_env("XAI_API_KEY"),
                    max_retries=0)
                supplemental_result=await asyncio.wait_for(
                    XaiSupplementalKnowledge(
                        supplemental_client,
                        session.supplemental_knowledge_settings,
                    ).explain(
                        ctx["reference_query"],language=turn_language),
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
            await _finish_research_operation(
                sender,session,turn_id,generation,"success",
                primary_text=research_plan.primary_text,
                answer_origin=research_plan.answer_origin,
                citations=list(research_plan.citations),
                retrieval_backend=research_plan.retrieval_backend,
                limitations=list(research_plan.limitations),
            )
            if session.experiment_report_store is not None:
                report=await asyncio.to_thread(
                    _record_experiment_report_plan,
                    session,curated,research_plan,
                    turn_id=turn_id,generation=generation,
                    pre_transition_index=pre_transition_index,
                )
                await sender.text("experiment.report.state",report=_public_experiment_report_state(report))
        elif session.owns_research_result(turn_id,generation,configuration_id):
            status=(
                result.get("status","disabled")
                if isinstance(result,dict) else
                supplemental_result.get("status","disabled")
                if isinstance(supplemental_result,dict) else "disabled"
            )
            if (
                session.experiment_report_store is not None
                and status not in {"disabled","not_found","no_allowed_citation"}
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
                await sender.text("experiment.report.state",report=_public_experiment_report_state(report))
            await _finish_research_operation(
                sender,session,turn_id,generation,status,
                limitation=(
                    "현재 적용된 실험 PDF 근거는 유지했지만 요청한 추가 차원을 웹 참고 자료에서 확인하지 못했습니다."
                    if turn_language=="ko" else
                    "The protocol evidence remains available, but the additional dimension could not be verified."
                ),
            )

    task=asyncio.create_task(worker())
    session.track_visual_task(task)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        pass


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
        _scope_tenant_resource(
            "experiment_report",session.experiment_report_id,bind=True)
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
    if plan.action is CuratedProtocolAction.START and plan.state_changed:
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
    elif plan.action is CuratedProtocolAction.STOP and plan.state_changed:
        event_type="session_stopped"
        payload["stop_reason"]="stopped_by_user"
    elif plan.action is CuratedProtocolAction.START_TIMER:
        event_type="timer_started"
    elif plan.action is CuratedProtocolAction.PAUSE:
        event_type="workflow_paused"
    elif plan.action is CuratedProtocolAction.RESUME:
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
    if event_type is not None:
        report=store.append_event(
            session.experiment_report_id,
            event_key=event_key,
            event_type=event_type,
            step_id=step_id,
            step_label=step_label,
            user_wording=(plan.anomaly_text or plan.observation_outcome),
            category=(plan.anomaly_category or plan.observation_predicate),
            severity=("unknown" if plan.reported_anomaly else None),
            confirmation_state=(
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
    if plan.action is CuratedProtocolAction.STOP and plan.state_changed:
        report=store.finalize(
            session.experiment_report_id,
            status="stopped",
            event_key=f"{event_key}-finalize",
        )
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
})


def _acknowledge_report_persistence(plan:Any,language:str)->Any:
    if (
        plan.action is CuratedProtocolAction.NEXT
        and plan.state_changed
        and plan.reported_completion
    ):
        if language=="ko":
            record_phrase=(
                "단계를 완료하고 관찰 결과와 함께 실험 기록에 반영했습니다."
                if plan.reported_observation else
                "단계를 완료하고 실험 기록에 반영했습니다."
            )
            # The deterministic plan may describe semantic completion before
            # persistence, but record-success language is added only here,
            # after the reporting gate succeeds.
            persisted_speech=plan.speech_text.replace(
                "단계를 완료했습니다.",record_phrase,1,
            )
            persisted_display=plan.display_text.replace(
                "단계를 완료했습니다.",record_phrase,1,
            )
            if persisted_display==plan.display_text:
                persisted_display=f"{persisted_speech}\n\n{plan.display_text}"
        else:
            acknowledgment=(
                "I added the reported observation and current-step completion to the experiment record."
                if plan.reported_observation else
                "I added the current-step completion to the experiment record."
            )
            persisted_speech=f"{acknowledgment} {plan.speech_text}"
            persisted_display=f"{acknowledgment}\n\n{plan.display_text}"
        display_document=getattr(plan,"display_document",None)
        if language=="ko" and isinstance(display_document,dict):
            sections=[]
            for section in display_document.get("sections",()):
                if not isinstance(section,dict):
                    continue
                updated=dict(section)
                if updated.get("kind")=="lead" and isinstance(
                    updated.get("text"),str
                ):
                    updated["text"]=updated["text"].replace(
                        "단계를 완료했습니다.",record_phrase,1,
                    )
                sections.append(updated)
            display_document={**display_document,"sections":sections}
        return replace(
            plan,
            display_text=persisted_display,
            speech_text=persisted_speech,
            display_document=display_document,
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


def _public_experiment_report_state(report:dict)->dict:
    events=list(report.get("events") or ())
    return {
        key:report.get(key) for key in (
            "report_id","status","started_at","ended_at","anomaly_count",
            "blocker_count","finalization_version","development_only",
            "session_id","protocol_id",
        )
    } | {"event_count":len(events),"events":events}


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


async def _send_session_greeting(
    sender:LockedSender,session:ListenerSession,*,language:str,
) -> None:
    """Emit one deterministic, interruptible greeting for a logical session."""

    if session.greeting_emitted or not session.greeting_audio_ready:
        return
    session.greeting_emitted=True
    greeting_turn_id=2_000_000_000  # Reserved display/audio identity; user turns start at 1.
    title=(
        session.curated_protocol_session.fixture.title
        if session.curated_protocol_session is not None else "the selected protocol"
    )
    greeting={
        "ko":f"Voice Workflow Agent입니다. 선택한 {title} 프로토콜이 준비되었습니다. 시작할까요, 아니면 먼저 질문하시겠어요?",
        "en":f"This is Voice Workflow Agent. {title} is ready. Would you like to begin, or ask a question first?",
        "vi":f"Voice Workflow Agent đã sẵn sàng với {title}. Bạn muốn bắt đầu hay hỏi trước?",
    }.get(language,"Voice Workflow Agent is ready.")
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
        text=greeting,language=language,
    )
    try:
        log.info("session.greeting.tts_started greeting_id=%s",greeting_id)
        pcm=await asyncio.to_thread(synthesize,greeting,language)
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
        primary_text=greeting,speech_text=greeting,
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


async def run_turn(websocket:WebSocket,session:ListenerSession,source_pcm:bytes,turn_id:int,
                   input_frames:int,voiced_frames:int=0,
                   retained_prefix_frames:int=0,
                   clock:Callable[[],float]=time.perf_counter,
                   sender:LockedSender|None=None,filler:CascadeFiller|None=None,
                   accepted_transcription:Transcription|None=None,
                   accepted_stt_ms:int|None=None,
                   accepted_stt_context:CascadeTranscriptionContext|None=None)->None:
    sender=sender or LockedSender(websocket); endpoint=session.endpoint_at or clock(); timings={}; generation=session.generation
    async def current_text(kind:str,**fields)->bool:
        if not session.is_current(turn_id,generation): return False
        fields.setdefault("generation",generation)
        if session.accepted_configuration_id is not None:
            fields.setdefault("configuration_id",session.accepted_configuration_id)
        await sender.text(kind,**fields); return True

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
    async def finish_blocked_voice(text:str,route:str)->None:
        """Complete one deterministic non-mutating clarification turn."""

        timings["primary_text_ready_ms"]=round((clock()-endpoint)*1000)
        if not await current_text(
            "reply.delta",turn_id=turn_id,segment_index=0,text=text
        ):
            return
        session.set_turn_terminal_outcome(turn_id,generation,"blocked")
        try:
            await progress("synthesizing",route=route)
            pcm=await asyncio.to_thread(
                synthesize,text,session.accepted_language or "ko"
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
    stt_language=transcription_context.language
    stt_keyterms=transcription_context.keyterms
    if accepted_transcription is None:
        started=clock()
        try:
            transcription=await asyncio.to_thread(
                transcribe_cascade_audio,source_pcm,transcription_context)
        except Exception:
            _record_workspace_metric(
                category="voice",metric_name="stt_failure",
                dimensions={"status":"failed","reason_code":"provider_failure"},
            )
            raise
        timings["stt"]=round((clock()-started)*1000)
    else:
        transcription=accepted_transcription
        timings["stt"]=max(0,accepted_stt_ms or 0)
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
    if not transcript.strip():
        if session.reject_empty_transcript(turn_id):
            _record_workspace_metric(
                category="voice",metric_name="stt_failure",
                dimensions={"status":"rejected","reason_code":"empty_transcript"},
            )
            _record_workspace_metric(
                category="voice",metric_name="command_failure",
                dimensions={"status":"rejected","reason_code":"empty_transcript"},
            )
            await sender.text("speech.rejected",turn_id=turn_id,reason="empty_transcript",voiced_frames=voiced_frames,total_frames=input_frames,duration_ms=input_frames*20)
            await sender.text("state.changed",state=session.state.value,turn_id=turn_id,cooldown_ms=session.detector.config.cooldown_ms)
        return
    input_decision=classify_input_event(
        transcription,
        keyterms=stt_keyterms,
        duration_seconds=transcription.duration_seconds,
    )
    if not input_decision.accepted:
        if session.reject_empty_transcript(turn_id):
            _record_workspace_metric(
                category="voice",metric_name="stt_failure",
                dimensions={
                    "status":"rejected",
                    "reason_code":str(input_decision.reason or "non_speech")[:100],
                },
            )
            _record_workspace_metric(
                category="voice",metric_name="command_failure",
                dimensions={
                    "status":"rejected",
                    "reason_code":str(input_decision.reason or "non_speech")[:100],
                },
            )
            await sender.text(
                "speech.rejected",turn_id=turn_id,generation=generation,
                reason=input_decision.reason or "non_speech",
                voiced_frames=voiced_frames,total_frames=input_frames,
                duration_ms=input_frames*20,
            )
            await sender.text(
                "state.changed",state=session.state.value,turn_id=turn_id,
                cooldown_ms=session.detector.config.cooldown_ms,
            )
        return
    # Participant-aware admission. Provider audio intelligence feeds the
    # deterministic layer that already exists; it never becomes a second router
    # and it never gains mutation authority of its own. When diarization is off
    # this is a no-op that makes no claim about who spoke.
    priority_stop=is_priority_stop_command(transcript)
    transcript_speaker_segments=transcript_segments(
        transcription.words,fallback_text=transcript)
    speaker_decision=evaluate_speaker_policy(
        transcript_speaker_segments,session.participants,
        diarization_enabled=bool(session.diarization_settings.enabled),
        mutating=arbitrate_request(transcript).mutation_candidate,
        priority_stop=priority_stop,
        settings=session.diarization_settings,
    )
    speaker_diagnostics=diarization_diagnostics(
        transcript_speaker_segments,speaker_decision,
        diarization_enabled=bool(session.diarization_settings.enabled),
    )
    await current_text(
        "voice.speaker_policy",turn_id=turn_id,generation=generation,
        mutation_authorized=speaker_decision.mutation_allowed,
        **speaker_diagnostics,
    )
    if not speaker_decision.mutation_allowed:
        reason=speaker_decision.reason[:100]
        _record_workspace_metric(
            category="voice",metric_name="command_failure",
            dimensions={
                "status":"blocked",
                "reason_code":reason,
            },
        )
        _record_workspace_metric(
            category="workflow",metric_name="blocked_mutation",
            dimensions={"status":"blocked","reason_code":reason},
        )
        if reason=="unknown_speaker":
            _record_workspace_metric(
                category="voice",metric_name="unknown_speaker_mutation_rejection",
                dimensions={"status":"blocked","reason_code":reason},
            )
        elif reason=="overlapping_speakers":
            _record_workspace_metric(
                category="voice",metric_name="overlapping_speaker_ambiguity",
                dimensions={"status":"blocked","reason_code":reason},
            )
        if not await current_text("transcript",turn_id=turn_id,text=transcript):
            return
        await finish_blocked_voice(
            speaker_decision.message or UNKNOWN_SPEAKER_MESSAGE,
            "speaker_attribution_clarification",
        )
        return
    admission = classify_transcription_language(
        transcription,session.accepted_input_language
    )
    if admission.correction_class is not None:
        transcript = admission.admitted_text
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
        "stt_endpoint":"/v1/stt",
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
        **speaker_diagnostics,
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
        stt_endpoint="/v1/stt",
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
        _record_workspace_metric(
            category="voice",metric_name="clarification_request",
            dimensions={
                "status":"blocked",
                "reason_code":admission.mismatch_status or "language_uncertain",
            },
        )
        if arbitrate_request(transcript).mutation_candidate:
            _record_workspace_metric(
                category="workflow",metric_name="blocked_mutation",
                dimensions={
                    "status":"blocked",
                    "reason_code":"language_uncertain",
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
    if not await current_text("transcript",turn_id=turn_id,text=transcript):
        return
    utterance_fingerprint=hashlib.sha256(
        " ".join(transcript.casefold().split()).encode("utf-8")
    ).digest()
    if hmac.compare_digest(
        session._previous_utterance_fingerprint or b"",utterance_fingerprint
    ):
        _record_workspace_metric(
            category="voice",metric_name="repeated_utterance",
            dimensions={"status":"observed","event_kind":"consecutive_repeat"},
        )
    session._previous_utterance_fingerprint=utterance_fingerprint
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
        turn_language=pending_language
        session.last_confirmed_language=turn_language
        await current_text("session.turn_language_resolved",turn_id=turn_id,
                           language=turn_language)
    elif session.curated_protocol_session is not None:
        turn_language="ko"
        session.last_confirmed_language="ko"
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
    request_arbitration=arbitrate_request(transcript)
    if session.curated_protocol_session is not None:
        curated=session.curated_protocol_session
        checkpoint=curated._checkpoint()
        curated_tools_used=[]
        report_prepared=False
        workflow_mutation_committed=False
        plan=None
        brain_run=None
        brain_activation=None
        brain_terminals={}
        answer_output=None
        brain_snapshot=None
        try:
            timings["protocol_lookup_started_ms"]=round((clock()-endpoint)*1000)
            await progress("checking_protocol",route="curated_protocol")
            pre_transition_index=curated.current_index
            routed_turn=route_curated_runtime_turn(
                curated,
                transcript,turn_id=turn_id,language=turn_language,
                transcript_quality=transcription_quality_issue(transcription),
                configuration_id=session.accepted_configuration_id,
                generation=generation,
                arbitration=request_arbitration)
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
            clarification_actions={
                CuratedProtocolAction.CLARIFY_COMPLETION,
                CuratedProtocolAction.CLARIFY_REFERENCE,
                CuratedProtocolAction.CLARIFY_PARAMETER,
                CuratedProtocolAction.TRANSCRIPT_UNRELIABLE,
            }
            if plan.action in clarification_actions:
                _record_workspace_metric(
                    category="voice",metric_name="clarification_request",
                    dimensions={
                        "status":"requested",
                        "event_kind":plan.action.value,
                        "reason_code":routed_turn.arbitration.reason_code or "ambiguous",
                    },
                )
            if plan.action is CuratedProtocolAction.REPEAT:
                _record_workspace_metric(
                    category="voice",metric_name="repeat_request",
                    dimensions={"status":"completed","event_kind":"current_step"},
                )
            if plan.action is CuratedProtocolAction.CLARIFY_COMPLETION:
                _record_workspace_metric(
                    category="workflow",metric_name="ambiguous_mutation_command",
                    dimensions={
                        "status":"blocked",
                        "reason_code":routed_turn.arbitration.reason_code or "ambiguous",
                    },
                )
            if (
                request_arbitration.mutation_candidate
                and not plan.state_changed
                and plan.speech_mode is CuratedProtocolSpeechMode.BLOCKED
            ):
                _record_workspace_metric(
                    category="workflow",metric_name="blocked_mutation",
                    dimensions={
                        "status":"blocked",
                        "event_kind":plan.action.value,
                        "reason_code":routed_turn.arbitration.reason_code or "policy_gate",
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
                brain_client=AsyncOpenAI(
                    base_url=api_url(""),api_key=require_env("XAI_API_KEY"),
                    max_retries=0)
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
                )
                envelope=curated.protocol_answer_envelope(
                    replace(plan,facts=tuple(facts)),language=turn_language)
                speech=envelope.speech_summary
                display=(
                    f"직접 답변\n{envelope.direct_answer}\n\n"
                    "근거 경계\n활성 프로토콜의 확인된 내용이며, "
                    "활성화된 경우에만 부족한 설명을 읽기 전용 참고자료에서 확인합니다."
                    if turn_language=="ko" else
                    f"Direct answer\n{envelope.direct_answer}\n\nSource boundary\n"
                    "The active protocol remains authoritative; missing explanation is checked read-only."
                )
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
                }
                session.begin_research(turn_id,generation)
                await current_text(
                    "research.state",turn_id=turn_id,status="pending",
                    phase="approved_references",
                    correlation_id=f"research-{generation}-{turn_id}",
                )
                async def enrichment_budget_notice()->None:
                    budget=(
                        session.external_reference_settings.user_visible_enrichment_budget_seconds
                        if session.external_reference_settings.enabled else 4.0
                    )
                    await asyncio.sleep(budget)
                    if (
                        (turn_id,generation) in session.research_operations
                        and session.is_current(turn_id,generation)
                    ):
                        await current_text(
                            "research.state",turn_id=turn_id,
                            status="background_bounded",
                            phase="optional_enrichment",
                            user_visible_budget_ms=round(budget*1000),
                            total_deadline_ms=round(min(30.0,max(
                                10.0,
                                (session.external_reference_settings.timeout_seconds
                                 if session.external_reference_settings.enabled else 0.0)
                                +(session.supplemental_knowledge_settings.timeout_seconds
                                  if session.supplemental_knowledge_settings.enabled else 0.0),
                            ))*1000),
                            correlation_id=f"research-{generation}-{turn_id}",
                        )
                session.track_visual_task(asyncio.create_task(
                    enrichment_budget_notice()))
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
                        report=await asyncio.to_thread(
                            _record_experiment_report_plan,
                            session,curated,plan,
                            turn_id=turn_id,generation=generation,
                            pre_transition_index=pre_transition_index,
                        )
                    except Exception:
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
                            if turn_language=="ko":
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
                session.experiment_report_store is not None
                and not report_prepared
                and plan.action in _EXPERIMENT_REPORT_ACTIONS
                and not (
                    session.experiment_state_version is not None
                    and plan.state_changed
                )
            ):
                try:
                    report=await asyncio.to_thread(
                        _record_experiment_report_plan,
                        session,curated,plan,
                        turn_id=turn_id,generation=generation,
                        pre_transition_index=pre_transition_index,
                    )
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
                    plan=_acknowledge_report_persistence(plan,turn_language)
                    await report_state(report)
            workspace_observation_requested=bool(
                plan.reported_observation or plan.reported_anomaly
            )
            workspace_observation_required=bool(
                plan.action is CuratedProtocolAction.RECORD_OBSERVATION
                and plan.reported_observation
            )
            if workspace_observation_requested:
                if session.experiment_state_version is None:
                    if workspace_observation_required:
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
                        if plan.action is CuratedProtocolAction.RECORD_OBSERVATION:
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
                    experiment_state=await asyncio.to_thread(
                        _record_workspace_experiment_progress,
                        session,curated,plan,
                        turn_id=turn_id,generation=generation,
                        pre_transition_index=pre_transition_index,
                    )
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
                and plan.action in _EXPERIMENT_REPORT_ACTIONS
                and workflow_mutation_committed
                and not report_prepared
            ):
                try:
                    report=await asyncio.to_thread(
                        _record_experiment_report_plan,
                        session,curated,plan,
                        turn_id=turn_id,generation=generation,
                        pre_transition_index=pre_transition_index,
                    )
                except Exception:
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
                    plan=_acknowledge_report_persistence(plan,turn_language)
                    await report_state(report)
            # Durable workspace persistence is the mutation gate; report
            # persistence is the reporting-acknowledgement gate. Re-read the
            # possibly replaced plan only after both so neither display nor
            # TTS can use pre-persistence success language.
            #
            # Publish the semantic and persistence outcomes as two separate
            # facts, both settled. Playback is a third, independent fact that
            # arrives later: a researcher who interrupts the spoken
            # acknowledgement stops audio, and stopping audio must never relabel
            # a transaction the server already committed.
            await current_text(
                "turn.outcome",turn_id=turn_id,generation=generation,
                workflow_outcome=(
                    "experiment_report_saved"
                    if plan.state_changed and report_prepared
                    else "workflow_state_saved" if plan.state_changed
                    else "clarification_required"
                    if plan.speech_mode.value=="blocked"
                    else "no_change"),
                state_changed=bool(plan.state_changed),
                report_persisted=bool(report_prepared),
                workflow_state_persisted=bool(workflow_mutation_committed),
            )
            display_text=plan.display_text
            speech_text=plan.speech_text
            speech_policy=getattr(plan,"speech_policy","speak")
            if not isinstance(display_text,str) or not display_text.strip():
                raise RuntimeError("curated protocol produced no display text")
            if speech_policy=="speak":
                if not isinstance(speech_text,str) or not speech_text.strip():
                    raise RuntimeError("curated protocol produced no speech text")
                timings["first_tts_request_ms"]=round((clock()-endpoint)*1000)
                await progress("synthesizing",route="curated_protocol")
                pcm=await asyncio.to_thread(synthesize,speech_text,turn_language)
                frames=frame_complete_audio(pcm)
                if filler is not None:await filler.primary_ready()
                if not frames or not session.start_playback(turn_id):
                    raise RuntimeError("curated protocol produced no playable audio")
            else:
                frames=[]
                if filler is not None:
                    await filler.cancel()
                session.complete_without_playback(turn_id)
        except asyncio.CancelledError:
            if brain_run is not None:
                brain_run.cancel()
            if not workflow_mutation_committed:
                curated._restore(checkpoint)
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
            CuratedProtocolAction.PAUSE:"workflow_paused",
            CuratedProtocolAction.RESUME:"workflow_resumed",
            CuratedProtocolAction.START_TIMER:"step_timer_started",
            CuratedProtocolAction.TIMER_STATUS:"step_timer_status_read",
            CuratedProtocolAction.PREVIEW_STEP:"step_preview_read",
            CuratedProtocolAction.STEP_RANGE:"step_range_read",
            CuratedProtocolAction.LAB_DOMAIN_QA:"lab_domain_qa_read",
            CuratedProtocolAction.REPORT_HANDOFF:"report_handoff_requested",
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
            transcript_corrections=[
                {"from":observed,"to":canonical}
                for observed,canonical in plan.transcript_corrections
            ],
            requested_entities=list(plan.requested_entities),
            question_dimensions=list(plan.question_dimensions),
            source_plan_scopes=list(plan.source_plan_scopes),
            unresolved_dimensions=list(plan.unresolved_dimensions),
            display_document=getattr(plan, "display_document", None))
        await current_text(
            "state.changed",state=session.state.value,turn_id=turn_id)
        if speech_policy=="speak":
            await sender.segment(turn_id,0,frames,generation)
        await current_text(
            "reply.complete",turn_id=turn_id,text=display_text,
            primary_text=plan.primary_text,
            source_texts=list(plan.source_texts),
            source_pages=list(plan.source_pages),
            evidence_ids=list(plan.evidence_ids),
            translation_status=plan.translation_status,
            source_language="en",speech_text=speech_text,
            answer_origin=plan.answer_origin,
            citations=list(plan.citations),
            display_document=getattr(plan,"display_document",None))
        await current_text(
            "audio.complete",turn_id=turn_id,segment_count=1 if speech_policy=="speak" else 0)
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
            segment_count=1 if speech_policy=="speak" else 0,input_frames=input_frames,
            output_frames=len(frames),tools_used=curated_tools_used,
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
        source_output=None
        visual_output=None
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
            if visual_kind=="web_photo" and existing_visual is None:
                web_visual_settings=session.web_visual_settings
                if web_visual_settings.enabled:
                    await _queue_curated_web_visual(
                        session=session,sender=sender,turn_id=turn_id,
                        generation=generation,endpoint=endpoint,clock=clock,
                        curated=curated,settings=web_visual_settings,
                        requested_entities=plan.requested_entities,
                        visual_intent=plan.visual_intent)
                else:
                    fixture=curated.fixture;step=fixture.steps[curated.current_index]
                    await current_text(
                        "protocol.visual.state",turn_id=turn_id,
                        protocol_id=fixture.protocol_id,step_id=step.step_id,
                        source_document_hash=fixture.source_pdf_sha256,
                        visual_job_id=hashlib.sha256(
                            f"web-unavailable\x1f{fixture.source_pdf_sha256}\x1f{step.step_id}".encode()
                        ).hexdigest(),status="visual_failed",
                        visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                        fallback="feature_disabled")
            elif existing_visual is None and visual_kind!="no_visual":
                visual_settings=session.generated_visual_settings
                visual_spec=(
                    _curated_visual_specification(curated)
                    if visual_settings.enabled else None)
                if visual_spec is not None:
                    await _queue_curated_generated_visual(
                        session=session,sender=sender,turn_id=turn_id,
                        generation=generation,endpoint=endpoint,clock=clock,
                        specification=visual_spec,settings=visual_settings)
                else:
                    fixture=curated.fixture;step=fixture.steps[curated.current_index]
                    await current_text(
                        "protocol.visual.state",turn_id=turn_id,
                        protocol_id=fixture.protocol_id,step_id=step.step_id,
                        source_document_hash=fixture.source_pdf_sha256,
                        visual_job_id=hashlib.sha256(
                            f"generated-unavailable\x1f{fixture.source_pdf_sha256}\x1f{step.step_id}".encode()
                        ).hexdigest(),status="visual_failed",
                        visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                        fallback="feature_disabled")
            elif existing_visual is None and visual_kind=="no_visual":
                fixture=curated.fixture;step=fixture.steps[curated.current_index]
                await current_text(
                    "protocol.visual.state",turn_id=turn_id,
                    protocol_id=fixture.protocol_id,step_id=step.step_id,
                    source_document_hash=fixture.source_pdf_sha256,
                    visual_job_id=hashlib.sha256(
                        f"no-visual\x1f{fixture.source_pdf_sha256}\x1f{step.step_id}".encode()
                    ).hexdigest(),status="visual_failed",
                    visual_ready_ms=max(0,round((clock()-endpoint)*1000)),
                    fallback="planner_no_visual")
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
        await current_text(
            "turn.route_decision",turn_id=turn_id,
            normalized_text=request_arbitration.normalized_text,
            intent=request_arbitration.intent.value,
            confidence=request_arbitration.confidence,
            reason_code=request_arbitration.reason_code,
            dimensions=list(request_arbitration.dimensions),
            runtime_router="deterministic_procedure",
            action=deterministic_tool,
            state_mutation=True,
            answer_origin="server_workflow_state",
            fallback_reason=None,
        )
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
        await current_text(
            "turn.route_decision",turn_id=turn_id,
            normalized_text=request_arbitration.normalized_text,
            intent=request_arbitration.intent.value,
            confidence=request_arbitration.confidence,
            reason_code=request_arbitration.reason_code,
            dimensions=list(request_arbitration.dimensions),
            runtime_router="brain",
            action="tool_or_grounded_response",
            state_mutation=False,
            answer_origin="pending",
            fallback_reason=None,
        )
        await progress("composing",route="brain")
        client=AsyncOpenAI(base_url=api_url(""),api_key=require_env("XAI_API_KEY")); client.model=require_env("CHAT_MODEL")
        result=await stream_brain_turn(
            client,session.history,transcript,sentence,mark_token,tool_event,
            tool_context=turn_context,arbitration=request_arbitration)
        if result.tool_ms is not None: timings["tool_ms"]=result.tool_ms
        await queue.put(None); await consumer
        if not first_audio: raise RuntimeError("Grok produced no playable spoken response")
        if not await current_text("reply.complete",turn_id=turn_id,text=result.text): return
        if not await current_text("audio.complete",turn_id=turn_id,segment_count=segment_count): return
        timings["total_ms"]=round((clock()-endpoint)*1000)
        if not await current_text("turn.done",turn_id=turn_id,timings_ms=timings,segment_count=segment_count,input_frames=input_frames,output_frames=output_frames,tools_used=result.tools_used,route="brain"): return
        _record_workspace_metric(
            category="agent",metric_name="turn_route",
            metric_value=float(timings.get("total_ms",0)),
            dimensions={
                "intent":request_arbitration.intent.value,
                "route":"brain",
                "answer_origin":"model_or_tool",
                "reason_code":request_arbitration.reason_code or "none",
                "status":"complete",
            },
        )
        if not session.is_current(turn_id,generation): return
        session.history.commit(result.messages,result.source_references)
    finally:
        if not consumer.done(): consumer.cancel()
        while not queue.empty():
            try: queue.get_nowait()
            except asyncio.QueueEmpty: break

async def run_turn_safely(
    websocket,session,source_pcm,turn_id,input_frames,voiced_frames=0,
    retained_prefix_frames=0,
    *,accepted_transcription:Transcription|None=None,
    accepted_stt_ms:int|None=None,
    accepted_stt_context:CascadeTranscriptionContext|None=None,
):
    generation=session.turn_generations.get(turn_id,session.generation)
    _record_workspace_metric(
        category="voice",metric_name="voice_turn",
        dimensions={"status":"accepted_endpoint","route":"cascade"},
    )
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
        retained_prefix_frames,
        sender=sender,filler=filler,
        accepted_transcription=accepted_transcription,
        accepted_stt_ms=accepted_stt_ms,
        accepted_stt_context=accepted_stt_context)
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
        pcm=await asyncio.to_thread(synthesize,text,language)
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

@app.websocket("/ws")
async def voice_socket(websocket:WebSocket):
    await websocket.accept()
    workspace_context_token=None
    try:
        settings=_workspace_settings()
        operational=_runtime_usage_scope()=="operational"
        if settings.enabled or operational:
            if not settings.enabled:
                raise IdentityConfigurationError(
                    "Operational scope requires the tenant workspace.")
            headers=getattr(websocket,"headers",{})
            query_params=getattr(websocket,"query_params",{})
            principal=_identity_resolver().resolve(
                headers.get("authorization"),
                dev_profile_id=(
                    headers.get("x-voice-dev-profile")
                    or query_params.get("dev_profile")
                ),
            )
            workspace=initialize_workspace_store(settings)
            try:
                workspace.bootstrap_principal(principal)
                principal=workspace.effective_principal(principal)
            finally:
                workspace.close()
            workspace_context_token=_REQUEST_PRINCIPAL.set(principal)
    except Exception as exc:
        await websocket.send_text(event(
            "error",message=getattr(exc,"code","authentication_failed")))
        await websocket.close(code=1008,reason="authentication required")
        return
    try:
        vad_settings=VoiceVadSettings.from_environment()
        config=VadConfig.from_settings(vad_settings.cascade)
        report_settings=ExperimentReportSettings.from_environment()
        external_settings=ExternalReferenceSettings.from_environment()
        supplemental_settings=SupplementalKnowledgeSettings.from_environment()
        multi_brain_settings=MultiBrainSettings.from_environment()
        web_visual_settings=WebVisualSettings.from_environment(external_settings)
        generated_visual_settings=GeneratedVisualSettings.from_environment()
    except (ConfigurationError,ValueError) as exc:
        await websocket.send_text(event(
            "error",message=f"invalid non-secret configuration: {exc}"))
        await websocket.close(code=1008,reason="invalid configuration")
        return
    research_capabilities={
        "external_text":external_settings.public_capability(),
        "supplemental_model":supplemental_settings.public_capability(),
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
        "multi_brain":multi_brain_settings.public_capability(),
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
        supplemental_knowledge_settings=supplemental_settings,
        web_visual_settings=web_visual_settings,
        generated_visual_settings=generated_visual_settings,
        multi_brain_settings=multi_brain_settings,
    ); task=None; trusted_config=None; procedure_store=None
    curated_fixture=None
    sender=LockedSender(websocket); pipeline="cascade"
    await websocket.send_text(event("ready",sample_rate=16000,
                                    pipelines=["cascade"],frame_ms=20,
                                    frame_bytes=FRAME_BYTES,vad_mode=config.mode,
                                    endpoint_silence_ms=config.endpoint_silence_frames*20,
                                    prefix_padding_ms=config.prefix_frames*20,
                                    barge_in_prefix_ms=config.barge_in_prefix_frames*20,
                                    voice_profile={
                                        "pipeline":"cascade",
                                        "provider":"xai",
                                        "voice_id":_tts_voice(),
                                        "persona":"professor",
                                    },
                                    research_capabilities=research_capabilities))
    try:
        while True:
            message=await websocket.receive()
            if message.get("type")=="websocket.disconnect": break
            if message.get("bytes") is not None:
                if session.refresh_cooldown(): await websocket.send_text(event("state.changed",state="IDLE"))
                listener_events=[]
                accepted_interrupts={}
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
                            item,"transcription_failed")
                        if rejected is not None:
                            listener_events.append(rejected)
                        continue
                    # Fail-safe: a short, explicit stop or pause is honoured
                    # before any other admission test. Refusing to stop is the
                    # more dangerous failure at a bench, so this path is
                    # deliberately reachable even for an unattributed voice.
                    priority_stop=is_priority_stop_command(transcription.text)
                    input_decision=classify_input_event(
                        transcription,
                        keyterms=stt_context.keyterms,
                        duration_seconds=transcription.duration_seconds,
                    )
                    if not input_decision.accepted and not priority_stop:
                        rejected=session.reject_interrupt_candidate(
                            item,input_decision.reason or "non_speech")
                        if rejected is not None:
                            listener_events.append(rejected)
                        continue
                    committed=session.commit_interrupt_candidate(
                        item,stt_ms=stt_ms,
                        reason=(
                            "priority_stop" if priority_stop
                            else "confirmed_speech"))
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
                    if item.kind=="assistant.interrupted":
                        interruption_reason=str(
                            item.reason or "confirmed_speech"
                        )[:100]
                        _record_workspace_metric(
                            category="voice",metric_name="barge_in_confirmed",
                            dimensions={
                                "status":"confirmed",
                                "reason_code":interruption_reason,
                            },
                        )
                        _record_workspace_metric(
                            category="voice",metric_name="playback_interruption",
                            dimensions={
                                "status":"playback_only",
                                "reason_code":interruption_reason,
                            },
                        )
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
                                    accepted["context"] if accepted else None)))
                continue
            if message.get("text") is None:continue
            control=parse_control(message["text"])
            if control["type"]=="session.start":
                if session.active:raise ProtocolError("session already active")
                requested_mode=control["mode"]
                configuration_id=control["configuration_id"]
                requested_protocol_id=control["protocol_id"]
                requested_input_language=normalize_input_language_preference(
                    control.get("input_language",control["language"])
                )
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
                                    # A reviewed, execution-approved catalog
                                    # revision of the same protocol supersedes
                                    # the development fixture, so a reviewer
                                    # decision actually changes what runs.
                                    superseded=False
                                    if _protocol_store_settings().enabled:
                                        catalog,protocol_store=_open_protocol_catalog()
                                        try:
                                            superseded=(
                                                catalog
                                                .development_fixture_is_superseded(
                                                    curated_fixture)
                                            )
                                        finally:
                                            protocol_store.close()
                                    if not superseded:
                                        selected_curated_fixture=curated_fixture
                                        selected_revision_id=getattr(
                                            curated_fixture,"revision_id",
                                            f"fixture-{requested_protocol_id}")
                            if selected_curated_fixture is None:
                                # The shared curated development fixture is not
                                # tenant-owned and is exempt above; every other
                                # protocol/procedure selection stays tenant-scoped.
                                configuration_stage="session_language"
                                _scope_catalog_resource(requested_protocol_id)
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
                    _seed_experiment_participants(session)
                    if session.curated_protocol_session is not None and selected_curated_fixture is not None:
                        try:
                            log.info(
                                "safety_pack.resolve.start: protocol_id=%s revision=%s catalog=%s",
                                requested_protocol_id,
                                selected_revision_id,
                                bool(trusted_config.catalog_path),
                            )
                            safety_pack = resolve_safety_pack(
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
                                status="unavailable",
                                error_reason=str(exc),
                            )

                        session.curated_protocol_session.set_safety_pack(safety_pack)
                        session.curated_protocol_session.activate_configured()
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
                    )
                    if (
                        recovery_session_id is not None
                        and experiment_state is not None
                        and session.curated_protocol_session is not None
                        and experiment_state["status"]=="in_progress"
                    ):
                        session.curated_protocol_session.restore_experiment_progress(
                            current_step_id=str(experiment_state["current_step_id"]),
                            completed_step_ids=tuple(
                                str(item["step_id"])
                                for item in experiment_state["completed_steps"]
                            ),
                        )
                    pipeline="cascade"
                    session.accept_configuration(
                        configuration_id,pipeline,context.language,
                        requested_protocol_id,selected_revision_id,
                        requested_input_language)
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
                ready_fields={
                    "configuration_id":session.accepted_configuration_id,
                    "generation":session.generation,
                    "mode":session.accepted_mode,
                    "language":session.accepted_language,
                    "input_language":session.accepted_input_language.value,
                    "protocol_id":session.accepted_protocol_id,
                    "research_capabilities":research_capabilities,
                }
                if session.accepted_protocol_id is not None:
                    ready_fields["revision_id"]=session.accepted_revision_id
                ready_fields["experiment_session_id"]=session.session_id
                if session.experiment_state_version is not None:
                    ready_fields["experiment_session_version"]=(
                        session.experiment_state_version)
                    ready_fields["experiment_recovered"]=(
                        recovery_session_id is not None)
                await websocket.send_text(event("session.ready",**ready_fields))
                if experiment_state is not None:
                    await websocket.send_text(event(
                        "experiment.session.state",state=experiment_state))
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
                if task and not task.done():
                    await _finish_all_research_operations(
                        sender,session,"cancelled")
                    task.cancel()
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
                    if context and session.tool_context and session.tool_context.procedure_controller:
                        context=ToolContext(context.catalog_path,context.facility_id,context.language,
                                            context.usage_scope,context.report_language,
                                            session.tool_context.procedure_controller)
                    session.set_language_mode(control["mode"],context)
                except (RuntimeError,ValueError):
                    await websocket.send_text(event("error",message="invalid language mode"))
                    continue
                if task and not task.done():
                    await _finish_all_research_operations(
                        sender,session,"cancelled")
                    task.cancel()
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
                session.reset_sensitive_state()
                if session.tool_context and session.tool_context.procedure_controller:
                    session.tool_context.procedure_controller.detach()
                await websocket.send_text(event("session.reset",state=session.state.value))
                if session.curated_protocol_session is not None:
                    fixture_state = session.curated_protocol_session.state()
                    await websocket.send_text(event("protocol.fixture.state", state=fixture_state))
                else:
                    await websocket.send_text(event("procedure.state",state=unattached_procedure_state()))
                await websocket.send_text(event("session.language_state",mode=session.language_mode,
                                                language=session.manual_language))
            elif control["type"]=="session.stop":
                if task and not task.done():
                    await _finish_all_research_operations(
                        sender,session,"cancelled")
                    task.cancel()
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
            elif control["type"]=="workflow.pause":
                if session.active and session.curated_protocol_session is not None:
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
                            session.curated_protocol_session.resume_workflow()
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
                    fixture_state=session.curated_protocol_session.state()
                    await websocket.send_text(event(
                        "protocol.fixture.state",
                        configuration_id=session.accepted_configuration_id,
                        action="pause",
                        state=fixture_state,
                    ))
            elif control["type"]=="workflow.resume":
                if session.active and session.curated_protocol_session is not None:
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
                    fixture_state=session.curated_protocol_session.state()
                    await websocket.send_text(event(
                        "protocol.fixture.state",
                        configuration_id=session.accepted_configuration_id,
                        action="resume",
                        state=fixture_state,
                    ))
            elif control["type"]=="workflow.start_protocol":
                curated=session.curated_protocol_session
                control_identity_valid=bool(
                    session.active
                    and curated is not None
                    and not curated.active
                    and curated.workflow_status in {"preview","ready"}
                    and control["configuration_id"]
                    ==session.accepted_configuration_id
                    and control["generation"]==session.generation
                )
                if not control_identity_valid:
                    await websocket.send_text(event(
                        "workflow.action.result",
                        action="start_protocol",
                        configuration_id=session.accepted_configuration_id,
                        generation=session.generation,
                        state_mutation=False,
                        message=(
                            "세션이 바뀌었거나 프로토콜을 시작할 수 없는 상태입니다. "
                            "화면을 새로 확인해 주세요. 실험 상태는 변경하지 않았습니다."
                        ),
                    ))
                    continue
                assert curated is not None
                restore_point=curated._checkpoint()
                pre_transition_index=curated.current_index
                control_turn_id=1_100_000_000+curated._revision
                plan=curated.plan(
                    "프로토콜 시작",
                    turn_id=control_turn_id,
                    language=session.accepted_language or "ko",
                    configuration_id=session.accepted_configuration_id,
                    generation=session.generation,
                )
                experiment_state=None
                try:
                    if not plan.state_changed:
                        raise WorkspaceError(
                            "The deterministic protocol start was not admitted."
                        )
                    experiment_state=await asyncio.to_thread(
                        _record_workspace_experiment_progress,
                        session,curated,plan,
                        turn_id=control_turn_id,
                        generation=session.generation,
                        pre_transition_index=pre_transition_index,
                        capture_source="manual",
                    )
                    if experiment_state is None:
                        raise WorkspaceError(
                            "Experiment progress persistence is unavailable."
                        )
                except Exception as exc:
                    curated._restore(restore_point)
                    _record_workspace_metric(
                        category="workflow",metric_name="mutation_failure",
                        dimensions={
                            "status":"rolled_back",
                            "reason_code":str(
                                getattr(exc,"code","workspace_error"))[:100],
                            "event_kind":"start_protocol",
                        },
                    )
                    await websocket.send_text(event(
                        "experiment.session.error",
                        code=getattr(exc,"code","workspace_error"),
                    ))
                    await websocket.send_text(event(
                        "protocol.fixture.state",
                        configuration_id=session.accepted_configuration_id,
                        action="start_protocol",
                        state=curated.state(),
                    ))
                    await websocket.send_text(event(
                        "workflow.action.result",
                        action="start_protocol",
                        configuration_id=session.accepted_configuration_id,
                        generation=session.generation,
                        state_mutation=False,
                        message=(
                            "실험 세션을 저장하지 못해 프로토콜 시작을 확정하지 "
                            "않았습니다. 시작 전 상태는 그대로입니다."
                        ),
                    ))
                    continue
                if session.experiment_report_store is not None:
                    try:
                        report=await asyncio.to_thread(
                            _record_experiment_report_plan,
                            session,curated,plan,
                            turn_id=control_turn_id,
                            generation=session.generation,
                            pre_transition_index=pre_transition_index,
                        )
                    except Exception:
                        await websocket.send_text(event(
                            "experiment.report.error",
                            code="report_persistence_failed",
                        ))
                    else:
                        await websocket.send_text(event(
                            "experiment.report.state",report=report,
                            configuration_id=session.accepted_configuration_id,
                            generation=session.generation,
                        ))
                await websocket.send_text(event(
                    "experiment.session.state",state=experiment_state,
                ))
                await websocket.send_text(event(
                    "protocol.fixture.state",
                    configuration_id=session.accepted_configuration_id,
                    action="start_protocol",
                    state=curated.state(spoken_summary=plan.spoken_summary),
                ))
                await websocket.send_text(event(
                    "workflow.action.result",
                    action="start_protocol",
                    configuration_id=session.accepted_configuration_id,
                    generation=session.generation,
                    state_mutation=True,
                    message=(
                        "프로토콜 시작을 저장했습니다. 승인된 1단계 화면을 "
                        "확인한 뒤 수동 완료 기능으로 진행하세요."
                    ),
                ))
            elif control["type"]=="workflow.complete_current_step":
                curated=session.curated_protocol_session
                current_step=(
                    curated.fixture.steps[curated.current_index]
                    if session.active and curated is not None and curated.active
                    else None
                )
                control_identity_valid=bool(
                    session.active
                    and curated is not None
                    and current_step is not None
                    and control["configuration_id"]
                    ==session.accepted_configuration_id
                    and control["generation"]==session.generation
                    and control["step_id"]==current_step.step_id
                )
                checkpoint=(
                    curated.active_human_checkpoint()
                    if control_identity_valid and curated is not None else None
                )
                if not control_identity_valid or checkpoint is not None:
                    message=(
                        "현재 단계는 연구자 확인 지점입니다. 화면의 조건 충족 여부를 직접 선택해 주세요. 실험 상태는 변경하지 않았습니다."
                        if checkpoint is not None else
                        "현재 단계가 바뀌었거나 세션을 확인할 수 없습니다. 화면을 새로 확인해 주세요. 실험 상태는 변경하지 않았습니다."
                    )
                    await websocket.send_text(event(
                        "workflow.action.result",
                        action="complete_current_step",
                        configuration_id=session.accepted_configuration_id,
                        generation=session.generation,
                        step_id=(current_step.step_id if current_step else None),
                        state_mutation=False,
                        message=message,
                    ))
                    continue
                assert curated is not None and current_step is not None
                restore_point=curated._checkpoint()
                pre_transition_index=curated.current_index
                control_turn_id=1_000_000_000+curated._revision
                plan=curated.plan(
                    "현재 단계 완료",
                    turn_id=control_turn_id,
                    language=session.accepted_language or "ko",
                    configuration_id=session.accepted_configuration_id,
                    generation=session.generation,
                )
                experiment_state=None
                if plan.state_changed:
                    try:
                        experiment_state=await asyncio.to_thread(
                            _record_workspace_experiment_progress,
                            session,curated,plan,
                            turn_id=control_turn_id,
                            generation=session.generation,
                            pre_transition_index=pre_transition_index,
                            capture_source="manual",
                        )
                        if experiment_state is None:
                            raise WorkspaceError(
                                "Experiment progress persistence is unavailable."
                            )
                    except Exception as exc:
                        curated._restore(restore_point)
                        _record_workspace_metric(
                            category="workflow",metric_name="mutation_failure",
                            dimensions={
                                "status":"rolled_back",
                                "reason_code":str(
                                    getattr(exc,"code","workspace_error"))[:100],
                                "event_kind":"complete_current_step",
                            },
                        )
                        await websocket.send_text(event(
                            "experiment.session.error",
                            code=getattr(exc,"code","workspace_error"),
                        ))
                        await websocket.send_text(event(
                            "protocol.fixture.state",
                            configuration_id=session.accepted_configuration_id,
                            action="complete_current_step",
                            state=curated.state(),
                        ))
                        await websocket.send_text(event(
                            "workflow.action.result",
                            action="complete_current_step",
                            configuration_id=session.accepted_configuration_id,
                            generation=session.generation,
                            step_id=current_step.step_id,
                            state_mutation=False,
                            message=(
                                "실험 세션을 저장하지 못해 단계를 완료로 확정하지 않았습니다. 현재 단계는 그대로입니다."
                            ),
                        ))
                        continue
                    if session.experiment_report_store is not None:
                        try:
                            report=await asyncio.to_thread(
                                _record_experiment_report_plan,
                                session,curated,plan,
                                turn_id=control_turn_id,
                                generation=session.generation,
                                pre_transition_index=pre_transition_index,
                            )
                        except Exception:
                            await websocket.send_text(event(
                                "experiment.report.error",
                                code="report_persistence_failed",
                            ))
                        else:
                            await websocket.send_text(event(
                                "experiment.report.state",report=report,
                                configuration_id=session.accepted_configuration_id,
                                generation=session.generation,
                            ))
                if experiment_state is not None:
                    await websocket.send_text(event(
                        "experiment.session.state",state=experiment_state,
                    ))
                await websocket.send_text(event(
                    "protocol.fixture.state",
                    configuration_id=session.accepted_configuration_id,
                    action="complete_current_step",
                    state=curated.state(spoken_summary=plan.spoken_summary),
                ))
                await websocket.send_text(event(
                    "workflow.action.result",
                    action="complete_current_step",
                    configuration_id=session.accepted_configuration_id,
                    generation=session.generation,
                    step_id=current_step.step_id,
                    state_mutation=bool(plan.state_changed),
                    message=(
                        f"{current_step.source_label}단계를 완료로 저장했습니다. "
                        + (
                            f"현재는 {curated.fixture.steps[curated.current_index].source_label}단계입니다."
                            if curated.active else
                            "프로토콜의 모든 단계를 완료했습니다."
                        )
                    ),
                ))
            elif control["type"]=="workflow.human_checkpoint":
                curated=session.curated_protocol_session
                checkpoint=(
                    curated.active_human_checkpoint()
                    if session.active and curated is not None else None
                )
                # The explicit bench action is admitted only for the checkpoint
                # and step the researcher is actually looking at, so a stale
                # screen can never move a live workflow.
                if (
                    checkpoint is None
                    or control["configuration_id"]
                    !=session.accepted_configuration_id
                    or control["generation"]!=session.generation
                    or checkpoint.checkpoint_id!=control["checkpoint_id"]
                    or curated.fixture.steps[curated.current_index].step_id
                    !=control["step_id"]
                ):
                    await websocket.send_text(event(
                        "error",message="human_checkpoint_not_active"))
                    continue
                pre_transition_index=curated.current_index
                # Same discipline as every other mutation path: take a restore
                # point, mutate, and roll the in-memory session back to its
                # exact pre-mutation state if the durable record cannot be
                # written. Canonical state never runs ahead of the ledger.
                try:
                    outcome=await _confirm_and_persist_human_checkpoint(
                        session,curated,control["decision"],
                        pre_transition_index=pre_transition_index,
                    )
                except Exception as exc:
                    _record_workspace_metric(
                        category="workflow",metric_name="mutation_failure",
                        dimensions={
                            "status":"rolled_back",
                            "reason_code":str(
                                getattr(exc,"code","workspace_error"))[:100],
                            "event_kind":"human_checkpoint",
                        },
                    )
                    log.warning(
                        "human checkpoint update failed decision=%s error=%s",
                        control["decision"],type(exc).__name__,
                    )
                    await websocket.send_text(event(
                        "error",message=getattr(exc,"code","workspace_error")))
                    await websocket.send_text(event(
                        "protocol.fixture.state",
                        configuration_id=session.accepted_configuration_id,
                        action="human_checkpoint",
                        decision=control["decision"],
                        outcome="persistence_failed",
                        state=curated.state(),
                    ))
                    continue
                fixture_state=curated.state()
                await websocket.send_text(event(
                    "protocol.fixture.state",
                    configuration_id=session.accepted_configuration_id,
                    action="human_checkpoint",
                    decision=control["decision"],
                    outcome=outcome.status,
                    state=fixture_state,
                ))
            elif control["type"]=="session.speaker.confirm":
                # Diarization labels are acoustic, session-scoped and not
                # identity. Only a human confirmation maps one to a participant
                # the server already knows, and the mapping dies with the
                # session - nothing biometric is derived or stored.
                confirmed=session.participants.confirm_label(
                    control["speaker_label"],control["participant_id"])
                await websocket.send_text(event(
                    "session.speaker.state",
                    speaker_label=control["speaker_label"],
                    confirmed=confirmed,
                    speaker_identity_verified=False,
                    reason=(
                        "confirmed_by_participant" if confirmed
                        else "participant_not_on_roster"),
                    confirmed_labels=len(session.participants.confirmed_labels),
                ))
            elif control["type"]=="session.speaker.release":
                released=session.participants.release_label(
                    control["speaker_label"])
                await websocket.send_text(event(
                    "session.speaker.state",
                    speaker_label=control["speaker_label"],
                    confirmed=False,speaker_identity_verified=False,
                    reason="released" if released else "unknown_label",
                    confirmed_labels=len(session.participants.confirmed_labels),
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
                                report = store.get_report(report_id)
                            except (KeyError, ValueError):
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
            elif control["type"]=="report.status.get":
                try:
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
                except Exception as err:
                    log.warning("report.status.get failed non-fatally: %s", err)
                    await websocket.send_text(event(
                        "report.status",
                        report_id=control.get("report_id", "unknown"),
                        status="error",
                        report_status="lookup_failed",
                        attempts=1,
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
                    RUNTIME_METRICS.observe("playback.completed",{
                        "turn_id":control["turn_id"],
                        "generation":generation,
                        "playback_completion_ms":playback_completion_ms,
                    })
                await websocket.send_text(event(
                    "state.changed",state=session.state.value,
                    turn_id=control["turn_id"],generation=generation,
                    cooldown_ms=config.cooldown_ms))
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
        if procedure_store is not None: procedure_store.close()
        if workspace_context_token is not None:
            _REQUEST_PRINCIPAL.reset(workspace_context_token)

app.mount("/",StaticFiles(directory=STATIC_DIR,html=True),name="static")
