"""One PDF library, PyMuPDF, reads every Protocol PDF (lane PX, decision 2).

Human decision of 2026-10-06: the PDF is read -- page text, text positions
and blocks, picture positions, whether a text layer exists -- by PyMuPDF
alone, wrapped in ``pdf_text_engine`` so a later library change touches that
module only; the three-way engine comparison that refused a document is
gone, and a document is refused only when it truly cannot be read. These
tests pin that the comparison rules tuned so far -- the line-end hyphen, the
OCR Hangul line break, the page boundary, the running-footer band -- hold on
the text PyMuPDF produces, and that picture positions come through the same
engine without changing what a stored analysis carries.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path

from tests.test_pdf_text_engine import _write_raw_pages
from voiney_lab import experiment_protocol as domain
from voiney_lab import experiment_protocol_analysis as analysis_module
from voiney_lab import pdf_text_engine as engine_module
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfPage,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
)
from voiney_lab.experiment_protocol_store import _decode_domain, _encode_domain
from voiney_lab.pdf_text_engine import PdfImageBox, read_document

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(engine_module.__file__).parent


def _line(y: int, text: str, x: int = 72) -> str:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"BT /F1 12 Tf {x} {y} Td ({escaped}) Tj ET "


class _TempDir(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        clear_protocol_pdf_cache()

    def tearDown(self) -> None:
        clear_protocol_pdf_cache()
        self.temp.cleanup()


class OneLibraryTests(unittest.TestCase):
    def test_pymupdf_is_imported_by_the_engine_module_alone(self) -> None:
        importers = set()
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                names = (
                    [alias.name for alias in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                )
                if any(name.split(".")[0] in {"pymupdf", "fitz"} for name in names):
                    importers.add(path.name)
        self.assertEqual(importers, {"pdf_text_engine.py"})

    def test_the_dependency_is_pinned_and_its_licence_decision_is_written_down(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(pyproject, r'"pymupdf==\d+\.\d+\.\d+"')
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^pymupdf==\d+\.\d+\.\d+$")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("AGPL-3.0 / commercial dual licence", readme)
        self.assertIn("commercial PyMuPDF licence is bought or the engine is replaced", readme)


class PicturePositionsTests(_TempDir):
    def _pdf_with_a_picture(self) -> Path:
        import pymupdf  # the test builds its fixture with the same library

        path = self.root / "picture.pdf"
        document = pymupdf.open()
        page = document.new_page(width=600, height=792)
        page.insert_text((72, 72), "1 Add buffer to the gel piece.", fontsize=12)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 0)
        pixmap.set_rect(pixmap.irect, (200, 30, 30))
        page.insert_image(pymupdf.Rect(100, 300, 300, 450), stream=pixmap.tobytes("png"))
        document.save(path)
        document.close()
        return path

    def test_the_engine_reports_where_a_picture_sits(self) -> None:
        path = self._pdf_with_a_picture()
        page = read_document(path).pages[0]
        self.assertEqual(len(page.images), 1)
        box = page.images[0]
        self.assertIsInstance(box, PdfImageBox)
        self.assertAlmostEqual(box.x0, 100, delta=1)
        self.assertAlmostEqual(box.y0, 300, delta=1)
        self.assertAlmostEqual(box.x1, 300, delta=1)
        self.assertAlmostEqual(box.y1, 450, delta=1)
        self.assertEqual(page.text, "1 Add buffer to the gel piece.")

    def test_a_page_without_pictures_reports_none(self) -> None:
        path = self.root / "plain.pdf"
        _write_raw_pages(path, (_line(720, "1 Add buffer."),))
        self.assertEqual(read_document(path).pages[0].images, ())

    def test_a_stored_analysis_is_unchanged_by_picture_positions(self) -> None:
        # Pictures stay on the engine's page; the recorded page has no such
        # field, so an analysis stored before this reads back identically.
        self.assertNotIn("images", {field.name for field in fields(ProtocolPdfPage)})
        path = self._pdf_with_a_picture()
        extraction = extract_protocol_pdf(path)
        self.assertEqual(_decode_domain(_encode_domain(extraction)), extraction)


class ComparisonRulesOnPyMuPdfTextTests(_TempDir):
    """The rules tuned on the previous engine's text hold on PyMuPDF's."""

    def test_a_hyphen_at_the_line_end_compares_as_one_range(self) -> None:
        path = self.root / "hyphen.pdf"
        _write_raw_pages(path, (
            _line(720, "3 Incubate the plate for 5-")
            + _line(706, "10 min at 37 degrees.")
            + _line(692, "4 Add alpha-")
            + _line(678, "amylase."),
        ))
        extraction = extract_protocol_pdf(path)
        text = extraction.pages[0].text
        self.assertEqual(
            text, "3 Incubate the plate for 5-\n10 min at 37 degrees.\n4 Add alpha-\namylase.")
        self.assertTrue(analysis_module._claim_occurs_in_text("5-10 min", text))
        verified = analysis_module._verified_evidence(
            domain.SourceEvidence(1, "Incubate the plate for 5-10 min at 37 degrees."),
            extraction)
        self.assertEqual(verified.source_excerpt, "Incubate the plate for 5-\n10 min at 37 degrees.")
        verified = analysis_module._verified_evidence(
            domain.SourceEvidence(1, "Add alpha-amylase."), extraction)
        self.assertEqual(verified.source_excerpt, "Add alpha-\namylase.")
        # The hyphen itself is never dropped.
        self.assertFalse(analysis_module._claim_occurs_in_text("510 min", text))

    def test_the_running_footer_band_is_found_by_geometry(self) -> None:
        path = self.root / "footer.pdf"
        _write_raw_pages(path, (
            _line(720, "1 Add buffer.") + _line(706, "2 Mix well.")
            + _line(20, "example.org | protocol 1/1"),
        ))
        page = extract_protocol_pdf(path).pages[0]
        self.assertIsNotNone(page.bottom_band_offset)
        self.assertEqual(page.text[page.bottom_band_offset:], "example.org | protocol 1/1")
        self.assertEqual(page.text[:page.bottom_band_offset].strip(), "1 Add buffer.\n2 Mix well.")

    def test_a_sentence_cut_at_the_page_end_is_found_in_the_joined_pages(self) -> None:
        path = self.root / "cut.pdf"
        _write_raw_pages(path, (
            _line(720, "Peptide extraction")
            + _line(706, "24 Spin down the digest. Keep the solution, which will contain the")
            + _line(20, "example.org | protocol 1/2"),
            _line(720, "peptides. Pool the peptides.")
            + _line(706, "25 Dry the extracted peptides.")
            + _line(20, "example.org | protocol 2/2"),
        ))
        extraction = extract_protocol_pdf(path)
        statement = "Keep the solution, which will contain the peptides."
        cut = analysis_module._statement_across_page_end(statement, extraction, 1)
        self.assertIsNotNone(cut)
        verified = analysis_module._verified_evidence(
            domain.SourceEvidence(1, statement), extraction)
        self.assertEqual(verified.source_page_number, 1)
        self.assertEqual(verified.continued_on_page_number, 2)
        self.assertEqual(verified.source_excerpt, "Keep the solution, which will contain the")
        self.assertEqual(verified.continued_excerpt, "peptides.")
        # The footer between the two pages is not part of the joined text.
        self.assertIsNone(analysis_module._statement_across_page_end(
            "contain the example.org", extraction, 1))
        # An invented continuation is still refused.
        self.assertIsNone(analysis_module._statement_across_page_end(
            "Keep the solution, which will contain the buffer.", extraction, 1))

    def test_an_ocr_page_still_joins_a_hangul_line_break_in_comparison_only(self) -> None:
        path = self.root / "scan.pdf"
        _write_raw_pages(path, (_line(720, "1 Add buffer."), None))
        extraction = extract_protocol_pdf(path)
        # Page 2 has no text layer: marked for OCR, the document kept.
        self.assertEqual(extraction.ocr_required_page_numbers, (2,))
        scanned = replace(
            extraction.pages[1], text="개봉한 날\n짜를 적는다.", text_empty=False,
            ocr_required=False, ocr_reason=None, ocr_derived=True)
        self.assertTrue(analysis_module._claim_occurs_in_text(
            "개봉한 날짜를 적는다.", scanned.text, ocr_derived=True))
        self.assertFalse(analysis_module._claim_occurs_in_text(
            "개봉한 날짜를 적는다.", scanned.text, ocr_derived=False))
        self.assertEqual(scanned.text, "개봉한 날\n짜를 적는다.")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
