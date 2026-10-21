#!/usr/bin/env python3
"""Pilot starter pack: public protocols, analysed ahead of the first day (lane PK).

A pilot user should be able to start an experiment on the first day without
having uploaded anything. This tool takes a list of public, reusable protocols
(``scripts/pilot_starter_pack.csv``: file, title, field, source, licence ...),
puts them into the catalog of one data folder, analyses them the way the server
does, and prints what came out: which can be used at once, which wait for
confirmation cards a person handles on the screen, and which failed and why.

    python scripts/pilot_starter_pack.py --data-dir DIR --source-dir DIR --dry-run
    python scripts/pilot_starter_pack.py --data-dir DIR --source-dir DIR --report-dir OUT

Nothing here is a second path. The catalog calls are the server's own
(``ProtocolCatalog.register`` -> ``run_ocr`` -> ``request_analysis`` ->
``analyze``), with the same checks, the same automatic retry and the same
evidence rules; no check is relaxed and nothing is confirmed on a person's
behalf. A protocol whose analysis passes is "ready"; one with items the server
could not verify waits as "검토 대기" with its cards, and a person settles them
on the screen. A protocol has no workflow state of its own here: the start is
still the experimenter's press on "이 프로토콜로 시작".

What it will and will not do:

- **Only reusable sources.** A row whose licence is not CC BY (any version),
  CC0 or public domain -- or whose licence carries NC, ND or SA -- is refused
  before any call. Source, authors and licence stay in the report
  (``starter_pack_sources.md``) for showing to the pilot's users.
- **A folder it is given.** ``--data-dir`` is required, and the repository's
  own ``data/runtime/`` is refused unless ``--allow-runtime-dir`` says that
  loading the pilot's folder is meant (stop the pilot server first).
- **Once.** The catalog finds a file again by its checksum, so a second run
  uploads nothing twice; a protocol already ready, waiting for review or
  failed is not analysed again (``--retry-failed`` asks for the failed ones).
- **A bounded bill.** ``--dry-run`` estimates the calls and the cost and
  touches nothing. A real run reserves every model call and every OCR page
  against ``--max-usd`` and ``--max-clova-pages`` (counting what an earlier
  run wrote to the same ledger), writes each call to the ledger (numbers and
  names only, never text), and stops when a cap is reached.
- **Shared with the lab.** The protocols are bound to the lab's organisation
  (``--organization``, ``lab-pilot`` as accounts are) by the service
  principal ``service-starter-pack``, so every account of the lab sees them.

The keys and the model settings come from the shell, as for the server
(``VOINEY_LAB_ANALYSIS_PROVIDER``/``_MODEL``/``_REASONING``,
``VOINEY_LAB_OCR_PROVIDERS`` and the engines' credentials). This tool does not
read ``.env``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_LIST = Path(__file__).with_name("pilot_starter_pack.csv")
PROTOCOL_DATABASE_FILENAME = "protocol_workspace.sqlite"
RUNTIME_DIR = ROOT / "data" / "runtime"
DEFAULT_ORGANIZATION = "lab-pilot"
SERVICE_PRINCIPAL_ID = "service-starter-pack"
REQUIRED_COLUMNS = ("file", "title", "field", "source", "license")
OPTIONAL_COLUMNS = ("id", "topic", "authors", "source_url", "license_evidence", "sha256", "note")

#: USD per million tokens: input, cached input, output (thinking is billed as
#: output). The ledger prices of the analysis lanes; ``--price`` overrides.
PRICES: dict[str, tuple[float, float, float]] = {
    "gemini-3.8-flash": (0.75, 0.075, 3.75),
    "claude-sonnet-5-5": (2.0, 0.2, 10.0),
    "claude-opus-5-5": (4.0, 0.4, 20.0),
}
#: Google Vision DOCUMENT_TEXT_DETECTION, USD per page.
VISION_USD_PER_PAGE = 0.0015
#: What one analysis call is held at before it is sent (USD), and the
#: dry-run's assumptions about how many calls a document needs.
CALL_RESERVE_USD = 0.05
EXPECTED_CALLS_FACTOR = 1.3
WORST_CALLS_FACTOR = 3.0
PROMPT_OVERHEAD_TOKENS = 4000
CHARS_PER_TOKEN = 3.2

STATE_READY = "ready"
STATE_REVIEW = "review"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"
STATE_LABELS = {
    STATE_READY: "바로 쓸 수 있음",
    STATE_REVIEW: "확인할 것 있음",
    STATE_FAILED: "실패",
    STATE_SKIPPED: "건너뜀",
}


class StarterPackError(RuntimeError):
    """The request is refused before anything is written (exit status 2)."""


class BudgetExceeded(RuntimeError):
    """A cap was reached; nothing more is sent (exit status 3)."""


# --- The list ---------------------------------------------------------------


@dataclass(frozen=True)
class PackEntry:
    id: str
    file: str
    title: str
    field: str
    source: str
    license: str
    topic: str = ""
    authors: str = ""
    source_url: str = ""
    license_evidence: str = ""
    sha256: str = ""
    note: str = ""


def license_allowed(text: str) -> bool:
    """Whether a licence line is one this pack may reuse.

    CC BY (any version), CC0 and public domain only. A line carrying NC, ND
    or SA, or anything else (an internal-use clause, "all rights reserved"),
    is not reusable here.
    """

    tokens = re.findall(r"[a-z0-9]+", text.casefold())
    if not tokens:
        return False
    if {"nc", "nd", "sa"} & set(tokens):
        return False
    if tokens[0] == "cc" and len(tokens) > 1 and tokens[1] == "by":
        return True
    if tokens[0] == "cc0":
        return True
    return tokens[:2] == ["public", "domain"]


def read_list(path: Path) -> list[PackEntry]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing = [name for name in REQUIRED_COLUMNS if name not in columns]
        if missing:
            raise StarterPackError(f"목록에 칸이 없습니다: {', '.join(missing)}")
        entries: list[PackEntry] = []
        seen: set[str] = set()
        for number, row in enumerate(reader, start=2):
            values = {key: (row.get(key) or "").strip() for key in (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS)}
            if not any(values.values()):
                continue
            blank = [name for name in REQUIRED_COLUMNS if not values[name]]
            if blank:
                raise StarterPackError(f"목록 {number}번째 줄에 빈 칸이 있습니다: {', '.join(blank)}")
            values["id"] = values["id"] or Path(values["file"]).stem
            if values["id"] in seen:
                raise StarterPackError(f"목록에 같은 id 가 두 번 있습니다: {values['id']}")
            seen.add(values["id"])
            entries.append(PackEntry(**values))
    if not entries:
        raise StarterPackError("목록이 비어 있습니다.")
    return entries


def resolve_source(entry: PackEntry, source_dirs: Sequence[Path]) -> Path | None:
    for directory in source_dirs:
        candidate = Path(directory) / entry.file
        if candidate.is_file():
            return candidate
    return None


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- Cost, ledger and caps --------------------------------------------------


def read_price(argument: str | None, model: str) -> tuple[float, float, float]:
    if argument:
        try:
            first, cached, last = (float(part) for part in argument.split(","))
        except ValueError as error:
            raise StarterPackError("--price 는 입력,캐시,출력 세 숫자(USD/백만 토큰)입니다.") from error
        return first, cached, last
    if model in PRICES:
        return PRICES[model]
    raise StarterPackError(
        f"가격표에 없는 모델입니다({model}). --price 입력,캐시,출력 으로 알려 주세요."
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Meter:
    """Every paid call reserved before it is sent, written after it ends.

    The caps count what the ledger already holds, so a second run does not
    start from zero. Rows hold names and numbers only: kind, provider, model,
    tokens, pages, USD, status, seconds -- never a prompt, a document's text
    or a key.
    """

    def __init__(
        self,
        *,
        max_usd: float,
        max_clova_pages: int,
        price: tuple[float, float, float] | None = None,
        ledger: Path | None = None,
        run_id: str | None = None,
        clock: Callable[[], str] = _now,
    ) -> None:
        self.max_usd = float(max_usd)
        self.max_clova_pages = int(max_clova_pages)
        self.price = price
        self.ledger = Path(ledger) if ledger is not None else None
        self.run_id = run_id or secrets.token_hex(4)
        self._clock = clock
        self._lock = threading.Lock()
        self.current_doc: str | None = None
        self.stopped: str | None = None
        self.usd = 0.0
        self.clova_pages = 0
        self._held = 0.0
        self.rows: list[dict[str, Any]] = []
        for row in self._earlier_rows():
            self.usd += float(row.get("usd") or 0.0)
            if row.get("provider") == "clova":
                self.clova_pages += int(row.get("pages") or 0)

    def _earlier_rows(self) -> list[dict[str, Any]]:
        if self.ledger is None or not self.ledger.is_file():
            return []
        rows = []
        for line in self.ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows

    # -- reserving -----------------------------------------------------------

    def remaining_usd(self) -> float:
        with self._lock:
            return self.max_usd - self.usd - self._held

    def reserve(self, kind: str, *, usd: float, pages: int = 0) -> None:
        """Hold ``usd`` (and ``pages`` of CLOVA) for one call, or refuse it."""

        with self._lock:
            if self.stopped:
                raise BudgetExceeded(self.stopped)
            if kind == "ocr_clova":
                if self.clova_pages + pages > self.max_clova_pages:
                    self.stopped = f"CLOVA {self.max_clova_pages}쪽 상한(쓴 {self.clova_pages}쪽)"
                    raise BudgetExceeded(self.stopped)
            elif self.usd + self._held + usd > self.max_usd + 1e-9:
                self.stopped = (
                    f"구글 USD {self.max_usd:g} 상한(쓴 {self.usd:.3f}, 보류 {self._held:.3f})"
                )
                raise BudgetExceeded(self.stopped)
            self._held += usd
            if kind == "ocr_clova":
                self.clova_pages += pages

    def release(self, row: dict[str, Any], *, held: float) -> None:
        with self._lock:
            self._held = max(0.0, self._held - held)
            self.usd += float(row.get("usd") or 0.0)
            row = {"at": self._clock(), "run": self.run_id, "doc": self.current_doc, **row}
            self.rows.append(row)
            if self.ledger is not None:
                self.ledger.parent.mkdir(parents=True, exist_ok=True)
                with self.ledger.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            status = str(row.get("status", ""))
            if ("403" in status or "402" in status) and not self.stopped:
                self.stopped = f"공급자 {status[:80]}"

    def usage_usd(self, usage: Any) -> dict[str, Any]:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        details = getattr(usage, "prompt_tokens_details", None)
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        reasoning = int(getattr(usage, "reasoning_tokens", 0) or 0)
        first, cheap, last = self.price or (0.0, 0.0, 0.0)
        return {
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "usd": round(((prompt - cached) * first + cached * cheap + completion * last) / 1e6, 5),
        }

    # -- summaries -----------------------------------------------------------

    def run_summary(self) -> dict[str, Any]:
        analysis = [row for row in self.rows if row.get("kind") == "analysis"]
        ocr = [row for row in self.rows if row.get("kind", "").startswith("ocr")]
        return {
            "run": self.run_id,
            "analysis_calls": len(analysis),
            "analysis_usd": round(sum(row.get("usd") or 0.0 for row in analysis), 4),
            "vision_pages": sum(int(row.get("pages") or 0) for row in ocr if row.get("provider") == "google"),
            "vision_usd": round(sum(row.get("usd") or 0.0 for row in ocr if row.get("provider") == "google"), 4),
            "clova_pages": sum(int(row.get("pages") or 0) for row in ocr if row.get("provider") == "clova"),
            "usd_total": round(sum(row.get("usd") or 0.0 for row in self.rows), 4),
            "usd_cumulative": round(self.usd, 4),
            "clova_pages_cumulative": self.clova_pages,
            "stopped": self.stopped,
        }


class MeteredClient:
    """The analysis role's client, every call reserved and ledgered.

    Looks like the client the analysis model takes
    (``.chat.completions.create``) and changes nothing about the request.
    """

    def __init__(self, inner: Any, meter: Meter, *, model: str) -> None:
        self._inner = inner
        self._meter = meter
        self._model = model
        completions = type("Completions", (), {"create": self._create})()
        self.chat = type("Chat", (), {"completions": completions})()

    def _create(self, **request: Any) -> Any:
        meter = self._meter
        held = max(CALL_RESERVE_USD, _prompt_usd(request, meter.price))
        meter.reserve("analysis", usd=held)
        started = time.time()
        row: dict[str, Any] = {
            "kind": "analysis", "provider": "google" if "gemini" in self._model else "model",
            "model": self._model, "status": "ok", "usd": 0.0,
        }
        try:
            response = self._inner.chat.completions.create(**request)
            row.update(meter.usage_usd(getattr(response, "usage", None)))
            return response
        except Exception as error:  # noqa: BLE001 - recorded, then raised
            row["status"] = f"error:{type(error).__name__}:{str(error)[:160]}"
            raise
        finally:
            row["seconds"] = round(time.time() - started, 1)
            meter.release(row, held=held)


def _prompt_usd(request: Mapping[str, Any], price: tuple[float, float, float] | None) -> float:
    if price is None:
        return 0.0
    chars = len(json.dumps(request.get("messages") or "", ensure_ascii=False))
    return (chars / CHARS_PER_TOKEN) * price[0] / 1e6 + 8000 * price[2] / 1e6


class MeteredSession:
    """An HTTP session for the OCR engines that counts and caps their pages.

    ``ocr_provider_from_environment(session_factory=...)`` hands this to each
    engine; a request to Google Vision is priced per page and one to CLOVA is
    counted against the page cap, both written to the ledger.
    """

    def __init__(self, meter: Meter, *, session: Any = None) -> None:
        self._meter = meter
        self._session = session

    def _http(self) -> Any:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def post(self, url: str, **kwargs: Any) -> Any:
        meter = self._meter
        vision = "vision.googleapis.com" in url
        kind, provider = ("ocr_vision", "google") if vision else ("ocr_clova", "clova")
        usd = VISION_USD_PER_PAGE if vision else 0.0
        try:
            meter.reserve(kind, usd=usd, pages=1)
        except BudgetExceeded as error:
            raise _OcrStopped(str(error)) from error
        started = time.time()
        row: dict[str, Any] = {
            "kind": kind, "provider": provider, "pages": 1, "status": "ok", "usd": usd,
        }
        try:
            response = self._http().post(url, **kwargs)
            status = getattr(response, "status_code", None)
            if isinstance(status, int) and status >= 400:
                row["status"] = f"error:HTTP {status}"
            return response
        except Exception as error:  # noqa: BLE001 - recorded, then raised
            row["status"] = f"error:{type(error).__name__}"
            raise
        finally:
            row["seconds"] = round(time.time() - started, 1)
            meter.release(row, held=usd)


class _OcrStopped(requests.RequestException):
    """A cap stopped an OCR request; the engines see a failed request."""


# --- Dry run ----------------------------------------------------------------


@dataclass
class Estimate:
    id: str
    pages: int = 0
    chars: int = 0
    ocr_pages: int = 0
    pieces: int = 0
    expected_calls: float = 0.0
    worst_calls: float = 0.0
    expected_usd: float = 0.0
    worst_usd: float = 0.0
    registered: bool = False
    note: str = ""


def estimate_entry(
    entry: PackEntry,
    pdf: Path,
    *,
    price: tuple[float, float, float],
    engines: Sequence[str],
) -> Estimate:
    """What this document would cost, from its text layer alone (no call)."""

    from voiney_lab.experiment_protocol_analysis import analysis_pieces
    from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf

    extraction = extract_protocol_pdf(pdf)
    chars = sum(len(page.text) for page in extraction.pages)
    ocr_pages = len(extraction.ocr_required_page_numbers)
    pieces = len(analysis_pieces(extraction) or ())
    shares = max(1, pieces)
    per_call_in = chars / shares / CHARS_PER_TOKEN + PROMPT_OVERHEAD_TOKENS
    per_call_out = min(30000.0, max(3000.0, per_call_in * 0.5))
    per_call = (per_call_in * price[0] + per_call_out * price[2]) / 1e6
    ocr_usd = ocr_pages * (VISION_USD_PER_PAGE if "google" in engines else 0.0)
    return Estimate(
        id=entry.id, pages=extraction.page_count, chars=chars, ocr_pages=ocr_pages, pieces=pieces,
        expected_calls=shares * EXPECTED_CALLS_FACTOR, worst_calls=shares * WORST_CALLS_FACTOR,
        expected_usd=round(shares * EXPECTED_CALLS_FACTOR * per_call + ocr_usd, 4),
        worst_usd=round(shares * WORST_CALLS_FACTOR * per_call + ocr_usd, 4),
    )


# --- The run ----------------------------------------------------------------


@dataclass
class Row:
    id: str
    title: str
    field: str
    state: str
    detail: str = ""
    protocol_id: str | None = None
    steps: int | None = None
    cards: int | None = None
    reasons: str = ""
    failure_code: str | None = None
    action: str = ""
    calls: int = 0
    usd: float = 0.0
    seconds: float = 0.0
    deduplicated: bool = False
    analysed_now: bool = False
    source: str = ""
    license: str = ""
    authors: str = ""
    source_url: str = ""

    @property
    def label(self) -> str:
        if self.state == STATE_REVIEW:
            return f"확인할 것 {self.cards}장"
        if self.state == STATE_FAILED:
            return f"실패 · {self.detail}"
        if self.state == STATE_SKIPPED:
            return f"건너뜀 · {self.detail}"
        return STATE_LABELS[self.state]


@dataclass
class Environment:
    """What the run needs from outside, so a test can stand in for each."""

    model_factory: Callable[[Meter], Any]
    ocr_factory: Callable[[Meter], Any | None]
    engines: tuple[str, ...] = ()


def default_environment(env: Mapping[str, str] | None = None) -> Environment:
    """The analysis model and OCR engines as the shell configures them."""

    environ = os.environ if env is None else env

    def model_factory(meter: Meter) -> Any:
        from voiney_lab.experiment_protocol_analysis import OpenAICompatibleProtocolAnalysisModel
        from voiney_lab.model_providers import RoleModel, chat_client

        effort = environ.get("VOINEY_LAB_ANALYSIS_REASONING", "high").strip().casefold()
        if effort not in {"low", "medium", "high", "xhigh"}:
            raise StarterPackError("VOINEY_LAB_ANALYSIS_REASONING 값이 올바르지 않습니다.")
        try:
            timeout = float(environ.get("VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS", "600"))
        except ValueError as error:
            raise StarterPackError("VOINEY_LAB_PROTOCOL_ANALYSIS_TIMEOUT_SECONDS 가 숫자가 아닙니다.") from error
        role = RoleModel.from_environment("analysis", environ)
        if not role.configured:
            raise StarterPackError("분석 역할의 공급자(VOINEY_LAB_ANALYSIS_PROVIDER)가 정해지지 않았습니다.")
        if role.provider == "xai":
            raise StarterPackError("이 도구는 xai 분석 공급자를 지원하지 않습니다.")
        model_name = (environ.get("VOINEY_LAB_ANALYSIS_MODEL") or role.model or "").strip()
        if not model_name:
            raise StarterPackError("VOINEY_LAB_ANALYSIS_MODEL 이 정해지지 않았습니다.")
        client = chat_client(role, asynchronous=False, timeout=timeout, max_retries=0, environment=environ)
        return OpenAICompatibleProtocolAnalysisModel(
            MeteredClient(client, meter, model=model_name), model_name, effort)

    def ocr_factory(meter: Meter) -> Any | None:
        from voiney_lab.protocol_ocr_providers import ocr_provider_from_environment

        return ocr_provider_from_environment(
            environ, session_factory=lambda: MeteredSession(meter))

    names = tuple(
        item.strip().casefold() for item in (environ.get("VOINEY_LAB_OCR_PROVIDERS") or "").split(",")
        if item.strip())
    return Environment(model_factory, ocr_factory, names)


def _data_dir_check(data_dir: Path, *, allow_runtime: bool) -> Path:
    resolved = Path(data_dir).expanduser().resolve()
    try:
        resolved.relative_to(RUNTIME_DIR.resolve())
    except ValueError:
        return resolved
    if not allow_runtime:
        raise StarterPackError(
            f"{resolved} 는 저장소의 data/runtime/ 안입니다. 실제 파일럿 폴더에 올릴 생각이면 서버를 멈추고 "
            "--allow-runtime-dir 를 더하세요. 아니면 새 폴더를 쓰세요."
        )
    return resolved


def _open_stores(data_dir: Path, workspace_dir: Path):
    from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
    from voiney_lab.experiment_protocol_store import initialize_protocol_store
    from voiney_lab.protocol_catalog import ProtocolCatalog
    from voiney_lab.workspace_store import WorkspaceSettings, initialize_workspace_store

    store = initialize_protocol_store(ProtocolPersistenceSettings(True, data_dir))
    workspace = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
    return ProtocolCatalog(store), store, workspace


def _service_principal(organization: str):
    from voiney_lab.identity import Principal, Role

    return Principal(
        principal_id=SERVICE_PRINCIPAL_ID,
        subject="service:starter-pack",
        organization_id=organization,
        display_name="스타터 팩",
        roles=frozenset({Role.RESEARCHER}),
        authentication_method="service",
    )


def _prepare_pdf(path: Path, work: Path) -> tuple[Path, Any]:
    """The PDF to register: a PDF as it is, a Word document or photo converted
    as the upload does it. Returns (pdf, conversion or None)."""

    from dataclasses import replace

    from voiney_lab.experiment_protocol_pdf import (
        PDF_MEDIA_TYPE,
        SOURCE_MEDIA_TYPES,
        convert_source_to_pdf,
    )

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return path, None
    media = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic",
    }.get(suffix)
    if media is None or media not in SOURCE_MEDIA_TYPES or media == PDF_MEDIA_TYPE:
        raise StarterPackError(f"읽을 수 없는 형식입니다: {suffix}")
    target = work / "converted.pdf"
    conversion = replace(convert_source_to_pdf(path, media, target), filename=path.name)
    return target, conversion


def _registered_state(catalog: Any, protocol_id: str) -> str:
    """ready / review / failed / open, from the catalog's own entry."""

    entry = catalog.get_entry(protocol_id)
    if entry.available_for_execution:
        return STATE_READY
    if entry.review_pending is not None:
        return STATE_REVIEW
    if entry.analysis_status in {"analysis_failed", "ocr_failed", "ocr_rejected"}:
        return STATE_FAILED
    return "open"


