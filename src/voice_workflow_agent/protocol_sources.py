"""Read-only protocol source adapters and immutable tenant-scoped ingestion."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import requests

from voice_workflow_agent.identity import Principal
from voice_workflow_agent.workspace_store import (
    ProtocolLineageRevision,
    WorkspaceError,
    WorkspaceStore,
)


PROTOCOLS_IO_API_ROOT = "https://www.protocols.io/api/v4"
GOOGLE_DRIVE_API_ROOT = "https://www.googleapis.com/drive/v3"
GITHUB_API_ROOT = "https://api.github.com"
PDF_MIME_TYPE = "application/pdf"
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
MAX_CONNECTOR_DOCUMENT_BYTES = 64 * 1024 * 1024
_PROTOCOLS_IDENTIFIER = re.compile(
    r"^(?:10\.17504/)?protocols\.io\.[A-Za-z0-9]+(?:/v[1-9][0-9]*|/latest)?$",
    re.IGNORECASE,
)
_PROTOCOLS_URI = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{2,250}(?:/v[1-9][0-9]*|/latest)?$"
)
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{3,200}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_GIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40,64}$")


class SourceConnectorError(RuntimeError):
    code = "source_connector_error"


class SourceIdentifierError(SourceConnectorError):
    code = "source_identifier_invalid"


class SourceAuthorizationError(SourceConnectorError):
    code = "source_authorization_required"


class SourceResponseError(SourceConnectorError):
    code = "source_response_invalid"


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def json(self) -> Mapping[str, object]:
        try:
            value = json.loads(self.content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceResponseError("Connector returned invalid JSON.") from exc
        if not isinstance(value, dict):
            raise SourceResponseError("Connector returned an invalid object.")
        return value


class ReadOnlyHttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
    ) -> HttpResult: ...


class RequestsReadOnlyTransport:
    """Bounded GET-only transport; adapters own and fix every destination host."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
    ) -> HttpResult:
        response = requests.get(
            url,
            headers=dict(headers),
            params=dict(params or {}),
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )
        content = response.content
        if len(content) > MAX_CONNECTOR_DOCUMENT_BYTES:
            raise SourceResponseError("Connector response exceeds the size limit.")
        return HttpResult(response.status_code, dict(response.headers), content)


@dataclass(frozen=True)
class SourceSnapshot:
    connector_kind: str
    external_id: str
    version_identity: str
    source_hash: str
    canonical_url: str
    title: str
    metadata: Mapping[str, object]
    content: Mapping[str, object]
    binary_content: bytes | None = None
    media_type: str | None = None


@dataclass(frozen=True)
class SourceImportResult:
    family_id: str
    revision: ProtocolLineageRevision
    changed: bool
    inbox_state: str


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SourceResponseError("Source content is not deterministic JSON.") from exc


def _checked(result: HttpResult, *, expected: int = 200) -> HttpResult:
    if result.status_code in {401, 403}:
        raise SourceAuthorizationError("Connector credentials were rejected.")
    if result.status_code != expected:
        raise SourceResponseError(
            f"Connector returned unexpected HTTP status {result.status_code}."
        )
    return result


def normalize_protocols_io_identifier(value: str) -> str:
    """Accept only an official DOI, protocol URI, or protocols.io view URL."""

    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise SourceIdentifierError("protocols.io identifier is invalid.")
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"protocols.io", "www.protocols.io", "dx.doi.org", "doi.org"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise SourceIdentifierError("Only official HTTPS protocols.io links are allowed.")
        path = parsed.path.strip("/")
        if parsed.hostname in {"dx.doi.org", "doi.org"}:
            candidate = path
        elif path.startswith("view/"):
            candidate = path.removeprefix("view/")
        else:
            raise SourceIdentifierError("protocols.io URL path is unsupported.")
    candidate = candidate.strip("/")
    if _PROTOCOLS_IDENTIFIER.fullmatch(candidate):
        normalized = candidate.casefold()
        return normalized if normalized.startswith("10.17504/") else f"10.17504/{normalized}"
    if _PROTOCOLS_URI.fullmatch(candidate):
        return candidate
    raise SourceIdentifierError("protocols.io identifier is invalid.")


