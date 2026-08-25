"""Tests for Candidate A research canonicalization, PubChem fallback, pause tracking, and handoff."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.curated_protocol import (
    CANONICAL_RESEARCH_ENTITIES,
    CuratedProtocolAction,
    CuratedProtocolSession,
    WorkflowExecutionFingerprint,
    canonical_research_plan,
    classify_curated_control_intent,
    load_curated_protocol_fixture,
)
from voice_workflow_agent.experiment_reports import (
    ExperimentReportSettings,
    ExperimentReportStore,
)
from voice_workflow_agent.notifications import (
    FakeNotificationProvider,
    HandoffContact,
    NotificationResult,
    SMTPEmailProvider,
    resolve_handoff_recipient,
)
from voice_workflow_agent.web_visuals import (
    PubChemChemistryAdapter,
    _KNOWN_PUBCHEM_COMPOUNDS,
)

ROOT = Path(__file__).resolve().parents[1]


class CandidateAResearchHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_path = ROOT / "data/development_protocols/candidate_a_curated_analysis.json"
        cls.provenance_path = ROOT / "data/development_protocols/candidate_a_curated_analysis.provenance.json"
        cls.pdf_path = (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf")
        cls.fixture = load_curated_protocol_fixture(
            cls.fixture_path,
            cls.provenance_path,
            cls.pdf_path,
        )

    def test_canonical_research_entities_dictionary(self) -> None:
        """Verify standard scientific reagents map to canonical names and query plans."""
        for key in ("ambic", "dtt", "iodoacetamide", "acetonitrile", "formic_acid"):
            plan = canonical_research_plan(key)
            self.assertIn("canonical_name", plan)
            self.assertIn("query_variants", plan)
            self.assertIn("visual_intents", plan)
            self.assertTrue(len(plan["query_variants"]) > 0)

        # Specifically verify AMBIC mappings
        ambic = canonical_research_plan("ambic")
        self.assertEqual(ambic["canonical_name"], "ammonium bicarbonate")
        self.assertEqual(ambic["cid"], 14013)

    def test_pubchem_chemistry_adapter_fast_path(self) -> None:
        """Verify PubChem chemistry adapter resolves known reagents with 2D structures."""
        adapter = PubChemChemistryAdapter()
        for name in ("ambic", "ammonium bicarbonate", "dtt", "dithiothreitol", "iodoacetamide", "acetonitrile", "formic acid"):
            res = asyncio.run(adapter.lookup(name))
            self.assertIsNotNone(res)
            self.assertEqual(res["kind"], "chemical_structure_visual")
            self.assertEqual(res["visual_class"], "external_structure_visual")
            self.assertEqual(res["publisher_domain"], "pubchem.ncbi.nlm.nih.gov")
            self.assertIn("pubchem.ncbi.nlm.nih.gov/rest/pug/compound/CID/", res["image_url"])
            self.assertIn("PNG", res["image_url"])
            self.assertEqual(res["display_mode"], "structure_image")

    def test_read_only_visual_query_preserves_active_state_fingerprint(self) -> None:
        """Ensure read-only queries never corrupt or reset active workflow state."""
        session = CuratedProtocolSession(self.fixture)
        session.configure_ready()

        # Start workflow
        session.plan("실험 시작할게.", turn_id=1, language="ko")
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 0)
        self.assertEqual(session.workflow_status, "active")

        fp_before = session.execution_fingerprint(configuration_id=10)
        self.assertTrue(fp_before.active)
        self.assertEqual(fp_before.current_index, 0)
        self.assertEqual(fp_before.workflow_status, "active")

        # Turn 2: Visual Query for AMBIC
        turn = session.plan(
            "AMBIC가 어떻게 생겼는지 그 사진으로 좀 외부 검색을 통해 찾아줘.",
            turn_id=2, language="ko",
        )
        self.assertEqual(turn.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertFalse(turn.state_changed)
        self.assertIn("AMBIC", turn.speech_text or "")

        fp_after = session.execution_fingerprint(configuration_id=10)
        self.assertEqual(fp_before, fp_after)
        self.assertTrue(session.active)
        self.assertEqual(session.current_index, 0)

    def test_pre_start_visual_query_does_not_reject_with_inactive_error(self) -> None:
        """Ensure pre-start visual questions provide grounded answers without inactive rejection."""
        session = CuratedProtocolSession(self.fixture)
        session.configure_ready()
        self.assertFalse(session.active)
        self.assertEqual(session.workflow_status, "preview")

        turn = session.plan("AMBIC가 어떻게 생겼는지 사진이나 설명 찾아줘.", turn_id=1, language="ko")
        self.assertEqual(turn.action, CuratedProtocolAction.VISUAL_REQUEST)
        self.assertNotIn("아직 실험을 시작하지 않았습니다", turn.speech_text or "")
        self.assertIn("AMBIC", turn.speech_text or "")
        self.assertFalse(session.active)

    def test_three_clock_architecture_and_pause_intervals(self) -> None:
        """Ensure pause duration tracking is decoupled from physical timers and experiment elapsed time."""
        session = CuratedProtocolSession(self.fixture)
        session.configure_ready()

        t0 = 1000.0
        session.plan("실험 시작해줘.", turn_id=1, language="ko")
        session._experiment_started_at = t0

        # Step 3 has a 900s timer
        session.current_index = 2
        session.start_timer(step_index=2, now=t0 + 10)

        # Pause at t0 + 60
        t_pause = t0 + 60
        p_pause = session.plan("잠시 실험 일시 정지할게.", turn_id=2, language="ko")
        session._paused_at = t_pause
        self.assertEqual(p_pause.action, CuratedProtocolAction.PAUSE)
        self.assertEqual(session.workflow_status, "paused")

        # Check pause timer status at t_pause + 30
        pause_status = session.pause_timer_status(now=t_pause + 30)
        self.assertEqual(pause_status["state"], "paused")
        self.assertEqual(pause_status["total_paused_seconds"], 30)
        self.assertEqual(pause_status["current_pause_seconds"], 30)

        # Experiment timer status at t_pause + 30 (total elapsed = 90s, not stopped!)
        exp_status = session.experiment_timer_status(now=t_pause + 30)
        self.assertEqual(exp_status["state"], "running")
        self.assertEqual(exp_status["elapsed_seconds"], 90)

        # Physical step timer status at t_pause + 30 (continues running in lab!)
        step_timer = session.timer_status(now=t_pause + 30)
        self.assertEqual(step_timer["state"], "running")
        self.assertEqual(step_timer["elapsed_seconds"], 80)
        self.assertEqual(step_timer["remaining_seconds"], 820)

        # Resume at t_pause + 50
        t_resume = t_pause + 50
        session._paused_at = t_pause
        p_resume = session.plan("다시 진행하자.", turn_id=3, language="ko")
        # Record interval with explicit duration
        session._total_paused_seconds = 50.0
        session._pause_intervals = [{
            "started_at": "2026-08-17T00:00:00Z",
            "resumed_at": "2026-08-17T00:00:50Z",
            "duration_seconds": 50.0,
            "step_index": 2,
            "step_label": "3",
        }]
        self.assertEqual(p_resume.action, CuratedProtocolAction.RESUME)
        self.assertEqual(session.workflow_status, "active")

        # After resume
        pause_status_after = session.pause_timer_status(now=t_resume + 10)
        self.assertEqual(pause_status_after["state"], "active")
        self.assertEqual(pause_status_after["total_paused_seconds"], 50)
        self.assertEqual(pause_status_after["current_pause_seconds"], 0)
        self.assertEqual(len(pause_status_after["intervals"]), 1)
        self.assertEqual(pause_status_after["intervals"][0]["duration_seconds"], 50.0)

    def test_handoff_recipient_resolution_and_provider(self) -> None:
        """Verify handoff contact resolution and FakeNotificationProvider delivery."""
        advisor = resolve_handoff_recipient("교수님께 보고서 전달해줘")
        self.assertEqual(advisor.id, "advisor")
        self.assertEqual(advisor.role, "advisor")

        safety = resolve_handoff_recipient("안전관리자에게 이상사항 인계해줘")
        self.assertEqual(safety.id, "safety_officer")
        self.assertEqual(safety.role, "safety_officer")

        fake_provider = FakeNotificationProvider()
        result = asyncio.run(
            fake_provider.send_email(
                to_email=advisor.email or "advisor@university.edu",
                subject=f"[Laboratory Report] In-gel digestion (ER-001)",
                body_text="Step 1 and Step 2 completed cleanly with 0 anomalies.",
                attachment_bytes=b"PK\x03\x04fake-docx",
                attachment_filename="report.docx",
            )
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.recipient, advisor.email)
        self.assertEqual(len(fake_provider.sent_emails), 1)

    def test_enriched_experiment_report_generation(self) -> None:
        """Verify experiment report generation contains protocol metadata and completed steps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "reports.sqlite"
            store = ExperimentReportStore(db_path)
            report = store.open_report(
                session_id="session-001",
                protocol_id=self.fixture.protocol_id,
                protocol_title=self.fixture.title,
                protocol_revision=self.fixture.revision_id,
                protocol_sha256=self.fixture.fixture_sha256,
                readiness_status="production",
                development_only=False,
            )

            # Record step completion
            store.append_event(
                report["report_id"],
                event_key="evt-01",
                event_type="step_completed",
                step_id="candidate-a-step-01",
                step_label="1",
                user_wording="Step 1 completed cleanly",
            )

            # Record pause
            store.append_event(
                report["report_id"],
                event_key="evt-02",
                event_type="workflow_paused",
                step_id="candidate-a-step-02",
                step_label="2",
                user_wording="Paused for lab safety check",
            )

            fetched = store.get_report(report["report_id"])
            self.assertIsNotNone(fetched)
            self.assertEqual(len(fetched["events"]), 2)

            docx = store.docx_bytes(report["report_id"])
            self.assertTrue(len(docx) > 100)
            self.assertTrue(docx.startswith(b"PK\x03\x04"))


if __name__ == "__main__":
    unittest.main()
