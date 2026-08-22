"""Authoritative web-image discovery tests are fake-backed and offline."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from voice_workflow_agent.external_references import ExternalReferenceSettings
from voice_workflow_agent.web_visuals import (
    WebVisualSettings,
    XaiAuthoritativeImageSearch,
)
from voice_workflow_agent.server import (
    _prepare_external_visual_candidate,
    _queue_curated_web_visual,
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
        self.assertEqual(responses.calls[0]["include"], ["no_inline_citations"])
        self.assertTrue(result["image_search_enabled"])
        self.assertEqual(result["max_results"], 1)
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
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["matches"], [])
        self.assertTrue(result["image_search_enabled"])
        self.assertEqual(result["max_results"], 1)

    async def test_runtime_only_displays_rights_labeled_same_origin_proxy(self):
        candidate = {
            "kind": "web_image_reference",
            "image_url": "https://pubchem.ncbi.nlm.nih.gov/image.png",
            "source_page_url": "https://pubchem.ncbi.nlm.nih.gov/compound/962",
            "publisher_domain": "pubchem.ncbi.nlm.nih.gov",
            "title": "Water structure",
        }
        source_only = await _prepare_external_visual_candidate(candidate)
        self.assertNotIn("image_url", source_only)
        self.assertEqual(source_only["display_mode"], "source_link")
        candidate["rights"] = "Public domain"
        registry = AsyncMock()
        registry.obtain_or_register.return_value = SimpleNamespace(
            asset_id="a" * 64)
        with patch(
            "voice_workflow_agent.server.WEB_VISUAL_REGISTRY", registry
        ):
            proxied = await _prepare_external_visual_candidate(candidate)
        self.assertEqual(proxied["image_url"], "/api/web-visuals/" + "a" * 64)
        self.assertEqual(proxied["rights"], "Public domain")
        registry.obtain_or_register.assert_awaited_once()

    async def test_production_visual_job_marks_the_single_xai_image_search(self):
        class Session:
            accepted_configuration_id = 9
            generated_visual_settings = SimpleNamespace(enabled=False)

            def __init__(self):
                self.task = None

            def owns_visual_result(self, *_args):
                return True

            def track_visual_task(self, task):
                self.task = task

        class Sender:
            def __init__(self):
                self.events = []

            async def text(self, kind, **fields):
                self.events.append({"type": kind, **fields})

        step = SimpleNamespace(
            step_id="step-1", source_label="1",
            instruction_source_text="Show the chromatography instrument",
        )
        fixture = SimpleNamespace(
            source_pdf_sha256="b" * 64, protocol_id="protocol-test",
            title="Fictional chromatography", steps=(step,),
        )
        curated = SimpleNamespace(fixture=fixture, current_index=0)
        session, sender = Session(), Sender()
        candidate = {
            "kind": "web_image_reference",
            "image_url": "https://pubchem.ncbi.nlm.nih.gov/instrument.png",
            "source_page_url": "https://pubchem.ncbi.nlm.nih.gov/docs/instrument",
            "publisher_domain": "pubchem.ncbi.nlm.nih.gov",
            "title": "Instrument",
            "rights": "Public domain",
        }
        with patch(
            "voice_workflow_agent.server.WikimediaVisualAdapter.lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            "voice_workflow_agent.server.XaiAuthoritativeImageSearch.search",
            new=AsyncMock(return_value={
                "status": "success", "matches": [candidate],
                "image_search_enabled": True, "web_search_count": 1,
                "image_search_count": 1,
            }),
        ), patch(
            "voice_workflow_agent.server.WEB_VISUAL_REGISTRY.obtain_or_register",
            new=AsyncMock(return_value=SimpleNamespace(asset_id="c" * 64)),
        ), patch(
            "voice_workflow_agent.server.require_env", return_value="test-value"
        ):
            await _queue_curated_web_visual(
                session=session, sender=sender, turn_id=3, generation=4,
                endpoint=0.0, clock=lambda: 1.0, curated=curated,
                settings=self.settings(),
                requested_entities=("chromatography instrument",),
                visual_intent="lab_equipment_image",
            )
            await session.task
        calls = [item for item in sender.events if item["type"] == "tool.call"]
        self.assertEqual(
            [(item["round"], item["image_search_enabled"]) for item in calls],
            [(2, False), (3, True)],
        )
        results = [item for item in sender.events if item["type"] == "tool.result"]
        self.assertEqual(results[-1]["image_search_count"], 1)
        ready = next(
            item for item in sender.events
            if item["type"] == "protocol.visual.state"
            and item["status"] == "web_visual_ready"
        )
        self.assertEqual(
            ready["candidate"]["image_url"], "/api/web-visuals/" + "c" * 64)


if __name__ == "__main__":
    unittest.main()
