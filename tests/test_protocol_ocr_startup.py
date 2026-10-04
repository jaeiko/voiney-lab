"""Server startup builds the OCR provider the catalog's OCR endpoint uses.

Fake engines and fake credentials only; nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from unittest.mock import patch

from voiney_lab import server as server_module
from voiney_lab.protocol_ocr import ProtocolOcrUnavailableError
from voiney_lab.protocol_ocr_providers import CLOVA, GOOGLE, DualEngineOcrProvider

from tests.test_protocol_ocr_providers import (
    CLOVA_SECRET,
    CLOVA_URL,
    ENGLISH,
    GOOGLE_KEY,
    _FakeEngine,
)


class ServerStartupTests(unittest.TestCase):
    """The production boundary: startup builds the provider the catalog uses."""

    def setUp(self) -> None:
        self._had = hasattr(server_module.app.state, "protocol_ocr_provider")
        self._saved = getattr(server_module.app.state, "protocol_ocr_provider", None)
        server_module.app.state.protocol_ocr_provider = None

    def tearDown(self) -> None:
        if self._had:
            server_module.app.state.protocol_ocr_provider = self._saved
        else:
            del server_module.app.state.protocol_ocr_provider

    def test_configured_engines_are_installed_and_served(self) -> None:
        env = {
            "VOINEY_LAB_OCR_PROVIDERS": "clova,google",
            "VOINEY_LAB_CLOVA_OCR_INVOKE_URL": CLOVA_URL,
            "VOINEY_LAB_CLOVA_OCR_SECRET": CLOVA_SECRET,
            "VOINEY_LAB_GOOGLE_VISION_API_KEY": GOOGLE_KEY,
        }
        with self.assertLogs("voiney_lab", level=logging.INFO) as logs:
            server_module._install_protocol_ocr_provider(env)
        provider = server_module._protocol_ocr_provider()
        self.assertIsInstance(provider, DualEngineOcrProvider)
        self.assertEqual(provider.engine_names, (CLOVA, GOOGLE))
        joined = "\n".join(logs.output)
        for secret in (CLOVA_SECRET, GOOGLE_KEY, "clova.invalid"):
            self.assertNotIn(secret, joined)

    def test_nothing_configured_keeps_the_not_configured_path(self) -> None:
        server_module._install_protocol_ocr_provider({})
        with self.assertRaises(ProtocolOcrUnavailableError):
            server_module._protocol_ocr_provider()

    def test_an_injected_provider_is_kept(self) -> None:
        injected = DualEngineOcrProvider((_FakeEngine(GOOGLE, ENGLISH),))
        server_module.app.state.protocol_ocr_provider = injected
        server_module._install_protocol_ocr_provider(
            {"VOINEY_LAB_OCR_PROVIDERS": "google",
             "VOINEY_LAB_GOOGLE_VISION_API_KEY": GOOGLE_KEY}
        )
        self.assertIs(server_module._protocol_ocr_provider(), injected)

    def test_the_lifespan_installs_it(self) -> None:
        env = {
            "VOINEY_LAB_OCR_PROVIDERS": "google",
            "VOINEY_LAB_GOOGLE_VISION_API_KEY": GOOGLE_KEY,
        }

        async def run() -> None:
            async with server_module.lifespan(server_module.app):
                pass

        with patch.dict("os.environ", env), patch.multiple(
            server_module,
            log_protocol_catalog_runtime_configuration=lambda: None,
            start_moss_runtime_from_environment=lambda: None,
            stop_moss_runtime=lambda: None,
        ):
            asyncio.run(run())
        self.assertEqual(server_module._protocol_ocr_provider().engine_names, (GOOGLE,))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
