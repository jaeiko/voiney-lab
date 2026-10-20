"""Bring a protocol in from a Google Drive link (lane DR, human decision 2026-10-20).

``POST /api/protocols/import-link {url}`` takes a link a person copied from
Google Drive, Docs or Sheets, downloads the file it names, and hands the file
to the upload route itself -- the same media-type check, size limit,
conversion, OCR and analysis an uploaded file meets. This module is the part
before that: reading the link, the download, and recording the address as the
protocol's source.

What a link may be:

* a Google Drive file link (``drive.google.com/file/d/<id>/...``,
  ``drive.google.com/open?id=<id>``, ``drive.google.com/uc?id=<id>``,
  ``drive.usercontent.google.com/download?id=<id>``) -- downloaded from
  ``drive.usercontent.google.com``, read as a PDF, a Word document or a photo;
* a Google Docs link (``docs.google.com/document/d/<id>/...``) -- exported as
  a PDF;
* a Google Sheets link (``docs.google.com/spreadsheets/d/<id>/...``) --
  exported as .xlsx and attached as a reagent table, only to a protocol the
  request names.

Security (decided 2026-10-20 with the evidence in the lane DR report):

* A link must be ``https`` on one of ``ALLOWED_LINK_HOSTS``, on the default
  port, with no user name or password in it.
* Google hands a Docs or Sheets export on to a host of its own
  (``doc-0g-3o-docstext.googleusercontent.com``,
  ``doc-08-4o-sheets.googleusercontent.com`` as measured on 2026-10-20), so a
  redirect may also go to a host matching ``_EXPORT_HOST``. That pattern is
  never accepted as the link itself. A redirect to Google's sign-in
  (``accounts.google.com``) means the file is not shared with "anyone with
  the link"; any other redirect is refused, not followed. At most
  ``MAX_REDIRECTS`` hops.
* No cookie, no credential and no Referer is sent: every hop is a new
  request with a fixed User-Agent, cookies Google set on one hop are dropped,
  and the client does not read the environment (no proxy, no ``.netrc``).
* Every host's addresses are resolved first and each must be a public
  (global) address; the address the connection reached is checked again
  before the body is read. Nothing reaches a private, loopback, link-local or
  reserved address.
* The download stops at the upload's own limit (``MAX_PROTOCOL_PDF_BYTES``;
  ``MAX_REAGENT_TABLE_BYTES`` for a reagent table) and at
  ``DOWNLOAD_DEADLINE_SECONDS`` in all.
* The file's format is read from its bytes, never from Google's
  Content-Type or the file name.

A private file is not downloaded some other way: the person is told to change
its sharing or to download and upload it (signing in to Google is for later,
a person's decision).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit

import httpx
from starlette.requests import Request

from voiney_lab.experiment_protocol_pdf import (
    DOCX_MEDIA_TYPE,
    MAX_PROTOCOL_PDF_BYTES,
    PDF_MEDIA_TYPE,
)
from voiney_lab.protocol_catalog import MAX_REAGENT_TABLE_BYTES, REAGENT_TABLE_MEDIA_TYPE

#: The hosts a link may name. drive.google.com and docs.google.com are where
#: Drive, Docs and Sheets share links point; drive.usercontent.google.com is
#: where Drive's own download (``uc?export=download``) is handed on to.
ALLOWED_LINK_HOSTS = frozenset({
    "drive.google.com",
    "docs.google.com",
    "drive.usercontent.google.com",
})
#: The hosts Google hands a Docs or Sheets export on to. Accepted only as a
#: redirect from an allowed host, never as a link.
_EXPORT_HOST = re.compile(
    r"^doc-[0-9a-z]{2,4}-[0-9a-z]{2,4}-(?:docs|docstext|sheets)\.googleusercontent\.com$")
#: Google's sign-in: a redirect here means the file is not shared publicly.
_SIGN_IN_HOSTS = frozenset({"accounts.google.com"})
MAX_REDIRECTS = 5
#: The whole download, from the first request to the last byte.
DOWNLOAD_DEADLINE_SECONDS = 60.0
#: Each connection and each read.
_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_USER_AGENT = "voiney-lab-link-import/1"
_CHUNK_BYTES = 64 * 1024

#: The formats a protocol source may be, as read from the bytes; the upload
#: route reads them by these media types.
JPEG_MEDIA_TYPE = "image/jpeg"
PNG_MEDIA_TYPE = "image/png"
HEIC_MEDIA_TYPE = "image/heic"
_PROTOCOL_MEDIA_TYPES = frozenset({
    PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE, JPEG_MEDIA_TYPE, PNG_MEDIA_TYPE, HEIC_MEDIA_TYPE})
_EXTENSIONS = {
    PDF_MEDIA_TYPE: ("pdf",),
    DOCX_MEDIA_TYPE: ("docx",),
    JPEG_MEDIA_TYPE: ("jpg", "jpeg"),
    PNG_MEDIA_TYPE: ("png",),
    HEIC_MEDIA_TYPE: ("heic", "heif"),
    REAGENT_TABLE_MEDIA_TYPE: ("xlsx",),
}
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
_HTML_MEDIA_TYPE = "text/html"

_FILE_ID = r"[A-Za-z0-9_-]{10,200}"
_DRIVE_FILE_PATH = re.compile(rf"^/file(?:/u/\d+)?/d/({_FILE_ID})(?:/[^/]*)?/?$")
_DRIVE_FOLDER_PATH = re.compile(r"^/drive(?:/u/\d+)?/(?:folders|my-drive|shared-with-me)")
_DOCS_PATH = re.compile(
    rf"^/(document|spreadsheets|presentation)(?:/u/\d+)?/d/({_FILE_ID})(?:/[^/]*)?/?$")
_RESOURCE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

LINK_EVENT_TYPE = "protocol_source_linked"


class LinkImportError(Exception):
    """Why a link was not brought in: ``code`` for the screen, a sentence to show."""

    def __init__(self, code: str, *, limit_bytes: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.limit_bytes = limit_bytes

    @property
    def status_code(self) -> int:
        return LINK_IMPORT_STATUS[self.code]

    @property
    def message(self) -> str:
        if self.code == "link_too_large":
            limit = self.limit_bytes or MAX_PROTOCOL_PDF_BYTES
            what = "시약 표가" if limit == MAX_REAGENT_TABLE_BYTES else "파일이"
            return (
                f"{what} 너무 커서 가져올 수 없어요. {limit // (1024 * 1024)}MB 까지 "
                "가져올 수 있어요. 파일 크기를 줄이거나 나눠서 올려 주세요."
            )
        return LINK_IMPORT_MESSAGES[self.code]

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


LINK_IMPORT_STATUS = {
    "link_sign_in_required": 401,
    "link_not_google_drive": 422,
    "link_not_a_file": 422,
    "link_sheet_needs_protocol": 422,
    "link_private": 403,
    "link_not_found": 404,
    "link_too_large": 413,
    "link_unsupported_format": 415,
    "link_not_a_reagent_table": 415,
    "link_redirect_refused": 502,
    "link_fetch_failed": 502,
    "link_timeout": 504,
}
#: The server's sentences (human decision 2 of 2026-10-20, 해요체); the
#: private-file sentence is the decided one, word for word. A file too large
#: names the limit it passed (``LinkImportError.message``).
LINK_IMPORT_MESSAGES = {
    "link_sign_in_required": "로그인한 뒤에 링크로 가져올 수 있어요.",
    "link_not_google_drive": (
        "구글 드라이브·구글 문서·구글 시트의 링크만 가져올 수 있어요. "
        "주소가 https://drive.google.com 이나 https://docs.google.com 으로 "
        "시작하는지 확인해 주세요."
    ),
    "link_not_a_file": (
        "이 링크는 파일 하나를 가리키지 않아요. 폴더라면 폴더 안의 파일을 열고 "
        "그 파일의 공유 링크를 넣어 주세요."
    ),
    "link_sheet_needs_protocol": (
        "구글 시트는 프로토콜에 시약 표로만 붙일 수 있어요. 프로토콜을 연 뒤 "
        "시약 표 자리에서 링크를 넣어 주세요."
    ),
    "link_private": (
        "이 파일은 공유 설정이 '링크가 있는 모든 사용자'가 아니라서 가져올 수 없어요. "
        "공유 설정을 바꾸거나 파일을 내려받아 올려 주세요."
    ),
    "link_not_found": (
        "이 링크의 파일을 찾지 못했어요. 주소가 맞는지 확인하고, 공유 설정이 "
        "'링크가 있는 모든 사용자'인지도 확인해 주세요."
    ),
    "link_unsupported_format": (
        "이 파일은 PDF·Word 문서·사진(JPG·PNG·HEIC)이 아니라서 가져올 수 없어요. "
        "이 형식으로 저장해 링크를 넣거나 파일을 올려 주세요."
    ),
    "link_not_a_reagent_table": (
        "시약 표로는 구글 시트나 엑셀(.xlsx) 파일만 붙일 수 있어요. "
        "링크가 시트를 가리키는지 확인해 주세요."
    ),
    "link_redirect_refused": (
        "구글 드라이브가 허용하지 않은 주소로 넘겨줘서 가져오지 않았어요. "
        "파일을 내려받아 올려 주세요."
    ),
    "link_fetch_failed": (
        "구글 드라이브에서 파일을 받지 못했어요. 잠시 뒤 다시 하거나 "
        "파일을 내려받아 올려 주세요."
    ),
    "link_timeout": "구글 드라이브가 제때 답하지 않아 가져오지 못했어요. 잠시 뒤 다시 해 주세요.",
}


@dataclass(frozen=True)
class GoogleLink:
    """A link read: what it names and where its file is downloaded from."""

    url: str
    kind: str  # "drive_file", "document" or "spreadsheet"
    file_id: str
    resource_key: str | None = None

    @property
    def download_url(self) -> str:
        key = {"resourcekey": self.resource_key} if self.resource_key else {}
        if self.kind == "drive_file":
            query = urlencode({"id": self.file_id, "export": "download", **key})
            return f"https://drive.usercontent.google.com/download?{query}"
        export = "pdf" if self.kind == "document" else "xlsx"
        query = urlencode({"format": export, **key})
        return f"https://docs.google.com/{self.kind_path}/d/{self.file_id}/export?{query}"

    @property
    def kind_path(self) -> str:
        return {"document": "document", "spreadsheet": "spreadsheets"}[self.kind]


@dataclass(frozen=True)
class FetchedLink:
    """A file downloaded from a link, on disk, with what it was read as."""

    link: GoogleLink
    path: Path
    media_type: str
    byte_size: int
    sha256: str
    filename: str
    final_host: str
    hops: tuple[str, ...] = field(default=())

    def source_record(self, *, attached_as: str, table_id: str | None = None) -> dict[str, object]:
        """The source as the ledger keeps it."""

        return {
            "url": self.link.url,
            "link_kind": self.link.kind,
            "file_id": self.link.file_id,
            "download_url": self.link.download_url,
            "final_host": self.final_host,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "filename": self.filename,
            "attached_as": attached_as,
            **({"table_id": table_id} if table_id is not None else {}),
        }


def _host_of(parts) -> str:
    return (parts.hostname or "").casefold().rstrip(".")


def _plain_https(parts) -> bool:
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.username is None
        and parts.password is None
        and port in (None, 443)
    )


def parse_google_link(value: object) -> GoogleLink:
    """Read a share link; refuse what is not a Google Drive, Docs or Sheets file."""

    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise LinkImportError("link_not_google_drive")
    text = value.strip()
    if any(ord(character) < 0x21 for character in text):
        raise LinkImportError("link_not_google_drive")
    parts = urlsplit(text)
    host = _host_of(parts)
    if not _plain_https(parts) or host not in ALLOWED_LINK_HOSTS:
        raise LinkImportError("link_not_google_drive")
    # The fragment (#gid=0, #heading=...) never names another file.
    url = text.split("#", 1)[0]
    query = parse_qs(parts.query)
    key = (query.get("resourcekey") or [None])[0]
    if key is not None and not _RESOURCE_KEY.fullmatch(key):
        key = None
    path = parts.path

    def query_id() -> str | None:
        found = (query.get("id") or [None])[0]
        return found if found and re.fullmatch(_FILE_ID, found) else None

    if host == "drive.google.com":
        match = _DRIVE_FILE_PATH.fullmatch(path)
        if match:
            return GoogleLink(url, "drive_file", match.group(1), key)
        if path in {"/open", "/uc"} and query_id():
            return GoogleLink(url, "drive_file", query_id(), key)
        raise LinkImportError("link_not_a_file")
    if host == "drive.usercontent.google.com":
        if path in {"/download", "/uc", "/u/0/uc"} and query_id():
            return GoogleLink(url, "drive_file", query_id(), key)
        raise LinkImportError("link_not_a_file")
    match = _DOCS_PATH.fullmatch(path)
    if match is None:
        if path == "/uc" and query_id():
            return GoogleLink(url, "drive_file", query_id(), key)
        raise LinkImportError("link_not_a_file")
    kind, file_id = match.groups()
    if kind == "presentation":
        raise LinkImportError("link_unsupported_format")
    return GoogleLink(url, "document" if kind == "document" else "spreadsheet", file_id, key)


def _resolve(host: str) -> list[str]:
    """The addresses a host name resolves to."""

    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def _transport() -> httpx.BaseTransport | None:
    """The transport downloads use; None is httpx's own (tests put a fake here)."""

    return None


