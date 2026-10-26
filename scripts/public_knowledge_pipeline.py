#!/usr/bin/env python3
"""Public knowledge pipeline: sources → fetch → pieces → bundle → Moss (lane KNOW).

    python scripts/public_knowledge_pipeline.py collect [--only ID,ID] [--full]
    python scripts/public_knowledge_pipeline.py status
    python scripts/public_knowledge_pipeline.py glossary
    python scripts/public_knowledge_pipeline.py upload --index NAME [--model moss-minilm] [--dry-run]
    python scripts/public_knowledge_pipeline.py evaluate --questions FILE --backend fts5|moss [--out FILE]

Human decision of 2026-10-24: public experimental, safety and scientific
material -- never the lab's own -- is collected into one bundle and searched
by Moss inside the server. This tool is the collection, run again whenever
the bundle should grow:

- **The source list decides.** ``data/public_knowledge/sources.toml`` names
  every source with its licence evidence, the date it was read and the terms
  of the fetch (rate limit, robots.txt). Only public domain, CC0, CC BY and
  CC BY-SA sources load; a source whose licence text says NC, ND or all
  rights reserved is refused before any request. Candidates left out are
  listed there too, with their reasons.
- **Again, and only what changed.** Each run fetches with conditional
  requests (ETag / Last-Modified, Wikipedia's revision ids, PubChem on a
  refresh interval) into ``<runtime>/raw/``, then rebuilds the whole bundle
  from that cache, so a parser change applies everywhere and piece ids stay
  the same. A source that fails is skipped and written to
  ``bundle/failures.jsonl``; the others go on. ``--full`` refetches.
- **The bundle.** ``bundle/chunks.jsonl`` (one piece per line: tier
  ``public``, title, text, locator, url, licence, language, domain, source,
  grade, version, fetched date), ``bundle/manifest.json`` (counts and bytes
  per source), ``bundle/glossary.json`` (Korean ↔ English names, Wikidata,
  CC0) and ``bundle/failures.jsonl``. Everything is under
  ``data/runtime/public-knowledge/`` by default and is never committed;
  ``glossary`` copies the glossary into ``data/public_knowledge/`` for the
  server.
- **Moss.** ``upload`` sends the bundle to the project's index
  (``MOSS_PROJECT_ID``, ``MOSS_PROJECT_KEY`` from the shell or ``--env-file``,
  never printed); a second upload upserts what changed and deletes what
  left. ``--dry-run`` counts documents and bytes and estimates the server
  memory from Moss's published figures without a client.
- **Measured, not assumed.** ``evaluate`` runs the question set against the
  bundle with an SQLite FTS5 baseline and against the loaded Moss index,
  and reports recall@k, latency p50/p95, load time and RSS growth.

No model is called anywhere here. The script talks only to the sources
listed, to Moss when asked to upload or evaluate, and to nothing else.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html as html_module
import io
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import tomllib
import unicodedata
import urllib.robotparser
import xml.etree.ElementTree as ElementTree
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voiney_lab.public_knowledge import (  # noqa: E402
    GRADES,
    METADATA_FIELDS,
    Glossary,
    default_glossary_path,
    default_words_path,
    index_text_for,
)

SOURCES_FILE = ROOT / "data" / "public_knowledge" / "sources.toml"
DEFAULT_RUNTIME_DIR = ROOT / "data" / "runtime" / "public-knowledge"
KINDS = ("niosh_npg", "pdf", "html", "pubchem", "wikipedia", "wikidata_glossary", "europepmc")
#: Pieces: a piece is at most this many characters and, unless it is a
#: document's only piece, at least the minimum (short lines carry little).
CHUNK_MAX_CHARS = 1000
CHUNK_MIN_CHARS = 80
#: Moss's published figures (docs.moss.dev, Memory Sizing, moss 1.19.0):
#: about 7 KB of RSS per loaded piece, twice that for non-Latin text, 149 MiB
#: for the embedding model and 37 MiB for Python with the SDK.
MOSS_KB_PER_CHUNK = 7.0
MOSS_KB_PER_KOREAN_CHUNK = 14.0
MOSS_MODEL_MIB = 149
MOSS_SDK_MIB = 37
UPLOAD_BATCH = 400
DEFAULT_MODEL = "moss-minilm"
#: How many days a record with no change signal (PubChem, Wikidata) is kept
#: before it is fetched again.
REFRESH_DAYS = 30

LICENSE_ALLOWED = re.compile(
    r"public domain|\bcc0\b|cc[\s-]?by(?:[\s-]?sa)?\b(?![\s-]?n[cd])|"
    r"creative commons attribution(?:[\s-]?share[\s-]?alike)?(?![\s-]?non)|"
    r"not subject to copyright|free of copyright|u\.s\. government work",
    re.I,
)
LICENSE_REFUSED = re.compile(
    r"\bnc\b|non[\s-]?commercial|\bnd\b|no[\s-]?derivative|all rights reserved|proprietary|"
    r"permission (?:is )?required|login required|비상업|변경금지|무단 복제",
    re.I,
)


def license_allowed(text: str) -> bool:
    """Decision 1: public domain, CC0, CC BY, CC BY-SA; nothing with NC or ND."""

    value = text or ""
    return bool(LICENSE_ALLOWED.search(value)) and not LICENSE_REFUSED.search(value)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --- The source list -------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    kind: str
    grade: str
    domains: tuple[str, ...]
    language: str
    license: str
    license_url: str
    license_checked: str
    terms: str
    requests_per_second: float
    options: Mapping[str, Any]

    def option(self, name: str, default: Any = None) -> Any:
        return self.options.get(name, default)

    @property
    def domain(self) -> str:
        return self.domains[0] if self.domains else ""


@dataclass(frozen=True)
class Excluded:
    id: str
    name: str
    reason: str
    evidence_url: str
    checked: str


@dataclass(frozen=True)
class SourceList:
    user_agent: str
    sources: tuple[Source, ...]
    excluded: tuple[Excluded, ...]
    directory: Path

    def by_id(self, source_id: str) -> Source:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(source_id)


class SourceListError(ValueError):
    """The source list is not usable; the message names the entry and the field."""


_REQUIRED = ("id", "name", "kind", "grade", "domains", "language", "license",
             "license_url", "license_checked", "terms")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_sources(path: Path = SOURCES_FILE) -> SourceList:
    """Read and check the source list; a bad entry stops the run by name."""

    payload = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    user_agent = str(payload.get("user_agent", "")).strip()
    if not user_agent or "(" not in user_agent:
        raise SourceListError("user_agent must name the client and a contact address")
    sources: list[Source] = []
    seen: set[str] = set()
    for entry in payload.get("source", []):
        for key in _REQUIRED:
            if not str(entry.get(key, "")).strip() and key != "domains":
                raise SourceListError(f"source {entry.get('id', '?')}: {key} is required")
        source_id = str(entry["id"])
        if not _ID.match(source_id) or source_id in seen:
            raise SourceListError(f"source {source_id}: id must be unique, lowercase, a-z 0-9 and -")
        seen.add(source_id)
        if entry["kind"] not in KINDS:
            raise SourceListError(f"source {source_id}: unknown kind {entry['kind']!r}")
        if entry["grade"] not in GRADES:
            raise SourceListError(f"source {source_id}: grade must be one of {', '.join(GRADES)}")
        if not license_allowed(str(entry["license"])):
            raise SourceListError(
                f"source {source_id}: licence {entry['license']!r} is not public domain, CC0, CC BY or CC BY-SA")
        if not _DATE.match(str(entry["license_checked"])):
            raise SourceListError(f"source {source_id}: license_checked must be a date (YYYY-MM-DD)")
        if not str(entry["license_url"]).startswith("https://"):
            raise SourceListError(f"source {source_id}: license_url must be an https address")
        domains = tuple(str(item) for item in entry.get("domains", []))
        if not domains:
            raise SourceListError(f"source {source_id}: domains is required")
        rate = float(entry.get("requests_per_second", 1.0))
        if not 0 < rate <= 5:
            raise SourceListError(f"source {source_id}: requests_per_second must be within (0, 5]")
        options = {k: v for k, v in entry.items() if k not in _REQUIRED and k != "requests_per_second"}
        sources.append(Source(
            id=source_id, name=str(entry["name"]), kind=str(entry["kind"]), grade=str(entry["grade"]),
            domains=domains, language=str(entry["language"]), license=str(entry["license"]),
            license_url=str(entry["license_url"]), license_checked=str(entry["license_checked"]),
            terms=str(entry["terms"]), requests_per_second=rate, options=options,
        ))
    excluded: list[Excluded] = []
    for entry in payload.get("excluded", []):
        for key in ("id", "name", "reason", "evidence_url", "checked"):
            if not str(entry.get(key, "")).strip():
                raise SourceListError(f"excluded {entry.get('id', '?')}: {key} is required")
        excluded.append(Excluded(*(str(entry[key]) for key in ("id", "name", "reason", "evidence_url", "checked"))))
    for source in sources:
        for name in ("sitelinks_from",):
            other = source.option(name)
            if other and other not in seen:
                raise SourceListError(f"source {source.id}: {name} names an unknown source {other!r}")
        for other in source.option("from_sources", []) or []:
            if other not in seen:
                raise SourceListError(f"source {source.id}: from_sources names an unknown source {other!r}")
    return SourceList(user_agent=user_agent, sources=tuple(sources), excluded=tuple(excluded),
                      directory=Path(path).parent)


# --- Fetching: rate limits, conditional requests, robots ----------------------------------


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str

    def header(self, name: str, default: str = "") -> str:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return default

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


Transport = Callable[[str, Mapping[str, str], float], Response]


def requests_transport(url: str, headers: Mapping[str, str], timeout: float) -> Response:
    import requests  # noqa: PLC0415 - the real transport, not used by the tests

    reply = requests.get(url, headers=dict(headers), timeout=timeout, allow_redirects=True)
    return Response(status=reply.status_code, headers=dict(reply.headers), body=reply.content,
                    url=str(reply.url))


class FetchError(RuntimeError):
    """One request could not be served; the message holds the status or the cause."""


def _mediawiki_lagged(reply: "Response") -> bool:
    """A MediaWiki API answer to ``maxlag``: HTTP 200 with an error body; wait and ask again."""

    body = reply.body[:200]
    return reply.status == 200 and body.lstrip().startswith(b'{"error"') and b'"maxlag"' in body


class OfflineHttp:
    """No request at all (``collect --offline``): the bundle is rebuilt from the
    raw cache, so a parser change is measured on the same material."""

    user_agent = ""

    def __init__(self) -> None:
        self.requests_made = 0
        self.requests_by_host: Counter[str] = Counter()

    def get(self, url: str, **kwargs: Any) -> "Response":
        raise FetchError("offline: no request is made")


class Http:
    """GETs for one run: one agent, a pace per source, retries, a robots gate.

    ``transport`` is the one network call; the tests pass their own. A
    request is retried on 429 and 5xx (honouring Retry-After); PubChem's
    ``X-Throttling-Control`` header slows the pace when it is not green.
    """

    def __init__(
        self,
        user_agent: str,
        *,
        transport: Transport = requests_transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 60.0,
    ) -> None:
        self.user_agent = user_agent
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._timeout = timeout
        self._last_at: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None | str] = {}
        self.requests_made = 0
        self.requests_by_host: Counter[str] = Counter()

    def _pace(self, key: str, per_second: float) -> None:
        interval = 1.0 / per_second
        last = self._last_at.get(key)
        now = self._clock()
        if last is not None and now - last < interval:
            self._sleep(interval - (now - last))
        self._last_at[key] = self._clock()

    def _robots_allow(self, url: str) -> bool:
        host = urlparse(url).netloc
        parsed = self._robots.get(host, "unread")
        if parsed == "unread":
            robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
            try:
                reply = self._transport(robots_url, {"User-Agent": self.user_agent}, self._timeout)
                self.requests_made += 1
                self.requests_by_host[host] += 1
            except Exception:  # noqa: BLE001 - unreachable robots is "unavailable"
                reply = None
            if reply is not None and reply.status == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(reply.text().splitlines())
                parsed = parser
            elif reply is not None and 400 <= reply.status < 500:
                parsed = None  # RFC 9309: no robots.txt, no restriction
            else:
                parsed = "unavailable"
            self._robots[host] = parsed
        if parsed is None:
            return True
        if parsed == "unavailable":
            return False
        return parsed.can_fetch(self.user_agent, url)  # type: ignore[union-attr]

    def crawl_delay(self, url: str) -> float | None:
        """The robots.txt Crawl-delay for this agent on ``url``'s host, once robots.txt was read."""

        parsed = self._robots.get(urlparse(url).netloc)
        if parsed is None or isinstance(parsed, str):
            return None
        try:
            delay = parsed.crawl_delay(self.user_agent)
        except (AttributeError, ValueError):
            return None
        return float(delay) if delay else None

    def get(
        self,
        url: str,
        *,
        pace_key: str,
        per_second: float,
        headers: Mapping[str, str] | None = None,
        robots: str = "check",
        retries: int = 3,
    ) -> Response:
        if robots == "check":
            if not self._robots_allow(url):
                raise FetchError(f"robots.txt disallows {urlparse(url).netloc}{urlparse(url).path}")
            delay = self.crawl_delay(url)
            if delay:
                # The site's Crawl-delay wins over the source list's pace.
                per_second = min(per_second, 1.0 / delay)
        sent = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
        sent.update(headers or {})
        attempt = 0
        while True:
            self._pace(pace_key, per_second)
            attempt += 1
            try:
                reply = self._transport(url, sent, self._timeout)
            except Exception as exc:  # noqa: BLE001 - a network failure retries, then reports
                if attempt > retries:
                    raise FetchError(f"{type(exc).__name__} fetching {urlparse(url).netloc}") from exc
                self._sleep(min(60.0, 5.0 * 3 ** (attempt - 1)))
                continue
            self.requests_made += 1
            self.requests_by_host[urlparse(url).netloc] += 1
            throttle = reply.header("X-Throttling-Control")
            if throttle and ("Yellow" in throttle or "Red" in throttle or "Black" in throttle):
                self._sleep(30.0 if ("Red" in throttle or "Black" in throttle) else 3.0)
            if reply.status in (429, 500, 502, 503, 504) and attempt <= retries \
                    or (_mediawiki_lagged(reply) and attempt <= retries + 3):
                wait = reply.header("Retry-After")
                try:
                    seconds = float(wait) if wait else 5.0 * 3 ** (attempt - 1)
                except ValueError:
                    seconds = 5.0 * 3 ** (attempt - 1)
                self._sleep(min(120.0, seconds))
                continue
            if _mediawiki_lagged(reply):
                # Still lagged after the waits: a failure to record, never a body to cache.
                raise FetchError("MediaWiki maxlag: the site asked us to wait and stayed lagged")
            return reply


