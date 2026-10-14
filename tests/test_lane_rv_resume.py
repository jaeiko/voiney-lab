"""An experiment is continued on the revision it started on (lane RV, decision f).

Lane RN (2026-10-11, its report 10.1) found the gap: after an analysis passed
again, continuing an experiment started on the older analysis was refused
with WorkspaceConflictError -- the voice session loaded the latest fixture
and the durable experiment named the older revision. Review versions (lane
RV, decision c) make the same happen at every new version. The session start
now loads the revision the experiment started on; a new run still gets what
runs now.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.identity_support import development_principal
from tests.test_lane_rv_review_api import STEP_TWO
from tests.test_lane_rv_review_pending import _CatalogCase
from voiney_lab import server
from voiney_lab.protocol_catalog import (
    ProtocolCatalogNotFoundError,
    ProtocolCatalogUnavailableError,
)
from voiney_lab.workspace_store import (
    WorkspaceConflictError,
    WorkspaceSettings,
    initialize_workspace_store,
)

SECOND = "2. Incubate for 10 min at 37 C. Mix."


class StartedOnRevisionTests(_CatalogCase):
    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer())
        self.analyze(self.answer(), analysis_id="analysis-" + "b" * 32, reanalysis=True)

    def test_an_older_passed_analysis_is_loaded_by_its_revision_id(self) -> None:
        self.assertEqual(self.catalog.get_entry(self.protocol_id).revision_id, "pdf-1-analysis-2")
        older = self.catalog.load_fixture_for_revision(self.protocol_id, "pdf-1-analysis-1")
        self.assertEqual(older.revision_id, "pdf-1-analysis-1")
        self.assertEqual(older.steps[1].instruction_source_text, STEP_TWO)
        self.assertEqual(older.timer_manifest, {"s2": 600})

    def test_a_review_version_is_loaded_by_its_revision_id(self) -> None:
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "김재준"})
        self.catalog.record_review_decision(self.protocol_id, {
            "item_id": "timer:s2", "action": "fix", "seconds": 900})
        self.catalog.register_review(self.protocol_id, {"reviewer_name": "최수진"})
        first = self.catalog.load_fixture_for_revision(self.protocol_id, "pdf-1-review-1")
        self.assertEqual((first.revision_id, first.timer_manifest), ("pdf-1-review-1", {"s2": 600}))
        latest = self.catalog.load_executable_fixture(self.protocol_id)
        self.assertEqual((latest.revision_id, latest.timer_manifest), ("pdf-1-review-2", {"s2": 900}))

    def test_nothing_that_never_ran_is_loaded(self) -> None:
        for revision_id, error in (
            ("pdf-1", ProtocolCatalogUnavailableError),
            ("pdf-1-analysis-9", ProtocolCatalogNotFoundError),
            ("pdf-1-review-1", ProtocolCatalogNotFoundError),
            ("pdf-2-analysis-1", ProtocolCatalogNotFoundError),
            ("anything", ProtocolCatalogNotFoundError),
        ):
            with self.subTest(revision_id=revision_id), self.assertRaises(error):
                self.catalog.load_fixture_for_revision(self.protocol_id, revision_id)


class SessionStartTests(_CatalogCase):
    """The session start's choice, read through the durable experiment."""

    def setUp(self) -> None:
        super().setUp()
        self.analyze(self.answer())
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace_dir = Path(temp.name) / "workspace"
        self.principal = development_principal()
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        store.bootstrap_principal(self.principal)
        self.experiment = store.start_experiment(
            self.principal, protocol_id=self.protocol_id,
            protocol_revision_id="pdf-1-analysis-1", voice_connection_id="voice-1")
        store.close()
        self.environment = {
            "VOINEY_LAB_WORKSPACE_ENABLED": "true",
            "VOINEY_LAB_WORKSPACE_DATA_DIR": str(self.workspace_dir),
        }
        # Analysed again since the experiment started.
        self.analyze(self.answer(), analysis_id="analysis-" + "c" * 32, reanalysis=True)

    def start(self, **recovery):
        token = server._REQUEST_PRINCIPAL.set(self.principal)
        try:
            with patch.dict("os.environ", self.environment, clear=False):
                entry = self.catalog.get_entry(self.protocol_id)
                return server._catalog_fixture_for_start(
                    self.catalog, self.protocol_id, entry, **recovery)
        finally:
            server._REQUEST_PRINCIPAL.reset(token)

    def resume(self, revision_id: str):
        store = initialize_workspace_store(WorkspaceSettings(True, self.workspace_dir))
        try:
            return store.resume_experiment(
                self.principal, self.experiment["session_id"],
                expected_version=self.experiment["version"], protocol_id=self.protocol_id,
                protocol_revision_id=revision_id, voice_connection_id="voice-2")
        finally:
            store.close()

    def test_a_new_run_gets_the_latest_and_a_recovery_the_one_it_started_on(self) -> None:
        fixture, revision_id, failure = self.start()
        self.assertEqual((fixture.revision_id, revision_id, failure),
                         ("pdf-1-analysis-2", "pdf-1-analysis-2", None))
        fixture, revision_id, failure = self.start(
            recovery_session_id=self.experiment["session_id"])
        self.assertEqual((fixture.revision_id, revision_id, failure),
                         ("pdf-1-analysis-1", "pdf-1-analysis-1", None))
        # The durable experiment accepts it: the gap lane RN found is closed.
        resumed = self.resume(revision_id)
        self.assertEqual(resumed["protocol_revision_id"], "pdf-1-analysis-1")

    def test_the_latest_revision_is_still_refused_for_the_older_experiment(self) -> None:
        with self.assertRaises(WorkspaceConflictError):
            self.resume("pdf-1-analysis-2")

    def test_an_unknown_experiment_leaves_the_start_as_it_was(self) -> None:
        fixture, revision_id, failure = self.start(recovery_session_id="experiment-unknown")
        self.assertEqual((revision_id, failure), ("pdf-1-analysis-2", None))
        self.assertIsNone(server._recovery_revision_id("experiment-unknown", self.protocol_id))


if __name__ == "__main__":
    unittest.main()
