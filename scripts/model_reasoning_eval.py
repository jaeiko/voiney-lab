#!/usr/bin/env python3
"""Plan and run bounded reasoning comparisons without reading ``.env``.

The default is always a zero-call plan.  ``--execute`` is required at the
provider boundary.  Public result rows contain identifiers, checks, timings
and token counts only; source sentences and model answers are not copied into
the report.  Analysis candidates needed by ``scripts/score_extraction.py``
are kept separately under the explicitly supplied private report directory.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import random
import re
import secrets
from types import SimpleNamespace
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings  # noqa: E402
from voiney_lab.experiment_protocol_store import initialize_protocol_store  # noqa: E402
from voiney_lab.model_providers import ProviderRetryPolicy, RoleModel, chat_client  # noqa: E402
from voiney_lab.protocol_catalog import ProtocolCatalog  # noqa: E402
from voiney_lab.protocol_translation import (  # noqa: E402
    BATCH_MAX_CHARACTERS,
    BATCH_MAX_ITEMS,
    BATCH_TIMEOUT_SECONDS,
    GlossaryEntry,
    batch_context,
    check_translation,
    is_korean,
    openai_batch_translator,
    openai_glossary_maker,
    protocol_context,
    translation_failure_kind,
    translation_units,
)

REPORT_ROOT = Path.home() / "reports"
RUNTIME_ROOT = ROOT / "data" / "runtime"
LENGTH_BANDS = ("short", "medium", "long")


class EvaluationError(RuntimeError):
    """A privacy, identity, configuration or execution boundary refused."""


class CallCapReached(EvaluationError):
    """The logical model-call cap stopped the next call before sending."""


@dataclass(frozen=True)
class SampleItem:
    group: str
    protocol_id: str
    fact_key: str
    source_sha256: str
    source_chars: int
    length_band: str = ""


@dataclass(frozen=True)
class LoadedSample:
    item: SampleItem
    fixture: Any
    unit: Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_sha256(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _checked_copy(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(RUNTIME_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise EvaluationError("data/runtime is never an evaluation target; use a catalog copy")
    if not (resolved / "protocol_workspace.sqlite").is_file():
        raise EvaluationError("catalog copy has no protocol_workspace.sqlite")
    return resolved


def _checked_report_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REPORT_ROOT.resolve())
    except ValueError as exc:
        raise EvaluationError("evaluation output must stay under ~/reports/") from exc
    return resolved


def _finite_positive(*values: float) -> bool:
    return all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) and value > 0 for value in values)


def _percentiles(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return {"p50": None, "p95": None, "n": 0}
    return {
        "p50": round(ordered[max(0, math.ceil(len(ordered) * .5) - 1)], 1),
        "p95": round(ordered[max(0, math.ceil(len(ordered) * .95) - 1)], 1),
        "n": len(ordered),
    }


def balanced_sample(
    candidates: Mapping[str, Sequence[SampleItem]], *, size: int, seed: int,
) -> list[SampleItem]:
    """Deterministically round-robin groups and within-group length terciles."""
    if size < 1 or not candidates:
        raise EvaluationError("sample size and groups must be non-empty")
    strata: dict[tuple[str, str], list[SampleItem]] = {}
    for group, raw in sorted(candidates.items()):
        ordered = sorted(raw, key=lambda item: (
            item.source_chars, item.protocol_id, item.fact_key, item.source_sha256))
        if not ordered:
            raise EvaluationError(f"sample group has no sentences: {group}")
        for index, item in enumerate(ordered):
            band = LENGTH_BANDS[min(2, index * 3 // len(ordered))]
            strata.setdefault((band, group), []).append(replace(item, length_band=band))
    for key, rows in strata.items():
        random.Random(f"{seed}:{key[0]}:{key[1]}").shuffle(rows)
    keys = [(band, group) for band in LENGTH_BANDS for group in sorted(candidates)]
    chosen: list[SampleItem] = []
    while len(chosen) < size:
        moved = False
        for key in keys:
            if len(chosen) >= size:
                break
            rows = strata.get(key) or []
            if rows:
                chosen.append(rows.pop())
                moved = True
        if not moved:
            raise EvaluationError(
                f"only {len(chosen)} eligible sentences for requested sample {size}")
    group_counts = Counter(item.group for item in chosen)
    balanced = (
        bool(group_counts)
        and max(group_counts.values()) - min(group_counts.values()) <= 1
    )
    for group in candidates:
        bands = Counter(item.length_band for item in chosen if item.group == group)
        balanced = balanced and set(bands) == set(LENGTH_BANDS)
        balanced = balanced and max(bands.values(), default=0) - min(
            bands.values(), default=0) <= 1
    if not balanced:
        raise EvaluationError(
            "eligible sentences cannot make a balanced group/length sample")
    return chosen


def sample_manifest(
    items: Sequence[SampleItem], *, catalog_sha256: str, seed: int,
) -> dict[str, Any]:
    return {
        "schema": 1,
        "catalog_sha256": catalog_sha256,
        "seed": seed,
        "sample_size": len(items),
        "items": [item.__dict__ for item in items],
    }


def _parse_groups(values: Sequence[str]) -> dict[str, tuple[str, ...]]:
    groups: dict[str, tuple[str, ...]] = {}
    used: set[str] = set()
    for value in values:
        name, separator, raw_ids = value.partition("=")
        ids = tuple(part.strip() for part in raw_ids.split(",") if part.strip())
        name = name.strip()
        if not separator or not re.fullmatch(r"[A-Za-z0-9가-힣_-]{1,40}", name) or not ids:
            raise EvaluationError("--group must be NAME=PROTOCOL_ID[,PROTOCOL_ID]")
        if name in groups or used.intersection(ids):
            raise EvaluationError("sample group names and protocol membership must be unique")
        groups[name] = ids
        used.update(ids)
    if not groups:
        raise EvaluationError("at least one --group is required")
    return groups


def make_translation_sample(
    data_dir: Path, groups: Mapping[str, Sequence[str]], *, size: int, seed: int,
) -> dict[str, Any]:
    data_dir = _checked_copy(data_dir)
    store = initialize_protocol_store(ProtocolPersistenceSettings(True, data_dir))
    try:
        catalog = ProtocolCatalog(store)
        candidates: dict[str, list[SampleItem]] = defaultdict(list)
        for group, protocol_ids in groups.items():
            for protocol_id in protocol_ids:
                fixture = catalog.load_executable_fixture(protocol_id)
                for unit in translation_units(fixture):
                    if is_korean(unit.source_text):
                        continue
                    candidates[group].append(SampleItem(
                        group=group, protocol_id=protocol_id, fact_key=unit.fact_key,
                        source_sha256=_source_sha256(unit.source_text),
                        source_chars=len(unit.source_text),
                    ))
        items = balanced_sample(candidates, size=size, seed=seed)
        return sample_manifest(
            items, catalog_sha256=_sha256(data_dir / "protocol_workspace.sqlite"),
            seed=seed)
    finally:
        store.close()


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationError("translation manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise EvaluationError("translation manifest schema is invalid")
    return payload


def load_sample(data_dir: Path, manifest: Path) -> list[LoadedSample]:
    data_dir = _checked_copy(data_dir)
    payload = _read_manifest(manifest)
    if payload.get("catalog_sha256") != _sha256(data_dir / "protocol_workspace.sqlite"):
        raise EvaluationError("manifest belongs to a different catalog copy")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) != payload.get("sample_size"):
        raise EvaluationError("translation manifest item count is invalid")
    store = initialize_protocol_store(ProtocolPersistenceSettings(True, data_dir))
    try:
        catalog = ProtocolCatalog(store)
        fixtures: dict[str, Any] = {}
        unit_maps: dict[str, dict[str, Any]] = {}
        loaded: list[LoadedSample] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise EvaluationError("translation manifest item is invalid")
            try:
                item = SampleItem(**raw)
            except TypeError as exc:
                raise EvaluationError("translation manifest item fields are invalid") from exc
            fixture = fixtures.get(item.protocol_id)
            if fixture is None:
                fixture = catalog.load_executable_fixture(item.protocol_id)
                fixtures[item.protocol_id] = fixture
            units = unit_maps.setdefault(
                item.protocol_id, {unit.fact_key: unit for unit in translation_units(fixture)})
            unit = units.get(item.fact_key)
            if unit is None or len(unit.source_text) != item.source_chars or (
                    _source_sha256(unit.source_text) != item.source_sha256):
                raise EvaluationError("translation source identity changed after sampling")
            loaded.append(LoadedSample(item, fixture, unit))
        return loaded
    finally:
        store.close()


def _batches(rows: Sequence[LoadedSample]) -> list[list[LoadedSample]]:
    batches: list[list[LoadedSample]] = []
    current: list[LoadedSample] = []
    chars = 0
    for row in rows:
        length = len(row.unit.source_text)
        if current and (len(current) >= BATCH_MAX_ITEMS
                        or chars + length > BATCH_MAX_CHARACTERS):
            batches.append(current)
            current, chars = [], 0
        current.append(row)
        chars += length
    if current:
        batches.append(current)
    return batches


def translation_plan(
    *, data_dir: Path, manifest: Path, mode: str, max_calls: int,
    max_usd: float, reserve_usd: float, input_rate: float, output_rate: float,
) -> dict[str, Any]:
    rows = load_sample(data_dir, manifest)
    if mode == "spot":
        calls = len(rows)
    else:
        by_protocol: dict[str, list[LoadedSample]] = defaultdict(list)
        for row in rows:
            by_protocol[row.item.protocol_id].append(row)
        calls = len(by_protocol) + sum(len(_batches(group)) for group in by_protocol.values())
    return {
        "mode": mode,
        "sample_size": len(rows),
        "logical_calls_at_most": calls,
        "external_requests_this_dry_run": 0,
        "call_cap": max_calls,
        "reserved_usd_cap": max_usd,
        "reserve_usd_per_transport": reserve_usd,
        "input_usd_per_million_assumed": input_rate,
        "output_usd_per_million_assumed": output_rate,
        "rates_verified": False,
    }


_FORMAL = re.compile(r"(?:합|됩|입)니다|습니다|하십시오")
_ADDRESSES = re.compile(
    r"(?<![가-힣])(?:귀하|당신|여러분)(?=(?:은|는|이|가|을|를|께|도|의)?(?:\s|[,.!?]|$))")
_ENGLISH_VERB = re.compile(r"[A-Za-z][A-Za-z-]*(?:해요|하면서|합니다|하세요)")
_AUTHOR_VOICE = re.compile(
    r"(?<![가-힣])(?:우리|저희)(?=(?:은|는|이|가|을|를|도|의)?(?:\s|[,.!?]|$))")
_TRANSLATIONESE = re.compile(
    r"하는 것을 환영합니다|에 의해|하는 것이 요구(?:됩니다|돼요|되어요|돼|되어)")
_FORBIDDEN = re.compile(
    r"워싱|흡입\s*제거|파이펫|비드|희끄무레|(?<![가-힣])겔(?![가-힣])|vortexing",
    re.I,
)


def style_issues(korean: str) -> tuple[str, ...]:
    """Mechanical proxy only; it is not a human prose-quality judgement."""
    issues = []
    for name, pattern in (
        ("formal_style", _FORMAL), ("addresses_reader", _ADDRESSES),
        ("english_verb", _ENGLISH_VERB), ("author_voice", _AUTHOR_VOICE),
        ("translationese", _TRANSLATIONESE),
        ("forbidden_wording", _FORBIDDEN),
    ):
        if pattern.search(korean or ""):
            issues.append(name)
    return tuple(issues)


def response_shape_failure_calls(failures: Mapping[str, int]) -> int:
    shapes = {"response_shape", "empty_response", "blocked"}
    return sum(
        count for kind, count in failures.items()
        if kind.removeprefix("glossary_") in shapes)


def _usage(value: object) -> dict[str, int]:
    usage: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                "cache_write_tokens", "reasoning_tokens"):
        number = getattr(value, key, None)
        if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
            usage[key] = number
    details = getattr(value, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    if isinstance(cached, int) and not isinstance(cached, bool) and cached >= 0:
        usage["cached_prompt_tokens"] = cached
    return usage


@dataclass
class CallRecord:
    content: str
    elapsed_ms: float
    usage: dict[str, int]
    status: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "elapsed_ms": round(self.elapsed_ms, 1),
            "usage": self.usage,
            "status": self.status,
        }


class _Capture:
    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.records: list[CallRecord] = []
        self.stopped = False

    def before(self) -> None:
        if len(self.records) >= self.max_calls:
            self.stopped = True
            raise CallCapReached("logical call cap reached")

    def keep(self, response: Any, started: float, status: str = "ok") -> None:
        choices = getattr(response, "choices", None) or ()
        message = getattr(choices[0], "message", None) if choices else None
        content = getattr(message, "content", None)
        self.records.append(CallRecord(
            content=content if isinstance(content, str) else "",
            elapsed_ms=(time.perf_counter() - started) * 1000,
            usage=_usage(getattr(response, "usage", None)), status=status))

    def failed(self, started: float, error: Exception) -> None:
        self.records.append(CallRecord(
            content="", elapsed_ms=(time.perf_counter() - started) * 1000,
            usage={}, status=f"error:{type(error).__name__}"))


class CaptureClient:
    """Synchronous transparent client that retains raw answers only in memory."""

    def __init__(self, inner: Any, max_calls: int | None = None, *, capture: _Capture | None = None) -> None:
        self._inner = inner
        self._capture = capture or _Capture(max_calls if max_calls is not None else 1)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @property
    def records(self) -> list[CallRecord]:
        return self._capture.records

    def _create(self, **request: Any) -> Any:
        self._capture.before()
        started = time.perf_counter()
        try:
            response = self._inner.chat.completions.create(**request)
        except Exception as exc:
            self._capture.failed(started, exc)
            raise
        self._capture.keep(response, started)
        return response

    def close(self) -> Any:
        closer = getattr(self._inner, "close", None)
        return closer() if callable(closer) else None


class AsyncCaptureClient:
    def __init__(self, inner: Any, capture: _Capture) -> None:
        self._inner, self._capture = inner, capture
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **request: Any) -> Any:
        self._capture.before()
        started = time.perf_counter()
        try:
            response = await self._inner.chat.completions.create(**request)
        except asyncio.CancelledError as exc:
            self._capture.failed(started, exc)
            raise
        except Exception as exc:
            self._capture.failed(started, exc)
            raise
        self._capture.keep(response, started)
        return response

    async def close(self) -> Any:
        closer = getattr(self._inner, "close", None)
        if callable(closer):
            result = closer()
            if inspect.isawaitable(result):
                return await result
        return None


def build_model_client(
    role: RoleModel, *, asynchronous: bool, timeout: float, capture: _Capture,
) -> Any:
    inner = chat_client(
        role, asynchronous=asynchronous, timeout=timeout, max_retries=0,
        retry_policy=ProviderRetryPolicy(max_retries=0))
    return AsyncCaptureClient(inner, capture) if asynchronous else CaptureClient(
        inner, capture=capture)


def _cost(usage: Mapping[str, int], input_rate: float, output_rate: float) -> float:
    return round((usage.get("prompt_tokens", 0) * input_rate
                  + usage.get("completion_tokens", 0) * output_rate) / 1e6, 5)


def _capture_summary(
    capture: _Capture, *, input_rate: float, output_rate: float,
) -> dict[str, Any]:
    usage: Counter[str] = Counter()
    for record in capture.records:
        usage.update(record.usage)
    return {
        "logical_calls": len(capture.records),
        "successful_calls": sum(record.status == "ok" for record in capture.records),
        "latency_ms": _percentiles(record.elapsed_ms for record in capture.records),
        "usage": dict(usage),
        "usage_missing_calls": sum(not record.usage for record in capture.records),
        "logical_call_cap_stopped": capture.stopped,
        "cost_usd_estimate": _cost(usage, input_rate, output_rate),
        "input_usd_per_million_assumed": input_rate,
        "output_usd_per_million_assumed": output_rate,
        "rates_verified": False,
    }


async def _run_translation(
    rows: Sequence[LoadedSample], *, mode: str, role: RoleModel,
    capture: _Capture,
) -> tuple[list[dict[str, Any]], Counter[str], list[float]]:
    results: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()
    translation_latencies: list[float] = []

    def row_result(
        row: LoadedSample, *, korean: str = "", check: str = "missing",
        failure: str | None = None, latency_ms: float | None = None,
    ) -> dict[str, Any]:
        return {
            "group": row.item.group,
            "protocol_id": row.item.protocol_id,
            "fact_key": row.item.fact_key,
            "source_sha256": row.item.source_sha256,
            "source_chars": row.item.source_chars,
            "length_band": row.item.length_band,
            "check": check,
            "style_issues": list(style_issues(korean)) if korean else [],
            "failure": failure,
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        }

    if mode == "spot":
        # These are the product's own two spot request functions and prompts.
        # The patch supplies the configured role's observed client only; the
        # request, six-second deadline and parsing remain server-owned.
        from voiney_lab import server

        client = build_model_client(
            role, asynchronous=True, timeout=6.0, capture=capture)
        try:
            with patch.object(server, "_role_client", return_value=client):
                session = SimpleNamespace(
                    multi_brain_settings=SimpleNamespace(answer_timeout_seconds=6.0))
                for row in rows:
                    started = time.perf_counter()
                    before = len(capture.records)
                    try:
                        if row.unit.kind == "step" and row.unit.step_label:
                            korean = await server._request_reader_translation(
                                session, row.unit.step_label, row.unit.source_text)
                        else:
                            quote = SimpleNamespace(
                                kind=row.unit.kind, label=row.unit.step_label,
                                text=row.unit.source_text)
                            korean = await server._request_spot_translation(session, quote)
                        check = check_translation(row.unit, korean)
                    except Exception as exc:  # provider/shape failures are measured, never echoed
                        failure = translation_failure_kind(exc)
                        failures[failure] += 1
                        latency = ((capture.records[-1].elapsed_ms
                                    if len(capture.records) > before else
                                    (time.perf_counter() - started) * 1000))
                        translation_latencies.append(latency)
                        results.append(row_result(
                            row, failure=failure, latency_ms=latency))
                    else:
                        latency = ((capture.records[-1].elapsed_ms
                                    if len(capture.records) > before else
                                    (time.perf_counter() - started) * 1000))
                        translation_latencies.append(latency)
                        results.append(row_result(
                            row, korean=korean, check=check, latency_ms=latency))
        finally:
            await client.close()
        return results, failures, translation_latencies

    by_protocol: dict[str, list[LoadedSample]] = defaultdict(list)
    for row in rows:
        by_protocol[row.item.protocol_id].append(row)
    for protocol_rows in by_protocol.values():
        fixture = protocol_rows[0].fixture
        glossary: Sequence[GlossaryEntry] = ()

        def factory() -> Any:
            return build_model_client(
                role, asynchronous=True, timeout=BATCH_TIMEOUT_SECONDS,
                capture=capture)

        make_glossary = openai_glossary_maker(
            factory, str(role.model), timeout=BATCH_TIMEOUT_SECONDS)
        try:
            glossary_result = await make_glossary(protocol_context(fixture))
            glossary = glossary_result.entries
        except Exception as exc:
            failure = translation_failure_kind(exc)
            failures[f"glossary_{failure}"] += 1
        translate = openai_batch_translator(
            factory, str(role.model), timeout=BATCH_TIMEOUT_SECONDS)
        for batch in _batches(protocol_rows):
            before = len(capture.records)
            try:
                translated = await translate(
                    [row.unit for row in batch],
                    batch_context(protocol_context(fixture), glossary,
                                  [row.unit for row in batch]))
            except Exception as exc:
                failure = translation_failure_kind(exc)
                failures[failure] += 1
                latency = (capture.records[-1].elapsed_ms
                           if len(capture.records) > before else 0.0)
                translation_latencies.append(latency)
                results.extend(row_result(
                    row, failure=failure, latency_ms=latency) for row in batch)
                continue
            latency = capture.records[-1].elapsed_ms if len(capture.records) > before else 0.0
            translation_latencies.append(latency)
            for row in batch:
                korean = translated.translations.get(row.unit.fact_key, "")
                results.append(row_result(
                    row, korean=korean,
                    check=check_translation(row.unit, korean, glossary),
                    latency_ms=latency))
    return results, failures, translation_latencies


def execute_translation(
    *, data_dir: Path, manifest: Path, mode: str, output_dir: Path,
    max_calls: int, max_usd: float, reserve_usd: float,
    input_rate: float, output_rate: float,
) -> dict[str, Any]:
    from scripts.sim.live import Budget

    rows = load_sample(data_dir, manifest)
    role = RoleModel.from_environment("translation")
    if not role.configured:
        raise EvaluationError("VOINEY_LAB_TRANSLATION_PROVIDER is unset")
    if not role.has_key():
        raise EvaluationError(f"{role.api_key_setting} is unset")
    if role.reasoning not in {"low", "medium", "high"}:
        raise EvaluationError(
            "translation comparison requires explicit low, medium, or high reasoning")
    output_dir = _checked_report_path(output_dir)
    if output_dir.exists():
        raise EvaluationError("translation output directory already exists")
    output_dir.mkdir(parents=True, mode=0o700)
    capture = _Capture(max_calls)
    budget = Budget(max_calls, max_usd, reserve_usd, output_dir / "transport.ledger.jsonl")
    with ExitStack() as stack:
        budget.guard(stack)
        result_rows, failures, translation_latencies = asyncio.run(_run_translation(
            rows, mode=mode, role=role, capture=capture))
    passed = sum(row["check"] == "passed" for row in result_rows)
    styled = sum(bool(row["style_issues"]) for row in result_rows)
    response_shape_calls = response_shape_failure_calls(failures)
    capture_summary = _capture_summary(
        capture, input_rate=input_rate, output_rate=output_rate)
    report = {
        "schema": 1,
        "kind": "translation_reasoning_comparison",
        "utc": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "mode": mode,
        "role": {"provider": role.provider, "model": role.model,
                 "reasoning": role.reasoning},
        "sample_size": len(result_rows),
        "passed": passed,
        "pass_rate": round(passed / len(result_rows), 4) if result_rows else None,
        "mechanical_style_violations": styled,
        "mechanical_style_violation_rate": (
            round(styled / len(result_rows), 4) if result_rows else None),
        "style_measurement": "mechanical_proxy_not_human_judgement",
        "response_shape_failure_calls": response_shape_calls,
        "failure_kinds": dict(sorted(failures.items())),
        "translation_call_latency_ms": _percentiles(translation_latencies),
        "within_6_seconds": ({
            "calls": sum(value <= 6000 for value in translation_latencies),
            "total": len(translation_latencies),
            "rate": round(sum(value <= 6000 for value in translation_latencies)
                          / len(translation_latencies), 4)
            if translation_latencies else None,
        } if mode == "spot" else None),
        "transport_requests": budget.calls,
        "reserved_usd": round(budget.usd, 4),
        "budget_stopped": budget.stopped,
        "caps": {"calls": max_calls, "usd": max_usd,
                 "reserve_usd_per_transport": reserve_usd},
        "calls": capture_summary,
        "rows": result_rows,
    }
    target = output_dir / "translation_result.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def _analysis_cases(values: Sequence[str]) -> list[tuple[str, Path]]:
    cases: list[tuple[str, Path]] = []
    used: set[str] = set()
    for value in values:
        protocol_id, separator, reference = value.partition("=")
        protocol_id = protocol_id.strip()
        path = Path(reference).expanduser().resolve()
        if (not separator
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", protocol_id)
                or protocol_id in used or not path.is_file()):
            raise EvaluationError("--case must be unique PROTOCOL_ID=REFERENCE_JSON")
        used.add(protocol_id)
        cases.append((protocol_id, path))
    if not cases:
        raise EvaluationError("at least one --case is required")
    return cases


def _source_path(catalog: ProtocolCatalog, protocol_id: str) -> Path:
    entry = catalog.get_entry(protocol_id)
    pdf_object = catalog.store.get_pdf_object(entry.source_sha256)
    if pdf_object is None:
        raise EvaluationError("catalog source object is unavailable")
    return catalog.store.file_store.object_path(
        entry.source_sha256, expected_size=pdf_object.byte_size)


def analysis_plan(
    *, data_dir: Path, cases: Sequence[tuple[str, Path]], max_calls: int,
    max_usd: float, reserve_usd: float, input_rate: float, output_rate: float,
) -> dict[str, Any]:
    from voiney_lab.experiment_protocol_analysis import analysis_pieces
    from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf

    data_dir = _checked_copy(data_dir)
    store = initialize_protocol_store(ProtocolPersistenceSettings(True, data_dir))
    rows = []
    try:
        catalog = ProtocolCatalog(store)
        for protocol_id, reference in cases:
            source = _source_path(catalog, protocol_id)
            extraction = extract_protocol_pdf(source)
            rows.append({
                "protocol_id": protocol_id,
                "reference": str(reference),
                "pages": extraction.page_count,
                "source_characters": sum(len(page.text) for page in extraction.pages),
                "pieces": len(analysis_pieces(extraction) or ()),
            })
    finally:
        store.close()
    return {
        "cases": rows,
        "external_requests_this_dry_run": 0,
        "call_cap": max_calls,
        "reserved_usd_cap": max_usd,
        "reserve_usd_per_transport": reserve_usd,
        "input_usd_per_million_assumed": input_rate,
        "output_usd_per_million_assumed": output_rate,
        "rates_verified": False,
        "note": "calls may include automatic retry, requote, and fewer-item reanalysis",
    }


def _score_candidate(
    source: Path, reference: Path, candidates: Sequence[CallRecord], folder: Path,
) -> tuple[dict[str, Any] | None, int | None]:
    """Keep every raw candidate privately; score the last full valid response."""
    scored: tuple[dict[str, Any], int] | None = None
    for index, record in enumerate(candidates, start=1):
        if not record.content.strip():
            continue
        candidate = folder / f"candidate-call-{index}.json"
        with candidate.open("x", encoding="utf-8") as stream:
            stream.write(record.content)
            stream.write("\n")
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "score_extraction.py"),
             str(source), "--reference", str(reference), "--candidate", str(candidate)],
            cwd=ROOT, text=True, capture_output=True, timeout=180)
        try:
            payload = json.loads(completed.stdout)
        except ValueError:
            continue
        if completed.returncode == 0 and payload.get("scored") is True:
            scored = payload, index
    if scored is None:
        return None, None
    payload, index = scored
    with (folder / "score.json").open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return payload, index


def analysis_card_count(entry: Any | None) -> int | None:
    """Count review cards while keeping a failed/no-result run distinct."""
    if entry is None:
        return None
    if entry.review_pending is None:
        return 0
    return int(entry.review_pending.get("needs_review_count") or 0)


def analysis_accuracy_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate comparable extraction fields without making an execution verdict."""
    scored = [
        row["accuracy"] for row in rows if isinstance(row.get("accuracy"), dict)
    ]
    count = len(scored)

    def total(key: str) -> int:
        return sum(
            value for score in scored
            if isinstance((value := score.get(key)), int)
            and not isinstance(value, bool) and value >= 0
        )

    def labels(key: str) -> int:
        return sum(
            len(value) for score in scored
            if isinstance((value := score.get(key)), (list, tuple))
        )

    similarities = [
        float(value) for score in scored
        if isinstance((value := score.get("mean_text_similarity")), (int, float))
        and not isinstance(value, bool) and math.isfinite(value)
    ]
    return {
        "scored_protocols": count,
        "step_count_match_rate": (
            round(sum(score.get("step_count_matches") is True for score in scored)
                  / count, 4) if count else None),
        "order_match_rate": (
            round(sum(score.get("order_matches") is True for score in scored)
                  / count, 4) if count else None),
        "protocol_mean_text_similarity": (
            round(sum(similarities) / len(similarities), 4) if similarities else None),
        "reference_steps": total("reference_steps"),
        "candidate_steps": total("candidate_steps"),
        "missing_labels": labels("missing_labels"),
        "extra_labels": labels("extra_labels"),
        "steps_compared": total("steps_compared"),
        "steps_with_matching_values": total("steps_with_matching_values"),
        "steps_with_contradicted_values": total("steps_with_contradicted_values"),
        "steps_unscorable_on_values": total("steps_unscorable_on_values"),
    }


