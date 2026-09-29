"""Confirmed experiment write-back boundary with an eLabFTW v2 adapter."""

from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Protocol
from urllib.parse import urljoin, urlparse

import requests


class ElnConnectorError(RuntimeError):
    code = "eln_connector_error"


class ElnConfirmationRequiredError(ElnConnectorError):
    code = "eln_confirmation_required"


class ElnConfigurationError(ElnConnectorError):
    code = "eln_configuration_invalid"


class ElnWritebackError(ElnConnectorError):
    code = "eln_writeback_failed"


@dataclass(frozen=True)
class CompletedStep:
    step_id: str
    completed_at: str
    instruction: str | None = None


@dataclass(frozen=True)
class Observation:
    step_id: str
    recorded_at: str
    value: str


@dataclass(frozen=True)
class ExperimentWriteback:
    report_id: str
    protocol_id: str
    protocol_revision_id: str
    protocol_title: str
    protocol_version: str
    protocol_source_url: str | None
    source_status: str
    started_at: str
    ended_at: str
    completed_steps: tuple[CompletedStep, ...]
    observations: tuple[Observation, ...]
    timer_events: tuple[Mapping[str, object], ...]
    deviations: tuple[str, ...]
    report_url: str | None = None


@dataclass(frozen=True)
class ElnWritebackPolicy:
    include_unpublished_protocol_instructions: bool = False


@dataclass(frozen=True)
class ElnWritebackResult:
    connector_kind: str
    external_experiment_id: str
    location: str
    request_sha256: str


@dataclass(frozen=True)
class ElnHttpResult:
    status_code: int
    headers: Mapping[str, str]
    content: bytes = b""


class ElnHttpTransport(Protocol):
    def post(
        self, url: str, *, headers: Mapping[str, str], json_body: Mapping[str, object]
    ) -> ElnHttpResult: ...

    def patch(
        self, url: str, *, headers: Mapping[str, str], json_body: Mapping[str, object]
    ) -> ElnHttpResult: ...


class RequestsElnTransport:
    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def post(self, url, *, headers, json_body):
        response = requests.post(
            url,
            headers=dict(headers),
            json=dict(json_body),
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )
        return ElnHttpResult(response.status_code, dict(response.headers), response.content)

    def patch(self, url, *, headers, json_body):
        response = requests.patch(
            url,
            headers=dict(headers),
            json=dict(json_body),
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )
        return ElnHttpResult(response.status_code, dict(response.headers), response.content)


class ElnConnector(Protocol):
    connector_kind: str

    def write_completed_experiment(
        self,
        experiment: ExperimentWriteback,
        *,
        confirmed: bool,
        policy: ElnWritebackPolicy = ElnWritebackPolicy(),
    ) -> ElnWritebackResult: ...


def _iso_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ElnWritebackError("Experiment timestamps are invalid.") from exc


