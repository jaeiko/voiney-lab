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

from voiney_lab.retrieval import TOPICS

log = logging.getLogger("voiney_lab.tools")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "reports"
INBOX_PATH = REPORTS_DIR / "inbox.jsonl"
PROCESSED_PATH = REPORTS_DIR / "processed.txt"
STATUS_DIR = REPORTS_DIR / "status"
OUTBOX_DIR = PROJECT_ROOT / "outbox"

SEARCH_TOOL_NAME = "search_approved_safety_manual"
APPROVED_LAB_REFERENCE_TOOL_NAME = "search_approved_lab_references"
CREATE_REPORT_TOOL_NAME = "create_safety_report"
CHECK_REPORT_TOOL_NAME = "check_safety_report_status"
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
            "submission returns a Voiney Lab report id and, when a workflow is "
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
            "status of a previously submitted Voiney Lab report and a valid report "
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
                        "The exact Voiney Lab report id returned by a confirmed "
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

TOOLS = [SEARCH_TOOL, CREATE_REPORT_TOOL, CHECK_REPORT_TOOL]
PROVIDER_TOOL_NAMES = tuple(
    tool["function"]["name"] for tool in TOOLS
)
if len(PROVIDER_TOOL_NAMES) != len(set(PROVIDER_TOOL_NAMES)):
    raise RuntimeError("Provider-facing Tool names must be unique.")

# This inventory is intentionally derived beside the schemas so tests and
# operator audits cannot mistake internal service operations for model Tools.
PROVIDER_TOOL_DECISIONS = {
    SEARCH_TOOL_NAME: "retained: legacy approved safety lookup used by generic sessions",
    CREATE_REPORT_TOOL_NAME: "retained: explicit confirmed safety-report write",
    CHECK_REPORT_TOOL_NAME: "retained: read-only report status",
}
INTERNAL_SERVICE_OPERATIONS = frozenset({
    APPROVED_LAB_REFERENCE_TOOL_NAME,
    "search_authoritative_web",
})


@dataclass(frozen=True)
class ToolContext:
    """Trusted retrieval inputs owned by the server, never by Tool JSON."""

    catalog_path: Path | None
    facility_id: str | None
    language: str
    usage_scope: str
    # Manager handoff language is trusted facility policy and never a Tool arg.
    report_language: str = "ko"


def _result(status: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, **fields}


#: A negation in what the researcher said (lane TS, human decision 4 of
#: 2026-10-08): 안 (as a word of its own), 않, 없, 못, 말고·말아, and the
#: English no, not, never, none, nothing, without, cannot, -n't.
_RECORD_NEGATION = re.compile(
    r"(?<![가-힣])안(?=\s|$)|않|없|못|말(?:고|아|라)"
    r"|\b(?:no|not|never|none|nothing|without|cannot|neither|nor)\b|\b\w+n['’]t\b",
    re.IGNORECASE,
)
#: Where one clause ends and the next begins: punctuation, or a joining word.
_RECORD_CLAUSE_BREAK = re.compile(
    r"\s*[,.;!?·\n]\s*|\s+(?:그리고|그런데|근데|하지만|그래서|and|but|so)\s+", re.IGNORECASE)
#: The request to record, which is not part of what was seen: "남겨 줘",
#: "기록해 줘", "적어 줘", "메모해 줘" (with the quotation ending before it),
#: "note this", "record that ...".
_RECORD_REQUEST_KO = re.compile(
    r"(?:(?:이?라고|다고|고)\s*)?(?:기록|메모|적어|남겨|저장)\S*(?:\s+(?:좀|줘|줘요|주세요|줄래|둬|놔|해|해줘|해주세요))*\s*$"
)
_RECORD_REQUEST_EN = re.compile(
    r"^(?:please\s+)?(?:note|record|log|jot|write)(?:\s+(?:this|that|it|down))*\s*:?\s*(?:that\s+)?"
    r"|\s*(?:please\s+)?(?:note|record|log|jot|write)(?:\s+(?:this|that|it|down))+\s*$",
    re.IGNORECASE,
)
_QUOTED_ENDING = re.compile(r"(?:이?라고|다고|고)$")


