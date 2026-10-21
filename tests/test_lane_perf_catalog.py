"""Cold protocol lists are projections of stored metadata, scoped before building rows."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import server
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import clear_protocol_pdf_cache
from voiney_lab.experiment_protocol_store import initialize_protocol_store
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
            write_text_pdf(source, f"Fictional protocol {number}. 1. Add buffer to tube.", title=f"Title {number}")
            self.entries.append(self.catalog.register(source, source_filename=source.name, media_type="application/pdf").entry)

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