def _fill_result(row: Row, catalog: Any) -> None:
    entry = catalog.get_entry(row.protocol_id)
    row.steps = entry.step_count
    if entry.available_for_execution:
        row.state = STATE_READY
        row.detail = ""
        return
    if entry.review_pending is not None:
        pending = entry.review_pending
        row.state = STATE_REVIEW
        row.cards = int(pending.get("needs_review_count") or 0)
        row.reasons = str(pending.get("message_ko") or "")
        row.action = "화면에서 확인 카드를 처리하세요."
        return
    pipeline = catalog.pipeline_status(row.protocol_id)
    row.state = STATE_FAILED
    row.failure_code = str(pipeline.get("failure_code") or entry.analysis_status)
    row.detail = row.failure_code
    row.reasons = str(pipeline.get("message") or "")
    row.action = str(pipeline.get("action") or "")


def run_pack(
    entries: Sequence[PackEntry],
    *,
    data_dir: Path,
    source_dirs: Sequence[Path],
    environment: Environment,
    meter: Meter,
    organization: str = DEFAULT_ORGANIZATION,
    workspace_dir: Path | None = None,
    retry_failed: bool = False,
    progress: Callable[[str], None] = lambda line: None,
) -> list[Row]:
    """Register, bind, read (OCR) and analyse each entry once."""

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    catalog, store, workspace = _open_stores(data_dir, Path(workspace_dir or data_dir / "workspace"))
    rows: list[Row] = []
    try:
        principal = _service_principal(organization)
        workspace.bootstrap_principal(principal)
        model = None
        provider = None
        by_sha: dict[str, Row] = {}
        for entry in entries:
            row = Row(
                id=entry.id, title=entry.title, field=entry.field, state=STATE_SKIPPED,
                source=entry.source, license=entry.license, authors=entry.authors,
                source_url=entry.source_url)
            rows.append(row)
            meter.current_doc = entry.id
            skipped = _precheck(entry, source_dirs)
            if isinstance(skipped, str):
                row.detail = skipped
                progress(f"{entry.id}: 건너뜀 · {skipped}")
                continue
            path = skipped
            if meter.stopped:
                row.detail = f"상한에 닿아 멈춤 ({meter.stopped})"
                continue
            started = time.time()
            calls_before = len(meter.rows)
            usd_before = meter.run_summary()["usd_total"]
            try:
                with tempfile.TemporaryDirectory(prefix="starter-pack-") as work:
                    pdf, conversion = _prepare_pdf(path, Path(work))
                    registration = catalog.register(
                        pdf, source_filename=_display_name(entry.title), media_type="application/pdf",
                        converted_from=conversion, organization_id=principal.organization_id,
                        visible_protocol_ids=workspace.resource_ids(principal, "protocol_catalog"))
                protocol_id = registration.entry.protocol_id
                row.protocol_id = protocol_id
                row.deduplicated = registration.deduplicated
                twin = by_sha.get(registration.entry.source_sha256)
                if twin is not None:
                    row.state, row.detail = STATE_SKIPPED, f"목록의 {twin.id} 와 같은 파일"
                    continue
                by_sha[registration.entry.source_sha256] = row
                workspace.bind_resource(principal, "protocol_catalog", protocol_id)
                state = _registered_state(catalog, protocol_id)
                if state == STATE_FAILED and not retry_failed:
                    _fill_result(row, catalog)
                    row.detail = f"{row.detail} (이전 분석 · --retry-failed 로 다시)"
                    continue
                if state in {STATE_READY, STATE_REVIEW}:
                    _fill_result(row, catalog)
                    continue
                if model is None:
                    model = environment.model_factory(meter)
                if provider is None:
                    provider = environment.ocr_factory(meter)
                _read_and_analyse(catalog, protocol_id, model, provider, meter)
                row.analysed_now = True
                _fill_result(row, catalog)
            except StarterPackError:
                raise
            except Exception as error:  # noqa: BLE001 - one document must not stop the pack
                if meter.stopped:
                    # The cap, not the document: the catalog may hold the failed
                    # attempt, which --retry-failed asks for again.
                    row.state, row.detail = STATE_SKIPPED, f"상한에 닿아 멈춤 ({meter.stopped})"
                else:
                    code = str(getattr(error, "code", None) or type(error).__name__)
                    row.state, row.failure_code, row.detail = STATE_FAILED, code, code
                    if row.protocol_id:
                        _fill_failure(row, catalog)
            finally:
                row.seconds = round(time.time() - started, 1)
                row.calls = sum(1 for call in meter.rows[calls_before:] if call.get("kind") == "analysis")
                row.usd = round(meter.run_summary()["usd_total"] - usd_before, 4)
                progress(f"{entry.id}: {row.label} ({row.seconds:g} s, USD {row.usd:g})")
    finally:
        store.close()
        workspace.close()
    return rows


