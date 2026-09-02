"""Focused tests for local Protocol PDF extraction and byte identity."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from voice_workflow_agent.experiment_protocol_pdf import (
    MAX_PROTOCOL_PDF_BYTES,
    PDF_MEDIA_TYPE,
    ProtocolPdfEncryptedError,
    ProtocolPdfExtraction,
    ProtocolPdfMalformedError,
    ProtocolPdfNotFoundError,
    ProtocolPdfNotRegularFileError,
    ProtocolPdfTooLargeError,
    ProtocolPdfTypeError,
    extract_protocol_pdf,
)


_FIXTURE_PAGE_WIDTH = 4000  # wide enough that one unwrapped fixture line
# is never clipped: a bounded extractor would otherwise drop the tail and
# disagree with an unbounded one on synthetic input only.


def _write_pdf(
    path: Path,
    page_texts: tuple[str | None, ...] = ("Page one marker",),
    *,
    metadata: dict[str, str] | None = None,
    password: str | None = None,
) -> bytes:
    writer = PdfWriter()
    for page_text in page_texts:
        page = writer.add_blank_page(width=_FIXTURE_PAGE_WIDTH, height=792)
        if page_text is None:
            continue
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        content = DecodedStreamObject()
        safe_text = page_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.set_data(
            f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("ascii")
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    if metadata:
        writer.add_metadata(metadata)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as stream:
        writer.write(stream)
    return path.read_bytes()


class ExperimentProtocolPdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_identity_size_media_type_and_checksum_are_deterministic(self):
        path = self.root / "fixture.pdf"
        pdf_bytes = _write_pdf(path)

        first = extract_protocol_pdf(path)
        second = extract_protocol_pdf(path)

        self.assertEqual(first.original_filename, "fixture.pdf")
        self.assertEqual(first.byte_size, len(pdf_bytes))
        self.assertEqual(first.sha256, hashlib.sha256(pdf_bytes).hexdigest())
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.media_type, PDF_MEDIA_TYPE)

    def test_different_bytes_produce_different_checksums(self):
        first_path = self.root / "first.pdf"
        second_path = self.root / "second.pdf"
        _write_pdf(first_path, ("First marker",))
        _write_pdf(second_path, ("Second marker",))

        self.assertNotEqual(
            extract_protocol_pdf(first_path).sha256,
            extract_protocol_pdf(second_path).sha256,
        )

    def test_page_count_mapping_and_boundaries_are_preserved(self):
        path = self.root / "pages.pdf"
        _write_pdf(path, ("First page only", "Second page only"))

        result = extract_protocol_pdf(path)

        self.assertEqual(result.page_count, 2)
        self.assertTrue(result.all_pages_inspected)
        self.assertEqual(
            [page.source_page_number for page in result.pages],
            [1, 2],
        )
        self.assertIn("First page only", result.pages[0].text)
        self.assertNotIn("Second page only", result.pages[0].text)
        self.assertIn("Second page only", result.pages[1].text)
        self.assertNotIn("First page only", result.pages[1].text)

    def test_missing_optional_metadata_and_empty_page_do_not_fail_or_use_ocr(self):
        path = self.root / "empty.pdf"
        _write_pdf(path, (None,))

        result = extract_protocol_pdf(path)

        self.assertIsNone(result.metadata.title)
        self.assertIsNone(result.metadata.author)
        self.assertTrue(result.pages[0].text_empty)
        self.assertEqual(result.pages[0].text, "")
        self.assertIsNone(result.pages[0].warning)
        self.assertEqual(result.non_empty_page_count, 0)

    def test_embedded_metadata_is_read_as_source_values(self):
        path = self.root / "metadata.pdf"
        _write_pdf(
            path,
            metadata={
                "/Title": "Fixture title",
                "/Author": "Fixture author",
                "/Subject": "Fixture subject",
                "/Creator": "Fixture creator",
                "/Producer": "Fixture producer",
                "/CreationDate": "D:20260102030405Z",
                "/ModDate": "D:20260203040506Z",
            },
        )

        metadata = extract_protocol_pdf(path).metadata

        self.assertEqual(metadata.title, "Fixture title")
        self.assertEqual(metadata.author, "Fixture author")
        self.assertEqual(metadata.subject, "Fixture subject")
        self.assertEqual(metadata.creator, "Fixture creator")
        self.assertEqual(metadata.producer, "Fixture producer")
        self.assertEqual(metadata.creation_date, "D:20260102030405Z")
        self.assertEqual(metadata.modification_date, "D:20260203040506Z")

    def test_missing_file_is_rejected(self):
        with self.assertRaises(ProtocolPdfNotFoundError) as context:
            extract_protocol_pdf(self.root / "missing.pdf")
        self.assertEqual(context.exception.code, "protocol_pdf_not_found")

    def test_non_regular_path_is_rejected(self):
        with self.assertRaises(ProtocolPdfNotRegularFileError):
            extract_protocol_pdf(self.root)

    def test_non_pdf_content_is_rejected_even_with_pdf_extension(self):
        path = self.root / "not-really.pdf"
        path.write_bytes(b"plain text is not a PDF")
        with self.assertRaises(ProtocolPdfTypeError):
            extract_protocol_pdf(path)

    def test_malformed_or_truncated_pdf_is_rejected(self):
        path = self.root / "truncated.pdf"
        data = _write_pdf(path)
        path.write_bytes(data[:-64])
        with self.assertRaises(ProtocolPdfMalformedError):
            extract_protocol_pdf(path)

    def test_password_encrypted_pdf_is_rejected(self):
        path = self.root / "encrypted.pdf"
        _write_pdf(path, password="secret")
        with self.assertRaises(ProtocolPdfEncryptedError) as context:
            extract_protocol_pdf(path)
        self.assertEqual(context.exception.code, "protocol_pdf_encrypted")

    def test_file_larger_than_64_mib_is_rejected_before_parsing(self):
        path = self.root / "large.pdf"
        path.write_bytes(b"%PDF-")
        with path.open("r+b") as stream:
            stream.truncate(MAX_PROTOCOL_PDF_BYTES + 1)
        with self.assertRaises(ProtocolPdfTooLargeError) as context:
            extract_protocol_pdf(path)
        self.assertIn("64 MiB", str(context.exception))

    def test_checksum_model_has_no_trust_or_approval_semantics(self):
        field_names = {field.name for field in fields(ProtocolPdfExtraction)}
        self.assertTrue(
            field_names.isdisjoint(
                {
                    "trusted",
                    "approved",
                    "official",
                    "current",
                    "approval_status",
                    "trust_status",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
