"""Reading the catalog of a protocol too large for one analysis call.

What is left of the provider-free large-PDF tests: chunk planning, the chunk
run, the merge and cancellation were deleted on 2026-10-13 (lane AN, decision
1; the git history keeps them). A protocol over the single-pass limit is still
registered as ``chunked_analysis_required``, and listing it or reading its
status must still call no model and make nothing executable.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.pdf_fixture_support import write_pages
from voiney_lab.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voiney_lab.experiment_protocol_store import (
    initialize_protocol_store,
)
from voiney_lab.protocol_catalog import ProtocolCatalog


class ProtocolChunkAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "large.pdf"
        self.page_texts = tuple(
            f"Protocol Large Section {number}\n{number}. Do action {number}. "
            + ("x" * 48)
            for number in range(1, 7)
        )
        write_pages(self.pdf, self.page_texts)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_listing_and_status_do_not_invoke_model_or_promote_analysis(self):
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "read-only-catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            with patch(
                "voiney_lab.protocol_catalog._analysis_state",
                return_value="chunked_analysis_required",
            ):
                entry = catalog.register(
                    self.pdf,
                    source_filename="large.pdf",
                    media_type="application/pdf",
                ).entry
                model = Mock()
                catalog.list_entries()
                status = catalog.analysis_run_status(entry.protocol_id)
            model.analyze.assert_not_called()
            self.assertEqual(status.state, "chunked_analysis_required")
            self.assertFalse(entry.available_for_execution)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