def _fill_failure(row: Row, catalog: Any) -> None:
    """The Korean line and advice the catalog holds for a failed document."""

    try:
        pipeline = catalog.pipeline_status(row.protocol_id)
    except Exception:  # noqa: BLE001 - the failure code is what gets reported
        return
    row.reasons = str(pipeline.get("message") or "")
    row.action = str(pipeline.get("action") or "")


def _display_name(title: str) -> str:
    """The catalog's one plain PDF filename for a title (no slash, bounded)."""

    plain = re.sub(r"[/\\\x00]+", "-", title).strip(" .") or "protocol"
    return f"{plain[:200]}.pdf"


def _precheck(entry: PackEntry, source_dirs: Sequence[Path]) -> Path | str:
    if not license_allowed(entry.license):
        return f"라이선스가 재사용 가능한 것이 아님({entry.license})"
    path = resolve_source(entry, source_dirs)
    if path is None:
        return "원문 파일이 없음(--source-dir 확인)"
    if entry.sha256 and file_sha256(path) != entry.sha256.casefold():
        return "목록의 sha256 과 다른 파일"
    return path


def _read_and_analyse(catalog: Any, protocol_id: str, model: Any, provider: Any, meter: Meter) -> None:
    """OCR for the pages without a usable text layer (accepted at once, as at
    an upload), then the analysis -- the server's own two catalog calls."""

    from voiney_lab.protocol_catalog import ProtocolOcrRequiredError

    state = catalog.ocr_status(protocol_id, include_text=False).get("state")
    if state in {"in_progress", "review_required"}:
        raise _CodedError(f"ocr_{state}")
    if state not in {"not_required", "accepted_for_analysis"}:
        if provider is None:
            raise _CodedError("ocr_not_configured")
        result = catalog.run_ocr(
            protocol_id, provider, ocr_id=f"ocr-{secrets.token_hex(16)}", accepted_automatically=True)
        if result.get("accepted_for_analysis") is not True:
            raise _CodedError(str(result.get("failure_code") or "protocol_ocr_not_accepted"))
    analysis_id = f"analysis-{secrets.token_hex(16)}"
    try:
        catalog.request_analysis(protocol_id, analysis_id)
        catalog.analyze(protocol_id, model, analysis_id=analysis_id)
    except ProtocolOcrRequiredError as error:
        raise _CodedError("protocol_ocr_required") from error


