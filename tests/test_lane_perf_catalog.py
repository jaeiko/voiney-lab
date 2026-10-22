"""Cold protocol lists are projections of stored metadata, scoped before building rows."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os

from fastapi.testclient import TestClient

from tests.test_protocol_catalog import analysis_draft, write_text_pdf
from voiney_lab import server
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.experiment_protocol_store import ProtocolSerializationError
from voiney_lab.protocol_catalog import ProtocolCatalog


class CatalogMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = ProtocolPersistenceSettings(True, root / "catalog")
        self.store = initialize_protocol_store(self.settings)
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)
        self.entries = []
        for number in range(2):
            source = root / f"source-{number}.pdf"
            write_text_pdf(source, f"Title {number}\nSection preparation\n1. Add solution.\nWear gloves.", title=f"Title {number}")
            self.entries.append(self.catalog.register(source, source_filename=source.name, media_type="application/pdf").entry)

    def test_http_list_reuses_verified_analysis_and_sees_a_new_revision(self) -> None:
        entry = self.entries[0]
        source = self.catalog.source_pdf(entry.protocol_id)[0]
        draft = analysis_draft(source, entry.protocol_id, "Title 0")
        self.store.append_analysis_revision(entry.protocol_id, 1, "perf-analysis-first", draft.protocol, draft.readiness, draft.capability_policy_id)
        original = type(self.store).get_analysis_revision
        reads: list[int] = []

        def read(store, protocol_id, source_revision, analysis_revision):
            reads.append(analysis_revision)
            return original(store, protocol_id, source_revision, analysis_revision)

        def open_catalog():
            store = initialize_protocol_store(self.settings)
            return ProtocolCatalog(store), store

        with (
            patch.dict(os.environ, {"VOINEY_LAB_AUTH_MODE": "dev", "VOINEY_LAB_WORKSPACE_ENABLED": "false"}),
            patch.object(server, "server_config"),
            patch.object(server, "_configured_candidate_fixture", return_value=None),
            patch.object(server, "_protocol_store_settings", return_value=self.settings),
            patch.object(server, "_open_protocol_catalog", open_catalog),
            patch.object(type(self.store), "get_analysis_revision", read),
        ):
            client = TestClient(server.app)
            self.assertEqual(client.get("/api/protocols").status_code, 200)
            self.assertEqual(client.get("/api/protocols").status_code, 200)
            self.assertEqual(reads, [1], "same immutable analysis decoded repeatedly")
            self.store.append_analysis_revision(entry.protocol_id, 1, "perf-analysis-second", draft.protocol, draft.readiness, draft.capability_policy_id)
            # Appending validates its own result through the same store read;
            # count the following HTTP projection separately.
            reads.clear()
            response = client.get("/api/protocols")
            shown = next(row for row in response.json()["protocols"] if row["protocol_id"] == entry.protocol_id)
            self.assertEqual(shown["revision_id"], "pdf-1-analysis-2")
            self.assertEqual(reads, [2])

    def test_corrupted_payload_cannot_reuse_a_previously_verified_record(self) -> None:
        entry = self.entries[0]
        draft = analysis_draft(self.catalog.source_pdf(entry.protocol_id)[0], entry.protocol_id, "Title 0")
        record = self.store.append_analysis_revision(entry.protocol_id, 1, "perf-analysis-corruption", draft.protocol, draft.readiness, draft.capability_policy_id)
        self.assertTrue(self.catalog.get_entry(entry.protocol_id).available_for_execution)
        self.store._connection.execute("DROP TRIGGER analysis_payloads_no_update")
        self.store._connection.execute(
            "UPDATE analysis_payloads SET payload_json=payload_json || ? WHERE payload_sha256=?",
            (" ", record.payload_sha256),
        )
        self.store._connection.commit()
        with self.assertRaises(ProtocolSerializationError):
            self.catalog.get_entry(entry.protocol_id)

    def test_http_list_after_cache_eviction_starts_no_pdf_workers(self) -> None:
        def open_catalog():
            store = initialize_protocol_store(self.settings)
            return ProtocolCatalog(store), store

        clear_protocol_pdf_cache()
        with (
            patch.dict(os.environ, {"VOINEY_LAB_AUTH_MODE": "dev", "VOINEY_LAB_WORKSPACE_ENABLED": "false"}),
            patch.object(server, "server_config"),
            patch.object(server, "_configured_candidate_fixture", return_value=None),
            patch.object(server, "_protocol_store_settings", return_value=self.settings),
            patch.object(server, "_open_protocol_catalog", open_catalog),
            patch("voiney_lab.pdf_text_engine.read_document", side_effect=AssertionError("HTTP list started PDF worker")),
        ):
            response = TestClient(server.app).get("/api/protocols")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["protocols"]), 2)

    def test_cold_list_and_run_status_extract_zero_pdfs_and_keep_bytes(self) -> None:
        expected = [entry.public_dict() for entry in self.catalog.list_entries()]
        expected_runs = [self.catalog.analysis_run_status(entry.protocol_id).public_dict() for entry in self.catalog.list_entries()]
        clear_protocol_pdf_cache()
        before = tuple(self.store.list_events(entry.protocol_id) for entry in self.entries)
        with patch("voiney_lab.protocol_catalog.extract_protocol_pdf", side_effect=AssertionError("list read a PDF")):
            reopened = ProtocolCatalog(self.store)
            self.assertEqual([entry.public_dict() for entry in reopened.list_entries()], expected)
            self.assertEqual([reopened.analysis_run_status(entry.protocol_id).public_dict() for entry in reopened.list_entries()], expected_runs)
        self.assertEqual(tuple(self.store.list_events(entry.protocol_id) for entry in self.entries), before)

    def test_server_filters_tenant_before_constructing_any_entry(self) -> None:
        visible = self.entries[0].protocol_id
        real = ProtocolCatalog._entry_for_revision
        constructed: list[str] = []

        def build(catalog, revision):
            constructed.append(revision.experiment_id)
            return real(catalog, revision)

        with (
            patch.object(server, "server_config"),
            patch.object(server, "_configured_candidate_fixture", return_value=None),
            patch.object(server, "_protocol_store_settings", return_value=self.settings),
            patch.object(server, "_open_protocol_catalog", return_value=(self.catalog, self.store)),
            patch.object(server, "_visible_catalog_resource_ids", return_value=frozenset({visible})),
            patch.object(ProtocolCatalog, "_entry_for_revision", build),
            patch.object(self.store, "close"),
        ):
            response = server.list_protocol_catalog()
        self.assertEqual([row["protocol_id"] for row in response["protocols"]], [visible])
        self.assertTrue(constructed)
        self.assertEqual(set(constructed), {visible})
