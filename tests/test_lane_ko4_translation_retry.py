"""Retranslation sends are bounded at the real CLI/provider boundary (no network)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from tests import test_lane_ko2_retranslate_script as script_tests
from tests.test_revision_translations import good_korean, rich_fixture
from voiney_lab import model_providers as mp
from voiney_lab import protocol_translation as pt


class StatusError(Exception):
    def __init__(self, code: int, retry_after: float | None = None) -> None:
        super().__init__("private-provider-message")
        self.code = code
        self.response = SimpleNamespace(headers=(
            {} if retry_after is None else {"Retry-After": str(retry_after)}))


class Gemini:
    def __init__(self, error: Exception, phase: str, *, always: bool = False) -> None:
        self.error = error
        self.phase = phase
        self.always = always
        self.requests: list[dict[str, Any]] = []
        self.failed = False
        self.closed = 0
        self.aio = SimpleNamespace(
            models=SimpleNamespace(generate_content=self.create), aclose=self.close)

    async def create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        payload = json.loads(request["contents"][-1]["parts"][0]["text"])
        phase = "batch" if "items" in payload else "glossary"
        if phase == self.phase and (self.always or not self.failed):
            self.failed = True
            raise self.error
        body = {"items": [
            {"id": item["id"], "korean": good_korean(item["source_text"])}
            for item in payload["items"]]} if phase == "batch" else {"entries": []}
        return SimpleNamespace(candidates=[SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text=json.dumps(body))]),
            finish_reason="STOP")])

    async def close(self) -> None:
        self.closed += 1


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    instance = script_tests.RetranslateScriptTests("runTest")
    instance.setUp()
    monkeypatch.setenv("VOINEY_LAB_TRANSLATION_PROVIDER", "google")
    monkeypatch.setenv("VOINEY_LAB_TRANSLATION_MODEL", "offline-model")
    monkeypatch.setenv("GEMINI_API_KEY", "offline")
    # Tests assert requested waits; they never wait in real time.
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(mp, "_jitter", lambda: 1.0)
    instance.waits = waits
    yield instance
    instance.doCleanups()


def install_sdk(harness: Any, monkeypatch: pytest.MonkeyPatch, sdk: Gemini) -> None:
    def client(role: mp.RoleModel, **kwargs: Any) -> Any:
        assert role.provider == "google"
        assert kwargs["max_retries"] == 0
        assert kwargs["retry_policy"].max_retries == 0
        return mp.chat_client(role, sdk_client=sdk, **kwargs)

    monkeypatch.setattr(harness.script, "chat_client", client)


@pytest.mark.parametrize("phase", ["glossary", "batch"])
@pytest.mark.parametrize(("error", "kind"), [
    (StatusError(429), "rate_limited"),
    (StatusError(500), "server"),
    (StatusError(502), "server"),
    (StatusError(503), "server"),
    (StatusError(504), "timeout"),
    (StatusError(408), "timeout"),
    (TimeoutError("private-provider-message"), "timeout"),
    (mp.ModelProviderError("google", "connection"), "connection"),
])
def test_cli_retries_transient_sends_and_reports_their_kinds(
    harness: Any, monkeypatch: pytest.MonkeyPatch, error: Exception, kind: str, phase: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sdk = Gemini(error, phase)
    install_sdk(harness, monkeypatch, sdk)
    report_dir = harness.root / "result"
    with caplog.at_level(logging.WARNING):
        status, out, _ = harness.main(
            "--data-dir", str(harness.data), "--run", "--max-calls", "8",
            "--report-dir", str(report_dir))
    result = json.loads((report_dir / "retranslate_result.json").read_text())[0]
    assert status == 0
    assert len(sdk.requests) == result["calls_made"] == 3
    assert result["failed_calls"] == 1
    assert result["failure_kinds"] == {kind: 1}
    assert result["rows_made"] == result["rows_passed"] > 0
    assert harness.waits == [2.0]
    assert sdk.closed == len(sdk.requests)
    index = 0 if phase == "glossary" else 1
    assert sdk.requests[index] == sdk.requests[index + 1]
    assert "private-provider-message" not in out + caplog.text


@pytest.mark.parametrize("cap", [0, 1, 2, 3, 4, 5])
def test_cli_cap_includes_glossary_retries_and_never_counts_an_unsent_call(
    harness: Any, monkeypatch: pytest.MonkeyPatch, cap: int,
) -> None:
    sdk = Gemini(StatusError(429), "glossary", always=True)
    install_sdk(harness, monkeypatch, sdk)
    result_dir = harness.root / "result"
    status, _, _ = harness.main(
        "--data-dir", str(harness.data), "--run", "--max-calls", str(cap),
        "--report-dir", str(result_dir))
    result = json.loads((result_dir / "retranslate_result.json").read_text())[0]
    assert status == 0
    assert len(sdk.requests) == result["calls_made"] == min(cap, 4)
    assert result["failed_calls"] == min(cap, 3)
    assert result["failure_kinds"] == ({"rate_limited": min(cap, 3)} if cap else {})
    assert harness.waits == [2.0, 4.0][:max(0, min(cap - 1, 2))]
    assert len(harness.rows()) == (1 if cap <= 3 else 1 + result["rows_made"])


@pytest.mark.parametrize("status", [400, 401, 402, 403])
def test_cli_does_not_retry_a_permanent_failure(
    harness: Any, monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    sdk = Gemini(StatusError(status), "glossary", always=True)
    install_sdk(harness, monkeypatch, sdk)
    result_dir = harness.root / "result"
    harness.main("--data-dir", str(harness.data), "--run", "--report-dir", str(result_dir))
    result = json.loads((result_dir / "retranslate_result.json").read_text())[0]
    assert len(sdk.requests) == 2  # failed glossary, then one independent sentence batch
    assert result["failed_calls"] == 1
    assert harness.waits == []


def test_cli_does_not_resend_a_safety_block(
    harness: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = Gemini(mp.ModelProviderError("google", "blocked"), "batch", always=True)
    install_sdk(harness, monkeypatch, sdk)
    result_dir = harness.root / "result"
    harness.main("--data-dir", str(harness.data), "--run", "--report-dir", str(result_dir))
    result = json.loads((result_dir / "retranslate_result.json").read_text())[0]
    assert len(sdk.requests) == result["calls_made"] == 2
    assert result["failure_kinds"] == {"blocked": 1}
    assert result["rows_made"] == 0
    assert harness.waits == []
    assert len(harness.rows()) == 1


@pytest.mark.parametrize("retry_after", [7.0, 31.0])
def test_cli_honours_retry_after_within_its_wait_bound(
    harness: Any, monkeypatch: pytest.MonkeyPatch, retry_after: float,
) -> None:
    sdk = Gemini(StatusError(429, retry_after), "glossary", always=True)
    install_sdk(harness, monkeypatch, sdk)
    harness.main("--data-dir", str(harness.data), "--run")
    assert harness.waits == ([7.0, 7.0] if retry_after == 7.0 else [])
    assert len(sdk.requests) == (4 if retry_after == 7.0 else 2)


def test_malformed_batches_take_the_existing_split_path_without_poisoning_stored_rows() -> None:
    sent: list[int] = []

    async def create(**request: Any) -> SimpleNamespace:
        payload = json.loads(request["messages"][-1]["content"])
        items = payload["items"]
        sent.append(len(items))
        text = "broken JSON" if len(items) > 1 else json.dumps({"items": [
            {"id": item["id"], "korean": good_korean(item["source_text"])} for item in items]})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    report = asyncio.run(pt.generate_revision_translations(
        rich_fixture(), pt.openai_batch_translator(lambda: client, "fake"), model="fake"))
    assert sent[:4] == [8, 4, 2, 1]
    assert len(report.records) == report.passed == 10
    assert len({row.fact_key for row in report.records}) == 10


@pytest.mark.parametrize("text", ["", "{}", "[]", '{"items": null}',
    '{"items": [{"id":"wrong","korean":"옮겨요."}]}'])
def test_invalid_envelopes_cannot_store_missing_rows(text: str) -> None:
    async def create(**request: Any) -> SimpleNamespace:
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    report = asyncio.run(pt.generate_revision_translations(
        rich_fixture(), pt.openai_batch_translator(lambda: client, "fake"), model="fake", max_calls=3))
    assert report.records == []
    assert report.failed_calls == 3


def test_broken_structure_uses_the_existing_value_preserving_repair() -> None:
    unit = pt.TranslationUnit("step-1/current_step", "step", "Add PBS.")
    # An outer closing brace is missing, the shape already handled by the analysis parser.
    text = '{"items":[{"id":"step-1/current_step","korean":"PBS를 넣어요."}],"meta":{}'

    async def create(**request: Any) -> SimpleNamespace:
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = asyncio.run(pt.openai_batch_translator(lambda: client, "fake")([unit], {}))
    assert result.translations == {unit.fact_key: "PBS를 넣어요."}


def test_cli_safe_failure_codes_do_not_include_arbitrary_exception_text() -> None:
    script = script_tests._load_script()
    assert script.translation_failure_kind(RuntimeError("private-provider-message")) == "other"
    assert script.translation_failure_kind(mp.ModelProviderError("google", "blocked")) == "blocked"