# --- The raw cache and per-source state --------------------------------------------------


class RawStore:
    """What a source's requests returned, by key, with the headers that make
    the next request conditional. ``meta.json`` beside the files is the state
    of this source: fetched dates, ETags, revision ids and resolved ids.
    ``offline`` answers from the cache alone (``collect --offline``)."""

    def __init__(self, directory: Path, *, offline: bool = False) -> None:
        self.offline = offline
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._meta_path = directory / "meta.json"
        self.meta: dict[str, Any] = {}
        if self._meta_path.is_file():
            try:
                self.meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            except ValueError:
                self.meta = {}
        self.meta.setdefault("items", {})
        self.meta.setdefault("state", {})

    @property
    def state(self) -> dict[str, Any]:
        return self.meta["state"]

    def save(self) -> None:
        self._meta_path.write_text(json.dumps(self.meta, ensure_ascii=False, indent=1, sort_keys=True),
                                   encoding="utf-8")

    def path_for(self, key: str, suffix: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:120]
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        return self.directory / f"{safe}.{digest}{suffix}"

    def item(self, key: str) -> dict[str, Any] | None:
        return self.meta["items"].get(key)

    def read(self, key: str) -> bytes | None:
        item = self.item(key)
        if not item:
            return None
        path = Path(item["path"])
        if not path.is_file():
            # A cache that moved (lane KNOW's was written in its worktree):
            # the file is beside this meta.json under the same name.
            path = self.directory / path.name
        return path.read_bytes() if path.is_file() else None

    def fetch(
        self,
        http: Http,
        key: str,
        url: str,
        *,
        suffix: str,
        source: Source,
        full: bool = False,
        headers: Mapping[str, str] | None = None,
        robots: str = "check",
        refresh_days: int | None = None,
    ) -> tuple[bytes, bool]:
        """The body for ``key``: fetched when new or changed, else the cached copy.

        Returns (body, changed). A conditional request answered 304 keeps the
        copy; ``refresh_days`` keeps a copy that young without any request.
        """

        item = self.item(key)
        cached = self.read(key)
        if self.offline:
            if cached is None:
                raise FetchError(f"offline: {key} is not in the raw cache")
            return cached, False
        if cached is not None and item and not full:
            if refresh_days is not None:
                fetched = item.get("fetched_at", "")
                if fetched and _days_since(fetched) < refresh_days:
                    return cached, False
            conditional = dict(headers or {})
            if item.get("etag"):
                conditional["If-None-Match"] = item["etag"]
            if item.get("last_modified"):
                conditional["If-Modified-Since"] = item["last_modified"]
            if not conditional and refresh_days is None and item.get("sha256"):
                # No validator the server offered: fetch and compare bytes.
                pass
            reply = http.get(url, pace_key=source.id, per_second=source.requests_per_second,
                             headers=conditional, robots=robots)
            if reply.status == 304:
                item["checked_at"] = now_iso()
                return cached, False
        else:
            reply = http.get(url, pace_key=source.id, per_second=source.requests_per_second,
                             headers=headers, robots=robots)
        if reply.status != 200:
            raise FetchError(f"HTTP {reply.status} for {key}")
        digest = hashlib.sha256(reply.body).hexdigest()
        path = self.path_for(key, suffix)
        changed = cached is None or digest != (item or {}).get("sha256")
        if changed:
            path.write_bytes(reply.body)
        self.meta["items"][key] = {
            "url": url, "path": str(path), "sha256": digest, "size": len(reply.body),
            "etag": reply.header("ETag") or None, "last_modified": reply.header("Last-Modified") or None,
            "fetched_at": now_iso() if changed or not item else item.get("fetched_at"),
            "checked_at": now_iso(),
        }
        return reply.body, changed


def _days_since(iso: str) -> float:
    try:
        then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


# --- Pieces and chunks -----------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """One stretch of a source's own words before it is cut to size."""

    title: str
    locator: str
    text: str
    url: str
    language: str
    domain: str
    version: str
    license: str = ""
    fetched_at: str = ""


@dataclass
class RunReport:
    started_at: str = field(default_factory=now_iso)
    failures: list[dict[str, str]] = field(default_factory=list)
    per_source: dict[str, dict[str, Any]] = field(default_factory=dict)

    def fail(self, source_id: str, item: str, reason: str) -> None:
        self.failures.append({"source_id": source_id, "item": item, "reason": reason, "at": now_iso()})
        self.per_source.setdefault(source_id, {}).setdefault("failures", 0)
        self.per_source[source_id]["failures"] += 1


_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")
_WS = re.compile(r"[ \t ]+")


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\r", "")
    text = _WS.sub(" ", text)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def chunk_text(text: str, *, max_chars: int = CHUNK_MAX_CHARS, min_chars: int = CHUNK_MIN_CHARS) -> list[str]:
    """Cut a text into pieces of at most ``max_chars`` at paragraph and sentence ends.

    Paragraphs are joined up to the bound; a paragraph longer than it is cut
    at sentence ends, and a sentence longer than it at the bound. A piece
    shorter than ``min_chars`` is joined to its neighbour; a text that is
    shorter altogether is one piece.
    """

    cleaned = clean_text(text)
    if not cleaned:
        return []
    units: list[str] = []
    for paragraph in cleaned.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        current = ""
        for sentence in _SENTENCE_END.split(paragraph.replace("\n", " ")):
            while len(sentence) > max_chars:
                cut = sentence.rfind(" ", 0, max_chars)
                cut = cut if cut > max_chars // 2 else max_chars
                units.append((current + " " + sentence[:cut]).strip() if current else sentence[:cut].strip())
                current = ""
                sentence = sentence[cut:].strip()
            if len(current) + len(sentence) + 1 > max_chars and current:
                units.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            units.append(current)
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 1 > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = f"{current}\n{unit}".strip() if current else unit
    if current:
        chunks.append(current)
    if len(chunks) > 1:
        merged: list[str] = []
        for chunk in chunks:
            if merged and (len(chunk) < min_chars or len(merged[-1]) < min_chars) \
                    and len(merged[-1]) + len(chunk) + 1 <= max_chars * 1.2:
                merged[-1] = f"{merged[-1]}\n{chunk}"
            else:
                merged.append(chunk)
        chunks = merged
    return chunks


def chunk_id(source_id: str, locator: str, text: str) -> str:
    digest = hashlib.sha1(f"{source_id}\x1f{locator}\x1f{text}".encode("utf-8")).hexdigest()
    return f"{source_id}:{digest[:16]}"


def records_for(piece: Piece, source: Source, *, index: int | None = None) -> list[dict[str, Any]]:
    """The bundle records of one piece: its chunks with the source's facts attached."""

    records: list[dict[str, Any]] = []
    chunks = chunk_text(piece.text)
    for number, text in enumerate(chunks, 1):
        locator = piece.locator if len(chunks) == 1 else f"{piece.locator} · {number}/{len(chunks)}"
        records.append({
            "id": chunk_id(source.id, locator, text),
            "tier": "public",
            "title": piece.title,
            "text": text,
            "locator": locator,
            "url": piece.url,
            "license": piece.license or source.license,
            "language": piece.language or source.language,
            "domain": piece.domain or source.domain,
            "source_id": source.id,
            "source_name": source.name,
            "grade": source.grade,
            "version": piece.version,
            "fetched_at": piece.fetched_at or today(),
        })
    return records


def _dedupe_key(text: str) -> str:
    return hashlib.sha1(" ".join(text.casefold().split()).encode("utf-8")).hexdigest()


#: A date, or a year on its own (1900–2099, not part of a longer number).
_DATE_IN = re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)|(?<!\d)((?:19|20)\d{2})(?!\d)")


def _record_date(record: Mapping[str, Any]) -> str:
    """When a record's material is from: the source version's date, else the fetched date.

    A version such as "revision 1380139469" or "6th edition (CDC/NIH; file
    dated 2025-08)" gives its year or date; without one the fetched date
    stands in, so of two copies of the same words the later one wins.
    """

    version = str(record.get("version", ""))
    match = _DATE_IN.search(version)
    if match:
        if match.group(1):
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return f"{match.group(4)}-00-00"
    return str(record.get("fetched_at", ""))[:10]


