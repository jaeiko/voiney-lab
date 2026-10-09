"""A web explanation and a licensed photograph for a thing asked about (lane WV, decision 2).

"써모믹서가 어떻게 생겼어?", "웹에서 찾아봐": the uploaded protocol has
answered first, by rule, and this module adds what the web says the thing
is and looks like -- shown on the screen with its sources, said in one or
two sentences, and never a value or a safety instruction.

Provider (decision of 2026-10-09, from the official documents read that
day): the OpenAI Responses API's ``web_search`` tool. Its documentation
requires that "inline citations must be made clearly visible and clickable
in your user interface" and states no limit on storing the results; the
page shows every citation as a link. Google's Grounding with Google Search
was not chosen because its terms forbid caching the grounded results,
modifying them or interspersing other content with them, and require the
Search Suggestions to be displayed with them (lane RP found the same); the
checks below take sentences out of the answer, which those terms do not
allow. Anthropic's web search tool ($10 per 1,000 searches, citations
required when outputs are shown directly) is equivalent in terms; the
OpenAI key is the one this environment has.

The photograph comes from Wikimedia Commons (the MediaWiki API, with the
User-Agent its etiquette requires), only when the file's licence is one
the Commons reuse guide lists as free (public domain, CC0, CC BY, CC BY-SA)
and is named with the author and the licence under the picture, and the
bytes are fetched by the server from Wikimedia's own hosts, checked, and
served same-origin -- never hot-linked.

Checks, all server-side and deterministic (``answer_checks``): a sentence
with any number, a sentence that gives or permits a safety instruction, a
sentence that claims a state change or asks a server question is taken
out of both the spoken and the screen text; the sentences taken out are
counted and reported. An answer with no citation is not shown.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import socket
import struct
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from voiney_lab.answer_checks import (
    asks_server_question,
    claims_state_change,
    numbers_in,
    permissive_hazard_topics,
    safety_instruction_topics,
)

log = logging.getLogger("voiney_lab.web_explanations")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})

WEB_SEARCH_BACKEND = "openai_responses_web_search"
COMMONS_BACKEND = "wikimedia_commons_api"
#: What the server sends with every request to Wikimedia (API etiquette).
COMMONS_USER_AGENT = "VoineyLab/0.1 (Voice Workflow Agent; https://github.com/jaeiko/voiney-lab)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
#: Where a Commons file's bytes may be fetched from.
COMMONS_FILE_HOSTS = frozenset({"upload.wikimedia.org", "thumb.wikimedia.org"})
#: Licences the Commons reuse guide lists as free to reuse with attribution.
FREE_LICENCE = re.compile(
    r"^(?:public\s+domain|pd(?:[- ].*)?|cc0(?:\s.*)?|cc[- ]by(?:[- ]sa)?(?:[- ]\d(?:\.\d)?)?(?:\s.*)?)$",
    re.IGNORECASE,
)
PHOTO_MAX_BYTES = 2 * 1024 * 1024
PHOTO_THUMB_WIDTH = 640
#: How many photographs are kept in memory for the same-origin route.
PHOTO_REGISTRY_SIZE = 32

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"), ipaddress.ip_network("fe80::/10"),
)
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
_MARKDOWN_LINK = re.compile(r"\s*\(?\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)\)?")
_BARE_NUMBER = re.compile(r"\d")
_ASSET_ID = re.compile(r"^[0-9a-f]{64}$")


def _enabled(name: str, default: str) -> bool:
    value = os.environ.get(name, default).strip().casefold()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean")


def _timeout(name: str, default: str) -> float:
    try:
        value = float(os.environ.get(name, default).strip())
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if not 3 <= value <= 60:
        raise ValueError(f"{name} is invalid")
    return value


@dataclass(frozen=True)
class WebExplanationSettings:
    """Off by default; on only with the OpenAI key the tool needs."""

    enabled: bool
    model: str = "gpt-6-luna"
    timeout_seconds: float = 15.0
    photos: bool = True

    @classmethod
    def from_environment(cls) -> "WebExplanationSettings":
        if not _enabled("VOINEY_LAB_WEB_EXPLANATIONS_ENABLED", "false"):
            return cls(False)
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise ValueError("VOINEY_LAB_WEB_EXPLANATIONS_ENABLED needs OPENAI_API_KEY")
        model = os.environ.get("VOINEY_LAB_WEB_EXPLANATION_MODEL", "gpt-6-luna").strip()
        if not model:
            raise ValueError("VOINEY_LAB_WEB_EXPLANATION_MODEL is invalid")
        return cls(
            True, model,
            _timeout("VOINEY_LAB_WEB_EXPLANATION_TIMEOUT_SECONDS", "15"),
            _enabled("VOINEY_LAB_WEB_PHOTOS_ENABLED", "true"),
        )

    def public_capability(self) -> dict[str, Any]:
        return {
            "status": "enabled" if self.enabled else "disabled",
            "authority": "web_explanation",
            "model": self.model if self.enabled else None,
            "photos": self.photos if self.enabled else False,
            "backend": WEB_SEARCH_BACKEND,
        }


@dataclass(frozen=True)
class WebCitation:
    title: str
    url: str
    domain: str

    def public_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "domain": self.domain}


@dataclass(frozen=True)
class WebPhoto:
    """One Commons photograph the server fetched, checked and now serves."""

    asset_id: str
    mime_type: str
    content: bytes
    width_px: int
    height_px: int
    title: str
    page_url: str
    author: str
    licence: str
    licence_url: str
    source_url: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "url": f"/api/web-visuals/{self.asset_id}",
            "mime_type": self.mime_type,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "title": self.title,
            "page_url": self.page_url,
            "author": self.author,
            "licence": self.licence,
            "licence_url": self.licence_url,
            "source": "Wikimedia Commons",
            "label": "웹 자료 · 출처 Wikimedia Commons",
        }


@dataclass(frozen=True)
class WebExplanation:
    """What the web said, after the checks; shown with its sources."""

    status: str
    subject: str
    spoken: str = ""
    screen: str = ""
    english_term: str = ""
    citations: tuple[WebCitation, ...] = ()
    removed_sentences: tuple[str, ...] = ()
    search_count: int = 0
    elapsed_ms: int = 0
    model: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)

    @property
    def shown(self) -> bool:
        return self.status == "success" and bool(self.screen or self.spoken) and bool(self.citations)


# --- the checks ---------------------------------------------------------------


def _without_markdown_links(text: str) -> str:
    cleaned = _MARKDOWN_LINK.sub("", text)
    cleaned = re.sub(r"\*\*|__|`", "", cleaned)
    return " ".join(cleaned.split())


def check_sentences(text: str) -> tuple[str, tuple[str, ...]]:
    """The text with the sentences the checks take out removed, and those sentences.

    Taken out: any sentence with a digit (a value is the protocol's to
    give), any sentence that gives or permits a safety instruction, any
    sentence that claims a state change or asks a server question.
    """

    kept: list[str] = []
    removed: list[str] = []
    for sentence in _SENTENCE_END.split(_without_markdown_links(text)):
        candidate = sentence.strip()
        if not candidate:
            continue
        if (
            _BARE_NUMBER.search(candidate) or numbers_in(candidate)
            or safety_instruction_topics(candidate) or permissive_hazard_topics(candidate)
            or claims_state_change(candidate) or asks_server_question(candidate)
        ):
            removed.append(candidate)
            continue
        kept.append(candidate)
    return " ".join(kept), tuple(removed)


def _limit_sentences(text: str, limit: int) -> str:
    parts = [part for part in _SENTENCE_END.split(text) if part.strip()]
    return " ".join(parts[:limit])


# --- the web explanation --------------------------------------------------------

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "english_term": {"type": "string"},
        "spoken": {"type": "string"},
        "screen": {"type": "string"},
    },
    "required": ["english_term", "spoken", "screen"],
}

_DEVELOPER_PROMPT = (
    "You explain one laboratory term to a researcher who is following their own "
    "uploaded protocol, which is the only authority on how the experiment is done. "
    "Search the web, then answer in {language}. Fields: 'spoken' -- at most two short "
    "sentences saying what the thing is and what it looks like, for speech; 'screen' -- "
    "at most three plain sentences for the screen, no markdown, no links, no lists; "
    "'english_term' -- the thing's common English name, for an image search. Never state "
    "a quantity, volume, time, temperature, concentration, speed or count, and never give "
    "a safety instruction or tell the researcher what to do: the protocol decides those. "
    "Do not repeat the protocol's values. Say nothing about steps being done."
)


def _clean_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _citations_from(response: Any) -> tuple[WebCitation, ...]:
    found: dict[str, WebCitation] = {}
    sources: list[tuple[str, str]] = []
    for item in getattr(response, "output", None) or ():
        kind = getattr(item, "type", None)
        if kind == "message":
            for part in getattr(item, "content", None) or ():
                for annotation in getattr(part, "annotations", None) or ():
                    if getattr(annotation, "type", None) != "url_citation":
                        continue
                    url = _clean_url(str(getattr(annotation, "url", "") or ""))
                    if not url.startswith("https://") or url in found:
                        continue
                    found[url] = WebCitation(
                        title=" ".join(str(getattr(annotation, "title", "") or "").split()) or url,
                        url=url, domain=urlsplit(url).hostname or "",
                    )
        elif kind == "web_search_call":
            action = getattr(item, "action", None)
            for source in getattr(action, "sources", None) or ():
                url = _clean_url(str(getattr(source, "url", "") or ""))
                if url.startswith("https://"):
                    sources.append((url, str(getattr(source, "title", "") or "")))
    if not found:
        for url, title in sources[:3]:
            found.setdefault(url, WebCitation(title=title or url, url=url, domain=urlsplit(url).hostname or ""))
    return tuple(found.values())


def _search_count(response: Any) -> int:
    return sum(
        1 for item in getattr(response, "output", None) or ()
        if getattr(item, "type", None) == "web_search_call"
    )


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def _parsed(response: Any) -> dict[str, str]:
    text = str(getattr(response, "output_text", "") or "")
    if not text:
        for item in getattr(response, "output", None) or ():
            if getattr(item, "type", None) == "message":
                for part in getattr(item, "content", None) or ():
                    if getattr(part, "type", None) == "output_text":
                        text += str(getattr(part, "text", "") or "")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {"english_term": "", "spoken": text, "screen": text}
    if not isinstance(payload, dict):
        return {"english_term": "", "spoken": text, "screen": text}
    return {key: str(payload.get(key) or "") for key in ("english_term", "spoken", "screen")}


async def explain_with_web(
    client: Any,
    settings: WebExplanationSettings,
    *,
    subject: str,
    question: str,
    step_text: str,
    protocol_names: str = "",
    language: str = "ko",
) -> WebExplanation:
    """Ask the web what ``subject`` is; the checks decide what reaches the person."""

    started = time.perf_counter()
    language_name = {"ko": "Korean", "en": "English", "vi": "Vietnamese"}.get(language, "Korean")
    user = (
        f"Protocol step (the researcher's own): {step_text.strip()}\n"
        + (f"Named in the protocol: {protocol_names.strip()}\n" if protocol_names.strip() else "")
        + f"The thing asked about: {subject}\nQuestion: {question.strip()}"
    )
    try:
        response = await asyncio.wait_for(
            client.responses.create(
                model=settings.model,
                input=[
                    {"role": "developer", "content": _DEVELOPER_PROMPT.format(language=language_name)},
                    {"role": "user", "content": user},
                ],
                tools=[{"type": "web_search", "search_context_size": "low"}],
                include=["web_search_call.action.sources"],
                text={"format": {
                    "type": "json_schema", "name": "web_explanation",
                    "schema": _OUTPUT_SCHEMA, "strict": True,
                }},
                store=False,
                max_output_tokens=700,
            ),
            timeout=settings.timeout_seconds,
        )
    except asyncio.TimeoutError:
        return WebExplanation("timeout", subject, model=settings.model,
                              elapsed_ms=round((time.perf_counter() - started) * 1000))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the provider's failure is reported, not raised
        log.info("web explanation failed category=%s", type(exc).__name__)
        return WebExplanation("provider_error", subject, model=settings.model,
                              elapsed_ms=round((time.perf_counter() - started) * 1000))
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    parsed = _parsed(response)
    spoken, removed_spoken = check_sentences(parsed["spoken"])
    screen, removed_screen = check_sentences(parsed["screen"])
    spoken = _limit_sentences(spoken, 2)
    screen = _limit_sentences(screen, 3)
    citations = _citations_from(response)
    removed = tuple(dict.fromkeys((*removed_spoken, *removed_screen)))
    status = "success"
    if not (spoken or screen):
        status = "empty"
    elif not citations:
        status = "no_citations"
    return WebExplanation(
        status=status, subject=subject, spoken=spoken, screen=screen,
        english_term=" ".join(parsed["english_term"].split())[:80],
        citations=citations, removed_sentences=removed,
        search_count=_search_count(response), elapsed_ms=elapsed_ms,
        model=str(getattr(response, "model", None) or settings.model),
        usage=_usage(response),
    )


# --- the photograph ----------------------------------------------------------------


def _is_safe_remote_url(url: str, hosts: frozenset[str]) -> bool:
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
            return False
        if parts.hostname not in hosts:
            return False
        for family, _kind, _proto, _name, address in socket.getaddrinfo(
            parts.hostname, 443, proto=socket.IPPROTO_TCP,
        ):
            ip = ipaddress.ip_address(address[0])
            if any(ip in network for network in _PRIVATE_NETWORKS):
                return False
        return True
    except Exception:  # noqa: BLE001 - an unresolved host is an unsafe one
        return False


def _image_size(raw: bytes) -> tuple[str, int, int] | None:
    """(mime, width, height) for a PNG or JPEG, None for anything else."""

    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        width, height = struct.unpack(">II", raw[16:24])
        return "image/png", int(width), int(height)
    if raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"):
        offset = 2
        while offset + 9 < len(raw):
            if raw[offset] != 0xFF:
                offset += 1
                continue
            marker = raw[offset + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", raw[offset + 5:offset + 9])
                return "image/jpeg", int(width), int(height)
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                offset += 2
                continue
            length = struct.unpack(">H", raw[offset + 2:offset + 4])[0]
            offset += 2 + length
    return None


def _plain(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html.unescape(text).split())


class PhotoRegistry:
    """The photographs fetched this run, served same-origin by asset id."""

    def __init__(self, size: int = PHOTO_REGISTRY_SIZE) -> None:
        self._size = size
        self._items: dict[str, WebPhoto] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def keep(self, photo: WebPhoto) -> None:
        with self._lock:
            if photo.asset_id not in self._items:
                self._order.append(photo.asset_id)
            self._items[photo.asset_id] = photo
            while len(self._order) > self._size:
                self._items.pop(self._order.pop(0), None)

    def get(self, asset_id: str) -> WebPhoto | None:
        if not _ASSET_ID.match(asset_id or ""):
            return None
        with self._lock:
            return self._items.get(asset_id)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._order.clear()


WEB_PHOTOS = PhotoRegistry()


async def find_commons_photo(
    term: str,
    *,
    timeout_seconds: float = 6.0,
    http_client: Any = None,
    registry: PhotoRegistry = WEB_PHOTOS,
) -> WebPhoto | None:
    """One freely licensed Commons photograph of ``term``, fetched and checked, or None."""

    import httpx

    clean = " ".join(re.findall(r"[0-9A-Za-z가-힣.-]+", term or ""))[:100]
    if not clean:
        return None
    params = {
        "action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {clean}",
        "gsrnamespace": 6, "gsrlimit": 5, "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime|size", "iiurlwidth": PHOTO_THUMB_WIDTH,
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit",
        "format": "json", "formatversion": 2,
    }
    headers = {"User-Agent": COMMONS_USER_AGENT}
    owned = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    try:
        response = await client.get(COMMONS_API, params=params, headers=headers)
        if response.status_code != 200:
            return None
        data = response.json()
        for page in (data.get("query") or {}).get("pages") or ():
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata") or {}
            licence = _plain((meta.get("LicenseShortName") or {}).get("value"))
            mime = str(info.get("mime") or "")
            thumb = _clean_url(str(info.get("thumburl") or info.get("url") or ""))
            if (
                not licence or not FREE_LICENCE.match(licence)
                or mime not in {"image/jpeg", "image/png"}
                or not _is_safe_remote_url(thumb, COMMONS_FILE_HOSTS)
            ):
                continue
            fetched = await client.get(thumb, headers=headers)
            if fetched.status_code != 200 or len(fetched.content) > PHOTO_MAX_BYTES:
                continue
            size = _image_size(fetched.content)
            if size is None:
                continue
            photo = WebPhoto(
                asset_id=hashlib.sha256(fetched.content).hexdigest(),
                mime_type=size[0], content=fetched.content,
                width_px=size[1], height_px=size[2],
                title=_plain(page.get("title")).removeprefix("File:"),
                page_url=_clean_url(str(info.get("descriptionurl") or "")),
                author=_plain((meta.get("Artist") or {}).get("value"))[:120],
                licence=licence[:80],
                licence_url=_clean_url(str((meta.get("LicenseUrl") or {}).get("value") or "")),
                source_url=thumb,
            )
            registry.keep(photo)
            return photo
        return None
    except Exception as exc:  # noqa: BLE001 - no photograph, nothing else
        log.info("commons photo lookup failed category=%s", type(exc).__name__)
        return None
    finally:
        if owned:
            await client.aclose()
