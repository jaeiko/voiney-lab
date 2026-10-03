"""One PDF engine behind one module, and what the server records from it.

PyMuPDF replaced pdfium + pdftotext + pypdf on 2026-10-02. These tests pin
what that change promised: the engine is imported in exactly one module and
runs in a child process; a page's result carries text, a footer offset and
blocks; a page with no usable text is marked as needing OCR instead of the
document being refused; and byte identity -- the 64 MiB bound, the SHA-256 of
the exact bytes, the refusal of a file that changed while being read -- is
unchanged.
"""

from __future__ import annotations

import ast
import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import voiney_lab.experiment_protocol_pdf as pdf_module
import voiney_lab.pdf_text_engine as engine_module
from voiney_lab import experiment_protocol as domain
from voiney_lab.experiment_protocol_pdf import (
    MAX_PROTOCOL_PDF_BYTES,
    OCR_REASON_NO_TEXT,
    OCR_REASON_UNREADABLE_GLYPHS,
    PdfTextBlock,
    ProtocolPdfChangedError,
    ProtocolPdfTooLargeError,
    TextVerification,
    clear_protocol_pdf_cache,
    extract_protocol_pdf,
    page_ocr_reason,
)
from voiney_lab.experiment_protocol_store import _decode_domain, _encode_domain
from voiney_lab.protocol_chunk_analysis import plan_protocol_chunks

from tests.test_protocol_claim_analysis import write_lined_pages

PACKAGE_ROOT = Path(engine_module.__file__).parent
IN_GEL = Path("data/runtime/candidate-a-source/in-gel-digestion.pdf")
ANKOM = Path(
    "data/runtime/candidate-a-live-acceptance/objects/sha256/53"
    "/5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a.pdf"
)


def _write_raw_pages(path: Path, contents: tuple[str | None, ...]) -> None:
    """Pages whose content stream is written verbatim; None is a blank page."""

    writer = PdfWriter()
    for content_text in contents:
        page = writer.add_blank_page(width=600, height=792)
        if content_text is None:
            continue
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        content = DecodedStreamObject()
        content.set_data(content_text.encode("ascii"))
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


def _modules_importing(names: set[str]) -> set[str]:
    found = set()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] in names for alias in node.names):
                    found.add(path.name)
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in names:
                    found.add(path.name)
    return found


