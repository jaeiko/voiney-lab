"""Safe, audited human handoff and notifications for laboratory workflow."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(frozen=True)
class HandoffContact:
    id: str
    role: str
    display_name: str
    email: str | None = None
    phone: str | None = None
    preferred_channel: str = "email"


@dataclass(frozen=True)
class NotificationResult:
    status: str  # "success", "failure", "unsupported", "skipped"
    channel: str
    recipient: str
    timestamp: str
    error_detail: str | None = None
    audit_id: str | None = None


class NotificationProvider(Protocol):
    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        attachment_bytes: bytes | None = None,
        attachment_filename: str | None = None,
    ) -> NotificationResult:
        ...

    async def send_sms(self, phone: str, message: str) -> NotificationResult:
        ...


class SMTPEmailProvider:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        from_email: str | None = None,
        use_tls: bool = True,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.host = host or os.environ.get("SMTP_HOST", "")
        self.port = port or int(os.environ.get("SMTP_PORT", "587"))
        self.username = username or os.environ.get("SMTP_USER", "")
        self.password = password or os.environ.get("SMTP_PASSWORD", "")
        self.from_email = from_email or os.environ.get("SMTP_FROM_EMAIL", "noreply@lab-workflow.local")
        self.use_tls = use_tls
        self.timeout_seconds = timeout_seconds

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        attachment_bytes: bytes | None = None,
        attachment_filename: str | None = None,
    ) -> NotificationResult:
        now_str = datetime.now(timezone.utc).isoformat()
        if not self.host or self.host in {"disabled", "none"}:
            return NotificationResult(
                status="failure",
                channel="email",
                recipient=to_email,
                timestamp=now_str,
                error_detail="SMTP provider is not configured",
            )
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.from_email
            msg["To"] = to_email
            msg.set_content(body_text)

            if attachment_bytes and attachment_filename:
                msg.add_attachment(
                    attachment_bytes,
                    maintype="application",
                    subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
                    filename=attachment_filename,
                )

            def _sync_send() -> None:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as server:
                    if self.use_tls:
                        server.starttls()
                    if self.username and self.password:
                        server.login(self.username, self.password)
                    server.send_message(msg)

            await asyncio.to_thread(_sync_send)
            return NotificationResult(
                status="success",
                channel="email",
                recipient=to_email,
                timestamp=now_str,
            )
        except Exception as exc:
            return NotificationResult(
                status="failure",
                channel="email",
                recipient=to_email,
                timestamp=now_str,
                error_detail=str(exc),
            )

    async def send_sms(self, phone: str, message: str) -> NotificationResult:
        return NotificationResult(
            status="unsupported",
            channel="sms",
            recipient=phone,
            timestamp=datetime.now(timezone.utc).isoformat(),
            error_detail="SMS channel is not configured. Email is the supported delivery path.",
        )


class FakeNotificationProvider:
    """In-memory notification provider for offline tests and development."""

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.sent_emails: list[dict[str, Any]] = []
        self.sent_sms: list[dict[str, Any]] = []

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        attachment_bytes: bytes | None = None,
        attachment_filename: str | None = None,
    ) -> NotificationResult:
        now_str = datetime.now(timezone.utc).isoformat()
        record = {
            "to_email": to_email,
            "subject": subject,
            "body_text": body_text,
            "has_attachment": attachment_bytes is not None,
            "attachment_filename": attachment_filename,
            "timestamp": now_str,
        }
        if self.succeed:
            self.sent_emails.append(record)
            return NotificationResult(
                status="success",
                channel="email",
                recipient=to_email,
                timestamp=now_str,
                audit_id=f"audit-{len(self.sent_emails)}",
            )
        return NotificationResult(
            status="failure",
            channel="email",
            recipient=to_email,
            timestamp=now_str,
            error_detail="Simulated delivery provider failure",
        )

    async def send_sms(self, phone: str, message: str) -> NotificationResult:
        now_str = datetime.now(timezone.utc).isoformat()
        if self.succeed:
            self.sent_sms.append({"phone": phone, "message": message, "timestamp": now_str})
            return NotificationResult(
                status="success",
                channel="sms",
                recipient=phone,
                timestamp=now_str,
                audit_id=f"audit-sms-{len(self.sent_sms)}",
            )
        return NotificationResult(
            status="failure",
            channel="sms",
            recipient=phone,
            timestamp=now_str,
            error_detail="SMS unsupported",
        )


def get_default_contacts() -> dict[str, HandoffContact]:
    return {
        "advisor": HandoffContact(
            id="advisor",
            role="advisor",
            display_name="지도교수님",
            email=os.environ.get("LAB_ADVISOR_EMAIL", "advisor@university.edu"),
            preferred_channel="email",
        ),
        "safety_officer": HandoffContact(
            id="safety_officer",
            role="safety_officer",
            display_name="연구실 안전관리자",
            email=os.environ.get("LAB_SAFETY_OFFICER_EMAIL", "safety@university.edu"),
            preferred_channel="email",
        ),
    }


def resolve_handoff_recipient(text: str, contacts: dict[str, HandoffContact] | None = None) -> HandoffContact | None:
    pool = contacts or get_default_contacts()
    lowered = text.casefold()
    if any(term in lowered for term in ("교수", "교수님", "advisor", "professor")):
        return pool.get("advisor")
    if any(term in lowered for term in ("안전", "안전관리자", "safety", "officer", "manager")):
        return pool.get("safety_officer")
    return pool.get("advisor")
