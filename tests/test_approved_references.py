"""Offline tests for the approved-document tier and the supplemental ("AI 일반 지식") explanation.

The xAI web reference search these tests used to cover was deleted in lane DOC3.
"""

from __future__ import annotations

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

from voiney_lab.document_store import ingest_manifest
from voiney_lab.external_references import (
    SupplementalKnowledgeSettings,
    XaiSupplementalKnowledge,
)
from voiney_lab.retrieval import retrieve_approved_lab_documents

from tests.test_document_ingestion import operational_document

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

    def test_supplemental_settings_are_separate_and_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(
                SupplementalKnowledgeSettings.from_environment().enabled
            )
        with patch.dict(os.environ, {
            "VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED": "true",
            "VOINEY_LAB_SUPPLEMENTAL_PROVIDER": "openai",
            "VOINEY_LAB_SUPPLEMENTAL_MODEL": "grok-test",
            "VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS": "7",
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

    async def test_supplemental_failure_taxonomy_is_stable(self):
        """The failure categories the explanation reports (kept from the web search's test)."""

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
        for failure, expected in failures:
            with self.subTest(expected=expected):
                async def create(**_kwargs):
                    raise failure
                result = await XaiSupplementalKnowledge(
                    SimpleNamespace(responses=SimpleNamespace(create=create)),
                    SupplementalKnowledgeSettings(True, "grok-test", 2.0),
                ).explain("Explain the background.", language="en")
                self.assertEqual(result["status"], expected)
                self.assertNotIn("failure", str(result))


if __name__ == "__main__":
    unittest.main()