class _CodedError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- Output -----------------------------------------------------------------


def _table(rows: Iterable[Row]) -> str:
    lines = [
        "| id | 제목 | 분야 | 결과 | 단계 | 호출 | USD |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.id} | {row.title} | {row.field} | {row.label} | "
            f"{row.steps if row.state == STATE_READY and row.steps is not None else '–'} | {row.calls} | {row.usd:g} |")
    return "\n".join(lines)


def summary_counts(rows: Sequence[Row]) -> dict[str, Any]:
    counts = {state: sum(1 for row in rows if row.state == state) for state in STATE_LABELS}
    counts["cards"] = sum(row.cards or 0 for row in rows if row.state == STATE_REVIEW)
    return counts


def human_checks(rows: Sequence[Row]) -> str:
    """The list for the person at the screen: what waits for them, and what failed."""

    review = [row for row in rows if row.state == STATE_REVIEW]
    failed = [row for row in rows if row.state == STATE_FAILED]
    skipped = [row for row in rows if row.state == STATE_SKIPPED]
    lines = ["# 스타터 팩 — 사람이 확인할 목록", ""]
    lines.append("## 확인 카드가 있는 프로토콜 (화면에서 처리)")
    lines.append("")
    if review:
        lines.append("| 제목 | 카드 | 이유 | protocol_id |")
        lines.append("|---|---|---|---|")
        for row in review:
            lines.append(f"| {row.title} | {row.cards} | {row.reasons} | `{row.protocol_id}` |")
    else:
        lines.append("없음.")
    lines += ["", "## 분석이 실패한 프로토콜", ""]
    if failed:
        lines.append("| 제목 | 이유 | 안내 |")
        lines.append("|---|---|---|")
        for row in failed:
            lines.append(f"| {row.title} | `{row.failure_code or row.detail}` {row.reasons} | {row.action} |")
    else:
        lines.append("없음.")
    lines += ["", "## 건너뛴 항목", ""]
    if skipped:
        lines.append("| 제목 | 이유 |")
        lines.append("|---|---|")
        for row in skipped:
            lines.append(f"| {row.title} | {row.detail} |")
    else:
        lines.append("없음.")
    lines += [
        "",
        "바로 쓸 수 있는 프로토콜도 실험을 시작할 때 사람이 \"이 프로토콜로 시작\"을 누르는 것은 그대로다. "
        "이 도구는 어떤 카드도 대신 확인하지 않았다.",
    ]
    return "\n".join(lines) + "\n"


