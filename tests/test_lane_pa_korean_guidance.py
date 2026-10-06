"""Failure and recovery guidance the server writes is Korean (lane PA, decision 4).

On 2026-10-06 the review of a failed analysis showed the English line "Review
the failure code and explicitly retry analysis." beside Korean labels, and a
missing analysis model was explained as "Configure XAI_API_KEY ...", although
the analysis role need not be xAI. The server now writes that guidance in
Korean; the codes stay as they were.
"""

from __future__ import annotations

import inspect
import re
import tempfile
import unittest
from pathlib import Path

from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import server
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

HANGUL = re.compile(r"[가-힣]")


class CatalogRecoveryGuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = initialize_protocol_store(ProtocolPersistenceSettings(True, root / "catalog"))
        self.catalog = ProtocolCatalog(self.store)
        pdf = root / "p.pdf"
        write_text_pdf(pdf, "Simple Protocol\n1. Add 500 uL buffer.", title="Simple Protocol")
        self.protocol_id = self.catalog.register(
            pdf, source_filename="p.pdf", media_type="application/pdf",
        ).entry.protocol_id

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def failure_after(self, code: str, attempt: int = 1) -> dict:
        analysis_id = f"analysis-{attempt:032x}"
        self.catalog.request_analysis(self.protocol_id, analysis_id)
        self.catalog.fail_analysis_request(self.protocol_id, analysis_id, failure_code=code)
        return self.catalog.review(self.protocol_id)["analysis_failure"]

    def test_the_evidence_failure_is_explained_in_korean(self) -> None:
        failure = self.failure_after("protocol_analysis_invalid_evidence")
        self.assertEqual(failure["code"], "protocol_analysis_invalid_evidence")
        self.assertTrue(failure["retryable"])
        self.assertIn("분석 다시 시도", failure["action"])
        self.assertIn("근거", failure["action"])
        self.assertNotIn("explicitly retry", failure["action"])

    def test_a_missing_analysis_role_names_the_analysis_settings_not_xai(self) -> None:
        action = self.failure_after("provider_configuration_missing")["action"]
        self.assertIn("VOINEY_LAB_ANALYSIS_PROVIDER", action)
        self.assertIn("VOINEY_LAB_ANALYSIS_MODEL", action)
        self.assertNotIn("XAI_API_KEY", action)
        self.assertRegex(action, HANGUL)

    def test_every_failure_code_gets_korean_guidance(self) -> None:
        for attempt, code in enumerate((
            "protocol_analysis_timeout", "protocol_analysis_model_failed",
            "protocol_analysis_invalid_response", "a_code_from_a_newer_server",
        ), start=1):
            with self.subTest(code=code):
                action = self.failure_after(code, attempt)["action"]
                self.assertRegex(action, HANGUL)
                self.assertIsNone(re.search(r"[A-Za-z]{3,} [a-z]{3,} [a-z]{3,}", action), action)


class WorkspaceGateGuidanceTests(unittest.TestCase):
    def test_the_workspace_recovery_actions_are_korean(self) -> None:
        source = inspect.getsource(server._workspace_catalog_analysis_gate)
        for english in (
            "Regenerate valid structured analysis before approval.",
            "Repair the immutable source/revision binding before review.",
            "Restore the catalog before review or approval.",
            "Regenerate analysis for this exact immutable source revision.",
            "Retry structured analysis; this item is recovery/triage only.",
            "Complete structured analysis before execution approval.",
            "Resolve readiness and safety blockers before approval.",
        ):
            self.assertNotIn(english, source)
        self.assertIn("구조 분석을 다시 시도해 주세요.", source)


if __name__ == "__main__":
    unittest.main()
