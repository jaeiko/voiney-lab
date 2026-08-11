"""Offline tests for approved-document and authoritative-web reference tiers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voice_workflow_agent.brain import answer_approved_reference_question
from voice_workflow_agent.document_store import ingest_manifest
from voice_workflow_agent.external_references import (
    ExternalReferenceSettings,
    XaiAuthoritativeWebSearch,
)
from voice_workflow_agent.retrieval import retrieve_approved_lab_documents
from voice_workflow_agent.tools import ToolContext, search_approved_lab_references

from tests.test_retrieval import operational_document


def reference_document(**overrides):
    base = operational_document(
        source_authority="supplier",
        usage_scope="reference_only",
        source_uri="test://fictional/reference",
        sections=[{
            "section_code": "REF-HANDLING",
            "section_title": "Fictional acetonitrile handling precautions",
            "page_start": 2,
            "page_end": 2,
            "content": (
                "FICTIONAL TEST REFERENCE. Keep the fictional acetonitrile "
                "container closed and use a ventilated test enclosure."
            ),
            "topic": "handling_storage",
            "keywords": ["acetonitrile", "precaution", "ventilated"],
        }],
    )
    base.update(overrides)
    return base


class _Completion:
    def __init__(self, payload):
        self.choices = [SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload))
        )]


class _ReferenceClient:
    model = "fake"

    def __init__(self, payload):
        self.payload = payload
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _Completion(self.payload)


class ApprovedReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "catalog.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_approved_index_is_filtered_ranked_and_deduplicated(self):
        duplicate = reference_document(
            document_id="FICTIONAL-OP-SDS-002",
            document_family_id="FICTIONAL-OP-SDS-FAMILY-2",
            canonical_source_id="FICTIONAL-OP-SDS-SOURCE-2",
        )
        draft = reference_document(
            document_id="FICTIONAL-DRAFT",
            document_family_id="FICTIONAL-DRAFT-FAMILY",
            canonical_source_id="FICTIONAL-DRAFT-SOURCE",
            approval_status="draft",
        )
        ingest_manifest(
            {"documents": [reference_document(), duplicate, draft]}, self.db
        )
        result = retrieve_approved_lab_documents(
            "2단계 acetonitrile 주의사항", self.db,
            filters={"approval_status": "approved", "lab_scope": "reference_only"},
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["matches"]), 1)
        match = result["matches"][0]
        self.assertEqual(match["approval_status"], "approved")
        self.assertEqual(match["page_number"], 2)
        self.assertEqual(len(match["chunk_id"]), 64)
        self.assertNotEqual(match["document_id"], "FICTIONAL-DRAFT")

    def test_tool_uses_read_only_baseline_without_moss(self):
        ingest_manifest({"documents": [reference_document()]}, self.db)
        context = ToolContext(self.db, None, "ko", "reference_only")
        with patch(
            "voice_workflow_agent.moss_retrieval.get_moss_runtime",
            return_value=None,
        ):
            result = search_approved_lab_references(
                "acetonitrile precaution", context=context,
                protocol_id="candidate-a-curated-development-v1",
            )
        self.assertTrue(result["answerable"])
        self.assertEqual(result["retrieval"]["backend"], "sqlite")

    def test_catalog_audit_cli_is_read_only_and_omits_source_text(self):
        ingest_manifest({"documents": [reference_document()]}, self.db)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "scripts/audit_approved_catalog.py",
                "--db",
                str(self.db),
                "--scope",
                "reference_only",
                "--query",
                "acetonitrile precaution",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("catalog healthy", result.stdout)
        self.assertIn("query status=success", result.stdout)
        self.assertIn("chunk_id=", result.stdout)
        self.assertNotIn("FICTIONAL TEST REFERENCE", result.stdout)

    async def test_reference_answer_rejects_unknown_citation_and_preserves_numbers(self):
        ingest_manifest({"documents": [reference_document()]}, self.db)
        evidence = tuple(retrieve_approved_lab_documents(
            "acetonitrile precaution", self.db,
            filters={"approval_status": "approved", "lab_scope": "reference_only"},
        )["matches"])
        good = _ReferenceClient({
            "answer_origin": "approved_lab_corpus",
            "primary_text": "용기는 닫아 두고 환기되는 시험 구역을 사용하세요.",
            "citation_ids": [evidence[0]["chunk_id"]],
            "limitations": ["활성 프로토콜 자체의 지시가 아닙니다."],
        })
        answer = await answer_approved_reference_question(
            good, "주의사항?", language="ko", protocol_id="candidate-a",
            step_id="step-2", evidence=evidence,
        )
        self.assertEqual(answer.citations[0]["document_id"], evidence[0]["document_id"])
        system_text = "\n".join(
            str(message["content"]) for message in answer.messages
            if message["role"] == "system"
        )
        self.assertIn("untrusted data", system_text.casefold())

        bad = _ReferenceClient({
            "answer_origin": "approved_lab_corpus",
            "primary_text": "unsupported",
            "citation_ids": ["unknown"],
            "limitations": [],
        })
        with self.assertRaises(RuntimeError):
            await answer_approved_reference_question(
                bad, "주의사항?", language="ko", protocol_id="candidate-a",
                step_id="step-2", evidence=evidence,
            )

    def test_external_settings_are_disabled_or_strictly_allowlisted(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ExternalReferenceSettings.from_environment().enabled)
        with patch.dict(os.environ, {
            "VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCES_ENABLED": "true",
            "VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCE_DOMAINS": "osha.gov,cdc.gov",
        }, clear=True):
            settings = ExternalReferenceSettings.from_environment()
        self.assertEqual(settings.allowed_domains, ("osha.gov", "cdc.gov"))

    def test_external_setting_alias_conflict_and_invalid_profile_fail_loudly(self):
        with patch.dict(os.environ, {
            "EXTERNAL_REFERENCES_ENABLED": "true",
            "VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCES_ENABLED": "false",
        }, clear=True), self.assertRaisesRegex(ValueError, "conflicts"):
            ExternalReferenceSettings.from_environment()
        with patch.dict(os.environ, {
            "EXTERNAL_REFERENCES_ENABLED": "true",
            "EXTERNAL_REFERENCE_DOMAIN_PROFILE": "unknown-profile",
        }, clear=True), self.assertRaisesRegex(ValueError, "DOMAIN_PROFILE"):
            ExternalReferenceSettings.from_environment()
        for name,value in (
            ("EXTERNAL_REFERENCE_MODEL",""),
            ("EXTERNAL_REFERENCE_TIMEOUT_SECONDS","31"),
            ("EXTERNAL_REFERENCE_MAX_CITATIONS","6"),
        ):
            with self.subTest(name=name),patch.dict(os.environ, {
                "EXTERNAL_REFERENCES_ENABLED":"true",
                "EXTERNAL_REFERENCE_DOMAIN_PROFILE":"candidate_a",
                name:value,
            },clear=True),self.assertRaises(ValueError):
                ExternalReferenceSettings.from_environment()

    async def test_external_adapter_accepts_documented_inline_citation_shape(self):
        url = "https://pubchem.ncbi.nlm.nih.gov/compound/Ammonium-bicarbonate"
        response = {
            "output": [
                {"type": "web_search_call"},
                {"type": "message", "content": [{
                    "type": "output_text",
                    "text": f"AMBIC is ammonium bicarbonate [[1]]({url}).",
                    "annotations": [],
                }]},
            ],
            "citations": [url],
        }
        endpoint = SimpleNamespace()
        async def create(**kwargs):
            endpoint.kwargs = kwargs
            return response
        endpoint.create = create
        result = await XaiAuthoritativeWebSearch(
            SimpleNamespace(responses=endpoint),
            ExternalReferenceSettings(True, ("pubchem.ncbi.nlm.nih.gov",), "fake"),
        ).search("AMBIC", language="ko")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["matches"][0]["canonical_url"], url)

    async def test_external_adapter_rejects_non_allowlisted_citations(self):
        response = SimpleNamespace(
            output_text="Use the cited official reference.",
            output=[
                SimpleNamespace(type="web_search_call"),
                SimpleNamespace(type="message", content=[SimpleNamespace(
                    annotations=[
                        SimpleNamespace(url_citation=SimpleNamespace(
                            url="https://www.osha.gov/laboratory", title="OSHA",
                            start_index=8, end_index=22,
                        )),
                        SimpleNamespace(url_citation=SimpleNamespace(
                            url="https://example.com/blog", title="Blog",
                            start_index=0, end_index=3,
                        )),
                    ]
                )]),
            ],
        )
        endpoint = SimpleNamespace(create=lambda **kwargs: None)

        async def create(**kwargs):
            endpoint.kwargs = kwargs
            return response

        endpoint.create = create
        client = SimpleNamespace(responses=endpoint)
        result = await XaiAuthoritativeWebSearch(
            client, ExternalReferenceSettings(True, ("osha.gov",), "fake")
        ).search("lab precaution", language="ko")
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["domain"], "www.osha.gov")
        self.assertEqual(
            endpoint.kwargs["tools"][0]["filters"]["allowed_domains"],
            ["osha.gov"],
        )

    async def test_external_adapter_has_one_bounded_request(self):
        class Responses:
            def __init__(self):
                self.calls = 0

            async def create(self, **_kwargs):
                self.calls += 1
                await asyncio.sleep(0.02)
                return SimpleNamespace(output_text="", output=[])

        responses = Responses()
        client = SimpleNamespace(responses=responses)
        with self.assertRaises(asyncio.TimeoutError):
            await XaiAuthoritativeWebSearch(
                client,
                ExternalReferenceSettings(
                    True, ("osha.gov",), "fake", timeout_seconds=0.001
                ),
            ).search("laboratory safety", language="ko")
        self.assertEqual(responses.calls, 1)


if __name__ == "__main__":
    unittest.main()