def execute_analysis(
    *, data_dir: Path, cases: Sequence[tuple[str, Path]], output_dir: Path,
    max_calls: int, max_usd: float, reserve_usd: float,
    input_rate: float, output_rate: float, timeout: float,
) -> dict[str, Any]:
    from scripts.sim.live import Budget
    from voiney_lab.experiment_protocol_analysis import OpenAICompatibleProtocolAnalysisModel

    data_dir = _checked_copy(data_dir)
    role = RoleModel.from_environment("analysis")
    if not role.configured:
        raise EvaluationError("VOINEY_LAB_ANALYSIS_PROVIDER is unset")
    if not role.has_key():
        raise EvaluationError(f"{role.api_key_setting} is unset")
    if role.reasoning not in {"low", "medium"}:
        raise EvaluationError("analysis comparison requires explicit low or medium reasoning")
    # score_extraction compares one complete provider response. Piece calls
    # are complete envelopes for only their page slice, while the merged
    # protocol exists only in the catalog; scoring the last piece as the
    # document would be false evidence. Reuse the zero-call plan and refuse
    # before any provider client is built.
    preflight = analysis_plan(
        data_dir=data_dir, cases=cases, max_calls=max_calls, max_usd=max_usd,
        reserve_usd=reserve_usd, input_rate=input_rate, output_rate=output_rate,
    )
    piecewise = [
        str(row["protocol_id"]) for row in preflight["cases"] if row["pieces"]
    ]
    if piecewise:
        raise EvaluationError(
            "score_extraction needs single-pass protocols; piecewise cases: "
            + ", ".join(piecewise))
    output_dir = _checked_report_path(output_dir)
    if output_dir.exists():
        raise EvaluationError("analysis output directory already exists")
    output_dir.mkdir(parents=True, mode=0o700)
    capture = _Capture(max_calls)
    budget = Budget(max_calls, max_usd, reserve_usd, output_dir / "transport.ledger.jsonl")
    store = initialize_protocol_store(ProtocolPersistenceSettings(True, data_dir))
    rows: list[dict[str, Any]] = []
    client = None
    try:
        catalog = ProtocolCatalog(store)
        with ExitStack() as stack:
            budget.guard(stack)
            client = build_model_client(
                role, asynchronous=False, timeout=timeout, capture=capture)
            model = OpenAICompatibleProtocolAnalysisModel(
                client, str(role.model), role.reasoning)
            for protocol_id, reference in cases:
                folder = output_dir / protocol_id
                folder.mkdir(mode=0o700)
                source = _source_path(catalog, protocol_id)
                before = len(capture.records)
                started = time.perf_counter()
                failure = None
                entry = None
                try:
                    analysis_id = f"model-eval-{secrets.token_hex(12)}"
                    catalog.request_analysis(
                        protocol_id, analysis_id, reanalysis=True)
                    entry = catalog.analyze(protocol_id, model, analysis_id=analysis_id)
                except Exception as exc:  # failure type only; provider text never leaves
                    failure = type(exc).__name__
                elapsed = (time.perf_counter() - started) * 1000
                records = capture.records[before:]
                score, scored_call = _score_candidate(
                    source, reference, records, folder)
                cards = analysis_card_count(entry)
                rows.append({
                    "protocol_id": protocol_id,
                    "analysis_status": entry.analysis_status if entry is not None else None,
                    "failure": failure,
                    "cards": cards,
                    "calls": len(records),
                    "elapsed_ms": round(elapsed, 1),
                    "scored_call_index": scored_call,
                    "accuracy": ({
                        key: score.get(key) for key in (
                            "reference_steps", "candidate_steps", "step_count_matches",
                            "order_matches", "missing_labels", "extra_labels",
                            "mean_text_similarity", "steps_compared",
                            "steps_with_matching_values", "steps_with_contradicted_values",
                            "steps_unscorable_on_values")
                    } if score is not None else None),
                })
    finally:
        store.close()
        if client is not None:
            client.close()
    report = {
        "schema": 1,
        "kind": "analysis_reasoning_comparison",
        "utc": datetime.now(timezone.utc).isoformat(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "role": {"provider": role.provider, "model": role.model,
                 "reasoning": role.reasoning},
        "rows": rows,
        "protocols": len(rows),
        "accuracy": analysis_accuracy_summary(rows),
        "cards": sum(row["cards"] or 0 for row in rows),
        "elapsed_ms": _percentiles(row["elapsed_ms"] for row in rows),
        "transport_requests": budget.calls,
        "reserved_usd": round(budget.usd, 4),
        "budget_stopped": budget.stopped,
        "caps": {"calls": max_calls, "usd": max_usd,
                 "reserve_usd_per_transport": reserve_usd},
        "calls": _capture_summary(
            capture, input_rate=input_rate, output_rate=output_rate),
        "standing": "reference_analysis_is_a_regression_baseline_not_ground_truth",
    }
    with (output_dir / "analysis_result.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def _caps(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-calls", type=int, required=True,
                        help="logical calls and guarded HTTP transports at most")
    parser.add_argument("--max-usd", type=float, required=True,
                        help="reserved transport budget in USD")
    parser.add_argument("--reserve-usd-per-request", type=float, required=True,
                        help="conservative USD reserved before every transport")
    parser.add_argument("--input-rate", type=float, required=True,
                        help="assumed USD per million input tokens")
    parser.add_argument("--output-rate", type=float, required=True,
                        help="assumed USD per million output tokens")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    sample = commands.add_parser(
        "translation-sample", help="make one source-identified balanced manifest; no call")
    sample.add_argument("--data-dir", type=Path, required=True)
    sample.add_argument("--group", action="append", default=[],
                        help="NAME=PROTOCOL_ID[,PROTOCOL_ID]")
    sample.add_argument("--sample-size", type=int, default=60)
    sample.add_argument("--seed", type=int, default=1028)
    sample.add_argument("--output", type=Path, required=True)

    translation = commands.add_parser(
        "translation", help="plan or execute the fixed translation sample")
    translation.add_argument("--data-dir", type=Path, required=True)
    translation.add_argument("--manifest", type=Path, required=True)
    translation.add_argument("--mode", choices=("batch", "spot"), required=True)
    translation.add_argument("--output-dir", type=Path, required=True)
    translation.add_argument("--execute", action="store_true")
    _caps(translation)

    analysis = commands.add_parser(
        "analysis", help="plan or execute catalog reanalysis against references")
    analysis.add_argument("--data-dir", type=Path, required=True)
    analysis.add_argument("--case", action="append", default=[],
                          help="PROTOCOL_ID=REFERENCE_JSON")
    analysis.add_argument("--output-dir", type=Path, required=True)
    analysis.add_argument("--timeout", type=float, default=600)
    analysis.add_argument("--execute", action="store_true")
    _caps(analysis)
    return parser


def _validate_caps(args: argparse.Namespace) -> None:
    if args.max_calls < 1 or not _finite_positive(
            args.max_usd, args.reserve_usd_per_request,
            args.input_rate, args.output_rate):
        raise EvaluationError("call/cost caps and assumed rates must be positive")
    if args.reserve_usd_per_request > args.max_usd:
        raise EvaluationError("one request reservation exceeds the condition's USD cap")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "translation-sample":
            output = _checked_report_path(args.output)
            if output.exists():
                raise EvaluationError("sample manifest already exists")
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = make_translation_sample(
                args.data_dir, _parse_groups(args.group),
                size=args.sample_size, seed=args.seed)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(json.dumps({
                "manifest": str(output), "sample_size": payload["sample_size"],
                "external_requests": 0}, ensure_ascii=False))
            return 0
        _validate_caps(args)
        if args.command == "translation":
            kwargs = dict(
                data_dir=args.data_dir, manifest=args.manifest, mode=args.mode,
                max_calls=args.max_calls, max_usd=args.max_usd,
                reserve_usd=args.reserve_usd_per_request,
                input_rate=args.input_rate, output_rate=args.output_rate)
            payload = (execute_translation(output_dir=args.output_dir, **kwargs)
                       if args.execute else translation_plan(**kwargs))
        else:
            if not 30 <= args.timeout <= 3600:
                raise EvaluationError("analysis timeout must be 30-3600 seconds")
            cases = _analysis_cases(args.case)
            kwargs = dict(
                data_dir=args.data_dir, cases=cases, max_calls=args.max_calls,
                max_usd=args.max_usd, reserve_usd=args.reserve_usd_per_request,
                input_rate=args.input_rate, output_rate=args.output_rate)
            payload = (execute_analysis(
                output_dir=args.output_dir, timeout=args.timeout, **kwargs)
                if args.execute else analysis_plan(**kwargs))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # only type/settings leave; provider bodies do not
        from scripts.sim.live import failure_settings

        print(json.dumps({
            "error": type(exc).__name__, "settings": failure_settings(exc),
        }), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
