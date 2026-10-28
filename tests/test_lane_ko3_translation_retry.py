"""Split timed-out translation batches; bounded calls and loop-owned clients."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Sequence

import pytest
import httpx
from openai import APITimeoutError

from tests.test_revision_translations import rich_fixture, good_korean
from tests.test_lane_ko2_retranslate_script import _load_script
from voiney_lab import protocol_translation as pt
from voiney_lab.model_providers import ModelProviderError


@pytest.mark.parametrize('error', [TimeoutError(), ModelProviderError('google', 'timeout'),
                                  APITimeoutError(request=httpx.Request('POST', 'https://offline.invalid'))])
def test_timeout_batches_are_split_and_successful_halves_are_saved(error: Exception) -> None:
    sent = []
    async def translate(batch: Sequence[pt.TranslationUnit], context: dict[str, Any]) -> pt.BatchResult:
        sent.append(len(batch))
        if len(batch) > 2:
            raise error
        return pt.BatchResult({unit.fact_key: good_korean(unit.source_text) for unit in batch})
    saved = []
    report = asyncio.run(pt.generate_revision_translations(
        rich_fixture(), translate, model='fake', on_batch=lambda rows: saved.extend(rows)))
    assert sent[:3] == [8, 4, 2]
    assert len(saved) == 10
    assert len({row.fact_key for row in saved}) == 10
    assert report.failed_calls > 0


def test_split_retries_respect_the_script_call_cap() -> None:
    script = _load_script()
    sent = []
    async def translate(batch: Sequence[pt.TranslationUnit], context: dict[str, Any]) -> pt.BatchResult:
        sent.append(len(batch))
        if len(batch) > 1:
            raise TimeoutError()
        return pt.BatchResult({unit.fact_key: good_korean(unit.source_text) for unit in batch})
    counter = {'calls': 0}
    report = asyncio.run(pt.generate_revision_translations(
        rich_fixture(), script._counted(translate, counter, 4), model='fake'))
    assert sent == [8, 4, 2, 1]
    assert counter['calls'] == 4
    assert len(report.records) == 1


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('glossary', [False, True])
def test_client_is_closed_in_its_own_loop_on_success_or_timeout(cancel: bool, glossary: bool) -> None:
    class Client:
        def __init__(self) -> None:
            self.closed = False
            self.loop = asyncio.get_running_loop()
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        async def create(self, **kwargs: Any) -> SimpleNamespace:
            if cancel:
                await asyncio.sleep(60)
            payload = json.loads(kwargs['messages'][-1]['content'])
            body = {'items': [{'id': item['id'], 'korean': '옮겨요.'}
                              for item in payload['items']]} if 'items' in payload else {'entries': []}
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))])
        async def close(self) -> None:
            assert asyncio.get_running_loop() is self.loop
            self.closed = True
    clients = []
    def factory() -> Client:
        client = Client()
        clients.append(client)
        return client
    async def run() -> None:
        if glossary:
            call = pt.openai_glossary_maker(factory, 'fake', timeout=.01)
            args = (pt.protocol_context(rich_fixture()),)
        else:
            call = pt.openai_batch_translator(factory, 'fake', timeout=.01)
            args = (pt.translation_units(rich_fixture())[:1], {})
        if cancel:
            with pytest.raises(TimeoutError):
                await call(*args)
        else:
            await call(*args)
        assert all(client.closed for client in clients)
    asyncio.run(run())


def test_google_facade_closes_its_async_sdk_before_the_loop_ends() -> None:
    closed = []
    async def create(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"items":[]}'))])
    async def close() -> None:
        closed.append(asyncio.get_running_loop())
    sdk = SimpleNamespace(aio=SimpleNamespace(aclose=close))
    facade = SimpleNamespace(_backend=SimpleNamespace(client=sdk),
                             chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    async def run() -> None:
        await pt.openai_batch_translator(lambda: facade, 'fake')([], {})
        assert closed == [asyncio.get_running_loop()]
    asyncio.run(run())