def _author_names(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    names = []
    for item in raw:
        if isinstance(item, dict):
            candidate = item.get("name") or item.get("displayName")
            if isinstance(candidate, str):
                names.append(candidate.strip())
        elif isinstance(item, str):
            names.append(item.strip())
    return [name for name in names if name]


class ProtocolsIoConnector:
    def __init__(
        self,
        *,
        access_token: str,
        transport: ReadOnlyHttpTransport | None = None,
    ) -> None:
        if not access_token:
            raise SourceAuthorizationError("A protocols.io bearer token is required.")
        self._access_token = access_token
        self._transport = transport or RequestsReadOnlyTransport()

    def fetch(self, identifier: str) -> SourceSnapshot:
        selected = normalize_protocols_io_identifier(identifier)
        encoded = quote(selected, safe="/")
        result = _checked(
            self._transport.get(
                f"{PROTOCOLS_IO_API_ROOT}/protocols/{encoded}",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Accept": "application/json",
                },
                params={"content_format": "markdown"},
            )
        ).json()
        status_code = result.get("status_code", 0)
        if status_code not in {0, "0", None}:
            raise SourceResponseError("protocols.io rejected the import request.")
        payload = result.get("payload", result.get("protocol"))
        if not isinstance(payload, dict):
            raise SourceResponseError("protocols.io protocol payload is absent.")
        title = payload.get("title")
        uri = payload.get("uri")
        if not isinstance(title, str) or not title.strip() or not isinstance(uri, str):
            raise SourceResponseError("protocols.io source identity is incomplete.")
        doi = str(payload.get("doi") or "").removeprefix("https://").removeprefix(
            "dx.doi.org/"
        )
        version_uri = payload.get("version_uri")
        version_id = payload.get("version_id")
        version_code = payload.get("version_code")
        version_identity = str(version_uri or version_code or f"version-{version_id}")
        if version_identity in {"", "version-None"}:
            raise SourceResponseError("protocols.io version identity is absent.")
        published_on = payload.get("published_on")
        source_status = payload.get("status") or (
            "Published" if published_on else "In development"
        )
        steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
        materials = (
            payload.get("materials")
            if isinstance(payload.get("materials"), list)
            else []
        )
        warning = payload.get("warning")
        warnings = warning if isinstance(warning, list) else ([warning] if warning else [])
        canonical_url = payload.get("url")
        if not isinstance(canonical_url, str) or not canonical_url.startswith(
            "https://www.protocols.io/"
        ):
            canonical_url = f"https://www.protocols.io/view/{quote(uri, safe='/-')}"
        content = {
            "title": title.strip(),
            "description": payload.get("description") or "",
            "before_start": payload.get("before_start") or "",
            "guidelines": payload.get("guidelines") or "",
            "materials": materials,
            "steps": steps,
            "warnings": warnings,
        }
        digest = hashlib.sha256(_canonical_json(content)).hexdigest()
        metadata = {
            "doi": doi or None,
            "protocol_uri": uri,
            "version_uri": version_uri,
            "version_id": version_id,
            "version_code": version_code,
            "authors": _author_names(payload.get("authors")),
            "license": payload.get("license") or payload.get("license_name"),
            "source_status": str(source_status),
            "published_on": published_on,
            "modified_on": payload.get("modified_on"),
            "owner": (payload.get("creator") or {}).get("name")
            if isinstance(payload.get("creator"), dict)
            else None,
            "risk_state": "review_required",
        }
        return SourceSnapshot(
            connector_kind="protocols_io",
            external_id=doi or uri,
            version_identity=version_identity,
            source_hash=digest,
            canonical_url=canonical_url,
            title=title.strip(),
            metadata=metadata,
            content=content,
        )