def _public_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return parsed.is_global and not parsed.is_multicast


def _check_resolves_public(host: str) -> None:
    try:
        addresses = _resolve(host)
    except OSError as exc:
        raise LinkImportError("link_fetch_failed") from exc
    if not addresses or not all(_public_address(item) for item in addresses):
        raise LinkImportError("link_redirect_refused")


def _check_connected_public(response: httpx.Response) -> None:
    """The address the connection reached, when the transport tells it."""

    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    try:
        peer = stream.get_extra_info("server_addr")
    except Exception:  # noqa: BLE001 - a transport without the answer
        return
    if peer and not _public_address(str(peer[0])):
        raise LinkImportError("link_redirect_refused")


def _redirect_target(current: str, location: str | None) -> str:
    """Where a redirect points, refused unless it stays with Google's file hosts."""

    if not location:
        raise LinkImportError("link_fetch_failed")
    target = urljoin(current, location.strip())
    parts = urlsplit(target)
    host = _host_of(parts)
    if host in _SIGN_IN_HOSTS or (
            host in ALLOWED_LINK_HOSTS and parts.path.startswith(("/ServiceLogin", "/accounts"))):
        raise LinkImportError("link_private")
    if not _plain_https(parts) or not (host in ALLOWED_LINK_HOSTS or _EXPORT_HOST.fullmatch(host)):
        raise LinkImportError("link_redirect_refused")
    return target


