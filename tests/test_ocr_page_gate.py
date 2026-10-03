"""A page waiting for OCR keeps its Protocol out of execution.

A page is marked as needing OCR when its text layer cannot be read -- no text,
or a glyph with no Unicode mapping. The rest of the document is still read and
analysed, so the page's mark has to travel to the one decision that matters:
until the page's OCR text has been accepted by a reviewer and analysed, the
Protocol does not execute. A signature cannot clear it, because a signature
does not make the glyph readable; OCR does.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_pdf_text_engine import _write_raw_pages
from tests.test_protocol_ocr import FakeOcrProvider
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_pdf import (
    OCR_REASON_UNREADABLE_GLYPHS,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import (
    _ACKNOWLEDGEABLE_GATES,
    ProtocolCatalog,
    SharedSecretApprovalPolicy,
)

ROOT = Path(__file__).resolve().parents[1]
IN_GEL = ROOT / "data" / "runtime" / "candidate-a-source" / "in-gel-digestion.pdf"
#: The reason this file is about, by its stored value.
CODE_VALUE = "source_page_requires_ocr"


def _with_ocr_page(protocol, page_number: int):
    pdf = protocol.metadata.pdf
    pages = tuple(
        replace(page, ocr_required=True, ocr_reason=OCR_REASON_UNREADABLE_GLYPHS)
        if page.source_page_number == page_number
        else page
        for page in pdf.pages
    )
    return replace(
        protocol, metadata=replace(protocol.metadata, pdf=replace(pdf, pages=pages))
    )


class TheReadinessReasonTests(unittest.TestCase):
    def _protocol(self):
        from tests.test_pdf_to_session_walkthrough import _pipeline

        if not IN_GEL.is_file():
            self.skipTest(f"{IN_GEL} is not present.")
        return _pipeline()[3].protocol

    def test_no_ocr_page_raises_nothing(self) -> None:
        protocol = self._protocol()
        self.assertEqual(protocol.metadata.pdf.ocr_required_page_numbers, ())
        self.assertNotIn(CODE_VALUE, domain.assess_readiness(protocol).reason_codes)

    def test_a_page_waiting_for_ocr_blocks_and_names_the_page(self) -> None:
        assessment = domain.assess_readiness(_with_ocr_page(self._protocol(), 3))
        self.assertEqual(assessment.status, domain.ReadinessStatus.ANALYSIS_REQUIRED)
        reason = next(item for item in assessment.reasons if item.code.value == CODE_VALUE)
        self.assertIn("page(s) 3", reason.message)

    def test_a_signature_cannot_clear_it(self) -> None:
        self.assertNotIn(CODE_VALUE, _ACKNOWLEDGEABLE_GATES)
        self.assertEqual(
            ProtocolCatalog._BLOCKER_RESOLUTION.get(CODE_VALUE),
            {"kind": "reviewer_can_clear", "action": "run_ocr"},
        )


class OnePageOfAReadableDocumentTests(unittest.TestCase):
    """The catalog's OCR route is open to a document with one OCR page."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        clear_protocol_pdf_cache()
        self.addCleanup(clear_protocol_pdf_cache)
        self.source = self.root / "one-glyph.pdf"
        _write_raw_pages(
            self.source,
            (
                "BT /F1 12 Tf 72 720 Td (1 Add 5 ml of buffer and mix well.) Tj ET",
                "BT /F1 12 Tf 72 720 Td (2 Incubate for \\20030 min at 37 C, then cool.) Tj ET",
            ),
        )
        self.store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "catalog")
        )
        self.addCleanup(self.store.close)
        self.catalog = ProtocolCatalog(self.store)

    def test_the_page_goes_through_ocr_and_review_while_the_rest_stays(self) -> None:
        entry = self.catalog.register(
            self.source, source_filename="one-glyph.pdf", media_type="application/pdf"
        ).entry
        # The document is not refused: its readable page can be analysed.
        self.assertNotEqual(entry.analysis_status, "ocr_required")
        self.assertEqual(
            self.catalog.ocr_status(entry.protocol_id)["state"], "ocr_required"
        )
        provider = FakeOcrProvider()
        completed = self.catalog.run_ocr(entry.protocol_id, provider, ocr_id="ocr-one")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(completed["state"], "review_required")
        accepted = self.catalog.review_ocr(
            entry.protocol_id,
            decision="accepted",
            policy=SharedSecretApprovalPolicy("review-secret"),
            presented_secret="review-secret",
            actor_principal_id="reviewer-a",
            actor_role="reviewer",
        )
        self.assertTrue(accepted["accepted_for_analysis"])
        revision = self.catalog._latest_protocol_revision(entry.protocol_id)
        analysed = self.catalog._extraction_for_analysis(
            revision, extract_protocol_pdf(self.source)
        )
        self.assertEqual(analysed.ocr_required_page_numbers, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
