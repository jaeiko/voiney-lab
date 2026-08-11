"""Authoritative web-image discovery tests are fake-backed and offline."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from voice_workflow_agent.external_references import ExternalReferenceSettings
from voice_workflow_agent.web_visuals import (
    WebVisualSettings,
    XaiAuthoritativeImageSearch,
)


class FakeResponses:
    def __init__(self, output):
        self.output = output
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": self.output}


class WebVisualTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return WebVisualSettings(True, ExternalReferenceSettings(
            True, ("pubchem.ncbi.nlm.nih.gov",), "fake-model", 2.0, 2,
            "candidate_a",
        ))

    async def test_image_search_is_domain_bound_and_returns_source_link_only(self):
        responses = FakeResponses([
            {"type": "web_search_call"},
            {"type": "message", "content": [{
                "image_url": "https://pubchem.ncbi.nlm.nih.gov/image.png",
                "source_url": "https://pubchem.ncbi.nlm.nih.gov/compound/962",
                "title": "Authoritative compound record",
                "caption": "Example structure",
            }]},
        ])
        result = await XaiAuthoritativeImageSearch(
            SimpleNamespace(responses=responses), self.settings()
        ).search("HPLC water authoritative real image")
        self.assertEqual(result["status"], "success")
        candidate = result["matches"][0]
        self.assertEqual(candidate["display_mode"], "source_link")
        self.assertNotIn("image_url", candidate)
        tool = responses.calls[0]["tools"][0]
        self.assertTrue(tool["enable_image_search"])
        self.assertTrue(tool["enable_image_understanding"])
        self.assertEqual(
            tool["filters"]["allowed_domains"],
            ["pubchem.ncbi.nlm.nih.gov"],
        )

    async def test_unallowlisted_or_rights_unknown_bytes_are_never_exposed(self):
        responses = FakeResponses([
            {"type": "web_search_call"},
            {"type": "message", "content": [{
                "image_url": "https://attacker.example/image.png",
                "source_url": "https://pubchem.ncbi.nlm.nih.gov/compound/962",
            }]},
        ])
        result = await XaiAuthoritativeImageSearch(
            SimpleNamespace(responses=responses), self.settings()
        ).search("example")
        self.assertEqual(result, {"status": "not_found", "matches": []})


if __name__ == "__main__":
    unittest.main()
