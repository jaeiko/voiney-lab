"""The experiment report, polished (lane RP2, decisions of 2026-10-06).

A person read a headspace report in Word and found long gaps between lines,
materials and equipment as bare names in one list, and nothing on a later
start or a return within a repeat. Built on lane RP's protocol double and
ledger helpers (tests/test_lab_report.py); provider calls are a fake client.
"""

from __future__ import annotations

import io

from docx import Document
from docx.shared import Pt

from tests.test_lab_report import _ReportCase
from voiney_lab import experiment_reports as er


class WordSpacingTests(_ReportCase):
    """Decision 1: tighter body, list and table spacing; room around headings."""

    def document(self):
        self.run_steps_one_to_four_then_stop()
        return Document(io.BytesIO(self.store.export_docx(self.report_id, fixture=self.fixture)))

    def test_the_decided_values(self) -> None:
        # Line spacing about 1.15, 3-4 pt after a paragraph, 0-2 pt between
        # list items; headings keep more room before them than after.
        self.assertEqual(er.DOCX_SPACING["p"], (0, 4, 1.15))
        self.assertEqual(er.DOCX_SPACING["list"], (0, 1, 1.15))
        self.assertEqual(er.DOCX_SPACING["table"], (0, 0, 1.0))
        self.assertEqual(er.DOCX_SPACING["note"], (2, 4, 1.15))
        self.assertEqual(er.DOCX_SPACING["h1"], (12, 4, 1.0))
        self.assertEqual(er.DOCX_SPACING["h2"], (8, 3, 1.0))
        self.assertEqual(er.DOCX_SPACING["title"], (0, 8, 1.0))

    def test_word_paragraphs_carry_them(self) -> None:
        document = self.document()
        normal = document.styles["Normal"].paragraph_format
        self.assertEqual((normal.space_after, normal.line_spacing), (Pt(4), 1.15))
        paragraphs = document.paragraphs
        texts = [p.text for p in paragraphs]

        def spacing(paragraph):
            form = paragraph.paragraph_format
            return form.space_before, form.space_after, form.line_spacing

        self.assertEqual(spacing(paragraphs[texts.index("1. 실험 목적")]), (Pt(12), Pt(4), 1.0))
        self.assertEqual(spacing(paragraphs[texts.index("3-2. 수행한 단계")]), (Pt(8), Pt(3), 1.0))
        body = paragraphs[texts.index("1. 실험 목적") + 1]
        self.assertEqual(spacing(body), (Pt(0), Pt(4), 1.15))
        # 5. 고찰 (나): one list; its items are close, its last item is not.
        start = texts.index("(나) 확인이 필요한 점") + 1
        items = []
        while texts[start + len(items)].startswith("• "):
            items.append(paragraphs[start + len(items)])
        self.assertGreaterEqual(len(items), 2)
        self.assertEqual({spacing(p) for p in items[:-1]}, {(Pt(0), Pt(1), 1.15)})
        self.assertEqual(spacing(items[-1]), (Pt(0), Pt(4), 1.15))
        cells = {spacing(p) for table in document.tables for row in table.rows
                 for cell in row.cells for p in cell.paragraphs}
        self.assertEqual(cells, {(Pt(0), Pt(0), 1.0)})

    def test_no_paragraph_falls_back_to_the_templates_ten_points(self) -> None:
        document = self.document()
        paragraphs = list(document.paragraphs) + [
            p for table in document.tables for row in table.rows for cell in row.cells
            for p in cell.paragraphs]
        for paragraph in paragraphs:
            self.assertIsNotNone(paragraph.paragraph_format.space_after, paragraph.text)
            self.assertLessEqual(paragraph.paragraph_format.space_after, Pt(8), paragraph.text)
