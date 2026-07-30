"""Moss integration tests use fakes only and make no Moss or network calls."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voice_workflow_agent.document_store import ingest_manifest, ingest_manifest_file
from voice_workflow_agent.moss_retrieval import (
    MossRerankResult,
    MossRuntime,
    MossSettings,
    catalog_sections_for_moss,
    moss_document_key,
)
from voice_workflow_agent.retrieval import search_safety_documents
from voice_workflow_agent.tools import ToolContext, search_approved_safety_manual
from tests.test_retrieval import operational_document


class FakeQueryOptions:
    def __init__(self, **values):
        self.__dict__.update(values)


class FakeMossClient:
    def __init__(self, project_id, project_key, *, reverse=True, delay=0):
        self.project_id = project_id
        self.project_key = project_key
        self.reverse = reverse
        self.delay = delay
        self.loaded = None
        self.unloaded = None
        self.last_options = None

    async def load_index(
        self, name, auto_refresh=False, polling_interval_in_seconds=600
    ):
        self.loaded = (name, auto_refresh, polling_interval_in_seconds)
        return name

    async def query(self, name, query, options):
        self.last_options = options
        if self.delay:
            await asyncio.sleep(self.delay)
        keys = options.filter["condition"]["$in"]
        ordered = list(reversed(keys)) if self.reverse else list(keys)
        return SimpleNamespace(
            docs=[SimpleNamespace(id=key, score=1.0) for key in ordered],
            time_taken_ms=2,
        )

    async def unload_index(self, name):
        self.unloaded = name


def match(
    document_id: str,
    section_code: str,
    document_type: str,
    *,
    page: int,
) -> dict:
    return {
        "document_id": document_id,
        "version": "1.0",
        "language": "ko",
        "section_code": section_code,
        "source_checksum": f"sha256:{document_id}",
        "document_type": document_type,
        "page_start": page,
        "content": document_id,
    }


class MossSettingsTests(unittest.TestCase):
    def test_disabled_is_dependency_and_credential_free(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = MossSettings.from_environment()
        self.assertFalse(settings.enabled)

    def test_enabled_settings_are_validated_and_operational_is_opt_in(self):
        environment = {
            "VOICE_WORKFLOW_AGENT_MOSS_ENABLED": "true",
            "MOSS_PROJECT_ID": "project",
            "MOSS_PROJECT_KEY": "key",
            "MOSS_INDEX_NAME": "safe-index",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = MossSettings.from_environment()
        self.assertEqual(settings.allowed_scopes, frozenset({"demo", "reference_only"}))
        self.assertNotIn("operational", settings.allowed_scopes)

        environment["VOICE_WORKFLOW_AGENT_MOSS_ALLOWED_SCOPES"] = "operational"
        with patch.dict(os.environ, environment, clear=True):
            settings = MossSettings.from_environment()
        self.assertEqual(settings.allowed_scopes, frozenset({"operational"}))

    def test_partial_or_invalid_enabled_configuration_is_rejected(self):
        with patch.dict(
            os.environ,
            {"VOICE_WORKFLOW_AGENT_MOSS_ENABLED": "true", "MOSS_PROJECT_ID": "project"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                MossSettings.from_environment()


class MossCatalogExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "catalog.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_export_contains_only_current_approved_reviewed_scope(self):
        approved = operational_document(
            sections=[
                {
                    "section_code": "SDS-04",
                    "section_title": "Approved",
                    "page_start": 4,
                    "page_end": 4,
                    "content": "APPROVED FICTIONAL CONTENT.",
                    "topic": "first_aid",
                    "keywords": ["fictional", "approved"],
                }
            ]
        )
        draft = operational_document(
            document_id="DRAFT-DOC",
            document_family_id="DRAFT-FAMILY",
            canonical_source_id="DRAFT-SOURCE",
            product_name="Draft Product",
            product_code="DRAFT-1",
            approval_status="draft",
        )
        unreviewed = operational_document(
            document_id="UNREVIEWED-DOC",
            language="vi",
            translation_status="machine_unreviewed",
            translation_of_document_id="FICTIONAL-OP-SDS-001",
        )
        ingest_manifest({"documents": [approved, draft, unreviewed]}, self.db)

        documents = catalog_sections_for_moss(
            self.db,
            "operational",
            now=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(len(documents), 1)
        exported = documents[0]
        self.assertEqual(exported.metadata["document_id"], "FICTIONAL-OP-SDS-001")
        self.assertEqual(exported.metadata["topic"], "first_aid")
        self.assertEqual(exported.metadata["voice_workflow_agent_key"], exported.id)
        self.assertIn("APPROVED FICTIONAL CONTENT.", exported.text)
        self.assertEqual(
            exported.id,
            moss_document_key(
                {
                    "document_id": "FICTIONAL-OP-SDS-001",
                    "version": "1.0",
                    "language": "en",
                    "section_code": "SDS-04",
                    "source_checksum": "sha256:fictional-test-checksum",
                }
            ),
        )

    def test_stale_and_wrong_scope_sections_are_not_exported(self):
        stale = operational_document(review_due_at="2026-01-01T00:00:00+00:00")
        ingest_manifest({"documents": [stale]}, self.db)
        now = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(
            catalog_sections_for_moss(self.db, "operational", now=now), []
        )
        self.assertEqual(catalog_sections_for_moss(self.db, "demo", now=now), [])

    def test_fictional_demo_catalog_is_searchable_and_exportable(self):
        project_root = Path(__file__).resolve().parents[1]
        manifest = (
            project_root / "data" / "moss_demo" / "approved_documents.ko.json"
        )
        ingest_manifest_file(manifest, self.db)
        result = search_safety_documents(
            "모스 가상 용제 누출",
            "ko",
            self.db,
            usage_scope="demo",
            facility_id="MOSS-DEMO-FACILITY",
            topic="spill",
            now=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            [item["document_type"] for item in result["matches"]],
            ["facility_sop", "supplier_sds"],
        )
        exported = catalog_sections_for_moss(
            self.db,
            "demo",
            now=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(len(exported), 3)
        self.assertTrue(
            all("FICTIONAL NON-OPERATIONAL" in item.text for item in exported)
        )


class MossRuntimeTests(unittest.TestCase):
    def settings(self, **overrides):
        values = {
            "enabled": True,
            "project_id": "project",
            "project_key": "key",
            "index_name": "safe-index",
            "allowed_scopes": frozenset({"demo"}),
            "query_timeout_seconds": 0.25,
            "load_timeout_seconds": 1.0,
        }
        values.update(overrides)
        return MossSettings(**values)

    def test_runtime_loads_and_reranks_inside_sqlite_route_priority(self):
        client = FakeMossClient("project", "key")
        runtime = MossRuntime(
            self.settings(),
            client_factory=lambda *_: client,
            query_options_factory=FakeQueryOptions,
        )
        sop = match("SOP", "SOP-SPILL", "facility_sop", page=1)
        sds_one = match("SDS-ONE", "SDS-06-A", "supplier_sds", page=2)
        sds_two = match("SDS-TWO", "SDS-06-B", "supplier_sds", page=3)
        try:
            self.assertTrue(runtime.start())
            result = runtime.rerank(
                "가상 누출 대응",
                [sop, sds_one, sds_two],
                usage_scope="demo",
                topic_routes=(("facility_sop", None), ("supplier_sds", "6")),
            )
            self.assertTrue(result.used)
            self.assertEqual(
                [item["document_id"] for item in result.matches],
                ["SOP", "SDS-TWO", "SDS-ONE"],
            )
            self.assertEqual(
                set(client.last_options.filter["condition"]["$in"]),
                {moss_document_key(item) for item in (sop, sds_one, sds_two)},
            )
        finally:
            runtime.close()
        self.assertEqual(client.unloaded, "safe-index")

    def test_timeout_and_disallowed_scope_fall_back_without_mutation(self):
        client = FakeMossClient("project", "key", delay=0.05)
        runtime = MossRuntime(
            self.settings(query_timeout_seconds=0.01),
            client_factory=lambda *_: client,
            query_options_factory=FakeQueryOptions,
        )
        original = [
            match("ONE", "SDS-04-A", "supplier_sds", page=1),
            match("TWO", "SDS-04-B", "supplier_sds", page=2),
        ]
        try:
            runtime.start()
            timeout = runtime.rerank(
                "first aid",
                original,
                usage_scope="demo",
                topic_routes=(("supplier_sds", "4"),),
            )
            self.assertFalse(timeout.used)
            self.assertEqual(timeout.matches, original)
            blocked = runtime.rerank(
                "first aid",
                original,
                usage_scope="operational",
                topic_routes=(("supplier_sds", "4"),),
            )
            self.assertFalse(blocked.used)
            self.assertEqual(blocked.matches, original)
        finally:
            runtime.close()


class MossToolIntegrationTests(unittest.TestCase):
    def test_tool_uses_moss_only_after_sqlite_approval_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "catalog.sqlite"
            sections = [
                {
                    "section_code": f"SDS-04-{index}",
                    "section_title": f"section {index}",
                    "page_start": index,
                    "page_end": index,
                    "content": f"FICTIONAL CONTENT {index}.",
                    "topic": "first_aid",
                    "keywords": [],
                }
                for index in range(1, 5)
            ]
            ingest_manifest(
                {"documents": [operational_document(sections=sections)]}, db
            )

            class Runtime:
                settings = SimpleNamespace(candidate_limit=64)

                @staticmethod
                def allows_scope(scope):
                    return scope == "operational"

                @staticmethod
                def rerank(query, matches, **_):
                    return MossRerankResult(
                        list(reversed(list(matches)))[:3], True, 4
                    )

            with patch(
                "voice_workflow_agent.moss_retrieval.get_moss_runtime",
                return_value=Runtime(),
            ):
                result = search_approved_safety_manual(
                    "first aid TEST-A100",
                    context=ToolContext(db, None, "en", "operational"),
                    topic="first_aid",
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["retrieval"], {"backend": "moss", "elapsed_ms": 4})
        self.assertEqual(
            [item["page_start"] for item in result["matches"]], [4, 3, 2]
        )
        self.assertNotIn("voice_workflow_agent_key", result["matches"][0])
