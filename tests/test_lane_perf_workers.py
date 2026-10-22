"""Heavy protocol I/O must yield to other requests on the production ASGI app."""

from __future__ import annotations

import asyncio
import contextvars
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
    async def test_repeated_upload_cancellation_keeps_source_and_store_alive(self) -> None:
        entered, released = threading.Event(), threading.Event()
        calls: list[tuple[str, int]] = []
        request_marker = contextvars.ContextVar("perf_request_marker", default=False)
        request_marker.set(True)
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.pdf"
            source.write_bytes(b"upload")

            def work(catalog: object) -> None:
                calls.append(("work", threading.get_ident()))
                entered.set()
                released.wait(2)
                calls.append(("source-present" if source.is_file() else "source-missing", threading.get_ident()))

            def open_catalog():
                self.assertTrue(request_marker.get(), "worker lost its request identity context")
                calls.append(("open", threading.get_ident()))
                return object(), unittest.mock.Mock(close=lambda: calls.append(("close", threading.get_ident())))

            with patch.object(server, "_open_protocol_catalog", side_effect=open_catalog):
                worker = asyncio.create_task(server._catalog_in_worker(work))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2), "worker did not start")
                    for _ in range(2):
                        worker.cancel()
                        await asyncio.sleep(0.01)
                    self.assertFalse(worker.done(), "repeated cancel detached a still-running source worker")
                finally:
                    released.set()
                    await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual([name for name, _ in calls], ["open", "work", "source-present", "close"])
        self.assertEqual(len({thread for _, thread in calls}), 1)
        self.assertNotEqual(calls[0][1], threading.get_ident())

    async def test_cancelled_worker_failure_keeps_cancellation_outcome(self) -> None:
        entered, released = threading.Event(), threading.Event()

        def work(catalog: object) -> None:
            entered.set()
            released.wait(2)
            raise ValueError("private source detail")

        with patch.object(server, "_open_protocol_catalog", return_value=(object(), unittest.mock.Mock())):
            worker = asyncio.create_task(server._catalog_in_worker(work))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2), "worker did not start")
                worker.cancel()
                await asyncio.sleep(0)
            finally:
                released.set()
            with self.assertRaises(asyncio.CancelledError):
                await worker

    async def test_cancelled_spool_waits_for_write_and_closes_off_loop(self) -> None:
        entered, released = threading.Event(), threading.Event()
        closed: list[int] = []

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

            def write(self, chunk: bytes) -> None:
                entered.set()
                released.wait(2)
                self.assert_open = not closed

            def close(self) -> None:
                closed.append(threading.get_ident())

        class Request:
            async def stream(self):
                yield b"upload"

        stream = Stream()
        with patch.object(Path, "open", return_value=stream):
            worker = asyncio.create_task(server._spool_protocol_pdf_upload(Request(), Path("unused")))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2), "write did not start")
                for _ in range(2):
                    worker.cancel()
                    await asyncio.sleep(0.01)
                self.assertFalse(worker.done(), "spool closed a file while its write was still running")
                self.assertFalse(closed)
            finally:
                released.set()
                await asyncio.gather(worker, return_exceptions=True)
        self.assertTrue(stream.assert_open)
        self.assertEqual(len(closed), 1)
        self.assertNotEqual(closed[0], threading.get_ident())

    async def test_cancelled_upload_worker_finishes_before_source_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.pdf"
            source.write_bytes(b"test source owned by the upload")
            entered = threading.Event()
            released = threading.Event()
            observed: list[bytes] = []

            def work(catalog):
                entered.set()
                released.wait(1)
                observed.append(source.read_bytes())

            with patch.object(server, "_open_protocol_catalog", return_value=(object(), unittest.mock.Mock())):
                worker = asyncio.create_task(server._catalog_in_worker(work))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2), "worker did not start")
                worker.cancel()
                await asyncio.sleep(0)
                self.assertFalse(worker.done())
                released.set()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
            self.assertEqual(observed, [b"test source owned by the upload"])

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
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2), "registration did not start")
                    health = await client.get("/healthz")
                    self.assertEqual(health.status_code, 200)
                    self.assertFalse(upload.done(), "upload held the event loop for its whole registration")
                    self.assertNotIn(loop_thread, register_threads)
                    self.assertEqual((await upload).status_code, 201)

    async def test_successful_upload_survives_ocr_status_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            data = Path(root)
            body = write_text_pdf(data / "source.pdf", "Fictional protocol. 1. Add buffer to tube.", title="Perf status")

            def open_catalog():
                store = initialize_protocol_store(ProtocolPersistenceSettings(True, data / "store"))
                return ProtocolCatalog(store), store

            with (
                patch.dict(os.environ, {"VOINEY_LAB_AUTH_MODE": "dev", "VOINEY_LAB_WORKSPACE_ENABLED": "false"}),
                patch.object(server, "_open_protocol_catalog", side_effect=open_catalog),
                patch.object(ProtocolCatalog, "ocr_status", side_effect=ValueError("private source detail")),
                patch.object(server, "_protocol_ocr_provider", side_effect=AssertionError("must not start OCR with unknown status")),
            ):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://testserver") as client:
                    response = await client.post("/api/protocols?filename=perf.pdf", content=body, headers={"content-type": "application/pdf"})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["ocr"], {"state": "unknown", "automatic": False, "blocked": "protocol_ocr_status_failed"})
            self.assertNotIn("private source detail", response.text)


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