def _safe_link(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ElnWritebackError("Experiment reference URL is invalid.")
    return value


def _render_body(
    experiment: ExperimentWriteback, policy: ElnWritebackPolicy
) -> str:
    source_link = _safe_link(experiment.protocol_source_url)
    report_link = _safe_link(experiment.report_url)
    published = experiment.source_status.strip().casefold() not in {
        "in development",
        "development",
        "draft",
        "unpublished",
    }
    include_instructions = published or policy.include_unpublished_protocol_instructions
    parts = [
        "<h2>Voice Workflow Agent experiment record</h2>",
        "<dl>",
        f"<dt>Protocol</dt><dd>{html.escape(experiment.protocol_title)}</dd>",
        f"<dt>Protocol ID</dt><dd>{html.escape(experiment.protocol_id)}</dd>",
        f"<dt>Exact revision</dt><dd>{html.escape(experiment.protocol_revision_id)}</dd>",
        f"<dt>Version</dt><dd>{html.escape(experiment.protocol_version)}</dd>",
        f"<dt>Source status</dt><dd>{html.escape(experiment.source_status)}</dd>",
        f"<dt>Started</dt><dd>{html.escape(experiment.started_at)}</dd>",
        f"<dt>Ended</dt><dd>{html.escape(experiment.ended_at)}</dd>",
        "</dl>",
    ]
    if source_link:
        escaped = html.escape(source_link, quote=True)
        parts.append(f'<p>Source: <a href="{escaped}">{escaped}</a></p>')
    parts.append("<h3>Completed steps</h3><ol>")
    for step in experiment.completed_steps:
        label = html.escape(step.step_id)
        if include_instructions and step.instruction:
            label += f" — {html.escape(step.instruction)}"
        parts.append(f"<li>{label} ({html.escape(step.completed_at)})</li>")
    parts.append("</ol><h3>Observations</h3><ul>")
    for observation in experiment.observations:
        parts.append(
            f"<li>{html.escape(observation.step_id)}: "
            f"{html.escape(observation.value)} ({html.escape(observation.recorded_at)})</li>"
        )
    parts.append("</ul><h3>Timer events</h3><pre>")
    parts.append(
        html.escape(
            json.dumps(
                [dict(item) for item in experiment.timer_events],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    )
    parts.append("</pre><h3>Deviations</h3><ul>")
    for deviation in experiment.deviations:
        parts.append(f"<li>{html.escape(deviation)}</li>")
    parts.append("</ul>")
    if report_link:
        escaped = html.escape(report_link, quote=True)
        parts.append(f'<p>Report: <a href="{escaped}">{escaped}</a></p>')
    parts.append(
        "<p><em>Raw audio, unrestricted transcripts, model reasoning, and credentials "
        "are not included.</em></p>"
    )
    return "".join(parts)


class ELabFtwConnector:
    """eLabFTW API v2 adapter: POST experiment, then PATCH its content."""

    connector_kind = "elabftw"

    def __init__(
        self,
        *,
        server_configured_base_url: str,
        api_key: str,
        transport: ElnHttpTransport | None = None,
    ) -> None:
        parsed = urlparse(server_configured_base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ElnConfigurationError(
                "The server-configured eLabFTW origin must be an HTTPS URL."
            )
        if not api_key:
            raise ElnConfigurationError("The eLabFTW API key is absent.")
        self._api_root = server_configured_base_url.rstrip("/") + "/api/v2/"
        self._api_key = api_key
        self._transport = transport or RequestsElnTransport()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def write_completed_experiment(
        self,
        experiment: ExperimentWriteback,
        *,
        confirmed: bool,
        policy: ElnWritebackPolicy = ElnWritebackPolicy(),
    ) -> ElnWritebackResult:
        if not confirmed:
            raise ElnConfirmationRequiredError(
                "The user must confirm this ELN write-back."
            )
        if not experiment.completed_steps or not experiment.ended_at:
            raise ElnWritebackError("Only a completed experiment can be written back.")
        create_url = urljoin(self._api_root, "experiments")
        created = self._transport.post(
            create_url, headers=self._headers, json_body={}
        )
        if created.status_code != 201:
            raise ElnWritebackError(
                f"eLabFTW create returned HTTP {created.status_code}."
            )
        location = created.headers.get("location") or created.headers.get("Location")
        if not isinstance(location, str):
            raise ElnWritebackError("eLabFTW create response omitted Location.")
        parsed_location = urlparse(location)
        parsed_root = urlparse(self._api_root)
        if (
            parsed_location.scheme != parsed_root.scheme
            or parsed_location.netloc != parsed_root.netloc
            or not parsed_location.path.startswith(parsed_root.path + "experiments/")
            or parsed_location.query
            or parsed_location.fragment
        ):
            raise ElnWritebackError("eLabFTW returned an unsafe experiment Location.")
        external_id = parsed_location.path.rstrip("/").rsplit("/", 1)[-1]
        if not external_id or len(external_id) > 200:
            raise ElnWritebackError("eLabFTW experiment identity is invalid.")
        payload = {
            "title": f"{experiment.protocol_title} — {experiment.report_id}",
            "date": _iso_date(experiment.started_at),
            "body": _render_body(experiment, policy),
        }
        patched = self._transport.patch(
            location, headers=self._headers, json_body=payload
        )
        if patched.status_code not in {200, 204}:
            raise ElnWritebackError(
                f"eLabFTW update returned HTTP {patched.status_code}."
            )
        request_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return ElnWritebackResult(
            connector_kind=self.connector_kind,
            external_experiment_id=external_id,
            location=location,
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        )
