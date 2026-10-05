"""Voice Workflow Agent handoff worker.

The voice agent appends small jobs to ``reports/inbox.jsonl``. This separate
process polls that flag file, asks its own model to draft a Korean manager
handoff, writes an ``.eml`` artifact, and advances a durable processed ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from voiney_lab.model_providers import RoleModel, chat_client
from voiney_lab.setting_names import refuse_old_setting_names
from voiney_lab.tools import INBOX_PATH, OUTBOX_DIR, PROCESSED_PATH, STATUS_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if not os.environ.get("PYTEST_VERSION"):
    # Under pytest the repository .env is not read (lane M1, decision 6).
    load_dotenv(PROJECT_ROOT / ".env")
refuse_old_setting_names()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voiney_lab.worker")

POLL_SECONDS = 2
MAX_ATTEMPTS = 3
URGENCY_ORDER = {"emergency": 0, "urgent": 1, "routine": 2}

HANDOFF_PROMPT = """You are the Voice Workflow Agent handoff worker for Voice Workflow Guide. Convert one queued lab safety report JSON into a concise Korean handoff note for a lab manager. Include the report id, reported location, event summary, named material or equipment when present, exposure status, urgency, and the fact that details still require human verification. If the JSON includes a workflow object, also identify its procedure title and version, current step number and title, and only the observation or timer facts present in that object; state that the workflow is blocked pending human handoff. Distinguish reported facts from unknown information. Do not invent causes, diagnoses, procedures, phone numbers, laws, exposure limits, observations, timer results, or completed actions. Do not say the area is safe or approve work resumption. For emergency severity, put an immediate human contact request first. Plain text only, no Markdown, no subject line, no placeholders. End with 'Voice Workflow Agent 자동 인계'."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            log.error("skipping invalid JSONL at %s:%d", path, number)
            continue
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            records.append(value)
    return records


def load_processed(processed_path: Path = PROCESSED_PATH) -> set[str]:
    if not processed_path.exists():
        return set()
    return {
        line.strip()
        for line in processed_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def mark_processed(report_id: str, processed_path: Path = PROCESSED_PATH) -> None:
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    with processed_path.open("a", encoding="utf-8") as handle:
        handle.write(report_id + "\n")
        handle.flush()


def load_status(report_id: str, status_dir: Path = STATUS_DIR) -> dict[str, Any]:
    path = status_dir / f"{report_id}.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def write_status(
    report_id: str,
    state: str,
    attempts: int,
    *,
    status_dir: Path = STATUS_DIR,
    detail: str | None = None,
) -> Path:
    status_dir.mkdir(parents=True, exist_ok=True)
    value: dict[str, Any] = {
        "report_id": report_id,
        "state": state,
        "attempts": attempts,
        "updated_at_epoch": time.time(),
    }
    if detail:
        value["detail"] = detail[:240]
    path = status_dir / f"{report_id}.json"
    temporary = status_dir / f".{report_id}.json.tmp"
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def pending_reports(
    inbox_path: Path = INBOX_PATH,
    processed_path: Path = PROCESSED_PATH,
    status_dir: Path = STATUS_DIR,
) -> list[dict[str, Any]]:
    processed = load_processed(processed_path)
    pending = []
    for report in _read_jsonl(inbox_path):
        if report["id"] in processed:
            continue
        attempts = int(load_status(report["id"], status_dir).get("attempts", 0))
        if attempts >= MAX_ATTEMPTS:
            continue
        pending.append(report)
    return sorted(
        pending,
        key=lambda report: (
            URGENCY_ORDER.get(report.get("urgency"), 99),
            float(report.get("filed_at_epoch", 0)),
        ),
    )


