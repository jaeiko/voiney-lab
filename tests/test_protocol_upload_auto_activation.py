import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_analysis import ProtocolAnalysisDraft
from voice_workflow_agent.experiment_protocol_config import ProtocolPersistenceSettings
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.experiment_protocol_store import initialize_protocol_store
from voice_workflow_agent.protocol_catalog import (
    ProtocolApprovalError,
    ProtocolCatalog,
    ProtocolCatalogNotFoundError,
    ProtocolCatalogUnavailableError,
)
from voice_workflow_agent.server import _auto_activate_ready_uploads_enabled
from tests.test_protocol_catalog import write_text_pdf, analysis_draft

ROOT = Path(__file__).resolve().parents[1]


class ProtocolUploadAutoActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.catalog = ProtocolCatalog(self.store)
        self.sample_pdf = self.root / "sample.pdf"
        write_text_pdf(
            self.sample_pdf,
            "Protocol Test\nSection preparation\n1. Add solution.",
            title="Protocol Test",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_auto_activate_policy_default_and_env(self):
        # Default without explicit toggle is False
        with patch.dict(os.environ, {"VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo", "VOICE_WORKFLOW_AGENT_AUTO_ACTIVATE_READY_UPLOADS": ""}, clear=False):
            self.assertFalse(_auto_activate_ready_uploads_enabled())

        # In operational mode, auto-activation is always disabled even if toggle is true
        with patch.dict(os.environ, {"VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "operational", "VOICE_WORKFLOW_AGENT_AUTO_ACTIVATE_READY_UPLOADS": "true"}, clear=False):
            self.assertFalse(_auto_activate_ready_uploads_enabled())

        # Explicit toggle enables auto-activation in non-operational mode
        with patch.dict(os.environ, {"VOICE_WORKFLOW_AGENT_AUTO_ACTIVATE_READY_UPLOADS": "true", "VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo"}, clear=False):
            self.assertTrue(_auto_activate_ready_uploads_enabled())
        with patch.dict(os.environ, {"VOICE_WORKFLOW_AGENT_AUTO_ACTIVATE_READY_UPLOADS": "1", "VOICE_WORKFLOW_AGENT_USAGE_SCOPE": "demo"}, clear=False):
            self.assertTrue(_auto_activate_ready_uploads_enabled())

    def test_activate_development_success_when_guidance_ready(self):
        reg = self.catalog.register(
            self.sample_pdf, source_filename="sample.pdf", media_type="application/pdf"
        )
        protocol_id = reg.entry.protocol_id

        # Before analysis: cannot activate development
        with self.assertRaises(ProtocolCatalogUnavailableError):
            self.catalog.activate_development(protocol_id)

        # Append analysis that is guidance_ready
        draft = analysis_draft(self.sample_pdf, protocol_id, "Protocol Test")
        self.store.append_analysis_revision(
            protocol_id,
            1,
            f"analysis-{reg.entry.source_sha256[:24]}",
            draft.protocol,
            draft.readiness,
            draft.capability_policy_id,
        )

        # Now activate development
        activated = self.catalog.activate_development(protocol_id)
        self.assertEqual(activated.protocol_id, protocol_id)
        self.assertEqual(activated.approval_status, "development_only")
        self.assertEqual(activated.analysis_status, "active_development")
        self.assertTrue(activated.available_for_execution)

        # Re-fetching entry confirms persistence
        entry = self.catalog.get_entry(protocol_id)
        self.assertEqual(entry.approval_status, "development_only")
        self.assertTrue(entry.available_for_execution)

    def test_activate_nonexistent_protocol_raises_not_found(self):
        with self.assertRaises(ProtocolCatalogNotFoundError):
            self.catalog.activate_development("nonexistent-protocol-12345")


if __name__ == "__main__":
    unittest.main()
