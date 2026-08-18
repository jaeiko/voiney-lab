"""Offline tests for approved-document and authoritative-web reference tiers."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from voice_workflow_agent.brain import answer_approved_reference_question
from voice_workflow_agent.document_store import ingest_manifest
from voice_workflow_agent.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
    XaiAuthoritativeWebSearch,
    XaiSupplementalKnowledge,
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
            "EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS": "2.5",
        }, clear=True):
            settings = ExternalReferenceSettings.from_environment()
        self.assertEqual(settings.allowed_domains, ("osha.gov", "cdc.gov"))
        self.assertEqual(settings.user_visible_enrichment_budget_seconds, 2.5)
        self.assertLess(
            settings.user_visible_enrichment_budget_seconds,
            settings.timeout_seconds,
        )

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
        with patch.dict(os.environ, {
            "EXTERNAL_REFERENCES_ENABLED":"true",
            "EXTERNAL_REFERENCE_DOMAIN_PROFILE":"candidate_a",
            "EXTERNAL_REFERENCE_TIMEOUT_SECONDS":"5",
            "EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS":"5",
        },clear=True),self.assertRaises(ValueError):
            ExternalReferenceSettings.from_environment()

    def test_supplemental_settings_are_separate_and_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(
                SupplementalKnowledgeSettings.from_environment().enabled
            )
        with patch.dict(os.environ, {
            "SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED": "true",
            "SUPPLEMENTAL_MODEL_KNOWLEDGE_MODEL": "grok-test",
            "SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS": "7",
        }, clear=True):
            settings = SupplementalKnowledgeSettings.from_environment()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.model, "grok-test")
        self.assertEqual(settings.timeout_seconds, 7.0)
        self.assertEqual(
            settings.public_capability()["authority"],
            "supplemental_model_knowledge",
        )

    async def test_supplemental_adapter_has_no_tools_or_citations(self):
        class Responses:
            calls = 0
            async def create(self, **kwargs):
                self.calls += 1
                self.kwargs = kwargs
                return SimpleNamespace(
                    id="safe-response-id",
                    output_text="AMBIC is a common abbreviation for ammonium bicarbonate.",
                )

        responses = Responses()
        result = await XaiSupplementalKnowledge(
            SimpleNamespace(responses=responses),
            SupplementalKnowledgeSettings(True, "grok-test", 2.0),
        ).explain("AMBIC의 일반적인 뜻은 뭐야?", language="ko")
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["backend"],
            "xai_responses_supplemental_model_knowledge",
        )
        self.assertEqual(responses.calls, 1)
        self.assertNotIn("tools", responses.kwargs)
        self.assertNotIn("include", responses.kwargs)

    async def test_supplemental_adapter_rejects_operational_values(self):
        class Responses:
            async def create(self, **_kwargs):
                return SimpleNamespace(
                    output_text="Use HPLC water instead for this laboratory step."
                )

        result = await XaiSupplementalKnowledge(
            SimpleNamespace(responses=Responses()),
            SupplementalKnowledgeSettings(True, "grok-test", 2.0),
        ).explain("Explain the background.", language="en")
        self.assertEqual(result["status"], "response_rejected")

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
        result = await XaiAuthoritativeWebSearch(
            client,
            ExternalReferenceSettings(
                True, ("osha.gov",), "fake", timeout_seconds=0.001
            ),
        ).search("laboratory safety", language="ko")
        self.assertEqual(result["status"], "timeout_total")
        self.assertEqual(responses.calls, 1)

    async def test_stream_cleanup_cannot_defeat_the_terminal_budget(self):
        class Stream:
            def __aiter__(self): return self
            async def __anext__(self):
                await asyncio.sleep(10)
            async def close(self):
                await asyncio.sleep(10)
        class Responses:
            calls = 0
            async def create(self, **_kwargs):
                self.calls += 1
                return Stream()
        responses=Responses();started=asyncio.get_running_loop().time()
        result=await XaiAuthoritativeWebSearch(
            SimpleNamespace(responses=responses),
            ExternalReferenceSettings(
                True,("osha.gov",),"stream-timeout",
                timeout_seconds=0.01,
            ),
        ).search("bounded stream cleanup",language="en")
        elapsed=asyncio.get_running_loop().time()-started
        self.assertEqual(result["status"],"timeout_total")
        self.assertEqual(responses.calls,1)
        self.assertLess(elapsed,0.5)

    async def test_external_adapter_accepts_server_usage_and_included_sources(self):
        url = "https://pubchem.ncbi.nlm.nih.gov/compound/7739"
        response = {
            "id": "response-safe-1",
            "output_text": "AMBIC means ammonium bicarbonate.",
            "output": [{"type": "message", "content": []}],
            "citations": [{"url": url}],
            "server_side_tool_usage": {"WEB_SEARCH": 1},
            "included": [{
                "type": "web_search_call",
                "action": {"sources": [{
                    "url": url, "title": "PubChem",
                    "snippet": "Ammonium bicarbonate compound record.",
                }]},
            }],
        }
        class Responses:
            calls = 0
            async def create(self, **kwargs):
                self.calls += 1
                self.kwargs = kwargs
                return response
        endpoint = Responses()
        adapter = XaiAuthoritativeWebSearch(
            SimpleNamespace(responses=endpoint),
            ExternalReferenceSettings(
                True, ("pubchem.ncbi.nlm.nih.gov",), "fake",
                cache_ttl_seconds=60,
            ),
        )
        first = await adapter.search("unique included-source query", language="ko")
        second = await adapter.search("unique included-source query", language="ko")
        self.assertEqual(first["status"], "success")
        self.assertEqual(first["tool_usage_count"], 1)
        self.assertEqual(first["provider_request_id"], "response-safe-1")
        self.assertEqual(first["matches"][0]["canonical_url"], url)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(endpoint.calls, 1)
        self.assertEqual(
            endpoint.kwargs["timeout"].connect,
            adapter.settings.connect_timeout_seconds,
        )

    async def test_external_adapter_consumes_stream_and_retains_safe_tool_timings(self):
        url = "https://pubchem.ncbi.nlm.nih.gov/compound/7739"
        final = {
            "id": "stream-response-1",
            "output_text": f"AMBIC is ammonium bicarbonate [[1]]({url}).",
            "output": [
                {"type": "web_search_call", "status": "completed"},
                {"type": "message", "content": []},
            ],
            "citations": [url],
            "usage": {"server_side_tool_usage": {"WEB_SEARCH": 1}},
        }
        endpoint = SimpleNamespace()
        async def create(**kwargs):
            endpoint.kwargs = kwargs
            return final
        endpoint.create = create
        result = await XaiAuthoritativeWebSearch(
            SimpleNamespace(responses=endpoint),
            ExternalReferenceSettings(
                True, ("pubchem.ncbi.nlm.nih.gov",), "stream-fake"
            ),
        ).search("unique streaming AMBIC query", language="ko")
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["streaming"])
        self.assertEqual(result["event_count"], 0)
        self.assertEqual(result["tool_event_count"], 0)
        self.assertTrue(endpoint.kwargs["stream"])
        self.assertEqual(endpoint.kwargs["max_output_tokens"], 350)

    async def test_external_adapter_distinguishes_tool_and_schema_failures(self):
        url = "https://www.osha.gov/laboratory"
        cases = (
            ({"id": "a", "output_text": f"See [[1]]({url}).",
              "output": [], "citations": [url]}, "tool_not_executed"),
            ({"id": "b", "output_text": "No evidence", "output": [{
                "type": "web_search_call", "status": "failed",
            }]}, "response_schema_error"),
            ({"id": "c", "output_text": "No cited evidence", "output": [{
                "type": "web_search_call", "status": "completed",
            }]}, "no_allowed_citation"),
        )
        for index, (response, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                async def create(**_kwargs):
                    return response
                result = await XaiAuthoritativeWebSearch(
                    SimpleNamespace(responses=SimpleNamespace(create=create)),
                    ExternalReferenceSettings(True, ("osha.gov",), f"fake-{index}"),
                ).search(f"schema case {index}", language="en")
                self.assertEqual(result["status"], expected)

    async def test_external_failure_taxonomy_is_stable(self):
        class StatusError(RuntimeError):
            def __init__(self, status, message="failure"):
                super().__init__(message)
                self.status_code = status
        class APITimeoutError(RuntimeError):
            pass
        request = httpx.Request("POST", "https://api.x.ai/v1/responses")
        failures = (
            (socket.gaierror("name resolution"), "dns_error"),
            (httpx.ConnectTimeout("connect", request=request), "timeout_connect"),
            (httpx.ReadTimeout("read", request=request), "timeout_read"),
            (APITimeoutError("Request timed out."), "timeout_read"),
            (ssl.SSLError("TLS handshake"), "tls_error"),
            (StatusError(401), "authentication_error"),
            (StatusError(403), "permission_error"),
            (StatusError(429), "rate_limited"),
            (StatusError(503), "provider_5xx"),
            (StatusError(400, "model not found"), "unsupported_model"),
            (StatusError(422), "invalid_request"),
            (ConnectionError("connection reset"), "connect_error"),
            (RuntimeError("unknown schema"), "response_schema_error"),
        )
        for index, (failure, expected) in enumerate(failures):
            with self.subTest(expected=expected):
                async def create(**_kwargs):
                    raise failure
                result = await XaiAuthoritativeWebSearch(
                    SimpleNamespace(responses=SimpleNamespace(create=create)),
                    ExternalReferenceSettings(True, ("osha.gov",), f"fault-{index}"),
                ).search(f"fault query {index}", language="en")
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["attempt_count"], 1)
                self.assertNotIn("failure", str(result))

    def test_candidate_a_launcher_script_resolves_to_open_mode_and_90s_timeouts(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "run_candidate_a.sh"
        self.assertTrue(script.is_file())
        text = script.read_text(encoding="utf-8")
        self.assertIn('EXTERNAL_REFERENCE_DOMAIN_PROFILE="open"', text)
        self.assertIn('EXTERNAL_REFERENCE_TIMEOUT_SECONDS="90"', text)
        self.assertIn('EXTERNAL_REFERENCE_READ_TIMEOUT_SECONDS="90"', text)
        self.assertIn('EXTERNAL_REFERENCE_CONNECT_TIMEOUT_SECONDS="5"', text)
        self.assertIn('EXTERNAL_REFERENCE_MODEL="grok-4.6"', text)
        self.assertIn('external_search_model', text)
        self.assertIn('external_search_open_mode', text)

    async def test_web_search_request_tool_spec_omits_image_search_by_default(self):
        captured_kwargs = {}
        async def create(**kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return {
                "output_text": "Ammonium bicarbonate is a buffer.",
                "citations": ["https://en.wikipedia.org/wiki/Ammonium_bicarbonate"],
                "output": [{"type": "web_search_call", "status": "completed"}],
            }
        searcher = XaiAuthoritativeWebSearch(
            SimpleNamespace(responses=SimpleNamespace(create=create)),
            ExternalReferenceSettings(True, (), "grok-4.6", 90.0, 5, "open"),
        )
        res = await searcher.search("AMBIC buffer", language="en", include_images=False)
        self.assertEqual(res["status"], "success")
        tools = captured_kwargs.get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["type"], "web_search")
        self.assertNotIn("enable_image_search", tools[0])
        self.assertNotIn("filters", tools[0])

    async def test_web_search_request_tool_spec_includes_image_search_when_requested(self):
        captured_kwargs = {}
        async def create(**kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return {
                "output_text": "![AMBIC](https://example.com/ambic.jpg)",
                "citations": ["https://example.com/ambic"],
                "output": [{"type": "web_search_call", "status": "completed"}],
            }
        searcher = XaiAuthoritativeWebSearch(
            SimpleNamespace(responses=SimpleNamespace(create=create)),
            ExternalReferenceSettings(True, (), "grok-4.6", 90.0, 5, "open"),
        )
        res = await searcher.search("AMBIC image", language="en", include_images=True)
        self.assertEqual(res["status"], "success")
        tools = captured_kwargs.get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertTrue(tools[0].get("enable_image_search"))


if __name__ == "__main__":
    unittest.main()