# --- HTML to text ---------------------------------------------------------------------------

_BLOCK_TAGS = r"p|div|tr|li|h[1-6]|dt|dd|section|article|table|ul|ol|blockquote|pre|br"


def html_to_text(markup: str, *, keep_headings: bool = True) -> str:
    """A plain reading of HTML: scripts, styles, navigation and footers gone,
    block ends as line breaks, headings kept as ``== Heading ==`` lines."""

    text = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    text = re.sub(r"<(script|style|nav|footer|header|aside|noscript|svg|form)[^>]*>.*?</\1>", "",
                  text, flags=re.S | re.I)
    if keep_headings:
        text = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>",
                      lambda m: f"\n\n== {re.sub(r'<[^>]+>', ' ', m.group(2)).strip()} ==\n\n",
                      text, flags=re.S | re.I)
    text = re.sub(rf"</?(?:{_BLOCK_TAGS})\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return clean_text(text)


def sections_of(text: str) -> list[tuple[str, str]]:
    """(heading path, body) pairs of a text with ``== Heading ==`` lines (Wikipedia extracts, html_to_text)."""

    heading = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$")
    path: list[str] = []
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.split("\n"):
        match = heading.match(line.strip())
        if match:
            level = len(match.group(1)) - 2
            path = path[:level] + [match.group(2)]
            sections.append((" › ".join(path), []))
        else:
            sections[-1][1].append(line)
    return [(name, "\n".join(lines).strip()) for name, lines in sections if "\n".join(lines).strip()]


_SKIPPED_SECTIONS = {
    "references", "external links", "see also", "further reading", "notes", "bibliography",
    "gallery", "sources", "citations", "footnotes", "explanatory notes",
    # Not bench knowledge: how a thing came to be, who makes money from it,
    # where it appears in culture or law (lane KNOW v1).
    "history", "etymology", "culture", "society and culture", "in popular culture", "popular culture",
    "in fiction", "economy", "economics", "economic aspects", "politics", "legal status", "legislation",
    "regulation", "awards", "notable people", "timeline", "demographics", "trivia", "in the media",
    "national legislation and public organizations", "etymology and history", "discovery",
    "각주", "외부 링크", "같이 보기", "참고 문헌", "더 보기", "주석", "출처", "참고 자료", "관련 항목",
    "역사", "어원", "문화", "대중문화", "대중 문화", "경제", "법적 지위",
}


def _skipped_section(name: str) -> bool:
    first = name.split(" › ")[0].strip().casefold()
    return first in _SKIPPED_SECTIONS


# --- Handlers: one per source kind ---------------------------------------------------------


@dataclass
class RunContext:
    sources: SourceList
    http: Http
    runtime_dir: Path
    report: RunReport
    full: bool = False
    offline: bool = False
    previous_domains: dict[tuple[str, str], str] = field(default_factory=dict)
    glossary_terms: dict[str, set[str]] = field(default_factory=dict)
    glossary_aliases: dict[str, set[str]] = field(default_factory=dict)
    sitelinks: dict[str, dict[str, str]] = field(default_factory=dict)
    pdf_text: Callable[[bytes], list[str]] | None = None

    def raw(self, source: Source) -> RawStore:
        return RawStore(self.runtime_dir / "raw" / source.id, offline=self.offline)

    def data_file(self, name: str) -> Path:
        return self.sources.directory / name


def read_titles(path: Path) -> list[tuple[str, str]]:
    """(title, domain) pairs of a list file: ``# domain`` lines name the domain of what follows."""

    titles: list[tuple[str, str]] = []
    domain = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            word = stripped.lstrip("#").strip()
            if word and " " not in word and word.islower():
                domain = word
            continue
        titles.append((stripped, domain))
    return titles


# NIOSH Pocket Guide -------------------------------------------------------------------------

_NPG_INDEX_LINK = re.compile(r"""href=["'](?:/niosh/npg/)?(npgd(\d{4})\.html)["'][^>]*>(.*?)</a>""", re.S | re.I)
#: The fields of one NPG page, in the order the page prints them, grouped
#: into the two pieces a chemical becomes.
_NPG_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Identification and physical properties", (
        "Synonyms & Trade Names", "CAS No.", "RTECS No.", "DOT ID & Guide", "Formula", "Conversion",
        "IDLH", "Exposure Limits", "Physical Description", "Molecular Weight", "Boiling Point",
        "Freezing Point", "Melting Point", "Solubility", "Vapor Pressure", "Ionization Potential",
        "Specific Gravity", "Flash Point", "Upper Explosive Limit", "Lower Explosive Limit",
        "Minimum Explosive Concentration", "Incompatibilities & Reactivities",
    )),
    ("Exposure, symptoms, protection and first aid", (
        "Exposure Routes", "Symptoms", "Target Organs", "Cancer Site",
        "Personal Protection/Sanitation", "First Aid", "Respirator Recommendations",
    )),
)
_NPG_FIELDS = tuple(name for _group, names in _NPG_GROUPS for name in names)


def parse_npg_page(markup: str) -> tuple[str, dict[str, str]]:
    """The chemical's name and its fields from one ``npgdNNNN.html`` page."""

    text = html_to_text(markup, keep_headings=True)
    lines = [line for line in text.split("\n") if line.strip()]
    name = ""
    for line in lines:
        match = re.match(r"^==\s*(.+?)\s*==$", line)
        if match and match.group(1) not in ("Related Pages",) and not name:
            name = re.sub(r"\s*\|.*$", "", match.group(1)).strip()
            break
    fields: dict[str, str] = {}
    current: str | None = None
    for line in lines:
        bare = re.sub(r"^==\s*|\s*==$", "", line).strip()
        if bare in _NPG_FIELDS:
            current = bare
            fields.setdefault(current, "")
            continue
        if bare in ("Related Pages", "See also", "Minus", "Print") or bare.startswith("Page last reviewed"):
            current = None
            continue
        if current:
            if bare.startswith("See:") or bare.startswith("(See ") or bare.startswith("( See "):
                continue
            fields[current] = f"{fields[current]} {bare}".strip() if fields[current] else bare
    return name, {k: v for k, v in fields.items() if v}


def handle_niosh_npg(source: Source, ctx: RunContext) -> Iterator[Piece]:
    raw = ctx.raw(source)
    index_url = source.option("index_url")
    try:
        body, _changed = raw.fetch(ctx.http, "index", index_url, suffix=".html", source=source, full=ctx.full)
    except FetchError as exc:
        ctx.report.fail(source.id, "index", str(exc))
        raw.save()
        return
    entries: dict[str, str] = {}
    for file_name, _number, label in _NPG_INDEX_LINK.findall(body.decode("utf-8", errors="replace")):
        name = html_module.unescape(re.sub(r"<[^>]+>", "", label)).strip()
        entries.setdefault(file_name, name)
    base = source.option("base_url")
    fetched_at = today()
    for file_name in sorted(entries):
        url = f"{base}{file_name}"
        try:
            page, _changed = raw.fetch(ctx.http, file_name, url, suffix=".html", source=source, full=ctx.full)
        except FetchError as exc:
            ctx.report.fail(source.id, file_name, str(exc))
            continue
        name, fields = parse_npg_page(page.decode("utf-8", errors="replace"))
        name = name or entries[file_name]
        if not fields:
            ctx.report.fail(source.id, file_name, "no fields parsed")
            continue
        code = file_name.removesuffix(".html")
        item = raw.item(file_name) or {}
        for group, names in _NPG_GROUPS:
            lines = [f"{field_name}: {fields[field_name]}" for field_name in names if field_name in fields]
            if not lines:
                continue
            yield Piece(
                title=name, locator=f"{code} · {group}", text="\n".join(lines), url=url,
                language="en", domain=source.domain, version="NIOSH Pocket Guide (online edition)",
                fetched_at=(item.get("fetched_at") or fetched_at)[:10],
            )
    raw.save()


# PDF and HTML documents ---------------------------------------------------------------------


def pdf_pages_text(body: bytes) -> list[str]:
    """Each page's text, through pypdf (a repository dependency; PyMuPDF stays in pdf_text_engine)."""

    from pypdf import PdfReader  # noqa: PLC0415

    reader = PdfReader(io.BytesIO(body))
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one unreadable page is an empty page
            pages.append("")
    return pages


_REFERENCE_LINE = re.compile(r"^\s*(?:\[\d+\]|\d{1,3}\.)\s+\S")


def looks_like_reference_page(text: str) -> bool:
    lines = [line for line in text.split("\n") if line.strip()]
    if len(lines) < 8:
        return False
    numbered = sum(1 for line in lines if _REFERENCE_LINE.match(line))
    return numbered / len(lines) >= 0.6


def _join_pdf_lines(text: str) -> str:
    """PDF text comes one printed line per newline; join lines into paragraphs."""

    out: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            out.append("")
            continue
        if out and out[-1] and not out[-1].endswith((".", "!", "?", ":", ";")) and not line[:1].isupper() \
                and not re.match(r"^(?:[-•▪·]|\d+[.)]|[a-z][.)])\s", line):
            out[-1] = f"{out[-1]} {line}"
        elif out and out[-1] and out[-1].endswith("-") and line[:1].islower():
            out[-1] = out[-1][:-1] + line
        else:
            out.append(line)
    return "\n".join(out)


def pdf_pieces(source: Source, pages: Sequence[str], *, url: str, fetched_at: str) -> Iterator[Piece]:
    skip_before = int(source.option("skip_pages_before", 1) or 1)
    for number, page_text in enumerate(pages, 1):
        if number < skip_before:
            continue
        text = _join_pdf_lines(page_text)
        if len(text) < 200 or looks_like_reference_page(text):
            continue
        yield Piece(
            title=source.name, locator=f"p. {number}", text=text, url=source.option("page_url") or url,
            language=source.language, domain=source.domain, version=str(source.option("version", "")),
            fetched_at=fetched_at,
        )


def handle_pdf(source: Source, ctx: RunContext) -> Iterator[Piece]:
    raw = ctx.raw(source)
    url = source.option("url")
    try:
        body, changed = raw.fetch(ctx.http, "document", url, suffix=".pdf", source=source, full=ctx.full)
    except FetchError as exc:
        ctx.report.fail(source.id, "document", str(exc))
        raw.save()
        return
    pages_path = raw.path_for("document", ".pages.json")
    if changed or not pages_path.is_file():
        pages = (ctx.pdf_text or pdf_pages_text)(body)
        pages_path.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    else:
        pages = json.loads(pages_path.read_text(encoding="utf-8"))
    raw.save()
    fetched_at = ((raw.item("document") or {}).get("fetched_at") or today())[:10]
    yield from pdf_pieces(source, pages, url=url, fetched_at=fetched_at)


def handle_html(source: Source, ctx: RunContext) -> Iterator[Piece]:
    raw = ctx.raw(source)
    url = source.option("url")
    try:
        body, _changed = raw.fetch(ctx.http, "page", url, suffix=".html", source=source, full=ctx.full)
    except FetchError as exc:
        ctx.report.fail(source.id, "page", str(exc))
        raw.save()
        return
    raw.save()
    fetched_at = ((raw.item("page") or {}).get("fetched_at") or today())[:10]
    text = html_to_text(body.decode("utf-8", errors="replace"))
    for name, section in sections_of(text):
        if _skipped_section(name) or len(section) < 120:
            continue
        yield Piece(
            title=source.name, locator=name or "Introduction", text=section, url=url,
            language=source.language, domain=source.domain, version=str(source.option("version", "")),
            fetched_at=fetched_at,
        )


