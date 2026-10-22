"""Concurrent worker-prepared translation batches retain every checked row and stale fences."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import AsyncMock, patch

from tests.protocol_vocabulary_support import miniprep_fixture
from voiney_lab import server
from voiney_lab.curated_protocol import CuratedProtocolSession
from voiney_lab.protocol_translation import translation_revision_key


class TranslationPushTests(unittest.IsolatedAsyncioTestCase):
    async def test_crossing_batches_merge_instead_of_dropping_a_checked_batch(self) -> None:
        fixture = miniprep_fixture()
        curated = CuratedProtocolSession(fixture)
        session = SimpleNamespace(curated_protocol_session=curated, accepted_configuration_id=1, active=True)
        subscriber = server._TranslationSubscriber(session, SimpleNamespace(text=AsyncMock()), asyncio.get_running_loop())
        entered, released = threading.Event(), threading.Event()
        before = curated._checkpoint()

        def prepare(original, rows, glossary):
            if rows == ["first"]:
                entered.set()
                released.wait(2)
            return replace(original, machine_localizations={**(original.machine_localizations or {}), rows[0]: rows[0]})

        with patch.object(server, "with_stored_translations", side_effect=prepare):
            first = asyncio.create_task(server._apply_translations(subscriber, translation_revision_key(fixture), ["first"]))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2), "translation worker did not start")
                second = asyncio.create_task(server._apply_translations(subscriber, translation_revision_key(fixture), ["second"]))
                await asyncio.sleep(0.02)
            finally:
                released.set()
            await asyncio.gather(first, second)
        self.assertEqual(curated.fixture.machine_localizations, {"first": "first", "second": "second"})
        self.assertEqual(curated._checkpoint(), before)

    async def test_reconfigured_session_drops_inflight_translation_without_redraw(self) -> None:
        fixture = miniprep_fixture()
        curated = CuratedProtocolSession(fixture)
        session = SimpleNamespace(curated_protocol_session=curated, accepted_configuration_id=1, active=True)
        sender = SimpleNamespace(text=AsyncMock())
        subscriber = server._TranslationSubscriber(session, sender, asyncio.get_running_loop())
        entered, released = threading.Event(), threading.Event()

        def prepare(original, rows, glossary):
            entered.set()
            released.wait(2)
            return replace(original, machine_localizations={"first": "first"})

        with patch.object(server, "with_stored_translations", side_effect=prepare):
            worker = asyncio.create_task(server._apply_translations(subscriber, translation_revision_key(fixture), ["first"]))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2), "translation worker did not start")
                session.accepted_configuration_id = 2
            finally:
                released.set()
            await worker
        self.assertIs(curated.fixture, fixture)
        sender.text.assert_not_awaited()
