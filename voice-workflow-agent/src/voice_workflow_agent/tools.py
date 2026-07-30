"""Deterministic, context-bound tools for the Voice Workflow Agent agent."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from voice_workflow_agent.retrieval import TOPICS

log = logging.getLogger("voice_workflow_agent.tools")

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
            "Call this whenever a worker asks a factual question that must be "
            "answered from approved local safety material, including SOP or SDS "
            "content, first aid, fire, spills, handling or storage, exposure or "
            "PPE, disposal, and equipment operation. Pass the worker's actual "
            "question and the one validated topic that best matches the request. "
            "Use only a successful, answerable result as evidence; if the catalog "
            "does not return an answerable match, do not fill the gap from general "
            "knowledge or claim that the requested fact is confirmed. Do not call "
            "this for greetings, ordinary conversation, workflow state, step "
            "observations, timers, or report status. The catalog path, facility, "
            "session language, and usage scope are trusted server context and must "
            "never be supplied or overridden in Tool arguments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The worker's non-empty safety question or identifying "
                        "keywords, preserving any stated product, material, equipment, "
                        "number, or unit. Do not add facts that the worker did not say."
                    ),
                },
                "topic": {
                    "type": "string",
                    "enum": list(TOPICS),
                    "description": (
                        "The single catalog route that matches the requested fact: "
                        "first_aid, fire, spill, handling_storage, exposure_ppe, "
                        "disposal, or equipment_operation. Select by user intent, "
                        "not by guessing the answer."
                    ),
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
            "Call this when a worker reports a hazard, near miss, spill, exposure "
            "concern, damaged equipment, abnormal device behavior, or another "
            "situation that should be recorded for human handoff, after location, "
            "a factual summary, urgency, and exposure status are all known and the "
            "worker asks to record, report, submit, or create a draft. Use only "
            "facts the worker stated; ask for any missing required fact instead of "
            "guessing. The runtime stages the normalized report and requires "
            "explicit user confirmation before it is actually queued. A confirmed "
            "submission returns a Voice Workflow Agent report id and, when a workflow is "
            "attached, links the report to the current step and blocks further "
            "progress for manager handoff. A draft awaiting confirmation is not "
            "submitted and must not be described as submitted or blocked. This "
            "Tool records and queues a handoff; it does not contact emergency "
            "services, determine that an area is safe, approve work resumption, or "
            "replace the facility's established emergency channel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The most specific location the worker provided, such as "
                        "laboratory, room, bench, hood, or equipment position. Do not "
                        "invent a building, room number, or device location."
                    ),
                },
                "summary": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "A short factual account of what the worker observed, "
                        "including relevant symptoms or device behavior, using only "
                        "their statements. Do not add a cause, diagnosis, safety "
                        "judgment, or corrective action."
                    ),
                },
                "urgency": {
                    "type": "string",
                    "enum": ["emergency", "urgent", "routine"],
                    "description": (
                        "Classify from the worker's stated present condition: "
                        "emergency means immediate danger now, urgent means prompt "
                        "human review is needed without a stated immediate danger, "
                        "and routine means a non-immediate issue or near miss. Do not "
                        "downgrade a stated immediate danger."
                    ),
                },
                "exposure_status": {
                    "type": "string",
                    "enum": ["yes", "no", "unknown"],
                    "description": (
                        "Whether any person may have been exposed: yes only when the "
                        "worker reports possible or actual exposure, no only when the "
                        "worker explicitly reports no exposure, otherwise unknown. "
                        "Never infer this from urgency or the event type."
                    ),
                },
                "language": {
                    "type": "string",
                    "enum": ["ko", "en", "vi"],
                    "description": (
                        "The trusted session language used by the worker: ko, en, or "
                        "vi. The server enforces this value; do not switch it based on "
                        "report content or the desired manager handoff language."
                    ),
                },
                "material_or_equipment": {
                    "type": "string",
                    "description": (
                        "The chemical, sample, instrument, or equipment name exactly "
                        "as the worker provided it. Preserve identifiers, digits, and "
                        "separators. Omit this optional field when it is unknown."
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
            "Call this only when the worker asks for the processing or handoff "
            "status of a previously submitted Voice Workflow Agent report and a valid report "
            "id is available from conversation memory or the worker. It can show "
            "whether the report is queued for handoff, being processed or retried, "
            "or has a manager handoff artifact. This is a read-only status check: "
            "it does not submit, edit, cancel, resend, or unblock a report or its "
            "linked workflow. Do not call it for a draft that has not been "
            "confirmed, and do not infer completion when the returned status does "
            "not say that the handoff is ready."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "report_id": {
                    "type": "string",
                    "description": (
                        "The exact Voice Workflow Agent report id returned by a confirmed "
                        "submission, in the form SR-YYYYMMDD-XXXXXX, for example "
                        "SR-20260722-A1B2C3. Preserve every character; do not invent "
                        "or reconstruct a missing id."
                    ),
                },
            },
            "required": ["report_id"],
            "additionalProperties": False,
        },
    },
}

START_PROCEDURE_TOOL = {
    "type": "function",
    "function": {
        "name": START_PROCEDURE_TOOL_NAME,
        "description": (
            "Call this only after the worker explicitly asks to begin one of the "
            "validated procedures listed for the current session. Use its exact "
            "stable procedure_id; do not invent an id, choose an unlisted "
            "procedure, or start a workflow merely because the worker asks a "
            "general safety question. The server revalidates the procedure against "
            "trusted facility, session language, and usage scope, and permits only "
            "one attached workflow. Repeating the same active procedure is "
            "idempotent; trying a different procedure while one is attached is a "
            "conflict. Facility, language, scope, session id, database path, and "
            "workflow state are server-owned and must never appear in arguments. "
            "Do not claim that the workflow started until the Tool returns success."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "procedure_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The exact stable id of a procedure listed as available in "
                        "the current session context, not its title, step id, or a "
                        "model-created label."
                    ),
                },
            },
            "required": ["procedure_id"],
            "additionalProperties": False,
        },
    },
}

GET_CURRENT_STEP_TOOL = {
    "type": "function",
    "function": {
        "name": GET_CURRENT_STEP_TOOL_NAME,
        "description": (
            "Call this to read the current server-attached workflow state when the "
            "worker asks what to do now, asks to repeat the current instruction, or "
            "when a state-changing Tool needs a fresh current step id. This is "
            "read-only and returns the approved instruction, source, step id and "
            "number, required observation state, fixed timer state, completion "
            "counts, and any human-handoff block. Read the approved instruction "
            "without rewriting or improvising operational details. No session id "
            "is accepted because attachment is trusted server state. This Tool "
            "does not start, complete, skip, record, time, unblock, or restart a "
            "workflow."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

COMPLETE_CURRENT_STEP_TOOL = {
    "type": "function",
    "function": {
        "name": COMPLETE_CURRENT_STEP_TOOL_NAME,
        "description": (
            "Call this only for the current step after the worker explicitly "
            "confirms completion in the current turn. Pass the exact current "
            "step_id returned by server-owned workflow state, never a step number, "
            "title, previous step, or guessed next step. Do not infer completion "
            "from an observation, a timer request, silence, or conversational "
            "agreement, and never use this Tool to skip steps. The server "
            "independently checks turn-scoped confirmation, step identity, required "
            "observations, fixed-timer start and elapsed state, completion status, "
            "and any manager-handoff block. If any gate fails, keep the workflow at "
            "the current step, explain the returned requirement, and do not claim "
            "success. A blocked_for_handoff workflow cannot advance or restart "
            "until handled outside this Tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expected_step_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The exact current_step_id from the latest trusted procedure "
                        "state. It is an optimistic concurrency check, not a request "
                        "to choose or jump to that step."
                    ),
                },
            },
            "required": ["expected_step_id"],
            "additionalProperties": False,
        },
    },
}

RECORD_STEP_OBSERVATION_TOOL = {
    "type": "function",
    "function": {
        "name": RECORD_STEP_OBSERVATION_TOOL_NAME,
        "description": (
            "Call this when the worker explicitly states an observation requested "
            "by the current server-approved step. Record only the value present in "
            "the current finalized user transcript. Preserve every letter, digit, "
            "decimal point, sign, separator, and boolean meaning exactly; do not "
            "shorten identifiers, correct or translate the value, infer a value "
            "from context, or add a unit the worker did not say. Pass the exact "
            "current step_id from trusted workflow state. The server rejects stale "
            "steps, steps without an observation schema, wrong value types, "
            "blocked or completed workflows, and values unsupported by the final "
            "transcript. This Tool records an auditable observation only; it does "
            "not complete the step or make a safety judgment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expected_step_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The exact current_step_id from the latest trusted procedure "
                        "state. Do not use a step number, title, or earlier step id."
                    ),
                },
                "value": {
                    "anyOf": [
                        {"type": "string", "minLength": 1},
                        {"type": "number"},
                        {"type": "boolean"},
                    ],
                    "description": (
                        "The exact user-observed value in the type required by the "
                        "current step. Preserve identifiers such as A-170 verbatim "
                        "and never substitute a shorter or normalized value."
                    ),
                },
            },
            "required": ["expected_step_id", "value"],
            "additionalProperties": False,
        },
    },
}

START_STEP_TIMER_TOOL = {
    "type": "function",
    "function": {
        "name": START_STEP_TIMER_TOOL_NAME,
        "description": (
            "Call this only when the worker explicitly asks to start the timer for "
            "the current step and trusted workflow state shows that the step has a "
            "configured timer. Pass the exact current step_id. Never choose, "
            "estimate, mention as started, or override a duration: the approved "
            "ProcedureDefinition on the server owns the fixed duration and this "
            "Tool accepts no duration argument. Repeating the call for the same "
            "active step is idempotent and does not reset the deadline. The server "
            "rejects stale steps, steps without a timer, completed workflows, and "
            "workflows blocked for manager handoff. Starting a timer does not "
            "complete the step; completion remains gated until the fixed deadline "
            "has elapsed and the worker separately confirms completion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expected_step_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The exact current_step_id from the latest trusted procedure "
                        "state. No duration, deadline, session id, or reset flag is "
                        "allowed."
                    ),
                },
            },
            "required": ["expected_step_id"],
            "additionalProperties": False,
        },
    },
}

GET_WORKFLOW_SUMMARY_TOOL = {
    "type": "function",
    "function": {
        "name": GET_WORKFLOW_SUMMARY_TOOL_NAME,
        "description": (
            "Call this when the worker asks for a workflow recap, completed-step "
            "history, recorded observations, timer records, current progress, or "
            "the report linked to manager handoff. It returns a read-only, "
            "server-owned audit summary together with current workflow state. Use "
            "the returned records exactly and distinguish the current step from "
            "completed steps. This Tool is for audit and recap, not for retrieving "
            "new SOP or SDS facts, generating missing observations, deciding that "
            "work is safe, or mutating, completing, unblocking, or restarting the "
            "workflow. It accepts no model-supplied session or database identifier."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

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
    """Search the approved catalog, optionally reranking safe candidates in Moss."""
    from voice_workflow_agent.retrieval import search_safety_documents
    from voice_workflow_agent.moss_retrieval import get_moss_runtime

    blocked = _search_failure("invalid_arguments")
    if (not isinstance(query, str) or not query.strip() or context is None or
            context.language not in ("ko", "en", "vi") or context.catalog_path is None):
        return blocked
    if not isinstance(topic, str) or topic not in TOPICS:
        return blocked
    try:
        runtime = get_moss_runtime()
        use_moss = runtime is not None and runtime.allows_scope(context.usage_scope)
        search_options = {
            "usage_scope": context.usage_scope,
            "facility_id": context.facility_id,
            "topic": topic,
        }
        if use_moss:
            search_options["max_matches"] = runtime.settings.candidate_limit
        result = search_safety_documents(
            query,
            context.language,
            context.catalog_path,
            **search_options,
        )
        if result.get("status") != "success" or not result.get("answerable"):
            return _search_failure(result.get("status", "error"))
        raw_matches = result["matches"]
        retrieval = {"backend": "sqlite"}
        if use_moss:
            reranked = runtime.rerank(
                query,
                raw_matches,
                usage_scope=context.usage_scope,
                topic_routes=TOPICS[topic],
            )
            raw_matches = reranked.matches
            retrieval = {
                "backend": "moss" if reranked.used else "sqlite_fallback",
                "elapsed_ms": reranked.elapsed_ms,
            }
            log.info(
                "approved retrieval backend=%s elapsed_ms=%s candidates=%s",
                retrieval["backend"],
                reranked.elapsed_ms,
                len(result["matches"]),
            )
        allowed = {
            "document_id", "document_type", "title", "issuer", "manufacturer",
            "product_name", "product_code", "cas_numbers", "version", "canonical_version",
            "section_code", "section_title", "page_start", "page_end", "content",
            "language", "translation_status", "source_uri", "source_checksum",
        }
        matches = [{key: value for key, value in match.items() if key in allowed}
                   for match in raw_matches[:3]]
        return {
            "status": "success",
            "answerable": True,
            "matches": matches,
            "retrieval": retrieval,
        }
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