# PubChem --------------------------------------------------------------------------------------

#: The licence each allowed reference carries (PubChem's source table, read 2026-10-24).
PUBCHEM_REFERENCE_LICENSES: Mapping[str, str] = {
    "PubChem": "Public domain (NLM)",
    "Hazardous Substances Data Bank (HSDB)": "Public domain (NLM)",
    "Medical Subject Headings (MeSH)": "Public domain (NLM)",
    "The National Institute for Occupational Safety and Health (NIOSH)": "Public domain (CDC/NIOSH)",
    "NIOSH Manual of Analytical Methods": "Public domain (CDC/NIOSH)",
    "Occupational Safety and Health Administration (OSHA)": "Public domain (DOL/OSHA)",
    "ILO-WHO International Chemical Safety Cards (ICSCs)": "CC BY 4.0 (ILO)",
    "DTP/NCI": "Public domain (NCI)",
    "NCI Thesaurus (NCIt)": "Public domain (NCI)",
    "Drugs@FDA": "Public domain (FDA)",
    "FDA Global Substance Registration System (GSRS)": "Public domain (FDA)",
    "EPA Chemicals under the TSCA": "Public domain (EPA)",
    "EPA DSSTox": "Public domain (EPA)",
    "EPA Hazardous Air Pollutants": "Public domain (EPA)",
    "EPA Acute Exposure Guideline Levels (AEGLs)": "Public domain (EPA)",
    "NTP Technical Reports": "Public domain (NTP)",
}
_PUBCHEM_SHORT = {
    "Hazardous Substances Data Bank (HSDB)": "HSDB",
    "The National Institute for Occupational Safety and Health (NIOSH)": "NIOSH",
    "NIOSH Manual of Analytical Methods": "NIOSH NMAM",
    "Occupational Safety and Health Administration (OSHA)": "OSHA",
    "ILO-WHO International Chemical Safety Cards (ICSCs)": "ILO ICSC",
    "Medical Subject Headings (MeSH)": "MeSH",
    "NCI Thesaurus (NCIt)": "NCIt",
    "FDA Global Substance Registration System (GSRS)": "FDA GSRS",
    "EPA Acute Exposure Guideline Levels (AEGLs)": "EPA AEGL",
}
#: Which top-level PubChem sections become one piece each.
_PUBCHEM_PIECE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Identity and description", ("Record Description", "Molecular Formula", "Molecular Weight", "IUPAC Name")),
    ("Physical properties", ("Physical Description", "Color/Form", "Odor", "Boiling Point", "Melting Point",
                             "Flash Point", "Solubility", "Density", "Vapor Pressure",
                             "Autoignition Temperature", "Decomposition", "Corrosivity", "pH",
                             "Stability/Shelf Life")),
    ("Hazards", ("Hazards Summary", "Fire Hazards", "Explosive Limits and Potential", "Fire Potential",
                 "Reactivity Profile", "Health Hazards", "Exposure Routes", "Symptoms", "Target Organs",
                 "Incompatibilities", "Incompatible Materials")),
    ("First aid and fire fighting", ("Fire Fighting", "First Aid", "Inhalation First Aid", "Skin First Aid",
                                     "Eye First Aid", "Ingestion First Aid")),
    ("Protection, storage and disposal", ("Personal Protective Equipment (PPE)",
                                          "Protective Equipment and Clothing", "Storage Conditions",
                                          "Safe Storage", "Spillage Disposal", "Cleanup Methods",
                                          "Disposal Methods", "Preventive Measures")),
    ("Exposure limits", ("Exposure Limits", "NIOSH Recommendations",
                         "Immediately Dangerous to Life or Health (IDLH)",
                         "Permissible Exposure Limit (PEL)", "Recommended Exposure Limit (REL)")),
)


def _pubchem_value(value: Mapping[str, Any]) -> str:
    if "StringWithMarkup" in value:
        return " / ".join(str(item.get("String", "")).strip() for item in value["StringWithMarkup"]).strip(" /")
    if "Number" in value:
        unit = value.get("Unit", "")
        return " / ".join(f"{number} {unit}".strip() for number in value["Number"])
    return ""


def pubchem_statements(
    record: Mapping[str, Any], *, headings: Iterable[str], allowed_references: Iterable[str],
) -> dict[str, list[tuple[str, str]]]:
    """heading → [(value, reference source)] for the headings kept and the references allowed."""

    wanted = set(headings)
    allowed = set(allowed_references)
    references = {ref.get("ReferenceNumber"): ref.get("SourceName", "") for ref in record.get("Reference", [])}
    found: dict[str, list[tuple[str, str]]] = {}

    def walk(sections: Iterable[Mapping[str, Any]]) -> None:
        for section in sections:
            heading = str(section.get("TOCHeading", ""))
            if heading in wanted:
                for info in section.get("Information", []):
                    number = info.get("ReferenceNumber")
                    source_name = references.get(number, "PubChem" if number is None else "")
                    if source_name not in allowed:
                        continue
                    value = _pubchem_value(info.get("Value", {}))
                    if not value:
                        continue
                    if (value, source_name) not in found.setdefault(heading, []):
                        found[heading].append((value, source_name))
            walk(section.get("Section", []))

    walk(record.get("Section", []))
    return found


def pubchem_pieces(
    record: Mapping[str, Any], source: Source, *, cid: int, fetched_at: str,
) -> Iterator[Piece]:
    statements = pubchem_statements(
        record, headings=source.option("headings", []), allowed_references=source.option("allowed_references", []))
    title = str(record.get("RecordTitle", "")).strip() or f"CID {cid}"
    url = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
    for group, names in _PUBCHEM_PIECE_GROUPS:
        lines: list[str] = []
        used: list[str] = []
        for name in names:
            for value, reference in statements.get(name, [])[:4]:
                short = _PUBCHEM_SHORT.get(reference, reference)
                lines.append(f"{name}: {value} [{short}]")
                if reference not in used:
                    used.append(reference)
        if not lines:
            continue
        licence = "; ".join(sorted({PUBCHEM_REFERENCE_LICENSES.get(ref, "") for ref in used} - {""}))
        yield Piece(
            title=title, locator=f"CID {cid} · {group}", text="\n".join(lines), url=url,
            language="en", domain=source.domain, version=f"PubChem record read {fetched_at}",
            license=licence or source.license, fetched_at=fetched_at,
        )


def read_compounds(path: Path) -> list[tuple[str, int | None, str]]:
    """(name, explicit CID or None, English Wikipedia title) per line of the compounds file.

    A line is ``name``, ``name<TAB>CID`` or ``name<TAB>enwiki:Title``; the
    title defaults to the name itself (MediaWiki capitalises its first letter).
    """

    compounds: list[tuple[str, int | None, str]] = []
    for line, _domain in read_titles(path):
        parts = [part.strip() for part in line.split("\t") if part.strip()]
        name = parts[0]
        cid: int | None = None
        title = name
        for extra in parts[1:]:
            if extra.isdigit():
                cid = int(extra)
            elif extra.lower().startswith("enwiki:"):
                title = extra.split(":", 1)[1].strip()
        compounds.append((name, cid, title))
    return compounds


WIKIDATA_API = "https://www.wikidata.org/w/api.php"
PUBCHEM_CID_PROPERTY = "P662"
#: Wikidata counts its query service's lag in ``maxlag``, and that sat above
#: 5 s for hours on 2026-10-24; a few dozen reads a run are no load, so the
#: courtesy bound is 8 s here (Wikipedia's reads keep 5).
WIKIDATA_MAXLAG = 8


def resolve_cids_from_wikidata(
    source: Source, ctx: RunContext, titles: Sequence[str],
) -> dict[str, int]:
    """English Wikipedia title → PubChem CID, from Wikidata's PubChem CID claim (P662, CC0).

    PubChem's robots.txt disallows the PUG REST name lookup (``/rest/pug/``)
    for every agent, so names are never sent to PubChem; the CID comes from
    Wikidata and only PUG View (``/rest/pug_view/``, allowed) is read.
    """

    found: dict[str, int] = {}
    # wbgetentities matches a sitelink title exactly (no normalisation for
    # several titles), and Wikipedia titles start with a capital.
    canonical = list(dict.fromkeys(title[:1].upper() + title[1:] for title in titles))
    for batch in _batches(canonical, 50):
        url = _api(WIKIDATA_API, {
            "action": "wbgetentities", "sites": "enwiki", "titles": "|".join(batch), "props": "claims|sitelinks",
            "sitefilter": "enwiki", "format": "json", "maxlag": WIKIDATA_MAXLAG,
        })
        try:
            reply = ctx.http.get(url, pace_key="wikidata", per_second=1.0, robots="api")
            entities = reply.json().get("entities", {})
        except (FetchError, ValueError) as exc:
            for title in batch:
                ctx.report.fail(source.id, title, f"wikidata: {exc}")
            continue
        for qid, entity in entities.items():
            if qid.startswith("-") or "missing" in entity:
                continue
            title = entity.get("sitelinks", {}).get("enwiki", {}).get("title", "")
            claims = entity.get("claims", {}).get(PUBCHEM_CID_PROPERTY, [])
            ranked = sorted(claims, key=lambda c: 0 if c.get("rank") == "preferred" else 1)
            for claim in ranked:
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                if isinstance(value, str) and value.isdigit():
                    found[title] = int(value)
                    break
    return found


def handle_pubchem(source: Source, ctx: RunContext) -> Iterator[Piece]:
    raw = ctx.raw(source)
    base = source.option("base_url")
    compounds = read_compounds(ctx.data_file(source.option("compounds_file")))
    names = [name for name, _cid, _title in compounds]
    cids: dict[str, int | None] = raw.state.setdefault("cids", {})
    for name, explicit, _title in compounds:
        if explicit:
            cids[name] = explicit
    unresolved = [(name, title) for name, explicit, title in compounds if not explicit and not cids.get(name)]
    if unresolved and ctx.offline:
        for name, _title in unresolved:
            ctx.report.fail(source.id, name, "offline: no CID in the raw cache")
    elif unresolved:
        by_title = resolve_cids_from_wikidata(source, ctx, [title for _name, title in unresolved])
        lowered = {key.casefold(): value for key, value in by_title.items()}
        for name, title in unresolved:
            cid = by_title.get(title) or by_title.get(title[:1].upper() + title[1:]) or lowered.get(title.casefold())
            if cid:
                cids[name] = cid
            else:
                cids[name] = None
                ctx.report.fail(source.id, name, "no PubChem CID on Wikidata (P662); add the CID to the list")
    raw.save()
    seen: set[int] = set()
    for name in names:
        cid = cids.get(name)
        if not cid or cid in seen:
            continue
        seen.add(cid)
        key = f"cid-{cid}"
        try:
            body, _changed = raw.fetch(
                ctx.http, key, f"{base}pug_view/data/compound/{cid}/JSON", suffix=".json", source=source,
                full=ctx.full, refresh_days=REFRESH_DAYS,
            )
        except FetchError as exc:
            ctx.report.fail(source.id, name, str(exc))
            continue
        try:
            record = json.loads(body)["Record"]
        except (ValueError, KeyError):
            ctx.report.fail(source.id, name, "record is not the PUG View shape")
            continue
        fetched_at = ((raw.item(key) or {}).get("fetched_at") or today())[:10]
        yield from pubchem_pieces(record, source, cid=cid, fetched_at=fetched_at)
    raw.save()