class GoogleDriveConnector:
    def __init__(
        self,
        *,
        access_token: str,
        allowed_folder_ids: tuple[str, ...],
        shared_drive_id: str | None = None,
        transport: ReadOnlyHttpTransport | None = None,
    ) -> None:
        if not access_token:
            raise SourceAuthorizationError("A Google Drive access token is required.")
        if not allowed_folder_ids or any(
            _DRIVE_ID.fullmatch(item) is None for item in allowed_folder_ids
        ):
            raise SourceIdentifierError("Google Drive folder allowlist is invalid.")
        if shared_drive_id is not None and _DRIVE_ID.fullmatch(shared_drive_id) is None:
            raise SourceIdentifierError("Google Shared Drive identifier is invalid.")
        self._access_token = access_token
        self.allowed_folder_ids = frozenset(allowed_folder_ids)
        self.shared_drive_id = shared_drive_id
        self._transport = transport or RequestsReadOnlyTransport()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def list_snapshots(self, folder_id: str) -> tuple[SourceSnapshot, ...]:
        if folder_id not in self.allowed_folder_ids:
            raise SourceAuthorizationError("Google Drive folder is outside the allowlist.")
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "spaces": "drive",
            "pageSize": "1000",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,createdTime,headRevisionId,parents,driveId,owners(displayName),lastModifyingUser(displayName),webViewLink,md5Checksum,size)",
            "corpora": "drive" if self.shared_drive_id else "user",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
        }
        if self.shared_drive_id:
            params["driveId"] = self.shared_drive_id
        snapshots: list[SourceSnapshot] = []
        page_token: str | None = None
        for _ in range(100):
            if page_token:
                params["pageToken"] = page_token
            payload = _checked(
                self._transport.get(
                    f"{GOOGLE_DRIVE_API_ROOT}/files",
                    headers=self._headers,
                    params=params,
                )
            ).json()
            files = payload.get("files")
            if not isinstance(files, list):
                raise SourceResponseError("Google Drive files response is invalid.")
            for item in files:
                if isinstance(item, dict) and item.get("mimeType") in {
                    PDF_MIME_TYPE,
                    GOOGLE_DOC_MIME_TYPE,
                }:
                    snapshots.append(self._snapshot(item, folder_id))
            page_token = payload.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                break
        else:
            raise SourceResponseError("Google Drive pagination limit was exceeded.")
        return tuple(snapshots)

    def _snapshot(self, item: Mapping[str, object], folder_id: str) -> SourceSnapshot:
        file_id = item.get("id")
        name = item.get("name")
        mime_type = item.get("mimeType")
        if (
            not isinstance(file_id, str)
            or _DRIVE_ID.fullmatch(file_id) is None
            or not isinstance(name, str)
            or not name.strip()
        ):
            raise SourceResponseError("Google Drive file identity is invalid.")
        if mime_type == GOOGLE_DOC_MIME_TYPE:
            content_result = _checked(
                self._transport.get(
                    f"{GOOGLE_DRIVE_API_ROOT}/files/{quote(file_id, safe='')}/export",
                    headers=self._headers,
                    params={"mimeType": PDF_MIME_TYPE},
                )
            )
        else:
            content_result = _checked(
                self._transport.get(
                    f"{GOOGLE_DRIVE_API_ROOT}/files/{quote(file_id, safe='')}",
                    headers=self._headers,
                    params={"alt": "media", "supportsAllDrives": "true"},
                )
            )
        if len(content_result.content) > MAX_CONNECTOR_DOCUMENT_BYTES:
            raise SourceResponseError("Google Drive file exceeds the size limit.")
        digest = hashlib.sha256(content_result.content).hexdigest()
        version_identity = str(
            item.get("headRevisionId") or item.get("modifiedTime") or digest
        )
        owners = item.get("owners")
        owner_names = _author_names(owners)
        content = {
            "document": {
                "format": "pdf",
                "sha256": digest,
                "byte_count": len(content_result.content),
                "analysis_state": "analysis_pending",
            }
        }
        metadata = {
            "drive_file_id": file_id,
            "folder_id": folder_id,
            "drive_id": item.get("driveId") or self.shared_drive_id,
            "folder_source_path": f"drive://{folder_id}/{name}",
            "modified_time": item.get("modifiedTime"),
            "created_time": item.get("createdTime"),
            "head_revision_id": item.get("headRevisionId"),
            "owners": owner_names,
            "last_modifying_user": (
                item.get("lastModifyingUser") or {}
            ).get("displayName")
            if isinstance(item.get("lastModifyingUser"), dict)
            else None,
            "source_status": "Imported draft",
            "risk_state": "review_required",
        }
        canonical_url = item.get("webViewLink")
        if not isinstance(canonical_url, str) or not canonical_url.startswith(
            "https://drive.google.com/"
        ):
            canonical_url = f"https://drive.google.com/open?id={quote(file_id, safe='')}"
        return SourceSnapshot(
            connector_kind="google_drive",
            external_id=file_id,
            version_identity=version_identity,
            source_hash=digest,
            canonical_url=canonical_url,
            title=name.strip(),
            metadata=metadata,
            content=content,
            binary_content=content_result.content,
            media_type=PDF_MIME_TYPE,
        )

    def start_page_token(self) -> str:
        params = {"supportsAllDrives": "true"}
        if self.shared_drive_id:
            params["driveId"] = self.shared_drive_id
        payload = _checked(
            self._transport.get(
                f"{GOOGLE_DRIVE_API_ROOT}/changes/startPageToken",
                headers=self._headers,
                params=params,
            )
        ).json()
        token = payload.get("startPageToken")
        if not isinstance(token, str) or not token:
            raise SourceResponseError("Google Drive change token is absent.")
        return token

    def changed_file_ids(self, page_token: str) -> tuple[tuple[str, ...], str]:
        if not isinstance(page_token, str) or not page_token or len(page_token) > 500:
            raise SourceIdentifierError("Google Drive change token is invalid.")
        params = {
            "pageToken": page_token,
            "spaces": "drive",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
            "fields": "nextPageToken,newStartPageToken,changes(fileId,removed,file(id,parents,trashed))",
        }
        if self.shared_drive_id:
            params["driveId"] = self.shared_drive_id
        changed: set[str] = set()
        for _ in range(100):
            payload = _checked(
                self._transport.get(
                    f"{GOOGLE_DRIVE_API_ROOT}/changes",
                    headers=self._headers,
                    params=params,
                )
            ).json()
            entries = payload.get("changes")
            if not isinstance(entries, list):
                raise SourceResponseError("Google Drive changes response is invalid.")
            for change in entries:
                if not isinstance(change, dict) or change.get("removed"):
                    continue
                file_info = change.get("file")
                parents = file_info.get("parents", []) if isinstance(file_info, dict) else []
                file_id = change.get("fileId")
                if isinstance(file_id, str) and self.allowed_folder_ids.intersection(parents):
                    changed.add(file_id)
            next_token = payload.get("nextPageToken")
            if isinstance(next_token, str) and next_token:
                params["pageToken"] = next_token
                continue
            new_token = payload.get("newStartPageToken")
            if not isinstance(new_token, str) or not new_token:
                raise SourceResponseError("Google Drive next change token is absent.")
            return tuple(sorted(changed)), new_token
        raise SourceResponseError("Google Drive changes pagination limit was exceeded.")


