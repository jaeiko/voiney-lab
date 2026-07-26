"""Deterministic, context-bound tools for the SafeBridge Voice agent."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from safebridge_voice.retrieval import TOPICS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "reports"
INBOX_PATH = REPORTS_DIR / "inbox.jsonl"
PROCESSED_PATH = REPORTS_DIR / "processed.txt"
STATUS_DIR = REPORTS_DIR / "status"
OUTBOX_DIR = PROJECT_ROOT / "outbox"

SEARCH_TOOL_NAME = "search_approved_safety_manual"
CREATE_REPORT_TOOL_NAME = "create_safety_report"
CHECK_REPORT_TOOL_NAME = "check_safety_report_status"
START_PROCEDURE_TOOL_NAME = "start_procedure"
GET_CURRENT_STEP_TOOL_NAME = "get_current_step"
COMPLETE_CURRENT_STEP_TOOL_NAME = "complete_current_step"
RECORD_STEP_OBSERVATION_TOOL_NAME = "record_step_observation"
START_STEP_TIMER_TOOL_NAME = "start_step_timer"
GET_WORKFLOW_SUMMARY_TOOL_NAME = "get_workflow_summary"
REPORT_ID_PATTERN = re.compile(r"^SR-[0-9]{8}-[0-9A-F]{6}$")
REPORT_WRITE_LOCK = threading.Lock()
DEDUPLICATION_WINDOW_SECONDS = 60


SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": SEARCH_TOOL_NAME,
        "description": (
            "Use this whenever a lab worker asks about a safety procedure or "
            "approved lab safety information. Search the trusted local catalog "
            "before answering; do not use it for greetings or ordinary "
            "conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The worker's non-empty safety question or keywords.",
                },
                "topic": {
                    "type": "string",
                    "enum": list(TOPICS),
                    "description": "An explicit validated safety topic.",
                },
            },
            "required": ["query", "topic"],
            "additionalProperties": False,
        },
    },
}


CREATE_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": CREATE_REPORT_TOOL_NAME,
        "description": (
            "Record a lab hazard, near miss, spill, exposure concern, damaged "
            "equipment, or other abnormal situation for human handoff. Collect "
            "the location, a factual summary, urgency, and exposure status before "
            "calling. This queues a report; it never replaces emergency contact."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Specific lab, room, bench, hood, or equipment location.",
                },
                "summary": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Short factual description using only details the worker gave.",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["emergency", "urgent", "routine"],
                    "description": (
                        "emergency = immediate danger now; urgent = prompt human "
                        "review needed; routine = non-immediate near miss or issue."
                    ),
                },
                "exposure_status": {
                    "type": "string",
                    "enum": ["yes", "no", "unknown"],
                    "description": "Whether a person may have been exposed; never guess.",
                },
                "language": {
                    "type": "string",
                    "enum": ["ko", "en", "vi"],
                    "description": "Language used by the worker.",
                },
                "material_or_equipment": {
                    "type": "string",
                    "description": (
                        "Chemical, sample, instrument, or equipment name if the "
                        "worker provided it. Omit when not known."
                    ),
                },
            },
            "required": ["location", "summary", "urgency", "exposure_status", "language"],
            "additionalProperties": False,
        },
    },
}


CHECK_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": CHECK_REPORT_TOOL_NAME,
        "description": (
            "Check whether a previously queued SafeBridge safety report is "
            "awaiting handoff, being retried, or has a manager handoff artifact."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "report_id": {
                    "type": "string",
                    "description": "SafeBridge report id, for example SR-20260722-A1B2C3.",
                },
            },
            "required": ["report_id"],
            "additionalProperties": False,
        },
    },
}

START_PROCEDURE_TOOL = {"type":"function","function":{
    "name":START_PROCEDURE_TOOL_NAME,
    "description":"Start one server-approved procedure by its stable procedure ID.",
    "parameters":{"type":"object","properties":{"procedure_id":{"type":"string","minLength":1}},
                  "required":["procedure_id"],"additionalProperties":False}}}
GET_CURRENT_STEP_TOOL = {"type":"function","function":{
    "name":GET_CURRENT_STEP_TOOL_NAME,
    "description":"Read the current approved step of the server-attached procedure.",
    "parameters":{"type":"object","properties":{},"required":[],"additionalProperties":False}}}
COMPLETE_CURRENT_STEP_TOOL = {"type":"function","function":{
    "name":COMPLETE_CURRENT_STEP_TOOL_NAME,
    "description":"Complete only the current step after explicit user confirmation.",
    "parameters":{"type":"object","properties":{"expected_step_id":{"type":"string","minLength":1}},
                  "required":["expected_step_id"],"additionalProperties":False}}}
RECORD_STEP_OBSERVATION_TOOL = {"type":"function","function":{
    "name":RECORD_STEP_OBSERVATION_TOOL_NAME,
    "description":(
        "Record a user-observed value against the current server-approved workflow "
        "step. Use only the value the user actually reported; never infer it."
    ),
    "parameters":{"type":"object","properties":{
        "expected_step_id":{"type":"string","minLength":1},
        "value":{"anyOf":[{"type":"string","minLength":1},{"type":"number"},{"type":"boolean"}]},
    },"required":["expected_step_id","value"],"additionalProperties":False}}}
START_STEP_TIMER_TOOL = {"type":"function","function":{
    "name":START_STEP_TIMER_TOOL_NAME,
    "description":(
        "Start the fixed-duration timer configured by the server for the current "
        "workflow step. Never choose or override the duration."
    ),
    "parameters":{"type":"object","properties":{
        "expected_step_id":{"type":"string","minLength":1},
    },"required":["expected_step_id"],"additionalProperties":False}}}
GET_WORKFLOW_SUMMARY_TOOL = {"type":"function","function":{
    "name":GET_WORKFLOW_SUMMARY_TOOL_NAME,
    "description":(
        "Read the server-owned workflow audit summary: completed steps, recorded "
        "observations, timers, and any linked human handoff."
    ),
    "parameters":{"type":"object","properties":{},"required":[],"additionalProperties":False}}}

PROCEDURE_TOOL_NAMES=frozenset({
    START_PROCEDURE_TOOL_NAME,GET_CURRENT_STEP_TOOL_NAME,COMPLETE_CURRENT_STEP_TOOL_NAME,
    RECORD_STEP_OBSERVATION_TOOL_NAME,START_STEP_TIMER_TOOL_NAME,
    GET_WORKFLOW_SUMMARY_TOOL_NAME})
TOOLS = [SEARCH_TOOL, CREATE_REPORT_TOOL, CHECK_REPORT_TOOL,
         START_PROCEDURE_TOOL,GET_CURRENT_STEP_TOOL,COMPLETE_CURRENT_STEP_TOOL,
         RECORD_STEP_OBSERVATION_TOOL,START_STEP_TIMER_TOOL,
         GET_WORKFLOW_SUMMARY_TOOL]


@dataclass(frozen=True)
class ToolContext:
    """Trusted retrieval inputs owned by the server, never by Tool JSON."""

    catalog_path: Path | None
    facility_id: str | None
    language: str
    usage_scope: str
    # Manager handoff language is trusted facility policy and never a Tool arg.
    report_language: str = "ko"
    procedure_controller: Any = None
    procedure_completion_authorized_step_id: str | None = None
    # Final server-owned transcript for the current turn. Procedure observations
    # are checked against this evidence so a model cannot shorten or alter a
    # spoken identifier before it is durably recorded.
    current_transcript: str | None = None


def _result(status: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, **fields}


def _observation_matches_transcript(value: Any, transcript: str) -> bool:
    """Require a recorded observation to be supported by the final transcript."""
    normalized_transcript = " ".join(transcript.split())
    if not normalized_transcript:
        return False
    if isinstance(value, bool):
        evidence = {
            True: ("true", "yes", "예", "맞아", "있음", "có"),
            False: ("false", "no", "아니", "없음", "không"),
        }[value]
        folded = normalized_transcript.casefold()
        return any(item in folded for item in evidence)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        token = re.escape(str(value))
        return re.search(rf"(?<![0-9.]){token}(?![0-9.])", normalized_transcript) is not None
    if not isinstance(value, str):
        return False
    cleaned = " ".join(value.split())
    if not cleaned:
        return False
    if re.search(r"[A-Za-z0-9]", cleaned):
        # ASCII identifiers require full token boundaries. This rejects a model
        # argument such as A-17 when the transcript actually contains A-170.
        token = re.escape(cleaned)
        return re.search(
            rf"(?<![A-Za-z0-9]){token}(?![A-Za-z0-9])",
            normalized_transcript,
            flags=re.IGNORECASE,
        ) is not None
    # Korean and Vietnamese particles can attach to a value in normal speech.
    return cleaned.casefold() in normalized_transcript.casefold()


def _search_failure(status: str) -> dict[str, Any]:
    return {"status": status, "answerable": False, "matches": []}


def search_approved_safety_manual(
    query: Any,
    *,
    context: ToolContext | None = None,
    topic: Any = None,
) -> dict[str, Any]:
    """Search SQLite only; the legacy demo JSON is never an implicit fallback."""
    from safebridge_voice.retrieval import search_safety_documents

    blocked = _search_failure("invalid_arguments")
    if (not isinstance(query, str) or not query.strip() or context is None or
            context.language not in ("ko", "en", "vi") or context.catalog_path is None):
        return blocked
    if not isinstance(topic, str) or topic not in TOPICS:
        return blocked
    try:
        result = search_safety_documents(
            query, context.language, context.catalog_path,
            usage_scope=context.usage_scope, facility_id=context.facility_id, topic=topic,
        )
        if result.get("status") != "success" or not result.get("answerable"):
            return _search_failure(result.get("status", "error"))
        allowed = {
            "document_id", "document_type", "title", "issuer", "manufacturer",
            "product_name", "product_code", "cas_numbers", "version", "canonical_version",
            "section_code", "section_title", "page_start", "page_end", "content",
            "language", "translation_status", "source_uri", "source_checksum",
        }
        matches = [{key: value for key, value in match.items() if key in allowed}
                   for match in result["matches"]]
        return {"status": "success", "answerable": True, "matches": matches}
    except Exception:
        return _search_failure("error")


def _clean_text(value: Any, field: str, maximum: int = 500) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, f"{field} is required"
    cleaned = " ".join(value.split())
    if len(cleaned) > maximum:
        return None, f"{field} is too long"
    return cleaned, None


def normalize_report_arguments(arguments: Any) -> dict[str, Any]:
    """Validate report input without causing any report side effects."""
    if not isinstance(arguments, dict):
        return _result("invalid_arguments", message="arguments must be an object")
    required = {"location", "summary", "urgency", "exposure_status", "language"}
    allowed = required | {"material_or_equipment"}
    if not required.issubset(arguments) or not set(arguments).issubset(allowed):
        return _result("invalid_arguments", message="unexpected or missing arguments")
    location, error = _clean_text(arguments["location"], "location", 160)
    if error:
        return _result("invalid_arguments", message=error)
    summary, error = _clean_text(arguments["summary"], "summary", 800)
    if error:
        return _result("invalid_arguments", message=error)
    if arguments["urgency"] not in ("emergency", "urgent", "routine"):
        return _result("invalid_arguments", message="urgency is invalid")
    if arguments["exposure_status"] not in ("yes", "no", "unknown"):
        return _result("invalid_arguments", message="exposure_status is invalid")
    if arguments["language"] not in ("ko", "en", "vi"):
        return _result("invalid_arguments", message="language is invalid")
    report = {"location": location, "summary": summary,
              "urgency": arguments["urgency"],
              "exposure_status": arguments["exposure_status"],
              "language": arguments["language"]}
    if arguments.get("material_or_equipment") is not None:
        material, error = _clean_text(arguments["material_or_equipment"], "material_or_equipment", 200)
        if error:
            return _result("invalid_arguments", message=error)
        report["material_or_equipment"] = material
    return _result("success", report=report)


def _report_fingerprint(report: dict[str, Any]) -> str:
    material = {key: report.get(key, "") for key in (
        "location",
        "summary",
        "urgency",
        "exposure_status",
        "language",
        "material_or_equipment",
    )}
    workflow=report.get("workflow")
    if isinstance(workflow,dict):
        material["workflow"]={
            key:workflow.get(key)
            for key in ("workflow_session_id","procedure_id","step_id")
        }
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True).casefold()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _new_report_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"SR-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


def create_safety_report(
    location: Any,
    summary: Any,
    urgency: Any,
    exposure_status: Any,
    language: Any,
    material_or_equipment: Any = None,
    *,
    inbox_path: Path = INBOX_PATH,
    now_epoch: float | None = None,
    workflow_context: dict[str,Any] | None = None,
) -> dict[str, Any]:
    """Validate and append one small report job, returning well under 100 ms locally."""
    normalized = normalize_report_arguments({
        "location": location, "summary": summary, "urgency": urgency,
        "exposure_status": exposure_status, "language": language,
        **({"material_or_equipment": material_or_equipment} if material_or_equipment is not None else {}),
    })
    if normalized["status"] != "success":
        return normalized
    values = normalized["report"]

    now_epoch = time.time() if now_epoch is None else now_epoch
    now = datetime.fromtimestamp(now_epoch, timezone.utc)
    report: dict[str, Any] = {
        "id": _new_report_id(now),
        **values,
        "filed_at": now.isoformat(),
        "filed_at_epoch": now_epoch,
    }
    if workflow_context is not None:
        try:
            encoded=json.dumps(
                workflow_context,ensure_ascii=False,separators=(",",":"))
            trusted=json.loads(encoded)
        except (TypeError,ValueError,json.JSONDecodeError):
            return _result("invalid_arguments",message="workflow context is invalid")
        if (not isinstance(trusted,dict) or len(encoded)>12000 or
                not isinstance(trusted.get("workflow_session_id"),str) or
                not isinstance(trusted.get("procedure_id"),str) or
                not isinstance(trusted.get("step_id"),str)):
            return _result("invalid_arguments",message="workflow context is invalid")
        report["workflow"]=trusted
    report["dedupe_key"] = _report_fingerprint(report)

    with REPORT_WRITE_LOCK:
        existing = _read_jsonl(inbox_path)
        for prior in reversed(existing):
            if (
                prior.get("dedupe_key") == report["dedupe_key"]
                and now_epoch - float(prior.get("filed_at_epoch", 0))
                <= DEDUPLICATION_WINDOW_SECONDS
            ):
                return _result(
                    "success",
                    report_id=prior["id"],
                    report_status="queued_for_handoff",
                    deduplicated=True,
                )
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        with inbox_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()

    return _result(
        "success",
        report_id=report["id"],
        report_status="queued_for_handoff",
        deduplicated=False,
        **({"workflow":report["workflow"]} if "workflow" in report else {}),
    )


def check_safety_report_status(
    report_id: Any,
    *,
    inbox_path: Path = INBOX_PATH,
    processed_path: Path = PROCESSED_PATH,
    status_dir: Path = STATUS_DIR,
    outbox_dir: Path = OUTBOX_DIR,
) -> dict[str, Any]:
    """Return a terse status without exposing the worker's email content."""
    if not isinstance(report_id, str):
        return _result("invalid_arguments", message="report_id is required")
    normalized = report_id.strip().upper()
    if not REPORT_ID_PATTERN.fullmatch(normalized):
        return _result("invalid_arguments", message="report_id format is invalid")

    report = next((item for item in _read_jsonl(inbox_path) if item.get("id") == normalized), None)
    if report is None:
        return _result("not_found", report_id=normalized)

    processed = set()
    if processed_path.exists():
        processed = {line.strip() for line in processed_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    status_file = status_dir / f"{normalized}.json"
    worker_status: dict[str, Any] = {}
    if status_file.exists():
        try:
            worker_status = json.loads(status_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            worker_status = {}

    if normalized in processed and (outbox_dir / f"{normalized}.eml").exists():
        report_status = "handoff_ready"
    else:
        report_status = worker_status.get("state", "queued_for_handoff")
    return _result(
        "success",
        report_id=normalized,
        report_status=report_status,
        urgency=report["urgency"],
        location=report["location"],
        attempts=int(worker_status.get("attempts", 0)),
        **({"workflow":report["workflow"]} if isinstance(report.get("workflow"),dict) else {}),
    )


REGISTERED_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    SEARCH_TOOL_NAME: search_approved_safety_manual,
    CREATE_REPORT_TOOL_NAME: create_safety_report,
    CHECK_REPORT_TOOL_NAME: check_safety_report_status,
}


def execute_tool(name: str, arguments: Any, context: ToolContext | None = None) -> dict[str, Any]:
    """Dispatch only registered functions with exact, schema-compatible keys."""
    if name not in REGISTERED_TOOLS and name not in PROCEDURE_TOOL_NAMES:
        return _result("invalid_arguments", message="unknown tool")
    if not isinstance(arguments, dict):
        return (_result("invalid_arguments", code="invalid_arguments")
                if name in PROCEDURE_TOOL_NAMES else
                _result("invalid_arguments", message="arguments must be an object"))

    required_and_allowed = {
        SEARCH_TOOL_NAME: ({"query", "topic"}, {"query", "topic"}),
        CREATE_REPORT_TOOL_NAME: (
            {"location", "summary", "urgency", "exposure_status", "language"},
            {
                "location",
                "summary",
                "urgency",
                "exposure_status",
                "language",
                "material_or_equipment",
            },
        ),
        CHECK_REPORT_TOOL_NAME: ({"report_id"}, {"report_id"}),
        START_PROCEDURE_TOOL_NAME: ({"procedure_id"}, {"procedure_id"}),
        GET_CURRENT_STEP_TOOL_NAME: (set(), set()),
        COMPLETE_CURRENT_STEP_TOOL_NAME: ({"expected_step_id"}, {"expected_step_id"}),
        RECORD_STEP_OBSERVATION_TOOL_NAME: (
            {"expected_step_id","value"},{"expected_step_id","value"}),
        START_STEP_TIMER_TOOL_NAME: ({"expected_step_id"},{"expected_step_id"}),
        GET_WORKFLOW_SUMMARY_TOOL_NAME: (set(),set()),
    }
    required, allowed = required_and_allowed[name]
    keys = set(arguments)
    if not required.issubset(keys) or not keys.issubset(allowed):
        if name == SEARCH_TOOL_NAME:
            return _search_failure("invalid_arguments")
        return (_result("invalid_arguments",code="invalid_arguments")
                if name in PROCEDURE_TOOL_NAMES else
                _result("invalid_arguments", message="unexpected or missing arguments"))
    if name == SEARCH_TOOL_NAME:
        return search_approved_safety_manual(**arguments, context=context)
    if name in PROCEDURE_TOOL_NAMES:
        controller=context.procedure_controller if context else None
        if controller is None:
            return _result("error",code="procedure_not_available")
        if name==START_PROCEDURE_TOOL_NAME:
            return controller.start(arguments["procedure_id"],facility_id=context.facility_id,
                                    language=context.language,usage_scope=context.usage_scope)
        if name==GET_CURRENT_STEP_TOOL_NAME:
            return controller.current()
        if name==RECORD_STEP_OBSERVATION_TOOL_NAME:
            if (
                context.current_transcript is not None
                and not _observation_matches_transcript(
                    arguments["value"], context.current_transcript
                )
            ):
                current = controller.current()
                return _result(
                    "error",
                    code="observation_evidence_mismatch",
                    **(
                        {"state": current["state"]}
                        if isinstance(current.get("state"), dict)
                        else {}
                    ),
                )
            return controller.record_observation(
                arguments["expected_step_id"],arguments["value"])
        if name==START_STEP_TIMER_TOOL_NAME:
            return controller.start_timer(arguments["expected_step_id"])
        if name==GET_WORKFLOW_SUMMARY_TOOL_NAME:
            return controller.summary()
        if context.procedure_completion_authorized_step_id != arguments["expected_step_id"]:
            return _result("error",code="explicit_confirmation_required")
        return controller.complete(arguments["expected_step_id"])
    if name==CREATE_REPORT_TOOL_NAME:
        controller=context.procedure_controller if context else None
        report_context=getattr(controller,"report_context",None)
        workflow=report_context() if callable(report_context) else None
        result=create_safety_report(
            **arguments,workflow_context=workflow)
        if result.get("status")=="success" and workflow is not None:
            block_for_handoff=getattr(controller,"block_for_handoff",None)
            if not callable(block_for_handoff):
                return {
                    **result,"status":"error","code":"workflow_block_failed",
                    "report_queued":True,
                }
            blocked=block_for_handoff(
                result.get("report_id"),arguments.get("summary"))
            if blocked.get("status")!="success":
                return {
                    **result,"status":"error","code":"workflow_block_failed",
                    "report_queued":True,
                    **({"procedure_state":blocked["state"]}
                       if isinstance(blocked.get("state"),dict) else {}),
                }
            result["procedure_state"]=blocked["state"]
            result["workflow_operation"]=blocked["operation"]
            result["workflow_idempotent"]=blocked["idempotent"]
            result["procedure_blocked"]=bool(
                blocked["state"].get("attached") and
                blocked["state"].get("status")=="blocked_for_handoff")
        return result
    return REGISTERED_TOOLS[name](**arguments)