def record_value_keeping_negation(value: str, transcript: str) -> str:
    """What ``record_log`` stores: ``value``, or the whole transcript.

    Lane TS, human decision 4 of 2026-10-08: when the words hold a negation,
    the value must hold the whole of every clause a negation stands in (the
    request to record taken off: "침전물 안 보임 남겨 줘" -> "침전물 안 보임");
    a value that does not ("침전물") is replaced by everything that was
    said, so a record never says the opposite of what was seen.
    """

    said = " ".join(str(transcript).split())
    wanted = " ".join(str(value).split()).casefold()
    if not _RECORD_NEGATION.search(said):
        return value
    for clause in _RECORD_CLAUSE_BREAK.split(said):
        if not clause or not _RECORD_NEGATION.search(clause):
            continue
        core = _RECORD_REQUEST_EN.sub("", _RECORD_REQUEST_KO.sub("", clause)).strip()
        if not core:
            continue
        shorter = _QUOTED_ENDING.sub("", core).strip()
        if core.casefold() not in wanted and (not shorter or shorter.casefold() not in wanted):
            return said
    return value


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
    from voiney_lab.retrieval import search_safety_documents
    from voiney_lab.moss_retrieval import get_moss_runtime

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


def search_approved_lab_references(
    query: Any,
    *,
    context: ToolContext | None = None,
    protocol_id: str | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Search approved read-only lab references for one protocol-related turn."""

    from voiney_lab.moss_retrieval import get_moss_runtime
    from voiney_lab.retrieval import retrieve_approved_lab_documents

    if (
        not isinstance(query, str) or not query.strip()
        or context is None or context.catalog_path is None
        or context.language not in {"ko", "en", "vi"}
        or protocol_id is not None and (
            not isinstance(protocol_id, str) or not protocol_id.strip()
        )
    ):
        return _search_failure("invalid_arguments")
    result = retrieve_approved_lab_documents(
        query,
        context.catalog_path,
        filters={
            "approval_status": "approved",
            "protocol_id": protocol_id,
            "lab_scope": context.usage_scope,
            "facility_id": context.facility_id,
            "exclude_non_operational": True,
        },
        top_k=top_k,
    )
    if result.get("status") != "success" or not result.get("answerable"):
        return _search_failure(str(result.get("status", "error")))
    matches = list(result["matches"])
    retrieval = dict(result.get("retrieval") or {"backend": "sqlite"})
    try:
        runtime = get_moss_runtime()
        if runtime is not None and runtime.allows_scope(context.usage_scope):
            reranked = runtime.rerank(
                query,
                matches,
                usage_scope=context.usage_scope,
                topic_routes=(
                    ("facility_sop", None),
                    ("supplier_sds", None),
                    ("equipment_manual", None),
                    ("regulatory_reference", None),
                ),
                result_limit=min(top_k, len(matches)),
            )
            matches = reranked.matches
            retrieval = {
                "backend": "moss" if reranked.used else "sqlite_fallback",
                "elapsed_ms": reranked.elapsed_ms,
            }
    except Exception:
        retrieval = {"backend": "sqlite_fallback"}
    return {
        "status": "success",
        "answerable": True,
        "matches": matches[:top_k],
        "retrieval": retrieval,
    }


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
    if name not in REGISTERED_TOOLS:
        return _result("invalid_arguments", message="unknown tool")
    if not isinstance(arguments, dict):
        return _result("invalid_arguments", message="arguments must be an object")

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
    }
    required, allowed = required_and_allowed[name]
    keys = set(arguments)
    if not required.issubset(keys) or not keys.issubset(allowed):
        if name == SEARCH_TOOL_NAME:
            return _search_failure("invalid_arguments")
        return _result("invalid_arguments", message="unexpected or missing arguments")
    if name == SEARCH_TOOL_NAME:
        return search_approved_safety_manual(**arguments, context=context)
    return REGISTERED_TOOLS[name](**arguments)
