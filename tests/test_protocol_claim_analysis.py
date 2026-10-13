"""Page evidence segments, the label trigger, and the default analysis route.

What is left here of the evidence-first claim-chunk tests: that path and its
tests were deleted on 2026-10-13 (lane AN, decision 1; the git history keeps
them). The PDF writers these tests use moved to ``tests/pdf_fixture_support.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.pdf_fixture_support import write_pages
from voiney_lab.experiment_protocol_config import (
    ProtocolPersistenceSettings,
)
from voiney_lab.experiment_protocol_pdf import extract_protocol_pdf
from voiney_lab.experiment_protocol_store import (
    initialize_protocol_store,
)
from voiney_lab.protocol_catalog import ProtocolCatalog
from voiney_lab.protocol_claim_analysis import (
    _numbered_action_matches,
    generate_page_evidence_segments,
)


class ProtocolClaimAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "rich.pdf"
        self.action = (
            "1. Add 10 mL buffer at 5% and incubate at 37 C for 15 min at "
            "800 rpm then hold for 20 min; WARNING hot; observe clear; repeat "
            "steps 1-1 until clear; volume is not specified."
        )
        write_pages(
            self.source,
            (
                "Protocol Evidence Preparation Before start: thaw sample. "
                "Material: buffer 10 mL 5%. Equipment: mixer 800 rpm.\n"
                + self.action,
            ),
        )
        self.extraction = extract_protocol_pdf(self.source)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_evidence_segments_are_deterministic_and_source_identity_bound(self):
        multiline = replace(
            self.extraction,
            pages=(
                replace(
                    self.extraction.pages[0],
                    text=(
                        "Preparation notes\n"
                        "1. Add 10 mL buffer.\nKeep the vessel covered.\n"
                        "2. Incubate for 15 min.\nWARNING: hot surface."
                    ),
                ),
            ),
        )
        first = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-segments",
            page_number=1,
        )
        duplicate = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-segments",
            page_number=1,
        )
        other_revision = generate_page_evidence_segments(
            multiline,
            source_revision="pdf-other",
            page_number=1,
        )

        self.assertEqual(first, duplicate)
        self.assertEqual("".join(segment.text for segment in first), multiline.pages[0].text)
        # Five segments, not three: an end-of-line sentence now closes a block,
        # so the unnumbered lines are no longer absorbed into the numbered step
        # above them.
        self.assertEqual(len(first), 5)
        texts = [segment.text.strip() for segment in first]
        self.assertIn("Keep the vessel covered.", texts)
        self.assertIn("WARNING: hot surface.", texts)
        for step_text in (s for s in texts if s.startswith(("1.", "2."))):
            self.assertNotIn("Keep the vessel covered", step_text)
            self.assertNotIn("WARNING: hot surface", step_text)
        self.assertNotEqual(
            [segment.segment_id for segment in first],
            [segment.segment_id for segment in other_revision],
        )
        self.assertTrue(all(segment.segment_id.startswith("seg-") for segment in first))

    def test_action_blocks_are_bounded_and_preserve_multiline_execution_text(self):
        action = (
            "1. Before start, add 10 mL buffer and incubate for 15 min.\n"
            "WARNING: hot surface. Repeat until the sample is clear.\n"
            + ("Continue mixing without changing the source text.\n" * 120)
        )
        extraction = replace(
            self.extraction,
            pages=(replace(self.extraction.pages[0], text=action + "2. Stop mixing."),),
        )
        segments = generate_page_evidence_segments(
            extraction,
            source_revision="pdf-long-action",
            page_number=1,
        )

        self.assertEqual("".join(item.text for item in segments), extraction.pages[0].text)
        self.assertTrue(all(len(item.text) <= 4096 for item in segments))
        reconstructed = "".join(item.text for item in segments)
        for phrase in (
            "Before start",
            "10 mL",
            "15 min",
            "WARNING: hot surface",
            "Repeat until the sample is clear",
        ):
            self.assertIn(phrase, reconstructed)

    def test_common_false_positive_numbered_structures_are_excluded(self):
        source_text = "\n".join(
            (
                "1.1 Scope",
                "1 200 mg",
                "[1] Reference entry",
                "Figure 1A",
                "Page 25",
                "Note 1: context only",
                "1) 10 mL",
                "7. Mix the sample.",
            )
        )

        labels = tuple(
            dict.fromkeys(
                match.group("label")
                for match in _numbered_action_matches(source_text)
            )
        )
        self.assertEqual(labels, ("7",))

    def test_production_catalog_keeps_page_count_route_monolithic_by_default(self):
        source = self.root / "nine-pages-default-off.pdf"
        write_pages(
            source,
            tuple(
                f"Protocol Large Section {number} {number}. Do action {number}."
                for number in range(1, 10)
            ),
        )
        store = initialize_protocol_store(
            ProtocolPersistenceSettings(True, self.root / "default-off-catalog")
        )
        try:
            catalog = ProtocolCatalog(store)
            entry = catalog.register(
                source,
                source_filename="nine-pages-default-off.pdf",
                media_type="application/pdf",
            ).entry
            self.assertEqual(
                entry.analysis_status,
                "structured_analysis_ready",
            )
            self.assertEqual(
                catalog.analysis_run_status(entry.protocol_id).total_chunks,
                0,
            )
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