def sniff_media_type(path: Path) -> str | None:
    """What the file is, read from its bytes."""

    with path.open("rb") as stream:
        head = stream.read(1024)
    if head.find(b"%PDF-") != -1:
        return PDF_MEDIA_TYPE
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG_MEDIA_TYPE
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG_MEDIA_TYPE
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        return HEIC_MEDIA_TYPE
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, OSError):
            return None
        if "word/document.xml" in names:
            return DOCX_MEDIA_TYPE
        if "xl/workbook.xml" in names:
            return REAGENT_TABLE_MEDIA_TYPE
        return None
    if head.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        return _HTML_MEDIA_TYPE
    return None


def _disposition_filename(header: str | None) -> str | None:
    if not header:
        return None
    star = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", header)
    if star:
        return unquote(star.group(1).strip().strip('"'))
    plain = re.search(r'filename\s*=\s*"([^"]*)"', header) or re.search(
        r"filename\s*=\s*([^;]+)", header)
    return plain.group(1).strip() if plain else None


def safe_filename(name: str | None, media_type: str, file_id: str) -> str:
    """A plain file name with the extension its bytes call for."""

    extensions = _EXTENSIONS[media_type]
    text = unicodedata.normalize("NFC", name or "")
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(character for character in text
                   if unicodedata.category(character)[0] != "C").strip().strip(".")
    if not text:
        return f"google-drive-{file_id[:12]}.{extensions[0]}"
    stem, dot, extension = text.rpartition(".")
    if not (dot and stem and extension.casefold() in extensions):
        stem = text
        extension = extensions[0]
    return f"{stem[:200]}.{extension}"


