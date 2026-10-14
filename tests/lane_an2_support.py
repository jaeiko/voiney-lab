"""Fakes shared by the lane AN2 provider tests: a google-genai client that plays
one outcome per call, an SDK error with a status, and a patched clock.

Contract-test fakes only -- no request leaves this machine.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

from voiney_lab import model_providers
from voiney_lab.model_providers import RoleModel, chat_client

#: Three retries, waits capped at 30 s (base step 2 s is the default).
ENV = {"VOINEY_LAB_PROVIDER_RETRY_MAX": "3", "VOINEY_LAB_PROVIDER_RETRY_MAX_WAIT_SECONDS": "30"}

MESSAGES = [
    {"role": "system", "content": "STANDING INSTRUCTIONS"},
    {"role": "user", "content": "PRIVATE PAGE TEXT"},
]


class StatusError(Exception):
    """An SDK error: a status, and optionally the response headers and details."""

    def __init__(self, status: int, *, headers: dict | None = None, details: Any = None) -> None:
        super().__init__(f"status {status}")
        self.status_code = status
        self.code = status
        if headers is not None:
            self.response = SimpleNamespace(headers=headers)
        if details is not None:
            self.details = details


def gemini_reply(
    text: str = '{"ok": true}', *, finish: str | None = "STOP", thought: str | None = None,
    parts: list | None = None,
) -> SimpleNamespace:
    """A Gemini reply: ``text`` (empty means no text part at all), an optional
    thought part, and a finish reason name (None leaves it unset)."""

    if parts is None:
        parts = []
        if thought:
            parts.append(SimpleNamespace(thought=True, text=thought, function_call=None))
        if text:
            parts.append(SimpleNamespace(thought=False, text=text, function_call=None))
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts))
    if finish is not None:
        candidate.finish_reason = SimpleNamespace(name=finish)
    return SimpleNamespace(
        model_version="gemini-test",
        candidates=[candidate],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0,
            cached_content_token_count=0),
    )


class SequencedGemini:
    """A fake google-genai client that plays one outcome per call, in order."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.models = SimpleNamespace(generate_content=self._generate)
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._agenerate))

    def _next(self, params: dict[str, Any]) -> Any:
        self.requests.append(params)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def _generate(self, **params: Any) -> Any:
        return self._next(params)

    async def _agenerate(self, **params: Any) -> Any:
        return self._next(params)


def client(fake: Any, role: str = "analysis", *, asynchronous: bool = False,
           environment: dict | None = None) -> Any:
    """A Google client for ``role`` over ``fake``; the retry settings come
    from ``environment`` (``ENV`` unless given), as in production."""

    return chat_client(
        RoleModel(role, "google", "gemini-3.8-flash", "low"),
        asynchronous=asynchronous, sdk_client=fake,
        environment=ENV if environment is None else environment,
    )


class Clock:
    """Replaces the adapter's sleep and jitter; remembers every wait."""

    def __init__(self, jitter: float = 1.0) -> None:
        self.waits: list[float] = []
        self._jitter = jitter

    def __enter__(self) -> "Clock":
        async def asleep(seconds: float) -> None:
            self.waits.append(seconds)

        self._patches = [
            mock.patch.object(model_providers, "_sleep", self.waits.append),
            mock.patch.object(model_providers, "_async_sleep", asleep),
            mock.patch.object(model_providers, "_jitter", lambda: self._jitter),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        for patch in self._patches:
            patch.stop()




def call(client: Any) -> Any:
    return client.chat.completions.create(model="gemini-3.8-flash", messages=MESSAGES)
