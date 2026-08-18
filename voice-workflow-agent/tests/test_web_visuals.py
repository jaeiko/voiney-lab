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
        return self.output


class WebVisualTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return WebVisualSettings(True, ExternalReferenceSettings(
            True, ("pubchem.ncbi.nlm.nih.gov",), "fake-model", 2.0, 2,
            "candidate_a",
        ))

    async def test_image_search_is_domain_bound_and_returns_source_link_only(self):
        source = "https://pubchem.ncbi.nlm.nih.gov/compound/962"
        responses = FakeResponses({
            "output_text": (
                "![Authoritative compound record]"
                "(https://pubchem.ncbi.nlm.nih.gov/image.png)"
            ),
            "citations": [source],
            "output": [{"type": "web_search_call"}],
        })
        result = await XaiAuthoritativeImageSearch(
            SimpleNamespace(responses=responses), self.settings()
        ).search("HPLC water authoritative real image")
        self.assertEqual(result["status"], "success")
        candidate = result["matches"][0]
        self.assertEqual(candidate["display_mode"], "web_image")
        self.assertEqual(candidate["image_url"], "https://pubchem.ncbi.nlm.nih.gov/image.png")
        self.assertEqual(candidate["source_page_url"], source)
        tool = responses.calls[0]["tools"][0]
        self.assertTrue(tool["enable_image_search"])
        self.assertNotIn("enable_image_understanding", tool)
        self.assertEqual(
            tool["filters"]["allowed_domains"],
            ["pubchem.ncbi.nlm.nih.gov"],
        )

    async def test_unallowlisted_or_rights_unknown_bytes_are_never_exposed(self):
        responses = FakeResponses({
            "output_text": "![unsafe](https://attacker.example/image.png)",
            "citations": ["https://pubchem.ncbi.nlm.nih.gov/compound/962"],
            "output": [{"type": "web_search_call"}],
        })
        result = await XaiAuthoritativeImageSearch(
            SimpleNamespace(responses=responses), self.settings()
        ).search("example")
        self.assertEqual(result, {"status": "not_found", "matches": []})


if __name__ == "__main__":
    unittest.main()