def _refusal_from_html(path: Path, limit: int) -> LinkImportError:
    """What a page Google answered with instead of the file means."""

    text = path.read_bytes()[:200_000].decode("utf-8", "replace").casefold()
    if "accounts.google.com" in text or "servicelogin" in text:
        return LinkImportError("link_private")
    if "virus" in text:
        # Drive asks before handing over a file too large to scan for viruses
        # (over 100 MB), which is past the upload limit anyway.
        return LinkImportError("link_too_large", limit_bytes=limit)
    if "quota" in text or "too many users" in text:
        return LinkImportError("link_fetch_failed")
    return LinkImportError("link_unsupported_format")


def fetch_google_link(
    link: GoogleLink,
    directory: Path,
    *,
    reagent_table: bool,
    clock: Callable[[], float] = time.monotonic,
) -> FetchedLink:
    """Download the file a link names into ``directory``, every check applied."""

    limit = MAX_REAGENT_TABLE_BYTES if reagent_table else MAX_PROTOCOL_PDF_BYTES
    deadline = clock() + DOWNLOAD_DEADLINE_SECONDS
    destination = directory / "linked.download"
    url = link.download_url
    hops: list[str] = []
    with httpx.Client(
        transport=_transport(), trust_env=False, follow_redirects=False, timeout=_TIMEOUT,
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            host = _host_of(parts)
            _check_resolves_public(host)
            hops.append(url)
            # A new request each hop: nothing Google set on one hop is sent on.
            client.cookies.clear()
            request = client.build_request("GET", url, headers={
                "User-Agent": _USER_AGENT, "Accept": "*/*", "Accept-Encoding": "identity"})
            try:
                response = client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise LinkImportError("link_timeout") from exc
            except httpx.HTTPError as exc:
                raise LinkImportError("link_fetch_failed") from exc
            try:
                _check_connected_public(response)
                if response.is_redirect:
                    url = _redirect_target(url, response.headers.get("location"))
                    continue
                if response.status_code in (401, 403):
                    raise LinkImportError("link_private")
                if response.status_code == 404:
                    raise LinkImportError("link_not_found")
                if response.status_code != 200:
                    raise LinkImportError("link_fetch_failed")
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > limit:
                    raise LinkImportError("link_too_large", limit_bytes=limit)
                byte_size, digest = _spool(response, destination, limit, deadline, clock)
                filename_header = response.headers.get("content-disposition")
            finally:
                response.close()
            break
        else:
            raise LinkImportError("link_redirect_refused")
    if byte_size == 0:
        raise LinkImportError("link_unsupported_format")
    media_type = sniff_media_type(destination)
    if media_type == _HTML_MEDIA_TYPE:
        raise _refusal_from_html(destination, limit)
    if reagent_table:
        if media_type != REAGENT_TABLE_MEDIA_TYPE:
            raise LinkImportError("link_not_a_reagent_table")
    elif media_type == REAGENT_TABLE_MEDIA_TYPE:
        raise LinkImportError("link_sheet_needs_protocol")
    elif media_type not in _PROTOCOL_MEDIA_TYPES:
        raise LinkImportError("link_unsupported_format")
    return FetchedLink(
        link=link,
        path=destination,
        media_type=media_type,
        byte_size=byte_size,
        sha256=digest,
        filename=safe_filename(
            _disposition_filename(filename_header), media_type, link.file_id),
        final_host=_host_of(urlsplit(url)),
        hops=tuple(hops),
    )


def _spool(
    response: httpx.Response,
    destination: Path,
    limit: int,
    deadline: float,
    clock: Callable[[], float],
) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with destination.open("xb") as stream:
            for chunk in response.iter_bytes(_CHUNK_BYTES):
                if clock() > deadline:
                    raise LinkImportError("link_timeout")
                byte_size += len(chunk)
                if byte_size > limit:
                    raise LinkImportError("link_too_large", limit_bytes=limit)
                digest.update(chunk)
                stream.write(chunk)
    except httpx.TimeoutException as exc:
        destination.unlink(missing_ok=True)
        raise LinkImportError("link_timeout") from exc
    except httpx.HTTPError as exc:
        destination.unlink(missing_ok=True)
        raise LinkImportError("link_fetch_failed") from exc
    except LinkImportError:
        destination.unlink(missing_ok=True)
        raise
    return byte_size, digest.hexdigest()


def upload_request(fetched: FetchedLink, path: str) -> Request:
    """The downloaded file as the request body the upload route reads."""

    def chunks() -> Iterable[bytes]:
        with fetched.path.open("rb") as stream:
            while chunk := stream.read(_CHUNK_BYTES):
                yield chunk

    source = iter(chunks())

    async def receive() -> dict[str, object]:
        chunk = next(source, b"")
        return {"type": "http.request", "body": chunk, "more_body": bool(chunk)}

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", fetched.media_type.encode()),
            (b"content-length", str(fetched.byte_size).encode()),
        ],
        "http_version": "1.1",
        "scheme": "https",
        "server": ("link-import", 443),
        "client": ("link-import", 0),
    }
    return Request(scope, receive)


def record_source_link(
    store: object,
    protocol_id: str,
    fetched: FetchedLink,
    *,
    attached_as: str,
    table_id: str | None = None,
) -> dict[str, object]:
    """Keep the address as the source in the protocol's ledger.

    One event per link and file: the same link bringing the same bytes again
    finds the event already there.
    """

    record = fetched.source_record(attached_as=attached_as, table_id=table_id)
    revision = store.list_protocol_revisions(protocol_id)[-1]
    identity = hashlib.sha256(
        json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    store.append_event(
        f"source-link-{identity[:40]}",
        protocol_id,
        revision.revision_number,
        LINK_EVENT_TYPE,
        record,
    )
    return record
