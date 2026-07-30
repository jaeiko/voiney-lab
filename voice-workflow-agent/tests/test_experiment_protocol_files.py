"""Focused tests for immutable content-addressed Protocol PDF storage."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from voice_workflow_agent.experiment_protocol_files import (
    MissingProtocolObjectError,
    ProtocolFileStore,
    ProtocolObjectIntegrityError,
    ProtocolObjectWriteError,
    ProtocolPdfObject,
)


def write_pdf(
    path: Path,
    *,
    title: str = "Immutable fixture",
) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": title})
    with path.open("wb") as stream:
        writer.write(stream)
    return path.read_bytes()


class ProtocolFileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProtocolFileStore(self.root / "protocol-data")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_atomic_storage_verifies_identity_and_leaves_no_temporary_file(self):
        source = self.root / "source.pdf"
        source_bytes = write_pdf(source)

        result = self.store.store(source)

        self.assertFalse(result.deduplicated)
        self.assertEqual(result.object.byte_size, len(source_bytes))
        self.assertEqual(
            result.object.checksum,
            hashlib.sha256(source_bytes).hexdigest(),
        )
        self.assertEqual(
            self.store.verify_object(
                result.object.checksum,
                expected_size=len(source_bytes),
            ),
            result.object,
        )
        object_root = self.root / "protocol-data" / "objects" / "sha256"
        self.assertEqual(len(tuple(object_root.rglob("*.pdf"))), 1)
        self.assertEqual(len(tuple(object_root.rglob("*.tmp"))), 0)

    def test_injected_write_failure_publishes_no_partial_final_object(self):
        source = self.root / "source.pdf"
        write_pdf(source)

        def fail_after_partial_write(source_stream, destination_stream):
            destination_stream.write(b"partial")
            raise OSError("injected local write failure")

        with patch(
            "voice_workflow_agent.experiment_protocol_files._copy_pdf_bytes",
            side_effect=fail_after_partial_write,
        ):
            with self.assertRaises(ProtocolObjectWriteError) as context:
                self.store.store(source)

        object_root = self.root / "protocol-data" / "objects" / "sha256"
        self.assertEqual(tuple(object_root.rglob("*.pdf")), ())
        self.assertEqual(tuple(object_root.rglob("*.tmp")), ())
        self.assertNotIn("injected", str(context.exception))
        self.assertNotIn(str(self.root), str(context.exception))

    def test_unlink_failure_is_best_effort_and_never_publishes_partial_bytes(self):
        source = self.root / "source.pdf"
        write_pdf(source)

        def fail_after_partial_write(source_stream, destination_stream):
            destination_stream.write(b"partial")
            raise OSError("injected private write failure")

        with patch(
            "voice_workflow_agent.experiment_protocol_files._copy_pdf_bytes",
            side_effect=fail_after_partial_write,
        ), patch.object(
            Path,
            "unlink",
            side_effect=OSError("injected private unlink failure"),
        ):
            with self.assertRaises(ProtocolObjectWriteError) as context:
                self.store.store(source)

        object_root = self.root / "protocol-data" / "objects" / "sha256"
        self.assertEqual(tuple(object_root.rglob("*.pdf")), ())
        temporary_files = tuple(object_root.rglob("*.tmp"))
        self.assertEqual(len(temporary_files), 1)
        self.assertEqual(temporary_files[0].read_bytes(), b"partial")
        self.assertEqual(
            str(context.exception),
            "Protocol object could not be written atomically.",
        )
        self.assertNotIn("injected", str(context.exception))
        self.assertNotIn(str(self.root), str(context.exception))
        self.assertNotIn("Traceback", str(context.exception))

    def test_identical_bytes_under_different_names_deduplicate(self):
        first = self.root / "first.pdf"
        second = self.root / "renamed.pdf"
        source_bytes = write_pdf(first)
        second.write_bytes(source_bytes)

        first_result = self.store.store(first)
        second_result = self.store.store(second)

        self.assertEqual(first_result.object, second_result.object)
        self.assertFalse(first_result.deduplicated)
        self.assertTrue(second_result.deduplicated)
        self.assertEqual(first_result.original_filename, "first.pdf")
        self.assertEqual(second_result.original_filename, "renamed.pdf")

    def test_different_bytes_create_different_objects(self):
        first = self.root / "first.pdf"
        second = self.root / "second.pdf"
        write_pdf(first, title="First")
        write_pdf(second, title="Second")

        first_result = self.store.store(first)
        second_result = self.store.store(second)

        self.assertNotEqual(
            first_result.object.checksum,
            second_result.object.checksum,
        )
        self.assertNotEqual(
            first_result.object.relative_path,
            second_result.object.relative_path,
        )

    def test_source_pdf_is_never_modified(self):
        source = self.root / "source.pdf"
        source_bytes = write_pdf(source)
        before = hashlib.sha256(source_bytes).hexdigest()

        self.store.store(source)

        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_corrupt_existing_object_is_detected_without_overwrite(self):
        source = self.root / "source.pdf"
        source_bytes = write_pdf(source)
        result = self.store.store(source)
        target = self.root / "protocol-data" / result.object.relative_path
        target.write_bytes(b"corrupt")

        with self.assertRaises(ProtocolObjectIntegrityError):
            self.store.store(source)

        self.assertEqual(target.read_bytes(), b"corrupt")
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_original_filename_cannot_select_object_destination(self):
        source = self.root / "nested" / "..evil-name.pdf"
        write_pdf(source)

        result = self.store.store(source)

        relative = Path(result.object.relative_path)
        self.assertEqual(relative.parts[:2], ("objects", "sha256"))
        self.assertEqual(relative.parts[2], result.object.checksum[:2])
        self.assertEqual(relative.name, f"{result.object.checksum}.pdf")
        self.assertNotIn("evil", result.object.relative_path)

    def test_concurrent_identical_writes_converge_on_one_valid_object(self):
        source = self.root / "source.pdf"
        source_bytes = write_pdf(source)

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = tuple(
                executor.map(lambda _: self.store.store(source), range(12))
            )

        self.assertEqual(
            {result.object.checksum for result in results},
            {hashlib.sha256(source_bytes).hexdigest()},
        )
        self.assertEqual(
            len(
                tuple(
                    (
                        self.root
                        / "protocol-data"
                        / "objects"
                        / "sha256"
                    ).rglob("*.pdf")
                )
            ),
            1,
        )
        self.store.verify_object(
            results[0].object.checksum,
            expected_size=len(source_bytes),
        )

    def test_missing_object_and_wrong_size_are_sanitized(self):
        with self.assertRaises(MissingProtocolObjectError) as missing:
            self.store.verify_object("b" * 64)
        self.assertNotIn(str(self.root), str(missing.exception))

        source = self.root / "source.pdf"
        result = self.store.store(source) if source.exists() else None
        if result is None:
            write_pdf(source)
            result = self.store.store(source)
        with self.assertRaises(ProtocolObjectIntegrityError):
            self.store.verify_object(
                result.object.checksum,
                expected_size=result.object.byte_size + 1,
            )

    def test_checksum_record_has_identity_only_fields_and_no_mutation_api(self):
        field_names = {field.name for field in fields(ProtocolPdfObject)}
        self.assertTrue(
            field_names.isdisjoint(
                {
                    "trusted",
                    "approved",
                    "official",
                    "current",
                    "approval_status",
                }
            )
        )
        self.assertFalse(hasattr(self.store, "delete"))
        self.assertFalse(hasattr(self.store, "update"))


if __name__ == "__main__":
    unittest.main()