def sources_table(rows: Sequence[Row]) -> str:
    lines = [
        "# 스타터 팩 — 출처·저자·라이선스",
        "",
        "아래 프로토콜은 공개되어 재사용이 허락된 것이다. 원문의 저자와 라이선스는 바뀌지 않는다.",
        "",
        "| 제목 | 분야 | 출처 | 저자 | 라이선스 | 주소 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.title} | {row.field} | {row.source} | {row.authors or '–'} | {row.license} | "
            f"{row.source_url or '–'} |")
    return "\n".join(lines) + "\n"


def report_text(rows: Sequence[Row], meter: Meter) -> str:
    counts = summary_counts(rows)
    usage = meter.run_summary()
    head = (
        f"바로 쓸 수 있음 {counts[STATE_READY]} · 확인할 것 있음 {counts[STATE_REVIEW]}"
        f"(카드 {counts['cards']}장) · 실패 {counts[STATE_FAILED]} · 건너뜀 {counts[STATE_SKIPPED]}")
    cost = (
        f"분석 호출 {usage['analysis_calls']}회 USD {usage['analysis_usd']:g} · "
        f"Vision {usage['vision_pages']}쪽 USD {usage['vision_usd']:g} · CLOVA {usage['clova_pages']}쪽 · "
        f"합계 USD {usage['usd_total']:g} (원장 누적 USD {usage['usd_cumulative']:g} · CLOVA {usage['clova_pages_cumulative']}쪽)")
    stopped = f"\n멈춤: {usage['stopped']}" if usage["stopped"] else ""
    return f"{_table(rows)}\n\n{head}\n{cost}{stopped}\n"