def _safe_repository_path(path: str) -> str:
    if not isinstance(path, str) or not path or len(path) > 500:
        raise SourceIdentifierError("GitHub source path is invalid.")
    normalized = path.strip("/")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts) or any(
        "\\" in part or "\x00" in part for part in parts
    ):
        raise SourceIdentifierError("GitHub source path is invalid.")
    return normalized


class GitHubConnector:
    def __init__(
        self,
        *,
        installation_token: str,
        allowed_repositories: tuple[str, ...],
        allowed_refs: tuple[str, ...] = ("main",),
        allowed_path_prefixes: tuple[str, ...] = ("protocols/", "workflows/"),
        transport: ReadOnlyHttpTransport | None = None,
    ) -> None:
        if not installation_token:
            raise SourceAuthorizationError("A GitHub App installation token is required.")
        if not allowed_repositories or any(
            _REPOSITORY.fullmatch(item) is None for item in allowed_repositories
        ):
            raise SourceIdentifierError("GitHub repository allowlist is invalid.")
        if not allowed_refs or any(_GIT_REF.fullmatch(item) is None for item in allowed_refs):
            raise SourceIdentifierError("GitHub ref allowlist is invalid.")
        self._token = installation_token
        self.allowed_repositories = frozenset(allowed_repositories)
        self.allowed_refs = frozenset(allowed_refs)
        self.allowed_path_prefixes = tuple(
            _safe_repository_path(prefix) + "/" for prefix in allowed_path_prefixes
        )
        self._transport = transport or RequestsReadOnlyTransport()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def fetch(self, repository: str, ref: str, path: str) -> SourceSnapshot:
        if repository not in self.allowed_repositories or ref not in self.allowed_refs:
            raise SourceAuthorizationError("GitHub source is outside the allowlist.")
        path = _safe_repository_path(path)
        if not any(path.startswith(prefix) for prefix in self.allowed_path_prefixes):
            raise SourceAuthorizationError("GitHub path is outside the allowlist.")
        repository_path = quote(repository, safe="/")
        commit = _checked(
            self._transport.get(
                f"{GITHUB_API_ROOT}/repos/{repository_path}/commits/{quote(ref, safe='')}",
                headers=self._headers,
            )
        ).json()
        commit_sha = commit.get("sha")
        if not isinstance(commit_sha, str) or _COMMIT_SHA.fullmatch(commit_sha) is None:
            raise SourceResponseError("GitHub commit identity is invalid.")
        repository_info = _checked(
            self._transport.get(
                f"{GITHUB_API_ROOT}/repos/{repository_path}", headers=self._headers
            )
        ).json()
        item = _checked(
            self._transport.get(
                f"{GITHUB_API_ROOT}/repos/{repository_path}/contents/{quote(path, safe='/')}",
                headers=self._headers,
                params={"ref": commit_sha},
            )
        ).json()
        if item.get("type") != "file" or not isinstance(item.get("content"), str):
            raise SourceResponseError("GitHub source is not one file.")
        try:
            encoded_content = "".join(item["content"].split())
            raw = base64.b64decode(encoded_content, validate=True)
        except (ValueError, TypeError) as exc:
            raise SourceResponseError("GitHub file content is invalid.") from exc
        if len(raw) > MAX_CONNECTOR_DOCUMENT_BYTES:
            raise SourceResponseError("GitHub file exceeds the size limit.")
        digest = hashlib.sha256(raw).hexdigest()
        suffix = path.rsplit(".", 1)[-1].casefold() if "." in path else ""
        text_content: str | None = None
        if suffix in {"md", "markdown", "txt", "smk", "nf", "config"} or path.endswith(
            "Snakefile"
        ):
            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SourceResponseError("GitHub text source is not UTF-8.") from exc
        content: dict[str, object] = {
            "document": {
                "path": path,
                "sha256": digest,
                "byte_count": len(raw),
                "text": text_content,
            }
        }
        license_info = repository_info.get("license")
        license_name = (
            license_info.get("spdx_id") if isinstance(license_info, dict) else None
        )
        canonical_url = item.get("html_url")
        expected_prefix = f"https://github.com/{repository}/"
        if not isinstance(canonical_url, str) or not canonical_url.startswith(expected_prefix):
            canonical_url = f"https://github.com/{repository}/blob/{commit_sha}/{quote(path, safe='/')}"
        metadata = {
            "repository": repository,
            "commit_sha": commit_sha,
            "ref": ref,
            "path": path,
            "git_blob_sha": item.get("sha"),
            "license": license_name,
            "source_status": "Imported draft",
            "risk_state": "review_required",
        }
        return SourceSnapshot(
            connector_kind="github",
            external_id=f"{repository}:{path}",
            version_identity=commit_sha,
            source_hash=digest,
            canonical_url=canonical_url,
            title=path.rsplit("/", 1)[-1],
            metadata=metadata,
            content=content,
            binary_content=raw,
            media_type=PDF_MIME_TYPE if suffix == "pdf" else "text/plain",
        )