# Wikipedia and Wikidata -----------------------------------------------------------------------


def _api(url: str, params: Mapping[str, Any]) -> str:
    return f"{url}?{urlencode({k: v for k, v in params.items() if v is not None}, doseq=False)}"


def _batches(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def wikipedia_info(source: Source, ctx: RunContext, titles: Sequence[str]) -> dict[str, dict[str, Any]]:
    """title → {pageid, lastrevid, fullurl, title (canonical)} for 50 titles per request; a
    missing page is a failure."""

    api_url = source.option("api_url")
    found: dict[str, dict[str, Any]] = {}
    for batch in _batches(list(titles), 50):
        url = _api(api_url, {
            "action": "query", "prop": "info", "inprop": "url", "redirects": 1, "format": "json",
            "formatversion": 2, "maxlag": 5, "titles": "|".join(batch),
        })
        try:
            reply = ctx.http.get(url, pace_key=source.id, per_second=source.requests_per_second, robots="api")
            payload = reply.json()
        except (FetchError, ValueError) as exc:
            for title in batch:
                ctx.report.fail(source.id, title, f"info: {exc}")
            continue
        query = payload.get("query", {})
        redirected = {item["from"]: item["to"] for item in query.get("redirects", [])}
        normalized = {item["from"]: item["to"] for item in query.get("normalized", [])}
        pages = {page["title"]: page for page in query.get("pages", [])}
        for title in batch:
            canonical = redirected.get(normalized.get(title, title), normalized.get(title, title))
            page = pages.get(canonical)
            if not page or page.get("missing"):
                ctx.report.fail(source.id, title, "no such article")
                continue
            found[title] = {"title": canonical, "pageid": page.get("pageid"),
                            "lastrevid": page.get("lastrevid"), "fullurl": page.get("fullurl", "")}
    return found


def _cached_wikipedia_info(raw: RawStore, titles: Sequence[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """``wikipedia_info`` from the raw cache (``--offline``): each cached extract
    under the title asked for, and the extracts of redirected titles under their
    own (canonical) title, which the run then reads too."""

    items = raw.meta["items"]
    revisions = raw.state.get("revisions", {})
    found: dict[str, dict[str, Any]] = {}
    for key, item in items.items():
        if not key.startswith("extract:"):
            continue
        title = key.split(":", 1)[1]
        found[title] = {"title": title, "pageid": None, "lastrevid": revisions.get(title, item.get("revid")),
                        "fullurl": item.get("url", "")}
    extra = [title for title in found if title not in set(titles)]
    return found, extra


def wikipedia_pieces(
    extract: str, *, title: str, url: str, revid: Any, language: str, domain: str, fetched_at: str,
) -> Iterator[Piece]:
    for name, section in sections_of(extract):
        if _skipped_section(name) or len(section) < 60:
            continue
        yield Piece(
            title=title, locator=name or ("개요" if language == "ko" else "Introduction"), text=section,
            url=url, language=language, domain=domain, version=f"revision {revid}", fetched_at=fetched_at,
        )


def handle_wikipedia(source: Source, ctx: RunContext) -> Iterator[Piece]:
    raw = ctx.raw(source)
    api_url = source.option("api_url")
    titles = read_titles(ctx.data_file(source.option("titles_file")))
    domains = {title: domain for title, domain in titles}
    wanted: list[str] = [title for title, _domain in titles]
    linked_from = source.option("sitelinks_from")
    if linked_from:
        for en_title, ko_title in ctx.sitelinks.get(linked_from, {}).items():
            if ko_title not in domains:
                domains[ko_title] = ctx.sitelinks.get(f"{linked_from}:domains", {}).get(en_title, "")
                wanted.append(ko_title)
    wanted = list(dict.fromkeys(wanted))
    if ctx.offline:
        info, extra = _cached_wikipedia_info(raw, wanted)
        wanted += extra
        # A redirected title's domain is the one its article had in the last bundle.
        for title in extra:
            domains.setdefault(title, ctx.previous_domains.get((source.id, title), ""))
    else:
        info = wikipedia_info(source, ctx, wanted)
    revisions = raw.state.setdefault("revisions", {})
    for title in wanted:
        page = info.get(title)
        if not page:
            continue
        canonical = page["title"]
        key = f"extract:{canonical}"
        unchanged = (not ctx.full and revisions.get(canonical) == page["lastrevid"] and raw.read(key) is not None)
        if not unchanged:
            url = _api(api_url, {
                "action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki",
                "format": "json", "formatversion": 2, "maxlag": 5, "titles": canonical, "redirects": 1,
            })
            try:
                reply = ctx.http.get(url, pace_key=source.id, per_second=source.requests_per_second, robots="api")
                pages = reply.json().get("query", {}).get("pages", [])
                extract = str(pages[0].get("extract", "")) if pages else ""
            except (FetchError, ValueError, IndexError) as exc:
                ctx.report.fail(source.id, canonical, f"extract: {exc}")
                continue
            if not extract.strip():
                ctx.report.fail(source.id, canonical, "empty extract")
                continue
            path = raw.path_for(key, ".txt")
            path.write_text(extract, encoding="utf-8")
            raw.meta["items"][key] = {"url": page["fullurl"], "path": str(path), "sha256": hashlib.sha256(
                extract.encode("utf-8")).hexdigest(), "size": len(extract), "fetched_at": now_iso(),
                "checked_at": now_iso(), "revid": page["lastrevid"]}
            revisions[canonical] = page["lastrevid"]
        if source.language == "ko":
            _collect_korean_redirects(source, ctx, raw, canonical)
        extract = (raw.read(key) or b"").decode("utf-8")
        fetched_at = ((raw.item(key) or {}).get("fetched_at") or today())[:10]
        yield from wikipedia_pieces(
            extract, title=canonical, url=page["fullurl"], revid=page["lastrevid"], language=source.language,
            domain=domains.get(title, "") or source.domain, fetched_at=fetched_at,
        )
    raw.save()


def _collect_korean_redirects(source: Source, ctx: RunContext, raw: RawStore, title: str) -> None:
    """The other spellings a Korean article is reached by ("아세토니트릴" → 아세토나이트릴) join the glossary."""

    aliases = raw.state.setdefault("redirect_aliases", {})
    if (title not in aliases or ctx.full) and not ctx.offline:
        url = _api(source.option("api_url"), {
            "action": "query", "prop": "redirects", "rdlimit": 50, "format": "json", "formatversion": 2,
            "maxlag": 5, "titles": title,
        })
        try:
            reply = ctx.http.get(url, pace_key=source.id, per_second=source.requests_per_second, robots="api")
            pages = reply.json().get("query", {}).get("pages", [])
            aliases[title] = [item["title"] for item in (pages[0].get("redirects", []) if pages else [])]
        except (FetchError, ValueError) as exc:
            ctx.report.fail(source.id, title, f"redirects: {exc}")
            aliases[title] = []
    for alias in aliases.get(title, []):
        if re.search(r"[가-힣]", alias) and ":" not in alias:
            ctx.glossary_terms.setdefault(alias, set()).update(ctx.glossary_terms.get(title, set()))


def handle_wikidata_glossary(source: Source, ctx: RunContext) -> Iterator[Piece]:
    raw = ctx.raw(source)
    api_url = source.option("api_url")
    titles: list[tuple[str, str]] = []
    for other_id in source.option("from_sources", []) or []:
        other = ctx.sources.by_id(other_id)
        if other.option("titles_file"):
            titles += read_titles(ctx.data_file(other.option("titles_file")))
    extra = source.option("extra_terms_file")
    if extra:
        titles += read_titles(ctx.data_file(extra))
    domains = {title: domain for title, domain in titles}
    unique = list(dict.fromkeys(title for title, _domain in titles))
    sitelinks: dict[str, str] = {}
    for batch in _batches(unique, 50):
        key = "batch-" + hashlib.sha1("|".join(batch).encode("utf-8")).hexdigest()[:12]
        url = _api(api_url, {
            "action": "wbgetentities", "sites": "enwiki", "titles": "|".join(batch), "props": "labels|aliases|sitelinks",
            "languages": "ko|en", "sitefilter": "enwiki|kowiki", "format": "json", "maxlag": WIKIDATA_MAXLAG,
        })
        try:
            body, _changed = raw.fetch(ctx.http, key, url, suffix=".json", source=source, full=ctx.full,
                                       robots="api", refresh_days=REFRESH_DAYS)
            entities = json.loads(body).get("entities", {})
        except (FetchError, ValueError) as exc:
            for title in batch:
                ctx.report.fail(source.id, title, f"wikidata: {exc}")
            continue
        for qid, entity in entities.items():
            if qid.startswith("-") or "missing" in entity:
                continue
            labels = entity.get("labels", {})
            aliases = entity.get("aliases", {})
            links = entity.get("sitelinks", {})
            en_title = links.get("enwiki", {}).get("title", "")
            ko_title = links.get("kowiki", {}).get("title", "")
            english = [labels.get("en", {}).get("value", "")] + [a.get("value", "") for a in aliases.get("en", [])]
            english = [item for item in dict.fromkeys(english) if item]
            if en_title and en_title not in english:
                english.insert(0, en_title)
            korean = [labels.get("ko", {}).get("value", "")] + [a.get("value", "") for a in aliases.get("ko", [])]
            if ko_title:
                korean.insert(0, ko_title)
            korean = [item for item in dict.fromkeys(korean) if item and re.search(r"[가-힣]", item)]
            for word in korean:
                ctx.glossary_terms.setdefault(word, set()).update(english[:4])
            if en_title and korean:
                ctx.glossary_aliases.setdefault(en_title, set()).update(korean[:3])
            if en_title and ko_title:
                sitelinks[en_title] = ko_title
    for other_id in source.option("from_sources", []) or []:
        ctx.sitelinks[other_id] = dict(sitelinks)
        ctx.sitelinks[f"{other_id}:domains"] = dict(domains)
    raw.state["sitelinks"] = sitelinks
    raw.save()
    return iter(())


# Europe PMC -------------------------------------------------------------------------------------

_EPMC_ALLOWED = re.compile(r"^(?:cc by|cc by-sa|cc0|cc by 4\.0|cc by 3\.0|cc by 2\.0|cc by-sa 4\.0)$", re.I)
_LICENSE_HREF_ALLOWED = re.compile(r"creativecommons\.org/(?:licenses/by(?:-sa)?/|publicdomain/zero/)", re.I)
_LICENSE_HREF_REFUSED = re.compile(r"/by-nc|/by-nd|/by-nc-nd|/by-nc-sa", re.I)
_JATS_SKIPPED = {"references", "reference", "acknowledgements", "acknowledgments", "funding",
                 "author contributions", "conflict of interest", "conflicts of interest", "competing interests",
                 "supplementary material", "supplementary materials", "abbreviations", "data availability",
                 "data availability statement", "ethics", "notes"}


def _epmc_license_ok(license_text: str) -> bool:
    return bool(_EPMC_ALLOWED.match((license_text or "").strip()))


def europepmc_query(phrase: str) -> str:
    """The search for one phrase: in the title or abstract, open access, full text, CC BY.

    A phrase (``"in-gel digestion"``) must appear in the title or the abstract
    so that a long review merely mentioning it is not chosen; several phrases
    joined by `` OR `` are each tried that way.
    """

    parts = [part.strip().strip('"') for part in phrase.split(" OR ") if part.strip()]
    fields = " OR ".join(f'TITLE:"{part}" OR ABSTRACT:"{part}"' for part in parts)
    return f'({fields}) AND LICENSE:"cc by" AND OPEN_ACCESS:y AND HAS_FT:y'


#: Sections a bench question is answered from, read first; the rest follow.
_PREFERRED_JATS = ("abstract", "method", "material", "protocol", "procedure", "troubleshoot", "result",
                   "limitation", "safety", "note", "tip")


def _section_order(name: str) -> int:
    lowered = name.casefold()
    for rank, word in enumerate(_PREFERRED_JATS):
        if word in lowered:
            return rank
    return len(_PREFERRED_JATS)


def jats_sections(xml_text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """(article facts, [(section title, text)]) of a JATS article; the licence href is a fact."""

    root = ElementTree.fromstring(xml_text)
    facts: dict[str, str] = {}
    title = root.find(".//article-meta/title-group/article-title")
    facts["title"] = " ".join("".join(title.itertext()).split()) if title is not None else ""
    journal = root.find(".//journal-meta/journal-title-group/journal-title")
    facts["journal"] = "".join(journal.itertext()).strip() if journal is not None else ""
    year = root.find(".//article-meta/pub-date/year")
    facts["year"] = (year.text or "").strip() if year is not None else ""
    pmcid = root.find(".//article-meta/article-id[@pub-id-type='pmcid']")
    facts["pmcid"] = (pmcid.text or "").strip() if pmcid is not None else ""
    licence = root.find(".//article-meta/permissions/license")
    href = ""
    if licence is not None:
        for key, value in licence.attrib.items():
            if key.endswith("href"):
                href = value
        if not href:
            ref = licence.find(".//{http://www.niso.org/schemas/ali/1.0/}license_ref")
            href = (ref.text or "").strip() if ref is not None else ""
    facts["license_href"] = href
    sections: list[tuple[str, str]] = []
    abstract = root.find(".//article-meta/abstract")
    if abstract is not None:
        text = clean_text(" ".join(abstract.itertext()))
        if text:
            sections.append(("Abstract", text))
    body = root.find("body")
    if body is not None:
        for sec in body.iter("sec"):
            heading = sec.find("title")
            name = "".join(heading.itertext()).strip() if heading is not None else ""
            paragraphs = []
            for child in sec:
                if child.tag in ("p", "list", "disp-quote", "boxed-text"):
                    paragraphs.append(clean_text(" ".join(child.itertext())))
            text = "\n\n".join(p for p in paragraphs if p)
            if text and name.casefold() not in _JATS_SKIPPED:
                sections.append((name or "Section", text))
    return facts, sections


def _license_label(href: str, field_value: str) -> str:
    text = href or field_value
    if "zero" in text:
        return "CC0 1.0"
    match = re.search(r"/by(-sa)?/(\d\.\d)", text)
    if match:
        return f"CC BY{'-SA' if match.group(1) else ''} {match.group(2)}"
    return field_value.upper() if field_value else "CC BY"


def handle_europepmc(source: Source, ctx: RunContext) -> Iterator[Piece]:
    raw = ctx.raw(source)
    base = source.option("base_url")
    per_query = int(source.option("max_per_query", 3))
    selected: dict[str, list[str]] = raw.state.setdefault("selected", {})
    facts_by_id: dict[str, dict[str, Any]] = raw.state.setdefault("articles", {})
    chosen: list[str] = [str(pmcid) for pmcid in source.option("pmcids", []) or []]
    for query in source.option("queries", []) or []:
        kept = [pmcid for pmcid in selected.get(query, [])]
        if (len(kept) < per_query or ctx.full) and not ctx.offline:
            url = f"{base}search?{urlencode({'query': europepmc_query(query), 'format': 'json', 'resultType': 'core', 'pageSize': per_query * 4})}"
            try:
                reply = ctx.http.get(url, pace_key=source.id, per_second=source.requests_per_second)
                results = reply.json().get("resultList", {}).get("result", [])
            except (FetchError, ValueError) as exc:
                ctx.report.fail(source.id, query, f"search: {exc}")
                results = []
            for result in results:
                pmcid = str(result.get("pmcid", "")).strip()
                if not pmcid or pmcid in kept or not _epmc_license_ok(str(result.get("license", ""))):
                    continue
                kept.append(pmcid)
                facts_by_id.setdefault(pmcid, {})["search_license"] = str(result.get("license", ""))
                facts_by_id[pmcid]["query"] = query
                if len(kept) >= per_query:
                    break
            selected[query] = kept
        chosen += kept
    raw.save()
    for pmcid in dict.fromkeys(chosen):
        key = f"fulltext:{pmcid}"
        try:
            body, _changed = raw.fetch(ctx.http, key, f"{base}{pmcid}/fullTextXML", suffix=".xml", source=source,
                                       full=ctx.full, refresh_days=REFRESH_DAYS * 6)
            facts, sections = jats_sections(body.decode("utf-8", errors="replace"))
        except (FetchError, ElementTree.ParseError) as exc:
            ctx.report.fail(source.id, pmcid, f"full text: {exc}")
            continue
        href = facts.get("license_href", "")
        field_value = facts_by_id.get(pmcid, {}).get("search_license", "")
        if _LICENSE_HREF_REFUSED.search(href) or not (_LICENSE_HREF_ALLOWED.search(href) or _epmc_license_ok(field_value)):
            ctx.report.fail(source.id, pmcid, f"licence not CC BY/CC0 ({href or field_value or 'none'})")
            continue
        licence = _license_label(href, field_value)
        fetched_at = ((raw.item(key) or {}).get("fetched_at") or today())[:10]
        url = f"https://europepmc.org/article/PMC/{pmcid}"
        where = " ".join(part for part in (facts.get("journal", ""), facts.get("year", "")) if part)
        for name, text in sorted(sections, key=lambda item: _section_order(item[0])):
            if len(text) < 120:
                continue
            yield Piece(
                title=facts.get("title", "") or pmcid, locator=f"{where} · {name}" if where else name, text=text,
                url=url, language="en", domain=source.domain, version=f"{pmcid} ({where})" if where else pmcid,
                license=licence, fetched_at=fetched_at,
            )
    raw.save()


HANDLERS: Mapping[str, Callable[[Source, RunContext], Iterator[Piece]]] = {
    "niosh_npg": handle_niosh_npg,
    "pdf": handle_pdf,
    "html": handle_html,
    "pubchem": handle_pubchem,
    "wikipedia": handle_wikipedia,
    "wikidata_glossary": handle_wikidata_glossary,
    "europepmc": handle_europepmc,
}


def ordered_sources(sources: Iterable[Source], only: set[str] | None) -> list[Source]:
    """Dependencies first: the glossary before the Korean Wikipedia that reads its sitelinks."""

    chosen = [source for source in sources if not only or source.id in only]
    glossaries = [source for source in chosen if source.kind == "wikidata_glossary"]
    needed = {source.option("sitelinks_from") for source in chosen} | {
        other for source in chosen for other in (source.option("from_sources", []) or [])}
    if only:
        for source in sources:
            if source.kind == "wikidata_glossary" and source not in glossaries and any(
                    s.option("sitelinks_from") for s in chosen):
                glossaries.append(source)
    others = [source for source in chosen if source.kind != "wikidata_glossary"]
    del needed
    return glossaries + others


# --- collect ------------------------------------------------------------------------------------


def collect(
    sources: SourceList,
    *,
    runtime_dir: Path,
    http: Http,
    only: set[str] | None = None,
    full: bool = False,
    offline: bool = False,
    pdf_text: Callable[[bytes], list[str]] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Fetch what changed, rebuild the bundle, write the manifest; returns the manifest.

    ``offline`` sends no request: every source is read from the raw cache
    (what is not cached is a failure), so a parser change is measured on the
    same material (lane KNOW2)."""

    bundle_dir = runtime_dir / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    report = RunReport()
    ctx = RunContext(sources=sources, http=http, runtime_dir=runtime_dir, report=report, full=full,
                     offline=offline, pdf_text=pdf_text)
    previous_chunks = bundle_dir / "chunks.jsonl"
    if offline and previous_chunks.is_file():
        for line in previous_chunks.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                ctx.previous_domains.setdefault((str(record.get("source_id")), str(record.get("title"))),
                                                str(record.get("domain", "")))
    previous_glossary = bundle_dir / "glossary.json"
    if previous_glossary.is_file():
        try:
            saved = json.loads(previous_glossary.read_text(encoding="utf-8"))
            for word, english in saved.get("terms", {}).items():
                ctx.glossary_terms.setdefault(word, set()).update(english)
            for title, korean in saved.get("aliases", {}).items():
                ctx.glossary_aliases.setdefault(title, set()).update(korean)
            for source_id, links in saved.get("sitelinks", {}).items():
                ctx.sitelinks[source_id] = dict(links)
        except ValueError:
            pass
    records: list[dict[str, Any]] = []
    seen_text: dict[str, dict[str, Any]] = {}
    dropped: Counter[str] = Counter()
    capped: Counter[str] = Counter()
    for source in ordered_sources(sources.sources, only):
        started = time.perf_counter()
        count_pieces = 0
        count_chunks = 0
        chars = 0
        requests_before = http.requests_made
        log(f"[{source.id}] start")
        try:
            cap = source.option("max_chunks_per_document")
            per_document: Counter[str] = Counter()
            for piece in HANDLERS[source.kind](source, ctx):
                count_pieces += 1
                for record in records_for(piece, source):
                    if cap and per_document[piece.title] >= int(cap):
                        capped[source.id] += 1
                        continue
                    key = _dedupe_key(record["text"])
                    if key in seen_text:
                        # The same words twice: the newer copy stays (human
                        # decision 2026-10-24: of the same material, the
                        # latest is enough), the older is dropped.
                        held = seen_text[key]
                        if _record_date(record) > _record_date(held):
                            records.remove(held)
                            seen_text[key] = record
                            records.append(record)
                            dropped[held["source_id"]] += 1
                        else:
                            dropped[source.id] += 1
                        continue
                    seen_text[key] = record
                    records.append(record)
                    per_document[piece.title] += 1
                    count_chunks += 1
                    chars += len(record["text"])
        except Exception as exc:  # noqa: BLE001 - one source's failure never stops the others
            report.fail(source.id, "source", f"{type(exc).__name__}: {exc}")
        report.per_source.setdefault(source.id, {}).update({
            "name": source.name, "kind": source.kind, "grade": source.grade, "license": source.license,
            "pieces": count_pieces, "chunks": count_chunks, "chars": chars,
            "duplicates_dropped": dropped[source.id], "capped": capped[source.id],
            "requests": http.requests_made - requests_before,
            "seconds": round(time.perf_counter() - started, 1),
        })
        report.per_source[source.id].setdefault("failures", 0)
        log(f"[{source.id}] pieces={count_pieces} chunks={count_chunks} chars={chars} "
            f"failures={report.per_source[source.id]['failures']} requests={http.requests_made - requests_before}")
    # A later source's newer copy of the same words takes an earlier source's
    # piece out again, so the counts are read from what is left at the end.
    for source_id, row in report.per_source.items():
        own = [record for record in records if record.get("source_id") == source_id]
        row.update({"chunks": len(own), "chars": sum(len(r["text"]) for r in own),
                    "duplicates_dropped": dropped[source_id]})
    if only:
        # Sources not run this time keep their earlier records, and their
        # rows of the manifest (counts from the records, the rest from the
        # earlier manifest where it has them).
        earlier = bundle_dir / "chunks.jsonl"
        kept: list[dict[str, Any]] = []
        if earlier.is_file():
            for line in earlier.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("source_id") not in only:
                    kept.append(record)
        previous_rows: dict[str, Any] = {}
        earlier_manifest = bundle_dir / "manifest.json"
        if earlier_manifest.is_file():
            try:
                previous_rows = dict(json.loads(earlier_manifest.read_text(encoding="utf-8")).get("sources", {}))
            except ValueError:
                previous_rows = {}
        for source in sources.sources:
            if source.id in only:
                continue
            own = [record for record in kept if record.get("source_id") == source.id]
            row = dict(previous_rows.get(source.id, {}))
            row.update({"name": source.name, "kind": source.kind, "grade": source.grade, "license": source.license,
                        "chunks": len(own), "chars": sum(len(r["text"]) for r in own), "carried": True})
            row.setdefault("failures", 0)
            report.per_source[source.id] = row
        for record in kept:
            seen_text.setdefault(_dedupe_key(record["text"]), record)
        records = kept + records
    glossary = {
        "generated_at": now_iso(),
        "license": "CC0 1.0 (Wikidata labels) · titles only",
        "terms": {word: sorted(english) for word, english in sorted(ctx.glossary_terms.items()) if english},
        "aliases": {title: sorted(korean) for title, korean in sorted(ctx.glossary_aliases.items()) if korean},
        "sitelinks": {k: v for k, v in ctx.sitelinks.items() if not k.endswith(":domains")},
    }
    (bundle_dir / "glossary.json").write_text(json.dumps(glossary, ensure_ascii=False, indent=0), encoding="utf-8")
    with (bundle_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (bundle_dir / "failures.jsonl").open("w", encoding="utf-8") as handle:
        for failure in report.failures:
            handle.write(json.dumps(failure, ensure_ascii=False) + "\n")
    manifest = {
        "generated_at": now_iso(),
        "started_at": report.started_at,
        "sources_file": str(SOURCES_FILE.relative_to(ROOT)),
        "sources": report.per_source,
        "totals": {
            "chunks": len(records),
            "chars": sum(len(r["text"]) for r in records),
            "bytes_jsonl": (bundle_dir / "chunks.jsonl").stat().st_size,
            "languages": dict(Counter(r["language"] for r in records)),
            "grades": dict(Counter(r["grade"] for r in records)),
            "duplicates_dropped": sum(dropped.values()),
            "failures": len(report.failures),
            "requests": http.requests_made,
            "requests_by_host": dict(http.requests_by_host),
            "glossary_terms": len(glossary["terms"]),
        },
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def read_bundle(runtime_dir: Path) -> list[dict[str, Any]]:
    path = runtime_dir / "bundle" / "chunks.jsonl"
    if not path.is_file():
        raise SystemExit(f"no bundle at {path}; run collect first")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_bundle_glossary(runtime_dir: Path, words_path: Path | None = None) -> Glossary:
    """The bundle's Wikidata glossary joined by the hand-written list (lane KNOW2):
    its Korean names go beside the English titles of the pieces indexed."""

    path = runtime_dir / "bundle" / "glossary.json"
    glossary = Glossary.load(path) if path.is_file() else Glossary.empty()
    words = words_path or default_words_path()
    return glossary.with_words(words) if words.is_file() else glossary


# --- upload -------------------------------------------------------------------------------------


def documents_for(records: Sequence[Mapping[str, Any]], glossary: Glossary) -> list[dict[str, Any]]:
    """Index documents: id, searched text (head line, then the words) and string metadata."""

    documents: list[dict[str, Any]] = []
    # A title's Korean names from Wikidata ("Fume hood") and from the hand-written
    # list ("fume hood"), whichever case each wrote it in.
    by_title: dict[str, tuple[str, ...]] = {}
    for name, koreans in glossary.aliases.items():
        key = name.casefold()
        by_title[key] = tuple(dict.fromkeys((*by_title.get(key, ()), *koreans)))
    for record in records:
        aliases = by_title.get(str(record.get("title", "")).casefold(), ())[:4]
        # The piece's own words are the searched text after its head line
        # (``public_knowledge._body_of``); sending them again as a payload
        # would double what the plan's monthly ingest counts.
        documents.append({
            "id": record["id"],
            "text": index_text_for(record, aliases),
            "metadata": {name: str(record.get(name, "")) for name in METADATA_FIELDS},
        })
    return documents


def estimate_memory_mib(records: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    korean = sum(1 for r in records if r.get("language") == "ko")
    other = len(records) - korean
    index_kib = (other * MOSS_KB_PER_CHUNK + korean * MOSS_KB_PER_KOREAN_CHUNK) / 1.024
    index_mib = index_kib / 1024
    return {
        "index_mib": round(index_mib, 1),
        "steady_mib": round(MOSS_SDK_MIB + MOSS_MODEL_MIB + index_mib, 1),
        "load_peak_mib": round(MOSS_SDK_MIB + max(380 - MOSS_SDK_MIB, MOSS_MODEL_MIB) + 2 * index_mib, 1),
    }


async def upload(
    records: Sequence[Mapping[str, Any]],
    glossary: Glossary,
    *,
    index_name: str,
    model_id: str,
    project_id: str,
    project_key: str,
    runtime_dir: Path,
    client_factory: Callable[..., Any] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Create or update the index from the bundle; upserts changed documents, deletes the gone."""

    documents = documents_for(records, glossary)
    state_path = runtime_dir / "bundle" / f"upload.{index_name}.json"
    previous: dict[str, Any] = {}
    if state_path.is_file():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except ValueError:
            previous = {}
    previous_hashes: dict[str, str] = dict(previous.get("documents", {}))
    current_hashes = {doc["id"]: hashlib.sha1(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                      for doc in documents}
    if client_factory is None:
        import moss  # noqa: PLC0415

        def client_factory(pid: str, key: str) -> Any:  # noqa: E306
            return moss.MossClient(pid, key)
        make_document = lambda doc: moss.DocumentInfo(  # noqa: E731
            id=doc["id"], text=doc["text"], metadata=doc["metadata"])
        upsert_options = moss.MutationOptions(upsert=True)
    else:
        make_document = lambda doc: doc  # noqa: E731
        upsert_options = {"upsert": True}
    client = client_factory(project_id, project_key)
    started = time.perf_counter()
    calls = 0
    exists = False
    try:
        try:
            await client.get_index(index_name)
            exists = True
        except Exception:  # noqa: BLE001 - "not found" is the only expected failure here
            exists = False
        calls += 1
        if not exists:
            batches = list(_batches(documents, UPLOAD_BATCH))
            first = batches[0] if batches else []
            log(f"create_index {index_name} model={model_id} docs={len(first)} (of {len(documents)})")
            await client.create_index(index_name, [make_document(doc) for doc in first], model_id)
            calls += 1
            for batch in batches[1:]:
                await client.add_docs(index_name, [make_document(doc) for doc in batch], upsert_options)
                calls += 1
                log(f"add_docs +{len(batch)}")
            changed_ids = [doc["id"] for doc in documents]
            deleted_ids: list[str] = []
        else:
            changed = [doc for doc in documents if previous_hashes.get(doc["id"]) != current_hashes[doc["id"]]]
            deleted_ids = sorted(set(previous_hashes) - set(current_hashes))
            log(f"index {index_name} exists: {len(changed)} changed/new, {len(deleted_ids)} to delete")
            for batch in _batches(changed, UPLOAD_BATCH):
                await client.add_docs(index_name, [make_document(doc) for doc in batch], upsert_options)
                calls += 1
                log(f"add_docs +{len(batch)}")
            for batch in _batches(deleted_ids, UPLOAD_BATCH):
                await client.delete_docs(index_name, list(batch))
                calls += 1
            changed_ids = [doc["id"] for doc in changed]
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass
    outcome = {
        "index": index_name, "model": model_id, "documents": len(documents), "created": not exists,
        "changed": len(changed_ids), "deleted": len(deleted_ids), "calls": calls,
        "text_bytes": sum(len(doc["text"].encode("utf-8")) for doc in documents),
        "seconds": round(time.perf_counter() - started, 1), "at": now_iso(),
    }
    state_path.write_text(json.dumps({"documents": current_hashes, "last": outcome}, ensure_ascii=False),
                          encoding="utf-8")
    return outcome


# --- evaluate -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    language: str
    kind: str
    gold: tuple[Mapping[str, str], ...]


def load_questions(path: Path) -> list[Question]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    questions: list[Question] = []
    for entry in payload.get("questions", []):
        questions.append(Question(
            id=str(entry["id"]), text=str(entry["text"]), language=str(entry.get("language", "ko")),
            kind=str(entry.get("kind", "")), gold=tuple(dict(g) for g in entry.get("gold", [])),
        ))
    return questions


def _contains(haystack: str, needle: str) -> bool:
    return " ".join(needle.casefold().split()) in " ".join(haystack.casefold().split())


def resolve_gold(question: Question, records: Sequence[Mapping[str, Any]]) -> set[str]:
    """The record ids a question's gold specs match: source (or any), title/locator/text contains."""

    ids: set[str] = set()
    for spec in question.gold:
        source_id = spec.get("source_id", "*")
        for record in records:
            if source_id != "*" and record.get("source_id") != source_id:
                continue
            if spec.get("title_contains") and not _contains(str(record.get("title", "")), spec["title_contains"]):
                continue
            if spec.get("locator_contains") and not _contains(str(record.get("locator", "")), spec["locator_contains"]):
                continue
            if spec.get("text_contains") and not _contains(str(record.get("text", "")), spec["text_contains"]):
                continue
            ids.add(record["id"])
    return ids


def rss_mib() -> float | None:
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


class Fts5Baseline:
    """SQLite FTS5 over the bundle's searched text: trigram tokens (Korean needs them)."""

    def __init__(self, documents: Sequence[Mapping[str, Any]]) -> None:
        self._connection = sqlite3.connect(":memory:")
        self._connection.execute("CREATE VIRTUAL TABLE pieces USING fts5(doc_id UNINDEXED, body, tokenize='trigram')")
        self._connection.executemany(
            "INSERT INTO pieces(doc_id, body) VALUES (?, ?)",
            [(doc["id"], doc["text"]) for doc in documents],
        )
        self._connection.commit()

    def search(self, query: str, *, top_k: int) -> list[str]:
        terms = [term for term in re.split(r"[\s,.;:!?()\[\]{}\"'“”‘’·…/]+", query) if len(term) >= 3]
        if not terms:
            return []
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        try:
            rows = self._connection.execute(
                "SELECT doc_id FROM pieces WHERE pieces MATCH ? ORDER BY bm25(pieces) LIMIT ?", (match, top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [row[0] for row in rows]


def _percentile(values: Sequence[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def score_runs(
    questions: Sequence[Question], gold: Mapping[str, set[str]], ranked: Mapping[str, Sequence[str]],
    latencies_ms: Mapping[str, float], *, ks: Sequence[int] = (1, 3, 5),
) -> dict[str, Any]:
    scored = [q for q in questions if gold.get(q.id)]
    by_group: dict[str, list[Question]] = {"all": list(scored)}
    for question in scored:
        by_group.setdefault(f"language:{question.language}", []).append(question)
        if question.kind:
            by_group.setdefault(f"kind:{question.kind}", []).append(question)
    result: dict[str, Any] = {"questions": len(questions), "scored": len(scored),
                              "unresolved": [q.id for q in questions if not gold.get(q.id)], "groups": {}}
    for name, group in by_group.items():
        row: dict[str, Any] = {"n": len(group)}
        for k in ks:
            hits = sum(1 for q in group if any(doc in gold[q.id] for doc in list(ranked.get(q.id, []))[:k]))
            row[f"recall@{k}"] = round(hits / len(group), 3) if group else 0.0
        reciprocal = []
        for q in group:
            rank = next((i for i, doc in enumerate(ranked.get(q.id, []), 1) if doc in gold[q.id]), None)
            reciprocal.append(1 / rank if rank else 0.0)
        row["mrr"] = round(statistics.fmean(reciprocal), 3) if reciprocal else 0.0
        result["groups"][name] = row
    values = [latencies_ms[q.id] for q in questions if q.id in latencies_ms]
    result["latency_ms"] = {"p50": round(_percentile(values, 0.5), 1), "p95": round(_percentile(values, 0.95), 1),
                            "max": round(max(values), 1) if values else 0.0, "n": len(values)}
    result["misses"] = [q.id for q in scored if not any(doc in gold[q.id] for doc in list(ranked.get(q.id, []))[:5])]
    return result


def evaluate_fts5(
    questions: Sequence[Question], records: Sequence[Mapping[str, Any]], glossary: Glossary, *, top_k: int = 10,
    expand: bool = True,
) -> dict[str, Any]:
    documents = documents_for(records, glossary)
    started = time.perf_counter()
    before = rss_mib()
    baseline = Fts5Baseline(documents)
    build_ms = round((time.perf_counter() - started) * 1000, 1)
    gold = {q.id: resolve_gold(q, records) for q in questions}
    ranked: dict[str, list[str]] = {}
    latencies: dict[str, float] = {}
    for question in questions:
        text = glossary.expand(question.text) if expand else question.text
        t0 = time.perf_counter()
        ranked[question.id] = baseline.search(text, top_k=top_k)
        latencies[question.id] = (time.perf_counter() - t0) * 1000
    result = score_runs(questions, gold, ranked, latencies)
    result.update({"backend": "fts5", "expand": expand, "build_ms": build_ms, "documents": len(documents),
                   "rss_before_mib": before, "rss_after_mib": rss_mib()})
    return result


async def evaluate_moss(
    questions: Sequence[Question], records: Sequence[Mapping[str, Any]], glossary: Glossary, *,
    index_name: str, project_id: str, project_key: str, alpha: float, top_k: int = 10, expand: bool = True,
    cache_dir: str | None = None, client_factory: Callable[..., Any] | None = None, repeats: int = 1,
) -> dict[str, Any]:
    from voiney_lab.public_knowledge import MossSdkClient  # noqa: PLC0415

    before = rss_mib()
    client = (client_factory or (lambda: MossSdkClient(project_id, project_key, cache_dir=cache_dir)))()
    started = time.perf_counter()
    await client.load(index_name)
    load_ms = round((time.perf_counter() - started) * 1000, 1)
    after_load = rss_mib()
    gold = {q.id: resolve_gold(q, records) for q in questions}
    ranked: dict[str, list[str]] = {}
    latencies: dict[str, float] = {}
    try:
        for question in questions:
            text = glossary.expand(question.text) if expand else question.text
            for _ in range(repeats):
                t0 = time.perf_counter()
                found = await client.query(index_name, text, top_k=top_k, alpha=alpha)
                latencies[question.id] = (time.perf_counter() - t0) * 1000
            ranked[question.id] = [doc.id for doc in found]
    finally:
        await client.close()
    result = score_runs(questions, gold, ranked, latencies)
    result.update({"backend": "moss", "index": index_name, "alpha": alpha, "expand": expand, "load_ms": load_ms,
                   "rss_before_mib": before, "rss_after_load_mib": after_load, "rss_after_queries_mib": rss_mib(),
                   "documents": len(records)})
    return result


def format_result_table(result: Mapping[str, Any]) -> str:
    lines = [f"backend={result.get('backend')} expand={result.get('expand')} scored={result.get('scored')}/"
             f"{result.get('questions')} unresolved={len(result.get('unresolved', []))}"]
    for name, row in result.get("groups", {}).items():
        lines.append(f"  {name:<16} n={row['n']:<4} r@1={row.get('recall@1', 0):.3f} r@3={row.get('recall@3', 0):.3f} "
                     f"r@5={row.get('recall@5', 0):.3f} mrr={row.get('mrr', 0):.3f}")
    latency = result.get("latency_ms", {})
    lines.append(f"  latency p50={latency.get('p50')} ms p95={latency.get('p95')} ms max={latency.get('max')} ms")
    for key in ("build_ms", "load_ms", "rss_before_mib", "rss_after_mib", "rss_after_load_mib", "rss_after_queries_mib"):
        if key in result:
            lines.append(f"  {key}={result[key]}")
    return "\n".join(lines)


# --- The command line -----------------------------------------------------------------------------


def _load_env_file(path: str | None) -> None:
    """Load credentials from a dotenv file into the process; nothing is printed."""

    if not path:
        return
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(path, override=False)


def _credentials() -> tuple[str, str]:
    project_id = os.environ.get("MOSS_PROJECT_ID", "").strip()
    project_key = os.environ.get("MOSS_PROJECT_KEY", "").strip()
    if not project_id:
        raise SystemExit("MOSS_PROJECT_ID is not set (설정 필요)")
    if not project_key:
        raise SystemExit("MOSS_PROJECT_KEY is not set (설정 필요)")
    return project_id, project_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="공개 지식 묶음: 출처 목록 → 내려받기 → 조각 → 묶음 → Moss")
    parser.add_argument("--sources", default=str(SOURCES_FILE), help="출처 목록 TOML")
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR),
                        help="받은 원본과 묶음을 둘 폴더 (기본 data/runtime/public-knowledge)")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("collect", help="바뀐 것만 받아 묶음을 다시 만든다")
    run.add_argument("--only", help="이 출처 id 만 (쉼표로 여럿)")
    run.add_argument("--full", action="store_true", help="조건 없이 전부 다시 받는다")
    run.add_argument("--offline", action="store_true", help="요청 없이 받아 둔 원본만으로 묶음을 다시 만든다")
    commands.add_parser("status", help="묶음의 출처별 조각 수·용량을 보인다")
    commands.add_parser("glossary", help="묶음의 용어집을 data/public_knowledge/glossary.json 으로 복사한다")
    up = commands.add_parser("upload", help="묶음을 Moss 색인에 올린다 (MOSS_PROJECT_ID/KEY)")
    up.add_argument("--index", required=True, help="Moss 색인 이름")
    up.add_argument("--model", default=DEFAULT_MODEL, help="임베딩 모델 (기본 moss-minilm)")
    up.add_argument("--dry-run", action="store_true", help="올리지 않고 문서 수·용량·예상 메모리만 센다")
    up.add_argument("--env-file", help="키를 읽을 dotenv 파일 (값은 출력하지 않음)")
    ev = commands.add_parser("evaluate", help="질문 세트로 FTS5 기준선과 Moss 를 잰다")
    ev.add_argument("--questions", required=True, help="질문 세트 JSON")
    ev.add_argument("--backend", choices=("fts5", "moss"), required=True)
    ev.add_argument("--index", help="Moss 색인 이름 (moss 일 때)")
    ev.add_argument("--alpha", type=float, default=0.5, help="Moss 하이브리드 비율 (기본 0.5, 서버와 같음)")
    ev.add_argument("--top-k", type=int, default=10)
    ev.add_argument("--no-expand", action="store_true", help="용어집 확장 없이 질문 그대로")
    ev.add_argument("--repeats", type=int, default=1, help="질의 반복 횟수 (지연 측정용)")
    ev.add_argument("--cache-dir", help="Moss 색인 디스크 캐시 폴더")
    ev.add_argument("--env-file", help="키를 읽을 dotenv 파일 (값은 출력하지 않음)")
    ev.add_argument("--out", help="결과 JSON 을 쓸 파일")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime_dir = Path(args.runtime_dir)
    sources = load_sources(Path(args.sources))
    if args.command == "collect":
        http = OfflineHttp() if args.offline else Http(sources.user_agent)
        only = {item.strip() for item in args.only.split(",") if item.strip()} if args.only else None
        manifest = collect(sources, runtime_dir=runtime_dir, http=http, only=only, full=args.full,
                           offline=args.offline)
        totals = manifest["totals"]
        print(f"bundle: {totals['chunks']} chunks, {totals['chars']} chars, {totals['failures']} failures, "
              f"{totals['requests']} requests, glossary {totals['glossary_terms']} terms")
        return 0
    if args.command == "status":
        path = runtime_dir / "bundle" / "manifest.json"
        if not path.is_file():
            print("no bundle yet")
            return 1
        manifest = json.loads(path.read_text(encoding="utf-8"))
        print(f"generated {manifest['generated_at']}")
        for source_id, row in manifest["sources"].items():
            print(f"  {source_id:<24} {row.get('grade', '')} chunks={row.get('chunks', 0):<6} chars={row.get('chars', 0):<9} "
                  f"failures={row.get('failures', 0):<4} {row.get('license', '')}")
        totals = manifest["totals"]
        print(f"total chunks={totals['chunks']} chars={totals['chars']} languages={totals['languages']} "
              f"grades={totals['grades']} failures={totals['failures']}")
        print("estimated Moss memory:", estimate_memory_mib(read_bundle(runtime_dir)))
        return 0
    if args.command == "glossary":
        source = runtime_dir / "bundle" / "glossary.json"
        if not source.is_file():
            raise SystemExit("no glossary in the bundle; run collect first")
        payload = json.loads(source.read_text(encoding="utf-8"))
        target = default_glossary_path()
        target.write_text(json.dumps({k: payload[k] for k in ("generated_at", "license", "terms", "aliases")},
                                     ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        print(f"wrote {target} ({len(payload['terms'])} terms)")
        return 0
    if args.command == "upload":
        records = read_bundle(runtime_dir)
        glossary = load_bundle_glossary(runtime_dir)
        if args.dry_run:
            documents = documents_for(records, glossary)
            text_bytes = sum(len(doc["text"].encode("utf-8")) for doc in documents)
            print(json.dumps({"documents": len(documents), "text_bytes": text_bytes,
                              "metadata_bytes": sum(len(json.dumps(doc["metadata"], ensure_ascii=False).encode("utf-8"))
                                                    for doc in documents),
                              "estimated_memory": estimate_memory_mib(records)}, ensure_ascii=False))
            return 0
        _load_env_file(args.env_file)
        project_id, project_key = _credentials()
        outcome = asyncio.run(upload(records, glossary, index_name=args.index, model_id=args.model,
                                     project_id=project_id, project_key=project_key, runtime_dir=runtime_dir))
        print(json.dumps(outcome, ensure_ascii=False))
        return 0
    if args.command == "evaluate":
        records = read_bundle(runtime_dir)
        glossary = load_bundle_glossary(runtime_dir)
        questions = load_questions(Path(args.questions))
        if args.backend == "fts5":
            result = evaluate_fts5(questions, records, glossary, top_k=args.top_k, expand=not args.no_expand)
        else:
            if not args.index:
                raise SystemExit("--index is required for moss")
            _load_env_file(args.env_file)
            project_id, project_key = _credentials()
            result = asyncio.run(evaluate_moss(
                questions, records, glossary, index_name=args.index, project_id=project_id, project_key=project_key,
                alpha=args.alpha, top_k=args.top_k, expand=not args.no_expand, cache_dir=args.cache_dir,
                repeats=max(1, args.repeats)))
        print(format_result_table(result))
        if args.out:
            Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
