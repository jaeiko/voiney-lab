"""Tests for test-only synthetic normalized document manifests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from voice_workflow_agent.document_store import connect, ingest_manifest
from voice_workflow_agent.safety_documents import ManifestValidationError


def synthetic_document(**overrides):
    """Return a fictional record used only in temporary test databases."""
    document = {
        "document_id": "FICTIONAL-TEST-SDS-001", "document_family_id": "FICTIONAL-TEST-SDS-FAMILY",
        "canonical_source_id": "FICTIONAL-TEST-SDS-SOURCE", "canonical_version": "1.0",
        "document_type": "supplier_sds", "title": "FICTIONAL TEST FIXTURE — NOT OPERATIONAL",
        "issuer": "Fictional Test Supplier", "manufacturer": "Fictional Test Manufacturer",
        "product_name": "Fictional Solvent", "product_code": "TEST-A100", "cas_numbers": ["111-11-1"],
        "version": "1.0", "language": "en", "facility_id": "TEST-FACILITY",
        "source_authority": "test_fixture", "approval_status": "approved", "usage_scope": "test_only",
        "effective_at": "2026-01-01T00:00:00+00:00", "review_due_at": "2030-01-01T00:00:00+00:00",
        "source_path": "tests/fixtures/fictional.json", "source_uri": "test://fictional/sds",
        "source_checksum": "sha256:fictional-test-checksum", "translation_status": "original", "active": True,
        "aliases": [{"alias": "가상 용제", "language": "ko", "approved": True}],
        "sections": [{"section_code": "SDS-04", "section_title": "FICTIONAL first aid fixture", "page_start": 3,
                      "page_end": 4, "content": "FICTIONAL TEST CONTENT. NOT SAFETY GUIDANCE.",
                      "topic": "first_aid", "keywords": ["fixture"]}],
    }
    document.update(overrides)
    return document


class DocumentIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "catalog.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_successful_ingestion_and_metadata_preservation(self):
        summary = ingest_manifest({"documents": [synthetic_document()]}, self.db)
        self.assertEqual(summary, {"documents": 1, "sections": 1, "aliases": 1})
        with connect(self.db) as connection:
            doc = connection.execute("SELECT * FROM documents").fetchone()
            section = connection.execute("SELECT * FROM sections").fetchone()
        self.assertEqual((doc["version"], doc["source_checksum"], doc["source_uri"]),
                         ("1.0", "sha256:fictional-test-checksum", "test://fictional/sds"))
        self.assertEqual((section["page_start"], section["page_end"]), (3, 4))

    def test_validation_failure_writes_nothing(self):
        good = synthetic_document()
        bad = synthetic_document(document_id="FICTIONAL-BAD", sections=[{
            "section_code": "SDS-06", "section_title": "bad", "page_start": 0, "page_end": 1,
            "content": "FICTIONAL", "topic": "spill", "keywords": [],
        }])
        with self.assertRaises(ManifestValidationError):
            ingest_manifest({"documents": [good, bad]}, self.db)
        self.assertFalse(self.db.exists())

    def test_database_constraint_rolls_back_complete_manifest(self):
        ingest_manifest({"documents": [synthetic_document()]}, self.db)
        second = synthetic_document(document_id="FICTIONAL-SECOND", version="2.0")
        duplicate = synthetic_document()
        with self.assertRaises(sqlite3.IntegrityError):
            ingest_manifest({"documents": [second, duplicate]}, self.db)
        with connect(self.db) as connection:
            count = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
        self.assertEqual(count, 1)

    def test_duplicate_identity_in_manifest_is_rejected(self):
        with self.assertRaises(ManifestValidationError):
            ingest_manifest({"documents": [synthetic_document(), synthetic_document()]}, self.db)

    def test_language_variants_share_canonical_version(self):
        vietnamese = synthetic_document(
            language="vi", translation_status="human_reviewed",
            translation_of_document_id="FICTIONAL-TEST-SDS-001",
        )
        summary = ingest_manifest({"documents": [synthetic_document(), vietnamese]}, self.db)
        self.assertEqual(summary["documents"], 2)
        with connect(self.db) as connection:
            rows = connection.execute(
                "SELECT canonical_source_id, canonical_version, language, translation_of_document_id "
                "FROM documents ORDER BY language"
            ).fetchall()
        self.assertEqual({row["language"] for row in rows}, {"en", "vi"})
        self.assertEqual({row["canonical_version"] for row in rows}, {"1.0"})

    def test_reviewed_translation_requires_source_relationship(self):
        with self.assertRaises(ManifestValidationError):
            ingest_manifest({"documents": [synthetic_document(
                language="vi", translation_status="human_reviewed"
            )]}, self.db)

    def test_translation_rejects_nonexistent_original(self):
        translation = synthetic_document(
            document_id="FICTIONAL-VI", language="vi", translation_status="human_reviewed",
            translation_of_document_id="MISSING-ORIGINAL",
        )
        with self.assertRaises(ManifestValidationError):
            ingest_manifest({"documents": [translation]}, self.db)

    def test_translation_rejects_reference_to_translation(self):
        original = synthetic_document()
        first_translation = synthetic_document(
            document_id="FICTIONAL-VI", language="vi", translation_status="human_reviewed",
            translation_of_document_id="FICTIONAL-TEST-SDS-001",
        )
        second_translation = synthetic_document(
            document_id="FICTIONAL-KO", language="ko", translation_status="human_reviewed",
            translation_of_document_id="FICTIONAL-VI",
        )
        with self.assertRaises(ManifestValidationError):
            ingest_manifest({"documents": [original, first_translation, second_translation]}, self.db)

    def test_translation_rejects_mismatched_canonical_source(self):
        translation = synthetic_document(
            document_id="FICTIONAL-VI", language="vi", translation_status="human_reviewed",
            translation_of_document_id="FICTIONAL-TEST-SDS-001",
            canonical_source_id="DIFFERENT-SOURCE",
        )
        with self.assertRaises(ManifestValidationError):
            ingest_manifest({"documents": [synthetic_document(), translation]}, self.db)

    def test_translation_rejects_mismatched_canonical_version(self):
        translation = synthetic_document(
            document_id="FICTIONAL-VI", language="vi", translation_status="human_reviewed",
            translation_of_document_id="FICTIONAL-TEST-SDS-001", canonical_version="2.0",
        )
        with self.assertRaises(ManifestValidationError):
            ingest_manifest({"documents": [synthetic_document(), translation]}, self.db)

    def test_translation_can_reference_original_from_prior_ingestion(self):
        ingest_manifest({"documents": [synthetic_document()]}, self.db)
        translation = synthetic_document(
            document_id="FICTIONAL-VI", language="vi", translation_status="human_reviewed",
            translation_of_document_id="FICTIONAL-TEST-SDS-001",
        )
        summary = ingest_manifest({"documents": [translation]}, self.db)
        self.assertEqual(summary["documents"], 1)

    def test_missing_source_location_is_rejected(self):
        with self.assertRaises(ManifestValidationError):
            ingest_manifest({"documents": [synthetic_document(source_path=None, source_uri=None)]}, self.db)


if __name__ == "__main__":
    unittest.main()
