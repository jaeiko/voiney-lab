from __future__ import annotations

import sqlite3

import pytest
from pypdf import PdfWriter

from voice_workflow_agent.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voice_workflow_agent.experiment_protocol_store import (
    initialize_protocol_store,
)
from voice_workflow_agent.protocol_catalog import (
    ProtocolCatalog,
    ProtocolOcrRequiredError,
    ProtocolOcrReviewError,
    SharedSecretApprovalPolicy,
)
from voice_workflow_agent.protocol_ocr import (
    OcrPage,
    OcrResult,
    ProtocolOcrResultError,
    validate_ocr_result,
)


_FIXTURE_PAGE_WIDTH = 4000  # wide enough that one unwrapped fixture line
# is never clipped: a bounded extractor would otherwise drop the tail and
# disagree with an unbounded one on synthetic input only.


def _blank_pdf(path, *, pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=_FIXTURE_PAGE_WIDTH, height=792)
    writer.add_metadata({"/Title": "Scanned fictional protocol"})
    with path.open("wb") as stream:
        writer.write(stream)
    return path.read_bytes()


class FakeOcrProvider:
    def __init__(self, *, wrong_hash: bool = False) -> None:
        self.wrong_hash = wrong_hash
        self.calls = 0

    def recognize(self, source_pdf, *, source_sha256, page_count):
        self.calls += 1
        assert source_pdf.is_file()
        return OcrResult(
            source_sha256=("0" * 64 if self.wrong_hash else source_sha256),
            provider="fictional-ocr",
            provider_version="1.0",
            pages=tuple(
                OcrPage(
                    page,
                    (
                        "Fictional protocol. Section preparation. "
                        f"Step {page}. Record the fictional result."
                    ),
                    0.94,
                )
                for page in range(1, page_count + 1)
            ),
            languages=("en",),
        )


def test_ocr_result_requires_exact_source_and_complete_ordered_pages():
    result = OcrResult(
        source_sha256="a" * 64,
        provider="fake",
        provider_version="1",
        pages=(OcrPage(2, "second"), OcrPage(1, "first")),
    )
    with pytest.raises(ProtocolOcrResultError, match="every source page"):
        validate_ocr_result(
            result, expected_sha256="a" * 64, expected_page_count=2
        )
    with pytest.raises(ProtocolOcrResultError, match="identity"):
        validate_ocr_result(
            OcrResult(
                source_sha256="b" * 64,
                provider="fake",
                provider_version="1",
                pages=(OcrPage(1, "text"),),
            ),
            expected_sha256="a" * 64,
            expected_page_count=1,
        )


def test_scanned_pdf_ocr_requires_human_review_before_analysis(tmp_path):
    source = tmp_path / "scanned.pdf"
    _blank_pdf(source)
    store = initialize_protocol_store(
        ProtocolPersistenceSettings(True, tmp_path / "catalog")
    )
    catalog = ProtocolCatalog(store)
    try:
        registered = catalog.register(
            source,
            source_filename="scanned.pdf",
            media_type="application/pdf",
        ).entry
        assert registered.analysis_status == "ocr_required"
        with pytest.raises(ProtocolOcrRequiredError):
            catalog.request_analysis(registered.protocol_id, "analysis-before-ocr")

        provider = FakeOcrProvider()
        completed = catalog.run_ocr(
            registered.protocol_id, provider, ocr_id="ocr-first"
        )
        assert provider.calls == 1
        assert completed["state"] == "review_required"
        assert completed["pages"][0]["source_page_number"] == 1
        assert completed["executable"] is False
        entry = catalog.get_entry(registered.protocol_id)
        assert entry.analysis_status == "ocr_review_required"
        assert entry.available_for_execution is False
        with pytest.raises(ProtocolOcrRequiredError):
            catalog.request_analysis(registered.protocol_id, "analysis-unreviewed")

        rejected = catalog.review_ocr(
            registered.protocol_id,
            decision="rejected",
            policy=SharedSecretApprovalPolicy("review-secret"),
            presented_secret="review-secret",
            comment="Page 2 did not match the scan.",
        )
        assert rejected["state"] == "rejected"
        with pytest.raises(ProtocolOcrRequiredError):
            catalog.request_analysis(registered.protocol_id, "analysis-rejected")

        rerun = catalog.run_ocr(
            registered.protocol_id, provider, ocr_id="ocr-corrected"
        )
        assert rerun["state"] == "review_required"
        accepted = catalog.review_ocr(
            registered.protocol_id,
            decision="accepted",
            policy=SharedSecretApprovalPolicy("review-secret"),
            presented_secret="review-secret",
            actor_principal_id="reviewer-a",
            actor_role="reviewer",
        )
        assert accepted["state"] == "accepted_for_analysis"
        assert accepted["accepted_for_analysis"] is True
        assert accepted["executable"] is False

        requested = catalog.request_analysis(
            registered.protocol_id, "analysis-after-reviewed-ocr"
        )
        assert requested.lifecycle_state == "analysis_pending"
        assert requested.available_for_execution is False
        review = catalog.review(registered.protocol_id)
        assert review["ocr"]["state"] == "accepted_for_analysis"
        assert review["ocr"]["pages"][0]["text"].startswith("Fictional")

        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._connection.execute(
                "UPDATE protocol_events SET payload_json='{}' "
                "WHERE event_type='protocol_ocr_completed'"
            )
    finally:
        store.close()


def test_invalid_provider_result_is_persisted_as_bounded_failure(tmp_path):
    source = tmp_path / "scanned.pdf"
    _blank_pdf(source, pages=1)
    store = initialize_protocol_store(
        ProtocolPersistenceSettings(True, tmp_path / "catalog")
    )
    catalog = ProtocolCatalog(store)
    try:
        protocol_id = catalog.register(
            source,
            source_filename="scanned.pdf",
            media_type="application/pdf",
        ).entry.protocol_id
        with pytest.raises(ProtocolOcrResultError):
            catalog.run_ocr(
                protocol_id,
                FakeOcrProvider(wrong_hash=True),
                ocr_id="ocr-invalid",
            )
        status = catalog.ocr_status(protocol_id)
        assert status["state"] == "failed"
        assert status["failure_code"] == "protocol_ocr_result_invalid"
        assert "pages" not in status
        assert catalog.get_entry(protocol_id).analysis_status == "ocr_failed"
        with pytest.raises(ProtocolOcrReviewError):
            catalog.review_ocr(
                protocol_id,
                decision="accepted",
                policy=SharedSecretApprovalPolicy("review-secret"),
                presented_secret="review-secret",
            )
    finally:
        store.close()