def verify_github_webhook_signature(
    raw_body: bytes, signature_header: str | None, secret: str
) -> bool:
    if (
        not secret
        or not isinstance(signature_header, str)
        or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature_header)
    ):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


class ProtocolSourceHub:
    """Create a draft revision for each changed immutable source snapshot."""

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def ingest(self, principal: Principal, snapshot: SourceSnapshot) -> SourceImportResult:
        latest = self.store.latest_revision_for_source(
            principal,
            connector_kind=snapshot.connector_kind,
            external_id=snapshot.external_id,
        )
        if latest is not None and latest.source_hash == snapshot.source_hash:
            return SourceImportResult(
                family_id=latest.family_id,
                revision=latest,
                changed=False,
                inbox_state="unchanged",
            )
        if latest is None:
            family = self.store.create_protocol_family(
                principal, title=snapshot.title
            )
            parent_id = None
            change_summary = f"Imported from {snapshot.connector_kind}."
        else:
            family = self.store._family_row(principal, latest.family_id)
            parent_id = latest.revision_id
            change_summary = (
                f"Source changed from {latest.source_hash[:12]} to "
                f"{snapshot.source_hash[:12]}; review required."
            )
        source = self.store.register_source(
            principal,
            connector_kind=snapshot.connector_kind,
            external_id=snapshot.external_id,
            version_identity=snapshot.version_identity,
            source_hash=snapshot.source_hash,
            canonical_url=snapshot.canonical_url,
            metadata=snapshot.metadata,
        )
        revision = self.store.add_protocol_revision(
            principal,
            family_id=family.family_id if hasattr(family, "family_id") else family["family_id"],
            source_id=source.source_id,
            parent_revision_id=parent_id,
            change_summary=change_summary,
            content=snapshot.content,
        )
        return SourceImportResult(
            family_id=revision.family_id,
            revision=revision,
            changed=True,
            inbox_state="new" if latest is None else "changed",
        )
