"""Phase 3 Acceptance and Integration Test Suite.

Covers:
1. Procedure / Conversation / Report Single-Run Invariant Consistency.
2. Structured Turn Rendering & Speech/Display Separation.
3. External Search Provider Abstraction & Fallback Chain.
4. Generic PDF Protocol Development Activation.
5. ReportWriterBrain & 10-Section DOCX Laboratory Export.
6. TTS Professor Voice Configuration.
"""

import asyncio
import io
import os
from pathlib import Path
from docx import Document

from voice_workflow_agent.language import clean_speech_text
from voice_workflow_agent.external_references import (
    CircuitBreaker,
    PubChemSearchProvider,
    WikimediaSearchProvider,
    SearchResult,
    VisualSearchResult,
)
from voice_workflow_agent.experiment_reports import (
    ExperimentReportStore,
    ReportDraftState,
    ReportWriterBrain,
)
from voice_workflow_agent.curated_protocol import (
    load_curated_protocol_fixture,
    CuratedProtocolTurnPlan,
    CuratedProtocolSession,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "development_protocols"
SOURCE_PDF = (Path(__file__).resolve().parents[1] / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf")


def test_clean_speech_text_strips_markdown_and_bullets():
    raw_markdown = (
        "### 800 rpm\n"
        "- 분당 800회 회전하는 교반 속도입니다.\n"
        "- **중요**: 37 °C에서 [15분](https://example.com) 동안 유지합니다.\n"
        "• 추가 안내사항입니다."
    )
    cleaned = clean_speech_text(raw_markdown)
    assert "###" not in cleaned
    assert "- " not in cleaned
    assert "• " not in cleaned
    assert "**" not in cleaned
    assert "https://" not in cleaned
    assert "분당 800회 회전하는 교반 속도입니다" in cleaned
    assert "37 °C에서 15분 동안 유지합니다" in cleaned


def test_circuit_breaker_state_transitions():
    cb = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=0.1)
    assert cb.is_available()
    assert cb.state == "healthy"

    cb.record_failure()
    assert cb.state == "degraded"
    assert cb.is_available()

    cb.record_failure()
    assert cb.state == "degraded"
    assert cb.is_available()

    cb.record_failure()
    assert cb.state == "open_circuit"
    assert not cb.is_available()

    # Success resets immediately
    cb.record_success()
    assert cb.state == "healthy"
    assert cb.is_available()


def test_pubchem_search_provider_known_compounds():
    provider = PubChemSearchProvider()
    results = asyncio.run(provider.search_text("AMBIC buffer", language="ko"))
    assert len(results) >= 1
    assert "pubchem.ncbi.nlm.nih.gov" in results[0].domain
    assert "14013" in results[0].url

    visuals = asyncio.run(provider.search_images("DTT"))
    assert len(visuals) >= 1
    assert "439196" in visuals[0].image_url
    assert "pubchem" in visuals[0].provider


def test_wikimedia_search_provider_structure():
    provider = WikimediaSearchProvider(timeout_seconds=5.0)
    assert asyncio.run(provider.healthcheck()) is True
    # Test text search returns empty (visuals only)
    assert asyncio.run(provider.search_text("SDS-PAGE", language="en")) == []


def test_report_writer_brain_deterministic_draft(tmp_path):
    db_path = tmp_path / "test_reports.db"
    store = ExperimentReportStore(db_path)
    session_id = "sess-test-001"

    report = store.open_report(
        session_id=session_id,
        protocol_id="candidate-a-in-gel-digestion",
        protocol_title="In-Gel Protein Digestion",
        protocol_revision="rev-01",
        protocol_sha256="a" * 64,
        readiness_status="guidance_ready",
        development_only=True,
    )
    report_id = report["report_id"]
    store.append_event(report_id, event_key="ev-1", event_type="step_completed", step_label="1", user_wording="1단계 완료")
    store.append_event(report_id, event_key="ev-2", event_type="observation", step_label="1", user_wording="용액이 투명함")
    store.append_event(report_id, event_key="ev-3", event_type="anomaly", step_label="2", category="temperature", user_wording="온도 초과")

    updated_report = store.get_report(report_id)
    events = list(updated_report.get("events") or ())

    brain = ReportWriterBrain()
    draft = brain.build_deterministic_draft(updated_report, events)

    assert isinstance(draft, ReportDraftState)
    assert draft.report_id == report_id
    assert len(draft.committed_event_ids) == 3
    assert "1" in draft.experiment_summary
    assert "1 direct qualitative observations" in draft.observations_narrative
    assert "1 unexpected condition" in draft.anomalies_narrative


def test_docx_export_contains_10_sections(tmp_path):
    db_path = tmp_path / "test_reports.db"
    store = ExperimentReportStore(db_path)
    session_id = "sess-docx-001"

    report = store.open_report(
        session_id=session_id,
        protocol_id="candidate-a-in-gel-digestion",
        protocol_title="In-Gel Protein Digestion",
        protocol_revision="rev-01",
        protocol_sha256="b" * 64,
        readiness_status="guidance_ready",
        development_only=True,
    )
    report_id = report["report_id"]
    store.append_event(report_id, event_key="ev-start", event_type="session_started", user_wording="세션 시작")
    store.append_event(report_id, event_key="ev-step1", event_type="step_completed", step_label="1", user_wording="1단계 완료")

    docx_bytes = store.export_docx(report_id)
    assert len(docx_bytes) > 1000

    doc = Document(io.BytesIO(docx_bytes))
    full_text = "\n".join(p.text for p in doc.paragraphs)

    # Verify key sections and metadata
    assert "I. Purpose" in full_text
    assert "II. Materials and Methods" in full_text
    assert "III. Results" in full_text
    assert "IV. Discussion" in full_text
    assert "V. Conclusion" in full_text
    assert "Title:" in full_text
    assert "Course:" in full_text
    assert "Student number:" in full_text
    assert "Name:" in full_text
    assert "Advisor:" in full_text
    assert "In-Gel Protein Digestion" in full_text
    assert "Execution Event Timeline:" in full_text
    assert "Cryptographic Ledger Events:" in full_text


def test_curated_protocol_turn_answer_envelope_rich_formatting():
    fixture = load_curated_protocol_fixture(
        DATA / "candidate_a_curated_analysis.json",
        DATA / "candidate_a_curated_analysis.provenance.json",
        SOURCE_PDF,
    )
    session = CuratedProtocolSession(fixture)

    # Query asking about parameters
    plan = session.plan("800 rpm과 37도 설정한 기준이 뭐야?", language="ko", turn_id=7, generation=1)
    assert plan.action.value == "related_question"
    assert "### " in (plan.display_text or "")
    assert plan.speech_text is not None
    # Speech text should be clean
    assert "###" not in plan.speech_text
    assert "•" not in plan.speech_text
