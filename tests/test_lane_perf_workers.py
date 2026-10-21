"""Heavy protocol I/O must yield to other requests on the production ASGI app."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from tests.test_protocol_catalog import write_text_pdf
from tests.lane_n_support import VoiceNotesHarness
from voiney_lab import server
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog


class WorkerBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_yields_to_health_and_other_loop_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            data = Path(root)
            body = write_text_pdf(data / "source.pdf", "Fictional protocol. 1. Add buffer to tube.", title="Perf")
            entered = threading.Event()
            real_register = ProtocolCatalog.register
            loop_thread = threading.get_ident()
            register_threads: list[int] = []

            def register(catalog, *args, **kwargs):
                register_threads.append(threading.get_ident())
                entered.set()
                time.sleep(0.3)
                return real_register(catalog, *args, **kwargs)

            def open_catalog():
                store = initialize_protocol_store(ProtocolPersistenceSettings(True, data / "store"))
                return ProtocolCatalog(store), store

            with (
                patch.dict(os.environ, {"VOINEY_LAB_AUTH_MODE": "dev", "VOINEY_LAB_WORKSPACE_ENABLED": "false"}),
                patch.object(server, "_open_protocol_catalog", open_catalog),
                patch.object(ProtocolCatalog, "register", register),
            ):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver") as client:
                    upload = asyncio.create_task(client.post("/api/protocols?filename=perf.pdf", content=body, headers={"content-type": "application/pdf"}))
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                    health = await client.get("/healthz")
                    self.assertEqual(health.status_code, 200)
                    self.assertFalse(upload.done(), "upload held the event loop for its whole registration")
                    self.assertNotIn(loop_thread, register_threads)
                    self.assertEqual((await upload).status_code, 201)


class ConnectionPreparationTests(VoiceNotesHarness, unittest.TestCase):
    def test_connection_loads_fixture_outside_event_loop(self) -> None:
        real_to_thread = asyncio.to_thread
        loop_thread = threading.get_ident()

        def load(*args):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            return self.fixture

        socket = self.voice(
            lambda socket, listener, say: asyncio.sleep(0),
            patch.object(server.asyncio, "to_thread", side_effect=real_to_thread),
            patch.object(server, "load_curated_protocol_fixture", side_effect=load),
        )
        self.assertTrue(any(event["type"] == "session.ready" for event in socket.sent))
