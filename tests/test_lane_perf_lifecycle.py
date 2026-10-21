"""Interrupted OCR and model construction failures remain visible and retryable."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import server
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, self.root / "store")
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"VOINEY_LAB_WORKSPACE_ENABLED": "false"}).start()
        patch.object(server, "_open_protocol_catalog", self.open_catalog).start()
        patch.dict(server._PROTOCOL_ANALYSIS_TASKS, {}, clear=True).start()
        patch.dict(server._PROTOCOL_OCR_TASKS, {}, clear=True).start()

    def open_catalog(self):
        store = initialize_protocol_store(self.settings)
        return ProtocolCatalog(store), store

    def register(self, *, scanned: bool = False) -> str:
        source = self.root / "source.pdf"
        write_text_pdf(source, None if scanned else "Fictional protocol. 1. Add buffer to tube.", title="Lifecycle")
        catalog, store = self.open_catalog()
        try:
            return catalog.register(source, source_filename="source.pdf", media_type="application/pdf").entry.protocol_id
        finally:
            store.close()

    def abandoned_ocr(self, protocol_id: str) -> None:
        catalog, store = self.open_catalog()
        try:
            store.append_event("ocr-requested-interrupted", protocol_id, 1, "protocol_ocr_requested", {"ocr_id": "interrupted", "status": "in_progress"})
        finally:
            store.close()

    async def test_model_constructor_value_error_ends_background_request(self) -> None:
        protocol_id = self.register()
        with patch.object(server, "_protocol_analysis_model", side_effect=ValueError("private configuration detail")):
            await server._begin_background_analysis(protocol_id, principal=None)
            task = server._PROTOCOL_ANALYSIS_TASKS.get(protocol_id)
            if task is not None:
                await task
        status = server.get_protocol_analysis_status(protocol_id)
        self.assertEqual(status["state"], "analysis_failed")
        self.assertEqual(status["failure_code"], "provider_configuration_missing")
        self.assertNotIn("private configuration detail", str(status))

    async def test_ocr_worker_keeps_analysis_status_polling_alive(self) -> None:
        protocol_id = self.register(scanned=True)
        self.abandoned_ocr(protocol_id)
        future = asyncio.get_running_loop().create_future()
        server._PROTOCOL_OCR_TASKS[protocol_id] = future
        try:
            status = server.get_protocol_analysis_status(protocol_id)
            self.assertEqual(status["state"], "ocr_in_progress")
            self.assertTrue(status["worker_running"])
        finally:
            future.cancel()

    async def test_startup_recovers_abandoned_ocr_and_duplicate_upload_can_retry(self) -> None:
        protocol_id = self.register(scanned=True)
        self.abandoned_ocr(protocol_id)
        with (
            patch.object(server, "_protocol_store_settings", return_value=self.settings),
            patch.object(server, "_public_protocol_catalog_entries", return_value=([], self.settings, None)),
        ):
            await asyncio.to_thread(server.log_protocol_catalog_runtime_configuration)
        status = server.get_protocol_ocr_status(protocol_id)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["failure_code"], "protocol_ocr_interrupted")
        catalog, store = self.open_catalog()
        try:
            duplicate = catalog.register(self.root / "source.pdf", source_filename="source.pdf", media_type="application/pdf")
            self.assertTrue(duplicate.deduplicated)
            self.assertEqual(duplicate.entry.analysis_status, "ocr_failed")
            with patch.object(server, "_protocol_ocr_provider", return_value=object()), patch.object(server, "_schedule_ocr_then_analysis") as schedule:
                response = server._automatic_ocr_on_upload(catalog, duplicate.entry)
                self.assertTrue(response["automatic"])
                schedule.assert_called_once()
        finally:
            store.close()