def draft_handoff(report: dict[str, Any], client: Any | None = None) -> str:
    """Use a worker-specific prompt and model; no voice-path history is shared.

    The model is the answer role's (lane M1, decision 1), grok-4 when unset
    as before, on the answer role's provider.
    """
    role = RoleModel.from_environment("answer")
    if client is None:
        if not role.has_key():
            raise RuntimeError(f"{role.api_key_setting} is not set")
        if role.provider == "xai":
            client = chat_client(role, asynchronous=False, sdk_client=OpenAI(
                base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
                api_key=os.environ.get("XAI_API_KEY", "").strip(),
            ))
        else:
            client = chat_client(role, asynchronous=False, max_retries=2)
    response = client.chat.completions.create(
        model=role.model or "grok-4",
        messages=[
            {"role": "system", "content": HANDOFF_PROMPT},
            {
                "role": "user",
                "content": json.dumps(report, ensure_ascii=False, sort_keys=True),
            },
        ],
    )
    body = response.choices[0].message.content
    if not isinstance(body, str) or not body.strip():
        raise RuntimeError("worker model returned an empty handoff")
    return body.strip()


def write_handoff_email(
    report: dict[str, Any],
    body: str,
    outbox_dir: Path = OUTBOX_DIR,
) -> Path:
    """Write a demo artifact only; real SMTP is deliberately out of scope."""
    outbox_dir.mkdir(parents=True, exist_ok=True)
    urgent = report.get("urgency") in ("emergency", "urgent")
    prefix = "[긴급] " if urgent else ""
    message = EmailMessage()
    message["To"] = os.environ.get("VOINEY_LAB_LAB_MANAGER_EMAIL", "lab-manager@example.invalid")
    message["From"] = os.environ.get(
        "VOINEY_LAB_FROM_EMAIL", "voice_workflow_agent@example.invalid"
    )
    message["Subject"] = (
        f"{prefix}Voice Workflow Agent 보고 {report['id']} — {report['location']}"
    )
    message.set_content(body)
    path = outbox_dir / f"{report['id']}.eml"
    temporary = outbox_dir / f".{report['id']}.eml.tmp"
    temporary.write_bytes(message.as_bytes())
    temporary.replace(path)
    return path


def process_once(
    *,
    inbox_path: Path = INBOX_PATH,
    processed_path: Path = PROCESSED_PATH,
    status_dir: Path = STATUS_DIR,
    outbox_dir: Path = OUTBOX_DIR,
    client: Any | None = None,
) -> int:
    """Drain currently eligible work, returning the number completed."""
    handled = 0
    for report in pending_reports(inbox_path, processed_path, status_dir):
        report_id = report["id"]
        previous = load_status(report_id, status_dir)
        attempts = int(previous.get("attempts", 0)) + 1
        write_status(report_id, "processing", attempts, status_dir=status_dir)
        # The location is the reporter's own words; the log keeps only its
        # size and digest, and an urgency outside the known set by name only.
        location = str(report.get("location") or "")
        urgency = report.get("urgency")
        log.info(
            "processing %s (location_chars=%d location_sha256=%s, %s)",
            report_id,
            len(location),
            hashlib.sha256(location.encode("utf-8")).hexdigest()[:16],
            urgency if isinstance(urgency, str) and urgency in URGENCY_ORDER else "unknown_urgency",
        )
        try:
            body = draft_handoff(report, client=client)
            path = write_handoff_email(report, body, outbox_dir)
            mark_processed(report_id, processed_path)
            write_status(report_id, "handoff_ready", attempts, status_dir=status_dir)
            handled += 1
            log.info("handoff ready -> %s", path)
        except Exception as exc:
            state = "failed" if attempts >= MAX_ATTEMPTS else "retry_pending"
            write_status(
                report_id,
                state,
                attempts,
                status_dir=status_dir,
                detail=str(exc),
            )
            log.exception("worker failed on %s (%s)", report_id, state)
    return handled


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice Workflow Agent report worker")
    parser.add_argument("--once", action="store_true", help="drain current backlog and exit")
    args = parser.parse_args()
    if args.once:
        count = process_once()
        log.info("done: %d report(s) processed", count)
        return

    log.info("watching %s (Ctrl-C to stop)", INBOX_PATH)
    while True:
        process_once()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
