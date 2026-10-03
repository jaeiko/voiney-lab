"""A fake streaming chat-completions client for the lane R router tests.

It answers ``client.chat.completions.create(**kwargs)`` with an async stream
of chunks shaped like the OpenAI SDK's (``choices[0].delta.content`` /
``delta.tool_calls``, a last chunk with ``usage``), from a script the test
gives. No network; every test that uses it is offline.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any


def _chunk(*, content: str | None = None, tool_calls: list | None = None, usage=None):
    choices = [] if content is None and tool_calls is None else [
        SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))
    ]
    return SimpleNamespace(model="fake-router-model", choices=choices, usage=usage)


def _usage(prompt: int = 1200, completion: int = 40):
    return SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        total_tokens=prompt + completion, prompt_tokens_details=None,
    )


def answer_reply(
    spoken: str,
    *,
    display: str = "",
    source_kind: str = "pdf",
    evidence_ids: tuple[str, ...] = (),
    outside_pdf_term: str | None = None,
) -> list:
    """The chunks of an answer: the JSON object, streamed in two pieces."""

    text = json.dumps({
        "spoken": spoken, "display": display, "source_kind": source_kind,
        "evidence_ids": list(evidence_ids), "outside_pdf_term": outside_pdf_term,
    }, ensure_ascii=False)
    half = len(text) // 2
    return [_chunk(content=text[:half]), _chunk(content=text[half:]), _chunk(usage=_usage())]


def text_reply(text: str) -> list:
    return [_chunk(content=text), _chunk(usage=_usage())]


def tool_reply(*calls: tuple[str, dict[str, Any] | str]) -> list:
    """The chunks of one or more tool calls, arguments streamed in two pieces."""

    chunks = []
    for index, (name, arguments) in enumerate(calls):
        raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
        half = len(raw) // 2
        chunks.append(_chunk(tool_calls=[SimpleNamespace(
            index=index, function=SimpleNamespace(name=name, arguments=raw[:half]),
        )]))
        chunks.append(_chunk(tool_calls=[SimpleNamespace(
            index=index, function=SimpleNamespace(name=None, arguments=raw[half:]),
        )]))
    chunks.append(_chunk(usage=_usage()))
    return chunks


class _Stream:
    def __init__(self, chunks: list, delay: float) -> None:
        self._chunks = chunks
        self._delay = delay

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk


class FakeRouterClient:
    """Replies with the scripted chunk lists in order; records each request.

    A script item may be an exception instance (raised by ``create``) or a
    ``(chunks, delay_seconds)`` pair for a slow stream.
    """

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any):
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        chunks, delay = item if isinstance(item, tuple) else (item, 0.0)
        return _Stream(chunks, delay)
