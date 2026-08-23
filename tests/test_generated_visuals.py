"""Offline generated-visual validation, cache, and provider-boundary tests."""

from __future__ import annotations

import asyncio
import base64
import unittest
from types import SimpleNamespace

from voice_workflow_agent.curated_protocol import _png_rgb
from voice_workflow_agent.generated_visuals import (
    GeneratedVisualRegistry,
    GeneratedVisualSettings,
    VisualSpecification,
    XaiImageGenerator,
)


def specification() -> VisualSpecification:
    return VisualSpecification(
        document_sha256="a" * 64,
        protocol_id="candidate-a-curated-development-v1",
        revision_id="candidate-a-curated-analysis-v1",
        step_id="step-2",
        step_label="2",
        source_page=2,
        source_evidence_ids=("current_step", "material_1"),
        action_summary="Add verified fictional solution A to the gel band.",
        verified_materials=("verified fictional solution A",),
        verified_tools=("verified fictional tube",),
        verified_relations=("solution A is added to the gel band",),
        forbidden_inferences=("color", "completion"),
    )


def valid_png() -> bytes:
    return _png_rgb(64, 64, b"\xff\xff\xff" * 64 * 64)


class GeneratedVisualTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_inflight_generation_is_cached_and_same_origin(self):
        registry = GeneratedVisualRegistry()
        calls = 0

        async def generate(_):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return valid_png()

        settings = GeneratedVisualSettings(True, "fake")
        first, second = await asyncio.gather(
            registry.obtain(specification(), settings, generate),
            registry.obtain(specification(), settings, generate),
        )
        self.assertEqual(calls, 1)
        self.assertEqual(first[0].asset_id, second[0].asset_id)
        asset = first[0].public_dict()
        self.assertEqual(asset["kind"], "generated_instructional")
        self.assertEqual(asset["url"], f"/api/generated-visuals/{asset['asset_id']}")
        self.assertNotIn("http", asset["url"])
        self.assertIn("not an original source image", asset["label"])
        cached, cache_hit = await registry.obtain(
            specification(), settings, generate
        )
        self.assertTrue(cache_hit)
        self.assertEqual(cached.asset_id, asset["asset_id"])
        self.assertEqual(calls, 1)

    async def test_invalid_or_oversized_bytes_fail_closed(self):
        for raw, maximum in ((b"not an image", 100), (valid_png(), 20)):
            with self.subTest(size=len(raw)):
                registry = GeneratedVisualRegistry()

                async def generate(_, value=raw):
                    return value

                with self.assertRaises(ValueError):
                    await registry.obtain(
                        specification(), GeneratedVisualSettings(True, "fake", maximum),
                        generate,
                    )

    async def test_official_image_contract_uses_base64_and_grounded_prompt(self):
        encoded = base64.b64encode(valid_png()).decode("ascii")

        class Images:
            async def generate(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json=encoded)]
                )

        client = SimpleNamespace(images=Images())
        output = await XaiImageGenerator(
            client, GeneratedVisualSettings(True, "grok-imagine-image-quality")
        ).generate(specification())
        self.assertEqual(output, valid_png())
        kwargs = client.images.kwargs
        self.assertEqual(kwargs["response_format"], "b64_json")
        self.assertEqual(kwargs["extra_body"], {"aspect_ratio": "4:3"})
        self.assertIn("verified fictional solution A", kwargs["prompt"])
        self.assertNotIn("ignore previous", kwargs["prompt"])
        self.assertNotIn("user transcript", kwargs["prompt"])

    async def test_corrupted_png_is_rejected(self):
        raw = bytearray(valid_png())
        raw[-8] ^= 1
        registry = GeneratedVisualRegistry()

        async def generate(_):
            return bytes(raw)

        with self.assertRaises(ValueError):
            await registry.obtain(
                specification(), GeneratedVisualSettings(True, "fake"), generate
            )


if __name__ == "__main__":
    unittest.main()
