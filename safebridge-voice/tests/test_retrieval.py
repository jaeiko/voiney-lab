"""Fail-closed retrieval tests using fictional records in temporary databases only."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from safebridge_voice.document_store import ingest_manifest
from safebridge_voice.retrieval import search_safety_documents

from tests.test_document_ingestion import synthetic_document


def operational_document(**overrides):
    """Fictional operational-scope metadata used only inside a temporary test DB."""
    base = synthetic_document(
        document_id="FICTIONAL-OP-SDS-001", document_family_id="FICTIONAL-OP-SDS-FAMILY",
        canonical_source_id="FICTIONAL-OP-SDS-SOURCE",
        title="FICTIONAL OPERATIONAL-SCOPE TEST RECORD — NOT REAL GUIDANCE",
        source_authority="supplier", usage_scope="operational", source_uri="test://fictional/operational",
        source_path="tests/generated/fictional-operational-record.json",
    )
    base.update(overrides)
    return base


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "catalog.sqlite"
        self.now = datetime(2027, 1, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, *documents):
        ingest_manifest({"documents": list(documents)}, self.db)

    def search(self, query, language="en", **kwargs):
        return search_safety_documents(
            query, language, self.db, usage_scope="operational", now=self.now, **kwargs
        )

    def assert_blocked(self, result, status):
        self.assertEqual(result, {"status": status, "answerable": False, "matches": []})

    def test_exact_product_code_and_source_fields(self):
        self.ingest(operational_document())
        result = self.search("first aid for TEST-A100")
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["answerable"])
        match = result["matches"][0]
        self.assertEqual(match["content"], "FICTIONAL TEST CONTENT. NOT SAFETY GUIDANCE.")
        self.assertEqual((match["version"], match["source_checksum"], match["page_start"], match["page_end"]),
                         ("1.0", "sha256:fictional-test-checksum", 3, 4))
        self.assertEqual(
            (match["canonical_source_id"], match["canonical_version"], match["language"],
             match["translation_of_document_id"], match["source_path"], match["source_uri"]),
            ("FICTIONAL-OP-SDS-SOURCE", "1.0", "en", None,
             "tests/generated/fictional-operational-record.json", "test://fictional/operational"),
        )

    def test_cas_number_retrieval(self):
        self.ingest(operational_document())
        self.assertEqual(self.search("first aid 111-11-1")["status"], "success")

    def test_korean_alias_retrieval(self):
        doc = operational_document(language="ko")
        self.ingest(doc)
        self.assertEqual(self.search("가상 용제 응급처치", "ko")["status"], "success")

    def test_vietnamese_alias_retrieval(self):
        doc = operational_document(language="vi", translation_status="human_reviewed",
                                   translation_of_document_id="FICTIONAL-OP-SDS-001",
                                   aliases=[{"alias": "dung môi hư cấu", "language": "vi", "approved": True}])
        self.ingest(operational_document(), doc)
        self.assertEqual(self.search("sơ cứu dung môi hư cấu", "vi")["status"], "success")

    def test_spill_routes_facility_sop_then_sds_six(self):
        sds = operational_document(sections=[{
            "section_code": "SDS-06", "section_title": "FICTIONAL spill", "page_start": 5, "page_end": 5,
            "content": "FICTIONAL SDS SPILL TEST CONTENT.", "topic": "spill", "keywords": []}])
        sop = operational_document(document_id="FICTIONAL-OP-SOP-001", document_family_id="FICTIONAL-OP-SOP-FAMILY",
            canonical_source_id="FICTIONAL-OP-SOP-SOURCE",
            document_type="facility_sop", source_authority="facility", title="FICTIONAL FACILITY SOP TEST",
            manufacturer=None, product_name=None, product_code=None, cas_numbers=[], aliases=[],
            sections=[{"section_code": "SOP-SPILL", "section_title": "FICTIONAL spill SOP", "page_start": 1,
                       "page_end": 2, "content": "FICTIONAL SOP SPILL TEST CONTENT.", "topic": "spill", "keywords": []}])
        self.ingest(sds, sop)
        result = self.search("spill TEST-A100", facility_id="TEST-FACILITY")
        self.assertEqual([m["document_type"] for m in result["matches"]], ["facility_sop", "supplier_sds"])

    def test_first_aid_only_routes_sds_section_four(self):
        doc = operational_document(sections=[
            {"section_code": "SDS-04", "section_title": "four", "page_start": 4, "page_end": 4, "content": "FICTIONAL FOUR.", "topic": "first_aid", "keywords": []},
            {"section_code": "SDS-06", "section_title": "six", "page_start": 6, "page_end": 6, "content": "FICTIONAL SIX.", "topic": "spill", "keywords": []},
        ])
        self.ingest(doc)
        result = self.search("first aid TEST-A100")
        self.assertEqual([m["section_code"] for m in result["matches"]], ["SDS-04"])

    def test_ambiguous_alias_is_blocked(self):
        other = operational_document(document_id="FICTIONAL-OTHER", document_family_id="FICTIONAL-OTHER-FAMILY",
                                     product_name="Other Fictional Solvent", product_code="TEST-B200")
        self.ingest(operational_document(), other)
        self.assert_blocked(self.search("first aid 가상 용제", "ko"), "ambiguous_product")

    def test_unapproved_document_is_blocked(self):
        self.ingest(operational_document(approval_status="draft"))
        self.assert_blocked(self.search("first aid TEST-A100"), "unapproved_document")

    def test_superseded_and_locally_stale_are_blocked(self):
        self.ingest(operational_document(approval_status="superseded", active=False))
        self.assert_blocked(self.search("first aid TEST-A100"), "stale_document")
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "stale.sqlite"
            ingest_manifest({"documents": [operational_document(review_due_at="2026-01-01T00:00:00+00:00")]}, db)
            self.assert_blocked(search_safety_documents(
                "first aid TEST-A100", "en", db, usage_scope="operational", now=self.now
            ), "stale_document")

    def test_conflicting_active_versions_are_blocked(self):
        second = operational_document(document_id="FICTIONAL-OP-SDS-002", version="2.0", canonical_version="2.0")
        self.ingest(operational_document(), second)
        self.assert_blocked(self.search("first aid TEST-A100"), "conflicting_documents")

    def test_unverified_vietnamese_translation_is_blocked(self):
        doc = operational_document(language="vi", translation_status="machine_unreviewed",
            translation_of_document_id="FICTIONAL-OP-SDS-001",
            aliases=[{"alias": "dung môi hư cấu", "language": "vi", "approved": True}])
        self.ingest(operational_document(), doc)
        self.assert_blocked(self.search("sơ cứu dung môi hư cấu", "vi"), "translation_unverified")

    def test_test_only_fixture_is_excluded(self):
        self.ingest(synthetic_document())
        self.assert_blocked(self.search("first aid TEST-A100"), "not_found")

    def test_deterministic_order_and_three_match_limit(self):
        sections = [{"section_code": f"SDS-04-{i}", "section_title": f"fixture {i}", "page_start": i,
                     "page_end": i, "content": f"FICTIONAL CONTENT {i}.", "topic": "first_aid", "keywords": []}
                    for i in range(1, 6)]
        self.ingest(operational_document(sections=list(reversed(sections))))
        first = self.search("first aid TEST-A100")
        second = self.search("first aid TEST-A100")
        self.assertEqual(first, second)
        self.assertEqual([m["page_start"] for m in first["matches"]], [1, 2, 3])

    def test_not_found(self):
        self.ingest(operational_document())
        self.assert_blocked(self.search("first aid UNKNOWN-CODE"), "not_found")

    def test_invalid_arguments(self):
        self.assert_blocked(search_safety_documents("", "en", self.db, usage_scope="operational"), "invalid_arguments")
        self.assert_blocked(search_safety_documents("first aid TEST-A100", "en", self.db), "invalid_arguments")
        self.assert_blocked(search_safety_documents(
            "unknown topic", "en", self.db, usage_scope="operational"
        ), "invalid_arguments")

    def test_language_variants_of_same_canonical_version_do_not_conflict(self):
        korean = operational_document(language="ko", aliases=[
            {"alias": "가상 용제", "language": "ko", "approved": True}
        ])
        vietnamese = operational_document(
            language="vi", translation_status="human_reviewed",
            translation_of_document_id="FICTIONAL-OP-SDS-001",
            aliases=[{"alias": "dung môi hư cấu", "language": "vi", "approved": True}],
        )
        self.ingest(korean, vietnamese)
        result = self.search("sơ cứu dung môi hư cấu", "vi")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["matches"][0]["canonical_version"], "1.0")
        self.assertEqual(result["matches"][0]["translation_of_document_id"], "FICTIONAL-OP-SDS-001")

    def test_outdated_translation_is_blocked(self):
        current = operational_document(version="2.0", canonical_version="2.0")
        old_original = operational_document(
            document_id="FICTIONAL-OP-SDS-OLD", version="1.0", canonical_version="1.0"
        )
        old_translation = operational_document(
            document_id="FICTIONAL-OP-SDS-OLD-VI", language="vi",
            translation_status="human_reviewed", translation_of_document_id="FICTIONAL-OP-SDS-OLD",
            aliases=[{"alias": "dung môi hư cấu", "language": "vi", "approved": True}],
        )
        self.ingest(current, old_original, old_translation)
        self.assert_blocked(self.search("sơ cứu dung môi hư cấu", "vi"), "conflicting_documents")

    def test_facility_wide_sop_has_no_product_metadata(self):
        sds = operational_document(sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "FICTIONAL SDS SIX.", "topic": "spill", "keywords": [],
        }])
        sop = operational_document(
            document_id="FICTIONAL-SOP-GENERAL", document_family_id="FICTIONAL-SOP-FAMILY",
            canonical_source_id="FICTIONAL-SOP-SOURCE", document_type="facility_sop",
            source_authority="facility", manufacturer=None, product_name=None, product_code=None,
            cas_numbers=[], aliases=[], sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "FICTIONAL FACILITY SPILL.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(sds, sop)
        result = self.search("spill TEST-A100", facility_id="TEST-FACILITY")
        self.assertEqual([match["document_type"] for match in result["matches"]],
                         ["facility_sop", "supplier_sds"])

    def test_cross_facility_and_unspecified_facility_exclude_sops(self):
        sds = operational_document(sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "SDS.", "topic": "spill", "keywords": [],
        }])
        other_sop = operational_document(
            document_id="OTHER-SOP", document_family_id="OTHER-SOP-FAMILY",
            canonical_source_id="OTHER-SOP-SOURCE", document_type="facility_sop",
            facility_id="OTHER-FACILITY", source_authority="facility", manufacturer=None,
            product_name=None, product_code=None, cas_numbers=[], aliases=[], sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "OTHER FACILITY.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(sds, other_sop)
        for kwargs in ({"facility_id": "TEST-FACILITY"}, {}):
            result = self.search("spill TEST-A100", **kwargs)
            self.assertEqual([match["document_type"] for match in result["matches"]], ["supplier_sds"])

    def test_unspecified_facility_can_use_only_global_sop(self):
        sds = operational_document(facility_id=None, sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "SDS.", "topic": "spill", "keywords": [],
        }])
        global_sop = operational_document(
            document_id="GLOBAL-SOP", document_family_id="GLOBAL-SOP-FAMILY",
            canonical_source_id="GLOBAL-SOP-SOURCE", document_type="facility_sop", facility_id=None,
            source_authority="facility", manufacturer=None, product_name=None, product_code=None,
            cas_numbers=[], aliases=[], sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "GLOBAL.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(sds, global_sop)
        result = self.search("spill TEST-A100")
        self.assertEqual([match["document_id"] for match in result["matches"]],
                         ["GLOBAL-SOP", "FICTIONAL-OP-SDS-001"])

    def test_product_specific_sop_is_restricted(self):
        sds = operational_document(sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "SDS.", "topic": "spill", "keywords": [],
        }])
        wrong_sop = operational_document(
            document_id="WRONG-PRODUCT-SOP", document_family_id="WRONG-PRODUCT-SOP-FAMILY",
            canonical_source_id="WRONG-PRODUCT-SOP-SOURCE", document_type="facility_sop",
            source_authority="facility", product_code="TEST-B200", aliases=[], sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "WRONG PRODUCT.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(sds, wrong_sop)
        result = self.search("spill TEST-A100", facility_id="TEST-FACILITY")
        self.assertEqual([match["document_type"] for match in result["matches"]], ["supplier_sds"])

    def test_cross_facility_product_specific_sop_is_excluded(self):
        sds = operational_document(sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "SDS.", "topic": "spill", "keywords": [],
        }])
        other_sop = operational_document(
            document_id="OTHER-PRODUCT-SOP", document_family_id="OTHER-PRODUCT-SOP-FAMILY",
            canonical_source_id="OTHER-PRODUCT-SOP-SOURCE", document_type="facility_sop",
            facility_id="OTHER-FACILITY", source_authority="facility", aliases=[], sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "OTHER FACILITY.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(sds, other_sop)
        result = self.search("spill TEST-A100", facility_id="TEST-FACILITY")
        self.assertEqual([match["document_id"] for match in result["matches"]],
                         ["FICTIONAL-OP-SDS-001"])

    def test_product_specific_sop_is_excluded_without_facility(self):
        sds = operational_document(facility_id=None, sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "SDS.", "topic": "spill", "keywords": [],
        }])
        facility_sop = operational_document(
            document_id="FACILITY-PRODUCT-SOP", document_family_id="FACILITY-PRODUCT-SOP-FAMILY",
            canonical_source_id="FACILITY-PRODUCT-SOP-SOURCE", document_type="facility_sop",
            source_authority="facility", aliases=[], sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "FACILITY.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(sds, facility_sop)
        result = self.search("spill TEST-A100")
        self.assertEqual([match["document_id"] for match in result["matches"]],
                         ["FICTIONAL-OP-SDS-001"])

    def test_requested_facility_product_specific_sop_is_included(self):
        sds = operational_document(sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "SDS.", "topic": "spill", "keywords": [],
        }])
        facility_sop = operational_document(
            document_id="LOCAL-PRODUCT-SOP", document_family_id="LOCAL-PRODUCT-SOP-FAMILY",
            canonical_source_id="LOCAL-PRODUCT-SOP-SOURCE", document_type="facility_sop",
            source_authority="facility", aliases=[], sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "LOCAL.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(sds, facility_sop)
        result = self.search("spill TEST-A100", facility_id="TEST-FACILITY")
        self.assertEqual([match["document_id"] for match in result["matches"]],
                         ["LOCAL-PRODUCT-SOP", "FICTIONAL-OP-SDS-001"])

    def test_other_facility_sop_alias_cannot_resolve_product(self):
        other_sop = operational_document(
            document_id="OTHER-ALIAS-SOP", document_family_id="OTHER-ALIAS-SOP-FAMILY",
            canonical_source_id="OTHER-ALIAS-SOP-SOURCE", document_type="facility_sop",
            facility_id="OTHER-FACILITY", source_authority="facility",
            aliases=[{"alias": "remote spill kit", "language": "en", "approved": True}],
            sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "OTHER.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(other_sop)
        self.assert_blocked(
            self.search("spill remote spill kit", facility_id="TEST-FACILITY"), "not_found"
        )

    def test_unrelated_sop_versions_do_not_conflict_for_spill(self):
        sds = operational_document(sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "SDS.", "topic": "spill", "keywords": [],
        }])
        fire_section = [{"section_code": "SOP-FIRE", "section_title": "fire", "page_start": 1,
                         "page_end": 1, "content": "FIRE.", "topic": "fire", "keywords": []}]
        first = operational_document(
            document_id="FIRE-SOP-1", document_family_id="FIRE-SOP-FAMILY",
            canonical_source_id="FIRE-SOP-SOURCE", document_type="facility_sop",
            source_authority="facility", manufacturer=None, product_name=None, product_code=None,
            cas_numbers=[], aliases=[], sections=fire_section,
        )
        second = operational_document(
            document_id="FIRE-SOP-2", document_family_id="FIRE-SOP-FAMILY",
            canonical_source_id="FIRE-SOP-SOURCE", canonical_version="2.0", version="2.0",
            document_type="facility_sop", source_authority="facility", manufacturer=None,
            product_name=None, product_code=None, cas_numbers=[], aliases=[], sections=fire_section,
        )
        self.ingest(sds, first, second)
        result = self.search("spill TEST-A100", facility_id="TEST-FACILITY")
        self.assertEqual(result["status"], "success")
        self.assertEqual([match["document_type"] for match in result["matches"]], ["supplier_sds"])

    def test_unrelated_stale_sop_does_not_block_valid_spill_sds(self):
        sds = operational_document(sections=[{
            "section_code": "SDS-06", "section_title": "spill", "page_start": 6,
            "page_end": 6, "content": "SDS.", "topic": "spill", "keywords": [],
        }])
        stale_fire_sop = operational_document(
            document_id="STALE-FIRE-SOP", document_family_id="STALE-FIRE-SOP-FAMILY",
            canonical_source_id="STALE-FIRE-SOP-SOURCE", document_type="facility_sop",
            source_authority="facility", approval_status="superseded", active=False,
            manufacturer=None, product_name=None, product_code=None, cas_numbers=[], aliases=[], sections=[{
                "section_code": "SOP-FIRE", "section_title": "fire", "page_start": 1,
                "page_end": 1, "content": "FIRE.", "topic": "fire", "keywords": [],
            }],
        )
        self.ingest(sds, stale_fire_sop)
        result = self.search("spill TEST-A100", facility_id="TEST-FACILITY")
        self.assertEqual([match["document_id"] for match in result["matches"]],
                         ["FICTIONAL-OP-SDS-001"])

    def test_document_without_requested_section_is_not_returned(self):
        unrelated_sds = operational_document()
        spill_sop = operational_document(
            document_id="SPILL-SOP", document_family_id="SPILL-SOP-FAMILY",
            canonical_source_id="SPILL-SOP-SOURCE", document_type="facility_sop",
            source_authority="facility", manufacturer=None, product_name=None, product_code=None,
            cas_numbers=[], aliases=[], sections=[{
                "section_code": "SOP-SPILL", "section_title": "spill", "page_start": 1,
                "page_end": 1, "content": "SPILL.", "topic": "spill", "keywords": [],
            }],
        )
        self.ingest(unrelated_sds, spill_sop)
        result = self.search("spill TEST-A100", facility_id="TEST-FACILITY")
        self.assertEqual([match["document_id"] for match in result["matches"]], ["SPILL-SOP"])

    def test_no_topic_eligible_routed_section_is_not_found(self):
        self.ingest(operational_document())
        self.assert_blocked(self.search("spill TEST-A100"), "not_found")

    def test_usage_scope_isolation(self):
        scopes = ("operational", "demo", "reference_only")
        for scope in scopes:
            with self.subTest(scope=scope):
                with tempfile.TemporaryDirectory() as directory:
                    db = Path(directory) / "scope.sqlite"
                    ingest_manifest({"documents": [operational_document(usage_scope=scope)]}, db)
                    for requested in scopes:
                        result = search_safety_documents(
                            "first aid TEST-A100", "en", db, usage_scope=requested, now=self.now
                        )
                        self.assertEqual(result["status"], "success" if requested == scope else "not_found")
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "test.sqlite"
            ingest_manifest({"documents": [synthetic_document()]}, db)
            for requested in scopes:
                self.assert_blocked(search_safety_documents(
                    "first aid TEST-A100", "en", db, usage_scope=requested, now=self.now
                ), "not_found")
        self.assert_blocked(search_safety_documents(
            "first aid TEST-A100", "en", self.db, usage_scope="test_only", now=self.now
        ), "invalid_arguments")

    def test_partial_product_code_and_cas_are_rejected(self):
        self.ingest(operational_document())
        self.assert_blocked(self.search("first aid TEST-A10"), "not_found")
        self.assert_blocked(self.search("first aid 111-11"), "not_found")
        self.assert_blocked(self.search("first aid 111-11-10"), "not_found")

    def test_alias_boundaries_and_language_isolation(self):
        self.ingest(operational_document(language="ko", aliases=[
            {"alias": "가상 용제", "language": "ko", "approved": True}
        ]))
        self.assert_blocked(self.search("first aid 초가상 용제", "ko"), "not_found")
        self.assert_blocked(self.search("first aid 가상 용제", "en"), "not_found")

    def test_explicit_topic_routes_without_keywords(self):
        self.ingest(operational_document())
        result = self.search("TEST-A100", topic="first_aid")
        self.assertEqual(result["status"], "success")
        self.assert_blocked(self.search("fire spill TEST-A100"), "invalid_arguments")
        self.assertEqual(self.search("fire spill TEST-A100", topic="first_aid")["status"], "success")


if __name__ == "__main__":
    unittest.main()