class _TempDir(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        clear_protocol_pdf_cache()
        self.addCleanup(clear_protocol_pdf_cache)


class OneEngineBehindOneModuleTests(unittest.TestCase):
    def test_only_the_engine_module_imports_pymupdf(self) -> None:
        self.assertEqual(
            _modules_importing({"pymupdf", "fitz"}), {"pdf_text_engine.py"}
        )

    def test_no_module_imports_the_removed_engines(self) -> None:
        self.assertEqual(_modules_importing({"pypdfium2", "pdfium"}), set())
        self.assertNotIn("experiment_protocol_pdf.py", _modules_importing({"pypdf"}))
        # The comparator was a subprocess found on PATH; the module now starts
        # no process of its own -- the engine module does.
        self.assertFalse(hasattr(pdf_module, "subprocess"))
        self.assertFalse(hasattr(pdf_module, "shutil"))

    def test_the_removed_cross_check_is_gone(self) -> None:
        for name in (
            "verify_page_text",
            "canonical_text_census",
            "resolve_unmapped_page_text",
            "unmapped_code_points",
            "COMPARATOR_COMMAND",
            "GlyphResolution",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(pdf_module, name))


class ExtractionShapeTests(_TempDir):
    def test_a_page_carries_text_blocks_and_engine_identity(self) -> None:
        path = self.root / "lined.pdf"
        write_lined_pages(path, (("1 Add buffer.", "2 Mix well."),))
        extraction = extract_protocol_pdf(path)
        page = extraction.pages[0]
        self.assertEqual(page.text, "1 Add buffer.\n2 Mix well.")
        self.assertFalse(page.ocr_required)
        self.assertIsNone(page.ocr_reason)
        self.assertTrue(page.blocks)
        for block in page.blocks:
            self.assertIsInstance(block, PdfTextBlock)
            self.assertLess(block.x0, block.x1)
            self.assertLess(block.y0, block.y1)
            self.assertGreater(block.font_size, 0)
            self.assertIsInstance(block.bold, bool)
        self.assertIn("Add buffer.", "\n".join(block.text for block in page.blocks))
        self.assertEqual(extraction.text_engine, "pymupdf")
        self.assertRegex(extraction.text_engine_version or "", r"^\d+\.\d+")
        # Retired cross-check fields are empty on every new extraction.
        self.assertIsNone(extraction.text_verification)
        self.assertEqual(extraction.divergent_page_numbers, ())
        self.assertEqual(extraction.glyph_resolutions, ())

    def test_a_number_set_apart_from_its_instruction_stays_on_its_line(self) -> None:
        """The one layout rule: same printed line, one line of text."""

        path = self.root / "gap.pdf"
        _write_raw_pages(
            path,
            (
                "BT /F1 12 Tf 72 720 Td (3) Tj ET "
                "BT /F1 12 Tf 140 720 Td (Wash the band.) Tj ET "
                "BT /F1 12 Tf 72 700 Td (4) Tj ET "
                "BT /F1 12 Tf 140 700 Td (Discard the solution.) Tj ET",
            ),
        )
        text = extract_protocol_pdf(path).pages[0].text
        self.assertEqual(text, "3 Wash the band.\n4 Discard the solution.")

    def test_engine_reply_round_trips_through_the_stored_analysis_encoding(self) -> None:
        path = self.root / "lined.pdf"
        write_lined_pages(path, (("1 Add buffer.",),))
        extraction = extract_protocol_pdf(path)
        decoded = _decode_domain(_encode_domain(extraction))
        self.assertEqual(decoded, extraction)


class OcrRequiredPageTests(_TempDir):
    def test_a_blank_page_needs_ocr_and_the_document_is_still_extracted(self) -> None:
        path = self.root / "mixed.pdf"
        _write_raw_pages(
            path,
            ("BT /F1 12 Tf 72 720 Td (1 Add 5 ml of buffer and mix.) Tj ET", None),
        )
        extraction = extract_protocol_pdf(path)
        self.assertEqual(extraction.page_count, 2)
        self.assertFalse(extraction.pages[0].ocr_required)
        self.assertTrue(extraction.pages[1].ocr_required)
        self.assertEqual(extraction.pages[1].ocr_reason, OCR_REASON_NO_TEXT)
        self.assertEqual(extraction.ocr_required_page_numbers, (2,))
        self.assertTrue(any("need OCR: 2" in item for item in extraction.warnings))

    def test_unreadable_glyphs_mark_the_page_not_the_document(self) -> None:
        path = self.root / "glyphs.pdf"
        _write_raw_pages(
            path,
            (
                "BT /F1 12 Tf 72 720 Td (1 Add 5 ml of buffer.) Tj ET",
                "BT /F1 12 Tf 72 720 Td (Add 5 ml " + "\\200\\201\\202\\203" * 3 + ") Tj ET",
            ),
        )
        extraction = extract_protocol_pdf(path)
        # The page is marked; the unreadable characters are not carried as text.
        self.assertNotIn("\ufffd", extraction.pages[1].text)
        self.assertEqual(extraction.pages[1].ocr_reason, OCR_REASON_UNREADABLE_GLYPHS)
        self.assertEqual(extraction.ocr_required_page_numbers, (2,))

    def test_one_unmapped_glyph_marks_the_page_and_never_reaches_the_text(self) -> None:
        """ANKOM page 3 in miniature: one unmapped glyph right before a value.

        A 5% share rule let this through -- one glyph in a page of text is far
        below it -- and the U+FFFD went into the text analysis reads. What
        stood there is unknown, so the page needs OCR and the text carries
        nothing in its place.
        """

        path = self.root / "one-glyph.pdf"
        _write_raw_pages(
            path,
            (
                "BT /F1 12 Tf 72 720 Td (1 Add 5 ml of buffer and mix well.) Tj ET",
                "BT /F1 12 Tf 72 720 Td (2 Incubate for \\20030 min at 37 C, then cool.) Tj ET"
                " BT /F1 10 Tf 72 20 Td (Page 2 footer) Tj ET",
            ),
        )
        extraction = extract_protocol_pdf(path)
        page = extraction.pages[1]
        self.assertTrue(page.ocr_required)
        self.assertEqual(page.ocr_reason, OCR_REASON_UNREADABLE_GLYPHS)
        self.assertEqual(extraction.ocr_required_page_numbers, (2,))
        self.assertFalse(extraction.pages[0].ocr_required)
        self.assertEqual(page.text, "2 Incubate for 30 min at 37 C, then cool.\nPage 2 footer")
        self.assertFalse([b for b in page.blocks if "\ufffd" in b.text])
        # The footer offset still points at the footer once the glyph is gone.
        self.assertEqual(page.text[page.bottom_band_offset :], "Page 2 footer")
        self.assertIn("1 character(s)", page.warning or "")
        self.assertTrue(any("need OCR: 2" in item for item in extraction.warnings))

    def test_a_document_with_an_ocr_page_is_still_admitted_for_analysis(self) -> None:
        path = self.root / "mixed.pdf"
        _write_raw_pages(
            path,
            ("BT /F1 12 Tf 72 720 Td (1 Add 5 ml of buffer and mix.) Tj ET", None),
        )
        plan = plan_protocol_chunks(extract_protocol_pdf(path), "p-1", "r-1")
        self.assertTrue(plan.chunks)

    def test_a_stored_cross_check_verdict_no_longer_refuses_or_gates(self) -> None:
        """An analysis stored with the retired verdict decodes and is not blocked."""

        path = self.root / "lined.pdf"
        write_lined_pages(path, (("1 Add 5 ml of buffer and mix.",),))
        extraction = extract_protocol_pdf(path)
        for verdict in TextVerification:
            with self.subTest(verdict=verdict.value):
                stored = _decode_domain(
                    _encode_domain(replace(extraction, text_verification=verdict))
                )
                self.assertIs(stored.text_verification, verdict)
                self.assertTrue(plan_protocol_chunks(stored, "p-1", "r-1").chunks)

    def test_a_control_code_written_in_a_string_is_layout_not_unreadable(self) -> None:
        path = self.root / "newline.pdf"
        _write_raw_pages(path, ("BT /F1 12 Tf 72 720 Td (1 Add buffer.\n2 Mix.) Tj ET",))
        page = extract_protocol_pdf(path).pages[0]
        self.assertEqual(page.text, "1 Add buffer.\n2 Mix.")
        self.assertFalse(page.ocr_required)

    def test_the_ocr_reason_rule(self) -> None:
        self.assertEqual(page_ocr_reason(""), OCR_REASON_NO_TEXT)
        self.assertEqual(page_ocr_reason(" \n\t"), OCR_REASON_NO_TEXT)
        self.assertIsNone(page_ocr_reason("Add 5 ml."))
        # One U+FFFD is a character nobody can read: the page needs OCR.
        self.assertEqual(
            page_ocr_reason("A" * 999 + "\ufffd"), OCR_REASON_UNREADABLE_GLYPHS
        )
        # Private use and unassigned keep the share rule: one among many is a
        # warning, not an OCR page.
        self.assertIsNone(page_ocr_reason("A" * 99 + "\ue081"))
        self.assertEqual(
            page_ocr_reason("Add" + "\ufffd" * 3), OCR_REASON_UNREADABLE_GLYPHS
        )
        self.assertEqual(
            page_ocr_reason("Add" + "\ue081" * 3), OCR_REASON_UNREADABLE_GLYPHS
        )


class StoredHandlesFromThePreviousEngineTests(_TempDir):
    """An analysis stored under pdfium is refused, never mis-pointed.

    A segment id hashes the page text, the segment's index and its text, so a
    handle made from the previous engine's text can only resolve to the
    identical segment of an identical page, or to nothing. Measured on
    2026-10-03 over the four local sources: 187 pdfium-era handles, 22
    resolved (all on byte-identical pages, to identical text), 165 refused,
    0 resolved to different text.
    """

    def test_a_previous_engine_handle_resolves_identically_or_is_refused(self) -> None:
        from voiney_lab.experiment_protocol_analysis import ProtocolAnalysisEvidenceError
        from voiney_lab.protocol_claim_analysis import (
            generate_page_evidence_segments,
            reopen_evidence_span,
        )

        path = self.root / "lined.pdf"
        write_lined_pages(path, (("1 Add 5 ml of buffer.", "2 Mix well."),))
        current = extract_protocol_pdf(path)
        page = current.pages[0]
        # The previous engine's reading: the same characters, other whitespace.
        previous = replace(
            current,
            pages=(replace(page, text=page.text.replace("\n", " \n")),),
        )
        for reading, resolves in ((previous, False), (current, True)):
            for segment in generate_page_evidence_segments(
                reading, source_revision="pdf-1", page_number=1
            ):
                stored = domain.SourceEvidence(
                    1, segment.text, evidence_segment_ids=(segment.segment_id,)
                )
                with self.subTest(resolves=resolves, index=segment.segment_index):
                    if resolves:
                        self.assertEqual(
                            reopen_evidence_span(current, stored, source_revision="pdf-1"),
                            segment.text,
                        )
                    else:
                        with self.assertRaises(ProtocolAnalysisEvidenceError):
                            reopen_evidence_span(current, stored, source_revision="pdf-1")


class ByteIdentityIsUnchangedTests(_TempDir):
    def test_sha256_and_size_are_of_the_exact_bytes(self) -> None:
        path = self.root / "lined.pdf"
        write_lined_pages(path, (("1 Add buffer.",),))
        data = path.read_bytes()
        extraction = extract_protocol_pdf(path)
        self.assertEqual(extraction.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(extraction.byte_size, len(data))

    def test_the_size_bound_is_checked_before_the_engine_runs(self) -> None:
        path = self.root / "large.pdf"
        path.write_bytes(b"%PDF-")
        with path.open("r+b") as stream:
            stream.truncate(MAX_PROTOCOL_PDF_BYTES + 1)
        with patch.object(engine_module, "read_document") as engine:
            with self.assertRaises(ProtocolPdfTooLargeError):
                extract_protocol_pdf(path)
        engine.assert_not_called()

    def test_a_file_replaced_while_the_engine_reads_it_is_refused(self) -> None:
        path = self.root / "lined.pdf"
        write_lined_pages(path, (("1 Add buffer.",),))
        other = self.root / "other.pdf"
        write_lined_pages(other, (("1 Something else entirely.",),))
        real = engine_module.read_document

        def swap_then_read(source):
            other.replace(path)
            return real(source)

        with patch.object(engine_module, "read_document", swap_then_read):
            with self.assertRaises(ProtocolPdfChangedError):
                extract_protocol_pdf(path)


class OneProcessPerDocumentTests(_TempDir):
    THREADS = 6

    def setUp(self) -> None:
        super().setUp()
        self.sources = []
        for index in range(3):
            path = self.root / f"source-{index}.pdf"
            write_lined_pages(path, ((f"{index + 1} Add buffer {index}.", "Mix."),))
            self.sources.append(path)

    def test_each_extraction_gets_its_own_engine_process(self) -> None:
        import subprocess

        real = subprocess.run
        started: list[tuple] = []

        def counting_run(command, **kwargs):
            started.append(tuple(command))
            return real(command, **kwargs)

        with patch.object(engine_module.subprocess, "run", counting_run):
            for source in self.sources:
                clear_protocol_pdf_cache()
                extract_protocol_pdf(source)
        engines = [c for c in started if c[1:] == ("-m", "voiney_lab.pdf_text_engine")]
        self.assertEqual(len(engines), len(self.sources))
        self.assertEqual(len(engines), len(started), "no other process is started")

    def test_concurrent_extraction_returns_what_serial_extraction_returns(self) -> None:
        serial = {
            source.name: tuple(page.text for page in extract_protocol_pdf(source).pages)
            for source in self.sources
        }
        concurrent: dict[str, tuple[str, ...]] = {}
        guard = threading.Lock()

        def extract(index: int) -> None:
            source = self.sources[index % len(self.sources)]
            clear_protocol_pdf_cache()
            pages = tuple(page.text for page in extract_protocol_pdf(source).pages)
            with guard:
                previous = concurrent.setdefault(source.name, pages)
                assert previous == pages, source.name

        with ThreadPoolExecutor(max_workers=self.THREADS) as pool:
            list(pool.map(extract, range(self.THREADS)))
        self.assertEqual(concurrent, serial)


class RenderForOcrTests(_TempDir):
    def test_a_page_renders_to_png_bytes_in_memory(self) -> None:
        path = self.root / "lined.pdf"
        write_lined_pages(path, (("1 Add buffer.",),))
        before = set(self.root.iterdir())
        png = engine_module.render_page_png(path, 1, dpi=72)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(set(self.root.iterdir()), before, "nothing written to disk")

    def test_a_page_outside_the_document_is_a_document_error(self) -> None:
        path = self.root / "lined.pdf"
        write_lined_pages(path, (("1 Add buffer.",),))
        with self.assertRaises(engine_module.PdfEngineDocumentError):
            engine_module.render_page_png(path, 2, dpi=72)


class LocalSourceTextTests(unittest.TestCase):
    """Values the earlier engines lost, read from the real sources."""

    def test_in_gel_values_read_correctly(self) -> None:
        if not IN_GEL.is_file():
            self.skipTest(f"{IN_GEL} is not present.")
        extraction = extract_protocol_pdf(IN_GEL)
        text = "".join(page.text for page in extraction.pages)
        self.assertFalse([c for c in text if c == "\ufffd" or 0xE000 <= ord(c) <= 0xF8FF])
        self.assertIn("(50:49:1)", extraction.pages[8].text)
        self.assertIn("00:30:00", extraction.pages[8].text)
        self.assertEqual(extraction.ocr_required_page_numbers, ())

    def test_ankom_values_read_correctly(self) -> None:
        if not ANKOM.is_file():
            self.skipTest(f"{ANKOM} is not present.")
        extraction = extract_protocol_pdf(ANKOM)
        text = "".join(page.text for page in extraction.pages)
        self.assertFalse([c for c in text if c == "\ufffd" or 0xE000 <= ord(c) <= 0xF8FF])
        self.assertIn("72 h at 65", extraction.pages[2].text)
        # The source sets "alpha-" at a line end; PyMuPDF keeps that line break.
        # The hyphen is the word's own, so only whitespace is set aside here.
        self.assertIn(
            "alpha-amylaseandenough",
            "".join(extraction.pages[8].text.split()),
        )

    def test_ankom_page_3_needs_ocr_for_the_glyphs_it_cannot_map(self) -> None:
        """Two glyphs before a duration have no Unicode mapping in the document.

        Measured 2026-10-03: code 0x00 of a Type3 font, glyph /g0 -- the
        font's missing-glyph box -- absent from the font's ToUnicode. The
        previous engine dropped them without a word. Nothing the document
        declares says what they were, so page 3 waits for OCR.
        """

        if not ANKOM.is_file():
            self.skipTest(f"{ANKOM} is not present.")
        extraction = extract_protocol_pdf(ANKOM)
        page = extraction.pages[2]
        self.assertTrue(page.ocr_required)
        self.assertEqual(page.ocr_reason, OCR_REASON_UNREADABLE_GLYPHS)
        self.assertEqual(extraction.ocr_required_page_numbers, (3,))
        self.assertNotIn("�", page.text)
        self.assertFalse([b for b in page.blocks if "�" in b.text])
        self.assertIn("2 character(s)", page.warning or "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