def write_report(report_dir: Path, rows: Sequence[Row], meter: Meter) -> list[Path]:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": _now(),
        "summary": summary_counts(rows),
        "usage": meter.run_summary(),
        "rows": [row.__dict__ | {"label": row.label} for row in rows],
    }
    files = {
        "starter_pack_result.json": json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        "starter_pack_result.md": report_text(rows, meter),
        "starter_pack_human_checks.md": human_checks(rows),
        "starter_pack_sources.md": sources_table(rows),
    }
    paths = []
    for name, text in files.items():
        target = report_dir / name
        target.write_text(text, encoding="utf-8")
        paths.append(target)
    return paths


def registered_checksums(data_dir: Path) -> set[str]:
    """The checksums already in a data folder's catalog, read without writing."""

    import sqlite3

    database = Path(data_dir) / PROTOCOL_DATABASE_FILENAME
    if not database.is_file():
        return set()
    try:
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return {row[0] for row in connection.execute("SELECT pdf_checksum FROM protocol_revisions")}
        finally:
            connection.close()
    except sqlite3.Error:
        return set()


def dry_run_text(
    entries: Sequence[PackEntry],
    source_dirs: Sequence[Path],
    *,
    price: tuple[float, float, float],
    engines: Sequence[str],
    max_usd: float,
    data_dir: Path | None = None,
) -> tuple[str, list[Estimate]]:
    estimates: list[Estimate] = []
    known = registered_checksums(data_dir) if data_dir is not None else set()
    lines = [
        "| id | 제목 | 쪽 | 글자 | OCR 쪽 | 조각 | 호출(예상·최대) | USD(예상·최대) | 비고 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in entries:
        found = _precheck(entry, source_dirs)
        if isinstance(found, str):
            lines.append(f"| {entry.id} | {entry.title} | | | | | | | 건너뜀 · {found} |")
            continue
        with tempfile.TemporaryDirectory(prefix="starter-pack-") as work:
            try:
                pdf, _ = _prepare_pdf(found, Path(work))
                est = estimate_entry(entry, pdf, price=price, engines=engines)
                est.registered = pdf == found and file_sha256(pdf) in known
            except Exception as error:  # noqa: BLE001 - reported on its line
                lines.append(f"| {entry.id} | {entry.title} | | | | | | | 읽지 못함 · {type(error).__name__} |")
                continue
        estimates.append(est)
        lines.append(
            f"| {est.id} | {entry.title} | {est.pages} | {est.chars} | {est.ocr_pages} | {est.pieces or '–'} | "
            f"{est.expected_calls:g}·{est.worst_calls:g} | {est.expected_usd:g}·{est.worst_usd:g} | "
            f"{'카탈로그에 이미 있음(올리지 않음)' if est.registered else ''} |")
    expected = sum(e.expected_usd for e in estimates)
    worst = sum(e.worst_usd for e in estimates)
    ocr = sum(e.ocr_pages for e in estimates)
    lines += [
        "",
        f"합계: 문서 {len(estimates)}개 · 분석 호출 예상 {sum(e.expected_calls for e in estimates):g}"
        f"(최대 {sum(e.worst_calls for e in estimates):g}) · OCR {ocr}쪽",
        f"예상 비용 USD {expected:.3f} (최대 USD {worst:.3f}) · 상한 USD {max_usd:g}",
        "가정: 글자 3.2자=1토큰, 호출마다 프롬프트 4000토큰, 출력은 입력의 절반(3000–30000토큰), "
        f"호출 수 = 조각 수 × {EXPECTED_CALLS_FACTOR:g}(최대 ×{WORST_CALLS_FACTOR:g}: 자동 재시도·근거 다시 묻기). "
        "'이미 있음'으로 표시된 파일도 이 합계에는 들어 있다(분석이 끝난 것은 실행하면 호출하지 않는다).",
    ]
    return "\n".join(lines) + "\n", estimates


# --- Command line -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="공개 프로토콜 스타터 팩을 데이터 폴더의 카탈로그에 올리고 분석한다.")
    parser.add_argument("--data-dir", required=True, help="카탈로그를 둘 데이터 폴더(필수)")
    parser.add_argument("--list", dest="list_file", default=str(DEFAULT_LIST), help="목록 CSV")
    parser.add_argument("--source-dir", action="append", default=[], help="원문 폴더(여러 번 쓸 수 있음)")
    parser.add_argument("--workspace-dir", help="작업공간 폴더(기본: 데이터 폴더/workspace)")
    parser.add_argument("--organization", default=DEFAULT_ORGANIZATION, help="올릴 연구실(기본 lab-pilot)")
    parser.add_argument("--only", help="이 id 만(쉼표로 여럿)")
    parser.add_argument("--dry-run", action="store_true", help="호출 없이 비용만 어림한다")
    parser.add_argument("--retry-failed", action="store_true", help="이전에 실패한 것을 다시 분석한다")
    parser.add_argument("--report-dir", help="결과표·확인 목록·출처표를 쓸 폴더")
    parser.add_argument("--ledger", help="호출 원장(JSONL). 기본: 보고서 폴더의 ledger.jsonl")
    parser.add_argument("--max-usd", type=float, default=4.0, help="구글 호출 상한 USD(원장 누적 포함, 기본 4)")
    parser.add_argument("--max-clova-pages", type=int, default=100, help="CLOVA 쪽 상한(원장 누적 포함, 기본 100)")
    parser.add_argument("--price", help="모델 가격 입력,캐시,출력(USD/백만 토큰). 가격표에 없는 모델에 필요")
    parser.add_argument("--allow-runtime-dir", action="store_true", help="저장소 data/runtime/ 안의 폴더를 허락한다")
    parser.add_argument("--json", action="store_true", help="결과를 JSON 으로 낸다")
    return parser


def main(argv: Sequence[str] | None = None, *, environment: Environment | None = None,
         env: Mapping[str, str] | None = None, out: Any = None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        entries = read_list(Path(args.list_file))
        if args.only:
            wanted = {part.strip() for part in args.only.split(",") if part.strip()}
            unknown = wanted - {entry.id for entry in entries}
            if unknown:
                raise StarterPackError(f"목록에 없는 id: {', '.join(sorted(unknown))}")
            entries = [entry for entry in entries if entry.id in wanted]
        source_dirs = [Path(item).expanduser() for item in args.source_dir]
        environ = os.environ if env is None else env
        environment = environment or default_environment(environ)
        model_name = (environ.get("VOINEY_LAB_ANALYSIS_MODEL") or "").strip()
        if args.dry_run:
            price = read_price(args.price, model_name) if (args.price or model_name) else PRICES["gemini-3.8-flash"]
            text, _ = dry_run_text(
                entries, source_dirs, price=price, engines=environment.engines, max_usd=args.max_usd,
                data_dir=Path(args.data_dir).expanduser())
            out.write(text)
            return 0
        data_dir = _data_dir_check(Path(args.data_dir), allow_runtime=args.allow_runtime_dir)
        price = read_price(args.price, model_name)
        report_dir = Path(args.report_dir).expanduser() if args.report_dir else None
        ledger = Path(args.ledger).expanduser() if args.ledger else (
            report_dir / "ledger.jsonl" if report_dir else None)
        meter = Meter(
            max_usd=args.max_usd, max_clova_pages=args.max_clova_pages, price=price, ledger=ledger)
        rows = run_pack(
            entries, data_dir=data_dir, source_dirs=source_dirs, environment=environment, meter=meter,
            organization=args.organization,
            workspace_dir=Path(args.workspace_dir).expanduser() if args.workspace_dir else None,
            retry_failed=args.retry_failed,
            progress=lambda line: print(line, file=sys.stderr, flush=True))
    except StarterPackError as error:
        print(f"[거절] {error}", file=sys.stderr)
        return 2
    if report_dir is not None:
        write_report(report_dir, rows, meter)
    if args.json:
        out.write(json.dumps(
            {"summary": summary_counts(rows), "usage": meter.run_summary(),
             "rows": [row.__dict__ | {"label": row.label} for row in rows]},
            ensure_ascii=False, indent=2) + "\n")
    else:
        out.write(report_text(rows, meter))
        out.write("\n" + human_checks(rows))
    return 3 if meter.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
