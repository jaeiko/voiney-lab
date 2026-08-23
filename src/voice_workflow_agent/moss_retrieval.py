"""Optional in-memory Moss reranking for approved Voice Workflow Agent catalog sections.

SQLite remains the policy authority. Moss only ranks the sections that already
passed Voice Workflow Agent's product, facility, language, approval, version, scope, and
topic gates.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .document_store import CATALOG_SCHEMA_VERSION, connect


log = logging.getLogger("voice_workflow_agent.moss")

MOSS_CAPABLE_SCOPES = frozenset({"operational", "demo", "reference_only"})
DEFAULT_ALLOWED_SCOPES = frozenset({"demo", "reference_only"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


def _boolean_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean")


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = default if not raw else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is out of range")
    return value


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = default if not raw else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is out of range")
    return value


@dataclass(frozen=True)
class MossSettings:
    """Validated Moss runtime settings loaded from trusted server environment."""

    enabled: bool
    project_id: str | None = None
    project_key: str | None = None
    index_name: str | None = None
    allowed_scopes: frozenset[str] = DEFAULT_ALLOWED_SCOPES
    alpha: float = 0.65
    candidate_limit: int = 64
    query_timeout_seconds: float = 0.25
    load_timeout_seconds: float = 60.0
    auto_refresh: bool = False
    refresh_seconds: int = 600

    @classmethod
    def from_environment(cls) -> "MossSettings":
        enabled = _boolean_env("VOICE_WORKFLOW_AGENT_MOSS_ENABLED")
        if not enabled:
            return cls(enabled=False)

        project_id = os.environ.get("MOSS_PROJECT_ID", "").strip()
        project_key = os.environ.get("MOSS_PROJECT_KEY", "").strip()
        index_name = os.environ.get("MOSS_INDEX_NAME", "").strip()
        if not all((project_id, project_key, index_name)):
            raise ValueError(
                "MOSS_PROJECT_ID, MOSS_PROJECT_KEY, and MOSS_INDEX_NAME are required"
            )

        raw_scopes = os.environ.get(
            "VOICE_WORKFLOW_AGENT_MOSS_ALLOWED_SCOPES",
            ",".join(sorted(DEFAULT_ALLOWED_SCOPES)),
        )
        allowed_scopes = frozenset(
            value.strip().casefold()
            for value in raw_scopes.split(",")
            if value.strip()
        )
        if not allowed_scopes or not allowed_scopes.issubset(MOSS_CAPABLE_SCOPES):
            raise ValueError("VOICE_WORKFLOW_AGENT_MOSS_ALLOWED_SCOPES is invalid")

        return cls(
            enabled=True,
            project_id=project_id,
            project_key=project_key,
            index_name=index_name,
            allowed_scopes=allowed_scopes,
            alpha=_bounded_float("VOICE_WORKFLOW_AGENT_MOSS_ALPHA", 0.65, 0.0, 1.0),
            candidate_limit=_bounded_int(
                "VOICE_WORKFLOW_AGENT_MOSS_CANDIDATE_LIMIT", 64, 3, 100
            ),
            query_timeout_seconds=_bounded_float(
                "VOICE_WORKFLOW_AGENT_MOSS_QUERY_TIMEOUT_MS", 250.0, 10.0, 5000.0
            )
            / 1000,
            load_timeout_seconds=_bounded_float(
                "VOICE_WORKFLOW_AGENT_MOSS_LOAD_TIMEOUT_SECONDS", 60.0, 1.0, 300.0
            ),
            auto_refresh=_boolean_env("VOICE_WORKFLOW_AGENT_MOSS_AUTO_REFRESH"),
            refresh_seconds=_bounded_int(
                "VOICE_WORKFLOW_AGENT_MOSS_REFRESH_SECONDS", 600, 60, 86400
            ),
        )


@dataclass(frozen=True)
class MossIndexDocument:
    """SDK-independent document representation used by the sync command."""

    id: str
    text: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class MossRerankResult:
    matches: list[dict[str, Any]]
    used: bool
    elapsed_ms: int


def moss_document_key(record: Mapping[str, Any]) -> str:
    """Create one stable, opaque ID shared by catalog export and runtime ranking."""

    identity = {
        "document_id": record.get("document_id"),
        "version": record.get("version"),
        "language": record.get("language"),
        "section_code": record.get("section_code"),
        "source_checksum": record.get("source_checksum"),
    }
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _aware_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current


def _review_is_current(value: str | None, now: datetime) -> bool:
    if not value:
        return True
    due = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due >= now


def catalog_sections_for_moss(
    db_path: str | Path,
    usage_scope: str,
    *,
    now: datetime | None = None,
) -> list[MossIndexDocument]:
    """Export only current, approved sections from exactly one catalog scope."""

    if (
        not isinstance(db_path, (str, Path))
        or usage_scope not in MOSS_CAPABLE_SCOPES
    ):
        raise ValueError("catalog export arguments are invalid")
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    current = _aware_now(now)

    connection = connect(path)
    try:
        metadata = connection.execute(
            "SELECT schema_version FROM catalog_metadata"
        ).fetchall()
        if (
            len(metadata) != 1
            or metadata[0]["schema_version"] != CATALOG_SCHEMA_VERSION
        ):
            raise ValueError("unsupported safety catalog schema")
        rows = connection.execute(
            """
            SELECT
              d.document_id, d.version, d.language, d.source_checksum,
              d.title, d.document_type, d.product_name, d.product_code,
              d.facility_id, d.usage_scope, d.review_due_at,
              s.section_code, s.section_title, s.content, s.topic, s.keywords
            FROM documents AS d
            JOIN sections AS s ON s.document_row_id = d.id
            WHERE d.usage_scope = ?
              AND d.approval_status = 'approved'
              AND d.active = 1
              AND d.source_authority != 'test_fixture'
              AND d.translation_status IN ('original', 'human_reviewed')
            ORDER BY d.document_id, d.version, d.language,
                     s.page_start, s.section_code
            """,
            (usage_scope,),
        ).fetchall()
    except sqlite3.Error:
        raise
    finally:
        connection.close()

    documents: list[MossIndexDocument] = []
    for row in rows:
        if not _review_is_current(row["review_due_at"], current):
            continue
        record = dict(row)
        key = moss_document_key(record)
        keywords = json.loads(row["keywords"])
        text_parts = [
            row["title"],
            row["product_name"] or "",
            row["product_code"] or "",
            row["section_title"],
            row["content"],
            " ".join(str(item) for item in keywords),
        ]
        documents.append(
            MossIndexDocument(
                id=key,
                text="\n".join(part for part in text_parts if part).strip(),
                metadata={
                    "voice_workflow_agent_key": key,
                    "document_id": row["document_id"],
                    "version": row["version"],
                    "language": row["language"],
                    "document_type": row["document_type"],
                    "section_code": row["section_code"],
                    "topic": row["topic"] or "",
                    "facility_id": row["facility_id"] or "__global__",
                    "usage_scope": row["usage_scope"],
                    "source_checksum": row["source_checksum"],
                },
            )
        )
    return documents


def _route_priority(topic_routes: Sequence[tuple[str, str | None]], match: Mapping[str, Any]) -> int:
    document_type = match.get("document_type")
    for index, (routed_type, _) in enumerate(topic_routes):
        if document_type == routed_type:
            return index
    return len(topic_routes)


class MossRuntime:
    """Own a loaded Moss index on a dedicated event-loop thread."""

    def __init__(
        self,
        settings: MossSettings,
        *,
        client_factory: Callable[[str, str], Any] | None = None,
        query_options_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory
        self._query_options_factory = query_options_factory
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_started = threading.Event()
        self._jobs: queue.Queue[
            tuple[Callable[[], Any], concurrent.futures.Future[Any]] | None
        ] = queue.Queue()
        self._ready = False
        self._state_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        with self._state_lock:
            return self._ready

    def allows_scope(self, usage_scope: str) -> bool:
        return self.ready and usage_scope in self.settings.allowed_scopes

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_started.set()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                coroutine_factory, future = job
                if future.cancelled():
                    continue
                try:
                    result = loop.run_until_complete(coroutine_factory())
                except BaseException as exc:
                    if not future.cancelled():
                        future.set_exception(exc)
                else:
                    if not future.cancelled():
                        future.set_result(result)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run_loop,
                name="voice_workflow_agent-moss-runtime",
                daemon=True,
            )
            self._thread.start()
        if not self._loop_started.wait(timeout=5) or self._loop is None:
            raise RuntimeError("Moss runtime loop did not start")
        return self._loop

    def _submit(
        self, coroutine_factory: Callable[[], Any], timeout: float
    ) -> Any:
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._jobs.put((coroutine_factory, future))
        try:
            return future.result(timeout=timeout)
        except Exception:
            future.cancel()
            raise

    def _sdk_types(self) -> tuple[Callable[[str, str], Any], Callable[..., Any]]:
        if self._client_factory is not None and self._query_options_factory is not None:
            return self._client_factory, self._query_options_factory
        try:
            from moss import MossClient, QueryOptions
        except ImportError as exc:
            raise RuntimeError(
                "Moss SDK is not installed; install voice-workflow-agent[moss]"
            ) from exc
        return self._client_factory or MossClient, self._query_options_factory or QueryOptions

    async def _load(self) -> None:
        client_factory, query_options_factory = self._sdk_types()
        self._query_options_factory = query_options_factory
        self._client = client_factory(
            self.settings.project_id or "", self.settings.project_key or ""
        )
        await self._client.load_index(
            self.settings.index_name,
            auto_refresh=self.settings.auto_refresh,
            polling_interval_in_seconds=self.settings.refresh_seconds,
        )

    def start(self) -> bool:
        if not self.settings.enabled:
            return False
        if self.ready:
            return True
        self._ensure_loop()
        self._submit(self._load, self.settings.load_timeout_seconds)
        with self._state_lock:
            self._ready = True
        log.info("Moss index loaded in memory: %s", self.settings.index_name)
        return True

    def rerank(
        self,
        query: str,
        matches: Iterable[Mapping[str, Any]],
        *,
        usage_scope: str,
        topic_routes: Sequence[tuple[str, str | None]],
        result_limit: int = 3,
    ) -> MossRerankResult:
        original = [dict(match) for match in matches]
        started = time.perf_counter()
        if (
            not query.strip()
            or not original
            or not self.allows_scope(usage_scope)
            or self._loop is None
            or self._client is None
            or self._query_options_factory is None
        ):
            return MossRerankResult(
                original[:result_limit],
                False,
                round((time.perf_counter() - started) * 1000),
            )

        by_key = {moss_document_key(match): match for match in original}
        candidate_keys = list(by_key)
        options = self._query_options_factory(
            top_k=min(self.settings.candidate_limit, len(candidate_keys)),
            alpha=self.settings.alpha,
            filter={
                "field": "voice_workflow_agent_key",
                "condition": {"$in": candidate_keys},
            },
        )
        try:
            result = self._submit(
                lambda: self._client.query(
                    self.settings.index_name, query, options
                ),
                self.settings.query_timeout_seconds,
            )
            moss_order = [
                item.id for item in getattr(result, "docs", []) if item.id in by_key
            ]
            if not moss_order:
                raise RuntimeError("Moss returned no approved candidate IDs")
            rank = {key: index for index, key in enumerate(moss_order)}
            ranked = sorted(
                enumerate(original),
                key=lambda item: (
                    _route_priority(topic_routes, item[1]),
                    rank.get(moss_document_key(item[1]), len(rank)),
                    item[0],
                ),
            )
            elapsed = round((time.perf_counter() - started) * 1000)
            return MossRerankResult(
                [match for _, match in ranked[:result_limit]], True, elapsed
            )
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000)
            log.warning(
                "Moss query failed; using deterministic SQLite order: %s",
                type(exc).__name__,
            )
            return MossRerankResult(original[:result_limit], False, elapsed)

    async def _unload(self) -> None:
        if self._client is not None and self.settings.index_name:
            await self._client.unload_index(self.settings.index_name)

    def close(self) -> None:
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return
        if self.ready:
            try:
                self._submit(self._unload, 5)
            except Exception:
                pass
        with self._state_lock:
            self._ready = False
        self._jobs.put(None)
        thread.join(timeout=5)
        self._thread = None
        self._loop = None
        self._client = None
        self._loop_started.clear()


_runtime_lock = threading.Lock()
_runtime: MossRuntime | None = None


def get_moss_runtime() -> MossRuntime | None:
    with _runtime_lock:
        return _runtime


def start_moss_runtime_from_environment() -> MossRuntime | None:
    """Load the configured index, or leave SQLite as the active backend."""

    global _runtime
    try:
        settings = MossSettings.from_environment()
    except ValueError as exc:
        log.warning("Moss configuration rejected; SQLite remains active: %s", exc)
        return None
    if not settings.enabled:
        return None

    runtime = MossRuntime(settings)
    try:
        runtime.start()
    except Exception as exc:
        runtime.close()
        log.warning(
            "Moss initialization failed; SQLite remains active: %s",
            type(exc).__name__,
        )
        return None
    with _runtime_lock:
        previous = _runtime
        _runtime = runtime
    if previous is not None and previous is not runtime:
        previous.close()
    return runtime


def stop_moss_runtime() -> None:
    global _runtime
    with _runtime_lock:
        runtime = _runtime
        _runtime = None
    if runtime is not None:
        runtime.close()
